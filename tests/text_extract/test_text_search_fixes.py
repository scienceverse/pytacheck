"""text_search() where pytacheck fixes metacheck (docs/UPSTREAM_ISSUES.md U79, U80)."""

from __future__ import annotations

import metacheck as pc
from metacheck.papers.model import PaperList


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
    # U79 (fixed in metacheck 0.4): an empty list is searched as an empty paper, so the
    # table has the typed columns of one and `text` is kept; it used to lose them
    empty = pc.text_search(PaperList([]), "x")
    assert len(empty) == 0
    assert list(empty.columns) == list(pc.text_search(pc.paper(), "x").columns)
    assert list(empty.columns)[:3] == ["text", "text_id", "paragraph_id"]
    assert str(empty["text"].dtype) == "string"
    assert str(empty["text_id"].dtype) == "Int64"
    again = pc.text_search(empty, "y", return_="section")
    assert len(again) == 0 and "text" in again.columns
