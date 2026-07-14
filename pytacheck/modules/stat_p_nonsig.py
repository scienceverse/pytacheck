from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeGuard

from pytacheck.context import PaperContext
from pytacheck.modules.base import ModuleMetadata, ModuleResult, TrafficLight, register_module

_EXPLANATION = (
    "Meta-scientific research has shown nonsignificant p values are commonly "
    "misinterpreted. It is incorrect to infer that there is 'no effect', 'no difference', "
    "or that groups are 'the same' after p > 0.05.\n\n"
    "It is possible that there is a true non-zero effect, but that the study did not "
    "detect it. Make sure your inference acknowledges that it is possible that there is "
    "a non-zero effect. It is correct to conclude the effect is 'not significantly' "
    "different, although this just restates that p > 0.05.\n\n"
    "Metacheck does not yet analyze automatically whether sentences which include "
    "non-significant p-values are correct, but we recommend manually checking the "
    "sentences below for possible misinterpreted non-significant p values."
)

_GUIDANCE = (
    "### Learn More\n\n"
    "For metascientific articles demonstrating that non-significant results are often "
    "misinterpreted, see:\n\n"
    "Aczel, B., Palfi, B., Szollosi, A., Kovacs, M., Szaszi, B., Szecsi, P., Zrubka, M., "
    "Gronau, Q. F., van den Bergh, D., & Wagenmakers, E.-J. (2018). Quantifying Support "
    "for the Null Hypothesis in Psychology: An Empirical Investigation. *Advances in "
    "Methods and Practices in Psychological Science*, 1(3), 357-366. "
    "https://doi.org/10.1177/2515245918773742\n\n"
    "Murphy, S. L., Merz, R., Reimann, L.-E., & Fernández, A. (2025). Nonsignificance "
    "misinterpreted as an effect's absence in psychology: Prevalence and temporal "
    "analyses. *Royal Society Open Science*, 12(3), 242167. "
    "https://doi.org/10.1098/rsos.242167\n\n"
    "For educational material on preventing the misinterpretation of p values, see "
    "[Improving Your Statistical Inferences]"
    "(https://lakens.github.io/statistical_inferences/01-pvalue.html#sec-misconception1)."
)


def _is_numeric(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_significant(row: dict[str, Any]) -> bool:
    value = row.get("p_value")
    return _is_numeric(value) and value <= 0.05 and row.get("p_comp") in {"=", "<"}


def _escape_table_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", r"\|").replace("\n", "<br>")


def _report_table(rows: list[dict[str, Any]]) -> str:
    table_rows = [
        f"| {_escape_table_cell(row.get('text'))} | {_escape_table_cell(row.get('expanded'))} |"
        for row in rows
    ]
    return "\n".join(["| Text | Sentence |", "| --- | --- |", *table_rows])


def _yellow_report(rows: list[dict[str, Any]]) -> str:
    return f"{_EXPLANATION}\n\n{_report_table(rows)}\n\n{_GUIDANCE}"


@register_module(
    ModuleMetadata(
        name="stat_p_nonsig",
        title="Non-Significant P Value Check",
        description=(
            "This module checks for imprecisely reported p values. If p > .05 is "
            "detected, it warns for misinterpretations."
        ),
        section="results",
        validated=True,
    )
)
def stat_p_nonsig(context: PaperContext) -> ModuleResult:
    table: list[dict[str, Any]] = []
    for cached_row in context.p_values:
        if _is_significant(cached_row):
            continue
        row = deepcopy(cached_row)
        row["significance"] = "nonsignificant"
        table.append(row)

    count = len(table)
    if count:
        traffic_light: TrafficLight = "yellow"
        suffix = "" if count == 1 else "s"
        summary_text = (
            f"We found {count} non-significant p value{suffix} that should be checked "
            "for appropriate interpretation."
        )
        report = _yellow_report(table)
    else:
        traffic_light = "green"
        summary_text = "We detected no nonsignificant p values."
        report = summary_text

    return ModuleResult(
        module="stat_p_nonsig",
        title="Non-Significant P Value Check",
        table=table,
        summary_table=[
            {
                "paper_id": context.paper_id,
                "n_nonsignificant": count,
            }
        ],
        summary_text=summary_text,
        report=report,
        traffic_light=traffic_light,
    )


__all__ = ["stat_p_nonsig"]
