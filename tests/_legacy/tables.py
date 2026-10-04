"""``paper_table()`` as it was before the indexed-document core: the oracle's copy.

``paper_table()``, ``_one_table()`` and ``_fast_concat()`` copied from
``src/metacheck/papers/tables.py`` at the commit before CORE-1b, so that
``tests/_legacy/search.py`` joins papers exactly as the façade did (the
façade's one-paper ``paper_table()`` no longer builds the paper's table).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from metacheck._r.frames import bind_rows
from metacheck.papers.model import Paper, PaperList
from metacheck.papers.schema import empty_table, records_to_frame, table_names
from metacheck.papers.tables import _schema_cols, as_paper_list


def paper_table(paper: Any, table: str, cols: Sequence[str] | None = None) -> pd.DataFrame:
    """Concatenate *table* across papers, adding a ``paper_id`` column."""
    papers = as_paper_list(paper)

    fast = (
        len(papers) > 1
        and table in table_names()
        and all(p._raw_records(table) is not None or table not in p for p in papers)
    )
    if len(papers) == 1:
        merged = _one_table(papers[0], table)
    elif fast and any(p._raw_records(table) is not None for p in papers):
        merged = _fast_concat(papers, table)
    else:
        frames = []
        for p in papers:
            x = p.get(table)
            if isinstance(x, pd.DataFrame):
                x = x.copy(deep=False)
                x["paper_id"] = pd.Series([p.paper_id] * len(x), index=x.index, dtype="string")
                frames.append(x)
        merged = bind_rows(frames) if frames else pd.DataFrame()

    if cols is not None:
        wanted = [*cols, "paper_id"]
        keep = [c for c in dict.fromkeys(wanted) if c in merged.columns]
        merged = merged.loc[:, keep]
    return merged.reset_index(drop=True)


def _one_table(p: Paper, table: str) -> pd.DataFrame:
    x = p.get(table)
    if not isinstance(x, pd.DataFrame):
        return pd.DataFrame()
    x = x.copy(deep=False)
    x["paper_id"] = pd.Series([p.paper_id] * len(x), index=x.index, dtype="string")
    return x


def _fast_concat(papers: PaperList, table: str) -> pd.DataFrame:
    order: dict[str, None] = {}
    records: list[dict[str, Any]] = []
    empty_schema = False
    for p in papers:
        raw = p._raw_records(table)
        if raw is None:
            if table in p:
                empty_schema = True
                for c, _ in _schema_cols(table):
                    order.setdefault(c, None)
                order.setdefault("paper_id", None)
            continue
        recs, columns = raw
        for c in columns:
            order.setdefault(c, None)
        order.setdefault("paper_id", None)
        pid = p.paper_id
        for r in recs:
            row = dict(r)
            row["paper_id"] = pid
            records.append(row)
    if not records and empty_schema:
        frame = empty_table(table)
        frame["paper_id"] = pd.Series([], dtype="string")
        return frame.loc[:, list(order)]
    frame = records_to_frame(table, records, list(order))
    frame["paper_id"] = frame["paper_id"].astype("string")
    return frame
