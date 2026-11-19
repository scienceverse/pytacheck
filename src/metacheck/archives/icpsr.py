"""ICPSR and openICPSR, with the AEA Data and Code Repository (port of ``R/archive-icpsr.R``).

Economics journals' replication packages have DOIs ``10.3886/E<project>V<version>``, and ICPSR's
curated studies ``10.3886/ICPSR<study>``. Their records are at DataCite (title, creators, date,
and the article a package belongs to), which is enough to list a deposit. Their files cannot be
read automatically: openICPSR has moved to ICPSR's site, and every project page and download
answers an automatic request with a browser check (HTTP 403, tested 2026-11-18, also after
logging in with a registered account). The AEA Data Editor's tools pass it with a token from
ICPSR (header ``x-openicpsr-cloudflare-token``), which metacheck does not have.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from metacheck._r import bind_rows
from metacheck._values import is_missing

__all__ = ["icpsr_info", "icpsr_links"]

_DOI_RX = r"10\.3886/(?:E[0-9]{4,7}(?:V[0-9]+)?|ICPSR[0-9]{4,6}(?:\.v[0-9]+)?)"

#: R ``.icpsr_rx``: links papers write: the DOI (with or without "V<n>"), the openICPSR project
#: page, ICPSR's study pages (old and new site), and the AEA landing page of a project
_ICPSR_RX = (
    rf"(?i){_DOI_RX}"
    r"|openicpsr\.org/openicpsr/project/[0-9]{4,7}(?:/version/V[0-9]+)?"
    r"|icpsr\.umich\.edu/(?:icpsrweb/[A-Za-z]+|web/[A-Za-z]+|sites/[A-Za-z]+/view)/studies/[0-9]{4,7}"
    r"(?:/versions/V[0-9]+)?"
)


def _icpsr_doi_one(link: Any) -> str | None:
    if is_missing(link):
        return None
    ln = str(link)
    out: str | None = None
    if re.search(r"(?i)10\.3886/", ln):
        m = re.search(rf"(?i){_DOI_RX}", ln)
        out = m.group(0) if m else None
    elif re.search(r"(?i)openicpsr\.org/openicpsr/project/", ln):
        proj = re.sub(r"^.*project/([0-9]+).*$", r"\1", ln, flags=re.DOTALL)
        ver = (
            re.sub(r"(?i)^.*/version/(V[0-9]+).*$", r"\1", ln, flags=re.DOTALL).upper()
            if re.search(r"(?i)/version/V[0-9]+", ln)
            else ""
        )
        out = f"10.3886/E{proj}{ver}"
    elif re.search(r"(?i)icpsr\.umich\.edu/.*/studies/[0-9]+", ln):
        # a study page names a curated study, or (on the new site) an openICPSR project; the
        # number is the same, and DataCite has the project's E-DOI for openICPSR numbers (6
        # digits, from 100000) and the ICPSR-DOI for the curated studies
        num = re.sub(r"^.*/studies/([0-9]+).*$", r"\1", ln, flags=re.DOTALL)
        ver = (
            re.sub(r"(?i)^.*/versions/(V[0-9]+).*$", r"\1", ln, flags=re.DOTALL).upper()
            if re.search(r"(?i)/versions/V[0-9]+", ln)
            else ""
        )
        # (ICPSR's study numbers have five digits in their DOIs: ICPSR04652)
        out = f"10.3886/E{num}{ver}" if len(num) >= 6 else f"10.3886/ICPSR{int(num):05d}"
    if out is None:
        return None
    out = re.sub(r"(?i)^10\.3886/e", "10.3886/E", out)
    return re.sub(r"(?i)^(10\.3886/E[0-9]+)v", r"\1V", out)


def _icpsr_doi_of(link: Any) -> Any:
    """Port of ``.icpsr_doi_of()``: the DOI a link names ("10.3886/E195681V1", "10.3886/ICPSR36138.v1").

    ``None`` where it names none. A string gives a string, a sequence a list.
    """
    if isinstance(link, str) or not hasattr(link, "__iter__"):
        return _icpsr_doi_one(link)
    return [_icpsr_doi_one(x) for x in link]


def icpsr_links(paper: Any) -> pd.DataFrame:
    """Port of ``R/archive-icpsr.R::icpsr_links()``: ICPSR and openICPSR deposits in a paper.

    Finds replication packages in openICPSR, including the AEA Data and Code Repository of the
    American Economic Association's journals, and ICPSR's curated studies: their DOIs
    (``10.3886/E195681V1``, ``10.3886/ICPSR36138.v1``), openICPSR project pages and ICPSR study
    pages.

    The table has a row for each deposit: ``href`` (its DOI as a link), ``doi``, ``repo_type``
    ("icpsr"), ``link`` (as the paper writes it), and ``text_id`` / ``paper_id``.
    """
    from metacheck.archives import _paper_urls
    from metacheck.text.search import text_search

    none = pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "href": pd.Series([], dtype="string"),
            "doi": pd.Series([], dtype="string"),
            "repo_type": pd.Series([], dtype="string"),
            "link": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
        }
    )
    rx = re.compile(_ICPSR_RX)
    found_href = _paper_urls(paper)
    if "href" in found_href.columns:
        keep = [isinstance(h, str) and rx.search(h) is not None for h in found_href["href"]]
        found_href = found_href[pd.Series(keep, index=found_href.index, dtype=bool)]
    else:
        found_href = found_href.iloc[0:0]
    hits = text_search(paper, _ICPSR_RX, return_="match", perl=True)
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
    rows = []
    for href, pid, tid in zip(
        links["href"].tolist(), links["paper_id"].tolist(), links["text_id"].tolist(), strict=True
    ):
        doi = _icpsr_doi_one(href)
        if doi is None:
            continue
        rows.append(
            {
                "href": href,
                "paper_id": None if is_missing(pid) else pid,
                "text_id": None if is_missing(tid) else tid,
                "doi": doi,
            }
        )
    # the text's own form first
    rows.sort(key=lambda r: r["text_id"] is None)

    # one row per deposit: a versioned and an unversioned DOI of one project are one deposit (the
    # versioned one is kept)
    def key(r: dict[str, Any]) -> str:
        return re.sub(r"(?i)(V[0-9]+|\.v[0-9]+)$", "", r["doi"]).lower()

    def unversioned(r: dict[str, Any]) -> bool:
        return re.search(r"(?i)(V[0-9]+|\.v[0-9]+)$", r["doi"]) is None

    rows.sort(key=lambda r: (key(r), unversioned(r)))
    seen: set[tuple[Any, str]] = set()
    kept = []
    for r in rows:
        k = (r["paper_id"], key(r))
        if k not in seen:
            seen.add(k)
            kept.append(r)
    if not kept:
        return none
    return pd.DataFrame(
        {
            "paper_id": pd.Series([r["paper_id"] for r in kept], dtype="string"),
            "href": pd.Series([f"https://doi.org/{r['doi']}" for r in kept], dtype="string"),
            "doi": pd.Series([r["doi"] for r in kept], dtype="string"),
            "repo_type": pd.Series(["icpsr"] * len(kept), dtype="string"),
            "link": pd.Series([r["href"] for r in kept], dtype="string"),
            "text_id": pd.Series([r["text_id"] for r in kept], dtype="Int64"),
        }
    )


def icpsr_info(
    icpsr_url: Any,
    id_col: int | str = 1,
    pb: Any = None,  # noqa: ARG001 - R signature (progress bar)
    cache: bool = False,
) -> pd.DataFrame:
    """Port of ``R/archive-icpsr.R::icpsr_info()``: the records of ICPSR and openICPSR deposits.

    Reads each deposit's record from DataCite: title, creators, publication date, description,
    resource type, and the works it relates to (for an AEA replication package, the article it
    belongs to). The files are not read: ICPSR's site does not answer automatic requests.

    *icpsr_url* is the deposits' addresses, as :func:`icpsr_links` gives them in ``href``, or a
    table with them in column *id_col*. Returns a row per address: ``icpsr_url``, ``doi``,
    ``title``, ``creators`` (names separated by "; "), ``publication_date``, ``description``,
    ``resource_type``, ``related`` (``<relation>:<DOI>`` pairs), and ``error`` ("unfound" when
    DataCite has no such DOI).
    """
    from metacheck import http
    from metacheck.archives.dataverse import _chr_values
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.archives.psycharchives import _column
    from metacheck.db._utils import url_encode
    from metacheck.utils import _meta_related

    raw: Any = (
        icpsr_url[_column(icpsr_url, id_col)].tolist()
        if isinstance(icpsr_url, pd.DataFrame)
        else icpsr_url
    )
    values, _ = _chr_values(raw)
    urls = [u for u in dict.fromkeys(v for v in values if v is not None) if u != ""]
    cols = [
        "icpsr_url",
        "doi",
        "title",
        "creators",
        "publication_date",
        "description",
        "resource_type",
        "related",
        "error",
    ]
    if not urls:
        return pd.DataFrame({c: pd.Series([], dtype="string") for c in cols})

    def one(u: str, resp: Any) -> pd.DataFrame:
        obj: dict[str, Any] = dict.fromkeys(cols)
        obj["icpsr_url"] = u
        obj["doi"] = _icpsr_doi_one(u)
        st = resp.status_code if resp is not None else None
        a = None
        if st == 200:
            try:
                a = http.resp_json(resp, check_type=False)["data"]["attributes"]
            except Exception:
                a = None
        if a is None:
            obj["error"] = "unfound" if st == 404 else "no_answer"
            return pd.DataFrame({k: pd.Series([v], dtype="string") for k, v in obj.items()})
        people = []
        for p in a.get("creators") or []:
            name = p.get("name")
            people.append(
                str(name)
                if name is not None
                else f"{p.get('givenName') or ''} {p.get('familyName') or ''}"
            )
        # DataCite writes "Family, Given"; the other readers give "Given Family"
        people = [
            f"{re.sub(r'^[^,]*,', '', p).strip()} {re.sub(r',.*$', '', p)}" if "," in p else p
            for p in people
        ]
        titles = [t.get("title") for t in a.get("titles") or [] if t.get("title") is not None]
        obj["title"] = str(titles[0]) if titles else None
        obj["creators"] = "; ".join(p.strip() for p in people) if people else None
        issued = [d.get("date") for d in a.get("dates") or [] if d.get("dateType") == "Issued"]
        first = issued[0] if issued else a.get("publicationYear")
        obj["publication_date"] = None if first is None else str(first)[:10]
        descs = [
            str(d.get("description"))
            for d in a.get("descriptions") or []
            if d.get("description") is not None
        ]
        obj["description"] = " ".join(descs)[:2000]
        types = a.get("types") or {}
        obj["resource_type"] = types.get("resourceTypeGeneral")
        obj["related"] = _meta_related(
            a.get("relatedIdentifiers"), "relationType", "relatedIdentifier"
        )
        return pd.DataFrame({k: pd.Series([v], dtype="string") for k, v in obj.items()})

    rows: list[pd.DataFrame | None] = [None] * len(urls)
    todo: list[int] = []
    for i, u in enumerate(urls):
        cached = _repo_info_cache_get("icpsr", u) if cache is True else None
        if cached is None:
            todo.append(i)
        else:
            rows[i] = cached
    if todo:
        resps = http.batch_query(
            [
                "https://api.datacite.org/dois/" + url_encode(_icpsr_doi_one(urls[i]) or "NA")
                for i in todo
            ],
            msg=None,
        )
        for k, i in enumerate(todo):
            rows[i] = one(urls[i], resps[k])
            row = rows[i]
            if cache is True and row is not None and _repo_info_ok(row):
                _repo_info_cache_put("icpsr", urls[i], row)
    return bind_rows(rows)
