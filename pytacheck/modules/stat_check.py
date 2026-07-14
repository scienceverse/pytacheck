from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, TypeGuard

from scipy.stats import f as f_distribution  # type: ignore[import-untyped]
from scipy.stats import t as t_distribution

from pytacheck.context import PaperContext
from pytacheck.modules.base import ModuleMetadata, ModuleResult, TrafficLight, register_module

_ALPHA = 0.05
_NO_TESTS_MESSAGE = (
    "No detectable t- or F-tests. StatCheck currently only detects statistics written "
    "in APA format."
)
_ERROR_REPORT_TEXT = (
    "We detected possible errors in test statistics. Note that the accuracy of statcheck "
    "has only been validated for *t*-tests and *F*-tests. As Metacheck only uses validated "
    "modules, we only provide statcheck results for *t*-tests and *F*-tests."
)


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_float(value: object) -> float | None:
    if not _is_number(value):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _compute_p(
    test_type: str,
    test_value: float,
    df1: float | None,
    df2: float,
) -> float:
    if test_type == "t":
        return float(2 * t_distribution.sf(abs(test_value), df2))
    if df1 is None:
        return math.nan
    return float(f_distribution.sf(test_value, df1, df2))


def _normalized_dfs(row: dict[str, Any]) -> tuple[int | float | None, int | float] | None:
    degrees_of_freedom = row.get("df")
    if not isinstance(degrees_of_freedom, list):
        return None

    test_type = row.get("test_type")
    if test_type == "t" and len(degrees_of_freedom) == 1:
        df2 = degrees_of_freedom[0]
        if _finite_float(df2) is None or float(df2) <= 0:
            return None
        return None, df2

    if test_type == "F" and len(degrees_of_freedom) == 2:
        df1, df2 = degrees_of_freedom
        if (
            _finite_float(df1) is None
            or _finite_float(df2) is None
            or float(df1) <= 0
            or float(df2) <= 0
        ):
            return None
        return df1, df2

    return None


def _compatible_p_interval(
    *,
    test_type: str,
    test_value: float,
    df1: float | None,
    df2: float,
    statistic_decimals: int,
) -> tuple[float, float]:
    half_unit = 0.5 / (10**statistic_decimals)
    if test_value >= 0:
        low_stat = test_value - half_unit
        up_stat = test_value + half_unit
    else:
        low_stat = test_value + half_unit
        up_stat = test_value - half_unit

    up_p = _compute_p(test_type, low_stat, df1, df2)
    low_p = _compute_p(test_type, up_stat, df1, df2)
    return low_p, up_p


def _is_error(
    *,
    reported_p: float | None,
    p_comp: str,
    test_comp: str,
    low_p: float,
    up_p: float,
    p_decimals: int | None,
) -> bool:
    checked_p = _ALPHA if p_comp == "ns" else reported_p
    checked_comp = ">" if p_comp == "ns" else p_comp
    if checked_p is None:
        return True
    if checked_p <= 0:
        return True

    if test_comp == "=":
        if checked_comp == "=":
            decimals = p_decimals or 0
            return checked_p > round(up_p, decimals) or checked_p < round(low_p, decimals)
        if checked_comp == "<":
            return checked_p < low_p
        if checked_comp == ">":
            return checked_p > up_p
    elif test_comp == "<":
        if checked_comp == "=":
            return checked_p < round(up_p, p_decimals or 0)
        if checked_comp == "<":
            return checked_p < up_p
        if checked_comp == ">":
            return False
    elif test_comp == ">":
        if checked_comp == "=":
            return checked_p > round(low_p, p_decimals or 0)
        if checked_comp == "<":
            return False
        if checked_comp == ">":
            return checked_p > low_p
    return True


def _is_decision_error(
    *,
    error: bool,
    reported_p: float | None,
    computed_p: float,
    test_comp: str,
    p_comp: str,
) -> bool:
    if not error:
        return False

    checked_p = _ALPHA if p_comp == "ns" else reported_p
    checked_comp = ">" if p_comp == "ns" else p_comp
    if checked_p is None:
        return False

    if test_comp == "=":
        if checked_comp == "=":
            return (checked_p <= _ALPHA < computed_p) or (checked_p > _ALPHA >= computed_p)
        if checked_comp == "<":
            return checked_p <= _ALPHA < computed_p
        if checked_comp == ">":
            return checked_p >= _ALPHA and computed_p <= _ALPHA
    elif test_comp == "<":
        if checked_comp in {"=", "<"}:
            return checked_p <= _ALPHA and computed_p >= _ALPHA
        return False
    elif test_comp == ">":
        if checked_comp == "=":
            return checked_p > _ALPHA and computed_p <= _ALPHA
        if checked_comp == "<":
            return False
        if checked_comp == ">":
            return checked_p >= _ALPHA and computed_p <= _ALPHA
    return False


def _checked_row(cached_row: dict[str, Any]) -> dict[str, Any] | None:
    test_type = cached_row.get("test_type")
    if test_type not in {"t", "F"}:
        return None

    normalized_dfs = _normalized_dfs(cached_row)
    test_value = _finite_float(cached_row.get("statistic"))
    if normalized_dfs is None or test_value is None:
        return None
    df1_value, df2_value = normalized_dfs
    df1 = _finite_float(df1_value)
    df2 = _finite_float(df2_value)
    if df2 is None or (test_type == "F" and df1 is None):
        return None
    statistic_decimals = cached_row.get("_statistic_decimals")
    p_decimals = cached_row.get("_p_decimals")
    if not isinstance(statistic_decimals, int) or isinstance(statistic_decimals, bool):
        return None
    if p_decimals is not None and (not isinstance(p_decimals, int) or isinstance(p_decimals, bool)):
        return None

    p_comp = cached_row.get("p_comp")
    test_comp = cached_row.get("comp")
    if p_comp not in {"=", "<", ">", "ns"} or test_comp not in {"=", "<", ">"}:
        return None
    reported_p = _finite_float(cached_row.get("p_value"))
    if p_comp != "ns" and reported_p is None:
        return None

    computed_p = _compute_p(test_type, test_value, df1, df2)
    low_p, up_p = _compatible_p_interval(
        test_type=test_type,
        test_value=test_value,
        df1=df1,
        df2=df2,
        statistic_decimals=statistic_decimals,
    )
    if not all(math.isfinite(value) for value in (computed_p, low_p, up_p)):
        return None

    error = _is_error(
        reported_p=reported_p,
        p_comp=p_comp,
        test_comp=test_comp,
        low_p=low_p,
        up_p=up_p,
        p_decimals=p_decimals,
    )
    decision_error = _is_decision_error(
        error=error,
        reported_p=reported_p,
        computed_p=computed_p,
        test_comp=test_comp,
        p_comp=p_comp,
    )

    row = deepcopy(cached_row)
    for internal_name in (
        "statistic",
        "df",
        "comp",
        "p_value",
        "_statistic_decimals",
        "_p_decimals",
    ):
        row.pop(internal_name, None)
    row.update(
        df1=df1_value,
        df2=df2_value,
        test_comp=test_comp,
        test_value=test_value,
        reported_p=reported_p,
        computed_p=computed_p,
        error=error,
        decision_error=decision_error,
    )
    return row


def _escape_table_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", r"\|").replace("\n", "<br>")


def _red_report(rows: list[dict[str, Any]]) -> str:
    report_rows = []
    for row in rows:
        if not row["error"]:
            continue
        section = row.get("section", row.get("header"))
        sentence = row.get("expanded", row.get("text"))
        report_rows.append(
            "| "
            f"{_escape_table_cell(row.get('raw'))} | "
            f"{_escape_table_cell(round(float(row['computed_p']), 5))} | "
            f"{_escape_table_cell(section)} | "
            f"{_escape_table_cell(sentence)} |"
        )
    report_table = "\n".join(
        [
            "| Text | Recomputed p | Section | Sentence |",
            "| --- | --- | --- | --- |",
            *report_rows,
        ]
    )
    return f"{_ERROR_REPORT_TEXT}\n\n{report_table}"


@register_module(
    ModuleMetadata(
        name="stat_check",
        title="StatCheck",
        description="Check consistency of p-values and test statistics",
        section="results",
        validated=True,
    )
)
def stat_check(context: PaperContext) -> ModuleResult:
    table = [
        row for cached_row in context.apa_tests if (row := _checked_row(cached_row)) is not None
    ]
    found = len(table)
    errors = sum(bool(row["error"]) for row in table)
    decision_errors = sum(bool(row["decision_error"]) for row in table)

    if not table:
        traffic_light: TrafficLight = "na"
        summary_text = _NO_TESTS_MESSAGE
        report = summary_text
    elif errors == 0:
        traffic_light = "green"
        summary_text = "We detected no errors in t-tests or F-tests."
        report = summary_text
    else:
        traffic_light = "red"
        suffix = "" if errors == 1 else "s"
        summary_text = f"{errors} possible error{suffix} in t-tests or F-tests"
        report = _red_report(table)

    return ModuleResult(
        module="stat_check",
        title="StatCheck",
        table=table,
        summary_table=[
            {
                "paper_id": context.paper_id,
                "statcheck_found": found,
                "statcheck_errors": errors,
                "statcheck_decision_errors": decision_errors,
            }
        ],
        summary_text=summary_text,
        report=report,
        traffic_light=traffic_light,
    )


__all__ = ["stat_check"]
