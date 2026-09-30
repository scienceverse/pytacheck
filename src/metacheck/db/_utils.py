"""Shared helpers for :mod:`metacheck.db`: R idioms the database clients rely on.

These reproduce the base-R / httr2 / jsonlite behaviour metacheck's database
code depends on (``utils::URLencode()``, ``$`` partial matching on parsed
JSON, ``unlist() |> paste()``, list-element assignment of ``NULL``,
``httr2::resp_body_json()``'s content-type check, ``dplyr::bind_rows()`` of
one-row records) so the ports can stay close to the R code.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metacheck._r.base import as_character
from metacheck._r.regex import is_na

if TYPE_CHECKING:
    import httpx
    import pandas as pd

__all__ = [
    "DEFAULT_EMAIL",
    "NA_character",
    "NA_real",
    "TypedNA",
    "as_vector",
    "default_email",
    "online",
    "paste_unlist",
    "r_dollar",
    "records_frame",
    "resp_body_json",
    "resp_content_type",
    "unlist",
    "url_encode",
    "user_data_dir",
]

#: metacheck's ``email()`` default when no address has been set.
DEFAULT_EMAIL = "metacheck@scienceverse.org"

_URL_OK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
_URL_OK_UNRESERVED = _URL_OK | frozenset("][!$&'()*+,;=:/?@#")
_HEX = frozenset("0123456789abcdefABCDEF")


def default_email() -> str:
    """``email()`` as metacheck reads it: the configured address or the default."""
    from metacheck.config import email

    return email() or DEFAULT_EMAIL


def user_data_dir() -> Path:
    """``rappdirs::user_data_dir("metacheck", "scienceverse")`` for pytacheck.

    Refreshed databases (``rw_update()``, ``FLoRA_update()``) are stored here.
    ``PYTACHECK_DATA_DIR`` overrides the platform default.
    """
    override = os.environ.get("PYTACHECK_DATA_DIR")
    if override:
        return Path(override)
    import platformdirs

    return Path(platformdirs.user_data_dir("pytacheck", "scienceverse"))


def message(text: str) -> None:
    """R ``message()``: progress chatter on stderr, shown when ``verbose()`` is on."""
    import sys

    from metacheck.config import verbose

    if verbose():
        print(text, file=sys.stderr)


def online(url: str) -> bool:
    """``online(url)``: can the host be resolved? (lazy link to ``metacheck.utils``)."""
    try:
        from metacheck.utils import online as _online  # type: ignore[import-not-found]
    except ImportError:
        return _dns_online(url)
    return bool(_online(url))


def _dns_online(url: str, tries: int = 3, wait: float = 1.0) -> bool:
    """Fallback for ``online()``: a DNS lookup of the URL's host, retried."""
    import socket

    from metacheck.http import sleep

    host = url.split("://", 1)[1] if "://" in url else url
    host = host.split("/", 1)[0]
    for i in range(tries):
        try:
            socket.getaddrinfo(host, None)
            return True
        except OSError:
            if i < tries - 1:
                sleep(wait)
    return False


# ---------------------------------------------------------------------------
# utils::URLencode
# ---------------------------------------------------------------------------


def _has_escape(s: str) -> bool:
    return any(s[i] == "%" and s[i + 1] in _HEX and s[i + 2] in _HEX for i in range(len(s) - 2))


def url_encode(url: Any, reserved: bool = False, repeated: bool = False) -> str:
    """``utils::URLencode()`` for one string.

    As in R, a string that already contains a ``%XX`` escape is returned
    unchanged unless ``repeated=True``; every other character outside the
    allowed set is percent-encoded byte by byte (UTF-8, upper-case hex).
    """
    if is_na(url):
        return "NA"  # grepl() is FALSE for NA and strsplit()/paste() give "NA"
    s = url if isinstance(url, str) else _unlist_first_str(url)
    if not repeated and _has_escape(s):
        return s
    ok = _URL_OK if reserved else _URL_OK_UNRESERVED
    out: list[str] = []
    for ch in s:
        if ch in ok:
            out.append(ch)
        else:
            out.append("".join(f"%{b:02X}" for b in ch.encode("utf-8")))
    return "".join(out)


def _unlist_first_str(x: Any) -> str:
    vals = unlist(x)
    if len(vals) != 1:
        raise ValueError("values must be length 1")
    s = as_character(vals[0])
    return "NA" if s is None else s


# ---------------------------------------------------------------------------
# R list idioms on parsed JSON
# ---------------------------------------------------------------------------


def r_dollar(x: Any, name: str) -> Any:
    """``x$name`` on a list: exact match, else a unique partial (prefix) match."""
    if not isinstance(x, Mapping):
        return None
    if name in x:
        return x[name]
    hits = [k for k in x if isinstance(k, str) and k.startswith(name)]
    return x[hits[0]] if len(hits) == 1 else None


def unlist(x: Any) -> list[Any]:
    """The values of ``unlist(x)`` (recursive, ``NULL`` dropped), uncoerced."""
    out: list[Any] = []

    def walk(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, Mapping):
            for e in v.values():
                walk(e)
        elif isinstance(v, list | tuple):
            for e in v:
                walk(e)
        else:
            out.append(v)

    walk(x)
    return out


def _coerce_common(values: Sequence[Any]) -> list[str | None]:
    """``as.character()`` of the common type R's ``unlist()``/``c()`` would pick."""
    if any(isinstance(v, str) for v in values):
        return [as_character(v) for v in values]
    if any(isinstance(v, float) for v in values):
        return [as_character(float(v)) if not is_na(v) else None for v in values]
    if any(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return [as_character(int(v)) if not is_na(v) else None for v in values]
    return [as_character(v) for v in values]


def paste_unlist(x: Any, collapse: str = "; ") -> str:
    """``unlist(x) |> paste(collapse = collapse)`` (``NA`` becomes ``"NA"``)."""
    vals = _coerce_common(unlist(x))
    return collapse.join("NA" if v is None else v for v in vals)


def as_vector(x: Any) -> list[Any]:
    """An R vector argument as a Python list (``NULL`` -> empty, scalar -> length 1)."""
    if x is None:
        return []
    if isinstance(x, str) or not isinstance(x, Iterable):
        return [x]
    try:
        import pandas as pd

        if isinstance(x, pd.DataFrame):
            raise TypeError("expected a vector, not a data frame")
        if isinstance(x, pd.Series | pd.Index):
            return [None if is_na(v) else v for v in x.tolist()]
    except ImportError:  # pragma: no cover
        pass
    if isinstance(x, Mapping):
        return list(x.values())
    return list(x)


# ---------------------------------------------------------------------------
# httr2 response helpers
# ---------------------------------------------------------------------------


def resp_content_type(resp: httpx.Response) -> str:
    """``httr2::resp_content_type()``: the media type without parameters."""
    ct = str(resp.headers.get("content-type", ""))
    return ct.split(";", 1)[0].strip().lower()


def resp_body_json(resp: httpx.Response | None) -> Any:
    """``httr2::resp_body_json()``: parse a JSON body, checking its content type."""
    if resp is None:
        raise TypeError("`resp` must be an HTTP response object, not `NULL`.")
    ct = resp_content_type(resp)
    if ct != "application/json" and not ct.endswith("+json"):
        raise ValueError(
            f'Unexpected content type "{ct}".\n'
            '• Expecting type "application/json" or suffix "json".'
        )
    import orjson

    try:
        return orjson.loads(resp.content)
    except orjson.JSONDecodeError as exc:
        raise ValueError(f"lexical error: {exc}") from exc


# ---------------------------------------------------------------------------
# dplyr::bind_rows() of one-row records
# ---------------------------------------------------------------------------


class TypedNA:
    """A typed R missing value in a record (``NA_character_``, ``NA_real_``).

    ``bind_rows()`` refuses to combine ``NA_character_`` with numbers (only a
    logical ``NA`` combines with anything), and an all-``NA_real_`` column is
    double: :func:`records_frame` honours both, then stores the value as missing.
    """

    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def __repr__(self) -> str:
        return {"character": "NA_character_", "double": "NA_real_"}.get(self.kind, "NA")


NA_character = TypedNA("character")
NA_real = TypedNA("double")


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, str | int | float | bool)


def _column_dtype(values: Sequence[Any]) -> Any:
    present = [v for v in values if not (v is None or (isinstance(v, float) and v != v))]
    if not all(_is_scalar(v) for v in present):
        return object
    if not present:
        # all missing: NA_real_ (a float NaN) makes a double column, NA a logical one
        return "float64" if any(isinstance(v, float) for v in values) else "boolean"
    kinds = {type(v) for v in present}
    if kinds <= {bool}:
        return "boolean"
    if kinds <= {int}:
        return "Int64"
    if kinds <= {int, float}:
        return "float64"
    if kinds <= {str}:
        return "string"
    return object


def _r_type(v: Any) -> str | None:
    """The vctrs type family of a cell value (``None``: missing, combines with anything)."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, TypedNA):
        return "character" if v.kind == "character" else "numeric"
    if isinstance(v, str):
        return "character"
    if isinstance(v, bool | int | float):
        return "numeric"
    return "list"


_TYPE_NAMES = {"character": "character", "list": "list"}


def _type_name(v: Any) -> str:
    if isinstance(v, TypedNA):
        return v.kind
    if isinstance(v, bool):
        return "logical"
    if isinstance(v, int):
        return "integer" if abs(v) <= 2147483647 else "double"
    if isinstance(v, float):
        return "double"
    return _TYPE_NAMES.get(_r_type(v) or "", "list")


def _check_combinable(column: str, values: Sequence[Any]) -> None:
    """``bind_rows()`` refuses to combine character, numeric and list values in a column."""
    first: tuple[int, Any] | None = None
    for i, v in enumerate(values):
        t = _r_type(v)
        if t is None:
            continue
        if first is None:
            first = (i, v)
        elif t != _r_type(first[1]):
            raise TypeError(
                f"Can't combine `..{first[0] + 1}${column}` <{_type_name(first[1])}> and "
                f"`..{i + 1}${column}` <{_type_name(v)}>."
            )


def records_frame(
    records: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """``dplyr::bind_rows()`` of one-row records (dicts; missing keys -> ``NA``).

    Columns are unioned in order of first appearance and typed like R
    vectors: character -> ``string``, integer -> ``Int64``, double ->
    ``float64``, logical -> ``boolean``; anything else is an ``object`` column.
    As in ``bind_rows()``, a column mixing character, numeric and list values
    is an error.
    """
    import pandas as pd

    if columns is None:
        seen: dict[str, None] = {}
        for r in records:
            for k in r:
                seen.setdefault(k, None)
        columns = list(seen)
    data: dict[str, Any] = {}
    for c in columns:
        raw = [r.get(c) for r in records]
        _check_combinable(c, raw)
        values = [None if isinstance(v, TypedNA) else v for v in raw]
        dtype = _column_dtype(values)
        na_kinds = {v.kind for v in raw if isinstance(v, TypedNA)}
        if na_kinds and dtype in ("boolean", "Int64"):
            if all(v is None for v in values) and na_kinds == {"character"}:
                dtype = "string"
            elif "double" in na_kinds:
                dtype = "float64"
        if dtype == "float64":
            data[c] = pd.array(
                [float("nan") if v is None else float(v) for v in values], dtype="float64"
            )
        elif dtype is object:
            import numpy as np

            arr = np.empty(len(values), dtype=object)
            for i, v in enumerate(values):  # element-wise: keeps list cells intact
                arr[i] = v
            data[c] = pd.Series(arr, dtype=object, copy=False).array
        else:
            data[c] = pd.array(values, dtype=dtype)
    return pd.DataFrame(data, columns=list(columns), index=pd.RangeIndex(len(records)))
