"""A tiny writer of R's ``.rds`` files (XDR serialization, version 3, gzip), for test fixtures.

R is not needed to make the fixtures of the FAIR_repo tests: a data frame, a plain list and a
"fitted model" (a list with the class ``lm``) are all those tests ask for. ``readRDS()`` reads
what this writes (the format is R's own); :func:`metacheck.datacheck._files_rdata.read_rds`
reads it too, which the tests of this folder check.
"""

from __future__ import annotations

import gzip
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_NILVALUE = 254
_NA_INT = -(2**31)
_ASCII_MASK = 64  # CHARSXP "levels" bit: the string is ASCII
_UTF8_MASK = 8


def _int(n: int) -> bytes:
    return struct.pack(">i", n)


def _flags(sexp_type: int, *, attr: bool = False, obj: bool = False, tag: bool = False) -> bytes:
    return _int(sexp_type | (256 if obj else 0) | (512 if attr else 0) | (1024 if tag else 0))


def _charsxp(s: str | None) -> bytes:
    if s is None:
        return _int(9) + _int(-1)
    raw = s.encode("utf-8")
    levels = _ASCII_MASK if s.isascii() else _UTF8_MASK
    return _int(9 | (levels << 12)) + _int(len(raw)) + raw


def _symbol(name: str) -> bytes:
    return _int(1) + _charsxp(name)


def _attributes(attrs: Mapping[str, bytes]) -> bytes:
    out = b""
    for name, value in attrs.items():
        out += _flags(2, tag=True) + _symbol(name) + value
    return out + _int(_NILVALUE)


def _strsxp(values: Sequence[str | None]) -> bytes:
    return _int(16) + _int(len(values)) + b"".join(_charsxp(v) for v in values)


def _intsxp(values: Sequence[int | None]) -> bytes:
    body = b"".join(_int(_NA_INT if v is None else v) for v in values)
    return _int(13) + _int(len(values)) + body


def _realsxp(values: Sequence[float | None]) -> bytes:
    na = struct.pack(">Q", 0x7FF00000000007A2)
    body = b"".join(na if v is None else struct.pack(">d", v) for v in values)
    return _int(14) + _int(len(values)) + body


def _lglsxp(values: Sequence[bool | None]) -> bytes:
    body = b"".join(_int(_NA_INT if v is None else int(v)) for v in values)
    return _int(10) + _int(len(values)) + body


def _vector(values: Sequence[Any]) -> bytes:
    """An atomic R vector from a list of Python values of one type."""
    kinds = {type(v) for v in values if v is not None}
    if not kinds or kinds == {str}:
        return _strsxp(values)  # type: ignore[arg-type]
    if kinds == {bool}:
        return _lglsxp(values)  # type: ignore[arg-type]
    if kinds == {int}:
        return _intsxp(values)  # type: ignore[arg-type]
    if kinds <= {int, float}:
        return _realsxp([None if v is None else float(v) for v in values])
    raise TypeError(f"cannot write a vector of {kinds}")


def _list(
    items: Mapping[str, bytes], klass: Sequence[str] | None, extra: Mapping[str, bytes]
) -> bytes:
    attrs: dict[str, bytes] = {"names": _strsxp(list(items))}
    attrs.update(extra)
    if klass:
        attrs["class"] = _strsxp(list(klass))
    out = _flags(19, attr=True, obj=bool(klass)) + _int(len(items))
    out += b"".join(items.values())
    return out + _attributes(attrs)


def data_frame_bytes(columns: Mapping[str, Sequence[Any]]) -> bytes:
    """An R data frame (one vector per column) as a serialized object."""
    n = len(next(iter(columns.values()))) if columns else 0
    cols = {name: _vector(list(values)) for name, values in columns.items()}
    return _list(cols, ["data.frame"], {"row.names": _intsxp([_NA_INT, -n])})


def list_bytes(items: Mapping[str, Sequence[Any]], klass: Sequence[str] | None = None) -> bytes:
    """A named R list of vectors, with an optional class (``klass=["lm"]`` makes a "fitted model")."""
    return _list({k: _vector(list(v)) for k, v in items.items()}, klass, {})


def write_rds(path: str | Path, payload: bytes) -> Path:
    """Write a serialized object (:func:`data_frame_bytes`, :func:`list_bytes`) as an ``.rds`` file."""
    version = _int(3) + _int(0x00040200) + _int(0x00030500)
    native = b"UTF-8"
    stream = b"X\n" + version + _int(len(native)) + native + payload
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: the same bytes for the same table, so two fixtures with one table are identical files
    p.write_bytes(gzip.compress(stream, mtime=0))
    return p


def write_table_rds(path: str | Path, columns: Mapping[str, Sequence[Any]]) -> Path:
    """``saveRDS(data.frame(...), path)``."""
    return write_rds(path, data_frame_bytes(columns))


def write_model_rds(path: str | Path) -> Path:
    """``saveRDS(<a fitted model>, path)``: a list of class ``lm``, which FAIR_repo files as a result."""
    return write_rds(
        path,
        list_bytes({"coefficients": [-17.58, 3.93], "df.residual": [48]}, klass=["lm"]),
    )


def write_list_rds(path: str | Path) -> Path:
    """``saveRDS(list(a = 1, b = letters), path)``: no table, no model."""
    return write_rds(path, list_bytes({"a": [1.0], "b": [chr(97 + i) for i in range(26)]}))
