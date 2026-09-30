"""Twin of rp_no_details.R (report parity tests)."""

from __future__ import annotations

from typing import Any

from metacheck.module import module


@module(title="No Details Module", keywords=["general"])
def rp_no_details(paper: Any) -> dict[str, Any]:
    return {"traffic_light": "green", "summary_text": "All good.", "report": "All good."}
