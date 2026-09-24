"""Cross-paper tables: ``paper_table()``, ``ref_table()``, ``paper_id()``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from pytacheck._r.frames import bind_rows
from pytacheck.papers.model import Paper, PaperList, is_paper_list
from pytacheck.papers.schema import empty_table, records_to_frame, table_names

__all__ = ["as_paper_list", "paper_id", "paper_table", "ref_table"]


def as_paper_list(paper: Any) -> PaperList:
    """Wrap a single paper in a :class:`PaperList`; validate the argument."""
    if isinstance(paper, PaperList):
        return paper
    if isinstance(paper, Paper):
        return PaperList([paper])
    if is_paper_list(paper):
        values = paper.values() if isinstance(paper, dict) else paper
        return PaperList(values)
    raise TypeError("paper must be a paper or paperlist object.")


def _columns_of(p: Paper, table: str) -> list[str] | None:
    """Columns *table* would have for *p*, or ``None`` when it is not a table."""
    raw = p._raw_records(table)
    if raw is not None:
        return list(raw[1])
    value = p.get(table)
    if value is None and table in p:
        return [c for c, _ in _schema_cols(table)]
    if isinstance(value, pd.DataFrame):
        return list(value.columns)
    return None


def _schema_cols(table: str) -> list[tuple[str, str]]:
    from pytacheck.papers.schema import table_columns

    return list(table_columns(table))


def paper_table(paper: Any, table: str, cols: Sequence[str] | None = None) -> pd.DataFrame:
    """Concatenate *table* across papers, adding a ``paper_id`` column.

    Equivalent to ``paper_table(paper, table, cols)``: ``paper_id`` is added
    as the last column of each paper's table (or overwritten in place when
    the table already has one), tables are row-bound with ``bind_rows``
    semantics, and ``cols`` (plus ``paper_id``) selects columns in that order.
    """
    papers = as_paper_list(paper)

    fast = table in table_names() and all(
        p._raw_records(table) is not None or table not in p for p in papers
    )
    if fast and any(p._raw_records(table) is not None for p in papers):
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


def _fast_concat(papers: PaperList, table: str) -> pd.DataFrame:
    """Build the concatenated table straight from each paper's JSON records."""
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


def paper_id(paper: Any) -> list[str]:
    """IDs of the papers (``paper_id()``), taken from their ``info`` tables."""
    info = paper_table(paper, "info", ["paper_id"])
    if "paper_id" not in info.columns:
        return []
    return [str(v) for v in info["paper_id"].tolist()]


def ref_table(paper: Any) -> pd.DataFrame:
    """``ref_table()``: references with their (possibly matched) DOIs and text."""
    cols = ["paper_id", "bib_id", "doi"]
    bib_orig = paper_table(paper, "bib", cols)
    bib_match = paper_table(paper, "bib_match", cols)
    if len(bib_match) == 0:
        bib = bib_orig
    else:
        keys = ["paper_id", "bib_id"]
        matched = bib_match[keys].drop_duplicates()
        anti = bib_orig.merge(matched, on=keys, how="left", indicator=True)
        anti = anti[anti["_merge"] == "left_only"].drop(columns="_merge")
        bib = bind_rows([anti, bib_match])
        bib = bib.sort_values(keys, kind="stable", na_position="last").reset_index(drop=True)

    bib_all = paper_table(paper, "bib")
    text_all = paper_table(paper, "text")
    if "text_id" not in bib_all.columns or "text" not in text_all.columns:
        return bib.iloc[0:0].assign(text=pd.Series([], dtype="string"))
    ref_text = bib_all.merge(
        text_all, on=["paper_id", "text_id"], how="inner", suffixes=(".x", ".y")
    )
    ref_text = ref_text.loc[:, ["paper_id", "bib_id", "text"]]
    return bib.merge(ref_text, on=["paper_id", "bib_id"], how="inner").reset_index(drop=True)
