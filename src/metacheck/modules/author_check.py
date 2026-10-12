"""Author Information: ORCIDs, affiliations, a corresponding author and CRediT roles.

A pytacheck module (metacheck has no counterpart). It reads bibr's ``author``,
``affiliation`` and ``affiliation_match`` tables.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from metacheck.module import module
from metacheck.papers.model import Paper

_ORCID = re.compile(r"(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dX])", re.IGNORECASE)


def orcid_valid(orcid: str) -> bool:
    """Whether *orcid* (bare or as a link) has a valid ISO 7064 MOD 11-2 check digit."""
    m = _ORCID.search(orcid)
    if not m:
        return False
    digits = "".join(m.groups()).upper()
    total = 0
    for d in digits[:-1]:
        total = (total + int(d)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def _missing(v: Any) -> bool:
    return v is None or (not isinstance(v, list | tuple) and pd.isna(v)) or v == ""


def _name(r: Mapping[Any, Any]) -> str:
    if not _missing(r.get("literal")):
        return str(r["literal"])
    parts = [str(r[k]) for k in ("given", "family") if not _missing(r.get(k))]
    return " ".join(parts) or f"author {r.get('author_id')}"


def _ids(v: Any) -> list[Any]:
    """The author ids of an affiliation row: a list, or one id."""
    if isinstance(v, list | tuple):
        return list(v)
    return [] if _missing(v) else [v]


def _paper_rows(paper: Paper) -> pd.DataFrame:
    authors = paper.get("author")
    cols = [
        "author_id",
        "name",
        "person",
        "orcid",
        "orcid_valid",
        "corresponding",
        "email",
        "affiliations",
        "ror_matched",
        "credit_roles",
    ]
    if not isinstance(authors, pd.DataFrame) or not len(authors):
        return pd.DataFrame(columns=cols)
    affs = paper.get("affiliation")
    matches = paper.get("affiliation_match")
    matched: set[Any] = set()
    if isinstance(matches, pd.DataFrame) and "affiliation_id" in matches.columns:
        ror = matches.get("service")
        rows = matches if ror is None else matches.loc[ror.astype("string") == "ror"]
        matched = set(rows["affiliation_id"].tolist())
    of_author: dict[Any, list[Any]] = {}
    if isinstance(affs, pd.DataFrame) and "author_ids" in affs.columns:
        for aff_id, ids in zip(affs["affiliation_id"], affs["author_ids"], strict=True):
            for a in _ids(ids):
                of_author.setdefault(a, []).append(aff_id)
    # affiliations that name no author (an older export) cannot be attributed
    linked = bool(of_author) or not (isinstance(affs, pd.DataFrame) and len(affs))
    out = []
    for r in authors.to_dict("records"):
        orcid = r.get("orcid")
        has_orcid = not _missing(orcid)
        roles = r.get("credit_roles")
        aff_ids = of_author.get(r.get("author_id"), [])
        out.append(
            {
                "author_id": r.get("author_id"),
                "name": _name(r),
                # a group or organisation ("The Europe PMC Consortium") has no ORCID
                "person": _missing(r.get("literal")),
                "orcid": orcid if has_orcid else None,
                "orcid_valid": orcid_valid(str(orcid)) if has_orcid else None,
                "corresponding": bool(r.get("corresponding"))
                if not _missing(r.get("corresponding"))
                else False,
                "email": None if _missing(r.get("email")) else r.get("email"),
                "affiliations": len(aff_ids) if linked else None,
                "ror_matched": sum(a in matched for a in aff_ids) if linked else None,
                "credit_roles": len(roles) if isinstance(roles, list | tuple) else 0,
            }
        )
    return pd.DataFrame(out, columns=cols)


def _problems(table: pd.DataFrame) -> list[str]:
    """What the paper's author information lacks, one sentence each."""
    out: list[str] = []
    n = len(table)
    people = table["person"].fillna(True).astype(bool)
    no_orcid = table.loc[people & table["orcid"].isna(), "name"].tolist()
    bad = table.loc[table["orcid_valid"].eq(False).fillna(False), "name"].tolist()
    no_aff = table.loc[(table["affiliations"] == 0).fillna(False), "name"].tolist()
    if bad:
        out.append(f"The ORCID of {', '.join(bad)} has an invalid check digit.")
    if no_orcid:
        out.append(
            f"{len(no_orcid)} of {int(people.sum())} authors have no ORCID: {', '.join(no_orcid)}."
        )
    if no_aff:
        out.append(f"{len(no_aff)} of {n} authors have no affiliation: {', '.join(no_aff)}.")
    if not table["corresponding"].any():
        out.append("No corresponding author is marked.")
    return out


@module(
    title="Author Information",
    description="Check that every author has an ORCID and an affiliation, and that a "
    "corresponding author is named.",
    details="""
        Lists the authors bibr extracted with their ORCID (and whether its check
        digit is valid), whether they are the corresponding author, their e-mail
        address, their number of affiliations (and how many bibr matched to a ROR
        record) and their number of CRediT roles. The check is yellow when an author
        has no ORCID or no affiliation, an ORCID has an invalid check digit, or no
        corresponding author is marked.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={"paper": "a paper object or paperlist object"},
)
def author_check(paper: Any) -> dict[str, Any]:
    """Check the author information of *paper* (a paper or paper list).

    ``table`` has one row per author: ``author_id``, ``name``, ``person``
    (``False`` for a group or organisation, which needs no ORCID), ``orcid``,
    ``orcid_valid``, ``corresponding``, ``email``, ``affiliations``,
    ``ror_matched`` (missing when the affiliations name no authors) and
    ``credit_roles`` (the number of roles). The summary
    table counts the authors, those with an ORCID and those with an
    affiliation per paper. The traffic light is ``"na"`` without authors,
    ``"green"`` when nothing is missing, ``"yellow"`` otherwise, and
    ``"info"`` for several papers.
    """
    from metacheck.papers.model import PaperList

    papers = list(paper) if isinstance(paper, PaperList) else [paper]
    parts = []
    for p in papers:
        rows = _paper_rows(p)
        parts.append(rows.assign(paper_id=pd.Series([p.paper_id] * len(rows), dtype="object")))
    table = pd.concat(parts, ignore_index=True)
    table = table.astype(
        {
            "author_id": "Int64",
            "name": "string",
            "person": "boolean",
            "orcid": "string",
            "orcid_valid": "boolean",
            "corresponding": "boolean",
            "email": "string",
            "affiliations": "Int64",
            "ror_matched": "Int64",
            "credit_roles": "Int64",
            "paper_id": "string",
        }
    )

    ids = [p.paper_id for p in papers]
    groups = {pid: table.loc[table["paper_id"] == pid] for pid in ids}
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(ids, dtype="string"),
            "authors": pd.Series([len(groups[i]) for i in ids], dtype="Int64"),
            "with_orcid": pd.Series(
                [int(groups[i]["orcid"].notna().sum()) for i in ids], dtype="Int64"
            ),
            "with_affiliation": pd.Series(
                [int((groups[i]["affiliations"] > 0).sum()) for i in ids], dtype="Int64"
            ),
        }
    )

    report: Any = ""
    if len(papers) > 1:
        tl = "info" if len(table) else "na"
        flagged = sum(bool(len(groups[i])) and bool(_problems(groups[i])) for i in ids)
        summary_text = f"{flagged} of {len(ids)} papers lack some author information."
    elif not len(table):
        tl, summary_text = "na", "No authors were found."
    else:
        problems = _problems(table)
        if problems:
            tl = "yellow"
            summary_text = " ".join(problems)
            report = summary_text + (
                " ORCIDs and affiliations (ideally with a ROR identifier) let readers and "
                "indexing services tell authors apart and credit their work."
            )
        else:
            tl = "green"
            summary_text = (
                f"All {len(table)} authors have an ORCID and an affiliation, and a "
                "corresponding author is marked."
            )
            report = summary_text

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {"authors": 0, "with_orcid": 0, "with_affiliation": 0},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
