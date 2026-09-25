"""R-compatible regular expressions.

metacheck is written in R, whose regex functions (``grepl``, ``sub``,
``gsub``, ``regexpr``, ``gregexpr``, ``regmatches``, ``strsplit``) run one of
two engines:

* ``perl = FALSE`` (the default): the TRE engine, POSIX *extended* regular
  expressions with *leftmost-longest* match semantics.
* ``perl = TRUE``: PCRE2, *leftmost-first* (backtracking) semantics.

Python's :mod:`re` behaves like neither in several ways that change results
on real papers, so every port in pytacheck must go through this module
instead of calling :mod:`re` or pandas ``.str`` regex methods directly.

The differences reproduced here were measured against R 4.5 under a
``C.UTF-8`` locale (the locale the parity goldens are generated in):

=========================  ==========================  ==========================
behaviour                  TRE (``perl=False``)        PCRE (``perl=True``)
=========================  ==========================  ==========================
alternation ``a|ab``       longest (``"ab"``)          first (``"a"``)
``\\w`` ``\\b``            Unicode letters             ASCII only
``\\d``                    ASCII ``[0-9]``             ASCII ``[0-9]``
``\\s``                    no NBSP (glibc iswspace)    ASCII whitespace only
``[[:alpha:]]`` etc.       Unicode                     ASCII only
``\\`` inside ``[...]``    a *literal* backslash       an escape
``.``                      matches ``\\n``             does not match ``\\n``
``$``                      only at the very end        end or before final ``\\n``
``\\<`` ``\\>``            word start / end            (literal ``<`` / ``>``)
case-insensitive           a letter's own upper/lower  Unicode case folding
=========================  ==========================  ==========================

Patterns passed to these functions are the *regex* after R string-literal
unescaping, i.e. the R source ``"\\\\d+"`` becomes the Python raw string
``r"\\d+"``.

All functions are vectorised like their R namesakes: they accept a scalar,
a list/tuple, or a :class:`pandas.Series` and return the same kind of
container (a Series keeps its index). Missing values (``None``, ``pd.NA``,
``NaN``) follow R: ``grepl`` gives ``False``, ``sub``/``gsub`` give ``NA``.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

import regex

from pytacheck._r import _charset as _cs
from pytacheck._r import _pcre, _tre
from pytacheck._r._charset import RegexError

__all__ = [
    "RegexError",
    "compile_r",
    "gregexpr_all",
    "grep",
    "grepl",
    "gsub",
    "regexec",
    "regextract",
    "regextract_all",
    "strsplit",
    "sub",
    "translate",
]

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Translation of R (TRE / PCRE) syntax to the Python `regex` module
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=4096)
def translate(pattern: str, perl: bool = False) -> str:
    """Translate an R regular expression into `regex`-module syntax."""
    return _pcre.translate(pattern).pattern if perl else _tre.translate(pattern).pattern


def _pcre_nullable(translated: str) -> bool:
    """Can the translated PCRE pattern match the empty string? (``True`` when unsure.)"""
    try:
        import re._parser as sre_parse  # the stdlib parser computes match widths

        return sre_parse.parse(translated).getwidth()[0] == 0
    except Exception:  # regex-module syntax the stdlib cannot parse
        return True


@dataclass(frozen=True)
class _Compiled:
    rx: regex.Pattern[str]
    nullable: bool  # can match the empty string
    wide_differs: bool  # TRE: R's wide-character mode matches differently
    # TRE with the `regex` module's word characters (see _tre.Translation.fast)
    fast: regex.Pattern[str] | None = None

    def pick(self, s: str) -> regex.Pattern[str]:
        """The pattern to run on the subject *s*."""
        if self.fast is not None and not _cs.word_divergent(s):
            return self.fast
        return self.rx


@functools.lru_cache(maxsize=4096)
def _compile(
    pattern: str,
    ignore_case: bool,
    perl: bool,
    fixed: bool,
    posix: bool,
    wide: bool = False,
    tnfa: bool = True,
) -> _Compiled:
    """Compile *pattern*; ``wide`` selects TRE's wide-character mode (see
    :func:`_compile_for`), ``tnfa`` TRE's own matcher for minimal repetitions."""
    if fixed:
        return _Compiled(regex.compile(regex.escape(pattern)), pattern == "", False)
    flags = regex.VERSION0
    wide_differs = False
    fast_body = None
    if perl:
        # PCRE2: case-insensitivity is spelled out by the translation too
        body = _pcre.translate(pattern, ignore_case).pattern
        nullable = _pcre_nullable(body)
    else:
        # TRE: case-insensitivity is spelled out by the translation
        tre = _tre.translate(pattern, ignore_case)
        body = tre.pattern_wide if wide else tre.pattern
        fast_body = tre.fast_wide if wide else tre.fast
        nullable = tre.nullable
        wide_differs = tre.pattern_wide != tre.pattern
        flags |= regex.DOTALL
        if tnfa and posix and tre.minimal and not tre.backrefs:
            # minimal repetitions: TRE's own matcher (no match is missed by
            # the `regex` translation, so detection alone does not need it)
            return _Compiled(
                _TnfaPattern(pattern, ignore_case, wide), nullable, wide_differs  # type: ignore[arg-type]
            )
        # TRE is leftmost-longest; POSIX mode (with backtracking for the rare
        # lazy repetition with back references)
        if posix and not tre.minimal:
            flags |= regex.POSIX
    try:
        fast = None if fast_body is None else regex.compile(fast_body, flags)
        return _Compiled(regex.compile(body, flags), nullable, wide_differs, fast)
    except regex.error as exc:  # pragma: no cover - message depends on regex version
        raise RegexError(f"invalid regular expression {pattern!r}: {exc}") from exc


class _TnfaMatch:
    """What :class:`_TnfaPattern` returns: the parts of ``regex.Match`` used here."""

    __slots__ = ("_s", "_spans")

    def __init__(self, s: str, spans: list[tuple[int, int]]) -> None:
        self._s, self._spans = s, spans

    def start(self, g: int = 0) -> int:
        return self._spans[g][0]

    def end(self, g: int = 0) -> int:
        return self._spans[g][1]

    def span(self, g: int = 0) -> tuple[int, int]:
        return self._spans[g]

    def group(self, g: int = 0) -> str | None:
        a, b = self._spans[g]
        return None if a < 0 else self._s[a:b]

    def groups(self) -> tuple[str | None, ...]:
        return tuple(self.group(g) for g in range(1, len(self._spans)))


class _TnfaPattern:
    """A TRE pattern with minimal repetitions, matched by TRE's own algorithm
    (:mod:`pytacheck._r._tnfa`); offers the ``regex.Pattern`` methods used here."""

    def __init__(self, pattern: str, icase: bool, wide: bool) -> None:
        from pytacheck._r import _tnfa

        self.pattern = pattern
        self._run = _tnfa.run
        self._tnfa = _tnfa.compile_tnfa(pattern, icase, wide)

    def search(self, s: str, pos: int = 0) -> _TnfaMatch | None:
        # a search from `pos` is R's search of the rest of the string (REG_NOTBOL)
        spans = self._run(self._tnfa, s, pos, pos > 0)
        return None if spans is None else _TnfaMatch(s, spans)

    def finditer(self, s: str) -> list[_TnfaMatch]:
        out, pos = [], 0
        while pos <= len(s) and (m := self.search(s, pos)) is not None:
            out.append(m)
            pos = m.end() if m.end() > m.start() else m.start() + 1
        return out

    def sub(self, repl: Callable[[Any], str], s: str, count: int = 0) -> str:
        assert count == 1 and callable(repl)
        m = self.search(s)
        return s if m is None else s[: m.start()] + repl(m) + s[m.end() :]


def _compile_for(
    pattern: str,
    ignore_case: bool,
    perl: bool,
    fixed: bool,
    x: Any,
    posix: bool = True,
    extra: str = "",
) -> _Compiled:
    """:func:`_compile` for a call on the vector *x*.

    R runs TRE in byte mode when the pattern (and *extra*, gsub's replacement)
    and every element of *x* are ASCII, and in wide-character mode otherwise;
    the two differ for a few patterns (:class:`pytacheck._r._tre.Translation`).
    """
    c = _compile(pattern, ignore_case, perl, fixed, posix)
    if c.wide_differs and not (pattern.isascii() and extra.isascii() and _all_ascii(x)):
        c = _compile(pattern, ignore_case, perl, fixed, posix, True)
    return c


def compile_r(
    pattern: str,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
    posix: bool | None = None,
    wide: bool = False,
) -> regex.Pattern[str]:
    """Compile an R pattern with R's semantics.

    ``posix`` selects leftmost-longest matching; it defaults to ``True`` for
    TRE patterns (``perl=False``), which is what R does. Pure *detection*
    (``grepl``) does not depend on it, so :func:`grepl` turns it off for speed.
    Invalid patterns raise :class:`RegexError` where R raises an error.

    The TRE word assertions (``\\b``, ``\\<``, ...) hold at the position a
    search starts from, as they do when R restarts a search after a match; use
    ``rx.search(s, pos)`` (not slicing) to continue a search. ``wide`` gives
    TRE's wide-character mode, which R uses when the pattern or any element of
    the input is non-ASCII (it only matters for negated ``[:class:]`` brackets
    in bounded repetitions such as ``\\W{2}``).

    TRE resolves minimal repetitions (``.*?``) in its own way, which only the
    R functions here reproduce (with :mod:`pytacheck._r._tnfa`); the pattern
    returned for such a TRE pattern matches minimally by backtracking.
    """
    return _compile(pattern, ignore_case, perl, fixed, posix is None or posix, wide, False).rx


# ---------------------------------------------------------------------------
# Vectorisation helpers
# ---------------------------------------------------------------------------


def is_na(x: Any) -> bool:
    """R's ``is.na()`` for a single value."""
    if x is None:
        return True
    if isinstance(x, float):
        return math.isnan(x)
    try:
        import pandas as pd

        return x is pd.NA or x is pd.NaT
    except ImportError:  # pragma: no cover
        return False


def _vectorize(x: Any, fn: Callable[[Any], T]) -> Any:
    """Apply *fn* elementwise, returning the same container type as *x*."""
    if isinstance(x, str) or x is None or not isinstance(x, Iterable):
        return fn(x)
    try:
        import pandas as pd

        if isinstance(x, pd.Series):
            return pd.Series([fn(v) for v in x], index=x.index, dtype=object)
        if isinstance(x, pd.Index):
            return [fn(v) for v in x]
    except ImportError:  # pragma: no cover
        pass
    return [fn(v) for v in x]


def _as_str(x: Any) -> str | None:
    if is_na(x):
        return None
    return x if isinstance(x, str) else str(x)


# ---------------------------------------------------------------------------
# R functions
# ---------------------------------------------------------------------------


_INLINE_FLAGS = regex.compile(r"\(\?[a-zA-Z-]*[a-zA-Z]")
_META = set("\\[](){}.*+?|^$")


def _branches(pattern: str) -> list[str] | None:
    """Split *pattern* on top-level ``|`` (``None`` if it cannot be done safely)."""
    out, depth, start, i, in_set = [], 0, 0, 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_set:
            if c == "]":
                in_set = False
        elif c == "[":
            in_set = True
            if pattern[i + 1 : i + 2] == "^":
                i += 1
            if pattern[i + 1 : i + 2] == "]":
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            out.append(pattern[start:i])
            start = i + 1
        i += 1
    if depth != 0 or in_set:
        return None
    out.append(pattern[start:])
    return out


def _leading_literal(branch: str) -> str:
    """The literal text every match of *branch* must start with."""
    i = 0
    while branch.startswith(("\\b", "\\<", "\\>", "^"), i):  # zero-width prefixes
        i += 1 if branch[i] == "^" else 2
    lit: list[str] = []
    while i < len(branch):
        c = branch[i]
        if c == "\\" and i + 1 < len(branch) and branch[i + 1] in "<>":
            break  # word anchors in TRE
        if c == "\\" and i + 1 < len(branch) and not branch[i + 1].isalnum():
            lit.append(branch[i + 1])  # escaped punctuation is a literal
            i += 2
        elif c in _META:
            break
        else:
            lit.append(c)
            i += 1
        if i < len(branch) and branch[i] in "*?{":
            lit.pop()  # the last literal is optional/repeated
            break
    return "".join(lit)


@functools.lru_cache(maxsize=4096)
def _prefilter(pattern: str, ignore_case: bool) -> tuple[str, ...] | None:
    """Literals of which every match must contain one, or ``None``.

    Used to skip the regex engine for text that cannot match: metacheck's
    patterns are mostly keyword alternations, so this avoids running the
    engine on nearly every sentence of a corpus. The test only ever keeps
    too much (never too little): texts are casefolded, which folds at least
    as much as the engine's case-insensitive matching.
    """
    if _INLINE_FLAGS.search(pattern):
        return None
    branches = _branches(pattern)
    if not branches:
        return None
    literals = []
    for b in branches:
        lit = _leading_literal(b)
        if len(lit) < 3 or not lit.isascii():
            return None
        literals.append(lit.casefold() if ignore_case else lit)
    return tuple(dict.fromkeys(literals))


def grepl(
    pattern: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``grepl()``: does each element contain a match? ``NA`` gives ``False``."""
    if fixed:
        return _vectorize(x, lambda v: (s := _as_str(v)) is not None and pattern in s)
    c = _compile_for(pattern, ignore_case, perl, False, x, posix=False)
    pick = c.pick
    literals = _prefilter(pattern, ignore_case)
    if literals is None:
        if c.fast is None:
            search = c.rx.search
            return _vectorize(x, lambda v: (s := _as_str(v)) is not None and search(s) is not None)
        return _vectorize(x, lambda v: (s := _as_str(v)) is not None and pick(s).search(s) is not None)

    def match(v: Any) -> bool:
        s = _as_str(v)
        if s is None:
            return False
        folded = s.casefold() if ignore_case else s
        if not any(lit in folded for lit in literals):
            return False
        return pick(s).search(s) is not None

    return _vectorize(x, match)


def grep(
    pattern: str,
    x: Sequence[Any],
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
    value: bool = False,
    invert: bool = False,
) -> list[Any]:
    """R ``grep()``: 0-based indices (or values) of matching elements."""
    hits = grepl(pattern, list(x), ignore_case, perl, fixed)
    items = list(x)
    return [items[i] if value else i for i, h in enumerate(hits) if h != invert]


@functools.lru_cache(maxsize=4096)
def _literal_template(repl: str, perl: bool) -> str | None:
    """A ``regex.sub`` template for *repl* if it is plain text, otherwise ``None``."""
    if "\\" in repl:
        if regex.search(r"\\[1-9]", repl) or (perl and regex.search(r"\\[ULE]", repl)):
            return None
        # an escaped character stands for itself; a trailing lone backslash is dropped
        stripped = regex.sub(r"\\(.)|\\\Z", r"\1", repl, flags=regex.DOTALL)
    else:
        stripped = repl
    return stripped.replace("\\", "\\\\")


def _r_replacement(repl: str, perl: bool) -> Callable[[regex.Match[str]], str]:
    """Build a replacement function implementing R's replacement syntax.

    ``\\1``-``\\9`` are backreferences (``\\0`` is a literal ``0``); with ``perl=TRUE``
    ``\\U``/``\\L``/``\\E`` switch case conversion, which (as in R's
    ``R_pcre_string_adj()``) applies to the back-referenced text only, never
    to literal text. Any other escaped character stands for itself, and a
    trailing lone backslash is dropped.
    """
    parts: list[tuple[str, Any]] = []
    i, n = 0, len(repl)
    lit: list[str] = []
    while i < n:
        c = repl[i]
        if c == "\\" and i + 1 == n:
            break  # R drops a trailing lone backslash
        if c == "\\" and i + 1 < n:
            e = repl[i + 1]
            if e in "123456789":
                if lit:
                    parts.append(("lit", "".join(lit)))
                    lit = []
                parts.append(("grp", int(e)))
            elif perl and e in "ULE":
                if lit:
                    parts.append(("lit", "".join(lit)))
                    lit = []
                parts.append(("case", e))
            else:
                lit.append(e)
            i += 2
        else:
            lit.append(c)
            i += 1
    if lit:
        parts.append(("lit", "".join(lit)))

    def replace(m: regex.Match[str]) -> str:
        buf: list[str] = []
        mode = "E"
        for kind, val in parts:
            if kind == "case":
                mode = val
                continue
            if kind == "grp":
                try:
                    piece = m.group(val) or ""
                except IndexError:
                    piece = ""
                if mode == "U":
                    piece = piece.upper()
                elif mode == "L":
                    piece = piece.lower()
            else:
                piece = val
            buf.append(piece)
        return "".join(buf)

    return replace


def _substitute(
    pattern: str,
    repl: str,
    x: Any,
    ignore_case: bool,
    perl: bool,
    fixed: bool,
    count: int,
) -> Any:
    if fixed:

        def fixed_sub(v: Any) -> str | None:
            s = _as_str(v)
            if s is None:
                return None
            return s.replace(pattern, repl, count if count else -1)

        return _vectorize(x, fixed_sub)
    c = _compile_for(pattern, ignore_case, perl, False, x, extra=repl)
    pick, nullable = c.pick, c.nullable
    literal = _literal_template(repl, perl)
    if literal is not None and (count == 1 or not nullable) and not isinstance(c.rx, _TnfaPattern):
        # Fast path: a plain replacement, and (for gsub) a pattern that cannot
        # match the empty string, so R's loop is exactly `regex.sub()`.
        n_sub = 1 if count == 1 else 0

        def literal_sub(v: Any) -> str | None:
            s = _as_str(v)
            return None if s is None else pick(s).sub(literal, s, count=n_sub)

        return _vectorize(x, literal_sub)
    fn = _r_replacement(repl, perl)
    if count == 1:

        def do_sub(v: Any) -> str | None:
            s = _as_str(v)
            return None if s is None else pick(s).sub(fn, s, count=1)

        return _vectorize(x, do_sub)

    def do_gsub(v: Any) -> str | None:
        s = _as_str(v)
        if s is None:
            return None
        rx = pick(s)
        # R's do_gsub(): search from `offset`; an empty match right at the end
        # of the previous match is not replaced, and after an empty match the
        # next character is copied before searching again.
        out: list[str] = []
        offset, last_end, n = 0, -1, len(s)
        search = rx.search
        while (m := search(s, offset)) is not None:
            start, end = m.span()
            out.append(s[offset:start])
            if end > last_end:
                out.append(fn(m))
                last_end = end
            offset = end
            if offset >= n:
                break
            if start == end:
                out.append(s[offset])
                offset += 1
        if not out:
            return s
        out.append(s[offset:])
        return "".join(out)

    return _vectorize(x, do_gsub)


def sub(
    pattern: str,
    replacement: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``sub()``: replace the first match in each element."""
    return _substitute(pattern, replacement, x, ignore_case, perl, fixed, 1)


def gsub(
    pattern: str,
    replacement: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``gsub()``: replace every match in each element."""
    return _substitute(pattern, replacement, x, ignore_case, perl, fixed, 0)


def regextract(
    pattern: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """First match per element, aligned with *x* (``None`` when no match).

    Note that R's ``regmatches(x, regexpr(p, x))`` *drops* non-matching
    elements; filter out the ``None`` values to reproduce that.
    """
    pick = _compile_for(pattern, ignore_case, perl, fixed, x).pick

    def first(v: Any) -> str | None:
        s = _as_str(v)
        if s is None:
            return None
        m = pick(s).search(s)
        return None if m is None else m.group(0)

    return _vectorize(x, first)


class _Span:
    """A match R reports that the `regex` module cannot produce (see
    :func:`_r_finditer`)."""

    __slots__ = ("_end", "_start", "_text")

    def __init__(self, start: int, end: int, text: str) -> None:
        self._start, self._end, self._text = start, end, text

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def group(self, _k: int = 0) -> str:
        return self._text


def _r_finditer(
    rx: regex.Pattern[str], s: str, nullable: bool = True, perl: bool = False
) -> list[Any]:
    """The matches R's ``gregexpr()`` finds.

    After a match R searches again from its end, or from the next character
    after an empty match; TRE (``perl = FALSE``) does not search at the very
    end of the string (so ``""`` has no match), PCRE does once.

    PCRE moves on by one *byte* after an empty match, so before a character of
    k UTF-8 bytes it searches k - 1 times from inside the character, where
    PCRE2 reads the continuation bytes as characters of their own (U+0080 to
    U+00BF). R reports such a match at the next character, with the bytes
    counted as characters. (Assertions looking at the bytes, such as ``\\b``
    next to them, are not reproduced exactly.)
    """
    if not nullable:
        return list(rx.finditer(s))
    out: list[Any] = []
    offset, n = 0, len(s)
    while perl or offset < n:
        m = rx.search(s, offset)
        if m is None:
            break
        out.append(m)
        if m.end() > m.start():
            offset = m.end()
        else:
            p = m.start()
            offset = p + 1
            if perl and p < n and ord(s[p]) > 0x7F:
                offset, stop = _pcre_mid_char(rx, s, p, out)
                if stop:
                    break
        if offset >= n:
            break
    return out


def _pcre_mid_char(rx: regex.Pattern[str], s: str, p: int, out: list[Any]) -> tuple[int, bool]:
    """PCRE's searches from inside the multibyte character ``s[p]``; returns
    the next search position and whether the search loop ends."""
    raw = s[p].encode("utf-8")
    width = len(raw)
    # the character's bytes as characters of their own, as PCRE2 reads them
    t = s[:p] + "".join(map(chr, raw)) + s[p + 1 :]

    def to_s(q: int) -> int:
        return q - width + 1 if q >= p + width else p + 1

    k = 1
    while k < width:
        m = rx.search(t, p + k)
        if m is None:
            return len(s), True
        if m.start() >= p + width:  # found at a later character
            start, end = to_s(m.start()), to_s(m.end())
            out.append(_Span(start, end, s[start:end]))
            return (end if end > start else start + 1), False
        # R counts the bytes read as characters: the match is reported at the
        # next character with that many characters (even past the end)
        start = p + 1
        end = start + m.end() - m.start()
        out.append(_Span(start, end, s[start:end]))
        if m.end() == m.start():
            k += 1
        elif m.end() < p + width:
            k = m.end() - p  # ended inside the character: search on from there
        else:
            return to_s(m.end()), False
    return p + 1, False


def regextract_all(
    pattern: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``regmatches(x, gregexpr(p, x))``: all matches per element."""
    c = _compile_for(pattern, ignore_case, perl, fixed, x)
    pick, nullable = c.pick, c.nullable

    def all_matches(v: Any) -> list[str]:
        s = _as_str(v)
        if s is None:
            return []
        return [m.group(0) for m in _r_finditer(pick(s), s, nullable, perl or fixed)]

    return _vectorize(x, all_matches)


def gregexpr_all(
    pattern: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``gregexpr()``: ``(start, length)`` pairs per element, 1-based starts.

    Elements without a match give an empty list (R gives ``-1``).
    """
    c = _compile_for(pattern, ignore_case, perl, fixed, x)
    pick, nullable = c.pick, c.nullable

    def spans(v: Any) -> list[tuple[int, int]]:
        s = _as_str(v)
        if s is None:
            return []
        return [
            (m.start() + 1, m.end() - m.start())
            for m in _r_finditer(pick(s), s, nullable, perl or fixed)
        ]

    return _vectorize(x, spans)


def regexec(
    pattern: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``regmatches(x, regexec(p, x))``: ``[match, group1, ...]`` or ``[]``.

    Unmatched optional groups give ``""`` like R.
    """
    pick = _compile_for(pattern, ignore_case, perl, fixed, x).pick

    def groups(v: Any) -> list[str]:
        s = _as_str(v)
        if s is None:
            return []
        m = pick(s).search(s)
        if m is None:
            return []
        return [m.group(0)] + [g if g is not None else "" for g in m.groups()]

    return _vectorize(x, groups)


def _all_ascii(x: Any) -> bool:
    if isinstance(x, str) or x is None or not isinstance(x, Iterable):
        v = _as_str(x)
        return v is None or v.isascii()
    return all(v is None or v.isascii() for v in map(_as_str, x))


def strsplit(
    x: Any,
    split: str,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``strsplit()``, including its quirks.

    The pattern is re-applied to the *remaining* string after every match
    (so anchors re-match), a leading match yields ``""``, a trailing match
    does not add a trailing ``""``, and an empty split or an empty match at
    the start of the remainder splits off single characters. With TRE
    (``perl = FALSE``) that piece is ``""`` instead for an ASCII element and
    *split* when another element is non-ASCII (R then takes its wide-character
    code path, which builds the piece with length 0 for ASCII input).
    """
    c = None if (fixed or split == "") else _compile_for(split, False, perl, False, x)
    # do_strsplit(): useBytes when everything is ASCII, otherwise wide strings
    # and mkCharWLenASCII(piece, 0, ascii_split && ascii_xi)
    wide_empty = not (perl or fixed) and split.isascii() and not _all_ascii(x)

    def split_one(v: Any) -> list[str | None]:
        s = _as_str(v)
        if s is None:
            return [None]
        pieces: list[str | None] = []
        if split == "":
            return list(s)
        ascii_v = s.isascii()
        while s:
            if fixed:
                k = s.find(split)
                if k == -1:
                    pieces.append(s)
                    break
                pieces.append(s[:k])
                s = s[k + len(split) :]
                continue
            assert c is not None
            m = c.pick(s).search(s)
            if m is None:
                pieces.append(s)
                break
            if m.end() == 0:
                # an empty match at the start: split off one character
                pieces.append("" if wide_empty and ascii_v else s[0])
                s = s[1:]
            else:
                # R tests the match *end*, so an empty match further in
                # splits there too
                pieces.append(s[: m.start()])
                s = s[m.end() :]
        return pieces

    return _vectorize(x, split_one)
