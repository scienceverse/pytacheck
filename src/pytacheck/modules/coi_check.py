"""COI Check (port of ``inst/modules/coi_check.R``).

References (roxygen ``@references`` of the R module):

Serghiou, S., Contopoulos-Ioannidis, D. G., Boyack, K. W., Riedel, N., Wallach, J. D., &
Ioannidis, J. P. (2021). Assessment of transparency indicators across the biomedical
literature: How open is open?. PLoS biology, 19(3), e3001107. doi: 10.1371/journal.pbio.3001107

Serghiou S (2025). _rtransparent: Identifies indicators of transparency_. R package version
0.2.5, commit d0d5dfe4b6c4e519d54e341436d4263fb05d84be, <https://github.com/serghiou/rtransparent>.

The module relies on base R's ``agrep()`` / ``agrepl()`` (TRE approximate matching of a
*literal* pattern, ``max.distance = 0.1``); :class:`_Agrep` reproduces it exactly.
"""

from __future__ import annotations

import bisect
import functools
import itertools
import math
from collections.abc import Sequence
from typing import Any

import pandas as pd

from pytacheck._r.base import paste, trimws
from pytacheck._r.regex import compile_r, gregexpr_all, grepl, gsub, regextract_all, strsplit
from pytacheck.module import module
from pytacheck.report import scroll_table
from pytacheck.text import text_search

__all__ = ["agrep", "agrepl", "coi_check", "rtransparent_coi"]

# ---------------------------------------------------------------------------
# agrep(): TRE approximate matching of a literal pattern
# ---------------------------------------------------------------------------

# Only ASCII letters are case-folded: with ignore.case = TRUE, TRE compiles each
# (ASCII) pattern letter to its lower/upper case pair and compares text
# characters exactly, so lowering just A-Z of the text and the pattern turns a
# case-insensitive match into an exact one without changing string lengths.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class _Agrep:
    """R ``agrepl(pattern, x, ignore.case = ignore_case)`` with the defaults.

    R's defaults are ``max.distance = 0.1`` and ``fixed = TRUE``: the pattern
    is a literal string (regex metacharacters match themselves) and a string
    matches when some substring of it is within Levenshtein distance
    ``ceiling(0.1 * nchar(pattern))`` (unit insertion/deletion/substitution
    costs) of the pattern.

    Matching is exact: the pattern is split into ``k + 1`` pieces, one of which
    must occur verbatim in any approximate match (pigeonhole), and the edit
    distance is then computed only in the windows around those occurrences,
    with Myers' bit-parallel algorithm. An exact occurrence at text position
    ``pos`` of the piece at pattern offset ``o`` places the whole match inside
    ``[pos - o - k, pos - o + m + k)``: at most ``k`` net insertions/deletions
    happen before or after the piece.
    """

    __slots__ = ("high", "icase", "k", "m", "mask", "pattern", "peq", "pieces")

    def __init__(self, pattern: str, ignore_case: bool = False) -> None:
        self.icase = ignore_case
        self.pattern = pattern.translate(_ASCII_LOWER) if ignore_case else pattern
        self.m = len(pattern)
        # R: .amatch_bounds(0.1) -> ceil(0.1 * patlen * max_cost) in amatch_regaparams()
        self.k = math.ceil(0.1 * self.m)
        n = self.k + 1
        size, extra = divmod(self.m, n)
        # piece -> its offsets in the pattern (a piece string may occur twice)
        pieces: dict[str, tuple[int, ...]] = {}
        start = 0
        for i in range(n):
            ln = size + (1 if i < extra else 0)
            if ln:
                piece = self.pattern[start : start + ln]
                pieces[piece] = (*pieces.get(piece, ()), start)
            start += ln
        self.pieces = pieces
        # bit masks for Myers' algorithm: bit i set where pattern[i] == char
        peq: dict[str, int] = {}
        for i, c in enumerate(self.pattern):
            peq[c] = peq.get(c, 0) | (1 << i)
        self.peq = peq
        self.mask = (1 << self.m) - 1
        self.high = 1 << (self.m - 1)

    def prepare(self, s: str) -> str:
        return _ascii_lower(s) if self.icase else s

    def _within(self, t: str) -> bool:
        """Whether some substring of *t* is within distance k of the pattern.

        Myers' bit-parallel approximate string matching (Hyyrö's formulation):
        ``score`` is the edit distance between the pattern and the best
        substring of *t* ending at the current character.
        """
        peq, k, mask, high = self.peq, self.k, self.mask, self.high
        pv, mv, score = mask, 0, self.m
        for c in t:
            eq = peq.get(c, 0)
            xv = eq | mv
            xh = (((eq & pv) + pv) ^ pv) | eq
            ph = mv | (~(xh | pv) & mask)
            mh = pv & xh
            if ph & high:
                score += 1
            elif mh & high:
                score -= 1
            ph = (ph << 1) & mask
            mh = (mh << 1) & mask
            pv = mh | (~(xv | ph) & mask)
            mv = ph & xv
            if score <= k:
                return True
        return False

    def match(self, s: str) -> bool:
        """``agrepl()`` for one non-missing, already :meth:`prepare`-d string."""
        if self.m <= self.k:  # a 1-character pattern: every string is within k = 1 edit
            return True
        # a match contains an exact occurrence of a piece; see the class docstring
        lead, trail = self.k, self.m + self.k
        windows: list[tuple[int, int]] = []
        for piece, offsets in self.pieces.items():
            pos = s.find(piece)
            while pos != -1:
                windows.extend((max(0, pos - o - lead), pos - o + trail) for o in offsets)
                pos = s.find(piece, pos + 1)
        if not windows:
            return False
        windows.sort()
        merged: list[list[int]] = []
        for lo, hi in windows:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        return any(self._within(s[lo:hi]) for lo, hi in merged)

    def which(self, xs: Sequence[str | None]) -> list[int]:
        """0-based indices of the matches among already :meth:`prepare`-d strings (``None`` = NA).

        The pigeonhole prefilter runs once over the concatenated strings (C-speed
        ``str.find``), so only the few elements containing a piece are checked with
        :meth:`match`. Pieces never contain the ``"\\x00"`` separator, so a piece
        occurrence always lies inside a single element.
        """
        if self.m <= self.k:  # every non-NA string is within k edits (R: agrepl("a", "") is TRUE)
            return [i for i, s in enumerate(xs) if s is not None]
        if not xs:
            return []
        texts = ["" if s is None else s for s in xs]
        big = "\x00".join(texts)
        starts = list(itertools.accumulate((len(t) + 1 for t in texts[:-1]), initial=0))
        ends = [*starts[1:], len(big) + 1]
        candidates: set[int] = set()
        for piece in self.pieces:
            pos = big.find(piece)
            while pos != -1:
                i = bisect.bisect_right(starts, pos) - 1
                candidates.add(i)
                pos = big.find(piece, ends[i])  # one hit makes the element a candidate
        return sorted(i for i in candidates if xs[i] is not None and self.match(texts[i]))

    def __call__(self, x: Sequence[Any]) -> list[bool]:
        """``agrepl()`` over a vector; ``NA`` never matches."""
        hits = set(self.which([None if _is_na(v) else self.prepare(str(v)) for v in x]))
        return [i in hits for i in range(len(x))]


def _ascii_lower(s: str) -> str:
    """Lower-case A-Z only (keeps the length, unlike :meth:`str.lower` on some letters)."""
    return s.lower() if s.isascii() else s.translate(_ASCII_LOWER)


def _is_na(v: Any) -> bool:
    return v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v))


def agrep(pattern: str, x: Sequence[Any], ignore_case: bool = False) -> list[int]:
    """R ``agrep(pattern, x, ignore.case = ignore_case)``: the 1-based indices of matches."""
    hits = _matcher(pattern, ignore_case)(x)
    return [i + 1 for i, h in enumerate(hits) if h]


def agrepl(pattern: str, x: Any, ignore_case: bool = False) -> Any:
    """R ``agrepl(pattern, x, ignore.case = ignore_case)`` (a scalar for a scalar)."""
    rx = _matcher(pattern, ignore_case)
    if isinstance(x, str):
        return rx.match(rx.prepare(x))
    if _is_na(x):
        return False
    return rx(list(x))


@functools.cache
def _matcher(pattern: str, ignore_case: bool) -> _Agrep:
    return _Agrep(pattern, ignore_case)


# ---------------------------------------------------------------------------
# rtransparent_coi()
# ---------------------------------------------------------------------------

_REPORT_WORDS = (
    "disclosed|reported|mentioned|declared|communicated|revealed|divulged|aired|voiced|expressed"
)
_PRECEDING = (
    r"^.*?(Disclosure.*conflict.*$)|^.*?(Disclosure.*compet.*$)|^.*?(Declaration of Conflict.*$)"
    r"|^.*?(Declaration of Interest.*$)|^.*?(Potential conflict.{0,1} of interest.*$)"
    r"|^.*?(Conflict.*$)|^.*?(Competing.*$)"
)
_NONE_STOP = (
    r"(^.+None\.).*$|(^.+None disclosed\.).*$|(^.+None reported\.).*$|(^.+None declared\.).*$"
    r"|(^.+None mentioned\.).*$|(^.+None aired\.).*$|(^.+None communicated\.).*$"
    r"|(^.+None revealed\.).*$|(^.+Nothing to declare\.).*$|(^.+No\.).*$|(^.+Nil\.).*$"
)
_AUTHORS_NO = "|".join(
    [
        r"^.*(The author.+no.*competing.+interest.*$)",
        r"^.*(The author.+no.*conflict.+interest.*$)",
        r"^.*(None of the author.+no.*competing.+interest.*$)",
        r"^.*(None of the author.+no.*conflict.+interest.*$)",
    ]
)
# The seven alternatives of R's `vals` (val1 ... val7), each `(<group>) [A-Z].*$`.
_AUTHORS_LAST = (
    r"^.*The author.+no.*competing.+interest.*?\.",
    r"^.*The author.+no.*conflict.+interest.*?\.",
    r"^.*All authors.+no.*conflict.+interest.*?\.",
    r"^.*Both authors.+no.*conflict.+interest.*?\.",
    r"^.*No conflicts of interest.{0,12}?\.",
    r"^.*No conflicting.{0,12} interest.{0,12}?\.",
    r"^.*No competing.{0,12} interest.{0,12}?\.",
)


def _cut_after_authors(coi_text: str) -> str:
    """``gsub(paste(val1, ..., val7, sep = "|"), "\\1...\\7", coi_text)`` with TRE semantics.

    Each alternative is ``(<group>) [A-Z].*$`` where the group contains a lazy
    ``.*?``/``{0,12}?``. TRE (unlike a backtracking engine, which lets the
    greedy ``^.*`` and ``.+`` run first) resolves such a match with the *earliest*
    possible end of the group, and prefers the first alternative that matches at
    all (measured against R 4.5). The whole string is matched, so the result is
    the group of the first matching alternative, cut at its earliest ``". [A-Z]"``.
    """
    ends = [start for start, _ in gregexpr_all(r"\. [A-Z]", coi_text)]
    if not ends:
        return coi_text
    for group in _AUTHORS_LAST:
        rx = compile_r(group)
        for e in ends:
            if rx.fullmatch(coi_text, 0, e):
                return coi_text[:e]
    return coi_text


def _str_count(x: str, pattern: str) -> int:
    """``stringr::str_count(x, pattern)`` (ICU regex; PCRE-compatible here)."""
    return len(regextract_all(pattern, x, perl=True))


def _unique(x: list[int]) -> list[int]:
    return list(dict.fromkeys(x))


def rtransparent_coi(splitted: Sequence[Any]) -> str:
    """Port of ``rtransparent_coi()`` (``inst/modules/coi_check.R``).

    Extract a conflict of interest statement from the sentences of one paper,
    adapted from rtransparent. Returns ``""`` when none is found. Indices are
    1-based, as in R. Raises :class:`ValueError` where R's ``if`` meets ``NA``
    (a short COI heading without a "no" as the last sentence: R errors there).
    """
    splitted = [None if _is_na(s) else str(s) for s in splitted]
    n = len(splitted)

    def at(i: int) -> str | None:
        """``splitted[i]`` (1-based; out of range is ``NA``)."""
        return splitted[i - 1] if 1 <= i <= n else None

    def sel(idx: Sequence[int]) -> list[str | None]:
        return [at(i) for i in idx]

    # agrep(<pattern>, splitted, ignore.case = T), lower-casing each sentence once
    lowered = [None if s is None else _ascii_lower(s) for s in splitted]

    def agrep_i(pattern: str) -> list[int]:
        return [i + 1 for i in _matcher(pattern, True).which(lowered)]

    is_conflict = agrep_i("conflict of interest")
    is_conflicts = agrep_i("conflicts of interest")
    is_competing = agrep_i("competing interest")
    is_disclosure = agrep_i("disclosure")
    is_finance = agrep_i("competing financial interest")
    is_declare = agrep_i("declaration of interest")
    is_dual = agrep_i("duality of interest")

    # Exclude financial disclosures
    if is_disclosure:
        fin = grepl("financial disclosure", sel(is_disclosure), ignore_case=True)
        is_disclosure = [i for i, f in zip(is_disclosure, fin, strict=True) if not f]

    # Exclude mentions of COI that are not COI
    the_conflicts = _unique(is_conflict + is_conflicts + is_competing)
    if the_conflicts:
        kept: list[int] = []
        for j in the_conflicts:
            s = at(j)
            is_capital = (
                agrepl("Conflict", s)
                or agrepl("CONFLICT", s)
                or agrepl("Compet", s)
                or agrepl("COMPET", s)
            )
            # agrep() uses fixed = TRUE: these "regexes" are literal strings
            is_no = agrepl("no.{0,20}conflict", s, ignore_case=True)
            is_author = agrepl("author", s, ignore_case=True)
            is_punctuation = agrepl("interest.{0,1}[.:;,]", s, ignore_case=True)
            is_report = grepl(_REPORT_WORDS, s, ignore_case=True)
            if is_capital or is_no or is_punctuation or is_report or is_author:
                kept.append(j)
        the_conflicts = kept

    index = sorted(_unique(the_conflicts + is_disclosure + is_finance + is_declare + is_dual))

    # Try to protect from mentions of words in other sections of text
    if len(index) > 1:
        a = grepl("interest.{0,1}:|Disclosure.{0,1}:", sel(index), ignore_case=True)
        first = next((i for i, hit in enumerate(a) if hit), None)
        if first is not None:
            cut = index[first]
            index = [i for i in index if i >= cut]

    # If in point form, sections are missed when they do not include the keywords.
    if (
        len(index) > 1
        and any(b - a > 1 for a, b in itertools.pairwise(index))
        and max(index) - min(index) < 10  # Safeguard
    ):
        index = list(range(min(index), max(index) + 1))

    coi_text: str = paste(sel(index), collapse=" ")

    # Identify text that may have been missed because it was in a new line
    if len(index) == 1:
        no_stop_words = gsub(" of ", " ", coi_text, ignore_case=True)
        if len(strsplit(no_stop_words, " ")) < 4 and not grepl(
            "no", at(index[0]), ignore_case=True
        ):
            nxt = at(index[0] + 1)
            if nxt is None:
                # R: if (nchar(NA_character_) == 0) -> error
                raise ValueError("missing value where TRUE/FALSE needed")
            second = index[0] + 2 if len(nxt) == 0 else index[0] + 1
            index = [index[0], second]
            new_str = gsub("^.+(None.*$)", r"\1", at(second))
            if grepl(r"^.*\.$", new_str):  # make sure this is a whole sentence
                coi_text = paste(coi_text, new_str)
            else:
                coi_text = paste(coi_text, new_str, at(second + 1))

    # Exclude other mentions of disclosure that are not disclosures of interest
    if is_disclosure and not the_conflicts:
        is_capital = grepl("Disclos|DISCLOS", coi_text)
        is_no = grepl("None|Nothing|No|Nil", coi_text) or grepl("NONE|NOTHING|NO|NIL", coi_text)
        is_punctuation = grepl("disclosure.{0,1}[.:;,]", coi_text, ignore_case=True)
        is_author = grepl("Author.{0,2} [dD]isclo", coi_text)
        if not (is_capital or is_no or is_punctuation or is_author):
            index = []
            coi_text = ""

    # Remove preceding text that is not relevant
    coi_text = gsub(_PRECEDING, r"\1\2\3\4\5\6\7", coi_text)

    # Capture disclosures that do not contain "conflict" but are relevant
    if grepl("disclosure", coi_text, ignore_case=True):
        val = "^.*conflict.*disclosure.*$|^.*compet.*disclosure.*$"
        if not grepl(val, coi_text, ignore_case=True):
            coi_text = gsub(r"^.*?(Disclosure.*$)", r"\1", coi_text)

    # If only None/No/Nothing appear, stop after the fullstop.
    # (R keeps only groups 1-4: a match of a later alternative empties the text)
    coi_text = gsub(_NONE_STOP, r"\1\2\3\4", coi_text, ignore_case=True)

    # Correct statements with repeating sentences
    new = strsplit(coi_text, r"\. {0,1}")
    if len(new) > 1 and all(s in new[:i] for i, s in enumerate(new) if i > 0):
        coi_text = new[0] + "."

    # If 'The authors declare no competing financial interests' appears, extract.
    if grepl(_AUTHORS_NO, coi_text):
        new_n = _str_count(coi_text, "[cC]ompeting|[cC]onflict")
        if new_n == 1 and not (is_disclosure or is_declare or is_dual):
            coi_text = gsub(_AUTHORS_NO, r"\1\2\3\4\5\6", coi_text)
        # Do not extract info after the last such sentence, if such info exists.
        if _str_count(coi_text, "author") < 2:
            coi_text = _cut_after_authors(coi_text)

    return trimws(coi_text)


# ---------------------------------------------------------------------------
# the module
# ---------------------------------------------------------------------------


def coi_results(table: pd.DataFrame) -> dict[str, Any]:
    """The shared tail of the COI modules: summary table, traffic light and report."""
    ids = table["paper_id"].drop_duplicates()
    summary_table = pd.DataFrame(
        {
            "paper_id": ids.reset_index(drop=True),
            "coi_found": pd.Series([True] * len(ids), dtype="boolean"),
        }
    )
    tl = "green" if len(table) else "red"
    if tl == "green":
        report: Any = [
            "The following conflict of interest statement was detected.",
            scroll_table(table["text"].tolist()),
        ]
        summary_text = "A conflict of interest statement was detected."
    else:
        report = "No conflict of interest statement was detected. Consider adding one."
        summary_text = "No conflict of interest statement was detected."
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": False,
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }


@module(
    title="COI Check",
    description="Identify and extract Conflicts of Interest (COI) statements.",
    details="""
        The COI Check module uses regular expressions to check sentences for words related to conflict of interest statements. It will return the sentences in which the conflict of interest statement was found.

        The function incorporates code from [rtransparent](https://github.com/serghiou/rtransparent), which is no longer maintained. For their validation, see [the paper](https://doi.org/10.1371/journal.pbio.3001107).
    """,
    keywords=["general"],
    author=["Daniel Lakens <d.lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def coi_check(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/coi_check.R::coi_check()``."""
    sentences = text_search(paper)
    paper_ids = sentences["paper_id"].astype("string")
    texts = sentences["text"].tolist()
    # dplyr::summarise(text = rtransparent_coi(text), .by = paper_id)
    groups: dict[Any, list[Any]] = {}
    for pid, txt in zip(paper_ids.tolist(), texts, strict=True):
        groups.setdefault(pid, []).append(txt)
    ids = list(groups)
    coi = [rtransparent_coi(groups[pid]) for pid in ids]
    keep = [c != "" for c in coi]
    table = pd.DataFrame(
        {
            "paper_id": pd.Series([p for p, k in zip(ids, keep, strict=True) if k], dtype="string"),
            "text": pd.Series([c for c, k in zip(coi, keep, strict=True) if k], dtype="string"),
        }
    )
    return coi_results(table)
