"""DataONE member nodes (port of ``R/archive-dataone.R``).

DataONE is a federation of independently run repositories ("member nodes")
that all run Metacat and expose the same REST API, each at its own base path.
Links are recognised against the verified hosts in :data:`DATAONE_HOSTS`
(by host name, or for bare DOIs by the host's DOI prefix), and
:func:`dataone_info` reads each dataset's EML metadata document from its own
member node.

This module also holds the small XML helpers (``xml2::read_xml()``,
``xml_find_first()``, ``xml_text()``) that the FSD port shares.
"""

from __future__ import annotations

import functools
import warnings
from typing import TYPE_CHECKING, Any

import pandas as pd

from pytacheck._r import is_na, regexec, sub, trimws

if TYPE_CHECKING:
    from lxml import etree

__all__ = ["DATAONE_HOSTS", "dataone_info", "dataone_links"]

#: R ``.dataone_hosts()``: verified member nodes, in R's order (``doi_prefix``
#: ``None`` where the node has no DOI prefix of its own on record).
DATAONE_HOSTS: tuple[dict[str, str | None], ...] = (
    {"host": "arcticdata.io", "api_base": "/metacat/d1/mn/v2/", "doi_prefix": "10.18739"},
    {"host": "knb.ecoinformatics.org", "api_base": "/knb/d1/mn/v2/", "doi_prefix": "10.5063"},
    {"host": "metacat.tfri.gov.tw", "api_base": "/metacat/d1/mn/v2/", "doi_prefix": None},
    {"host": "smithsonian.dataone.org", "api_base": "/metacat/d1/mn/v2/", "doi_prefix": None},
    {"host": "data.piscoweb.org", "api_base": "/metacat/d1/mn/v2/", "doi_prefix": "10.6085"},
)


def _dataone_hosts() -> list[dict[str, str | None]]:
    """Port of R/archive-dataone.R::.dataone_hosts(): the verified member nodes.

    Each entry has ``host``, ``api_base`` (the node's REST API path) and
    ``doi_prefix`` (``None`` when unknown).
    """
    return [dict(h) for h in DATAONE_HOSTS]


def _escape_dots(x: str) -> str:
    """R ``gsub("\\\\.", "\\\\\\\\.", x)``: escape the dots of a host or DOI prefix."""
    return x.replace(".", r"\.")


@functools.cache
def _dataone_host_regex() -> str:
    """Port of R/archive-dataone.R::.dataone_host_regex(): any member node host, as a regex."""
    return "|".join(_escape_dots(str(h["host"])) for h in DATAONE_HOSTS)


@functools.cache
def _doi_regex() -> str:
    """The known DOI prefixes, as a regex alternation (R: ``doi_regex`` in dataone_links())."""
    return "|".join(_escape_dots(str(h["doi_prefix"])) for h in DATAONE_HOSTS if h["doi_prefix"])


def dataone_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-dataone.R::dataone_links(): DataONE links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table naming a known
    member node or one of their DOI prefixes, plus bare mentions in the text
    (a member node's ``view/doi:...`` landing page, or a DOI under a known
    prefix). Trailing slashes are stripped and duplicate rows dropped;
    ``dataone_url``, ``dataone_host`` and ``dataone_pid`` columns are added.
    """
    from pytacheck.archives.dataverse import (
        _collect_links,
        _link_matches,
        _string_series,
        _url_rows,
    )

    host_regex = _dataone_host_regex()
    doi_regex = _doi_regex()
    found_href = _url_rows(paper, f"{host_regex}|{doi_regex}")
    bare_regex = (
        f"(?:https?://)?(?:{host_regex})/(?:view|catalog/view)/doi:[^\\s\"'<>)]+"
        f"|(?:https?://)?(?:doi\\.org/)?(?:{doi_regex})/[A-Za-z0-9._/-]+"
    )
    other = _link_matches(paper, bare_regex)
    links = _collect_links([found_href, other])
    urls = links["href"].tolist()
    links["dataone_url"] = links["href"]
    links["dataone_host"] = _string_series([_host_one(u) for u in urls])
    links["dataone_pid"] = _string_series([_pid_one(u) for u in urls])
    return links


def _clean_one(x: Any) -> str | None:
    """``trimws(as.character(x))``, ``None`` for ``NA`` or ``""``."""
    from pytacheck._r import as_character

    if is_na(x):
        return None
    s = trimws(x if isinstance(x, str) else as_character(x))
    return None if s is None or s == "" else s


def _host_one(url: Any) -> str | None:
    from pytacheck._r import grepl

    u = _clean_one(url)
    if u is None:
        return None
    for h in DATAONE_HOSTS:
        if str(h["host"]) in u:
            return str(h["host"])
        prefix = h["doi_prefix"]
        if prefix is not None and grepl(_escape_dots(prefix) + "/", u, perl=True):
            return str(h["host"])
    return None


def _map(x: Any, one: Any) -> Any:
    """R's ``length 0 -> character(0)``, ``length > 1 -> vapply()`` dispatch.

    A string (or other scalar) gives a scalar, a sequence gives a list.
    """
    from pytacheck.archives.dataverse import _as_values

    vals, scalar = _as_values(x)
    out = [one(v) for v in vals]
    return out[0] if scalar else out


def _dataone_host(dataone_url: Any) -> Any:
    """Port of R/archive-dataone.R::.dataone_host(): the member node of each URL or DOI.

    A URL naming a known host, or containing a known host's DOI prefix, gives
    that host (hosts are tried in :data:`DATAONE_HOSTS` order, host name
    before prefix); anything else ``None``.
    """
    return _map(dataone_url, _host_one)


_PID_MARKED = r"doi:(10\.[0-9]+/[A-Za-z0-9._-]+)"
_PID_BARE = r"(?:doi\.org/)?(10\.[0-9]+/[A-Za-z0-9._/-]+)$"


def _pid_one(url: Any) -> str | None:
    u = _clean_one(url)
    if u is None:
        return None
    for pattern in (_PID_MARKED, _PID_BARE):
        groups = regexec(pattern, u, perl=True, ignore_case=True)
        if len(groups) >= 2:
            return f"doi:{groups[1]}"
    return None


def _dataone_pid(dataone_url: Any) -> Any:
    """Port of R/archive-dataone.R::.dataone_pid(): DataONE PIDs from URLs or DOIs.

    A ``doi:10.xxx/...`` marker (as in a landing-page URL) is taken as is;
    otherwise a DOI at the end of the string (its suffix may contain
    slashes). The result is in DataONE's ``doi:<prefix>/<suffix>`` form, or
    ``None``.
    """
    return _map(dataone_url, _pid_one)


def dataone_info(
    dataone_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-dataone.R::dataone_info(): look up DataONE datasets.

    *dataone_url* is a URL/DOI, a sequence of them, or a table whose *id_col*
    (1-based position or name) holds them (e.g. from :func:`dataone_links`).
    Each dataset with a recognised member node and PID has its EML metadata
    fetched from that node (with *cache*, from the on-disk listing cache when
    possible); the input rows are returned with ``dataone_host``,
    ``dataone_pid`` and the dataset's ``title``, ``doi``,
    ``publication_date``, ``authors``, ``license`` and ``files`` (or
    ``error``) added.
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import _check_named_ids, _info_table, _string_series
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from pytacheck.utils import left_join, online

    with _spinner(pb, "DataONE Retrieve") as bar:
        table = _info_table(dataone_url, id_col, "dataone_url", ("dataone_host", "dataone_pid"))
        urls = table["dataone_url"].tolist()
        _check_named_ids(urls)
        ids = pd.DataFrame(
            {
                "dataone_url": table["dataone_url"].to_numpy(),
                "dataone_host": _string_series([_host_one(u) for u in urls]),
                "dataone_pid": _string_series([_pid_one(u) for u in urls]),
            }
        )
        if all(isinstance(v, str) or is_na(v) for v in urls):
            ids["dataone_url"] = ids["dataone_url"].astype("string")
        ids = ids.drop_duplicates()
        ids = ids[ids["dataone_url"].notna().to_numpy()].reset_index(drop=True)
        valid = ids[(ids["dataone_host"].notna() & ids["dataone_pid"].notna()).to_numpy()]

        if len(valid) == 0:
            _tick(bar, "No valid DataONE links")
            return left_join(table, ids, by="dataone_url")

        if not online("dataone.org"):
            raise ConnectionError("dataone.org seems to be offline")

        nv = len(valid)
        _tick(bar, f"Starting DataONE retrieval for {nv} dataset{'' if nv == 1 else 's'}...")
        id_info = []
        for host, pid in zip(
            valid["dataone_host"].tolist(), valid["dataone_pid"].tolist(), strict=True
        ):
            ckey = f"{host} {pid}"
            cached = _repo_info_cache_get("dataone", ckey) if cache is True else None
            if cached is not None:
                id_info.append(cached)
                continue
            got = _dataone_info(pid, host=host, pb=bar)
            if cache is True and _repo_info_ok(got):
                _repo_info_cache_put("dataone", ckey, got)
            id_info.append(got)

        info = bind_rows(id_info)
        data = left_join(table, ids, by="dataone_url")
        data = left_join(data, info, by=["dataone_host", "dataone_pid"], suffix=("", ".dataone"))
        _tick(bar, "...DataONE retrieval complete!")
        return data


# ---------------------------------------------------------------------------
# XML helpers (xml2 idioms), shared with the FSD port
# ---------------------------------------------------------------------------


def _read_xml(content: bytes | str | None) -> etree._Element:
    """``xml2::read_xml(x)`` of a response body (xml2's default ``NOBLANKS`` option).

    Raises on anything that is not well-formed XML (R: an error, which the
    callers catch). External entities and network access are never resolved.
    """
    from lxml import etree

    if content is None:
        raise ValueError("no XML content")
    data = content.encode("utf-8") if isinstance(content, str) else content
    parser = etree.XMLParser(
        remove_blank_text=True, resolve_entities=False, no_network=True, load_dtd=False
    )
    root = etree.fromstring(data, parser)
    if root is None:
        raise ValueError("Document is empty")
    return root


def _local_name(node: etree._Element) -> str:
    """``xml2::xml_name()`` of an element: its name without namespace."""
    from lxml import etree

    return str(etree.QName(node).localname)


def _find_first(node: etree._Element, xpath: str) -> etree._Element | None:
    """``xml2::xml_find_first()``: the first matching element (``None``: R's ``xml_missing``)."""
    hits = node.xpath(xpath)
    return hits[0] if isinstance(hits, list) and hits else None


def _xml_text(node: etree._Element | None) -> str | None:
    """``xml2::xml_text()``: an element's string value (``None`` for a missing node)."""
    if node is None:
        return None
    return str(node.xpath("string()"))


def _text_of(node: etree._Element, xpath: str) -> str | None:
    """``xml_text(xml_find_first(node, xpath))``."""
    return _xml_text(_find_first(node, xpath))


def _local(name: str) -> str:
    """An XPath step matching any element with local name *name* (R: ``*[local-name()=...]``)."""
    return f".//*[local-name()='{name}']"


def _as_numeric_quiet(x: str | None) -> float:
    """``suppressWarnings(as.numeric(x))`` of a string (``NaN`` for ``NA``)."""
    import math

    from pytacheck.archives.dataverse import _as_numeric

    if x is None:
        return math.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _as_numeric(x)


def _dataone_info(pid: Any, host: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-dataone.R::.dataone_info(): one dataset from one member node.

    Fetches ``<api_base>object/<pid>`` (the dataset's science metadata) and
    parses it as EML. Returns a one-row table: ``dataone_host``,
    ``dataone_pid`` and either ``error`` (``"unknown_host"``, ``"unfound"``
    with a warning, or ``"unsupported_metadata_format"`` for anything that is
    not an EML document) or ``title``, ``doi``, ``publication_date``,
    ``authors``, ``license`` and ``files`` (one record per ``<physical>``
    element: ``key``, ``size``, ``pid``).
    """
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import (
        _cell,
        _list_cell,
        _paste,
        _query,
        _url_encode_reserved,
    )

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from DataONE {_paste(host)} ({_paste(pid)})...")
        obj: dict[str, pd.Series] = {"dataone_host": _cell(host), "dataone_pid": _cell(pid)}

        api_base = None
        for h in DATAONE_HOSTS:
            if isinstance(host, str) and h["host"] == host:
                api_base = h["api_base"]
        if api_base is None:
            obj["error"] = _cell("unknown_host")
            return pd.DataFrame(obj)

        epid = _url_encode_reserved(_paste(pid))
        obj_url = f"https://{host}{api_base}object/{epid}"
        resp = _query(obj_url, lambda spec: spec)
        if resp is None or resp.status_code != 200:
            warnings.warn(f"{_paste(pid)} could not be found on {_paste(host)}", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        try:
            doc = _read_xml(resp.content)
        except Exception:
            doc = None
        if doc is None or _local_name(doc) != "eml":
            obj["error"] = _cell("unsupported_metadata_format")
            return pd.DataFrame(obj)

        title = _text_of(doc, _local("title"))
        authors = [_creator_name(cr) for cr in doc.xpath(_local("creator"))]
        pub_date = _text_of(doc, _local("pubDate"))
        license_ = _text_of(doc, _local("intellectualRights"))

        files = []
        for p in doc.xpath(_local("physical")):
            name = _text_of(p, _local("objectName"))
            size = _text_of(p, _local("size"))
            url = _text_of(p, _local("url"))
            file_pid = None if url is None else sub("^.*/", "", url)
            files.append(
                {
                    "key": name,
                    "size": _as_numeric_quiet(size),
                    "pid": file_pid if file_pid else None,
                }
            )

        obj["title"] = _cell(title)
        obj["doi"] = _cell(pid)
        obj["publication_date"] = _cell(pub_date)
        obj["authors"] = _list_cell(authors)
        obj["license"] = _cell(license_)
        obj["files"] = _list_cell(files)
        return pd.DataFrame(obj)


def _creator_name(creator: etree._Element) -> str | None:
    """One EML ``<creator>`` as R's ``vapply()`` names it.

    ``paste(given, surname)`` where a missing part is R's ``NA`` (so it reads
    ``"NA"``, as in R); the organisation name only when both parts are
    present but blank.
    """
    given = _text_of(creator, _local("givenName"))
    surname = _text_of(creator, _local("surName"))
    org = _text_of(creator, _local("organizationName"))
    full = trimws(f"{'NA' if given is None else given} {'NA' if surname is None else surname}")
    if full:
        return str(full)
    # R: if (nzchar(org)) org else NA -- nzchar(NA) is TRUE, so a missing org is NA
    return org if org else None
