"""Python halves of ``tests/mod_ref_accuracy/edits.R``.

The demo paper with edited reference tables, for the parity cases in
``parity/cases/mod_ref_accuracy.yaml`` and the pytest ports.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import pandas as pd

import pytacheck as pc
from pytacheck.papers import Paper


def ra_set(p: Paper, table: str, bib_id: int, col: str, value: Any) -> Paper:
    """Set ``p[table][col]`` in the row(s) with *bib_id* (``ra_set()``)."""
    df = p[table].copy()
    mask = (df["bib_id"] == bib_id).fillna(False).to_numpy(dtype=bool)
    if df[col].dtype == object:
        cells = df[col].tolist()
        for i in np.flatnonzero(mask):
            cells[i] = value
        arr = np.empty(len(cells), dtype=object)
        for i, cell in enumerate(cells):
            arr[i] = cell
        df[col] = pd.Series(arr, index=df.index, dtype=object)
    else:
        df.loc[mask, col] = pd.NA if value is None else value
    p[table] = df
    return p


def ra_demo(
    edits: Iterable[Sequence[Any]] = (),
    drop: Iterable[str] = (),
    empty: Iterable[str] = (),
    paper_id: str | None = None,
    dup_match: Iterable[int] = (),
) -> Paper:
    """The demo paper with edits ``(table, bib_id, col, value)`` (``ra_demo()``)."""
    p = pc.demopaper()
    for table, bib_id, col, value in edits:
        p = ra_set(p, table, bib_id, col, value)
    for bib_id in dup_match:
        bm = p["bib_match"]
        rows = bm.loc[(bm["bib_id"] == bib_id).fillna(False).to_numpy(dtype=bool)]
        p["bib_match"] = pd.concat([bm, rows], ignore_index=True)
    for t in empty:
        p[t] = p[t].iloc[0:0].copy()
    for t in drop:
        del p[t]
    if paper_id is not None:
        p.paper_id = paper_id
    return p


def ra_xrefs(
    xref_id: Sequence[int | None],
    contents: Sequence[str],
    text_id: int | Sequence[int] = 4,
    xref_type: str = "bibr",
    paper: Paper | None = None,
) -> Paper:
    """The demo paper with extra cross-references (``ra_xrefs()``)."""
    p = pc.demopaper() if paper is None else paper
    n = len(xref_id)
    tid = [text_id] * n if isinstance(text_id, int) else list(text_id)
    extra = pd.DataFrame(
        {
            "xref_id": pd.Series(list(xref_id), dtype="Int64"),
            "xref_type": pd.Series([xref_type] * n, dtype="string"),
            "contents": pd.Series(list(contents), dtype="string"),
            "text_id": pd.Series(tid, dtype="Int64"),
        }
    )
    xref = p["xref"]
    p["xref"] = pd.concat([xref, extra.astype(xref.dtypes.to_dict())], ignore_index=True)
    return p


def ra_report_tables(o: Any) -> list[pd.DataFrame]:
    """The data frames a module's report tables display (``ra_report_tables()``)."""
    from pytacheck.report.blocks import ReportTable

    blocks = o.report if isinstance(o.report, list) else [o.report]
    return [b.data for b in blocks if isinstance(b, ReportTable)]


def ra_test_paper(text: str | Sequence[str], paper_id: str = "test_paper") -> Paper:
    """``test_paper()`` with a fixed paper_id (``ra_test_paper()``)."""
    p = pc.test_paper(text)
    p.paper_id = paper_id
    return p


# ---- helpers for parity/cases/mod_ref_accuracy_review.yaml ----


def ra_setcol(p: Paper, table: str, col: str, values: Any, dtype: Any = None) -> Paper:
    """Replace a whole column of ``p[table]``; ``None`` drops it (``ra_setcol()``)."""
    df = p[table].copy()
    if values is None:
        df = df.drop(columns=col)
    elif dtype == "object":
        arr = np.empty(len(values), dtype=object)
        for i, v in enumerate(values):
            arr[i] = v
        df[col] = pd.Series(arr, index=df.index, dtype=object)
    else:
        df[col] = pd.Series(list(values), index=df.index, dtype=dtype)
    p[table] = df
    return p


def ra_text(p: Paper, text_id: int, value: str | None) -> Paper:
    """Set the text of the text-table row(s) with *text_id* (``ra_text()``)."""
    df = p["text"].copy()
    mask = (df["text_id"] == text_id).fillna(False).to_numpy(dtype=bool)
    df.loc[mask, "text"] = pd.NA if value is None else value
    p["text"] = df
    return p


def ra_match_copy(p: Paper, from_: int, to: int | None) -> Paper:
    """Append a copy of the bib_match row for *from_* with bib_id *to* (``ra_match_copy()``)."""
    bm = p["bib_match"]
    row = bm.loc[(bm["bib_id"] == from_).fillna(False).to_numpy(dtype=bool)].copy()
    row["bib_id"] = pd.Series([pd.NA if to is None else to] * len(row), index=row.index).astype(
        bm["bib_id"].dtype
    )
    p["bib_match"] = pd.concat([bm, row], ignore_index=True)
    return p


def ra_keep(p: Paper, table: str, bib_id: Sequence[int]) -> Paper:
    """Keep only the rows of ``p[table]`` with these bib_ids (``ra_keep()``)."""
    df = p[table]
    p[table] = df.loc[df["bib_id"].isin(list(bib_id)).fillna(False).to_numpy(dtype=bool)].copy()
    return p
