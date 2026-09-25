"""OSF API helpers (port of ``R/archive-osf-helpers.R``).

OSF API responses are kept as parsed JSON: a single resource is a ``dict``
and a listing a list of resource dicts (R keeps the ``jsonlite``-simplified
data frame). The ``_osf_*_data()`` functions reproduce R's column semantics
over such records, including how ``a %||% b`` behaves on a simplified
listing: a field counts as present when *any* record in the listing has it.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from pytacheck._r import bind_rows, grepl, is_na

if TYPE_CHECKING:
    import httpx

__all__ = ["osf_pat", "osf_user_projects"]

# pandas dtypes of the columns the OSF helpers build (R: chr / lgl / int / dbl)
_DTYPES: dict[str, str] = {
    "osf_id": "string",
    "name": "string",
    "description": "string",
    "osf_type": "string",
    "public": "boolean",
    "category": "string",
    "registration": "boolean",
    "preprint": "boolean",
    "self": "string",
    "children": "string",
    "files": "string",
    "parent": "string",
    "project": "string",
    "provider": "string",
    "kind": "string",
    "filetype": "string",
    "size": "float64",
    "downloads": "Int64",
    "path": "string",
    "download_url": "string",
    "doi": "string",
    "version": "Int64",
    "is_published": "boolean",
    "date_created": "string",
    "date_modified": "string",
    "primary_file": "string",
    "orcid": "string",
    "osf_url": "string",
}


# ---------------------------------------------------------------------------
# record helpers
# ---------------------------------------------------------------------------


def _get(rec: Any, *path: str) -> Any:
    """``rec$a$b$c`` on parsed JSON (``None`` when any step is missing)."""
    cur = rec
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _is_scalar(x: Any) -> bool:
    return isinstance(x, str | int | float | bool)


def _scalar(x: Any) -> Any:
    if isinstance(x, list | tuple):
        return x[0] if len(x) == 1 else (x or None)
    return x


def _records(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, dict):
        return [data] if data else []
    return [r for r in data if isinstance(r, dict)]


def _dollar_key(keys: Sequence[str], name: str) -> str | None:
    """The element R's ``x$name`` selects: an exact name, else a unique prefix match."""
    if name in keys:
        return name
    hits = [k for k in keys if k.startswith(name)]
    return hits[0] if len(hits) == 1 else None


class _Cols:
    """Column extraction with ``%||%`` evaluated as R sees the data.

    ``$`` partially matches names in R (``x$root`` finds ``root_folder`` when
    there is no ``root``), for named lists and data frames alike. A single
    resource is a named list: ``x$a$b`` is ``NULL`` when any step is missing
    or JSON ``null``. A listing is a jsonlite data frame: a column exists
    when *any* record has the key (even with a ``null`` value, which becomes
    ``NA``), and ``$`` into a nested column whose values are all
    ``null``/scalars (an atomic vector, not a data frame) is an R error.
    """

    def __init__(
        self,
        records: list[dict[str, Any]],
        context: list[dict[str, Any]] | None = None,
        listing: bool = True,
    ):
        self.records = records
        self.context = records if context is None else context
        self.listing = listing

    def resolve(self, path: Sequence[str]) -> tuple[str, ...] | None:
        """The concrete keys ``x$p1$p2...`` selects, or ``None`` when it is ``NULL``."""
        keys: list[str] = []
        if not self.listing:
            cur: Any = self.context[0] if self.context else None
            for name in path:
                if cur is None or isinstance(cur, list):
                    return None
                if not isinstance(cur, dict):
                    raise TypeError("$ operator is invalid for atomic vectors")
                key = _dollar_key(list(cur), name)
                if key is None:
                    return None
                keys.append(key)
                cur = cur[key]
            return None if cur is None else tuple(keys)
        level: list[Any] = list(self.context)
        for depth, name in enumerate(path):
            if depth > 0 and not any(isinstance(v, dict) for v in level):
                # `$name` on a column that is not a data frame
                if all(v is None or _is_scalar(v) for v in level):
                    raise TypeError("$ operator is invalid for atomic vectors")
                return None  # a list column: `$` gives NULL
            parents = [v for v in level if isinstance(v, dict)]
            union = list(dict.fromkeys(k for v in parents for k in v))
            key = _dollar_key(union, name)
            if key is None:
                return None
            keys.append(key)
            level = [v.get(key) for v in parents]
        return tuple(keys)

    def present(self, path: Sequence[str]) -> bool:
        return self.resolve(path) is not None

    def values(self, path: Sequence[str]) -> list[Any] | None:
        """``x$path`` for every record (``None`` where missing), or ``None`` if ``NULL``."""
        keys = self.resolve(path)
        if keys is None:
            return None
        return [_get(r, *keys) for r in self.records]

    def first(self, *paths: Sequence[str], default: Any = None, drop: bool = False) -> Any:
        """``x$p1 %||% x$p2 %||% default``; with *drop*, ``None`` when all are absent."""
        for path in paths:
            vals = self.values(path)
            if vals is not None:
                return [_scalar(v) for v in vals]
        if drop:
            return None
        return [default] * len(self.records)


def _frame(columns: dict[str, Any], n: int) -> pd.DataFrame:
    """A data frame with R's column types; ``None`` columns are dropped (R ``data.frame(x = NULL)``)."""
    out: dict[str, pd.Series] = {}
    for name, values in columns.items():
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values] * n
        dtype = _DTYPES.get(name, object)
        vals = [
            None if (v is not None and not isinstance(v, list | dict) and is_na(v)) else v
            for v in values
        ]
        if dtype == "string":
            vals = [None if v is None else (v if isinstance(v, str) else _as_chr(v)) for v in vals]
        elif dtype in ("float64", "Int64"):
            vals = [_as_num(v) for v in vals]
        elif dtype == "boolean":
            vals = [None if v is None else bool(v) for v in vals]
        try:
            out[name] = pd.Series(vals, dtype=dtype)
        except (TypeError, ValueError):
            out[name] = pd.Series(vals, dtype=object)
    return pd.DataFrame(out, index=pd.RangeIndex(n))


def _as_chr(v: Any) -> str:
    from pytacheck._r import as_character

    return as_character(v) or "NA"


def _as_num(v: Any) -> Any:
    if v is None or isinstance(v, bool):
        return None if v is None else int(v)
    if isinstance(v, int | float):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# httr2's parse_content_type() pattern (type, subtype, suffix)
_CONTENT_TYPE = (
    r"^(application|audio|font|example|image|message|model|multipart|text|video)/"
    r"((?:(?:vnd|prs|x)\.)?(?:[^+;])+)(?:\+((?:[^;])+))?(?:;((?:.)+))?$"
)


def _resp_body_json(resp: httpx.Response) -> Any:
    """``httr2::resp_body_json(resp)``: the body parsed as jsonlite parses it; raises on failure.

    The media type must be ``application/json`` or carry a ``+json`` suffix
    (``application/vnd.api+json``); anything else raises. The body is read as
    ``resp_body_string(resp, "UTF-8")`` does, whatever charset the response
    names: up to the first NUL byte, and bytes that are not valid UTF-8 give
    ``NA``, which jsonlite refuses. The text is then parsed by
    :func:`_from_json`.
    """
    from pytacheck._r import regexec

    header = resp.headers.get("content-type")
    media = None if header is None else header.split(";", 1)[0].strip()
    m = regexec(_CONTENT_TYPE, media, perl=True) if media is not None else []
    base = f"{m[1]}/{m[2]}" if m else ""
    suffix = (m[3] or "") if m else ""
    if base != "application/json" and suffix != "json":
        shown = "NA" if media is None else media
        raise ValueError(
            f'Unexpected content type "{shown}".\n'
            '* Expecting type "application/json" or suffix "json".'
        )
    content = resp.content
    if not content:
        raise ValueError("Can't retrieve empty body.")
    try:
        text = content.split(b"\x00", 1)[0].decode("utf-8")
    except UnicodeDecodeError:  # R: iconv() gives NA, and fromJSON(NA) fails
        raise ValueError("missing value where TRUE/FALSE needed") from None
    return _from_json(text)


# yajl (jsonlite 2.0.0) reads what json.loads() refuses or reads differently:
# comments between tokens (an unterminated ``/*`` runs to the end), \v and \f
# as whitespace, an escaped NUL (ends the string), a high surrogate escape
# (combined with any following \u escape, else "?"). Strings are matched
# first so that comment markers inside them are kept.
_JSON_TOKENS = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|/\*.*?(?:\*/|\Z)|//[^\n]*|[\v\f]', re.S)
_JSON_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|.)", re.S)
_JSON_SPECIAL = re.compile(r"\\u(?:[dD][89abAB]|0000)")
_INT_MAX = 2147483647


def _from_json(text: str) -> Any:
    """``jsonlite::fromJSON(text, simplifyVector = FALSE)``; raises what jsonlite refuses.

    As yajl does, a leading byte-order mark is dropped with a warning,
    ``NaN`` and ``Infinity`` are refused, comments are skipped, integers
    outside R's integer range become doubles and an escaped NUL ends its
    string. jsonlite keeps both values of a repeated key, of which ``$``
    finds the first: the first is the one kept here.
    """
    if text.startswith("\ufeff"):
        warnings.warn("JSON string contains (illegal) UTF8 byte-order-mark!", stacklevel=3)
        text = text[1:]
    if _JSON_SPECIAL.search(text) is None:
        try:  # plain JSON: no comments, yajl-only whitespace or odd escapes
            return _json_loads(text)
        except (ValueError, RecursionError):
            pass
    nul = False

    def token(m: re.Match[str]) -> str:
        nonlocal nul
        tok = m.group(0)
        if tok[0] != '"':
            return " "
        if "\\u" not in tok:
            return tok
        body, has_nul = _yajl_escapes(tok[1:-1])
        nul = nul or has_nul
        return f'"{body}"'

    value = _json_loads(_JSON_TOKENS.sub(token, text))
    return _cut_nul(value) if nul else value


def _json_loads(text: str) -> Any:
    import json

    def constant(name: str) -> Any:
        raise ValueError(f"lexical error: invalid char in json text ({name})")

    def parse_int(s: str) -> int | float:
        v = int(s)
        return v if -_INT_MAX <= v <= _INT_MAX else float(s)

    def first_wins(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k not in out:
                out[k] = v
        return out

    return json.loads(
        text, parse_constant=constant, parse_int=parse_int, object_pairs_hook=first_wins
    )


def _yajl_escapes(body: str) -> tuple[str, bool]:
    """A string token's ``\\u`` escapes rewritten as yajl decodes them.

    Returns the body (still JSON-escaped) and whether it holds a NUL.
    """
    out: list[str] = []
    nul = False
    pos = 0
    skip_to = -1
    for m in _JSON_ESCAPE.finditer(body):
        if m.start() < skip_to or m.group(1) is None:
            continue
        cp = int(m.group(1), 16)
        out.append(body[pos : m.start()])
        end = m.end()
        if cp & 0xFC00 == 0xD800:
            nxt = re.match(r"\\u([0-9a-fA-F]{4})", body[end:])
            if nxt is None:
                out.append("?")
            else:  # yajl combines it with any \u escape, low surrogate or not
                low = int(nxt.group(1), 16)
                cp = ((cp & 0x3F) << 10) | ((((cp >> 6) & 0xF) + 1) << 16) | (low & 0x3FF)
                end += nxt.end()
                cp -= 0x10000
                out.append(f"\\u{0xD800 + (cp >> 10):04x}\\u{0xDC00 + (cp & 0x3FF):04x}")
        else:
            nul = nul or cp == 0
            out.append(m.group(0))
        pos = skip_to = end
    out.append(body[pos:])
    return "".join(out), nul


def _cut_nul(x: Any) -> Any:
    """Strings of parsed JSON ended at their first NUL, as R's strings are."""
    if isinstance(x, str):
        return x.split("\x00", 1)[0]
    if isinstance(x, list):
        return [_cut_nul(v) for v in x]
    if isinstance(x, dict):
        out: dict[str, Any] = {}
        for k, v in x.items():
            key = k.split("\x00", 1)[0]
            if key not in out:
                out[key] = _cut_nul(v)
        return out
    return x


def _data_of(body: Any) -> Any:
    """R ``body$data`` on a parsed JSON body (``NULL`` unless it is an object)."""
    return body.get("data") if isinstance(body, dict) else None


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def _osf_headers(req: dict[str, Any] | None = None, pat: str | None = None) -> dict[str, Any]:
    """Port of R/archive-osf-helpers.R::.osf_headers(): add OSF headers to a request.

    *req* is a request spec as used by :func:`pytacheck.http.batch_query`
    (``{"method", "url", "headers", ...}``); a new spec is returned with
    ``User-Agent: metacheck``, ``Accept: application/vnd.api+json`` and, when
    a token is set (*pat*, default :func:`osf_pat`), ``Authorization: Bearer``.
    Usable directly as ``batch_query(..., req_func=_osf_headers)``.
    """
    spec = dict(req or {})
    headers = dict(spec.get("headers") or {})
    headers["User-Agent"] = "metacheck"
    headers["Accept"] = "application/vnd.api+json"
    token = osf_pat() if pat is None else pat
    if token:
        headers["Authorization"] = f"Bearer {token}"
    spec["headers"] = headers
    return spec


def osf_pat(pat: str | None = None) -> str:
    """Port of R/archive-osf-helpers.R::osf_pat(): get or set the OSF personal access token.

    Without an argument, returns the token set for this session, else the
    ``OSF_PAT`` environment variable (``""`` when neither is set). With a
    string, sets it for the rest of the session. Create a token (scope
    ``osf.full_read``) at https://osf.io/settings/tokens.
    """
    from pytacheck.utils import get_option, options

    if pat is None:
        value = get_option("metacheck.osf.pat")
        return value if value is not None else os.environ.get("OSF_PAT", "")
    if not isinstance(pat, str):
        raise ValueError("Set osf_pat with a single string containing your OSF token")
    options({"metacheck.osf.pat": pat})
    return pat


def _osf_pat_validate(osf_pat: str | None = None) -> bool:
    """Port of R/archive-osf-helpers.R::.osf_pat_validate(): is ``OSF_PAT`` usable?

    Fetches a public preprint anonymously and with the token; a token that is
    refused (401/403) where anonymous access works is cleared from
    ``OSF_PAT`` for this session, with a warning.
    """
    from pytacheck import http
    from pytacheck.utils import online

    token = os.environ.get("OSF_PAT", "") if osf_pat is None else osf_pat
    if token == "":
        return False
    if not online("api.osf.io"):
        return False
    probe = "https://api.osf.io/v2/preprints/khbvy/"
    headers = {"User-Agent": "metacheck", "Accept": "application/vnd.api+json"}

    def status(extra: dict[str, str]) -> int | None:
        try:
            resp = http.request("GET", probe, headers={**headers, **extra}, max_tries=1)
        except Exception:
            return None
        return None if resp is None else resp.status_code

    if status({}) != 200:
        warnings.warn(
            "The OSF_PAT could not be validated because the test file is not avilable; "
            "the OSF may be down.",
            stacklevel=2,
        )
        return False
    sc_auth = status({"Authorization": f"Bearer {token}"})
    if sc_auth == 200:
        return True
    if sc_auth in (401, 403):
        warnings.warn(
            "The current OSF_PAT blocks access to public files. Clearing OSF_PAT for this "
            "session. Update or remove it in .Renviron.",
            stacklevel=2,
        )
        os.environ["OSF_PAT"] = ""
    return False


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------


def _osf_info(osf_id: Any, pb: Any = None, cache: bool = False) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::.osf_info(): look up OSF IDs or URLs.

    One API request per valid ID (5-character GUIDs and view-only links via
    ``/guids/``, anything else via ``/files/``), in batches. Rows come back
    GUIDs first, then file IDs, then invalid inputs (``osf_type = "invalid"``).
    With *cache*, per-ID results are reused from the on-disk listing cache.
    """
    from pytacheck.archives import _spinner

    with _spinner(pb) as bar:
        return _osf_info_ids(osf_id, bar, cache)


def _osf_info_ids(osf_id: Any, pb: Any, cache: bool) -> pd.DataFrame:
    """The body of :func:`_osf_info` (R: after ``pb`` is set up)."""
    from pytacheck import http
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from pytacheck.archives.osf import osf_check_id
    from pytacheck.utils import get_option

    ids = [osf_id] if isinstance(osf_id, str) or osf_id is None else list(osf_id)
    valid = list(osf_check_id(ids)) if ids else []

    if all(v is None for v in valid):
        return _frame({"osf_id": ids, "osf_type": "invalid"}, len(ids))

    osf_api = get_option("metacheck.osf.api")
    is_guid = [v is not None and len(v) == 5 for v in valid]
    is_vo = [v is not None and bool(grepl(r"/?\?\s*view_only=", v)) for v in valid]
    guid_ids = [v for v, g, o in zip(valid, is_guid, is_vo, strict=True) if g or o]
    wb_ids = [
        v
        for v, g, o in zip(valid, is_guid, is_vo, strict=True)
        if not g and not o and v is not None
    ]
    all_urls = [f"{osf_api}/guids/{g}" for g in guid_ids] + [f"{osf_api}/files/{w}" for w in wb_ids]
    all_ids = guid_ids + wb_ids

    results: list[pd.DataFrame | None] = [None] * len(all_ids)
    need_fetch = [True] * len(all_ids)
    if cache is True:
        for i, ident in enumerate(all_ids):
            hit = _repo_info_cache_get("osf", ident)
            if hit is not None:
                results[i] = hit
                need_fetch[i] = False

    fetch_idx = [i for i, need in enumerate(need_fetch) if need]
    if fetch_idx:
        resps = http.batch_query(
            [all_urls[i] for i in fetch_idx], msg="OSF Info", req_func=_osf_headers
        )
        for i, resp in zip(fetch_idx, resps, strict=True):
            ident = all_ids[i]
            try:
                results[i] = _osf_parse_response(resp, pb=pb, osf_id=ident)
            except Exception:
                results[i] = _frame({"osf_id": [ident], "osf_type": "error"}, 1)
            if cache is True and _repo_info_ok(results[i]):
                _repo_info_cache_put("osf", ident, results[i])

    info_table = bind_rows(results)
    if len(info_table) != len(all_ids):
        # R: `info_table$osf_id <- all_ids`
        n = len(all_ids)
        raise ValueError(
            f"replacement has {n} row{'' if n == 1 else 's'}, data has {len(info_table)}"
        )
    info_table = info_table.copy()
    info_table["osf_id"] = pd.Series(all_ids, dtype="string")

    invalid = [o for o, v in zip(ids, valid, strict=True) if v is None]
    if invalid:
        info_table = bind_rows(
            [info_table, _frame({"osf_id": invalid, "osf_type": "invalid"}, len(invalid))]
        )
    return info_table


def _osf_parse_response(
    resp: httpx.Response | list[Any] | dict[str, Any] | None,
    pb: Any = None,  # noqa: ARG001 - R signature (progress bar)
    osf_id: str | None = None,
) -> pd.DataFrame | None:
    """Port of R/archive-osf-helpers.R::.osf_parse_response(): API data -> a table.

    *resp* is an HTTP response, or already-fetched records (a listing from
    :func:`~pytacheck.archives.osf.osf_get_all_pages`). Error statuses give a
    one-row table typed ``private`` (401/403), ``too many requests`` (429) or
    ``unfound`` (others, with a warning). An empty listing gives ``None``.
    """
    ident = osf_id
    single = False
    if isinstance(resp, list) and getattr(resp, "osf_error", None) is None:
        # R: a listing arrives as a (possibly empty) data frame
        all_data: Any = resp
        if len(all_data) == 0:
            return None
    elif isinstance(resp, list | dict):
        # R: a single resource (a named list) or a failed listing (an empty
        # list() carrying osf_error) is not a data frame, so it is treated as
        # an httr2 response and httr2::resp_status() rejects it
        what = "an empty list" if len(resp) == 0 else "a list"
        raise TypeError(f"`resp` must be an HTTP response object, not {what}.")
    else:
        if resp is None or not hasattr(resp, "status_code"):
            raise TypeError("`resp` must be an HTTP response object")
        sc = resp.status_code
        if sc == 200:
            content = _resp_body_json(resp)
            all_data = content.get("data") if isinstance(content, dict) else None
            single = isinstance(all_data, dict)
        elif sc in (401, 403):
            return _frame({"osf_id": [ident], "osf_type": "private", "public": False}, 1)
        elif sc == 429:
            warnings.warn("Too many requests", stacklevel=2)
            return _frame({"osf_id": [ident], "osf_type": "too many requests"}, 1)
        else:
            who = "An OSF resource" if ident is None else ident
            if sc == 410:
                msg = (
                    f"{who} has been deleted or withdrawn from the OSF (HTTP 410), so its "
                    "files cannot be downloaded."
                )
            else:
                see = "" if ident is None else f" and that you can see it at https://osf.io/{ident}"
                msg = (
                    f"{who} could not be found on the OSF (HTTP {sc}). Check the ID is spelled "
                    f"correctly{see}. A private project needs an OSF token; see ?osf_pat"
                )
            warnings.warn(msg, stacklevel=2)
            return _frame({"osf_id": [ident], "osf_type": "unfound"}, 1)

    records = [all_data] if single else _records(all_data)
    if not records:
        return pd.DataFrame()

    builders = {
        "nodes": _osf_node_data,
        "files": _osf_file_data,
        "preprints": _osf_preprint_data,
        "registrations": _osf_reg_data,
        "users": _osf_user_data,
    }
    # R: lapply(seq_along(all_data$id), ...) -- no `id` at all means no rows
    if not any(r.get("id") is not None if single else "id" in r for r in records):
        return pd.DataFrame()
    type_column = single or any("type" in r for r in records)

    frames: list[pd.DataFrame] = []
    i = 0
    while i < len(records):
        otype = records[i].get("type")
        if otype is None:
            # R: `if (osf_type == "nodes")` with NULL (no type) or NA (a listing
            # where other records have one)
            raise TypeError(
                "missing value where TRUE/FALSE needed"
                if type_column and not single
                else "argument is of length zero"
            )
        j = i + 1
        while j < len(records) and records[j].get("type") == otype:
            j += 1
        run = records[i:j]
        build = builders.get(otype) if isinstance(otype, str) else None
        if build is None:
            for _ in run:
                warnings.warn(
                    f"{'NA' if ident is None else ident} has unknown type: {otype}", stacklevel=2
                )
                frames.append(_frame({"osf_id": [ident], "osf_type": "unknown"}, 1))
        else:
            data: Any = run[0] if single else run
            if build is _osf_file_data:
                frames.append(_osf_file_data(data, _context=records, _per_row=True))
            else:
                frames.append(build(data, _context=records))
        i = j
    return bind_rows(frames)


# ---------------------------------------------------------------------------
# structuring API data
# ---------------------------------------------------------------------------


def _osf_node_data(data: Any, _context: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::.osf_node_data(): node records -> a table."""
    recs = _records(data)
    if not recs:
        return pd.DataFrame()
    c = _Cols(recs, _context, listing=not isinstance(data, dict))
    return _frame(
        {
            "osf_id": c.first(("id",)),
            "name": c.first(("attributes", "title")),
            "description": c.first(("attributes", "description")),
            "osf_type": c.first(("type",)),
            "public": c.first(("attributes", "public")),
            "category": c.first(("attributes", "category")),
            "registration": c.first(("attributes", "registration")),
            "preprint": c.first(("attributes", "preprint")),
            "self": c.first(("links", "self")),
            "children": c.first(("relationships", "children", "links", "related", "href")),
            "files": c.first(("relationships", "files", "links", "related", "href")),
            "parent": c.first(("relationships", "parent", "data", "id")),
            "project": c.first(("relationships", "root", "data", "id")),
        },
        len(recs),
    )


def _osf_file_data(
    data: Any,
    _context: list[dict[str, Any]] | None = None,
    _per_row: bool = False,
) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::.osf_file_data(): file/folder records -> a table.

    Guesses ``filetype`` from each file's extension, names unnamed folders
    after their provider, and gives provider root folders their root-folder
    ID. (Called on several records at once, R names *every* unnamed row after
    its provider once any folder is present; that is reproduced.)
    """
    recs = _records(data)
    if not recs:
        return pd.DataFrame()
    c = _Cols(recs, _context, listing=not isinstance(data, dict))
    n = len(recs)
    cols: dict[str, Any] = {
        "osf_id": c.first(("id",)),
        "name": c.first(("attributes", "name")),
        "description": c.first(("attributes", "description")),
        "provider": c.first(("attributes", "provider")),
        "osf_type": c.first(("type",)),
        "kind": c.first(("attributes", "kind")),
        "filetype": [None] * n,
        "public": c.first(("attributes", "public")),
        "category": c.first(("attributes", "category")),
        "size": c.first(("attributes", "size")),
        "downloads": c.first(("attributes", "extra", "downloads")),
        "path": c.first(("attributes", "materialized_path"), ("attributes", "path")),
        "self": c.first(("links", "self")),
        "files": c.first(("relationships", "files", "links", "related", "href")),
        "download_url": c.first(("links", "download")),
        "parent": c.first(
            ("relationships", "parent_folder", "data", "id"),
            ("relationships", "target", "data", "id"),
        ),
        "project": c.first(
            ("relationships", "target", "data", "id"), ("relationships", "root", "data", "id")
        ),
    }
    kind = cols["kind"]
    is_file = [k == "file" for k in kind]
    if any(is_file):
        from pytacheck.fileinfo.category import filetype

        names = [nm for nm, f in zip(cols["name"], is_file, strict=True) if f]
        types = list(filetype(names))
        it = iter(types)
        cols["filetype"] = [next(it) if f else None for f in is_file]

    folders = [i for i, k in enumerate(kind) if k == "folder"]
    if folders:
        name = list(cols["name"])
        provider = cols["provider"]
        rows = folders if _per_row else range(n)
        for i in rows:
            if name[i] is None:
                name[i] = provider[i]
        cols["name"] = name
        root_ids = c.values(("relationships", "root_folder", "data", "id"))
        if root_ids is not None:
            osf_ids = list(cols["osf_id"])
            for i in folders:
                if root_ids[i] is not None:
                    osf_ids[i] = root_ids[i]
            cols["osf_id"] = osf_ids
    return _frame(cols, n)


def _osf_preprint_data(data: Any, _context: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::.osf_preprint_data(): preprint records -> a table."""
    recs = _records(data)
    if not recs:
        return pd.DataFrame()
    c = _Cols(recs, _context, listing=not isinstance(data, dict))
    return _frame(
        {
            "osf_id": c.first(("id",)),
            "name": c.first(("attributes", "title"), drop=True),
            "description": c.first(("attributes", "description")),
            "osf_type": c.first(("type",)),
            "provider": c.first(("relationships", "provider", "data", "id"), drop=True),
            "public": c.first(("attributes", "public")),
            "doi": c.first(("attributes", "doi")),
            "version": c.first(("attributes", "version")),
            "is_published": c.first(("attributes", "is_published")),
            "date_created": c.first(("attributes", "date_created")),
            "date_modified": c.first(("attributes", "date_modified")),
            "self": c.first(("links", "self")),
            "parent": c.first(("relationships", "node", "data", "id")),
            "project": c.first(("relationships", "root", "data", "id")),
            "primary_file": c.first(("relationships", "primary_file", "links", "related", "href")),
        },
        len(recs),
    )


def _osf_reg_data(data: Any, _context: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::.osf_reg_data(): registration records -> a table."""
    recs = _records(data)
    if not recs:
        return pd.DataFrame()
    c = _Cols(recs, _context, listing=not isinstance(data, dict))
    return _frame(
        {
            "osf_id": c.first(("id",)),
            "name": c.first(("attributes", "title")),
            "osf_type": c.first(("type",)),
            "category": "registration",
            "registration": c.first(("attributes", "registration")),
            "preprint": c.first(("attributes", "preprint")),
            "self": c.first(("links", "self")),
            "children": c.first(("relationships", "children", "links", "related", "href")),
            "files": c.first(("relationships", "files", "links", "related", "href")),
            "parent": c.first(("relationships", "registered_from", "data", "id")),
            "project": c.first(("relationships", "root", "data", "id")),
        },
        len(recs),
    )


def _osf_user_data(data: Any, _context: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::.osf_user_data(): user records -> a table."""
    recs = _records(data)
    if not recs:
        return pd.DataFrame()
    c = _Cols(recs, _context, listing=not isinstance(data, dict))
    return _frame(
        {
            "osf_id": c.first(("id",)),
            "name": c.first(("attributes", "full_name")),
            "osf_type": c.first(("type",)),
            "public": True,
            "orcid": c.first(("attributes", "social", "orcid")),
            "self": c.first(("links", "self")),
        },
        len(recs),
    )


# ---------------------------------------------------------------------------
# users and their projects
# ---------------------------------------------------------------------------


def _empty_projects() -> pd.DataFrame:
    return _frame({"osf_id": [], "name": [], "category": [], "public": [], "osf_url": []}, 0)


def osf_user_projects(user_id: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-osf-helpers.R::osf_user_projects(): a user's projects.

    Lists every node the user contributes to and reduces components to the
    project that contains them. Returns ``osf_id``, ``name``, ``category``,
    ``public`` and ``osf_url``, one row per project (zero rows when nothing
    could be listed). Pass the table (or a filtered subset) to
    :func:`~pytacheck.archives.osf.osf_file_download`.
    """
    from pytacheck import http
    from pytacheck.archives.osf import _osf_err, osf_check_id, osf_get_all_pages
    from pytacheck.utils import get_option

    ids = [user_id] if isinstance(user_id, str) or user_id is None else list(user_id)
    checked = list(dict.fromkeys(v for v in (osf_check_id(ids) if ids else []) if v is not None))
    if not checked:
        return _empty_projects()

    osf_api = get_option("metacheck.osf.api")
    nodes = osf_get_all_pages(f"{osf_api}/users/{checked[0]}/nodes/")
    if _osf_err(nodes) is not None or len(nodes) == 0:
        return _empty_projects()

    info = _osf_parse_response(nodes, pb=pb)
    if info is None or len(info) == 0:
        return _empty_projects()

    osf_ids = info["osf_id"].tolist()
    project = info["project"].tolist() if "project" in info else [None] * len(info)
    root = [o if is_na(p) else p for p, o in zip(project, osf_ids, strict=True)]
    root = [None if is_na(r) else r for r in root]

    own = [i for i, (o, r) in enumerate(zip(osf_ids, root, strict=True)) if not is_na(o) and o == r]
    own_ids = [osf_ids[i] for i in own]
    missing_root = [r for r in dict.fromkeys(r for r in root if r is not None) if r not in own_ids]

    def col(name: str) -> list[Any]:
        if name not in info:
            return [None] * len(own)
        values = info[name].tolist()
        return [None if is_na(values[i]) else values[i] for i in own]

    out_ids = own_ids + missing_root
    names = col("name") + [None] * len(missing_root)
    cats = col("category") + [None] * len(missing_root)
    public = col("public") + [None] * len(missing_root)

    keep: list[int] = []
    seen: set[str] = set()
    for i, o in enumerate(out_ids):
        if is_na(o) or o in seen:
            continue
        seen.add(o)
        keep.append(i)
    out_ids = [out_ids[i] for i in keep]
    names = [names[i] for i in keep]
    cats = [cats[i] for i in keep]
    public = [public[i] for i in keep]

    unnamed = [i for i, nm in enumerate(names) if nm is None]
    if unnamed:
        urls = [f"{osf_api}/nodes/{out_ids[i]}/" for i in unnamed]
        resps = http.batch_query(urls, msg=None, req_func=_osf_headers)
        for i, resp in zip(unnamed, resps, strict=True):
            if resp is None:
                continue
            if resp.status_code != 200:
                if resp.status_code in (401, 403):
                    public[i] = False
                continue
            try:
                body = _resp_body_json(resp)
            except Exception:  # R: tryCatch(..., error = \(e) NULL) -> next
                body = None
            if body is None:
                continue
            att = _get(_data_of(body), "attributes") or {}
            names[i] = att.get("title")
            cats[i] = att.get("category")
            public[i] = att.get("public")

    return _frame(
        {
            "osf_id": out_ids,
            "name": names,
            "category": cats,
            "public": public,
            "osf_url": [f"https://osf.io/{o}" for o in out_ids],
        },
        len(out_ids),
    )


def _osf_user_nodes(user_id: Any, pb: Any = None) -> list[str]:
    """Port of R/archive-osf-helpers.R::.osf_user_nodes(): a user's project IDs."""
    return [str(x) for x in osf_user_projects(user_id, pb=pb)["osf_id"].tolist()]


def _osf_expand_user_ids(osf_id: Sequence[str], pb: Any = None) -> list[str]:
    """Port of R/archive-osf-helpers.R::.osf_expand_user_ids(): users -> their projects.

    Any 5-character ID that the OSF reports as a *user* is replaced by that
    user's projects; everything else passes through, in order, then the
    expansions are appended and duplicates dropped.
    """
    from pytacheck._r import plural
    from pytacheck.archives import _message
    from pytacheck.archives.osf import osf_type

    ids = list(osf_id)
    if not ids:
        return ids
    could = [len(x) == 5 and not grepl("view_only=", x) for x in ids]
    if not any(could):
        return ids
    candidates = [x for x, c in zip(ids, could, strict=True) if c]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        types = osf_type(candidates)
    types = [types] if isinstance(types, str) or types is None else list(types)
    user_ids = [x for x, t in zip(candidates, types, strict=True) if t is not None and t == "users"]
    if not user_ids:
        return ids

    expanded: list[str] = []
    for uid in user_ids:
        projects = _osf_user_nodes(uid, pb=pb)
        if not projects:
            warnings.warn(
                f"OSF user {uid} has no projects that could be listed. Private projects need "
                "an OSF token; see ?osf_pat",
                stacklevel=2,
            )
            continue
        _message(f"OSF user {uid} has {len(projects)} project{plural(len(projects))} to download")
        expanded.extend(projects)
    combined = [x for x in ids if x not in user_ids] + expanded
    return list(dict.fromkeys(combined))


def _osf_verify_downloads(
    ret: pd.DataFrame | None, download_to: str, check_size: Any = True
) -> pd.DataFrame | None:
    """Port of R/archive-osf-helpers.R::.osf_verify_downloads(): check files on disk.

    Sets ``downloaded`` to whether each row's ``path`` (under *download_to*)
    is a file on disk and, where *check_size* is true for the row and the OSF
    reported a ``size``, has that size; adds ``size_on_disk``.
    """
    if ret is None or len(ret) == 0:
        return ret
    ret = ret.copy()
    n = len(ret)
    if "path" not in ret.columns:
        ret["size_on_disk"] = pd.Series([float("nan")] * n, dtype="float64", index=ret.index)
        ret["downloaded"] = pd.Series([False] * n, dtype="boolean", index=ret.index)
        return ret

    paths = ret["path"].tolist()
    has_path = [not is_na(p) for p in paths]
    # R file.path(): a plain "/" join (a path starting with "/" is not absolute here)
    full = [f"{download_to}/{p}" if h else None for p, h in zip(paths, has_path, strict=True)]
    on_disk = [f is not None and os.path.exists(f) and not os.path.isdir(f) for f in full]
    size_on_disk = [
        float(os.path.getsize(f)) if d and f is not None else float("nan")
        for f, d in zip(full, on_disk, strict=True)
    ]
    ret["size_on_disk"] = pd.Series(size_on_disk, dtype="float64", index=ret.index)
    ok = [d and s == s for d, s in zip(on_disk, size_on_disk, strict=True)]

    checks = check_size if isinstance(check_size, list | tuple) else [check_size]
    checks = [checks[i % len(checks)] for i in range(n)] if checks else [None] * n
    check = [c is True or (c is not None and not is_na(c) and bool(c)) for c in checks]
    if any(check):
        if "size" in ret.columns:
            expected = pd.to_numeric(ret["size"], errors="coerce").astype("float64").tolist()
        else:
            expected = [float("nan")] * n
        matches = [e != e or s == e for e, s in zip(expected, size_on_disk, strict=True)]
        ok = [o and (not c or m) for o, c, m in zip(ok, check, matches, strict=True)]

    from pytacheck.archives.dataverse import _is_true

    if "downloaded" not in ret.columns:
        # R: `ret$downloaded <- ok & ret$downloaded %in% TRUE` is a length-0 value
        raise ValueError(f"replacement has 0 rows, data has {n}")
    prev = ret["downloaded"].tolist()
    ret["downloaded"] = pd.Series(
        [o and _is_true(p) for o, p in zip(ok, prev, strict=True)],
        dtype="boolean",
        index=ret.index,
    )
    return ret


def _osf_parent_project(osf_id: str) -> str | None:
    """Port of R/archive-osf-helpers.R::.osf_parent_project(): the top-level project of an ID."""
    from pytacheck.archives.osf import osf_check_id
    from pytacheck.log import logger

    valid_id = osf_check_id(osf_id)
    if valid_id is None:
        return None
    obj = _osf_info(valid_id)
    if obj["osf_type"].iloc[0] == "error":
        logger(".osf_parent_project", {"error": "osf error"})
        return None
    if "project" in obj.columns and not is_na(obj["project"].iloc[0]):
        return str(obj["project"].iloc[0])
    if "parent" not in obj.columns or is_na(obj["parent"].iloc[0]):
        return osf_id
    return _osf_parent_project(str(obj["parent"].iloc[0]))
