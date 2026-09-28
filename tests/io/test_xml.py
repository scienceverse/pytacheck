"""Port of tests/testthat/test-svutils-xml.R, plus the xml2 emulation layer."""

from __future__ import annotations

import pytest

from pytacheck.io import xml as px


def test_xml_find_text() -> None:
    with pytest.raises(AttributeError):
        px._xml_find_text("string", "//a")

    doc = px.read_xml("<p>A <b>B1</b> and <b>B2</b></p>")
    assert px._xml_find_text(doc, "//a") == [""]
    assert px._xml_find_text(doc, "//p //b") == ["B1", "B2"]
    assert px._xml_find_text(doc, "//p //b", join="-") == "B1-B2"


def test_xml_find1_text() -> None:
    with pytest.raises(AttributeError):
        px._xml_find1_text("string", "//a")

    doc = px.read_xml("<p>A <b>B1</b> and <b>B2</b></p>")
    assert px._xml_find1_text(doc, "//p //b") == "B1"
    with pytest.raises(TypeError):
        px._xml_find1_text(doc, "//p //b", join="-")  # type: ignore[call-arg]
    assert px._xml_find1_text(doc, "//a") == ""


def test_xml_find_text_trims_and_squeezes() -> None:
    doc = px.read_xml("<p><b>  two   spaces\t</b></p>")
    # trims [[:space:]] and NBSP at the ends, collapses runs of spaces only
    assert px._xml_find_text(doc, "//b") == ["two spaces"]


def test_xml_read_grobid(fixtures_dir) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        px._xml_read_grobid("bad_arg")

    from pytacheck import demofile

    xml = px._xml_read_grobid(demofile("xml"))
    title = px.xml_find_first(xml, "//title")
    assert px.xml_attr(title, "level") == "a"
    # the TEI namespace is stripped, so unprefixed XPath works
    assert px._xml_find1_text(xml, ".//titleStmt/title").startswith("To Err is Human")


def test_xml_read_grobid_fixes(tmp_path) -> None:
    f = tmp_path / "x.xml"
    f.write_text(
        "<TEI><text><body><p>r p 2 = .1, η p 2 = .2, R 2 = .3, BF 10 = 2, "
        "see Fig. 2 and Table. 3, https:// osf.io/x, 1. 5, d z = 1</p></body></text></TEI>",
        encoding="utf-8",
    )
    text = px._xml_find1_text(px._xml_read_grobid(f), "//p")
    # the "<" of "</p>" is not an operator (metacheck: "dz =1"; U16)
    assert text == (
        "rp² = .1, ηp² = .2, R² = .3, BF10 = 2, see Fig 2 and Table 3, https://osf.io/x, 1.5, dz = 1"
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # a number before a tag keeps its space (metacheck glues it: "et al.,2015", U16)
        (
            '(<ref type="bibr">Smith et al., 2015</ref>; <ref type="bibr">Jones 1999</ref>)',
            "(Smith et al., 2015; Jones 1999)",
        ),
        ("in Study 2", "in Study 2"),
        ("t(168) = 1.11, p = .27.", "t(168) = 1.11, p = .27."),
        # a number before a text operator is still glued to it
        ("η G 2 = .20 and t 10 = 2.63", "ηG² = .20 and t10 = 2.63"),
        # omega and Hedges' g_z (metacheck: "\u03C" is "<", "\\g" matches a "g")
        ("ω p 2 = .05, g z = 0.4", "ωp² = .05, gz = 0.4"),
        ("a dgz word", "a dgz word"),
    ],
)
def test_xml_read_grobid_number_fixes(tmp_path, body: str, expected: str) -> None:
    f = tmp_path / "x.xml"
    f.write_text(f"<TEI><text><body><p>{body}</p></body></text></TEI>", encoding="utf-8")
    assert px._xml_find1_text(px._xml_read_grobid(f), "//p") == expected


def test_xml_read_grobid_unwraps_sentences(tmp_path) -> None:
    f = tmp_path / "x.xml"
    f.write_text(
        '<TEI xml:space="preserve"><text><body><div><p><s>One.</s><s>Two <ref>[1]</ref>.</s>'
        "</p></div></body></text></TEI>",
        encoding="utf-8",
    )
    xml = px._xml_read_grobid(f)
    assert px.xml_find_all(xml, "//s") == []
    # one space between two sentences, and no indentation in the paragraph (U28)
    assert px.as_character(px.xml_find_first(xml, "//p")) == "<p>One. Two <ref>[1]</ref>.</p>"


def test_as_character_node_and_missing() -> None:
    doc = px.read_xml(
        '<a xmlns="http://x" xmlns:q="http://q"><c><d>t</d><e/></c><f>&amp;&lt;"é</f></a>'
    )
    px._strip_default_namespaces(doc)
    # xmlSaveTree(format): indentation for element-only content, no ancestor xmlns
    assert px.as_character(px.xml_find_first(doc, "//c")) == "<c>\n  <d>t</d>\n  <e/>\n</c>"
    assert px.as_character(px.xml_find_first(doc, "//f")) == '<f>&amp;&lt;"é</f>'
    assert px.as_character(None) is None
    assert px.as_character(doc).startswith('<?xml version="1.0" encoding="UTF-8"?>\n<a xmlns:q')


def test_xml_attr_matches_local_name() -> None:
    doc = px.read_xml('<a><b xml:id="b0"/><c/></a>')
    assert px.xml_attr(px.xml_find_first(doc, "//b"), "id") == "b0"
    assert px.xml_attr(px.xml_find_first(doc, "//c"), "id") is None
    assert px.xml_attr(None, "id") is None


def test_html_text() -> None:
    src = '<p> A <ref type="url" target="x">B &amp; C</ref> <head n="1">T</head> <p>'
    # read_html() defaults drop blank text nodes (NOBLANKS) ...
    assert px.html_text(src, noblanks=True) == " A B & CT "
    # ... the options .process_full_text() uses keep them
    assert px.html_text(src) == " A B & C T "
