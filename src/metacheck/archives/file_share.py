"""File-sharing services (port of ``R/file-share.R``, metacheck #660).

Google Drive, Dropbox, OneDrive and the like are not repositories. Files there can be changed or
deleted at any time, they get no DOI, and a link stops working when the account is closed, so
repo_check warns for every such link. The files are not read: these services give no access
without a login or a key.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from metacheck._r import bind_rows

__all__ = ["file_share_links"]

_END = r"""[^\s<>"')\]]*"""

#: R ``.file_share_services``: the services, each with the pattern of its sharing links (host and
#: path). Google Forms is a survey tool, not a place to share files, and is left out.
_FILE_SHARE_SERVICES: dict[str, str] = {
    "Google Drive": (
        rf"(?:drive\.google\.com|drive\.usercontent\.google\.com)/{_END}"
        rf"|docs\.google\.com/(?:document|spreadsheets|presentation)/{_END}"
    ),
    "Dropbox": rf"(?:www\.)?dropbox\.com/(?:s|sh|scl|l)/{_END}|dl\.dropboxusercontent\.com/{_END}",
    "OneDrive": rf"1drv\.ms/{_END}|onedrive\.live\.com/{_END}|[a-z0-9-]+\.sharepoint\.com/{_END}",
    "Box": rf"(?:[a-z0-9-]+\.)*box\.com/(?:s|shared|v)/{_END}",
    "WeTransfer": rf"(?:wetransfer\.com/downloads|we\.tl)/{_END}",
    "MEGA": rf"mega\.(?:nz|co\.nz|io)/(?:file|folder|#){_END}",
    "iCloud Drive": rf"(?:www\.)?icloud\.com/iclouddrive/{_END}",
    "Baidu Netdisk": rf"pan\.baidu\.com/{_END}",
    "SURFdrive": rf"surfdrive\.surf\.nl/{_END}",
}


def _file_share_regex() -> str:
    """Port of ``.file_share_regex()``: a link to any of the services."""
    return "(?i)(?:https?://)?(?:www\\.)?(?:" + "|".join(_FILE_SHARE_SERVICES.values()) + ")"


def _file_share_regex_archived() -> str:
    """Port of ``.file_share_regex_archived()``: the same, also matching the Internet Archive's address in front.

    ("web.archive.org/web/20220203164905/https://docs.google.com/..."): such a link is an archived
    copy, which lasts, so it is found in order to be left out (3 Collabra papers cited lists on
    Google Sheets this way).
    """
    return (
        "(?i)(?:(?:https?://)?web\\.archive\\.org/web/[0-9a-z_]+/)?(?:https?://)?(?:www\\.)?(?:"
        + "|".join(_FILE_SHARE_SERVICES.values())
        + ")"
    )


def _file_share_service(url: Any) -> Any:
    """Port of ``.file_share_service()``: the service a link belongs to (``None`` for none).

    A string gives a string, a sequence a list.
    """
    if isinstance(url, str) or not hasattr(url, "__iter__"):
        return _service_of(url)
    return [_service_of(u) for u in url]


def _service_of(u: Any) -> str | None:
    if u is None or (isinstance(u, float) and u != u) or u is pd.NA:
        return None
    # R loops over the services in reverse, so the first one that matches wins
    for name, pat in _FILE_SHARE_SERVICES.items():
        if re.search(rf"(?i)(?:^|//|\.|\b)(?:{pat})", str(u)):
            return name
    return None


def file_share_links(paper: Any) -> pd.DataFrame:
    """Port of ``R/file-share.R::file_share_links()``: links to file-sharing services in a paper.

    Finds links to file-sharing services that are not repositories: Google Drive (and Google
    Docs, Sheets and Slides), Dropbox, OneDrive and SharePoint, Box, WeTransfer, MEGA, iCloud
    Drive, Baidu Netdisk and SURFdrive, as hyperlinks or in the text, the reference list included.
    Files on these services can be changed or deleted at any time, get no DOI, and the link stops
    working when the account is closed, so data and code belong in a repository instead. Google
    Forms, a survey tool, is not included.

    The table has a row for each link (``href``, plus ``text_id`` / ``paper_id``) and
    ``service``, the name of the file-sharing service.
    """
    from metacheck.archives import _paper_urls
    from metacheck.text.search import text_search

    none = pd.DataFrame(
        {"href": pd.Series([], dtype="string"), "service": pd.Series([], dtype="string")}
    )
    rx = re.compile(_file_share_regex())
    found_href = _paper_urls(paper)
    if "href" in found_href.columns:
        keep_rows = [isinstance(h, str) and rx.search(h) is not None for h in found_href["href"]]
        found_href = found_href[pd.Series(keep_rows, index=found_href.index, dtype=bool)]
    else:
        found_href = found_href.iloc[0:0]
    hits = text_search(
        paper, _file_share_regex_archived(), return_="match", perl=True, include_refs=True
    )
    ids = [c for c in ("text_id", "paper_id") if c in hits.columns]
    in_text = (
        hits.loc[:, ["text", *ids]].rename(columns={"text": "href"})
        if "text" in hits.columns
        else hits.loc[:, ids]
    )
    links = bind_rows([found_href, in_text])
    if len(links) == 0 or "href" not in links.columns:
        return none
    # the punctuation that ends a sentence is not part of the link
    links = links.copy()
    links["href"] = pd.Series(
        [
            re.sub(r"[.,;:]+$", "", h.strip()) if isinstance(h, str) else h
            for h in links["href"].tolist()
        ],
        index=links.index,
        dtype="string",
    )
    ok = [isinstance(h, str) and rx.search(h) is not None for h in links["href"]]
    links = links[pd.Series(ok, index=links.index, dtype=bool)]
    # an archived copy lasts (see _file_share_regex_archived()): no warning
    arch = [
        re.search(r"web\.archive\.org/web/", h, re.IGNORECASE) is not None for h in links["href"]
    ]
    links = links[pd.Series([not a for a in arch], index=links.index, dtype=bool)]
    if len(links) == 0:
        return none
    if "paper_id" not in links.columns:
        links["paper_id"] = pd.Series([None] * len(links), index=links.index, dtype="string")
    keep = [c for c in ("paper_id", "href", "text_id") if c in links.columns]
    links = links.loc[:, keep].drop_duplicates()
    links = links[~links.duplicated(subset=["paper_id", "href"])].reset_index(drop=True)
    # one link written twice, in full and shortened ("https://nihcc.app.box.com/v/x" as a
    # hyperlink, "app.box.com/v/x" read from the text): the full form stays
    core = [re.sub(r"^https?://(www\.)?", "", h.lower()) for h in links["href"]]
    pids = [None if pd.isna(p) else p for p in links["paper_id"].tolist()]
    part = [
        any(
            pids[j] == pids[i] and len(core[j]) > len(core[i]) and core[j].endswith(core[i])
            for j in range(len(links))
            if j != i
        )
        for i in range(len(links))
    ]
    links = links[pd.Series([not p for p in part], index=links.index, dtype=bool)]
    links = links.reset_index(drop=True)
    links["service"] = pd.Series(_file_share_service(links["href"].tolist()), dtype="string")
    return links
