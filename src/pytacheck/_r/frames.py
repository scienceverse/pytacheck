"""dplyr idioms that metacheck leans on, with dplyr's exact semantics.

pandas covers most of dplyr directly; this module only holds the cases
where the obvious pandas spelling silently differs from dplyr:

* ``dplyr::count()`` sorts groups in the C locale with ``NA`` last and
  returns an integer ``n`` column, and on a zero-row input returns a zero-row
  frame that still has the grouping and count columns.
* ``dplyr::bind_rows()`` unions columns in order of first appearance and
  fills missing cells with ``NA`` (pandas ``concat`` also does this, but
  drops/warns on empty frames and can upcast integer columns to float).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import pandas as pd

__all__ = ["bind_rows", "count", "empty_like", "ensure_columns"]


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
    columns: list[str] = []
    seen: set[str] = set()
    for f in parts:
        for c in f.columns:
            if c not in seen:
                seen.add(c)
                columns.append(c)
    non_empty = [f for f in parts if len(f) > 0]
    if not non_empty:
        out = parts[0].iloc[0:0].copy()
        for c in columns:
            if c not in out.columns:
                out[c] = pd.Series([], dtype=_first_dtype(parts, c))
        return out.loc[:, columns].reset_index(drop=True)
    aligned = [_align(f, columns, parts) for f in non_empty]
    out = pd.concat(aligned, ignore_index=True, sort=False)
    return out.loc[:, columns]


def _first_dtype(parts: Sequence[pd.DataFrame], col: str) -> object:
    for f in parts:
        if col in f.columns:
            return f[col].dtype
    return object


def _align(f: pd.DataFrame, columns: list[str], parts: Sequence[pd.DataFrame]) -> pd.DataFrame:
    missing = [c for c in columns if c not in f.columns]
    if not missing:
        return f
    f = f.copy()
    for c in missing:
        dtype = _first_dtype(parts, c)
        f[c] = pd.Series([pd.NA] * len(f), index=f.index, dtype=_nullable(dtype))
    return f


def _nullable(dtype: object) -> object:
    """Map a numpy dtype to a pandas dtype that can hold ``NA``."""
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "Int64"
    if pd.api.types.is_float_dtype(dtype):
        return "float64"
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "string"
    return object


def empty_like(df: pd.DataFrame) -> pd.DataFrame:
    """A zero-row copy of *df* with the same columns and dtypes (R ``df[c(), ]``)."""
    return df.iloc[0:0].copy()


def ensure_columns(df: pd.DataFrame, columns: Sequence[str], fill: object = pd.NA) -> pd.DataFrame:
    """Add any missing *columns* filled with *fill* (in place and returned)."""
    for c in columns:
        if c not in df.columns:
            df[c] = pd.Series([fill] * len(df), index=df.index, dtype=object)
    return df
