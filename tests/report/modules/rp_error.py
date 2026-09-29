"""Twin of rp_error.R (report parity tests)."""

from __future__ import annotations

from typing import Any

from metacheck.module import module


@module(title="Error Module", description="Always fails.", keywords=["results"])
def rp_error(paper: Any) -> dict[str, Any]:
    raise ValueError("boom")
