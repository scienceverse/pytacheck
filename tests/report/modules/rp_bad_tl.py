"""Twin of rp_bad_tl.R (report parity tests)."""

from __future__ import annotations

from typing import Any

from pytacheck.module import module


@module(
    title="Bad Traffic Light",
    description="Uses a traffic light that is not defined.",
    details="Details here.",
    keywords=["results"],
)
def rp_bad_tl(paper: Any) -> dict[str, Any]:
    return {"traffic_light": "blue", "summary_text": "A blue summary.", "report": "A blue report."}
