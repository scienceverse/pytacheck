"""A reader for R's serialization format (``.rds``, ``.RData``/``.rda``).

metacheck reads ``.rds`` files with ``readRDS()`` and ``.RData`` workspaces
with ``load()`` in a child R process (``.read_rdata_isolated()``). Workspaces
routinely hold fitted models, formulas, closures and byte code next to the
data, which ``pyreadr``/librdata cannot parse, so pytacheck implements R's
unserializer (``src/main/serialize.c``) directly: XDR, native-binary and ASCII
streams, gzip/bzip2/xz compression, reference tables, byte code, ALTREP
compact sequences and wrappers. Nothing is evaluated -- environments and
namespaces are opaque placeholders -- so any object R could restore can be
walked safely.

:func:`workspace_first_data_frame` reproduces ``load()`` into ``new.env()``
followed by ``Filter(is.data.frame, as.list(e))[[1]]``, including the order
in which R's hashed environment lists its bindings.
"""

from __future__ import annotations

import bz2
import contextlib
import datetime as dt
import gzip
import lzma
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["RObject", "r_frame_to_pandas", "read_rdata", "read_rds", "workspace_first_data_frame"]

# SEXPTYPEs
NILSXP, SYMSXP, LISTSXP, CLOSXP, ENVSXP, PROMSXP, LANGSXP = 0, 1, 2, 3, 4, 5, 6
SPECIALSXP, BUILTINSXP, CHARSXP, LGLSXP, INTSXP, REALSXP = 7, 8, 9, 10, 13, 14
CPLXSXP, STRSXP, DOTSXP, VECSXP, EXPRSXP, BCODESXP = 15, 16, 17, 19, 20, 21
EXTPTRSXP, WEAKREFSXP, RAWSXP, OBJSXP = 22, 23, 24, 25
# pseudo-SEXPTYPEs of the serialization format
REFSXP, NILVALUE_SXP, GLOBALENV_SXP, UNBOUNDVALUE_SXP = 255, 254, 253, 252
MISSINGARG_SXP, BASENAMESPACE_SXP, NAMESPACESXP, PACKAGESXP = 251, 247, 249, 250
PERSISTSXP, CLASSREFSXP, GENERICREFSXP, BCREPDEF, BCREPREF = 248, 246, 245, 244, 243
EMPTYENV_SXP, BASEENV_SXP, ATTRLANGSXP, ATTRLISTSXP, ALTREP_SXP = 242, 241, 240, 239, 238

UTF8_MASK, LATIN1_MASK, BYTES_MASK, ASCII_MASK = 1 << 3, 1 << 2, 1 << 1, 1 << 6
NA_INTEGER = -(2**31)
_NA_REAL_BITS = 0x7FF00000000007A2  # R's NA_real_ (a NaN with payload 1954)


class RSerializationError(ValueError):
    """The stream is not valid R serialization (or uses an unsupported feature)."""


@dataclass
class RObject:
    """A deserialized R value.

    ``value`` holds the payload: a numpy array for logical/integer/double/
    complex vectors (``NA_integer_`` = ``-2**31``), a list of ``str | None`` for
    character vectors, a list of objects for lists, ``bytes`` for raw vectors,
    a list of ``(tag, value)`` pairs for pairlists / calls, and ``None`` for
    environments and other opaque objects.
    """

    type: int
    value: Any = None
    attrs: dict[str, Any] = field(default_factory=dict)
    name: str | None = None  # symbols, namespaces, builtins

    def attr(self, name: str) -> Any:
        return self.attrs.get(name)

    @property
    def classes(self) -> list[str]:
        cls = self.attrs.get("class")
        if isinstance(cls, RObject) and cls.type == STRSXP:
            return [c for c in cls.value if c is not None]
        return []

    def __len__(self) -> int:
        v = self.value
        return 0 if v is None else len(v)


NULL = None


class _Stream:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        fmt = data[:2]
        if fmt == b"X\n":
            self.kind = "xdr"
        elif fmt == b"B\n":
            self.kind = "bin"
        elif fmt == b"A\n":
            self.kind = "ascii"
        else:
            raise RSerializationError("unknown input format")
        self.pos = 2
        self.refs: list[Any] = []

    # -- primitives ----------------------------------------------------------
    def _word(self) -> bytes:
        d, n = self.data, len(self.data)
        p = self.pos
        while p < n and d[p] in b" \t\n\r\f\v":
            p += 1
        start = p
        while p < n and d[p] not in b" \t\n\r\f\v":
            p += 1
        if start == p:
            raise RSerializationError("read error")
        self.pos = p
        return d[start:p]

    def integer(self) -> int:
        if self.kind == "ascii":
            w = self._word()
            return NA_INTEGER if w == b"NA" else int(w)
        p = self.pos
        self.pos += 4
        fmt = ">i" if self.kind == "xdr" else "<i"
        try:
            return struct.unpack_from(fmt, self.data, p)[0]
        except struct.error as exc:
            raise RSerializationError("read error") from exc

    def real(self) -> float:
        if self.kind == "ascii":
            w = self._word()
            special = {b"NA": _na_real(), b"NaN": math.nan, b"Inf": math.inf, b"-Inf": -math.inf}
            return special[w] if w in special else float(w)
        p = self.pos
        self.pos += 8
        return struct.unpack_from(">d" if self.kind == "xdr" else "<d", self.data, p)[0]

    def ints(self, n: int) -> np.ndarray:
        if self.kind == "ascii":
            return np.array([self.integer() for _ in range(n)], dtype=np.int32)
        p = self.pos
        self.pos += 4 * n
        if self.pos > len(self.data):
            raise RSerializationError("read error")
        dt_ = ">i4" if self.kind == "xdr" else "<i4"
        return np.frombuffer(self.data, dtype=dt_, count=n, offset=p).astype(np.int32)

    def reals(self, n: int) -> np.ndarray:
        if self.kind == "ascii":
            return np.array([self.real() for _ in range(n)], dtype=np.float64)
        p = self.pos
        self.pos += 8 * n
        if self.pos > len(self.data):
            raise RSerializationError("read error")
        dt_ = ">f8" if self.kind == "xdr" else "<f8"
        return np.frombuffer(self.data, dtype=dt_, count=n, offset=p).astype(np.float64)

    def string(self, length: int) -> bytes:
        if self.kind != "ascii":
            p = self.pos
            self.pos += length
            return self.data[p : p + length]
        return self._ascii_string(length)

    def _ascii_string(self, length: int) -> bytes:
        d = self.data
        p = self.pos
        while d[p] in b" \t\n\r\f\v":
            p += 1
        out = bytearray()
        escapes = {
            ord("n"): 10, ord("t"): 9, ord("v"): 11, ord("b"): 8, ord("r"): 13,
            ord("f"): 12, ord("a"): 7, ord("\\"): 92, ord("?"): 63, ord("'"): 39, ord('"'): 34,
        }  # fmt: skip
        while len(out) < length:
            c = d[p]
            p += 1
            if c == 92:
                c = d[p]
                p += 1
                if 48 <= c < 56:
                    v, j = 0, 0
                    while 48 <= c < 56 and j < 3:
                        v = v * 8 + (c - 48)
                        c = d[p]
                        p += 1
                        j += 1
                    p -= 1
                    out.append(v & 0xFF)
                else:
                    out.append(escapes.get(c, c))
            else:
                out.append(c)
        self.pos = p
        return bytes(out)

    def length(self) -> int:
        n = self.integer()
        if n < -1:
            raise RSerializationError("negative serialized length for vector")
        if n == -1:
            hi, lo = self.integer(), self.integer()
            return (hi << 32) + (lo & 0xFFFFFFFF)
        return n

    # -- items -----------------------------------------------------------------
    def item(self) -> Any:
        return self._item(self.integer())

    def _item(self, flags: int) -> Any:
        t = flags & 0xFF
        levs = flags >> 12
        has_attr = bool(flags & (1 << 9))
        if t == NILVALUE_SXP:
            return NULL
        if t in (EMPTYENV_SXP, BASEENV_SXP, GLOBALENV_SXP, BASENAMESPACE_SXP):
            return RObject(ENVSXP, name={242: "emptyenv", 241: "baseenv", 253: "globalenv",
                                         247: "base"}[t])  # fmt: skip
        if t in (UNBOUNDVALUE_SXP, MISSINGARG_SXP):
            return RObject(t)
        if t == REFSXP:
            i = flags >> 8
            if i == 0:
                i = self.integer()
            return self.refs[i - 1]
        if t == PERSISTSXP:
            names = self._string_vec()
            obj = RObject(PERSISTSXP, value=names)
            self.refs.append(obj)
            return obj
        if t == ALTREP_SXP:
            info = self.item()
            state = self.item()
            attr = self.item()
            return _altrep(info, state, attr)
        if t == SYMSXP:
            pname = self.item()
            obj = RObject(SYMSXP, name=pname if isinstance(pname, str) else "")
            self.refs.append(obj)
            return obj
        if t in (PACKAGESXP, NAMESPACESXP):
            obj = RObject(ENVSXP, value=None, name=":".join(v or "" for v in self._string_vec()))
            self.refs.append(obj)
            return obj
        if t == ENVSXP:
            self.integer()  # locked
            env = RObject(ENVSXP)
            self.refs.append(env)
            enclos = self.item()
            frame = self.item()
            hashtab = self.item()
            attrib = self.item()
            env.value = {"enclos": enclos, "frame": frame, "hashtab": hashtab}
            env.attrs = _attrs(attrib)
            return env
        if t in (LISTSXP, LANGSXP, CLOSXP, PROMSXP, DOTSXP):
            return self._pairlist(flags)
        # the remaining types read their attributes after the payload
        obj: Any
        if t == EXTPTRSXP:
            obj = RObject(EXTPTRSXP)
            self.refs.append(obj)
            obj.value = (self.item(), self.item())
        elif t == WEAKREFSXP:
            obj = RObject(WEAKREFSXP)
            self.refs.append(obj)
        elif t in (SPECIALSXP, BUILTINSXP):
            n = self.integer()
            obj = RObject(t, name=self.string(n).decode("latin-1"))
        elif t == CHARSXP:
            n = self.integer()
            if n == -1:
                obj = None
            else:
                obj = _decode_char(self.string(n), levs)
            if has_attr:
                self.item()
            return obj
        elif t in (LGLSXP, INTSXP):
            obj = RObject(t, self.ints(self.length()))
        elif t == REALSXP:
            obj = RObject(t, self.reals(self.length()))
        elif t == CPLXSXP:
            n = self.length()
            parts = self.reals(2 * n)
            obj = RObject(t, parts[0::2] + 1j * parts[1::2])
        elif t == STRSXP:
            n = self.length()
            obj = RObject(t, [self._charsxp() for _ in range(n)])
        elif t in (VECSXP, EXPRSXP):
            n = self.length()
            obj = RObject(t, [self.item() for _ in range(n)])
        elif t == BCODESXP:
            nreps = self.integer()
            obj = self._bc1([None] * max(nreps, 0))
        elif t == RAWSXP:
            n = self.length()
            if self.kind == "ascii":
                obj = RObject(t, bytes(int(self._word(), 16) for _ in range(n)))
            else:
                obj = RObject(t, self.string(n))
        elif t == OBJSXP:
            obj = RObject(OBJSXP)
        elif t in (CLASSREFSXP, GENERICREFSXP):
            raise RSerializationError("this version of R cannot read class references")
        else:
            raise RSerializationError(f"ReadItem: unknown type {t}")
        if has_attr:
            attrib = self.item()
            if isinstance(obj, RObject):
                obj.attrs = _attrs(attrib)
        return obj

    def _charsxp(self) -> str | None:
        flags = self.integer()
        if flags & 0xFF != CHARSXP:
            v = self._item(flags)
            return v if isinstance(v, str) or v is None else None
        n = self.integer()
        s = None if n == -1 else _decode_char(self.string(n), flags >> 12)
        if flags & (1 << 9):
            self.item()
        return s

    def _string_vec(self) -> list[str | None]:
        if self.integer() != 0:
            raise RSerializationError("names in persistent strings are not supported yet")
        n = self.integer()
        return [self.item() for _ in range(n)]

    def _pairlist(self, flags: int) -> RObject:
        head: RObject | None = None
        items: list[tuple[Any, Any]] = []
        t = flags & 0xFF
        attrs: dict[str, Any] = {}
        while t in (LISTSXP, LANGSXP, CLOSXP, PROMSXP, DOTSXP):
            has_attr = bool(flags & (1 << 9))
            has_tag = bool(flags & (1 << 10))
            a = self.item() if has_attr else NULL
            tag = self.item() if has_tag else NULL
            car = self.item()
            if head is None:
                head = RObject(t)
                attrs = _attrs(a)
            items.append((tag.name if isinstance(tag, RObject) else None, car))
            flags = self.integer()
            t = flags & 0xFF
        tail = self._item(flags)
        assert head is not None
        # a non-NULL tail is a dotted pair (or a closure/promise body)
        head.value = items if tail is None else [*items, (None, tail)]
        head.attrs = attrs
        return head

    def _bc1(self, reps: list[Any]) -> RObject:
        code = self.item()
        n = self.integer()
        consts = []
        for _ in range(n):
            t = self.integer()
            if t == BCODESXP:
                consts.append(self._bc1(reps))
            elif t in (LANGSXP, LISTSXP, BCREPDEF, BCREPREF, ATTRLANGSXP, ATTRLISTSXP):
                consts.append(self._bclang(t, reps))
            else:
                consts.append(self.item())
        return RObject(BCODESXP, value=(code, consts))

    def _bclang(self, t: int, reps: list[Any]) -> Any:
        if t == BCREPREF:
            return reps[self.integer()]
        if t in (BCREPDEF, LANGSXP, LISTSXP, ATTRLANGSXP, ATTRLISTSXP):
            pos = -1
            if t == BCREPDEF:
                pos = self.integer()
                t = self.integer()
            has_attr = t in (ATTRLANGSXP, ATTRLISTSXP)
            t = LANGSXP if t in (ATTRLANGSXP, LANGSXP) else LISTSXP
            ans = RObject(t)
            if pos >= 0:
                reps[pos] = ans
            if has_attr:
                ans.attrs = _attrs(self.item())
            tag = self.item()
            car = self._bclang(self.integer(), reps)
            cdr = self._bclang(self.integer(), reps)
            items = [(tag.name if isinstance(tag, RObject) else None, car)]
            if isinstance(cdr, RObject) and cdr.type in (LISTSXP, LANGSXP) and cdr is not ans:
                items += list(cdr.value or [])
            ans.value = items
            return ans
        return self.item()


def _na_real() -> float:
    return struct.unpack("<d", struct.pack("<Q", _NA_REAL_BITS))[0]


class Latin1Str(str):
    """A CHARSXP marked latin1 whose bytes are not valid UTF-8.

    It reads as its Latin-1 text; :func:`_as_bytes_str` gives R's view of the
    raw bytes (``validUTF8()`` is FALSE), which ``.utf8_repair_df()`` repairs
    and counts.
    """

    __slots__ = ("raw",)
    raw: bytes


def _decode_char(raw: bytes, levs: int) -> str:
    if levs & LATIN1_MASK:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            out = Latin1Str(raw.decode("latin-1"))
            out.raw = raw
            return out
        return raw.decode("latin-1")
    return raw.decode("utf-8", "surrogateescape")


def _as_bytes_str(v: Any) -> Any:
    """R's byte-level view of a string (invalid UTF-8 kept as surrogate escapes)."""
    if isinstance(v, Latin1Str):
        return v.raw.decode("utf-8", "surrogateescape")
    return v


def _attrs(pl: Any) -> dict[str, Any]:
    if not isinstance(pl, RObject) or pl.type != LISTSXP:
        return {}
    return {tag: val for tag, val in (pl.value or []) if tag is not None}


def _altrep(info: Any, state: Any, attr: Any) -> Any:
    """Rebuild the base ALTREP classes (compact sequences, wrappers, deferred strings)."""
    cls = ""
    if isinstance(info, RObject) and info.value:
        first = info.value[0][1]
        if isinstance(first, RObject):
            cls = first.name or ""
    obj: Any
    if cls == "compact_intseq":
        n, n1, inc = (int(v) for v in state.value[:3])
        obj = RObject(INTSXP, np.arange(n1, n1 + inc * n, inc, dtype=np.int32)[:n])
    elif cls == "compact_realseq":
        n, n1, inc = state.value[:3]
        obj = RObject(REALSXP, n1 + inc * np.arange(int(n), dtype=np.float64))
    elif cls.startswith("wrap_"):
        inner = state.value[0] if isinstance(state, RObject) and state.value else None
        obj = RObject(inner.type, inner.value) if isinstance(inner, RObject) else RObject(NILSXP)
    elif cls == "deferred_string":
        arg = state.value[0][1] if isinstance(state, RObject) and state.value else None
        obj = RObject(STRSXP, _coerce_to_string(arg))
    elif isinstance(state, RObject) and state.type in (INTSXP, REALSXP, STRSXP, LGLSXP, VECSXP):
        obj = RObject(state.type, state.value)
    else:
        obj = RObject(NILSXP)
    obj.attrs = _attrs(attr)
    return obj


def _coerce_to_string(arg: Any) -> list[str | None]:
    from pytacheck._r.base import as_character

    if not isinstance(arg, RObject):
        return []
    if arg.type == INTSXP:
        return [None if v == NA_INTEGER else str(int(v)) for v in arg.value]
    if arg.type == REALSXP:
        return [None if _is_na_real(v) else as_character(float(v)) for v in arg.value]
    return []


def _is_na_real(v: float) -> bool:
    return math.isnan(v)


def _decompress(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:3] == b"BZh":
        return bz2.decompress(raw)
    if raw[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(raw)
    return raw


def _unserialize(data: bytes) -> Any:
    st = _Stream(data)
    version = st.integer()
    st.integer()  # writer version
    st.integer()  # minimal reader version
    if version == 3:
        n = st.integer()
        st.string(n)  # native encoding
    elif version != 2:
        raise RSerializationError(f"cannot read workspace version {version}")
    return st.item()


def read_rds(path: str | Path) -> Any:
    """``readRDS(path)``: the deserialized object."""
    return _unserialize(_decompress(Path(path).read_bytes()))


def read_rdata(path: str | Path) -> list[tuple[str, Any]]:
    """The ``(name, object)`` pairs of an ``.RData``/``.rda`` file, in saved order."""
    data = _decompress(Path(path).read_bytes())
    magic = data[:5]
    if magic not in (b"RDX2\n", b"RDX3\n", b"RDB2\n", b"RDB3\n", b"RDA2\n", b"RDA3\n"):
        raise RSerializationError("bad restore file magic number (file may be corrupted)")
    obj = _unserialize(data[5:])
    if not isinstance(obj, RObject) or obj.type != LISTSXP:
        return []
    return [(tag or "", val) for tag, val in obj.value or []]


# -----------------------------------------------------------------------------
# load() into new.env() and as.list() of it
# -----------------------------------------------------------------------------


def _pjw_hash(name: str) -> int:
    """R_Newhashpjw() over the symbol's bytes (C ``char`` is signed)."""
    h = 0
    for b in name.encode("utf-8", "surrogateescape"):
        c = b - 256 if b > 127 else b
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
            h ^= g
    return h


def env_binding_order(names: list[str], size: int = 29) -> list[str]:
    """The order ``as.list(e)`` lists bindings defined, in order, into ``new.env()``.

    Emulates R's hashed environment: new bindings are consed onto the front of
    their bucket's chain, the table grows by 1.2 once more than 85% of it is
    "used" (HASHPRI counts every new binding) and ``as.list`` walks the buckets
    in index order.
    """
    table: list[list[str]] = [[] for _ in range(size)]
    pri = 0
    for name in names:
        bucket = table[_pjw_hash(name) % len(table)]
        if name in bucket:
            continue
        bucket.insert(0, name)
        pri += 1
        if pri > len(table) * 0.85:
            new_size = 1 + int(len(table) * 1.2)
            new_table: list[list[str]] = [[] for _ in range(new_size)]
            pri = 0
            for chain in table:
                for nm in chain:
                    nb = new_table[_pjw_hash(nm) % new_size]
                    if not nb:
                        pri += 1
                    nb.insert(0, nm)
            table = new_table
    return [nm for chain in table for nm in chain]


def is_data_frame(obj: Any) -> bool:
    return isinstance(obj, RObject) and obj.type == VECSXP and "data.frame" in obj.classes


def workspace_first_data_frame(path: str | Path) -> RObject | None:
    """``Filter(is.data.frame, as.list(e))[[1]]`` after ``load(path, envir = e)``."""
    objects = read_rdata(path)
    values = dict(objects)
    for name in env_binding_order([n for n, _ in objects]):
        if name.startswith("."):
            continue
        if is_data_frame(values[name]):
            return values[name]
    return None


# -----------------------------------------------------------------------------
# data.frame -> pandas
# -----------------------------------------------------------------------------


def _nrow(df: RObject) -> int:
    rn = df.attr("row.names")
    if isinstance(rn, RObject):
        if rn.type == INTSXP and len(rn.value) == 2 and rn.value[0] == NA_INTEGER:
            return abs(int(rn.value[1]))
        return len(rn.value)
    cols = df.value or []
    return len(cols[0]) if cols and isinstance(cols[0], RObject) else 0


def _strvec(obj: Any) -> list[str | None]:
    if isinstance(obj, RObject) and obj.type == STRSXP:
        return list(obj.value)
    return []


def _attr_value(obj: Any) -> Any:
    """A column attribute in plain Python (named vectors become dicts)."""
    if not isinstance(obj, RObject):
        return obj
    names = _strvec(obj.attr("names"))
    if obj.type in (INTSXP, LGLSXP):
        vals: list[Any] = [None if v == NA_INTEGER else int(v) for v in obj.value]
        if obj.type == LGLSXP:
            vals = [None if v is None else bool(v) for v in vals]
    elif obj.type == REALSXP:
        vals = [None if _is_r_na(v) else float(v) for v in obj.value]
    elif obj.type == STRSXP:
        vals = list(obj.value)
    else:
        return None
    if names and len(names) == len(vals):
        return {("" if n is None else n): v for n, v in zip(names, vals, strict=True)}
    return vals[0] if len(vals) == 1 else vals


def _is_r_na(v: float) -> bool:
    return math.isnan(v)


_KEPT_ATTRS = ("label", "labels", "na_values", "na_range", "format.spss", "format.stata",
               "format.sas", "display_width", "class", "levels", "tzone")  # fmt: skip

# Classes whose ``[`` method is vctrs::vec_slice(), which keeps every attribute.
_VCTRS_CLASSES = frozenset({"vctrs_vctr", "vctrs_rcrd", "vctrs_list_of"})
# Base R ``[`` methods and the attributes each carries over to the subset.
_BASE_SUBSET_KEEPS: dict[str, tuple[str, ...]] = {
    "factor": ("class", "levels"),
    "Date": ("class",),
    "POSIXct": ("class", "tzone"),
    "difftime": ("class", "units"),
    "AsIs": ("class",),
    "noquote": ("class",),
    "numeric_version": ("class",),
    "roman": ("class",),
    "octmode": ("class",),
    "hexmode": ("class",),
}


def _attrs_after_subset(attrs: dict[str, Any], classes: list[str], vctrs: bool) -> dict[str, Any]:
    """The attributes a column keeps through ``[.data.frame`` (``xj[i]``).

    S3 dispatch picks the first class with a ``[`` method; without one the
    default method keeps no attributes at all (so an unclassed column's
    ``label``, or bit64's ``integer64`` class when bit64 is not loaded, is
    lost). *vctrs* says whether the vctrs namespace is loaded in the R session
    (it is once haven, readxl or readODS have been used).
    """
    for cl in classes:
        if vctrs and cl in _VCTRS_CLASSES:
            return dict(attrs)
        keep = _BASE_SUBSET_KEEPS.get(cl)
        if keep is not None:
            return {k: v for k, v in attrs.items() if k in keep}
    return {}


def _is_flattened(col: RObject) -> bool:
    """Columns ``.utf8_repair_df()`` renders as text (data frames, matrices, lists)."""
    if col.type in (VECSXP, EXPRSXP):
        return True
    dim = col.attr("dim")
    return isinstance(dim, RObject) and len(dim.value) == 2


def _column_to_pandas(col: Any, n: int) -> pd.Series:
    if not isinstance(col, RObject):
        return pd.Series([None] * n, dtype=object)
    classes = col.classes
    v = col.value
    t = col.type
    if t == VECSXP and "data.frame" in classes:
        return pd.Series(_flatten_df_column(col, n), dtype="string")
    dim = col.attr("dim")
    if isinstance(dim, RObject) and len(dim.value) == 2 and t != VECSXP:
        return pd.Series(_flatten_matrix(col), dtype="string")
    if t == INTSXP and "factor" in classes:
        levels = [_as_bytes_str(x) for x in _strvec(col.attr("levels"))]
        codes = np.where(v == NA_INTEGER, -1, v.astype(np.int64) - 1)
        cats = list(dict.fromkeys(levels))
        mapping = [cats.index(lv) if lv in cats else -1 for lv in levels]
        codes = np.array([mapping[c] if c >= 0 else -1 for c in codes], dtype=np.int64)
        return pd.Series(
            pd.Categorical.from_codes(codes, categories=cats, ordered="ordered" in classes)
        )
    if t in (INTSXP, REALSXP) and "Date" in classes:
        days = v.astype(np.float64)
        return pd.Series(
            [
                None
                if not math.isfinite(d)
                else dt.date(1970, 1, 1) + dt.timedelta(days=math.floor(d))
                for d in days
            ],
            dtype=object,
        )
    if t in (INTSXP, REALSXP) and "POSIXct" in classes:
        secs = pd.Series(
            np.where(v == NA_INTEGER, np.nan, v) if t == INTSXP else v, dtype="float64"
        )
        out = pd.to_datetime(secs, unit="s", utc=True)
        tz = _strvec(col.attr("tzone"))
        if tz and tz[0] not in (None, "", "UTC", "GMT"):
            with contextlib.suppress(Exception):  # unknown zones stay in UTC
                out = out.dt.tz_convert(tz[0])
        return out
    if t == REALSXP and "integer64" in classes:
        ints = v.view(np.int64)
        return pd.Series([None if x == -(2**63) else int(x) for x in ints.tolist()], dtype="Int64")
    if t == LGLSXP:
        return pd.Series(
            [None if x == NA_INTEGER else bool(x) for x in v.tolist()], dtype="boolean"
        )
    if t == INTSXP:
        return pd.Series([None if x == NA_INTEGER else int(x) for x in v.tolist()], dtype="Int64")
    if t == REALSXP:
        return pd.Series(np.asarray(v, dtype=np.float64), dtype="float64")
    if t == CPLXSXP:
        return pd.Series(v, dtype=complex)
    if t == STRSXP:
        return pd.Series([_as_bytes_str(x) for x in v], dtype="string")
    if t in (VECSXP, EXPRSXP):
        return pd.Series([_flatten_list_cell(e, 20) for e in v], dtype="string")
    if t == RAWSXP:
        return pd.Series([f"{b:02x}" for b in v], dtype="string")
    return pd.Series([None] * n, dtype=object)


def _as_character_vec(obj: Any) -> list[str | None]:
    """``as.character()`` of an atomic R vector (factor labels, dates, numbers)."""
    from pytacheck._r.base import as_character

    if not isinstance(obj, RObject):
        return []
    t, v, classes = obj.type, obj.value, obj.classes
    if t == INTSXP and "factor" in classes:
        levels = _strvec(obj.attr("levels"))
        return [None if x == NA_INTEGER else levels[x - 1] for x in v.tolist()]
    if t in (INTSXP, REALSXP) and "Date" in classes:
        return [
            None
            if not math.isfinite(float(d))
            else (dt.date(1970, 1, 1) + dt.timedelta(days=math.floor(float(d)))).isoformat()
            for d in v.tolist()
        ]
    if t == LGLSXP:
        return [None if x == NA_INTEGER else ("TRUE" if x else "FALSE") for x in v.tolist()]
    if t == INTSXP:
        return [None if x == NA_INTEGER else str(x) for x in v.tolist()]
    if t == REALSXP:
        return [None if math.isnan(x) and _is_na_bits(x) else as_character(x) for x in v.tolist()]
    if t == STRSXP:
        return list(v)
    if t == CPLXSXP:
        return [str(x) for x in v.tolist()]
    return []


def _is_na_bits(x: float) -> bool:
    return math.isnan(x)


def _unlist(obj: Any) -> list[str | None]:
    """``unlist(obj)`` coerced to character (as ``paste()`` then sees it)."""
    if obj is None:
        return []
    if isinstance(obj, RObject) and obj.type in (VECSXP, EXPRSXP):
        leaves = [x for x in (obj.value or []) if x is not None]
        types = {x.type for x in leaves if isinstance(x, RObject)}
        # coercion hierarchy: logical < integer < double < character
        if (
            types
            and types <= {LGLSXP, INTSXP, REALSXP}
            and not any("factor" in x.classes for x in leaves if isinstance(x, RObject))
        ):
            top = REALSXP if REALSXP in types else INTSXP if INTSXP in types else LGLSXP
            out: list[str | None] = []
            for x in leaves:
                vals = x.value.tolist()
                for val in vals:
                    if x.type != REALSXP and val == NA_INTEGER:
                        out.append(None)
                    elif top == LGLSXP:
                        out.append("TRUE" if val else "FALSE")
                    else:
                        out.extend(_as_character_vec(RObject(REALSXP if top == REALSXP else INTSXP,
                                                             np.array([val]))))  # fmt: skip
            return out
        res: list[str | None] = []
        for x in leaves:
            res.extend(_unlist(x))
        return res
    return _as_character_vec(obj)


def _flatten_list_cell(e: Any, limit: int) -> str:
    items = _unlist(e)[:limit]
    return "; ".join("NA" if s is None else s for s in items)


def _flatten_df_column(col: RObject, n: int) -> list[str]:
    """.utf8_repair_df()'s text rendering of a data-frame column."""
    names = _strvec(col.attr("names"))
    parts: list[list[str]] = []
    for k, sub in zip(names, col.value or [], strict=False):
        if isinstance(sub, RObject) and sub.type == VECSXP:
            if "data.frame" in sub.classes:
                rows = []
                for i in range(n):
                    leaves: list[str | None] = []
                    for c in sub.value or []:
                        leaves.extend(_unlist(_slice(c, i)))
                    rows.append(",".join("NA" if s is None else s for s in leaves[:10]))
            else:
                rows = [
                    ",".join("NA" if s is None else s for s in _unlist(e)[:10])
                    for e in (sub.value or [])
                ]
        else:
            rows = ["NA" if s is None else s for s in _as_character_vec(sub)]
        parts.append([f"{k}={r}" for r in rows])
    return ["; ".join(p[i] for p in parts) for i in range(n)] if parts else [""] * n


def _slice(obj: Any, i: int) -> Any:
    if isinstance(obj, RObject) and obj.type in (LGLSXP, INTSXP, REALSXP, CPLXSXP, STRSXP):
        return RObject(obj.type, obj.value[i : i + 1], dict(obj.attrs))
    if isinstance(obj, RObject) and obj.type == VECSXP:
        return obj.value[i] if i < len(obj.value) else None
    return obj


def _flatten_matrix(col: RObject) -> list[str]:
    nrow, ncol = (int(x) for x in col.attr("dim").value[:2])
    vals = _as_character_vec(RObject(col.type, col.value, {}))
    out = []
    for i in range(nrow):
        row = [vals[i + j * nrow] for j in range(ncol)]
        out.append("; ".join("NA" if s is None else s for s in row))
    return out


def r_frame_to_pandas(
    df: RObject, n_rows: float = math.inf, subset: bool = True, vctrs: bool = True
) -> pd.DataFrame:
    """Convert a deserialized data frame to pandas (``head(df, n_rows)``).

    Column attributes that matter downstream (haven labels, formats, classes)
    are kept in ``attrs["col_attrs"]``. With *subset* the columns go through
    ``head()``'s ``[`` as in R, which drops the attributes of classes without a
    ``[`` method (see :func:`_attrs_after_subset`); *vctrs* says whether the
    vctrs methods are available. Data-frame, matrix and list columns are
    flattened to text (as ``.utf8_repair_df()`` does) and keep no attributes.
    """
    n = _nrow(df)
    names = _strvec(df.attr("names"))
    cols = list(df.value or [])
    names = (names + [""] * len(cols))[: len(cols)]
    keep = n if not math.isfinite(n_rows) else max(0, min(n, int(n_rows)))
    series = []
    col_attrs: dict[str, dict[str, Any]] = {}
    for name, col in zip(names, cols, strict=True):
        s = _column_to_pandas(col, n)
        series.append(s.iloc[:keep].reset_index(drop=True))
        if isinstance(col, RObject) and not _is_flattened(col):
            kept = {a: _attr_value(col.attrs[a]) for a in _KEPT_ATTRS if a in col.attrs}
            if subset:
                kept = _attrs_after_subset(kept, col.classes, vctrs)
            kept.pop("levels", None)
            if kept:
                col_attrs["" if name is None else name] = kept
    out = pd.DataFrame(dict(enumerate(series))) if series else pd.DataFrame(index=range(keep))
    out.columns = pd.Index(["" if nm is None else nm for nm in names], dtype=object)
    if col_attrs:
        out.attrs["col_attrs"] = col_attrs
    return out
