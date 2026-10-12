"""dplyr idioms whose obvious pandas spelling gives a different table.

* ``dplyr::count()`` sorts groups in the C locale with ``NA`` last, returns an
  integer ``n`` column, and on a zero-row input returns a zero-row frame that
  still has the grouping and count columns.
* ``dplyr::bind_rows()`` unions columns in order of first appearance and fills
  missing cells with ``NA`` in a type that holds it (pandas ``concat`` turns a
  logical or integer column missing from some frames into object or float).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

__all__ = ["bind_rows", "count"]


def count(df: pd.DataFrame, by: str | Sequence[str], name: str = "n") -> pd.DataFrame:
    """``dplyr::count(df, ..., name = name)``."""
    cols = [by] if isinstance(by, str) else list(by)
    if len(df) == 0:
        out = df.loc[:, cols].iloc[0:0].copy()
        out[name] = pd.Series([], dtype="Int64")
        return out.reset_index(drop=True)
    grouped = df.groupby(cols, dropna=False, sort=True, observed=True).size()
    out = grouped.reset_index(name=name)
    out[name] = out[name].astype("Int64")
    return out


def bind_rows(frames: Iterable[pd.DataFrame | None]) -> pd.DataFrame:
    """``dplyr::bind_rows()``: row-bind, unioning columns in first-seen order."""
    parts = [f for f in frames if f is not None]
    if not parts:
        return pd.DataFrame()
    schemas = [f.dtypes for f in parts]
    first: dict[Any, Any] = {}  # each column's first dtype, in first-seen order
    for schema in schemas:
        for c, dtype in schema.items():
            first.setdefault(c, dtype)
    columns = list(first)
    non_empty = [f for f in parts if len(f) > 0]
    same = [
        list(s) for f, s in zip(parts, schemas, strict=True) if len(f) and list(s.index) == columns
    ]
    if len(same) == len(non_empty) > 0 and all(s == same[0] for s in same):
        # the same columns and types everywhere (a single frame, or the parts of
        # one table): nothing to add, cast or reorder
        if len(non_empty) == 1:
            return non_empty[0].reset_index(drop=True)
        return pd.concat(non_empty, ignore_index=True, sort=False)
    if not non_empty:
        out = parts[0].iloc[0:0].copy()
        for c in columns:
            if c not in out.columns:
                out[c] = pd.Series([], dtype=first[c])
        return out.loc[:, columns].reset_index(drop=True)
    aligned = []
    for f in non_empty:
        missing = [c for c in columns if c not in f.columns]
        if missing:
            f = f.copy()
            for c in missing:
                f[c] = pd.Series([None] * len(f), index=f.index, dtype=_nullable(first[c]))
        aligned.append(f)
    if all(f.columns.is_unique for f in aligned):
        _common_types(aligned, columns)
    return pd.concat(aligned, ignore_index=True, sort=False).loc[:, columns]


def _common_types(frames: list[pd.DataFrame], columns: list[Any]) -> None:
    """Cast logical parts to vctrs' common type, in place: numeric (``TRUE`` is 1)
    next to integer or double parts, and all-``NA`` ones to the other parts' type."""
    is_lgl, is_num = pd.api.types.is_bool_dtype, pd.api.types.is_numeric_dtype
    for c in columns:
        others = [f[c].dtype for f in frames if not is_lgl(f[c].dtype)]
        if not others or len(others) == len(frames):
            continue
        numeric = all(map(is_num, others))
        if numeric:
            target: Any = next((d for d in others if pd.api.types.is_float_dtype(d)), "Int64")
        else:
            target = next(d for d in others if not is_num(d))
        for i, f in enumerate(frames):
            if is_lgl(f[c].dtype) and (numeric or f[c].isna().all()):
                values = f[c] if numeric else [None] * len(f)
                frames[i] = f = f.copy()
                f[c] = pd.Series(values, index=f.index).astype(target)


def _nullable(dtype: Any) -> Any:
    """A dtype like *dtype* that can hold ``NA``."""
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "Int64"
    if pd.api.types.is_float_dtype(dtype):
        return "float64"
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "string"
    return object
