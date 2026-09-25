"""Grobid TEI to bibr export schema 12.0: grobid_to_bibr(schema_version).

Port of metacheck's tests/testthat/test-import-grobid-bibr12.R, plus
pytacheck's own checks: the 12.0 default of ``grobid_to_bibr()`` and
``read()``, the converter name, ``.bibr12_utc()`` and the module suite on
12.0 Grobid papers.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import warnings
from pathlib import Path
from typing import Any

import orjson
import pytest

import pytacheck as pc
from parity.canonical import canonical
from parity.compare import Options, compare
from pytacheck.io.bibr12 import _bibr12_sha256
from pytacheck.io.grobid import _grobid_to_bibr, grobid_to_bibr
from pytacheck.io.grobid_bibr12 import _bibr12_utc, _grobid_to_bibr12

HERE = Path(__file__).resolve().parent
EDGES = HERE / "fixtures" / "v12_edges.tei.xml"
SCHEMA_ERROR = re.escape('schema_version must be None or "12.0"')


@pytest.fixture
def tei(fixtures_dir: Path):
    return lambda name: fixtures_dir / "formats" / f"{name}.pdf.tei.xml"


def same(a: Any, b: Any, **options: Any) -> list[str]:
    """Differences between two values, as the parity harness sees them."""
    return compare(canonical(a), canonical(b), Options.from_case(options or None))


def codes(paper: pc.Paper) -> list[str]:
    return [w["code"] for w in paper["extraction"]["warnings"]]


def expect_valid_bibr12(path: Path, fixtures_dir: Path) -> None:
    """helper-bibr12.R: validate against bibr's strict 12.0 schema (needs jsonschema)."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = orjson.loads((fixtures_dir / "bibr12" / "bibr-export-v12.schema.json").read_bytes())
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(orjson.loads(Path(path).read_bytes())), key=str)
    assert not errors, [f"{list(e.path)} {e.message}" for e in errors[:10]]


# -- test-import-grobid-bibr12.R -------------------------------------------------------


@pytest.mark.parametrize("name", ["demo", "published", "preprint"])
def test_grobid_tei_converts_to_bibr12_that_reads_back_the_same(
    name: str, tei, fixtures_dir: Path, tmp_path: Path
) -> None:
    xml_path = pc.demofile("xml") if name == "demo" else tei(name)
    paper = _grobid_to_bibr(xml_path, schema_version="12.0")
    assert pc.paper_validate(paper)

    json_path = grobid_to_bibr(xml_path, tmp_path, schema_version="12.0")
    try:
        expect_valid_bibr12(Path(json_path), fixtures_dir)
    except pytest.skip.Exception:
        pass

    paper2 = pc.read(json_path)
    assert not same(paper2, paper)


def test_source_producer_ids_and_targets() -> None:
    paper = _grobid_to_bibr(pc.demofile("xml"), schema_version="12.0")

    # the source is the PDF next to the TEI
    info = paper.info.iloc[0]
    assert paper.paper_id == "to_err_is_human"
    assert info["file_name"] == "to_err_is_human.pdf"
    assert info["input_format"] == "pdf"
    assert info["sha256"] == _bibr12_sha256(pc.demofile("pdf"))
    ext = paper["extraction"]
    assert ext["producer"] == {"name": "grobid", "version": "0.9.0", "build_sha": None}
    # deliberate difference: pytacheck converted it (metacheck names itself)
    assert ext["converter"] == {"name": "pytacheck", "version": pc.__version__, "build_sha": None}
    # when Grobid extracted it: <application when="2026-05-27T08:58+0000">
    assert ext["completed_at"] == "2026-05-27T08:58:00Z"

    # 1-based ids, references before captions and footnotes
    sec, text, bib = paper.section, paper.text, paper.bib
    assert sec["section_id"].tolist() == list(range(1, len(sec) + 1))
    assert bib["bib_id"].tolist() == [1, 2, 3, 4, 5]
    assert text["text_id"].tolist() == list(range(1, len(text) + 1))
    refs = sec.loc[sec["section_type"] == "references", "section_id"].tolist()
    assert text.set_index("text_id").loc[bib["text_id"], "section_id"].tolist() == refs * 5
    assert bib["text_id"].max() < paper.figure["text_id"].min()

    # a citation resolves to the right reference
    xref = paper.xref
    cite = xref[xref["xref_type"] == "bib"]
    assert cite["contents"].tolist() == ["(Gino and Wiltermuth 2014)"]
    assert (
        bib.set_index("bib_id").loc[cite["target_id"].iloc[0], "title"]
        == "Evil Genius? How Dishonesty Can Lead to Greater Creativity"
    )

    # figures in document order: Grobid lists Figure 2 first
    fig = paper.figure
    assert fig["label"].tolist() == ["2", "1"]
    # a caption starts with its label, which Grobid's figDesc can leave out
    assert fig["caption"].iloc[1] == "Figure 1: The simulated data."
    by_id = text.set_index("text_id")
    assert by_id.loc[fig["text_id"], "text"].tolist() == fig["caption"].tolist()
    assert by_id.loc[fig["text_id"], "section_id"].isna().all()
    fig_refs = xref[xref["xref_type"] == "figure"]
    labels = fig.set_index("figure_id").loc[fig_refs["target_id"], "label"].tolist()
    assert labels == fig_refs["contents"].tolist()

    # a table, and a table reference Grobid did not link
    tab = paper.table
    assert tab["label"].tolist() == ["1"]
    assert tab["caption"].iloc[0].startswith("Table 1: The average number of mistakes")
    assert tab["contents"].iloc[0][1] == ["control", "10.90", "4.50"]
    assert xref.loc[xref["xref_type"] == "table", "target_id"].isna().all()

    # a footnote and the reference to it
    foot = paper.footnote
    assert foot["label"].tolist() == ["1"]
    assert by_id.loc[foot["text_id"].iloc[0], "text"] == (
        "Remember, this is a demo and none of this is real."
    )
    foot_refs = xref[xref["xref_type"] == "foot"]
    assert foot_refs["target_id"].tolist() == foot["footnote_id"].tolist()

    # no caption or footnote pseudo-sections
    assert not sec["section_type"].isin(["figure", "table", "foot"]).any()
    floats = {*fig["text_id"], *tab["text_id"], *foot["text_id"]}
    body = text[~text["text_id"].isin(floats)]
    assert not body["section_id"].isna().any()

    # warnings are codes and messages
    assert codes(paper) == ["METACHECK_FLOAT_SECTION_UNKNOWN"]


def test_figure_and_footnote_references_target_their_rows(tei) -> None:
    paper = _grobid_to_bibr(tei("preprint"), schema_version="12.0")
    xref, fig = paper.xref, paper.figure
    fig_refs = xref[xref["xref_type"] == "figure"]
    assert not fig_refs["target_id"].isna().any()
    labels = fig.set_index("figure_id").loc[fig_refs["target_id"], "label"].tolist()
    assert labels == fig_refs["contents"].tolist()

    paper = _grobid_to_bibr(tei("published"), schema_version="12.0")
    xref = paper.xref
    foot_refs = xref[xref["xref_type"] == "foot"]
    labels = paper.footnote.set_index("footnote_id").loc[foot_refs["target_id"], "label"]
    assert labels.tolist() == ["1", "2"]


def test_without_the_pdf_the_tei_is_the_source(tmp_path: Path) -> None:
    xml_path = tmp_path / "demo.xml"
    shutil.copy(pc.demofile("xml"), xml_path)

    paper = _grobid_to_bibr(xml_path, schema_version="12.0")
    info = paper.info.iloc[0]
    assert paper.paper_id == "demo"
    assert info["file_name"] == "demo.xml"
    assert info["input_format"] == "tei"
    assert info["sha256"] == _bibr12_sha256(xml_path)
    assert "METACHECK_SOURCE_IS_TEI" in codes(paper)


def test_without_an_extraction_time_the_conversion_time(tmp_path: Path) -> None:
    xml_path = tmp_path / "demo.xml"
    text = pc.demofile("xml").read_text(encoding="utf-8")
    xml_path.write_text(
        pc._r.sub('(<application[^>]*) when="[^"]*"', r"\1", text), encoding="utf-8"
    )

    paper = _grobid_to_bibr(xml_path, schema_version="12.0")
    completed_at = dt.datetime.strptime(
        paper["extraction"]["completed_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=dt.UTC)
    assert abs(completed_at - dt.datetime.now(dt.UTC)) < dt.timedelta(minutes=10)


def test_grobid_to_bibr_keeps_metacheck_default_output() -> None:
    # metacheck's default (schema_version = NULL) is pytacheck's schema_version=None
    paper = grobid_to_bibr(pc.demofile("xml"), None, schema_version=None)
    assert "extraction" not in paper
    assert pc._r.grepl("^grobid ", paper.info["input_format"].iloc[0])

    with pytest.raises(ValueError, match=SCHEMA_ERROR):
        grobid_to_bibr(pc.demofile("xml"), None, schema_version="11")
    with pytest.raises(ValueError, match=SCHEMA_ERROR):
        _grobid_to_bibr(pc.demofile("xml"), schema_version="11")
    with pytest.raises(ValueError, match=SCHEMA_ERROR):
        _grobid_to_bibr12(pc.demofile("xml"), 12.0)


def test_same_pdf_through_bibr_and_grobid_has_the_same_sha256(tei, fixtures_dir: Path) -> None:
    # bibr exports of the fixture PDFs (metacheck has preprint.json)
    exports = fixtures_dir / "bibr12"
    checked = 0
    for name in ("preprint", "published"):
        bibr_path = exports / f"{name}.json"
        if not bibr_path.exists():
            continue
        bibr = pc.read(bibr_path)
        grobid = _grobid_to_bibr(tei(name), schema_version="12.0")
        assert grobid.info["sha256"].iloc[0] == bibr.info["sha256"].iloc[0]
        assert grobid.info["file_name"].iloc[0] == bibr.info["file_name"].iloc[0]
        assert grobid.info["input_format"].iloc[0] == "pdf"
        assert bibr.info["input_format"].iloc[0] == "pdf"
        assert bibr["extraction"]["producer"]["name"] == "bibr"
        assert grobid["extraction"]["producer"]["name"] == "grobid"
        # both find about the same number of references
        assert abs(len(grobid.bib) - len(bibr.bib)) < 0.1 * len(bibr.bib)
        checked += 1
    assert checked

    grobid = _grobid_to_bibr(tei("preprint"), schema_version="12.0")
    bibr = pc.read(exports / "preprint.json")
    assert grobid.info["title"].iloc[0] == bibr.info["title"].iloc[0]


# -- pytacheck: bibr export schema 12.0 is the default ----------------------------------


def test_grobid_to_bibr_and_read_default_to_bibr12(tmp_path: Path) -> None:
    xml = pc.demofile("xml")
    paper = grobid_to_bibr(xml, None)
    assert paper.info["schema_version"].iloc[0] == "12.0"
    assert not same(paper, _grobid_to_bibr(xml, schema_version="12.0"))

    # read() of a TEI file: 12.0 by default, metacheck's conversion with None
    assert not same(pc.read(xml), paper)
    legacy = pc.read(xml, schema_version=None)
    assert not same(legacy, _grobid_to_bibr(xml))
    assert "schema_version" not in legacy.info.columns
    with pytest.raises(ValueError, match=SCHEMA_ERROR):
        pc.read(xml, schema_version="11.0")
    # JSON files are read as they are
    assert "schema_version" not in pc.read(pc.demofile("json")).info.columns

    # grobid_to_bibr() saves a bibr 12.0 file, named after the TEI file
    json_path = Path(grobid_to_bibr(xml, tmp_path))
    assert json_path.name == "to_err_is_human.json"
    json = orjson.loads(json_path.read_bytes())
    assert json["schema_version"] == "12.0"
    assert json["source"]["file_name"] == "to_err_is_human.pdf"
    assert json["extraction"]["producer"]["name"] == "grobid"
    assert json["extraction"]["converter"]["name"] == "pytacheck"
    assert json["extraction"]["completed_at"] == "2026-05-27T08:58:00Z"
    # the older format when asked for
    legacy_path = Path(grobid_to_bibr(xml, tmp_path / "legacy", schema_version=None))
    assert "schema_version" not in orjson.loads(legacy_path.read_bytes())


def test_the_edge_fixture() -> None:
    """tests/grobid12/fixtures/v12_edges.tei.xml (compared with metacheck in parity)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        paper = _grobid_to_bibr(EDGES, schema_version="12.0")
    assert pc.paper_validate(paper)
    ext = paper["extraction"]
    # no GROBID application: the first one names the producer
    assert ext["producer"] == {"name": "sometool", "version": "1.2", "build_sha": None}
    assert codes(paper) == [
        "METACHECK_SOURCE_IS_TEI",
        "METACHECK_FLOAT_SECTION_UNKNOWN",
        "METACHECK_XREF_TYPE_UNMAPPED",
        "METACHECK_DOI_NOT_VALID",
        "METACHECK_EQ_COMP_UNMAPPED",
    ]
    messages = [w["message"] for w in ext["warnings"]]
    assert messages[2] == (
        "2 reference(s) of Grobid type other, NA have no xref_type and were left out."
    )
    assert messages[3].startswith("4 DOI(s) are not bare DOIs (10.xxxx/...) and were left out: ")
    assert messages[4] == "2 statistic(s) with comparator <>, ≤= were left out."

    # a <ref> naming two targets is two rows; a missing target is NA
    xref = paper.xref
    cite = xref[xref["contents"] == "(Smith 2020; Jones 2021)"]
    assert cite["target_id"].tolist() == [1, 2]
    assert cite["start"].tolist() == [27, 27]  # characters, not bytes (Müller, Ångström)
    assert xref.loc[xref["contents"] == "(Missing 1999)", "target_id"].isna().all()
    assert xref.loc[xref["xref_type"] == "equation", "target_id"].isna().all()
    # printed twice in its sentence: no span
    assert xref.loc[xref["contents"] == "(Twice)", "start"].isna().all()

    fig, tab = paper.figure, paper.table
    assert fig["caption"].tolist()[:3] == [
        "Fig 3: A picture without its label.",
        "plot a shows Jones (2021) twice: Jones (2021).",
        "Figure S1. Supplementary figure.",
    ]
    assert fig["page_number"].tolist()[0] == 2
    assert fig["page_number"].isna().tolist() == [False, True, True, True]
    assert fig["text_id"].isna().tolist() == [False, False, False, True]
    assert tab["label"].tolist()[0] == "1a"
    assert tab["contents"].tolist()[1] == []
    assert paper.footnote["label"].isna().tolist() == [True, False]

    types = paper.section["section_type"].tolist()
    for t in ("acknowledgment", "data_availability", "coi", "author_contributions", "unknown"):
        assert t in types
    assert paper.eq["comp"].tolist() == ["≤", "="]
    assert paper.author["orcid"].tolist()[0] == "https://orcid.org/0000-0002-1825-009X"
    assert paper.affiliation["author_ids"].tolist() == [[1, 3], [2]]
    url = paper.url
    assert url["link_text"].tolist()[0] == "the OSF page"
    assert url["start"].isna().tolist() == [False, True]  # the href is printed twice


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        ("2026-05-27T08:58+0000", "2026-05-27T08:58:00Z"),
        ("2026-05-27T08:58:30.7+02:00", "2026-05-27T06:58:30Z"),
        ("2026-05-27T08:58:30Z", "2026-05-27T08:58:30Z"),
        ("2026-05-27", None),
        (None, None),
        ("2026-02-30T08:58+0000", None),
        ("2026-05-27T24:00+0000", "2026-05-28T00:00:00Z"),
        ("2026-05-27T08:58:60+0000", "2026-05-27T08:59:00Z"),
        ("2026-05-27T08:58+1500", None),
        ("0026-05-27T08:58+0000", "26-05-27T08:58:00Z"),
    ],
)
def test_bibr12_utc(x: str | None, expected: str | None) -> None:
    assert _bibr12_utc(x) == expected


# -- the module suite on 12.0 Grobid papers ---------------------------------------------


def _builtin_modules() -> list[str]:
    from pytacheck.module import _builtin_names

    return list(_builtin_names())


@pytest.mark.parametrize("module", _builtin_modules())
def test_every_module_runs_on_grobid12_papers(module: str) -> None:
    """module_run() of every built-in module on a 12.0 Grobid paper: no error, no mutation.

    Network requests replay metacheck's recorded API responses and LLM-backed
    modules run without an LLM. Modules that need no network or LLM are
    compared with metacheck in the grobid12 parity cases.
    """
    from pytacheck.packs.check import run_issues
    from tests.httpmock import replay

    paper = grobid_to_bibr(pc.demofile("xml"), None)
    info = pc.module_info(module)
    with replay("apis"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        issues = run_issues(info, [("demo", paper)])
    offline = [i for i in issues if i.code == "run"]
    if offline and "network" in info.requires:
        pytest.skip(f"{module} needs API responses that are not recorded: {offline[0]}")
    # metacheck's own "error" light: ref_accuracy on a paper without bib_match
    # (every Grobid conversion, 12.0 or not), reg_check when RegCheck is unreachable
    problems = [
        str(i)
        for i in issues
        if i.level == "error" and not (i.code == "traffic_light" and "'error'" in str(i))
    ]
    if any("There were no modules that matched" in p for p in problems):
        # e.g. codebook_check reads data_check's output, and data_check is not ported yet
        pytest.skip(f"{module} runs a module that is not ported yet: {problems[0]}")
    assert not problems, problems
