"""Summarise References (port of ``inst/modules/ref_summary.R``)."""

from __future__ import annotations

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
    from pytacheck.modules.ref_accuracy import _key_kind

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


_NUMERIC = frozenset({"logical", "integer", "double"})
_TEXT = frozenset({"character", "factor"})


def _vec_kind(s: pd.Series) -> str:
    """The R vector type a column stands for (``"unspecified"``: all-``NA`` logical)."""
    dt = s.dtype
    if isinstance(dt, pd.CategoricalDtype):
        return "factor"
    if not pd.api.types.is_object_dtype(dt):
        if pd.api.types.is_bool_dtype(dt):
            return "unspecified" if bool(s.isna().all()) else "logical"
        if pd.api.types.is_integer_dtype(dt):
            return "integer"
        if pd.api.types.is_float_dtype(dt):
            return "double"
        if pd.api.types.is_string_dtype(dt):
            return "character"
        return "other"
    vals = [v for v in s.tolist() if not is_na(v)]
    if not vals:
        return "unspecified"
    if all(isinstance(v, bool | np.bool_) for v in vals):
        return "logical"
    if all(isinstance(v, str) for v in vals):
        return "character"
    if all(isinstance(v, int | np.integer) and not isinstance(v, bool) for v in vals):
        return "integer"
    if all(isinstance(v, int | float | np.number) and not isinstance(v, bool) for v in vals):
        return "double"
    if all(isinstance(v, list | dict | pd.DataFrame) for v in vals):
        return "list"
    return "other"


def _check_combinable(acc: pd.DataFrame, cols: Sequence[str]) -> None:
    """``tidyr::pivot_longer()`` fails when the value columns have no common type."""
    first: tuple[str, str] | None = None
    for c in cols:
        kind = _vec_kind(acc[c])
        if kind in ("unspecified", "other"):
            continue
        if first is None:
            first = (c, kind)
            continue
        prev = first[1]
        if prev == kind or {prev, kind} <= _NUMERIC or {prev, kind} <= _TEXT:
            continue
        raise ValueError(f"Can't combine `{first[0]}` <{prev}> and `{c}` <{kind}>.")


def _in_bool(s: pd.Series, value: bool) -> np.ndarray:
    """``s %in% TRUE`` / ``s %in% FALSE`` as a bool array (``NA`` is in neither)."""
    kind = _vec_kind(s)
    if kind == "unspecified":
        return np.zeros(len(s), dtype=bool)
    if kind in _NUMERIC:
        num = np.asarray(s.to_numpy(dtype="float64", na_value=np.nan))
        return np.asarray(num == (1.0 if value else 0.0))
    if kind in _TEXT:
        text = s.astype("string") == ("TRUE" if value else "FALSE")
        return text.fillna(False).to_numpy(dtype=bool)
    test = _is_true if value else _is_false
    return np.fromiter((test(v) for v in s.tolist()), dtype=bool, count=len(s))


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

    The long table is never built: the kept cells of the ``*_mismatch``
    columns are read row by row (pivot_longer()'s order) and grouped by the
    key codes of their rows, in order of first appearance.
    """
    cols = ["paper_id", "bib_id", *grep("no_match|_mismatch", list(acc.columns), value=True)]
    missing = [c for c in cols if c not in acc.columns]
    if missing:
        raise ValueError(f"Can't subset columns that don't exist.\n✖ Column `{missing[0]}`")
    # `tables$accuracy[, cols]`: a repeated name selects its first column (as
    # often as grep() lists the name)
    acc = _first_columns(acc)
    # tidyselect's ends_with() ignores case
    value_cols = [c for c in cols if c.lower().endswith("_mismatch")]
    if not value_cols:
        raise ValueError("`cols` must select at least one column.")
    _check_combinable(acc, value_cols)
    if "no_match" not in cols:
        # metacheck fails with an empty message (U119): no column, no "no match"
        acc = acc.assign(no_match=pd.Series([False] * len(acc), index=acc.index, dtype="boolean"))
    names = np.array([str(gsub("_mismatch", "", c)) for c in value_cols], dtype=object)

    # filter(!value %in% FALSE): the kept (row, column) cells, row-major
    kept = np.column_stack([~_in_bool(acc[c], False) for c in value_cols])
    rows, which = np.nonzero(kept)
    (codes,) = _key_codes([acc], ["paper_id", "bib_id", "no_match"])
    group = codes[rows]
    order = np.argsort(group, kind="stable")  # cells grouped by key, row-major within
    starts = np.flatnonzero(np.r_[True, np.diff(group[order]) != 0]) if len(order) else order
    parts = np.split(names[which][order], starts[1:])
    # summarise(.by) orders the groups by their first (kept) cell
    appearance = np.argsort(order[starts], kind="stable")
    first = rows[order[starts]][appearance]
    joined = [", ".join(parts[i]) for i in appearance.tolist()]
    # tbl$accuracy_mismatch[tbl$no_match %in% TRUE] <- "no match"
    no_match_all = _in_bool(acc["no_match"], True)
    no_match = no_match_all[first]
    mismatch = np.where(no_match, "no match", np.array(joined, dtype=object))
    # a reference without a match whose *_mismatch are all FALSE: metacheck's
    # filter() drops it before the relabel, so it had no accuracy_mismatch (U119)
    listed = set(codes[first].tolist())
    extra: list[int] = []
    for i in np.flatnonzero(no_match_all).tolist():
        if codes[i] not in listed:
            listed.add(codes[i])
            extra.append(i)
    rows_out = [*first.tolist(), *extra]
    values = [*mismatch.tolist(), *(["no match"] * len(extra))]
    return pd.DataFrame(
        {
            "paper_id": acc["paper_id"].iloc[rows_out].reset_index(drop=True),
            "bib_id": acc["bib_id"].iloc[rows_out].reset_index(drop=True),
            "accuracy_mismatch": pd.array(values, dtype="string"),
        }
    )


def _one_per_reference(tbl: pd.DataFrame) -> pd.DataFrame:
    """One row per reference (``paper_id``, ``bib_id``) of an earlier module's table.

    Several rows for a reference (several FLoRA replications, a DOI that
    PubPeer lists twice) made metacheck's left joins repeat the reference
    (U119). Each other column keeps its distinct values, joined with ", "
    when they are text.
    """
    if len(tbl) == 0:
        return tbl
    (codes,) = _key_codes([tbl], _KEYS)
    if len(set(codes.tolist())) == len(codes):
        return tbl
    groups: dict[int, list[int]] = {}
    for i, c in enumerate(codes.tolist()):
        groups.setdefault(c, []).append(i)
    first = [rows[0] for rows in groups.values()]
    out = tbl.iloc[first].reset_index(drop=True)
    for col in tbl.columns:
        if col in _KEYS:
            continue
        values = tbl[col].tolist()
        merged = []
        for rows in groups.values():
            distinct = list(dict.fromkeys(values[i] for i in rows if not is_na(values[i])))
            if not distinct:
                merged.append(None)
            elif len(distinct) == 1 or not all(isinstance(v, str) for v in distinct):
                merged.append(distinct[0])
            else:
                merged.append(", ".join(distinct))
        out[col] = pd.Series(merged, dtype=tbl[col].dtype)
    return out


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
        table = _join(table, _one_per_reference(tbl), _KEYS, "left")

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
    # the references (metacheck counts the rows of its one-to-many joins, U119)
    (keys,) = _key_codes([table], _KEYS)
    n = len(set(keys.tolist()))
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
