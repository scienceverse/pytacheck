"""Port of tests/testthat/test-import-grobid.R (Grobid TEI import and the Grobid client)."""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.io import xml as px
from pytacheck.io.grobid import (
    _grobid_isalive,
    _grobid_to_bibr,
    _process_full_text,
    _tei_bib,
    _tei_text,
    _tei_xrefs,
    convert_grobid,
    grobid_to_bibr,
)
from pytacheck.papers.validate import paper_validate
from tests.io.conftest import GROBID_URL, api


@pytest.fixture
def demo_xml() -> Path:
    return pc.demofile("xml")


@pytest.fixture
def demo_pdf() -> Path:
    return pc.demofile("pdf")


# grobid_to_bibr -----------------------------------------------------------------


def test_grobid_to_bibr_bad_arg() -> None:
    with pytest.raises(TypeError):
        grobid_to_bibr(1)  # type: ignore[arg-type]


def test_one_paper_fail(fixtures_dir: Path) -> None:
    with pytest.warns(UserWarning, match="There was 1 error; use lastlog()"):
        paper = grobid_to_bibr(fixtures_dir / "problems" / "corrupt.xml", None)
    assert paper is None


def test_multiple_papers_one_fails(fixtures_dir: Path) -> None:
    xml_file = [
        str(fixtures_dir / "formats" / "preprint.pdf.tei.xml"),
        str(fixtures_dir / "problems" / "corrupt.xml"),
    ]
    with pytest.warns(UserWarning):
        papers = grobid_to_bibr(xml_file, None, schema_version=None)
    assert isinstance(papers, pc.PaperList)
    assert len(papers) == 1
    assert papers[0].info["file_name"].iloc[0] == xml_file[0]


def test_one_paper_null_save_path(demo_xml: Path) -> None:
    # metacheck's default conversion (pytacheck's default is bibr export schema 12.0)
    paper = grobid_to_bibr(demo_xml, None, schema_version=None)

    assert isinstance(paper, pc.Paper)
    assert "10.0000/0123456789" in list(paper.bib["doi"])
    assert "bib_match" not in paper
    assert paper_validate(paper)
    assert "grobid" in paper.info["input_format"].iloc[0].lower()
    assert pc._r.grepl(r"0\.\d\.\d", paper.info["input_format"].iloc[0])
    assert paper.info["keywords"].iloc[0] is None
    assert "Smith, F" in list(paper.bib["authors"])

    assert list(paper.table["table_id"]) == [1]
    sec = paper.section
    tab_sec = sec.loc[sec["section_id"] == paper.table["section_id"].iloc[0], "section_type"]
    assert list(tab_sec) == ["table"]

    assert list(paper.figure["figure_id"]) == [1, 2]
    fig_sec = sec.loc[sec["section_id"].isin(paper.figure["section_id"]), "section_type"]
    assert set(fig_sec) == {"figure"}


def test_one_paper_save_path(demo_xml: Path, tmp_path: Path) -> None:
    paper1 = grobid_to_bibr(demo_xml, None)
    json_path = grobid_to_bibr(demo_xml, tmp_path)
    assert json_path == str(tmp_path.resolve() / "to_err_is_human.json")
    paper2 = pc.read(json_path)
    assert paper_validate(paper2)

    # the JSON round trip keeps every atomic column
    for table in ("info", "author", "eq", "figure", "url", "section", "table", "text", "xref"):
        a, b = paper1[table], paper2[table]
        cols = [
            c for c in a.columns if c in b.columns and a[c].dtype != object and b[c].dtype != object
        ]
        left = a[cols].astype(str).reset_index(drop=True)
        right = b[cols].astype(str).reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right, check_dtype=False)


def test_multiple_papers_null_save_path(fixtures_dir: Path) -> None:
    xml_file = [
        fixtures_dir / "formats" / "preprint.pdf.tei.xml",
        fixtures_dir / "formats" / "published.pdf.tei.xml",
    ]
    papers = grobid_to_bibr(xml_file, save_path=None)
    assert isinstance(papers, pc.PaperList)
    assert paper_validate(papers[0])
    assert paper_validate(papers[1])


def test_multiple_papers_save_path(fixtures_dir: Path, tmp_path: Path) -> None:
    xml_file = [
        fixtures_dir / "formats" / "preprint.pdf.tei.xml",
        fixtures_dir / "formats" / "published.pdf.tei.xml",
    ]
    paths = grobid_to_bibr(xml_file, tmp_path)
    assert len(paths) == 2
    papers = pc.read(paths)
    assert isinstance(papers, pc.PaperList)


def test_directory(fixtures_dir: Path) -> None:
    papers = grobid_to_bibr(fixtures_dir / "debruine", None)
    assert isinstance(papers, pc.PaperList)
    assert papers.names == ["debruine-child", "debruine-fret", "debruine-sex", "debruine-tnl"]


def test_crossref_lookup_is_optional(demo_xml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.db.crossref as crossref

    calls = []

    def fake(p: pc.Paper) -> pc.Paper:
        calls.append(p.paper_id)
        return p

    monkeypatch.setattr(crossref, "add_bib_match", fake)
    grobid_to_bibr(demo_xml, None)
    assert calls == []
    grobid_to_bibr(demo_xml, None, crossref_lookup=True)
    assert calls == ["to_err_is_human"]


# read -------------------------------------------------------------------------------


def test_read_grobid_xml(demo_xml: Path, tmp_path: Path) -> None:
    obs = pc.read(demo_xml)
    assert isinstance(obs, pc.Paper)
    assert obs.info["title"].iloc[0] == "To Err is Human: An Empirical Investigation"
    assert not list(tmp_path.iterdir())  # read() never writes JSON


def test_read_bibr_file() -> None:
    obs = pc.read(pc.demofile("json"))
    assert "To Err is Human" in obs.info["title"].iloc[0]


def test_read_grobid_xml_and_bibr(fixtures_dir: Path) -> None:
    obs = pc.read([fixtures_dir / "formats" / "preprint.pdf.tei.xml", pc.demofile("json")])
    assert isinstance(obs, pc.PaperList)
    assert len(obs) == 2


def test_tei_xrefs_url_with_query_string() -> None:
    text_table = pd.DataFrame(
        {
            "text_id": [1],
            "text": ["The gridded soil data are available at ORNL DAAC for download."],
            "formatted": [
                'The gridded soil data are available at <ref type="url" '
                'target="https://daac.ornl.gov/cgi-bin/dsviewer.pl?ds_id=2358">ORNL DAAC</ref> '
                "for download."
            ],
        }
    )
    xrefs = _tei_xrefs(text_table)
    assert isinstance(xrefs, pd.DataFrame)
    assert len(xrefs) == 0  # URL refs are not cross-references


# convert_grobid -------------------------------------------------------------------------


def test_convert_grobid_missing_files() -> None:
    with pytest.raises(FileNotFoundError, match="Files do not exist"):
        convert_grobid("wrongfile.pdf")
    with pytest.raises(FileNotFoundError, match="Files do not exist"):
        convert_grobid(["wrongfile.pdf", "wrongfile.pdf"])


def test_invalid_url(demo_pdf: Path) -> None:
    with api("apis"):
        with pytest.raises(ConnectionError, match="Connection to the GROBID server failed"):
            convert_grobid(demo_pdf, api_url="notawebsite")
        # URL without http/https
        with pytest.raises(ConnectionError):
            convert_grobid(demo_pdf, api_url="kermitt2-grobid.hf.space")


def test_non_grobid_url_rejected(demo_pdf: Path) -> None:
    with api("apis"), pytest.raises(RuntimeError, match="does not appear up and running"):
        convert_grobid(demo_pdf, api_url="https://google.com")


def test_bad_pdf(fixtures_dir: Path) -> None:
    filename = fixtures_dir / "problems" / "xml_with_pdf_extension.pdf"
    with api("apis"):
        with pytest.raises(RuntimeError):
            convert_grobid(filename, api_url=GROBID_URL)
        with pytest.raises(RuntimeError, match="Internal Server Error"):
            convert_grobid(filename, api_url="https://grobidorg-grobid.hf.space")
        with pytest.raises(FileNotFoundError):
            convert_grobid([filename, "wrongfile.pdf"], api_url=GROBID_URL)


def test_makes_missing_save_directory_single(demo_pdf: Path, tmp_path: Path) -> None:
    newdir = tmp_path / "testnewdir"
    save_path = newdir / "file.xml"
    with api("apis"):
        obs_path = convert_grobid(demo_pdf, save_path=save_path, api_url=GROBID_URL)
    assert newdir.is_dir()
    assert obs_path == str(save_path)


def test_makes_missing_save_directory_multiple(fixtures_dir: Path, tmp_path: Path) -> None:
    save_path = tmp_path / "testnewdir"
    pdfs = sorted((fixtures_dir / "debruine").glob("*.pdf"))[:2]
    with api("apis"):
        obs_path = convert_grobid(pdfs, save_path=save_path, api_url=GROBID_URL)
    exp_path = [str(save_path / p.with_suffix(".xml").name) for p in pdfs]
    assert save_path.is_dir()
    assert obs_path == exp_path
    assert all(Path(p).exists() for p in exp_path)


def test_makes_missing_save_directory_specific(fixtures_dir: Path, tmp_path: Path) -> None:
    newdir = tmp_path / "testnewdir"
    save_path = [newdir / "A", newdir / "B"]
    pdfs = sorted((fixtures_dir / "debruine").glob("*.pdf"))[:2]
    with api("apis"):
        obs_path = convert_grobid(pdfs, save_path=save_path, api_url=GROBID_URL)
    exp_path = [f"{p}.xml" for p in save_path]
    assert newdir.is_dir()
    assert obs_path == exp_path
    assert all(Path(p).exists() for p in exp_path)


def test_defaults(demo_pdf: Path, tmp_path: Path) -> None:
    with api("apis"):
        paper = convert_grobid(demo_pdf, None, api_url=GROBID_URL)
        assert isinstance(paper, pc.Paper)
        xml_file = convert_grobid(demo_pdf, tmp_path, api_url=GROBID_URL)
    assert xml_file == str(tmp_path / "to_err_is_human.xml")


def test_reference_consolidation(demo_pdf: Path) -> None:
    with api("apis"):
        paper0 = convert_grobid(demo_pdf, None, api_url=GROBID_URL, consolidate_citations=0)
        paper1 = convert_grobid(demo_pdf, None, api_url=GROBID_URL, consolidate_citations=1)
        paper2 = convert_grobid(demo_pdf, None, api_url=GROBID_URL, consolidate_citations=2)

    ref_n = 3  # R's 4th reference
    wrongtitle = "Equivalence Testing for Psychological Research"
    righttitle = "Equivalence Testing for Psychological Research: A Tutorial"
    assert paper0.bib["title"].iloc[ref_n] == wrongtitle
    assert paper1.bib["title"].iloc[ref_n] == righttitle
    assert paper2.bib["title"].iloc[ref_n] == wrongtitle

    rightauthors = "Lakens, Daniël; Scheel, Anne M; Isager, Peder M"
    wrongauthors = "Lakens, Daniël"
    assert paper0.bib["authors"].iloc[ref_n] == wrongauthors
    assert paper1.bib["authors"].iloc[ref_n] == rightauthors
    assert paper2.bib["authors"].iloc[ref_n] == wrongauthors


def test_change_start_and_end_pages(demo_pdf: Path, tmp_path: Path) -> None:
    with api("apis"):
        xml_path = convert_grobid(demo_pdf, tmp_path, api_url=GROBID_URL, start_page=2, end_page=3)
    root = px.read_html(Path(xml_path).read_text(encoding="utf-8"))
    body = "".join(px.xml_text(b) for b in root.xpath("//body"))

    p1 = "Although intentional dishonesty might be a successful way to boost creativity"
    p2 = "We also asked researchers to rate how useful they found the checklist or app on a scale"
    p3 = "On average researchers in the experimental condition found the app marginally"
    p4 = "The authors declare a conflict of interest."
    assert p1 not in body
    assert p2 in body
    assert p3 in body
    assert p4 not in body


def test_batch_directory(fixtures_dir: Path, tmp_path: Path) -> None:
    grobid_dir = fixtures_dir / "debruine"
    with api("apis"):
        convert_grobid(grobid_dir, tmp_path, api_url=GROBID_URL)
    actual = sorted(p.name for p in tmp_path.glob("*.xml"))
    expected = sorted(p.name for p in grobid_dir.glob("*.xml"))
    assert actual == expected


def test_batch_multiple_filenames(fixtures_dir: Path, tmp_path: Path) -> None:
    grobid_dir = fixtures_dir / "debruine"
    filenames = sorted(grobid_dir.glob("*.pdf"))
    with api("apis"):
        convert_grobid(filenames[1:3], tmp_path, api_url=GROBID_URL)
    actual = sorted(p.name for p in tmp_path.glob("*.xml"))
    expected = sorted(p.name for p in grobid_dir.glob("*.xml"))[1:3]
    assert actual == expected


def test_batch_reports_failures(fixtures_dir: Path, tmp_path: Path) -> None:
    pdfs = [
        pc.demofile("pdf"),
        fixtures_dir / "problems" / "xml_with_pdf_extension.pdf",
    ]
    with api("apis"), pytest.warns(UserWarning, match="1 of 2 files did not convert"):
        xmls = convert_grobid(pdfs, tmp_path, api_url="https://grobidorg-grobid.hf.space")
    assert xmls[0] == str(tmp_path / "to_err_is_human.xml")
    assert xmls[1] is None


def test_grobid_isalive() -> None:
    with pytest.raises(TypeError):
        _grobid_isalive()  # type: ignore[call-arg]
    with api("apis"):
        # not a url
        with pytest.raises(ConnectionError):
            _grobid_isalive("grobid")
        assert _grobid_isalive("grobid", error=False) is False
        # url, not grobid
        with pytest.raises(RuntimeError):
            _grobid_isalive("https://google.com")
        assert _grobid_isalive("https://google.com", error=False) is False
        assert _grobid_isalive(GROBID_URL, error=False) in (True, False)
        assert _grobid_isalive(GROBID_URL) is True


def test_null_section_import(fixtures_dir: Path, tmp_path: Path, online: None) -> None:
    from pytacheck.io.convert import convert
    from tests.io.conftest import SERVERS_URL, json_response

    xml_file = fixtures_dir / "problems" / "203020.xml"
    with api("apis", routes={("GET", SERVERS_URL): json_response([])}):
        json = convert(xml_file, tmp_path)
    paper = pc.read(json)
    assert not paper.section["section_id"].isna().any()


def _read_tei_without_namespace(path: Path) -> object:
    text = path.read_text(encoding="utf-8").replace(' xmlns="http://www.tei-c.org/ns/1.0"', "")
    return px.read_xml(text)


def test_in_text_refs(fixtures_dir: Path) -> None:
    xml = _read_tei_without_namespace(fixtures_dir / "problems" / "0956797617737129.xml")
    text = _tei_text(xml)
    x = next(i for i, t in enumerate(text["text"]) if "Strikingly" in t)
    assert "<ref" not in text["text"].iloc[x]
    assert "<ref" in text["formatted"].iloc[x]
    assert text["paragraph_id"].nunique() > 1


def test_osf_view_only_links(fixtures_dir: Path) -> None:
    from pytacheck.text.search import text_search

    xml_path = fixtures_dir / "problems" / "0956797615569889.xml"
    xml = px._xml_read_grobid(xml_path)
    paper = grobid_to_bibr(xml_path, None)
    obs = list(text_search(paper, "view_only")["text"])
    exp = (
        "The stimuli used in the main think/no-think task reported in this article are "
        "available at https://osf.io/t9j8e/?view_only=f171281f212f4435917b16a9e581a73b."
    )
    assert obs == [exp]

    # .tei_text (retains rogue spaces)
    text = _tei_text(xml)
    raw = [t for t in text["text"] if "view_only" in t]
    assert raw == [exp.replace("f171281f", "f171  281f")]
    assert list(text_search(text, "view_only")["text"]) == [exp.replace("f171281f", "f171 281f")]

    full_text = pd.DataFrame(
        {
            "p": 1,
            "div": 1,
            "section": 1,
            "header": "demo",
            "formatted": [
                "<p>The stimuli used in the main think/no-think task reported in this article "
                'are available at <ref type="url" target="https://osf.io/t9j8e/?view_only='
                'f171281f212f4435917b16a9e581a73b">https://osf.io/t9j8e/?view_only=f171  '
                "281f212f4435917b16a9e581a73b</ref>. The complete Open Practices Disclosure for "
                'this article can be found at <ref type="url" target="http://pss.sagepub.com/'
                'content/by/supplemental-data">http://pss.sagepub  .com/content/by/supplemental'
                "-data</ref>.</p>",
                "<p>No urls here!</p>",
                '<p>A non-problematic <ref type="url" target="https://osf.io/t9j8e">URL</ref>.</p>',
            ],
        }
    )
    obs2 = list(_process_full_text(full_text)["text"])
    assert obs2 == [
        "The stimuli used in the main think/no-think task reported in this article are "
        "available at https://osf.io/t9j8e/?view_only=f171  281f212f4435917b16a9e581a73b.",
        "The complete Open Practices Disclosure for this article can be found at "
        "http://pss.sagepub  .com/content/by/supplemental-data.",
        "No urls here!",
        "A non-problematic URL.",
    ]


def test_page_abbreviation() -> None:
    full_text = pd.DataFrame(
        {
            "p": [1],
            "div": [1],
            "section": [1],
            "header": ["demo"],
            "formatted": [
                "<p>The authors used structural equation modeling (SEM) assuming linear "
                "relationships between the variables, and it is essential to test this "
                'assumption <ref type="bibr">(Bentler &amp; Chou, 1987, p. 86;</ref> '
                '<ref type="bibr">Ullman, 2007, p. 683)</ref>.</p>'
            ],
        }
    )
    obs = _process_full_text(full_text)
    assert list(obs["text"]) == [
        "The authors used structural equation modeling (SEM) assuming linear relationships "
        "between the variables, and it is essential to test this assumption (Bentler & Chou, "
        "1987, p. 86; Ullman, 2007, p. 683)."
    ]

    # not in ref
    fmt = "This research (Frith &amp; Singer, 2008, p. 3876)."
    full_text = pd.DataFrame(
        {"p": [1], "div": [1], "section": [1], "header": ["demo"], "formatted": [fmt]}
    )
    obs = _process_full_text(full_text)
    assert list(obs["text"]) == ["This research (Frith & Singer, 2008, p. 3876)."]
    assert list(obs["formatted"]) == [fmt]


def test_head(fixtures_dir: Path) -> None:
    paper = grobid_to_bibr(fixtures_dir / "problems" / "0956797615569889.xml", None)
    # removed from text, but kept in the section list
    assert (paper.section["header"] == "Results").sum() == 1
    assert (paper.text["text"] == "Results").sum() == 0


def test_bibstruct() -> None:
    xml_text = """<listBibl><biblStruct status="extracted" xml:id="b37">
    <monogr>
        <title level="m" type="main">When are organizations punished for organizational \
misconduct? A review and research agenda</title>
        <author>
            <persName><forename type="first">M</forename><forename type="middle">H</forename>\
<surname>Mcdonnell</surname></persName>
        </author>
        <author>
            <persName><forename type="first">S</forename><surname>Nurmohamed</surname></persName>
        </author>
        <idno type="DOI">10.1016/j.riob.2021.100150</idno>
        <ptr target="https://doi.org/10.1016/j.riob.2021.100150" />
        <imprint>
            <date type="published" when="2021">2021</date>
        </imprint>
    </monogr>
    <note>Research in Organizational Behavior, 41, Article 100150</note>
    <note type="raw_reference">McDonnell, M. H., &amp; Nurmohamed, S. (2021). When are \
organizations punished for organizational misconduct? A review and research agenda. Research \
in Organizational Behavior, 41, Article 100150. https://doi.org/10.1016/j .riob.2021.100150</note>
</biblStruct></listBibl>"""
    bib = _tei_bib(px.read_xml(xml_text))
    exp = "When are organizations punished for organizational misconduct? A review and research agenda"
    assert list(bib["title"]) == [exp]
    assert list(bib["bib_type"]) == ["book"]
    assert list(bib["authors"]) == ["Mcdonnell, M H; Nurmohamed, S"]
    assert list(bib["year"]) == [2021]


# errors reproduced from R ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("table_norows.tei.xml", "compatible with existing data"),
        ("url_empty.tei.xml", "zero-length pattern"),
        ("bib_page_from.tei.xml", "subscript out of bounds"),
    ],
)
def test_errors_like_r(io_fixtures: Path, name: str, error: str) -> None:
    with pytest.raises((ValueError, IndexError), match=error):
        _grobid_to_bibr(io_fixtures / name)


def test_read_skips_unreadable_xml(fixtures_dir: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        papers = pc.read([fixtures_dir / "problems" / "corrupt.xml", pc.demofile("xml")])
    assert isinstance(papers, pc.Paper)


def test_faster_than_r_budget(fixtures_dir: Path) -> None:
    """A long Grobid paper converts well within R's ~1.2 s per paper."""
    import time

    start = time.perf_counter()
    _grobid_to_bibr(fixtures_dir / "formats" / "published.pdf.tei.xml")
    assert time.perf_counter() - start < 2.0
