"""Shared statistics helpers (port of ``R/stat_helpers.R``).

``R/stat_helpers.R`` also (re)defines ``.stat_num_to_chr()``,
``.stat_sanitize_id()``, ``.norm_stat_name()``, ``.norm_value()``,
``.norm_interval()``, ``.stat_is_placeholder()``, ``.stat_is_label_col()`` and
``.table_header_ambiguous()``. Their bodies are identical to the definitions
in ``R/stat-output.R``, ``R/stat-tables.R``, ``R/match-reported.R``,
``R/match-table.R`` and ``R/extract-tests.R``, and they are ported there
(``metacheck.statout.*``, ``metacheck.text.extract_tests``; see
``porting/symbols.json``).
"""

from __future__ import annotations

import math
from typing import Any

from metacheck._r.base import as_character, r_round
from metacheck._r.regex import grepl, gsub, sub
from metacheck._values import as_float, is_missing

__all__ = [
    "_r_stat_pattern",
    "_stat_display_value",
    "_stat_html_escape",
    "_stato_strip_variant",
]

_NUMBER_TEXT = r"^[-+]?[0-9.]+([eE][-+]?[0-9]+)?$"


def _format_whole(x: float) -> str:
    """``format(x, scientific = FALSE, trim = TRUE)`` for a whole number."""
    s = f"{x:.0f}"
    return "0" if s == "-0" else s


def _stat_display_value(x: Any) -> str:
    """Port of ``.stat_display_value()``: a value cell as shown in HTML tables.

    Numbers are rounded to 3 decimals for display, except whole numbers (shown
    without decimals) and values that would round to zero (kept as written).
    ``NA``/``None`` gives ``""``; anything that is not plainly a number is
    returned unchanged.
    """
    if is_missing(x):
        return ""
    if isinstance(x, list | tuple):
        if len(x) != 1:
            raise ValueError("the condition has length > 1")
        x = x[0]
        if is_missing(x):
            return ""
    text = x if isinstance(x, str) else as_character(x)
    assert text is not None
    num = as_float(text)
    if num is None or not math.isfinite(num) or not grepl(_NUMBER_TEXT, text):
        return text
    if num == r_round(num):
        return _format_whole(r_round(num))
    rounded = f"{num:.3f}"
    if num != 0 and float(rounded) == 0:
        return text
    return rounded


def _stat_html_escape(x: Any) -> Any:
    """Port of ``.stat_html_escape()``: escape ``&``, ``<`` and ``>`` (vectorised).

    ``None`` (R ``NULL``) gives ``""``; ``NA`` elements stay ``NA``.
    """
    if x is None:
        x = ""
    scalar = not isinstance(x, list | tuple)
    values = [x] if scalar else list(x)
    chars = [
        None if is_missing(v) else (v if isinstance(v, str) else as_character(v)) for v in values
    ]
    out = gsub("&", "&amp;", chars, fixed=True)
    out = gsub("<", "&lt;", out, fixed=True)
    out = gsub(">", "&gt;", out, fixed=True)
    return out[0] if scalar else out


def _stato_strip_variant(key: Any) -> Any:
    """Port of ``.stato_strip_variant()``: drop a trailing ``[...]`` variant suffix.

    jamovi appends sphericity corrections and test variants in brackets
    (``f[gg]``, ``p[hf]``, ``stat[stud]``); the suffix is stripped before
    STATO lookup. Vectorised like R's ``sub()``.
    """
    return sub(r"\[[^]]*\]$", "", key)


_OPERATORS = ("=", "<", ">", "~", "≈", "≠", "≤", "≥", "≪", "≫")


def _r_stat_pattern() -> dict[str, str]:
    """Port of ``.r_stat_pattern()``: the ``<name> [(df)] <op> <value>`` pattern.

    Returns ``{"op": <comparison characters>, "pattern": <TRE regex>}``;
    shared by ``extract_eq()`` and ``read_r_output()``.
    """
    op = "".join(_OPERATORS)
    gr = "Ͱ-Ͽ"
    pattern = (
        f"([{gr}²a-zA-Z][{gr}²a-zA-Z0-9._-]*)\\s*"  # statistic name
        "(\\([^)]*\\))?\\s*"  # optional (df)
        f"([{op}]{{1,3}})\\s*"  # comparator
        # value: a number (no trailing comma), scientific notation, or "< .001".
        "(<\\s*[.0-9]+|-?[0-9]+(?:\\.[0-9]+)?(?:e[-+]?[0-9]+)?)"
    )
    return {"op": op, "pattern": pattern}
