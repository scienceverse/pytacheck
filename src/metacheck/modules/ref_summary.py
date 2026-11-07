"""Summarise References (port of ``inst/modules/ref_summary.R``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r.base import as_character, plural
from metacheck._r.regex import grep
from metacheck._values import is_missing
from metacheck.module import module

__all__ = ["ref_summary"]

_KEYS = ["paper_id", "bib_id"]

# report columns: grep("^(pubpeer.*|replication.*|retractionwatch|.*_mismatch)$", ...)
_REPORT_COLS = r"^(pubpeer.*|replication.*|retractionwatch|.*_mismatch)$"


# -- dplyr joins ----------------------------------------------------------------------


def _key_array(s: pd.Series) -> np.ndarray:
    """A join/group key column as a numpy array that compares like R's keys.

    Numbers (and logicals) become ``float64`` with ``NaN`` for ``NA``, so integer
    and double keys match by value; anything else becomes objects with
    ``None`` for ``NA`` (factors by their labels).
    """
    dt = s.dtype
    plain = not (pd.api.types.is_object_dtype(dt) or isinstance(dt, pd.CategoricalDtype))
    if plain and (pd.api.types.is_bool_dtype(dt) or pd.api.types.is_numeric_dtype(dt)):
        return np.asarray(s.to_numpy(dtype="float64", na_value=np.nan))
    return np.asarray(s.astype(object).where(s.notna(), None).to_numpy(dtype=object))


def _combine_codes(codes: Sequence[np.ndarray], n: int) -> np.ndarray:
    """One integer code per row from per-column codes (equal codes = equal key tuples)."""
    if not codes:
        return np.zeros(n, dtype=np.intp)
    out = np.asarray(codes[0], dtype=np.int64)
    for c in codes[1:]:
        width = int(c.max()) + 1 if len(c) else 1
        out, _ = pd.factorize(out * width + c)
    return np.asarray(out, dtype=np.intp)


def _key_codes(frames: Sequence[pd.DataFrame], by: Sequence[str]) -> list[np.ndarray]:
    """Integer key codes of *frames*' rows, shared across the frames.

    ``NA`` keys match each other (dplyr's ``na_matches = "na"``, and the ``.by``
    groups of ``summarise()``).
    """
    sizes = [len(f) for f in frames]
    per_col = []
    for k in by:
        values = np.concatenate([_key_array(f[k]) for f in frames]) if frames else np.empty(0)
        codes, _ = pd.factorize(values, use_na_sentinel=False)
        per_col.append(codes)
    combined = _combine_codes(per_col, sum(sizes))
    bounds = np.cumsum([0, *sizes])
    return [combined[bounds[i] : bounds[i + 1]] for i in range(len(frames))]


def _add_suffixes(x: Sequence[str], y: Sequence[str], suffix: str) -> list[str]:
    """``dplyr:::add_suffixes()``: suffix the names of *x* until none is in *y* or repeated."""
    names = [*y, *x]
    while True:
        seen: set[str] = set()
        dup = []
        for i, nm in enumerate(names):
            if nm in seen:
                dup.append(i)
            else:
                seen.add(nm)
        if not dup:
            return names[len(y) :]
        for i in dup:
            names[i] += suffix


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
    ``NA`` keys match each other and integer and double keys compare by value.
    Non-key columns in both tables get dplyr's ``.x`` / ``.y`` suffixes,
    repeated until the names are unique (``dplyr:::add_suffixes()``).
    Many-to-many matches are kept silently: dplyr only warns about them when
    the join is called from the global environment, never inside a module.
    """
    by = list(by)
    for side, df in (("x", x), ("y", y)):
        missing = [k for k in by if k not in df.columns]
        if missing:
            problem = ", ".join(f"`{k}`" for k in missing)
            raise ValueError(
                f"Join columns in `{side}` must be present in the data.\n✖ Problem with {problem}."
            )
    # vctrs casts each key pair to a common type: a character (or factor) key
    # never joins a number or logical one, even when all its values are NA
    # (e.g. a hand-edited earlier module's table with a character bib_id),
    # and an integer key joined to a double one comes back as a double
    from metacheck.modules.ref_accuracy import _key_kind

    to_double: list[str] = []
    for k in by:
        kx, ky = _key_kind(x[k]), _key_kind(y[k])
        if "chr" in (kx, ky) and {kx, ky} & {"num", "lgl"}:
            raise TypeError(f"Can't join `x${k}` with `y${k}` due to incompatible types.")
        if (
            ky == "num"
            and pd.api.types.is_float_dtype(y[k])
            and kx in ("num", "lgl")
            and not pd.api.types.is_float_dtype(x[k])
        ):
            to_double.append(k)
    nx, ny = len(x), len(y)
    xc, yc = _key_codes([x, y], by)
    if ny:
        ncode = int(max(yc.max(), xc.max() if nx else 0)) + 1
        order = np.argsort(yc, kind="stable")  # y rows grouped by key, in y order
        counts = np.bincount(yc, minlength=ncode)
        hits = counts[xc]
        first = (np.cumsum(counts) - counts)[xc]
    else:
        order = np.empty(0, dtype=np.intp)
        hits = first = np.zeros(nx, dtype=np.intp)
    reps = np.maximum(hits, 1) if how == "left" else hits
    x_idx = np.repeat(np.arange(nx, dtype=np.intp), reps)
    offset = np.arange(len(x_idx)) - np.repeat(np.cumsum(reps) - reps, reps)
    matched = np.repeat(hits, reps) > 0
    y_idx = np.full(len(x_idx), -1, dtype=np.intp)
    y_idx[matched] = order[(np.repeat(first, reps) + offset)[matched]]

    x_cols = [str(c) for c in x.columns]
    y_cols = [str(c) for c in y.columns]
    x_aux = [c for c in x_cols if c not in by]
    y_aux = [c for c in y_cols if c not in by]
    x_names = dict(zip(x_aux, _add_suffixes(x_aux, [*by, *y_aux], ".x"), strict=True))
    y_names = dict(zip(y_cols, _add_suffixes(y_cols, x_cols, ".y"), strict=True))
    cols: dict[str, pd.Series] = {}
    for c, col in zip(x_cols, x.columns, strict=True):
        cols[x_names.get(c, c)] = x[col].iloc[x_idx].reset_index(drop=True)
    for k in to_double:
        cols[k] = cols[k].astype("Float64").astype("float64")
    for c, col in zip(y_cols, y.columns, strict=True):
        if c not in by:
            cols[y_names[c]] = _take(y[col], y_idx).reset_index(drop=True)
    return pd.DataFrame(cols)


def _join_doi(x: pd.DataFrame, y: pd.DataFrame, y_doi: str = "doi") -> pd.DataFrame:
    """``dplyr::inner_join(x, y, by = c(doi = y_doi))`` with DOIs compared case-insensitively.

    DOIs are case-insensitive, but the databases keep the publishers' capitals
    (13,123 RetractionWatch DOIs, e.g. ``10.1016/S0140-6736(97)11096-0``) and
    reference DOIs are mostly lower case, so metacheck's exact join missed them
    (U157). *x* keeps its own ``doi``; *y*'s DOI column is dropped. Missing
    DOIs match nothing.
    """
    key = ".doi_key"
    x_key = x["doi"].astype("string").str.lower()
    y_key = y[y_doi].astype("string").str.lower()
    wanted = set(x_key.dropna().tolist())
    keep = (y_key.notna() & y_key.isin(wanted)).to_numpy(dtype=bool)
    y = y.loc[keep].drop(columns=y_doi).assign(**{key: y_key[keep].array})
    table = _join(x.assign(**{key: x_key.array}), y, [key], "inner")
    return table.drop(columns=key)


# -- the earlier modules' tables ---------------------------------------------------------


def _is_true_flag(v: Any) -> bool:
    """``isTRUE(v)``: only a logical ``TRUE``."""
    return isinstance(v, bool | np.bool_) and bool(v)


def _accuracy_mismatches(acc: pd.DataFrame) -> pd.DataFrame:
    """The ``accuracy_mismatch`` table ref_summary joins from ``ref_accuracy``.

    One row per reference (its first row), with ``"no match"`` when the reference
    had no match, else the names of its ``*_mismatch`` columns that are ``TRUE``
    (``"doi, year"``). References without a mismatch are left out, and a table
    without ``no_match`` has no reference without a match (U119).
    """
    acc = _first_columns(acc)
    (codes,) = _key_codes([acc], _KEYS)
    first = ~pd.Series(codes).duplicated().to_numpy(dtype=bool)
    acc = acc.loc[first].reset_index(drop=True)
    mm = [str(c) for c in acc.columns if str(c).endswith("_mismatch")]
    no_match = acc["no_match"].tolist() if "no_match" in acc.columns else [None] * len(acc)
    flags = {m: acc[m].tolist() for m in mm}
    values: list[str | None] = []
    for i in range(len(acc)):
        if _is_true_flag(no_match[i]):
            values.append("no match")
            continue
        hit = [m[: -len("_mismatch")] for m in mm if _is_true_flag(flags[m][i])]
        values.append(", ".join(hit) if hit else None)
    keep = np.array([v is not None for v in values], dtype=bool)
    out = acc.loc[keep, _KEYS].reset_index(drop=True)
    out["accuracy_mismatch"] = pd.array([v for v in values if v is not None], dtype="string")
    return out


def _one_per_reference(tbl: pd.DataFrame) -> pd.DataFrame:
    """One row per reference (``paper_id``, ``bib_id``) of an earlier module's table.

    Several rows for a reference (several FLoRA replications, a DOI that PubPeer
    lists twice) made metacheck's left joins repeat the reference (U119). Each other
    column keeps its distinct non-missing values, joined with "; " (text, and
    ``NA`` when there are none).
    """
    (codes,) = _key_codes([tbl], _KEYS)
    groups: dict[int, list[int]] = {}
    for i, c in enumerate(codes.tolist()):
        groups.setdefault(c, []).append(i)
    first = [rows[0] for rows in groups.values()]
    out = tbl.loc[:, _KEYS].iloc[first].reset_index(drop=True)
    for col in tbl.columns:
        if col in _KEYS:
            continue
        values = tbl[col].tolist()
        merged: list[str | None] = []
        for rows in groups.values():
            distinct = list(
                dict.fromkeys(
                    _chr(values[i]) for i in rows if not is_missing(values[i]) and _chr(values[i])
                )
            )
            merged.append("; ".join(distinct) if distinct else None)
        out[col] = pd.array(merged, dtype="string")
    return out


def _chr(v: Any) -> str:
    """``as.character(v)`` of one value."""
    return v if isinstance(v, str) else str(as_character(v))


def _first_columns(df: pd.DataFrame) -> pd.DataFrame:
    """*df* without the later columns of a repeated name.

    Selecting a column by name in R (``df[, cols]``, ``df$x``) takes the
    first column with that name, e.g. in a hand-edited earlier module table.
    """
    later = pd.Index(df.columns).duplicated()
    return df.loc[:, ~later] if later.any() else df


def _select(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    """``df[, cols]`` (by name: the first column of a repeated name)."""
    return _first_columns(df).loc[:, list(cols)].reset_index(drop=True)


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
def ref_summary(paper: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001 - R `...`
    """Port of ``inst/modules/ref_summary.R::ref_summary()``.

    Joins the tables of the reference modules run earlier in a
    :func:`~metacheck.module.module_run` chain (``ref_accuracy``,
    ``ref_pubpeer``, ``ref_replication``, ``ref_retraction``) onto the
    paper's ``ref_table()``. Extra arguments (R's ``...``) are ignored.
    """
    from metacheck.module import get_prev_outputs
    from metacheck.papers.tables import ref_table
    from metacheck.report import link, scroll_table

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
        table = _join(table, _accuracy_mismatches(tables["accuracy"]), _KEYS, "left")

    ## pubpeer ----
    if _has_rows(tables["pubpeer"]):
        pp = tables["pubpeer"]
        cols = [c for c in ("paper_id", "bib_id", "url") if c in pp.columns]
        tbl = _select(pp, cols)
        if "url" in tbl.columns:
            urls = tbl["url"].tolist()
            tbl["pubpeer"] = pd.array(link(urls, "Link") if urls else [], dtype="string")
            tbl = tbl.drop(columns="url")
        table = _join(table, _one_per_reference(tbl), _KEYS, "left")

    ## replication ----
    if _has_rows(tables["replication"]):
        rep = tables["replication"]
        cols = [c for c in ("paper_id", "bib_id", "replication_type") if c in rep.columns]
        table = _join(table, _one_per_reference(_select(rep, cols)), _KEYS, "left")

    ## retraction ----
    if _has_rows(tables["retraction"]):
        ret = tables["retraction"]
        cols = list(dict.fromkeys(c for c in ret.columns if c not in ("text", "doi")))
        table = _join(table, _one_per_reference(_select(ret, cols)), _KEYS, "left")

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
