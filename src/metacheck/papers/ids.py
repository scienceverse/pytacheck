"""One rule for repeated paper IDs in a list of papers (F6; docs/design/ARCHITECTURE.md §2.2).

metacheck leaves repeated IDs to each module, with five different results on the
same list: rows dropped as duplicates, counts and texts that change, references of
two papers pooled, a summary join that repeats rows, a crash. pytacheck resolves them
once, where a list is taken in (:func:`as_paper_list`, :class:`~metacheck.core.doc.Docs`,
``module_run()`` and ``report()``), so that every module sees distinct papers:

* the first paper with an ID keeps it;
* a later paper with the same ID is renamed ``<id>~2``, ``<id>~3``, ... (a copy, so the
  caller's paper keeps its ID), skipping a name another paper of the list already has;
* one :class:`~metacheck.core.errors.PytacheckWarning` says which IDs were renamed.

An ID that is not text (``None``) is left as it is.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from typing import Any, TypeVar

from metacheck.core.errors import PytacheckWarning
from metacheck.papers.model import Paper, PaperList

__all__ = ["resolve", "resolve_ids"]

L = TypeVar("L", PaperList, list[Paper])

#: how many repeated IDs the warning names
_NAMED = 5


def resolve_ids(ids: Sequence[Any]) -> list[Any]:
    """*ids* with each later repeat renamed ``<id>~2``, ``<id>~3``, ... (first kept).

    A name another ID of the list already has is skipped (``["X", "X", "X~2"]`` gives
    ``["X", "X~3", "X~2"]``), so the result has no repeats.
    """
    taken = {i for i in ids if isinstance(i, str)}
    seen: set[str] = set()
    count: dict[str, int] = {}
    out: list[Any] = []
    for i in ids:
        if not isinstance(i, str) or i not in seen:
            if isinstance(i, str):
                seen.add(i)
            out.append(i)
            continue
        n = count.get(i, 1)
        while True:
            n += 1
            new = f"{i}~{n}"
            if new not in taken:
                break
        count[i] = n
        taken.add(new)
        seen.add(new)
        out.append(new)
    return out


def _warn(old: Sequence[Any], new: Sequence[Any], stacklevel: int) -> None:
    renamed: dict[str, list[str]] = {}
    for a, b in zip(old, new, strict=True):
        if a != b:
            renamed.setdefault(a, []).append(b)
    shown = [
        f"'{a}' as {', '.join(repr(b) for b in bs)}" for a, bs in list(renamed.items())[:_NAMED]
    ]
    more = len(renamed) - _NAMED
    if more > 0:
        shown.append(f"and {more} more")
    warnings.warn(
        "Paper IDs are repeated in the list, so every later paper with an ID is renamed "
        f"(the first keeps it): {'; '.join(shown)}. Give each paper its own ID to keep "
        "your own names.",
        PytacheckWarning,
        stacklevel=stacklevel,
    )


def resolve(papers: L, *, stacklevel: int = 3) -> L:
    """The papers of a list with distinct IDs (F6).

    Returns *papers* itself when no ID repeats. Otherwise a new list of the same kind:
    the papers that keep their ID are the very objects of *papers*, the renamed ones are
    shallow copies that share the tables of the original (nothing is read or built).
    Warns once with a :class:`~metacheck.core.errors.PytacheckWarning`.
    """
    old = [p.paper_id for p in papers]
    new = resolve_ids(old)
    if new == old:
        return papers
    items: list[Paper] = []
    for p, pid in zip(papers, new, strict=True):
        if pid != p.paper_id:
            p = p.copy(deep=False)
            p.paper_id = pid
        items.append(p)
    _warn(old, new, stacklevel)
    return PaperList(items) if isinstance(papers, PaperList) else items  # type: ignore[return-value]
