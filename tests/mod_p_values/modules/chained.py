"""Twin of metacheck's tests/testthat/modules/chained.R."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck.module import get_prev_outputs, module


@module(
    title="Chained (Test version)",
    description="Use the list of all p-values if it exists",
    keywords=["general"],
    author=["Lisa DeBruine"],
    params={
        "paper": "a paper object or paperlist object",
        "...": "further arguments (not used)",
    },
    returns="a list with table, summary, traffic light",
)
def chained(paper: Any, **kwargs: Any) -> dict[str, Any]:
    p = get_prev_outputs("all_p_values", "table")
    if p is not None:
        table = p.iloc[0:2, 0:2]
    else:
        table = pd.DataFrame({"a": pd.Series(["not from prev"], dtype="string")})

    # return a list ----
    return {
        "table": table,
        "summary_text": "chained summary text",
        "report": "chained report text",
    }
