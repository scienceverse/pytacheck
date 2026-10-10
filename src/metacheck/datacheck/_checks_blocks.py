"""Likert scale-block and behavioural-task column detection (private).

Port of the "Likert scale-block detection" and "Task column detection"
sections of ``R/data_check_helpers.R``. Column positions returned by the
block detectors are 0-based (R's are 1-based).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r.base import paste
from metacheck._r.regex import grepl, sub
from metacheck._values import as_float, as_str
from metacheck.datacheck._checks_facets import _ACC_NAME_RE, _RT_NAME_RE
from metacheck.datacheck._kinds import LOGICAL, NUMERIC, as_numbers, as_text, column, kind

__all__ = [
    "_SCALE_MIN_ITEMS",
    "_TASK_ACC_MIN_ITEMS",
    "_detect_accuracy_blocks",
    "_detect_scale_blocks",
    "_detect_task_columns",
    "_is_accuracy_item",
    "_is_likert_item",
    "_is_task_data",
    "_looks_like_accuracy",
    "_looks_like_rt",
    "_scale_block_is_ratinglike",
    "_scale_block_range",
    "_scale_name_prefix",
]

#: R: .scale_min_items -- minimum items for a scale block
_SCALE_MIN_ITEMS = 3
#: R: .task_acc_min_items -- minimum items for an accuracy block
_TASK_ACC_MIN_ITEMS = 8

_COND_TASK_RE = (
    r"(^|[^a-z])(condition|cond|block|trial[._ -]?type|congruen\w*|stimulus[._ -]?type)"
    r"([^a-z]|$)"
)


def _values_array(x: Any) -> Any:
    """The shared coercion step of the value classifiers: a numeric column as is,
    otherwise ``as.numeric(as.character(x))`` as a float64 array; ``None`` when
    that is empty or more than 20% missing (the shared rejection)."""
    col = column(x)
    if kind(col) == NUMERIC:
        return as_numbers(col)
    a = as_numbers(as_text(col))
    if not a.size or np.isnan(a).sum() > 0.2 * a.size:
        return None
    return a


def _is_likert_item(x: Any) -> bool:
    """Is a column a plausible Likert item (whole numbers, 3-11 levels, narrow range)?

    Port of ``R/data_check_helpers.R::.is_likert_item()``.
    """
    a = _values_array(x)
    if a is None:
        return False
    a = a[~np.isnan(a)]
    if a.size < 10:
        return False
    if not np.all(a == np.round(a)):  # Inf counts as whole, as in R
        return False
    u = np.unique(a)
    lo, hi = float(u[0]), float(u[-1])
    return bool(3 <= u.size <= 11 and (hi - lo) <= 12 and lo >= -5 and hi <= 100)


def _looks_like_rt(x: Any) -> bool:
    """Does a column look like reaction times by its values (many positive values, wide)?

    Port of ``R/data_check_helpers.R::.looks_like_rt()``.
    """
    a = _values_array(x)
    if a is None:
        return False
    a = a[np.isfinite(a)]
    if a.size < 10:
        return False
    if np.any(a < 0):
        return False
    if np.unique(a).size < 10:
        return False
    rng = float(a.max() - a.min())
    med = float(np.median(a))
    return bool((rng > 12 and med > 20) or (np.any(a != np.round(a)) and rng > 0.05 and med < 60))


def _logical_count(x: Any) -> int | None:
    """The number of non-NA values when *x* is a logical column, else ``None``."""
    col = column(x)
    return int(col.notna().sum()) if kind(col) == LOGICAL and len(col) else None


def _looks_like_accuracy(x: Any) -> bool:
    """Does a column look like accuracy by its values (binary or a proportion)?

    Port of ``R/data_check_helpers.R::.looks_like_accuracy()``.
    """
    n_lgl = _logical_count(x)
    if n_lgl is not None:
        return n_lgl >= 10
    a = _values_array(x)
    if a is None:
        return False
    a = a[np.isfinite(a)]
    if a.size < 10:
        return False
    u = np.unique(a)
    if np.all((u == 0) | (u == 1)):
        return True
    return bool(np.all((a >= 0) & (a <= 1)) and u.size > 2)


def _names(df: Any) -> list[str]:
    return [str(c) for c in df.columns]


def _empty_task_columns() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "column_name": pd.Series([], dtype="string"),
            "kind": pd.Series([], dtype="string"),
            "by_name": pd.Series([], dtype="boolean"),
            "by_value": pd.Series([], dtype="boolean"),
        }
    )


def _detect_task_columns(df: Any) -> pd.DataFrame:
    """Classify each column as an RT / accuracy / condition task column.

    Port of ``R/data_check_helpers.R::.detect_task_columns()``: a data frame
    with ``column_name``, ``kind`` (``"rt"``, ``"accuracy"``, ``"condition"``),
    ``by_name`` and ``by_value``, one row per task column.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.shape[1] == 0:
        return _empty_task_columns()
    nm = _names(df)
    key = [s.lower() for s in nm]
    rt_name = [bool(b) for b in grepl(_RT_NAME_RE, key, perl=True)]
    acc_name = [bool(b) for b in grepl(_ACC_NAME_RE, key, perl=True)]
    cols = [df.iloc[:, j] for j in range(df.shape[1])]
    rt_val = [_looks_like_rt(c) for c in cols]
    acc_val = [_looks_like_accuracy(c) for c in cols]
    cond_name = [bool(b) for b in grepl(_COND_TASK_RE, key, perl=True)]
    cond_shape = []
    for c in cols:
        c = c.dropna()
        cond_shape.append(len(c) >= 10 and 2 <= c.nunique() <= 8)
    kind = [""] * len(nm)
    for i in range(len(nm)):
        if acc_name[i] and acc_val[i]:
            kind[i] = "accuracy"
        if rt_name[i] and rt_val[i]:
            kind[i] = "rt"
        if cond_name[i] and cond_shape[i] and kind[i] == "":
            kind[i] = "condition"
    keep = [i for i in range(len(nm)) if kind[i] != ""]
    return pd.DataFrame(
        {
            "column_name": pd.Series([nm[i] for i in keep], dtype="string"),
            "kind": pd.Series([kind[i] for i in keep], dtype="string"),
            "by_name": pd.Series(
                [rt_name[i] or acc_name[i] or cond_name[i] for i in keep], dtype="boolean"
            ),
            "by_value": pd.Series([rt_val[i] or acc_val[i] for i in keep], dtype="boolean"),
        }
    )


def _is_accuracy_item(x: Any) -> bool:
    """Is a column a per-trial accuracy item (binary 0/1 or TRUE/FALSE)?

    Port of ``R/data_check_helpers.R::.is_accuracy_item()``.
    """
    n_lgl = _logical_count(x)
    if n_lgl is not None:
        return n_lgl >= 10
    a = _values_array(x)
    if a is None:
        return False
    a = a[~np.isnan(a)]  # x[!is.na(x)]: infinite values stay
    if a.size < 10:
        return False
    u = np.unique(a)
    return bool(u.size == 2 and u[0] == 0 and u[1] == 1)


def _scale_name_prefix(nm: Any) -> Any:
    """Variable-name prefix without a trailing item number (``bfi_1`` -> ``bfi``).

    Port of ``R/data_check_helpers.R::.scale_name_prefix()`` (vectorised: a
    string gives a string, a vector a list).
    """
    if isinstance(nm, str) or nm is None:
        p = sub("[._-]?[0-9]+$", "", nm)
        p = sub("[._-]+$", "", p)
        return None if p is None else p.lower()
    return [_scale_name_prefix(s) for s in as_text(nm)]


def _runs(ok: list[bool], nm: list[str], min_items: int) -> list[list[int]]:
    """Maximal runs of adjacent *ok* columns sharing a name prefix (0-based)."""
    blocks: list[list[int]] = []
    start: int | None = None
    cur_pre: str | None = None
    for j in range(len(nm)):
        p = _scale_name_prefix(nm[j]) if ok[j] else None
        if p is not None and start is not None and p == cur_pre:
            continue
        if start is not None and (j - 1) - start + 1 >= min_items:
            blocks.append(list(range(start, j)))
        cur_pre = p
        start = j if p is not None else None
    if start is not None and len(nm) - start >= min_items:
        blocks.append(list(range(start, len(nm))))
    return blocks


def _detect_accuracy_blocks(df: Any, min_items: int = _TASK_ACC_MIN_ITEMS) -> list[list[int]]:
    """Runs of adjacent binary accuracy items sharing a name prefix (``raven_1..18``).

    Port of ``R/data_check_helpers.R::.detect_accuracy_blocks()``; returns
    lists of 0-based column positions.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.shape[1] == 0:
        return []
    ok = [_is_accuracy_item(df.iloc[:, j]) for j in range(df.shape[1])]
    return _runs(ok, _names(df), int(min_items))


def _is_task_data(df: Any) -> bool:
    """Does *df* look like task data (an RT / accuracy column or an accuracy block)?

    Port of ``R/data_check_helpers.R::.is_task_data()``.
    """
    tc = _detect_task_columns(df)
    if tc["kind"].isin(["rt", "accuracy"]).any():
        return True
    return len(_detect_accuracy_blocks(df)) > 0


def _scale_block_range(block: Any) -> str:
    """Pooled response range of a set of item columns as ``"min-max"`` (``"?"`` if none).

    Port of ``R/data_check_helpers.R::.scale_block_range()``.
    """
    if isinstance(block, pd.DataFrame):
        cols: list[Any] = [block.iloc[:, j] for j in range(block.shape[1])]
    elif isinstance(block, Mapping):
        cols = list(block.values())
    elif (
        isinstance(block, list | tuple)
        and block
        and all(isinstance(c, list | tuple | pd.Series) for c in block)
    ):
        cols = list(block)
    else:
        cols = [block]
    v = [f for c in cols for s in as_text(c) if (f := as_float(s)) is not None and f == f]
    if not v:
        return "?"
    return f"{as_str(min(v))}-{as_str(max(v))}"


def _detect_scale_blocks(df: Any, min_items: int = _SCALE_MIN_ITEMS) -> list[list[int]]:
    """Likert scale blocks: runs of adjacent Likert columns sharing a name prefix.

    Port of ``R/data_check_helpers.R::.detect_scale_blocks()``; returns lists
    of 0-based column positions, one per block of at least *min_items* items.
    """
    if df is None or df.shape[1] == 0:
        return []
    ok = [_is_likert_item(df.iloc[:, j]) for j in range(df.shape[1])]
    return _runs(ok, _names(df), int(min_items))


def _as_list(x: Any) -> list[Any]:
    return [x] if isinstance(x, str) else list(x)


def _scale_block_is_ratinglike(cols: Any, source_file: Any, columns_df: Any) -> bool:
    """Is a prefix group a rating-like block, judged from data_check's column statistics?

    Port of ``R/data_check_helpers.R::.scale_block_is_ratinglike()``: mostly
    numeric columns with a pooled range inside ``[-1, 100]`` and a maximum
    above 1.
    """
    need = ("source_file", "column_name", "min", "max")
    if columns_df is None or len(columns_df) == 0 or not all(c in columns_df.columns for c in need):
        return False
    key = _as_list(
        paste(list(columns_df["source_file"]), list(columns_df["column_name"]), sep="\x01")
    )
    want = set(_as_list(paste(as_text(source_file), as_text(cols), sep="\x01")))
    idx = [i for i, k in enumerate(key) if k in want]
    if len(idx) < _SCALE_MIN_ITEMS:
        return False
    mn = as_numbers(columns_df["min"].iloc[idx])
    mx = as_numbers(columns_df["max"].iloc[idx])
    numeric_frac = float((np.isfinite(mn) & np.isfinite(mx)).mean())
    if numeric_frac < 0.6:
        return False
    lo_vals, hi_vals = mn[~np.isnan(mn)], mx[~np.isnan(mx)]
    if not lo_vals.size or not hi_vals.size:
        return False
    lo = float(lo_vals.min())
    hi = float(hi_vals.max())
    if not math.isfinite(lo) or not math.isfinite(hi):
        return False
    return lo >= -1 and hi > 1 and hi <= 100
