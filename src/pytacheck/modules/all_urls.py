"""List All URLs (port of ``inst/modules/all_urls.R``)."""

from __future__ import annotations

from typing import Any

from pytacheck._r.frames import count
from pytacheck.module import module


@module(
    title="List All URLs",
    description="List all the URLs in the main text.",
    details="""
        Checks for valid URLs using a regular expression.
    """,
    keywords=["general"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def all_urls(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/all_urls.R::all_urls()``.

    Lists every URL in the main text of *paper* (a paper or paper list),
    one row per URL (:func:`~pytacheck.text.extract.extract_urls`), with the
    number of URLs per paper as the summary table. The traffic light is
    ``"info"`` when any URL was found and ``"na"`` otherwise.
    """
    from pytacheck.text.extract import extract_urls

    # detailed table of results ----
    table = extract_urls(paper)

    # summary output for paperlists ----
    summary_table = count(table, "paper_id", name="urls")

    # determine the traffic light ----
    tl = "info" if len(table) else "na"

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
    }
