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
    assert info.details == "Checks for valid URLs using a regular expression."
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
