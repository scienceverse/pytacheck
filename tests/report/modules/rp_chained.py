"""Twin of rp_chained.R (report parity tests)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck.module import get_prev_outputs, module


@module(
    title="Chained Module",
    description="Uses the marginal module's table.",
    details="Counts the rows of the marginal table.",
    keywords=["reference"],
    author=["Lisa DeBruine"],
)
def rp_chained(paper: Any, extra: str = "") -> dict[str, Any]:
    p = get_prev_outputs("marginal", "table")
    n = -1 if p is None else len(p)
    return {
        "table": pd.DataFrame({"n": pd.Series([n], dtype="Int64")}),
        "traffic_light": "red" if n > 0 else "green",
        "summary_text": f"The marginal table had {n} rows.{extra}",
        "report": [f"Chained report {n}.", extra],
    }
