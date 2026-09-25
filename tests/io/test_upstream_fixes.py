"""Grobid TEI conversion: metacheck bugs pytacheck fixes (docs/UPSTREAM_ISSUES.md).

The parity cases of these inputs are known divergences (``kind: r_bug_fixed``),
so the fixed behaviour is pinned here: U16 (the TEI clean-up), U17 (TEI that
fails or is mis-read), U27 (whitespace in 12.0 body text) and U28 (URLs,
Grobid's sentence tags, ORCIDs).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import pytacheck as pc
from pytacheck.io.grobid import _grobid_to_bibr, _join_doi, _print_hrefs, _prints_url
from tests.io.conftest import IO_FIXTURES

UPSTREAM = IO_FIXTURES.parents[2] / "upstream" / "metacheck"
FIXTURES = UPSTREAM / "tests" / "testthat" / "fixtures"
GROBID12 = IO_FIXTURES.parents[1] / "grobid12" / "fixtures"


def convert(path: Path, schema_version: str | None = None) -> pc.Paper:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _grobid_to_bibr(path, None, schema_version)


def tei(tmp_path: Path, body: str, back: str = "", header: str = "") -> Path:
    path = tmp_path / "x.tei.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<TEI xml:space="preserve" xmlns="http://www.tei-c.org/ns/1.0">'
        f"<teiHeader><fileDesc><titleStmt><title>T</title></titleStmt>{header}</fileDesc>"
        f"</teiHeader><text><body>{body}</body><back>{back}</back></text></TEI>",
        encoding="utf-8",
    )
    return path


def texts(p: pc.Paper) -> list[str]:
    return [str(t) for t in p.text["text"].tolist()]


# -- U16: the clean-up of the raw TEI ---------------------------------------------------


@pytest.mark.parametrize("schema_version", [None, "12.0"])
def test_numbers_before_a_tag_keep_their_space(schema_version: str | None) -> None:
    # metacheck: "Rhodes et al.1999", "Bornstein1989"
    body = texts(convert(FIXTURES / "debruine" / "debruine-sex.xml", schema_version))
    joined = " ".join(body)
    assert "Rhodes et al. 1999" in joined
    assert "Rhodes et al.1999" not in joined
    assert "experiment 1." in joined
    # metacheck: "p =.27." (the full stop before </p>)
    psych = " ".join(texts(convert(FIXTURES / "problems" / "0956797617737129.xml", schema_version)))
    assert "t(168) = 1.11, p = .27." in psych
    assert "G*Power 3.1" in psych


def test_paper_doi_comes_from_the_header() -> None:
    # metacheck: the demo's first reference DOI ("10.32614/10.5281/zenodo.2669586")
    assert convert(pc.demofile("xml")).info["doi"].tolist() == [""]
    assert convert(FIXTURES / "formats" / "preprint.pdf.tei.xml").info["doi"].tolist() == [""]
    p = convert(FIXTURES / "problems" / "0956797617737129.xml")
    assert p.info["doi"].tolist() == ["10.1177/0956797617737129"]


def test_reference_dois_broken_by_line_breaks_are_joined() -> None:
    assert (
        _join_doi("https://doi.org/10.1037/0022- 3514.91.2.295", "10.1037/0022-3514.91.2.295")
        == "https://doi.org/10.1037/0022-3514.91.2.295"
    )
    assert _join_doi("doi:10.1037/0003-066X .54", "10.1037/0003-066x.54") == (
        "doi:10.1037/0003-066X.54"
    )
    assert _join_doi("Nature, 378, 669.", "") == "Nature, 378, 669."
    # the DOI's line break in a real reference (Grobid's raw text keeps it)
    refs = " ".join(texts(convert(FIXTURES / "formats" / "published.pdf.tei.xml")))
    assert "https://doi.org/10.1080/02699930802613828" in refs
    assert "https://doi.org/10.1037/0033-2909.133.4.694" in refs
    refs = " ".join(texts(convert(FIXTURES / "problems" / "0956797617737129.xml")))
    assert "doi:10.1037/0033-2909.103.2.193" in refs


# -- U17: TEI metacheck fails on or mis-reads ------------------------------------------


def test_nested_divs_do_not_repeat_paragraphs() -> None:
    p = convert(IO_FIXTURES / "edge_body.tei.xml")
    assert texts(p).count("Participants (N = 40) were tested.") == 1
    assert "Nested Method" in p.section["header"].tolist()


def test_caption_and_note_divs_are_not_body_divs(tmp_path: Path) -> None:
    # Grobid's sentence segmentation writes <figDesc><div><p><s>: the caption is read
    # once, as the figure's row (metacheck also reads it as a body section)
    body = (
        "<div><head>Intro</head><p>Text.</p></div>"
        '<figure xml:id="fig_0"><head>Figure 1</head><figDesc><div><p><s>A caption.</s>'
        "</p></div></figDesc></figure>"
        '<note place="foot" xml:id="foot_0"><div><p>A note.</p></div></note>'
    )
    assert texts(convert(tei(tmp_path, body))) == ["Text.", "A caption.", "A note."]
    # a div in a figure but outside its caption is still read
    body = (
        "<div><head>Intro</head><p>Text.</p>"
        '<figure xml:id="fig_0"><figDesc>A caption.</figDesc><div><p>Inside.</p></div>'
        "</figure></div>"
    )
    assert texts(convert(tei(tmp_path, body))) == ["Text.", "Inside.", "A caption."]


def test_figure_rows_only_for_figure_sections() -> None:
    p = convert(IO_FIXTURES / "edge_body.tei.xml")
    # metacheck adds a figure and a table row (section_id NA) per untyped section
    assert not p.figure["section_id"].isna().any()
    assert not p.table["section_id"].isna().any()
    types = dict(zip(p.section["section_id"], p.section["section_type"], strict=True))
    assert all(types[s] == "figure" for s in p.figure["section_id"])
    assert all(types[s] == "table" for s in p.table["section_id"])


def test_first_div_of_figure_sentences_is_a_figure() -> None:
    # two sentences start with "Figure N" (metacheck only fires for exactly one)
    p = convert(IO_FIXTURES / "figure_twice.tei.xml")
    assert p.section["section_type"].tolist()[0] == "figure"


def test_back_matter_outside_an_inner_div(tmp_path: Path) -> None:
    back = (
        '<div type="availability"><head>Data availability</head>'
        "<p>Data are on the OSF.</p></div>"
        '<div type="acknowledgement"><div><head>Acknowledgements</head>'
        "<p>We thank A.</p></div></div>"
    )
    p = convert(tei(tmp_path, "<div><head>Intro</head><p>Text.</p></div>", back))
    assert texts(p) == ["Text.", "Data are on the OSF.", "We thank A."]
    assert p.section["section_type"].tolist() == ["intro", "availability", "acknowledgement"]


@pytest.mark.parametrize("schema_version", [None, "12.0"])
def test_typed_div_inside_a_typed_div_is_read_once(
    tmp_path: Path, schema_version: str | None
) -> None:
    # read with the outer div, as metacheck reads it (not a second time for its own type)
    back = (
        '<div type="annex"><div><head>Appendix A</head><p>Annex text.</p></div>'
        '<div type="acknowledgement"><head>Ack</head><p>We thank A.</p></div></div>'
    )
    p = convert(tei(tmp_path, "<div><head>Intro</head><p>Text.</p></div>", back), schema_version)
    assert texts(p) == ["Text.", "Annex text.", "We thank A."]


def test_each_reference_has_its_own_raw_text(tmp_path: Path) -> None:
    refs = "".join(
        f'<biblStruct xml:id="b{i}"><monogr><title level="j">J</title><imprint>'
        f'<date type="published" when="200{i}">200{i}</date></imprint></monogr>{note}'
        "</biblStruct>"
        for i, note in enumerate(
            ['<note type="raw_reference">First. 2000.</note>', "", "<note>other</note>"]
        )
    )
    back = f'<div type="references"><listBibl>{refs}</listBibl></div>'
    p = convert(tei(tmp_path, "<div><head>Intro</head><p>Text.</p></div>", back))
    # metacheck gives every reference "First. 2000." (one note is recycled)
    assert texts(p)[1:] == ["First. 2000.", "", ""]
    assert p.bib["text_id"].tolist() == [2, 3, 4]


def test_page_range_with_only_an_end(tmp_path: Path) -> None:
    ref = (
        '<biblStruct xml:id="b0"><monogr><title level="j">J</title><imprint>'
        '<biblScope unit="page" to="99"/></imprint></monogr>'
        '<note type="raw_reference">J, 99.</note></biblStruct>'
    )
    back = f'<div type="references"><listBibl>{ref}</listBibl></div>'
    p = convert(tei(tmp_path, "<div><p>Text.</p></div>", back))
    assert p.bib["last_page"].tolist() == ["99"]
    assert "first_page" not in p.bib.columns or p.bib["first_page"].isna().all()


# -- U28: URLs ----------------------------------------------------------------------------

URL_BODY = (
    '<div><head>Methods</head><p>Data are on <ref type="url" target="https://osf.io/abc">'
    "osf</ref>.</p><p>The osf team made osfx.</p>"
    '<p>See <ref type="url" target="https://github.com/a/b">the GitHub repo</ref> and '
    '<ref type="url" target="https://osf.io/q2">osf.io/ q2</ref>.</p>'
    '<p>A <ref type="url">link without target</ref> here.</p></div>'
)
URL_BACK = (
    '<div type="references"><listBibl><biblStruct xml:id="b0"><monogr>'
    '<title level="j">J</title></monogr><note type="raw_reference">The osf book.</note>'
    "</biblStruct></listBibl></div>"
)


@pytest.mark.parametrize("schema_version", [None, "12.0"])
def test_urls_are_printed_where_their_link_is(tmp_path: Path, schema_version: str | None) -> None:
    p = convert(tei(tmp_path, URL_BODY, URL_BACK), schema_version)
    assert texts(p)[:5] == [
        # a link in a word keeps the word (metacheck: "Data are on https://osf.io/abc.")
        "Data are on osf https://osf.io/abc.",
        # metacheck replaces "osf" in every row: "The https://osf.io/abc team made ..."
        "The osf team made osfx.",
        # the words of a link are kept (metacheck: "See https://github.com/a/b and ...")
        "See the GitHub repo https://github.com/a/b and https://osf.io/q2.",
        # metacheck: NA for every row printing the link text of a URL without target
        "A link without target here.",
        "The osf book.",
    ]


def test_print_hrefs_does_not_replace_inside_replacements() -> None:
    # metacheck: "https://https://osf.io/q2.io/q2" (gsub("osf", ...) after gsub("osf.io/q2", ...))
    out = _print_hrefs(
        ["see osf.io/q2 and osf"],
        [1],
        ["osf.io/q2", "osf"],
        ["https://osf.io/q2", "https://osf.io"],
        [1, 1],
    )
    assert out == ["see https://osf.io/q2 and osf https://osf.io"]
    assert _prints_url("osf  .io/abc", "https://osf.io/abc")
    assert _prints_url("https:// osf.io", "https://osf.io/abc")
    # words, also when the URL contains them: they are kept, with the href after them
    assert not _prints_url("OSF", "https://osf.io/abc")
    assert not _prints_url("code", "https://github.com/lab/code")
    assert not _prints_url("the GitHub repo", "https://github.com/a/b")


def test_print_hrefs_prints_each_url_at_its_own_link() -> None:
    # the link is the second "OSF" (the first is inside "OSFX", which stays a word)
    out = _print_hrefs(
        ["The OSFX project is on OSF."], [1], ["OSF"], ["https://osf.io/abc"], [1], [1]
    )
    assert out == ["The OSFX project is on OSF https://osf.io/abc."]
    # the punctuation printed with a URL stays, the href's own is not doubled
    out = _print_hrefs(
        ["See osf.io/xyz/; and (en.wikipedia.org/wiki/X_(Y))."],
        [1],
        ["osf.io/xyz/;", "(en.wikipedia.org/wiki/X_(Y))."],
        ["https://osf.io/xyz/", "https://en.wikipedia.org/wiki/X_(Y)"],
        [1, 1],
    )
    assert out == ["See https://osf.io/xyz/; and (https://en.wikipedia.org/wiki/X_(Y))."]


def test_print_hrefs_does_not_print_a_url_twice() -> None:
    # a doi: link text prints the DOI's URL; words that print the whole URL keep it
    # (without the stray spaces) and get no second copy of the href
    out = _print_hrefs(
        ["See doi:10.1234/abc and OSF (osf .io/xyz) and www.osf.io/q2 here."],
        [1],
        ["doi:10.1234/abc", "OSF (osf .io/xyz)", "www.osf.io/q2 here"],
        ["https://doi.org/10.1234/abc", "https://osf.io/xyz/", "https://osf.io/q2"],
        [1, 1, 1],
    )
    assert out == ["See https://doi.org/10.1234/abc and OSF (osf.io/xyz) and www.osf.io/q2 here."]
    # a longer URL in the words is not the link's URL: the href is printed after them
    out = _print_hrefs(
        ["At osf.io/abcdef now."], [1], ["osf.io/abcdef now"], ["https://osf.io/abc"], [1]
    )
    assert out == ["At osf.io/abcdef now https://osf.io/abc."]


@pytest.mark.parametrize("schema_version", [None, "12.0"])
def test_relative_links_are_printed_as_in_the_paper(schema_version: str | None) -> None:
    # Grobid resolves "osf  .io/6b4ag" against its own temp folder; metacheck prints
    # "file://localhost/opt/grobid/grobid-home/tmp/osf.io/6b4ag" in the text
    p = convert(FIXTURES / "problems" / "0956797617737129.xml", schema_version)
    body = " ".join(texts(p))
    assert "can be accessed at osf.io/6b4ag." in body
    assert "file://" not in body
    # the preregistration links keep the ";" between them
    assert "565fb3678c5e4a66b5582f67; Study 3: http://www.osf.io/zmf4c" in body


@pytest.mark.parametrize("schema_version", [None, "12.0"])
def test_grobid_sentence_tags_are_split(schema_version: str | None) -> None:
    # metacheck keeps each paragraph as one row ("First abstract sentence.   Second ...")
    body = texts(convert(GROBID12 / "review_sentences.tei.xml", schema_version))
    assert body[:2] == ["First abstract sentence.", "Second one cites [1]."]
    assert "We tested t(28) = 2.20, p = .036." in body
    assert not any("   " in t for t in body)


def test_first_orcid_is_kept() -> None:
    # "see 0000-0001-2345-6789 and 0000-0002-2345-6789": metacheck keeps the last
    p = convert(GROBID12 / "review_header.tei.xml", "12.0")
    assert p.author["orcid"].tolist()[1] == "https://orcid.org/0000-0001-2345-6789"


# -- U27: 12.0 body text whitespace -----------------------------------------------------


def test_bibr12_body_text_is_squished(tmp_path: Path) -> None:
    body = (
        '<div><head>Intro</head><p>Two   spaces and\n a  <ref type="bibr" target="#b0">'
        "(Smith,\n 2020)</ref> ref.</p></div>"
    )
    back = (
        '<div type="references"><listBibl><biblStruct xml:id="b0"><monogr>'
        '<title level="j">J</title></monogr><note type="raw_reference">Smith 2020.</note>'
        "</biblStruct></listBibl></div>"
    )
    p = convert(tei(tmp_path, body, back), "12.0")
    assert texts(p)[0] == "Two spaces and a (Smith, 2020) ref."
    xref = p.xref
    assert xref["contents"].tolist() == ["(Smith, 2020)"]
    assert xref["start"].tolist() == [17]  # found in the sentence


# -- U11: ids of papers made without one ---------------------------------------------


def test_paper_ids_are_unique_in_one_clock_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(time, "time", lambda: 1700000000.0)  # a clock that does not move
    ids = {pc.test_paper(["A."]).paper_id for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 14 for i in ids)
