"""The statements bibr extracts from a paper's structure (D84).

bibr fills ``info.coi_statement``, ``info.funding_statement`` and
``info.ethics_statement`` from the document itself (a JATS ``<fn-group>`` or
``<notes>``, a section title), also when the statement is not in the main text
the regular expressions of the COI, funding and ethics checks search. Those
checks run their expressions first and fall back to bibr's statement for a
paper they found nothing in: the first step of the cascade is the cheap,
validated one, and bibr's statement fills its gaps.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck.papers.model import Paper, PaperList

__all__ = ["bibr_statements", "with_bibr_statements"]


def _papers(paper: Any) -> list[Paper]:
    if isinstance(paper, Paper):
        return [paper]
    if isinstance(paper, PaperList):
        return list(paper)
    if isinstance(paper, list | tuple) and all(isinstance(p, Paper) for p in paper):
        return list(paper)
    return []  # a table or strings: no info to read


def bibr_statements(paper: Any, field: str) -> dict[str, str]:
    """Each paper's statement *field* of its ``info`` table (whitespace collapsed), by
    paper ID; papers without one are left out."""
    out: dict[str, str] = {}
    for p in _papers(paper):
        info = p.get("info")
        if not isinstance(info, pd.DataFrame) or field not in info.columns or not len(info):
            continue
        value = info[field].iloc[0]
        if isinstance(value, str) and value.strip():
            out.setdefault(str(p.paper_id), " ".join(value.split()))
    return out


def with_bibr_statements(table: pd.DataFrame, paper: Any, field: str) -> pd.DataFrame:
    """*table* (one row per paper with a statement: ``paper_id``, ``text``) with a row
    for each paper it lacks whose bibr statement *field* exists, in paper order."""
    from metacheck._r.frames import bind_rows

    found = bibr_statements(paper, field)
    have = set(table["paper_id"].dropna().astype(str)) if "paper_id" in table.columns else set()
    extra = {pid: text for pid, text in found.items() if pid not in have}
    if not extra:
        return table
    rows = pd.DataFrame(
        {
            "paper_id": pd.Series(list(extra), dtype="string"),
            "text": pd.Series(list(extra.values()), dtype="string"),
        }
    )
    out = bind_rows([table, rows]).reset_index(drop=True)
    order = {str(p.paper_id): i for i, p in enumerate(_papers(paper))}
    keys = out["paper_id"].map(lambda pid: order.get(str(pid), len(order)))
    return out.iloc[keys.argsort(kind="stable").to_numpy()].reset_index(drop=True)
