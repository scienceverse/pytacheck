from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

P_VALUE_PATTERN = re.compile(
    r"\bp-?(?:value)?\s*(?P<p_comp>[=<>~≈≠≤≥≪≫]{1,2})\s*"
    r"(?P<value>n\.?s\.?|\d?\.\d+)(?:\s*e\s*-\d+)?"
    r"(?:\s*[x*]\s*10\s*\^\s*-\d+)?"
)

_EXPONENT_PATTERN = re.compile(r"(?:e\s*-\s*|[x*]\s*10\s*\^\s*-\s*)(?P<exponent>\d+)\s*$")
_EQUATION_PATTERN = re.compile(
    r"(?<!\w)"
    r"(?P<lhs>"
    r"partial\s+eta\s+squared|"
    r"(?:(?:Hedge.{0,3}|Cronbach.{0,2}|Cohen.{0,2}|\d{1,2}%)\s+)?"
    r"[\u0370-\u03FF²a-zA-Z0-9_.{}^\\-]+"
    r")"
    r"\s*(?P<df>\([^()]*\))?\s*"
    r"(?P<comp>[=<>~≈≠≤≥≪≫]{1,3})\s*"
    r"(?P<rhs>"
    r"\[[^\]\r\n]+\]|"
    r"n\.?\s*s\.?|"
    r"[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)"
    r"(?:[eE]\s*[+-]?\s*\d+)?"
    r"(?:\s*[x*]\s*10\s*\^\s*-\s*\d+)?"
    r")",
    re.IGNORECASE,
)
_APA_WS = r"[ \t\r\n\f\v]?"
_APA_NUMBER = r"\d*,?\d*\.?\d+"
_APA_TEST_PATTERN = re.compile(
    rf"(?:"
    rf"(?P<t_type>t){_APA_WS}\({_APA_WS}(?P<t_df>\d*\.?\d+){_APA_WS}\)"
    rf"|"
    rf"(?P<f_type>F){_APA_WS}\({_APA_WS}(?P<f_df1>I|l|\d*\.?\d+)"
    rf"{_APA_WS},{_APA_WS}(?P<f_df2>\d*\.?\d+){_APA_WS}\)"
    rf")"
    rf"{_APA_WS}(?P<comp>[<>=]){_APA_WS}"
    rf"(?P<stat_prefix>[^a-zA-Z\d.]{{0,3}}){_APA_WS}"
    rf"(?P<statistic>{_APA_NUMBER}){_APA_WS},{_APA_WS}"
    rf"(?:"
    rf"p{_APA_WS}(?P<p_comp>[<>=]){_APA_WS}"
    rf"(?P<p_value>\d?\.\d+e?-?\d*)"
    rf"|(?P<ns>n\.?s\.?)"
    rf")"
)


def _is_searchable(row: Mapping[str, Any]) -> bool:
    section_type = row.get("section_type")
    return not isinstance(section_type, str) or section_type.casefold() != "references"


def _row_text(row: Mapping[str, Any]) -> str | None:
    text = row.get("text")
    return text if isinstance(text, str) else None


def _copy_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(row))


def _compile_pattern(pattern: str | re.Pattern[str], *, ignore_case: bool) -> re.Pattern[str]:
    if isinstance(pattern, re.Pattern):
        flags = pattern.flags
        if ignore_case:
            flags |= re.IGNORECASE
        else:
            flags &= ~re.IGNORECASE
        return re.compile(pattern.pattern, flags)

    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(pattern, flags)


def search_rows(
    rows: Iterable[Mapping[str, Any]],
    pattern: str | re.Pattern[str],
    *,
    return_matches: bool = False,
    ignore_case: bool = True,
) -> tuple[dict[str, Any], ...]:
    compiled = _compile_pattern(pattern, ignore_case=ignore_case)
    results: list[dict[str, Any]] = []

    for row in rows:
        if not _is_searchable(row):
            continue
        text = _row_text(row)
        if text is None:
            continue

        matches = compiled.finditer(text)
        if return_matches:
            for match in matches:
                result = _copy_row(row)
                result["text"] = match.group(0)
                results.append(result)
        elif compiled.search(text) is not None:
            results.append(_copy_row(row))

    return tuple(results)


def assemble_paragraphs(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    parts: dict[tuple[Any, Any, Any], list[str]] = {}

    for row in rows:
        key = (row.get("paper_id"), row.get("section_id"), row.get("paragraph_id"))
        if key not in grouped:
            grouped[key] = _copy_row(row)
            parts[key] = []

        text = _row_text(row)
        if text:
            parts[key].append(text)

    for key, paragraph in grouped.items():
        paragraph["text"] = " ".join(parts[key])

    return tuple(grouped.values())


def _numeric_p_value(match: re.Match[str]) -> float | None:
    value = match.group("value")
    if value.replace(".", "").casefold() == "ns":
        return None

    numeric = float(value)
    exponent_match = _EXPONENT_PATTERN.search(match.group(0))
    if exponent_match is not None:
        numeric *= 10 ** -int(exponent_match.group("exponent"))
    return numeric


def extract_p_values(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []

    for row in rows:
        if not _is_searchable(row):
            continue
        expanded = _row_text(row)
        if expanded is None:
            continue

        for match in P_VALUE_PATTERN.finditer(expanded):
            result = _copy_row(row)
            result.update(
                text=match.group(0),
                expanded=expanded,
                p_comp=match.group("p_comp"),
                p_value=_numeric_p_value(match),
            )
            results.append(result)

    return tuple(results)


def extract_equations(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    group_id = 0

    for row in rows:
        if not _is_searchable(row):
            continue
        expanded = _row_text(row)
        if expanded is None:
            continue

        matches = tuple(_EQUATION_PATTERN.finditer(expanded))
        if not matches:
            continue
        group_id += 1

        for match in matches:
            if re.fullmatch(r"[0-9]", match.group("lhs")) is not None:
                continue
            result = _copy_row(row)
            result.update(
                text=match.group(0),
                expanded=expanded,
                lhs=match.group("lhs"),
                df=match.group("df"),
                comp=match.group("comp"),
                rhs=match.group("rhs"),
                grp_id=group_id,
            )
            results.append(result)

    return tuple(results)


def _number(value: str) -> int | float:
    return float(value) if "." in value else int(value)


def _decimal_places(value: str) -> int:
    match = re.search(r"\.(\d+)", value)
    return len(match.group(1)) if match is not None else 0


def _apa_statistic(match: re.Match[str]) -> float:
    value = float(match.group("statistic").replace(",", ""))
    prefix = match.group("stat_prefix")
    if re.search(r"[^\d.\s]", prefix) is not None:
        return -abs(value)
    return value


def extract_apa_tests(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []

    for row in rows:
        if not _is_searchable(row):
            continue
        expanded = _row_text(row)
        if expanded is None:
            continue

        for match in _APA_TEST_PATTERN.finditer(expanded):
            if re.search(r"[<>=]", match.group("stat_prefix")) is not None:
                continue

            test_type = "t" if match.group("t_type") is not None else "F"
            if test_type == "t":
                degrees_of_freedom = [_number(match.group("t_df"))]
            else:
                raw_df1 = match.group("f_df1")
                df1 = 1 if raw_df1 in {"I", "l"} else _number(raw_df1)
                degrees_of_freedom = [df1, _number(match.group("f_df2"))]

            p_literal = match.group("p_value")
            if p_literal is None:
                p_comp = "ns"
                p_value = None
                p_decimals = None
            else:
                p_comp = match.group("p_comp")
                try:
                    p_value = float(p_literal)
                except ValueError:
                    continue
                p_decimals = _decimal_places(p_literal)
            if p_value is not None and p_value > 1:
                continue

            raw = match.group(0)
            result = _copy_row(row)
            result.update(
                text=raw,
                expanded=expanded,
                raw=raw,
                test_type=test_type,
                statistic=_apa_statistic(match),
                df=degrees_of_freedom,
                df1=degrees_of_freedom[0],
                df2=degrees_of_freedom[1] if len(degrees_of_freedom) == 2 else None,
                comp=match.group("comp"),
                p_comp=p_comp,
                p_value=p_value,
                _statistic_decimals=_decimal_places(match.group("statistic")),
                _p_decimals=p_decimals,
            )
            results.append(result)

    return tuple(results)
