"""R's serialization format (``serialize()``, ``saveRDS()``, ``readRDS()``).

metacheck's LLM cache is keyed by ``md5(serialize(payload, ascii = TRUE))``
and stores each entry as an ``.rds`` file. To share one cache between R and
Python, pytacheck writes the same bytes: this module implements version-3
serialization (ASCII and XDR binary) for the R objects the cache holds —
``NULL``, atomic vectors, lists, attributes (names, class, levels,
``row.names``), data frames, factors and ``POSIXct`` — and reads them back,
including the ALTREP compact sequences R uses for ``1:n``.

Values
------
:class:`RVec` is an atomic vector with an explicit R type; :class:`RList` a
generic vector. Python values map naturally when written: ``None`` ->
``NULL``, ``str`` -> character, ``bool`` -> logical, ``float`` and plain
``int`` -> double (an R literal ``4096`` is a double), :class:`RInt` ->
integer, ``dict`` -> named list, ``list`` -> list, ``pandas.DataFrame`` ->
``data.frame``, ``datetime`` -> ``POSIXct``.

The header of a serialization records the writing R version; keys written
by pytacheck carry :data:`R_VERSION` (``PYTACHECK_R_SERIALIZE_VERSION`` or
the ``pytacheck.r_serialize_version`` option override it, e.g. ``"4.4.1"``)
so they match caches written by that R release.
"""

from __future__ import annotations

import datetime as dt
import gzip
import math
import os
import struct
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "R_VERSION",
    "EllmerOutput",
    "RInt",
    "RList",
    "RVec",
    "read_rds",
    "serialize",
    "to_python",
    "unserialize",
    "write_rds",
]

#: R version recorded in serialization headers (the pinned reference R).
R_VERSION = (4, 5, 3)

# SEXP types
NILSXP, SYMSXP, LISTSXP, CLOSXP, ENVSXP = 0, 1, 2, 3, 4
LGLSXP, INTSXP, REALSXP, CPLXSXP, STRSXP, VECSXP, EXPRSXP, RAWSXP = 10, 13, 14, 15, 16, 19, 20, 24
CHARSXP = 9
S4SXP = 25
# pseudo-SEXP types used by serialize
REFSXP, NILVALUE_SXP, GLOBALENV_SXP, UNBOUNDVALUE_SXP = 255, 254, 253, 252
MISSINGARG_SXP, BASENAMESPACE_SXP, NAMESPACESXP, PACKAGESXP = 251, 250, 249, 248
PERSISTSXP, EMPTYENV_SXP, BASEENV_SXP, ATTRLANGSXP, ATTRLISTSXP, ALTREP_SXP = (
    247,
    242,
    241,
    240,
    239,
    238,
)

_IS_OBJECT, _HAS_ATTR, _HAS_TAG = 1 << 8, 1 << 9, 1 << 10
_UTF8_MASK, _ASCII_MASK = 1 << 3, 1 << 6
NA_INTEGER = -(2**31)
# R's NA_real_ is a NaN with payload 1954
_NA_REAL_BITS = 0x7FF00000000007A2
NA_REAL = struct.unpack(">d", struct.pack(">Q", _NA_REAL_BITS))[0]

_TYPE_CODES = {
    "lgl": LGLSXP,
    "int": INTSXP,
    "dbl": REALSXP,
    "chr": STRSXP,
    "cplx": CPLXSXP,
    "raw": RAWSXP,
}
_CODE_TYPES = {v: k for k, v in _TYPE_CODES.items()}


class RInt(int):
    """An ``int`` that R should store as an integer (``1L``) rather than a double."""


class EllmerOutput(str):
    """A string of S3 class ``"ellmer_output"`` (what ellmer's ``chat$chat()`` returns)."""


class RVec:
    """An R atomic vector: ``type`` is lgl/int/dbl/chr/cplx/raw; ``None`` is ``NA``.

    For doubles, ``None`` is ``NA_real_`` and ``float("nan")`` is ``NaN``.
    """

    __slots__ = ("attrs", "type", "values")

    def __init__(self, type: str, values: Iterable[Any], attrs: Mapping[str, Any] | None = None):
        if type not in _TYPE_CODES:
            raise ValueError(f"unknown R vector type {type!r}")
        self.type = type
        self.values = list(values)
        self.attrs: dict[str, Any] = dict(attrs or {})

    def __len__(self) -> int:
        return len(self.values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RVec):
            return NotImplemented
        return (
            self.type == other.type
            and _values_equal(self.values, other.values)
            and self.attrs.keys() == other.attrs.keys()
            and all(_obj_equal(self.attrs[k], other.attrs[k]) for k in self.attrs)
        )

    def __repr__(self) -> str:
        extra = f", attrs={self.attrs!r}" if self.attrs else ""
        return f"RVec({self.type!r}, {self.values!r}{extra})"


class RList:
    """An R generic vector (``list()``) with optional attributes."""

    __slots__ = ("attrs", "values")

    def __init__(self, values: Iterable[Any], attrs: Mapping[str, Any] | None = None):
        self.values = list(values)
        self.attrs: dict[str, Any] = dict(attrs or {})

    def __len__(self) -> int:
        return len(self.values)

    @property
    def names(self) -> list[str | None] | None:
        n = self.attrs.get("names")
        return None if n is None else list(n.values if isinstance(n, RVec) else n)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RList):
            return NotImplemented
        return (
            len(self.values) == len(other.values)
            and all(_obj_equal(a, b) for a, b in zip(self.values, other.values, strict=True))
            and self.attrs.keys() == other.attrs.keys()
            and all(_obj_equal(self.attrs[k], other.attrs[k]) for k in self.attrs)
        )

    def __repr__(self) -> str:
        extra = f", attrs={self.attrs!r}" if self.attrs else ""
        return f"RList({self.values!r}{extra})"


def _values_equal(a: list[Any], b: list[Any]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
            continue
        if x != y:
            return False
    return True


def _obj_equal(a: Any, b: Any) -> bool:
    return bool(a == b)


def _r_version_int() -> int:
    ver: Any = os.environ.get("PYTACHECK_R_SERIALIZE_VERSION")
    try:
        from pytacheck.utils import get_option

        ver = get_option("pytacheck.r_serialize_version") or ver
    except ImportError:  # pragma: no cover
        pass
    parts = R_VERSION if not ver else tuple(int(p) for p in str(ver).split(".")[:3])
    major, minor, patch = [*parts, 0, 0, 0][:3]
    return major * 65536 + minor * 256 + patch


# ---------------------------------------------------------------------------
# Python -> R object model
# ---------------------------------------------------------------------------


def as_robj(x: Any) -> Any:
    """Convert a Python value into :class:`RVec` / :class:`RList` / ``None``."""
    if x is None or isinstance(x, RVec | RList):
        return x
    if isinstance(x, bool):
        return RVec("lgl", [x])
    if isinstance(x, RInt):
        return RVec("int", [int(x)])
    if isinstance(x, int):
        return RVec("dbl", [float(x)])
    if isinstance(x, float):
        return RVec("dbl", [x])
    if isinstance(x, EllmerOutput):
        return RVec("chr", [str(x)], {"class": RVec("chr", ["ellmer_output"])})
    if isinstance(x, str):
        return RVec("chr", [x])
    if isinstance(x, dt.datetime):
        return _posixct([x])
    if isinstance(x, Mapping):
        return RList([as_robj(v) for v in x.values()], {"names": RVec("chr", [str(k) for k in x])})
    if isinstance(x, list | tuple):
        return RList([as_robj(v) for v in x])
    try:
        import numpy as np
        import pandas as pd
    except ImportError:  # pragma: no cover
        raise TypeError(f"cannot serialise {type(x).__name__}") from None
    if isinstance(x, pd.DataFrame):
        return _data_frame(x)
    if isinstance(x, pd.Series):
        return _series(x)
    if isinstance(x, pd.Categorical):
        return _series(pd.Series(x))
    if isinstance(x, pd.Timestamp):
        return _posixct([x.to_pydatetime()])
    if isinstance(x, np.bool_):
        return RVec("lgl", [bool(x)])
    if isinstance(x, np.integer):
        return RVec("int", [int(x)])
    if isinstance(x, np.floating):
        return RVec("dbl", [float(x)])
    if isinstance(x, np.ndarray):
        return _series(pd.Series(x))
    if x is pd.NA:
        return RVec("lgl", [None])
    raise TypeError(f"cannot serialise {type(x).__name__}")


def _posixct(values: list[Any]) -> RVec:
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        if isinstance(v, dt.datetime):
            ts = v.timestamp() if v.tzinfo is not None else v.replace(tzinfo=dt.UTC).timestamp()
        else:
            ts = float(v)
        out.append(ts)
    return RVec("dbl", out, {"class": RVec("chr", ["POSIXct", "POSIXt"])})


def _series(s: Any) -> Any:
    import numpy as np
    import pandas as pd

    dtype = s.dtype
    vals = s.tolist()

    def na(v: Any) -> bool:
        return v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and math.isnan(v))

    if isinstance(dtype, pd.CategoricalDtype):
        cats = [str(c) for c in dtype.categories]
        codes = [None if c < 0 else int(c) + 1 for c in s.cat.codes.tolist()]
        return RVec(
            "int",
            codes,
            {"levels": RVec("chr", cats), "class": RVec("chr", ["factor"])},
        )
    if pd.api.types.is_bool_dtype(dtype):
        return RVec("lgl", [None if na(v) else bool(v) for v in vals])
    if pd.api.types.is_integer_dtype(dtype):
        return RVec("int", [None if na(v) else int(v) for v in vals])
    if pd.api.types.is_float_dtype(dtype):
        # pandas holds NA_real_ and NaN alike: both become NA (as R's as.data.frame does)
        return RVec("dbl", [None if na(v) else float(v) for v in vals])
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return _posixct([None if na(v) else v.to_pydatetime() for v in vals])
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return RVec("chr", [None if na(v) else str(v) for v in vals])
    # object column: infer an atomic type, else a list column
    kinds = {type(v) for v in vals if not na(v)}
    if kinds and kinds <= {str}:
        return RVec("chr", [None if na(v) else v for v in vals])
    if kinds and kinds <= {bool, np.bool_}:
        return RVec("lgl", [None if na(v) else bool(v) for v in vals])
    if not kinds:
        return RVec("lgl", [None] * len(vals))
    return RList(
        [None if na(v) and not isinstance(v, list) else _list_cell_robj(v) for v in vals]
    )


def _list_cell_robj(v: Any) -> Any:
    """A list-column cell: a Python list of scalars is the atomic vector it came from."""
    if isinstance(v, list | tuple):
        present = [x for x in v if x is not None]
        if all(isinstance(x, str) for x in present) and present:
            return RVec("chr", list(v))
        if all(isinstance(x, bool) for x in present) and present:
            return RVec("lgl", list(v))
        if all(isinstance(x, int) and not isinstance(x, bool) for x in present) and present:
            return RVec("int", [None if x is None else int(x) for x in v])
        if all(isinstance(x, int | float) and not isinstance(x, bool) for x in present) and present:
            return RVec("dbl", [None if x is None else float(x) for x in v])
        if not present:  # character(0) / NA_character_: string arrays are the common case
            return RVec("chr", [None] * len(v))
    return as_robj(v)


def _data_frame(df: Any) -> RList:
    cols = [_series(df[c]) for c in df.columns]
    return RList(
        cols,
        {
            "names": RVec("chr", [str(c) for c in df.columns]),
            "class": RVec("chr", ["data.frame"]),
            "row.names": RVec("int", [None, -len(df)]),
        },
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class _Writer:
    def __init__(self, ascii: bool) -> None:
        self.ascii = ascii
        self.out = bytearray()
        self.refs: dict[str, int] = {}

    # primitives ---------------------------------------------------------
    def int_(self, i: int | None) -> None:
        if self.ascii:
            self.out += b"NA\n" if i is None or i == NA_INTEGER else f"{int(i)}\n".encode()
        else:
            self.out += struct.pack(">i", NA_INTEGER if i is None else int(i))

    def real(self, d: float | None) -> None:
        if self.ascii:
            if d is None:
                s = "NA"
            elif math.isnan(d):
                s = "NA" if struct.pack(">d", d) == struct.pack(">d", NA_REAL) else "NaN"
            elif math.isinf(d):
                s = "Inf" if d > 0 else "-Inf"
            else:
                s = f"{d:.16g}"
            self.out += f"{s}\n".encode()
        else:
            self.out += struct.pack(">d", NA_REAL if d is None else float(d))

    def string(self, b: bytes) -> None:
        if self.ascii:
            self.out += _ascii_escape(b) + b"\n"
        else:
            self.out += b

    # items ----------------------------------------------------------------
    def charsxp(self, s: str | None) -> None:
        if s is None:
            self.int_(CHARSXP)
            self.int_(-1)
            return
        b = s.encode("utf-8")
        levels = _ASCII_MASK if b.isascii() else _UTF8_MASK
        self.int_(CHARSXP | (levels << 12))
        self.int_(len(b))
        self.string(b)

    def symbol(self, name: str) -> None:
        ref = self.refs.get(name)
        if ref is not None:
            self.int_((ref << 8) | REFSXP)
            return
        self.refs[name] = len(self.refs) + 1
        self.int_(SYMSXP)
        self.charsxp(name)

    def attributes(self, attrs: Mapping[str, Any]) -> None:
        for name, value in attrs.items():
            self.int_(LISTSXP | _HAS_TAG)
            self.symbol(name)
            self.item(as_robj(value))
        self.int_(NILVALUE_SXP)

    def item(self, x: Any) -> None:
        if x is None:
            self.int_(NILVALUE_SXP)
            return
        attrs = x.attrs
        flags = _HAS_ATTR if attrs else 0
        if attrs and "class" in attrs:
            flags |= _IS_OBJECT
        if isinstance(x, RList):
            self.int_(VECSXP | flags)
            self.int_(len(x.values))
            for v in x.values:
                self.item(as_robj(v))
        else:
            code = _TYPE_CODES[x.type]
            self.int_(code | flags)
            self.int_(len(x.values))
            if x.type == "chr":
                for v in x.values:
                    self.charsxp(v)
            elif x.type == "dbl":
                for v in x.values:
                    self.real(v)
            elif x.type in ("int", "lgl"):
                for v in x.values:
                    self.int_(None if v is None else int(v))
            elif x.type == "cplx":
                for v in x.values:
                    self.real(None if v is None else v.real)
                    self.real(None if v is None else v.imag)
            elif x.type == "raw":
                data = bytes(x.values)
                if self.ascii:
                    self.out += b"".join(f"{b:02x}\n".encode() for b in data)
                else:
                    self.out += data
        if attrs:
            self.attributes(attrs)


def _ascii_escape(b: bytes) -> bytes:
    """R's OutStringAscii() escapes."""
    special = {
        0x0A: b"\\n",
        0x09: b"\\t",
        0x0B: b"\\v",
        0x08: b"\\b",
        0x0D: b"\\r",
        0x0C: b"\\f",
        0x07: b"\\a",
        0x5C: b"\\\\",
        0x3F: b"\\?",
        0x27: b"\\'",
        0x22: b'\\"',
    }
    out = bytearray()
    for c in b:
        if c in special:
            out += special[c]
        elif c <= 32 or c > 126:
            out += f"\\{c:03o}".encode()
        else:
            out.append(c)
    return bytes(out)


def serialize(x: Any, ascii: bool = False) -> bytes:
    """``serialize(x, connection = NULL, ascii = ascii, xdr = TRUE)`` (version 3)."""
    w = _Writer(ascii)
    w.out += b"A\n" if ascii else b"X\n"
    w.int_(3)
    w.int_(_r_version_int())
    w.int_(3 * 65536 + 5 * 256 + 0)  # oldest R that can read version 3
    enc = b"UTF-8"
    w.int_(len(enc))
    if ascii:
        w.out += enc + b"\n"
    else:
        w.out += enc
    w.item(as_robj(x))
    return bytes(w.out)


def write_rds(x: Any, path: str | os.PathLike[str]) -> None:
    """``saveRDS(x, path)``: gzip-compressed XDR serialization."""
    data = serialize(x, ascii=False)
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(data)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.refs: list[Any] = []
        fmt = data[:2]
        if fmt == b"X\n":
            self.kind = "xdr"
        elif fmt == b"A\n":
            self.kind = "ascii"
        elif fmt == b"B\n":
            self.kind = "native"
        else:
            raise ValueError("unknown input format")
        self.pos = 2

    def _word(self) -> bytes:
        data = self.data
        while self.pos < len(data) and data[self.pos] in b" \t\n\r":
            self.pos += 1
        start = self.pos
        while self.pos < len(data) and data[self.pos] not in b" \t\n\r":
            self.pos += 1
        return data[start : self.pos]

    def int_(self) -> int | None:
        if self.kind == "ascii":
            w = self._word()
            return None if w == b"NA" else int(w)
        fmt = ">i" if self.kind == "xdr" else "<i"
        (v,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += 4
        return None if v == NA_INTEGER else v

    def real(self) -> float | None:
        if self.kind == "ascii":
            w = self._word()
            if w == b"NA":
                return None
            if w == b"NaN":
                return float("nan")
            if w in (b"Inf", b"-Inf"):
                return float(w.decode().replace("Inf", "inf"))
            return float(w)
        fmt = ">d" if self.kind == "xdr" else "<d"
        raw = self.data[self.pos : self.pos + 8]
        (v,) = struct.unpack(fmt, raw)
        self.pos += 8
        if math.isnan(v):
            bits = struct.unpack(">Q", struct.pack(">d", v))[0]
            if bits & 0xFFFFFFFF == 1954:
                return None
        return float(v)

    def bytes_(self, n: int) -> bytes:
        if self.kind == "ascii":
            while self.data[self.pos] in b" \t\n\r":
                self.pos += 1
            out = bytearray()
            while len(out) < n:
                c = self.data[self.pos]
                self.pos += 1
                if c == 0x5C:
                    e = self.data[self.pos]
                    self.pos += 1
                    simple = {
                        ord("n"): 0x0A,
                        ord("t"): 0x09,
                        ord("v"): 0x0B,
                        ord("b"): 0x08,
                        ord("r"): 0x0D,
                        ord("f"): 0x0C,
                        ord("a"): 0x07,
                        ord("\\"): 0x5C,
                        ord("?"): 0x3F,
                        ord("'"): 0x27,
                        ord('"'): 0x22,
                    }
                    if e in simple:
                        out.append(simple[e])
                    else:
                        digits = bytes([e])
                        while len(digits) < 3 and self.data[self.pos] in b"01234567":
                            digits += bytes([self.data[self.pos]])
                            self.pos += 1
                        out.append(int(digits, 8))
                else:
                    out.append(c)
            return bytes(out)
        out_b = self.data[self.pos : self.pos + n]
        self.pos += n
        return out_b

    def length(self) -> int:
        n = self.int_()
        if n == -1:
            hi, lo = self.int_() or 0, self.int_() or 0
            return (hi << 32) + lo
        return int(n or 0)

    def charsxp(self) -> str | None:
        flags = self.int_() or 0
        n = self.int_()
        if n == -1 or n is None:
            return None
        raw = self.bytes_(n)
        levels = flags >> 12
        if levels & 4:  # LATIN1
            return raw.decode("latin-1")
        return raw.decode("utf-8", "replace")

    def attributes_pairlist(self) -> dict[str, Any]:
        item = self.item()
        return _pairlist_dict(item)

    def item(self) -> Any:
        flags = self.int_() or 0
        typ = flags & 0xFF
        has_attr = bool(flags & _HAS_ATTR)
        has_tag = bool(flags & _HAS_TAG)
        if typ == NILVALUE_SXP:
            return None
        if typ == REFSXP:
            idx = flags >> 8
            if idx == 0:
                idx = self.int_() or 0
            return self.refs[idx - 1]
        if typ in (
            EMPTYENV_SXP,
            BASEENV_SXP,
            GLOBALENV_SXP,
            UNBOUNDVALUE_SXP,
            MISSINGARG_SXP,
            BASENAMESPACE_SXP,
        ):
            return _REnv(typ)
        if typ == PERSISTSXP:
            names = self._strvec()
            obj = _REnv(PERSISTSXP, names)
            self.refs.append(obj)
            return obj
        if typ == SYMSXP:
            name = self.charsxp()
            sym = _RSym(name or "")
            self.refs.append(sym)
            return sym
        if typ in (PACKAGESXP, NAMESPACESXP):
            names = self._strvec()
            obj = _REnv(typ, names)
            self.refs.append(obj)
            return obj
        if typ == ENVSXP:
            obj = _REnv(ENVSXP)
            self.refs.append(obj)
            self.int_()  # locked
            self.item()  # enclos
            self.item()  # frame
            self.item()  # hashtab
            attrib = self.item()  # attributes
            obj.attrs = _pairlist_dict(attrib)
            return obj
        if typ in (LISTSXP, 5, 6, 7, ATTRLANGSXP, ATTRLISTSXP, CLOSXP):  # pairlist-like
            attrs = self.attributes_pairlist() if has_attr else {}
            tag = self.item() if has_tag else None
            car = self.item()
            cdr = self.item()
            node = _RPair(tag.name if isinstance(tag, _RSym) else None, car, cdr, attrs)
            return node
        if typ == ALTREP_SXP:
            info = self.item()
            state = self.item()
            attr = self.item()
            alt = _altrep(info, state)
            if attr is not None:
                alt.attrs.update(_pairlist_dict(attr))
            return alt
        if typ == CHARSXP:
            self.pos -= 0  # a bare CHARSXP (should not happen at top level)
            raise ValueError("unexpected CHARSXP")
        if typ in _CODE_TYPES:
            n = self.length()
            kind = _CODE_TYPES[typ]
            if kind == "chr":
                vals: list[Any] = [self.charsxp() for _ in range(n)]
            elif kind == "dbl":
                vals = [self.real() for _ in range(n)]
            elif kind in ("int", "lgl"):
                vals = [self.int_() for _ in range(n)]
                if kind == "lgl":
                    vals = [None if v is None else bool(v) for v in vals]
            elif kind == "cplx":
                vals = []
                for _ in range(n):
                    re_, im = self.real(), self.real()
                    vals.append(None if re_ is None else complex(re_, im or 0.0))
            else:  # raw
                if self.kind == "ascii":
                    vals = [int(self._word(), 16) for _ in range(n)]
                else:
                    vals = list(self.bytes_(n))
            vec = RVec(kind, vals)
            if has_attr:
                vec.attrs = self.attributes_pairlist()
            return vec
        if typ in (VECSXP, EXPRSXP):
            n = self.length()
            lst = RList([self.item() for _ in range(n)])
            if has_attr:
                lst.attrs = self.attributes_pairlist()
            return lst
        if typ == S4SXP:
            s4 = RList([])
            if has_attr:
                s4.attrs = self.attributes_pairlist()
            return s4
        raise ValueError(f"unsupported SEXP type {typ} in serialized data")

    def _strvec(self) -> list[str | None]:
        self.int_()  # 0
        n = self.int_() or 0
        return [self.charsxp() for _ in range(n)]

    def read(self) -> Any:
        version = self.int_()
        self.int_()  # writer version
        self.int_()  # min reader version
        if version == 3:
            n = self.int_() or 0
            if self.kind == "ascii":
                self._word()
            else:
                self.bytes_(n)
        return self.item()


class _RSym:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _REnv:
    def __init__(self, kind: int, names: Any = None) -> None:
        self.kind = kind
        self.names = names
        self.attrs: dict[str, Any] = {}


class _RPair:
    __slots__ = ("attrs", "car", "cdr", "tag")

    def __init__(self, tag: str | None, car: Any, cdr: Any, attrs: dict[str, Any]) -> None:
        self.tag, self.car, self.cdr, self.attrs = tag, car, cdr, attrs


def _pairlist_dict(node: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    while isinstance(node, _RPair):
        out[node.tag or ""] = node.car
        node = node.cdr
    return out


def _altrep(info: Any, state: Any) -> RVec | RList:
    """Materialise the ALTREP classes base R serialises (compact sequences, wrappers)."""
    cls = ""
    if isinstance(info, _RPair) and isinstance(info.car, _RSym):
        cls = info.car.name
    if cls == "compact_intseq":
        n, start, incr = (int(v) for v in state.values[:3])
        return RVec("int", [start + i * incr for i in range(n)])
    if cls == "compact_realseq":
        n, start, incr = state.values[:3]
        return RVec("dbl", [start + i * incr for i in range(int(n))])
    if cls == "deferred_string":
        arg = state.car if isinstance(state, _RPair) else state
        vals = [None if v is None else _deferred_str(v, arg.type) for v in arg.values]
        return RVec("chr", vals)
    if cls.startswith("wrap_"):
        inner = state.values[0] if isinstance(state, RList) else state
        if not isinstance(inner, RVec | RList):
            raise ValueError(f"unsupported ALTREP state for {cls!r}")
        return inner
    raise ValueError(f"unsupported ALTREP class {cls!r}")


def _deferred_str(v: Any, typ: str) -> str:
    from pytacheck._r import as_character

    if typ == "lgl":
        return "TRUE" if v else "FALSE"
    return as_character(v) or "NA"


def unserialize(data: bytes) -> Any:
    """``unserialize()``: the R object model (:class:`RVec`, :class:`RList`, ``None``)."""
    return _Reader(data).read()


def read_rds(path: str | os.PathLike[str]) -> Any:
    """``readRDS(path)`` as the R object model (gzip, bzip2, xz or uncompressed)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:3] == b"BZh":
        import bz2

        raw = bz2.decompress(raw)
    elif raw[:6] == b"\xfd7zXZ\x00":
        import lzma

        raw = lzma.decompress(raw)
    return unserialize(raw)


# ---------------------------------------------------------------------------
# R object model -> Python
# ---------------------------------------------------------------------------


def _classes(x: Any) -> list[str]:
    cls = x.attrs.get("class") if isinstance(x, RVec | RList) else None
    return [c for c in cls.values if c] if isinstance(cls, RVec) else []


def to_python(x: Any) -> Any:
    """Convert the R object model into convenient Python values.

    ``data.frame`` -> ``pandas.DataFrame`` (R column types preserved), named
    list -> ``dict``, unnamed list -> ``list``, a length-1 atomic vector ->
    scalar (``None`` for ``NA``), longer vectors -> ``list``, factors ->
    their labels, ``POSIXct`` -> ``datetime`` (UTC).
    """
    if x is None:
        return None
    if isinstance(x, RList):
        cls = _classes(x)
        if "data.frame" in cls:
            return _to_frame(x)
        names = x.names
        vals = [to_python(v) for v in x.values]
        if names is not None:
            return {str(k or ""): v for k, v in zip(names, vals, strict=True)}
        return vals
    if isinstance(x, RVec):
        vals = _atomic_values(x)
        if len(vals) == 1 and "names" not in x.attrs:
            return vals[0]
        names = x.attrs.get("names")
        if isinstance(names, RVec):
            return dict(zip([str(n) for n in names.values], vals, strict=True))
        return vals
    return None


def _atomic_values(x: RVec) -> list[Any]:
    cls = _classes(x)
    if "ellmer_output" in cls and x.type == "chr":
        return [None if v is None else EllmerOutput(v) for v in x.values]
    if "factor" in cls:
        levels = x.attrs.get("levels")
        labs = levels.values if isinstance(levels, RVec) else []
        return [None if v is None else labs[v - 1] for v in x.values]
    if "POSIXct" in cls:
        return [
            None if v is None or math.isnan(v) else dt.datetime.fromtimestamp(v, tz=dt.UTC)
            for v in x.values
        ]
    if "Date" in cls:
        epoch = dt.date(1970, 1, 1)
        return [None if v is None else epoch + dt.timedelta(days=float(v)) for v in x.values]
    return list(x.values)


def _to_frame(x: RList) -> pd.DataFrame:
    import pandas as pd

    names = x.names or []
    cols: dict[str, Any] = {}
    nrow = None
    rn = x.attrs.get("row.names")
    if isinstance(rn, RVec):
        if rn.type == "int" and len(rn.values) == 2 and rn.values[0] is None:
            nrow = abs(int(rn.values[1] or 0))
        else:
            nrow = len(rn.values)
    for name, col in zip(names, x.values, strict=True):
        cols[str(name)] = _to_series(col)
        if nrow is None:
            nrow = len(cols[str(name)])
    if not cols:
        return pd.DataFrame(index=range(nrow or 0))
    return pd.DataFrame(cols)


def _list_elem(v: Any) -> Any:
    """One element of an R list column: vectors stay lists (``character(0)`` is ``[]``)."""
    if isinstance(v, RVec):
        return _atomic_values(v)
    if isinstance(v, list | tuple):
        return [_list_elem(x) for x in v]
    if isinstance(v, RList):
        return to_python(v)
    return v


def _to_series(col: Any) -> pd.Series:
    import pandas as pd

    if isinstance(col, RList):
        if "data.frame" in _classes(col):  # a data-frame column: one record per row
            return pd.Series(_to_frame(col).to_dict("records"), dtype=object)
        return pd.Series([_list_elem(v) for v in col.values], dtype=object)
    if isinstance(col, list | tuple):  # a list column built in Python (convert_from_type)
        return pd.Series([_list_elem(v) for v in col], dtype=object)
    if not isinstance(col, RVec):
        return pd.Series([], dtype=object)
    cls = _classes(col)
    if "factor" in cls:
        levels = col.attrs.get("levels")
        labs = list(levels.values) if isinstance(levels, RVec) else []
        return pd.Series(pd.Categorical(_atomic_values(col), categories=labs))
    if "POSIXct" in cls:
        return pd.Series(pd.to_datetime(_atomic_values(col), utc=True))
    if "Date" in cls:
        return pd.Series(_atomic_values(col), dtype=object)
    dtype = {"chr": "string", "int": "Int64", "dbl": "float64", "lgl": "boolean"}.get(col.type)
    vals = [float("nan") if (v is None and col.type == "dbl") else v for v in col.values]
    return pd.Series(vals, dtype=dtype or object)
