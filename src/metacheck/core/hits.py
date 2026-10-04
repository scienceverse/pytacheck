"""``Hits``: the rows a search chain found, one bitset per Doc.

metacheck's chains map one to one:

==========================================================  ==================================
``text_search(paper, P)`` (references skipped, raw text)     ``Docs.of(paper).hits(P)``
``text_search(text_search(paper, A), B)``                    ``.hits(A).where(B)``: B matches
                                                             the text the first call returned
                                                             (cleaned once), exactly
``text_search(x, X, exclude = TRUE)``                         ``.without(X)``
``text_search(x, P, return = "paragraph")`` (and the other    ``.where(P).group("paragraph")``
grouped modes)
==========================================================  ==================================

:meth:`Hits.table` is the table the last call returns.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from metacheck._values import is_missing
from metacheck.core.doc import bits, empty_text_frame
from metacheck.core.patterns import Pat, PatternSet, as_patterns

if TYPE_CHECKING:
    from metacheck.core.doc import Doc, Docs

__all__ = ["Hits", "Ordered"]

Order = tuple[dict[int, Any], ...]


@dataclass(frozen=True, slots=True, eq=False)
class Hits:
    """The rows found in each Doc of *docs*; the next call matches their text cleaned
    *stage* times.

    A Hits is the table one ``text_search()`` call of a chain returns. Besides
    its rows it keeps what the grouped modes and the chain's row order need:

    * *order*: per Doc, each row's position in R's table (``None``: Doc order,
      then row order). A list of patterns makes the table pattern-major
      (``bind_rows()`` of each pattern's rows); the order carries through.
    * *universe*, *uorder*: the rows the call searched (its input table) and
      their order: a grouped mode joins all of them, matched or not.
    * *empty_from*: the Docs whose empty table is the call's input table, the
      shape R returns when nothing matched (``ft[c(), ]``).
    * *ranks*, *call_ranks*: per Doc, the first pattern of the first call's (or
      of this call's) list that matched each row (``None``: one pattern). A
      grouped mode of a list is pattern-major too: R runs the call once per
      pattern and binds the grouped tables.
    * *excluded*: this call excluded a list of patterns. R then intersects the
      per-pattern tables, which a grouped mode cannot follow:
      :meth:`group` refuses it (the façade does the intersection itself).
    """

    docs: Docs
    masks: tuple[int, ...]
    stage: int = 1
    ranks: tuple[dict[int, int], ...] | None = None
    order: Order | None = None
    universe: tuple[int, ...] | None = None
    uorder: Order | None = None
    empty_from: Docs | None = None
    call_ranks: tuple[dict[int, int], ...] | None = None
    excluded: bool = False

    @classmethod
    def first(
        cls, docs: Docs, ps: Pat | PatternSet, *, include_refs: bool = False, header: bool = False
    ) -> Hits:
        """``text_search(paper, ps)``: rows whose raw text (or header) matches *ps*."""
        ranks: list[dict[int, int]] = []
        masks: list[int] = []
        universe: list[int] = []
        for d in docs:
            rank: dict[int, int] = {}
            within = d.all if include_refs else d.body
            universe.append(within)
            masks.append(d.any(ps, 0, within, header, rank))
            ranks.append(rank)
        order: Order | None = None
        if len(as_patterns(ps)) > 1:  # pattern-major: bind_rows() of each pattern's rows
            keyed = sorted((ranks[k].get(i, 0), k, i) for k, m in enumerate(masks) for i in bits(m))
            order_l: list[dict[int, Any]] = [{} for _ in masks]
            for n, (_, k, i) in enumerate(keyed):
                order_l[k][i] = n
            order = tuple(order_l)
        return cls(
            docs,
            _dedup(docs, tuple(masks), order, 1),
            1,
            tuple(ranks),
            order,
            tuple(universe),
            None,
            docs,
            None if order is None else tuple(ranks),
        )

    def _searched(self, include_refs: bool) -> tuple[int, ...]:
        """The rows the next call searches: this table, references dropped unless asked."""
        if include_refs:
            return self.masks
        return tuple(m & d.body for d, m in zip(self.docs, self.masks, strict=True))

    def _next(
        self,
        searched: tuple[int, ...],
        masks: tuple[int, ...],
        order: Order | None,
        call_ranks: tuple[dict[int, int], ...] | None = None,
        excluded: bool = False,
    ) -> Hits:
        """The next call's table: rows *masks* of *searched*, cleaned once more."""
        stage = self.stage + 1
        return Hits(
            self.docs,
            _dedup(self.docs, masks, order, stage),
            stage,
            self.ranks,
            order,
            searched,
            self.order,
            self.docs if any(self.masks) else self.empty_from,
            call_ranks,
            excluded,
        )

    def where(
        self, ps: Pat | PatternSet, *, header: bool = False, include_refs: bool = False
    ) -> Hits:
        """Keep the rows whose text at this stage (or header) also matches *ps*."""
        searched = self._searched(include_refs)
        if len(as_patterns(ps)) <= 1:
            masks = tuple(
                d.any(ps, self.stage, m, header) if m else 0
                for d, m in zip(self.docs, searched, strict=True)
            )
            return self._next(searched, masks, self.order)
        # several patterns: bind_rows() of each pattern's rows, then distinct()
        ranks: list[dict[int, int]] = []
        out = []
        for d, m in zip(self.docs, searched, strict=True):
            rank: dict[int, int] = {}
            out.append(d.any(ps, self.stage, m, header, rank) if m else 0)
            ranks.append(rank)
        keyed = [((ranks[k][i], self._pos(k, i)), k, i) for k, m in enumerate(out) for i in bits(m)]
        keyed.sort(key=lambda x: x[0])
        order: list[dict[int, Any]] = [{} for _ in out]
        for n, (_, k, i) in enumerate(keyed):
            order[k][i] = n
        return self._next(searched, tuple(out), tuple(order), tuple(ranks))

    def without(
        self, ps: Pat | PatternSet, *, header: bool = False, include_refs: bool = False
    ) -> Hits:
        """Drop the rows whose text at this stage (or header) matches *ps* (``exclude = TRUE``).

        R intersects the tables of two patterns with ``dplyr::intersect()``, which
        takes no third: more than two patterns raise, as in R.
        """
        pats = as_patterns(ps)
        if len(pats) > 2:
            raise ValueError("`...` must be empty: exclude intersects the tables of two patterns")
        searched = self._searched(include_refs)
        masks = tuple(
            m & ~d.any(ps, self.stage, m, header) if m else 0
            for d, m in zip(self.docs, searched, strict=True)
        )
        return self._next(searched, masks, self.order, excluded=len(pats) > 1)

    def match(self, ps: Pat | PatternSet) -> tuple[int, ...]:
        """Per Doc, the rows that match *ps* at this stage (a flag, not a new call)."""
        return tuple(
            d.any(ps, self.stage, m) if m else 0 for d, m in zip(self.docs, self.masks, strict=True)
        )

    # -- grouped return modes ----------------------------------------------------------

    def group(self, level: str) -> Hits:
        """``return = level`` of the call that found these rows (V5): its searched rows
        grouped (``"paragraph"``, ``"header"``, ``"section"`` or ``"paper_id"``), the
        groups with a match kept, their text joined and cleaned once."""
        from metacheck.core.doc import Docs as _Docs

        if self.universe is None:
            raise ValueError("a grouped mode applies to the rows of one search call")
        if self.excluded:
            raise ValueError(
                "a grouped mode of a call that excludes a list of patterns intersects "
                "the per-pattern tables; exclude one pattern at a time"
            )
        gdocs = []
        masks = []
        uorder, cranks = self.uorder, self.call_ranks
        keyed: list[tuple[Any, int, int]] = []
        for k, (d, m, u) in enumerate(zip(self.docs, self.masks, self.universe, strict=True)):
            rows = list(bits(u))
            if uorder is not None:
                rows.sort(key=uorder[k].__getitem__)
            g = d.groups(level, rows, self.stage - 1)
            gdocs.append(g)
            kept = g.kept(m) if m else 0
            masks.append(kept)
            if (uorder is not None or cranks is not None) and kept:
                # each group at its first row's place in the input table; with a list
                # of patterns, after the groups of every earlier pattern
                grank: dict[int, int] = {}
                if cranks is not None:
                    for i in bits(m):
                        j = g.group_of[i]
                        r = cranks[k].get(i, 0)
                        if r < grank.get(j, r + 1):
                            grank[j] = r
                for j in bits(kept):
                    first = g.first[j]
                    upos = (k, first) if uorder is None else uorder[k][first]
                    keyed.append(((grank.get(j, 0), upos), k, j))
        order: Order | None = None
        if uorder is not None or cranks is not None:
            keyed.sort(key=lambda x: x[0])
            order_l: list[dict[int, Any]] = [{} for _ in masks]
            for n, (_, k, j) in enumerate(keyed):
                order_l[k][j] = n
            order = tuple(order_l)
        return Hits(
            _Docs.wrap(gdocs, self.docs.papers),
            tuple(masks),
            1,
            None,
            order,
            None,
            None,
            self.empty_from,
        )

    def paragraphs(self) -> Hits:
        """``return = "paragraph"``."""
        return self.group("paragraph")

    def sections(self) -> Hits:
        """``return = "section"`` (a section's header: its paragraphs' shared one, U80)."""
        return self.group("section")

    # -- rows --------------------------------------------------------------------------

    def _pos(self, k: int, i: int) -> Any:
        return (k, i) if self.order is None else self.order[k][i]

    def table_rows(self) -> list[tuple[Doc, int]]:
        """``(doc, row)`` in the order of R's table (see *order*)."""
        if self.order is None:
            return list(self)
        keyed = [(self.order[k][i], k, i) for k, m in enumerate(self.masks) for i in bits(m)]
        keyed.sort()
        return [(self.docs[k], i) for _, k, i in keyed]

    def table(self) -> pd.DataFrame:
        """The table the chain's last call returns: its rows in R's order, the text at
        the chain's stage; with no rows, the call's input table emptied."""
        rows = self.table_rows()
        if not rows:
            src = self.empty_from
            if src is None or len(src) == 0:
                return empty_text_frame()
            template = src.template()
            out = template if template is not None else src[0].take([])
            return _drop_text(out, src)
        stage = self.stage
        frames = []
        run_doc: Any = None
        run: list[int] = []
        for d, i in rows:
            if d is not run_doc and run:
                frames.append(run_doc.rows(run, stage))
                run = []
            run_doc = d
            run.append(i)
        frames.append(run_doc.rows(run, stage))
        template = self.docs.template()
        if template is not None:
            frames = [template, *frames]
        if len(frames) == 1:
            out = frames[0]
        else:
            from metacheck._r.frames import bind_rows

            out = bind_rows(frames)
        return _drop_text(out.reset_index(drop=True), self.docs)

    def ordered(self, levels: Sequence[str] | None = None) -> Ordered:
        """``arrange(factor(paper_id, levels), text_id)`` of the table, ``NA`` last.

        *levels* default to the IDs of the papers (:meth:`Docs.paper_ids`). The sort is
        stable over R's order of the table. Each row is labelled with its paper's ID
        when that is a level (else ``None``). The rows are the table's: a row identical
        to an earlier one of its paper was dropped by the call that found it (V7), and
        papers have distinct IDs (F6), so nothing else is dropped here.
        """
        names = list(self.docs.paper_ids()) if levels is None else list(levels)
        level = {p: k for k, p in enumerate(names)}
        inf = float("inf")
        rows: list[tuple[Any, ...]] = []
        for k, (d, m) in enumerate(zip(self.docs, self.masks, strict=True)):
            if not m:
                continue
            pid = d.paper_id
            lv = level.get(pid) if isinstance(pid, str) else None
            text_id = d.values("text_id")
            for i in bits(m):
                t = None if is_missing(text_id[i]) else text_id[i]
                rows.append(
                    (
                        inf if lv is None else lv,
                        t is None,
                        0 if t is None else t,
                        self._pos(k, i),
                        d,
                        i,
                        None if lv is None else pid,
                    )
                )
        rows.sort(key=lambda r: r[:4])
        return Ordered([(r[4], r[5], r[6]) for r in rows], self.stage, names)

    def statements(self, levels: Sequence[str] | None = None) -> dict[str, list[str | None]]:
        """``list(unique(text))`` per paper: :meth:`ordered`'s distinct texts by paper ID."""
        return self.ordered(levels).statements()

    def _combine(self, other: Hits, op: Any) -> Hits:
        if other.docs is not self.docs or other.stage != self.stage:
            raise ValueError("combine hits of the same documents and stage")
        return Hits(
            self.docs,
            tuple(op(a, b) for a, b in zip(self.masks, other.masks, strict=True)),
            self.stage,
            None,
            self.order,
            self.universe,
            self.uorder,
            self.empty_from,
        )

    def __or__(self, other: Hits) -> Hits:
        return self._combine(other, lambda a, b: a | b)

    def __and__(self, other: Hits) -> Hits:
        return self._combine(other, lambda a, b: a & b)

    def __sub__(self, other: Hits) -> Hits:
        return self._combine(other, lambda a, b: a & ~b)

    def __bool__(self) -> bool:
        return any(self.masks)

    def __len__(self) -> int:
        return sum(m.bit_count() for m in self.masks)

    def __iter__(self) -> Iterator[tuple[Doc, int]]:
        for d, m in zip(self.docs, self.masks, strict=True):
            for i in bits(m):
                yield d, i


def _drop_text(frame: pd.DataFrame, docs: Docs) -> pd.DataFrame:
    """*frame* without the ``text`` column ``text_search()`` added to a table without one."""
    if len(docs) and all("text" in d.missing for d in docs) and "text" in frame.columns:
        return frame.drop(columns="text")
    return frame


def _dedup(docs: Docs, masks: tuple[int, ...], order: Order | None, stage: int) -> tuple[int, ...]:
    """``distinct()`` of a call's table: a later row identical to an earlier one is dropped (V7)."""
    out = list(masks)
    for k, (d, m) in enumerate(zip(docs, masks, strict=True)):
        if not m or d.unique():
            continue
        rows = list(bits(m))
        if order is not None:
            rows.sort(key=order[k].__getitem__)
        out[k] = sum(1 << i for i in d.first_rows(rows, stage))
    return tuple(out)


class Ordered:
    """Rows in output order: ``(doc, row, label)``, the text at *stage*.

    The label is the paper's ID when it is one of the levels the rows were ordered
    by, else ``None`` (``as.character()`` of a factor gives ``NA`` for a value that
    is not a level).
    """

    __slots__ = ("levels", "rows", "stage")

    def __init__(self, rows: list[tuple[Doc, int, str | None]], stage: int, levels: list[str]):
        self.rows = rows
        self.stage = stage
        self.levels = levels

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[tuple[Doc, int, str | None]]:
        return iter(self.rows)

    def texts(self) -> list[str | None]:
        """The text of each row, at the stage of the call that found them."""
        return [d.field(self.stage)[i] for d, i, _ in self.rows]

    def statements(self) -> dict[str, list[str | None]]:
        """``list(unique(text))`` per paper with rows, in output order.

        Rows whose paper is not a level have no label and are left out; a text that
        repeats in one paper (in two places) is listed once.
        """
        out: dict[str, dict[str | None, None]] = {}
        for d, i, label in self.rows:
            if label is not None:
                out.setdefault(label, {})[d.field(self.stage)[i]] = None
        return {k: list(v) for k, v in out.items()}
