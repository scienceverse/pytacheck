"""Handle links (port of ``R/handle-links.R``, metacheck #660).

"hdl.handle.net/10411/XYZ" names a deposit by its Handle, which leads to the deposit's address on
its host (DataverseNL, a DSpace installation, an institutional Figshare). The Handle system's
public interface gives that address in one request; when one of repo_check's finders recognises
it, the deposit is checked like a link to that address. In 19 corpora (18,708 papers) there were
119 Handle links in 96 papers; of 40 sampled, 36 resolved and 12 led to a host metacheck reads
(most of the others to theses and articles in institutional repositories).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pandas as pd

from metacheck._r import bind_rows
from metacheck._values import is_missing
from metacheck.archives._memo import session_memo

__all__ = ["handle_links"]

#: R ``.handle_cache``: resolved Handles, for the session
_HANDLE_CACHE = session_memo()

_HANDLE_RX = (
    r"""(?i)(?:https?://)?(?:hdl\.)?handle\.net/([0-9]{2,5}(?:\.[0-9]+)*/[^\s<>"')\],;]+)"""
)


def _repo_link_finders() -> dict[str, Callable[[Any], pd.DataFrame]]:
    """Port of ``.repo_link_finders()``: repo_check's link finders, by repository type.

    For reading a list of addresses (the reference list, resolved Handles) with the same rules.
    """
    from metacheck.archives.ckan import ckan_links
    from metacheck.archives.dataone import dataone_links
    from metacheck.archives.dataverse import dataverse_links
    from metacheck.archives.dryad import dryad_links
    from metacheck.archives.dspace7 import dspace7_links
    from metacheck.archives.figshare import figshare_links
    from metacheck.archives.fourtu import researchdata4tu_links
    from metacheck.archives.github import github_links
    from metacheck.archives.gitlab import gitlab_links
    from metacheck.archives.ldbase import ldbase_links
    from metacheck.archives.mendeley import mendeley_links
    from metacheck.archives.osf import osf_links
    from metacheck.archives.psycharchives import dspace_links
    from metacheck.archives.researchbox import rbox_links
    from metacheck.archives.reshare import eprints_links
    from metacheck.archives.zenodo import invenio_links

    return {
        "osf": osf_links,
        "github": github_links,
        "gitlab": gitlab_links,
        "researchbox": rbox_links,
        "dspace": dspace_links,
        "dspace7": dspace7_links,
        "zenodo": invenio_links,
        "dataverse": dataverse_links,
        "figshare": figshare_links,
        "dryad": dryad_links,
        "reshare": eprints_links,
        "researchdata4tu": researchdata4tu_links,
        "mendeley": mendeley_links,
        "dataone": dataone_links,
        "ldbase": ldbase_links,
        "ckan": ckan_links,
    }


def _handle_resolve(handle: str) -> str | None:
    """Port of ``.handle_resolve()``: where a Handle leads (``None`` when it does not, or cannot be asked)."""
    from metacheck.archives.dspace7 import _handle_registered_url

    if handle in _HANDLE_CACHE:
        return _HANDLE_CACHE[handle]  # type: ignore[no-any-return]
    url, ok = _handle_registered_url(handle, api="https://hdl.handle.net/api/handles/")
    if ok:
        _HANDLE_CACHE[handle] = url
    return url


def handle_links(paper: Any) -> pd.DataFrame:
    """Port of ``R/handle-links.R::handle_links()``: deposits cited by their Handle.

    Finds Handle links (``hdl.handle.net/<prefix>/<suffix>``) in a paper, asks the Handle system
    where each leads, and keeps those that lead to a repository the repo_check module reads (a
    Dataverse installation such as DataverseNL or Borealis, a DSpace installation, an
    institutional Figshare), as a link to that address. A Handle that leads elsewhere (a thesis in
    an institutional repository, a web page) is left out.

    The table has a row for each deposit: ``href`` (the address the Handle leads to),
    ``repo_type`` (the repository type, as repo_check names it), ``handle`` (the link the paper
    gave), and ``text_id`` / ``paper_id``.
    """
    from metacheck.archives import _paper_urls
    from metacheck.papers.io import test_paper
    from metacheck.text.search import text_search

    none = pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "href": pd.Series([], dtype="string"),
            "repo_type": pd.Series([], dtype="string"),
            "handle": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
        }
    )
    rx = re.compile(_HANDLE_RX)
    found_href = _paper_urls(paper)
    if "href" in found_href.columns:
        keep = [isinstance(h, str) and rx.search(h) is not None for h in found_href["href"]]
        found_href = found_href[pd.Series(keep, index=found_href.index, dtype=bool)]
    else:
        found_href = found_href.iloc[0:0]
    hits = text_search(paper, _HANDLE_RX, return_="match", perl=True, include_refs=True)
    ids = [c for c in ("text_id", "paper_id") if c in hits.columns]
    in_text = (
        hits.loc[:, ["text", *ids]].rename(columns={"text": "href"})
        if "text" in hits.columns
        else hits.loc[:, ids]
    )
    links = bind_rows([found_href, in_text])
    if len(links) == 0:
        return none
    links = links.copy()
    if "paper_id" not in links.columns:
        links["paper_id"] = pd.Series([None] * len(links), index=links.index, dtype="string")
    if "text_id" not in links.columns:
        links["text_id"] = pd.Series([None] * len(links), index=links.index, dtype="Int64")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, str]] = set()
    for href, pid, tid in zip(
        links["href"].tolist(), links["paper_id"].tolist(), links["text_id"].tolist(), strict=True
    ):
        if is_missing(href):
            continue
        href = re.sub(r"[.]+$", "", str(href).strip())
        m = rx.search(href)
        if m is None:
            continue
        handle = m.group(1)
        p = None if is_missing(pid) else pid
        if (p, handle) in seen:
            continue
        seen.add((p, handle))
        rows.append(
            {
                "paper_id": p,
                "href": href,
                "handle": handle,
                "text_id": None if is_missing(tid) else tid,
            }
        )
    for r in rows:
        try:
            r["resolved"] = _handle_resolve(r["handle"])
        except Exception:
            r["resolved"] = None
    rows = [r for r in rows if r["resolved"]]
    if not rows:
        return none
    # which finder knows the address: each address read as one sentence
    q = test_paper([r["resolved"] for r in rows])
    found: list[tuple[str, int, str]] = []
    for k, finder in _repo_link_finders().items():
        try:
            x = finder(q)
        except Exception:  # noqa: S112 - a finder that fails finds nothing, as in R's tryCatch
            continue
        if not isinstance(x, pd.DataFrame) or len(x) == 0 or "href" not in x.columns:
            continue
        tids = x["text_id"].tolist() if "text_id" in x.columns else [None] * len(x)
        for h, i in zip(x["href"].tolist(), tids, strict=True):
            found.append((re.sub(r"[.,;:]+$", "", str(h)), i, k))
    found = [f for f in found if not is_missing(f[1])]
    first: dict[int, tuple[str, int, str]] = {}
    for f in found:
        first.setdefault(int(f[1]), f)
    if not first:
        return none
    # (R keeps the finders' order: the first finder's hits first)
    kept = []
    done: set[int] = set()
    for h, i, k in found:
        if int(i) in done:
            continue
        done.add(int(i))
        kept.append((h, int(i), k))
    return pd.DataFrame(
        {
            "paper_id": pd.Series([rows[i - 1]["paper_id"] for _, i, _ in kept], dtype="string"),
            "href": pd.Series([h for h, _, _ in kept], dtype="string"),
            "repo_type": pd.Series([k for _, _, k in kept], dtype="string"),
            "handle": pd.Series([rows[i - 1]["href"] for _, i, _ in kept], dtype="string"),
            "text_id": pd.Series([rows[i - 1]["text_id"] for _, i, _ in kept], dtype="Int64"),
        }
    )
