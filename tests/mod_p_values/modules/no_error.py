"""Twin of metacheck's tests/testthat/modules/no_error.R (function ``pvals2``)."""

from __future__ import annotations

from typing import Any

from metacheck._r.frames import count
from metacheck.module import module
from metacheck.text import text_search


@module(
    title="Demo No Error",
    description="Demo description",
    details="Demo details...\n\n<validation>\nHere is my demo validation information.\n</validation>",
    keywords=["results"],
    author=["Lisa DeBruine <debruine@gmail.com>", "Daniel Lakens"],
    params={
        "paper": "a paper object or paperlist object",
        "demo_arg": "an example of a passed argument",
        "...": "further arguments (not used)",
    },
    returns="a list with table, summary, traffic light",
)
def no_error(paper: Any, demo_arg: str = "", **kwargs: Any) -> dict[str, Any]:
    # detailed table of results ----
    pattern = r"\bp-?(value)?\s*[<>=≤≥]{1,2}\s*(n\.?s\.?|\d?\.\d+)(e-\d+)?"
    table = text_search(paper, pattern, return_="match", perl=True)

    # summary output for paperlists ----
    summary_table = count(table, "paper_id", name="p_values")

    # determine the traffic light ----
    tl = "info" if len(table) else "na"

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "summary_text": f"summary text{demo_arg}",
        "report": "This is the report text.",
    }
