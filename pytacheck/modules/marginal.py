from __future__ import annotations

from collections import Counter
from typing import Any

from pytacheck.context import PaperContext
from pytacheck.modules.base import ModuleMetadata, ModuleResult, TrafficLight, register_module
from pytacheck.text import search_rows

_MARGINAL_PATTERN = (
    r"margin\w* (?:\w+\s+){0,5}significan\w*|"
    r"trend\w* (?:\w+\s+){0,1}significan\w*|"
    r"almost (?:\w+\s+){0,2}significan\w*|"
    r"approach\w* (?:\w+\s+){0,2}significan\w*|"
    r"border\w* (?:\w+\s+){0,2}significan\w*|"
    r"close to (?:\w+\s+){0,2}significan\w*"
)

_REPORT_TEXT = (
    "You described effects with terms related to 'marginally significant'. "
    "If *p* values above 0.05 are interpreted as an effect, you inflate the alpha level, "
    "and increase the Type 1 error rate. If a *p* value is higher than the prespecified "
    "alpha level, it should be interpreted as a non-significant result."
)

_REFERENCE = (
    "Olsson-Collentine, A., van Assen, M. A. L. M., & Hartgerink, C. H. J. (2019). "
    "The Prevalence of Marginally Significant Results in Psychology Over Time. "
    "*Psychological Science*, 30(4), 576-586. "
    "https://doi.org/10.1177/0956797619830326"
)

_BLOG_URL = (
    "https://web.archive.org/web/20251001114321/"
    "https://mchankins.wordpress.com/2013/04/21/still-not-significant-2/"
)


def _paper_counts(
    context: PaperContext,
    table: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = Counter(row.get("paper_id") for row in table)
    paper_ids = dict.fromkeys(row.get("paper_id") for row in context.sentences)
    return [
        {"paper_id": paper_id, "marginal": counts[paper_id]}
        for paper_id in paper_ids
        if paper_id is not None
    ]


def _escape_table_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", r"\|").replace("\n", "<br>")


def _red_report(table: list[dict[str, Any]]) -> str:
    rows = [
        f"| {_escape_table_cell(row.get('text'))} | {_escape_table_cell(row.get('header'))} |"
        for row in table
    ]
    report_table = "\n".join(["| Text | Section Header |", "| --- | --- |", *rows])
    guidance = (
        "### Learn More\n\n"
        "For metascientific articles demonstrating the rate at which non-significant "
        "p-values are interpreted as marginally significant, see:\n\n"
        f"{_REFERENCE}\n\n"
        "For the list of terms used to identifify marginally significant results, see "
        f"this [blog post by Matthew Hankins]({_BLOG_URL})."
    )
    return f"{_REPORT_TEXT}\n\n{report_table}\n\n{guidance}"


@register_module(
    ModuleMetadata(
        name="marginal",
        title="Marginal Significance",
        description="List all sentences that describe an effect as 'marginally significant'.",
        section="results",
        validated=True,
    )
)
def marginal(context: PaperContext) -> ModuleResult:
    table = list(search_rows(context.sentences, _MARGINAL_PATTERN))
    count = len(table)
    traffic_light: TrafficLight = "red" if count else "green"
    suffix = "" if count == 1 else "s"
    summary_text = (
        f"You described {count} effect{suffix} with terms related to 'marginally significant'."
    )
    report = (
        _red_report(table)
        if table
        else "No effects were described with terms related to 'marginally significant'."
    )

    return ModuleResult(
        module="marginal",
        title="Marginal Significance",
        table=table,
        summary_table=_paper_counts(context, table),
        summary_text=summary_text,
        report=report,
        traffic_light=traffic_light,
    )


__all__ = ["marginal"]
