"""Grouped views of a Doc: ``text_search()``'s ``return = "paragraph" | "header" |
"section" | "paper_id"`` (docs/design/ARCHITECTURE.md §2.2, invariant V5).

R (``text_search.R``) pastes the searched sentences of each paragraph with a
space, keyed by section type, header, section, paragraph and paper in order of
first appearance; keeps the paragraphs whose *level* key has a matching
sentence (``semi_join``); pastes those per level key with the paragraph marker;
and cleans the joined text once. A section's header is the one its paragraphs
share (U80: metacheck gave ``NA``).

A :class:`GroupDoc` is that table for every group of one call's searched rows:
its rows are the groups, its stage-0 text the joined text before cleaning, so
its stage 1 is what the call returns and what the next call of a chain
matches. The groups a call returns are :meth:`GroupDoc.kept` of its matches. A
GroupDoc is itself a Doc, so a chain goes on from it with the same machinery.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from metacheck._r.regex import _as_str
from metacheck.core.doc import MARKER, Doc, bits

__all__ = ["GROUPS", "JOIN", "PARAGRAPH", "GroupDoc", "build_groups"]

#: the key of a paragraph
PARAGRAPH = ("section_type", "header", "section_id", "paragraph_id", "paper_id")
#: the key of each grouped return mode
GROUPS: dict[str, tuple[str, ...]] = {
    "paragraph": PARAGRAPH,
    "header": ("section_type", "header", "section_id", "paper_id"),
    "section": ("section_type", "section_id", "paper_id"),
    "paper_id": ("paper_id",),
}
#: what joins a group's paragraphs (read at each build)
JOIN = MARKER


class GroupDoc(Doc):
    """The *level* groups of one call's searched rows of *source*, as a Doc."""

    __slots__ = ("_series", "first", "group_of", "level", "source")

    _series: dict[str, pd.Series]
    first: list[int]  #: each group's first source row (in the call's row order)
    group_of: dict[int, int]  #: source row -> its group
    level: str
    source: Doc

    def kept(self, matched: int) -> int:
        """The groups with a matched source row (R's ``semi_join``)."""
        g = self.group_of
        out = 0
        for i in bits(matched):
            out |= 1 << g[i]
        return out

    def rows(self, idx: Sequence[int], stage: int = 1) -> pd.DataFrame:
        """Groups *idx* as the call returns them: the key columns typed as in the
        source, the others ``NA``, the text cleaned *stage* times."""
        idx = list(idx)
        field = self.field(stage)
        data: dict[str, pd.Series] = {}
        for c in self.columns:
            if c == "text":
                data[c] = pd.Series([field[i] for i in idx], dtype="string")
            elif c in self._series:
                data[c] = self._series[c].take(idx).reset_index(drop=True)
            else:
                data[c] = pd.Series([pd.NA] * len(idx), dtype=object)
        return pd.DataFrame(data)

    def frame(self) -> pd.DataFrame:
        """The table the call returns for every group (text at stage 1)."""
        frame = self._frame
        if frame is None:
            frame = self._frame = self.rows(range(self.n), 1)
        return frame

    def take(self, idx: Sequence[int]) -> pd.DataFrame:
        return self.frame().take(list(idx)).reset_index(drop=True)

    def take_series(self, c: str, idx: Sequence[int]) -> pd.Series:
        if c in self._series:
            return self._series[c].take(list(idx)).reset_index(drop=True)
        return pd.Series([pd.NA] * len(idx), dtype=object)

    def values(self, c: str) -> list[Any]:
        if c == "text":
            return self.field(1)
        if c in self._series:
            return self._series[c].tolist()
        return [pd.NA] * self.n

    def unique(self) -> bool:
        return True  # group keys are distinct, and every key column is an output column


def build_groups(d: Doc, level: str, rows: Sequence[int], stage: int) -> GroupDoc:
    """The *level* groups of *d*'s rows *rows* (in the call's row order), joining the
    text at *stage* (what the call searched)."""
    text = d.field(stage)
    cols = [d.key_values(c) for c in PARAGRAPH]
    pindex: dict[tuple[Any, ...], int] = {}
    pmembers: list[list[int]] = []
    pkeys: list[tuple[Any, ...]] = []
    for i in rows:
        key = tuple(col[i] for col in cols)
        j = pindex.get(key)
        if j is None:
            j = pindex[key] = len(pmembers)
            pmembers.append([])
            pkeys.append(key)
        pmembers[j].append(i)
    ptext = [" ".join(_or_na(text[i]) for i in m) for m in pmembers]

    if level == "paragraph":
        groups = [[j] for j in range(len(pmembers))]
    else:
        pos = [PARAGRAPH.index(c) for c in GROUPS[level]]
        lindex: dict[tuple[Any, ...], int] = {}
        groups = []
        for j, key in enumerate(pkeys):
            lk = tuple(key[p] for p in pos)
            k = lindex.get(lk)
            if k is None:
                k = lindex[lk] = len(groups)
                groups.append([])
            groups[k].append(j)

    group_of: dict[int, int] = {}
    for k, members in enumerate(groups):
        for j in members:
            for i in pmembers[j]:
                group_of[i] = k
    first = [pmembers[members[0]][0] for members in groups]

    series = {c: d.take_series(c, first) for c in GROUPS[level]}
    if level == "section":
        # U80: a section's header is its paragraphs' header when they all share one
        hdr = PARAGRAPH.index("header")
        headers = []
        for members in groups:
            found = {pkeys[j][hdr] for j in members}
            headers.append(next(iter(found)) if len(found) == 1 else None)
        series["header"] = pd.Series(headers, dtype=d.take_series("header", []).dtype)

    g = GroupDoc()
    g.source = d
    g.level = level
    g._series = series
    g.first = first
    g.group_of = group_of
    g.n = len(groups)
    g.columns = list(d.columns)
    g.paper_id = d.paper_id
    g.text = [JOIN.join(ptext[j] for j in members) for members in groups]
    g.header = [_as_str(v) for v in g.values("header")]
    g.section_type = g.values("section_type")
    g._finish()
    return g


def _or_na(s: str | None) -> str:
    """*s*, or ``"NA"`` (what ``paste()`` makes of a missing string)."""
    return "NA" if s is None else s
