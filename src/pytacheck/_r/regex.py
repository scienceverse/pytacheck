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
case-insensitive           Unicode                     Unicode
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
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

import regex

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


class RegexError(ValueError):
    """An R regular expression that cannot be compiled."""


# ---------------------------------------------------------------------------
# Translation of R (TRE / PCRE) syntax to the Python `regex` module
# ---------------------------------------------------------------------------

# glibc iswspace() in a UTF-8 locale: ASCII whitespace plus the Unicode space
# separators *except* the no-break ones (U+00A0, U+2007, U+202F). The ASCII
# information separators U+001C-U+001F are *not* space for TRE (measured: R's
# grepl("\\s") / "[[:space:]]" are FALSE for them, unlike Python's isspace()).
_TRE_SPACE_CHARS = r"\t\n\x0b\x0c\r \u1680\u2000-\u2006\u2008-\u200a\u2028\u2029\u205f\u3000"
_TRE_ESCAPES = {
    "d": "[0-9]",
    "D": "[^0-9]",
    "s": f"[{_TRE_SPACE_CHARS}]",
    "S": f"[^{_TRE_SPACE_CHARS}]",
    "<": r"\m",
    ">": r"\M",
}
# glibc classifies the no-break spaces as punctuation, not space.
_TRE_POSIX_CLASSES = {
    "space": _TRE_SPACE_CHARS,
    # glibc's iswcntrl() (C.UTF-8) also counts the line/paragraph separators
    "cntrl": r"[:cntrl:]\u2028\u2029",
    "punct": r"[:punct:]\xa0\u2007\u202f",
    "blank": r"\t \u1680\u2000-\u2006\u2008-\u200a\u205f\u3000",
}
# Escapes that mean the same thing in TRE and in `regex` (Unicode mode).
_TRE_KEEP = set("wWbBntrfv") | set("0123456789")
# ASCII-only character classes for PCRE (R does not enable PCRE2_UCP).
_ASCII_WORD = "A-Za-z0-9_"
_PCRE_SET_ESCAPES = {
    "w": _ASCII_WORD,
    "d": "0-9",
    "s": r"\t\n\x0b\x0c\r ",
    "h": r"\t \xa0\u1680\u180e\u2000-\u200a\u202f\u205f\u3000",
    "v": r"\n\x0b\x0c\r\x85\u2028\u2029",
}
_PCRE_ESCAPES = {
    "w": f"[{_ASCII_WORD}]",
    "W": f"[^{_ASCII_WORD}]",
    "d": "[0-9]",
    "D": "[^0-9]",
    "s": f"[{_PCRE_SET_ESCAPES['s']}]",
    "S": f"[^{_PCRE_SET_ESCAPES['s']}]",
    "h": f"[{_PCRE_SET_ESCAPES['h']}]",
    "H": f"[^{_PCRE_SET_ESCAPES['h']}]",
    "v": f"[{_PCRE_SET_ESCAPES['v']}]",
    "V": f"[^{_PCRE_SET_ESCAPES['v']}]",
    "b": r"(?a:\b)",
    "B": r"(?a:\B)",
    "z": r"\Z",
    "Z": r"(?=\n?\Z)",
    "e": r"\x1b",
    "R": r"(?:\r\n|[\n\x0b\x0c\r\x85\u2028\u2029])",
}
_POSIX_ASCII = {
    "alpha": "A-Za-z",
    "digit": "0-9",
    "alnum": "A-Za-z0-9",
    "upper": "A-Z",
    "lower": "a-z",
    "space": r"\t\n\x0b\x0c\r ",
    "blank": r"\t ",
    "punct": r"!-/:-@\[-`{-~",
    "xdigit": "0-9A-Fa-f",
    "cntrl": r"\x00-\x1f\x7f",
    "print": r"\x20-\x7e",
    "graph": r"\x21-\x7e",
    "word": _ASCII_WORD,
}


def _parse_brace_hex(pattern: str, i: int) -> tuple[str, int]:
    """Parse ``\\x{hhhh}`` starting at the ``{`` in position *i*."""
    end = pattern.find("}", i)
    if end == -1:
        raise RegexError(f"unterminated \\x{{...}} in {pattern!r}")
    return chr(int(pattern[i + 1 : end], 16)), end + 1


def _translate_tre(pattern: str) -> str:
    out: list[str] = []
    i, n = 0, len(pattern)
    depth = 0  # open groups; TRE reads an unmatched ")" as a literal
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 >= n:
                raise RegexError(f"trailing backslash in {pattern!r}")
            e = pattern[i + 1]
            if e in _TRE_ESCAPES:
                out.append(_TRE_ESCAPES[e])
            elif e in _TRE_KEEP:
                out.append("\\" + e)
            elif e == "x" and i + 2 < n and pattern[i + 2] == "{":
                ch, i = _parse_brace_hex(pattern, i + 2)
                out.append(regex.escape(ch))
                continue
            else:
                # TRE treats any other escaped character as itself.
                out.append(regex.escape(e))
            i += 2
        elif c == "[":
            i = _copy_posix_bracket(pattern, i, out)
        elif c == "$":
            out.append(r"\Z")
            i += 1
        elif c == "(":
            depth += 1
            out.append(c)
            i += 1
        elif c == ")":
            if depth:
                depth -= 1
                out.append(c)
            else:
                out.append(r"\)")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _copy_posix_bracket(pattern: str, i: int, out: list[str]) -> int:
    """Copy a POSIX bracket expression, where ``\\`` is a literal character."""
    n = len(pattern)
    j = i + 1
    buf = ["["]
    if j < n and pattern[j] == "^":
        buf.append("^")
        j += 1
    if j < n and pattern[j] == "]":
        buf.append(r"\]")
        j += 1
    while j < n:
        c = pattern[j]
        if c == "]":
            buf.append("]")
            out.append("".join(buf))
            return j + 1
        if c == "[" and j + 1 < n and pattern[j + 1] in ":=.":
            kind = pattern[j + 1]
            end = pattern.find(kind + "]", j + 2)
            if end == -1:
                raise RegexError(f"unterminated [{kind} in {pattern!r}")
            name = pattern[j + 2 : end]
            if kind == ":":
                buf.append(_TRE_POSIX_CLASSES.get(name, f"[:{name}:]"))
            else:  # collating element / equivalence class: treat as literal
                buf.append(regex.escape(name))
            j = end + 2
            continue
        if c in "\\[&~|":
            buf.append("\\" + c)
        else:
            buf.append(c)
        j += 1
    raise RegexError(f"unterminated bracket expression in {pattern!r}")


def _translate_pcre(pattern: str) -> str:
    out: list[str] = []
    n = len(pattern)
    i = 0
    # Leading PCRE verbs such as (*UCP) or (*UTF8).
    ucp = False
    while pattern.startswith("(*", i):
        end = pattern.find(")", i)
        verb = pattern[i + 2 : end]
        ucp = ucp or verb == "UCP"
        i = end + 1
    in_set = False
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 >= n:
                raise RegexError(f"trailing backslash in {pattern!r}")
            e = pattern[i + 1]
            if e == "Q":
                end = pattern.find(r"\E", i + 2)
                literal = pattern[i + 2 :] if end == -1 else pattern[i + 2 : end]
                out.append(regex.escape(literal))
                i = n if end == -1 else end + 2
                continue
            if e == "E":
                i += 2
                continue
            if e == "x" and i + 2 < n and pattern[i + 2] == "{":
                ch, i = _parse_brace_hex(pattern, i + 2)
                out.append(regex.escape(ch))
                continue
            if in_set:
                if not ucp and e in _PCRE_SET_ESCAPES:
                    out.append(_PCRE_SET_ESCAPES[e])
                elif e in "WDSHV" and not ucp:
                    # A negated class inside a set cannot be spelled with
                    # ranges; fall back to a scoped-ASCII escape. The whole
                    # set is wrapped in (?a:...) when it closes.
                    out.append("\\" + e)
                else:
                    out.append("\\" + e)
            elif ucp and e in "wWdDsSbB":
                out.append("\\" + e)
            elif e in _PCRE_ESCAPES:
                out.append(_PCRE_ESCAPES[e])
            else:
                out.append("\\" + e)
            i += 2
            continue
        if in_set:
            if c == "[" and i + 1 < n and pattern[i + 1] == ":":
                end = pattern.find(":]", i + 2)
                if end == -1:
                    raise RegexError(f"unterminated [: in {pattern!r}")
                name = pattern[i + 2 : end]
                neg = name.startswith("^")
                name = name.lstrip("^")
                if not ucp and name in _POSIX_ASCII and not neg:
                    out.append(_POSIX_ASCII[name])
                else:
                    out.append(f"[:{'^' if neg else ''}{name}:]")
                i = end + 2
                continue
            if c == "]":
                out.append("]")
                in_set = False
                i += 1
                continue
            if c in "[&~|":
                out.append("\\" + c)
            else:
                out.append(c)
            i += 1
            continue
        if c == "[":
            in_set = True
            out.append("[")
            i += 1
            if i < n and pattern[i] == "^":
                out.append("^")
                i += 1
            if i < n and pattern[i] == "]":
                out.append(r"\]")
                i += 1
            continue
        out.append(c)
        i += 1
    if in_set:
        raise RegexError(f"unterminated character class in {pattern!r}")
    translated = "".join(out)
    if not ucp and any(f"\\{e}" in translated for e in "WDSHV"):
        # only reachable for negated escapes inside sets; scope them ASCII
        translated = f"(?a:{translated})"
    return translated


_LAZY = regex.compile(r"(?<!\\)(?:[*+?]|\})\?")


@functools.lru_cache(maxsize=4096)
def translate(pattern: str, perl: bool = False) -> str:
    """Translate an R regular expression into `regex`-module syntax."""
    return _translate_pcre(pattern) if perl else _translate_tre(pattern)


@functools.lru_cache(maxsize=4096)
def compile_r(
    pattern: str,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
    posix: bool | None = None,
) -> regex.Pattern[str]:
    """Compile an R pattern with R's semantics.

    ``posix`` selects leftmost-longest matching; it defaults to ``True`` for
    TRE patterns (``perl=False``), which is what R does. Pure *detection*
    (``grepl``) does not depend on it, so :func:`grepl` turns it off for speed.
    """
    if fixed:
        return regex.compile(regex.escape(pattern))
    flags = regex.VERSION0
    if ignore_case:
        flags |= regex.IGNORECASE
    if perl:
        body = translate(pattern, True)
    else:
        body = translate(pattern, False)
        flags |= regex.DOTALL
        # TRE is leftmost-longest, but honours lazy quantifiers (`.*?`)
        # as minimal; backtracking gives that result, POSIX mode does not.
        if (posix is None or posix) and not _LAZY.search(pattern):
            flags |= regex.POSIX
    try:
        return regex.compile(body, flags)
    except regex.error as exc:  # pragma: no cover - message depends on regex version
        raise RegexError(f"invalid regular expression {pattern!r}: {exc}") from exc


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
    rx = compile_r(pattern, ignore_case, perl, False, posix=False)
    search = rx.search
    literals = _prefilter(pattern, ignore_case)
    if literals is None:
        return _vectorize(x, lambda v: (s := _as_str(v)) is not None and search(s) is not None)

    def match(v: Any) -> bool:
        s = _as_str(v)
        if s is None:
            return False
        folded = s.casefold() if ignore_case else s
        if not any(lit in folded for lit in literals):
            return False
        return search(s) is not None

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
def _literal_template(repl: str, perl: bool, translated: str) -> str | None:
    """A ``regex.sub`` template for *repl*, if it is plain text and *translated*
    provably cannot match the empty string; otherwise ``None``."""
    if "\\" in repl:
        stripped = regex.sub(r"\\(.)", r"\1", repl, flags=regex.DOTALL)
        if regex.search(r"\\[1-9]", repl) or (perl and regex.search(r"\\[ULE]", repl)):
            return None
    else:
        stripped = repl
    try:
        import re._parser as sre_parse  # the stdlib parser computes match widths

        if sre_parse.parse(translated).getwidth()[0] == 0:
            return None
    except Exception:  # regex-module syntax the stdlib cannot parse: slow path
        return None
    return stripped.replace("\\", "\\\\")


def _r_replacement(repl: str, perl: bool) -> Callable[[regex.Match[str]], str]:
    """Build a replacement function implementing R's replacement syntax.

    ``\\1``-``\\9`` are backreferences (``\\0`` is a literal ``0``); with ``perl=TRUE``
    ``\\U``/``\\L``/``\\E`` switch case conversion. Any other escaped
    character stands for itself.
    """
    parts: list[tuple[str, Any]] = []
    i, n = 0, len(repl)
    lit: list[str] = []
    while i < n:
        c = repl[i]
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
            else:
                piece = val
            if mode == "U":
                piece = piece.upper()
            elif mode == "L":
                piece = piece.lower()
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
    rx = compile_r(pattern, ignore_case, perl, False)
    literal = _literal_template(repl, perl, rx.pattern)
    if literal is not None:
        # Fast path: a plain replacement for a pattern that can never match the
        # empty string needs neither R's replacement syntax nor the empty-match
        # guard below, so the substitution runs entirely in C.
        n_sub = 1 if count == 1 else 0

        def literal_sub(v: Any) -> str | None:
            s = _as_str(v)
            return None if s is None else rx.sub(literal, s, count=n_sub)

        return _vectorize(x, literal_sub)
    fn = _r_replacement(repl, perl)

    def do_sub(v: Any) -> str | None:
        s = _as_str(v)
        if s is None:
            return None
        if count == 1:
            return rx.sub(fn, s, count=1)
        prev_end = -1

        def guarded(m: regex.Match[str]) -> str:
            # R does not allow an empty match adjacent to the previous match.
            nonlocal prev_end
            if m.start() == m.end() == prev_end:
                return ""
            prev_end = m.end() if m.end() > m.start() else -1
            return fn(m)

        return rx.sub(guarded, s)

    return _vectorize(x, do_sub)


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
    rx = compile_r(pattern, ignore_case, perl, fixed)

    def first(v: Any) -> str | None:
        s = _as_str(v)
        if s is None:
            return None
        m = rx.search(s)
        return None if m is None else m.group(0)

    return _vectorize(x, first)


def _r_finditer(rx: regex.Pattern[str], s: str) -> list[regex.Match[str]]:
    """``finditer`` minus empty matches adjacent to a previous match (as R)."""
    out: list[regex.Match[str]] = []
    prev_end = -1
    for m in rx.finditer(s):
        if m.start() == m.end() == prev_end:
            continue
        out.append(m)
        prev_end = m.end() if m.end() > m.start() else -1
    return out


def regextract_all(
    pattern: str,
    x: Any,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``regmatches(x, gregexpr(p, x))``: all matches per element."""
    rx = compile_r(pattern, ignore_case, perl, fixed)

    def all_matches(v: Any) -> list[str]:
        s = _as_str(v)
        if s is None:
            return []
        return [m.group(0) for m in _r_finditer(rx, s)]

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
    rx = compile_r(pattern, ignore_case, perl, fixed)

    def spans(v: Any) -> list[tuple[int, int]]:
        s = _as_str(v)
        if s is None:
            return []
        return [(m.start() + 1, m.end() - m.start()) for m in _r_finditer(rx, s)]

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
    rx = compile_r(pattern, ignore_case, perl, fixed)

    def groups(v: Any) -> list[str]:
        s = _as_str(v)
        if s is None:
            return []
        m = rx.search(s)
        if m is None:
            return []
        return [m.group(0)] + [g if g is not None else "" for g in m.groups()]

    return _vectorize(x, groups)


def strsplit(
    x: Any,
    split: str,
    perl: bool = False,
    fixed: bool = False,
) -> Any:
    """R ``strsplit()``, including its quirks.

    The pattern is re-applied to the *remaining* string after every match
    (so anchors re-match), a leading match yields ``""``, a trailing match
    does not add a trailing ``""``, and an empty split or empty match splits
    off single characters.
    """
    rx = None if (fixed or split == "") else compile_r(split, False, perl, False)

    def split_one(v: Any) -> list[str | None]:
        s = _as_str(v)
        if s is None:
            return [None]
        pieces: list[str | None] = []
        if split == "":
            return list(s)
        while s:
            if fixed:
                k = s.find(split)
                if k == -1:
                    pieces.append(s)
                    break
                pieces.append(s[:k])
                s = s[k + len(split) :]
                continue
            assert rx is not None
            m = rx.search(s)
            if m is None:
                pieces.append(s)
                break
            if m.end() == m.start():
                pieces.append(s[0])
                s = s[1:]
            else:
                pieces.append(s[: m.start()])
                s = s[m.end() :]
        return pieces

    return _vectorize(x, split_one)
