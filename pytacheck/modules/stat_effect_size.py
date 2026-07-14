from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from pytacheck.context import PaperContext
from pytacheck.modules.base import ModuleMetadata, ModuleResult, TrafficLight, register_module

_TOLERANCE = 0.01
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_INTEGER_F_DF_PATTERN = re.compile(r"^\(\s*[0-9]+\s*,\s*[0-9]+\s*\)$")
_T_STAT_PATTERN = re.compile(
    rf"\bt\s*\(\s*(?P<df>[0-9]+(?:\.[0-9]+)?)\s*\)\s*=\s*"
    rf"(?P<value>{_NUMBER})"
)
_F_STAT_PATTERN = re.compile(
    rf"\bF\s*\(\s*(?P<df1>[0-9]+(?:\.[0-9]+)?)\s*,\s*"
    rf"(?P<df2>[0-9]+(?:\.[0-9]+)?)\s*\)\s*=\s*(?P<value>{_NUMBER})"
)
_D_STAT_PATTERN = re.compile(
    rf"\b(?P<label>"
    rf"cohen(?:['’])?s\s+d\s*z|cohen(?:['’])?s\s+d|d\s*z|ds|d"
    rf")\b\s*[=≈<>≤≥]{{1,3}}\s*(?P<value>{_NUMBER})",
    re.IGNORECASE,
)
_ETA_STAT_PATTERN = re.compile(
    rf"^\s*(?P<label>[^=≈<>≤≥;]+?)\s*[=≈<>≤≥]{{1,3}}\s*"
    rf"(?P<value>{_NUMBER})\s*$",
    re.IGNORECASE,
)

_NO_TESTS_MESSAGE = "No t-tests or F-tests were detected."
_GREEN_MESSAGE = (
    "All detected t-tests and F-tests had an effect size reported in the same sentence."
)
_NO_MATCH_MESSAGE = (
    "All effect sizes were reported, but some appear inconsistent with the test statistic. "
    "This can be because the effect size is not clearly labeled (e.g., d, instead of d_rm), "
    "because the effect sizes is not reported with enough precisions (e.g., 0.3 instead of "
    "0.32), or because the effect size is incorrectly reported."
)
_GUIDANCE = (
    "### Learn More\n\n"
    "Effect sizes should accompany t-tests and F-tests. When a reported effect size cannot be "
    "reconstructed from the test statistic alone, verify the design assumptions and calculation "
    "manually."
)


def _r_number(value: float) -> str:
    """Format a finite number like R's character columns in the upstream table."""
    return format(value, ".15g")


def _equation_text(row: dict[str, Any]) -> str:
    lhs = str(row.get("lhs", ""))
    df = row.get("df")
    df_text = df if isinstance(df, str) else ""
    return f"{lhs}{df_text} {row.get('comp', '')} {row.get('rhs', '')}"


def _equation_kind(row: dict[str, Any]) -> str | None:
    lhs = row.get("lhs")
    if not isinstance(lhs, str):
        return None
    if lhs == "t":
        return "t-test"
    if lhs == "F":
        df = row.get("df")
        if isinstance(df, str) and _INTEGER_F_DF_PATTERN.fullmatch(df) is not None:
            return "F-test"
        return None

    raw = re.sub(r"\s+", "", lhs.casefold()).replace("²", "2")
    is_effect_size = (
        re.fullmatch(r"(?:cohen.{0,3})?d(?:_?(?:z|s|av|rm))?", raw) is not None
        or re.fullmatch(r"(?:hedge.{0,3})?g", raw) is not None
        or re.fullmatch(r"f2?", raw) is not None
        or "cohen" in raw
        or "ω" in raw
        or "omega" in raw
        or "η" in raw
        or "eta" in raw
        or raw in {"ξ", "β", "b", "r"}
    )
    return "es" if is_effect_size else None


def _empty_d_coherence() -> dict[str, Any]:
    return {
        "d_reported": None,
        "d_reported_text": None,
        "t_value": None,
        "df": None,
        "d_implied_paired_dz": None,
        "d_implied_paired_drm_r05": None,
        "d_implied_indep_equal_n": None,
        "d_implied_indep_unequal_min": None,
        "d_implied_indep_unequal_max": None,
        "d_implied_n": None,
        "d_coherence": None,
        "d_coherence_assumption": None,
        "d_coherence_note": None,
    }


def _unequal_d(abs_t: float, n1: int, n_total: int) -> float:
    n2 = n_total - n1
    return abs_t * math.sqrt(1 / n1 + 1 / n2)


def _closest_unequal_split(abs_t: float, n_total: int, reported_d: float) -> int:
    """Return R's first closest n1 without materializing every possible split."""
    half = n_total // 2
    if abs_t == 0:
        return 2

    minimum = _unequal_d(abs_t, half, n_total)
    maximum = _unequal_d(abs_t, 2, n_total)
    if reported_d <= minimum:
        return half
    if reported_d >= maximum:
        return 2

    squared_ratio = (reported_d / abs_t) ** 2
    discriminant = max(0.0, n_total**2 - 4 * n_total / squared_ratio)
    root = (2 * n_total / squared_ratio) / (n_total + math.sqrt(discriminant))
    center = math.floor(root)
    candidates = {2, half}
    for offset in range(-2, 3):
        candidates.add(min(half, max(2, center + offset)))
    return min(
        candidates,
        key=lambda n1: (abs(_unequal_d(abs_t, n1, n_total) - reported_d), n1),
    )


def _classify_d(test: str, test_text: str, effect_text: str | None) -> dict[str, Any]:
    output = _empty_d_coherence()
    if test != "t-test":
        return output

    test_match = _T_STAT_PATTERN.search(test_text)
    if test_match is None:
        output.update(
            d_coherence="indeterminate",
            d_coherence_assumption="none",
            d_coherence_note="No parseable t(df)=value found.",
        )
        return output

    d_matches = list(_D_STAT_PATTERN.finditer(effect_text or ""))
    if not d_matches:
        output.update(
            d_coherence="indeterminate",
            d_coherence_assumption="none",
            d_coherence_note="No parseable d effect size found.",
        )
        return output

    t_value = float(test_match.group("value"))
    df = float(test_match.group("df"))
    output["t_value"] = _r_number(t_value)
    output["df"] = _r_number(df)

    if abs(df - round(df)) > 1e-8:
        output.update(
            d_coherence="indeterminate",
            d_coherence_assumption="none",
            d_coherence_note=(
                "Non-integer df indicates Welch's t-test (unequal variances); sample sizes "
                "cannot be determined."
            ),
        )
        return output

    abs_t = abs(t_value)
    d_paired_dz = abs_t / math.sqrt(df + 1)
    d_paired_drm = d_paired_dz / math.sqrt(1 - 0.5)
    d_equal = 2 * abs_t / math.sqrt(df + 2)
    output.update(
        d_implied_paired_dz=_r_number(d_paired_dz),
        d_implied_paired_drm_r05=_r_number(d_paired_drm),
        d_implied_indep_equal_n=_r_number(d_equal),
    )

    n_total_float = df + 2
    use_unequal = (
        math.isfinite(n_total_float)
        and abs(n_total_float - round(n_total_float)) < 1e-8
        and n_total_float >= 4
    )
    n_total = int(round(n_total_float))
    d_unequal_min: float | None = None
    d_unequal_max: float | None = None
    if use_unequal:
        d_unequal_min = _unequal_d(abs_t, n_total // 2, n_total)
        d_unequal_max = _unequal_d(abs_t, 2, n_total)
        output.update(
            d_implied_indep_unequal_min=_r_number(d_unequal_min),
            d_implied_indep_unequal_max=_r_number(d_unequal_max),
        )

    reported_values = [float(match.group("value")) for match in d_matches]
    absolute_reported = [abs(value) for value in reported_values]
    output["d_reported"] = _r_number(reported_values[0])
    output["d_reported_text"] = d_matches[0].group(0)

    paired_match = any(abs(value - d_paired_dz) <= _TOLERANCE for value in absolute_reported)
    equal_match = any(abs(value - d_equal) <= _TOLERANCE for value in absolute_reported)
    unequal_match = (
        use_unequal
        and d_unequal_min is not None
        and d_unequal_max is not None
        and any(
            d_unequal_min - _TOLERANCE <= value <= d_unequal_max + _TOLERANCE
            for value in absolute_reported
        )
    )

    if paired_match:
        output.update(
            d_implied_n=f"n = {int(df + 1)}",
            d_coherence="match_under_assumptions",
            d_coherence_assumption="paired_dz",
            d_coherence_note="Match under paired-samples dz assumption.",
        )
    elif equal_match:
        n_each = n_total_float / 2
        n_each_text = _r_number(n_each)
        n_total_text = _r_number(n_total_float)
        implied_n = f"n1 = n2 = {n_each_text}, N = {n_total_text}"
        output.update(
            d_implied_n=implied_n,
            d_coherence="match_under_assumptions",
            d_coherence_assumption="independent_equal_n",
            d_coherence_note=(f"Match under independent-samples equal-n assumption ({implied_n})."),
        )
    elif unequal_match and d_unequal_min is not None and d_unequal_max is not None:
        matching_d = next(
            value
            for value in absolute_reported
            if d_unequal_min - _TOLERANCE <= value <= d_unequal_max + _TOLERANCE
        )
        best_n1 = _closest_unequal_split(abs_t, n_total, matching_d)
        best_n2 = n_total - best_n1
        implied_n = f"n1 = {best_n1}, n2 = {best_n2}, N = {n_total}"
        output.update(
            d_implied_n=implied_n,
            d_coherence="match_under_assumptions",
            d_coherence_assumption="independent_unequal_n_range",
            d_coherence_note=(
                "Match under independent-samples unequal-n range assumption "
                f"(closest split {implied_n})."
            ),
        )
    else:
        output.update(
            d_coherence="no_match",
            d_coherence_assumption="none",
            d_coherence_note=(
                "No match under tested assumptions (paired dz, independent equal-n, "
                "independent unequal-n range). Tolerance = 0.01. A no-match can occur when "
                "fewer than 2 decimal places are reported."
            ),
        )
    return output


def _empty_f_coherence() -> dict[str, Any]:
    return {
        "f_reported": None,
        "f_reported_text": None,
        "df1": None,
        "df2": None,
        "eta_implied_partial": None,
        "omega_implied_partial": None,
        "eta_coherence": None,
        "eta_coherence_assumption": None,
        "eta_coherence_note": None,
    }


def _eta_stats(effect_text: str | None) -> list[tuple[str, float, str]]:
    if not effect_text:
        return []
    parsed: list[tuple[str, float, str]] = []
    for part in re.split(r"\s*;\s*", effect_text.strip()):
        match = _ETA_STAT_PATTERN.fullmatch(part)
        if match is None:
            continue
        raw_label = re.sub(r"\s+", "", match.group("label").casefold()).replace("²", "2")
        if raw_label.startswith(("ξ", "bf")):
            label = "non_checkable"
        elif re.fullmatch(r"f2?", raw_label) is not None or "cohen" in raw_label:
            label = "cohens_f"
        elif "ω" in raw_label or "omega" in raw_label:
            label = "non_checkable"
        elif ("η" in raw_label or "eta" in raw_label) and (
            "partial" in raw_label or "p" in raw_label
        ):
            label = "partial_eta_squared"
        else:
            label = "eta_squared"
        parsed.append((label, float(match.group("value")), part))
    return parsed


def _classify_f(test: str, test_text: str, effect_text: str | None) -> dict[str, Any]:
    output = _empty_f_coherence()
    if test != "F-test":
        return output

    test_match = _F_STAT_PATTERN.search(test_text)
    if test_match is None:
        output.update(
            eta_coherence="indeterminate",
            eta_coherence_assumption="none",
            eta_coherence_note="No parseable F(df1,df2)=value found.",
        )
        return output

    f_value = float(test_match.group("value"))
    df1 = float(test_match.group("df1"))
    df2 = float(test_match.group("df2"))
    output.update(
        f_reported=_r_number(f_value),
        f_reported_text=test_match.group(0),
        df1=_r_number(df1),
        df2=_r_number(df2),
    )

    abs_f = abs(f_value)
    eta_denominator = df1 * abs_f + df2
    omega_denominator = eta_denominator + 1
    if eta_denominator == 0 or omega_denominator == 0:
        output.update(
            eta_coherence="indeterminate",
            eta_coherence_assumption="none",
            eta_coherence_note="Effect size could not be reconstructed from F and dfs.",
        )
        return output

    eta_implied = (df1 * abs_f) / eta_denominator
    omega_implied = (df1 * (abs_f - 1)) / omega_denominator
    output.update(
        eta_implied_partial=_r_number(eta_implied),
        omega_implied_partial=_r_number(omega_implied),
    )

    eta_stats = _eta_stats(effect_text)
    if not eta_stats:
        output.update(
            eta_coherence="indeterminate",
            eta_coherence_assumption="none",
            eta_coherence_note="No parseable eta-squared effect size found.",
        )
        return output

    cohens_f_present = any(label == "cohens_f" for label, _, _ in eta_stats)
    checkable = [stat for stat in eta_stats if stat[0] not in {"cohens_f", "non_checkable"}]
    if not checkable:
        output.update(
            eta_coherence="indeterminate",
            eta_coherence_assumption="none",
            eta_coherence_note=(
                "Cohen's f not checked: cannot determine whether based on eta squared or "
                "partial eta squared."
                if cohens_f_present
                else "Effect size reported but not verifiable from F and degrees of freedom alone."
            ),
        )
        return output

    if all(label == "eta_squared" for label, _, _ in checkable):
        output.update(
            eta_coherence="indeterminate",
            eta_coherence_assumption="eta_squared",
            eta_coherence_note=(
                "Eta-squared reported; cannot be reconstructed from F and dfs in factorial or "
                "repeated-measures designs."
            ),
        )
        return output

    partial_eta = [value for label, value, _ in checkable if label == "partial_eta_squared"]
    if not partial_eta:
        output.update(
            eta_coherence="indeterminate",
            eta_coherence_assumption="none",
            eta_coherence_note="Only eta-squared (not partial) reported; cannot test coherence.",
        )
        return output

    if any(abs(abs(value) - eta_implied) <= _TOLERANCE for value in partial_eta):
        output.update(
            eta_coherence="match_under_assumptions",
            eta_coherence_assumption="partial_eta_squared",
            eta_coherence_note="Match under partial eta-squared formula from F and dfs.",
        )
    else:
        output.update(
            eta_coherence="no_match",
            eta_coherence_assumption="partial_eta_squared",
            eta_coherence_note=(
                "No match under partial eta-squared formula from F and dfs. Tolerance = 0.01. "
                "A no-match can occur when fewer than 2 decimal places are reported."
            ),
        )
    return output


def _build_rows(context: PaperContext) -> list[dict[str, Any]]:
    grouped: dict[tuple[object, object], list[dict[str, Any]]] = {}
    for cached_row in context.equations:
        row = dict(cached_row)
        key = (row.get("paper_id"), row.get("text_id"))
        grouped.setdefault(key, []).append(row)

    table: list[dict[str, Any]] = []
    for equations in grouped.values():
        labeled = [(row, _equation_kind(row)) for row in equations]
        tests = [(row, kind) for row, kind in labeled if kind in {"t-test", "F-test"}]
        effects = [row for row, kind in labeled if kind == "es"]
        if not tests:
            continue

        effect_texts = [_equation_text(row) for row in effects]
        pair_positionally = len(tests) > 1 and len(tests) == len(effect_texts)
        attached_effects: list[str | None] = []
        if pair_positionally:
            attached_effects.extend(effect_texts)
        elif not effect_texts:
            attached_effects.extend([None] * len(tests))
        else:
            attached_effects.extend(["; ".join(effect_texts)] * len(tests))

        for (test_row, raw_test), effect_text in zip(tests, attached_effects, strict=True):
            test = str(raw_test)
            test_text = _equation_text(test_row)
            source_text = test_row.get("expanded")
            row = {
                "paper_id": test_row.get("paper_id", context.paper_id),
                "text_id": test_row.get("text_id"),
                "test": test,
                "test_text": test_text,
                "es": effect_text,
                "section_id": test_row.get("section_id"),
                "paragraph_id": test_row.get("paragraph_id"),
                "text": source_text if isinstance(source_text, str) else test_row.get("text"),
            }
            row.update(_classify_d(test, test_text, effect_text))
            row.update(_classify_f(test, test_text, effect_text))
            table.append(row)
    return table


def _coherence_count(rows: Iterable[dict[str, Any]], field: str, value: str) -> int:
    return sum(row.get(field) == value for row in rows)


def _coherence_text(
    matches: int,
    no_matches: int,
    indeterminate: int,
    test_label: str,
    effect_label: str,
) -> str | None:
    if matches + no_matches + indeterminate == 0:
        return None
    parts: list[str] = []
    if matches:
        parts.append(f"{matches} match{'es' if matches != 1 else ''} under assumptions")
    if no_matches:
        parts.append(f"{no_matches} no match{'es' if no_matches != 1 else ''}")
    if indeterminate:
        parts.append(f"{indeterminate} indeterminate case{'s' if indeterminate != 1 else ''}")
    return (
        f"For {test_label} with a reported {effect_label}, coherence checks yielded "
        f"{', '.join(parts)}."
    )


def _escape_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", r"\|").replace("\n", "<br>")


def _detail_table(rows: list[dict[str, Any]]) -> str:
    body = [
        "| Sentence | Effect Size | Reported Test | Test Type | d Coherence | d Assumption | "
        "d Coherence Note | eta Coherence | eta Assumption | eta Coherence Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    body.extend(
        "| "
        + " | ".join(
            _escape_cell(row.get(field))
            for field in (
                "text",
                "es",
                "test_text",
                "test",
                "d_coherence",
                "d_coherence_assumption",
                "d_coherence_note",
                "eta_coherence",
                "eta_coherence_assumption",
                "eta_coherence_note",
            )
        )
        + " |"
        for row in rows
    )
    return "\n".join(body)


def _report(
    table: list[dict[str, Any]],
    summary_text: str,
    *,
    has_missing: bool,
    has_no_match: bool,
) -> str:
    t_rows = [row for row in table if row["test"] == "t-test" and row["es"] is not None]
    f_rows = [row for row in table if row["test"] == "F-test" and row["es"] is not None]
    d_text = _coherence_text(
        _coherence_count(t_rows, "d_coherence", "match_under_assumptions"),
        _coherence_count(t_rows, "d_coherence", "no_match"),
        _coherence_count(t_rows, "d_coherence", "indeterminate"),
        "t-tests",
        "d",
    )
    eta_text = _coherence_text(
        _coherence_count(f_rows, "eta_coherence", "match_under_assumptions"),
        _coherence_count(f_rows, "eta_coherence", "no_match"),
        _coherence_count(f_rows, "eta_coherence", "indeterminate"),
        "F-tests",
        "eta-squared effect size",
    )
    details = _detail_table(table)

    if not has_missing and not has_no_match:
        parts = [summary_text, d_text, eta_text, details]
    else:
        report_text = (
            "We recommend checking the sentences below, and add any missing effect sizes."
            if has_missing
            else "All tests had effect sizes, but some effect sizes do not match the reported "
            "test statistic."
        )
        parts = [report_text, d_text, eta_text, _GUIDANCE, details]
    return "\n\n".join(part for part in parts if part)


@register_module(
    ModuleMetadata(
        name="stat_effect_size",
        title="Effect Sizes in t-tests and F-tests",
        description=(
            "The Effect Size module checks if effect sizes are correctly reported in "
            "t-tests and F-tests."
        ),
        section="results",
        validated=True,
    )
)
def stat_effect_size(context: PaperContext) -> ModuleResult:
    table = _build_rows(context)
    if not table:
        return ModuleResult(
            module="stat_effect_size",
            title="Effect Sizes in t-tests and F-tests",
            table=[],
            summary_table=[{"paper_id": context.paper_id}],
            summary_text=_NO_TESTS_MESSAGE,
            report=_NO_TESTS_MESSAGE,
            traffic_light="na",
        )

    summary = {
        "paper_id": context.paper_id,
        "ttests_with_es": sum(row["test"] == "t-test" and row["es"] is not None for row in table),
        "ttests_without_es": sum(row["test"] == "t-test" and row["es"] is None for row in table),
        "Ftests_with_es": sum(row["test"] == "F-test" and row["es"] is not None for row in table),
        "Ftests_without_es": sum(row["test"] == "F-test" and row["es"] is None for row in table),
    }
    missing_count = sum(row["es"] is None for row in table)
    has_no_match = any(
        row["d_coherence"] == "no_match" or row["eta_coherence"] == "no_match" for row in table
    )
    if missing_count == len(table):
        traffic_light: TrafficLight = "red"
    elif has_no_match:
        traffic_light = "red"
    elif missing_count:
        traffic_light = "yellow"
    else:
        traffic_light = "green"

    if missing_count:
        suffix = "" if missing_count == 1 else "s"
        summary_text = (
            f"We found {missing_count} t-test{suffix} and/or F-test{suffix} where effect sizes "
            "are not reported. Check these tests in the table below, and consider adding effect "
            "sizes"
        )
    elif has_no_match:
        summary_text = _NO_MATCH_MESSAGE
    else:
        summary_text = _GREEN_MESSAGE

    return ModuleResult(
        module="stat_effect_size",
        title="Effect Sizes in t-tests and F-tests",
        table=table,
        summary_table=[summary],
        summary_text=summary_text,
        report=_report(
            table,
            summary_text,
            has_missing=missing_count > 0,
            has_no_match=has_no_match,
        ),
        traffic_light=traffic_light,
    )


__all__ = ["stat_effect_size"]
