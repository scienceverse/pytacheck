"""PsychArchives and other legacy DSpace installations (port of ``R/archive-psycharchives.R``).

PsychArchives (ZPID) runs DSpace, whose legacy (DSpace <= 6) REST API at
``/rest/`` lists an item's public bitstreams without downloading anything:
:func:`psycharchives_file_download` returns that listing with absolute
``file_url`` values for :func:`download_repo_files` to fetch later.
Restricted bitstreams are simply not listed by the API.

Every installation in :data:`DSPACE_LEGACY_HOSTS` exposes the same API, so
the host is derived from each URL (a bare handle with no host can only be
attributed to PsychArchives' own prefix, ``20.500.12034``);
:func:`dspace_links` finds links to any of them and
:func:`psycharchives_links` only PsychArchives ones.

This module also holds helpers shared with the ResearchBox, FSD and DSpace 7
ports: the ``*_info()`` loop over distinct URLs (:func:`_info_loop`) and the
extension / ``file_types`` columns of a file listing (:func:`_add_ext_type`).
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd

from pytacheck._r import is_na, regextract, sub

__all__ = [
    "DSPACE_LEGACY_HOSTS",
    "dspace_links",
    "psycharchives_file_download",
    "psycharchives_info",
    "psycharchives_links",
]

#: R ``.dspace_legacy_hosts()``: installations answering the legacy ``/rest/`` API.
DSPACE_LEGACY_HOSTS: tuple[str, ...] = (
    "www.psycharchives.org",  # PsychArchives, ZPID (Germany)
    "bonndoc.ulb.uni-bonn.de",  # bonndoc, University of Bonn (Germany)
    "dspace.ut.ee",  # University of Tartu Library (Estonia)
    "qsardb.org",  # QsarDB
    "repositorio-digital.cide.edu",  # CIDE (Mexico)
    "ses.library.usyd.edu.au",  # University of Sydney eScholarship (Australia)
)

_HANDLE_RX = r"[0-9]{1,5}(?:\.[0-9]+){0,3}/[0-9A-Za-z.]+"


def _dspace_legacy_hosts() -> list[str]:
    """Port of R/archive-psycharchives.R::.dspace_legacy_hosts(): the known installations."""
    return list(DSPACE_LEGACY_HOSTS)


@functools.cache
def _dspace_legacy_host_regex() -> str:
    """Port of R/archive-psycharchives.R::.dspace_legacy_host_regex(): any host, as a regex."""
    return "|".join(h.replace(".", r"\.") for h in DSPACE_LEGACY_HOSTS)


def _chr_list(x: Any) -> list[str | None]:
    """``as.character(x)`` as a list (``None`` for ``NA``; ``NULL`` gives ``[]``)."""
    from pytacheck._r import as_character

    if x is None:
        return []
    vals = [x] if isinstance(x, str) or not isinstance(x, Sequence | pd.Series) else list(x)
    return [None if is_na(v) else (v if isinstance(v, str) else as_character(v)) for v in vals]


def _dspace_legacy_parse(url: Any) -> pd.DataFrame:
    """Port of R/archive-psycharchives.R::.dspace_legacy_parse(): host and handle of URLs.

    One row per URL (``host``, ``handle``). The host is the known
    installation named in the URL (lower-cased), else PsychArchives when the
    URL carries its handle prefix ``20.500.12034``; the handle is the first
    ``<prefix>/<suffix>`` in the URL, without trailing ``.,;``.
    """
    from pytacheck._r import grepl

    urls = _chr_list(url)
    n = len(urls)
    host: list[str | None] = [None] * n
    handle: list[str | None] = [None] * n
    host_rx = _dspace_legacy_host_regex()
    for i, u in enumerate(urls):
        if u is None or u == "":
            continue
        hm = regextract(host_rx, u, ignore_case=True, perl=True)
        if hm is not None:
            host[i] = hm.lower()
        elif grepl(r"20\.500\.12034/[0-9]+", u):
            host[i] = "www.psycharchives.org"
        hd = regextract(_HANDLE_RX, u, perl=True)
        if hd is not None:
            handle[i] = sub("[.,;]+$", "", hd)
    return pd.DataFrame(
        {"host": pd.Series(host, dtype="string"), "handle": pd.Series(handle, dtype="string")}
    )


def _psycharchives_handle(url: Any) -> str | None:
    """Port of R/archive-psycharchives.R::.psycharchives_handle(): the handle of a reference.

    The handle (e.g. ``"20.500.12034/17526"``) in a ``hdl.handle.net`` URL, an
    item page or a bare handle; ``None`` when there is none. Only the first
    element of a vector is used; an empty vector is an error, as in R.
    """
    parsed = _dspace_legacy_parse(url)
    if len(parsed) == 0:
        raise IndexError("subscript out of bounds")
    v = parsed["handle"].iloc[0]
    return None if is_na(v) else str(v)


def dspace_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-psycharchives.R::dspace_links(): legacy DSpace links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table to any host
    in :data:`DSPACE_LEGACY_HOSTS`, plus bare mentions in the text of such a
    host or of a PsychArchives handle (``20.500.12034/...``). Trailing
    slashes are stripped and duplicate rows dropped.
    """
    from pytacheck.archives.dataone import _scan_links
    from pytacheck.archives.dataverse import _collect_links, _url_rows

    host_regex = _dspace_legacy_host_regex()
    found_href = _url_rows(paper, host_regex)
    bare = (
        f"(?:https?://)?(?:www\\.)?(?:{host_regex})/[A-Za-z0-9/._-]+"
        r"|(?:https?://)?(?:hdl\.handle\.net/)?20\.500\.12034/[0-9]+"
    )
    literals = [*(f"{h}/" for h in DSPACE_LEGACY_HOSTS), "20.500.12034/"]
    return _collect_links([found_href, _scan_links(paper, bare, literals)])


def psycharchives_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-psycharchives.R::psycharchives_links(): PsychArchives links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ``psycharchives.org`` or the ``20.500.12034/`` handle prefix, plus bare
    item-page URLs and handles in the text. Trailing slashes are stripped and
    duplicate rows dropped.
    """
    from pytacheck.archives.dataone import _scan_links
    from pytacheck.archives.dataverse import _collect_links, _url_rows

    found_href = _url_rows(paper, r"psycharchives\.org|20\.500\.12034/")
    bare = (
        r"(?:https?://)?(?:www\.)?psycharchives\.org/[A-Za-z0-9/._-]+"
        r"|(?:https?://)?(?:hdl\.handle\.net/)?20\.500\.12034/[0-9]+"
    )
    other = _scan_links(paper, bare, ["psycharchives.org/", "20.500.12034/"])
    return _collect_links([found_href, other])


# ---------------------------------------------------------------------------
# the *_info() loop shared by PsychArchives, ResearchBox and FSD
# ---------------------------------------------------------------------------


def _column(table: pd.DataFrame, id_col: int | str) -> str:
    """``colnames(table[id_col])``: the name of a 1-based position or a column name."""
    if isinstance(id_col, int | float) and not isinstance(id_col, bool):
        k = int(id_col)
        if not 1 <= k <= table.shape[1]:
            raise IndexError("undefined columns selected")
        return str(table.columns[k - 1])
    if id_col not in table.columns:
        raise KeyError("undefined columns selected")
    return str(id_col)


def _info_loop(
    x: Any,
    id_col: int | str,
    pb: Any,
    cache: bool,
    *,
    url_col: str,
    label: str,
    noun: str,
    cache_ns: str | None,
    one: Callable[[Any], pd.DataFrame],
    suffix: str,
) -> pd.DataFrame:
    """The body shared by ``psycharchives_info()``, ``rbox_info()`` and ``fsd_info()``.

    Looks each distinct non-missing URL up with *one* (stopping after the
    first result with an ``error`` column), then joins the results back onto
    the input rows (a table's *id_col*, or ``data.frame(<url_col> = x)``).
    """
    from pytacheck._r import as_character, bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import _as_values
    from pytacheck.utils import left_join

    with _spinner(pb, f"{label} Retrieve") as bar:
        if isinstance(x, pd.DataFrame):
            table = x
            id_col_name = _column(table, id_col)
            raw = table[id_col_name].tolist()
        elif x is None:
            # R: data.frame(<url_col> = NULL) has no columns, and is returned as is
            _tick(bar, f"No valid {label} links")
            return pd.DataFrame(index=pd.RangeIndex(0))
        else:
            id_col_name = url_col
            vals, _ = _as_values(x)
            raw = [v for v in dict.fromkeys(None if is_na(v) else v for v in vals) if v is not None]
            table = pd.DataFrame({url_col: _vector(raw)})

        present = [v for v in raw if not is_na(v)]
        ids = pd.DataFrame({url_col: _vector(present)}).drop_duplicates().reset_index(drop=True)
        valid = list(dict.fromkeys(present))

        if not valid:
            _tick(bar, f"No valid {label} links")
            return table

        nv = len(valid)
        _tick(bar, f"Starting {label} retrieval for {nv} {noun}{'' if nv == 1 else 's'}...")
        id_info = []
        for v in valid:
            key = v if isinstance(v, str) else as_character(v)
            info = _cached(cache_ns, key, cache, lambda v=v: one(v))
            id_info.append(info)
            if "error" in info.columns:
                break

        info = left_join(bind_rows(id_info), ids, by=url_col)
        data = left_join(table, info, by={id_col_name: url_col}, suffix=("", suffix))
        _tick(bar, f"...{label} retrieval complete!")
        return data


def _obj_cell(value: Any) -> pd.Series:
    """A one-row list column holding *value* (R ``obj$x <- list(value)``), e.g. a table."""
    import numpy as np

    arr = np.empty(1, dtype=object)
    arr[0] = value
    return pd.Series(arr, dtype=object)


def _url_values(x: Any) -> list[str | None]:
    """A URL argument as a list: a string or ``None`` (R's ``NA``) is one element."""
    if x is None or isinstance(x, str):
        return [x]
    return _chr_list(x)


def _vector(values: list[Any]) -> pd.Series:
    """An R vector from Python values (``string`` dtype for strings and ``None``)."""
    if all(v is None or isinstance(v, str) for v in values):
        return pd.Series(values, dtype="string")
    return pd.Series(values)


def _cached(ns: str | None, key: Any, cache: bool, fetch: Callable[[], pd.DataFrame]) -> Any:
    """``.repo_info_cache_get()``, else *fetch* and ``.repo_info_cache_put()`` a good result."""
    if ns is None:
        return fetch()
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )

    cached = _repo_info_cache_get(ns, key) if cache is True else None
    if cached is not None:
        return cached
    got = fetch()
    if cache is True and _repo_info_ok(got):
        _repo_info_cache_put(ns, key, got)
    return got


def psycharchives_info(
    pa_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-psycharchives.R::psycharchives_info(): look up PsychArchives items.

    *pa_url* is an item URL, handle or ``hdl.handle.net`` link, a sequence of
    them, or a table whose *id_col* (1-based position or name) holds them
    (e.g. from :func:`psycharchives_links`). Each distinct URL is looked up
    in turn (with *cache*, from the on-disk listing cache when possible),
    stopping at the first that fails; the input rows are returned with
    ``PA_title``, ``PA_authors``, ``PA_doi``, ``PA_license``, ``PA_date``,
    ``PA_abstract`` and ``files`` (or ``error``) added.
    """
    from pytacheck.utils import online

    if not online("psycharchives.org"):
        raise ConnectionError("PsychArchives.org seems to be offline")
    return _info_loop(
        pa_url,
        id_col,
        pb,
        cache,
        url_col="pa_url",
        label="PsychArchives",
        noun="item",
        cache_ns="psycharchives",
        one=_psycharchives_info,
        suffix=".pa",
    )


def _psycharchives_info(pa_url: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-psycharchives.R::.psycharchives_info(): one legacy DSpace item.

    Resolves the URL's handle on its host's REST API and returns a one-row
    table: ``pa_url`` and either ``error`` (``"unfound"``, with a warning) or
    ``PA_title``, ``PA_authors``, ``PA_doi``, ``PA_license``, ``PA_date``,
    ``PA_abstract`` and ``files`` (``name``, ``size``, ``retrieve``: the
    public bitstreams).
    """
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import _cell, _dollar, _empty_or, _field_cell, _paste

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from {_paste(pa_url)}...")
        obj: dict[str, pd.Series] = {"pa_url": _cell(pa_url)}

        parsed = _dspace_legacy_parse([None] if pa_url is None else pa_url)
        if len(parsed) == 0:
            raise IndexError("subscript out of bounds")
        host_v = parsed["host"].iloc[0]
        # a handle on no known host is looked up on PsychArchives, the intent of
        # metacheck's `%||%`, which never falls back from NA (it requests
        # https://NA/rest/...: U42)
        host = "www.psycharchives.org" if is_na(host_v) else str(host_v)
        handle_v = parsed["handle"].iloc[0]
        if is_na(handle_v):
            warnings.warn(f"{_paste(pa_url)} is not a valid PsychArchives handle", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)
        handle = str(handle_v)

        item = _psycharchives_rest(f"/handle/{handle}", host=_paste(host))
        uuid = _dollar(item, "uuid")
        if item is None or uuid is None:
            warnings.warn(f"{_paste(pa_url)} could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        # R: paste0("/items/", uuid, ...) -- several strings for an array, which
        # httr2::request() refuses inside .psycharchives_rest()'s tryCatch (NULL)
        upath = _url_piece(uuid)
        meta = (
            None
            if upath is None
            else _psycharchives_rest(f"/items/{upath}?expand=metadata", host=_paste(host))
        )
        md = _dollar(meta, "metadata")
        if md is None:
            md = []

        def md_val(key: str) -> str | None:
            from pytacheck.archives.dataverse import _elements

            vals = []
            for m in _elements(md):
                k = _dollar(m, "key")
                if isinstance(k, str) and k == key:
                    vals.append(_chr1(_empty_or(_dollar(m, "value"), None)))
                else:
                    vals.append(None)
            vals = [v for v in vals if v is not None]
            return "; ".join(vals) if vals else None

        title = _empty_or(_dollar(item, "name"), None)
        obj["PA_title"] = _field_cell(title if title is not None else md_val("dc.title"))
        obj["PA_authors"] = _cell(md_val("dc.contributor.author"))
        obj["PA_doi"] = _cell(md_val("dc.identifier.doi"))
        obj["PA_license"] = _cell(md_val("dc.rights"))
        obj["PA_date"] = _cell(md_val("dc.date.available"))
        obj["PA_abstract"] = _cell(md_val("dc.description.abstract"))

        bitstreams = (
            None
            if upath is None
            else _psycharchives_rest(f"/items/{upath}/bitstreams?limit=1000", host=_paste(host))
        )
        obj["files"] = _obj_cell(_bitstream_table(bitstreams, _paste(host)))
        return pd.DataFrame(obj)


def _bitstream_table(bitstreams: Any, host: str) -> pd.DataFrame:
    """The ``file_list`` of ``.psycharchives_info()``: ``name``, ``size``, ``retrieve``."""
    from pytacheck.archives.dataverse import (
        _as_numeric,
        _dollar,
        _elements,
        _empty_or,
        _is_empty,
    )

    # R: length(bitstreams) == 0 -> an empty table; a JSON scalar has length 1
    items = [] if _is_empty(bitstreams) else _elements(bitstreams)
    names = [_chr1(_empty_or(_dollar(b, "name"), None)) for b in items]
    sizes = [_as_numeric(_empty_or(_dollar(b, "sizeBytes"), None)) for b in items]
    retrieve = []
    for b in items:
        link = _dollar(b, "retrieveLink")
        retrieve.append(None if link is None else f"https://{host}{_paste_json(link)}")
    return pd.DataFrame(
        {
            "name": pd.Series(names, dtype="string"),
            "size": pd.Series(sizes, dtype="float64"),
            "retrieve": pd.Series(retrieve, dtype="string"),
        }
    )


def _chr1(x: Any) -> str | None:
    """A ``vapply(..., character(1))`` result from parsed JSON: a string or ``NA``.

    Unlike jsonlite's simplified vectors, ``resp_body_json()`` keeps a JSON
    array as a list, so even a one-string array (``["a"]``) is R's error
    "values must be type 'character'", as are numbers and booleans.
    """
    from pytacheck.archives.dataverse import _chr_elt

    if isinstance(x, list | tuple | dict):
        raise TypeError("values must be type 'character',\n but FUN(X[[1]]) result is type 'list'")
    return _chr_elt(x)


class PasteLengthError(ValueError):
    """``paste0()`` of a JSON array with several elements gives several strings."""


def _url_piece(x: Any) -> str | None:
    """:func:`_paste_json` of an id spliced into a request URL; ``None`` for an array
    of several ids (R builds several URLs, which ``httr2::request()`` refuses)."""
    try:
        return _paste_json(x)
    except PasteLengthError:
        return None


def _paste_json(x: Any) -> str:
    """``paste0()`` piece for a parsed JSON value: R's ``as.character()`` of it.

    Strings as is, numbers as R prints them, ``NULL`` as ``"NA"`` (a missing
    field, R's ``NA`` here). An array or object is an R list: empty, it is
    dropped by ``paste0()`` (``""``); with one element, that element (a
    nested array, object or ``null`` deparsed, as ``"list(1, 2)"`` or
    ``"NULL"``); with more, ``paste0()`` returns several strings, which
    every caller here fails on: :class:`PasteLengthError`.
    """
    from pytacheck.archives.dataverse import _json_chr

    if isinstance(x, str):
        return x
    if isinstance(x, list | tuple | dict):
        items = list(x.values()) if isinstance(x, dict) else list(x)
        if not items:
            return ""
        if len(items) > 1:
            raise PasteLengthError(
                f"values must be length 1,\n but FUN(X[[1]]) result is length {len(items)}"
            )
        v = items[0]
        return (
            _deparse_json(v) if v is None or isinstance(v, list | tuple | dict) else _paste_json(v)
        )
    s = _json_chr(x)
    return "NA" if s is None else s


def _deparse_json(x: Any) -> str:
    """R ``deparse()`` of a parsed JSON value (as ``as.character()`` of a list shows it)."""
    from pytacheck._r import grepl
    from pytacheck.archives.dataverse import _json_chr

    if x is None:
        return "NULL"
    if isinstance(x, str):
        esc = x.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return f'"{esc}"'
    if isinstance(x, list | tuple):
        return "list(" + ", ".join(_deparse_json(v) for v in x) + ")"
    if isinstance(x, dict):
        parts = []
        for k, v in x.items():
            name = k if grepl("^((([A-Za-z]|[.][._A-Za-z])[._A-Za-z0-9]*)|[.])$", k) else f"`{k}`"
            parts.append(f"{name} = {_deparse_json(v)}")
        return "list(" + ", ".join(parts) + ")"
    s = _json_chr(x)
    return "NA" if s is None else s


def _psycharchives_rest(path: str, host: str = "www.psycharchives.org") -> Any:
    """Port of R/archive-psycharchives.R::.psycharchives_rest(): one legacy DSpace REST call.

    GETs ``https://<host>/rest<path>`` asking for JSON; returns the parsed
    body, or ``None`` on any failure or non-200 status.
    """
    return _rest_json(f"https://{host}/rest{path}")


def _rest_json(url: str) -> Any:
    """``tryCatch(request |> Accept json |> perform; 200 ? resp_body_json : NULL, error = NULL)``."""
    from pytacheck import http

    try:
        resp = http.request("GET", url, headers={"Accept": "application/json"}, max_tries=1)
    except Exception:
        return None
    if resp is None or resp.status_code != 200:
        return None
    return _resp_json(resp)


def _resp_json(resp: Any) -> Any:
    """``tryCatch(httr2::resp_body_json(resp), error = \\(e) NULL)`` (``None`` on failure)."""
    from pytacheck.archives.osf_helpers import _resp_body_json

    try:
        return _resp_body_json(resp)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# file listings
# ---------------------------------------------------------------------------


def _file_ext(names: Sequence[Any]) -> list[str]:
    """``tolower(sapply(strsplit(name, "\\\\."), last piece or ""))``."""
    from pytacheck._r import strsplit

    pieces = strsplit([None if is_na(v) else str(v) for v in names], r"\.")
    out = []
    for p in pieces:
        if p is None or len(p) < 2:
            out.append("")
        else:
            last = p[-1]
            out.append("NA" if last is None else last.lower())
    return out


def _add_ext_type(df: pd.DataFrame) -> pd.DataFrame:
    """``df$ext <- <extension>; left_join(df, metacheck::file_types, by = "ext")``.

    One row per file: an extension listed under several types takes the first
    (metacheck repeats the file once per type: U46).
    """
    from pytacheck.archives.github import _file_types
    from pytacheck.utils import left_join

    df = df.copy()
    df["ext"] = pd.Series(_file_ext(df["name"].tolist()), dtype="string", index=df.index)
    return left_join(df, _file_types(), by="ext").reset_index(drop=True)


_EMPTY_FILES = {
    "pa_url": "string",
    "name": "string",
    "file_url": "string",
    "file_location": "string",
    "size": "float64",
    "isdir": "boolean",
    "ext": "string",
    "type": "string",
}


def psycharchives_file_download(pa_url: Any, pb: Any = None, cache: bool = False) -> Any:
    """Port of R/archive-psycharchives.R::psycharchives_file_download(): list public files.

    Lists the public bitstreams of one or more items without downloading
    them: one row per file with ``pa_url``, ``name``, ``file_url`` (the
    bitstream's retrieve URL, fetched later by ``download_repo_files()``),
    ``file_location`` (``NA``), ``size``, ``isdir``, ``ext`` and ``type``.
    Each item's rights statement and DOI are carried in the table's
    ``attrs["rights"]`` / ``attrs["doi"]`` (``{url: value}``; R: named
    vectors in attributes), also for an item that lists no public files (a
    zero-row table). ``None`` when the item could not be found (or *pa_url*
    is ``None``, R's ``NA``). A sequence of URLs gives one table, aligned with
    the input. With *cache*, listings are reused from the on-disk cache.
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import _paste
    from pytacheck.utils import left_join

    urls = _url_values(pa_url)
    with _spinner(pb) as bar:
        if len(urls) > 1:
            unique_pa = [u for u in dict.fromkeys(urls) if u is not None]
            file_lists = [psycharchives_file_download(u, pb=bar, cache=cache) for u in unique_pa]
            info = bind_rows(file_lists)
            orig = pd.DataFrame({"pa_url": pd.Series(urls, dtype="string")})
            if "pa_url" not in info.columns:
                return None  # every URL failed (metacheck's join errors here: U43)
            df = left_join(orig, info, by="pa_url")
            # R: unlist(lapply(file_lists, attr, "rights")) -- NULL (no attribute) if none
            for name in ("rights", "doi"):
                merged: dict[str, Any] = {}
                for fl in file_lists:
                    if fl is not None:
                        merged.update(fl.attrs.get(name) or {})
                if merged:
                    df.attrs[name] = merged
            return df

        if not urls:
            raise IndexError("subscript out of bounds")
        url = urls[0]
        _tick(bar, f"* Listing files from {_paste(url)}...")
        info = _cached(
            None if url is None else "psycharchives",
            url,
            cache,
            lambda: _psycharchives_info(url, pb=bar),
        )
        if "error" in info.columns:
            return None

        def attr(col: str) -> dict[str, Any]:
            v = info[col].iloc[0] if col in info.columns and len(info) else None
            return {_paste(url): None if is_na(v) else v}

        rights, doi = attr("PA_license"), attr("PA_doi")
        file_list = info["files"].iloc[0] if "files" in info.columns and len(info) else None
        if file_list is None or len(file_list) == 0:
            empty = pd.DataFrame({k: pd.Series([], dtype=t) for k, t in _EMPTY_FILES.items()})
            empty.attrs["rights"] = rights
            empty.attrs["doi"] = doi
            return empty

        n = len(file_list)
        df = pd.DataFrame(
            {
                "pa_url": pd.Series([url] * n, dtype="string"),
                "name": file_list["name"].astype("string").reset_index(drop=True),
                "file_url": file_list["retrieve"].astype("string").reset_index(drop=True),
                "file_location": pd.Series([None] * n, dtype="string"),
                "size": file_list["size"].astype("float64").reset_index(drop=True),
                "isdir": pd.Series([False] * n, dtype="boolean"),
            }
        )
        df = _add_ext_type(df)
        df.attrs["rights"] = rights
        df.attrs["doi"] = doi
        return df
