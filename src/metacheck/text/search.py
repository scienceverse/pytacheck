"""``text_search()``: search the sentences of papers.

Port of ``R/text_search.R``, as a façade on the indexed document
(:mod:`metacheck.core.doc`). R's semantics are kept: pattern-major order for a
list of patterns, the ``exclude`` intersection, every ``return`` mode and R's
errors. A paper is indexed once (its Doc is cached on it) and each (pattern,
sentence) is tested at most once per Doc, however many searches ask. The
implementation before the core is frozen in ``tests/_legacy/search.py``, the
oracle of the differential tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import pandas as pd

from metacheck._r.frames import bind_rows
from metacheck._r.regex import RegexError, compile_r, regextract_all
from metacheck.core.doc import Doc, bits, merge_sections, sentence_frame
from metacheck.core.patterns import Pat
from metacheck.papers.model import Paper, PaperList, is_paper_list
from metacheck.papers.tables import paper_table

__all__ = ["search_text", "sentence_table", "text_search"]

ReturnType = Literal["sentence", "paragraph", "section", "header", "match", "paper_id"]
_RETURN_TYPES = ("sentence", "paragraph", "section", "header", "match", "paper_id")


def _is_papers(paper: Any) -> bool:
    return isinstance(paper, PaperList) or (is_paper_list(paper) and not isinstance(paper, str))


def _list_frame(paper: Any) -> pd.DataFrame:
    """A paper list's sentence table: its text tables with their sections merged."""
    return merge_sections(paper_table(paper, "text"), lambda: paper_table(paper, "section"))


def _strings(paper: Any) -> list[str] | None:
    """*paper* as character strings, or ``None`` when it is not a string or strings."""
    if isinstance(paper, str):
        return [paper]
    if isinstance(paper, Sequence) and all(isinstance(s, str) for s in paper):
        return list(paper)
    return None


def _not_a_paper() -> TypeError:
    return TypeError(
        "The paper argument doesn't seem to be a scivrs_paper object or a list of paper objects"
    )


def search_doc(paper: Any) -> tuple[Doc, bool]:
    """The Doc a search of *paper* searches, and whether *paper* was character strings.

    A paper's Doc is its cached one; a table, a paper list or strings get a Doc
    of their own. A Doc is searched as it is, so that several searches can share it.
    """
    if isinstance(paper, Doc):
        return paper, False
    if isinstance(paper, pd.DataFrame):
        return Doc.from_frame(paper.copy(deep=False)), False
    if isinstance(paper, Paper):
        return Doc.of(paper), False
    if _is_papers(paper):
        return Doc.from_frame(_list_frame(paper)), False
    strings = _strings(paper)
    if strings is None:
        raise _not_a_paper()
    return Doc.from_strings(strings), True


def sentence_table(paper: Any) -> tuple[pd.DataFrame, bool]:
    """The table :func:`text_search` searches (raw text), and whether *paper* was strings.

    The table of a paper or a paper list has each sentence's section ``header``
    and ``section_type``; a table is returned as it is (a shallow copy). The
    columns ``text_search()`` adds when they are missing are not added.
    """
    if isinstance(paper, pd.DataFrame):
        return paper.copy(deep=False), False
    if isinstance(paper, Paper):
        return sentence_frame(paper).copy(deep=False), False
    if _is_papers(paper):
        return _list_frame(paper), False
    strings = _strings(paper)
    if strings is None:
        raise _not_a_paper()
    return pd.DataFrame({"text": pd.Series(strings, dtype="string")}), True


class _Search:
    """One ``text_search()`` call's view of a Doc: the rows it searches and R's tables."""

    __slots__ = ("_rows", "doc", "from_papers", "is_vector", "within")

    def __init__(
        self, doc: Doc, is_vector: bool, include_refs: bool, from_papers: bool = False
    ) -> None:
        self.doc = doc
        self.is_vector = is_vector
        #: the rows are the text of a paper or paper list (not a table or strings given)
        self.from_papers = from_papers
        #: the searched rows: the references are skipped unless included
        self.within = doc.all if include_refs else doc.body
        self._rows: list[int] | None = None

    def searched(self) -> list[int]:
        """The searched rows, in table order."""
        if self._rows is None:
            self._rows = list(bits(self.within))
        return self._rows

    def matches(self, p: Pat, exclude: bool, header: bool) -> int:
        """The searched rows whose text (or header) matches *p* (or does not)."""
        d = self.doc
        m = d.mask(p, 0, self.within)
        if header:
            m |= d.header_mask(p, self.within)
        return self.within & ~m if exclude else m

    def sentences(self, rows: list[int]) -> pd.DataFrame:
        """The result of a sentence search that found *rows* (in result order)."""
        d = self.doc
        if not rows:
            return d.take([])
        result = d.rows(rows, 1)
        if d.distinct(rows):
            return result  # distinct() has nothing to drop
        return result.drop_duplicates().reset_index(drop=True)

    def search(self, p: Pat, return_: str, exclude: bool, header: bool) -> pd.DataFrame:
        """One pattern's result table (before a ``text`` column it added is dropped)."""
        matched = self.matches(p, exclude, header)
        found = list(bits(matched))
        if return_ == "sentence":
            return self.sentences(found)
        d = self.doc
        if return_ == "match":
            if self.from_papers:
                # V7 (deliberate; docs/UPSTREAM_ISSUES.md D60): a sentence repeated exactly
                # in a paper gives its matches once. metacheck's unique() skips this mode.
                found = d.first_rows(found, 0)
            texts = d.text
            hits = regextract_all(p.src, [texts[i] for i in found], p.icase, p.perl, p.fixed)
            result = d.take([i for i, h in zip(found, hits, strict=True) for _ in h])
            result["text"] = pd.Series([h for each in hits for h in each], dtype="string")
            return result
        # the grouped modes (V5): the groups of the searched rows with a match
        g = d.groups(return_, self.searched(), 0)
        kept = g.kept(matched)
        if not kept:
            return d.take([])
        return g.rows(list(bits(kept)), 1).drop_duplicates().reset_index(drop=True)

    def finish(self, result: pd.DataFrame) -> Any:
        """*result* as text_search() returns it: without an added ``text``, or as strings."""
        if "text" in self.doc.missing:
            result = result.drop(columns="text")
        if self.is_vector:
            return [None if pd.isna(t) else str(t) for t in result["text"].tolist()]
        return result


def _check_pattern(pattern: str, ignore_case: bool, perl: bool, fixed: bool) -> None:
    try:
        compile_r(pattern, ignore_case, perl, fixed)
    except RegexError as exc:
        raise ValueError(f"Check the pattern argument in '{pattern}':\n{exc}") from exc


def text_search(
    paper: Any,
    pattern: str | Sequence[str] = ".*",
    return_: ReturnType = "sentence",
    ignore_case: bool = True,
    fixed: bool = False,
    perl: bool = False,
    exclude: bool = False,
    search_header: bool = False,
    include_refs: bool = False,
) -> Any:
    """Search the text of a paper, a paper list, a previous result table, or strings.

    Parameters mirror metacheck's ``text_search()``; ``return_`` is R's
    ``return`` argument (``"sentence"``, ``"paragraph"``, ``"section"``,
    ``"header"``, ``"match"`` or ``"paper_id"``). Patterns are R regular
    expressions (TRE unless ``perl=True``). Several patterns are searched
    separately and their results combined (or intersected when
    ``exclude=True``). References are skipped unless ``include_refs=True``.

    Returns a DataFrame, or a list of strings when *paper* is a string or a
    sequence of strings.
    """
    if return_ not in _RETURN_TYPES:
        raise ValueError(f"'arg' should be one of {', '.join(repr(r) for r in _RETURN_TYPES)}")
    if fixed:
        ignore_case = False
    patterns = [pattern] if isinstance(pattern, str) else list(pattern)
    # R checks each pattern before it builds the table to search for it
    _check_pattern(patterns[0], ignore_case, perl, fixed)
    doc, is_vector = search_doc(paper)
    for src in patterns[1:]:
        _check_pattern(src, ignore_case, perl, fixed)
    s = _Search(doc, is_vector, include_refs, isinstance(paper, Paper) or _is_papers(paper))
    pats = [Pat.from_r(src, perl=perl, fixed=fixed, ignore_case=ignore_case) for src in patterns]

    if len(pats) == 1:
        return s.finish(s.search(pats[0], return_, exclude, search_header))

    if return_ == "sentence" and not exclude and not is_vector:
        # bind_rows() of each pattern's rows, then distinct(): pattern-major, first seen
        order: dict[int, None] = {}
        for p in pats:
            order.update(dict.fromkeys(bits(s.matches(p, False, search_header))))
        return s.finish(s.sentences(list(order)))

    parts = [s.finish(s.search(p, return_, exclude, search_header)) for p in pats]
    if is_vector:
        return _combine_vectors(parts, exclude)
    if exclude and len(parts) > 2:
        # dplyr::intersect(x, y, ...) on data frames: the dots must be empty
        extra = "\n".join(
            f"• ..{i} = <tibble[,{part.shape[1]}]>" for i, part in enumerate(parts[2:], start=1)
        )
        raise ValueError(
            f"`...` must be empty.\n✖ Problematic argument{'s' if len(parts) > 3 else ''}:"
            f"\n{extra}\nℹ Did you forget to name an argument?"
        )
    if exclude:
        result = parts[0]
        for part in parts[1:]:
            result = result.merge(part, how="inner")
        return result.drop_duplicates().reset_index(drop=True)
    return bind_rows(parts).drop_duplicates().reset_index(drop=True)


search_text = text_search


def _combine_vectors(parts: list[list[str | None]], exclude: bool) -> list[str | None]:
    """R's combination of the per-pattern results of a search of character strings.

    ``dplyr::bind_rows()`` refuses character vectors, so several patterns
    always fail unless ``exclude = TRUE``; then ``dplyr::intersect()`` is
    ``base::intersect(x, y)`` (unique strings of the first result that are in
    the second), which has no room for a third result.
    """
    if not exclude:
        raise ValueError("Argument 1 must be a data frame or a named atomic vector.")
    if len(parts) > 2:
        from metacheck.report.render import deparse

        extra = ", ".join("".join(deparse(part)) if part else "character(0)" for part in parts[2:])
        raise TypeError(f"unused argument{'s' if len(parts) > 3 else ''} ({extra})")
    second = set(parts[1])
    return [t for t in dict.fromkeys(parts[0]) if t in second]
