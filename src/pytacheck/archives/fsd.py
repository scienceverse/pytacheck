"""Finnish Social Science Data Archive (port of ``R/archive-fsd.R``).

FSD (the "Aila Data Service", Tampere University) publishes an open DDI-C 2.5
XML record per study, which :func:`fsd_info` reads for the study's title,
DOI, access restriction and data-file inventory. The record carries no
download URI and data access needs a login or an access application, so
there is no file download here: the listing is for reporting what a study
contains.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from pytacheck._r import is_na

if TYPE_CHECKING:
    from lxml import etree

__all__ = ["fsd_info", "fsd_links"]

#: R ``.FSD_DDI_BASE``: where the DDI records live.
_FSD_DDI_BASE = "https://services.fsd.tuni.fi/catalogue"

_STUDY_RX = r"(?i)fsd[:_-]?([0-9]{3,6})"


def _fsd_study_id(url: Any) -> Any:
    """Port of R/archive-fsd.R::.fsd_study_id(): the ``FSD<digits>`` study id in a reference.

    Accepts the catalogue URL, the ``10.60686/t-fsd<digits>`` DOI, the
    ``urn.fi`` URN or a bare ``FSD<digits>`` mention; ``None`` when there is
    no study id. A string gives a string, a sequence a list aligned with it
    (metacheck is not vectorised: a vector with two ids is an error and one
    without a match in every element loses its alignment: U44).
    """
    from pytacheck._r import as_character, regextract, sub

    if url is None:
        return None
    scalar = isinstance(url, str) or not isinstance(url, list | tuple | pd.Series)
    vals = [url] if scalar else list(url)
    strs = [None if is_na(v) else (v if isinstance(v, str) else as_character(v)) for v in vals]
    out: list[str | None] = []
    for m in regextract(_STUDY_RX, strs, perl=True):
        if m is None or m == "":
            out.append(None)
            continue
        digits = sub(r"(?i).*?([0-9]{3,6})$", r"\1", m, perl=True)
        out.append(f"FSD{digits}")
    return out[0] if scalar else out


_FSD_URL_RX = (
    r"(?:https?://)?(?:www\.|services\.)?fsd\.tuni\.fi/[A-Za-z0-9/._?=&%-]+"
    r"|(?:https?://)?doi\.org/10\.60686/[A-Za-z0-9.-]+"
    r"|(?:https?://)?urn\.fi/urn:nbn:fi:fsd:[A-Za-z0-9.-]+"
)


def fsd_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-fsd.R::fsd_links(): FSD links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table to the FSD
    catalogue, a ``10.60686`` DOI or a ``urn.fi`` FSD URN, plus bare
    ``FSD<digits>`` mentions in the text. Trailing slashes are stripped and
    duplicate rows dropped.
    """
    from pytacheck._r import grepl
    from pytacheck.archives.dataone import _scan_links
    from pytacheck.archives.dataverse import _collect_links
    from pytacheck.papers.tables import paper_table

    urls = paper_table(paper, "url")
    if "href" in urls.columns:
        keep = [bool(v) for v in grepl(_FSD_URL_RX, urls["href"], ignore_case=True, perl=True)]
        found_href = urls[pd.Series(keep, index=urls.index, dtype=bool)]
    elif len(urls) > 0:
        # R: `href` is then the (NULL) local variable, and grepl() a logical(0) filter
        raise ValueError(f"`..1` must be of size {len(urls)} or 1, not size 0.")
    else:
        found_href = urls  # an empty paper list: a table without columns
    other = _scan_links(paper, "FSD[0-9]{3,6}", ["fsd"], anchor=None)
    return _collect_links([found_href, other])


def fsd_info(
    fsd_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-fsd.R::fsd_info(): look up FSD studies.

    *fsd_url* is a reference (catalogue URL, DOI, URN or ``FSD<digits>``), a
    sequence of them, or a table whose *id_col* (1-based position or name)
    holds them (e.g. from :func:`fsd_links`). Each distinct reference is
    looked up in turn (with *cache*, from the on-disk listing cache when
    possible), stopping at the first that fails; the input rows are returned
    with ``FSD_title``, ``FSD_doi``, ``FSD_access`` and ``files`` (or
    ``error``) added.
    """
    from pytacheck.archives.psycharchives import _info_loop
    from pytacheck.utils import online

    # the bare domain does not resolve; www. and services. do
    if not online("www.fsd.tuni.fi"):
        raise ConnectionError("FSD (fsd.tuni.fi) seems to be offline")
    return _info_loop(
        fsd_url,
        id_col,
        pb,
        cache,
        url_col="fsd_url",
        label="FSD",
        noun="item",
        cache_ns="fsd",
        one=_fsd_info,
        suffix=".fsd",
    )


def _fsd_info(fsd_url: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-fsd.R::.fsd_info(): one FSD study.

    Returns a one-row table: ``fsd_url`` and either ``error`` (``"unfound"``,
    with a warning) or ``FSD_title``, ``FSD_doi``, ``FSD_access`` and
    ``files`` (a table of ``name``, ``format``, ``cases``, ``variables``).
    """
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataone import _find_first, _xml_text
    from pytacheck.archives.dataverse import _cell, _paste
    from pytacheck.archives.psycharchives import _obj_cell

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from {_paste(fsd_url)}...")
        obj: dict[str, pd.Series] = {"fsd_url": _cell(fsd_url)}

        study_id = _fsd_study_id(fsd_url)
        if study_id is None:
            warnings.warn(f"{_paste(fsd_url)} is not a valid FSD study reference", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        doc = _fsd_ddi_xml(study_id)
        if doc is None:
            warnings.warn(f"{_paste(fsd_url)} could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        obj["FSD_title"] = _cell(_xml_text(_find_first(doc, "//stdyDscr/citation/titlStmt/titl")))
        obj["FSD_doi"] = _cell(
            _xml_text(_find_first(doc, "//stdyDscr/citation/titlStmt/IDNo[@agency='DOI']"))
        )
        obj["FSD_access"] = _cell(
            _xml_text(_find_first(doc, "//stdyDscr/dataAccs/useStmt/restrctn"))
        )

        nodes = cast("list[etree._Element]", doc.xpath("//fileDscr"))
        obj["files"] = _obj_cell(
            pd.DataFrame(
                {
                    "name": pd.Series(
                        [_node_text(n, ".//fileName") for n in nodes], dtype="string"
                    ),
                    "format": pd.Series(
                        [_node_text(n, ".//fileType") for n in nodes], dtype="string"
                    ),
                    "cases": pd.Series(
                        [_as_integer(_node_text(n, ".//caseQnty")) for n in nodes], dtype="Int64"
                    ),
                    "variables": pd.Series(
                        [_as_integer(_node_text(n, ".//varQnty")) for n in nodes], dtype="Int64"
                    ),
                }
            )
        )
        return pd.DataFrame(obj)


def _node_text(node: etree._Element, path: str) -> str | None:
    """R ``.node_text()`` inside ``.fsd_info()``: text of the first match, or ``NA``."""
    from pytacheck.archives.dataone import _find_first, _xml_text

    return _xml_text(_find_first(node, path))


def _as_integer(x: str | None) -> int | None:
    """``suppressWarnings(as.integer(x))`` of a string: truncated, ``NA`` when invalid."""
    import math

    from pytacheck.archives.dataone import _as_numeric_quiet

    v = _as_numeric_quiet(x)
    if math.isnan(v) or math.isinf(v) or abs(v) >= 2**31:
        return None
    return int(v)


def _strip_default_ns(root: etree._Element) -> None:
    """``xml2::xml_ns_strip()``: drop default (unprefixed) namespaces, in place."""
    from lxml import etree

    for el in root.iter():
        tag = el.tag
        # an undeclared prefix leaves a plain "prefix:name" tag (no namespace)
        if isinstance(tag, str) and tag.startswith("{") and el.prefix is None:
            el.tag = tag.rsplit("}", 1)[1]
    etree.cleanup_namespaces(root)


def _fsd_ddi_xml(study_id: str) -> etree._Element | None:
    """Port of R/archive-fsd.R::.fsd_ddi_xml(): a study's DDI record, namespace-stripped.

    ``None`` when the record cannot be fetched (any failure or non-200) or
    parsed.
    """
    from pytacheck import http
    from pytacheck.archives.dataone import _read_xml, _resp_body_string

    url = f"{_FSD_DDI_BASE}/{study_id}/DDI/{study_id}_eng.xml"
    try:
        resp = http.request("GET", url, max_tries=1)  # httr2 default: no retries
        if resp is None or resp.status_code != 200:
            return None
        doc = _read_xml(_resp_body_string(resp))  # R: read_xml(resp_body_string(resp))
        _strip_default_ns(doc)
        return doc
    except Exception:
        return None
