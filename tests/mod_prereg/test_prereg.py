"""Port of metacheck's tests/testthat/test-module-prereg.R (plus module contracts)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pandas as pd
import pytest

import pytacheck as pc
from tests.mod_prereg.parity_support import LOCAL_MOCKS, mocked, plist, tp


@pytest.fixture(autouse=True)
def _online() -> Iterator[None]:
    # skip the DNS check in aspredicted_info()
    with mock.patch("pytacheck.utils.online", return_value=True):
        yield


def run(paper: object, *mock_dirs: object) -> pc.module.ModuleOutput:
    with mocked(*mock_dirs):  # type: ignore[arg-type]
        return pc.module_run(paper, "prereg_check")


def osf_paper(guid: str) -> pc.Paper:
    return pc.test_paper(url=[f"https://osf.io/{guid}"])


def test_multiple_prereg() -> None:
    mo = run(pc.demopaper())
    assert len(mo.table) == 2
    assert mo.table["template_name"].tolist() == ["OSF Preregistration", "AsPredicted"]
    assert mo.table["id"].tolist() == ["48ncu", "by8i8v"]
    assert mo.traffic_light == "info"
    assert mo.summary_text == "We found 2 preregistrations."


@pytest.mark.parametrize(
    ("guid", "template"),
    [
        ("5xysn", "Open-Ended Registration"),  # oer
        ("jez3g", "Prereg Challenge"),  # prc
        ("g59u6", "OSF Preregistration"),  # osf_pr_28
        ("7qcxa", "OSF Preregistration"),  # osf_pr_31
        ("dr42m", "OSF-Standard Pre-Data Collection Registration"),  # osf_pre
        ("7v28u", "Preregistration Template from AsPredicted.org"),  # prap
        ("vzb48", "Replication Recipe (Brandt et al., 2013): Pre-Registration"),  # rrbrandt
        (
            "r5bme",
            "Pre-Registration in Social Psychology (van 't Veer & Giner-Sorolla, 2016): "
            "Pre-Registration",
        ),  # vant veer (prsp)
    ],
)
def test_templates(guid: str, template: str) -> None:
    mo = run(osf_paper(guid))
    assert len(mo.table) == 1
    assert mo.table["template_name"].tolist() == [template]
    assert mo.table["id"].tolist() == [guid]


def test_blocks_format_extraction_maps_to_canonical_fields() -> None:
    # 9h2pj is a current OSF Preregistration (v4, blocks-format)
    mo = run(osf_paper("9h2pj"))
    assert len(mo.table) == 1
    assert mo.table["template_name"].tolist() == ["OSF Preregistration"]
    assert {
        "research_questions",
        "sample_size",
        "statistical_tests",
        "inference_criteria",
        "data_exclusion_criteria",
    } <= set(mo.table.columns)
    assert mo.table["sample_size"].tolist() == ["The total sample size will be 400."]


def test_pages_format_extraction_maps_to_canonical_fields() -> None:
    # g59u6 is an older OSF Preregistration (pages-format, qN keys)
    mo = run(osf_paper("g59u6"))
    assert {"research_questions", "sample_size", "statistical_tests"} <= set(mo.table.columns)
    assert mo.table["sample_size"].iloc[0] != ""


def test_aspredicted_on_osf_extraction_maps_to_canonical_fields() -> None:
    # 7v28u is an AsPredicted-on-OSF registration (pages, semantic word keys)
    mo = run(osf_paper("7v28u"))
    assert {"research_questions", "sample_size"} <= set(mo.table.columns)
    assert mo.table["sample_size"].iloc[0] != ""


def test_multiple_papers() -> None:
    paper1 = pc.test_paper(url=["https://osf.io/48ncu"])
    paper2 = pc.test_paper(url=["https://aspredicted.org/by8i8v.pdf"])
    paper = pc.PaperList([paper1, paper2])
    mo = run(paper)
    assert len(mo.table) == 2
    assert set(mo.table["template_name"]) == {"OSF Preregistration", "AsPredicted"}
    assert "paper_id" in mo.table.columns
    assert set(mo.table["id"]) == {"48ncu", "by8i8v"}
    ids = set(pc.paper_id(paper))
    assert set(mo.table["paper_id"]) == ids
    assert set(mo.summary_table["paper_id"]) == ids
    assert mo.summary_table["preregistration"].tolist() == [1, 1]


def test_inaccessible_registration_link_is_reported() -> None:
    # metacheck#361: osf_type() says "inaccessible" for a valid but unreadable id
    guid = "abcde"
    paper = osf_paper(guid)
    with mock.patch("pytacheck.archives.osf.osf_type", lambda guid: "inaccessible"):
        mo = run(paper)
    assert "no registrations" not in mo.summary_text
    assert "could not be accessed" in mo.summary_text
    report = mo.report if isinstance(mo.report, list) else [mo.report]
    assert any("private, embargoed, or withdrawn" in str(r) for r in report)
    assert any(guid in str(r) for r in report)
    assert mo.traffic_light == "na"


def test_registration_that_type_checks_but_fails_to_fetch_is_reported() -> None:
    from pytacheck.archives.osf import _osf_error_result

    guid = "fghij"
    paper = osf_paper(guid)
    with (
        mock.patch("pytacheck.archives.osf.osf_type", lambda guid: "registrations"),
        mock.patch(
            "pytacheck.archives.osf.osf_get_all_pages",
            lambda url, page_end=float("inf"): _osf_error_result("forbidden"),
        ),
    ):
        mo = run(paper)
    assert "could not be accessed" in mo.summary_text
    report = mo.report if isinstance(mo.report, list) else [mo.report]
    assert any(guid in str(r) for r in report)


def test_combine_more_than_10_osf_registrations() -> None:
    # metacheck#262: >10 registrations of different templates combine
    reg_urls = [
        f"https://osf.io/{g}"
        for g in (
            "bdvxs", "wrh4x", "trwb4", "z2bsa", "jez3g", "9bg3z", "7qcxa", "a6y7r", "4v3sg",
            "hwu9x", "yab8q",
        )
    ]  # fmt: skip
    mo = run(pc.test_paper(url=reg_urls))
    assert mo.summary_table["preregistration"].tolist() == [11]
    assert len(mo.table) == 11
    assert "paper_id" in mo.table.columns


# -- beyond the R tests: every branch of the summary --------------------------------


def test_no_links() -> None:
    paper = tp([], "p_none", ["No links here."])
    mo = pc.module_run(paper, "prereg_check")  # no request is made
    assert mo.traffic_light == "na"
    assert mo.summary_text == "No preregistration links were found."
    assert mo.table is None
    assert mo.summary_table["preregistration"].tolist() == [0]


def test_no_registrations() -> None:
    mo = run(osf_paper("pngda"))
    assert mo.traffic_light == "na"
    assert mo.summary_text == "We found 1 OSF link, but no registrations."
    assert mo.summary_table["preregistration"].tolist() == [0]


def test_mixed_accessible_and_inaccessible() -> None:
    urls = ["https://osf.io/48ncu", "https://osf.io/3cz2e", "https://osf.io/8c3kb"]
    mo = run(tp(urls, "p_mixed"))
    assert mo.traffic_light == "info"
    assert mo.summary_text == (
        "We found 1 preregistration. 2 registration links could not be accessed "
        "(private, embargoed, or withdrawn)."
    )
    assert (
        "The following registration links could not be accessed and may be private, "
        "embargoed, or withdrawn: https://osf.io/3cz2e, "
        "https://api.osf.io/v2/registrations/8c3kb."
    ) in mo.report


def test_aspredicted_only_errors_as_in_r() -> None:
    # R: osf_type(character(0)) -> "argument is of length zero"
    with pytest.raises(pc.module.ModuleError, match="argument is of length zero"):
        run(tp(["https://aspredicted.org/by8i8v.pdf"], "p_ap"))


def test_several_aspredicted_links_collapse_into_one_row() -> None:
    urls = [
        "https://aspredicted.org/by8i8v.pdf",
        "https://aspredicted.org/ve2qn.pdf",
        "https://osf.io/pngda",
    ]
    mo = run(tp(urls, "p_ap2"))
    assert len(mo.table) == 1
    assert mo.table["id"].tolist() == ["by8i8v\n\nve2qn"]
    assert mo.table["paper_id"].isna().all()
    assert mo.summary_table["preregistration"].tolist() == [0]


def test_withdrawn_registration() -> None:
    mo = run(osf_paper("wdrwn"), LOCAL_MOCKS)
    assert mo.table["description"].tolist() == ["WITHDRAWN"]
    assert mo.table["embargo_end_date"].tolist() == [""]


def test_schemas_are_fetched_once() -> None:
    urls = ["https://osf.io/blkss", "https://osf.io/pagss"]
    papers = plist(tp(urls, "p1"), tp(["https://osf.io/blkss"], "p2"))
    with mocked(LOCAL_MOCKS) as router:
        mo = pc.module_run(papers, "prereg_check")
        urls = [str(c.request.url) for c in router.calls]
    # one request per registration, one per schema
    assert len([u for u in urls if "/registrations/blkss" in u]) == 1
    assert len([u for u in urls if "/schemas/" in u]) == 2
    # the same link in two papers joins to both (R: left_join() duplicates the row)
    assert mo.table["id"].tolist() == ["blkss", "blkss", "pagss"]
    assert mo.table["paper_id"].tolist() == ["p1", "p2", "p1"]
    assert mo.summary_table["preregistration"].tolist() == [2, 1]


def test_report_structure() -> None:
    mo = run(pc.demopaper())
    from pytacheck.report import ReportTable

    assert mo.report[0] == "We found 2 preregistrations."
    tables = [b for b in mo.report if isinstance(b, ReportTable)]
    link_table, sample_table, full_table = tables
    assert list(link_table.data.columns) == ["id", "title", "template"]
    assert link_table.data["id"].iloc[0] == (
        "<a href='https://osf.io/48ncu' target='_blank'>48ncu</a>"
    )
    assert list(sample_table.data.columns) == ["id", "sample_size"]
    assert list(full_table.data.columns) == ["Field", "Preregistration 1", "Preregistration 2"]
    assert full_table.maxrows == 5
    # all-NA columns are left out of the full preregistration table
    assert set(full_table.data["Field"]) == set(mo.table.columns)
    assert mo.report[-1].startswith('::: {.callout-tip title="Learn More" collapse="true"}')


def test_module_contract() -> None:
    from pytacheck.packs.check import run_issues

    demo = pc.demopaper()
    with mocked():
        issues = run_issues(pc.module_info("prereg_check"), [("demopaper()", demo)])
    assert not [str(i) for i in issues if i.level == "error"]


def test_module_metadata() -> None:
    info = pc.module_info("prereg_check")
    assert info.title == "Preregistration Check"
    assert info.keywords == ("method",)
    assert info.requires == ("network",)
    assert info.section == "method"


def test_table_dtypes() -> None:
    mo = run(pc.demopaper())
    assert all(pd.api.types.is_string_dtype(mo.table[c]) for c in mo.table.columns)


def test_aspredicted_info_messages_are_suppressed(capsys: pytest.CaptureFixture[str]) -> None:
    # R: table_ap <- suppressMessages(aspredicted_info(links_ap$href)); the
    # messages would be "Starting AsPredicted retrieval ...", "* Retrieving
    # info from ..." and "...AsPredicted retrieval complete!" (or "No valid
    # AsPredicted links" for a paper with only OSF links)
    from pytacheck.config import verbose

    old = verbose()
    try:
        verbose(True)
        run(pc.demopaper())  # an AsPredicted and an OSF link
        run(osf_paper("48ncu"))
    finally:
        verbose(old)
    assert "AsPredicted" not in capsys.readouterr().err
