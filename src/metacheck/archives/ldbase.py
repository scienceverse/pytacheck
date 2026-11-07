"""LDbase (port of ``R/archive-ldbase.R``).

LDbase (https://ldbase.org) is the data repository for educational and
developmental sciences run by Florida State University. It runs on Drupal 10
but has no public API (``/jsonapi`` answers 404), so everything is read from
the public web pages, the way :func:`~metacheck.archives.researchbox.rbox_info`
reads ResearchBox.

Content is organised in projects, each holding datasets, code and documents
(and child projects). Every item has its own page, at
``ldbase.org/<projects|datasets|code|documents>/<uuid>``, and DOIs under the
prefix ``10.33009`` lead to those pages. A project page lists all of its items,
child projects included, in its "Project Hierarchy" tree; an item page links its
current file under ``/system/files/``; files download without an account, and a
one-byte ranged request reports the size, which ``download_repo_files()`` uses
when the listing has none. The project's "download all project files" zip is
not used: LDbase answered 500/503 for it for four of five projects tried.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import Any

import pandas as pd

from metacheck._values import is_missing
from metacheck.archives.zip_peek import _waf_challenge_check

__all__ = ["ldbase_info", "ldbase_links"]

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KINDS = "projects|datasets|code|documents"
_DOI = r"10\.33009/ldbase\.[A-Za-z0-9.]*[A-Za-z0-9]"

#: the columns of a listing without a files column (R: the empty result)
_INFO_COLUMNS = ("ldbase_url", "ldbase_page", "ldbase_type", "title", "doi", "license", "error")
_FILE_COLUMNS = ("file_name", "file_path", "file_url", "item_type", "item_url")


def _ldbase_regex() -> str:
    """An LDbase item page or an LDbase deposit DOI.

    A DOI must carry something after ``ldbase.``: the bare ``10.33009/ldbase``
    is the repository's own DOI.
    """
    return (
        r"(?:https?://)?(?:www\.)?ldbase\.org(?::443)?/"
        rf"(?:{_KINDS})/{_UUID}"
        rf"|(?:https?://)?(?:dx\.)?(?:doi\.org/)?{_DOI}"
    )


def ldbase_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-ldbase.R::ldbase_links(): LDbase links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table, plus bare
    mentions in the text, of LDbase projects, datasets, code and documents and
    of LDbase DOIs (prefix 10.33009). LDbase's own DOI (``10.33009/ldbase``)
    and links to its home or search pages are not included: they name no
    deposit. Trailing slashes and full stops are dropped (a DOI often ends a
    sentence) and duplicate rows removed; ``ldbase_url`` (the link) and
    ``ldbase_type`` (``projects``, ``datasets``, ``code``, ``documents``, or
    ``doi`` for a DOI not yet resolved) columns are added.
    """
    from metacheck._r import bind_rows, grepl, sub, trimws
    from metacheck.archives.dataone import _scan_links
    from metacheck.archives.dataverse import _string_series, _url_rows

    found_href = _url_rows(paper, r"ldbase\.org|10\.33009/")
    other = _scan_links(paper, _ldbase_regex(), ["ldbase.org/", "10.33009/ldbase."])

    links = bind_rows([found_href, other])
    if "href" not in links.columns:
        if len(links) > 0:
            raise ValueError(f"`href` must be size {len(links)} or 1, not 0.")
        links["href"] = pd.Series([], dtype="string", index=links.index)
    links["href"] = pd.Series(
        sub(r"[/.]+$", "", trimws(links["href"].tolist())), index=links.index, dtype="string"
    )
    links = links.drop_duplicates().reset_index(drop=True)
    keep = grepl(_ldbase_regex(), links["href"].tolist(), ignore_case=True, perl=True)
    links = links.loc[[bool(k) for k in keep]].reset_index(drop=True)

    links["ldbase_url"] = links["href"]
    links["ldbase_type"] = _string_series(_ldbase_type(links["ldbase_url"].tolist()))
    return links


def _ldbase_type(url: Any) -> list[str | None]:
    """Port of R/archive-ldbase.R::.ldbase_type(): the kind of LDbase link.

    ``projects``/``datasets``/``code``/``documents`` for an item page URL,
    ``doi`` for an LDbase DOI, ``None`` otherwise.
    """
    if url is None:
        return []
    values = [url] if isinstance(url, str) else list(url)
    page = re.compile(rf"ldbase\.org(?::443)?/({_KINDS})/", re.IGNORECASE)
    doi = re.compile(r"10\.33009/ldbase\.", re.IGNORECASE)
    out: list[str | None] = []
    for u in values:
        if is_missing(u):
            out.append(None)
            continue
        text = str(u)
        m = page.search(text)
        if m:
            out.append(m.group(1).lower())
        elif doi.search(text):
            out.append("doi")
        else:
            out.append(None)
    return out


def _ldbase_page_url(url: Any) -> str | None:
    """Port of R/archive-ldbase.R::.ldbase_page_url(): the canonical page URL of an item link.

    https, no port, no trailing slash or query; ``None`` for anything else.
    """
    if is_missing(url):
        return None
    m = re.search(rf"ldbase\.org(?::443)?/(?:{_KINDS})/[0-9a-f-]{{36}}", str(url), re.IGNORECASE)
    if m is None:
        return None
    return "https://" + m.group(0).lower().replace(":443", "")


# --------------------------------------------------------------------------- info


def ldbase_info(
    ldbase_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-ldbase.R::ldbase_info(): the files of LDbase projects, datasets, code and documents.

    *ldbase_url* is a URL or DOI, a sequence of them, or a table whose
    *id_col* (1-based position or name) holds them (e.g. from
    :func:`ldbase_links`). For a project every dataset, code and document in
    its hierarchy is listed (child projects included, each item's files in a
    folder named after the item); for a single item, its own file. A DOI is
    first resolved to the page it leads to. LDbase has no API, so the public
    pages are read (one request per project or item); items stored elsewhere
    ("external link") have no file here and are not listed.

    Returns one row per distinct link: ``ldbase_url``, ``ldbase_page`` (the
    resolved page), ``ldbase_type``, ``title``, ``doi``, ``license`` (the
    item's, or every distinct licence of a project's items joined by
    ``"; "``), ``error`` (missing, or why the link could not be listed) and
    ``files`` (a table per link with ``file_name``, ``file_path``,
    ``file_url``, ``item_type`` and ``item_url``). With *cache*, a listing is
    read from and written to the repository-listing cache.
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
    from metacheck.utils import online

    if not online("ldbase.org"):
        raise ConnectionError("ldbase.org seems to be offline")

    with _spinner(pb, "LDbase Retrieve") as bar:
        if isinstance(ldbase_url, pd.DataFrame):
            raw: Any = ldbase_url[_column(ldbase_url, id_col)].tolist()
        else:
            raw = ldbase_url
        values, _ = _chr_values(raw)
        urls = [u for u in dict.fromkeys(v for v in values if v is not None) if u != ""]
        if not urls:
            return pd.DataFrame({c: pd.Series([], dtype="string") for c in _INFO_COLUMNS})

        rows: list[pd.DataFrame] = []
        for u in urls:
            cached = _repo_info_cache_get("ldbase", u) if cache is True else None
            if cached is not None:
                rows.append(cached)
                continue
            # A failure (e.g. a firewall browser check) is recorded as this
            # link's error, so the other links are still listed.
            try:
                res = _ldbase_info(u, pb=bar)
            except Exception as e:
                res = _info_row(u, ldbase_type=(_ldbase_type(u) or [None])[0], error=str(e))
            if cache is True and _repo_info_ok(res):
                _repo_info_cache_put("ldbase", u, res)
            rows.append(res)

        _tick(bar, "...LDbase retrieval complete!")
        return bind_rows(rows)


def _info_row(
    url: str,
    page: str | None = None,
    ldbase_type: str | None = None,
    error: str | None = None,
) -> pd.DataFrame:
    """A one-row listing of *url* without files (R: the ``obj`` data frame)."""
    from metacheck.archives.dataverse import _cell
    from metacheck.archives.psycharchives import _obj_cell

    obj = {
        "ldbase_url": _cell(url),
        "ldbase_page": _cell(page),
        "ldbase_type": _cell(ldbase_type),
        "title": _cell(None),
        "doi": _cell(None),
        "license": _cell(None),
        "error": _cell(error),
    }
    out = pd.DataFrame(obj)
    out["files"] = _obj_cell(None)
    return out


def _ldbase_info(url: str, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-ldbase.R::.ldbase_info(): one LDbase link, one row."""
    from metacheck.archives import _tick
    from metacheck.archives.dataverse import _cell
    from metacheck.archives.dspace7 import _handle_registered_url
    from metacheck.archives.psycharchives import _obj_cell

    obj = _info_row(url)
    _tick(pb, f"* Retrieving info from LDbase {url}...")

    def failed(why: str) -> pd.DataFrame:
        obj["error"] = _cell(why)
        return obj

    page = _ldbase_page_url(url)
    if page is None and _ldbase_type(url) == ["doi"]:
        m = re.search(_DOI, url, re.IGNORECASE)
        resolved, ok = _handle_registered_url(m.group(0) if m else url)
        page = _ldbase_page_url(resolved)
        if page is None:
            return failed("unfound" if ok else "doi_unresolved")
    if page is None:
        return failed("unfound")
    obj["ldbase_page"] = _cell(page)
    kind = _ldbase_type(page)[0]
    obj["ldbase_type"] = _cell(kind)

    html = _ldbase_get(page)
    if html is None:
        return failed("unfound")
    meta = _ldbase_page_meta(html)
    obj["title"] = _cell(meta["title"])
    obj["doi"] = _cell(meta["doi"])

    # A project lists every item in its hierarchy, each read from its own
    # page; a single item is its own (already fetched) page.
    items = _ldbase_project_items(html) if kind == "projects" else [page]
    parts: list[pd.DataFrame] = []
    licenses: list[str] = []
    for item in items:
        item_html = html if item == page else _ldbase_get(item)
        if item_html is None:
            continue
        f = _ldbase_item_files(item_html, item)
        if len(f) == 0:
            continue
        item_meta = _ldbase_page_meta(item_html)
        if item_meta["license"] is not None:
            licenses.append(item_meta["license"])
        if kind == "projects":
            # Each item's files in a folder named after the item, as in
            # LDbase's own project zip, so files of different items do not
            # collide. (metacheck writes "NA/" for an item without a title;
            # the item's id names its folder here.)
            title = item_meta["title"] or posixpath.basename(item)
            folder = re.sub(r'[/\\:*?"<>|]+', "_", title).strip(" \t\r\n")
            f["file_path"] = pd.Series(
                [f"{folder}/{n}" for n in f["file_name"].tolist()], index=f.index, dtype="string"
            )
        parts.append(f)
    if parts:
        files = pd.concat(parts, ignore_index=True)
        lic = list(dict.fromkeys(licenses))
        obj["license"] = _cell("; ".join(lic) if lic else None)
        obj["files"] = _obj_cell(files)
    return obj


#: LDbase's firewall answers a burst of requests with a browser check, which it
#: did for about an hour during a validation run of eight projects: pages are
#: asked for at most once a second, shared by every request of the session
#: (httr2's throttle is per host)
_THROTTLE: Any = None


def _throttle() -> Any:
    global _THROTTLE
    if _THROTTLE is None:
        from metacheck.http import Throttle

        _THROTTLE = Throttle(1, 1.0)
    return _THROTTLE


def _ldbase_get(url: str) -> Any:
    """Port of R/archive-ldbase.R::.ldbase_get(): one LDbase page as an HTML tree, or ``None``.

    The request is retried on LDbase's occasional 500/503 answers. A firewall
    browser check is an error, recorded as the link's ``error``.
    """
    from metacheck import http
    from metacheck.archives.dataone import _resp_body_string

    resp = http.request(
        "GET", url, headers={"Accept": "text/html"}, throttle=_throttle(), timeout=60
    )
    if resp is None:
        return None
    if _waf_challenge_check(resp, url):
        raise RuntimeError("firewall browser check (AWS WAF challenge); try again later")
    if resp.status_code != 200:
        return None
    try:
        return _read_html(_resp_body_string(resp))
    except Exception:
        return None


def _read_html(text: str | None) -> Any:
    """``xml2::read_html(text, encoding = "UTF-8")``: a lenient HTML parse."""
    from lxml import etree
    from lxml import html as lxml_html

    if text is None:
        raise ValueError("`x` must be a single string, not a character `NA`.")
    parser = lxml_html.HTMLParser(
        encoding="utf-8", recover=True, remove_blank_text=True, huge_tree=True, no_network=True
    )
    root = etree.fromstring(text.encode("utf-8", "surrogateescape"), parser)
    if root is None:
        raise ValueError("Document is empty")
    return root


def _ldbase_page_meta(html: Any) -> dict[str, str | None]:
    """Port of R/archive-ldbase.R::.ldbase_page_meta(): title, DOI and licence of an LDbase page.

    The title and licence come from the page's schema.org JSON-LD (the title
    falls back to the page's first heading), the DOI from its DOI field.
    """
    out: dict[str, str | None] = {"title": None, "doi": None, "license": None}
    for script in html.xpath("//script[@type='application/ld+json']"):
        try:
            data = json.loads("".join(script.itertext()))
        except ValueError:
            continue
        for x in data if isinstance(data, list) else [data]:
            if not isinstance(x, dict) or x.get("@type") == "BreadcrumbList":
                continue
            if isinstance(x.get("name"), str):
                out["title"] = x["name"]
            if isinstance(x.get("license"), str):
                out["license"] = x["license"]
    if out["title"] is None:
        h1 = html.xpath("//h1")
        if h1:
            text = h1[0].text_content()
            out["title"] = re.sub(r"^[^:]+:\s*", "", text).strip(" \t\r\n")
    for href in html.xpath("//div[contains(@class,'field--name-field-doi')]//a/@href"):
        m = re.search(r"10\.33009/[^\s\"'<>]+", str(href))
        if m:
            out["doi"] = m.group(0)
            break
    return out


def _ldbase_project_items(html: Any) -> list[str]:
    """Port of R/archive-ldbase.R::.ldbase_project_items(): the item pages of a project.

    Every dataset, code and document page in the project's "Project
    Hierarchy" tree, below the project itself (the tree item marked current),
    child projects included.
    """
    hrefs = [
        str(h)
        for h in html.xpath("//li[contains(@class,'project-tree-view-item-current')]//a/@href")
    ]
    # Without the tree (a page layout change), fall back to every item link on
    # the page, which may be only the most recent ones.
    if not hrefs:
        hrefs = [str(h) for h in html.xpath("//a/@href")]
    hrefs = [h for h in hrefs if re.match(r"^/(datasets|code|documents)/", h)]
    pages = [_ldbase_page_url("https://ldbase.org" + h) for h in hrefs]
    urls = list(dict.fromkeys(p for p in pages if p is not None))
    return [u for u in urls if _ldbase_type(u)[0] in ("datasets", "code", "documents")]


def _ldbase_item_files(html: Any, item_url: str) -> pd.DataFrame:
    """Port of R/archive-ldbase.R::.ldbase_item_files(): the current file(s) of an item page.

    The links in the page's file field (datasets, code and documents have
    their own); older dataset versions are not linked there. Columns
    ``file_name``, ``file_path``, ``file_url``, ``item_type`` and ``item_url``;
    no rows for an item stored elsewhere.
    """
    from metacheck.archives.dataverse import _string_series
    from metacheck.utils import _url_decode

    hrefs = [
        str(h)
        for h in html.xpath(
            "//div[contains(@class,'field--name-field-file-upload') or "
            "contains(@class,'field--name-field-code-file') or "
            "contains(@class,'field--name-field-document-file')]//a/@href"
        )
    ]
    hrefs = list(dict.fromkeys(h for h in hrefs if "/system/files/" in h))
    if not hrefs:
        return pd.DataFrame({c: pd.Series([], dtype="string") for c in _FILE_COLUMNS})
    file_url = [h if re.match(r"^https?://", h) else "https://ldbase.org" + h for h in hrefs]
    file_url = [re.sub(r"^http://", "https://", u) for u in file_url]
    names = _url_decode([posixpath.basename(h.rstrip("/")) for h in hrefs])
    kind = (_ldbase_type(item_url) or [None])[0]
    return pd.DataFrame(
        {
            "file_name": _string_series(names),
            "file_path": _string_series(names),
            "file_url": _string_series(file_url),
            "item_type": _string_series([kind] * len(hrefs)),
            "item_url": _string_series([item_url] * len(hrefs)),
        }
    )
