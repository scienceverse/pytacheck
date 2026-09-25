"""R vector semantics shared by the data_check helpers (private).

The helpers in ``R/data_check_helpers.R`` branch on R's vector types
(``is.numeric()``, ``is.character()``, ``is.logical()``) and lean on base-R
coercions (``as.character()``, ``as.numeric()``, ``tolower()``, ``median()``,
``quantile()``). This module gives those a single, tested implementation:

* :func:`rvec` maps a Python value (list, tuple, ``numpy`` array, ``pandas``
  Series or Index, scalar) to an :class:`RVec` -- its R type and values, with
  ``None`` for ``NA``. ``pandas`` nullable dtypes map directly (``Int64`` ->
  integer, ``float64`` -> double, ``boolean`` -> logical, ``string`` ->
  character, ``category`` -> factor); plain lists and ``object`` columns are
  typed from their elements the way ``c()`` would coerce them.
* :func:`chr` / :func:`num` are ``as.character()`` / ``as.numeric()``.
* :func:`as_numeric_str` is ``as.numeric()`` of one string (``R_strtod()``:
  decimal, hexadecimal, ``Inf``/``NaN`` spellings, surrounding blanks).
"""

from __future__ import annotations

import datetime as dt
import functools
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pytacheck._r.base import as_character as _as_character_scalar
from pytacheck._r.regex import compile_r

__all__ = [
    "RVec",
    "as_numeric_str",
    "chr",
    "chr_counts",
    "df_columns",
    "is_na",
    "is_whole",
    "median",
    "num",
    "quantile7",
    "r_colon",
    "row_as_character",
    "rvec",
    "scalar_chr",
    "tolower",
    "toupper",
    "trim",
    "trimmed_counts",
    "unique",
    "weighted_median",
]

_INT_MAX = 2147483647

#: kinds whose ``is.numeric()`` is TRUE
_NUMERIC = frozenset(("double", "integer"))


class RVec:
    """An R atomic vector: its type (``kind``) and values (``None`` = ``NA``).

    ``kind`` is one of ``"double"``, ``"integer"``, ``"logical"``,
    ``"character"``, ``"factor"``, ``"Date"``, ``"POSIXct"`` or ``"NULL"``.
    For a factor, ``values`` are the labels and ``levels`` the level set.
    """

    __slots__ = ("kind", "levels", "values")

    def __init__(self, kind: str, values: list[Any], levels: list[str] | None = None) -> None:
        self.kind = kind
        self.values = values
        self.levels = levels

    def __len__(self) -> int:
        return len(self.values)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RVec({self.kind!r}, {self.values[:6]!r}{'...' if len(self.values) > 6 else ''})"

    @property
    def is_numeric(self) -> bool:
        """R ``is.numeric()``."""
        return self.kind in _NUMERIC

    def subset(self, keep: Sequence[bool]) -> RVec:
        """``x[keep]`` (a logical index without NA)."""
        return RVec(
            self.kind, [v for v, k in zip(self.values, keep, strict=True) if k], self.levels
        )

    def drop_na(self) -> RVec:
        """``x[!is.na(x)]``."""
        return RVec(self.kind, [v for v in self.values if v is not None], self.levels)


def is_na(x: Any) -> bool:
    """R ``is.na()`` for one Python value (``None``, NaN, ``pd.NA``, ``NaT``)."""
    if x is None:
        return True
    if isinstance(x, float):
        return x != x
    try:
        if x != x:  # numpy floating NaN, NaT
            return True
    except (TypeError, ValueError):
        pass
    t = type(x).__name__
    return t in ("NAType", "NaTType")


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool) or type(v).__name__ == "bool_"


def _is_int(v: Any) -> bool:
    if _is_bool(v):
        return False
    if isinstance(v, int):
        return True
    import numpy as np

    return isinstance(v, np.integer)


def _is_float(v: Any) -> bool:
    if isinstance(v, float):
        return True
    import numpy as np

    return isinstance(v, np.floating)


def _from_elements(items: Iterable[Any]) -> RVec:
    """Type a list of Python values the way ``c()`` combines R scalars."""
    vals = [None if is_na(v) else v for v in items]
    kinds = set()
    for v in vals:
        if v is None:
            continue
        if _is_bool(v):
            kinds.add("logical")
        elif _is_int(v):
            kinds.add("integer" if abs(int(v)) <= _INT_MAX else "double")
        elif _is_float(v):
            kinds.add("double")
        elif isinstance(v, str | bytes):
            kinds.add("character")
        elif isinstance(v, dt.datetime):
            kinds.add("POSIXct")
        elif isinstance(v, dt.date):
            kinds.add("Date")
        else:
            kinds.add("character")
    if not kinds:
        return RVec("logical", vals)
    if len(kinds) == 1:
        kind = kinds.pop()
    elif kinds <= {"logical", "integer", "double"}:
        kind = "double" if "double" in kinds else "integer"
    else:
        kind = "character"
    if kind == "logical":
        return RVec(kind, [None if v is None else bool(v) for v in vals])
    if kind == "integer":
        return RVec(kind, [None if v is None else int(v) for v in vals])
    if kind == "double":
        out: list[Any] = []
        for v in vals:
            if v is None:
                out.append(None)
            else:
                f = float(v)
                out.append(None if f != f else f)
        return RVec(kind, out)
    if kind == "character":
        return RVec(kind, [None if v is None else _scalar_chr(v) for v in vals])
    return RVec(kind, vals)


def _scalar_chr(v: Any) -> str:
    """``as.character()`` of one non-NA scalar of any type (``c()`` coercion)."""
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "surrogateescape")
    if isinstance(v, dt.datetime):
        return _posix_chr(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    s = _as_character_scalar(v)
    return "NaN" if s is None else s


def _posix_chr(v: Any) -> str:
    """``as.character(<POSIXct>)`` (R >= 4.3, ``base::as.character.POSIXt``).

    The date is ``"%d-%02d-%02d"``; the time is added when the (unrounded)
    time of day is non-zero, with the seconds rounded to 6 digits and printed
    in fixed notation without trailing zeros, zero-padded below 10
    (``"10:00:09.25"``).
    """
    from pytacheck._r.base import r_round

    date = f"{v.year}-{v.month:02d}-{v.day:02d}"
    sec = v.second + getattr(v, "microsecond", 0) / 1e6 + getattr(v, "nanosecond", 0) / 1e9
    if v.hour + v.minute + sec == 0:
        return date
    s = float(r_round(sec, 6))
    txt = f"{s:.6f}".rstrip("0").rstrip(".")
    return f"{date} {v.hour:02d}:{v.minute:02d}:{'0' if s < 10 else ''}{txt}"


def rvec(x: Any) -> RVec:
    """Map *x* to its R vector type and values (``None`` is R's ``NULL``)."""
    if isinstance(x, RVec):
        return x
    if x is None:
        return RVec("NULL", [])
    if isinstance(x, str | bytes):
        return RVec("character", [_scalar_chr(x)])
    if _is_bool(x):
        return RVec("logical", [bool(x)])
    if _is_int(x):
        return _from_elements([x])
    if _is_float(x):
        f = float(x)
        return RVec("double", [None if f != f else f])
    if isinstance(x, dt.date):
        return _from_elements([x])
    if hasattr(x, "dtype") and hasattr(x, "__len__"):
        return _from_array(x)
    if isinstance(x, Mapping):
        return _from_elements(x.values())
    if isinstance(x, Iterable):
        return _from_elements(list(x))
    return _from_elements([x])


def _from_array(x: Any) -> RVec:
    import numpy as np
    import pandas as pd

    dtype = x.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        cat = pd.Categorical(x) if not isinstance(x, pd.Categorical) else x
        cats = [_scalar_chr(c) for c in cat.categories]
        codes = np.asarray(cat.codes)
        return RVec("factor", [None if c < 0 else cats[c] for c in codes.tolist()], cats)
    arr = x.array if isinstance(x, pd.Series | pd.Index) else x
    if pd.api.types.is_bool_dtype(dtype):
        return RVec("logical", _object_list(arr))
    if pd.api.types.is_integer_dtype(dtype):
        ints = _object_list(arr)
        if ints and _max_abs_int(arr) > _INT_MAX:
            return RVec("double", [None if v is None else float(v) for v in ints])
        return RVec("integer", ints)
    if pd.api.types.is_float_dtype(dtype):
        fl = np.asarray(arr, dtype="float64")
        return RVec("double", [None if v != v else v for v in fl.tolist()])
    if pd.api.types.is_datetime64_any_dtype(dtype):
        ts = pd.Series(arr)
        return RVec("POSIXct", [None if is_na(v) else v for v in ts.tolist()])
    if isinstance(dtype, pd.StringDtype):
        return RVec("character", _object_list(arr))
    return _from_elements(list(arr))


def _object_list(arr: Any) -> list[Any]:
    """The Python values of a boolean / integer / string array, ``None`` for NA.

    Vectorised: ``to_numpy(object)`` gives Python ``bool``/``int``/``str``
    scalars (a NumPy array has no missing values).
    """
    import numpy as np

    if hasattr(arr, "to_numpy") and not isinstance(arr, np.ndarray):
        return arr.to_numpy(dtype=object, na_value=None).tolist()  # type: ignore[no-any-return]
    return np.asarray(arr).tolist()  # type: ignore[no-any-return]


def _max_abs_int(arr: Any) -> int:
    """The largest absolute value in an integer array (NA ignored)."""
    import numpy as np

    if hasattr(arr, "to_numpy") and not isinstance(arr, np.ndarray):
        a = arr.to_numpy(dtype="float64", na_value=0.0)
    else:
        a = np.asarray(arr, dtype="float64")
    return int(np.abs(a).max()) if a.size else 0


# -- coercions -----------------------------------------------------------------


def chr(x: Any) -> list[str | None]:
    """R ``as.character(x)``."""
    v = rvec(x)
    k = v.kind
    if k in ("character", "factor"):
        return list(v.values)
    if k == "logical":
        return [None if b is None else ("TRUE" if b else "FALSE") for b in v.values]
    if k == "integer":
        return [None if i is None else str(i) for i in v.values]
    if k == "double":
        return _dbl_chr_list(v.values)
    if k == "Date":
        return [None if d is None else d.isoformat() for d in v.values]
    if k == "POSIXct":
        return [None if d is None else _posix_chr(d) for d in v.values]
    return [None if e is None else _scalar_chr(e) for e in v.values]


def chr_counts(x: Any) -> dict[str | None, int]:
    """How often each value of ``as.character(x)`` occurs (``None`` = ``NA``).

    Keys are in order of first appearance, so a check that only needs the
    multiset of values (``unique()``, ``mean()``, ``table()``) converts each
    distinct value once instead of every element.
    """
    from collections import Counter

    v = rvec(x)
    raw = Counter(v.values)
    labels = chr(RVec(v.kind, list(raw), v.levels))
    out: dict[str | None, int] = {}
    for lab, n in zip(labels, raw.values(), strict=True):
        out[lab] = out.get(lab, 0) + n
    return out


def trimmed_counts(x: Any) -> dict[str, int]:
    """``table(trimws(as.character(x)))`` without NA and blanks, in first-appearance order."""
    out: dict[str, int] = {}
    for s, n in chr_counts(x).items():
        if s is None:
            continue
        t = s.strip(" \t\r\n")
        if t:
            out[t] = out.get(t, 0) + n
    return out


def weighted_median(pairs: Iterable[tuple[float, int]]) -> float:
    """``stats::median()`` of a vector given as ``(value, count)`` pairs."""
    items = sorted(pairs)
    n = sum(c for _, c in items)
    if n == 0:
        return math.nan
    lo_i, hi_i = (n - 1) // 2, n // 2  # 0-based middle positions
    lo = hi = None
    seen = 0
    for val, c in items:
        if lo is None and seen + c > lo_i:
            lo = val
        if seen + c > hi_i:
            hi = val
            break
        seen += c
    assert lo is not None and hi is not None
    return float(lo) if lo == hi else (lo + hi) / 2


def _dbl_chr_list(values: list[Any]) -> list[str | None]:
    cache: dict[float, str | None] = {}
    out: list[str | None] = []
    for f in values:
        if f is None:
            out.append(None)
            continue
        s = cache.get(f)
        if s is None:
            s = _as_character_scalar(f)
            if s is None:
                s = "NaN"
            cache[f] = s
        out.append(s)
    return out


def dbl_chr(f: float) -> str:
    """``as.character()`` of one double (15 significant digits, ``Inf``/``NaN``)."""
    s = _as_character_scalar(float(f))
    return "NaN" if s is None else s


_C_SPACE = " \t\n\v\f\r"
_DECIMAL = compile_r(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", perl=True)
_HEX = compile_r(
    r"([+-]?)0[xX]((?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?[0-9]+)?)", perl=True
)
_SPECIAL = {"nan": math.nan, "inf": math.inf, "infinity": math.inf}


@functools.lru_cache(maxsize=65536)
def as_numeric_str(s: str | None) -> float | None:
    """``as.numeric(s)`` for one string: ``None`` is ``NA``; ``"NaN"`` gives NaN.

    Mirrors ``String2Real()``/``R_strtod()``: surrounding C blanks are ignored,
    ``"NA"`` is ``NA``, decimal and hexadecimal (``0x1A``, ``0x1p3``) numbers,
    and ``NaN``/``Inf``/``Infinity`` in any case with an optional sign.
    """
    if s is None:
        return None
    body = s.strip(_C_SPACE)
    if not body or body == "NA":
        return None
    if _DECIMAL.fullmatch(body):
        return float(body)
    m = _HEX.fullmatch(body)
    if m:
        mant = m.group(2)
        try:
            v = float.fromhex("0x" + mant if "p" in mant.lower() else "0x" + mant + "p0")
        except (ValueError, OverflowError):
            return None
        return -v if m.group(1) == "-" else v
    sign = 1.0
    rest = body
    if rest[:1] in "+-":
        sign = -1.0 if rest[0] == "-" else 1.0
        rest = rest[1:]
    special = _SPECIAL.get(rest.lower())
    if special is None:
        return None
    return special if special != special else sign * special


def num(x: Any) -> list[float | None]:
    """R ``as.numeric(x)``: ``None`` is ``NA``; NaN only from ``"NaN"`` strings.

    A factor gives its integer codes, as in R.
    """
    v = rvec(x)
    k = v.kind
    if k == "double":
        return list(v.values)
    if k == "integer":
        return [None if i is None else float(i) for i in v.values]
    if k == "logical":
        return [None if b is None else (1.0 if b else 0.0) for b in v.values]
    if k == "factor":
        pos = {lv: i + 1 for i, lv in enumerate(v.levels or [])}
        return [None if s is None else float(pos[s]) for s in v.values]
    if k == "Date":
        epoch = dt.date(1970, 1, 1)
        return [None if d is None else float((d - epoch).days) for d in v.values]
    if k == "POSIXct":
        return [None if d is None else float(_posix_seconds(d)) for d in v.values]
    return [as_numeric_str(s) for s in chr(v)]


def _posix_seconds(d: Any) -> float:
    ts = getattr(d, "timestamp", None)
    if ts is not None:
        if getattr(d, "tzinfo", None) is None:
            return float(d.replace(tzinfo=dt.UTC).timestamp())
        return float(ts())
    return float("nan")


def num_chr(values: Iterable[str | None]) -> list[float | None]:
    """``as.numeric()`` of a character vector."""
    return [as_numeric_str(s) for s in values]


def row_as_character(
    cells: Sequence[Any],
    kinds: Sequence[str],
    levels: Sequence[Sequence[str] | None] | None = None,
) -> list[str | None]:
    """``as.character(df[i, , drop = TRUE])`` for a row of a multi-column frame.

    ``df[i, , drop = TRUE]`` is a list, and ``as.character()`` of a list
    deparses each element: a character ``NA`` stays ``NA`` but any other
    ``NA`` becomes the string ``"NA"``; a Date / POSIXct becomes its
    underlying number and a factor its integer code (*levels* gives each
    factor column's level set; a cell that is already a code is kept).
    """
    out: list[str | None] = []
    for j, (v, k) in enumerate(zip(cells, kinds, strict=True)):
        if is_na(v):
            out.append(None if k == "character" else "NA")
        elif k == "character":
            out.append(_scalar_chr(v))
        elif k == "factor":
            lv = levels[j] if levels is not None else None
            if lv is not None and v in lv:
                out.append(str(list(lv).index(v) + 1))
            else:
                out.append(str(v))
        elif k == "logical":
            out.append("TRUE" if v else "FALSE")
        elif k == "integer":
            out.append(str(int(v)))
        elif k == "double":
            out.append(dbl_chr(float(v)))
        elif k == "Date":
            out.append(str((v - dt.date(1970, 1, 1)).days))
        elif k == "POSIXct":
            out.append(dbl_chr(_posix_seconds(v)))
        else:
            out.append(_scalar_chr(v))
    return out


def scalar_chr(x: Any) -> str | None:
    """The first element of ``as.character(x)`` for a scalar argument.

    ``None`` (NA or NULL) and zero-length vectors give ``None``; a string is
    returned as is, and a longer vector gives its first element (R's
    ``strsplit(x)[[1]]`` / ``regexec(x)[[1]]`` idiom).
    """
    if x is None or isinstance(x, str):
        return x
    v = chr(x)
    return v[0] if v else None


def df_columns(df: Any) -> list[Any]:
    """The columns of a data frame by position (``as.list(df)``)."""
    return [df.iloc[:, j] for j in range(df.shape[1])]


def column_kinds(df: Any) -> list[str]:
    """The R type of each column of *df*."""
    return [rvec(c).kind for c in df_columns(df)]


# -- string helpers ------------------------------------------------------------


def trim(s: str | None) -> str | None:
    """R ``trimws()`` (default ``[ \\t\\r\\n]``) of one string."""
    return None if s is None else s.strip(" \t\r\n")


def tolower(s: str | None) -> str | None:
    """R ``tolower()``: ``towlower()`` per character (no final-sigma rule)."""
    if s is None:
        return None
    if s.isascii():
        return s.lower()
    return "".join("i" if c == "İ" else (c.lower() if len(c.lower()) == 1 else c) for c in s)


def toupper(s: str | None) -> str | None:
    """R ``toupper()``: ``towupper()`` per character (``ß`` stays ``ß``)."""
    if s is None:
        return None
    if s.isascii():
        return s.upper()
    return "".join(u if len(u := c.upper()) == 1 else c for c in s)


def unique(values: Iterable[Any]) -> list[Any]:
    """R ``unique()`` (first-appearance order; ``NA`` kept once)."""
    return list(dict.fromkeys(values))


# -- numeric helpers -----------------------------------------------------------


def is_whole(f: float) -> bool:
    """``f == round(f)`` for a non-NA double (``Inf`` counts as whole, as in R)."""
    return math.isinf(f) or float(f).is_integer()


def median(values: Sequence[float]) -> float:
    """``stats::median()`` of non-NA numbers (NA for an empty vector)."""
    n = len(values)
    if n == 0:
        return math.nan
    s = sorted(values)
    h = n // 2
    if n % 2:
        return float(s[h])
    return (s[h - 1] + s[h]) / 2


def quantile7(values: Sequence[float], probs: Sequence[float]) -> list[float]:
    """``stats::quantile(x, probs, names = FALSE)`` (type 7) of non-NA numbers."""
    x = sorted(values)
    n = len(x)
    out = []
    for p in probs:
        index = (n - 1) * p
        lo = math.floor(index)
        hi = math.ceil(index)
        qs = x[lo]
        h = index - lo
        if index > lo and x[hi] != qs:
            qs = (1 - h) * qs + h * x[hi]
        out.append(float(qs))
    return out


def r_colon(a: float, b: float) -> list[float]:
    """R ``a:b`` (steps of 1 from *a*, up or down)."""
    if a <= b:
        n = math.floor(b - a + 1e-10) + 1
        return [a + i for i in range(n)]
    n = math.floor(a - b + 1e-10) + 1
    return [a - i for i in range(n)]


def fmt_d(v: float) -> str:
    """``sprintf("%d", v)`` for a double: whole values only, as in R."""
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    f = float(v)
    if f != f:
        return "NA"
    if not math.isfinite(f) or f != round(f):
        raise ValueError("invalid format '%d'; use format %f, %e, %g or %a for numeric objects")
    return str(int(f))


def fmt_g(v: float, digits: int) -> str:
    """C ``sprintf("%.<digits>g", v)`` as R prints it (``Inf``, ``NaN``)."""
    if v != v:
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.{digits}g}"


def fmt_pct0(v: float) -> str:
    """``sprintf("%.0f", v)``."""
    if v != v:
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.0f}"
