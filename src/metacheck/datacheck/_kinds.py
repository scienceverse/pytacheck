"""Column kinds for the data_check helpers (private).

``R/data_check_helpers.R`` branches on a column's R type (``is.numeric()``,
``is.character()``, ``is.logical()``, ``is.factor()``). Here that type is the
column's *kind*, read from its pandas dtype: :data:`NUMERIC`, :data:`LOGICAL`,
:data:`TEXT`, :data:`CATEGORICAL` or :data:`DATETIME`. An ``object`` column
(and a plain list, which :func:`column` turns into one) takes its kind from
``infer_dtype()``; an all-missing one is logical, as in R.

:func:`as_text` is ``as.character()`` with R's number formatting, which users
see in messages and sample values; :func:`as_numbers` is ``as.numeric()``.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.extensions import ExtensionArray

from metacheck._r.base import format_num
from metacheck._values import as_float, as_str

__all__ = [
    "CATEGORICAL",
    "DATETIME",
    "LOGICAL",
    "NUMERIC",
    "TEXT",
    "WS",
    "as_numbers",
    "as_text",
    "column",
    "is_integer",
    "kind",
    "row_texts",
    "trimmed_counts",
]

NUMERIC, LOGICAL, TEXT, CATEGORICAL, DATETIME = (
    "numeric", "logical", "text", "categorical", "datetime",
)  # fmt: skip

#: R's ``trimws()`` whitespace
WS = " \t\r\n"

_INFERRED = {
    "empty": LOGICAL,
    "boolean": LOGICAL,
    "integer": NUMERIC,
    "floating": NUMERIC,
    "mixed-integer-float": NUMERIC,
    "decimal": NUMERIC,
    "datetime": DATETIME,
    "datetime64": DATETIME,
    "date": DATETIME,
}


def column(x: Any) -> pd.Series:
    """*x* as a Series: a Series as is, an array wrapped, anything else as an ``object`` column."""
    if isinstance(x, pd.Series):
        return x
    if isinstance(x, pd.Index | np.ndarray | ExtensionArray):
        return pd.Series(x, copy=False)
    if x is None:
        return pd.Series([], dtype=object)
    if isinstance(x, Mapping):
        x = list(x.values())
    elif isinstance(x, str | bytes) or not isinstance(x, Iterable):
        x = [x]
    return pd.Series(list(x), dtype=object)


def kind(x: Any) -> str:
    """The kind of a column (see the module docstring)."""
    col = column(x)
    dtype = col.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return CATEGORICAL
    if pd.api.types.is_bool_dtype(dtype):
        return LOGICAL
    if pd.api.types.is_numeric_dtype(dtype):
        return NUMERIC
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return DATETIME
    if pd.api.types.is_object_dtype(dtype):
        return _INFERRED.get(pd.api.types.infer_dtype(col, skipna=True), TEXT)
    return TEXT


def is_integer(x: Any) -> bool:
    """Is a numeric column whole-number typed (R integer)?"""
    col = column(x)
    if pd.api.types.is_object_dtype(col.dtype):
        return pd.api.types.infer_dtype(col, skipna=True) == "integer"
    return pd.api.types.is_integer_dtype(col.dtype) and not pd.api.types.is_bool_dtype(col.dtype)


def as_text(x: Any) -> list[str | None]:
    """``as.character(x)``: ``None`` for a missing value; numbers as R prints them."""
    col = column(x)
    if isinstance(col.dtype, pd.StringDtype):
        return _values(col)
    if isinstance(col.dtype, pd.CategoricalDtype):
        col = col.astype(object)
    elif is_integer(col):
        if bool((np.abs(_floats(col)) > 2147483647).any()):
            col = col.astype("float64")  # beyond R's integers: a double
        else:  # an R integer prints as its digits
            return [None if v is None else str(int(v)) for v in _values(col)]
    return _texts(col)


def _texts(col: pd.Series) -> list[str | None]:
    """as_text() of a column whose integer/categorical handling is done."""
    dtype = col.dtype
    if dtype == np.float64 or isinstance(dtype, pd.Float64Dtype):
        return _double_texts(col.to_numpy(dtype="float64", na_value=np.nan))
    if pd.api.types.is_bool_dtype(dtype):
        return [None if v is None else ("TRUE" if v else "FALSE") for v in _values(col)]
    if pd.api.types.is_datetime64_any_dtype(dtype):
        fast = _datetime_texts(col)
        if fast is not None:
            return fast
    cache: dict[tuple[type, Any], str | None] = {}
    out: list[str | None] = []
    for v in _values(col):
        try:
            out.append(cache[type(v), v])
        except KeyError:
            out.append(cache.setdefault((type(v), v), None if v is None else _text(v)))
        except TypeError:  # an unhashable value
            out.append(_text(v))
    return out


def integer_numbers(x: Any) -> list[float | None] | None:
    """``as.numeric(as.character(x))`` of an R integer column, without the text; else ``None``.

    An integer prints as its digits, which read back as the same number, so
    the text is skipped. ``None`` for any other column (and for integers beyond
    R's range, which print as doubles).
    """
    col = column(x)
    if isinstance(col.dtype, pd.CategoricalDtype | pd.StringDtype) or not is_integer(col):
        return None
    floats = _floats(col)
    if bool((np.abs(floats) > 2147483647).any()):
        return None
    return [None if f != f else f for f in floats.tolist()]


def as_text_head(x: Any, k: int) -> list[str | None]:
    """``head(as.character(x[!is.na(x)]), k)``: as_text() of the first *k* non-missing values.

    The column-wide choice (an integer column beyond R's range prints as doubles) is made
    on the whole column, so each value reads as it does in ``as_text(x)``.
    """
    col = column(x)
    if isinstance(col.dtype, pd.StringDtype | pd.CategoricalDtype):
        return as_text(col[col.notna()].iloc[:k])
    if is_integer(col):
        if bool((np.abs(_floats(col)) > 2147483647).any()):
            col = col.astype("float64")
        else:
            return [str(int(v)) for v in _values(col[col.notna()].iloc[:k])]
    return _texts(col[col.notna()].iloc[:k])


def _datetime_texts(col: pd.Series) -> list[str | None] | None:
    """_text() of a date-time column whose times are whole seconds in years 1000-9999, at
    numpy speed: ``YYYY-mm-dd HH:MM:SS``, the date alone at midnight. ``None`` otherwise."""
    wall = col.dt.tz_localize(None) if getattr(col.dtype, "tz", None) is not None else col
    na = wall.isna().to_numpy()
    ok = ~na
    if not ok.any():
        return [None] * len(col)
    year = wall.dt.year.to_numpy(dtype="float64", na_value=np.nan)[ok]
    frac = (wall.dt.microsecond + wall.dt.nanosecond).to_numpy(dtype="float64", na_value=0.0)[ok]
    if (year < 1000).any() or (year > 9999).any() or frac.any():
        return None
    secs = wall.to_numpy(dtype="datetime64[s]")[ok]
    full = np.char.replace(np.datetime_as_string(secs, unit="s"), "T", " ")
    midnight = (secs - secs.astype("datetime64[D]")).astype(np.int64) == 0
    text = np.where(midnight, np.char.ljust(full, 10).astype("U10"), full)
    out = np.full(len(col), None, dtype=object)
    out[ok] = text
    return out.tolist()


def _r_double(v: float) -> str:
    """R's ``as.character()`` of a non-NA double: C's ``%.15g`` where it is what R writes
    (fixed notation that is no wider than scientific), else format_num()."""
    if v == 0:
        return "0"
    if v in (math.inf, -math.inf):
        return "Inf" if v > 0 else "-Inf"
    s = f"{v:.15g}"
    if "e" not in s:
        body = s[1:] if s[0] == "-" else s
        if "." in body:
            if not body.startswith("0.000"):
                return s
        elif body[-1] != "0":
            return s
    return format_num(v, 15)


def _double_texts(a: np.ndarray) -> list[str | None]:
    out: list[str | None] = []
    cache: dict[float, str] = {}
    for v in a.tolist():
        if v != v:
            out.append(None)
            continue
        s = cache.get(v)
        if s is None:
            s = cache[v] = _r_double(v)
        out.append(s)
    return out


def _values(col: pd.Series) -> list[Any]:
    """The column's values as Python objects, ``None`` for a missing one."""
    vals = col.to_numpy(dtype=object, na_value=None)  # type: ignore[call-overload]
    if col.dtype.kind in "mM":  # pandas keeps NaT in a date-time column
        vals[col.isna().to_numpy()] = None
    return vals.tolist()  # type: ignore[no-any-return]


def _floats(col: pd.Series) -> np.ndarray:
    return col.to_numpy(dtype="float64", na_value=np.nan)


def _text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dt.datetime):  # R >= 4.3: the time only when it is not midnight
        date = f"{v.year}-{v.month:02d}-{v.day:02d}"
        sec = round(v.second + v.microsecond / 1e6 + getattr(v, "nanosecond", 0) / 1e9, 6)
        if v.hour + v.minute + sec == 0:
            return date
        secs = f"{sec:.6f}".rstrip("0").rstrip(".")
        return f"{date} {v.hour:02d}:{v.minute:02d}:{'0' if sec < 10 else ''}{secs}"
    if isinstance(v, dt.date):
        return v.isoformat()
    s = as_str(v)
    return "NaN" if s is None else s


def as_numbers(x: Any) -> np.ndarray:
    """``as.numeric(x)`` as a float64 array (NaN for NA); a categorical gives its codes."""
    col = column(x)
    k = kind(col)
    if k in (NUMERIC, LOGICAL):
        return _floats(col)
    if k == CATEGORICAL:
        codes = col.cat.codes.to_numpy()
        return np.where(codes < 0, np.nan, codes + 1.0)
    texts = as_text(col)
    parsed = {s: as_float(s) for s in set(texts)}
    return np.array([np.nan if (f := parsed[s]) is None else f for s in texts], dtype="float64")


def trimmed_counts(x: Any) -> Counter[str]:
    """``table(trimws(as.character(x)))`` without NA and blanks, in first-appearance order."""
    return Counter(t for s in as_text(x) if s is not None and (t := s.strip(WS)))


def row_texts(df: pd.DataFrame, nrows: int | None = None) -> list[list[str | None]]:
    """``as.character(df[i, , drop = TRUE])`` for the first *nrows* rows of *df*.

    A one-column frame gives the cell itself (``drop = TRUE``). A wider row is
    a list, whose ``as.character()`` keeps a text ``NA`` but writes any other
    missing cell as ``"NA"``, a categorical cell as its code and a date-time as
    its seconds. Each column's kind is read from the whole column.
    """
    head = df if nrows is None else df.iloc[: max(nrows, 0)]
    if df.shape[1] == 1:
        return [[s] for s in as_text(head.iloc[:, 0])]
    cols: list[list[str | None]] = []
    for j in range(df.shape[1]):
        k, c = kind(df.iloc[:, j]), head.iloc[:, j]
        if k == CATEGORICAL:
            texts = [None if i < 0 else str(i + 1) for i in c.cat.codes.tolist()]
        elif k == DATETIME:
            stamps = _values(c)
            texts = [None if v is None else as_str(pd.Timestamp(v).timestamp()) for v in stamps]
        else:
            texts = as_text(c)
        cols.append(texts if k == TEXT else ["NA" if s is None else s for s in texts])
    return [list(r) for r in zip(*cols, strict=True)] if cols else [[] for _ in range(len(head))]
