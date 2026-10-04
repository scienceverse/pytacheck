"""Cross-paper tables: ``paper_table()``, ``ref_table()``, ``paper_id()``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from metacheck._r.frames import bind_rows
from metacheck.papers.ids import resolve
from metacheck.papers.model import Paper, PaperList, is_paper_list
from metacheck.papers.schema import empty_table, records_to_frame, table_names

__all__ = ["as_paper_list", "empty_paper_table", "paper_id", "paper_table", "ref_table"]


def as_paper_list(paper: Any) -> PaperList:
    """Wrap a single paper in a :class:`PaperList`; validate the argument.

    Papers of a list that share an ID get distinct ones (F6, :func:`~metacheck.papers.ids.resolve`).
    """
    if isinstance(paper, PaperList):
        return resolve(paper, stacklevel=4)
    if isinstance(paper, Paper):
        return PaperList([paper])
    if is_paper_list(paper):
        values = paper.values() if isinstance(paper, dict) else paper
        return resolve(PaperList(values), stacklevel=4)
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
    """The columns of the empty table ``paper()`` creates for *table*."""
    from metacheck.papers.schema import base_table_columns

    return list(base_table_columns(table))


def paper_table(paper: Any, table: str, cols: Sequence[str] | None = None) -> pd.DataFrame:
    """Concatenate *table* across papers, adding a ``paper_id`` column.

    Equivalent to ``paper_table(paper, table, cols)``: ``paper_id`` is added
    as the last column of each paper's table (or overwritten in place when
    the table already has one), tables are row-bound with ``bind_rows``
    semantics, and ``cols`` (plus ``paper_id``) selects columns in that order.
    """
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
    """*table* of one paper plus ``paper_id``: from the typed columns of its JSON
    records while it has them (its table is not built), else its own table as a
    copy-on-write view."""
    from metacheck.core.doc import table_frame  # the core imports papers.model

    fast = table_frame(p, table)
    if fast is not None:
        return fast
    x = p.get(table)
    if not isinstance(x, pd.DataFrame):
        return pd.DataFrame()
    x = x.copy(deep=False)
    x["paper_id"] = pd.Series([p.paper_id] * len(x), index=x.index, dtype="string")
    return x


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
    """IDs of the papers (``paper_id()``), taken from their ``info`` tables.

    ``paper_table(paper, "info", "paper_id")`` gives each paper's ID once per
    row of its ``info`` table, so only the rows are counted (building the
    table costs more than every module's use of it).
    """
    papers = as_paper_list(paper)
    ids: list[str] = []
    for p in papers:
        rows = _info_rows(p)
        if rows is None or not isinstance(p.paper_id, str):
            return _paper_id_table(paper)
        ids += [p.paper_id] * rows
    return ids


def _info_rows(p: Paper) -> int | None:
    """Rows of *p*'s ``info`` table (0 without one), or ``None`` if they cannot be counted."""
    raw = p._raw_records("info")
    if raw is not None:
        return len(raw[0]) if raw[1] else None
    if "info" not in p:
        return 0
    info = p.get("info")
    return len(info) if isinstance(info, pd.DataFrame) and len(info.columns) else None


def _paper_id_table(paper: Any) -> list[str]:
    info = paper_table(paper, "info", ["paper_id"])
    if "paper_id" not in info.columns:
        return []
    return [str(v) for v in info["paper_id"].tolist()]


def empty_paper_table(table: str) -> pd.DataFrame:
    """*table* with its ``paper.json`` columns and ``paper_id``, but no rows.

    What a module can use in place of ``paper_table()`` of an empty paper list,
    which has no columns at all (a paper with an empty table has them all).
    """
    return empty_table(table).assign(paper_id=pd.Series([], dtype="string"))


def _empty_ref_table() -> pd.DataFrame:
    """The columns and types of ``ref_table()`` with no rows."""
    bib = empty_table("bib")
    return pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "bib_id": bib["bib_id"],
            "doi": bib["doi"],
            "text": empty_table("text")["text"],
        }
    )


def ref_table(paper: Any) -> pd.DataFrame:
    """``ref_table()``: references with their (possibly matched) DOIs and text."""
    if len(as_paper_list(paper)) == 0:
        # an empty paper list has no references; metacheck's inner_join() stops
        # because its empty tables have no paper_id or text_id column (U79)
        return _empty_ref_table()
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
    # R: dplyr::inner_join(bib, text, by = c("paper_id", "text_id")) errors when a
    # join column is missing (e.g. a bib without text_id, or no text table)
    for side, df in (("x", bib_all), ("y", text_all)):
        missing = [k for k in ("paper_id", "text_id") if k not in df.columns]
        if missing:
            problem = ", ".join(f"`{k}`" for k in missing)
            raise ValueError(
                f"Join columns in `{side}` must be present in the data.\n✖ Problem with {problem}."
            )
    ref_text = bib_all.merge(
        text_all, on=["paper_id", "text_id"], how="inner", suffixes=(".x", ".y")
    )
    ref_text = ref_text.loc[:, ["paper_id", "bib_id", "text"]]
    return bib.merge(ref_text, on=["paper_id", "bib_id"], how="inner").reset_index(drop=True)
