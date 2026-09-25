"""A tiny pack module for the tests: ClinicalTrials.gov numbers (NCT + 8 digits)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck.module import module
from pytacheck.text import text_search


@module(
    title="NCT Numbers",
    description="Find ClinicalTrials.gov registration numbers.",
    details="<validation>Not validated: a test fixture.</validation>",
    keywords=["method"],
    author=["pytacheck tests"],
)
def nct_ids(paper: Any) -> dict[str, Any]:
    found = text_search(paper, r"NCT\d{8}", return_="match", perl=True)
    table = pd.DataFrame(
        {"paper_id": found["paper_id"].astype("string"), "trial_id": found["text"].astype("string")}
    ).drop_duplicates(ignore_index=True)
    ids = table.groupby("paper_id", sort=False)["trial_id"]
    summary_table = pd.DataFrame(
        {"paper_id": ids.size().index.astype("string"), "nct_ids": ids.nunique().to_numpy()}
    )
    return {
        "table": table,
        "summary_table": summary_table,
        "traffic_light": "green" if len(table) else "na",
        "summary_text": f"{len(table)} ClinicalTrials.gov number(s) found.",
    }
