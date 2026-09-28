"""``text_expand()``: expand search results to their full sentence/paragraph/section.

Port of ``R/text_expand.R``.
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd

from pytacheck.papers.model import Paper
from pytacheck.text.search import text_search

__all__ = ["expand_text", "text_expand"]

ExpandTo = Literal["sentence", "paragraph", "div", "section"]
_BY = ["paper_id", "section_id", "paragraph_id", "text_id", "text"]


def _key(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(None if pd.isna(v) else v for v in row)


def _collapse(df: pd.DataFrame, by: list[str], col: str, sep: str) -> pd.DataFrame:
    """``summarise(expanded = paste(col, collapse = sep), .by = by)``."""
    groups: dict[tuple[Any, ...], list[str]] = {}
    first: dict[tuple[Any, ...], int] = {}
    values = ["NA" if pd.isna(v) else str(v) for v in df[col].tolist()]
    for i, row in enumerate(df.loc[:, by].itertuples(index=False, name=None)):
        k = _key(row)
        if k not in groups:
            groups[k] = []
            first[k] = i
        groups[k].append(values[i])
    out = df.loc[:, by].iloc[[first[k] for k in groups]].reset_index(drop=True)
    out["expanded"] = pd.Series([sep.join(v) for v in groups.values()], dtype="string")
    return out


def _align_keys(left: pd.DataFrame, right: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Cast *right*'s join keys to *left*'s dtypes (R joins int/double freely)."""
    right = right.copy()
    for k in keys:
        if k in left.columns and k in right.columns and left[k].dtype != right[k].dtype:
            try:
                right[k] = right[k].astype(left[k].dtype)
            except (TypeError, ValueError):
                right[k] = right[k].astype(object)
    return right


def text_expand(
    results_table: Any,
    paper: Any,
    expand_to: ExpandTo = "sentence",
    plus: int = 0,
    minus: int = 0,
) -> pd.DataFrame:
    """Add an ``expanded`` column with the full text around each result row.

    *results_table* may be a DataFrame (e.g. from :func:`text_search`), a
    module output (its ``table`` is used) or a paper (its ``text`` table).
    ``plus``/``minus`` add that many following/preceding sentences of the
    same paragraph and only apply when ``expand_to == "sentence"``.
    """
    from pytacheck.module import ModuleOutput

    if not isinstance(results_table, pd.DataFrame):
        if isinstance(results_table, ModuleOutput):
            results_table = results_table.table
        elif isinstance(results_table, Paper):
            results_table = results_table.text
        else:
            raise TypeError("The results table was not a table or object containing a table")
    if expand_to not in ("sentence", "paragraph", "div", "section"):
        raise ValueError("'arg' should be one of 'sentence', 'paragraph', 'div', 'section'")

    ft = text_search(paper).loc[:, _BY]
    by = list(_BY)
    if expand_to == "sentence":
        by = by[:4]
        text = _collapse(ft, by, "text", " ")
    else:
        by = by[:3]
        text_p = _collapse(ft, by, "text", " ")
        if expand_to == "section":
            by = by[:2]
        text = _collapse(text_p, by, "expanded", "\n\n")

    if minus > 0 or plus > 0:
        if expand_to != "sentence":
            print("Plus and minus only work when expand_to == 'sentence'")
        else:
            coord_cols = ["paper_id", "section_id", "paragraph_id", "text_id"]
            blocks = []
            for offset in range(-minus, plus + 1):
                coords = results_table.loc[:, coord_cols].copy()
                coords["exp_text_id"] = coords["text_id"] + offset
                blocks.append(coords)
            coords = pd.concat(blocks, ignore_index=True).drop_duplicates().reset_index(drop=True)
            lookup = text.rename(columns={"text_id": "exp_text_id"})
            lookup = _align_keys(
                coords, lookup, ["paper_id", "section_id", "paragraph_id", "exp_text_id"]
            )
            joined = coords.merge(
                lookup,
                on=["paper_id", "section_id", "paragraph_id", "exp_text_id"],
                how="left",
                sort=False,
            )
            joined = joined.loc[joined["expanded"].notna()].reset_index(drop=True)
            text = _collapse(joined, coord_cols, "expanded", " ")

    join_by = [c for c in by if c in results_table.columns]
    text = _align_keys(results_table, text, join_by)
    expanded: pd.DataFrame = results_table.merge(
        text, on=join_by, how="left", suffixes=("", ".full"), sort=False
    )
    if "text" in expanded.columns:
        missing = expanded["expanded"].isna()
        expanded["expanded"] = expanded["expanded"].astype(object).where(~missing, expanded["text"])
        expanded["expanded"] = expanded["expanded"].astype("string")
    return expanded


expand_text = text_expand
