"""Per-column R attributes of a data frame read from a file (``df.attrs["col_attrs"]``).

R keeps attributes on each column vector, so two columns sharing a name
(``fread()``, ``readRDS()`` of a frame built with ``check.names = FALSE``)
each keep their own class and labels. :class:`ColAttrs` is a ``dict`` keyed
by column name -- a repeated name maps to its first column, the one R's
``df[[name]]`` / ``df$name`` returns -- that also remembers every column's
attributes by position. :func:`col_attrs_at` reads column *j*'s attributes by
position while the frame still has the columns the attributes were recorded
for, and falls back to the name otherwise (after columns were dropped,
reordered or renamed without :func:`rename_col_attrs`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["ColAttrs", "col_attrs_at", "rename_col_attrs"]


class ColAttrs(dict):  # type: ignore[type-arg]
    """Column attributes keyed by name (first column of a name) and by position.

    ``names`` and ``positional`` are aligned with the frame's columns; a column
    without attributes has ``{}``. The name entries are the positional dicts
    themselves, so an update through either view is seen by both.
    """

    names: list[Any]
    positional: list[dict[str, Any]]

    def __init__(
        self, names: Iterable[Any] = (), per_column: Iterable[Mapping[str, Any] | None] = ()
    ) -> None:
        super().__init__()
        self.names = list(names)
        self.positional = [dict(a) if a else {} for a in per_column]
        if len(self.names) != len(self.positional):
            raise ValueError("names and per_column must have the same length")
        seen: set[Any] = set()
        for name, attrs in zip(self.names, self.positional, strict=True):
            if name in seen:
                continue
            seen.add(name)
            if attrs:
                self[name] = attrs

    def any(self) -> bool:
        """Does any column carry attributes?"""
        return any(self.positional)


def col_attrs_at(df: pd.DataFrame, j: int) -> Mapping[str, Any]:
    """The R attributes of column *j* of *df* (``{}`` when it has none)."""
    ca = df.attrs.get("col_attrs") if isinstance(df.attrs, Mapping) else None
    if not isinstance(ca, Mapping):
        return {}
    if (
        isinstance(ca, ColAttrs)
        and len(ca.positional) == df.shape[1]
        and ca.names == list(df.columns)
    ):
        return ca.positional[j]
    return ca.get(df.columns[j]) or {}


def rename_col_attrs(ca: Mapping[str, Any], old: Sequence[Any], new: Sequence[Any]) -> ColAttrs:
    """*ca* after ``names(df) <- new`` (the attributes follow their columns by position)."""
    if isinstance(ca, ColAttrs) and ca.names == list(old):
        return ColAttrs(new, ca.positional)
    seen: set[Any] = set()
    per: list[Mapping[str, Any] | None] = []
    for o in old:  # a plain name-keyed dict: each name's first column
        per.append(None if o in seen else ca.get(o))
        seen.add(o)
    return ColAttrs(new, per)
