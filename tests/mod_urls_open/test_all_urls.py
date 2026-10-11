"""Tests for the all_urls module (ports of metacheck's testthat tests).

Sources: tests/testthat/test-module-.R ("all_urls", "chaining modules -
paperlist", "all builtin modules have essential components") and
tests/testthat/test-report.R ("module_report" without validation).
"""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import SECTION_LEVELS, module_find

MODULE = "all_urls"


def test_all_urls_listed() -> None:
    assert MODULE in pc.module_list()["name"].tolist()


def test_all_urls_essential_components() -> None:
    info = pc.module_info(MODULE)
    assert info.title == "List All URLs"
    assert info.description == "List all the URLs in the main text."
    assert info.details.startswith("Lists the links bibr extracted from the document")
    assert info.keywords[0] in SECTION_LEVELS
    assert info.keywords == ("general",)
    assert info.requires == ()


def test_all_urls_demo(demo: pc.Paper) -> None:
    urls = pc.module_run(demo, MODULE)
    assert urls.traffic_light == "info"
    assert "https://osf.io/48ncu" in urls.table["text"].tolist()
    assert urls.module == MODULE
    assert urls.summary_text is None
    assert urls.report == ""
    assert urls.na_replace == 0
    st = urls.summary_table
    assert st.columns.tolist() == ["paper_id", "urls"]
    assert st["urls"].tolist() == [len(urls.table)]


def test_all_urls_iteration(psychsci: pc.PaperList) -> None:
    out = pc.module_run(psychsci, MODULE)
    ids = out.table["paper_id"].unique().tolist()
    assert set(ids) <= set(psychsci.names)
    # every paper gets a row, papers without URLs get 0 (na_replace)
    assert out.summary_table["paper_id"].tolist() == psychsci.names
    assert out.summary_table["urls"].notna().all()
    counts = out.table["paper_id"].value_counts()
    for pid, n in zip(out.summary_table["paper_id"], out.summary_table["urls"], strict=True):
        assert n == counts.get(pid, 0)


def test_all_urls_none() -> None:
    out = pc.module_run(pc.test_paper(["Nothing here.", "Or here."]), MODULE)
    assert out.traffic_light == "na"
    assert len(out.table) == 0
    assert out.summary_table["urls"].tolist() == [0]


def test_all_urls_empty_paper() -> None:
    out = pc.module_run(pc.paper(), MODULE)
    assert out.traffic_light == "na"
    assert len(out.table) == 0
    assert "paper_id" in out.table.columns


def test_all_urls_paperlist() -> None:
    papers = pc.PaperList(
        [
            pc.test_paper(["See https://osf.io/abcde/.", "And https://github.com/x/y."]),
            pc.test_paper(["No links."]),
        ]
    )
    out = pc.module_run(papers, MODULE)
    assert out.traffic_light == "info"
    assert out.summary_table["urls"].tolist() == [2, 0]


def test_chaining_modules_paperlist(psychsci: pc.PaperList) -> None:
    marg = pc.module_run(psychsci, "marginal")
    url = pc.module_run(psychsci, MODULE)
    x = pc.module_run(pc.module_run(psychsci, "marginal"), MODULE)
    assert x.summary_table.columns.tolist() == ["paper_id", "marginal", "urls"]
    pd.testing.assert_series_equal(
        x.summary_table["marginal"], marg.summary_table["marginal"], check_names=False
    )
    pd.testing.assert_series_equal(
        x.summary_table["urls"], url.summary_table["urls"], check_names=False
    )


def test_chaining_with_all_p_values(psychsci: pc.PaperList) -> None:
    try:
        module_find("all_p_values")
    except Exception:
        pytest.skip("all_p_values is not ported yet")
    p = pc.module_run(psychsci, "all_p_values")
    url = pc.module_run(psychsci, MODULE)
    x = pc.module_run(pc.module_run(psychsci, "all_p_values"), MODULE)
    assert x.summary_table.columns.tolist() == ["paper_id", "p_values", "urls"]
    assert x.summary_table["p_values"].tolist() == p.summary_table["p_values"].tolist()
    assert x.summary_table["urls"].tolist() == url.summary_table["urls"].tolist()


def test_module_report_without_validation(demo: pc.Paper) -> None:
    try:
        from metacheck.report.report import module_report
    except ImportError:  # pragma: no cover - ported concurrently
        pytest.skip("module_report is not available")
    rep = module_report(pc.module_run(demo, MODULE))
    assert "::: {.validation}" not in rep


def test_does_not_mutate_paper(demo: pc.Paper) -> None:
    before = demo.text.copy()
    pc.module_run(demo, MODULE)
    pd.testing.assert_frame_equal(demo.text, before)


# bibr's links and the plain-text URLs that are not links (D82) ---------------------------


def _with_links(sentences: list[str], links: list[dict[str, object]]) -> pc.Paper:
    """A test paper whose ``url`` table holds *links* (``text_id`` is 1-based)."""
    paper = pc.test_paper(sentences)
    paper["url"] = pd.DataFrame(
        {
            "url_id": pd.array(range(1, len(links) + 1), dtype="Int64"),
            "href": pd.array([x["href"] for x in links], dtype="string"),
            "link_text": pd.array([x.get("link_text") for x in links], dtype="string"),
            "text_id": pd.array([x["text_id"] for x in links], dtype="Int64"),
            "start": pd.array([x.get("start") for x in links], dtype="Int64"),
            "end": pd.array([None] * len(links), dtype="Int64"),
        }
    )
    return paper


def test_all_urls_columns(demo: pc.Paper) -> None:
    table = pc.module_run(demo, MODULE).table
    assert table.columns.tolist()[-4:] == ["link_text", "url", "domain", "source"]
    assert set(table["source"]) == {"link"}
    assert table["domain"].tolist()[:2] == ["osf.io", "aspredicted.org"]


def test_all_urls_link_cut_by_a_line_break(demo: pc.Paper) -> None:
    # the TEI's link stops at the line break; the text has the whole URL
    assert "https://github.com/Lak-" in demo["url"]["href"].tolist()
    table = pc.module_run(demo, MODULE).table
    assert "https://github.com/Lak-ens/to_err_is_human" in table["url"].tolist()
    assert "https://github.com/Lak-" not in table["url"].tolist()


def test_all_urls_drops_dotted_names(demo: pc.Paper) -> None:
    assert "pwr.t.test" not in pc.module_run(demo, MODULE).table["text"].tolist()
    paper = pc.test_paper(
        [
            "We used is.na(x), pwr.t.test, data.csv and Soc.Lond in the code.",
            "See osf.io and 999.999.999.999 but 10.0.0.1 and doi:10.5555/xyz.abc.",
        ]
    )
    table = pc.module_run(paper, MODULE).table
    assert table["text"].tolist() == ["osf.io", "10.0.0.1"]
    assert table["url"].tolist() == ["https://osf.io", "https://10.0.0.1"]
    assert table["source"].tolist() == ["text", "text"]


def test_all_urls_repairs_text_matches() -> None:
    paper = pc.test_paper(
        ["Notes (https://osf.io/abc).foot_2 and ftp://files.example.net/pub and naïve.org/x."]
    )
    table = pc.module_run(paper, MODULE).table
    assert table["text"].tolist() == [
        "https://osf.io/abc",
        "ftp://files.example.net/pub",
        "naïve.org/x",
    ]


def test_all_urls_link_text() -> None:
    paper = _with_links(
        ["The data are on OSF and at https://osf.io/abc/.", "No link here."],
        [
            {"href": "https://osf.io/xyz", "link_text": "on OSF", "text_id": 1, "start": 13},
            {"href": "https://osf.io/abc/", "text_id": 1, "start": 27},
        ],
    )
    out = pc.module_run(paper, MODULE)
    table = out.table
    assert table["url"].tolist() == ["https://osf.io/xyz", "https://osf.io/abc/"]
    assert table["text"].tolist() == ["https://osf.io/xyz", "https://osf.io/abc"]
    assert table["link_text"].tolist()[0] == "on OSF"
    assert pd.isna(table["link_text"].tolist()[1])
    assert table["source"].tolist() == ["link", "link"]
    assert out.summary_table["urls"].tolist() == [2]


def test_all_urls_links_and_text_in_document_order() -> None:
    paper = _with_links(
        ["First www.example.org/a then the link.", "Mail someone@example.com."],
        [
            {"href": "https://example.com/b", "link_text": "the link", "text_id": 1},
            {"href": "mailto:someone@example.com", "text_id": 2},
            {"href": "http://pss", "text_id": 2},
        ],
    )
    table = pc.module_run(paper, MODULE).table
    # the e-mail link and the cut-off http://pss are left out
    assert table["url"].tolist() == ["https://www.example.org/a", "https://example.com/b"]
    assert table["source"].tolist() == ["text", "link"]


def test_all_urls_bare_host_is_not_a_link_to_a_page_on_it() -> None:
    paper = _with_links(
        ["We preregistered on AsPredicted.org (https://aspredicted.org/abc.pdf)."],
        [{"href": "https://aspredicted.org/abc.pdf", "text_id": 1}],
    )
    table = pc.module_run(paper, MODULE).table
    assert table["text"].tolist() == ["AsPredicted.org", "https://aspredicted.org/abc.pdf"]
    assert table["source"].tolist() == ["text", "link"]


def test_all_urls_local_file_link_stands_for_its_text() -> None:
    paper = _with_links(
        ["Materials: osf .io/6b4ag."],
        [
            {
                "href": "file://localhost/opt/grobid/tmp/osf.io/6b4ag",
                "link_text": "osf .io/6b4ag",
                "text_id": 1,
            }
        ],
    )
    table = pc.module_run(paper, MODULE).table
    assert table["url"].tolist() == ["https://osf.io/6b4ag"]
    assert table["source"].tolist() == ["link"]
