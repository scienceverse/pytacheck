"""Marginal Significance (port of ``inst/modules/marginal.R``)."""

from __future__ import annotations

from typing import Any

from metacheck._r.frames import count
from metacheck.module import module
from metacheck.report import collapse_section, format_ref, scroll_table
from metacheck.text import text_search

# R's format_ref() (bibentry html style) of marg_ref with its authors given as person()
# objects. metacheck passes them as one "Last, F., ..., & Last, F." string, which the
# bibentry formatter mangles into "van Assen, M. MAL, Hartgerink &amp;, J. CH" (U3).
_MARG_REF = (
    "Olsson-Collentine A, van Assen MALM, Hartgerink CHJ (2019). "
    "&ldquo;The Prevalence of Marginally Significant Results in Psychology Over Time.&rdquo; "
    "<em>Psychological Science</em>, <b>30</b>, 576&ndash;586. "
    '<a href="https://doi.org/10.1177/0956797619830326">doi:10.1177/0956797619830326</a>.'
)

_PATTERN = (
    r"margin\w* (?:\w+\s+){0,5}significan\w*|trend\w* (?:\w+\s+){0,1}significan\w*"
    r"|almost (?:\w+\s+){0,2}significan\w*|approach\w* (?:\w+\s+){0,2}significan\w*"
    r"|border\w* (?:\w+\s+){0,2}significan\w*|close to (?:\w+\s+){0,2}significan\w*"
)

_HANKINS = (
    "For the list of terms used to identifify marginally significant results, see this "
    "[blog post by Matthew Hankins](https://web.archive.org/web/20251001114321/"
    "https://mchankins.wordpress.com/2013/04/21/still-not-significant-2/)."
)


@module(
    title="Marginal Significance",
    description="List all sentences that describe an effect as 'marginally significant'.",
    details="""
        The marginal module searches for regular expressions that match a predefined pattern. The list of terms is a subset of those listed in a [blog post by Matthew Hankins](https://web.archive.org/web/20251001114321/https://mchankins.wordpress.com/2013/04/21/still-not-significant-2/). The module returns all sentences that match terms describing ‘marginally significant’ results.

        Some of the terms identified might not be problematic in some contexts, and there are ways to describe ‘marginal significance’ that are not detected by the module.

        <validation>In a sample of 51 papers with 87 statements, this module correctly identified 38 statements (true positives) and incorrectly flagged 22 statements (false positives). It failed to detect 27 statements. Thus, among all statements flagged by the module, 63% were genuine cases (positive predictive value). However, the module missed 42% of all true statements (false negative rate).</validation>
    """,
    keywords=["results"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
)
def marginal(paper: Any) -> dict[str, Any]:
    table = text_search(paper, _PATTERN)
    summary_table = count(table, "paper_id", name="marginal")
    n = len(table)
    tl = "red" if n else "green"
    summary_text = (
        f"You described {n} effect{'' if n == 1 else 's'} with terms related to "
        "'marginally significant'."
    )
    if tl == "green":
        report: Any = "No effects were described with terms related to 'marginally significant'."
    else:
        report_text = (
            "You described effects with terms related to 'marginally significant'. If *p* values "
            "above 0.05 are interpreted as an effect, you inflate the alpha level, and increase "
            "the Type 1 error rate. If a *p* value is higher than the prespecified alpha level, "
            "it should be interpreted as a non-significant result."
        )
        guidance = [
            "For metascientific articles demonstrating the rate at which non-significant p-values "
            "are interpreted as marginally significant, see:",
            format_ref(_MARG_REF),
            _HANKINS,
        ]
        report_table = table.loc[:, ["text", "header"]].rename(
            columns={"text": "Text", "header": "Section Header"}
        )
        report = [report_text, scroll_table(report_table), collapse_section(guidance)]

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
