"""Character sets for the R regex emulation.

R's TRE engine classifies characters with glibc (``C.UTF-8``): ``\\w`` is
``iswalnum()`` plus ``_``, ``[[:punct:]]`` is ``iswpunct()`` and so on, and
case-insensitive matching uses ``towupper()`` / ``towlower()``. None of these
agree with the Unicode properties of the `regex` module (``[[:punct:]]`` misses
``≤``, ``±`` and ``−``; ``\\w`` includes combining marks, ...), so the exact
tables are stored (:mod:`pytacheck._r._ctype_tables`, generated from R) and
every set is spelled out as code-point ranges.

Sets are sorted tuples of disjoint inclusive ``(lo, hi)`` code-point ranges.
:func:`emit_set` turns one into a `regex` expression that stays fast on ASCII
text: a long list of ranges is written as a Unicode property of the `regex`
module (computed once at run time, so the result is exact for the installed
`regex` version) plus the few ranges where the two differ.
"""

from __future__ import annotations

import functools
from array import array
from collections.abc import Iterable, Sequence
from itertools import chain

import regex

MAX_CP = 0x10FFFF
Ranges = tuple[tuple[int, int], ...]


class RegexError(ValueError):
    """An R regular expression that cannot be compiled."""


# ---------------------------------------------------------------------------
# Range-set algebra
# ---------------------------------------------------------------------------


def norm(ranges: Iterable[tuple[int, int]]) -> Ranges:
    """Sort and merge (overlapping or adjacent) ranges."""
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return tuple(out)


def union(*sets: Iterable[tuple[int, int]]) -> Ranges:
    return norm(chain.from_iterable(sets))


def complement(s: Ranges, lo: int = 0, hi: int = MAX_CP) -> Ranges:
    out: list[tuple[int, int]] = []
    cur = lo
    for a, b in s:
        if b < lo or a > hi:
            continue
        if a > cur:
            out.append((cur, a - 1))
        cur = max(cur, b + 1)
    if cur <= hi:
        out.append((cur, hi))
    return tuple(out)


def intersect(x: Ranges, y: Ranges) -> Ranges:
    out: list[tuple[int, int]] = []
    i = j = 0
    while i < len(x) and j < len(y):
        a = max(x[i][0], y[j][0])
        b = min(x[i][1], y[j][1])
        if a <= b:
            out.append((a, b))
        if x[i][1] < y[j][1]:
            i += 1
        else:
            j += 1
    return tuple(out)


def difference(x: Ranges, y: Ranges) -> Ranges:
    return intersect(x, complement(y))


def contains(s: Ranges, c: int) -> bool:
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi) // 2
        if s[mid][1] < c:
            lo = mid + 1
        else:
            hi = mid
    return lo < len(s) and s[lo][0] <= c


def chars(cs: Iterable[int]) -> Ranges:
    return norm((c, c) for c in cs)


ASCII: Ranges = ((0, 0x7F),)
NON_ASCII: Ranges = ((0x80, MAX_CP),)
ALL: Ranges = ((0, MAX_CP),)


# ---------------------------------------------------------------------------
# glibc tables (TRE)
# ---------------------------------------------------------------------------


def _decode_ranges(parts: Sequence[str]) -> Ranges:
    out: list[tuple[int, int]] = []
    prev = 0
    for tok in "".join(parts).split():
        gap, _, length = tok.partition(".")
        lo = prev + int(gap, 36)
        hi = lo + (int(length, 36) if length else 0)
        out.append((lo, hi))
        prev = hi + 1
    return tuple(out)


def _decode_map(parts: Sequence[str]) -> dict[int, int]:
    out: dict[int, int] = {}
    for tok in "".join(parts).split():
        start, count, delta, step = (int(v, 36) for v in tok.split("."))
        for k in range(count):
            c = start + k * step
            out[c] = c + delta
    return out


@functools.cache
def glibc_class(name: str) -> Ranges | None:
    """Members of the glibc class *name* (``None`` for an unknown name)."""
    from pytacheck._r._ctype_tables import CLASSES

    parts = CLASSES.get(name)
    return None if parts is None else _decode_ranges(parts)


@functools.cache
def case_maps() -> tuple[dict[int, int], dict[int, int]]:
    """glibc ``towupper()`` and ``towlower()`` (only the code points they change)."""
    from pytacheck._r._ctype_tables import TOLOWER, TOUPPER

    return _decode_map(TOUPPER), _decode_map(TOLOWER)


def towupper(c: int) -> int:
    return case_maps()[0].get(c, c)


def towlower(c: int) -> int:
    return case_maps()[1].get(c, c)


@functools.cache
def _case_sets() -> tuple[Ranges, Ranges]:
    upper, lower = glibc_class("upper"), glibc_class("lower")
    assert upper is not None and lower is not None
    return upper, lower


def iswupper(c: int) -> bool:
    return contains(_case_sets()[0], c)


def iswlower(c: int) -> bool:
    return contains(_case_sets()[1], c)


@functools.cache
def glibc_class_icase(name: str) -> Ranges | None:
    """TRE's ``[[:name:]]`` under ``REG_ICASE``: characters whose ``towlower()``
    or ``towupper()`` is in the class."""
    base = glibc_class(name)
    if base is None:
        return None
    up, low = case_maps()
    extra = [c for c, m in chain(up.items(), low.items()) if contains(base, m)]
    return union(base, chars(extra))


@functools.cache
def tre_word() -> Ranges:
    """TRE's word characters (``IS_WORD_CHAR``): ``_`` and ``iswalnum()``."""
    alnum = glibc_class("alnum")
    assert alnum is not None
    return union(alnum, ((0x5F, 0x5F),))


def tre_literal_icase(c: int) -> Ranges:
    """A literal under TRE's ``REG_ICASE``: ``towupper(c)`` and ``towlower(c)``
    for a letter (``iswupper()`` / ``iswlower()``), otherwise the character."""
    if iswupper(c) or iswlower(c):
        return chars((towupper(c), towlower(c)))
    return ((c, c),)


def tre_range_icase(lo: int, hi: int) -> list[tuple[int, int]]:
    """The opposite-case items TRE adds for a bracket item ``lo-hi`` under
    ``REG_ICASE`` (``tre_parse_bracket_items()``), in TRE's order."""
    out: list[tuple[int, int]] = []
    c = lo
    while c <= hi:
        if iswlower(c):
            cmin = ccurr = towupper(c)
            c += 1
            while c <= hi and iswlower(c) and towupper(c) == ccurr + 1:
                ccurr = towupper(c)
                c += 1
            out.append((cmin, ccurr))
        elif iswupper(c):
            cmin = ccurr = towlower(c)
            c += 1
            while c <= hi and iswupper(c) and towlower(c) == ccurr + 1:
                ccurr = towlower(c)
                c += 1
            out.append((cmin, ccurr))
        else:
            # skip the stretch without case in one step
            nxt = _next_cased(c, hi)
            c = nxt
    return out


def _next_cased(c: int, hi: int) -> int:
    """The first code point >= *c* that is upper or lower case (``hi + 1`` if none)."""
    upper, lower = _case_sets()
    best = hi + 1
    for s in (upper, lower):
        lo_i, hi_i = 0, len(s)
        while lo_i < hi_i:
            mid = (lo_i + hi_i) // 2
            if s[mid][1] < c:
                lo_i = mid + 1
            else:
                hi_i = mid
        if lo_i < len(s):
            best = min(best, max(c, s[lo_i][0]))
    return best


# ---------------------------------------------------------------------------
# Emitting sets as `regex` syntax
# ---------------------------------------------------------------------------


def _esc(c: int) -> str:
    if c < 0x80 and chr(c).isalnum():
        return chr(c)
    if c <= 0xFF:
        return f"\\x{c:02x}"
    if c <= 0xFFFF:
        return f"\\u{c:04x}"
    return f"\\U{c:08x}"


def _items(s: Ranges) -> str:
    out = []
    for lo, hi in s:
        if lo == hi:
            out.append(_esc(lo))
        elif hi == lo + 1:
            out.append(_esc(lo) + _esc(hi))
        else:
            out.append(f"{_esc(lo)}-{_esc(hi)}")
    return "".join(out)


def _plain(s: Ranges) -> str:
    if len(s) == 1 and s[0][0] == s[0][1]:
        c = s[0][0]
        return regex.escape(chr(c)) if c < 0x80 else _esc(c)
    return f"[{_items(s)}]"


@functools.cache
def _all_chars() -> str:
    """Every code point except the surrogates, as one string."""
    cps = array("I", chain(range(0xD800), range(0xE000, MAX_CP + 1)))
    return cps.tobytes().decode("utf-32-le" if array("I", [1]).tobytes()[0] else "utf-32-be")


@functools.cache
def regex_property(name: str) -> Ranges:
    """The non-ASCII members of the `regex` module's ``\\p{name}``."""
    return intersect(sweep(rf"\p{{{name}}}"), NON_ASCII)


def sweep(expr: str) -> Ranges:
    """The code points (surrogates excluded) that the `regex` expression *expr*
    matches as a single character."""
    out: list[tuple[int, int]] = []
    for m in regex.finditer(f"(?:{expr})+", _all_chars()):
        lo, hi = m.start(), m.end() - 1
        # positions after the surrogate gap are 0x800 below their code points
        if lo < 0xD800 <= hi:
            out.append((lo, 0xD7FF))
            lo = 0xD800
        if lo >= 0xD800:
            lo, hi = lo + 0x800, hi + 0x800
        out.append((lo, hi))
    return norm(out)


# A recipe is a conjunction of `regex` properties, each negated or not; the
# non-ASCII part of a set is written as `recipe` minus some ranges plus others.
_RECIPES: tuple[tuple[tuple[str, bool], ...], ...] = (
    (("Alnum", False),),
    (("Alnum", True),),
    (("Graph", False),),
    (("Graph", True),),
    (("Print", False),),
    (("Print", True),),
    (("Graph", False), ("Alnum", True)),
    (("Lowercase", False),),
    (("Lowercase", True),),
    (("Cased", False), ("Lowercase", True)),
    (("Uppercase", False),),
    (("Cased", False), ("Uppercase", True)),
)
_PLAIN_LIMIT = 12  # this many non-ASCII ranges are fine as a plain set


@functools.cache
def _recipe_set(recipe: tuple[tuple[str, bool], ...]) -> Ranges:
    out = NON_ASCII
    for name, neg in recipe:
        p = regex_property(name)
        out = intersect(out, complement(p, 0x80) if neg else p)
    return out


@functools.cache
def emit_set(s: Ranges) -> str:
    """A `regex` expression matching exactly one character of *s*."""
    if not s:
        return "(?!)"
    asc = intersect(s, ASCII)
    non = intersect(s, NON_ASCII)
    if not non or non == NON_ASCII or len(non) <= _PLAIN_LIMIT:
        return _plain(norm(asc + non))
    best: tuple[int, int, Ranges, Ranges] | None = None  # cost, recipe index, minus, plus
    for k, recipe in enumerate(_RECIPES):
        rs = _recipe_set(recipe)
        minus, plus = difference(rs, non), difference(non, rs)
        cost = len(minus) + len(plus)
        if best is None or cost < best[0]:
            best = (cost, k, minus, plus)
    assert best is not None
    if best[0] >= len(non):
        return _plain(s)
    _, k, minus, plus = best
    # [^\x00-\x7f ...]: a non-ASCII character in every property, not in `minus`
    neg = "".join(f"\\P{{{n}}}" if not negated else f"\\p{{{n}}}" for n, negated in _RECIPES[k])
    parts = []
    first = norm(asc + plus)
    if first:
        parts.append(_plain(first))
    parts.append(f"[^\\x00-\\x7f{neg}{_items(minus)}]")
    return parts[0] if len(parts) == 1 else "(?:" + "|".join(parts) + ")"


# ---------------------------------------------------------------------------
# PCRE2 caseless matching
# ---------------------------------------------------------------------------


@functools.cache
def _pcre_caseless() -> tuple[dict[int, tuple[int, ...]], list[int]]:
    from pytacheck._r._ctype_tables import PCRE_CASELESS

    groups: dict[int, tuple[int, ...]] = {}
    for tok in "".join(PCRE_CASELESS).split():
        members = tuple(int(v, 36) for v in tok.split("."))
        for c in members:
            groups[c] = members
    return groups, sorted(groups)


def pcre_caseless(c: int) -> tuple[int, ...]:
    """The characters PCRE2 matches *c* with under ``PCRE2_CASELESS`` (with *c*)."""
    return _pcre_caseless()[0].get(c, (c,))


def pcre_caseless_closure(s: Ranges) -> Ranges:
    """*s* plus the caseless equivalents of its members (PCRE2)."""
    import bisect

    groups, keys = _pcre_caseless()
    extra: list[int] = []
    for lo, hi in s:
        for k in range(bisect.bisect_left(keys, lo), bisect.bisect_right(keys, hi)):
            extra.extend(groups[keys[k]])
    return union(s, chars(extra)) if extra else s


@functools.cache
def _word_divergent() -> frozenset[int]:
    """Characters that are word characters for the `regex` module's ``\\w``
    but not for glibc (TRE), or vice versa: combining marks, most of them."""
    word = tre_word()
    native = sweep(r"\w")
    d = union(difference(native, word), difference(word, native))
    return frozenset(c for lo, hi in d for c in range(lo, hi + 1))


_NON_ASCII_RUN = regex.compile(r"[^\x00-\x7f]+")


def word_divergent(s: str) -> bool:
    """Does *s* contain a character on which TRE's and the `regex` module's
    word characters disagree?"""
    if s.isascii():
        return False
    d = _word_divergent()
    return any(not d.isdisjoint(map(ord, m.group())) for m in _NON_ASCII_RUN.finditer(s))
