"""Twin of rp_repo.R (report parity tests)."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from metacheck.module import module


@module(
    title="Repository Stand-in",
    description="Reports the arguments report_repository() passes.",
    keywords=["general"],
)
def rp_repo(
    paper: Any, local_path: Any = None, local_only: bool = False, note: str = ""
) -> dict[str, Any]:
    folder = os.path.basename(local_path) if local_path is not None else "none"
    return {
        "table": pd.DataFrame(
            {"folder": [folder], "local_only": pd.Series([local_only], dtype="boolean")}
        ),
        "traffic_light": "info",
        "summary_text": f"Folder {folder}; local only: {'TRUE' if local_only else 'FALSE'}{note}",
    }
