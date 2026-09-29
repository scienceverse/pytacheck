"""JSON encoding compatible with ``jsonlite::toJSON()`` defaults (as plumber uses them).

metacheck's API serialises responses with plumber's JSON serializer, i.e.
``jsonlite::toJSON()`` with its defaults, so clients of that API expect:

* vectors as arrays even when of length one (``"status": ["ok"]``);
* data frames as arrays of row objects, **omitting** missing cells;
* numbers with at most 4 decimal places (``digits = 4``), through jsonlite's
  own formatter (``modp_dtoa2``) between 1e-5 and 2^31, ``%.Ng`` outside;
* numeric ``NA``/``NaN``/``Inf`` as the strings ``"NA"``/``"NaN"``/``"Inf"``,
  logical/character ``NA`` as ``null``; ``NULL`` as ``{}``.

``unboxed=True`` gives plumber's ``serializer_unboxed_json()`` (scalars not
wrapped in arrays), used for error responses.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from typing import Any

import orjson

__all__ = ["format_number", "to_json"]

_POW10 = [1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000, 1000000000]


def _modp_dtoa2(value: float, prec: int) -> str:
    """stringencoders' ``modp_dtoa2`` as modified by jsonlite (trailing zeros trimmed)."""
    prec = max(0, min(prec, 9))
    neg = value < 0
    if neg:
        value = -value
    whole = int(value)
    tmp = (value - whole) * _POW10[prec]
    frac = int(tmp)
    diff = tmp - frac
    if diff > 0.5:
        frac += 1
        if frac >= _POW10[prec]:
            frac = 0
            whole += 1
    elif diff == 0.5 and frac & 1:  # jsonlite: ties to even
        frac += 1
    if prec == 0:
        diff = value - whole
        if diff > 0.5 or (diff == 0.5 and whole & 1):
            whole += 1
        text = str(whole)
    elif frac:
        digits = str(frac).rjust(prec, "0").rstrip("0")
        text = f"{whole}.{digits}"
    else:
        text = str(whole)
    return f"-{text}" if neg and text != "0" else text


def format_number(value: float, digits: int = 4) -> str:
    """How ``jsonlite::toJSON(x, digits = digits)`` prints one finite double."""
    if value == 0:
        return "0"
    if -1 < digits < 10 and 1e-5 < abs(value) < 2147483647:
        return _modp_dtoa2(value, digits)
    sig = math.ceil(min(17, max(1, math.log10(abs(value))) + digits))
    text = f"{value:.{sig}g}"
    if "e" in text:
        mant, exp = text.split("e")
        sign = exp[0]
        text = f"{mant}e{sign}{exp[1:].lstrip('0').rjust(2, '0')}"
    return text


class _Raw(str):
    """An already-encoded JSON fragment."""


def _num(value: Any, digits: int) -> Any:
    if value is None:
        return _Raw('"NA"')
    f = float(value)
    if math.isnan(f):
        return _Raw('"NaN"')
    if math.isinf(f):
        return _Raw('"Inf"' if f > 0 else '"-Inf"')
    if isinstance(value, bool):
        return value
    if isinstance(value, int) or (f.is_integer() and abs(f) < 1e15):
        return _Raw(str(int(f)) if abs(f) < 2147483647 else format_number(f, digits))
    return _Raw(format_number(f, digits))


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        import pandas as pd

        if v is pd.NA or v is pd.NaT:
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(v, float) and math.isnan(v)


def _scalar(v: Any, digits: int) -> Any:
    import numpy as np

    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return _num(v, digits)
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    if _is_missing(v):
        return None
    return str(v)


def _series(values: list[Any], kind: str, digits: int) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if isinstance(v, float) and math.isnan(v) and kind == "list":
            out.append(_Raw('"NaN"'))
        elif _is_missing(v):
            out.append(_Raw('"NA"') if kind == "num" else None)
        else:
            out.append(_scalar(v, digits))
    return out


def _convert(x: Any, digits: int, unboxed: bool) -> Any:
    import numpy as np
    import pandas as pd

    from metacheck.papers.model import Paper

    if x is None:
        return {}
    if isinstance(x, pd.DataFrame):
        return _frame(x, digits)
    if isinstance(x, Paper):
        return {k: _convert(v, digits, unboxed) for k, v in x.to_dict().items()}
    if isinstance(x, pd.Series):
        kind = (
            "num"
            if pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x)
            else "other"
        )
        return _series(x.tolist(), kind, digits)
    if isinstance(x, np.ndarray):
        return _convert(x.tolist(), digits, unboxed)
    if isinstance(x, Mapping):
        return {str(k): _convert(v, digits, unboxed) for k, v in x.items()}
    if isinstance(x, list | tuple):
        if all(_is_missing(v) or isinstance(v, str | bool | int | float | np.generic) for v in x):
            numeric = any(
                isinstance(v, int | float | np.number) and not isinstance(v, bool) for v in x
            )
            if numeric:
                return [
                    _Raw('"NaN"')
                    if isinstance(v, float) and math.isnan(v)
                    else _num(v, digits)
                    if not _is_missing(v)
                    else _Raw('"NA"')
                    for v in x
                ]
            return _series(list(x), "other", digits)
        return [_convert(v, digits, unboxed) for v in x]
    if hasattr(x, "to_canonical") or hasattr(x, "data"):
        data = getattr(x, "data", None)
        if isinstance(data, pd.DataFrame):
            return _frame(data, digits)
    value = _scalar(x, digits)
    if value is None and isinstance(x, float | int):
        value = _Raw('"NA"')
    return value if unboxed else [value]


def _frame(df: Any, digits: int) -> list[dict[str, Any]]:
    import pandas as pd

    cols = [str(c) for c in df.columns]
    rows: list[dict[str, Any]] = []
    numeric = {
        c: pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
        for c in df.columns
    }
    for record in df.itertuples(index=False, name=None):
        row: dict[str, Any] = {}
        for name, col, v in zip(cols, df.columns, record, strict=True):
            if isinstance(v, list | tuple | dict):
                row[name] = _convert(list(v) if isinstance(v, tuple) else v, digits, False)
                continue
            if _is_missing(v):
                continue
            row[name] = _num(v, digits) if numeric[col] else _scalar(v, digits)
        rows.append(row)
    return rows


def _dump(x: Any) -> str:
    if isinstance(x, _Raw):
        return str(x)
    if isinstance(x, dict):
        return "{" + ",".join(f"{orjson.dumps(k).decode()}:{_dump(v)}" for k, v in x.items()) + "}"
    if isinstance(x, list):
        return "[" + ",".join(_dump(v) for v in x) + "]"
    return orjson.dumps(x).decode()


def to_json(x: Any, digits: int = 4, unboxed: bool = False) -> str:
    """Serialise *x* the way plumber/jsonlite would."""
    return _dump(_convert(x, digits, unboxed))
