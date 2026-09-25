"""The :class:`Paper` and :class:`PaperList` objects.

A :class:`Paper` mirrors metacheck's ``scivrs_paper``: a ``paper_id`` plus a
set of named tables (``info``, ``author``, ``text``, ``section``, ``url``,
``bib``, ``xref``, ``figure``, ``table``, ``eq`` and optionally
``bib_match``) held as :class:`pandas.DataFrame` objects with the dtypes
described in :mod:`pytacheck.papers.schema`.

Tables read from JSON are materialised lazily: a paper keeps the parsed
records and only builds a DataFrame the first time the table is accessed.
:func:`pytacheck.papers.paper_table` can also concatenate the raw records of
many papers into one DataFrame without building a DataFrame per paper, which
is the dominant cost when checking a large corpus.

Tables are returned by reference. Library code (modules in particular) must
never mutate a paper it was given; the test suite checks this.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, overload

import pandas as pd

from pytacheck.papers.schema import empty_table, records_to_frame, required_tables, table_names

__all__ = ["Paper", "PaperList", "is_paper", "is_paper_list"]

_RESERVED = frozenset({"paper_id", "extra", "_tables", "_raw", "_columns", "_generation"})
# Process-wide mutation counter: ``Paper._generation`` changes whenever a paper is
# modified through its API, which invalidates ``run_session()`` memo entries.
_GENERATION = itertools.count(1)


# Papers created in the same clock tick (a coarse clock, e.g. on Windows) must not
# share an id, so the process-wide count of ids made goes into the hash too.
_ID_COUNT = itertools.count(1)


def _random_id() -> str:
    """A 14-character id hashed from the time, as ``metacheck::paper()`` makes one."""
    stamp = f"{time.time():.6f}-{next(_ID_COUNT)}".encode()
    return hashlib.md5(stamp, usedforsecurity=False).hexdigest()[:14]


class Paper:
    """A paper in the bibr schema (metacheck's ``scivrs_paper``).

    Parameters
    ----------
    paper_id:
        Unique ID. When omitted a random 14-character hash is generated, as
        ``metacheck::paper()`` does.
    **tables:
        Initial tables, as DataFrames. Every required schema table that is
        not given starts as an empty, fully-typed table.
    """

    __slots__ = ("_columns", "_generation", "_raw", "_tables", "extra", "paper_id")

    paper_id: str | None
    extra: dict[str, Any]

    def __init__(self, paper_id: str | None = None, **tables: Any) -> None:
        object.__setattr__(self, "paper_id", _random_id() if paper_id is None else paper_id)
        object.__setattr__(self, "extra", {})
        object.__setattr__(self, "_raw", {})
        object.__setattr__(self, "_columns", {})
        object.__setattr__(self, "_generation", next(_GENERATION))
        store: dict[str, Any] = {}
        for name in required_tables():
            store[name] = tables.pop(name) if name in tables else None
        store.update(tables)
        object.__setattr__(self, "_tables", store)

    # -- construction from raw JSON records (used by the readers) ----------

    def _set_raw(self, name: str, records: list[dict[str, Any]], columns: Sequence[str]) -> None:
        """Store *records* for lazy materialisation of table *name*."""
        self._touch()
        self._tables[name] = None
        self._raw[name] = records
        self._columns[name] = list(columns)

    def _touch(self) -> None:
        object.__setattr__(self, "_generation", next(_GENERATION))

    def _raw_records(self, name: str) -> tuple[list[dict[str, Any]], list[str]] | None:
        """``(records, columns)`` if *name* is still unmaterialised JSON."""
        if name in self._raw and self._tables.get(name) is None:
            return self._raw[name], self._columns[name]
        return None

    def _materialise(self, name: str) -> Any:
        value = self._tables.get(name)
        if value is None:
            if name in self._raw:
                value = records_to_frame(name, self._raw.pop(name), self._columns.pop(name))
            elif name in table_names():
                value = empty_table(name)
            else:
                return None
            self._tables[name] = value
        return value

    # -- mapping-style access ----------------------------------------------

    def keys(self) -> list[str]:
        """Element names, in R's order (``names(paper)`` minus ``paper_id``)."""
        return list(self._tables)

    def __contains__(self, name: object) -> bool:
        return name in self._tables

    def __getitem__(self, name: str) -> Any:
        if name == "paper_id":
            return self.paper_id
        if name not in self._tables:
            raise KeyError(name)
        return self._materialise(name)

    def __setitem__(self, name: str, value: Any) -> None:
        self._touch()
        if name == "paper_id":
            object.__setattr__(self, "paper_id", value)
            return
        self._raw.pop(name, None)
        self._columns.pop(name, None)
        self._tables[name] = value

    def __delitem__(self, name: str) -> None:
        self._touch()
        self._tables.pop(name, None)
        self._raw.pop(name, None)
        self._columns.pop(name, None)

    def get(self, name: str, default: Any = None) -> Any:
        """The element *name*, or *default* when the paper has no such element."""
        return self[name] if name in self._tables else default

    def items(self) -> Iterator[tuple[str, Any]]:
        for name in list(self._tables):
            yield name, self._materialise(name)

    # -- attribute access (paper.text, paper.info, ...) ---------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        tables = object.__getattribute__(self, "_tables")
        if name in tables:
            return self._materialise(name)
        if name in table_names():
            return None  # a schema table this paper does not have (R NULL)
        raise AttributeError(f"{type(self).__name__!s} has no table {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _RESERVED:
            object.__setattr__(self, name, value)
            self._touch()
        else:
            self[name] = value

    def __delattr__(self, name: str) -> None:
        del self[name]

    # -- convenience ---------------------------------------------------------

    @property
    def title(self) -> str | None:
        """The paper title from the ``info`` table."""
        info = self.info
        if info is None or len(info) == 0 or "title" not in info.columns:
            return None
        value = info["title"].iloc[0]
        return None if pd.isna(value) else str(value)

    def copy(self, deep: bool = True) -> Paper:
        """A copy; with ``deep=True`` every table is copied as well."""
        new = Paper(self.paper_id)
        tables: dict[str, Any] = {}
        for name in self._tables:
            raw = self._raw_records(name)
            if raw is not None:
                new._raw[name] = list(raw[0]) if deep else raw[0]
                new._columns[name] = list(raw[1])
                tables[name] = None
            else:
                value = self._tables[name]
                if isinstance(value, pd.DataFrame):
                    tables[name] = value.copy(deep=deep)
                elif deep and isinstance(value, dict | list):
                    tables[name] = copy.deepcopy(value)  # e.g. a bibr 12.x paper's extraction
                else:
                    tables[name] = value
        object.__setattr__(new, "_tables", tables)
        object.__setattr__(new, "extra", dict(self.extra))
        return new

    def to_dict(self) -> dict[str, Any]:
        """The paper as ``{"paper_id": ..., table: DataFrame, ...}``."""
        return {"paper_id": self.paper_id, **dict(self.items())}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Paper):
            return NotImplemented
        if self.paper_id != other.paper_id or self.keys() != other.keys():
            return False
        for name, value in self.items():
            theirs = other[name]
            if isinstance(value, pd.DataFrame) and isinstance(theirs, pd.DataFrame):
                if not value.equals(theirs):
                    return False
            elif value != theirs:
                return False
        return True

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        def rows(name: str) -> int:
            raw = self._raw_records(name)
            if raw is not None:
                return len(raw[0])
            value = self._tables.get(name)
            return len(value) if isinstance(value, pd.DataFrame) else 0

        pid = str(self.paper_id)
        line = "-" * len(pid)
        return (
            f"{line}\n{pid}\n{line}\n\n{self.title or '{No title}'}\n\n"
            f"* Sections: {rows('section')}\n* Sentences: {rows('text')}\n"
            f"* Bibliography: {rows('bib')}\n* X-Refs: {rows('xref')}\n"
        )


class PaperList(Sequence[Paper]):
    """An ordered collection of papers (metacheck's ``scivrs_paperlist``).

    Index with an ``int`` (0-based), a ``slice``, a paper ID ``str`` or a
    list of either. Iteration yields :class:`Paper` objects.
    """

    __slots__ = ("_papers",)

    def __init__(self, papers: Iterable[Paper] = (), merge_duplicates: bool = False) -> None:
        items: list[Paper] = []
        for p in papers:
            if isinstance(p, PaperList):
                items.extend(p)
            elif isinstance(p, Paper):
                items.append(p)
            else:
                raise TypeError("The arguments must be paper objects or lists of paper objects")
        if merge_duplicates:
            kept: list[Paper] = []
            for p in items:
                if any(q.paper_id == p.paper_id and q == p for q in kept):
                    continue
                kept.append(p)
            items = kept
        self._papers = items

    @property
    def names(self) -> list[str | None]:
        """Paper IDs, in order (R ``names(paperlist)``)."""
        return [p.paper_id for p in self._papers]

    def __len__(self) -> int:
        return len(self._papers)

    def __iter__(self) -> Iterator[Paper]:
        return iter(self._papers)

    @overload
    def __getitem__(self, key: int) -> Paper: ...
    @overload
    def __getitem__(self, key: str) -> Paper: ...
    @overload
    def __getitem__(self, key: slice | list[int] | list[str]) -> PaperList: ...

    def __getitem__(self, key: Any) -> Paper | PaperList:
        if isinstance(key, slice):
            return PaperList(self._papers[key])
        if isinstance(key, str):
            for p in self._papers:
                if p.paper_id == key:
                    return p
            raise KeyError(key)
        if isinstance(key, list):
            return PaperList(self[k] for k in key)
        return self._papers[key]

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return item in self.names
        return item in self._papers

    def __add__(self, other: Iterable[Paper]) -> PaperList:
        return PaperList([*self._papers, *other])

    def to_dict(self) -> dict[str | None, Paper]:
        return {p.paper_id: p for p in self._papers}

    def __repr__(self) -> str:
        rows = [f"{p.paper_id}: {p.title or ''}" for p in self._papers[:20]]
        more = f"\n... and {len(self) - 20} more" if len(self) > 20 else ""
        return f"<PaperList of {len(self)} papers>\n" + "\n".join(rows) + more


def is_paper(x: Any) -> bool:
    """``.is_paper()``."""
    return isinstance(x, Paper)


def is_paper_list(x: Any) -> bool:
    """``.is_paper_list()``: a PaperList, or a list/tuple made only of papers."""
    if isinstance(x, PaperList):
        return True
    if isinstance(x, list | tuple) and not isinstance(x, str):
        return all(isinstance(p, Paper) for p in x)
    if isinstance(x, Mapping):
        return all(isinstance(p, Paper) for p in x.values())
    return False
