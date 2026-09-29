"""Twin of rp_list_summary.R (report parity tests)."""

from __future__ import annotations

from typing import Any

from metacheck.module import module


@module(
    title="List Summary",
    description="A summary that is a markdown list.",
    details="<validation>Only validation.</validation>",
    keywords=["discussion"],
    author=["A One", "B Two"],
)
def rp_list_summary(paper: Any, na: bool = False) -> dict[str, Any]:
    return {
        "traffic_light": "na" if na else "info",
        "summary_text": "\n* first item\n* second item",
        "report": "Short report.",
    }
