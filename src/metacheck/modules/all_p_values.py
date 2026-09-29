"""List All P-Values (port of ``inst/modules/all_p_values.R``)."""

from __future__ import annotations

from typing import Any

from metacheck._r.frames import count
from metacheck.module import module
from metacheck.report import scroll_table

_EXPLAN = (
    "This will catch most comparators like =<>~≈≠≤≥≪≫ and most versions of scientific "
    "notation like 5.0 x 10^-2 or 5.0e-2. If you find any formats that are not correctly "
    "handled by this function, please contact debruine@gmail.com."
)


@module(
    title="List All P-Values",
    description=(
        "List all p-values in the text, returning the matched text (e.g., 'p = 0.04') and "
        "document location in a table."
    ),
    details=r"""
        Note that this will not catch p-values reported like "the p-value is 0.03" because that results in a ton of false positives when papers discuss p-value thresholds. If you need to detect text like that, use `text_search()` function and a custom pattern like "\\bp(-| )?values?\\s+.{1,20}\\s+[0-9\\.]+"

        This will catch most comparators like =<>~≈≠≤≥≪≫ and most versions of scientific notation like 5.0 x 10^-2 or 5.0e-2. If you find any formats that are not correctly handled by this function, please contact the author.

        This module only checks p-values reported in the running text of the manuscript. It cannot (yet) process p-values reported only in tables.
    """,
    keywords=["results"],
    author=["Lisa DeBruine <lisa.debruine@glasgow.ac.uk>"],
    params={"paper": "a paper object or paperlist object"},
)
def all_p_values(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/all_p_values.R::all_p_values()``."""
    from metacheck.text.expand import text_expand
    from metacheck.text.extract import extract_p_values

    # is its own function now
    p = extract_p_values(paper)

    # summary_table ----
    summary_table = count(p, "paper_id", name="p_values")

    # traffic_light ----
    n = len(p)
    tl = "info" if n else "na"

    # summary_text ----
    summary_text = f"We found {n:d} p-value{'' if n == 1 else 's'}"

    # report ----
    report_table = text_expand(p, paper).loc[:, ["text", "expanded"]]
    report_table.columns = ["Text", "Sentence"]
    report_table_code = scroll_table(report_table, colwidths=["5em", None])
    report = [report_table_code, _EXPLAN]

    # return a list ----
    return {
        "table": p,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
