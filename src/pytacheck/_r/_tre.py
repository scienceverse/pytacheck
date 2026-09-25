"""R's TRE regular expressions (``perl = FALSE``) in the `regex` module.

:func:`parse` is a port of TRE's parser (R's ``src/extra/tre/tre-parse.c``,
R 4.5), including its stack machine, so a pattern means exactly what it means
to R: the same errors (``"a{2"``, ``"c++"``), the same empty atoms (``"+a"``
repeats nothing), ``(?i)`` extending to the end of the enclosing group, the
TRE bracket rules (a backslash is literal, ``[a-c-e]`` is an error, negated
brackets built from sorted items) and ``REG_ICASE`` spelled out character by
character with glibc's ``towupper()`` / ``towlower()``.

:func:`translate` emits `regex` syntax for the parsed pattern: character sets
are exact glibc sets (:mod:`pytacheck._r._charset`), and the word assertions
``\\b \\B \\< \\>`` test glibc word characters with TRE's rule that the start
of a search (where R restarts ``gsub()`` / ``gregexpr()``) is preceded by a
non-word character, and that ``\\b`` always holds at the start and end.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import regex

from pytacheck._r import _charset as cs
from pytacheck._r._charset import RegexError

# TRE compile flags that the parser tracks (REG_EXTENDED is always on in R).
ICASE = 1
NEWLINE = 2
RIGHT_ASSOC = 4
UNGREEDY = 8
LITERAL = 16

RE_DUP_MAX = 255

# TRE's error messages (tre_error()), as R reports them.
_MESSAGES = {
    "BADPAT": "Invalid regexp",
    "ECOLLATE": "Unknown collating element",
    "ECTYPE": "Unknown character class name",
    "EESCAPE": "Trailing backslash",
    "ESUBREG": "Invalid back reference",
    "EBRACK": "Missing ']'",
    "EPAREN": "Missing ')'",
    "EBRACE": "Missing '}'",
    "BADBR": "Invalid contents of {}",
    "ERANGE": "Invalid character range",
    "BADRPT": "Invalid use of repetition operators",
}


class TREError(RegexError):
    """A pattern R's TRE refuses (``regcomp()`` error)."""

    def __init__(self, pattern: str, code: str) -> None:
        reason = _MESSAGES.get(code, code)
        super().__init__(f"invalid regular expression '{pattern}', reason '{reason}'")
        self.code = code


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class Node:
    kind: str  # set, empty, assert, backref, cat, union, iter
    chars: cs.Ranges = ()  # set
    name: str = ""  # assert: BOL EOL BOW EOW WB WB_NEG
    ref: int = 0  # backref
    left: Node | None = None  # cat / union / iter operand
    right: Node | None = None
    min: int = 0
    max: int = -1
    minimal: bool = False
    submatch_id: int = -1
    # a negated bracket's set without its [:class:] exclusions: what the copies
    # made by tre_expand_ast() match (tre_copy_ast() drops `neg_classes`)
    copy_chars: cs.Ranges | None = None
    # the number of literal nodes TRE builds for a set (a bracket or a
    # case-insensitive letter is a union of them), for pytacheck._r._tnfa
    n_items: int = 1


def _set(ranges: cs.Ranges) -> Node:
    return Node("set", chars=ranges)


def _char(c: int) -> Node:
    return Node("set", chars=((c, c),))


_EMPTY_KIND = "empty"


def _empty() -> Node:
    return Node(_EMPTY_KIND)


# ---------------------------------------------------------------------------
# Parser (port of tre_parse)
# ---------------------------------------------------------------------------

_MACROS = {
    "t": "\t",
    "n": "\n",
    "r": "\r",
    "f": "\f",
    "a": "\a",
    "e": "\x1b",
    "w": "[[:alnum:]_]",
    "W": "[^[:alnum:]_]",
    "s": "[[:space:]]",
    "S": "[^[:space:]]",
    "d": "[[:digit:]]",
    "D": "[^[:digit:]]",
}

(
    PARSE_RE,
    PARSE_ATOM,
    PARSE_MARK_FOR_SUBMATCH,
    PARSE_BRANCH,
    PARSE_PIECE,
    PARSE_CATENATION,
    PARSE_POST_CATENATION,
    PARSE_UNION,
    PARSE_POST_UNION,
    PARSE_POSTFIX,
    PARSE_RESTORE_CFLAGS,
) = range(11)


class _Ctx:
    def __init__(self, pattern: str, re: str, cflags: int, icase_global: bool) -> None:
        self.pattern = pattern  # for error messages
        self.re = re
        self.i = 0
        self.end = len(re)
        self.cflags = cflags
        self.icase_global = icase_global
        self.submatch_id = 0
        self.max_backref = -1

    def at(self, k: int = 0) -> str:
        """The character at the cursor + *k* (``""`` past the end, like C's NUL)."""
        j = self.i + k
        return self.re[j] if 0 <= j < self.end else ""

    def fail(self, code: str) -> TREError:
        return TREError(self.pattern, code)


def _parse_int(ctx: _Ctx, r: int) -> tuple[int, int]:
    num = -1
    while r < ctx.end and "0" <= ctx.re[r] <= "9":
        num = (0 if num < 0 else num) * 10 + ord(ctx.re[r]) - 48
        r += 1
    return num, r


def _parse_bound(ctx: _Ctx, result: Node) -> Node:
    """Port of ``tre_parse_bound()``: ``{m,n}`` after the ``{`` at the cursor."""
    r = ctx.i
    minimal = bool(ctx.cflags & UNGREEDY)
    mn = -1
    if r < ctx.end and "0" <= ctx.re[r] <= "9":
        mn, r = _parse_int(ctx, r)
    mx = mn
    if r < ctx.end and ctx.re[r] == ",":
        mx, r = _parse_int(ctx, r + 1)
    if (mx >= 0 and mn > mx) or mx > RE_DUP_MAX:
        raise ctx.fail("BADBR")
    approx = False
    counts_set = costs_set = False
    while True:
        start = r
        done = False
        if not counts_set:
            while r + 1 < ctx.end and not done:
                c = ctx.re[r]
                if c in "+-#~":
                    _, r = _parse_int(ctx, r + 1)
                    counts_set = True
                    approx = approx or c == "~"
                elif c in ", ":
                    r += 1
                else:
                    done = True
        done = False
        if not costs_set:
            while r + 1 < ctx.end and not done:
                c = ctx.re[r]
                if c in "+ ":
                    r += 1
                elif c == "<":
                    r += 1
                    while r < ctx.end and ctx.re[r] == " ":
                        r += 1
                    _, r = _parse_int(ctx, r)
                    approx = True
                elif c == ",":
                    r += 1
                    done = True
                elif "0" <= c <= "9":
                    _, r = _parse_int(ctx, r)
                    if r < ctx.end and ctx.re[r] in "ids":
                        r += 1
                        costs_set = True
                    else:
                        raise ctx.fail("BADBR")
                else:
                    done = True
        if start == r:
            break
    if r >= ctx.end:
        raise ctx.fail("EBRACE")
    if r == ctx.i:
        raise ctx.fail("BADBR")
    if ctx.re[r] != "}":
        raise ctx.fail("BADBR")
    r += 1
    if r < ctx.end:
        if ctx.re[r] == "?":
            minimal = not (ctx.cflags & UNGREEDY)
            r += 1
        elif ctx.re[r] in "*+":
            raise ctx.fail("BADRPT")
    if approx or counts_set or costs_set:
        raise RegexError(
            f"invalid regular expression '{ctx.pattern}': TRE approximate matching "
            "bounds are not supported"
        )
    ctx.i = r
    if mn < 0 and mx < 0:
        mn = mx = 1
    if mn < 0 and mx == 0:
        # "{,0}": an unexpanded iteration with max 0 (tre_ast_to_tnfa())
        raise ctx.fail("BADBR")
    return Node("iter", left=result, min=mn, max=mx, minimal=minimal)


def _bracket_items(ctx: _Ctx, negate: bool) -> tuple[list[tuple[int, int]], list[str]]:
    """Port of ``tre_parse_bracket_items()``: the items (ranges, with the
    opposite-case counterparts under ``REG_ICASE``) and, for a positive
    bracket, the character classes (``neg_classes`` for a negated one)."""
    re, end = ctx.re, ctx.end
    start = ctx.i
    r = ctx.i
    items: list[tuple[int, int]] = []
    classes: list[str] = []
    while True:
        if r >= end:
            raise ctx.fail("EBRACK")
        if re[r] == "]" and r > start:
            r += 1
            break
        cls = None
        if r + 2 < end and re[r + 1] == "-" and re[r + 2] != "]":
            lo, hi = ord(re[r]), ord(re[r + 2])
            r += 3
            if lo > hi:
                raise ctx.fail("ERANGE")
        elif r + 1 < end and re[r] == "[" and re[r + 1] in ".=":
            raise ctx.fail("ECOLLATE")
        elif r + 1 < end and re[r] == "[" and re[r + 1] == ":":
            endptr = r + 2
            while endptr < end and re[endptr] != ":":
                endptr += 1
            if endptr == end:
                raise ctx.fail("ECTYPE")
            cls = re[r + 2 : endptr][:63]
            if cs.glibc_class(cls) is None:
                raise ctx.fail("ECTYPE")
            r = endptr + 2
            lo, hi = 0, cs.MAX_CP
        else:
            if re[r] == "-" and (r + 1 >= end or re[r + 1] != "]") and r != start:
                raise ctx.fail("ERANGE")
            lo = hi = ord(re[r])
            r += 1
        if cls is not None:
            classes.append(cls)
            continue
        items.append((lo, hi))
        if ctx.cflags & ICASE:
            items.extend(cs.tre_range_icase(lo, hi))
    ctx.i = r
    return items, classes


def _class_set(ctx: _Ctx, name: str) -> cs.Ranges:
    # classes are matched with the regcomp() flags, not the inline ones
    s = cs.glibc_class_icase(name) if ctx.icase_global else cs.glibc_class(name)
    assert s is not None
    return s


def _parse_bracket(ctx: _Ctx) -> Node:
    """Port of ``tre_parse_bracket()`` (the cursor is after the ``[``)."""
    negate = ctx.at() == "^"
    if negate:
        ctx.i += 1
    items, classes = _bracket_items(ctx, negate)
    if not negate:
        out = cs.norm(items)
        for cls in classes:
            out = cs.union(out, _class_set(ctx, cls))
        node = _set(out)
        node.n_items = len(items) + len(classes)
        return node
    # TRE complements the sorted items; an item that overlaps an earlier one
    # extends the gap's end but not its start (so "[^a-cb-e]" matches "d").
    items.sort(key=lambda it: it[0])  # stable, like glibc's qsort (merge sort)
    kept: list[tuple[int, int]] = []
    curr_min = curr_max = 0
    for mn, mx in items:
        if mn < curr_max:
            curr_max = max(mx + 1, curr_max)
        else:
            curr_max = mn - 1
            if curr_max >= curr_min:
                kept.append((curr_min, curr_max))
            curr_min = curr_max = mx + 1
    kept.append((curr_min, cs.MAX_CP))
    out = cs.norm(r for r in kept if r[0] <= r[1])
    node = _set(out)
    node.n_items = sum(1 for r in kept if r[0] <= r[1])
    if not classes:
        return node
    node.copy_chars = out
    for cls in classes:
        node.chars = cs.difference(node.chars, _class_set(ctx, cls))
    return node


def _literal(ctx: _Ctx, c: int) -> Node:
    if ctx.cflags & ICASE and (cs.iswupper(c) or cs.iswlower(c)):
        node = _set(cs.tre_literal_icase(c))
        node.n_items = 2  # union(towupper(c), towlower(c))
        return node
    return _char(c)


def _parse(ctx: _Ctx, nofirstsub: bool = False) -> Node:  # noqa: C901 - a port
    """Port of ``tre_parse()``."""
    stack: list[object] = []
    result: Node | None = None
    depth = 0
    temporary_cflags = 0

    def pop_int() -> int:
        v = stack.pop()
        assert isinstance(v, int)
        return v

    if not nofirstsub:
        stack.append(ctx.submatch_id)
        stack.append(PARSE_MARK_FOR_SUBMATCH)
        ctx.submatch_id += 1
    stack.append(PARSE_RE)
    re, end = ctx.re, ctx.end

    while stack:
        symbol = pop_int()
        if symbol == PARSE_RE:
            if not (ctx.cflags & LITERAL):
                stack.append(PARSE_UNION)
            stack.append(PARSE_BRANCH)
        elif symbol == PARSE_BRANCH:
            stack.append(PARSE_CATENATION)
            stack.append(PARSE_PIECE)
        elif symbol == PARSE_PIECE:
            if not (ctx.cflags & LITERAL):
                stack.append(PARSE_POSTFIX)
            stack.append(PARSE_ATOM)
        elif symbol == PARSE_CATENATION:
            if ctx.i >= end:
                continue
            c = re[ctx.i]
            if not (ctx.cflags & LITERAL):
                if c == "|":
                    continue
                if c == ")" and depth > 0:
                    depth -= 1
                    continue
            stack.append(PARSE_CATENATION)
            stack.append(result)
            stack.append(PARSE_POST_CATENATION)
            stack.append(PARSE_PIECE)
        elif symbol == PARSE_POST_CATENATION:
            tree = stack.pop()
            assert isinstance(tree, Node) and result is not None
            result = Node("cat", left=tree, right=result)
        elif symbol == PARSE_UNION:
            if ctx.i >= end or ctx.cflags & LITERAL:
                continue
            c = re[ctx.i]
            if c == "|":
                stack.append(PARSE_UNION)
                stack.append(result)
                stack.append(PARSE_POST_UNION)
                stack.append(PARSE_BRANCH)
                ctx.i += 1
            elif c == ")":
                ctx.i += 1
        elif symbol == PARSE_POST_UNION:
            tree = stack.pop()
            assert isinstance(tree, Node) and result is not None
            result = Node("union", left=tree, right=result)
        elif symbol == PARSE_POSTFIX:
            if ctx.i >= end or ctx.cflags & LITERAL:
                continue
            c = re[ctx.i]
            if c in "+?*":
                minimal = bool(ctx.cflags & UNGREEDY)
                rep_min = 1 if c == "+" else 0
                rep_max = 1 if c == "?" else -1
                if ctx.i + 1 < end:
                    if re[ctx.i + 1] == "?":
                        minimal = not (ctx.cflags & UNGREEDY)
                        ctx.i += 1
                    elif re[ctx.i + 1] in "*+":
                        raise ctx.fail("BADRPT")
                ctx.i += 1
                result = Node("iter", left=result, min=rep_min, max=rep_max, minimal=minimal)
                stack.append(PARSE_POSTFIX)
            elif c == "{":
                ctx.i += 1
                assert result is not None
                result = _parse_bound(ctx, result)
                stack.append(PARSE_POSTFIX)
        elif symbol == PARSE_ATOM:
            atom, depth, temporary_cflags = _parse_atom(ctx, stack, depth, temporary_cflags)
            if atom is not _PLACEHOLDER:
                result = atom
        elif symbol == PARSE_MARK_FOR_SUBMATCH:
            submatch_id = pop_int()
            if result is not None:
                if result.submatch_id >= 0:
                    result = Node("cat", left=_empty(), right=result)
                result.submatch_id = submatch_id
        elif symbol == PARSE_RESTORE_CFLAGS:
            ctx.cflags = pop_int()
        else:  # pragma: no cover
            raise AssertionError(symbol)
    if depth > 0:
        raise ctx.fail("EPAREN")
    assert result is not None
    return result


def _parse_atom(  # noqa: C901 - a port
    ctx: _Ctx, stack: list[object], depth: int, temporary_cflags: int
) -> tuple[Node, int, int]:
    re, end = ctx.re, ctx.end
    c = ctx.at()
    if ctx.i < end and not (ctx.cflags & LITERAL):
        if c == "(":
            if ctx.at(1) == "?":
                new_cflags = ctx.cflags
                bit = True
                ctx.i += 2
                while True:
                    f = ctx.at()
                    if f in ("i", "n", "r", "U"):
                        flag = {"i": ICASE, "n": NEWLINE, "r": RIGHT_ASSOC, "U": UNGREEDY}[f]
                        new_cflags = new_cflags | flag if bit else new_cflags & ~flag
                        ctx.i += 1
                    elif f == "-":
                        ctx.i += 1
                        bit = False
                    elif f == ":":
                        ctx.i += 1
                        depth += 1
                        break
                    elif f == "#":
                        while ctx.i < end and re[ctx.i] != ")":
                            ctx.i += 1
                        if ctx.i < end:
                            ctx.i += 1
                            break
                        raise ctx.fail("BADPAT")
                    elif f == ")":
                        ctx.i += 1
                        break
                    else:
                        raise ctx.fail("BADPAT")
                stack.append(ctx.cflags)
                stack.append(PARSE_RESTORE_CFLAGS)
                stack.append(PARSE_RE)
                ctx.cflags = new_cflags
                return _PLACEHOLDER, depth, temporary_cflags
            depth += 1
            ctx.i += 1
            stack.append(ctx.submatch_id)
            stack.append(PARSE_MARK_FOR_SUBMATCH)
            stack.append(PARSE_RE)
            ctx.submatch_id += 1
            return _PLACEHOLDER, depth, temporary_cflags
        if c == ")" and depth > 0:
            return _empty(), depth, temporary_cflags
        if c == "[":
            ctx.i += 1
            return _parse_bracket(ctx), depth, temporary_cflags
        if c == "\\":
            e = ctx.at(1)
            if e in _MACROS:
                sub = _Ctx(ctx.pattern, _MACROS[e], ctx.cflags, ctx.icase_global)
                node = _parse(sub, nofirstsub=True)
                ctx.i += 2
                return node, depth, temporary_cflags
            if ctx.i + 1 >= end:
                raise ctx.fail("EESCAPE")
            if e == "Q":
                ctx.cflags |= LITERAL
                temporary_cflags |= LITERAL
                ctx.i += 2
                stack.append(PARSE_ATOM)
                return _PLACEHOLDER, depth, temporary_cflags
            ctx.i += 1
            if e in "bB<>":
                ctx.i += 1
                name = {"b": "WB", "B": "WB_NEG", "<": "BOW", ">": "EOW"}[e]
                return Node("assert", name=name), depth, temporary_cflags
            if e == "x":
                ctx.i += 1
                if ctx.at() != "{" and ctx.i < end:
                    digits = ""
                    for _ in range(2):
                        if ctx.i < end and ctx.at() in "0123456789abcdefABCDEF":
                            digits += ctx.at()
                            ctx.i += 1
                    return _char(int(digits or "0", 16)), depth, temporary_cflags
                if ctx.i < end:
                    ctx.i += 1
                    digits = ""
                    while True:
                        if ctx.at() == "}":
                            break
                        if ctx.at() and ctx.at() in "0123456789abcdefABCDEF":
                            digits += ctx.at()
                            ctx.i += 1
                            continue
                        raise ctx.fail("EBRACE")
                    ctx.i += 1
                    return _char(int(digits or "0", 16)), depth, temporary_cflags
                # "\x" at the very end: TRE reads the terminating NUL
                ctx.i += 1
                return _char(0), depth, temporary_cflags
            if "0" <= e <= "9":
                ctx.max_backref = max(ctx.max_backref, int(e))
                ctx.i += 1
                return Node("backref", ref=int(e)), depth, temporary_cflags
            ctx.i += 1
            return _char(ord(e)), depth, temporary_cflags
        if c == ".":
            ctx.i += 1
            if ctx.cflags & NEWLINE:
                return _set(((0, 9), (11, cs.MAX_CP))), depth, temporary_cflags
            return _set(cs.ALL), depth, temporary_cflags
        if c == "^":
            ctx.i += 1
            return Node("assert", name="BOL"), depth, temporary_cflags
        if c == "$":
            ctx.i += 1
            return Node("assert", name="EOL"), depth, temporary_cflags
    # parse_literal
    if temporary_cflags and ctx.i + 1 < end and c == "\\" and ctx.at(1) == "E":
        ctx.cflags &= ~temporary_cflags
        temporary_cflags = 0
        ctx.i += 2
        stack.append(PARSE_PIECE)
        return _PLACEHOLDER, depth, temporary_cflags
    if not (ctx.cflags & LITERAL) and (ctx.i >= end or c in "*|{+?"):
        return _empty(), depth, temporary_cflags
    if ctx.cflags & LITERAL and ctx.i >= end:
        return _empty(), depth, temporary_cflags
    ctx.i += 1
    return _literal(ctx, ord(c)), depth, temporary_cflags


# The result register is not changed by atoms that only push parser states;
# _parse_atom returns this marker for them and _parse keeps the old result.
_PLACEHOLDER = Node("placeholder")


def parse(pattern: str, icase: bool = False) -> tuple[Node, int]:
    """Parse *pattern* as R's TRE does; returns the AST and the number of groups."""
    ctx = _Ctx(pattern, pattern, ICASE if icase else 0, icase)
    tree = _parse(ctx)
    nsub = ctx.submatch_id - 1
    if ctx.max_backref > nsub:
        raise ctx.fail("ESUBREG")
    return tree, nsub


# ---------------------------------------------------------------------------
# Emitting `regex` syntax
# ---------------------------------------------------------------------------


@functools.cache
def _word() -> str:
    return cs.emit_set(cs.tre_word())


# The same assertions with the `regex` module's Unicode word characters, for
# subjects without the characters on which they and glibc disagree
# (:func:`pytacheck._r._charset.word_divergent`).
_FAST_ASSERTIONS = {
    "BOW": r"(?:\G(?=\w)|\m)",
    "EOW": r"(?!\G)\M",
    "WB": r"(?:\b|\G|\Z)",
    "WB_NEG": r"(?!\G)(?!\Z)\B",
}


@functools.cache
def _fast_sets() -> dict[cs.Ranges, str]:
    """Sets that are the `regex` module's ``\\w`` / ``\\W`` on such subjects."""
    word = cs.tre_word()
    alnum = cs.glibc_class("alnum")
    alnum_i = cs.glibc_class_icase("alnum")
    assert alnum is not None and alnum_i is not None
    out = {word: r"\w", cs.complement(word): r"\W", alnum: r"[^\W_]"}
    word_i = cs.union(alnum_i, ((0x5F, 0x5F),))
    if word_i == word:
        out[cs.complement(word_i)] = r"\W"
    if alnum_i == alnum:
        out[alnum_i] = r"[^\W_]"
    return out


@functools.cache
def _assertion(name: str) -> str:
    w = _word()
    if name == "BOL":
        return "^"
    if name == "EOL":
        return r"\Z"
    if name == "BOW":  # previous character not a word character (or search start)
        return rf"(?:\G|(?<!{w}))(?={w})"
    if name == "EOW":
        return rf"(?!\G)(?<={w})(?!{w})"
    if name == "WB":  # always true at the search start and at the end
        return rf"(?:\G|\Z|(?<={w})(?!{w})|(?<!{w})(?={w}))"
    if name == "WB_NEG":
        return rf"(?!\G)(?!\Z)(?:(?<={w})(?={w})|(?<!{w})(?!{w}))"
    raise AssertionError(name)


@dataclass
class Translation:
    """A TRE pattern in `regex` syntax.

    ``pattern`` is what TRE matches in R's byte mode (pattern and every
    element of the input ASCII); ``pattern_wide`` is the wide-character mode
    R uses otherwise, where repetitions that ``tre_expand_ast()`` spells out
    lose the ``[:class:]`` exclusions of negated brackets (``\\W{2}`` matches
    ``"ab"``). They differ only for such patterns.
    """

    pattern: str
    pattern_wide: str
    ngroups: int
    nullable: bool  # can match the empty string
    minimal: bool  # has a minimal (lazy) repetition
    backrefs: bool
    # the same with the `regex` module's `\w` / `\b` (much faster), exact for
    # subjects without glibc-divergent word characters; None if no different
    fast: str | None = None
    fast_wide: str | None = None


def _quant(node: Node) -> str:
    mn, mx = node.min, node.max
    if (mn, mx) == (0, -1):
        q = "*"
    elif (mn, mx) == (1, -1):
        q = "+"
    elif (mn, mx) == (0, 1):
        q = "?"
    elif mx == -1:
        q = f"{{{mn},}}"
    elif mn == mx:
        q = f"{{{mn}}}"
    else:
        q = f"{{{mn},{mx}}}"
    return q + ("?" if node.minimal else "")


class _Emitter:
    def __init__(self, wide: bool, fast: bool = False) -> None:
        self.wide = wide
        self.fast = fast
        self.used_fast = False
        self.minimal = False
        self.backrefs = False
        self.in_copy = 0  # inside an iteration tre_expand_ast() spells out
        self.quirk = False  # a negated bracket with classes was copied

    def emit(self, node: Node) -> tuple[str, int]:
        """`regex` syntax for *node* and its minimum match length."""
        body, width = self._emit(node)
        if node.submatch_id > 0:
            return f"({body})", width
        return body, width

    def _emit(self, node: Node) -> tuple[str, int]:  # noqa: C901
        k = node.kind
        if k == "set":
            chars = node.chars
            if self.in_copy and node.copy_chars is not None:
                self.quirk = True
                if self.wide:
                    chars = node.copy_chars
            if chars == cs.ALL:
                return ".", 1
            if chars == ((0, 9), (11, cs.MAX_CP)):
                return r"[^\n]", 1
            if self.fast and chars in _fast_sets():
                self.used_fast = True
                return _fast_sets()[chars], 1
            return cs.emit_set(chars), 1
        if k == _EMPTY_KIND:
            return "", 0
        if k == "assert":
            if self.fast and node.name in _FAST_ASSERTIONS:
                self.used_fast = True
                return _FAST_ASSERTIONS[node.name], 0
            return _assertion(node.name), 0
        if k == "backref":
            self.backrefs = True
            # "\0" refers to the match itself, which is still unset: empty
            return (f"(?:\\{node.ref})" if node.ref else ""), 0
        if k == "cat":
            assert node.left is not None and node.right is not None
            a, wa = self.emit(node.left)
            b, wb = self.emit(node.right)
            return a + b, wa + wb
        if k == "union":
            assert node.left is not None and node.right is not None
            a, wa = self.emit(node.left)
            b, wb = self.emit(node.right)
            if _nullable(node.left) and _only_empty(node.right):
                # TRE takes the empty path of the first nullable branch only
                # (tre_match_empty()), so "(^|)" is "^"
                b = "(?!)"
            return f"(?:{a}|{b})", min(wa, wb)
        if k == "iter":
            assert node.left is not None
            self.minimal = self.minimal or node.minimal
            expanded = node.min > 1 or node.max > 1
            self.in_copy += expanded
            body, width = self.emit(node.left)
            self.in_copy -= expanded
            mn, mx = _tre_bounds(node)
            if body == "":
                return "", 0
            if not _atomic(body):
                body = f"(?:{body})"
            q = Node("iter", min=mn, max=mx, minimal=node.minimal)
            return body + _quant(q), width * mn
        raise AssertionError(k)


def _nullable(node: Node) -> bool:
    """TRE's ``nullable`` (``tre_compute_nfl()``): back references are not."""
    k = node.kind
    if k in ("empty", "assert"):
        return True
    if k in ("set", "backref"):
        return False
    assert node.left is not None
    if k == "cat":
        assert node.right is not None
        return _nullable(node.left) and _nullable(node.right)
    if k == "union":
        assert node.right is not None
        return _nullable(node.left) or _nullable(node.right)
    return _tre_bounds(node)[0] == 0 or _nullable(node.left)


def _only_empty(node: Node) -> bool:
    """Can *node* only match the empty string?"""
    k = node.kind
    if k in ("empty", "assert"):
        return True
    if k in ("set", "backref"):
        return False
    assert node.left is not None
    if k in ("cat", "union"):
        assert node.right is not None
        return _only_empty(node.left) and _only_empty(node.right)
    return node.max == 0 or _only_empty(node.left)


def _tre_bounds(node: Node) -> tuple[int, int]:
    """The repetition an iteration node really has in TRE.

    ``tre_expand_ast()`` spells out ``{m,n}`` when ``m > 1`` or ``n > 1``; an
    omitted minimum (``{,n}``) starts that loop at -1, so ``{,2}`` allows three
    copies. A remaining (unexpanded) iteration is nullable only through ``min
    == 0``, and its empty path is that of its operand (``tre_match_empty()``):
    ``(^)*`` is ``^``, and ``{,1}`` means exactly once.
    """
    assert node.left is not None
    mn, mx = node.min, node.max
    expanded = mn > 1 or mx > 1
    if expanded:
        if mn < 0:
            return 0, mx + 1
        if mx == -1 and _nullable(node.left):
            return mn + 1, -1  # the trailing star needs the operand's empty path
        return mn, mx
    if mn < 0:
        return 1, 1
    if mn == 0 and _nullable(node.left):
        return 1, mx
    return mn, mx


def _atomic(body: str) -> bool:
    """Is *body* one `regex` atom (a quantifier may follow it directly)?"""
    if len(body) == 1 and body not in "^$|()[]{}*+?\\":
        return True
    if len(body) == 2 and body[0] == "\\":
        return True
    if body.startswith("[") and body.endswith("]") and body.count("]") == 1:
        return True
    if body.startswith("(") and body.endswith(")"):
        # a single group spanning the whole body
        level = 0
        for i, ch in enumerate(body):
            if ch == "\\":
                continue
            if ch == "(" and (i == 0 or body[i - 1] != "\\"):
                level += 1
            elif ch == ")" and body[i - 1] != "\\":
                level -= 1
                if level == 0 and i != len(body) - 1:
                    return False
        return True
    return False


@functools.lru_cache(maxsize=4096)
def translate(pattern: str, icase: bool = False) -> Translation:
    """Translate the TRE *pattern* (``ignore.case = icase``) to `regex` syntax."""
    tree, nsub = parse(pattern, icase)
    em = _Emitter(wide=False)
    body, width = em.emit(tree)
    wide = _Emitter(wide=True).emit(tree)[0] if em.quirk else body
    fem = _Emitter(wide=False, fast=True)
    fast = fem.emit(tree)[0]
    fast_wide = None
    if not fem.used_fast:
        fast = None
    elif em.quirk:
        fast_wide = _Emitter(wide=True, fast=True).emit(tree)[0]
    else:
        fast_wide = fast
    try:
        regex.compile(body, regex.VERSION0 | regex.DOTALL)
    except regex.error as exc:  # pragma: no cover - a bug in the emitter
        raise RegexError(f"invalid regular expression {pattern!r}: {exc}") from exc
    return Translation(body, wide, nsub, width == 0, em.minimal, em.backrefs, fast, fast_wide)
