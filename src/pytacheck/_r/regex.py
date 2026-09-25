"""R's regular expression functions on the `regex` module.

metacheck's patterns are written for R's two engines, and pytacheck keeps them
as they are (docs/PORTING.md, section 3). Patterns are the regex after R's
string-literal unescaping: the R source ``"\\\\d+"`` is the Python ``r"\\d+"``.

* ``perl = FALSE`` (R's default) is TRE, a POSIX extended regex engine.
  :func:`translate_tre` rewrites the few constructs whose meaning differs in
  the `regex` module: ``\\<`` and ``\\>`` are word start and end; ``\\d`` is
  ``[0-9]``; ``\\s`` has no no-break spaces (as glibc); a backslash inside
  ``[...]`` is literal; ``.`` matches a newline; ``$`` is the very end; an
  unknown escape is the character itself. POSIX classes are the `regex`
  module's. Match extents are leftmost-longest (``regex.POSIX``), except
  for patterns with a lazy quantifier, which keep their laziness.
* ``perl = TRUE`` is PCRE2 without Unicode properties: :func:`translate_pcre`
  makes ``\\w \\d \\s \\b`` and the POSIX classes ASCII, and ``\\Z``/``\\z``
  PCRE's. Inline flags such as ``(?i)`` pass through: `regex` (``V0``) scopes
  them to the end of their group, as PCRE2 does.

The functions keep what R users see: vectorisation (a scalar, list, tuple or
Series in, the same kind out; a Series keeps its index), ``NA`` handling
(``grepl`` gives ``False``, ``sub``/``gsub`` give ``NA``), R's replacement
syntax (``\\1``-``\\9``; ``\\U \\L \\E`` with ``perl = TRUE``), and R's
rules for empty matches in ``gsub``, ``gregexpr`` and ``strsplit``. R's
error texts and TRE/PCRE behaviour on malformed patterns or glibc character
table corner cases are not reproduced: an invalid pattern raises
:class:`RegexError`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

import pandas as pd
import regex

from pytacheck._values import is_missing

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
]

T = TypeVar("T")

#: R's ``is.na()`` for one value (the shared helper).
is_na = is_missing


class RegexError(ValueError):
    """An R regular expression that cannot be compiled."""


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

# glibc's iswspace() and iswblank(): Unicode white space without the no-break
# spaces (U+00A0, U+2007, U+202F) and U+0085
_SPACE = "\\t\\n\\v\\f\\r \\u1680\\u2000-\\u2006\\u2008-\\u200a\\u2028\\u2029\\u205f\\u3000"
_BLANK = "\\t \\u1680\\u2000-\\u2006\\u2008-\\u200a\\u205f\\u3000"
_TRE_ESCAPES = {
    **{c: "\\" + c for c in "wWbBntrfax"},
    **{"<": r"\m", ">": r"\M", "d": "[0-9]", "D": "[^0-9]", "e": r"\x1b"},
    **{"s": f"[{_SPACE}]", "S": f"[^{_SPACE}]"},
}
_TRE_CLASSES = {"space": _SPACE, "blank": _BLANK}


def _tre_bracket(p: str, i: int) -> tuple[str, int]:
    """The bracket expression starting at ``p[i] == "["``, and the index after it."""
    out = ["["]
    i += 1
    if p.startswith("^", i):
        out.append("^")
        i += 1
    first = True
    while i < len(p):
        c = p[i]
        if c == "]" and not first:
            out.append("]")
            return "".join(out), i + 1
        first = False
        if p.startswith(("[:", "[=", "[."), i):
            end = p.find(p[i + 1] + "]", i + 2)
            if end < 0:
                break
            name = p[i + 2 : end]
            if p[i + 1] == ":":
                out.append(_TRE_CLASSES.get(name, f"[:{name}:]"))
            else:  # an equivalence class or collating element: the character
                out.append(regex.escape(name))
            i = end + 2
            continue
        out.append("\\" + c if c in "\\[]^" else c)  # a backslash is literal
        i += 1
    raise RegexError(f"invalid regular expression '{p}': missing ']'")


@functools.lru_cache(maxsize=8192)
def translate_tre(p: str) -> str:
    """A TRE (POSIX extended) pattern in `regex` syntax (compile with DOTALL)."""
    out: list[str] = []
    i, n = 0, len(p)
    while i < n:
        c = p[i]
        if c == "\\":
            if i + 1 == n:
                raise RegexError(f"invalid regular expression '{p}': trailing backslash")
            e = p[i + 1]
            if e == "Q":  # literal text up to \E
                end = p.find("\\E", i + 2)
                end = n if end < 0 else end
                out.append(regex.escape(p[i + 2 : end]))
                i = end + 2
                continue
            if e in _TRE_ESCAPES:
                out.append(_TRE_ESCAPES[e])
            elif e.isdigit():
                out.append("\\" + e)
            else:  # any other escaped character stands for itself ("\g" is "g")
                out.append(regex.escape(e))
            i += 2
        elif c == "[":
            s, i = _tre_bracket(p, i)
            out.append(s)
        else:
            out.append(r"\Z" if c == "$" else c)
            i += 1
    return "".join(out)


# fmt: off
_ASCII_ITEMS = {  # PCRE2's classes in a bracket expression: ASCII only
    r"\w": "0-9A-Za-z_", "[:word:]": "0-9A-Za-z_", r"\d": "0-9", "[:digit:]": "0-9",
    r"\s": r"\t\n\v\f\r ", "[:space:]": r"\t\n\v\f\r ", "[:blank:]": r"\t ",
    "[:alpha:]": "A-Za-z", "[:alnum:]": "0-9A-Za-z", "[:upper:]": "A-Z", "[:lower:]": "a-z",
    "[:punct:]": r"!-/:-@\[-`{-~", "[:xdigit:]": "0-9A-Fa-f", "[:cntrl:]": r"\x00-\x1f\x7f",
    "[:print:]": r" -~", "[:graph:]": "!-~", "[:ascii:]": r"\x00-\x7f",
}
# fmt: on
_SET_ITEM = regex.compile(r"\\[wds]|\[:[a-z]+:\]")
# an escape, or a bracket expression (its POSIX classes and escapes as groups)
_PCRE_TOKEN = regex.compile(r"\\(.)|\[(\^?)(\]?(?:\[:\^?[a-z]+:\]|\\.|[^\]])*)\]", regex.DOTALL)


def _pcre_token(m: Any) -> str:
    e = m.group(1)
    if e is None:  # inside sets, explicit ASCII ranges: a scoped (?a:[...]) still
        # folds non-ASCII letters under IGNORECASE
        body = _SET_ITEM.sub(lambda k: _ASCII_ITEMS.get(k.group(0), k.group(0)), m.group(3))
        return f"[{m.group(2)}{body}]"
    if e in "wWdDsSbB":
        return f"(?a:\\{e})"
    # PCRE's \Z is the end or before a final newline, its \z the end
    return r"(?=\n?\Z)" if e == "Z" else r"\Z" if e == "z" else m.group(0)


@functools.lru_cache(maxsize=8192)
def translate_pcre(p: str) -> str:
    """A PCRE2 pattern (R's: no Unicode properties) in `regex` syntax."""
    return _PCRE_TOKEN.sub(_pcre_token, p) if "\\" in p or "[:" in p else p


_LAZY = regex.compile(r"[*+?}]\?")


@functools.lru_cache(maxsize=8192)
def _compile(pattern: str, ignore_case: bool, perl: bool, fixed: bool, posix: bool) -> Any:
    if fixed:
        return regex.compile(regex.escape(pattern))
    flags = regex.V0 | (regex.IGNORECASE if ignore_case else 0)
    try:
        if perl:
            return regex.compile(translate_pcre(pattern), flags)
        flags |= regex.DOTALL
        # TRE is leftmost-longest; POSIX mode would override lazy quantifiers
        if posix and not _LAZY.search(pattern):
            flags |= regex.POSIX
        return regex.compile(translate_tre(pattern), flags)
    except regex.error as exc:
        raise RegexError(f"invalid regular expression '{pattern}': {exc}") from exc


def compile_r(
    pattern: str,
    ignore_case: bool = False,
    perl: bool = False,
    fixed: bool = False,
    posix: bool | None = None,
    wide: bool = False,
) -> regex.Pattern[str]:
    """Compile an R pattern into a `regex` pattern.

    ``posix`` selects leftmost-longest matching for a TRE pattern (the
    default, as R); detection alone does not depend on it, so ``posix=False``
    is faster where only whether a pattern matches is used. ``wide`` is
    accepted for compatibility and has no effect. Raises :class:`RegexError`
    for an invalid pattern.
    """
    del wide
    return _compile(pattern, ignore_case, perl, fixed, posix is None or posix)


# ---------------------------------------------------------------------------
# Vectorisation
# ---------------------------------------------------------------------------


def _vectorize(x: Any, fn: Callable[[Any], T]) -> Any:
    """Apply *fn* elementwise, returning the same container type as *x*."""
    if isinstance(x, str) or x is None or not isinstance(x, Iterable):
        return fn(x)
    if isinstance(x, pd.Series):
        return pd.Series([fn(v) for v in x], index=x.index, dtype=object)
    return [fn(v) for v in x]


def _as_str(x: Any) -> str | None:
    if type(x) is str:
        return x
    return None if is_missing(x) else str(x)


# ---------------------------------------------------------------------------
# A literal prefilter for grepl
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


_TURKISH_I = str.maketrans({"\u0130": "i", "\u0131": "i"})


def _casefold(s: str) -> str:
    """``s.casefold()``, with the dotted capital and the dotless small I as ``i``:
    ignoring case, the engine matches ``İ`` to ``i`` and ``ı`` to ``I``, which their
    case folding (``i`` + U+0307 and ``ı``) does not show."""
    return s.translate(_TURKISH_I).casefold()


@functools.lru_cache(maxsize=8192)
def _prefilter(pattern: str, ignore_case: bool) -> tuple[str, ...] | None:
    """Literals of which every match must contain one, or ``None``.

    metacheck's patterns are mostly keyword alternations, so this skips the
    regex engine for nearly every sentence of a paper. It only ever keeps too
    much, never too little: texts are casefolded (:func:`_casefold`), which
    folds at least as much as the engine's case-insensitive matching.
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


# ---------------------------------------------------------------------------
# R functions
# ---------------------------------------------------------------------------


def grepl(
    pattern: str, x: Any, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Any:
    """R ``grepl()``: does each element contain a match? ``NA`` gives ``False``."""
    if fixed:
        return _vectorize(x, lambda v: (s := _as_str(v)) is not None and pattern in s)
    search = _compile(pattern, ignore_case, perl, False, False).search
    literals = _prefilter(pattern, ignore_case)
    if literals is None:
        return _vectorize(x, lambda v: (s := _as_str(v)) is not None and search(s) is not None)

    def match(v: Any) -> bool:
        s = _as_str(v)
        if s is None:
            return False
        folded = (s.casefold() if s.isascii() else _casefold(s)) if ignore_case else s
        return any(lit in folded for lit in literals) and search(s) is not None

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
    items = list(x)
    hits = grepl(pattern, items, ignore_case, perl, fixed)
    return [items[i] if value else i for i, h in enumerate(hits) if h != invert]


@functools.lru_cache(maxsize=4096)
def _replacement(repl: str, perl: bool) -> str | Callable[[Any], str]:
    """R's replacement syntax: the literal text, or a function of the match.

    ``\\1``-``\\9`` are back references (``\\0`` is a literal ``0``); with
    ``perl=TRUE`` ``\\U``/``\\L``/``\\E`` switch case conversion, which (as in
    R) applies to back-referenced text only. Any other escaped character
    stands for itself, and a trailing lone backslash is dropped.
    """
    parts: list[tuple[str, Any]] = []
    for m in regex.finditer(r"\\([1-9])|\\([ULE])|\\(.)|\\\Z|([^\\]+)", repl, flags=regex.DOTALL):
        if m.group(1):
            parts.append(("grp", int(m.group(1))))
        elif m.group(2) and perl:
            parts.append(("case", m.group(2)))
        elif lit := m.group(2) or m.group(3) or m.group(4):
            parts.append(("lit", lit))
    if all(kind == "lit" for kind, _ in parts):
        return "".join(val for _, val in parts)

    def replace(m: Any) -> str:
        buf: list[str] = []
        mode = "E"
        for kind, val in parts:
            if kind == "case":
                mode = val
            elif kind == "lit":
                buf.append(val)
            else:
                try:
                    piece = m.group(val) or ""
                except IndexError:
                    piece = ""
                buf.append(
                    piece.upper() if mode == "U" else piece.lower() if mode == "L" else piece
                )
        return "".join(buf)

    return replace


# a pattern that can match the empty string somewhere (true when unsure)
_NULLABLE = regex.compile(r"[*?]|\{0?,|\{0\}|\\[bB<>]|[\^$]|\(\?[=!<]|\|\||\(\||\|\)|^\||\|$")


def _substitute(
    pattern: str, repl: str, x: Any, ignore_case: bool, perl: bool, fixed: bool, count: int
) -> Any:
    if fixed:
        n = 1 if count == 1 else -1
        return _vectorize(
            x, lambda v: None if (s := _as_str(v)) is None else s.replace(pattern, repl, n)
        )
    rx = _compile(pattern, ignore_case, perl, False, True)
    r = _replacement(repl, perl)
    if count == 1 or not _NULLABLE.search(pattern):
        template = r.replace("\\", "\\\\") if isinstance(r, str) else r
        return _vectorize(
            x, lambda v: None if (s := _as_str(v)) is None else rx.sub(template, s, count=count)
        )

    def rsub(s: str) -> str:
        # R's gsub does not replace an empty match right at the end of the
        # previous match (Python's sub does)
        last = -1

        def one(m: Any) -> str:
            nonlocal last
            if m.end() <= last:
                return ""
            last = m.end()
            return r if isinstance(r, str) else r(m)

        return rx.sub(one, s)

    return _vectorize(x, lambda v: None if (s := _as_str(v)) is None else rsub(s))


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


def _first(pattern: str, x: Any, flags: tuple[bool, bool, bool], fn: Callable[[Any], T]) -> Any:
    """*fn* of each element's first match (``None`` for no match or ``NA``)."""
    search = _compile(pattern, *flags, True).search
    return _vectorize(x, lambda v: fn(None if (s := _as_str(v)) is None else search(s)))


def _all(pattern: str, x: Any, flags: tuple[bool, bool, bool], fn: Callable[[Any], T]) -> Any:
    """*fn* of each of the matches R's ``gregexpr()`` finds in each element: R
    does not search again at the very end after a match, and TRE finds nothing
    in ``""``."""
    rx = _compile(pattern, *flags, True)
    tre = not (flags[1] or flags[2])

    def each(v: Any) -> list[T]:
        s = _as_str(v)
        if s is None:
            return []
        out = list(rx.finditer(s))
        if out and out[-1].start() == len(s) and (s or tre):
            out.pop()
        return [fn(m) for m in out]

    return _vectorize(x, each)


def regextract(
    pattern: str, x: Any, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Any:
    """First match per element, aligned with *x* (``None`` when no match).

    R's ``regmatches(x, regexpr(p, x))`` *drops* non-matching elements;
    filter out the ``None`` values to reproduce that.
    """
    return _first(pattern, x, (ignore_case, perl, fixed), lambda m: m and m.group(0))


def regexec(
    pattern: str, x: Any, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Any:
    """R ``regmatches(x, regexec(p, x))``: ``[match, group1, ...]`` or ``[]``.

    Unmatched optional groups give ``""`` like R.
    """
    return _first(
        pattern,
        x,
        (ignore_case, perl, fixed),
        lambda m: [] if m is None else [m.group(0), *(g or "" for g in m.groups())],
    )


def regextract_all(
    pattern: str, x: Any, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Any:
    """R ``regmatches(x, gregexpr(p, x))``: all matches per element."""
    return _all(pattern, x, (ignore_case, perl, fixed), lambda m: m.group(0))


def gregexpr_all(
    pattern: str, x: Any, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Any:
    """R ``gregexpr()``: ``(start, length)`` pairs per element, 1-based starts.

    Elements without a match give an empty list (R gives ``-1``).
    """
    return _all(
        pattern, x, (ignore_case, perl, fixed), lambda m: (m.start() + 1, m.end() - m.start())
    )


def strsplit(x: Any, split: str, perl: bool = False, fixed: bool = False) -> Any:
    """R ``strsplit()``.

    The pattern is applied again to the *rest* of the string after every
    match (so anchors match again), a leading match gives ``""``, a trailing
    match adds no trailing ``""``, and an empty split or an empty match at
    the start of the rest splits off one character.
    """
    search = _compile(split, False, perl, fixed, True).search

    def split_one(v: Any) -> list[str | None]:
        s = _as_str(v)
        if s is None:
            return [None]
        if split == "":
            return list(s)
        pieces: list[str | None] = []
        while s:
            m = search(s)
            if m is None:
                pieces.append(s)
                break
            if m.end() == 0:  # an empty match at the start: split off one character
                pieces.append(s[0])
                s = s[1:]
            else:  # R tests the match end, so an empty match further in splits too
                pieces.append(s[: m.start()])
                s = s[m.end() :]
        return pieces

    return _vectorize(x, split_one)
