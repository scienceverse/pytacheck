"""bibr export schema 12.x: read, and write with paper_write(schema_version).

Port of metacheck's tests/testthat/test-import-bibr12.R (and helper-bibr12.R),
plus pytacheck's own checks: the in-memory reader (``from_bibr``), the
``paper_write()`` default, lazy tables, and the module suite on 12.x papers.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd
import pytest

import metacheck as pc
from metacheck.io import bibr12
from parity.canonical import canonical
from parity.compare import Options, compare

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def bibr12_path(fixtures_dir: Path, name: str) -> Path:
    return fixtures_dir / "bibr12" / f"{name}.json"


@pytest.fixture
def f12(fixtures_dir: Path):
    return lambda name: bibr12_path(fixtures_dir, name)


def read_json(path: Path) -> Any:
    return orjson.loads(Path(path).read_bytes())


def same(a: Any, b: Any, **options: Any) -> list[str]:
    """Differences between two values, as the parity harness sees them."""
    return compare(canonical(a), canonical(b), Options.from_case(options or None))


def expect_valid_bibr12(path: Path, fixtures_dir: Path) -> None:
    """helper-bibr12.R: validate against bibr's strict 12.0 schema (needs jsonschema)."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = read_json(fixtures_dir / "bibr12" / "bibr-export-v12.schema.json")
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(read_json(path)), key=str)
    assert not errors, f"{path.name} is not valid bibr 12.0:\n" + "\n".join(
        f"{list(e.path)} {e.message}" for e in errors[:10]
    )


# -- test-import-bibr12.R ------------------------------------------------------------


def test_read_a_bibr12_export(f12) -> None:
    path = f12("PMC4383902")
    json = read_json(path)
    paper = pc.read(path)

    assert isinstance(paper, pc.Paper)
    assert pc.paper_validate(paper)
    assert paper.paper_id == "PMC4383902"

    # metadata and source make the info table
    info = paper.info.iloc[0]
    assert info["title"] == json["metadata"]["title"]
    assert info["doi"] == "10.1093/nar/gku1061"
    assert info["abstract"] == json["metadata"]["abstract"]
    assert info["file_name"] == "PMC4383902.xml"
    assert info["sha256"] == json["source"]["sha256"]
    assert info["input_format"] == "jats"
    assert info["schema_version"] == "12.0"

    # a group author, and the affiliation table
    assert paper.author["literal"].tolist() == ["The Europe PMC Consortium"]
    assert pd.isna(paper.author["family"].iloc[0])
    assert len(paper.affiliation) == 4
    assert paper.affiliation["author_ids"].iloc[0] == [1]

    # sections in order, and the sentences
    assert paper.section["section_id"].tolist() == list(range(1, len(json["section"]) + 1))
    assert paper.section["header"].tolist() == [s["header"] for s in json["section"]]
    assert paper.text["text"].tolist() == [t["text"] for t in json["text"]]

    # references, and a citation that resolves to its reference
    assert paper.bib["bib_id"].tolist() == list(range(1, 18))
    cite = paper.xref[paper.xref["xref_type"] == "bib"].iloc[0]
    assert cite["contents"] == "(1)"
    assert "NAR database issue (1)" in paper.text["text"].iloc[cite["text_id"] - 1]
    ref = paper.bib[paper.bib["bib_id"] == cite["target_id"]].iloc[0]
    assert ref["title"] == "UKPMC: a full text article resource for the life sciences"
    assert ref["doi"] == "10.1093/nar/gkq1063"

    # a figure: label, caption and its caption row, which has no section
    fig = paper.figure.iloc[0]
    assert fig["label"] == "1"
    assert fig["caption"].startswith("Figure 1. (a) Total scope of content")
    assert paper.text["text"].iloc[fig["text_id"] - 1] == fig["caption"]
    assert pd.isna(paper.text["section_id"].iloc[fig["text_id"] - 1])
    assert fig["section_id"] in paper.section["section_id"].tolist()
    fref = paper.xref[paper.xref["xref_type"] == "figure"].iloc[0]
    assert fref["contents"] == "Figure 1a"
    assert fref["target_id"] == fig["figure_id"]

    # a footnote
    assert paper.footnote["label"].tolist() == ["†"]
    note = paper.text.iloc[[t - 1 for t in paper.footnote["text_id"]]]
    assert "list of authors of the Europe PMC Consortium" in note["text"].iloc[0]
    assert note["section_id"].isna().all()

    # no caption or footnote pseudo-sections: every section holds body text
    special = {*paper.figure["text_id"].tolist(), *paper.footnote["text_id"].tolist()}
    body = paper.text[~paper.text["text_id"].isin(special)]
    assert not body["section_id"].isna().any()

    # text search still finds captions and footnotes
    found = pc.text_search(paper, "list of authors of the Europe PMC Consortium")
    assert found["text_id"].tolist() == paper.footnote["text_id"].tolist()

    # how the paper was made
    assert paper.extraction["producer"]["name"] == "bibr"
    assert paper.extraction["converter"] is None
    assert paper.extraction["warnings"][0]["code"] == "STATEMENT_LEXICAL_FALLBACK"


def test_read_a_bibr12_table(f12) -> None:
    paper = pc.read(f12("probe_html"))
    assert pc.paper_validate(paper)

    tbl = paper.table.iloc[0]
    assert tbl["label"] == "1"
    assert tbl["caption"] == "Table 1. Values"
    assert paper.text["text"].iloc[tbl["text_id"] - 1] == "Table 1. Values"
    assert pd.isna(paper.text["section_id"].iloc[tbl["text_id"] - 1])
    assert paper.table["contents"].iloc[0] == [["A", "B"], ["1", "2"]]

    # references that name no row have no target
    sup = paper.xref[paper.xref["xref_type"].isin(["equation", "section", "supplementary"])]
    assert len(sup) == 4
    assert sup["target_id"].isna().all()


def test_read_bibr12_statistics_matches_and_images(f12) -> None:
    paper = pc.read(f12("full"))
    assert pc.paper_validate(paper)

    # metacheck keeps degrees of freedom in parentheses; 12.x writes "28"
    assert paper.eq["df"].tolist() == ["(28)"]
    assert paper.eq["verbatim"].tolist() == ["t(28) = 3.42"]

    # match tables, with the given/family columns metacheck's modules read
    assert paper.info_match["score"].tolist() == [0.99]
    assert paper.bib_match["author"].iloc[0][0]["family"] == "Smith"
    assert paper.bib_match["authors"].iloc[0] == [{"given": "Jane", "family": "Smith"}]
    assert paper.affiliation_match["service_id"].tolist() == ["https://ror.org/0abcde123"]
    assert paper.funding_match["funder_doi"].tolist() == ["10.13039/100000001"]

    # figure images are dropped unless asked for
    docx = pc.read(f12("probe_docx"))
    assert docx.figure["image"].isna().all()
    docx = pc.read(f12("probe_docx"), include_images=True)
    assert docx.figure["image"].iloc[0].startswith("data:image/png;base64,")


def test_modules_read_citations_of_bibr12_papers(f12) -> None:
    paper = pc.read(f12("PMC4383902"))
    n_cite = int((paper.xref["xref_type"] == "bib").sum())

    mo = pc.module_run(paper, "ref_consistency")
    assert mo.summary_table["n_bib"].tolist() == [17]
    assert mo.summary_table["n_xrefs"].tolist() == [n_cite]


def test_ref_accuracy_scores_bibr12_matches_0_1(f12) -> None:
    paper = pc.read(f12("full"))
    # a reference without a DOI, and a match scoring 0.98 with one (the module
    # suggests the DOIs of matches that have no title)
    bib = paper.bib.copy()
    bib["doi"] = pd.Series([None] * len(bib), dtype="string")
    bib["text_id"] = pd.Series([2] * len(bib), dtype="Int64")
    paper.bib = bib
    match = paper.bib_match.copy()
    match["title"] = pd.Series([None] * len(match), dtype="string")
    paper.bib_match = match
    assert paper.bib_match["score"].tolist() == [0.98]

    mo = pc.module_run(paper, "ref_accuracy")
    assert "10.1234/prior" in "\n".join(map(str, mo.report))

    # for an older paper, 0.98 is far below the default suggest_score of 70
    info = paper.info.copy()
    info["schema_version"] = pd.Series([None], dtype="string")
    paper.info = info
    mo = pc.module_run(paper, "ref_accuracy")
    assert "10.1234/prior" not in "\n".join(map(str, mo.report))


def test_a_later_bibr12_file_reads_ignoring_unknown_keys(f12, tmp_path: Path) -> None:
    json = read_json(f12("probe_html"))
    json["schema_version"] = "12.1"
    json["new_table"] = [{"new_id": 1}]
    json["metadata"]["new_field"] = "new"
    json["text"][0]["new_field"] = "new"
    json["extraction"]["new_block"] = {"new_field": "new"}
    path = tmp_path / "probe_html.json"
    path.write_bytes(orjson.dumps(json))

    paper = pc.read(path)
    orig = pc.read(f12("probe_html"))
    assert pc.paper_validate(paper)
    assert paper.info["schema_version"].tolist() == ["12.1"]
    assert paper.keys() == orig.keys()
    assert paper.title == orig.title
    assert paper.text.equals(orig.text)
    assert paper.extraction["new_block"] == {"new_field": "new"}


@pytest.mark.parametrize("version", ["11.0", "13.0", 12])
def test_only_bibr_schema_12x_is_read(f12, tmp_path: Path, version: Any) -> None:
    json = read_json(f12("probe_html"))
    json["schema_version"] = version
    path = tmp_path / "v11.json"
    path.write_bytes(orjson.dumps(json))
    shown = "12" if version == 12 else version
    with pytest.raises(ValueError, match=f"schema {shown} is not supported"):
        pc.papers.read_bibr(path)
    with pytest.raises(ValueError, match="is not supported"):
        pc.from_bibr(json)


@pytest.mark.parametrize("name", ["PMC4383902", "probe_html", "full", "preprint"])
def test_paper_write_writes_bibr12_that_reads_back_the_same(
    f12, fixtures_dir: Path, tmp_path: Path, name: str
) -> None:
    path = f12(name)
    paper = pc.read(path)
    json_path = pc.paper_write(paper, save_path=tmp_path, schema_version="12.0")
    assert isinstance(json_path, Path)
    try:
        expect_valid_bibr12(json_path, fixtures_dir)
    except pytest.skip.Exception:
        pass

    # nothing is lost: the rewrite differs from bibr's JSON only in converter
    orig = read_json(path)
    json = read_json(json_path)
    assert orig["extraction"]["converter"] is None
    assert json["extraction"]["converter"] == {
        "name": "pytacheck",
        "version": pc.__version__,
        "build_sha": None,
    }
    del orig["extraction"]["converter"], json["extraction"]["converter"]
    # every value exactly (metacheck rounds doubles to 15 significant digits; U22)
    assert json == orig

    # and it reads back the same paper
    paper2 = pc.read(json_path)
    assert not same(paper2, paper, ignore=["extraction.converter"])

    # jsonlite's layout: two-space indentation, a final newline
    text = json_path.read_text(encoding="utf-8")
    assert text.startswith('{\n  "paper_id": ')
    assert text.endswith("}\n")


def test_paper_write_keeps_images_when_read_with_them(f12, tmp_path: Path) -> None:
    paper = pc.read(f12("probe_docx"), include_images=True)
    json_path = pc.paper_write(paper, save_path=tmp_path, schema_version="12.0")
    assert read_json(json_path)["figure"] == read_json(f12("probe_docx"))["figure"]


def test_paper_write_default_output(f12, tmp_path: Path) -> None:
    paper = pc.read(f12("probe_html"))

    # schema_version=None (metacheck's default) saves the paper object as before
    json_path = pc.paper_write(paper, save_path=tmp_path / "legacy", schema_version=None)
    json = read_json(json_path)
    assert "schema_version" not in json
    assert json["info"][0]["title"] == paper.title

    # 12.0 needs a 12.x paper
    with pytest.raises(ValueError, match="metacheck's older format"):
        pc.paper_write(pc.demopaper(), save_path=tmp_path, schema_version="12.0")
    with pytest.raises(ValueError, match=r'schema_version must be "auto", None or "12\.0"'):
        pc.paper_write(paper, save_path=tmp_path, schema_version="11.0")

    # a paper read from a later 12.x file is not rewritten
    info = paper.info.copy()
    info["schema_version"] = pd.Series(["12.1"], dtype="string")
    paper.info = info
    with pytest.raises(ValueError, match=r"read from bibr export schema 12\.1"):
        pc.paper_write(paper, save_path=tmp_path, schema_version="12.0")


def test_paper_write_converts_bib_match_rows_from_add_bib_match(
    f12, fixtures_dir: Path, tmp_path: Path
) -> None:
    paper = pc.read(f12("full"))
    # the columns add_bib_match() makes: given/family authors, CrossRef scores
    paper.bib_match = pd.DataFrame(
        {
            "bib_id": pd.Series([1], dtype="Int64"),
            "service": pd.Series(["crossref"], dtype="string"),
            "service_id": pd.Series([None], dtype="string"),
            "score": [61.7],
            "bib_type": pd.Series(["article"], dtype="string"),
            "doi": pd.Series(["10.1234/PRIOR"], dtype="string"),
            "title": pd.Series(["A Prior Study"], dtype="string"),
            "publisher": pd.Series([None], dtype="string"),
            "year": pd.Series([2020], dtype="Int64"),
            "date": pd.Series([None], dtype="string"),
            "container": pd.Series(["Journal of Things"], dtype="string"),
            "authors": pd.Series(
                [pd.DataFrame({"given": ["Jane"], "family": ["Smith"]})], dtype=object
            ),
            "editors": pd.Series(
                [pd.DataFrame({"given": pd.Series([], dtype="string"), "family": []})],
                dtype=object,
            ),
        }
    )

    json_path = pc.paper_write(paper, save_path=tmp_path, schema_version="12.0")
    try:
        expect_valid_bibr12(json_path, fixtures_dir)
    except pytest.skip.Exception:
        pass

    json = read_json(json_path)
    match = json["bib_match"][0]
    assert match["author"] == [{"given": "Jane", "family": "Smith"}]
    assert match["editor"] is None
    assert match["score"] is None
    assert match["bib_type"] == "journal_article"
    assert match["doi"] == "10.1234/prior"
    codes = [w["code"] for w in json["extraction"]["warnings"]]
    assert "METACHECK_MATCH_SCORE_NOT_0_1" in codes


# -- pytacheck ------------------------------------------------------------------------


def test_paper_write_auto_default(f12, tmp_path: Path) -> None:
    """``schema_version="auto"``: 12.0 for a 12.x paper, the paper object otherwise."""
    paper = pc.read(f12("full"))
    auto = pc.paper_write(paper, "auto", tmp_path)
    explicit = pc.paper_write(paper, "explicit", tmp_path, schema_version="12.0")
    assert auto.read_bytes() == explicit.read_bytes()  # type: ignore[union-attr]
    assert read_json(auto)["schema_version"] == "12.0"

    demo = pc.paper_write(pc.demopaper(), "demo", tmp_path)
    legacy = pc.paper_write(pc.demopaper(), "legacy", tmp_path, schema_version=None)
    assert demo.read_bytes() == legacy.read_bytes()  # type: ignore[union-attr]
    assert "schema_version" not in read_json(demo)

    # a list: each paper by its own format
    papers = pc.PaperList([pc.demopaper(), paper])
    paths = pc.paper_write(papers, save_path=tmp_path / "list")
    assert [p.name for p in paths] == ["to_err_is_human.json", "demo.json"]  # type: ignore[union-attr]
    assert "schema_version" not in read_json(paths[0])  # type: ignore[index]
    assert read_json(paths[1])["schema_version"] == "12.0"  # type: ignore[index]


def test_is_bibr12_and_paper_ids(f12) -> None:
    paper = pc.read(f12("full"))
    assert bibr12.is_bibr12(paper)
    assert not bibr12.is_bibr12(pc.demopaper())
    assert not bibr12.is_bibr12(pc.test_paper())
    papers = pc.PaperList([pc.demopaper(), paper])
    assert bibr12._bibr12_paper_ids(papers) == ["demo"]
    assert bibr12._bibr12_paper_ids(paper) == ["demo"]


def test_from_bibr_reads_12x_natively(f12) -> None:
    """The in-process bibr path: a dict (or a bibr.Result) is read like the file."""
    data = read_json(f12("full"))

    class FakeResult:
        pass

    result = FakeResult()
    result.data = data  # type: ignore[attr-defined]
    from_file = pc.read(f12("full"))
    for source in (data, result):
        p = pc.from_bibr(source)
        assert p.title == "A Demonstration Paper"
        assert not same(p, from_file)
    # the caller's dict is not shared with the paper
    p = pc.from_bibr(data)
    data["extraction"]["warnings"].clear()
    assert p.extraction["warnings"]


def test_chew_returns_native_12x_papers(f12, monkeypatch: pytest.MonkeyPatch) -> None:
    """``metacheck.io.bibr.chew()`` with a stand-in bibr that returns 12.0 exports."""
    import sys
    import types

    from metacheck.io import bibr as bibr_io

    data = read_json(f12("full"))

    class Result:
        ok = True

        def __init__(self, path: str) -> None:
            self.path = path
            self.data = data

    fake = types.ModuleType("bibr")
    fake.chew = lambda target, **options: (  # type: ignore[attr-defined]
        [Result(t) for t in target] if isinstance(target, list) else Result(target)
    )
    monkeypatch.setitem(sys.modules, "bibr", fake)
    bibr_io.bibr_available.cache_clear()
    try:
        paper = bibr_io.chew("paper.pdf")
        assert bibr12.is_bibr12(paper)
        assert not same(paper, pc.read(f12("full")))
        papers = bibr_io.chew(["a.pdf", "b.pdf"])
        assert len(papers) == 2
    finally:
        bibr_io.bibr_available.cache_clear()


@pytest.mark.parametrize("name", ["PMC4383902", "full", "preprint", "probe_docx", "probe_html"])
def test_lazy_tables_equal_bibr12_df(f12, name: str) -> None:
    """The lazily materialised tables are ``.bibr12_df(.bibr12_rows(...))`` exactly."""
    json = read_json(f12(name))
    paper = pc.read(f12(name))
    for tbl, table in bibr12.BIBR12_TABLES.items():
        if tbl in ("eq", "figure"):
            continue  # df is parenthesised, images dropped (checked above)
        cols = bibr12.BIBR12_COLS[tbl]
        expected = bibr12._bibr12_df(bibr12._bibr12_rows(json.get(tbl), cols), cols)
        got = paper[table].loc[:, list(cols)]
        assert not same(got, expected), (tbl, same(got, expected))


def test_bibr12_json_layout_and_values() -> None:
    """Two-space indentation, one value per line; every value reads back as written."""
    x = {
        "s": 'a"b\\c/d</e\n\t\x01\x7fé',
        "n": None,
        "na": pd.NA,
        "e": [],
        "o": {},
        "arr": bibr12._Array(["a", None]),
        "arr0": bibr12._Array(),
        "nums": [1, 0.1 + 0.2, 1e-5, 1e5, 1e21, 2147483648, float("nan"), float("inf"), True],
        "np": [np.int64(3), np.float64(1.5), np.bool_(False)],
        "rows": [{"a": 1, "b": [bibr12._Array(["x"])]}],
    }
    text = bibr12.bibr12_json(x)
    assert text.startswith('{\n  "s": ')
    assert '  "e": [],\n  "o": {},\n' in text
    assert '  "arr": [\n    "a",\n    null\n  ],\n  "arr0": [],\n' in text
    got = orjson.loads(text)
    assert got == {
        "s": x["s"],
        "n": None,
        "na": None,
        "e": [],
        "o": {},
        "arr": ["a", None],
        "arr0": [],
        # a double that 15 significant digits would change is kept in full (U22)
        "nums": [1, 0.30000000000000004, 1e-05, 100000, 1e21, 2147483648, None, None, True],
        "np": [3, 1.5, False],
        "rows": [{"a": 1, "b": [["x"]]}],
    }
    assert isinstance(got["nums"][2], float)


def test_bibr12_json_other_types() -> None:
    table = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
    got = orjson.loads(bibr12.bibr12_json({"t": table, 3: "three", "p": Path("a/b")}))
    assert got == {"t": [{"a": 1, "b": "x"}, {"a": 2, "b": None}], "3": "three", "p": "a/b"}
    with pytest.raises(TypeError):
        bibr12.bibr12_json(2**70)


def test_bibr12_helpers() -> None:
    assert bibr12._bibr12_doi([" https://doi.org/10.1234/ABC ", "doi: 10.5/x", None, "x"]) == [
        "10.1234/abc",
        None,
        None,
        None,
    ]
    assert bibr12._bibr12_bib_type(["article", "misc", None, "thesis"]) == [
        "journal_article",
        "other",
        None,
        "thesis",
    ]
    import hashlib

    path = FIXTURES / "bibr_v12_full.json"
    assert bibr12._bibr12_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert bibr12._bibr12_sha256(FIXTURES / "no-such-file.pdf") is None


def test_pytacheck_fixtures() -> None:
    """pytacheck's own 12.0 fixtures (optional metadata keys and tables missing)."""
    full = pc.read(FIXTURES / "bibr_v12_full.json")
    assert full.title == "A Demonstration Paper"
    assert list(full.info["keywords"].iloc[0]) == ["schema", "export"]
    assert len(full.info["file_hash"].iloc[0]) == 16

    p = pc.read(FIXTURES / "bibr_v12_inspect.json")
    assert pc.paper_validate(p)
    assert p.info["pmid"].isna().all()  # a metadata key the file leaves out
    assert len(p.affiliation_match) == 0  # a table the file leaves out
    assert list(p.affiliation_match.columns) == list(bibr12.BIBR12_COLS["affiliation_match"])
    out = pc.module_run(p, "marginal")
    assert out.traffic_light in ("green", "red")


def test_older_papers_read_as_before() -> None:
    """Files without a root schema_version use the older reader (no 12.x tables)."""
    demo = pc.demopaper()
    assert not bibr12.is_bibr12(demo)
    assert "extraction" not in demo
    assert "footnote" not in demo
    assert demo.footnote is None


# -- the module suite on 12.x papers ----------------------------------------------------


def _builtin_modules() -> list[str]:
    from metacheck.module import _builtin_names

    return list(_builtin_names())


@pytest.mark.parametrize("module", _builtin_modules())
@pytest.mark.parametrize("name", ["preprint", "full"])
def test_every_module_runs_on_bibr12_papers(f12, module: str, name: str) -> None:
    """module_run() of every built-in module on a 12.x paper: no error, no mutation.

    Network requests replay metacheck's recorded API responses (anything
    unrecorded is a 404), and LLM-backed modules run without an LLM, so this
    never leaves the machine. Modules that need no network or LLM are compared
    with metacheck in the bibr12 parity cases.
    """
    from metacheck.packs.check import run_issues
    from tests.httpmock import replay

    paper = pc.read(f12(name))
    info = pc.module_info(module)
    with replay("apis"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        issues = run_issues(info, [(name, paper)])
    offline = [i for i in issues if i.code == "run"]
    if offline and "network" in info.requires:
        # e.g. PubPeer or the causal-sentence API answering 404 for requests
        # metacheck never recorded (ref_pubpeer then fails as metacheck does
        # when PubPeer is unreachable: pubpeer_comments() returns NULL)
        pytest.skip(f"{module} needs API responses that are not recorded: {offline[0]}")
    problems = [str(i) for i in issues if i.level == "error"]
    if any("There were no modules that matched" in p for p in problems):
        # e.g. codebook_check reads data_check's output, and data_check is not ported yet
        pytest.skip(f"{module} runs a module that is not ported yet: {problems[0]}")
    assert not problems, problems
