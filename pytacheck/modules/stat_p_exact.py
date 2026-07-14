from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard

from pytacheck.context import PaperContext
from pytacheck.modules.base import ModuleMetadata, ModuleResult, TrafficLight, register_module

_IMPRECISE_REPORT_TEXT = (
    "Reporting *p* values imprecisely (e.g., *p* < .05) reduces transparency, "
    "reproducibility, and re-use (e.g., in *p* value meta-analyses). Best practice "
    "is to report exact p-values with three decimal places (e.g., *p* = .032) unless "
    "*p* values are smaller than 0.001, in which case you can use *p* < .001."
)

_IMPRECISE_GUIDANCE = (
    "### Learn More\n\n"
    "The APA manual states: Report exact *p* values (e.g., *p* = .031) to two or "
    "three decimal places. However, report *p* values less than .001 as *p* < .001. "
    "Two decimals are too imprecise for many uses, including *p* value meta-analysis, "
    "so report *p* values with three digits.\n\n"
    "American Psychological Association. (2020). *Publication manual of the American "
    "Psychological Association* (7th ed.). American Psychological Association."
)

_ZERO_REPORT_TEXT = (
    "*P* values are never exactly zero. A *p* value of .000 is a rounding artifact — "
    "the actual value is simply smaller than the reported precision. Very small *p* "
    "values should be reported as *p* < .001 rather than *p* = .000."
)

_ZERO_GUIDANCE = (
    "### Learn More\n\n"
    "The APA manual states: report *p* values less than .001 as *p* < .001.\n\n"
    "American Psychological Association. (2020). *Publication manual of the American "
    "Psychological Association* (7th ed.). American Psychological Association."
)


def _is_numeric(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_imprecise(row: Mapping[str, Any]) -> bool:
    if row.get("_p_valid") is False:
        return True
    comparator = row.get("p_comp")
    value = row.get("p_value")
    imprecise = (
        (comparator == "<" and _is_numeric(value) and value > 0.001)
        or comparator not in {"=", "<"}
        or value is None
    )
    is_star_note = row.get("_star_note") is True
    return imprecise and not is_star_note


def _is_zero(row: Mapping[str, Any]) -> bool:
    value = row.get("p_value")
    lexical_zero = row.get("_p_lexical_zero")
    if isinstance(lexical_zero, bool):
        return row.get("p_comp") == "=" and lexical_zero
    return row.get("p_comp") == "=" and _is_numeric(value) and value == 0


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _escape_table_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", r"\|").replace("\n", "<br>")


def _unique_issue_rows(rows: list[dict[str, Any]]) -> list[tuple[object, object]]:
    unique_rows: list[tuple[object, object]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        text = row.get("text")
        expanded = row.get("expanded")
        key = (str(text), str(expanded))
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append((text, expanded))
    return unique_rows


def _report_table(rows: list[dict[str, Any]]) -> str:
    table_rows = [
        f"| {_escape_table_cell(text)} | {_escape_table_cell(expanded)} |"
        for text, expanded in _unique_issue_rows(rows)
    ]
    return "\n".join(["| P-Value | Text |", "| --- | --- |", *table_rows])


def _red_report(
    imprecise_rows: list[dict[str, Any]],
    zero_rows: list[dict[str, Any]],
) -> str:
    sections: list[str] = []
    if imprecise_rows:
        sections.append(
            f"{_IMPRECISE_REPORT_TEXT}\n\n{_report_table(imprecise_rows)}\n\n{_IMPRECISE_GUIDANCE}"
        )
    if zero_rows:
        sections.append(f"{_ZERO_REPORT_TEXT}\n\n{_report_table(zero_rows)}\n\n{_ZERO_GUIDANCE}")
    return "\n\n".join(sections)


@register_module(
    ModuleMetadata(
        name="stat_p_exact",
        title="Exact P-Values",
        description=(
            "List any p-values reported with insufficient precision (e.g., p < .05 or "
            "p = n.s.) or reported as exactly zero (e.g., p = .000)."
        ),
        section="results",
        validated=True,
    )
)
def stat_p_exact(context: PaperContext) -> ModuleResult:
    table: list[dict[str, Any]] = []
    for cached_row in context.p_values:
        row = _public_row(cached_row)
        if cached_row.get("_p_valid") is False:
            row["invalid"] = True
        row["imprecise"] = _is_imprecise(cached_row)
        row["zero"] = _is_zero(cached_row)
        table.append(row)

    imprecise_rows = [row for row in table if row["imprecise"]]
    zero_rows = [row for row in table if row["zero"]]
    n_imprecise = len(imprecise_rows)
    n_zero = len(zero_rows)
    n_unique_imprecise = len(_unique_issue_rows(imprecise_rows))
    n_unique_zero = len(_unique_issue_rows(zero_rows))
    count = len(table)

    if count == 0:
        traffic_light: TrafficLight = "na"
        summary_text = "We detected no *p* values."
        report = summary_text
    elif n_imprecise == 0 and n_zero == 0:
        traffic_light = "green"
        summary_text = (
            "We found no imprecise *p* values or *p*-values of exactly zero "
            f"out of {count} detected."
        )
        report = summary_text
    else:
        traffic_light = "red"
        summary_parts: list[str] = []
        if n_imprecise:
            summary_parts.append(
                f"{n_unique_imprecise} imprecise *p* value{_plural(n_unique_imprecise)}"
            )
        if n_zero:
            summary_parts.append(
                f"{n_unique_zero} *p* value{_plural(n_unique_zero)} reported as exactly zero"
            )
        summary_text = (
            f"We found {' and '.join(summary_parts)} out of {count} detected "
            f"*p* value{_plural(count)}."
        )
        report = _red_report(imprecise_rows, zero_rows)

    return ModuleResult(
        module="stat_p_exact",
        title="Exact P-Values",
        table=table,
        summary_table=[
            {
                "paper_id": context.paper_id,
                "n_imprecise": n_imprecise,
                "n_zero": n_zero,
            }
        ],
        summary_text=summary_text,
        report=report,
        traffic_light=traffic_light,
    )


__all__ = ["stat_p_exact"]
