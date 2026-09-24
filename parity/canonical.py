"""Canonical encoding of Python/pandas values, mirroring parity/r/canonical.R."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

_SCALAR_TYPES = (str, bool, int, float, np.generic)


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    if isinstance(x, float | np.floating):
        return bool(np.isnan(x))
    return False


def _scalar_type(x: Any) -> str | None:
    if _is_na(x):
        return None
    if isinstance(x, bool | np.bool_):
        return "lgl"
    if isinstance(x, int | np.integer):
        return "int"
    if isinstance(x, float | np.floating):
        return "dbl"
    if isinstance(x, str | np.str_):
        return "chr"
    if isinstance(x, dt.date | pd.Timestamp):
        return "chr"
    return None


def _scalar_value(x: Any, t: str) -> Any:
    if _is_na(x):
        return None
    if t == "lgl":
        return bool(x)
    if t == "int":
        return int(x)
    if t == "dbl":
        f = float(x)
        if math.isinf(f):
            return "Inf" if f > 0 else "-Inf"
        return f
    if isinstance(x, pd.Timestamp | dt.datetime):
        return x.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(x, dt.date):
        return x.isoformat()
    return str(x)


def _vector(values: list[Any], t: str | None = None) -> dict[str, Any]:
    if t is None:
        kinds = {_scalar_type(v) for v in values} - {None}
        if not kinds:
            t = "lgl"
        elif kinds <= {"int", "dbl"}:
            t = "dbl" if "dbl" in kinds else "int"
        elif len(kinds) == 1:
            t = kinds.pop()
        else:
            t = "chr"
    return {"t": t, "v": [_scalar_value(v, t) for v in values]}


def _series_type(s: pd.Series) -> str | None:
    dtype = s.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return "chr"
    if pd.api.types.is_bool_dtype(dtype):
        return "lgl"
    if pd.api.types.is_integer_dtype(dtype):
        return "int"
    if pd.api.types.is_float_dtype(dtype):
        return "dbl"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "chr"
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "chr"
    return None  # object: inspect cells


def _is_scalar_like(x: Any) -> bool:
    return _is_na(x) or isinstance(x, (*_SCALAR_TYPES, dt.date))


def encode_column(s: pd.Series) -> dict[str, Any]:
    t = _series_type(s)
    values = s.tolist()
    if t is not None:
        if t == "chr" and isinstance(s.dtype, pd.CategoricalDtype):
            values = [None if _is_na(v) else str(v) for v in values]
        return _vector(values, t)
    if all(_is_scalar_like(v) for v in values):
        return _vector(values)
    return {"t": "list", "names": None, "v": [canonical(v) for v in values]}


def encode_frame(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "t": "df",
        "nrow": len(df),
        "names": [str(c) for c in df.columns],
        "v": [encode_column(df.iloc[:, i]) for i in range(df.shape[1])],
    }


def _records_frame(records: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """A list of same-keyed dicts is what R holds as a data frame."""
    if not records or not all(isinstance(r, Mapping) for r in records):
        return None
    keys = list(records[0])
    if not all(list(r) == keys for r in records):
        return None
    if not all(_is_scalar_like(r[k]) for r in records for k in keys):
        return None
    return encode_frame(pd.DataFrame(records, columns=keys))


def canonical(x: Any) -> dict[str, Any]:
    """Encode *x* in the canonical parity form."""
    from pytacheck.module import ModuleOutput
    from pytacheck.papers import Paper, PaperList

    if x is None or x is pd.NA:
        return {"t": "null"}
    if isinstance(x, ModuleOutput):
        names = [k for k in list(x.keys()) if k not in ("paper", "prev_outputs")]
        return {"t": "module_output", "names": names, "v": [canonical(x.get(k)) for k in names]}
    if isinstance(x, Paper):
        names = ["paper_id", *x.keys()]
        return {"t": "paper", "names": names, "v": [canonical(x[k]) for k in names]}
    if isinstance(x, PaperList):
        return {"t": "paperlist", "names": x.names, "v": [canonical(p) for p in x]}
    if isinstance(x, pd.DataFrame):
        return encode_frame(x)
    if isinstance(x, pd.Series):
        return encode_column(x)
    if isinstance(x, np.ndarray):
        if x.ndim > 1:
            return {
                "t": "matrix",
                "dim": list(x.shape),
                "v": _vector(x.flatten(order="F").tolist()),
            }
        return _vector(x.tolist())
    if _is_scalar_like(x):
        t = _scalar_type(x)
        return _vector([x], t) if t else {"t": "lgl", "v": [None]}
    if isinstance(x, Mapping):
        return {"t": "list", "names": [str(k) for k in x], "v": [canonical(v) for v in x.values()]}
    if isinstance(x, list | tuple | set | frozenset):
        items = list(x)
        if all(_is_scalar_like(v) for v in items):
            return _vector(items)
        frame = _records_frame(items)
        if frame is not None:
            return frame
        return {"t": "list", "names": None, "v": [canonical(v) for v in items]}
    if hasattr(x, "to_canonical"):
        return x.to_canonical()  # type: ignore[no-any-return]
    return {"t": "other", "class": [type(x).__name__], "repr": repr(x)}
