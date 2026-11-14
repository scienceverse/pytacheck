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

from metacheck._values import field, is_missing
from metacheck.archives import _repo_tables as _tables

__all__ = [
    "EPRINTS_HOSTS",
    "eprints_links",
    "reshare_file_download",
    "reshare_info",
    "reshare_links",
]

#: R ``.reshare_id()``'s patterns: a UK Data Service DOI, or ReShare's own address (``/<id>``, or
#: EPrints' record address ``/id/eprint/<id>``, metacheck #637)
_ID_PATTERNS = (
    r"10\.5255/UKDA-SN-([0-9]+)",
    r"reshare\.ukdataservice\.ac\.uk/(?:id/eprint/)?([0-9]+)",
)

# ReShare is one EPrints installation; every EPrints data repository answers the same
# /id/eprint/<id> request (metacheck verified live 2026-11-13: JSON with the same
# documents[].files[] shape). So the functions below take the host from the link, as the
# legacy DSpace functions do, and eprints_links() is the general (all-hosts) counterpart to
# reshare_links() (metacheck #637). A deposit on ReShare keeps its plain eprint id
# ("854001"); one on another host is "<host>/<id>" ("datacompass.lshtm.ac.uk/4333").

#: R ``.eprints_hosts()``: ReShare, UCLan's data repository (its DOIs, 10.17030, are registered
#: at uclandata.uclan.ac.uk, while DataCite's sample landed on its older name
#: data.lancashire.ac.uk), and the EPrints hosts the repository survey found. Most of those
#: mainly hold papers and theses, but they hold datasets too (eprints.soton.ac.uk,
#: wrap.warwick.ac.uk): repo_check ignores a record whose own type says it is a publication.
EPRINTS_HOSTS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            "reshare.ukdataservice.ac.uk",  # ReShare, UK Data Service
            "uclandata.uclan.ac.uk",  # UCLan Data Repository (UK)
            *_tables.eprints_hosts_survey,
        )
    )
)


def _eprints_hosts() -> list[str]:
    """Port of R/archive-reshare.R::.eprints_hosts(): the known EPrints repositories."""
    return list(EPRINTS_HOSTS)


def _eprints_host_regex() -> str:
    """Port of R/archive-reshare.R::.eprints_host_regex(): any known host, as a regex."""
    return "|".join(h.replace(".", r"\.") for h in EPRINTS_HOSTS)


def _eprints_doi_prefixes() -> list[str]:
    """Port of R/repo-tables.R::.eprints_doi_prefixes(): DOI prefixes of the EPrints hosts.

    DataCite's count per host, or a repository whose DOIs land on one, less the other detectors'
    platforms' and ReShare's own 10.5255. :func:`eprints_links` looks up online only DOIs under
    these prefixes, and drops one DataCite calls a publication; a looked-up DOI still has to lead
    to a host in :data:`EPRINTS_HOSTS`.
    """
    return list(_tables.eprints_doi_prefixes)


def eprints_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-reshare.R::eprints_links(): EPrints repository links in papers.

    All links to records on an EPrints repository: ReShare (see :func:`reshare_links`) and the
    others in the list (LSHTM Data Compass, Glasgow's, Bath's and Reading's research data
    repositories, Mannheim's MADATA, ...) and university repositories that also hold datasets.
    Hyperlinks from the paper's own ``url`` table, bare mentions in the text, and DOIs under one
    of these repositories' DOI prefixes, which are looked up once at DataCite to find the
    record's address; a DOI DataCite calls a publication (a paper, book or thesis) is left out,
    and a DOI under any other prefix is not looked up. A link to a publication is found here, and
    the ``repo_check`` module ignores it once its record says what it is.

    The table has ``href`` (the deposit's url), ``reshare_url`` and ``reshare_id`` (the eprint id
    on ReShare, ``host/id`` on another host).
    """
    from metacheck._r import regexec
    from metacheck.archives.dataone import _scan_links
    from metacheck.archives.dataverse import (
        _collect_links,
        _link_matches,
        _string_series,
        _url_rows,
    )

    host_regex = _eprints_host_regex()
    found_href = _url_rows(paper, f"{host_regex}|10\\.5255/ukda-sn")
    bare = (
        rf"(?:https?://)?(?:www\.)?(?:{host_regex})/(?:id/eprint/)?[0-9]+/?"
        r"|(?:https?://)?(?:dx\.)?(?:doi\.org/)?10\.5255/UKDA-SN-[0-9]+(?:-[0-9]+)?"
    )
    other = _scan_links(paper, bare, [*(f"{h}/" for h in EPRINTS_HOSTS), "10.5255/ukda-sn-"])
    # a DOI under an EPrints repository's prefix, replaced by the address it is registered with
    # when that is on one of these hosts
    by_doi = None
    prefixes = _eprints_doi_prefixes()
    if prefixes:
        alt = "|".join(p.replace(".", r"\.") for p in prefixes)
        doi_rx = (
            rf"(?:https?://)?(?:dx\.)?(?:doi\.org/)?((?:{alt})/[-._;()/:A-Za-z0-9]+[A-Za-z0-9])"
        )
        by_doi = _link_matches(paper, doi_rx, alt)
        if len(by_doi) > 0:
            hrefs = []
            for href in by_doi["href"].tolist():
                groups = regexec(doi_rx, href, perl=True, ignore_case=True)
                hrefs.append(_eprints_resolve_doi_url(str(groups[1])) if len(groups) >= 2 else None)
            by_doi = by_doi.copy()
            by_doi["href"] = pd.Series(hrefs, index=by_doi.index, dtype="string")
            by_doi = by_doi[by_doi["href"].notna()]
    parts = [found_href, other] if by_doi is None else [found_href, other, by_doi]
    links = _collect_links(parts)
    links["reshare_url"] = links["href"]
    links["reshare_id"] = _string_series([_reshare_id_one(u) for u in links["href"].tolist()])
    return links


def _eprints_resolve_doi_url(doi: str) -> str | None:
    """Port of R/archive-reshare.R::.eprints_resolve_doi_url().

    The address a DOI is registered with, when it is on an EPrints repository in
    :data:`EPRINTS_HOSTS` and DataCite does not call it a publication
    (:func:`metacheck.archives.repo_kind._repo_doi_url`, one request); ``None`` otherwise.
    """
    from metacheck.archives.download import _url_host_is
    from metacheck.archives.repo_kind import _repo_doi_url

    url = _repo_doi_url(doi)[0]
    return url if url is not None and _url_host_is(url, EPRINTS_HOSTS) else None


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
    from metacheck._r import grepl, regexec
    from metacheck.archives.dataone import _clean_one

    s = _clean_one(x)
    if s is None:
        return None
    if grepl("^[0-9]+$", s):
        return s
    # a deposit on another EPrints repository (EPRINTS_HOSTS): its address, /<id>/ or
    # /id/eprint/<id>; already a "<host>/<id>" key too
    other = [h for h in EPRINTS_HOSTS if h != "reshare.ukdataservice.ac.uk"]
    if other:
        alt = "|".join(h.replace(".", r"\.") for h in other)
        groups = regexec(
            rf"(?i)^(?:https?://)?(?:www\.)?({alt})/(?:id/eprint/)?([0-9]+)(?:[/?#].*)?$",
            s,
            perl=True,
        )
        if len(groups) >= 3:
            return f"{str(groups[1]).lower()}/{groups[2]}"
    for pattern in _ID_PATTERNS:
        groups = regexec(pattern, s, perl=True, ignore_case=True)
        if len(groups) >= 2:
            return str(groups[1])
    return None


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
        if all(isinstance(v, str) or is_missing(v) for v in urls):
            ids[url_col] = ids[url_col].astype("string")
        ids = ids.drop_duplicates()
        ids = ids[ids[url_col].notna().to_numpy()].reset_index(drop=True)
        valid = list(dict.fromkeys(v for v in ids[id_name].tolist() if not is_missing(v)))

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
    from metacheck.archives.dataverse import _as_values
    from metacheck.utils import online

    # only ReShare's own deposits need ReShare; another EPrints host that does not answer gives
    # its deposit an error row (_reshare_info())
    if isinstance(reshare_url, pd.DataFrame):
        col = (
            reshare_url.iloc[:, int(id_col) - 1] if isinstance(id_col, int) else reshare_url[id_col]
        )
        urls = col.tolist()
    else:
        urls = _as_values(reshare_url)[0]
    ids0 = [_reshare_id_one(u) for u in urls]
    if any(i is not None and "/" not in i for i in ids0) and not online(
        "reshare.ukdataservice.ac.uk"
    ):
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
        obj: dict[str, pd.Series] = {"reshare_id": _cell(None if is_missing(reshare_id) else rid)}

        # "<host>/<id>" for another EPrints repository (see _reshare_id_one())
        host = rid.split("/", 1)[0] if "/" in rid else "reshare.ukdataservice.ac.uk"
        resp = _query(f"https://{host}/id/eprint/{rid.rsplit('/', 1)[-1]}", _reshare_headers)
        if resp is None or resp.status_code != 200:
            where = "ReShare" if host == "reshare.ukdataservice.ac.uk" else host
            warnings.warn(f"{rid} could not be found on {where}", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        rec = _resp_json(resp)
        if rec is None:
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)

        creators = field(rec, "creators")
        authors = (
            [_creator(a) for a in _elements(creators)] if isinstance(creators, list | dict) else []
        )

        docs = field(rec, "documents")
        files_flat: list[Any] = []
        for d in _elements(docs):
            content = field(d, "content")
            files_flat.extend(_set_content(f, content) for f in _elements(field(d, "files")))

        obj["title"] = _field_cell(_empty_or(field(rec, "title"), None))
        # what the record is (data_collection, dataset, article, thesis, ...): repo_check ignores
        # a publication (metacheck #637; _repo_publication_type())
        obj["record_type"] = _field_cell(_empty_or(field(rec, "type"), None))
        obj["doi"] = _field_cell(_empty_or(field(rec, "doi"), None))
        obj["publication_date"] = _field_cell(_empty_or(field(rec, "datestamp"), None))
        obj["updated_date"] = _field_cell(_empty_or(field(rec, "lastmod"), None))
        obj["authors"] = _obj_cell(authors)
        obj["license"] = _cell(None)
        obj["files"] = _obj_cell(files_flat)
        return pd.DataFrame(obj)


def _creator(a: Any) -> str | None:
    """One EPrints creator: ``trimws(paste(given, family))``, ``NA`` when blank."""
    from metacheck._r import trimws
    from metacheck.archives.dataverse import _empty_or
    from metacheck.archives.psycharchives import _paste_json

    nm = field(a, "name")
    if nm is None:
        nm = {}
    given = _paste_json(_empty_or(field(nm, "given"), ""))
    family = _paste_json(_empty_or(field(nm, "family"), ""))
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
            self_url = _empty_or(field(x, "uri"), None)
            if self_url is not None:
                from metacheck._r import sub

                self_url = sub("^http://", "https://", self_url)
            rows.append(
                {
                    "id": _json_chr(_empty_or(field(x, "fileid"), None)),
                    "key": _empty_or(field(x, "filename"), None),
                    "size": _as_numeric(_empty_or(field(x, "filesize"), None)),
                    "checksum": _empty_or(field(x, "hash"), None),
                    "checksum_type": _lower(_empty_or(field(x, "hash_type"), None)),
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
            folder_name="reshare_" + rid.replace("/", "_"),  # "<host>/<id>" (another EPrints host)
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
    size: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-reshare.R::.reshare_zip_members(): extract wanted zip members.

    Reads the zip's central directory with byte-range requests and fetches
    only members of the *keep_types* categories no larger than
    *max_file_size* MB into *dest*. Returns one row per member (``name``,
    ``path``, ``size``, ``ok``), or ``None`` when the archive cannot be listed
    (the caller then downloads it whole).
    *size*, the zip's size as the listing gives it, saves the peek a ``HEAD``
    request.
    """
    from metacheck.archives.dataverse import _zip_members

    return _zip_members(url, dest, keep_types, max_file_size, size)


def _reshare_verify_downloads(files: pd.DataFrame | None, download_to: str) -> Any:
    """Port of R/archive-reshare.R::.reshare_verify_downloads(): check files on disk.

    ``downloaded`` becomes whether each file is on disk under *download_to*
    with the reported size and (for an MD5 ``checksum_type``) checksum; adds
    ``size_on_disk`` and ``checksum_ok``. Rows extracted from a zip count as
    downloaded.
    """
    from metacheck.archives.dataverse import _verify_file_table

    return _verify_file_table(files, download_to, typed=True)
