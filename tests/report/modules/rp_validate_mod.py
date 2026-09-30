"""Twin of rp_validate_mod.R (report parity tests)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck._r import grepl
from metacheck.module import module
from metacheck.text import text_search


@module(
    title="Validate Demo", description="Flags sentences for validate() tests.", keywords=["results"]
)
def rp_validate_mod(paper: Any) -> dict[str, Any]:
    t = text_search(paper, ".")
    texts = t["text"].tolist()
    table = pd.DataFrame(
        {
            "paper_id": pd.Series([*t["paper_id"].tolist(), "zzz"], dtype="string"),
            "text": pd.Series([*texts, "an extra row"], dtype="string"),
            "flag": pd.Series([*grepl("significan", texts), None], dtype="boolean"),
            "n": pd.Series([*[len(x) for x in texts], 0], dtype="Int64"),
        }
    )
    return {"table": table, "traffic_light": "info", "summary_text": "done"}
