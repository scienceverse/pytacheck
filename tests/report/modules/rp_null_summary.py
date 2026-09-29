"""Twin of rp_null_summary.R (report parity tests)."""

from __future__ import annotations

from typing import Any

from metacheck.module import module


@module(
    title="Null Summary",
    description="A module without a summary text.",
    details="Only details.",
    keywords=["intro"],
    author=["Daniel Lakens"],
)
def rp_null_summary(paper: Any) -> dict[str, Any]:
    return {"traffic_light": "info", "report": ["Some report text.", "More report text."]}
