"""R's PCRE2 regular expressions (``perl = TRUE``) in the `regex` module.

R compiles PCRE2 patterns in UTF mode but without ``PCRE2_UCP``, so ``\\w``,
``\\d``, ``\\s``, ``\\b`` and the POSIX classes only know ASCII characters, and
caseless matching (``ignore.case = TRUE``, ``(?i)``) folds with PCRE2's own
Unicode tables. :func:`translate` rewrites a pattern for the `regex` module so
that it means the same:

* the character-type escapes and POSIX classes become explicit ASCII sets
  (``[[:^alpha:]]`` is "not an ASCII letter");
* case-insensitivity is spelled out rather than left to the `regex` module's
  ``IGNORECASE``, which differs from PCRE2 (it matches ``İ`` / ``ı`` with
  ``i`` / ``I``, and folds ``\\w`` and ``[[:alnum:]]``): literals and
  character classes are closed under PCRE2's caseless sets
  (:func:`pytacheck._r._charset.pcre_caseless_closure`). In a caseless
  class ``[:upper:]`` and ``[:lower:]`` mean any ASCII letter, and ``\\p{Lu}``,
  ``\\p{Ll}`` and ``\\p{Lt}`` any cased letter (``\\p{LC}``);
* option settings such as ``(?i)`` or ``(?s)`` last to the end of the
  enclosing group, as in PCRE2;
* escapes PCRE2 rejects (``\\i``, ``\\u``, ...) raise :class:`RegexError`.

With a leading ``(*UCP)`` the Unicode meanings of the `regex` module are kept.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

import regex

from pytacheck._r import _charset as cs
from pytacheck._r._charset import RegexError

_ASCII_WORD: cs.Ranges = ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A))
_ASCII_ALPHA: cs.Ranges = ((0x41, 0x5A), (0x61, 0x7A))
_SPACE: cs.Ranges = ((0x09, 0x0D), (0x20, 0x20))
_HSPACE: cs.Ranges = cs.norm(
    [
        (0x09, 0x09),
        (0x20, 0x20),
        (0xA0, 0xA0),
        (0x1680, 0x1680),
        (0x180E, 0x180E),
        (0x2000, 0x200A),
        (0x202F, 0x202F),
        (0x205F, 0x205F),
        (0x3000, 0x3000),
    ]
)
_VSPACE: cs.Ranges = ((0x0A, 0x0D), (0x85, 0x85), (0x2028, 0x2029))
_ESCAPE_SETS: dict[str, tuple[cs.Ranges, bool]] = {
    "d": (((0x30, 0x39),), False),
    "D": (((0x30, 0x39),), True),
    "w": (_ASCII_WORD, False),
    "W": (_ASCII_WORD, True),
    "s": (_SPACE, False),
    "S": (_SPACE, True),
    "h": (_HSPACE, False),
    "H": (_HSPACE, True),
    "v": (_VSPACE, False),
    "V": (_VSPACE, True),
}
_POSIX: dict[str, cs.Ranges] = {
    "alpha": _ASCII_ALPHA,
    "digit": ((0x30, 0x39),),
    "alnum": ((0x30, 0x39), (0x41, 0x5A), (0x61, 0x7A)),
    "upper": ((0x41, 0x5A),),
    "lower": ((0x61, 0x7A),),
    "space": _SPACE,
    "blank": ((0x09, 0x09), (0x20, 0x20)),
    "punct": ((0x21, 0x2F), (0x3A, 0x40), (0x5B, 0x60), (0x7B, 0x7E)),
    "xdigit": ((0x30, 0x39), (0x41, 0x46), (0x61, 0x66)),
    "cntrl": ((0x00, 0x1F), (0x7F, 0x7F)),
    "print": ((0x20, 0x7E),),
    "graph": ((0x21, 0x7E),),
    "word": _ASCII_WORD,
    "ascii": ((0x00, 0x7F),),
}
_SIMPLE_ESCAPES = {"a": 0x07, "e": 0x1B, "f": 0x0C, "n": 0x0A, "r": 0x0D, "t": 0x09}
# alphanumeric escapes PCRE2 knows (others are errors)
_KNOWN = set("aAbBcCdDeEfgGhHkKnNopPQrRsStvVwWxXzZ") | set("0123456789")
_UNSUPPORTED = set("FLlUu")
_CASED_PROPS = {"Lu", "Ll", "Lt", "L&", "LC"}
_QUANT_START = set("*+?{")
_BOUND = regex.compile(r"\{(?:\d+(?:,\d*)?|,\d+)\}")


@dataclass
class _Frame:
    icase: bool
    flags: str  # other inline flags (s, m, x) opened as scoped groups here
    ungreedy: bool
    nocapture: bool
    extended: bool
    scopes: list[str] = field(default_factory=list)  # open "(?s:" groups


@dataclass
class Translation:
    pattern: str
    ngroups: int


class _Lit:
    """A literal character, merged with its neighbours when emitted."""

    __slots__ = ("c", "icase")

    def __init__(self, c: int, icase: bool) -> None:
        self.c = c
        self.icase = icase


def _esc(c: int) -> str:
    ch = chr(c)
    if c < 0x80:
        return regex.escape(ch) if not ch.isalnum() else ch
    return cs._esc(c)


def _class_items(s: cs.Ranges) -> str:
    return cs._items(s)


class _Translator:
    def __init__(self, pattern: str, icase: bool) -> None:
        self.p = pattern
        self.n = len(pattern)
        self.i = 0
        self.out: list[object] = []
        self.ngroups = 0
        self.ucp = False
        self.frames = [_Frame(icase, "", False, False, False)]

    # -- helpers -----------------------------------------------------------

    @property
    def frame(self) -> _Frame:
        return self.frames[-1]

    def fail(self, msg: str) -> RegexError:
        return RegexError(f"invalid regular expression '{self.p}', reason '{msg}'")

    def at(self, k: int = 0) -> str:
        j = self.i + k
        return self.p[j] if j < self.n else ""

    def lit(self, c: int) -> None:
        self.out.append(_Lit(c, self.frame.icase))

    def emit(self, s: str) -> None:
        self.out.append(s)

    # -- escapes -----------------------------------------------------------

    def _hex(self, digits: str) -> int:
        try:
            return int(digits, 16)
        except ValueError as exc:
            raise self.fail("digits missing after \\x or in \\x{} or \\o{} or \\N{U+}") from exc

    def char_escape(self, in_class: bool) -> int | None:
        """A backslash escape at the cursor that denotes one character (the
        cursor moves past it), or ``None`` (cursor unchanged)."""
        e = self.at(1)
        if e in _SIMPLE_ESCAPES:
            self.i += 2
            return _SIMPLE_ESCAPES[e]
        if e == "b" and in_class:
            self.i += 2
            return 0x08
        if e == "x":
            if self.at(2) == "{":
                end = self.p.find("}", self.i + 3)
                if end == -1:
                    raise self.fail("missing terminating } in \\x{}")
                c = self._hex(self.p[self.i + 3 : end])
                self.i = end + 1
                return c
            j = self.i + 2
            digits = ""
            while len(digits) < 2 and j < self.n and self.p[j] in "0123456789abcdefABCDEF":
                digits += self.p[j]
                j += 1
            if not digits:
                raise self.fail("digits missing after \\x or in \\x{} or \\o{} or \\N{U+}")
            self.i = j
            return int(digits, 16)
        if e == "o" and self.at(2) == "{":
            end = self.p.find("}", self.i + 3)
            if end == -1:
                raise self.fail("missing terminating } in \\o{}")
            try:
                c = int(self.p[self.i + 3 : end], 8)
            except ValueError as exc:
                raise self.fail("non-octal character in \\o{}") from exc
            self.i = end + 1
            return c
        if e == "c":
            x = self.at(2)
            if not x or ord(x) > 127:
                raise self.fail("\\c must be followed by a printable ASCII character")
            self.i += 3
            return ord(x.upper()) ^ 0x40
        if e == "N" and self.at(2) == "{":
            end = self.p.find("}", self.i + 3)
            name = self.p[self.i + 3 : end] if end != -1 else ""
            if not name.startswith("U+"):
                raise self.fail("PCRE2 does not support \\F, \\L, \\l, \\N{name}, \\U, or \\u")
            c = self._hex(name[2:])
            self.i = end + 1
            return c
        if e == "0" or (in_class and e.isdigit()):
            # octal: \0 plus up to two more octal digits (in a class \1.. too)
            if e in "89":
                self.i += 2
                return ord(e)
            j = self.i + 1
            digits = ""
            while len(digits) < 3 and j < self.n and self.p[j] in "01234567":
                digits += self.p[j]
                j += 1
            self.i = j
            return int(digits, 8)
        if e and not e.isalnum() and ord(e) < 0x80:
            self.i += 2
            return ord(e)
        if e and ord(e) >= 0x80:
            self.i += 2
            return ord(e)
        return None

    def check_escape(self, e: str, in_class: bool) -> None:
        if e == "":
            raise self.fail("\\ at end of pattern")
        if e in _UNSUPPORTED:
            raise self.fail("PCRE2 does not support \\F, \\L, \\l, \\N{name}, \\U, or \\u")
        if e.isalnum() and e.isascii() and e not in _KNOWN:
            raise self.fail("unrecognized character follows \\")
        if in_class and e in "NgkKRXBAzZG":
            raise self.fail(f"escape sequence is invalid in character class (\\{e})")

    def prop(self) -> tuple[str, bool]:
        """``\\p{..}`` / ``\\pL`` at the cursor: the property name and negation."""
        neg = self.at(1) == "P"
        if self.at(2) == "{":
            end = self.p.find("}", self.i + 3)
            if end == -1:
                raise self.fail("malformed \\P or \\p sequence")
            name = self.p[self.i + 3 : end]
            self.i = end + 1
        else:
            name = self.at(2)
            if not name:
                raise self.fail("malformed \\P or \\p sequence")
            self.i += 3
        if name.startswith("^"):
            neg, name = not neg, name[1:]
        return name, neg

    def prop_expr(self, name: str, neg: bool) -> str:
        if (self.frame.icase and name in _CASED_PROPS) or name == "L&":
            name = "LC"
        return f"\\{'P' if neg else 'p'}{{{name}}}"

    # -- classes -----------------------------------------------------------

    def parse_class(self) -> str:
        """``[...]`` at the cursor, as one `regex` set."""
        self.i += 1
        negate = self.at() == "^"
        if negate:
            self.i += 1
        icase = self.frame.icase
        fold: list[tuple[int, int]] = []  # literal items: case-folded
        plain: list[tuple[int, int]] = []  # class escapes / POSIX: not folded
        props: list[str] = []
        first = True
        while True:
            if self.i >= self.n:
                raise self.fail("missing terminating ] for character class")
            c = self.at()
            if c == "]" and not first:
                self.i += 1
                break
            first = False
            lo = self.class_atom(plain, props, icase)
            if lo is None:
                if self.at() == "-" and self.at(1) not in ("]", ""):
                    raise self.fail("invalid range in character class")
                continue
            # a range?
            if self.at() == "-" and self.at(1) not in ("]", ""):
                self.i += 1
                hi = self.class_atom(plain, props, icase, as_range_end=True)
                if hi is None:
                    raise self.fail("invalid range in character class")
                if hi < lo:
                    raise self.fail("range out of order in character class")
                fold.append((lo, hi))
            else:
                fold.append((lo, lo))
        folded = cs.norm(fold)
        if icase:
            folded = cs.pcre_caseless_closure(folded)
        ranges = cs.union(folded, plain)
        body = _class_items(ranges) + "".join(props)
        if not body:
            return "[^\\x00-\\U0010ffff]" if not negate else "[\\x00-\\U0010ffff]"
        return f"[{'^' if negate else ''}{body}]"

    def class_atom(
        self,
        plain: list[tuple[int, int]],
        props: list[str],
        icase: bool,
        as_range_end: bool = False,
    ) -> int | None:
        """One class member at the cursor: a character (returned) or a class
        escape / POSIX class (added to *plain* / *props*, ``None`` returned)."""
        c = self.at()
        if c == "[" and self.at(1) in ":.=":
            m = regex.match(r"\[([:.=])(\^?)([A-Za-z<>]+)\1\]", self.p[self.i :])
            if m:
                if as_range_end:
                    raise self.fail("invalid range in character class")
                if m.group(1) != ":":
                    raise self.fail("POSIX collating elements are not supported")
                name, neg = m.group(3), bool(m.group(2))
                self.i += m.end()
                if self.ucp and name not in ("word",):
                    props.append(f"[:{'^' if neg else ''}{name}:]")
                    return None
                if name not in _POSIX:
                    raise self.fail("unknown POSIX class name")
                s = _POSIX[name]
                if icase and name in ("upper", "lower"):
                    s = _ASCII_ALPHA
                if neg:
                    s = cs.complement(s)
                plain.extend(s)
                return None
        if c == "\\":
            e = self.at(1)
            self.check_escape(e, True)
            if e == "Q":
                end = self.p.find("\\E", self.i + 2)
                text = self.p[self.i + 2 :] if end == -1 else self.p[self.i + 2 : end]
                self.i = self.n if end == -1 else end + 2
                if not text:
                    return None
                # all but the last character are plain members
                for member in text[:-1]:
                    one = ((ord(member), ord(member)),)
                    plain.extend(cs.pcre_caseless_closure(one) if icase else one)
                return ord(text[-1])
            if e == "E":
                self.i += 2
                return None
            if e in _ESCAPE_SETS:
                if as_range_end:
                    raise self.fail("invalid range in character class")
                self.i += 2
                if self.ucp and e in "dDwWsS":
                    props.append("\\" + e)
                    return None
                s, neg = _ESCAPE_SETS[e]
                plain.extend(cs.complement(s) if neg else s)
                return None
            if e in "pP":
                if as_range_end:
                    raise self.fail("invalid range in character class")
                name, neg = self.prop()
                props.append(self.prop_expr(name, neg))
                return None
            ch = self.char_escape(in_class=True)
            if ch is None:
                raise self.fail("unrecognized character follows \\")
            return ch
        self.i += 1
        return ord(c)

    # -- groups ------------------------------------------------------------

    def open_scopes(self) -> None:
        for f in self.frame.scopes:
            self.emit(f"(?{f}:")

    def close_scopes(self) -> None:
        self.emit(")" * len(self.frame.scopes))

    def parse_group(self) -> None:
        p, i = self.p, self.i
        fr = self.frame
        if p.startswith("(*", i):
            end = p.find(")", i)
            if end == -1:
                raise self.fail("(*VERB) not terminated")
            self.emit(p[i : end + 1])
            self.i = end + 1
            return
        if not p.startswith("(?", i):
            self.i += 1
            if fr.nocapture:
                self.push("(?:")
            else:
                self.ngroups += 1
                self.push("(")
            return
        rest = p[i + 2 :]
        if rest.startswith("#"):
            end = p.find(")", i)
            if end == -1:
                raise self.fail("missing ) at end of (?# comment")
            self.i = end + 1
            return
        m = regex.match(r"(\^?)([a-zA-Z]*)(?:-([a-zA-Z]*))?([:)])", rest)
        if m and (m.group(1) or m.group(2) or m.group(3) is not None):
            reset, on, off, kind = m.group(1), m.group(2), m.group(3) or "", m.group(4)
            self.i += 2 + m.end()
            icase, ungreedy, nocap, ext = fr.icase, fr.ungreedy, fr.nocapture, fr.extended
            if reset:
                icase = ungreedy = nocap = ext = False
            other_on: list[str] = []
            other_off: list[str] = []
            for flags, value in ((on, True), (off, False)):
                for f in flags:
                    if f == "i":
                        icase = value
                    elif f == "U":
                        ungreedy = value
                    elif f == "n":
                        nocap = value
                    elif f == "x":
                        ext = value  # whitespace and comments are dropped here
                    elif f in "sm":
                        (other_on if value else other_off).append(f)
                    elif f == "J":
                        pass
                    else:
                        raise self.fail("unrecognized character after (? or (?-")
            flag_str = "".join(other_on) + ("-" + "".join(other_off) if other_off else "")
            if kind == ":":
                self.push(f"(?{flag_str}:" if flag_str else "(?:")
                self.frame.icase, self.frame.ungreedy = icase, ungreedy
                self.frame.nocapture, self.frame.extended = nocap, ext
            else:
                # applies to the rest of the enclosing group
                fr.icase, fr.ungreedy, fr.nocapture, fr.extended = icase, ungreedy, nocap, ext
                if flag_str:
                    fr.scopes.append(flag_str)
                    self.emit(f"(?{flag_str}:")
            return
        # other group types: copy the opener
        for opener in ("(?:", "(?=", "(?!", "(?<=", "(?<!", "(?>", "(?|"):
            if p.startswith(opener, i):
                self.i += len(opener)
                self.push(opener)
                return
        m = regex.match(r"\(\?(?:P?<([A-Za-z_]\w*)>|'([A-Za-z_]\w*)')", p[i:])
        if m:
            self.ngroups += 1
            self.i += m.end()
            self.push(f"(?P<{m.group(1) or m.group(2)}>")
            return
        m = regex.match(r"\(\?P=([A-Za-z_]\w*)\)", p[i:])
        if m:
            self.i += m.end()
            self.emit(f"(?{'i' if fr.icase else ''}:(?P={m.group(1)}))")
            return
        # recursion, conditionals and the like: pass through
        m = regex.match(r"\(\?(?:R|[+-]?\d+|&\w+|P>\w+)\)", p[i:])
        if m:
            self.i += m.end()
            self.emit(m.group(0))
            return
        m = regex.match(r"\(\?\((\d+|<\w+>|'\w+'|R\d*|R&\w+|DEFINE|\w+)\)", p[i:])
        if m:
            self.i += m.end()
            cond = m.group(1).strip("<>'")
            self.push(f"(?({cond})")
            return
        raise self.fail("unrecognized character after (? or (?-")

    def push(self, opener: str) -> None:
        fr = self.frame
        self.emit(opener)
        self.frames.append(_Frame(fr.icase, "", fr.ungreedy, fr.nocapture, fr.extended))

    def close_group(self) -> None:
        if len(self.frames) == 1:
            raise self.fail("unmatched closing parenthesis")
        self.close_scopes()
        self.frames.pop()
        self.emit(")")

    # -- main loop ---------------------------------------------------------

    def translate(self) -> Translation:
        p = self.p
        # leading verbs such as (*UCP) or (*UTF)
        while p.startswith("(*", self.i):
            end = p.find(")", self.i)
            if end == -1:
                break
            verb = p[self.i + 2 : end]
            if not regex.fullmatch(r"[A-Z_0-9]+(=\d+)?", verb):
                break
            self.ucp = self.ucp or verb == "UCP"
            self.i = end + 1
        while self.i < self.n:
            c = p[self.i]
            fr = self.frame
            if fr.extended and (c in " \t\n\r\x0b\x0c" or c == "#"):
                if c == "#":
                    end = p.find("\n", self.i)
                    self.i = self.n if end == -1 else end + 1
                else:
                    self.i += 1
                continue
            if c == "\\":
                self.escape()
            elif c == "[":
                if p.startswith(("[[:<:]]", "[[:>:]]"), self.i):
                    ahead = "(?=[A-Za-z0-9_])" if p[self.i + 3] == "<" else "(?<=[A-Za-z0-9_])"
                    self.emit(r"(?a:\b)" + ahead)
                    self.i += 7
                elif regex.match(r"\[([:.=])\^?[A-Za-z]+\1\]", p[self.i :]):
                    raise self.fail("POSIX named classes are supported only within a class")
                else:
                    self.emit(self.parse_class())
            elif c == "(":
                self.parse_group()
            elif c == ")":
                self.i += 1
                self.close_group()
            elif c == "|":
                self.i += 1
                self.close_scopes()
                self.emit("|")
                self.open_scopes()
            elif c in "*+?":
                self.quantifier(c)
            elif c == "{":
                m = _BOUND.match(p, self.i)
                if m:
                    self.quantifier(m.group(0))
                else:
                    self.i += 1
                    self.lit(ord("{"))
            elif c in ".^$":
                self.i += 1
                self.emit(c)
            else:
                self.i += 1
                self.lit(ord(c))
        if len(self.frames) > 1:
            raise self.fail("missing closing parenthesis")
        self.close_scopes()
        return Translation(_render(self.out), self.ngroups)

    def quantifier(self, q: str) -> None:
        self.i += len(q)
        suffix = ""
        if self.at() in ("?", "+"):
            suffix = self.at()
            self.i += 1
        if self.frame.ungreedy and suffix != "+":
            suffix = "" if suffix == "?" else "?"
        if q.startswith("{,"):
            q = "{0" + q[1:]
        self.emit(_Quant(q + suffix))

    def escape(self) -> None:
        p = self.p
        e = self.at(1)
        self.check_escape(e, False)
        if e == "Q":
            end = p.find("\\E", self.i + 2)
            text = p[self.i + 2 :] if end == -1 else p[self.i + 2 : end]
            self.i = self.n if end == -1 else end + 2
            for t in text:
                self.lit(ord(t))
            return
        if e == "E":
            self.i += 2
            return
        if e in _ESCAPE_SETS:
            self.i += 2
            if self.ucp and e in "dDwWsS":
                self.emit("\\" + e)
                return
            s, neg = _ESCAPE_SETS[e]
            self.emit(f"[{'^' if neg else ''}{_class_items(s)}]")
            return
        if e in "pP":
            name, neg = self.prop()
            self.emit(self.prop_expr(name, neg))
            return
        simple = {
            "b": r"\b" if self.ucp else r"(?a:\b)",
            "B": r"\B" if self.ucp else r"(?a:\B)",
            "A": r"\A",
            "z": r"\Z",
            "Z": r"(?=\n?\Z)",
            "G": r"\G",
            "K": r"\K",
            "X": r"\X",
            "C": r"(?s:.)",
            "R": r"(?:\r\n|[\n\x0b\x0c\r\x85\u2028\u2029])",
        }
        if e in simple:
            self.i += 2
            self.emit(simple[e])
            return
        if e == "N" and self.at(2) != "{":
            self.i += 2
            self.emit(r"[^\n]")
            return
        if e in "123456789":
            m = regex.match(r"\d+", p[self.i + 1 :])
            assert m is not None
            num = int(m.group(0))
            if num < 10 or num <= self.ngroups:
                if num > self.ngroups and num >= 10:
                    raise self.fail("reference to non-existent subpattern")
                self.i += 1 + m.end()
                self.backref(str(num))
                return
            # octal escape
            ch = self.char_escape(in_class=True)
            assert ch is not None
            self.lit(ch)
            return
        if e == "g":
            m = regex.match(
                r"g(?:\{(-?\d+|[A-Za-z_]\w*)\}|(-?\d+)|<([^>]+)>|'([^']+)')", p[self.i + 1 :]
            )
            if not m:
                raise self.fail("a numbered reference must not be zero")
            self.i += 1 + m.end()
            ref = m.group(1) or m.group(2)
            if ref is None:  # subroutine call \g<name>
                self.emit(f"(?&{m.group(3) or m.group(4)})")
                return
            if ref.lstrip("-").isdigit():
                num = int(ref)
                if num < 0:
                    num = self.ngroups + num + 1
                self.backref(str(num))
            else:
                self.backref_named(ref)
            return
        if e == "k":
            m = regex.match(r"k(?:<([^>]+)>|'([^']+)'|\{([^}]+)\})", p[self.i + 1 :])
            if not m:
                raise self.fail("\\k is not followed by a braced, angle-bracketed, or quoted name")
            self.i += 1 + m.end()
            self.backref_named(m.group(1) or m.group(2) or m.group(3))
            return
        ch = self.char_escape(in_class=False)
        if ch is None:  # pragma: no cover - check_escape covers the rest
            raise self.fail("unrecognized character follows \\")
        self.lit(ch)

    def backref(self, num: str) -> None:
        if num == "0":
            raise self.fail("a numbered reference must not be zero")
        self.emit(f"(?i:\\g<{num}>)" if self.frame.icase else f"(?:\\g<{num}>)")

    def backref_named(self, name: str) -> None:
        self.emit(f"(?i:(?P={name}))" if self.frame.icase else f"(?P={name})")


class _Quant(str):
    """A quantifier token."""


def _render(tokens: list[object]) -> str:
    return "".join(_render_run([t]) if isinstance(t, _Lit) else str(t) for t in tokens)


def _render_run(run: list[_Lit]) -> str:
    """Literal characters; a caseless one as the set of its PCRE2 equivalents.

    (Not the `regex` module's ``(?i:...)``: besides ``İ`` / ``ı`` it folds a
    negated set that follows an optional caseless group at the start of a
    pattern, e.g. ``(?i:k)?[^7b]`` does not match ``"B"``.)
    """
    out = []
    for t in run:
        group = cs.pcre_caseless(t.c) if t.icase else (t.c,)
        out.append(_esc(t.c) if len(group) == 1 else "[" + "".join(map(_esc, group)) + "]")
    return "".join(out)


@functools.lru_cache(maxsize=4096)
def translate(pattern: str, icase: bool = False) -> Translation:
    """Translate the PCRE *pattern* (``ignore.case = icase``) to `regex` syntax."""
    return _Translator(pattern, icase).translate()
