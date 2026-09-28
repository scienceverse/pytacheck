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
import os
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd


def checkout_spellings(checkout: Path, *others: str | Path) -> tuple[str, ...]:
    """The ways *checkout* is written in a result, longest first: its real path
    (as R's ``normalizePath()`` writes it) and each of *others* that reaches it
    through a symlink, each also with "/" (as R writes it on Windows)."""
    real = os.path.realpath(checkout)
    found = [real] + [os.path.abspath(o) for o in others if o and os.path.realpath(o) == real]
    both = dict.fromkeys(s for f in found for s in (f, Path(f).as_posix()))
    return tuple(sorted(both, key=len, reverse=True))


#: the checkout's directories that may be symlinks to a tree elsewhere (a submodule
#: shared between worktrees)
LINKABLE = ("upstream/metacheck",)


def linked_trees(checkout: Path, rels: tuple[str, ...] = LINKABLE) -> tuple[tuple[str, str], ...]:
    """Each of *rels* that is a symlink out of *checkout*, as ``(real path, rel)``,
    longest real path first. R was run on a checkout that holds these files itself,
    so a result spells them ``<repo>/<rel>``."""
    found = []
    for rel in rels:
        link = checkout / rel
        real = os.path.realpath(link)
        if link.is_symlink() and real != str(Path(os.path.realpath(checkout)) / rel):
            found.append((real, rel))
    return tuple(sorted(found, key=lambda t: len(t[0]), reverse=True))


def _in_json(s: str) -> bytes:
    """*s* as it is written inside a JSON string."""
    return orjson.dumps(s)[1:-1]


@dataclass(frozen=True)
class Spellings:
    """How a result writes the paths of a checkout, and what they become."""

    #: its real path: ``<repo>`` wherever it appears (as R writes goldens)
    roots: tuple[str, ...]
    #: the symlinks it was reached by: ``<repo>`` only as a whole path, not inside a
    #: longer name or a URL (a short link such as ``/w`` is common text)
    aliases: tuple[str, ...]
    #: the real paths of the trees it links to, and the ``<repo>/<rel>`` they become
    links: tuple[tuple[str, str], ...]

    @classmethod
    def of(cls, checkout: Path, *others: str | Path) -> Spellings:
        """The spellings of *checkout*, reached by each of *others* that is a symlink
        to it (see :func:`checkout_spellings` and :func:`linked_trees`)."""
        roots = checkout_spellings(checkout)
        aliases = tuple(s for s in checkout_spellings(checkout, *others) if s not in roots)
        links = tuple(
            (spelling, f"<repo>/{rel}")
            for real, rel in linked_trees(checkout)
            for spelling in checkout_spellings(Path(real))
        )
        return cls(roots, aliases, links)

    @cached_property
    def plain_json(self) -> tuple[tuple[bytes, bytes], ...]:
        """The plain substitutions (links, then roots) in canonical JSON."""
        pairs = [*self.links, *((r, "<repo>") for r in self.roots)]
        return tuple((_in_json(a), b.encode()) for a, b in pairs)

    @cached_property
    def aliases_json(self) -> tuple[bytes, ...]:
        return tuple(_in_json(a) for a in self.aliases)

    @cached_property
    def alias_pattern(self) -> re.Pattern[str] | None:
        if not self.aliases:
            return None
        either = "|".join(re.escape(a) for a in self.aliases)  # longest first
        return re.compile(rf"(?<![\w.-])(?:{either})(?![\w.-])")

    def apply(self, value: str) -> str:
        for a, b in self.links:
            value = value.replace(a, b)
        for r in self.roots:
            value = value.replace(r, "<repo>")
        if self.alias_pattern is not None:
            value = self.alias_pattern.sub("<repo>", value)
        return value


_CHECKOUT = Path(__file__).resolve().parent.parent
#: this checkout, also as reached through a symlink (the import path, the shell's)
_SPELLINGS = Spellings.of(
    _CHECKOUT, Path(__file__).absolute().parent.parent, os.environ.get("PWD", "")
)
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
    if any(a in raw for a in _SPELLINGS.aliases_json):  # rare: judged string by string
        return _portable_walk(value)
    if not any(a in raw for a, _ in _SPELLINGS.plain_json):
        return value
    for a, b in _SPELLINGS.plain_json:
        raw = raw.replace(a, b)
    return orjson.loads(raw)


def _portable_str(value: str) -> str:
    return _SPELLINGS.apply(value)


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
        # the object encodes itself, and its parts with the encoder it is given
        return x.to_canonical(_encode)  # type: ignore[no-any-return]
    return {"t": "other", "class": [type(x).__name__], "repr": repr(x)}
