from __future__ import annotations

import re
from typing import Any

from pytacheck.context import PaperContext
from pytacheck.modules.base import ModuleMetadata, ModuleResult, TrafficLight, register_module
from pytacheck.text import search_rows

_POWER_PATTERN = re.compile(r"\bpower(?:ed|s)?\b", re.IGNORECASE)
_POWER_ANALYSIS_PATTERN = re.compile(
    r"power analy|"
    r"effect size|"
    r"sized? effect|"
    r"sample[- ]size|"
    r"g[-* ]?power|"
    r"a[- ]?priori|"
    r"a[- ]?posteriori|"
    r"post[- ]?hoc|"
    r"sensitivity|"
    r"pwr|"
    r"statistical power|"
    r"to detect|"
    r"achieve|"
    r"(?:small|medium|large) effect|"
    r"observed power|"
    r"a power of|"
    r"%|"
    r"power\s*=",
    re.IGNORECASE,
)
_NON_YEAR_NUMBER_PATTERN = re.compile(r"\b(?!\d{4}\b)\d+(?:[.,]\d+)?\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_APRIORI_PATTERN = re.compile(r"a[- ]?priori", re.IGNORECASE)
_SENSITIVITY_PATTERN = re.compile(r"sensitivity", re.IGNORECASE)
_COMPROMISE_PATTERN = re.compile(r"compromise power", re.IGNORECASE)
_POSTHOC_PATTERN = re.compile(
    r"a[- ]?posteriori|post[- ]?hoc|retrospective",
    re.IGNORECASE,
)

_REGEX_REPORT = (
    "You chose to not use an LLM to assess if all information was reported, so please check "
    "for all required information manually."
)


def _power_type(text: str) -> str:
    if _APRIORI_PATTERN.search(text) is not None:
        return "apriori"
    if _SENSITIVITY_PATTERN.search(text) is not None:
        return "sensitivity"
    if _COMPROMISE_PATTERN.search(text) is not None:
        return "compromise"
    if _POSTHOC_PATTERN.search(text) is not None:
        return "posthoc"
    return "unknown"


def _candidate_rows(context: PaperContext) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in search_rows(context.paragraphs, _POWER_PATTERN):
        text = row["text"]
        if not isinstance(text, str):
            continue
        search_text = _WHITESPACE_PATTERN.sub(" ", text)
        if _POWER_ANALYSIS_PATTERN.search(search_text) is None:
            continue
        if _NON_YEAR_NUMBER_PATTERN.search(search_text) is None:
            continue

        row["power_type"] = _power_type(search_text)
        row["complete"] = None
        row["power_id"] = len(candidates) + 1
        candidates.append(row)
    return candidates


@register_module(
    ModuleMetadata(
        name="power",
        title="Power Analysis Check",
        description=(
            "This module uses uses regular expressions to identify sentences that contain a "
            "statistical power analysis. If specified by the user, it also uses a large language "
            "module (LLM) to extract information reported in power analyses, including the "
            "statistical test, sample size, alpha level, desired level of power, and magnitude and "
            "type of effect size."
        ),
        section="method",
        validated=True,
    )
)
def power(context: PaperContext) -> ModuleResult:
    table = _candidate_rows(context)
    count = len(table)
    if count:
        traffic_light: TrafficLight = "yellow"
        noun = "analysis" if count == 1 else "analyses"
        summary_text = f"We detected {count} potential power {noun}."
        report = _REGEX_REPORT
    else:
        traffic_light = "na"
        summary_text = "No power analyses were detected."
        report = summary_text

    return ModuleResult(
        module="power",
        title="Power Analysis Check",
        table=table,
        summary_table=[
            {
                "paper_id": context.paper_id,
                "power_n": count,
                "power_complete": None,
            }
        ],
        summary_text=summary_text,
        report=report,
        traffic_light=traffic_light,
    )


__all__ = ["power"]
