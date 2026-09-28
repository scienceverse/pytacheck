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
# \x{hhhh} and \Q...\E (literal text) in both engines
_SPECIAL = regex.compile(r"\\x\{([0-9A-Fa-f]+)\}|\\Q(.*?)(?:\\E|\Z)", regex.DOTALL)


def _special(m: Any) -> str:
    return f"\\U{int(m[1], 16):08x}" if m[1] else regex.escape(m[2])


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
            elif len(name) == 1:  # an equivalence class or collating element: the character
                out.append(regex.escape(name))
            else:
                raise RegexError(f"invalid regular expression '{p}': unknown collating element")
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
            if m := _SPECIAL.match(p, i):
                out.append(_special(m))
                i = m.end()
                continue
            if e in _TRE_ESCAPES:
                out.append(_TRE_ESCAPES[e])
            elif "0" <= e <= "9":  # a back reference
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


_VSPACE = r"\n\x0b\f\r\x85\u2028\u2029"  # PCRE's \v
_PCRE_ESCAPES = {"v": f"[{_VSPACE}]", "V": f"[^{_VSPACE}]", "Z": r"(?=\n?\Z)", "z": r"\Z"}
# fmt: off
_ASCII_ITEMS = {  # PCRE2's classes in a set are ASCII (\W \D \S [:^x:], unused, stay Unicode)
    r"\w": "0-9A-Za-z_", "[:word:]": "0-9A-Za-z_", r"\d": "0-9", "[:digit:]": "0-9",
    r"\s": r"\t\n\v\f\r ", "[:space:]": r"\t\n\v\f\r ", "[:blank:]": r"\t ",
    "[:alpha:]": "A-Za-z", "[:alnum:]": "0-9A-Za-z", "[:upper:]": "A-Z", "[:lower:]": "a-z",
    "[:punct:]": r"!-/:-@\[-`{-~", "[:xdigit:]": "0-9A-Fa-f", "[:cntrl:]": r"\x00-\x1f\x7f",
    "[:print:]": r" -~", "[:graph:]": "!-~", "[:ascii:]": r"\x00-\x7f", r"\v": _VSPACE,
}
# fmt: on
_SET_ITEM = regex.compile(_SPECIAL.pattern + r"|\\.|\[:[a-z]+:\]", regex.DOTALL)  # [\\w]: \\ w
_PCRE_TOKEN = regex.compile(  # \x{hhhh}, \Q...\E, an escape, or a set (its classes, escapes)
    _SPECIAL.pattern + r"|\\(.)|\[(\^?)(\]?(?:\[:\^?[a-z]+:\]|\\.|[^\]])*)\]", regex.DOTALL
)


def _pcre_token(m: Any) -> str:
    if m.lastindex <= 2:
        return _special(m)
    if m[3] is None:  # in sets, ASCII ranges: (?a:[...]) still folds non-ASCII letters (?i)
        items = _SET_ITEM.sub(
            lambda k: _special(k) if k.lastindex else _ASCII_ITEMS.get(k[0], k[0]), m[5]
        )
        return f"[{m[4]}{items}]"
    # ASCII classes; PCRE's \v; its \Z is the end or before a final newline, its \z the end
    return f"(?a:\\{m[3]})" if m[3] in "wWdDsSbB" else _PCRE_ESCAPES.get(m[3], m[0])


@functools.lru_cache(maxsize=8192)
def translate_pcre(p: str) -> str:
    """A PCRE2 pattern (R's: no Unicode properties) in `regex` syntax."""
    return _PCRE_TOKEN.sub(_pcre_token, p) if "\\" in p or "[:" in p else p


_LAZY = regex.compile(r"[*+?}]\?")
_NOT_QUANTIFIER = regex.compile(r"\\.|\[\^?\]?(?:\[:[a-z]+:\]|[^\]])*\]", regex.DOTALL)  # \*? [*?]


@functools.lru_cache(maxsize=8192)
def _compile(pattern: str, icase: bool, perl: bool, fixed: bool, posix: bool) -> regex.Pattern[str]:
    if fixed:
        return regex.compile(regex.escape(pattern))
    flags = regex.V0 | (regex.IGNORECASE if icase else 0)
    try:
        if perl:
            return regex.compile(translate_pcre(pattern), flags)
        flags |= regex.DOTALL
        # TRE is leftmost-longest; POSIX mode would override lazy quantifiers
        if posix and not _LAZY.search(_NOT_QUANTIFIER.sub("x", pattern)):
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


def casefold(s: str) -> str:
    """``s.casefold()``, with the dotted capital and the dotless small I as ``i``:
    ignoring case, the engine matches ``İ`` to ``i`` and ``ı`` to ``I``, which their
    case folding (``i`` + U+0307 and ``ı``) does not show."""
    if "\u0130" in s:
        s = s.replace("\u0130", "i")
    if "\u0131" in s:
        s = s.replace("\u0131", "i")
    return s.casefold()


@functools.lru_cache(maxsize=8192)
def _prefilter(pattern: str, ignore_case: bool) -> tuple[str, ...] | None:
    """Literals of which every match must contain one, or ``None``.

    metacheck's patterns are mostly keyword alternations, so this skips the
    regex engine for nearly every sentence of a paper. It only ever keeps too
    much, never too little: texts are casefolded (:func:`casefold`), which
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


def fold(s: str) -> str:
    """*s* as the literal prefilter folds it when case is ignored."""
    return s.casefold() if s.isascii() else casefold(s)


class Detector:
    """``grepl(pattern, s)`` for one string at a time (made by :func:`detector`).

    Every match's text, folded (:func:`fold`) when case is ignored, contains one
    of :attr:`literals` (``None``: none are known), so a string without any is
    rejected before the regex runs, and the regex is compiled only when a
    string gets past them. A caller testing many patterns on the same strings
    folds each string once and passes it in.
    """

    __slots__ = ("_search", "fixed", "folds", "ignore_case", "literals", "pattern", "perl")

    def __init__(self, pattern: str, ignore_case: bool, perl: bool, fixed: bool) -> None:
        self.pattern = pattern
        self.ignore_case = ignore_case
        self.perl = perl
        self.fixed = fixed
        self.literals = None if fixed else _prefilter(pattern, ignore_case)
        #: whether :attr:`literals` are looked for in the folded string
        self.folds = ignore_case and self.literals is not None
        self._search: Callable[[str], Any] | None = None

    def compile(self) -> Callable[[str], Any]:
        """The compiled pattern's ``search``; an invalid pattern raises :class:`RegexError`."""
        if self._search is None:
            args = (self.pattern, self.ignore_case, self.perl, self.fixed, False)
            self._search = _compile(*args).search
        return self._search

    def __call__(self, s: str | None, folded: str | None = None) -> bool:
        """Whether *s* contains a match (never ``None``); *folded* is ``fold(s)``, if known."""
        if s is None:
            return False
        if self.fixed:
            return self.pattern in s
        if self.literals is not None:
            hay = (fold(s) if folded is None else folded) if self.folds else s
            if not any(lit in hay for lit in self.literals):
                return False
        return (self._search or self.compile())(s) is not None


@functools.lru_cache(maxsize=8192)
def detector(
    pattern: str, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Detector:
    """The :class:`Detector` of *pattern*, made once per pattern and options."""
    return Detector(pattern, ignore_case, perl, fixed)


# ---------------------------------------------------------------------------
# R functions
# ---------------------------------------------------------------------------


def grepl(
    pattern: str, x: Any, ignore_case: bool = False, perl: bool = False, fixed: bool = False
) -> Any:
    """R ``grepl()``: does each element contain a match? ``NA`` gives ``False``."""
    match = detector(pattern, ignore_case, perl, fixed)
    if not fixed:
        match.compile()  # an invalid pattern fails whatever x is, as in R
    return _vectorize(x, lambda v: match(_as_str(v)))


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
_NULLABLE = regex.compile(r"[*?^$]|\{0?[,}]|\\[bBAZzGK<>]|\(\)|(?:^|[|(])\||\|(?:$|\))")


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
        # R's gsub loop: it stops at the end of the string, steps over one
        # character after an empty match, and does not replace an empty match
        # right at the end of the previous one (Python's sub does all three
        # differently)
        out: list[str] = []
        pos, last = 0, -1
        while (m := rx.search(s, pos)) is not None:
            out.append(s[pos : m.start()])
            if m.end() > last:
                out.append(r if isinstance(r, str) else r(m))
                last = m.end()
            pos = m.end()
            if pos >= len(s):
                break
            if m.start() == m.end():
                out.append(s[pos])
                pos += 1
        out.append(s[pos:])
        return "".join(out)

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
    """*fn* of each of the matches R's ``gregexpr()`` finds in each element.

    R's loop: the next search starts after a match, one character further
    after an empty match, and only before the end of the string (TRE finds
    nothing in ``""``).
    """
    search = _compile(pattern, *flags, True).search
    tre = not (flags[1] or flags[2])

    def each(v: Any) -> list[T]:
        s = _as_str(v)
        if s is None or (tre and not s):
            return []
        out: list[T] = []
        pos = 0
        while (m := search(s, pos)) is not None:
            out.append(fn(m))
            pos = m.end() if m.end() > m.start() else m.start() + 1
            if pos >= len(s):
                break
        return out

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
