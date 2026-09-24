"""Review checks of prereg_check branches found by the adversarial review.

The same scenarios are parity cases (parity/cases/mod_prereg_review.yaml);
these assert the R behaviour directly, on the synthetic recordings of
tests/mod_prereg/make_review_mocks.py and metacheck's recordings.
"""

from __future__ import annotations

import pytest

from tests.mod_prereg.parity_support import run_prereg, run_prereg_tables


def test_view_only_link_keeps_its_token_and_does_not_join() -> None:
    url = "https://osf.io/vwonl/?view_only=abc123"
    mo = run_prereg(papers=[{"url": [url], "id": "p_vwonl"}], mock="local")
    assert mo.table["id"].tolist() == ["vwonl"]
    assert mo.table["link"].tolist() == ["https://osf.io/vwonl"]
    # the link in the paper has the token, so the row has no paper_id (R's join)
    assert mo.table["paper_id"].isna().all()
    assert mo.summary_table["preregistration"].tolist() == [0]
    assert mo.summary_text == "We found 1 preregistration."


def test_registration_without_data_errors_as_in_r() -> None:
    # no row and no osf_error: R falls through to dplyr::count(), which fails
    with pytest.raises(Exception, match="paper_id"):
        run_prereg(papers=[{"url": ["https://osf.io/emptd"], "id": "p"}], mock="local")


def test_registration_without_data_next_to_a_readable_one() -> None:
    urls = ["https://osf.io/emptd", "https://osf.io/blkss"]
    mo = run_prereg(papers=[{"url": urls, "id": "p"}], mock="local")
    assert mo.table["id"].tolist() == ["blkss"]
    assert mo.summary_text == "We found 1 preregistration."


def test_field_slugged_to_paper_id_errors_as_in_r() -> None:
    # left_join() makes paper_id.x / paper_id.y, so count(paper_id) fails
    with pytest.raises(Exception, match="paper_id"):
        run_prereg(papers=[{"url": ["https://osf.io/pidsl"], "id": "p"}], mock="local")


def test_label_case_mapping() -> None:
    mo = run_prereg(papers=[{"url": ["https://osf.io/unicd"], "id": "p"}], mock="local")
    assert "istanbul_study" in mo.table.columns
    assert mo.table["sample_size"].tolist() == ["answer 4 answer 5"]


def test_same_aspredicted_link_in_two_papers_joins_to_both() -> None:
    ap = "https://aspredicted.org/by8i8v.pdf"
    mo = run_prereg(
        papers=[
            {"url": [ap, "https://osf.io/48ncu"], "id": "paper_a"},
            {"url": [ap], "id": "paper_b"},
        ]
    )
    assert mo.table["paper_id"].tolist() == ["paper_a", "paper_a", "paper_b"]
    assert mo.summary_table["preregistration"].tolist() == [2, 1]
    assert mo.summary_text == "We found 3 preregistrations."


def test_inaccessible_link_twice_is_named_twice() -> None:
    papers = [{"url": ["https://osf.io/3cz2e"], "id": "p", "text": ["See osf.io/3cz2e."]}]
    mo = run_prereg(papers=papers)
    assert mo.traffic_light == "na"
    assert "2 registration links could not be accessed" in mo.summary_text


def test_report_tables() -> None:
    tables = run_prereg_tables(demo=True)
    assert [list(t.columns) for t in tables] == [
        ["id", "title", "template"],
        ["id", "sample_size"],
        ["Field", "Preregistration 1", "Preregistration 2"],
    ]
    assert tables[0]["id"].str.startswith("<a href='https://").all()
    # line breaks in cells are shown as <br>
    assert not tables[2].iloc[:, 1:].apply(lambda s: s.str.contains("\n").any()).any()
