"""``json_expand()``: expand a column of JSON text into columns.

Port of ``R/text-json_expand.R``. The R function leans on three pieces of R
machinery whose exact behaviour decides the output, all reproduced here:

* ``jsonlite::fromJSON()`` (yajl with comments allowed, integers vs doubles,
  ``NULL`` -> ``NA``, and the ``simplifyVector`` / ``simplifyDataFrame`` /
  ``simplifyMatrix`` rules that turn arrays into vectors, matrices and data
  frames);
* ``paste(x, collapse = ";")`` / ``as.character()`` of those values, which
  *deparses* non-scalar list elements (``c(1, 3)``, ``1:2``, ``list(i = 1)``);
* ``utils::type.convert(as.is = TRUE)`` (``R_strtod()``/``strtol()`` number
  grammar, ``T``/``F``/``TRUE``/``FALSE`` logicals, blank fields as ``NA``),
  followed by ``dplyr::bind_rows()`` and ``dplyr::left_join()`` with suffixes.

The helpers :func:`as_numeric` (R ``as.numeric()`` of strings) and
:func:`type_convert` are reused by the other text extractors.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character
from pytacheck._r.regex import gsub

__all__ = ["as_numeric", "json_expand", "type_convert"]

_INT_MAX = 2147483647
_C_SPACE = " \t\n\x0b\x0c\r"
# glibc iswspace() in a UTF-8 locale (isBlankString): no no-break spaces.
_W_SPACE = frozenset("\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f               　")
_DIGITS = "0123456789"
_HEX = "0123456789abcdefABCDEF"


# ---------------------------------------------------------------------------
# R number parsing: R_strtod(), strtol(), String2Real(), type.convert()
# ---------------------------------------------------------------------------


def _is_blank(s: str) -> bool:
    """R ``isBlankString()``."""
    return all(c in _W_SPACE for c in s)


def _r_strtod(s: str, start: int = 0, na: bool = False) -> tuple[float | None, int]:
    """R's ``R_strtod5()``: ``(value, end)``; ``None`` is ``NA``.

    When no number can be read, ``end`` is *start* (R backs out). With
    ``na=True`` a leading ``"NA"`` (after blanks) is read as ``NA``, as
    ``type.convert()`` does when it rules out column types.
    """
    n = len(s)
    p = start
    while p < n and s[p] in _C_SPACE:
        p += 1
    if na and s.startswith("NA", p):
        return None, p + 2
    sign = 1.0
    if p < n and s[p] in "+-":
        sign = -1.0 if s[p] == "-" else 1.0
        p += 1
    low = s[p : p + 8].lower()
    if low.startswith("nan"):
        return math.nan, p + 3
    if low.startswith("inf"):
        p += 3
        if s[p : p + 5].lower() == "inity":
            p += 5
        return sign * math.inf, p
    if n - p > 2 and s[p] == "0" and s[p + 1] in "xX":
        p += 2
        ans = 0.0
        exph = -1
        while p < n:
            c = s[p]
            if c in _HEX:
                ans = 16 * ans + int(c, 16)
                if exph >= 0:
                    exph += 4
            elif c == ".":  # R restarts the binary exponent at every "."
                exph = 0
            else:
                break
            p += 1
        expn = 0
        if p < n and s[p] in "pP":
            p += 1
            expsign = 1
            if p < n and s[p] in "+-":
                expsign = -1 if s[p] == "-" else 1
                p += 1
            e0 = p
            e = 0
            while p < n and s[p] in _DIGITS:
                e = min(e * 10 + int(s[p]), 99999)
                p += 1
            if p == e0:  # an exponent needs digits: R backs out
                return None, start
            if ans != 0.0:
                expn = expsign * e
        if exph > 0:
            expn -= exph
        try:
            value = math.ldexp(ans, expn)
        except OverflowError:
            value = math.inf
        return sign * value, p
    q = p
    while q < n and s[q] in _DIGITS:
        q += 1
    ndigits = q - p
    if q < n and s[q] == ".":
        q += 1
        f0 = q
        while q < n and s[q] in _DIGITS:
            q += 1
        ndigits += q - f0
    if ndigits == 0:
        return None, start
    mantissa = s[p:q]
    if q < n and s[q] in "eE":
        e = q + 1
        if e < n and s[e] in "+-":
            e += 1
        e0 = e
        while e < n and s[e] in _DIGITS:
            e += 1
        if e > e0:
            mantissa = s[p:e]
            q = e
    if mantissa.endswith("."):
        mantissa += "0"
    if mantissa.startswith("."):
        mantissa = "0" + mantissa
    return sign * float(mantissa), q


def as_numeric(x: Any) -> float:
    """R ``as.numeric()`` of one value (strings via ``R_strtod()``); NaN is ``NA``."""
    if x is None or x is pd.NA:
        return math.nan
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, int | float):
        return float(x)
    s = str(x)
    if _is_blank(s):
        return math.nan
    value, end = _r_strtod(s)
    if value is None or not _is_blank(s[end:]):
        return math.nan
    return value


def _strtoi(s: str) -> int | None:
    """R ``Strtoi(s, 10)`` (``strtol`` with the whole string consumed)."""
    i, n = 0, len(s)
    while i < n and s[i] in _C_SPACE:
        i += 1
    j = i
    if j < n and s[j] in "+-":
        j += 1
    if j == n or not all(c in _DIGITS for c in s[j:]):
        return None
    try:
        v = int(s[i:])
    except ValueError:  # pragma: no cover - guarded above
        return None
    if v > _INT_MAX or v <= -_INT_MAX - 1:
        return None
    return v


def _strtoc(s: str, na: bool = False) -> tuple[complex | None, int]:
    """R's ``strtoc()`` (utils ``io.c``): ``(value, end)``; ``None`` is ``NA``.

    A failed read returns ``end = 0`` (R backs out to the start).
    """
    x, end = _r_strtod(s, 0, na)
    if _is_blank(s[end:]):
        return (None if x is None else complex(x, 0.0)), end
    if s[end] == "i":
        if end == 0:
            return None, 0
        return (None if x is None else complex(0.0, x)), end + 1
    y, end2 = _r_strtod(s, end, na)
    if end2 < len(s) and s[end2] == "i":
        if x is None or y is None:
            return None, end2 + 1
        return complex(x, y), end2 + 1
    return None, 0


_LOGICAL = {"F": False, "FALSE": False, "T": True, "TRUE": True}


class _TypeInfo:
    """``Typecvt_Info``: the column types not yet ruled out."""

    __slots__ = ("cplx", "int", "lgl", "real")

    def __init__(self) -> None:
        self.lgl = self.int = self.real = self.cplx = True

    def ruleout(self, s: str) -> None:
        """``ruleout_types()``: rule out types by one field (reads ``"NA"`` as NA)."""
        if self.lgl:
            if s in _LOGICAL:
                self.int = self.real = self.cplx = False
                return
            self.lgl = False
        if self.int and _strtoi(s) is None:
            self.int = False
        if self.real and not _is_blank(s[_r_strtod(s, 0, True)[1] :]):
            self.real = False
        if self.cplx and not _is_blank(s[_strtoc(s, True)[1] :]):
            self.cplx = False


def type_convert(values: Sequence[Any]) -> pd.Series:
    """``utils::type.convert(x, as.is = TRUE)`` of a character vector.

    Follows R's ``typeconvert()``: the first non-missing field rules out
    column types, then logical (``T``, ``F``, ``TRUE``, ``FALSE``),
    integer, double and complex are tried in turn over all fields (a field
    that fails one type rules out others too); otherwise the values stay
    character. ``"NA"`` is missing, and blank fields are missing in
    non-character results.
    """
    vals = [None if (v is None or v is pd.NA or v == "NA") else str(v) for v in values]

    def missing(s: str | None) -> bool:
        return s is None or _is_blank(s)

    info = _TypeInfo()
    first = next((s for s in vals if not missing(s)), None)
    if first is not None:
        info.ruleout(first)

    if info.lgl:
        lgl: list[Any] = []
        for s in vals:
            if missing(s):
                lgl.append(pd.NA)
            elif s in _LOGICAL:
                lgl.append(_LOGICAL[s])  # type: ignore[index]
            else:
                info.lgl = False
                info.ruleout(s)  # type: ignore[arg-type]
                break
        if info.lgl:
            return pd.Series(lgl, dtype="boolean")
    if info.int:
        ints: list[Any] = []
        for s in vals:
            v = None if missing(s) else _strtoi(s)  # type: ignore[arg-type]
            if v is None and not missing(s):
                info.int = False
                info.ruleout(s)  # type: ignore[arg-type]
                break
            ints.append(pd.NA if v is None else v)
        if info.int:
            return pd.Series(ints, dtype="Int64")
    if info.real:
        dbl: list[float] = []
        for s in vals:
            if missing(s):
                dbl.append(math.nan)
                continue
            v, end = _r_strtod(s)  # type: ignore[arg-type]
            if not _is_blank(s[end:]):  # type: ignore[index]
                info.real = False
                info.ruleout(s)  # type: ignore[arg-type]
                break
            dbl.append(math.nan if v is None else v)
        if info.real:
            return pd.Series(dbl, dtype="float64")
    if info.cplx:
        cx: list[complex] = []
        for s in vals:
            if missing(s):
                cx.append(complex(math.nan, math.nan))
                continue
            z, end = _strtoc(s)  # type: ignore[arg-type]
            if not _is_blank(s[end:]):  # type: ignore[index]
                info.cplx = False
                break
            cx.append(complex(math.nan, math.nan) if z is None else z)
        if info.cplx:
            return pd.Series(cx, dtype="complex128")
    return pd.Series(vals, dtype="string")


# ---------------------------------------------------------------------------
# A tiny model of the R values jsonlite produces
# ---------------------------------------------------------------------------

_RANK = {"logical": 0, "integer": 1, "double": 2, "character": 3}
_EMPTY = {
    "logical": "logical(0)",
    "integer": "integer(0)",
    "double": "numeric(0)",
    "character": "character(0)",
}


class _Vec:
    """An atomic R vector (``values`` hold ``None`` for ``NA``).

    ``posix`` marks a ``POSIXct`` vector (double seconds since the epoch):
    ``""`` for the local time zone, ``"UTC"`` for UTC; ``None`` otherwise.
    """

    __slots__ = ("dim", "posix", "type", "values")

    def __init__(
        self,
        type_: str,
        values: list[Any],
        dim: tuple[int, ...] | None = None,
        posix: str | None = None,
    ):
        self.type = type_
        self.values = values
        self.dim = dim
        self.posix = posix


class _List:
    """An R list; ``names`` is ``None`` for an unnamed list."""

    __slots__ = ("items", "names")

    def __init__(self, items: list[Any], names: list[str] | None = None):
        self.items = items
        self.names = names


class _Frame:
    """An R data frame whose columns are ``_Vec``, ``_List`` or ``_Frame``."""

    __slots__ = ("columns", "names", "nrow")

    def __init__(self, names: list[str], columns: list[Any], nrow: int):
        self.names = names
        self.columns = columns
        self.nrow = nrow


def _r_length(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, _Vec):
        return len(x.values)
    if isinstance(x, _List):
        return len(x.items)
    return len(x.names)


def _double_str(v: float) -> str:
    """``as.character()`` of a non-missing double (``NaN`` is not ``NA``)."""
    if math.isnan(v):
        return "NaN"
    return as_character(v) or "NA"


def _coerce(v: Any, src: str, dest: str) -> Any:
    if v is None or src == dest:
        return v
    if dest == "character":
        if src == "logical":
            return "TRUE" if v else "FALSE"
        if src == "integer":
            return str(v)
        return _double_str(v)
    if dest == "double":
        return float(v)
    if dest == "integer":
        return int(v)
    return v  # pragma: no cover


def _combine(vecs: Sequence[_Vec]) -> _Vec:
    """``unlist()`` / ``rbind()`` type promotion of atomic vectors."""
    type_ = max((v.type for v in vecs), key=_RANK.__getitem__, default="logical")
    values = [_coerce(x, v.type, type_) for v in vecs for x in v.values]
    return _Vec(type_, values)


# ---------------------------------------------------------------------------
# JSON parsing (yajl as configured by jsonlite)
# ---------------------------------------------------------------------------


class _JSONError(ValueError):
    pass


class _Obj(list):  # type: ignore[type-arg]
    """A JSON object as its ordered (key, value) pairs (duplicates kept)."""


def _preprocess(txt: str) -> str:
    """Drop ``//`` and ``/* */`` comments and map ``\\v``/``\\f`` to spaces (yajl)."""
    if "/" not in txt and "\x0b" not in txt and "\x0c" not in txt:
        return txt
    out: list[str] = []
    i, n = 0, len(txt)
    in_str = False
    while i < n:
        c = txt[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(txt[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and txt.startswith("//", i):
            end = txt.find("\n", i)
            out.append(" ")
            i = n if end == -1 else end
            continue
        elif c == "/" and txt.startswith("/*", i):
            end = txt.find("*/", i + 2)
            out.append(" ")
            i = n if end == -1 else end + 2
            continue
        elif c in "\x0b\x0c":
            out.append(" ")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _parse_int(s: str) -> int | float:
    try:
        v = int(s)
    except ValueError:
        return float(s)
    return v if -_INT_MAX <= v <= _INT_MAX else float(s)


def _reject_constant(s: str) -> Any:
    raise _JSONError(f"invalid char in json text: {s}")


def _clean_str(s: str) -> str:
    """R strings cannot hold NUL (truncated) or lone surrogates (``?``)."""
    k = s.find("\x00")
    if k != -1:
        s = s[:k]
    if any("\ud800" <= c <= "\udfff" for c in s):
        s = "".join("?" if "\ud800" <= c <= "\udfff" else c for c in s)
    return s


def _to_r(x: Any) -> Any:
    """Convert a parsed JSON value to the R value ``parseJSON()`` gives."""
    if x is None:
        return None
    if isinstance(x, _Obj):
        return _List([_to_r(v) for _, v in x], [_clean_str(k) for k, _ in x])
    if isinstance(x, list):
        return _List([_to_r(v) for v in x], None)
    if isinstance(x, bool):
        return _Vec("logical", [x])
    if isinstance(x, int):
        return _Vec("integer", [x])
    if isinstance(x, float):
        return _Vec("double", [x])
    return _Vec("character", [_clean_str(x)])


def _parse_json(txt: str) -> Any:
    """``jsonlite::parseJSON()`` of a string: the R value, or ``_JSONError``."""
    if txt.startswith("\ufeff"):  # jsonlite's R_parse() skips a leading byte-order mark
        import warnings

        warnings.warn("JSON string contains (illegal) UTF8 byte-order-mark!", stacklevel=3)
        txt = txt[1:]
    try:
        raw = json.loads(
            _preprocess(txt),
            object_pairs_hook=_Obj,
            parse_int=_parse_int,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise _JSONError(str(exc)) from exc
    return _to_r(raw)


# ---------------------------------------------------------------------------
# jsonlite::simplify()
# ---------------------------------------------------------------------------


def _is_named_list(x: Any) -> bool:
    return isinstance(x, _List) and x.names is not None


def _is_recordlist(x: _List) -> bool:
    if x.names is not None or not x.items:
        return False
    seen = False
    for el in x.items:
        if _is_named_list(el):
            seen = True
        elif el is not None:
            return False
    return seen


def _is_scalarlist(x: Any) -> bool:
    if not isinstance(x, _List):
        return False
    return all(el is None or (isinstance(el, _Vec) and len(el.values) <= 1) for el in x.items)


def _null_to_na(items: list[Any]) -> list[_Vec]:
    out = [_Vec("logical", [None]) if el is None else el for el in items]
    for el in out:
        if el.type == "character" and el.values[0] not in ("NA", "NaN", "Inf", "-Inf"):
            return out
    fixed: list[_Vec] = []
    for el in out:
        if el.type == "character":
            s = el.values[0]
            if s == "NA":
                el = _Vec("logical", [None])
            else:
                el = _Vec("double", [{"NaN": math.nan, "Inf": math.inf, "-Inf": -math.inf}[s]])
        fixed.append(el)
    return fixed


def _list_to_vec(x: _List) -> _Vec:
    return _combine(_null_to_na(x.items))


def _is_atomic(x: Any) -> bool:
    return isinstance(x, _Vec)


def _is_matrixlist(x: _List) -> bool:
    if not x.items or x.names is not None:
        return False
    if not all(isinstance(el, _Vec) for el in x.items):
        return False
    return len({len(el.values) for el in x.items}) == 1


def _rbind(vecs: list[_Vec]) -> _Vec:
    comb = _combine(vecs)
    nr, nc = len(vecs), len(vecs[0].values)
    values = [comb.values[i * nc + j] for j in range(nc) for i in range(nr)]
    return _Vec(comb.type, values, (nr, nc))


def _is_arraylist(x: _List) -> bool:
    if not x.items or x.names is not None:
        return False
    if not all(isinstance(el, _Vec) and el.dim is not None for el in x.items):
        return False
    return len({el.dim for el in x.items}) == 1


def _simplify(x: Any, simplify_matrix: bool = True, sub_matrix: bool = True) -> Any:
    """``jsonlite:::simplify()`` with the ``fromJSON()`` defaults."""
    if not isinstance(x, _List) or not x.items:
        return x
    if _is_recordlist(x):
        frame = _simplify_data_frame(x.items, sub_matrix)
        if _is_datelist(frame):
            return _parse_date(frame.columns[0])
        return frame
    if x.names is None and _is_scalarlist(x):
        return _list_to_vec(x)
    out = _List([_simplify(el, sub_matrix, sub_matrix) for el in x.items], x.names)
    if _is_scalarlist(out) and all(
        isinstance(el, _Vec) and el.posix is not None for el in out.items
    ):
        # POSIXct scalars (Mongo dates) combine into one POSIXct vector (tzone dropped)
        return _Vec("double", [el.values[0] if el.values else None for el in out.items], None, "")
    if simplify_matrix and _is_matrixlist(out) and all(_is_scalarlist(el) for el in x.items):
        return _rbind(out.items)
    if simplify_matrix and _is_arraylist(out):
        first = out.items[0]
        rows = _rbind([_Vec(el.type, el.values) for el in out.items])
        return _Vec(rows.type, rows.values, (len(out.items), *first.dim))
    if out.names is None:
        empty = [isinstance(el, _List) and el.names is None and not el.items for el in out.items]
        if any(empty) and not all(empty):
            rest = [el for el, e in zip(out.items, empty, strict=True) if not e]
            if all(isinstance(el, _Frame) for el in rest):
                out.items = [
                    _Frame([], [], 0) if e else el for el, e in zip(out.items, empty, strict=True)
                ]
                return out
            if all(isinstance(el, _Vec) and el.dim is None and el.posix is None for el in rest):
                mode = rest[0].type
                out.items = [
                    _Vec(mode, []) if e else el for el, e in zip(out.items, empty, strict=True)
                ]
                return out
    if _is_datelist(out):
        return _parse_date(out.items[0])
    return out


# ---------------------------------------------------------------------------
# Mongo-style dates ({"$date": ...}): jsonlite's parse_date() and POSIXct
# ---------------------------------------------------------------------------


def _is_datelist(x: Any) -> bool:
    """``jsonlite:::is.datelist()``: a list or data frame holding only ``$date``."""
    if isinstance(x, _List):
        names, items = x.names, x.items
    elif isinstance(x, _Frame):
        names, items = x.names, x.columns
    else:
        return False
    if names != ["$date"]:
        return False
    el = items[0]
    return (
        isinstance(el, _Vec) and el.posix is None and el.type in ("integer", "double", "character")
    )


def _parse_date(x: _Vec) -> _Vec:
    """``jsonlite:::parse_date()``: milliseconds or ISO strings to ``POSIXct``."""
    if x.type != "character":
        return _Vec("double", [None if v is None else v / 1000 for v in x.values], x.dim, "")
    utc = all(v is not None and v.endswith("Z") for v in x.values)
    tz = "UTC" if utc else ""
    return _Vec("double", [_strptime_iso(v, tz) for v in x.values], x.dim, tz)


def _get_number(s: str, p: int, lo: int, hi: int, n: int) -> tuple[int | None, int]:
    """``get_number()`` of R's ``strptime()``: up to *n* digits within [lo, hi]."""
    while p < len(s) and s[p] == " ":
        p += 1
    if p >= len(s) or s[p] not in _DIGITS:
        return None, p
    val = 0
    while True:
        val = val * 10 + int(s[p])
        p += 1
        n -= 1
        if not (n > 0 and p < len(s) and s[p] in _DIGITS):
            break
    return (val if lo <= val <= hi else None), p


def _month_days(year: int, month: int) -> int:
    if month == 2:
        return 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _strptime_iso(v: str | None, tz: str) -> float | None:
    """``as.POSIXct(strptime(v, "%Y-%m-%dT%H:%M:%OS", tz))``: seconds, or ``None``."""
    if v is None:
        return None
    fields: list[int] = []
    p = 0
    for sep, (lo, hi, n) in zip(
        ("", "-", "-", "T", ":"),
        ((0, 9999, 4), (1, 12, 2), (1, 31, 2), (0, 24, 2), (0, 59, 2)),
        strict=True,
    ):
        if sep:
            if not v.startswith(sep, p):
                return None
            p += len(sep)
        val, p = _get_number(v, p, lo, hi, n)
        if val is None:
            return None
        fields.append(val)
    if not v.startswith(":", p):
        return None
    # %OS: seconds with a fraction; a value outside [0, 61] leaves them at 0
    sval, _ = _r_strtod(v, p + 1)
    secs = sval if sval is not None and 0.0 <= sval <= 61.0 else 0.0
    year, mon, mday, hour, minute = fields
    if mday > _month_days(year, mon) or int(secs) > 60:
        return None
    if hour == 24 and (minute or int(secs)):
        return None
    days = _days_from_civil(year, mon, mday)
    local = days * 86400.0 + hour * 3600 + minute * 60 + secs
    if tz == "UTC":
        return local
    return local - _utc_offset(local)


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 of a proleptic Gregorian date."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z: int) -> tuple[int, int, int]:
    """Proleptic Gregorian ``(year, month, day)`` of days since 1970-01-01."""
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def _utc_offset(secs: float) -> float:
    """The local time zone's UTC offset (seconds) at *secs*; 0 when unknown."""
    import time

    try:
        return float(time.localtime(math.floor(secs)).tm_gmtoff)
    except (OverflowError, OSError, ValueError):
        return 0.0


def _posix_str(v: float | None, tz: str) -> str | None:
    """``as.character()`` of one ``POSIXct`` value (R >= 4.3 rules)."""
    if v is None:
        return None
    if not math.isfinite(v):
        return "NaN" if math.isnan(v) else ("Inf" if v > 0 else "-Inf")
    from pytacheck._r import r_round

    if tz != "UTC":
        v += _utc_offset(v)
    whole = math.floor(v)
    days, rem = divmod(int(whole), 86400)
    y, m, d = _civil_from_days(days)
    out = f"{y}-{m:02d}-{d:02d}"
    hour, rem = divmod(rem, 3600)
    minute, sec = divmod(rem, 60)
    secs = sec + (v - whole)
    if hour + minute + secs != 0:
        s = float(r_round(secs, 6))
        out += f" {hour:02d}:{minute:02d}:{'0' if s < 10 else ''}{as_character(s)}"
    return out


def _simplify_data_frame(records: list[Any], sub_matrix: bool) -> _Frame:
    """``jsonlite:::simplifyDataFrame()`` of a record list."""
    n = len(records)
    if not any(_r_length(r) for r in records):
        return _Frame([], [], n)
    columns: list[str] = []
    seen: set[str] = set()
    for r in records:
        if r is None:
            continue
        for k in r.names:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    cols: list[Any] = []
    for name in columns:
        cells: list[Any] = []
        for r in records:
            if r is None:
                cells.append(None)
                continue
            try:
                cells.append(r.items[r.names.index(name)])
            except ValueError:
                cells.append(None)
        cols.append(_simplify(_List(cells, None), False, sub_matrix))
    lengths = {c.nrow if isinstance(c, _Frame) else _r_length(c) for c in cols}
    if len(lengths) > 1:
        raise _JSONError("Elements not of equal length")
    n = lengths.pop()
    if "_row" in columns:
        k = columns.index("_row")
        rn = cols[k]
        del columns[k], cols[k]
        n = _row_names_nrow(rn, n)
    return _Frame(columns, cols, n)


def _identity_key(x: Any) -> Any:
    """A hashable key with ``identical()`` semantics for duplicated()."""
    if x is None:
        return ("NULL",)
    if isinstance(x, _Vec):
        vals = tuple("NaN" if isinstance(v, float) and math.isnan(v) else v for v in x.values)
        return ("vec", x.type, vals, x.dim)
    if isinstance(x, _List):
        names = None if x.names is None else tuple(x.names)
        return ("list", names, tuple(_identity_key(el) for el in x.items))
    return ("df", tuple(x.names), tuple(_identity_key(c) for c in x.columns), x.nrow)


def _is_na_elt(x: Any) -> bool:
    """``is.na()`` of one list element: a length-one atomic ``NA``/``NaN``."""
    if not isinstance(x, _Vec) or len(x.values) != 1:
        return False
    v = x.values[0]
    return v is None or (isinstance(v, float) and math.isnan(v))


def _has_na(x: Any) -> bool:
    """Does ``is.na()`` of a (list or data frame) column have any ``TRUE``?"""
    if isinstance(x, _Vec):
        return any(v is None or (isinstance(v, float) and math.isnan(v)) for v in x.values)
    if isinstance(x, _List):
        return any(_is_na_elt(el) for el in x.items)
    if isinstance(x, _Frame):
        return any(_has_na(c) for c in x.columns)
    return False


def _na_leaves(col: Any, nrow: int) -> list[list[bool]]:
    """The columns ``col`` contributes to ``is.na(<data frame>)`` (a logical matrix).

    ``is.na.data.frame()`` ``cbind()``s ``is.na()`` of every column, so a
    nested data frame contributes one column per (nested) leaf column.
    """
    if isinstance(col, _Frame):
        return [m for c in col.columns for m in _na_leaves(c, col.nrow)]
    if isinstance(col, _Vec):
        return [[v is None or (isinstance(v, float) and math.isnan(v)) for v in col.values]]
    if isinstance(col, _List):
        return [[_is_na_elt(el) for el in col.items]]
    return [[False] * nrow]


def _assign_na_labels(col: Any, mask: list[bool], labels: list[str]) -> Any:
    """``x[[v]][thisvar] <- labels`` inside ``[<-.data.frame``'s logical-matrix branch."""
    if isinstance(col, _Frame):
        # a data frame indexed by one logical vector selects its *columns*:
        # seq_along(x)[thisvar] is NA for a TRUE beyond the last column, and
        # the one selected column becomes the label, recycled to every row
        if len(col.columns) != 1:
            raise _JSONError("unsupported matrix index in replacement")
        if not mask[0] or any(mask[1:]):
            raise _JSONError("attempt to select less than one element in integerOneIndex")
        return _Frame(col.names, [_Vec("character", labels * col.nrow)], col.nrow)
    it = iter(labels)
    if isinstance(col, _List):
        return _List(
            [
                _Vec("character", [next(it)]) if m else el
                for el, m in zip(col.items, mask, strict=True)
            ],
            col.names,
        )
    if isinstance(col, _Vec):
        return _Vec(
            "character",
            [
                next(it) if m else _coerce(v, col.type, "character")
                for v, m in zip(col.values, mask, strict=True)
            ],
        )
    return col


def _replace_frame_na(rn: _Frame) -> list[Any]:
    """``rn[is.na(rn)] <- paste0("NA_", ...)`` on a ``_row`` data frame.

    ``[<-.data.frame`` takes a logical matrix index only when it has the
    frame's dimensions, i.e. when every nested data frame column is one
    (leaf) column wide; column ``v`` then gets the labels of the matrix's
    column ``v`` (U9: a nested data frame is indexed by columns, see
    :func:`_assign_na_labels`).
    """
    if not _has_na(rn):
        return list(rn.columns)
    leaves = [m for c in rn.columns for m in _na_leaves(c, rn.nrow)]
    if len(leaves) != len(rn.columns):
        raise _JSONError("unsupported matrix index in replacement")
    k = 0
    out: list[Any] = []
    for col, mask in zip(rn.columns, leaves, strict=True):
        nv = sum(mask)
        if nv:
            col = _assign_na_labels(col, mask, [f"NA_{k + i + 1}" for i in range(nv)])
        k += nv
        out.append(col)
    return out


def _elements(x: Any) -> list[Any]:
    """The elements ``mapply()`` iterates over (a data frame gives its columns)."""
    if isinstance(x, _Vec):
        return [
            ("vec", x.type, "NaN" if isinstance(v, float) and math.isnan(v) else v)
            for v in x.values
        ]
    if isinstance(x, _List):
        return [_identity_key(el) for el in x.items]
    if isinstance(x, _Frame):
        return [_identity_key(c) for c in x.columns]
    return []


def _any_duplicated(x: Any) -> bool:
    """``any(duplicated(x))`` for a vector, list or data frame (R 4.5 rules)."""
    if isinstance(x, _Frame):
        if not x.columns:
            return x.nrow > 1  # duplicated(logical(nrow(x)))
        if len(x.columns) == 1:
            return _any_duplicated(x.columns[0])
        if any(isinstance(c, _Frame) for c in x.columns):
            # split into one-row data frames whose row names all differ
            return False
        cols = [_elements(c) for c in x.columns]
        if any(len(c) == 0 for c in cols):
            return False  # Map() over a zero-length input gives list()
        length = max(len(c) for c in cols)
        keys = [tuple(c[i % len(c)] for c in cols) for i in range(length)]
    else:
        keys = _elements(x)
    return len(set(keys)) < len(keys)


def _row_names_nrow(rn: Any, n: int) -> int:
    """Rows of a data frame after jsonlite sets its ``_row`` names.

    ``row.names<-`` on the freshly classed list accepts a value of any
    length, so a ``_row`` column that is itself a data frame (JSON objects)
    turns the frame into one with ``ncol(_row)`` rows. Invalid row names
    raise, which ``json_expand()`` reports as a parsing error.
    """
    if isinstance(rn, _Vec):
        return n  # NAs become "NA_<k>" and duplicates fall back to 1:n
    if isinstance(rn, _List):
        items = list(rn.items)
        k = 0
        for i, el in enumerate(items):
            if _is_na_elt(el):
                k += 1
                items[i] = _Vec("character", [f"NA_{k}"])
        if _any_duplicated(_List(items, None)):
            return n
        values = [_elt_to_str(el) for el in items]
    elif isinstance(rn, _Frame):
        new_cols = _replace_frame_na(rn)
        if _any_duplicated(_Frame(rn.names, new_cols, rn.nrow)):
            return n
        values = [_elt_to_str(col) for col in new_cols]
    else:
        return n
    if len(set(values)) < len(values):
        raise _JSONError("duplicate 'row.names' are not allowed")
    if any(v is None for v in values):
        raise _JSONError("missing values in 'row.names' are not allowed")
    return len(values)


# ---------------------------------------------------------------------------
# as.character() / paste() / deparse()
# ---------------------------------------------------------------------------

_RESERVED = frozenset(
    {
        "NULL",
        "NA",
        "TRUE",
        "FALSE",
        "Inf",
        "NaN",
        "NA_integer_",
        "NA_real_",
        "NA_character_",
        "NA_complex_",
        "function",
        "while",
        "repeat",
        "for",
        "if",
        "in",
        "else",
        "next",
        "break",
    }
)
_ESCAPES = {
    "\x07": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\x0b": "\\v",
    "\\": "\\\\",
    '"': '\\"',
}


def _encode_string(s: str) -> str:
    out = ['"']
    for c in s:
        if c in _ESCAPES:
            out.append(_ESCAPES[c])
        elif c < " " or c == "\x7f":
            out.append(f"\\{ord(c):03o}")
        elif "\x80" <= c <= "\x9f":
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _is_valid_name(name: str) -> bool:
    if not name:
        return False
    first = name[0]
    if first != "." and not first.isalpha():
        return False
    if first == "." and len(name) > 1 and name[1] in _DIGITS:
        return False
    if not all(c.isalnum() or c in "._" for c in name[1:]):
        return False
    return name == "..." or name not in _RESERVED


class _Deparser:
    """``deparse1line()`` with ``SIMPLEDEPARSE`` options (cutoff 500 bytes)."""

    cutoff = 500

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.len = 0
        self.indent = 0
        self.startline = True

    def put(self, s: str) -> None:
        if self.startline:
            self.startline = False
            for i in range(1, self.indent + 1):
                self.put("    " if i <= 4 else "  ")
        self.buf.append(s)
        self.len += len(s.encode("utf-8"))

    def writeline(self) -> None:
        self.lines.append("".join(self.buf))
        self.buf = []
        self.len = 0
        self.startline = True

    def result(self) -> str:
        if self.buf or not self.lines:
            self.lines.append("".join(self.buf))
        return "\n".join(self.lines)

    def element(self, v: Any, type_: str) -> str:
        if v is None:
            return "NA"
        if type_ == "logical":
            return "TRUE" if v else "FALSE"
        if type_ == "integer":
            return str(v)
        if type_ == "double":
            return _double_str(v)
        return _encode_string(v)

    def vector(self, x: _Vec) -> None:
        vals, n = x.values, len(x.values)
        if n == 0:
            self.put(_EMPTY[x.type])
            return
        if x.type == "integer" and n > 1 and vals[0] is not None and vals[1] is not None:
            step = vals[1] - vals[0]
            if abs(step) == 1 and all(
                vals[i] is not None and vals[i] - vals[i - 1] == step for i in range(2, n)
            ):
                self.put(f"{vals[0]}:{vals[-1]}")
                return
        if n > 1:
            self.put("c(")
        for i, v in enumerate(vals):
            self.put(self.element(v, x.type))
            if i < n - 1:
                self.put(", ")
            if n > 1 and self.len > self.cutoff:
                self.writeline()
        if n > 1:
            self.put(")")

    def items(self, items: list[Any], names: list[str] | None) -> None:
        lbreak = False
        for i, el in enumerate(items):
            if i > 0:
                self.put(", ")
            if self.len > self.cutoff:
                if not lbreak:
                    lbreak = True
                    self.indent += 1
                self.writeline()
            if names is not None and names[i]:
                nm = names[i]
                self.put(nm if _is_valid_name(nm) else f"`{nm}`")
                self.put(" = ")
            self.deparse(el)
        if lbreak:
            self.indent -= 1

    def deparse(self, x: Any) -> None:
        if x is None:
            self.put("NULL")
        elif isinstance(x, _Vec):
            self.vector(x)
        elif isinstance(x, _List):
            self.put("list(")
            self.items(x.items, x.names if x.names else None)
            self.put(")")
        else:  # a data frame deparses as its list of columns
            self.put("list(")
            self.items(x.columns, x.names if x.names else None)
            self.put(")")


def _deparse(x: Any) -> str:
    d = _Deparser()
    d.deparse(x)
    return d.result()


def _elt_to_str(el: Any) -> str | None:
    """``as.character()`` of one list element (``coerceVectorList``)."""
    if isinstance(el, _Vec) and el.type == "character" and len(el.values) == 1:
        return el.values[0]  # type: ignore[no-any-return]
    return _deparse(el)


def _as_character(x: Any) -> list[str | None]:
    """R ``as.character(x)``."""
    if x is None:
        return []
    if isinstance(x, _Vec):
        if x.posix is not None:
            return [_posix_str(v, x.posix) for v in x.values]
        return [_coerce(v, x.type, "character") for v in x.values]
    if isinstance(x, _List):
        return [_elt_to_str(el) for el in x.items]
    return [_elt_to_str(col) for col in x.columns]


def _paste_collapse(x: Any) -> str:
    """``paste(x, collapse = ";")``."""
    return ";".join("NA" if v is None else v for v in _as_character(x))


# ---------------------------------------------------------------------------
# json_expand()
# ---------------------------------------------------------------------------

_ERROR = "error"
_TEMP = ".temp_id."


class _Piece:
    """One element's contribution to ``bind_rows()``: named columns of rows."""

    __slots__ = ("columns", "nrow")

    def __init__(self, columns: dict[str, list[Any]], nrow: int):
        self.columns = columns
        self.nrow = nrow


def _fit_rows(vals: list[Any], n: int) -> list[Any] | None:
    """``j[] <- value`` for one column: recycle, truncate or fill with NA."""
    k = len(vals)
    if k == n:
        return vals
    if k == 0:
        return [None] * n
    if k > n:
        return vals[:n]
    if n % k:
        return None
    return vals * (n // k)


def _error_piece(i: int, msg: str) -> _Piece:
    return _Piece({_TEMP: [i], _ERROR: [msg]}, 1)


def _expand_one(value: Any, i: int) -> _Piece:
    """The ``tryCatch()`` body of ``json_expand()`` for element *i* (1-based)."""
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return _error_piece(i, "parsing error")
    txt = value if isinstance(value, str) else (as_character(value) or "NA")
    txt = gsub('"null"', "null", txt)
    txt = gsub(".*```json\\s*\n", "", txt)
    txt = gsub("\n\\s*```.*", "", txt)
    try:
        j = _simplify(_parse_json(txt))
    except _JSONError:
        return _error_piece(i, "parsing error")

    if _r_length(j) == 0:
        j = _Frame([_ERROR], [_Vec("character", [None])], 1)
    if _is_atomic(j):
        j = _Frame([_ERROR], [_Vec("character", ["not a list"])], 1)

    if isinstance(j, _Frame):
        if j.nrow == 0:  # `j$.temp_id. <- i`: "replacement has 1 row, data has 0"
            return _error_piece(i, "parsing error")
        cols: dict[str, list[Any]] = {}
        for name, col in zip(j.names, j.columns, strict=True):
            vals = _as_character(col)
            vals = _fit_rows(vals, j.nrow)
            if vals is None:  # "replacement element has k rows, need n"
                return _error_piece(i, "parsing error")
            cols.setdefault(name, vals)
        cols[_TEMP] = [i] * j.nrow
        return _Piece(cols, j.nrow)

    # a (named or unnamed) list: one row of pasted values
    names = list(j.names) if j.names is not None else [""] * len(j.items)
    values: list[Any] = [_paste_collapse(el) for el in j.items]
    if _TEMP in names:
        values[names.index(_TEMP)] = i
    else:
        names.append(_TEMP)
        values.append(i)
    if any(not nm for nm in names):
        raise ValueError(f"Argument {i} must be a data frame or a named atomic vector.")
    seen: set[str] = set()
    for nm in names:
        if nm in seen:
            raise ValueError(
                f"Column name `{nm}` must not be duplicated.\nUse `.name_repair` to specify repair."
            )
        seen.add(nm)
    return _Piece({nm: [v] for nm, v in zip(names, values, strict=True)}, 1)


def _add_suffixes(x: list[str], y: list[str], suffix: str) -> list[str]:
    """``dplyr:::add_suffixes()``."""
    if suffix == "":
        return list(x)
    both = [*y, *x]
    while True:
        seen: set[str] = set()
        dup = []
        for k, nm in enumerate(both):
            if nm in seen:
                dup.append(k)
            seen.add(nm)
        if not dup:
            break
        for k in dup:
            both[k] = both[k] + suffix
    return both[len(y) :]


def json_expand(
    table: Any,
    col: str | int = "answer",
    suffix: Sequence[str] = ("", ".json"),
) -> pd.DataFrame:
    """Expand a column of JSON text into columns (port of ``json_expand()``).

    Each element of the column is parsed like ``jsonlite::fromJSON()``: an
    object becomes one row, an array of objects several rows (duplicating
    the table row), and values that are arrays or nested objects are pasted
    together with ``";"`` (or deparsed, as R does). Unparsable text gives an
    ``error`` column of ``"parsing error"``; JSON that is not an object
    (a number, a string, an array of scalars) gives ``"not a list"``. The
    ``error`` column is dropped when there are no errors. Column types are
    then guessed like ``utils::type.convert(as.is = TRUE)``.

    Parameters
    ----------
    table:
        A DataFrame, or a string / sequence of strings (treated as a table
        with a single ``json`` column).
    col:
        The column to expand: a name, or a 1-based column index as in R.
        When the name is absent the first column is used.
    suffix:
        Suffixes for expanded columns whose names clash with the table's
        (``dplyr::left_join()`` semantics).

    Differences from R: text that is not JSON but looks like a URL or an
    existing file path is *not* downloaded/read (``fromJSON()`` would do
    so); it gives ``"parsing error"``.
    """
    if isinstance(table, str):
        table = [table]
    if not isinstance(table, pd.DataFrame):
        table = pd.DataFrame({"json": list(table)})
    suffixes = [suffix] if isinstance(suffix, str) else list(suffix)
    if len(suffixes) != 2 or any(not isinstance(s, str) for s in suffixes):
        raise ValueError("`suffix` must be a character vector of length 2.")

    table = table.copy(deep=False)
    if isinstance(col, str) and col not in table.columns:
        col = 1
    if isinstance(col, int):
        if not 1 <= col <= table.shape[1]:
            raise IndexError("subscript out of bounds")
        to_expand = table.iloc[:, col - 1].tolist()
    else:
        to_expand = table[col].tolist()

    n = len(table)
    table[_TEMP] = pd.Series(range(1, n + 1), index=table.index, dtype="Int64")
    if n == 0:
        raise ValueError(
            "Join columns in `y` must be present in the data.\n✖ Problem with `.temp_id.`."
        )

    pieces = [_expand_one(v, i) for i, v in enumerate(to_expand, start=1)]

    # dplyr::bind_rows()
    names: list[str] = []
    seen: set[str] = set()
    for pc in pieces:
        for nm in pc.columns:
            if nm not in seen:
                seen.add(nm)
                names.append(nm)
    columns: dict[str, list[Any]] = {nm: [] for nm in names}
    for pc in pieces:
        for nm in names:
            vals = pc.columns.get(nm)
            columns[nm].extend(vals if vals is not None else [None] * pc.nrow)

    # fix data types from making all character
    temp_ids = [int(v) for v in columns[_TEMP]]
    converted: dict[str, pd.Series] = {}
    for nm in names:
        if nm == _TEMP:
            converted[nm] = pd.Series(temp_ids, dtype="Int64")
        else:
            converted[nm] = type_convert(columns[nm])
    if _ERROR in converted and bool(converted[_ERROR].isna().all()):
        del converted[_ERROR]
        names.remove(_ERROR)

    # dplyr::left_join(table, expanded, by = ".temp_id.", suffix = suffix)
    x_names = [str(c) for c in table.columns]
    y_aux = [nm for nm in names if nm != _TEMP]
    x_out = _merge_names(x_names, [_TEMP, *y_aux], suffixes[0])
    y_out = dict(zip(names, _add_suffixes(names, x_names, suffixes[1]), strict=True))

    rows_for: dict[int, list[int]] = {}
    for k, tid in enumerate(temp_ids):
        rows_for.setdefault(tid, []).append(k)
    x_idx: list[int] = []
    y_idx: list[int | None] = []
    for r in range(n):
        matches = rows_for.get(r + 1)
        if matches:
            x_idx.extend([r] * len(matches))
            y_idx.extend(matches)
        else:
            x_idx.append(r)
            y_idx.append(None)

    out = table.iloc[x_idx].reset_index(drop=True)
    out.columns = x_out
    for nm in y_aux:
        s = converted[nm]
        if all(k is not None for k in y_idx):
            vals = s.iloc[[k for k in y_idx if k is not None]].reset_index(drop=True)
        else:
            vals = pd.Series([s.iloc[k] if k is not None else None for k in y_idx], dtype=s.dtype)
        out[y_out[nm]] = vals
    return out.drop(columns=_TEMP)


def _merge_names(x_names: list[str], others: list[str], suffix: str) -> list[str]:
    """Suffixed names for the non-key columns of x, aligned with *x_names*."""
    non_key = [nm for nm in x_names if nm != _TEMP]
    renamed = iter(_add_suffixes(non_key, others, suffix))
    return [nm if nm == _TEMP else next(renamed) for nm in x_names]
