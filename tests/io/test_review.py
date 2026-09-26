"""Regression tests for the divergences from metacheck found in the io review.

Each test pins R behaviour that was checked against metacheck (the same
inputs are parity cases in ``parity/cases/io_review.yaml``).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import httpx
import pandas as pd
import pytest

from pytacheck.io.bibr_convert import _status_desc, format_bib_authors
from pytacheck.io.convert import convert
from pytacheck.io.corpus import _papers_release_assets, papers_available, papers_load
from pytacheck.io.grobid import (
    _grobid_to_bibr,
    _list_files,
    _restore_refs,
    convert_grobid,
    grobid_to_bibr,
)
from pytacheck.io.xml import (
    XmlParseError,
    _xml_find1_text,
    _xml_find_text,
    as_character,
    read_xml,
    xml_find_all,
    xml_ns,
)
from pytacheck.papers.model import PaperList
from tests.io.conftest import GROBID_URL, IO_FIXTURES, api

NORDS = str(IO_FIXTURES / "apis_papers_nords")


def _quiet_grobid(path: Path):  # type: ignore[no-untyped-def]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _grobid_to_bibr(path)


# ---------------------------------------------------------------------------
# .grobid_to_bibr(): tables without rows
# ---------------------------------------------------------------------------


def test_second_table_without_rows_has_no_contents() -> None:
    # metacheck: `contents[[2]] <- NULL` leaves list(<table 1>), which the tibble
    # recycles, so both tables get the first one's contents (U17)
    p = _quiet_grobid(IO_FIXTURES / "tables_empty_second.tei.xml")
    assert len(p.table) == 2
    first, second = p.table["contents"]
    assert first[0] == ["Condition", "M"]
    assert second is None


def test_first_table_without_rows_keeps_second_contents() -> None:
    p = _quiet_grobid(IO_FIXTURES / "tables_empty_first.tei.xml")
    first, second = p.table["contents"]
    assert first is None
    assert second[0] == ["Group", "SD"]


def test_one_of_three_tables_without_rows() -> None:
    # metacheck fails the paper ("must be compatible with existing data"; U17)
    p = _quiet_grobid(IO_FIXTURES / "tables_three_one_empty.tei.xml")
    contents = p.table["contents"].tolist()
    assert contents[0][0] == ["Condition", "M"]
    assert contents[1] is None
    assert contents[2] == [["x"]]


def test_references_without_any_text() -> None:
    # metacheck: "Can't combine `..1$header` <list> and `..2$header` <character>." (U17)
    p = _quiet_grobid(IO_FIXTURES / "refs_no_text.tei.xml")
    assert p.section["header"].tolist() == ["References"]
    assert p.text["text"].tolist() == ["A reference. 1999."]
    assert p.bib["text_id"].tolist() == [1]


# ---------------------------------------------------------------------------
# serialisation: namespaces, reference tokens
# ---------------------------------------------------------------------------


def test_formatted_text_has_no_inherited_namespace_declarations() -> None:
    p = _quiet_grobid(IO_FIXTURES / "xlink_ns.tei.xml")
    formatted = list(p.text["formatted"].dropna())
    assert not any('xmlns:xlink="' in f for f in formatted)
    assert any('<ptr xlink:href="https://github.com/x"/>' in f for f in formatted)
    # a declaration on the paragraph itself is kept
    assert any(f.startswith('<p xmlns:b="urn:b">') for f in formatted) or any(
        "<b:q>kept</b:q>" in f for f in formatted
    )
    assert 'xmlns:xlink="' not in p.table["html"].iloc[0]


def test_as_character_keeps_own_declarations_only() -> None:
    doc = read_xml(
        '<r xmlns:xlink="urn:x" xmlns:a="urn:a"><p xmlns:b="urn:b">x <ptr xlink:href="h"/>'
        ' <b:q>y</b:q> <a:z a:k="1">w</a:z></p><p xmlns:c="urn:c">none</p></r>'
    )
    first, second = as_character(xml_find_all(doc, "//p"))
    assert first == (
        '<p xmlns:b="urn:b">x <ptr xlink:href="h"/> <b:q>y</b:q> <a:z a:k="1">w</a:z></p>'
    )
    assert second == '<p xmlns:c="urn:c">none</p>'


def test_restore_refs_replaces_first_occurrence_of_each_token() -> None:
    tokens = {("1", "1"): "<ref>A</ref>", ("2", "1"): "<ref>B</ref>"}
    # R: sub(token, ref, fixed = TRUE) per token -> a second copy stays literal
    assert _restore_refs("{{ref1-1}} and {{ref1-1}}", tokens) == "<ref>A</ref> and {{ref1-1}}"
    assert _restore_refs("{{ref2-1}} x {{ref1-1}}", tokens) == "<ref>B</ref> x <ref>A</ref>"
    assert _restore_refs("{{ref9-9}}", tokens) == "{{ref9-9}}"


def test_literal_ref_token_in_text_survives() -> None:
    p = _quiet_grobid(IO_FIXTURES / "ref_token_literal.tei.xml")
    assert any("literal {{ref1-1}} token" in t for t in p.text["text"])
    assert any("{{ref9-9}}" in t for t in p.text["text"])


# ---------------------------------------------------------------------------
# XML parsing and XPath (xml2 semantics)
# ---------------------------------------------------------------------------


def test_non_fatal_parse_errors_warn_and_keep_document() -> None:
    with pytest.warns(UserWarning, match=r"ID x already defined \[513\]"):
        doc = read_xml('<a><b xml:id="x"/><c xml:id="x">t</c></a>')
    assert _xml_find1_text(doc, "//c") == "t"
    with pytest.warns(UserWarning, match=r"Namespace prefix xlink .* is not defined \[201\]"):
        read_xml('<a><ptr xlink:href="u"/></a>')
    with pytest.warns(UserWarning):
        p = _grobid_to_bibr(IO_FIXTURES / "nonfatal_errors.tei.xml")
    assert len(p.text) > 0


def test_fatal_parse_error_reports_first_fatal_error() -> None:
    with pytest.raises(
        XmlParseError, match=r"^Opening and ending tag mismatch: c line 1 and a \[76\]$"
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            read_xml('<a><b xml:id="x"/><c xml:id="x"></a>')
    with pytest.raises(XmlParseError, match=r"Entity 'nbsp' not defined \[26\]"):
        read_xml("<a>&nbsp;</a>")


def test_xpath_uses_document_namespaces() -> None:
    doc = read_xml(
        '<r xmlns="urn:d" xmlns:a="urn:a"><a:p>one</a:p><q xmlns:a="urn:a2"><a:p>two</a:p></q>'
        '<s xmlns="urn:e">three</s></r>'
    )
    assert _xml_find_text(doc, "//a:p") == ["one"]
    assert _xml_find_text(doc, "//a1:p") == ["two"]
    assert _xml_find_text(doc, "//d1:q | //d2:s", join="; ") == "two; three"
    # a colon inside a string literal is not a prefix
    assert _xml_find_text(doc, "//*[@k='a:b']") == [""]


def test_bad_xpath_warns_and_finds_nothing() -> None:
    doc = read_xml("<r><p>one</p></r>")
    with pytest.warns(UserWarning, match=r"Invalid expression \[1207\]"):
        assert _xml_find_text(doc, "//p[") == [""]
    with pytest.warns(UserWarning, match="Undefined namespace prefix"):
        assert _xml_find_text(doc, "//zz:p") == [""]


def test_xml_ns_matches_xml2_naming() -> None:
    doc = read_xml(
        '<r xmlns="urn:z" xmlns:b="urn:b" xmlns:a="urn:y"><q xmlns="urn:b" xmlns:a="urn:a"/>'
        '<s xmlns:b="urn:b" xmlns:c="urn:z"/><t xmlns:a="urn:y"/><u xmlns:a1="urn:q"/></r>'
    )
    assert list(xml_ns(doc).items()) == [
        ("d1", "urn:z"),
        ("d2", "urn:b"),
        ("a", "urn:y"),
        ("a2", "urn:a"),
        ("a3", "urn:y"),
        ("a1", "urn:q"),
        ("b", "urn:b"),
        ("b1", "urn:b"),
        ("c", "urn:z"),
    ]
    assert xml_ns(read_xml("<r/>")) == {}


# ---------------------------------------------------------------------------
# files and directories
# ---------------------------------------------------------------------------


def test_list_files_skips_dotfiles_and_keeps_directory_string(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_text("<a/>")
    (tmp_path / ".b.xml").write_text("<b/>")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "c.pdf").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.PDF").write_text("x")
    assert _list_files(f"{tmp_path}/", r"\.xml$") == [f"{tmp_path}//a.xml"]
    assert _list_files(str(tmp_path), r"\.pdf", recursive=True) == [f"{tmp_path}/sub/d.PDF"]


def test_grobid_to_bibr_directory_of_dotfiles_only() -> None:
    assert grobid_to_bibr(IO_FIXTURES / "dotonly") == []
    with pytest.raises(IndexError):
        grobid_to_bibr(IO_FIXTURES / "dotonly", save_path=None)


def test_grobid_to_bibr_directory_keeps_successes() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        papers = grobid_to_bibr(IO_FIXTURES, save_path=None, schema_version=None)
    assert isinstance(papers, PaperList)
    ids = papers.names
    assert "xlink_ns.tei" in ids
    assert "refs_no_text.tei" in ids  # metacheck cannot convert it (U17)


# ---------------------------------------------------------------------------
# format_bib_authors(): paste() recycling
# ---------------------------------------------------------------------------


def test_format_bib_authors_recycles_missing_columns() -> None:
    assert format_bib_authors(pd.DataFrame({"family": ["A", "B"]})) == "A, ; B, "
    assert format_bib_authors(pd.DataFrame({"given": ["x", "y"]})) == ", x; , y"
    assert format_bib_authors(pd.DataFrame({"x": [1, 2]})) == ""


def test_format_bib_authors_formats_values_like_r() -> None:
    df = pd.DataFrame(
        {"family": [1.5, 2.0, 1e5], "given": pd.array([True, False, None], dtype="boolean")}
    )
    assert format_bib_authors(df) == "1.5, TRUE; 2, FALSE; 1e+05, NA"


def test_format_bib_authors_partial_column_match() -> None:
    df = pd.DataFrame({"family_name": ["Eagly"], "given": ["Alice"]})
    with pytest.warns(UserWarning, match="Partial match of 'family' to 'family_name'"):
        assert format_bib_authors(df) == "Eagly, Alice"


# ---------------------------------------------------------------------------
# corpora without .rds assets
# ---------------------------------------------------------------------------


def test_release_assets_without_rds_is_none() -> None:
    with api(NORDS):
        assert _papers_release_assets("scienceverse/nords") is None


def test_papers_available_without_assets_is_empty() -> None:
    # metacheck: "arguments imply differing number of rows: 0, 1" (U18)
    for mock, repo in (
        (NORDS, "scienceverse/nords"),
        ("apis_papers_empty", "scienceverse/norealeases"),
    ):
        with api(mock):
            out = papers_available(repo)
        assert len(out) == 0
        assert list(out.columns) == ["name", "tag", "size_mb", "cached"]


def test_papers_load_without_assets() -> None:
    # metacheck: "argument is of length zero" (U18)
    with api(NORDS), pytest.raises(ValueError, match="'misc' not found in releases"):
        papers_load("misc", "scienceverse/nords")
    with api("apis_papers_empty"), pytest.raises(ValueError, match="not found in releases"):
        papers_load("demo", "scienceverse/norealeases")


# ---------------------------------------------------------------------------
# convert() / convert_grobid()
# ---------------------------------------------------------------------------


def test_convert_partially_matches_method(tmp_path: Path) -> None:
    import pytacheck as pc

    out = tmp_path / "out"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with api("apis"):
            json_path = convert(pc.demofile("pdf"), out, method="gro", api_url=GROBID_URL)
    # R: sub("\\.json$", ".xml", save_path) is a *file* name for convert_grobid()
    assert Path(json_path).name == "out.json"
    assert (tmp_path / "out.xml").exists()


def test_convert_without_keep_xml_removes_temp_xml(tmp_path: Path) -> None:
    import pytacheck as pc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with api("apis"):
            json_path = convert(
                pc.demofile("pdf"), tmp_path, method="grobid", keep_xml=False, api_url=GROBID_URL
            )
    assert Path(json_path).exists()
    tmp_xml = Path(__import__("tempfile").gettempdir()) / Path(json_path).name.replace(
        ".json", ".xml"
    )
    assert not tmp_xml.exists()


def test_convert_ignores_dotfiles() -> None:
    with pytest.raises(ValueError, match="No PDF, XML, DOC or DOCX files detected"):
        convert(IO_FIXTURES / "dotonly", api_url="unused")


def test_status_descriptions_follow_httr2() -> None:
    assert _status_desc(413) == "Payload Too Large"
    assert _status_desc(300) == "Multiple Choice"
    assert _status_desc(431) is None
    assert _status_desc(500) == "Internal Server Error"


def test_convert_grobid_error_uses_httr2_status_text(tmp_path: Path) -> None:
    import pytacheck as pc

    routes = {
        ("POST", f"{GROBID_URL}/api/processFulltextDocument"): lambda _r: httpx.Response(413),
    }
    with api("apis", routes=routes), pytest.raises(RuntimeError, match=r"^Payload Too Large$"):
        convert_grobid(pc.demofile("pdf"), tmp_path, api_url=GROBID_URL)


# ---------------------------------------------------------------------------
# sentence splitting: stringi drops a leading byte order mark per call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("﻿i.e x. Y", ["i.e x.", "Y"]),
        ("﻿﻿ab. Cd", ["ab.", "Cd"]),
        ("﻿﻿﻿ab", ["ab"]),
        ("﻿ ﻿ab", ["﻿ab"]),
        (" ﻿ab", ["﻿ab"]),
        ("﻿", []),
        ("a ﻿b. ﻿C", ["a ﻿b. ﻿", "C"]),
    ],
)
def test_tokenize_sentences_drops_leading_bom_like_stringi(text: str, expected: list[str]) -> None:
    from pytacheck.io.grobid import _tokenize_sentences

    assert _tokenize_sentences(text) == expected


def test_bom_paragraphs_fixture() -> None:
    p = _quiet_grobid(IO_FIXTURES / "bom_paragraphs.tei.xml")
    texts = list(p.text["text"])
    assert "First sentence." in texts
    assert "Triple mark." in texts
    assert not any(t.startswith("﻿") for t in texts if t != "﻿Mark then space.")


def test_read_rds_matrices_are_column_major_arrays() -> None:
    from pytacheck.io.corpus import _read_rds

    out = _read_rds(IO_FIXTURES / "matrices.rds")
    assert out["int"].tolist() == [[1, 3, 5], [2, 4, 6]]
    assert out["chr"].tolist() == [["a", "c"], [None, "d"]]
    assert out["dbl"].shape == (1, 3)


def test_read_rds_real_paperlists() -> None:
    from pytacheck.io.corpus import _read_rds

    papers = _read_rds(IO_FIXTURES / "psychsci_paperlist.rds")
    assert isinstance(papers, PaperList)
    assert len(papers) >= 2
    xmls = _read_rds(IO_FIXTURES / "debruine_xml_paperlist.rds")
    assert isinstance(xmls, PaperList)
    assert all(len(p.text) > 0 for p in xmls)


# ---------------------------------------------------------------------------
# read(): list.files() scan, twins and errors (parity: read.dir.*, read.list.*)
# ---------------------------------------------------------------------------

EMPTY_TEI = IO_FIXTURES / "empty_body.tei.xml"


def _tei_copies(root: Path, names: list[str]) -> None:
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(EMPTY_TEI.read_bytes())


def test_read_dir_lists_like_list_files(tmp_path: Path) -> None:
    from pytacheck.io.read import read

    _tei_copies(tmp_path, ["good.xml", ".hidden.xml", "UP.XML", "Z.xml", "sub/c.xml"])
    _tei_copies(tmp_path, [".hid/h.xml", "sub.xml"])
    # R: lower-case extensions only, no hidden files, ICU order ("Z" after "sub")
    assert read(tmp_path, schema_version=None).names == ["good", "sub", "Z"]
    # "sub.xml" sorts before "sub/c.xml" ("." < "/"); hidden directories are not searched
    assert read(tmp_path, recursive=True, schema_version=None).names == ["good", "sub", "c", "Z"]


def test_list_files_sorts_full_paths_as_r(tmp_path: Path) -> None:
    from pytacheck._r.base import r_sort_key
    from pytacheck.io._files import list_files

    (tmp_path / "b.json").write_text("{}")
    # "B.json" is a second file only where names are case-sensitive (not macOS, Windows)
    upper = not (tmp_path / "B.json").exists()
    for name in ["B.json" if upper else "", "a.json", "a/b.json", "_x.json", "e.xml", "x.JSON"]:
        if name:
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text("{}")
    rel = [p[len(str(tmp_path)) + 1 :] for p in list_files(tmp_path, r"\.(json|xml)$", True)]
    # R 4.5 list.files(d, "\\.(json|xml)$", recursive = TRUE) (ICU root collation)
    expected = ["_x.json", "a.json", "a/b.json", "b.json", "B.json", "e.xml"]
    assert rel == (expected if upper else [p for p in expected if p != "B.json"])
    assert sorted(["B.json", "b.json"], key=r_sort_key) == ["b.json", "B.json"]
    # without recursive, a directory whose name matches is listed, as in R
    (tmp_path / "dir.json").mkdir()
    rel = [p[len(str(tmp_path)) + 1 :] for p in list_files(tmp_path, r"\.json$")]
    assert rel == ["_x.json", "a.json", "b.json", "B.json", "dir.json"]


def test_read_list_drops_exact_twins_and_repeats(tmp_path: Path) -> None:
    from pytacheck.io.read import read

    _tei_copies(tmp_path, ["Z.xml", "p.xml", "q.xml"])
    probe = IO_FIXTURES.parents[2] / "upstream/metacheck/tests/testthat/fixtures/bibr12"
    for name in ["p.JSON", "q.json"]:
        (tmp_path / name).write_bytes((probe / "probe_docx.json").read_bytes())
    d = str(tmp_path)
    names = [f"{d}/{n}" for n in ["Z.xml", "p.JSON", "p.xml", "q.json", "q.xml", "Z.xml"]]
    # setdiff(): only the exact twin of a lower-case ".json" goes, and each path is
    # read once; "p.JSON" is still read as JSON (grepl(ignore.case = TRUE))
    assert read(names, schema_version=None).names == ["Z", "probe", "p", "probe"]


def test_read_dir_skips_any_unreadable_file(tmp_path: Path) -> None:
    from pytacheck.io.read import read

    _tei_copies(tmp_path, ["b.xml"])
    # a JSON file whose root is an array (R: "$ operator is invalid for atomic vectors")
    (tmp_path / "a.json").write_text("[1, 2]")
    p = read(tmp_path, schema_version=None)  # D6: logged and skipped
    assert p.paper_id == "b"
    with pytest.raises(AttributeError):
        read(tmp_path / "a.json")  # a single file raises the real error (U21)


# ---------------------------------------------------------------------------
# convert_grobid(save_path = None): the PDF is the 12.0 paper's source
# ---------------------------------------------------------------------------


def test_convert_grobid_null_save_path_names_the_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import tempfile

    demo = IO_FIXTURES.parents[2] / "upstream/metacheck/inst/demos/to_err_is_human.pdf"
    pdf = tmp_path / "My Paper.PDF"  # any name: the source keeps it
    pdf.write_bytes(demo.read_bytes())
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    with api("apis"):  # the reply is the same whatever the file name
        paper = convert_grobid(pdf, None, api_url=GROBID_URL)
    info = paper.info.iloc[0]
    assert paper.paper_id == "My Paper"
    assert (info["file_name"], info["input_format"]) == ("My Paper.PDF", "pdf")
    assert info["sha256"] == hashlib.sha256(demo.read_bytes()).hexdigest()
    codes = [w["code"] for w in paper.extraction["warnings"]]
    assert "METACHECK_SOURCE_IS_TEI" not in codes
    # the temporary folder (TEI and PDF link) is removed
    assert list(tmp.iterdir()) == []
    assert pdf.exists()


def test_read_empty_dir_messages_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from pytacheck.config import verbose
    from pytacheck.io.read import read

    old = verbose()
    try:
        verbose(True)
        assert len(read(tmp_path)) == 0
        out = capsys.readouterr()
        # metacheck's message(): stderr, silenced by verbose(FALSE)
        assert (out.out, out.err.strip()) == ("", "No JSON or XML files found.")
        verbose(False)
        assert len(read(tmp_path)) == 0
        assert capsys.readouterr().err == ""
    finally:
        verbose(old)
