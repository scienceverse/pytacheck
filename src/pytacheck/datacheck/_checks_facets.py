"""Column concepts and facets (private; see :mod:`pytacheck.datacheck.checks`).

Port of the "Column facets" section of ``R/data_check_helpers.R``:
``.concept_is_*()``, ``.parse_frac()``, ``data_col_concept()``,
``.coltype_to_facets()``, ``data_col_facets()`` and ``.tabular_usable()``.

``.parse_frac()`` asks how many values ``as.POSIXct(v, format = f)`` parses,
so this module carries a small emulation of R's ``strptime()`` for the
directives the date formats use (``%Y %y %m %d %H %M %S %b %B``): greedy
digit fields that stop early when another digit would overflow the field,
leading blanks skipped inside numbers, a blank in the format matching any run
of blanks, trailing input ignored, and the calendar validation R applies
afterwards (day within the month, ``24:00:00`` only as midnight, seconds up
to 60).
"""

from __future__ import annotations

import functools
from collections import Counter
from collections.abc import Mapping
from typing import Any

from pytacheck._r.regex import compile_r, grepl
from pytacheck.datacheck._checks_rvec import (
    as_numeric_str,
    chr,
    fmt_pct0,
    median,
    rvec,
    tolower,
    trim,
    unique,
)

__all__ = [
    "_ACC_NAME_RE",
    "_DATETIME_FMTS",
    "_DATE_FMTS",
    "_RT_NAME_RE",
    "_TABULAR_MISS_MID",
    "_TABULAR_PROSE_HIGH",
    "_TABULAR_PROSE_MID",
    "_coltype_to_facets",
    "_concept_is_accuracy",
    "_concept_is_condition",
    "_concept_is_date",
    "_concept_is_rt",
    "_concept_is_timestamp",
    "_parse_frac",
    "_strptime_ok",
    "_tabular_usable",
    "data_col_concept",
    "data_col_facets",
]

#: R: .RT_NAME_RE / .ACC_NAME_RE (matched against the lower-cased name, perl = TRUE)
_RT_NAME_RE = (
    r"(^|[^a-z])(rt|reaction[._ -]?time|response[._ -]?time|latency|resp[._ -]?time)([^a-z]|$)"
)
_ACC_NAME_RE = (
    r"(^|[^a-z])(acc|accuracy|correct|iscorrect|is[._ -]?correct|error|errors|hit|hits|miss"
    r"|misses)([^a-z]|$)"
)
_COND_NAME_RE = (
    r"(^|[^a-z])(cond|condition|group|treatment|arm|manipulation|between|within)([^a-z]|$)"
)
_TIMESTAMP_NAME_RE = (
    r"(^|[^a-z])(time|timestamp|datetime|onset|startdate|enddate|recordeddate)([^a-z]|$)"
)
_DATE_NAME_RE = (
    r"(^|[^a-z])(date|dob|birth|birthday|geboorte|dated)([^a-z]|$)|(^|[^a-z])(day|dag)([^a-z]|$)"
)

#: R: .DATETIME_FMTS / .DATE_FMTS
_DATETIME_FMTS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S", "%Y-%m-%d_%Hh%M", "%Y_%b_%d_%H%M",
)  # fmt: skip
_DATE_FMTS: tuple[str, ...] = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y",
    "%m/%d/%y", "%d/%m/%y", "%d %b %Y", "%B %d, %Y",
)  # fmt: skip

#: R: .tabular_prose_high / .tabular_prose_mid / .tabular_miss_mid
_TABULAR_PROSE_HIGH = 0.70
_TABULAR_PROSE_MID = 0.40
_TABULAR_MISS_MID = 0.40

# -- strptime emulation ----------------------------------------------------------

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip
_MONTH_NUM = {
    **{m: i + 1 for i, m in enumerate(_MONTHS)},
    **{m[:3]: i + 1 for i, m in enumerate(_MONTHS)},
}
# (directive -> (from, to, width)) as in R's Rstrptime.h get_number()
_NUMBER_FIELDS = {
    "Y": (0, 9999, 4),
    "y": (0, 99, 2),
    "m": (1, 12, 2),
    "d": (1, 31, 2),
    "H": (0, 24, 2),
    "M": (0, 59, 2),
    "S": (0, 61, 2),
}
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _number_regex(_lo: int, hi: int, width: int) -> str:
    """The digits ``get_number(lo, hi, width)`` consumes (value checked later).

    Digits are read greedily while fewer than *width* are taken and the value
    times ten does not exceed *hi*; there is no backtracking.
    """
    if width == 4:
        return "[0-9]{1,4}+"
    d1 = "".join(str(d) for d in range(10) if d * 10 <= hi)
    d2 = "".join(str(d) for d in range(10) if d * 10 > hi)
    alt = f"[{d1}][0-9]?+"
    return f"(?:{alt}|[{d2}])" if d2 else alt


@functools.cache
def _format_regex(fmt: str) -> tuple[Any, tuple[str, ...]]:
    """Compile a strptime *fmt* to an anchored regex and its field order."""
    parts: list[str] = []
    fields: list[str] = []
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c == "%" and i + 1 < len(fmt):
            d = fmt[i + 1]
            i += 2
            if d in _NUMBER_FIELDS:
                parts.append(" *(" + _number_regex(*_NUMBER_FIELDS[d]) + ")")
                fields.append(d)
            elif d in ("b", "B", "h"):
                names = "|".join(f"{m}|{m[:3]}" for m in _MONTHS)
                parts.append(f"((?>(?i:{names})))")
                fields.append("b")
            elif d == "%":
                parts.append("%")
            else:  # pragma: no cover - not used by the date formats
                raise ValueError(f"unsupported strptime directive %{d}")
            continue
        if c in " \t\n\v\f\r":
            parts.append("[ \t\n\v\f\r]*")
        else:
            parts.append("\\" + c if not c.isalnum() else c)
        i += 1
    return compile_r("^" + "".join(parts), perl=True), tuple(fields)


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def _strptime_ok(s: str, fmt: str) -> bool:
    """Does ``as.POSIXct(s, format = fmt, tz = "UTC")`` give a non-NA time?"""
    rx, fields = _format_regex(fmt)
    m = rx.match(s)
    if m is None:
        return False
    year = 1900
    mon = day = None
    hour = minute = sec = 0
    for f, txt in zip(fields, m.groups(), strict=True):
        if f == "b":
            mon = _MONTH_NUM[txt.lower()]
            continue
        v = int(txt)
        lo, hi, _ = _NUMBER_FIELDS[f]
        if v < lo or v > hi:
            return False
        if f == "Y":
            year = v
        elif f == "y":
            year = 1900 + (v if v >= 69 else v + 100)
        elif f == "m":
            mon = v
        elif f == "d":
            day = v
        elif f == "H":
            hour = v
        elif f == "M":
            minute = v
        else:
            sec = v
    if sec > 60 or minute > 59 or hour > 24 or (hour == 24 and (minute > 0 or sec > 0)):
        return False
    if mon is None or day is None:  # pragma: no cover - every format has both
        return False
    dim = _DAYS_IN_MONTH[mon - 1] + (1 if mon == 2 and _is_leap(year) else 0)
    return day <= dim


def _parse_frac(v: Any, fmts: tuple[str, ...] | list[str]) -> float:
    """Fraction of *v* that parses under the best single format of *fmts*.

    Port of ``R/data_check_helpers.R::.parse_frac()``: numeric vectors give
    0; values over 200 characters count as unparseable.
    """
    rv = rvec(v)
    if rv.is_numeric:
        return 0
    vals = [s for s in chr(rv) if s is not None and s != ""]
    if not vals:
        return 0
    counts = Counter(s for s in vals if len(s) <= 200)
    n = len(vals)
    best = 0.0
    for f in fmts:
        ok = sum(k for s, k in counts.items() if _strptime_ok(s, f))
        best = max(best, ok / n)
        if best >= 1:
            break
    return best


# -- concept detectors ------------------------------------------------------------


def _numbers(x: Any) -> list[float]:
    """``as.numeric(gsub(",", ".", as.character(x), fixed = TRUE))`` without NA."""
    out = []
    for s in chr(x):
        if s is None:
            continue
        f = as_numeric_str(s.replace(",", "."))
        if f is not None and f == f:
            out.append(f)
    return out


def _lower_name(col_name: Any) -> str | None:
    names = chr(col_name)
    return tolower(names[0]) if names else None


def _concept_is_rt(col_name: Any, x: Any) -> bool:
    """A reaction-time column: RT-like name and non-negative durations.

    Port of ``R/data_check_helpers.R::.concept_is_rt()``.
    """
    if not grepl(_RT_NAME_RE, _lower_name(col_name), perl=True):
        return False
    vals = _numbers(x)
    if len(vals) < 3:
        return True
    return sum(1 for f in vals if f >= 0) / len(vals) >= 0.95


_BOOL_WORDS = frozenset(
    ("true", "false", "correct", "incorrect", "hit", "miss", "yes", "no", "right", "wrong")
)


def _concept_is_accuracy(col_name: Any, x: Any) -> bool:
    """An accuracy column: accuracy-like name and 0/1 or correct/incorrect values.

    Port of ``R/data_check_helpers.R::.concept_is_accuracy()``.
    """
    if not grepl(_ACC_NAME_RE, _lower_name(col_name), perl=True):
        return False
    v = [t for s in chr(x) if s is not None and (t := tolower(trim(s))) != ""]
    if len(v) < 3:
        return True
    u = unique(v)
    nums = [as_numeric_str(s) for s in u]
    is01 = all(f is not None and f == f for f in nums) and all(f in (0, 1) for f in nums)
    is_bool = all(s in _BOOL_WORDS for s in u)
    return is01 or is_bool


def _concept_is_condition(col_name: Any, x: Any) -> bool:  # noqa: ARG001 - R's signature
    """A condition/group assignment column, by name only.

    Port of ``R/data_check_helpers.R::.concept_is_condition()``.
    """
    return bool(grepl(_COND_NAME_RE, _lower_name(col_name), perl=True))


def _concept_is_timestamp(col_name: Any, x: Any) -> bool:
    """A timestamp column: time-like name and values that parse as date-times.

    Port of ``R/data_check_helpers.R::.concept_is_timestamp()``.
    """
    if not grepl(_TIMESTAMP_NAME_RE, _lower_name(col_name), perl=True):
        return False
    return _parse_frac(x, _DATETIME_FMTS) >= 0.5


def _concept_is_date(col_name: Any, x: Any) -> bool:
    """A calendar-date column: date-like name, values parse as dates, not date-times.

    Port of ``R/data_check_helpers.R::.concept_is_date()``.
    """
    if not grepl(_DATE_NAME_RE, _lower_name(col_name), perl=True):
        return False
    if _parse_frac(x, _DATETIME_FMTS) >= 0.5:
        return False
    return _parse_frac(x, _DATE_FMTS) >= 0.5


def data_col_concept(col_name: Any, x: Any) -> str | None:
    """Detect the substantive concept a column measures (name + value agreement).

    Port of ``R/data_check_helpers.R::data_col_concept()``: one of
    ``"reaction_time"``, ``"accuracy"``, ``"age"``, ``"gender"``, ``"race"``,
    ``"timestamp"``, ``"date"``, ``"condition"`` or ``None`` (``NA``).
    """
    from pytacheck.datacheck._checks_pii import _utf8_name, data_check_demographic

    if col_name is None:
        return None
    names = chr(col_name)
    if len(names) != 1 or names[0] is None or names[0] == "":
        return None
    nm = _utf8_name(names[0])
    if _concept_is_rt(nm, x):
        return "reaction_time"
    if _concept_is_accuracy(nm, x):
        return "accuracy"
    demo = data_check_demographic(nm, x)
    if demo is not None:
        return demo
    if _concept_is_timestamp(nm, x):
        return "timestamp"
    if _concept_is_date(nm, x):
        return "date"
    if _concept_is_condition(nm, x):
        return "condition"
    return None


_COLTYPE_FACETS: dict[str, tuple[str | None, str | None]] = {
    "empty": ("empty", None),
    "constant": (None, None),
    "binary": (None, "nominal"),
    "date": ("datetime", None),
    "text": ("text", None),
    "id": ("text", "nominal"),
    "continuous": ("numeric", "ratio"),
    "continuous_comma_decimal": ("numeric", "ratio"),
    "continuous_outliers_excluded": ("numeric", "ratio"),
}


def _coltype_to_facets(
    ct: Any,
    is_numeric_hint: Any = None,  # noqa: ARG001 - R's signature (unused there too)
) -> dict[str, str | None]:
    """Map a ``data_col_type()`` type to ``{"rep": representation, "lvl": level}``.

    Port of ``R/data_check_helpers.R::.coltype_to_facets()``.
    """
    if ct is not None and not isinstance(ct, str):
        v = chr(ct)
        if len(v) != 1:
            raise ValueError("EXPR must be a length 1 vector")
        ct = v[0]
        if ct is None:  # switch(NA_character_, ...) takes the default
            return {"rep": None, "lvl": None}
    rep, lvl = _COLTYPE_FACETS.get(ct if ct is not None else "unknown", (None, None))
    return {"rep": rep, "lvl": lvl}


def _is_true(x: Any) -> bool:
    """R ``isTRUE(x)``: a single, non-missing ``TRUE``."""
    if x is None or isinstance(x, str):
        return False
    v = rvec(x)
    return v.kind == "logical" and len(v) == 1 and v.values[0] is True


def data_col_facets(col_name: Any, values: Any, in_scale_block: Any = None) -> dict[str, Any]:
    """Describe a data column as orthogonal facets (DDI-style).

    Port of ``R/data_check_helpers.R::data_col_facets()``: ``representation``,
    ``measurement_level``, ``concept``, ``role``, ``unit``, ``quality`` and
    ``parse_note``, plus ``data_col_type()``'s ``numeric_values``,
    ``n_coerced``, ``is_numeric`` and ``ambiguous``. *in_scale_block* is
    ``True`` when the caller found the column inside a scale block (``None``
    means unknown, R's ``NA``).
    """
    from pytacheck.datacheck.columns import data_col_type

    prim = data_col_type(col_name, values)
    ct = prim.get("col_type")
    if ct is not None and ct != ct:
        ct = None
    rv = rvec(values).drop_na()
    n_nona = len(rv)
    n_unique = len(unique(rv.values))
    parsed: list[list[float]] = []

    def nums() -> list[float]:
        """``as.numeric(gsub(",", ".", as.character(x_noNA)))`` without NA (parsed once)."""
        if not parsed:
            parsed.append(_numbers(rv))
        return parsed[0]

    f = _coltype_to_facets(ct, prim.get("is_numeric"))
    representation = f["rep"]
    measurement_level = f["lvl"]

    if representation is None and n_nona > 0:
        representation = "numeric" if len(nums()) / n_nona >= 0.8 else "text"

    if ct == "empty" or n_nona == 0:
        quality = "empty"
    elif ct == "constant" or n_unique == 1:
        quality = "constant"
    else:
        quality = "ok"

    concept = data_col_concept(col_name, values)

    if ct == "id":
        role = "identifier"
    elif concept == "timestamp" or ct == "date":
        role = "timestamp"
    elif concept == "condition":
        role = "condition"
    else:
        role = "measure"

    if concept is None:
        if role == "identifier":
            concept = "id"
        elif ct == "date":
            concept = "date"
        elif representation == "datetime":
            concept = "timestamp"

    if concept is None and _is_true(in_scale_block):
        concept = "likert"
        measurement_level = "ordinal"

    if measurement_level is None and concept in ("gender", "race", "accuracy", "condition"):
        measurement_level = "nominal"

    if (
        measurement_level is None
        and not _is_true(prim.get("is_numeric"))
        and representation != "empty"
        and n_nona > 0
        and len(nums()) / n_nona < 0.5
    ):
        measurement_level = "nominal"

    unit: str | None = None
    if concept == "reaction_time":
        pos = [v for v in nums() if v > 0]
        if pos:
            unit = "milliseconds" if median(pos) >= 100 else "seconds"
        if measurement_level is None:
            measurement_level = "ratio"
    elif concept == "age":
        unit = "years"
        if measurement_level is None:
            measurement_level = "ratio"

    if ct == "continuous_comma_decimal":
        parse_note: str | None = "comma_decimal"
    elif ct == "continuous_outliers_excluded":
        parse_note = "mostly_numeric"
    else:
        parse_note = None

    return {
        "representation": representation,
        "measurement_level": measurement_level,
        "concept": concept,
        "role": role,
        "unit": unit,
        "quality": quality,
        "parse_note": parse_note,
        "numeric_values": prim.get("numeric_values"),
        "n_coerced": prim.get("n_coerced"),
        "is_numeric": prim.get("is_numeric"),
        "ambiguous": prim.get("ambiguous"),
    }


def _facet(f: Any, key: str) -> Any:
    if isinstance(f, dict):
        return f.get(key)
    return getattr(f, key, None)


def _tabular_usable(facets: Any, df: Any) -> dict[str, Any]:
    """Is a read-in data frame a usable rectangular dataset (not a coding worksheet)?

    Port of ``R/data_check_helpers.R::.tabular_usable()``: returns
    ``{"usable": bool, "reason": str | None}`` from the share of free-text
    columns and of mostly-empty columns.
    """
    if isinstance(facets, Mapping):
        facets = list(facets.values())
    p = len(facets) if facets is not None else 0
    if p == 0 or df is None or len(df) == 0:
        return {"usable": False, "reason": "the file has no data rows or columns"}
    is_prose = []
    miss_hi = []
    for j in range(p):
        f = facets[j]
        col = rvec(df.iloc[:, j])
        non_na = [v for v in col.values if v is not None]
        miss_hi.append((len(col) - len(non_na)) / len(col) > 0.5 if len(col) else False)
        if _facet(f, "representation") != "text":
            is_prose.append(False)
            continue
        if _facet(f, "concept") in ("id", "date", "timestamp"):
            is_prose.append(False)
            continue
        is_prose.append(bool(non_na) and len(unique(non_na)) / len(non_na) > 0.5)
    prose_frac = sum(is_prose) / p
    miss_frac = sum(miss_hi) / p

    def pct(v: float) -> str:
        return f"{fmt_pct0(100 * v)}%"

    if prose_frac >= _TABULAR_PROSE_HIGH:
        return {
            "usable": False,
            "reason": f"{pct(prose_frac)} of columns are free text, not variables",
        }
    if prose_frac >= _TABULAR_PROSE_MID and miss_frac >= _TABULAR_MISS_MID:
        return {
            "usable": False,
            "reason": (
                f"{pct(prose_frac)} of columns are free text and {pct(miss_frac)} are mostly "
                "empty -- this looks like a coding worksheet, not a rectangular dataset"
            ),
        }
    return {"usable": True, "reason": None}
