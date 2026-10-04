"""Patterns as values: :class:`Pat` and :class:`PatternSet`.

A :class:`Pat` is one of metacheck's R patterns, verbatim, with its dialect:
``"tre"`` (R's default engine), ``"pcre"`` (``perl = TRUE``) or ``"fixed"``
(``fixed = TRUE``), and whether case is ignored. Patterns are compared by value,
so equal patterns share their cached results in every module that searches the
same Doc.

Nothing here compiles a pattern itself. Detection goes through
:func:`metacheck._r.regex.detector`, so a match is exactly ``grepl()``'s, and the
literals a match needs come from :func:`metacheck._r.regex.required_literals`
(empty with ``METACHECK_LITERALS=off``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from metacheck._r.regex import CNF, Detector, detector, required_literals
from metacheck._r.regex import detect_many as _detect_many

__all__ = ["Dialect", "Pat", "PatternSet", "as_patterns", "detect_many", "patterns"]

Dialect = Literal["tre", "pcre", "fixed"]


@dataclass(frozen=True, slots=True)
class Pat:
    """One R pattern: its source, its dialect and whether case is ignored."""

    src: str
    dialect: Dialect = "tre"
    icase: bool = True

    @classmethod
    def from_r(
        cls, pattern: str, *, perl: bool = False, fixed: bool = False, ignore_case: bool = True
    ) -> Pat:
        """The pattern ``grepl(pattern, x, ignore.case, perl, fixed)`` looks for.

        ``fixed = TRUE`` wins over ``perl`` and makes the match case sensitive, as
        ``text_search()`` does.
        """
        if fixed:
            return cls(pattern, "fixed", False)
        return cls(pattern, "pcre" if perl else "tre", ignore_case)

    @property
    def perl(self) -> bool:
        """Whether the pattern is a PCRE pattern (``perl = TRUE``)."""
        return self.dialect == "pcre"

    @property
    def fixed(self) -> bool:
        """Whether the pattern is a literal string (``fixed = TRUE``)."""
        return self.dialect == "fixed"

    def detector(self) -> Detector:
        """``grepl()`` for one string at a time (compiled on its first candidate)."""
        return detector(self.src, self.icase, self.perl, self.fixed)

    def literals(self) -> CNF:
        """Clauses of casefolded pieces, one of each clause in every match's folded text.

        Empty when nothing is known, for a fixed pattern (its detector is a plain
        substring test) and while the prefilter is switched off.
        """
        if self.fixed:
            return ()
        return required_literals(self.src, self.perl, self.icase)


@dataclass(frozen=True, slots=True)
class PatternSet:
    """An ordered any-of: each pattern is searched for on its own, in order.

    It is never compiled into one alternation: the patterns of a list keep their
    order, which ``text_search()``'s results follow (pattern by pattern).
    """

    name: str
    pats: tuple[Pat, ...]

    def __iter__(self) -> Iterator[Pat]:
        return iter(self.pats)

    def __len__(self) -> int:
        return len(self.pats)


def patterns(
    *srcs: str | Pat | Iterable[str | Pat],
    dialect: Dialect = "tre",
    icase: bool = True,
    name: str = "",
) -> PatternSet:
    """A :class:`PatternSet` of R patterns (strings in *dialect*, or :class:`Pat` values)."""
    out: list[Pat] = []
    for s in srcs:
        if isinstance(s, Pat):
            out.append(s)
        elif isinstance(s, str):
            out.append(Pat(s, dialect, icase))
        else:
            out.extend(p if isinstance(p, Pat) else Pat(p, dialect, icase) for p in s)
    return PatternSet(name, tuple(out))


def as_patterns(x: Pat | PatternSet) -> tuple[Pat, ...]:
    """The patterns of *x*, in order."""
    return x.pats if isinstance(x, PatternSet) else (x,)


def detect_many(pats: Sequence[Pat], text: str | None) -> list[bool]:
    """Whether *text* matches each pattern (one text, many patterns: a codebook scan).

    The text is folded once and each pattern runs only when its required
    literals are in it (:func:`metacheck._r.regex.detect_many`); patterns with
    the same flags are tested together.
    """
    out: list[bool] = [False] * len(pats)
    groups: dict[tuple[bool, bool, bool], list[int]] = {}
    for k, p in enumerate(pats):
        groups.setdefault((p.icase, p.perl, p.fixed), []).append(k)
    for (icase, perl, fixed), ks in groups.items():
        found = _detect_many([pats[k].src for k in ks], text, icase, perl, fixed)
        for k, hit in zip(ks, found, strict=True):
            out[k] = hit
    return out
