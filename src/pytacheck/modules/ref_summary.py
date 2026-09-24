"""Summarise References (port of ``inst/modules/ref_summary.R``)."""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r.base import plural
from pytacheck._r.regex import grep, gsub, is_na
from pytacheck.module import module

__all__ = ["ref_summary"]

_KEYS = ["paper_id", "bib_id"]

# report columns: grep("^(pubpeer.*|replication.*|retractionwatch|.*_mismatch)$", ...)
_REPORT_COLS = r"^(pubpeer.*|replication.*|retractionwatch|.*_mismatch)$"


# -- dplyr joins ----------------------------------------------------------------------


class _NA:
    """Join-key stand-in for a missing value (dplyr's ``na_matches = "na"``)."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NA"


_NA_KEY = _NA()


def _key_value(v: Any) -> Any:
    """A hashable join-key value: ``NA`` matches ``NA``; integer and double keys agree."""
    if is_na(v):
        return _NA_KEY
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _key_tuples(df: pd.DataFrame, by: Sequence[str]) -> list[tuple[Any, ...]]:
    cols = [[_key_value(v) for v in df[k].tolist()] for k in by]
    return list(zip(*cols, strict=True)) if cols else [() for _ in range(len(df))]


def _take(s: pd.Series, idx: np.ndarray) -> pd.Series:
    """``s[idx]`` where ``-1`` gives a missing value (keeps R-like nullable dtypes)."""
    arr: Any = s.array
    if (idx < 0).any():
        if s.dtype == bool:
            arr = pd.array(s.to_numpy(), dtype="boolean")
        elif isinstance(s.dtype, np.dtype) and s.dtype.kind in "iu":
            arr = pd.array(s.to_numpy(), dtype="Int64")
        elif s.dtype == object:
            return pd.Series(
                [None if i < 0 else s.iloc[i] for i in idx.tolist()], dtype=object, name=s.name
            )
    out = pd.api.extensions.take(arr, idx, allow_fill=True)
    return pd.Series(out, name=s.name)


def _join(x: pd.DataFrame, y: pd.DataFrame, by: Sequence[str], how: str) -> pd.DataFrame:
    """``dplyr::inner_join()`` / ``dplyr::left_join()`` (``how = "inner" | "left"``).

    Rows follow ``x``; each ``x`` row gets its matches in ``y`` order (a
    ``left`` join keeps unmatched ``x`` rows, with missing ``y`` values).
    ``NA`` keys match each other, integer and double keys compare by value,
    and non-key columns in both tables get dplyr's ``.x`` / ``.y`` suffixes.
    """
    by = list(by)
    for side, df in (("x", x), ("y", y)):
        missing = [k for k in by if k not in df.columns]
        if missing:
            problem = ", ".join(f"`{k}`" for k in missing)
            raise ValueError(
                f"Join columns in `{side}` must be present in the data.\n"
                f"✖ Problem with {problem}."
            )
    y_rows: dict[tuple[Any, ...], list[int]] = {}
    for j, key in enumerate(_key_tuples(y, by)):
        y_rows.setdefault(key, []).append(j)
    xi: list[int] = []
    yi: list[int] = []
    for i, key in enumerate(_key_tuples(x, by)):
        hits = y_rows.get(key)
        if hits:
            xi.extend([i] * len(hits))
            yi.extend(hits)
        elif how == "left":
            xi.append(i)
            yi.append(-1)
    # dplyr warns when an x row matches several y rows *and* a y row several x rows
    x_multi = len(xi) != len(set(xi))
    if x_multi and len([j for j in yi if j >= 0]) != len({j for j in yi if j >= 0}):
        warnings.warn(
            "Detected an unexpected many-to-many relationship between `x` and `y`.",
            stacklevel=3,
        )
    x_idx = np.asarray(xi, dtype=np.intp)
    y_idx = np.asarray(yi, dtype=np.intp)
    y_cols = [c for c in y.columns if c not in by]
    x_names = {c: (f"{c}.x" if c in y_cols else c) for c in x.columns}
    y_names = {c: (f"{c}.y" if c in x.columns else c) for c in y_cols}
    cols: dict[str, pd.Series] = {}
    for c in x.columns:
        cols[x_names[c]] = x[c].iloc[x_idx].reset_index(drop=True)
    for c in y_cols:
        cols[y_names[c]] = _take(y[c], y_idx).reset_index(drop=True)
    return pd.DataFrame(cols)


# -- ref_accuracy -----------------------------------------------------------------------


def _is_false(v: Any) -> bool:
    """``v %in% FALSE`` (``FALSE`` matches ``0`` and ``"FALSE"`` too; ``NA`` does not)."""
    if is_na(v):
        return False
    if isinstance(v, bool | np.bool_):
        return not bool(v)
    if isinstance(v, int | float | np.number):
        return v == 0
    return isinstance(v, str) and v == "FALSE"


def _is_true(v: Any) -> bool:
    """``v %in% TRUE``."""
    if is_na(v):
        return False
    if isinstance(v, bool | np.bool_):
        return bool(v)
    if isinstance(v, int | float | np.number):
        return v == 1
    return isinstance(v, str) and v == "TRUE"


def _accuracy_mismatches(acc: pd.DataFrame) -> pd.DataFrame:
    """The ``accuracy_mismatch`` table ref_summary joins from ``ref_accuracy``.

    R::

        tables$accuracy[, cols] |>
          tidyr::pivot_longer(dplyr::ends_with("_mismatch")) |>
          dplyr::mutate(name = gsub("_mismatch", "", name)) |>
          dplyr::filter(!value %in% FALSE) |>
          dplyr::summarise(accuracy_mismatch = paste(name, collapse = ", "),
                           .by = c(paper_id, bib_id, no_match))
        tbl$accuracy_mismatch[tbl$no_match %in% TRUE] <- "no match"
        tbl$no_match <- NULL
    """
    cols = ["paper_id", "bib_id", *grep("no_match|_mismatch", list(acc.columns), value=True)]
    missing = [c for c in cols if c not in acc.columns]
    if missing:
        raise ValueError(f"Can't subset columns that don't exist.\n✖ Column `{missing[0]}`")
    # tidyselect's ends_with() ignores case
    value_cols = [c for c in cols if c.lower().endswith("_mismatch")]
    if not value_cols:
        raise ValueError("`cols` must select at least one column.")
    for c in ("paper_id", "bib_id", "no_match"):
        if c not in cols or c in value_cols:
            raise ValueError(f"Can't select columns that don't exist.\n✖ Column `{c}`")
    names = [str(gsub("_mismatch", "", c)) for c in value_cols]
    values = [acc[c].tolist() for c in value_cols]
    pid = acc["paper_id"].tolist()
    bid = acc["bib_id"].tolist()
    nm = acc["no_match"].tolist()

    groups: dict[tuple[Any, ...], list[Any]] = {}
    for i in range(len(acc)):
        for name, vals in zip(names, values, strict=True):
            if _is_false(vals[i]):
                continue
            key = (_key_value(pid[i]), _key_value(bid[i]), _key_value(nm[i]))
            group = groups.get(key)
            if group is None:
                groups[key] = [pid[i], bid[i], nm[i], [name]]
            else:
                group[3].append(name)
    rows = list(groups.values())
    mismatch = [
        "no match" if _is_true(r[2]) else ", ".join(r[3])  # no_match %in% TRUE
        for r in rows
    ]
    return pd.DataFrame(
        {
            "paper_id": pd.Series([r[0] for r in rows], dtype=acc["paper_id"].dtype),
            "bib_id": pd.Series([r[1] for r in rows], dtype=acc["bib_id"].dtype),
            "accuracy_mismatch": pd.array(mismatch, dtype="string"),
        }
    )


def _select(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    return df.loc[:, list(cols)].reset_index(drop=True)


def _has_rows(x: Any) -> bool:
    """``!is.null(x) && nrow(x) > 0``."""
    return isinstance(x, pd.DataFrame) and len(x) > 0


@module(
    title="Summarise References",
    description="Summarise information about each reference in a paper.",
    details="""
        This module summarises previously-run reference section modules: ref_accuracy, ref_pubpeer, ref_replication, and ref_retraction.
    """,
    keywords=["reference"],
    author=["Lisa DeBruine <debruine@gmail.com>"],
    params={"paper": "a paper object or paperlist object"},
)
def ref_summary(paper: Any, **kwargs: Any) -> dict[str, Any]:
    """Port of ``inst/modules/ref_summary.R::ref_summary()``.

    Joins the tables of the reference modules run earlier in a
    :func:`~pytacheck.module.module_run` chain (``ref_accuracy``,
    ``ref_pubpeer``, ``ref_replication``, ``ref_retraction``) onto the
    paper's ``ref_table()``. Extra arguments (R's ``...``) are ignored.
    """
    from pytacheck.module import get_prev_outputs
    from pytacheck.papers.tables import ref_table
    from pytacheck.report import link, scroll_table

    # if all tables null, quit here
    table = ref_table(paper)
    if len(table) == 0:
        return {"traffic_light": "na", "summary_text": "No references to summarise"}

    # module code ----
    tables = {
        "accuracy": get_prev_outputs("ref_accuracy", "table"),
        "pubpeer": get_prev_outputs("ref_pubpeer", "table"),
        "replication": get_prev_outputs("ref_replication", "table"),
        "retraction": get_prev_outputs("ref_retraction", "table"),
    }

    # create return items ----

    ## accuracy -----
    if _has_rows(tables["accuracy"]):
        tbl = _accuracy_mismatches(tables["accuracy"])
        table = _join(table, tbl, _KEYS, "left")

    ## pubpeer ----
    if _has_rows(tables["pubpeer"]):
        pp = tables["pubpeer"]
        cols = [c for c in ("paper_id", "bib_id", "url") if c in pp.columns]
        tbl = _select(pp, cols)
        if "url" in tbl.columns:
            urls = tbl["url"].tolist()
            tbl["pubpeer"] = pd.array(link(urls, "Link") if urls else [], dtype="string")
            tbl = tbl.drop(columns="url")
        table = _join(table, tbl, _KEYS, "left")

    ## replication ----
    if _has_rows(tables["replication"]):
        rep = tables["replication"]
        cols = [c for c in ("paper_id", "bib_id", "replication_type") if c in rep.columns]
        table = _join(table, _select(rep, cols), _KEYS, "left")

    ## retraction ----
    if _has_rows(tables["retraction"]):
        ret = tables["retraction"]
        cols = [c for c in ret.columns if c not in ("text", "doi")]
        table = _join(table, _select(ret, cols), _KEYS, "left")

    ## traffic light ----
    tl = "info"

    ## summary_text ----
    n = len(table)
    summary_text = f"Summary information provided for {n:d} reference{plural(n)}"

    ## report ----
    cols = ["text", *grep(_REPORT_COLS, [str(c) for c in table.columns], value=True)]
    report_table = _select(table, cols)
    colwidths = [0.75, *([None] * (len(cols) - 1))]
    report = [
        "See the specific reports above for details.",
        scroll_table(report_table, maxrows=10, colwidths=colwidths),
    ]

    # return a list ----
    return {
        "table": table,
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
