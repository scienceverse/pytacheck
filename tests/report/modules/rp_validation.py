"""Twin of rp_validation.R (report parity tests)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck._r import count, plural
from pytacheck.module import module
from pytacheck.report import collapse_section, scroll_table
from pytacheck.text import text_search


@module(
    title="Validation Demo",
    description="Demo description",
    details="""
        Demo details...

        A second paragraph of details.

        <validation>
        Here is my demo validation information.
        </validation>
    """,
    keywords=["method"],
    author=["Lisa DeBruine <debruine@gmail.com>", "Daniel Lakens", "Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object",
        "pattern": "the pattern to search for",
    },
)
def rp_validation(paper: Any, pattern: str = "significan") -> dict[str, Any]:
    table = text_search(paper, pattern)
    small = pd.DataFrame({"n": [len(table)], "label": ["matches"]})
    summary_table = count(table, "paper_id", name="hits")
    tl = "yellow" if len(table) else "green"
    report = [
        "This module searched every sentence for the pattern and listed the "
        "sentences where it was found. Check each of them: a mention is not "
        "necessarily a problem, but it is worth a second look before you submit "
        "the manuscript, because reviewers will often ask about it.",
        scroll_table(table.loc[:, ["text", "header"]], colwidths=[0.8, 0.2], maxrows=3),
        collapse_section(scroll_table(small), "Full table"),
        collapse_section(["Guidance paragraph one.", "Guidance *two*."]),
    ]
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "summary_text": f"Found {len(table)} sentence{plural(len(table))}.",
        "report": report,
    }
