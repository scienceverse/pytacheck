"""UK Data Service ReShare (port of ``R/archive-reshare.R``).

ReShare runs EPrints, whose REST API returns a deposit's metadata as JSON at
``/id/eprint/<id>`` (the numeric id is also the last segment of the
deposit's ``10.5255/UKDA-SN-<id>`` DOI) and serves each file from its own
``/id/file/<fileid>`` URL. :func:`reshare_file_download` therefore downloads
file by file, with the same size caps, zip-member extraction and
verification as the Dataverse/Figshare/Dryad ports.

This module also holds the ``*_info()`` body shared with the Mendeley Data
port (:func:`_info_by_id`).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

import pandas as pd

from metacheck._r import is_na

__all__ = ["reshare_file_download", "reshare_info", "reshare_links"]

_ID_PATTERNS = (r"10\.5255/UKDA-SN-([0-9]+)", r"reshare\.ukdataservice\.ac\.uk/([0-9]+)")


def reshare_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-reshare.R::reshare_links(): ReShare links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ReShare or a ``10.5255/UKDA-SN`` DOI, plus bare deposit URLs and DOIs in
    the text. Trailing slashes are stripped and duplicate rows dropped;
    ``reshare_url`` and ``reshare_id`` columns are added.
    """
    from metacheck.archives.dataone import _scan_links
    from metacheck.archives.dataverse import _collect_links, _string_series, _url_rows

    found_href = _url_rows(paper, r"reshare\.ukdataservice\.ac\.uk|10\.5255/ukda-sn")
    bare = (
        r"(?:https?://)?(?:www\.)?reshare\.ukdataservice\.ac\.uk/[0-9]+/?"
        r"|(?:https?://)?(?:dx\.)?(?:doi\.org/)?10\.5255/UKDA-SN-[0-9]+"
    )
    other = _scan_links(paper, bare, ["reshare.ukdataservice.ac.uk/", "10.5255/ukda-sn-"])
    links = _collect_links([found_href, other])
    links["reshare_url"] = links["href"]
    links["reshare_id"] = _string_series([_reshare_id_one(u) for u in links["href"].tolist()])
    return links


def _id_one(x: Any, simple: str, patterns: tuple[str, ...]) -> str | None:
    """The single-value body of the ``.<archive>_id()`` functions.

    ``trimws(as.character(x))``; a value matching *simple* is an id as is,
    else the first group of the first of *patterns* that matches (PCRE,
    caseless); ``None`` for ``NA``, ``""`` or no match.
    """
    from metacheck._r import grepl, regexec
    from metacheck.archives.dataone import _clean_one

    s = _clean_one(x)
    if s is None:
        return None
    if grepl(simple, s):
        return s
    for pattern in patterns:
        groups = regexec(pattern, s, perl=True, ignore_case=True)
        if len(groups) >= 2:
            return str(groups[1])
    return None


def _reshare_id_one(x: Any) -> str | None:
    return _id_one(x, "^[0-9]+$", _ID_PATTERNS)


def _unique_ids(ids: Any) -> list[str]:
    """``ids |> stats::na.omit() |> unique()`` of an ``.<archive>_id()`` result."""
    vals = ids if isinstance(ids, list) else [ids]
    return list(dict.fromkeys(v for v in vals if v is not None))


def _reshare_id(reshare_url: Any) -> Any:
    """Port of R/archive-reshare.R::.reshare_id(): EPrints ids from ReShare URLs or DOIs.

    A digits-only value is an id; otherwise the id is taken from a
    ``10.5255/UKDA-SN-<id>`` DOI or a ``reshare.ukdataservice.ac.uk/<id>``
    URL; ``None`` where there is none. A string gives a string, a sequence a
    list.
    """
    from metacheck.archives.dataone import _map

    return _map(reshare_url, _reshare_id_one)


def _info_by_id(
    x: Any,
    id_col: int | str,
    pb: Any,
    cache: bool,
    *,
    url_col: str,
    id_name: str,
    to_id: Callable[[Any], str | None],
    label: str,
    noun: str,
    cache_ns: str,
    one: Callable[..., pd.DataFrame],
    suffix: str,
) -> pd.DataFrame:
    """The body shared by ``reshare_info()`` and ``mendeley_info()``.

    Builds the input table (a table's *id_col* copied to *url_col*, its stale
    *id_name* column dropped; or ``data.frame(<url_col> = na.omit(unique(x)))``),
    resolves ids with *to_id*, looks every distinct id up with *one* (with
    *cache*, from the on-disk listing cache when possible) and joins the
    results back onto the input rows.
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _info_table, _string_series
    from metacheck.archives.psycharchives import _cached
    from metacheck.utils import left_join

    with _spinner(pb, f"{label} Retrieve") as bar:
        table = _info_table(x, id_col, url_col, (id_name,))
        urls = table[url_col].tolist()
        ids = pd.DataFrame(
            {url_col: table[url_col].to_numpy(), id_name: _string_series([to_id(u) for u in urls])}
        )
        if all(isinstance(v, str) or is_na(v) for v in urls):
            ids[url_col] = ids[url_col].astype("string")
        ids = ids.drop_duplicates()
        ids = ids[ids[url_col].notna().to_numpy()].reset_index(drop=True)
        valid = list(dict.fromkeys(v for v in ids[id_name].tolist() if not is_na(v)))

        if not valid:
            _tick(bar, f"No valid {label} links")
            return left_join(table, ids, by=url_col)

        nv = len(valid)
        _tick(bar, f"Starting {label} retrieval for {nv} {noun}{'' if nv == 1 else 's'}...")
        id_info = [_cached(cache_ns, v, cache, lambda v=v: one(v, pb=bar)) for v in valid]
        info = bind_rows(id_info)
        data = left_join(table, ids, by=url_col)
        data = left_join(data, info, by=id_name, suffix=("", suffix))
        _tick(bar, f"...{label} retrieval complete!")
        return data


def reshare_info(
    reshare_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-reshare.R::reshare_info(): look up ReShare deposits.

    *reshare_url* is a URL/DOI/id, a sequence of them, or a table whose
    *id_col* (1-based position or name) holds them (e.g. from
    :func:`reshare_links`). Each deposit is fetched from the EPrints API
    (with *cache*, from the on-disk listing cache when possible); the input
    rows are returned with ``reshare_id`` and the deposit's ``title``,
    ``doi``, ``publication_date``, ``updated_date``, ``authors``,
    ``license`` and ``files`` (or ``error``) added.
    """
    from metacheck.utils import online

    if not online("reshare.ukdataservice.ac.uk"):
        raise ConnectionError("ReShare seems to be offline")
    return _info_by_id(
        reshare_url,
        id_col,
        pb,
        cache,
        url_col="reshare_url",
        id_name="reshare_id",
        to_id=_reshare_id_one,
        label="ReShare",
        noun="deposit",
        cache_ns="reshare",
        one=_reshare_info,
        suffix=".reshare",
    )


def _reshare_info(reshare_id: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-reshare.R::.reshare_info(): one ReShare deposit.

    Returns a one-row table: ``reshare_id`` and either ``error``
    (``"unfound"``, with a warning, or ``"parse_error"``) or ``title``,
    ``doi``, ``publication_date``, ``updated_date``, ``authors``,
    ``license`` (always ``NA``) and ``files`` (every file of every document,
    as parsed JSON records with the document's ``content`` added).
    """
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import (
        _cell,
        _dollar,
        _elements,
        _empty_or,
        _field_cell,
        _paste,
        _query,
    )
    from metacheck.archives.psycharchives import _obj_cell, _resp_json

    with _spinner(pb) as bar:
        rid = _paste(reshare_id)
        _tick(bar, f"* Retrieving info from ReShare eprint {rid}...")
        obj: dict[str, pd.Series] = {"reshare_id": _cell(None if is_na(reshare_id) else rid)}

        resp = _query(f"https://reshare.ukdataservice.ac.uk/id/eprint/{rid}", _reshare_headers)
        if resp is None or resp.status_code != 200:
            warnings.warn(f"{rid} could not be found on ReShare", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        rec = _resp_json(resp)
        if rec is None:
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)

        creators = _dollar(rec, "creators")
        authors = (
            [_creator(a) for a in _elements(creators)] if isinstance(creators, list | dict) else []
        )

        docs = _dollar(rec, "documents")
        files_flat: list[Any] = []
        for d in _elements(docs):
            content = _dollar(d, "content")
            files_flat.extend(_set_content(f, content) for f in _elements(_dollar(d, "files")))

        obj["title"] = _field_cell(_empty_or(_dollar(rec, "title"), None))
        obj["doi"] = _field_cell(_empty_or(_dollar(rec, "doi"), None))
        obj["publication_date"] = _field_cell(_empty_or(_dollar(rec, "datestamp"), None))
        obj["updated_date"] = _field_cell(_empty_or(_dollar(rec, "lastmod"), None))
        obj["authors"] = _obj_cell(authors)
        obj["license"] = _cell(None)
        obj["files"] = _obj_cell(files_flat)
        return pd.DataFrame(obj)


def _creator(a: Any) -> str | None:
    """One EPrints creator: ``trimws(paste(given, family))``, ``NA`` when blank."""
    from metacheck._r import trimws
    from metacheck.archives.dataverse import _dollar, _empty_or
    from metacheck.archives.psycharchives import _paste_json

    nm = _dollar(a, "name")
    if nm is None:
        nm = {}
    given = _paste_json(_empty_or(_dollar(nm, "given"), ""))
    family = _paste_json(_empty_or(_dollar(nm, "family"), ""))
    full = trimws(f"{given} {family}")
    return str(full) if full else None


def _set_content(f: Any, content: Any) -> Any:
    """R ``f$content <- d$content %||% NA_character_`` on a parsed JSON file record."""
    value = content  # None is R's NA here
    if isinstance(f, dict):
        out = dict(f)
        out["content"] = value
        return out
    if isinstance(f, list):
        return [*f, value]  # an unnamed list gains one (named) element
    if f is None:  # a JSON null: R's NULL$content <- x is list(content = x)
        return {"content": value}
    # R: `$<-` on an atomic value warns "Coercing LHS to a list" and gives
    # list(<value>, content = ...): an unnamed element, then a named one
    warnings.warn("Coercing LHS to a list", stacklevel=3)
    return {"": f, "content": value}


def _reshare_headers(req: dict[str, Any]) -> dict[str, Any]:
    """Port of R/archive-reshare.R::.reshare_headers(): add ``User-Agent: metacheck``.

    *req* is a request spec (``{"method", "url", "headers", ...}``, as used by
    :func:`metacheck.http.batch_query`); a new spec is returned. ReShare has
    no token scheme, so only public deposits can be read.
    """
    spec = dict(req)
    headers = dict(spec.get("headers") or {})
    headers["User-Agent"] = "metacheck"
    spec["headers"] = headers
    return spec


_FILE_COLUMNS = (
    "folder",
    "reshare_id",
    "id",
    "key",
    "path",
    "size",
    "size_on_disk",
    "checksum",
    "checksum_ok",
    "self",
    "downloaded",
    "extracted",
)


def reshare_file_download(
    reshare_id: Any,
    download_to: str = ".",
    max_file_size: float | None = 10,
    max_download_size: float | None = 100,
    unzip_types: Any = None,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-reshare.R::reshare_file_download(): download a deposit's files.

    *reshare_id* is an eprint id, URL or DOI, or a sequence of them. Creates
    a ``reshare_<id>`` folder under *download_to* (``_1``, ``_2``... when it
    exists) and downloads each file into it. Files over *max_file_size* MB
    are skipped, then the largest files until the total is under
    *max_download_size* MB (``None``/``inf``: no limit). Zips are downloaded
    whole unless *unzip_types* names file categories (as
    ``data_classify_files()`` does) to extract from them instead. Returns the
    file table (``None`` when the deposit lists no files); files left out by
    the size limits have ``downloaded = False`` (when all are, no folder is
    made and ``folder`` is missing), and files that did not arrive intact
    have ``downloaded = False`` and are warned about.
    """
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import (
        _as_numeric,
        _dollar,
        _download_file_table,
        _download_many,
        _elements,
        _empty_or,
        _file_frame,
        _finish_file_table,
        _json_chr,
        _lower,
    )

    ids = _unique_ids(_reshare_id(reshare_id))
    if not ids:
        return None

    with _spinner(pb, "ReShare File Download") as bar:
        if len(ids) > 1:
            n = len(ids)
            return _download_many(
                ids,
                lambda x: reshare_file_download(
                    x,
                    download_to=download_to,
                    max_file_size=max_file_size,
                    max_download_size=max_download_size,
                    unzip_types=unzip_types,
                    pb=bar,
                ),
                lambda x: x,
                bar,
                f"Starting downloads for {n} ReShare deposit{'' if n == 1 else 's'}...\n",
                f"...Completed downloads for {n} ReShare deposit{'' if n == 1 else 's'}",
            )

        rid = ids[0]
        _tick(bar, f"Starting retrieval for ReShare deposit {rid}")
        contents = _reshare_info(rid, pb=bar)
        files_list: Any = []
        if "files" in contents.columns and len(contents) > 0:
            files_list = contents["files"].iloc[0]
        if files_list is None or len(files_list) == 0:
            _tick(bar, f"- {rid} contained no files")
            return None

        rows = []
        for x in _elements(files_list):
            self_url = _empty_or(_dollar(x, "uri"), None)
            if self_url is not None:
                from metacheck._r import sub

                self_url = sub("^http://", "https://", self_url)
            rows.append(
                {
                    "id": _json_chr(_empty_or(_dollar(x, "fileid"), None)),
                    "key": _empty_or(_dollar(x, "filename"), None),
                    "size": _as_numeric(_empty_or(_dollar(x, "filesize"), None)),
                    "checksum": _empty_or(_dollar(x, "hash"), None),
                    "checksum_type": _lower(_empty_or(_dollar(x, "hash_type"), None)),
                    "self": self_url,
                }
            )
        files = _file_frame(rows, typed=True)
        if len(files) == 0:
            from metacheck.archives import _message

            _message("- ", rid, " contained no files")
            return None

        done = _download_file_table(
            files,
            ident=rid,
            folder_name=f"reshare_{rid}",
            download_to=download_to,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            unzip_types=unzip_types,
            pb=bar,
            headers=_reshare_headers,
            zip_members=lambda *a, **k: _reshare_zip_members(*a, **k),
            verify=lambda f, to: _reshare_verify_downloads(f, to),
            what="ReShare deposit",
        )
        if done is None:
            return None
        files, folder = done
        return _finish_file_table(files, folder, {"reshare_id": rid}, _FILE_COLUMNS)


def _reshare_zip_members(
    url: str,
    dest: str,
    keep_types: Any = ("data", "documentation"),
    max_file_size: float | None = 10,
) -> pd.DataFrame | None:
    """Port of R/archive-reshare.R::.reshare_zip_members(): extract wanted zip members.

    Reads the zip's central directory with byte-range requests and fetches
    only members of the *keep_types* categories no larger than
    *max_file_size* MB into *dest*. Returns one row per member (``name``,
    ``path``, ``size``, ``ok``), or ``None`` when the archive cannot be listed
    (the caller then downloads it whole).
    """
    from metacheck.archives.dataverse import _zip_members

    return _zip_members(url, dest, keep_types, max_file_size)


def _reshare_verify_downloads(files: pd.DataFrame | None, download_to: str) -> Any:
    """Port of R/archive-reshare.R::.reshare_verify_downloads(): check files on disk.

    ``downloaded`` becomes whether each file is on disk under *download_to*
    with the reported size and (for an MD5 ``checksum_type``) checksum; adds
    ``size_on_disk`` and ``checksum_ok``. Rows extracted from a zip count as
    downloaded.
    """
    from metacheck.archives.dataverse import _verify_file_table

    return _verify_file_table(files, download_to, typed=True)
