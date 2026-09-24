"""Read current bibr output (schema v11/v12) as metacheck's paper schema (v10.x).

metacheck's reader (``.read_bibr()``) and every module expect the v10.x layout
(``info``, ``info_match``, ``xref_id`` = the referenced bib row, match scores
0-100, ...). bibr >= 0.5 writes schema v12 (and 0.4 wrote v11): ``metadata``
+ ``source`` + ``extraction`` instead of ``info``, a separate affiliation
table, ``target_id`` for the referenced row, 0-1 scores and snake_case enum
values. Read as-is, metacheck finds no title, DOI or keywords in such a file.

:func:`to_metacheck_schema` converts a v11/v12 payload back to the v10.x
layout so that every module sees what it would see for an older bibr file.
It is applied automatically by :func:`pytacheck.papers.from_bibr` (and so by
:func:`pytacheck.read` and the in-process bibr integration). Payloads without
a root ``schema_version`` (v10.x and earlier, and every metacheck fixture)
pass through untouched, so parity with metacheck is unaffected for them.

The mapping follows the migration notes in ``bibr/export/models.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["is_modern_bibr", "schema_major", "to_metacheck_schema"]

_PAPER_TYPE = {"meta_analysis": "meta-analysis", "case_study": "case-study"}
_SECTION_TYPE = {"data_availability": "open_data"}
_INFO_FIELDS = (
    "title",
    "abstract",
    "keywords",
    "doi",
    "paper_type",
    "oecd_l1",
    "oecd_l2",
    "journal",
    "volume",
    "issue",
    "first_page",
    "last_page",
    "issn",
    "publisher",
    "published",
    "license",
    "funding_statement",
    "coi_statement",
    "ethics_statement",
    "data_availability",
)


def schema_major(data: Mapping[str, Any]) -> int | None:
    """Major version from a root ``schema_version`` (v11+), else ``None``."""
    version = data.get("schema_version")
    if not isinstance(version, str | int | float):
        return None
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return None


def is_modern_bibr(data: Mapping[str, Any]) -> bool:
    """Whether *data* uses the v11+ layout (root ``schema_version``, no ``info``)."""
    major = schema_major(data)
    return major is not None and major >= 11 and "info" not in data


def _match_rows(rows: Any, with_bib_id: bool) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        r = dict(row)
        if "author" in r and "authors" not in r:
            r["authors"] = [
                {"given": a.get("given"), "family": a.get("family") or a.get("literal")}
                for a in (r.pop("author") or [])
            ]
        if "editor" in r and "editors" not in r:
            r["editors"] = [
                {"given": a.get("given"), "family": a.get("family") or a.get("literal")}
                for a in (r.pop("editor") or [])
            ]
        if "published_date" in r and "date" not in r:
            r["date"] = r.pop("published_date")
        if isinstance(r.get("score"), int | float):
            r["score"] = r["score"] * 100  # v12 scores are 0-1, v10 0-100
        if not with_bib_id:
            r.pop("bib_id", None)
        out.append(r)
    return out


def to_metacheck_schema(data: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a bibr v11/v12 payload to metacheck's v10.x paper layout.

    Returns *data* unchanged (as a dict) when it is already v10.x.
    """
    if not is_modern_bibr(data):
        return dict(data)
    source = data.get("source") or {}
    meta = data.get("metadata") or {}
    extraction = data.get("extraction") or {}
    diagnostics = extraction.get("diagnostics") or {}
    producer = extraction.get("producer") or {}

    info: dict[str, Any] = {k: meta.get(k) for k in _INFO_FIELDS if k in meta}
    info["keywords"] = meta.get("keywords") or []
    sha = source.get("sha256") or source.get("file_hash") or ""
    info["file_hash"] = sha[:16] if sha else None
    info["input_format"] = source.get("input_format")
    info["file_name"] = source.get("file_name")
    info["schema_version"] = data.get("schema_version")
    info["bibr_version"] = producer.get("version") or extraction.get("bibr_version")
    if info.get("paper_type") in _PAPER_TYPE:
        info["paper_type"] = _PAPER_TYPE[info["paper_type"]]
    classification = diagnostics.get("paper_classification") or {}
    info["paper_type_confidence"] = classification.get("paper_type_confidence")
    info["oecd_confidence"] = classification.get("oecd_confidence")

    affiliations = [dict(a) for a in data.get("affiliation") or []]
    by_author: dict[int, list[str]] = {}
    for a in affiliations:
        for aid in a.get("author_ids") or []:
            by_author.setdefault(aid, []).append(a.get("text") or a.get("institution") or "")
    authors = []
    for a in data.get("author") or []:
        r = dict(a)
        if not r.get("family") and r.get("literal"):
            r["family"] = r["literal"]
        r.setdefault("affiliation", "; ".join(by_author.get(r.get("author_id"), [])) or None)
        authors.append(r)

    section_scores = {
        s.get("section_id"): s for s in diagnostics.get("section_classification") or []
    }
    sections = []
    for s in data.get("section") or []:
        r = dict(s)
        if r.get("section_type") in _SECTION_TYPE:
            r["section_type"] = _SECTION_TYPE[r["section_type"]]
        score = section_scores.get(r.get("section_id"), {})
        r.setdefault("classification_score", score.get("score"))
        r.setdefault("classification_source", score.get("source"))
        if r.get("header") is None:
            r["header"] = ""
        sections.append(r)

    xrefs = []
    for x in data.get("xref") or []:
        r = dict(x)
        if "target_id" in r:
            r.pop("xref_id", None)
            r["xref_id"] = r.pop("target_id")  # v10: xref_id is the referenced row
        xrefs.append(r)

    eqs = [
        {**e, "df": e.get("df") if e.get("df") is not None else ""} for e in data.get("eq") or []
    ]

    out: dict[str, Any] = {"paper_id": data.get("paper_id"), "info": info}
    out["author"] = authors
    for key in ("text", "url", "bib", "figure", "table"):
        if key in data:
            out[key] = [dict(r) for r in data.get(key) or []]
    out["section"] = sections
    out["xref"] = xrefs
    out["eq"] = eqs
    if data.get("bib_match"):
        out["bib_match"] = _match_rows(data["bib_match"], with_bib_id=True)
    if data.get("metadata_match"):
        out["info_match"] = _match_rows(data["metadata_match"], with_bib_id=False)
    if affiliations:
        out["affiliations"] = affiliations
    if data.get("funding"):
        out["funding"] = [dict(f) for f in data["funding"]]
    for key in (
        "footnote",
        "affiliation_match",
        "funding_match",
        "extraction",
        "source",
        "schema_version",
    ):
        if key in data:
            out[f"bibr_{key}" if key != "schema_version" else "bibr_schema_version"] = data[key]
    return out
