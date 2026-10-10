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
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.extensions import ExtensionArray

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
    elif is_integer(col) and bool((np.abs(_floats(col)) > 2147483647).any()):
        col = col.astype("float64")  # beyond R's integers: a double
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


def _values(col: pd.Series) -> list[Any]:
    """The column's values as Python objects, ``None`` for a missing one."""
    return col.to_numpy(dtype=object, na_value=None).tolist()  # type: ignore[call-overload,no-any-return]


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
