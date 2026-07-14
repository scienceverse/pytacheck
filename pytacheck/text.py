from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

MAX_SEARCH_RESULTS = 5_000
MAX_EXTRACTED_ROWS = 5_000
MAX_MATCHES_PER_SOURCE = 256
MAX_REPEATED_SOURCE_CHARS = 1_000_000
MAX_ASSEMBLED_PARAGRAPH_CHARS = 1_000_000
MAX_P_EXPONENT_DIGITS = 4
MAX_P_MANTISSA_CHARS = 64


class ExtractionLimitError(ValueError):
    """Raised when text extraction would amplify a paper beyond safe limits."""


P_VALUE_PATTERN = re.compile(
    r"\bp-?(?:value)?\s*(?P<p_comp>[=<>~≈≠≤≥≪≫]{1,2})\s*"
    r"(?P<value>n\.?s\.?|(?:\d+\.\d+|\.\d+))(?:\s*e\s*-\d+)?"
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


def _check_result_budget(
    *,
    result_count: int,
    source_match_count: int,
    repeated_source_chars: int,
    row_limit: int,
) -> None:
    if source_match_count > MAX_MATCHES_PER_SOURCE:
        raise ExtractionLimitError(
            f"Extraction per-source match limit exceeded ({MAX_MATCHES_PER_SOURCE})"
        )
    if result_count > row_limit:
        raise ExtractionLimitError(f"Extraction row limit exceeded ({row_limit})")
    if repeated_source_chars > MAX_REPEATED_SOURCE_CHARS:
        raise ExtractionLimitError(
            "Extraction repeated source text budget exceeded "
            f"({MAX_REPEATED_SOURCE_CHARS} characters)"
        )


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
    repeated_source_chars = 0

    for row in rows:
        if not _is_searchable(row):
            continue
        text = _row_text(row)
        if text is None:
            continue

        if return_matches:
            for source_match_count, match in enumerate(compiled.finditer(text), start=1):
                repeated_source_chars += len(text)
                _check_result_budget(
                    result_count=len(results) + 1,
                    source_match_count=source_match_count,
                    repeated_source_chars=repeated_source_chars,
                    row_limit=MAX_SEARCH_RESULTS,
                )
                result = _copy_row(row)
                result["text"] = match.group(0)
                results.append(result)
        elif compiled.search(text) is not None:
            repeated_source_chars += len(text)
            _check_result_budget(
                result_count=len(results) + 1,
                source_match_count=1,
                repeated_source_chars=repeated_source_chars,
                row_limit=MAX_SEARCH_RESULTS,
            )
            results.append(_copy_row(row))

    return tuple(results)


def assemble_paragraphs(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    parts: dict[tuple[Any, Any, Any], list[str]] = {}
    assembled_chars: dict[tuple[Any, Any, Any], int] = {}

    for row in rows:
        key = (row.get("paper_id"), row.get("section_id"), row.get("paragraph_id"))
        if key not in grouped:
            if len(grouped) >= MAX_SEARCH_RESULTS:
                raise ExtractionLimitError(
                    f"Paragraph assembly row limit exceeded ({MAX_SEARCH_RESULTS})"
                )
            grouped[key] = _copy_row(row)
            parts[key] = []
            assembled_chars[key] = 0

        text = _row_text(row)
        if text:
            separator_chars = 1 if parts[key] else 0
            next_size = assembled_chars[key] + separator_chars + len(text)
            if next_size > MAX_ASSEMBLED_PARAGRAPH_CHARS:
                raise ExtractionLimitError(
                    "Assembled paragraph text budget exceeded "
                    f"({MAX_ASSEMBLED_PARAGRAPH_CHARS} characters)"
                )
            parts[key].append(text)
            assembled_chars[key] = next_size

    for key, paragraph in grouped.items():
        paragraph["text"] = " ".join(parts[key])

    return tuple(grouped.values())


def _numeric_p_value(match: re.Match[str]) -> tuple[float | None, bool]:
    value = match.group("value")
    if value.replace(".", "").casefold() == "ns":
        return None, True

    if len(value) > MAX_P_MANTISSA_CHARS:
        return None, False
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None, False
    if not math.isfinite(numeric):
        return None, False
    exponent_match = _EXPONENT_PATTERN.search(match.group(0))
    if exponent_match is not None:
        exponent = exponent_match.group("exponent")
        if len(exponent) > MAX_P_EXPONENT_DIGITS:
            return None, False
        numeric *= 10 ** -int(exponent)
    return numeric, 0 <= numeric <= 1


def _lexical_zero(match: re.Match[str]) -> bool:
    value = match.group("value")
    digits = value.replace(".", "")
    return bool(digits) and digits.isdigit() and set(digits) == {"0"}


def _is_star_note(expanded: str, match: re.Match[str]) -> bool:
    if match.group("p_comp") != "<":
        return False
    if re.fullmatch(r"0?\.0+[15]", match.group("value")) is None:
        return False
    prefix = expanded[max(0, match.start() - 16) : match.start()]
    return re.search(r"\*\s*$", prefix) is not None


def extract_p_values(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    repeated_source_chars = 0

    for row in rows:
        if not _is_searchable(row):
            continue
        expanded = _row_text(row)
        if expanded is None:
            continue

        for source_match_count, match in enumerate(P_VALUE_PATTERN.finditer(expanded), start=1):
            repeated_source_chars += len(expanded)
            _check_result_budget(
                result_count=len(results) + 1,
                source_match_count=source_match_count,
                repeated_source_chars=repeated_source_chars,
                row_limit=MAX_EXTRACTED_ROWS,
            )
            p_value, p_valid = _numeric_p_value(match)
            result = _copy_row(row)
            result.update(
                text=match.group(0),
                expanded=expanded,
                p_comp=match.group("p_comp"),
                p_value=p_value,
                _p_valid=p_valid,
                _p_lexical_zero=_lexical_zero(match),
                _star_note=_is_star_note(expanded, match),
                _match_start=match.start(),
                _match_end=match.end(),
            )
            results.append(result)

    return tuple(results)


def extract_equations(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    group_id = 0
    repeated_source_chars = 0

    for row in rows:
        if not _is_searchable(row):
            continue
        expanded = _row_text(row)
        if expanded is None:
            continue

        source_match_count = 0
        source_group_id: int | None = None
        for match in _EQUATION_PATTERN.finditer(expanded):
            if re.fullmatch(r"[0-9]", match.group("lhs")) is not None:
                continue
            source_match_count += 1
            repeated_source_chars += len(expanded)
            _check_result_budget(
                result_count=len(results) + 1,
                source_match_count=source_match_count,
                repeated_source_chars=repeated_source_chars,
                row_limit=MAX_EXTRACTED_ROWS,
            )
            if source_group_id is None:
                group_id += 1
                source_group_id = group_id
            result = _copy_row(row)
            result.update(
                text=match.group(0),
                expanded=expanded,
                lhs=match.group("lhs"),
                df=match.group("df"),
                comp=match.group("comp"),
                rhs=match.group("rhs"),
                grp_id=source_group_id,
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
    repeated_source_chars = 0

    for row in rows:
        if not _is_searchable(row):
            continue
        expanded = _row_text(row)
        if expanded is None:
            continue

        source_match_count = 0
        for match in _APA_TEST_PATTERN.finditer(expanded):
            if re.search(r"[<>=]", match.group("stat_prefix")) is not None:
                continue

            source_match_count += 1
            repeated_source_chars += len(expanded)
            _check_result_budget(
                result_count=len(results) + 1,
                source_match_count=source_match_count,
                repeated_source_chars=repeated_source_chars,
                row_limit=MAX_EXTRACTED_ROWS,
            )

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


__all__ = [
    "MAX_ASSEMBLED_PARAGRAPH_CHARS",
    "MAX_EXTRACTED_ROWS",
    "MAX_MATCHES_PER_SOURCE",
    "MAX_P_EXPONENT_DIGITS",
    "MAX_P_MANTISSA_CHARS",
    "MAX_REPEATED_SOURCE_CHARS",
    "MAX_SEARCH_RESULTS",
    "ExtractionLimitError",
    "P_VALUE_PATTERN",
    "assemble_paragraphs",
    "extract_apa_tests",
    "extract_equations",
    "extract_p_values",
    "search_rows",
]
