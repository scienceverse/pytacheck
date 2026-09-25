"""The Open Science Framework (port of ``R/archive-osf.R``).

Finding OSF links in papers (:func:`osf_links`), validating IDs
(:func:`osf_check_id`), looking them up (:func:`osf_info`, :func:`osf_type`),
paging through API listings (:func:`osf_get_all_pages`), listing preprints
(:func:`osf_preprint_list`) and downloading whole projects
(:func:`osf_file_download`).

API data is kept as parsed JSON (see :mod:`pytacheck.archives.osf_helpers`).
A failed listing is an empty :class:`OsfResult` whose ``osf_error`` says why
(R: an empty ``list()`` with an ``osf_error`` attribute).
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import threading
import warnings
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any
from urllib.parse import unquote, unquote_plus

import pandas as pd

from pytacheck._r import (
    bind_rows,
    grepl,
    gsub,
    is_na,
    plural,
    regextract,
    regextract_all,
    strsplit,
    sub,
)

__all__ = [
    "OsfResult",
    "osf_api_check",
    "osf_cache_clear",
    "osf_check_id",
    "osf_delay",
    "osf_file_download",
    "osf_get_all_pages",
    "osf_info",
    "osf_links",
    "osf_preprint_list",
    "osf_type",
]


class OsfResult(list):  # type: ignore[type-arg]
    """A list of OSF API records, carrying the attributes R attaches.

    ``osf_error`` is ``"forbidden"``, ``"not_found"``, ``"gone"`` or
    ``"request_failed"`` when the listing could not be retrieved (the list is
    then empty); ``osf_incomplete`` is ``{"expected": n, "got": m}`` when the
    OSF returned fewer items than it reported.
    """

    osf_error: str | None = None
    osf_incomplete: dict[str, float] | None = None


def _eq(value: Any, target: Any) -> bool:
    """``value %in% target`` for one value: ``False`` (not ``NA``) when missing."""
    return not is_na(value) and value == target


def _osf_err(x: Any) -> str | None:
    """``attr(x, "osf_error")``."""
    return getattr(x, "osf_error", None)


def _api() -> str:
    from pytacheck.utils import get_option

    return str(get_option("metacheck.osf.api"))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _has_internet() -> bool:
    """``curl::has_internet()``: can a well-known host be resolved?"""
    from pytacheck.utils import online

    return online("r-project.org", tries=1)


def osf_api_check(osf_api: str | None = None, on_error: str = "stop") -> str:
    """Port of R/archive-osf.R::osf_api_check(): check the OSF API server status.

    Returns the status description (``"OK"`` when up). When the server does
    not answer 200, *on_error* decides: ``"stop"`` raises, ``"warn"`` warns,
    ``"ignore"`` only returns the status. Without internet access the status
    is ``"no internet"``, and *on_error* applies too (metacheck returns it
    silently whatever *on_error* says: U53).
    """
    from pytacheck import http
    from pytacheck.archives.osf_helpers import _osf_headers
    from pytacheck.log import logger
    from pytacheck.utils import match_arg

    on_error = match_arg(on_error, ["stop", "warn", "ignore"])
    if osf_api is None:
        osf_api = _api()
    status_code = 0
    try:
        if not _has_internet():
            status = "no internet"
        else:
            resp = http.client().request("GET", osf_api, headers=_osf_headers()["headers"])
            status_code = resp.status_code
            status = resp.reason_phrase
    except Exception as exc:
        status = str(exc) or type(exc).__name__

    if status_code != 200:
        logger("osf_api_check", {"error": status, "code": status_code})
        msg = (
            f"The OSF API seems to be having a problem:\nError {status_code}: {status}\n"
            f"Check {osf_api}"
        )
        if on_error == "warn":
            warnings.warn(msg, stacklevel=2)
        elif on_error == "stop":
            raise RuntimeError(msg)
    return status


# ---------------------------------------------------------------------------
# links and IDs
# ---------------------------------------------------------------------------

_OSF_BARE_REGEX = r"(?:https?://)?osf\.io/[A-Za-z0-9]+/?"


def osf_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-osf.R::osf_links(): all OSF links in a paper or paper list.

    Real hyperlinks from the ``url`` table whose ``href`` mentions
    ``osf.io``, plus bare ``osf.io/<id>`` mentions in the text; trailing
    slashes are stripped and duplicate rows dropped. The OSF URL is in the
    first (``href``) column.
    """
    from pytacheck.papers.tables import paper_table
    from pytacheck.text.search import text_search

    urls = paper_table(paper, "url").copy()
    if "href" in urls.columns:
        href = urls["href"]
    else:  # R: `urls$href` on the 0 x 0 url table of an empty paper list is NULL
        warnings.warn("Unknown or uninitialised column: `href`.", stacklevel=2)
        href = []
    urls["href"] = pd.Series(gsub(r"\s", "", href), index=urls.index, dtype="string")
    osf = pd.Series(grepl(r"osf\.io", urls["href"], ignore_case=True), index=urls.index)
    found_href = urls[osf.astype(bool)]

    other = text_search(paper, _OSF_BARE_REGEX, return_="match", perl=True)
    # R: select(href = text, any_of(...)); an empty search result has no `text`
    keep = [c for c in ("text", "text_id", "paper_id") if c in other.columns]
    other_osf = other.loc[:, keep].rename(columns={"text": "href"})

    out = bind_rows([found_href, other_osf])
    out["href"] = pd.Series(sub("/+$", "", out["href"]), index=out.index, dtype="string")
    return out.drop_duplicates().reset_index(drop=True)


class _UrlParseError(ValueError):
    pass


def _url_parse(url: str) -> tuple[str, list[tuple[str, str]]]:
    """The parts of ``httr2::url_parse()`` (libcurl's parser) that OSF IDs need.

    Returns the (percent-decoded) path and the query parameters as
    ``(name, value)`` pairs, split the way ``curl::curl_parse_url()`` does
    (``+`` is a space; a value ends at its second ``=``); raises
    :class:`_UrlParseError` where curl refuses the URL (no scheme, no
    slashes after it, no host, a bad port).
    """
    m = regextract(r"^[A-Za-z][A-Za-z0-9+.-]*:/{1,3}", url, perl=True)
    if m is None:
        raise _UrlParseError("Failed to parse URL: Bad scheme")
    rest = url[len(m) :]
    rest = rest.split("#", 1)[0]
    cut = min((k for k in (rest.find("/"), rest.find("?")) if k >= 0), default=len(rest))
    authority, remainder = rest[:cut], rest[cut:]
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("["):
        port = host.split("]", 1)[1].lstrip(":")
    else:
        host, _, port = host.partition(":")
    if host == "":
        raise _UrlParseError("Failed to parse URL: No host part in the URL")
    if port and (not port.isdigit() or int(port) > 65535):
        raise _UrlParseError(
            "Failed to parse URL: Port number was not a decimal number between 0 and 65535"
        )
    path, _, query = remainder.partition("?")
    params: list[tuple[str, str]] = []
    if query:
        # curl:::parse_query_urlencoded(): strsplit() on "&" then "=", keeping
        # only the first two pieces (so "a=b=c" is a = "b")
        for piece in query.split("&"):
            if piece == "":
                continue
            parts = piece.split("=")
            value = parts[1] if len(parts) > 1 else ""
            params.append((unquote_plus(parts[0]), unquote_plus(value)))
    return unquote(path or "/"), params


def _dollar(params: list[tuple[str, str]], name: str) -> str | None:
    """R ``as.list(params)$name``: the first exact match, else a unique partial match."""
    for key, value in params:
        if key == name:
            return value
    partial = [value for key, value in params if key.startswith(name)]
    return partial[0] if len(partial) == 1 else None


#: 5-letter OSF page names that follow a project ID in a URL (osf.io/<id>/files/).
_OSF_ROUTES = frozenset({"files", "forks"})

#: Path segments followed by a 24-character id that is not a file's: the old
#: draft-registration page osf.io/<id>/register/<schema id> and the API's
#: schemas/registrations/<schema id>.
_SCHEMA_PARENTS = frozenset({"register", "registrations"})


def _osf_check_one(osf_id: Any) -> str | None:
    from pytacheck._r import as_character

    raw = as_character(osf_id) if not isinstance(osf_id, str) else osf_id
    if raw is None:
        return None
    ident = gsub(r"\s", "", raw).lower()
    if grepl(r"^(https?://)?(www\.)?osf\.io/?$", ident):
        return None
    try:
        if grepl(r"^[a-z0-9]{5}(_v\d+)?$", ident):
            return ident
        if grepl(r"^[a-z0-9]{5}(_v\d+)?\?view_only=.+$", ident):
            return ident
        if len(ident) == 24 and grepl("^[a-z0-9]+$", ident):
            return ident
        path, query = _url_parse(ident)
        last = path.split("/")
        while len(last) > 1 and last[-1] == "":
            last.pop()  # R's strsplit() drops the trailing empty piece
        tail = last[-1]
        # a project page such as osf.io/j3gcx/files/ ends in a route name, not
        # an ID; a 24-character file ID is hexadecimal (an OSF object id), and
        # the one after register/ is a registration schema, not a file
        # (metacheck returns "files", or any 24-character segment: U51)
        if grepl(r"^[a-z0-9]{5}(_v\d+)?$", tail) and tail not in _OSF_ROUTES:
            view_only = _dollar(query, "view_only")
            if view_only is not None:
                tail = f"{tail}?view_only={view_only}"
            return tail
        if grepl("^[0-9a-f]{24}$", tail) and not (len(last) > 1 and last[-2] in _SCHEMA_PARENTS):
            return tail
        raise _UrlParseError("not an OSF ID")
    except _UrlParseError:
        # an ID is 5 characters, not the start of a longer word: osf.io/prereg
        # is not "prere" (metacheck takes the first five characters: U51)
        matches = regextract_all(
            r"(?<=osf\.io/)[a-z0-9]{5}(_v\d+)?(?![a-z0-9])[?/]?", ident, perl=True
        )
        # the first osf.io ID (metacheck coerces the list of matches with
        # as.character(), so a URL with two is rejected: U51)
        for m in matches:
            id5 = sub("[?/]$", "", m)
            if len(id5) in (5, 8, 9) and id5 not in _OSF_ROUTES:
                return id5
        warnings.warn(f"{ident} is not a valid OSF ID", stacklevel=3)
        return None


def osf_check_id(osf_id: Any) -> Any:
    """Port of R/archive-osf.R::osf_check_id(): valid OSF IDs, ``None`` for invalid ones.

    Accepts 5-character GUIDs (optionally versioned, ``abcde_v2``, or with a
    ``?view_only=`` token), 24-character Waterbutler IDs, and OSF URLs;
    whitespace is removed and case folded. Invalid inputs warn (except
    missing values and bare links to osf.io). A string gives a string (or
    ``None``); a sequence gives a list.
    """
    if isinstance(osf_id, str) or osf_id is None or not isinstance(osf_id, Sequence | pd.Series):
        return _osf_check_one(osf_id)
    return [_osf_check_one(x) for x in osf_id]


# ---------------------------------------------------------------------------
# listings
# ---------------------------------------------------------------------------

_LISTING_CACHE: dict[str, pd.DataFrame] = {}
_LISTING_LOCK = threading.Lock()


def _osf_cache_key(osf_url: Any, recursive: Any) -> str:
    """Port of R/archive-osf.R::.osf_cache_key(): session-cache key for a listing."""
    from pytacheck._r import as_character, r_sorted

    if isinstance(osf_url, pd.DataFrame):
        ids = [v for col in osf_url.columns for v in osf_url[col].tolist()]
    elif isinstance(osf_url, str) or osf_url is None:
        ids = [osf_url]
    else:
        ids = list(osf_url)
    chars = [as_character(v) for v in ids]
    uniq = [c for c in dict.fromkeys(chars) if c is not None]
    rec = "TRUE" if recursive is True else "FALSE"
    return "|".join(r_sorted(uniq)) + f"::recursive={rec}"


def osf_cache_clear() -> int:
    """Port of R/archive-osf.R::osf_cache_clear(): empty the session cache of OSF listings.

    Returns the number of cached listings removed.
    """
    with _LISTING_LOCK:
        n = len(_LISTING_CACHE)
        _LISTING_CACHE.clear()
    return n


def _column_name(table: pd.DataFrame, id_col: int | str) -> str:
    """R ``colnames(table[id_col])``: a 1-based position or a name."""
    if isinstance(id_col, int | float) and not isinstance(id_col, bool):
        return str(table.columns[int(id_col) - 1])
    if id_col not in table.columns:
        raise KeyError(f"Can't subset columns that don't exist: {id_col}")
    return str(id_col)


def _fetch_listings(urls: list[str]) -> OsfResult:
    """``lapply(urls, osf_get_all_pages) |> bind_rows()`` (listings only, in order)."""
    if not urls:
        return OsfResult()
    if len(urls) == 1:
        pages = [osf_get_all_pages(urls[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
            futures = [pool.submit(copy_context().run, osf_get_all_pages, u) for u in urls]
            pages = [f.result() for f in futures]
    out = OsfResult()
    for page in pages:
        if isinstance(page, list):
            out.extend(page)
    return out


def osf_info(
    osf_url: Any,
    id_col: int | str = 1,
    recursive: bool = False,
    pb: Any = None,
    cache: bool = False,
) -> pd.DataFrame:
    """Port of R/archive-osf.R::osf_info(): look up OSF IDs or URLs.

    *osf_url* is an ID/URL, a sequence of them, or a table whose *id_col*
    (R-style 1-based position, or a name) holds them; the result keeps the
    input rows (and columns) and adds the OSF information. With *recursive*,
    all components and files are listed too (appended as extra rows).
    Listings are memoised for the session (see :func:`osf_cache_clear`;
    ``options({"metacheck.osf.cache": False})`` disables it); with *cache*,
    per-ID lookups also use the on-disk listing cache.
    """
    from pytacheck.archives import _spinner, _tick
    from pytacheck.utils import get_option

    use_cache = get_option("metacheck.osf.cache", True) is True
    cache_key = _osf_cache_key(osf_url, recursive) if use_cache else None
    if cache_key is not None:
        with _LISTING_LOCK:
            hit = _LISTING_CACHE.get(cache_key)
        if hit is not None:
            _tick(pb, "OSF listing (cached)")
            return hit.copy()

    with _spinner(pb, "OSF Retrieve") as bar:
        return _osf_info_listing(osf_url, id_col, recursive, bar, cache, cache_key)


def _osf_info_listing(
    osf_url: Any,
    id_col: int | str,
    recursive: bool,
    pb: Any,
    cache: bool,
    cache_key: str | None,
) -> pd.DataFrame:
    """The body of :func:`osf_info` after the session-cache check."""
    from pytacheck._r import as_character
    from pytacheck.archives import _tick
    from pytacheck.archives.osf_helpers import _osf_info, _osf_parse_response
    from pytacheck.utils import left_join

    if isinstance(osf_url, pd.DataFrame):
        table = osf_url
        id_col_name = _column_name(table, id_col)
        raw_osf_urls = table[id_col_name].tolist()
    else:
        vals = [osf_url] if isinstance(osf_url, str) or osf_url is None else list(osf_url)
        uniq = list(dict.fromkeys(None if is_na(v) else v for v in vals))
        raw_osf_urls = [as_character(v) for v in uniq if v is not None]
        id_col_name = "osf_url"
        table = pd.DataFrame({"osf_url": pd.Series(raw_osf_urls, dtype="string")})

    checked = osf_check_id(raw_osf_urls) if raw_osf_urls else []
    pairs = [(u, i) for u, i in zip(raw_osf_urls, checked, strict=True) if i is not None]
    pairs = list(dict.fromkeys((None if is_na(u) else u, i) for u, i in pairs))
    ids = pd.DataFrame(
        {
            "osf_url": pd.Series([u for u, _ in pairs], dtype="string"),
            "osf_id": pd.Series([i for _, i in pairs], dtype="string"),
        }
    )
    valid_ids = list(dict.fromkeys(i for _, i in pairs))
    if not valid_ids:
        _tick(pb, "No valid OSF links")
        return table

    info = left_join(_osf_info(valid_ids, pb=pb, cache=cache), ids, by="osf_id")
    if "project" not in info.columns:
        info["project"] = pd.Series([None] * len(info), dtype="string")

    data = left_join(table, info, by={id_col_name: "osf_url"}, suffix=("", ".osf"))

    if recursive is True:
        _tick(pb, "...Main retrieval complete")
        _tick(pb, "Starting retrieval of children...")
        child_parts: list[pd.DataFrame] = []
        children: pd.DataFrame | None = info
        urls = _non_na(children, "children")
        depth = 0
        while urls:
            depth += 1
            found = sum(len(p) for p in child_parts)
            _tick(
                pb,
                f"Listing components: level {depth}, {len(urls)} to check ({found} found so far)",
            )
            children = _osf_parse_response(_fetch_listings(urls))
            if children is not None:
                child_parts.append(children)
            urls = _non_na(children, "children")
        child_collector = bind_rows(child_parts) if child_parts else pd.DataFrame()
        if len(child_collector) > 0:
            n = len(child_collector)
            _tick(pb, f"Found {n} component{plural(n)}")

        all_nodes = bind_rows([info, child_collector])
        file_parts: list[pd.DataFrame] = []
        urls = _non_na(all_nodes, "files")
        while urls:
            n_files = sum(
                int((p["kind"] == "file").fillna(False).sum()) for p in file_parts if "kind" in p
            )
            _tick(
                pb,
                f"Listing files: {len(urls)} folder{plural(len(urls))} to check "
                f"({n_files} file{plural(n_files)} found so far)",
            )
            files = _osf_parse_response(_fetch_listings(urls))
            if files is not None:
                file_parts.append(files)
            urls = _non_na(files, "files")
        file_collector = bind_rows(file_parts) if file_parts else pd.DataFrame()
        data = bind_rows([data, child_collector, file_collector])

    _tick(pb, "...OSF retrieval complete!")
    if cache_key is not None:
        with _LISTING_LOCK:
            _LISTING_CACHE[cache_key] = data.copy()
    return data


def _non_na(df: pd.DataFrame | None, col: str) -> list[str]:
    if df is None or col not in df.columns:
        return []
    return [str(v) for v in df[col].tolist() if not is_na(v)]


def osf_type(guid: Any) -> Any:
    """Port of R/archive-osf.R::osf_type(): the type of an OSF GUID.

    ``"nodes"``, ``"files"``, ``"preprints"``, ``"registrations"``,
    ``"users"``...; ``"inaccessible"`` when the ID is well-formed but the
    resource could not be reached (private, embargoed, withdrawn, deleted);
    ``None`` when the input is not a valid OSF ID. A sequence gives a list.
    """
    if not isinstance(guid, str) and guid is not None and isinstance(guid, Sequence | pd.Series):
        values = list(guid)
        if not values:
            # R: `if (is.na(osf_check_id(character(0))))`
            raise ValueError("argument is of length zero")
        if len(values) == 1:
            return [osf_type(values[0])]
        from pytacheck.utils import pb

        bar = pb(len(values), "Checking OSF Types [:bar] :current/:total :elapsedfull")
        types = []
        for g in values:
            bar.tick()
            types.append(osf_type(g))
        return types

    ident = osf_check_id(guid)
    if ident is None:
        return None
    info = osf_get_all_pages(f"{_api()}/guids/{ident}/?resolve=false")
    if _osf_err(info) is not None:
        return "inaccessible"
    from pytacheck.archives.osf_helpers import _get

    otype = _get(info, "relationships", "referent", "links", "related", "meta", "type")
    if otype is None or (isinstance(otype, list) and len(otype) == 0):
        return None
    return otype[0] if isinstance(otype, list) else otype


def _osf_max_page_size(url: str) -> str:
    """Port of R/archive-osf.R::.osf_max_page_size(): ask for 100 items per page."""
    if grepl(r"page%5Bsize%5D|page\[size\]", url):
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}page[size]=100"


def _osf_status_error(status: int) -> str | None:
    """Port of R/archive-osf.R::.osf_status_error(): HTTP status -> ``osf_error`` kind."""
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 410:
        return "gone"
    if status >= 400:
        return "request_failed"
    return None


def _osf_error_result(kind: str) -> OsfResult:
    """Port of R/archive-osf.R::.osf_error_result(): an empty result carrying ``osf_error``."""
    out = OsfResult()
    out.osf_error = kind
    return out


def _strip_query(url: str) -> str:
    """R ``sub("\\?.*$", "", url)``."""
    return str(sub(r"\?.*$", "", url))


def _get_path(content: Any, *path: str) -> Any:
    from pytacheck.archives.osf_helpers import _get

    return _get(content, *path)


def _num(x: Any) -> float | None:
    if isinstance(x, list):
        x = x[0] if x else None
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def osf_get_all_pages(url: str, page_end: float = math.inf) -> Any:
    """Port of R/archive-osf.R::osf_get_all_pages(): every page of an OSF API listing.

    Returns the records of a listing (an :class:`OsfResult`), or the resource
    itself (a ``dict``) for a single-resource URL. Pages after the first are
    fetched concurrently, up to page *page_end* (an absolute page number). A
    failure gives an empty :class:`OsfResult` with ``osf_error`` set; a
    listing shorter than the total the OSF reports warns and records
    ``osf_incomplete``.
    """
    from pytacheck import http
    from pytacheck.log import logger

    http.sleep(osf_delay() or 0)

    content = _osf_get_one_page(url)
    for attempt in (1, 2):
        err = _osf_err(content)
        if err is None or err != "request_failed":
            break
        http.sleep(2**attempt)
        content = _osf_get_one_page(url)
    if _osf_err(content) is not None:
        return content

    data = content.get("data") if isinstance(content, dict) else None
    next_url = _get_path(content, "links", "next")
    if isinstance(next_url, list):
        next_url = next_url[0] if next_url else None
    if next_url is None:
        if isinstance(data, list) or data is None or len(data) == 0:
            out = OsfResult(data if isinstance(data, list) else [])
            total1 = _num(_get_path(content, "links", "meta", "total"))
            if total1 is None:
                total1 = _num(_get_path(content, "meta", "total"))
            if total1 is not None and math.isfinite(total1) and len(out) < total1:
                logger("osf_get_all_pages", {"url": url, "expected": total1, "got": len(out)})
                warnings.warn(
                    f"The OSF listed only {len(out)} of the {int(total1)} items it reports for "
                    f"{_strip_query(url)}. Anything not listed is not retrieved, so this "
                    "listing is incomplete. Run again (see ?osf_pat, which raises the request "
                    "limit).",
                    stacklevel=2,
                )
                out.osf_incomplete = {"expected": total1, "got": float(len(out))}
            return out
        return data

    first = list(data) if isinstance(data, list) else ([] if data is None else [data])
    found = regextract(r"(?<=page=)\d+", next_url, perl=True)
    next_page_num = float(found) if found is not None else None
    total = _num(_get_path(content, "links", "meta", "total"))
    if total is None:
        total = _num(_get_path(content, "meta", "total"))
    per_page = _num(_get_path(content, "links", "meta", "per_page"))
    if per_page is None:
        per_page = _num(_get_path(content, "meta", "per_page"))
    if next_page_num is None or total is None or per_page is None or per_page <= 0:
        subdata = osf_get_all_pages(next_url, page_end)
        if _osf_err(subdata) is not None or not isinstance(subdata, list):
            subdata = []
        return OsfResult(first + list(subdata))

    last_page = min(math.ceil(total / per_page), page_end)
    if last_page < next_page_num:
        return OsfResult(first)

    page_urls = [
        sub(r"page=\d+", f"page={p}", next_url)
        for p in range(int(next_page_num), int(last_page) + 1)
    ]
    http.sleep(osf_delay() or 0)
    more_pages = _osf_get_pages_parallel(page_urls)
    for attempt in (1, 2):
        failed = [i for i, p in enumerate(more_pages) if _osf_err(p) is not None]
        if not failed:
            break
        http.sleep(2**attempt)
        retried = _osf_get_pages_parallel([page_urls[i] for i in failed])
        for i, page in zip(failed, retried, strict=True):
            more_pages[i] = page

    out = OsfResult(first)
    for page in more_pages:
        if _osf_err(page) is None and isinstance(page, dict):
            page_data = page.get("data")
            if isinstance(page_data, list):
                out.extend(page_data)
            elif isinstance(page_data, dict):
                out.append(page_data)

    got = len(out)
    expected = min(total, per_page * page_end)
    if math.isfinite(expected) and got < expected:
        logger("osf_get_all_pages", {"url": url, "expected": expected, "got": got})
        warnings.warn(
            f"The OSF listed only {got} of the {int(expected)} items it reports for "
            f"{_strip_query(url)}. Anything not listed is not retrieved, so this listing "
            "is incomplete. This usually means the OSF refused a request under load: run "
            "again (see ?osf_pat, which raises the request limit).",
            stacklevel=2,
        )
        out.osf_incomplete = {"expected": expected, "got": float(got)}
    return out


def _osf_get_one_page(url: str) -> Any:
    """Port of R/archive-osf.R::.osf_get_one_page(): fetch and parse one API page.

    Returns the parsed JSON document, or an :func:`_osf_error_result`.
    """
    from pytacheck import http
    from pytacheck.archives.osf_helpers import _osf_headers, _resp_body_json
    from pytacheck.log import logger

    try:
        resp = http.request(
            "GET",
            _osf_max_page_size(url),
            headers=_osf_headers()["headers"],
            max_tries=3,
            retry_statuses=(429,),
        )
        if resp is None:
            return _osf_error_result("request_failed")
        err = _osf_status_error(resp.status_code)
        if err is not None:
            logger("osf_get_all_pages", {"url": url, "status": resp.status_code})
            return _osf_error_result(err)
        return _resp_body_json(resp)
    except Exception:
        return _osf_error_result("request_failed")


def _osf_get_pages_parallel(urls: Sequence[str]) -> list[Any]:
    """Port of R/archive-osf.R::.osf_get_pages_parallel(): several pages concurrently."""
    urls = list(urls)
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(10, len(urls))) as pool:
        futures = [pool.submit(copy_context().run, _osf_get_one_page, u) for u in urls]
        return [f.result() for f in futures]


def osf_delay(delay: float | None = None) -> float:
    """Port of R/archive-osf.R::osf_delay(): get or set the wait (seconds) before OSF calls."""
    from pytacheck.utils import get_option, options

    if delay is None:
        return get_option("metacheck.osf.delay")  # type: ignore[no-any-return]
    if isinstance(delay, bool) or not isinstance(delay, int | float):
        raise ValueError(
            "set osf_delay with a numeric value for the number of seconds to wait between OSF calls"
        )
    options({"metacheck.osf.delay": delay})
    return delay


# ---------------------------------------------------------------------------
# preprints
# ---------------------------------------------------------------------------


def _as_list(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, str) or not isinstance(x, Sequence):
        return [x]
    return list(x)


def osf_preprint_list(
    provider: Any = None,
    date_created: Any = None,
    date_modified: Any = None,
    page_start: int = 1,
    page_end: int | None = None,
) -> pd.DataFrame:
    """Port of R/archive-osf.R::osf_preprint_list(): list OSF preprints.

    Filters by *provider* (e.g. ``"psyarxiv"``) and by a single date or a
    ``[min, max]`` range for *date_created* / *date_modified*; reads pages
    *page_start* to *page_end* (default: just *page_start*) of 10 entries.
    """
    from pytacheck._r import as_character
    from pytacheck.archives.osf_helpers import _osf_preprint_data

    if page_end is None:
        page_end = page_start
    filters = [f"page={as_character(page_start)}"]
    if provider is not None:
        filters.append("filter[provider]=" + ",".join(str(p) for p in _as_list(provider)))
    for name, value in (("date_created", date_created), ("date_modified", date_modified)):
        values = [str(v) for v in _as_list(value)]
        if len(values) == 1:
            filters.append(f"filter[{name}]={values[0]}")
        elif len(values) == 2:
            filters.append(f"filter[{name}][gte]={min(values)}")
            filters.append(f"filter[{name}][lte]={max(values)}")
    url = f"{_api()}/preprints/?" + "&".join(filters)
    pp = osf_get_all_pages(url, page_end=page_end)
    return _osf_preprint_data(pp)


# ---------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------

_MB = 1024 * 1024
# R/repo-download.R::.storage_is_transient() / .storage_backoff(), used when the
# download port is not importable (they are two constants).
_STORAGE_TRANSIENT = (403, 429, 500, 502, 503, 504)


def _cap_helpers() -> tuple[Any, Any, Any]:
    """``cap_report()``, ``.cap_size_str()`` (R/cap-prompt.R) and ``.cap_num()``."""
    from pytacheck.report.blocks import _cap_num

    try:
        from pytacheck.llm.cap_prompt import _cap_size_str, cap_report
    except ImportError:
        _cap_size_str, cap_report = _cap_size_str_local, _cap_report_local
    return cap_report, _cap_size_str, _cap_num


def _cap_report_local(message: str) -> None:
    from pytacheck.archives import _message

    _message(message)
    warnings.warn(message, stacklevel=3)


def _cap_size_str_local(nbytes: float | None) -> str:
    if nbytes is None or nbytes != nbytes:
        return "unknown size"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 1 if nbytes <= 0 else min(len(units), 1 + math.floor(math.log(nbytes, 1024)))
    return f"{nbytes / 1024 ** (i - 1):.1f} {units[i - 1]}"


def _storage_retry() -> tuple[Any, Any]:
    try:
        from pytacheck.archives.download import _storage_backoff, _storage_is_transient
    except ImportError:
        return (lambda resp: resp.status_code in _STORAGE_TRANSIENT), (
            lambda attempt: min(2**attempt, 30)
        )
    return _storage_is_transient, _storage_backoff


def _stream_to_file(url: str, path: str, timeout_s: float = 1800, max_tries: int = 3) -> int:
    """GET *url* straight to *path* with the storage retry policy; returns the status.

    Raises the last connection error when every try failed to connect.
    """
    import httpx

    from pytacheck import http
    from pytacheck.archives.osf_helpers import _osf_headers

    is_transient, backoff = _storage_retry()
    headers = _osf_headers()["headers"]
    last_exc: Exception | None = None
    status = 0
    for attempt in range(1, max_tries + 1):
        try:
            with http.client().stream("GET", url, headers=headers, timeout=timeout_s) as resp:
                status = resp.status_code
                if status == 200 or not is_transient(resp) or attempt == max_tries:
                    with open(path, "wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                    return status
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt == max_tries:
                raise
        http.sleep(backoff(attempt))
    if last_exc is not None:
        raise last_exc
    return status


def _osf_prepare_save_paths(
    files: pd.DataFrame,
    contents: pd.DataFrame,
    osf_id: str,
    max_folder_length: float,
    ignore_folder_structure: bool,
) -> pd.DataFrame:
    """Where each listed file is saved, below the project folder (R: nested helper)."""
    from pytacheck.utils import path_sanitize

    files = files.copy()
    c_ids = contents["osf_id"].tolist()
    c_project = contents["project"].tolist() if "project" in contents else [None] * len(contents)
    c_name = contents["name"].tolist() if "name" in contents else [None] * len(contents)
    rows_by_id: dict[Any, list[int]] = {}
    for k, cid in enumerate(c_ids):
        rows_by_id.setdefault(cid, []).append(k)

    parent_folders: list[str] = []
    for last_parent in files["project"].tolist() if "project" in files else [None] * len(files):
        chain: list[int] = []
        while not is_na(last_parent) and last_parent != osf_id:
            hits = rows_by_id.get(last_parent, [])
            if not hits:
                break
            chain.extend(hits)
            last_parent = c_project[chain[-1]]
            if last_parent is not None and is_na(last_parent):
                last_parent = None
        if not chain:
            chain = list(rows_by_id.get(osf_id, []))
        names = [c_name[k] for k in reversed(chain)]
        sanitized = path_sanitize(names) if names else []
        parent_folders.append("/".join("NA" if s is None else s for s in sanitized))

    paths = files["path"].tolist()
    in_path = [
        (f"/{folder}/" in ("NA" if is_na(p) else str(p)))
        and ("NA" if is_na(p) else str(p)).find(f"/{folder}/") == 0
        for folder, p in zip(parent_folders, paths, strict=True)
    ]
    if in_path and all(in_path):
        parent_folders = [""] * len(files)

    providers = files["provider"].tolist()
    save = [
        f"{'NA' if is_na(prov) else prov}{'/' if folder else ''}{folder}{'NA' if is_na(p) else p}"
        for prov, folder, p in zip(providers, parent_folders, paths, strict=True)
    ]

    if max_folder_length < math.inf:
        hacky = "--replace-this--"
        width = int(max_folder_length)
        new_save = []
        for sp in save:
            fp = sp + hacky if sp.endswith("/") else sp
            parts = strsplit(_r_dirname(fp), "/", fixed=True)
            trimmed = "/".join(part[:width] for part in parts)
            new_save.append(f"{trimmed}/{_r_basename(fp)}".replace(hacky, ""))
        if any(a != b for a, b in zip(new_save, save, strict=True)):
            warnings.warn(
                "Some folder names were truncated to max_folder_length = "
                f"{_r_num(max_folder_length)} characters",
                stacklevel=3,
            )
        save = new_save

    kind = files["kind"].tolist()
    to_copy = [i for i, k in enumerate(kind) if _eq(k, "file")]
    if ignore_folder_structure and to_copy:
        names = files["name"].tolist()
        ids = files["osf_id"].tolist()
        flat = path_sanitize([names[i] for i in to_copy], keep_sep=False)
        seen: set[Any] = set()
        for pos, i in enumerate(to_copy):
            value = flat[pos]
            if value in seen:
                value = (
                    f"{'NA' if is_na(ids[i]) else ids[i]}-{'NA' if is_na(names[i]) else names[i]}"
                )
            else:
                seen.add(value)
            save[i] = value
    files["save_path"] = pd.Series(save, index=files.index, dtype="string")
    return files


def _r_dirname(path: str) -> str:
    """R ``dirname()`` on Unix: trailing slashes dropped, ``"."`` without a slash."""
    if path == "":
        return ""
    stripped = path.rstrip("/")
    if stripped == "":
        return "/"
    head, sep, _ = stripped.rpartition("/")
    if not sep:
        return "."
    head = head.rstrip("/")
    return head if head else "/"


def _r_basename(path: str) -> str:
    """R ``basename()`` on Unix: the part after the last slash, trailing slashes dropped."""
    return path.rstrip("/").rpartition("/")[2]


def _r_num(x: float) -> str:
    from pytacheck._r import as_character

    return as_character(float(x)) or "NA"


def _osf_zip_url(node: str) -> str:
    return f"https://files.osf.io/v1/resources/{node}/providers/osfstorage/?zip="


def _osf_zip_content_length(url: str) -> float:
    """HEAD the archive for its size (``NaN`` when not reported)."""
    from pytacheck import http
    from pytacheck.archives.osf_helpers import _osf_headers

    try:
        resp = http.request("HEAD", url, headers=_osf_headers()["headers"], max_tries=1)
    except Exception:
        return math.nan
    if resp is None or resp.status_code >= 400:
        return math.nan
    try:
        return float(resp.headers.get("content-length", "nan"))
    except ValueError:
        return math.nan


def _osf_download_zip(zip_url: str, zip_path: str, zip_size: float = math.nan) -> str:
    """Stream one Waterbutler archive to disk, reporting each stage."""
    import time

    from pytacheck.archives import _message

    t0 = time.monotonic()
    size = f"{zip_size / 1024**2:.1f} MB" if math.isfinite(zip_size) else "unknown size"
    _message(f"[zip] requesting archive: {zip_url} ({size})")
    _message(
        "[zip] OSF builds the archive server-side first; for a large repo this can take "
        "several minutes before download starts. Streaming to:"
    )
    _message("[zip]   ", zip_path)
    try:
        status = _stream_to_file(zip_url, zip_path, timeout_s=1800)
    except Exception as exc:
        elapsed = round(time.monotonic() - t0, 1)
        _message(f"[zip] FAILED after {elapsed}s: {exc}")
        raise RuntimeError(f"OSF zip download failed for {zip_url}") from exc
    elapsed = round(time.monotonic() - t0, 1)
    if status != 200:
        _message(f"[zip] FAILED after {elapsed}s: HTTP {status}")
        raise RuntimeError(f"OSF zip download failed for {zip_url} (HTTP {status})")
    got = os.path.getsize(zip_path) if os.path.exists(zip_path) else 0
    _message(
        f"[zip] downloaded {got / 1024**2:.1f} MB in {elapsed}s -> {os.path.basename(zip_path)}"
    )
    return zip_path


def _copy_file(src: str, dest: str, overwrite: bool = False) -> bool:
    """R ``file.copy(from, to)``: FALSE when *dest* exists (unless *overwrite*)."""
    if not os.path.exists(src) or (os.path.exists(dest) and not overwrite):
        return False
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        shutil.copyfile(src, dest)
    except OSError:
        return False
    return True


def osf_file_download(
    osf_id: Any,
    download_to: str = ".",
    max_file_size: float | None = None,
    max_download_size: float | None = None,
    max_folder_length: float = math.inf,
    ignore_folder_structure: bool = False,
    mode: str = "all",
    unzip: bool = True,
    metadata: bool = True,
    osf_pat: str | None = None,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-osf.R::osf_file_download(): download OSF projects.

    Each project is saved in its own folder (named after its ID) under
    *download_to*; downloading again resumes into the same folder. A user ID
    downloads every project the user contributes to; a table with an
    ``osf_id`` column (e.g. from :func:`osf_user_projects`) downloads those.

    *mode*: ``"all"`` (default) takes one archive per component without
    listing files (one row per node); ``"select"`` lists every file, applies
    *max_file_size* / *max_download_size* (MB) and verifies each download
    (one row per file); ``"zip"`` lists files but transports whole nodes as
    archives (kept, or unpacked with *unzip*); ``"files"`` is an old name for
    ``"select"``. With *metadata*, wikis, the activity log and descriptive
    metadata are written to ``_osf_metadata``. *osf_pat* sets the OSF token
    for the session.

    Note: metacheck's ``mode = "files"`` falls through every branch and
    downloads nothing (its alias line maps ``"select"`` to itself); pytacheck
    treats ``"files"`` as ``"select"``, as documented.
    """
    from pytacheck.archives import _spinner
    from pytacheck.archives.osf_helpers import osf_pat as _set_pat
    from pytacheck.utils import match_arg

    mode = match_arg(mode, ["all", "select", "files", "zip"])
    if mode == "files":
        mode = "select"
    if max_download_size is None:
        max_download_size = math.inf
    if max_file_size is None:
        max_file_size = math.inf
    if osf_pat is not None:
        _set_pat(osf_pat)

    if isinstance(osf_id, pd.DataFrame):
        id_cols = [c for c in ("osf_id", "osf_project", "id") if c in osf_id.columns]
        if not id_cols:
            raise ValueError(
                "`osf_id` is a data frame with no `osf_id` column. Pass the table from "
                "osf_user_projects(), or a vector of IDs."
            )
        osf_id = osf_id[id_cols[0]].tolist()

    raw = [osf_id] if isinstance(osf_id, str) or osf_id is None else list(osf_id)
    ids = list(dict.fromkeys(v for v in osf_check_id(raw) if v is not None)) if raw else []
    if not ids:
        return None

    with _spinner(pb, "OSF File Download") as bar:
        return _osf_file_download_ids(
            ids,
            download_to=download_to,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            max_folder_length=max_folder_length,
            ignore_folder_structure=ignore_folder_structure,
            mode=mode,
            unzip=unzip,
            metadata=metadata,
            pb=bar,
        )


def _osf_file_download_ids(
    ids: list[str],
    download_to: str,
    max_file_size: float,
    max_download_size: float,
    max_folder_length: float,
    ignore_folder_structure: bool,
    mode: str,
    unzip: bool,
    metadata: bool,
    pb: Any,
) -> pd.DataFrame | None:
    """The body of :func:`osf_file_download` once the IDs are checked."""
    from pytacheck.archives import _message, _tick
    from pytacheck.archives.osf_helpers import _osf_expand_user_ids, _osf_verify_downloads
    from pytacheck.log import logger
    from pytacheck.utils import left_join

    ids = _osf_expand_user_ids(ids, pb=pb)
    if not ids:
        return None

    if len(ids) > 1:
        _tick(pb, f"Starting downloads for {len(ids)} OSF projects...\n")
        results: list[pd.DataFrame | None] = []
        for x in ids:
            try:
                results.append(
                    osf_file_download(
                        osf_id=x,
                        download_to=download_to,
                        max_file_size=max_file_size,
                        max_download_size=max_download_size,
                        max_folder_length=max_folder_length,
                        ignore_folder_structure=ignore_folder_structure,
                        mode=mode,
                        unzip=unzip,
                        metadata=metadata,
                    )
                )
            except Exception as exc:
                warnings.warn(f"{x} resulted in an error:\n  {exc}\n", stacklevel=2)
        _tick(pb, f"...Completed downloads for {len(ids)} OSF projects")
        return bind_rows(results)

    osf_id = ids[0]

    if mode == "all":
        from pytacheck.archives.osf_all import _osf_download_all

        return _osf_download_all(osf_id, download_to, metadata=metadata, pb=pb)

    cap_report, cap_size_str, cap_num = _cap_helpers()

    _tick(pb, f"Starting retrieval for {osf_id}")
    contents = osf_info(osf_id, recursive=True, pb=pb)
    cols = [
        c
        for c in (
            "osf_id",
            "name",
            "provider",
            "path",
            "kind",
            "size",
            "download_url",
            "parent",
            "project",
        )
        if c in contents.columns
    ]
    is_files = (
        (contents["osf_type"] == "files").fillna(False).astype(bool)
        if "osf_type" in contents
        else pd.Series(False, index=contents.index)
    )
    files = contents.loc[is_files, cols].reset_index(drop=True)

    def kind_is_file(df: pd.DataFrame) -> list[bool]:
        return [_eq(k, "file") for k in df["kind"].tolist()] if "kind" in df else [False] * len(df)

    file_mask = kind_is_file(files)
    n_f = sum(file_mask)
    if n_f > 0:
        sizes = pd.to_numeric(files["size"], errors="coerce")[file_mask] if "size" in files else []
        total_bytes = float(pd.Series(sizes, dtype="float64").sum(skipna=True))
        _message(f"{osf_id}: {n_f} file{plural(n_f)}, {cap_size_str(total_bytes)} to download")

    if len(files) == 0:
        types = contents["osf_type"].tolist() if "osf_type" in contents else []
        unreadable = [t for t in types if t in ("unfound", "private", "error", "invalid")]
        if unreadable:
            why = unreadable[0]
            reason = {
                "unfound": f"no such project on the OSF. Check the ID at https://osf.io/{osf_id}",
                "private": "this project is private and your token cannot read it. See ?osf_pat",
                "invalid": "not a valid OSF ID",
            }.get(why, "the OSF could not be reached for this project")
            _message(f"{osf_id}: {reason}. Nothing was downloaded.")
        else:
            _tick(pb, f"- {osf_id} contained no files")
        return None

    size_num = (
        pd.to_numeric(files["size"], errors="coerce")
        if "size" in files
        else pd.Series(math.nan, index=files.index)
    )

    # restrict file size
    if mode == "select" and math.isfinite(max_file_size) and max_file_size > 0:
        too_big = [i for i, s in enumerate(size_num.tolist()) if s == s and s > max_file_size * _MB]
        if too_big:
            names = files["name"].tolist()
            big_sizes = [size_num.iloc[i] for i in too_big]
            largest = sorted(zip(too_big, big_sizes, strict=True), key=lambda t: -t[1])[0][0]
            n_big = len(too_big)
            _message(
                f"{n_big} file{plural(n_big)} in {osf_id} exceeded the {cap_num(max_file_size)} "
                f"MB per-file limit and {'was' if n_big == 1 else 'were'} skipped (the rest of "
                f"the repository was downloaded). Largest: {names[largest]} "
                f"({cap_num(round(max(big_sizes) / _MB))} MB). Raise max_file_size to include them."
            )
            files = files.drop(index=too_big).reset_index(drop=True)
            size_num = size_num.drop(index=too_big).reset_index(drop=True)

    # restrict total download size
    repo_total_mb = float(size_num.sum(skipna=True)) / _MB
    if mode == "select" and math.isfinite(max_download_size) and repo_total_mb > max_download_size:
        need_total = math.ceil(repo_total_mb)
        cap_report(
            f"Repository {osf_id} was not downloaded: its {len(files)} file{plural(len(files))} "
            f"total {cap_num(need_total)} MB, over the {cap_num(max_download_size)} MB "
            f"per-repository limit. Set `max_download_size >= {cap_num(need_total)}` to "
            "download it."
        )
        files = files.iloc[0:0]

    # set up download directory
    download_to = _normalize(download_to)
    if os.path.isdir(download_to):
        download_to = os.path.join(download_to, osf_id).replace("\\", "/")
    resuming = os.path.isdir(download_to)
    os.makedirs(download_to, exist_ok=True)
    _tick(
        pb,
        f"- Adding to existing directory {download_to}"
        if resuming
        else f"- Created directory {download_to}",
    )

    files_to_copy: list[int] = []
    file_rows = [i for i, f in enumerate(kind_is_file(files)) if f]
    tmp_dirs: list[str] = []
    try:
        if file_rows and mode == "select":
            temppath = tempfile.mkdtemp()
            tmp_dirs.append(temppath)
            files_to_download = list(file_rows)
            files = _osf_prepare_save_paths(
                files, contents, osf_id, max_folder_length, ignore_folder_structure
            )
            providers = files["provider"].tolist()
            save_paths = files["save_path"].tolist()
            sizes = pd.to_numeric(files["size"], errors="coerce").tolist()
            if resuming and files_to_download:
                already: list[bool] = []
                for i in files_to_download:
                    on_disk = os.path.join(download_to, save_paths[i])
                    trust = is_na(providers[i]) or str(providers[i]).lower() == "osfstorage"
                    have = os.path.exists(on_disk) and not os.path.isdir(on_disk)
                    exp = sizes[i]
                    right = (not trust) or exp != exp or (have and os.path.getsize(on_disk) == exp)
                    already.append(have and right)
                n_have = sum(already)
                if n_have:
                    n_dl = len(files_to_download)
                    _message(
                        f"{n_have} of {n_dl} file{plural(n_dl)} from {osf_id} "
                        f"{'is' if n_have == 1 else 'are'} already on disk and "
                        f"{'was' if n_have == 1 else 'were'} not downloaded again."
                    )
                    files_to_copy.extend(
                        i for i, a in zip(files_to_download, already, strict=True) if a
                    )
                    files_to_download = [
                        i for i, a in zip(files_to_download, already, strict=True) if not a
                    ]

            _tick(pb, "Downloading files")
            dl_urls = [files["download_url"].iloc[i] for i in files_to_download]
            ids_col = files["osf_id"].tolist()
            dests = [os.path.join(temppath, str(ids_col[i])) for i in files_to_download]
            expected = [
                sizes[i]
                if (is_na(providers[i]) or str(providers[i]).lower() == "osfstorage")
                else math.nan
                for i in files_to_download
            ]
            errs: list[Any] = []
            if dl_urls:
                from pytacheck.archives.download import _download_many_parallel

                errs = list(_download_many_parallel(dl_urls, dests, expected))
            failed_j = [j for j, e in enumerate(errs) if not is_na(e)]
            for j in failed_j:
                logger("osf_file_download", {"error": errs[j], "url": dl_urls[j]})
            if failed_j:
                n_dl = len(files_to_download)
                first = failed_j[0]
                _message(
                    f"{len(failed_j)} of {n_dl} file{plural(n_dl)} from {osf_id} failed to "
                    f"download after retries (e.g. {os.path.basename(str(dl_urls[first]))}: "
                    f"{errs[first]})."
                )
                if any("not authorised" in str(errs[j]) for j in failed_j):
                    warnings.warn(
                        f"{osf_id} is private and its files could not be downloaded without an "
                        "authorised OSF token. The listing shows the files because listings are "
                        "authorised, but the file downloads were refused. Set a token with "
                        'osf_pat("your-token") or OSF_PAT in .Renviron, then run this again. '
                        "See ?osf_pat",
                        stacklevel=2,
                    )
            _tick(pb, "Setting up file structure")
            for i in files_to_download:
                src = os.path.join(temppath, str(ids_col[i]))
                dest = os.path.join(download_to, save_paths[i])
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    _copy_file(src, dest)
                files_to_copy.append(i)
        elif file_rows and mode == "zip":
            files_to_copy, files = _osf_zip_mode(
                files,
                contents,
                osf_id,
                download_to,
                max_download_size,
                max_folder_length,
                ignore_folder_structure,
                unzip,
                pb,
                tmp_dirs,
                cap_report,
                cap_num,
            )
    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    # return table
    folder = os.path.basename(download_to)
    kind_all = (
        [_eq(k, "file") for k in contents["kind"].tolist()]
        if "kind" in contents
        else [False] * len(contents)
    )
    ret_cols = ["osf_id", "name", "filetype", "size", "downloads", "provider"]
    ret = contents.loc[kind_all, [c for c in ret_cols if c in contents.columns]].reset_index(
        drop=True
    )
    ret.insert(0, "folder", pd.Series([folder] * len(ret), dtype="string"))
    n_file_rows = sum(kind_is_file(files))

    def joined(rows: list[int]) -> pd.DataFrame:
        copied = pd.DataFrame(
            {
                "osf_id": files["osf_id"].iloc[rows].tolist(),
                "path": pd.Series(files["save_path"].iloc[rows].tolist(), dtype="string"),
                "downloaded": pd.Series([True] * len(rows), dtype="boolean"),
            }
        ).astype({"osf_id": "string"})
        out = left_join(ret, copied, by="osf_id")
        out["downloaded"] = out["downloaded"].fillna(False).astype("boolean")
        return out

    if mode == "select" and files_to_copy:
        ret = joined(files_to_copy)
    elif mode == "zip" and n_file_rows > 0:
        if files_to_copy:
            ret = joined(files_to_copy)
        else:
            ret["path"] = pd.Series([None] * len(ret), dtype="string")
            ret["downloaded"] = pd.Series([False] * len(ret), dtype="boolean")
    else:
        # a project with folders but no files gives a zero-row table (metacheck's
        # `ret$downloaded <- FALSE` fails on it: U53)
        ret["downloaded"] = pd.Series([False] * len(ret), dtype="boolean")

    ret["download_path"] = pd.Series([download_to] * len(ret), dtype="string")
    ret["osf_project"] = pd.Series([osf_id] * len(ret), dtype="string")
    ret["osf_url"] = pd.Series([f"https://osf.io/{osf_id}"] * len(ret), dtype="string")

    if metadata is True:
        from pytacheck.archives.osf_metadata import _osf_metadata_download

        try:
            _osf_metadata_download(osf_id, download_to, pb=pb)
        except Exception as exc:
            logger(".osf_metadata_download", {"osf_id": osf_id, "error": str(exc)})
            _message(
                f"Could not retrieve metadata for {osf_id} ({exc}); its files were downloaded."
            )

    check_size = [True] * len(ret)
    if mode == "zip" and unzip is not True and "path" in ret.columns:
        for i, p in enumerate(ret["path"].tolist()):
            if not is_na(p) and str(p).endswith(".zip"):
                check_size[i] = False
    if "provider" in ret.columns:
        for i, prov in enumerate(ret["provider"].tolist()):
            if not (is_na(prov) or str(prov).lower() == "osfstorage"):
                check_size[i] = False
    ret = _osf_verify_downloads(ret, download_to, check_size=check_size)

    planned = set(files["osf_id"].tolist()) if "osf_id" in files else set()
    ret["attempted"] = pd.Series([o in planned for o in ret["osf_id"].tolist()], dtype="boolean")
    downloaded = [bool(d) for d in ret["downloaded"].fillna(False).tolist()] if len(ret) else []
    attempted = [bool(a) for a in ret["attempted"].tolist()] if len(ret) else []
    n_ok = sum(downloaded)
    failed = [i for i, (d, a) in enumerate(zip(downloaded, attempted, strict=True)) if not d and a]
    if failed:
        sizes_ret = (
            pd.to_numeric(ret["size"], errors="coerce").tolist()
            if "size" in ret
            else [0] * len(ret)
        )
        worst = sorted(
            failed, key=lambda i: -(sizes_ret[i] if sizes_ret[i] == sizes_ret[i] else -math.inf)
        )
        names = ret["name"].tolist()
        n_att = sum(attempted)
        warnings.warn(
            f"{len(failed)} of {n_att} file{plural(n_att)} from {osf_id} did not arrive on disk "
            f"(e.g. {', '.join(str(names[i]) for i in worst[:3])}). The returned table marks "
            f"{'it' if len(failed) == 1 else 'them'} downloaded = FALSE. Rerun to try again.",
            stacklevel=2,
        )
    n_skipped = len(attempted) - sum(attempted)
    skipped = f" ({n_skipped} skipped by the size limits)" if n_skipped > 0 else ""
    _tick(pb, f"{n_ok} of {sum(attempted)} files verified on disk{skipped}")
    return ret


def _normalize(path: str) -> str:
    """R ``normalizePath(path, winslash = "/", mustWork = FALSE)``.

    An existing path is made absolute (symlinks resolved); a path that does
    not exist yet comes back unchanged apart from ``~`` expansion, relative
    or not, as R returns it.
    """
    path = os.path.expanduser(str(path))
    if os.path.exists(path):
        return os.path.realpath(path).replace("\\", "/")
    return path


def _osf_zip_mode(
    files: pd.DataFrame,
    contents: pd.DataFrame,
    osf_id: str,
    download_to: str,
    max_download_size: float,
    max_folder_length: float,
    ignore_folder_structure: bool,
    unzip: bool,
    pb: Any,
    tmp_dirs: list[str],
    cap_report: Any,
    cap_num: Any,
) -> tuple[list[int], pd.DataFrame]:
    """``mode = "zip"``: one archive per node owning osfstorage files, the rest singly.

    Returns the row numbers accounted for, and *files* with ``save_path``.
    """
    from pytacheck.archives import _message, _tick
    from pytacheck.utils import path_sanitize

    kinds = files["kind"].tolist()
    provs = [None if is_na(p) else str(p).lower() for p in files["provider"].tolist()]
    projects = files["project"].tolist() if "project" in files else [None] * len(files)
    is_file = [_eq(k, "file") for k in kinds]
    zip_nodes = list(
        dict.fromkeys(
            p
            for p, f, pv in zip(projects, is_file, provs, strict=True)
            if f and pv == "osfstorage" and not is_na(p)
        )
    )
    other_idx = [
        i for i, (f, pv) in enumerate(zip(is_file, provs, strict=True)) if f and pv != "osfstorage"
    ]
    if other_idx:
        raw_provs = files["provider"].tolist()
        others = list(
            dict.fromkeys("NA" if is_na(raw_provs[i]) else str(raw_provs[i]) for i in other_idx)
        )
        n = len(other_idx)
        _message(
            f"[zip] {n} file{plural(n)} on {', '.join(others)} cannot be in an OSF archive (the "
            f"?zip= endpoint covers osfstorage only); downloading {'it' if n == 1 else 'them'} "
            "individually."
        )
    if len(zip_nodes) > 1:
        _message(
            f"[zip] osfstorage files belong to {len(zip_nodes)} nodes; requesting one archive per node."
        )

    files = _osf_prepare_save_paths(
        files, contents, osf_id, max_folder_length, ignore_folder_structure
    )
    save = files["save_path"].tolist()
    copied_rows: list[int] = []
    for node in zip_nodes:
        node_idx = [
            i
            for i, (f, p, pv) in enumerate(zip(is_file, projects, provs, strict=True))
            if f and _eq(p, node) and pv == "osfstorage"
        ]
        if not node_idx:
            continue
        zip_url = _osf_zip_url(node)
        zip_size = _osf_zip_content_length(zip_url)
        shown = (
            f"{zip_size / _MB:.1f} MB"
            if math.isfinite(zip_size)
            else "not reported by server (will stream blind)"
        )
        _message(f"[zip] {node}: {len(node_idx)} file(s), archive size {shown}")
        if (
            math.isfinite(max_download_size)
            and math.isfinite(zip_size)
            and zip_size > max_download_size * _MB
        ):
            need = math.ceil(zip_size / _MB)
            cap_report(
                f"Node {node} was not downloaded: its zip archive totals {cap_num(need)} MB, over "
                f"the {cap_num(max_download_size)} MB per-repository limit. Set "
                f"`max_download_size >= {cap_num(need)}` to download it."
            )
            continue
        zip_name = f"{path_sanitize(node, keep_sep=False)}.zip"
        zip_path = os.path.join(download_to, zip_name)
        _tick(pb, f"Downloading zip archive for {node}")
        try:
            _osf_download_zip(zip_url, zip_path, zip_size=zip_size)
        except Exception as exc:
            _message(
                f"[zip] {node}: archive download failed ({exc}); its files are downloaded "
                "individually below."
            )
            continue
        if unzip is True:
            unzip_dir = tempfile.mkdtemp(prefix="osf-zip-")
            tmp_dirs.append(unzip_dir)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(unzip_dir)
            paths = files["path"].tolist()
            copied = []
            for i in node_idx:
                rel = sub("^/+", "", "NA" if is_na(paths[i]) else str(paths[i]))
                src = os.path.join(unzip_dir, rel)
                if not os.path.exists(src):
                    continue
                dest = os.path.join(download_to, save[i])
                _copy_file(src, dest, overwrite=True)
                copied.append(i)
            copied_rows.extend(copied)
            _message(f"[zip] {node}: relocated {len(copied)} of {len(node_idx)} file(s)")
            os.remove(zip_path)
            shutil.rmtree(unzip_dir, ignore_errors=True)
        else:
            for i in node_idx:
                save[i] = zip_name
            copied_rows.extend(node_idx)
    files["save_path"] = pd.Series(save, index=files.index, dtype="string")

    left = [i for i, f in enumerate(is_file) if f and i not in set(copied_rows)]
    if left:
        _tick(pb, f"Downloading {len(left)} remaining file{plural(len(left))} individually")
        urls = files["download_url"].tolist()
        wanted = [i for i in left if not is_na(urls[i]) and str(urls[i]) != ""]
        if wanted:
            from pytacheck.archives.download import _download_many_parallel

            temppath2 = tempfile.mkdtemp()
            tmp_dirs.append(temppath2)
            ids = files["osf_id"].tolist()
            sizes = pd.to_numeric(files["size"], errors="coerce").tolist()
            dests = [os.path.join(temppath2, str(ids[i])) for i in wanted]
            expected2 = [sizes[i] if provs[i] in ("osfstorage", None) else math.nan for i in wanted]
            errs = list(_download_many_parallel([urls[i] for i in wanted], dests, expected2))
            fetched = [i for i, e in zip(wanted, errs, strict=True) if is_na(e)]
            copied_rows.extend(
                i
                for i in fetched
                if _copy_file(
                    os.path.join(temppath2, str(ids[i])),
                    os.path.join(download_to, save[i]),
                    overwrite=True,
                )
            )
            nfail = len(wanted) - len(fetched)
            if nfail > 0:
                _message(
                    f"[zip] {nfail} of {len(wanted)} individually-downloaded "
                    f"file{plural(len(wanted))} failed."
                )
    return copied_rows, files
