"""text_search() where pytacheck fixes metacheck (docs/UPSTREAM_ISSUES.md U79, U80)."""

from __future__ import annotations

import pytacheck as pc
from pytacheck.papers.model import PaperList


def test_section_return_keeps_the_header(demo) -> None:
    # U80: R groups return = "section" without the header, which comes back NA
    sections = pc.text_search(demo, "significant", return_="section")
    assert sections["header"].tolist() == ["Abstract", "Procedure"]
    headers = pc.text_search(demo, "significant", return_="header")
    assert sections["text"].tolist() == headers["text"].tolist()
    # a table whose rows disagree on the header of a section gets NA
    table = pc.text_search(demo, "significant").iloc[:2].copy()
    table["section_id"] = 0
    table["section_type"] = "abstract"
    table["header"] = ["A", "B"]
    merged = pc.text_search(table, return_="section")
    assert len(merged) == 1
    assert merged["header"].isna().all()


def test_empty_paper_list_gives_a_typed_text_table() -> None:
    # U79: R adds the missing columns as logical NA and drops `text`, so
    # searches of the result (and modules) fail
    empty = pc.text_search(PaperList([]), "x")
    assert len(empty) == 0
    assert list(empty.columns) == [
        "text",
        "text_id",
        "section_id",
        "paragraph_id",
        "paper_id",
        "header",
        "section_type",
    ]
    assert str(empty["text"].dtype) == "string"
    assert str(empty["text_id"].dtype) == "Int64"
    again = pc.text_search(empty, "y", return_="section")
    assert len(again) == 0 and "text" in again.columns
