"""Missing values, truth tests and scalar coercion shared by every part of pytacheck.

Use these instead of private ``_is_na``/``_is_true``/``_chr``/``_dollar``/
``as_numeric`` copies (docs/PORTING.md, section 3). They work on one value
(a table cell, a parsed JSON value, an argument), never warn, and give
``None`` for a missing or unusable value instead of raising:

* :func:`is_missing`: ``None``, ``pd.NA``, NaN or NaT (R's ``is.na()``).
* :func:`is_true`: a single ``True`` (R's ``isTRUE()``).
* :func:`as_float`, :func:`as_int`, :func:`as_str`: R's ``as.numeric()``,
  ``as.integer()`` and ``as.character()`` of one value.
* :func:`field`: nested access into parsed JSON, ``None`` when a step is missing.
"""

from __future__ import annotations

import cmath
import functools
import math
import numbers
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.extensions import ExtensionArray

__all__ = ["as_float", "as_int", "as_str", "field", "is_missing", "is_true"]

_NA = pd.NA
_NAT = pd.NaT


def is_missing(x: Any) -> bool:
    """Whether one value is missing: ``None``, ``pd.NA``, NaN or NaT.

    NaN counts in any numeric type (float, numpy, complex, ``Decimal``), as in
    ``pd.isna()``. A container (list, dict, array, Series) is never missing itself.
    """
    if x is None or x is _NA or x is _NAT:
        return True
    if isinstance(x, float | np.floating):
        return math.isnan(x)
    if isinstance(x, complex | np.complexfloating):
        return cmath.isnan(x)
    if isinstance(x, Decimal):
        return x.is_nan()
    if isinstance(x, np.datetime64 | np.timedelta64):
        return bool(np.isnat(x))
    return False


def is_true(x: Any) -> bool:
    """R's ``isTRUE()``: whether *x* is a single ``True``.

    A Python or numpy bool, or a numpy array, Series, Index or pandas array
    holding one (a length-one logical vector in R). ``1``, ``"TRUE"``,
    ``[True]`` and a missing value are not.
    """
    if isinstance(x, np.ndarray | pd.Series | pd.Index | ExtensionArray):
        if x.size != 1:
            return False
        x = np.asarray(x, dtype=object).reshape(-1)[0]
    return x is True or (isinstance(x, np.bool_) and bool(x))


# what String2Real() skips around a number: isspace() in the C locale
_C_SPACE = " \t\n\v\f\r"
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_HEX = re.compile(r"[+-]?0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?[0-9]+)?")
_SPECIAL = {"nan": math.nan, "inf": math.inf, "infinity": math.inf}


@functools.lru_cache(maxsize=65536)
def _parse_float(text: str) -> float | None:
    body = text.strip(_C_SPACE)
    if _DECIMAL.fullmatch(body):
        return float(body)
    if _HEX.fullmatch(body):
        try:
            return float.fromhex(body)
        except OverflowError:
            return -math.inf if body.startswith("-") else math.inf
    sign, rest = (body[0], body[1:]) if body[:1] in ("+", "-") else ("+", body)
    special = _SPECIAL.get(rest.lower())
    if special is None:
        return None
    return -special if sign == "-" and not math.isnan(special) else special


def as_float(x: Any) -> float | None:
    """R's ``as.numeric()`` of one value; ``None`` is ``NA``.

    A string is read with R's number grammar: surrounding ASCII blanks are
    ignored; decimal and hexadecimal numbers (``" 1e5 "``, ``".5"``,
    ``"0x1A"``, ``"0x1.8p1"``) and ``NaN``/``Inf``/``Infinity`` in any case
    with an optional sign are numbers; anything else (``"NA"``, ``"1,5"``,
    ``"1_000"``, ``"TRUE"``, non-ASCII digits) is ``None``. A hexadecimal
    number needs a digit: R reads ``"0x."`` and ``"0xp1"`` as 0, this reads
    them as ``None``. Values are correctly rounded (R's long-double reading
    can be one bit off, or overflow just below the largest double). A bool
    is 1 or 0, a number is itself as a float (NaN stays NaN, a number beyond
    the double range is an infinity), and anything else is ``None``.
    """
    if isinstance(x, str):
        return _parse_float(str(x))
    if isinstance(x, bool | np.bool_):
        return 1.0 if x else 0.0
    if isinstance(x, numbers.Rational):  # an int or a Fraction may be beyond the double range
        try:
            return float(x)
        except OverflowError:
            return math.inf if int(x.numerator) > 0 else -math.inf
    if isinstance(x, numbers.Real | Decimal):
        try:
            return float(x)
        except ValueError:  # a signalling Decimal NaN
            return None
    return None


_INTEGER = re.compile(r"[+-]?[0-9]+")


def as_int(x: Any) -> int | None:
    """R's ``as.integer()`` of one value; ``None`` is ``NA``.

    Numbers and numeric strings (see :func:`as_float`) are truncated towards
    zero (``"1.9"`` is 1, ``-1.9`` is -1); integers and integer strings are
    read exactly, whatever their size (a digit string longer than Python's
    conversion limit, 4300 digits by default, is beyond the double range and
    ``None``). NaN, infinities and anything :func:`as_float` does not read are
    ``None``.
    """
    if isinstance(x, numbers.Integral):
        return int(x)
    if isinstance(x, str):
        body = x.strip(_C_SPACE)
        if _INTEGER.fullmatch(body):
            try:
                return int(body)
            except ValueError:  # beyond sys.get_int_max_str_digits()
                pass
    value = as_float(x)
    if value is None or not math.isfinite(value):
        return None
    return int(value)


def as_str(x: Any) -> str | None:
    """R's ``as.character()`` of one value; ``None`` is ``NA``.

    A string is itself, a bool is ``"TRUE"``/``"FALSE"`` and a number is
    printed as R prints it (15 significant digits: ``0.1 + 0.2`` is
    ``"0.3"``, ``1e5`` is ``"1e+05"``). Bytes are decoded as UTF-8 (invalid
    bytes become U+FFFD). A missing value, and a container or iterator (list,
    dict, set, array, Series, DataFrame, pandas array, range, generator), is
    ``None``; any other object is ``str(x)``.
    """
    if isinstance(x, str):
        return str(x)
    if isinstance(x, bytes | bytearray | memoryview):
        return bytes(x).decode("utf-8", "replace")
    if is_missing(x) or isinstance(x, Collection | Iterator):
        return None
    from metacheck._r.base import as_character

    return as_character(x)


def field(obj: Any, *keys: str | int | np.integer[Any], default: Any = None) -> Any:
    """``obj[k1][k2]...`` on parsed JSON, or *default* when a step is not there.

    A string key looks up a mapping and an integer key (a Python or numpy
    int, not a bool) indexes a list (Python indexing: ``-1`` is the last
    item). Keys match exactly. When a step is absent, of the wrong type, or
    ``None`` (JSON ``null``), the result is *default*, so a malformed record
    gives missing fields instead of an error::

        field(record, "attributes", "title")      # None if any level is missing
        field(record, "authors", 0, "name", default="")
    """
    for key in keys:
        if isinstance(obj, Mapping):
            obj = obj.get(key)
        elif (
            isinstance(obj, Sequence)
            and not isinstance(obj, str | bytes | bytearray)
            and isinstance(key, numbers.Integral)
            and not isinstance(key, bool)
            and -len(obj) <= key < len(obj)
        ):
            obj = obj[int(key)]
        else:
            return default
        if obj is None:
            return default
    return default if obj is None else obj
