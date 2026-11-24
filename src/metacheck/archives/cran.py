"""CRAN (port of ``R/archive-cran.R``, metacheck #478, #660).

A paper that presents an R package often shares its code on CRAN only. A CRAN package lists its
authors (CRAN requires it), so whose package a link names is reliable: in 40 sampled links from
19 corpora, a paper author was a package author in all 18 packages the paper presented and in
none of the 22 it only used (lme4, vegan, ggplot2). The paper's own package would be listed, not
downloaded: CRAN has checked it, and its code is a package, not the paper's analysis.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from metacheck._r import bind_rows
from metacheck._values import is_missing

__all__ = ["cran_info", "cran_links"]

#: R ``.cran_rx``: a CRAN package link, as papers write it (#478): the package page in its forms
#: (".../web/packages/<pkg>", ".../package=<pkg>", once "package-<pkg>"), a file of the package
#: (its manual, a vignette), and the package DOI.
_CRAN_RX = (
    r"(?i)cran\.r-project\.org/(?:web/packages/|package=|package-)([A-Za-z][A-Za-z0-9.]*[A-Za-z0-9])"
    r"|10\.32614/CRAN\.package\.([A-Za-z][A-Za-z0-9.]*[A-Za-z0-9])"
)


def _cran_url(pkg: Any) -> Any:
    """Port of ``.cran_url()``: a package's CRAN address."""
    if isinstance(pkg, str):
        return f"https://CRAN.R-project.org/package={pkg}"
    return [f"https://CRAN.R-project.org/package={p}" for p in pkg]


def _cran_pkg_of(url: Any) -> Any:
    """Port of ``.cran_pkg_of()``: the package a link names (``None`` for none).

    A string gives a string, a sequence a list.
    """
    rx = re.compile(_CRAN_RX)

    def one(u: Any) -> str | None:
        if is_missing(u):
            return None
        m = rx.search(str(u))
        if m is None:
            return None
        return m.group(1) if m.group(1) else m.group(2)

    if isinstance(url, str) or not hasattr(url, "__iter__"):
        return one(url)
    return [one(u) for u in url]


def cran_links(paper: Any) -> pd.DataFrame:
    """Port of ``R/archive-cran.R::cran_links()``: CRAN package links in a paper.

    Finds links to R packages on CRAN: the package page in its usual forms
    (``https://CRAN.R-project.org/package=<name>``,
    ``cran.r-project.org/web/packages/<name>/index.html``), a file of the package (its manual or
    a vignette), and the package DOI (``10.32614/CRAN.package.<name>``). The repo_check module
    reads each package's record.

    The table has a row for each package: ``href`` (the package's CRAN address), ``package``,
    ``repo_type`` ("cran"), ``link`` (the link as the paper writes it), and ``text_id`` /
    ``paper_id``.
    """
    from metacheck.archives import _paper_urls
    from metacheck.text.search import text_search

    none = pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "href": pd.Series([], dtype="string"),
            "package": pd.Series([], dtype="string"),
            "repo_type": pd.Series([], dtype="string"),
            "link": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
        }
    )
    rx = re.compile(_CRAN_RX)
    found_href = _paper_urls(paper)
    if "href" in found_href.columns:
        keep = [isinstance(h, str) and rx.search(h) is not None for h in found_href["href"]]
        found_href = found_href[pd.Series(keep, index=found_href.index, dtype=bool)]
    else:
        found_href = found_href.iloc[0:0]
    hits = text_search(paper, _CRAN_RX, return_="match", perl=True)
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
    pkgs = _cran_pkg_of(links["href"].tolist())
    # the text's own form first, as the sentence holds it
    rows = [(i, p) for i, p in enumerate(pkgs) if p is not None]
    rows.sort(key=lambda ip: bool(pd.isna(links["text_id"].iloc[ip[0]])))
    seen: set[tuple[Any, str]] = set()
    out_rows = []
    for i, p in rows:
        pid = None if pd.isna(links["paper_id"].iloc[i]) else links["paper_id"].iloc[i]
        if (pid, p) in seen:
            continue
        seen.add((pid, p))
        out_rows.append((i, p, pid))
    if not out_rows:
        return none
    return pd.DataFrame(
        {
            "paper_id": pd.Series([r[2] for r in out_rows], dtype="string"),
            "href": pd.Series(_cran_url([r[1] for r in out_rows]), dtype="string"),
            "package": pd.Series([r[1] for r in out_rows], dtype="string"),
            "repo_type": pd.Series(["cran"] * len(out_rows), dtype="string"),
            "link": pd.Series([links["href"].iloc[r[0]] for r in out_rows], dtype="string"),
            "text_id": pd.Series(
                [
                    None if pd.isna(links["text_id"].iloc[r[0]]) else links["text_id"].iloc[r[0]]
                    for r in out_rows
                ],
                dtype="Int64",
            ),
        }
    )


def _cran_people(author: Any) -> str | None:
    """Port of ``.cran_people()``: a package's authors as "Name; Name".

    "Roger Bivand [cre, aut] (<https://orcid.org/...>), Nicholas Lewin-Koh [aut]" ->
    "Roger Bivand; Nicholas Lewin-Koh". ``None`` when no name is left.
    """
    a = re.sub(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>", " ", "" if author is None else str(author))
    parts = [p.strip() for p in re.split(r",|\band\b|\n", a)]
    names = [re.sub(r"\s+", " ", p) for p in parts if len(p) > 1]
    return "; ".join(dict.fromkeys(names)) if names else None


def cran_info(
    cran_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of ``R/archive-cran.R::cran_info()``: the records of CRAN packages.

    Reads each package's record from crandb (the CRAN package database), also for an archived
    package: its title, authors, version, publication date and description. *cran_url* is the
    packages' CRAN addresses, as :func:`cran_links` gives them in ``href``, or a table with them
    in column *id_col*. Returns a row per address: ``cran_url``, ``package``, ``title``,
    ``creators`` (names separated by "; "), ``version``, ``publication_date``, ``description``,
    ``resource_type`` ("Software"), and ``error`` ("unfound" when CRAN has no such package).
    With *cache*, each answer is kept for the session.
    """
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _chr_values
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.archives.psycharchives import _column
    from metacheck.archives.seq import _seq_get

    raw: Any = (
        cran_url[_column(cran_url, id_col)].tolist()
        if isinstance(cran_url, pd.DataFrame)
        else cran_url
    )
    values, _ = _chr_values(raw)
    urls = [u for u in dict.fromkeys(v for v in values if v is not None) if u != ""]
    cols = [
        "cran_url",
        "package",
        "title",
        "creators",
        "version",
        "publication_date",
        "description",
        "resource_type",
        "error",
    ]
    if not urls:
        return pd.DataFrame({c: pd.Series([], dtype="string") for c in cols})
    rows: list[pd.DataFrame] = []
    with _spinner(pb, "Retrieve") as bar:
        for u in urls:
            cached = _repo_info_cache_get("cran", u) if cache is True else None
            if cached is not None:
                rows.append(cached)
                continue
            pkg = _cran_pkg_of(u)
            _tick(bar, f"* Retrieving the CRAN record of {pkg}...")
            obj: dict[str, Any] = {
                "cran_url": u,
                "package": pkg,
                "title": None,
                "creators": None,
                "version": None,
                "publication_date": None,
                "description": None,
                "resource_type": "Software",
                "error": None,
            }
            txt = _seq_get(f"https://crandb.r-pkg.org/{pkg}")
            try:
                j = json.loads(txt or "")
            except Exception:
                j = None
            if not isinstance(j, dict) or j.get("Package") is None:
                obj["error"] = "unfound"
            else:
                title = j.get("Title")
                obj["title"] = re.sub(r"\s+", " ", str(title)) if title is not None else None
                author = j.get("Author") if j.get("Author") is not None else j.get("Maintainer")
                obj["creators"] = _cran_people(author)
                obj["version"] = None if j.get("Version") is None else str(j["Version"])
                when = j.get("Date/Publication") or j.get("Date")
                obj["publication_date"] = None if when is None else str(when)[:10]
                desc = j.get("Description")
                obj["description"] = (
                    re.sub(r"\s+", " ", str(desc))[:2000] if desc is not None else None
                )
            res = pd.DataFrame({k: pd.Series([v], dtype="string") for k, v in obj.items()})
            if cache is True and _repo_info_ok(res):
                _repo_info_cache_put("cran", u, res)
            rows.append(res)
    return bind_rows(rows)
