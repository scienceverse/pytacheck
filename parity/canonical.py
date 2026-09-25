"""Canonical encoding of Python/pandas values, mirroring parity/r/canonical.R.

A Python list is an R atomic vector only when its elements (``None`` aside)
share one R type (``int`` and ``float`` together are a double vector); any
other list stays a list, as R keeps the element types of a list
(``jsonlite::read_json(simplifyVector = FALSE)``, list columns). For the same
reason a list of dicts is a data frame only when every column has one type.
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

_ROOT = str(Path(__file__).resolve().parent.parent)  # the checkout
_ROOT_JSON = orjson.dumps(_ROOT)[1:-1]
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
        return complex_as_character(complex(x))
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


# -- R's as.character() of a complex number (R >= 4.4) -------------------------
#
# coerceToString() -> StringFromComplex(): formatComplex() formats Re(z) and
# |Im(z)| separately with formatReal() at 15 significant digits, and
# EncodeComplex() prints them with EncodeReal0(), which (unlike
# as.character() of a double) keeps trailing zeros. formatReal()'s scientific()
# scales by powers of ten in long double; its table holds the *double* literals
# 1e0..1e27, so 1e23 and above are not exact, which decides some last digits
# (5.7273955524584049e-12 gives "5.72739555245840e-12"). numpy's longdouble is
# the same 80-bit x87 type on x86-64 Linux, where the goldens are made.

_LD = np.longdouble
_KP_MAX = 27
_TBL = [_LD(float(10**k)) for k in range(_KP_MAX + 1)]
_R_DIGITS = 15  # R_print.digits = DBL_DIG in coerceToString()


def _scientific(x: float, digits: int = _R_DIGITS) -> tuple[int, int, int, bool]:
    """``scientific()`` (src/main/format.c): ``(neg, kpower, nsig, roundingwidens)``."""
    if x == 0.0:
        return 0, 0, 1, False
    neg = 1 if x < 0 else 0
    r = -x if neg else x
    kp = math.floor(math.log10(r)) - digits + 1
    r_prec = _LD(r)
    if abs(kp) <= _KP_MAX:
        if kp > 0:
            r_prec /= _TBL[kp]
        elif kp < 0:
            r_prec *= _TBL[-kp]
    else:
        r_prec /= np.power(_LD(10), _LD(kp))
    if r_prec < _TBL[digits - 1]:
        r_prec *= _LD(10)
        kp -= 1
    alpha = float(np.rint(r_prec))
    nsig = digits
    for _ in range(digits):
        alpha /= 10.0
        if alpha == math.floor(alpha):
            nsig -= 1
        else:
            break
    if nsig == 0 and digits > 0:
        nsig = 1
        kp += 1
    kpower = kp + digits - 1
    rgt = min(max(digits - kpower, 0), _KP_MAX)
    fuzz = _LD(0.5 / float(_TBL[rgt]))
    widens = bool(0 < kpower <= _KP_MAX and _LD(r) < _TBL[kpower] - fuzz)
    return neg, kpower, nsig, widens


def _format_real(x: float) -> tuple[int, int, int]:
    """``formatReal()`` of one number: ``(w, d, e)`` (``scipen = 0``)."""
    if not math.isfinite(x):
        return (4 if x < 0 else 3), 0, 0
    neg, kpower, nsig, widens = _scientific(x)
    left = kpower + 1 - (1 if widens else 0)
    sleft = neg + (1 if left <= 0 else left)
    rgt = max(nsig - left, 0)
    mxsl = 1 + neg if left < 0 else sleft
    w_fixed = mxsl + rgt + (rgt != 0)
    e = 2 if (left > 100 or left <= -99) else 1
    d = nsig - 1
    w = neg + (d > 0) + d + 4 + e
    if w_fixed <= w:
        return w_fixed, rgt, 0
    return w, d, e


def _encode_real0(x: float, w: int, d: int, e: int) -> str:
    """``EncodeReal0()`` (src/main/printutils.c)."""
    if x == 0.0:
        x = 0.0  # no signed zeros
    if not math.isfinite(x):
        return ("NaN" if math.isnan(x) else "Inf" if x > 0 else "-Inf").rjust(w)
    if e:
        return format(x, f"{'#' if d else ''}{w}.{d}e")
    return format(x, f"{w}.{d}f")


def _r_na_bits(x: float) -> bool:
    """R's ``NA_real_`` (a NaN whose low word is 1954), not just any NaN."""
    return math.isnan(x) and struct.unpack("<Q", struct.pack("<d", x))[0] & 0xFFFFFFFF == 1954


def _complex_is_na(z: complex) -> bool:
    """``NA_complex_``: an R NA in either part, or (pytacheck's missing complex) NaN in both."""
    return _r_na_bits(z.real) or _r_na_bits(z.imag) or (math.isnan(z.real) and math.isnan(z.imag))


def complex_as_character(z: complex) -> str:
    """R's ``as.character()`` of a (non-NA) complex number, e.g. ``"1+2i"``."""
    re_ = 0.0 if z.real == 0.0 else z.real
    im = 0.0 if z.imag == 0.0 else z.imag
    real = _encode_real0(re_, *_format_real(re_))
    neg_im = im < 0
    imag = _encode_real0(-im if neg_im else im, *_format_real(abs(im)))
    if imag == "0":
        neg_im = False
    return f"{real}{'-' if neg_im else '+'}{imag}i"


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
        "v": [encode_column(df.iloc[:, i]) for i in range(df.shape[1])],
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
    return portable(_encode(x))


def portable(value: Any) -> Any:
    """*value* (canonical JSON or a string) with the checkout directory written ``<repo>``."""
    if isinstance(value, str):
        return value.replace(_ROOT, "<repo>")
    try:
        raw = orjson.dumps(value)
    except TypeError:  # e.g. a lone surrogate from undecodable bytes
        return _portable_walk(value)
    if _ROOT_JSON not in raw:
        return value
    return orjson.loads(raw.replace(_ROOT_JSON, b"<repo>"))


def _portable_walk(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(_ROOT, "<repo>")
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
