"""Is a repository record a data or code deposit, or a publication? (port of ``R/repo-kind.R``)

Institutional repositories (DSpace, EPrints) hold papers, books and theses beside data, and a
paper often cites one of its own earlier papers or a thesis there. Such a record is not a
deposit, and ``repo_check`` ignores it (metacheck #637).

* A DOI's kind is known before its record is read: DataCite's record of a DOI gives its registered
  address and its resource type in one request. A DOI DataCite does not have (a journal article
  registered with Crossref) is looked up through the handle service, as before.
* A plain link carries no type, so the record read anyway decides: EPrints records give ``type``,
  DSpace items ``dc.type``, Invenio's ``resource_type``. When the record gives none, the deposit is
  kept only when it holds data or code files.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from metacheck._values import field, is_missing
from metacheck.archives._memo import session_memo

__all__ = ["PUBLICATION_RESOURCE_TYPES"]

#: R ``.publication_resource_types``: DataCite resource types (``resourceTypeGeneral``) of
#: publications
PUBLICATION_RESOURCE_TYPES: tuple[str, ...] = (
    "Text",
    "JournalArticle",
    "Journal",
    "Book",
    "BookChapter",
    "ConferencePaper",
    "ConferenceProceeding",
    "Dissertation",
    "Preprint",
    "Report",
    "PeerReview",
    "Standard",
)

_PUBLICATION_RX = re.compile(
    r"\b(publication|article|articles|book|book section|chapter|monograph"
    r"|conference( item| paper| proceedings?)?"
    r"|proceedings?|thesis|theses|dissertation|patent|report|working paper|preprint|review|journal|text"
    r"|journalarticle|bookchapter|conferencepaper|conferenceproceeding|peerreview|standard"
    r"|letter|editorial|poster|presentation|lecture)\b"
)
_DATA_RX = re.compile(r"\b(dataset|data set|data collection|data|software|code)\b")
_SEPARATORS = re.compile(r"[_-]+")
_PUBLICATION_LOWER = frozenset(t.lower() for t in PUBLICATION_RESOURCE_TYPES)


def _publication_one(value: Any) -> bool | None:
    if is_missing(value):
        return None
    t = _SEPARATORS.sub(" ", str(value).strip().lower())
    if t == "":
        return None
    out = _PUBLICATION_RX.search(t) is not None or t in _PUBLICATION_LOWER
    # a record that also names data or software is not a publication
    return out and _DATA_RX.search(t) is None


def _repo_publication_type(type: Any) -> Any:
    """Port of R/repo-kind.R::.repo_publication_type(): does a record's own type say it is a publication?

    The type is EPrints' ``type``, DSpace's ``dc.type`` or a DataCite/Invenio resource type.
    EPrints' own publication types are article, book, book_section, monograph, conference_item,
    thesis and patent; DSpace's ``dc.type`` is free text ("Article", "Journal article",
    "Doctoral thesis", "Working paper", "Text", ...); Zenodo's "publication" (InvenioRDM:
    "publication-article", ...), poster and presentation are publications too. A type that also
    names data or software is not a publication. A string gives ``True``/``False``/``None`` (``None``:
    no type); a sequence gives a list.
    """
    if isinstance(type, str) or not isinstance(type, Sequence):
        return _publication_one(type)
    return [_publication_one(v) for v in type]


#: R ``.repo_doi_cache``: lower-cased DOI -> ``{"url", "resource_type", "in_datacite"}``
_DOI_CACHE = session_memo()


def _repo_doi_records(doi: Sequence[str]) -> list[dict[str, Any]]:
    """Port of R/repo-kind.R::.repo_doi_records(): DataCite's record of each DOI.

    Its registered address and resource type, several requests at once. One dict per distinct
    lower-cased DOI, in order: ``doi``, ``url``, ``resource_type`` and ``in_datacite`` (``False`` for
    a DOI DataCite does not have, ``None`` when the request failed: not remembered, so asked again).
    """
    from metacheck import http
    from metacheck.db._utils import url_encode

    dois = list(dict.fromkeys(str(d).lower() for d in doi if not is_missing(d) and str(d) != ""))
    todo = [d for d in dois if d not in _DOI_CACHE]
    if todo:
        resps = http.batch_query(
            [f"https://api.datacite.org/dois/{url_encode(d)}" for d in todo], msg=None
        )
        for d, resp in zip(todo, resps, strict=True):
            status = None if resp is None else resp.status_code
            attrs = None
            if resp is not None and status == 200:
                try:
                    attrs = field(http.resp_json(resp, check_type=False), "data", "attributes")
                except Exception:  # R: tryCatch(..., error = \(e) NULL)
                    attrs = None
            if attrs is not None:
                rtype = field(attrs, "types", "resourceTypeGeneral")
                url = field(attrs, "url")
                _DOI_CACHE[d] = {
                    "url": url if isinstance(url, str) and url != "" else None,
                    "resource_type": rtype if isinstance(rtype, str) and rtype != "" else None,
                    "in_datacite": True,
                }
            elif status == 404:
                _DOI_CACHE[d] = {"url": None, "resource_type": None, "in_datacite": False}
    out = []
    for d in dois:
        rec = _DOI_CACHE.get(d, {"url": None, "resource_type": None, "in_datacite": None})
        out.append({"doi": d, **rec})
    return out


def _repo_doi_url(doi: Any) -> tuple[str | None, bool, bool]:
    """Port of R/repo-kind.R::.repo_doi_url(): the address a DOI is registered with, unless it is a publication.

    DataCite's registered address, else (a DOI DataCite does not have, or DataCite did not answer)
    the handle service's. Returns ``(url, ok, publication)``: *ok* is ``False`` when no lookup
    answered, as :func:`metacheck.archives.dspace7._handle_registered_url` gives it; *publication*
    is ``True`` when DataCite calls the DOI a publication (then there is no url and *ok* is ``True``).
    """
    from metacheck.archives.dspace7 import _handle_registered_url

    if not isinstance(doi, str) or doi == "":
        return None, False, False
    rec = _repo_doi_records([doi])[0]
    if _publication_one(rec["resource_type"]) is True:
        return None, True, True
    if rec["url"] is not None:
        return str(rec["url"]), True, False
    try:
        url, ok = _handle_registered_url(doi)
    except Exception:  # R: tryCatch(.handle_registered_url(doi), error = \(e) none)
        url, ok = None, False
    return url, bool(ok), False
