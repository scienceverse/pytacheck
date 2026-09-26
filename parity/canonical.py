"""Canonical encoding of Python/pandas values, mirroring parity/r/canonical.R.

A Python list is an R atomic vector only when its elements (``None`` aside)
share one R type (``int`` and ``float`` together are a double vector); any
other list stays a list, as R keeps the element types of a list
(``jsonlite::read_json(simplifyVector = FALSE)``, list columns). For the same
reason a list of dicts is a data frame only when every column has one type.

A complex number is the pair ``[re, im]`` (each part as in a double vector),
on both sides: nothing reproduces R's printing of complex numbers.
"""

from __future__ import annotations

import datetime as dt
import math
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd

_CHECKOUT = Path(__file__).resolve().parent.parent
#: the checkout, as the OS writes it and (on Windows) with "/" as R writes it
_ROOTS = tuple(dict.fromkeys((str(_CHECKOUT), _CHECKOUT.as_posix())))
_ROOTS_JSON = tuple(orjson.dumps(r)[1:-1] for r in _ROOTS)
_SCALAR_TYPES = (str, bool, int, float, complex, np.generic)


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    if isinstance(x, float | np.floating):
        return bool(np.isnan(x))
    if isinstance(x, complex | np.complexfloating):
        return _complex_is_na(complex(x))
    return False


def _scalar_type(x: Any) -> str | None:
    if _is_na(x):
        return None
    if isinstance(x, complex | np.complexfloating):
        return "cplx"
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
    if t == "cplx":
        z = complex(x)
        return [_dbl(z.real), _dbl(z.imag)]
    if t == "lgl":
        return bool(x)
    if t == "int":
        return int(x)
    if t == "dbl":
        return _dbl(float(x))
    if isinstance(x, pd.Timestamp | dt.datetime):
        return x.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(x, dt.date):
        return x.isoformat()
    return str(x)


def _dbl(f: float) -> float | str:
    """A double as parity/r/canonical.R writes it: NaN and infinities as strings."""
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "Inf" if f > 0 else "-Inf"
    return f


def _vector_type(values: list[Any]) -> str | None:
    """The R type of the atomic vector of *values*, or ``None`` when they mix types."""
    kinds = {_scalar_type(v) for v in values} - {None}
    if not kinds:
        return "lgl"
    if kinds <= {"int", "dbl"}:
        return "dbl" if "dbl" in kinds else "int"
    if len(kinds) == 1:
        return kinds.pop()
    return None


def _vector(values: list[Any], t: str | None = None) -> dict[str, Any]:
    if t is None:
        t = _vector_type(values) or "chr"
    return {"t": t, "v": [_scalar_value(v, t) for v in values]}


def _atomic_or_list(values: list[Any]) -> dict[str, Any]:
    """*values* (all scalars) as a vector when they share a type, else as a list."""
    t = _vector_type(values)
    if t is None:
        return {"t": "list", "names": None, "v": [_encode(v) for v in values]}
    return _vector(values, t)


def _raw(x: bytes | bytearray | memoryview) -> dict[str, Any]:
    """A raw vector: R's ``as.character()``, two lower-case hex digits per byte."""
    return {"t": "raw", "v": [f"{b:02x}" for b in bytes(x)]}


def _r_na_bits(x: float) -> bool:
    """R's ``NA_real_`` (a NaN whose low word is 1954), not just any NaN."""
    return math.isnan(x) and struct.unpack("<Q", struct.pack("<d", x))[0] & 0xFFFFFFFF == 1954


def _complex_is_na(z: complex) -> bool:
    """A missing complex number: R's ``NA_real_`` in either part (``is.na()`` in R and
    not ``is.nan()``: parity/r/canonical.R writes it ``null``), or -- pytacheck's
    missing complex -- NaN in both."""
    return _r_na_bits(z.real) or _r_na_bits(z.imag) or (math.isnan(z.real) and math.isnan(z.imag))


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
    if pd.api.types.is_complex_dtype(dtype):
        return "cplx"
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
        return _atomic_or_list(values)
    return {"t": "list", "names": None, "v": [_encode(v) for v in values]}


def encode_frame(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "t": "df",
        "nrow": len(df),
        "names": [str(c) for c in df.columns],
        "v": [encode_column(col) for _, col in df.items()],
    }


def _records_frame(records: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """A list of same-keyed dicts is what R holds as a data frame, when each
    key holds scalars of one type (otherwise R holds a list of lists)."""
    if not records or not all(isinstance(r, Mapping) for r in records):
        return None
    keys = list(records[0])
    if not all(list(r) == keys for r in records):
        return None
    if not all(_is_scalar_like(r[k]) for r in records for k in keys):
        return None
    if any(_vector_type([r[k] for r in records]) is None for k in keys):
        return None
    return encode_frame(pd.DataFrame(records, columns=keys))


def canonical(x: Any) -> dict[str, Any]:
    """Encode *x* in the canonical parity form, the checkout directory written
    ``<repo>`` (as parity/r/run_cases.R writes goldens)."""
    encoded: dict[str, Any] = portable(_encode(x))
    return encoded


def portable(value: Any) -> Any:
    """*value* (canonical JSON or a string) with the checkout directory written ``<repo>``."""
    if isinstance(value, str):
        return _portable_str(value)
    try:
        raw = orjson.dumps(value)
    except TypeError:  # e.g. a lone surrogate from undecodable bytes
        return _portable_walk(value)
    if not any(r in raw for r in _ROOTS_JSON):
        return value
    for r in _ROOTS_JSON:
        raw = raw.replace(r, b"<repo>")
    return orjson.loads(raw)


def _portable_str(value: str) -> str:
    for r in _ROOTS:
        value = value.replace(r, "<repo>")
    return value


def _portable_walk(value: Any) -> Any:
    if isinstance(value, str):
        return _portable_str(value)
    if isinstance(value, dict):
        return {_portable_walk(k): _portable_walk(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable_walk(v) for v in value]
    return value


def _encode(x: Any) -> dict[str, Any]:
    from pytacheck.module import ModuleOutput
    from pytacheck.papers import Paper, PaperList

    if x is None or x is pd.NA:
        return {"t": "null"}
    if isinstance(x, ModuleOutput):
        names = [k for k in list(x.keys()) if k not in ("paper", "prev_outputs")]
        return {"t": "module_output", "names": names, "v": [_encode(x.get(k)) for k in names]}
    if isinstance(x, Paper):
        names = ["paper_id", *x.keys()]
        return {"t": "paper", "names": names, "v": [_encode(x[k]) for k in names]}
    if isinstance(x, PaperList):
        return {"t": "paperlist", "names": x.names, "v": [_encode(p) for p in x]}
    if isinstance(x, pd.DataFrame):
        return encode_frame(x)
    if isinstance(x, pd.Series):
        return encode_column(x)
    if isinstance(x, bytes | bytearray | memoryview):
        return _raw(x)
    if isinstance(x, np.ndarray):
        if x.ndim > 1:
            return {
                "t": "matrix",
                "dim": list(x.shape),
                "v": _vector(x.flatten(order="F").tolist()),
            }
        return _atomic_or_list(x.tolist()) if x.dtype == object else _vector(x.tolist())
    if _is_scalar_like(x):
        t = _scalar_type(x)
        return _vector([x], t) if t else {"t": "lgl", "v": [None]}
    if isinstance(x, Mapping):
        return {"t": "list", "names": [str(k) for k in x], "v": [_encode(v) for v in x.values()]}
    if isinstance(x, list | tuple | set | frozenset):
        items = list(x)
        if all(_is_scalar_like(v) for v in items):
            return _atomic_or_list(items)
        frame = _records_frame(items)
        if frame is not None:
            return frame
        return {"t": "list", "names": None, "v": [_encode(v) for v in items]}
    if hasattr(x, "to_canonical"):
        return x.to_canonical()  # type: ignore[no-any-return]
    return {"t": "other", "class": [type(x).__name__], "repr": repr(x)}
