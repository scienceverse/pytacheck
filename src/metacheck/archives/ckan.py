"""CKAN data portals (port of ``R/archive-ckan.R``, metacheck #637).

CKAN (https://ckan.org) is the catalogue software behind many government and research data portals
(EnviDat, the Natural History Museum's Data Portal, Canada's and California's open data, ...).
Every installation answers the same API (verified 2026-11-13 on data.cnra.ca.gov and
www.envidat.ch):

* ``GET /api/3/action/status_show`` names the software (``ckan_version``); the repository survey
  used it to find the installations;
* ``GET /api/3/action/package_show?id=<name or id>`` gives a dataset: its title, notes, author,
  ``license_id``, ``doi`` (where the portal registers one) and its ``resources``, each with a name,
  format, size, url and ``url_type``.

A resource is a file the catalogue holds (``"upload"``, or an address on the portal itself), or a
link to another website (an ArcGIS map page, a government page); only the first are the dataset's
files, as for LDbase's "external link" items. A dataset's address is ``/dataset/<name or id>`` (on
open.canada.ca ``/data/en/dataset/<id>``), or on EnviDat ``/#/metadata/<name>``, where its DOIs lead
(10.16904/envidat.75).
"""

from __future__ import annotations

import posixpath
import re
from typing import Any
from urllib.parse import unquote

import pandas as pd

from metacheck._values import field, is_missing
from metacheck.archives import _repo_tables as _tables

__all__ = ["CKAN_HOSTS", "ckan_info", "ckan_links"]

#: R ``.ckan_hosts()``: Canada's open data portal, whose CKAN is mounted under ``/data`` (its API
#: at ``/data/api/3/...``, checked 2026-11-13; the survey's check asked the host's root, as for
#: every other installation, where only this one of 49 answered under ``/data`` instead), and the
#: installations the repository survey found
CKAN_HOSTS: tuple[str, ...] = tuple(dict.fromkeys(("open.canada.ca", *_tables.ckan_hosts_survey)))

#: R ``.ckan_api_base()``: where an installation's API is, below its host
_API_BASES = {"open.canada.ca": "/data"}

#: R ``.ckan_doi_prefixes_all()``: DOI prefixes of the hand-listed installations (DataCite's
#: repositories whose DOIs land on open.canada.ca: Agriculture and Agri-Food Canada, the Canada
#: Energy Regulator, GEOSCAN, PSC), with the survey's
CKAN_DOI_PREFIXES: tuple[str, ...] = tuple(
    dict.fromkeys(("10.23684", "10.35002", "10.23687", "10.82126", *_tables.ckan_doi_prefixes))
)

_PATH = r"(?:/data/[a-z]{2})?/(?:dataset|#/metadata)/[-A-Za-z0-9_]+"

_INFO_COLUMNS = ("ckan_url", "ckan_id", "title", "doi", "license", "error")


def _ckan_hosts() -> list[str]:
    """Port of R/archive-ckan.R::.ckan_hosts(): the known CKAN installations."""
    return list(CKAN_HOSTS)


def _ckan_api_base(host: Any) -> Any:
    """Port of R/archive-ckan.R::.ckan_api_base(): where an installation's API is below its host.

    A host gives its base (``""`` for all but those mounted under a path); a sequence of hosts, a
    list.
    """
    if isinstance(host, str):
        return _API_BASES.get(re.sub(r"^www\.", "", host.lower()), "")
    return [_ckan_api_base(h) for h in host]


def _ckan_doi_prefixes_all() -> list[str]:
    """Port of R/archive-ckan.R::.ckan_doi_prefixes_all()."""
    return list(CKAN_DOI_PREFIXES)


def _host_alt() -> str:
    return "|".join(h.replace(".", r"\.") for h in CKAN_HOSTS)


def ckan_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-ckan.R::ckan_links(): CKAN data portal links in papers.

    Finds links to datasets on the CKAN data portals in the list (EnviDat, the Natural History
    Museum's Data Portal, government open data portals, ...), as hyperlinks or in the text, and
    DOIs under one of these portals' DOI prefixes, which are looked up once at DataCite to find the
    dataset's address (a DOI under any other prefix is not looked up).

    The table has a row for each link: ``href`` (plus ``text_id`` / ``paper_id``), ``ckan_url``
    (the link) and ``ckan_id`` (the portal's host and the dataset's name or id, ``host/name``).
    """
    from metacheck._r import bind_rows, regexec, sub
    from metacheck.archives.dataverse import _link_matches, _string_series, _url_rows
    from metacheck.archives.download import _url_host_is
    from metacheck.archives.repo_kind import _repo_doi_url

    empty = pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "href": pd.Series([], dtype="string"),
            "ckan_url": pd.Series([], dtype="string"),
            "ckan_id": pd.Series([], dtype="string"),
        }
    )
    rx = _host_alt()
    found_href = _url_rows(paper, rf"(?:{rx}){_PATH}")
    bare = _link_matches(paper, rf"(?:https?://)?(?:www\.)?(?:{rx}){_PATH}", rx)
    # a DOI under a portal's prefix, replaced by the address it is registered with when that is on
    # one of these hosts
    pre = _ckan_doi_prefixes_all()
    by_doi = None
    if pre:
        alt = "|".join(p.replace(".", r"\.") for p in pre)
        doi_rx = (
            rf"(?:https?://)?(?:dx\.)?(?:doi\.org/)?((?:{alt})/[-._;()/:A-Za-z0-9]+[A-Za-z0-9])"
        )
        by_doi = _link_matches(paper, doi_rx, alt)
        if len(by_doi) > 0:
            hrefs = []
            for href in by_doi["href"].tolist():
                groups = regexec(doi_rx, href, perl=True, ignore_case=True)
                u = _repo_doi_url(str(groups[1]))[0] if len(groups) >= 2 else None
                hrefs.append(u if u is not None and _url_host_is(u, CKAN_HOSTS) else None)
            by_doi = by_doi.copy()
            by_doi["href"] = pd.Series(hrefs, index=by_doi.index, dtype="string")
            by_doi = by_doi[by_doi["href"].notna()]
    links = bind_rows([found_href, bare] if by_doi is None else [found_href, bare, by_doi])
    if len(links) == 0:
        return empty
    links["href"] = pd.Series(
        sub(r"[/.]+$", "", links["href"].tolist()), index=links.index, dtype="string"
    )
    links = links.drop_duplicates().reset_index(drop=True)
    links["ckan_url"] = links["href"]
    links["ckan_id"] = _string_series(_ckan_id(links["ckan_url"].tolist()))
    return links[links["ckan_id"].notna()].reset_index(drop=True)


def _ckan_id_one(url: Any) -> str | None:
    if is_missing(url):
        return None
    m = re.match(
        rf"(?i)^(?:https?://)?((?:www\.)?(?:{_host_alt()}))(?:/data/[a-z]{{2}})?"
        r"/(?:dataset|#/metadata)/([-A-Za-z0-9_]+)",
        str(url),
    )
    return None if m is None else f"{m.group(1).lower()}/{m.group(2)}"


def _ckan_id(url: Any) -> Any:
    """Port of R/archive-ckan.R::.ckan_id(): ``<host>/<dataset name or id>`` from a CKAN address.

    ``None`` where there is none. A string gives a string, a sequence a list.
    """
    if isinstance(url, str) or not hasattr(url, "__iter__"):
        return _ckan_id_one(url)
    return [_ckan_id_one(u) for u in url]


def ckan_info(
    ckan_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-ckan.R::ckan_info(): information about CKAN datasets.

    Reads each dataset's record from its portal's API (``/api/3/action/package_show``): title, DOI,
    licence, and its files. A resource that is only a link to another website (a map page, another
    agency's page) is not one of the dataset's files and is not listed.

    *ckan_url* is a vector of CKAN dataset URLs, or a table containing them (e.g. from
    :func:`ckan_links`, *id_col* is the column that holds the URLs). Returns one row per link:
    ``ckan_url``, ``ckan_id``, ``title``, ``doi``, ``license``, ``error`` (missing, or why the link
    could not be listed), ``authors`` and a list column ``files`` (a table per link with
    ``file_name``, ``file_path``, ``file_url``, ``file_size`` and ``format``). With *cache*, a
    listing is read from and written to the persistent repository-listing cache.
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _chr_values
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.archives.psycharchives import _column

    with _spinner(pb, "CKAN Retrieve") as bar:
        raw: Any = (
            ckan_url[_column(ckan_url, id_col)].tolist()
            if isinstance(ckan_url, pd.DataFrame)
            else ckan_url
        )
        values, _ = _chr_values(raw)
        urls = [u for u in dict.fromkeys(v for v in values if v is not None) if u != ""]
        if not urls:
            return pd.DataFrame({c: pd.Series([], dtype="string") for c in _INFO_COLUMNS})
        rows: list[pd.DataFrame] = []
        for u in urls:
            cid = _ckan_id_one(u)
            cached = (
                _repo_info_cache_get("ckan", cid) if cache is True and cid is not None else None
            )
            if cached is not None:
                cached["ckan_url"] = pd.Series([u], dtype="string")
                rows.append(cached)
                continue
            res = _ckan_info(u, cid, pb=bar)
            if cache is True and cid is not None and _repo_info_ok(res):
                _repo_info_cache_put("ckan", cid, res)
            rows.append(res)
        _tick(bar, "...CKAN retrieval complete!")
        return bind_rows(rows)


def _ckan_info(url: str, cid: str | None, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-ckan.R::.ckan_info(): one CKAN dataset, one row (see :func:`ckan_info`)."""
    from metacheck import http
    from metacheck.archives import _tick
    from metacheck.archives.dataverse import _cell
    from metacheck.archives.download import _url_host_is
    from metacheck.archives.github import _empty_or
    from metacheck.archives.psycharchives import _obj_cell
    from metacheck.db._utils import url_encode

    obj = pd.DataFrame(
        {
            "ckan_url": _cell(url),
            "ckan_id": _cell(cid),
            "title": _cell(None),
            "doi": _cell(None),
            "license": _cell(None),
            "error": _cell(None),
        }
    )
    obj["authors"] = _obj_cell([])
    obj["files"] = _obj_cell(None)
    if cid is None:
        obj["error"] = _cell("not a CKAN dataset link")
        return obj
    host = cid.split("/", 1)[0]
    _tick(pb, f"* Retrieving info from CKAN dataset {cid}...")
    name = cid.split("/", 1)[1]
    api = (
        f"https://{host}{_ckan_api_base(host)}/api/3/action/package_show?id="
        f"{url_encode(name, reserved=True)}"
    )
    resp = http.batch_query([api], msg=None)[0]
    if resp is None or resp.status_code != 200:
        obj["error"] = _cell("unfound")
        return obj
    try:
        rec = field(http.resp_json(resp, check_type=False), "result")
    except Exception:
        rec = None
    if rec is None:
        obj["error"] = _cell("parse_error")
        return obj

    def text(*values: Any) -> str | None:
        for v in values:
            v = _empty_or(v)
            if v is not None:
                return str(v)
        return None

    obj["title"] = _cell(text(field(rec, "title")))
    obj["doi"] = _cell(text(field(rec, "doi")))
    obj["license"] = _cell(text(field(rec, "license_title"), field(rec, "license_id")))
    obj["authors"] = _obj_cell(
        [a for a in (text(field(rec, "author")), text(field(rec, "maintainer"))) if a is not None]
    )
    res = field(rec, "resources")
    res = res if isinstance(res, list) else []
    # the dataset's own files: uploaded, or at an address on the portal itself
    own = []
    for r in res:
        r_url = field(r, "url")
        is_upload = field(r, "url_type") == "upload"
        on_portal = isinstance(r_url, str) and r_url != "" and _url_host_is(r_url, host)
        if is_upload or on_portal:
            own.append(r)
    if own:
        file_url = [text(field(r, "url")) for r in own]
        from_url = [
            unquote(re.sub(r"[?#].*$", "", posixpath.basename(u))) if u is not None else "NA"
            for u in file_url
        ]
        fmt = [(text(field(r, "format")) or "").lower() for r in own]
        nm = [text(field(r, "name")) for r in own]
        # the file's own name where its address ends in one, else the resource's name with its
        # format as the extension
        file_name: list[str | None] = []
        for f, fm, n in zip(from_url, fmt, nm, strict=True):
            if re.search(r"\.[A-Za-z0-9]{1,8}$", f):
                file_name.append(f)
            elif fm != "" and n is not None:
                file_name.append(f"{n}.{fm}")
            else:
                file_name.append(n)
        sizes = []
        for r in own:
            v = _empty_or(field(r, "size"))
            try:
                sizes.append(float(v) if v is not None else float("nan"))
            except (TypeError, ValueError):
                sizes.append(float("nan"))
        obj["files"] = _obj_cell(
            pd.DataFrame(
                {
                    "file_name": pd.Series(file_name, dtype="string"),
                    "file_path": pd.Series(file_name, dtype="string"),
                    "file_url": pd.Series(file_url, dtype="string"),
                    "file_size": pd.Series(sizes, dtype="float64"),
                    "format": pd.Series(fmt, dtype="string"),
                }
            )
        )
    return obj
