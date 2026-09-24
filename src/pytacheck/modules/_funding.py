"""rtransparent funding-statement detection (helpers of ``inst/modules/funding_check.R``).

metacheck's ``funding_check`` module embeds a copy of the funding detector of
`rtransparent <https://github.com/serghiou/rtransparent>`_: a dictionary of
synonyms (:func:`_create_synonyms`), small pattern builders (:func:`_encase`,
:func:`_bound`, ...), ~40 ``get_*`` sentence locators, some negation helpers
and text cleaners (``obliterate_*``, unused by the module but ported for
completeness), and :func:`rtransparent_funding`, which combines them for one
paper. This file is not a module (it starts with ``_``).

Conventions of the port:

* the patterns are built with the same string operations as R, so they are
  byte-identical to R's (``tests/mod_funding`` checks this against R), and
  run through :mod:`pytacheck._r` with R's engine (PCRE unless R uses TRE);
* ``article`` is a character vector (a string or a sequence of strings);
  the ``get_*`` locators return **0-based** positions (R's ``grep()`` values
  minus one), including positions one or two past the end, which R's
  ``c(a, a + 1)`` / ``c(a, a + 2)`` can produce; an out-of-range position is
  ``NA`` when indexed, as in R;
* R's ``if (!is.na(article[a + 1]))`` fails when several lines match a title
  pattern (``the condition has length > 1``); the port raises the same error;
* for speed on paper lists, the module wraps the whole text column in an
  :class:`_Article` whose regex matches are computed once for all papers and
  shared by the per-paper views (every locator matches sentence by sentence,
  so this gives exactly R's per-paper results);
* before running a PCRE pattern, :class:`_Article` skips the sentences that
  lack the literal text every match needs (:func:`_required`, looked up in a
  word index of the column): a necessary condition only, so results are
  unchanged (``tests/mod_funding`` compares against full scans).

The ``obliterate_*`` cleaners call ``stringr::str_replace_all()`` (the ICU
engine) in R; their patterns are written here with ICU's Unicode classes
spelled out (``\\s`` is White_Space, ``[[:punct:]]`` is ``\\p{P}``, ``.``
excludes all line terminators) and run through the PCRE helpers.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from functools import cache
from typing import Any

import numpy as np

from pytacheck._r.regex import grepl, gsub

__all__ = [
    "get_acknow_1",
    "get_acknow_2",
    "get_authors_1",
    "get_authors_2",
    "get_common_1",
    "get_common_2",
    "get_common_3",
    "get_common_4",
    "get_common_5",
    "get_developed_1",
    "get_disclosure_1",
    "get_disclosure_2",
    "get_financial_1",
    "get_financial_2",
    "get_financial_3",
    "get_french_1",
    "get_fund_1",
    "get_fund_2",
    "get_fund_3",
    "get_fund_acknow",
    "get_fund_acknow_new",
    "get_grant_1",
    "get_project_acknow",
    "get_received_1",
    "get_received_2",
    "get_recipient_1",
    "get_support_1",
    "get_support_2",
    "get_support_3",
    "get_support_4",
    "get_support_5",
    "get_support_6",
    "get_support_7",
    "get_support_8",
    "get_support_9",
    "get_support_10",
    "get_supported_1",
    "get_thank_1",
    "get_thank_2",
    "negate_absence_1",
    "negate_conflict_1",
    "negate_disclosure_1",
    "negate_disclosure_2",
    "obliterate_conflict_1",
    "obliterate_disclosure_1",
    "obliterate_fullstop_1",
    "rtransparent_funding",
]


# ---------------------------------------------------------------------------
# Literal prefilter: skip the regex engine for sentences that cannot match
# ---------------------------------------------------------------------------


def _class_end(p: str, i: int) -> int:
    """Index after the ``]`` closing the character class opened at ``p[i]``."""
    j = i + 1
    if j < len(p) and p[j] == "^":
        j += 1
    if j < len(p) and p[j] == "]":
        j += 1
    while j < len(p):
        c = p[j]
        if c == "\\":
            j += 2
            continue
        if c == "[" and p.startswith("[:", j):
            end = p.find(":]", j + 2)
            if end != -1:
                j = end + 2
                continue
        if c == "]":
            return j + 1
        j += 1
    raise ValueError(f"unterminated character class in {p!r}")


def _group_end(p: str, i: int) -> int:
    """Index after the ``)`` closing the group opened at ``p[i]``."""
    depth = 0
    j = i
    while j < len(p):
        c = p[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            j = _class_end(p, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError(f"unbalanced parentheses in {p!r}")


def _split_top(p: str) -> list[str]:
    """Split a PCRE pattern on its top-level ``|``."""
    out: list[str] = []
    start = 0
    j = 0
    while j < len(p):
        c = p[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            j = _class_end(p, j)
            continue
        if c == "(":
            j = _group_end(p, j)
            continue
        if c == "|":
            out.append(p[start:j])
            start = j + 1
        j += 1
    out.append(p[start:])
    return out


def _brace_bounds(p: str, i: int) -> tuple[int, int] | None:
    """``(min, index after "}")`` for a ``{m}`` / ``{m,}`` / ``{m,n}`` quantifier at ``p[i]``."""
    end = p.find("}", i)
    if end == -1:
        return None
    lo, _, hi = p[i + 1 : end].partition(",")
    if not lo.isdigit() or not (hi == "" or hi.isdigit()):
        return None
    return int(lo), end + 1


def _quantifier(p: str, i: int) -> tuple[int, int, bool]:
    """``(min, next index, quantified)`` for the quantifier (if any) at ``p[i]``."""
    if i >= len(p):
        return 1, i, False
    c = p[i]
    if c in "?*":
        qmin, i = 0, i + 1
    elif c == "+":
        qmin, i = 1, i + 1
    elif c == "{" and (bounds := _brace_bounds(p, i)) is not None:
        qmin, i = bounds
    else:
        return 1, i, False
    if i < len(p) and p[i] in "?+":  # lazy / possessive
        i += 1
    return qmin, i, True


def _best(conj: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    """The most selective requirement of a conjunction (longest shortest literal)."""
    if not conj:
        return None
    return max(conj, key=lambda d: min(len(x) for x in d))


def _required(p: str) -> list[tuple[str, ...]]:
    """Literals any match of PCRE pattern *p* must contain.

    A conjunction of disjunctions: every match contains, for each tuple, at
    least one of its (casefolded) strings. Only plain literal characters
    count; anything else (classes, escapes like ``\\b``, lookarounds, inline
    flags, optional or repeated atoms) just ends a literal run, so the
    requirement is always necessary (never too strict).
    """
    branches = _split_top(p)
    if len(branches) > 1:
        union: list[str] = []
        for b in branches:
            best = _best(_required(b))
            if best is None:
                return []
            union.extend(best)
        return [tuple(dict.fromkeys(union))]

    conj: list[tuple[str, ...]] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            conj.append(("".join(run).casefold(),))
            run.clear()

    i, n = 0, len(p)
    while i < n:
        c = p[i]
        inner: str | None = None
        char: str | None = None
        if c == "\\":
            e = p[i + 1]
            i += 2
            if not e.isalnum():
                char = e
        elif c == "[":
            i = _class_end(p, i)
        elif c == "(":
            j = _group_end(p, i)
            body = p[i + 1 : j - 1]
            if not body.startswith("?"):
                inner = body
            elif body.startswith("?:"):
                inner = body[2:]
            i = j
        elif c in ".^$":
            i += 1
        else:
            char = c
            i += 1
        qmin, i, quantified = _quantifier(p, i)
        if char is not None and not quantified:
            run.append(char)
            continue
        if char is not None and qmin >= 1:
            run.append(char)
        flush()
        if inner is not None and qmin >= 1:
            conj.extend(_required(inner))
    flush()
    return conj


@cache
def _prefilter(pattern: str) -> tuple[tuple[str, ...], ...]:
    """:func:`_required` for a PCRE pattern (cached; ``()`` when nothing is known)."""
    try:
        return tuple(dict.fromkeys(_required(pattern)))
    except (ValueError, IndexError):  # pragma: no cover - unparsable: no prefilter
        return ()


#: shorter literals ("by", "id") are too common to make a useful prefilter
_MIN_LITERAL = 3


@cache
def _prefilter_pieces(pattern: str) -> tuple[tuple[str, ...], ...]:
    """:func:`_prefilter` for word lookups: each literal becomes its longest
    whitespace-free piece; requirements with a short piece are dropped."""
    out: list[tuple[str, ...]] = []
    for disj in _prefilter(pattern):
        pieces = [max(lit.split(), key=len, default="") for lit in disj]
        if min(len(p) for p in pieces) >= _MIN_LITERAL:
            out.append(tuple(dict.fromkeys(pieces)))
    return tuple(out)


# ---------------------------------------------------------------------------
# Character vectors with shared, lazily computed regex matches
# ---------------------------------------------------------------------------

_Key = tuple[str, bool, bool]

#: R's PCRE2 does not match Turkish ``İ`` (U+0130) / ``ı`` (U+0131) caselessly
#: with ``i`` / ``I`` (they have no simple case folding), the `regex` engine
#: does. For caseless PCRE patterns they are replaced by ``×`` (U+00D7), which
#: every construct of these (ASCII) patterns treats alike (``.`` and negated
#: classes match it; ``\\w``, ``\\s``, ``\\d``, ``\\b``, ``[[:alnum:]]`` and
#: literals do not).
_DOTTED_I = str.maketrans({"\u0130": "\u00d7", "\u0131": "\u00d7"})


def _is_posix_only_class(body: str) -> bool:
    """Is a class body (between ``[`` and ``]``) made only of POSIX classes and
    ``\\w``-like escapes (the members PCRE2 does not case-fold)?

    ``[:^upper:]`` / ``[:^lower:]`` are left to the engine (not scoped)."""
    j = 1 if body.startswith("^") else 0
    if j >= len(body):
        return False
    while j < len(body):
        if body.startswith("[:", j):
            end = body.find(":]", j + 2)
            if end == -1 or body[j + 2 : end] in ("^upper", "^lower"):
                return False
            j = end + 2
        elif body[j] == "\\" and j + 1 < len(body) and body[j + 1] in "wdshv":
            j += 2
        else:
            return False
    return True


@cache
def _caseless_pcre(pattern: str) -> str:
    """*pattern* with ``\\w`` / ``\\W`` and POSIX-only classes scoped case-sensitive.

    In PCRE2, caseless matching folds literals and explicit class members
    (``[a-z]`` matches ``ſ`` and the Kelvin sign) but not ``\\w``, ``\\W`` or
    ``[[:alnum:]]``; the `regex` translation of those is an explicit ASCII
    class, which it would fold. ``(?-i:...)`` gives PCRE's meaning in both
    engines. Caseless ``[:upper:]`` and ``[:lower:]`` match every ASCII letter
    in PCRE2 (and still not ``ſ`` or the Kelvin sign): they become
    ``[:alpha:]``.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            e = pattern[i + 1]
            out.append(f"(?-i:\\{e})" if e in "wW" else pattern[i : i + 2])
            i += 2
        elif c == "[":
            j = _class_end(pattern, i)
            cls = pattern[i:j]
            if _is_posix_only_class(cls[1:-1]):
                cls = cls.replace("[:upper:]", "[:alpha:]").replace("[:lower:]", "[:alpha:]")
                cls = f"(?-i:{cls})"
            out.append(cls)
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


class _Article:
    """A character vector ``texts[start:stop]`` whose regex matches are cached.

    Several articles (the papers of a table) can view one column and share
    its cache, so each pattern runs once over the whole column.
    """

    __slots__ = ("_cache", "_texts", "start", "stop")

    def __init__(
        self,
        texts: list[str | None],
        start: int = 0,
        stop: int | None = None,
        cache: dict[Any, np.ndarray] | None = None,
    ) -> None:
        self._texts = texts
        self.start = start
        self.stop = len(texts) if stop is None else stop
        self._cache: dict[Any, np.ndarray] = {} if cache is None else cache

    def __len__(self) -> int:
        return self.stop - self.start

    def _vocabulary(self) -> tuple[str, list[int], list[list[int]]]:
        """An index of the column's casefolded words (whitespace-separated chunks).

        Returns the distinct words joined by NULs, where each word starts in
        that string, and the rows each word occurs in.
        """
        vocab = self._cache.get("vocabulary")
        if vocab is None:
            index: dict[str, list[int]] = {}
            for row, text in enumerate(self._texts):
                if text is None:
                    continue
                for word in set(text.casefold().split()):
                    rows = index.get(word)
                    if rows is None:
                        index[word] = [row]
                    else:
                        rows.append(row)
            starts: list[int] = []
            pos = 0
            for word in index:
                starts.append(pos)
                pos += len(word) + 1
            vocab = ("\0".join(index), starts, list(index.values()))
            self._cache["vocabulary"] = vocab  # type: ignore[assignment]
        return vocab  # type: ignore[return-value]

    def _literal_mask(self, piece: str) -> np.ndarray:
        """Which texts contain *piece* (casefolded, no whitespace) once casefolded.

        A whitespace-free string lies within one whitespace-separated word, so
        the words of the vocabulary that contain it give the rows.
        """
        key = ("literal", piece)
        mask = self._cache.get(key)
        if mask is None:
            joined, starts, postings = self._vocabulary()
            n = len(starts)
            mask = np.zeros(len(self._texts), dtype=bool)
            pos = joined.find(piece)
            while pos != -1:
                k = bisect_right(starts, pos) - 1
                mask[postings[k]] = True
                if k + 1 >= n:
                    break
                pos = joined.find(piece, starts[k + 1])
            self._cache[key] = mask
        return mask

    def _candidates(self, pattern: str, perl: bool) -> np.ndarray | None:
        """Rows that can match *pattern* (``None``: all rows)."""
        if not perl:
            return None
        cand: np.ndarray | None = None
        for pieces in _prefilter_pieces(pattern):
            m = self._literal_mask(pieces[0])
            for piece in pieces[1:]:
                m = m | self._literal_mask(piece)
            cand = m if cand is None else cand & m
        return cand

    def _dotted_i_rows(self) -> np.ndarray:
        """Rows containing a Turkish ``İ`` / ``ı`` (see :data:`_DOTTED_I`)."""
        rows = self._cache.get("dotted_i")
        if rows is None:
            rows = np.fromiter(
                (
                    i
                    for i, t in enumerate(self._texts)
                    if t is not None and ("İ" in t or "ı" in t)
                ),
                dtype=np.intp,
            )
            self._cache["dotted_i"] = rows
        return rows

    def _column_mask(self, pattern: str, ignore_case: bool, perl: bool) -> np.ndarray:
        key = (pattern, ignore_case, perl)
        mask = self._cache.get(key)
        if mask is None:
            texts = self._texts
            cand = self._candidates(pattern, perl)
            caseless = perl and (ignore_case or "(?i)" in pattern)
            run = _caseless_pcre(pattern) if caseless else pattern
            if cand is None:
                hits = grepl(run, texts, ignore_case=ignore_case, perl=perl)
                mask = np.fromiter(hits, dtype=bool, count=len(texts))
            else:
                mask = np.zeros(len(texts), dtype=bool)
                rows = np.flatnonzero(cand)
                if len(rows):
                    hits = grepl(run, [texts[i] for i in rows], ignore_case=ignore_case, perl=perl)
                    mask[rows] = np.fromiter(hits, dtype=bool, count=len(rows))
            if caseless:
                # PCRE2's caseless matching leaves U+0130/U+0131 alone; the
                # `regex` engine folds them to i/I: rematch those rows without them
                rows = self._dotted_i_rows()
                if cand is not None:
                    rows = rows[cand[rows]]
                if len(rows):
                    hits = grepl(
                        run,
                        [texts[i].translate(_DOTTED_I) for i in rows],  # type: ignore[union-attr]
                        ignore_case=ignore_case,
                        perl=perl,
                    )
                    mask[rows] = np.fromiter(hits, dtype=bool, count=len(rows))
            self._cache[key] = mask
        return mask

    def mask(self, pattern: str, ignore_case: bool = False, perl: bool = True) -> np.ndarray:
        """``grepl(pattern, article, ...)`` as a boolean array."""
        return self._column_mask(pattern, ignore_case, perl)[self.start : self.stop]

    def grep(self, pattern: str, ignore_case: bool = False, perl: bool = True) -> list[int]:
        """``grep(pattern, article, ...)`` (0-based positions)."""
        return np.flatnonzero(self.mask(pattern, ignore_case, perl)).tolist()

    def grepl_at(
        self, pattern: str, idx: Sequence[int], ignore_case: bool = False, perl: bool = True
    ) -> list[bool]:
        """``grepl(pattern, article[idx + 1], ...)``: ``NA`` (out of range) is ``FALSE``."""
        mask = self.mask(pattern, ignore_case, perl)
        n = len(mask)
        return [bool(mask[i]) if 0 <= i < n else False for i in idx]

    def get(self, i: int) -> str | None:
        """``article[i + 1]`` (``None`` for ``NA`` or out of range)."""
        if 0 <= i < len(self):
            return self._texts[self.start + i]
        return None

    def view(self, first: int, last: int) -> _Article:
        """``article[(first + 1):(last + 1)]`` for ``0 <= first <= last < length``."""
        return _Article(self._texts, self.start + first, self.start + last + 1, self._cache)


def _is_character(article: Any) -> bool:
    if isinstance(article, _Article | str):
        return True
    try:
        import pandas as pd

        if isinstance(article, pd.Series):
            return pd.api.types.is_string_dtype(article.dtype) or all(
                v is None or isinstance(v, str) or pd.isna(v) for v in article
            )
    except ImportError:  # pragma: no cover
        pass
    if isinstance(article, Iterable) and not isinstance(article, bytes | dict):
        values = list(article)
        return all(v is None or isinstance(v, str) for v in values)
    return False


def _stopifnot_character(article: Any) -> None:
    """R: ``stopifnot(is.character(article))``."""
    if not _is_character(article):
        raise TypeError("is.character(article) is not TRUE")


def _as_article(article: Any) -> _Article:
    _stopifnot_character(article)
    if isinstance(article, _Article):
        return article
    if isinstance(article, str):
        return _Article([article])
    texts: list[str | None] = [v if isinstance(v, str) else None for v in article]
    return _Article(texts)


def _unique(x: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(x))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


@cache
def _synonyms() -> dict[str, tuple[str, ...]]:
    s: dict[str, tuple[str, ...]] = {}

    s["txt"] = ("[a-zA-Z0-9\\s,()\\[\\]/:-]*",)  # order matters

    s["This"] = ("This", "These", "The", "Our", "All")
    s["This_singular"] = ("This", "The", "Our")
    s["These"] = ("These", "Our", "Research", "All")
    s["this"] = ("[Tt]his", "[Tt]hese", "[Tt]he", "[Oo]ur")
    s["this_singular"] = ("this", "the")
    s["these"] = ("these",)

    s["is"] = ("is", "are", "was", "were", "been")
    s["is_singular"] = ("is", "was", "have", "has")
    s["are"] = ("are", "were", "have", "had")
    s["have"] = ("have", "has", "had")
    s["is_have"] = (*s["is"], *s["have"])

    s["We"] = ("We",)

    s["by"] = ("by", "from", "within", "under")
    s["and"] = ("and", "&", "or")
    s["for"] = ("for",)
    s["of"] = ("of", "about")
    s["for_of"] = (*s["for"], *s["of"])

    s["no"] = ("[Nn]o", "[Nn]il", "[Nn]one", "[Nn]othing")
    s["No"] = ("N(?i)o(?-i)", "N(?i)il(?-i)", "N(?i)one(?-i)", "N(?i)othing(?-i)")
    s["not"] = ("not",)

    s["author"] = (
        "author(|s|\\(s\\))",
        "researcher(|s|\\(s\\))",
        "investigator(|s|\\(s\\))",
        "scientist(|s|\\(s\\))",
    )

    s["research"] = (
        "[Ww]ork(|s)", "[Rr]esearch", "[Ss]tud(y|ies)", "[Pp]roject(|s)",
        "[Tt]rial(|s)", "[Pp]ublication(|s)", "[Rr]eport(|s)", "[Pp]rogram(|s)",
        "[Pp]aper(|s)", "[Mm]anuscript(|s)", "[Aa]nalys(is|es)", "[Ii]nvestigation(|s)",
        "[Pp]rotocol(|s)", "[Cc]ohort(|s)", "[Cc]ollaboration(|s)",
    )  # fmt: skip
    s["research_strict"] = (
        "[Ww]ork(|s)", "[Rr]esearch", "[Ss]tud(y|ies)", "[Pp]roject(|s)",
        "[Tt]rial(|s)", "[Pp]rogram(|s)", "[Aa]nalys(is|es)", "[Ii]nvestigation(|s)",
        "[Pp]rotocol(|s)", "[Cc]ohort(|s)", "[Cc]ollaboration(|s)",
    )  # fmt: skip
    s["research_lower"] = (
        "work(|s)", "research", "stud(y|ies)", "project(|s)", "trial(|s)",
        "publication(|s)", "report(|s)", "program(|s)", "paper(|s)",
        "manuscript(|s)", "analys(is|es)", "investigation(|s)", "protocol(|s)",
        "cohort(|s)", "collaboration(|s)",
    )  # fmt: skip
    s["research_lower_strict"] = (
        "work(|s)", "research", "stud(y|ies)", "project(|s)", "trial(|s)",
        "program(|s)", "analys(is|es)", "investigation(|s)", "protocol(|s)",
        "cohort(|s)", "collaboration(|s)",
    )  # fmt: skip
    s["Research"] = (
        "Work(|s)", "Research", "Stud(y|ies)", "Project(|s)", "Trial(|s)",
        "Publication(|s)", "Report(|s)", "Program(|s)", "Paper(|s)",
        "Manuscript(|s)", "Analys(is|es)", "Investigation(|s)",
    )  # fmt: skip
    s["Research_strict"] = (
        "Work(|s)", "Research", "Stud(y|ies)", "Project(|s)", "Trial(|s)",
        "Program(|s)", "Analys(is|es)", "Investigation(|s)",
    )  # fmt: skip
    s["research_singular"] = (
        "[Ww]ork", "[Rr]esearch", "[Ss]tudy", "[Pp]roject", "[Tt]rial",
        "[Pp]ublication", "[Rr]eport", "[Pp]rogram", "[Pp]aper",
        "[Mm]anuscript", "[Aa]nalysis", "[Ii]nvestigation",
    )  # fmt: skip
    # R's typo "[Pp]papers" is kept
    s["researches"] = (
        "[Ww]orks", "[Ss]tudies", "[Pp]rojects", "[Tt]rials", "[Pp]ublications",
        "[Rr]eports", "[Pp]rograms", "[Pp]papers", "[Mm]anuscripts",
        "[Aa]nalyses", "[Ii]nvestigations",
    )  # fmt: skip

    s["funder"] = ("[Ff]under", "[Ss]ponsor", "[Ss]upporter")
    s["funds"] = ("[Ff]und(|s)", "[Ff]unding", "[Ss]elf-funding")
    s["funded"] = (
        "[Ff]unded", "[Ss]elf-funded", "[Ff]inanced", "[Ss]upported",
        "[Ss]ponsored", "[Rr]esourced", "[Aa]ided",
    )  # fmt: skip
    s["funding"] = (
        "[Ff]unding", "[Ff]unds", "[Ss]elf-funding", "[Ff]und support(|s)",
        "[Ss]upport", "[Ss]ponsorship", "\\b[Aa]id", "[Rr]esources",
    )  # fmt: skip
    s["funded_funding"] = (*s["funded"], *s["funding"])
    s["funding_financial"] = (*s["funding"], "[Ff]inancial")

    s["funding_title"] = (
        "F(?i)unding(?-i)", "F(?i)unding/Support(?-i)", "F(?i)unding source(|s)(?-i)",
        "S(?i)ource(|s) of funding(?-i)", "F(?i)unding source(|s) for the stud(y|ies)(?-i)",
        "F(?i)unding information(?-i)", "F(?i)unding statement(|s)(?-i)",
        "S(?i)upport statement(|s)(?-i)", "S(?i)ource(|s) of support(|s)(?-i)",
        "S(?i)ource(|s) of funding(?-i)",
        "F(?i)unding and (|potential )(conflict(|s)|competing) (|of )interest(|s)(?-i)",
    )  # fmt: skip

    s["financial"] = (
        "[Ff]inancial support(|s)", "[Ff]inancial source(|s)",
        "[Ff]inancial or other support(|s)", "[Ff]inancial assistance",
        "[Ff]inancial aid(|s)", "[Ff]inancial sponsorship(|s)",
        "[Ff]inancial support(|s) and sponsorship(|s)",
        "[Gg]rant support(|s)", "[Gg]rant assistance", "[Gg]rant aid(|s)",
        "[Gg]rant sponsorship(|s)",
    )  # fmt: skip

    s["financial_title"] = (
        "F(?i)inancial support(|s)(?-i)", "F(?i)inancial source(|s)(?-i)",
        "S(?i)ource(|s) of financial support(|s)(?-i)", "F(?i)inancial or other support(|s)(?-i)",
        "F(?i)inancial assistance(?-i)", "F(?i)inancial aid(?-i)",
        "F(?i)inancial sponsorship(|s)(?-i)",
        "F(?i)inancial support(|s) and sponsorship(|s)(?-i)",
        "F(?i)inancial source(|s) for the stud(y|ies)(?-i)",
        "F(?i)inancial information(?-i)", "F(?i)inancial statement(|s)(?-i)",
        "F(?i)inanciamento(?-i)",
    )  # fmt: skip

    s["any_title"] = (
        *s["funding_title"], *s["financial_title"],
        "G(?i)rant(|s)(?-i)", "G(?i)rant sponsor(|s|ship(|s))(?-i)",
        "G(?i)rant support(|s)(?-i)", "G(?i)rant assistance(?-i)", "G(?i)rant aid(|s)(?-i)",
    )  # fmt: skip

    s["disclosure_title"] = (
        "F(?i)unding disclosure(|s)(?-i)", "F(?i)inancial disclosure(|s)(?-i)",
        "F(?i)inancial declaration(|s)(?-i)",
        "F(?i)inancial (&|and) competing interests disclosure(|s)(?-i)",
        "F(?i)inancial (&|and) competing interests declaration(|s)(?-i)",
        "D(?i)isclosure(|s)(?-i)", "D(?i)eclaration(|s)(?-i)",
    )  # fmt: skip

    s["support"] = ("[Ss]upport(|s)", "\\b[Aa]id(|s)", "[Aa]ssistance", "[Ss]ponsorship(|s)")
    s["support_only"] = ("[Ss]upport(|s)",)
    s["Supported"] = ("Supported",)

    s["award"] = (
        "[Gg]rant(|s)", "(?<![Ss]pecialty) [Ff]ellowship(|s)", "[Aa]ward(|s|ing)",
        "[Ss]cholar(|s|ship|ships)", "[Ee]ndowment(|s)", "[Ss]tipend(|s)", "[Bb]ursar(y|ies)",
    )  # fmt: skip

    s["grant_title"] = (
        "G(?i)rant(|s)(?-i)", "G(?i)rant sponsor(|s|ship(|s))(?-i)",
        "G(?i)rant support(|s)(?-i)", "G(?i)rant assistance(?-i)", "G(?i)rant aid(|s)(?-i)",
        "^[A-Z]\\w+ grant sponsor(|s|ship(|s))(?-i)",
    )  # fmt: skip

    s["funds_award_financial"] = (*s["funds"], *s["financial"], *s["award"])
    s["funding_financial_award"] = (*s["funding"], *s["financial"], *s["award"])

    s["receive"] = (
        "received", "own(|s)", "hold(|s)", "ha(s|ve)", "charged", "invent(ed|or|ors)", "declare",
    )  # fmt: skip
    s["received"] = (
        "[Rr]eceived", "[Aa]ccepted", "[Aa]cquired", "[Pp]rovided", "[Gg]ranted",
        "[Aa]warded", "[Gg]iven", "[Oo]ffered", "[Aa]llotted", "[Dd]isclosed",
        "[Dd]eclared", "[Ss]upplied", "[Pp]resented",
    )  # fmt: skip
    s["received_strict"] = (
        "[Rr]eceived", "[Aa]ccepted", "[Aa]cquired", "[Gg]iven", "[Oo]ffered",
        "[Dd]isclose(|d)", "[Dd]eclare(|d)",
    )  # fmt: skip

    s["recipient"] = ("[Rr]ecipient(|s)", "[Aa]wardee(|s)", "[Gg]rantee(|s)")

    s["provide"] = ("provid(ed|ing)", "g(ave|iving)", "award(ed|ing)")

    s["thank"] = ("[Tt]hank(|ful)", "[Aa]cknowledge", "[Dd]isclose", "[Gg]rateful")

    s["info"] = (
        "info(|rmation)\\b", "detail(|s)", "particulars", "data\\b", "material\\b",
    )  # fmt: skip

    s["acknowledge"] = (
        "acknowledge", "recognize", "disclose", "declare", "report", "appreciate",
    )  # fmt: skip
    s["acknowledged"] = (
        "acknowledged", "recognized", "disclosed", "declared", "reported", "appreciated",
    )  # fmt: skip

    # R's typo "Ffoundation(|s)" is kept
    s["foundation"] = (
        "Ffoundation(|s)", "Institut(e|es|ution)", "Universit", "Universit(y|ies)",
        "Academ(y|ies)", "Ministr(y|ies)", "[Gg]overnment(|s)", "Council(|s)",
        "National", "NIH", "NSF", "HHMI", "Trust(|s)", "Association(|s)",
        "Societ(y|ies)", "College(|s)", "Commission(|s)", "Center(|s)",
        "[Oo]ffice(|s)", "[Pp]rogram(|s)", "[Aa]lliance(|s)", "[Aa]gency",
    )  # fmt: skip
    s["foundation_award"] = (*s["foundation"], *s["award"])

    s["References"] = (
        "R(?i)eferences(?-i)", "L(?i)terature(?-i)", "L(?i)iterature Cited(?-i)",
        "N(?i)otes and References(?-i)", "W(?i)orks Cited(?-i)", "^C(?i)itations(?-i)",
        "B(?i)ibliograpy(?-i)", "B(?i)ibliographic references(?-i)",
        "R(?i)eferences and recommended reading(?-i)",
    )  # fmt: skip

    s["Methods"] = (
        ".{0,4}M(?i)ethod(|s)(?-i)", ".{0,4}O(?i)nline method(|s)(?-i)",
        ".{0,4}M(?i)aterial(|s) and Method(|s)(?-i)", ".{0,4}M(?i)aterial(|s)/Method(|s)(?-i)",
        ".{0,4}M(?i)ethod(|s) and Material(|s)(?-i)", ".{0,4}M(?i)ethod(|s)/Material(|s)(?-i)",
        ".{0,4}S(?i)ubjects and method(|s)(?-i)", ".{0,4}P(?i)atients and method(|s)(?-i)",
        ".{0,4}P(?i)articipants and method(|s)(?-i)",
        ".{0,4}M(?i)ethods and preliminary analysis(?-i)",
        ".{0,4}E(?i)xperimental section(?-i)", ".{0,4}M(?i)ethodology(?-i)",
        ".{0,4}M(?i)etodologia(?-i)", ".{0,4}M(?i)etodologie(?-i)",
    )  # fmt: skip
    s["Abstract"] = ("Abstract", "Synopsis", "Summary")
    s["Introduction"] = ("Introduction", "Background")
    s["Results"] = ("Results", "Findings")
    s["Conclusion"] = ("Conclusion", "Interpretation")

    s["sources"] = ("source(|s)",)
    s["register"] = ("register",)
    s["registered"] = ("registered",)
    s["registration"] = ("registration",)
    s["registry"] = ("[Rr]egistr(y|ies)",)
    s["registered_registration"] = (*s["registered"], *s["registration"])

    s["conflict_title"] = (
        "C(?i)onflict(|s) of interest(|s)(?-i)",
        "C(?i)onflict(|s) of interest(|s) declaration(?-i)",
        "C(?i)onflicting interest(|s)(?-i)",
        "C(?i)onflicting interest(|s) declaration(?-i)",
        "C(?i)onflicting financial intere",
        "C(?i)onflicting of interest(|s)(?-i)",
        "C(?i)onflits d'int(?-i)",
        "C(?i)onflictos de Inter(?-i)",
        "C(?i)ompeting interest(|s)(?-i)",
        "C(?i)ompeting interest(|s) declaration(?-i)",
        "C(?i)ompeting of interest(|s)(?-i)",
        "C(?i)ompeting financial interest(|s)(?-i)",
        "D(?i)eclaration of interest(|s)(?-i)",
        "D(?i)eclaration of conflicting interest(|s)(?-i)",
        "D(?i)uality of interest(|s)(?-i)",
        "S(?i)ource(|s) of bias(?-i)",
        "F(?i)unding and (|\\w+ )(conflict(|s)|competing) (|of )interest(|s)(?-i)",
    )

    s["conflict_title_strict"] = (
        "(|\\w+ )C(?i)onflict(|s) of interest(|s)(?-i)(| \\(?COI\\)?| \\w+)",
        "(|\\w+ )C(?i)onflicting interest(|s)(?-i)(| \\(?COI\\)?| \\w+)",
        "(|\\w+ )C(?i)onflicting financial interest(|s)(?-i)(| \\w+)",
        "(|\\w+ )C(?i)onflicting of interest(|s)(?-i)(| \\(?COI\\)?| \\w+)",
        "(|\\w+ )C(?i)onflits d'int(?-i)",
        "(|\\w+ )C(?i)onflictos de Inter(?-i)",
        "(|\\w+ )C(?i)ompeting interest(|s)(?-i)(| \\w+)",
        "(|\\w+ )C(?i)ompeting of interest(|s)(?-i)(| \\(?COI\\)?| \\w+)",
        "(|\\w+ )C(?i)ompeting financial interest(|s)(?-i)(| \\w+)",
        "D(?i)eclaration of interest(|s)(?-i)(| \\w+)",
        "D(?i)eclaration of conflicting interest(|s)(?-i)",
        "D(?i)uality of interest(|s)(?-i)(| \\w+)",
        "(|\\w+ )S(?i)ource(|s) of bias(?-i)(| \\w+)",
        "F(?i)unding and (|\\w+ )(conflict(|s)|competing) (|of )interest(|s)(?-i)",
    )

    s["conflict"] = (
        "[Cc]onflict(|ing)(|s)", "[Cc]ompet(e|ing)", "source(|s) of bias", "[Cc]onflits",
        "[Cc]onflictos",
    )  # fmt: skip
    s["disclose"] = (
        "disclose(|s)", "declare(|s)", "state(|s)", "disclaim(|s)", "report(|s)",
        "acknowledge(|s)",
    )  # fmt: skip
    s["disclosure"] = (
        "disclosure", "declaration", "statement", "disclaimer", "acknowledgement", "reporting",
    )  # fmt: skip

    s["disclosure_coi_title"] = (
        "F(?i)inancial disclosure(|s)(?-i)", "F(?i)inancial declaration(|s)(?-i)",
        "F(?i)inancial (&|and) competing interests disclosure(|s)(?-i)",
        "F(?i)inancial (&|and) competing interests declaration(|s)(?-i)",
        "D(?i)isclosure(|s)(?-i)", "D(?i)eclaration(|s)(?-i)",
    )  # fmt: skip

    s["no_financial_disclosure"] = (
        "[Nn]othing to (disclose|declare|report|acknowledge)",
        "[Nn]o financial disclosure(|s)",
        "[Nn]o [a-z]+ financial disclosure(|s)",
        "[Nn]o [a-z]+ [a-z]+ financial disclosure(|s)",
    )

    s["commercial"] = ("commercial(|ly)", "financial(|y)")
    s["commercial_strict"] = ("commercial(|ly)",)

    s["relationship"] = (
        "relation(|s|ship(|s))", "connection(|s)", "association(|s)", "involvement(|s)",
        "tie(|s)", "contract(|s)",
    )  # fmt: skip
    s["relationship_strict"] = (
        "relation(|s|ship(|s))", "connection(|s)", "association(|s)", "involvement(|s)",
        "tie(|s)",
    )  # fmt: skip
    s["related_adjectives"] = ("related", "connected", "associated", "involved", "tied")

    s["interests"] = ("gain(|s)", "benefit(|s)", "interest(|s)")
    s["stock"] = ("stock(|s)", "shares", "bonds")
    s["fees"] = ("fe(e|es)", "compensation", "payment", "honorari(um|a)", "sponsorship")
    s["consultant"] = (
        "consultant(|s)", "advis(or(|s)|er(|s))", "board member(|s)", "member(|s) of the board",
    )  # fmt: skip
    s["consult"] = ("consult(|s|ing)", "advise(|s)", "counsel(|s)")
    s["consult_all"] = ("consult(|s|ant(|s))", "advis(e(|s)|or(|s)|er(|s))", "counsel(|s)")
    s["speaker"] = ("speaker", "presenter", "lectur(e|es|ing)", "employee(|s)")
    s["proprietary"] = (
        "proprietary", "patent(|s)", "copyright(|s)", "license(|s)", "rights", "permit(|s)",
        "priviledge(|s)", "franchise(|s)",
    )  # fmt: skip
    s["founder"] = ("founder", "co(|-)founder", "founding member")
    s["played"] = ("played", "had")
    s["role"] = ("role", "hand", "part", "involvement")

    return s


def _create_synonyms() -> dict[str, list[str]]:
    """Port of ``.create_synonyms()``: the rtransparent synonym dictionary.

    R caches the list in ``.syn_cache``; this returns a fresh copy of the
    cached dictionary so callers can modify it (R's copy-on-modify).
    """
    return {k: list(v) for k, v in _synonyms().items()}


def _syn(name: str) -> list[str]:
    """``synonyms[[name]]`` (``NULL``, i.e. empty, for unknown names)."""
    return list(_synonyms().get(name, ()))


def _encase(x: Sequence[str]) -> str:
    """Port of ``.encase()``: one capturing group of alternatives."""
    return "(" + "|".join(x) + ")"


def _bound(x: Sequence[str], location: str = "end") -> list[str]:
    """Port of ``.bound()``: add word boundaries (vectorised like ``paste0``)."""
    if isinstance(x, str):
        x = [x]
    if location == "both":
        pre, post = "\\b[[:alnum:]]{0,1}", "\\b"
    elif location == "end":
        pre, post = "", "\\b"
    elif location == "start":
        pre, post = "\\b[[:alnum:]]{0,1}", ""
    else:
        raise ValueError("Unknown location in .bound")
    if len(x) == 0:
        # paste0() drops zero-length arguments
        return [pre + post]
    return [pre + v + post for v in x]


def _max_words(x: Any, n_max: int = 3, space_first: bool = True) -> Any:
    """Port of ``.max_words()``: allow up to *n_max* words after (before) *x*."""
    suffix = f"(?:\\s+\\w+){{0,{n_max}}}" if space_first else f"(?:\\w+\\s+){{0,{n_max}}}"
    if isinstance(x, str):
        return x + suffix
    return [v + suffix for v in x]


def _title(x: Any, within_text: bool = False) -> Any:
    """Port of ``.title()``: a section title (a whole line unless *within_text*)."""

    def one(v: str) -> str:
        return v + "(|:|\\.)" if within_text else "^" + v + "(|:|\\.)$"

    return one(x) if isinstance(x, str) else [one(v) for v in x]


def _title_strict(x: Any, within_text: bool = False) -> Any:
    """Port of ``.title_strict()``."""

    def one(v: str) -> str:
        return v + "( [A-Z][a-zA-Z]|:|\\.|\\s*-+)" if within_text else "^.{0,4}" + v + ".{0,4}$"

    return one(x) if isinstance(x, str) else [one(v) for v in x]


def _first_capital(x: Any, location: str = "both") -> Any:
    """Port of ``.first_capital()``: case-insensitive after the first letter."""
    if location == "both":
        return gsub("^([A-Z])(.*)$", "\\1(?i)\\2(?-i)", x)
    if location == "start":
        return gsub("^(.)(.*)$", "\\1(?i)\\2", x)
    if location == "end":
        return gsub("^(.*)$", "\\1(?-i)", x)
    raise ValueError("Unknown location in .first_capital")


def _groups(
    words: Sequence[str],
    fn: Callable[[list[str]], Any] = _bound,
    syn: dict[str, list[str]] | None = None,
) -> list[str]:
    """``lapply(lapply(synonyms[words], fn), .encase)``."""
    source = syn if syn is not None else {w: _syn(w) for w in words}
    return [_encase(fn(source.get(w, []))) for w in words]


_TXT = "[a-zA-Z0-9\\s,()\\[\\]/:-]*"  # synonyms$txt


def _joined(words: Sequence[str], sep: str = _TXT, location: str = "end") -> str:
    """``paste(unlist(lapply(lapply(synonyms[words], .bound, location), .encase)), collapse = sep)``."""
    return sep.join(_groups(words, lambda v: _bound(v, location)))


# ---------------------------------------------------------------------------
# Text obliteration/cleanup (stringr/ICU in R; unused by the module)
# ---------------------------------------------------------------------------

# ICU classes spelled out for the PCRE helpers
_ICU_S = "\t\n\x0b\x0c\r\x85\\p{Z}"  # \s = White_Space
_ICU_DOT = "[^\n\x0b\x0c\r\x85\u2028\u2029]"  # . excludes every line terminator
_ICU_PUNCT = "\\p{P}"  # [[:punct:]]

_FULLSTOP_PATTERNS = (
    # R: "([A-Z])(\\.)\\s*([A-Z])(\\.)\\s*([A-Z])(\\.)" = "\\1 \\3 \\5"
    (f"([A-Z])(\\.)[{_ICU_S}]*([A-Z])(\\.)[{_ICU_S}]*([A-Z])(\\.)", "\\1 \\3 \\5"),
    (f"([A-Z])(\\.)[{_ICU_S}]*([A-Z])(\\.)", "\\1 \\3"),
    (f"([{_ICU_S}][A-Z])(\\.) ([A-Z][a-z]+)", "\\1 \\3"),
    (f"\\.[{_ICU_S}]*([a-z0-9])", " \\1"),
    ("\\.([A-Z])", " \\1"),
    (f"\\.[{_ICU_S}]*([A-Z]+[0-9])", " \\1"),
    (f"\\.([^{_ICU_S}0-9\\[])", "\\1"),
    (f"\\.[{_ICU_S}]+(\\()", " \\1"),
    ("([0-9])\\.([0-9])", "\\1\\2"),
    (f"\\.([{_ICU_S}]*[{_ICU_PUNCT}])", "\\1"),
)


def _str_replace_all(article: Any, pattern: str, replacement: str) -> Any:
    """``stringr::str_replace_all()`` with an ICU pattern pre-translated to PCRE."""
    return gsub(pattern, replacement, article, perl=True)


def obliterate_fullstop_1(article: Any) -> Any:
    """Port of ``obliterate_fullstop_1()``: remove full stops unlikely to end a sentence."""
    _stopifnot_character(article)
    out = article
    for pattern, replacement in _FULLSTOP_PATTERNS:
        out = _str_replace_all(out, pattern, replacement)
    return out


def _obliterate_semicolon_1(article: Any) -> Any:
    """Port of ``.obliterate_semicolon_1()``."""
    _stopifnot_character(article)
    return _str_replace_all(article, f"(\\({_ICU_DOT}*); ({_ICU_DOT}*\\))", "\\1 - \\2")


def _obliterate_comma_1(article: Any) -> Any:
    """Port of ``.obliterate_comma_1()``."""
    _stopifnot_character(article)
    return gsub(", ", " ", article, fixed=True)


def _obliterate_apostrophe_1(article: Any) -> Any:
    """Port of ``.obliterate_apostrophe_1()``."""
    _stopifnot_character(article)
    x = _str_replace_all(article, "([a-zA-Z])'([a-zA-Z])", "\\1\\2")
    return _str_replace_all(x, "[a-z]+s'", "s")


def _obliterate_hash_1(article: Any) -> Any:
    """Port of ``.obliterate_hash_1()``."""
    _stopifnot_character(article)
    return gsub("#", "", article, fixed=True)


def _obliterate_punct_1(article: Any) -> Any:
    """Port of ``.obliterate_punct_1()``."""
    _stopifnot_character(article)
    return _str_replace_all(article, '[~@#$%^&*{}_+"<>?/=]', "")


def _obliterate_line_break_1(article: Any) -> Any:
    """Port of ``.obliterate_line_break_1()``."""
    _stopifnot_character(article)
    return gsub("\n", " ", article, fixed=True)


def _obliterate_refs_1(article: Any) -> Any:
    """Port of ``.obliterate_refs_1()``."""
    _stopifnot_character(article)
    article = gsub("^.*\\([0-9]{4}\\).*$", "References", article)
    return gsub("^.* et al\\..*$", "References", article)


@cache
def _pattern_obliterate_conflict_1() -> str:
    financial_1 = "(funding|financial|support)"
    financial_2 = "(financial|support)"
    relationship = _encase(_bound(_syn("relationship")))
    conflict = _encase(_bound(_syn("conflict")))
    financial_interest = "(financial(?:\\s+\\w+){0,3} interest)"
    regex_1 = _TXT.join([financial_1, relationship, conflict])
    regex_2 = _TXT.join([conflict, relationship, financial_2])
    regex_3 = _TXT.join([relationship, financial_interest])
    return _encase([regex_1, regex_2, regex_3])


def obliterate_conflict_1(article: Any) -> Any:
    """Port of ``obliterate_conflict_1()``: remove COI mentions that cause false positives."""
    _stopifnot_character(article)
    return gsub(_pattern_obliterate_conflict_1(), "", article, perl=True)


@cache
def _pattern_obliterate_disclosure_1() -> str:
    words = ("conflict", "and", "not", "funded")
    parts = _encase(_bound([v for w in words for v in _syn(w)]))
    return _TXT + parts + _TXT + "($|.)"


def obliterate_disclosure_1(article: Any) -> Any:
    """Port of ``obliterate_disclosure_1()``: remove misleading disclosure sentences."""
    _stopifnot_character(article)
    return gsub(_pattern_obliterate_disclosure_1(), "", article, perl=True)


# ---------------------------------------------------------------------------
# Locators (references, acknowledgements, methods)
# ---------------------------------------------------------------------------


@cache
def _pattern_where_refs() -> str:
    next_sentence = "((|:|\\.)|(|:|\\.) [A-Z0-9]+.*)$"
    return _encase([x + next_sentence for x in _syn("References")])


def _where_refs_txt(article: Any) -> list[int]:
    """Port of ``.where_refs_txt()``: the (last) reference-section line, or ``[]``."""
    art = _as_article(article)
    ref_index = art.grep(_pattern_where_refs())
    if ref_index:
        return [ref_index[-1]]
    ref_index = art.grep("^1(|\\.)\\s+[A-Z]", perl=False)
    if ref_index:
        return [ref_index[-1]]
    return []


def _where_acknows_txt(article: Any) -> list[int]:
    """Port of ``.where_acknows_txt()``: where the acknowledgements start, or ``[]``."""
    art = _as_article(article)
    acknow_index = get_acknow_2(art)
    fund_index = get_fund_2(art)
    finance_index = get_financial_1(art)
    grant_index = get_grant_1(art)

    found = [acknow_index[-1]] if acknow_index else []
    found.extend(idx[0] for idx in (fund_index, finance_index, grant_index) if idx)
    if not found:
        return []
    all_max, all_min = max(found), min(found)
    return [all_min if (all_max - all_min) <= 10 else all_max]


@cache
def _patterns_where_methods() -> tuple[str, str]:
    methods = _syn("Methods")
    pats = [s[:-1] for s in _title_strict(methods)]  # stringr::str_sub(s, end = -2)
    pats2 = [s + "\\s*[A-Z]" for s in _title_strict(methods, within_text=True)]
    return _encase(pats), _encase(pats2)


def _where_methods_txt(article: Any) -> list[int]:
    """Port of ``.where_methods_txt()``: the (last) methods title line, or ``[]``."""
    art = _as_article(article)
    pattern, pattern2 = _patterns_where_methods()
    idx = art.grep(pattern)
    if idx:
        return [idx[-1]]
    idx = art.grep(pattern2)
    if idx:
        return [idx[-1]]
    return []


# ---------------------------------------------------------------------------
# Patterned builders
# ---------------------------------------------------------------------------


def _title_with_next(art: _Article, a: list[int]) -> list[int]:
    """R's ``if (!is.na(article[a + 1])) { if (nchar(article[a + 1]) == 0) ... }``."""
    if len(a) > 1:
        raise ValueError("the condition has length > 1")
    nxt = art.get(a[0] + 1)
    if nxt is None:
        return list(a)
    if len(nxt) == 0:
        return [a[0], a[0] + 2]
    return [a[0], a[0] + 1]


@cache
def _patterns_support_1() -> tuple[str, str]:
    this_research = ".{0,15}".join(_groups(["This_singular", "research_singular"]))
    was_funded_by = _joined(["is_singular", "funded_funding", "by"])
    singular = _TXT.join([this_research, was_funded_by])
    plural = _joined(["These", "researches", "are", "funded_funding", "by"])
    return singular, plural


def get_support_1(article: Any) -> list[int]:
    """Port of ``get_support_1()``: "This study was funded by ..."."""
    art = _as_article(article)
    singular, plural = _patterns_support_1()
    idx = art.grep(singular)
    if idx:
        return idx
    return art.grep(plural)


@cache
def _patterns_support_2() -> tuple[str, str]:
    return (
        _joined(["funded_funding", "this_singular", "research_singular"]),
        _joined(["funded", "these", "researches"]),
    )


def get_support_2(article: Any) -> list[int]:
    """Port of ``get_support_2()`` (not used by metacheck)."""
    art = _as_article(article)
    first, second = _patterns_support_2()
    idx = art.grep(first)
    if idx:
        return idx
    return art.grep(second)


@cache
def _pattern_support_3() -> str:
    research_is = " ".join(_groups(["research", "is_have"]))
    return _TXT.join([research_is, _joined(["funded", "by"])])


def get_support_3(article: Any) -> list[int]:
    """Port of ``get_support_3()``."""
    return _as_article(article).grep(_pattern_support_3())


@cache
def _pattern_support_4() -> str:
    return _joined(["funded", "by", "award"])


def get_support_4(article: Any) -> list[int]:
    """Port of ``get_support_4()``."""
    return _as_article(article).grep(_pattern_support_4())


@cache
def _pattern_support_5() -> str:
    funded_by_award = _joined(["funded", "by", "foundation"])
    start_of_sentence = "(^\\s*|(:|\\.)\\s*|[A-Z][a-zA-Z]+\\s*)"
    return _max_words(start_of_sentence, n_max=4, space_first=False) + funded_by_award


def get_support_5(article: Any) -> list[int]:
    """Port of ``get_support_5()``."""
    return _as_article(article).grep(_pattern_support_5())


@cache
def _patterns_support_6() -> tuple[str, str]:
    acknowledge = _max_words(_groups(["acknowledge"]))
    support_foundation = _joined(["support_only", "foundation_award"])
    a = " ".join([*acknowledge, support_foundation])
    b = _joined(["support_only", "foundation_award", "acknowledged"])
    return a, b


def get_support_6(article: Any) -> list[int]:
    """Port of ``get_support_6()``."""
    art = _as_article(article)
    a, b = _patterns_support_6()
    return _unique([*art.grep(a), *art.grep(b)])


@cache
def _pattern_support_7() -> str:
    return _TXT.join(["Support", _encase(_syn("received")), _encase(_syn("by"))])


def get_support_7(article: Any) -> list[int]:
    """Port of ``get_support_7()``."""
    return _as_article(article).grep(_pattern_support_7())


@cache
def _pattern_support_8() -> str:
    return _joined(["foundation", "provide", "funding_financial", "for_of", "research"])


def get_support_8(article: Any) -> list[int]:
    """Port of ``get_support_8()``."""
    return _as_article(article).grep(_pattern_support_8())


@cache
def _pattern_support_9() -> str:
    foundation = _groups(["foundation"])
    # grep("upport", v, value = TRUE, invert = TRUE) (TRE)
    funded = [
        v for v, hit in zip(_syn("funded"), grepl("upport", _syn("funded")), strict=True) if not hit
    ]
    funding = [_encase(_bound(funded))]
    research = [g + ".{0,20}\\." for g in _groups(["research"])]
    return _TXT.join([*foundation, *funding, *research])


def get_support_9(article: Any) -> list[int]:
    """Port of ``get_support_9()``."""
    return _as_article(article).grep(_pattern_support_9())


@cache
def _pattern_support_10() -> str:
    return _joined(["thank", "foundation", "funding_financial", "research"])


def get_support_10(article: Any) -> list[int]:
    """Port of ``get_support_10()``."""
    return _as_article(article).grep(_pattern_support_10())


@cache
def _pattern_developed_1() -> str:
    this_research_is = _joined(["This", "research", "is"])
    by_foundation = _joined(["by", "foundation"])
    return _TXT.join([this_research_is, "developed", by_foundation])


def get_developed_1(article: Any) -> list[int]:
    """Port of ``get_developed_1()``."""
    return _as_article(article).grep(_pattern_developed_1())


@cache
def _pattern_received_1() -> str:
    return _joined(["received", "funds_award_financial", "by"], sep=" ")


def get_received_1(article: Any) -> list[int]:
    """Port of ``get_received_1()``."""
    return _as_article(article).grep(_pattern_received_1())


@cache
def _pattern_received_2() -> str:
    return _joined(["received", "funding_financial", "by", "foundation_award"])


def get_received_2(article: Any) -> list[int]:
    """Port of ``get_received_2()``."""
    return _as_article(article).grep(_pattern_received_2())


@cache
def _pattern_recipient_1() -> str:
    return _joined(["recipient", "award"])


def get_recipient_1(article: Any) -> list[int]:
    """Port of ``get_recipient_1()``."""
    return _as_article(article).grep(_pattern_recipient_1())


@cache
def _pattern_authors_1() -> str:
    return _joined(["This", "author", "funds_award_financial"])


def get_authors_1(article: Any) -> list[int]:
    """Port of ``get_authors_1()``."""
    return _as_article(article).grep(_pattern_authors_1())


@cache
def _pattern_authors_2() -> str:
    return _joined(["This", "author", "have", "no", "funding_financial_award"])


def get_authors_2(article: Any) -> list[int]:
    """Port of ``get_authors_2()``."""
    return _as_article(article).grep(_pattern_authors_2())


@cache
def _pattern_thank_1() -> str:
    syn = _create_synonyms()
    syn["financial"] = [*syn["financial"], "for supporting"]
    return _TXT.join(_groups(["We", "thank", "financial"], syn=syn))


def get_thank_1(article: Any) -> list[int]:
    """Port of ``get_thank_1()``."""
    return _as_article(article).grep(_pattern_thank_1())


@cache
def _pattern_thank_2() -> str:
    thank = _joined(["thank"])
    txt = "[a-zA-Z0-9\\s,()/:;-]*"
    return txt.join([thank, "[0-9]{5}"])


def get_thank_2(article: Any) -> list[int]:
    """Port of ``get_thank_2()``."""
    return _as_article(article).grep(_pattern_thank_2())


@cache
def _pattern_fund_1() -> str:
    funding_for = _joined(["funding_financial_award", "for"], sep=" ")
    return _TXT.join([funding_for, _joined(["research", "received"])])


def get_fund_1(article: Any) -> list[int]:
    """Port of ``get_fund_1()``."""
    return _as_article(article).grep(_pattern_fund_1())


@cache
def _patterns_title(words: str) -> tuple[str, str]:
    """The whole-line and within-text title patterns of a synonym set."""
    return (
        _encase(_title(_syn(words))),
        _encase(_title(_syn(words), within_text=True)),
    )


def _get_title(art: _Article, words: str) -> list[int]:
    line, within = _patterns_title(words)
    a = art.grep(line)
    if a:
        return _title_with_next(art, a)
    return art.grep(within)


def get_fund_2(article: Any) -> list[int]:
    """Port of ``get_fund_2()``: a "Funding" title line and the line after it."""
    return _get_title(_as_article(article), "funding_title")


@cache
def _pattern_fund_3() -> str:
    return _encase(_syn("any_title")) + " [A-Z]"


def get_fund_3(article: Any) -> list[int]:
    """Port of ``get_fund_3()``."""
    return _as_article(article).grep(_pattern_fund_3())


@cache
def _pattern_fund_acknow() -> str:
    funded_synonyms = [v for w in ("funded", "funds_award_financial") for v in _bound(_syn(w))]
    return _encase([*funded_synonyms, "NIH (|\\()(?:(R|P))[0-9]{2}", "awarded by"])


def get_fund_acknow(article: Any) -> list[int]:
    """Port of ``get_fund_acknow()``."""
    return _as_article(article).grep(_pattern_fund_acknow(), ignore_case=True)


@cache
def _pattern_fund_acknow_new() -> str:
    words = ("acknowledge", "support_only", "grant|foundation|institute|organization")
    # the last name is not a synonym: .bound(NULL) is "\\b"
    return _encase([v for w in words for v in _bound(_syn(w))])


def get_fund_acknow_new(article: Any) -> list[int]:
    """Port of ``get_fund_acknow_new()`` (not used by metacheck)."""
    return _as_article(article).grep(_pattern_fund_acknow_new(), ignore_case=True)


@cache
def _pattern_supported_1() -> str:
    return _joined(["Supported", "by"], sep=" ") + " [a-zA-Z]+"


def get_supported_1(article: Any) -> list[int]:
    """Port of ``get_supported_1()``."""
    return _as_article(article).grep(_pattern_supported_1())


def get_financial_1(article: Any) -> list[int]:
    """Port of ``get_financial_1()``: a "Financial support" title line and the next line."""
    return _get_title(_as_article(article), "financial_title")


@cache
def _pattern_financial_2() -> str:
    return _joined(["financial_title", "No"], sep=" ")


def get_financial_2(article: Any) -> list[int]:
    """Port of ``get_financial_2()``."""
    return _as_article(article).grep(_pattern_financial_2())


@cache
def _pattern_financial_3() -> str:
    return _joined(["financial_title", "this", "research"])


def get_financial_3(article: Any) -> list[int]:
    """Port of ``get_financial_3()``."""
    return _as_article(article).grep(_pattern_financial_3())


@cache
def _patterns_disclosure_1() -> tuple[str, str]:
    title = _patterns_title("disclosure_title")[0]
    d_pat = "|".join(_bound(_syn("funding_financial_award")))
    return title, d_pat


def get_disclosure_1(article: Any) -> list[int]:
    """Port of ``get_disclosure_1()``: a disclosure title followed by a funding line."""
    art = _as_article(article)
    title, d_pat = _patterns_disclosure_1()
    a = art.grep(title)
    out: list[int] = []
    for ai in a:
        next_line = ai + 1
        nxt = art.get(next_line)
        if nxt is not None and len(nxt) == 0:
            next_line = ai + 2
        if art.grepl_at(d_pat, [next_line])[0]:
            out.extend([ai, next_line])
    return out


@cache
def _pattern_disclosure_2() -> str:
    disclosure = _encase(_title(_syn("disclosure_title"), within_text=True))
    funding = _encase(_bound(_syn("funding_financial_award")))
    return _TXT.join([disclosure, funding])


def get_disclosure_2(article: Any) -> list[int]:
    """Port of ``get_disclosure_2()``."""
    return _as_article(article).grep(_pattern_disclosure_2())


@cache
def _pattern_grant_1_within() -> str:
    grant = ("G(?i)rant ", "^[A-Z](?i)\\w+ grant ", "Contract grant ")
    support = [v for w in ("support", "funder") for v in _title(_syn(w), within_text=True)]
    return _encase([g + s for g in grant for s in support])


def get_grant_1(article: Any) -> list[int]:
    """Port of ``get_grant_1()``: a "Grant" title line and the next line."""
    art = _as_article(article)
    line = _patterns_title("grant_title")[0]
    a = art.grep(line)
    if a:
        return _title_with_next(art, a)
    return art.grep(_pattern_grant_1_within())


def get_french_1(article: Any) -> list[int]:
    """Port of ``get_french_1()``."""
    return _as_article(article).grep("Cette.*tude.*financ.*par", ignore_case=True)


def get_project_acknow(article: Any) -> list[int]:
    """Port of ``get_project_acknow()``."""
    return _as_article(article).grep("project (no|num)", ignore_case=True)


@cache
def _pattern_common_1() -> str:
    no_funding = _joined(["no", "funding_financial_award"], sep=" ")
    was_received = _joined(["is", "received"], sep=" ")
    return _TXT.join([no_funding, was_received])


def get_common_1(article: Any) -> list[int]:
    """Port of ``get_common_1()``."""
    return _as_article(article).grep(_pattern_common_1())


@cache
def _pattern_common_2() -> str:
    return _joined(["No", "funding_financial_award", "received"])


def get_common_2(article: Any) -> list[int]:
    """Port of ``get_common_2()``."""
    return _as_article(article).grep(_pattern_common_2())


def get_common_3(article: Any) -> list[int]:
    """Port of ``get_common_3()`` (TRE)."""
    return _as_article(article).grep("required to disclose.*disclosed none", perl=False)


@cache
def _pattern_common_4() -> str:
    no_funding = _joined(["no", "funding_financial_award"])
    for_this = _joined(["for", "this"], sep=" ")
    research = _joined(["research"], sep="|")
    return _TXT.join([no_funding, for_this, research])


def get_common_4(article: Any) -> list[int]:
    """Port of ``get_common_4()``."""
    return _as_article(article).grep(_pattern_common_4())


@cache
def _pattern_common_5() -> str:
    return _joined(["no", "sources", "funding_financial"], sep=_max_words(" ", space_first=False))


def get_common_5(article: Any) -> list[int]:
    """Port of ``get_common_5()``."""
    return _as_article(article).grep(_pattern_common_5())


# ---------------------------------------------------------------------------
# Negation/predicate helpers
# ---------------------------------------------------------------------------


@cache
def _patterns_negate_disclosure_1() -> tuple[str, str]:
    txt = "[a-zA-Z0-9\\s,()-]*"
    disclose = _encase(["[Dd]isclose(|s)(|:|\\.)", "[Dd]isclosure(|s)(|:|\\.)"])
    conflict = _encase(
        [
            "conflict(|s) of interest",
            "conflicting interest",
            "conflicting financial interest",
            "conflicting of interest",
            "conflits d'int",
            "conflictos de Inter",
            "competing interest",
            "competing of interest",
            "competing financial interest",
        ]
    )
    and_ = _encase(["and", "&", "or"])
    not_ = _encase(["not"])
    funded_synonyms = [
        "\\bfunded",
        "\\bfinanced",
        "\\bsupported",
        "\\bsponsored",
        "\\bresourced",
    ]
    funded = _encase(funded_synonyms)
    regex_1 = txt.join([disclose, conflict, and_, not_, funded])
    funded_2 = _encase([*funded_synonyms, *_syn("funding")])
    regex_2 = txt.join([disclose, funded_2, conflict])
    return regex_1, regex_2


def _grepl_list(pattern: str, article: Any, ignore_case: bool = False) -> list[bool]:
    art = _as_article(article)
    return art.mask(pattern, ignore_case).tolist()


def negate_disclosure_1(article: Any) -> list[bool]:
    """Port of ``negate_disclosure_1()`` (not used by the module)."""
    regex_1, regex_2 = _patterns_negate_disclosure_1()
    a = _grepl_list(regex_1, article)
    if any(a):
        return a
    return _grepl_list(regex_2, article)


@cache
def _pattern_negate_disclosure_2() -> str:
    disclosure_title = _encase(
        [
            "F(?i)inancial disclosure(|s)(?-i)(|:|\\.)",
            "F(?i)inancial declaration(|s)(?-i)(|:|\\.)",
            "Disclosure(|:|\\.)",
            "Declaration(|:|\\.)",
        ]
    )
    disclosure = _encase(
        ["financial disclosure(|s)", "financial declaration(|s)", "disclosure", "declaration"]
    )
    disclose = _encase(["to disclose", "to declare", "to report"])
    no = _encase(_syn("No"))
    no_1 = "(no|not have any)"
    no_2 = "(nil|nothing)"
    regex_1 = _encase([disclosure_title + " " + no])
    regex_2 = _encase([_TXT.join([disclosure_title, no_1 + " " + disclosure])])
    regex_3 = _encase([_TXT.join([disclosure_title, no_2 + " " + disclose])])
    return "|".join([regex_1, regex_2, regex_3])


def negate_disclosure_2(article: Any) -> list[bool]:
    """Port of ``negate_disclosure_2()`` (not used by the module)."""
    return _grepl_list(_pattern_negate_disclosure_2(), article)


@cache
def _pattern_negate_conflict_1() -> str:
    return _patterns_title("conflict_title")[0]


def negate_conflict_1(article: Any) -> list[bool]:
    """Port of ``negate_conflict_1()`` (not used by the module)."""
    return _grepl_list(_pattern_negate_conflict_1(), article)


@cache
def _pattern_negate_absence_1() -> str:
    return _joined(["No", "info", "of", "funding_financial_award", "is", "received"])


def negate_absence_1(article: Any) -> list[bool]:
    """Port of ``negate_absence_1()``: "No information about funding was received"."""
    return _grepl_list(_pattern_negate_absence_1(), article)


# ---------------------------------------------------------------------------
# Acknowledgements section
# ---------------------------------------------------------------------------

_ACKNOW_1 = " ".join(
    [
        "^Acknowledg(|e)ment(|s)",
        "(of|and)",
        "([Ss]upport |\\b[Ff]unding|\\b[Ff]inancial)",
    ]
)
_ACKNOW_2 = (
    "(^A(?i)cknowledg(|e)ment(|s)(?-i))"
    + "|"
    + " ".join(
        [
            "(^Acknowledg(|e)ment(|s)",
            "(of|and)",
            "([Ss]upport |\\b[Ff]unding|\\b[Ff]inancial))",
        ]
    )
)


def get_acknow_1(article: Any) -> list[int]:
    """Port of ``get_acknow_1()``: an "Acknowledgements and funding" line and the next."""
    art = _as_article(article)
    a = art.grep(_ACKNOW_1, ignore_case=True)
    if a:
        return _title_with_next(art, a)
    return a


def get_acknow_2(article: Any) -> list[int]:
    """Port of ``get_acknow_2()``: acknowledgement title lines."""
    return _as_article(article).grep(_ACKNOW_2)


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------

_INDEX_ANY: tuple[Callable[[Any], list[int]], ...] = (
    get_support_1,
    # get_support_2 is commented out in R
    get_support_3,
    get_support_4,
    get_support_5,
    get_support_6,
    get_support_7,
    get_support_8,
    get_support_9,
    get_support_10,
    get_developed_1,
    get_received_1,
    get_received_2,
    get_recipient_1,
    get_authors_1,
    get_authors_2,
    get_thank_1,
    get_thank_2,
    get_fund_1,
    get_fund_2,
    get_fund_3,
    get_supported_1,
    get_financial_1,
    get_financial_2,
    get_financial_3,
    get_grant_1,
    get_french_1,
    get_common_1,
    get_common_2,
    get_common_3,
    get_common_4,
    get_common_5,
    get_acknow_1,
    get_disclosure_1,
    get_disclosure_2,
)


def rtransparent_funding(text: Any) -> str:
    """Port of ``rtransparent_funding()``: the funding statement of one paper's sentences.

    *text* is the character vector of a paper's sentences. Returns the
    matching sentences pasted together with ``" "`` (``""`` if none); a
    position past the end (see the module docstring) pastes as ``"NA"``.
    """
    art = _as_article(text)
    n = len(art)

    index = sorted({i for fn in _INDEX_ANY for i in fn(art)})

    # Remove potential mistakes (absence)
    if index:
        is_absent = art.grepl_at(_pattern_negate_absence_1(), index)
        index = [i for i, absent in zip(index, is_absent, strict=True) if not absent]

    # Identify potentially missed signals within Acknowledgements
    if not index:
        from_ = _where_acknows_txt(art)
        refs = _where_refs_txt(art)
        if from_ and refs:
            start = from_[0]
            to = refs[0] - 1
            diff = to - start
            if diff < 0:
                to = min(n - 1, start + 100)
                diff = to - start
            if diff <= 100:
                part = art.view(start, to)
                found = [*get_fund_acknow(part), *get_project_acknow(part)]
                index = [i + start for i in found]

    index = sorted(set(index))
    return " ".join("NA" if (v := art.get(i)) is None else v for i in index)
