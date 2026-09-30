"""R's parser in Python: syntax trees and exact parse-error messages.

``code_parse_r()`` reports the message ``parse(text = x)`` raises in R. Where an
``Rscript`` is available (``PYTACHECK_RSCRIPT`` or ``Rscript`` on the
``PATH``), R itself parses the code (:func:`parse_errors`). Otherwise this
module's port of R 4.5.3's parser is used, which reproduces R's messages:

* the lexer is a line-by-line port of ``token()``/``yylex()`` in
  ``src/main/gram.y`` (context stack for newlines inside ``()``/``[]``/``{}``
  and ``if``/``else``, ``EatLines``, numbers, strings and their escapes, raw
  strings, ``%op%``, backticks, the pipe placeholder, UTF-8 handling with
  glibc's character classes, positions counted in characters with tab stops
  of 8, and the 256-byte parse-context ring buffer the messages quote);
* the grammar runs on bison's own LALR tables (``_rparse_tables``), so errors
  are detected at exactly the same token;
* the semantic checks that raise errors (repeated formal arguments, the pipe
  and placeholder rules, ``=>``) are ported from the grammar actions;
* messages are formatted as ``parseError()`` (``src/main/source.c``) and the
  lexer's ``raiseLexError()`` do.

The syntax trees (:class:`Sym`, :class:`Lang`, constants) are also what
``knitr``'s chunk-option parser needs (:mod:`._purl`).

Not reproduced: the ``"invalid \\u{xxxx} sequence (line %d)"`` message, whose
line number R prints from an uninitialised argument, and the ``=>`` operator
when R's ``_R_USE_PIPEBIND_`` environment variable enables it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import typing
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from metacheck.codecheck import _rparse_tables as T

__all__ = [
    "Lang",
    "RParseError",
    "Sym",
    "parse_error",
    "parse_errors",
    "parse_exprs",
]

R_EOF = -1
_CONTEXT_SIZE = 256
_PUSHBACK = 16

# token numbers (gram.c)
END_OF_INPUT = 258
ERROR = 259
STR_CONST = 260
NUM_CONST = 261
NULL_CONST = 262
SYMBOL = 263
FUNCTION = 264
INCOMPLETE_STRING = 265
LEFT_ASSIGN = 266
EQ_ASSIGN = 267
RIGHT_ASSIGN = 268
LBB = 269
FOR = 270
IN = 271
IF = 272
ELSE = 273
WHILE = 274
NEXT = 275
BREAK = 276
REPEAT = 277
GT = 278
GE = 279
LT = 280
LE = 281
EQ = 282
NE = 283
AND = 284
OR = 285
AND2 = 286
OR2 = 287
NS_GET = 288
NS_GET_INT = 289
PIPE = 299
PLACEHOLDER = 300
PIPEBIND = 301
SPECIAL = 306

_YYEMPTY = -2
_YYEOF = 0
_YYERROR = 256
_SYM_YYUNDEF = 2
_SYM_YYERROR = 1


class RParseError(Exception):
    """A parse error, with R's message as ``str(exc)``."""


# ---------------------------------------------------------------------------
# syntax trees
# ---------------------------------------------------------------------------


class Sym:
    """An R symbol (interned: compare with ``is``)."""

    __slots__ = ("name",)
    name: str
    _table: typing.ClassVar[dict[str, Sym]] = {}

    def __new__(cls, name: str) -> Sym:
        sym = cls._table.get(name)
        if sym is None:
            sym = super().__new__(cls)
            sym.name = name
            cls._table[name] = sym
        return sym

    def __repr__(self) -> str:
        return f"Sym({self.name!r})"


class _Placeholder:
    __slots__ = ()

    def __repr__(self) -> str:
        return "_"


PLACEHOLDER_TOKEN = _Placeholder()


@dataclass
class Const:
    """A constant: ``value`` is a str, float, int, bool, complex or ``None``
    (``NULL``); ``kind`` is R's type (``"character"``, ``"double"``, ...);
    ``na`` marks ``NA`` values."""

    value: Any
    kind: str
    na: bool = False


NULL = Const(None, "NULL")


@dataclass
class Lang:
    """A call: ``fun`` and ``args`` as ``[tag, value]`` pairs (tag ``None``)."""

    fun: Any
    args: list[list[Any]] = field(default_factory=list)


@dataclass
class Formals:
    """A function's formal arguments: ``[name, default]`` (``MISSING``)."""

    items: list[list[Any]] = field(default_factory=list)


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()

_SPECIAL_SYMBOLS = frozenset(
    {
        "if",
        "while",
        "repeat",
        "for",
        "break",
        "next",
        "return",
        "function",
        "(",
        "{",
        "+",
        "-",
        "*",
        "/",
        "^",
        "%%",
        "%/%",
        "%*%",
        ":",
        "::",
        ":::",
        "?",
        "|>",
        "~",
        "@",
        "=>",
        "==",
        "!=",
        "<",
        ">",
        "<=",
        ">=",
        "&",
        "|",
        "&&",
        "||",
        "!",
        "<-",
        "<<-",
        "=",
        "$",
        "[",
        "[[",
        "$<-",
        "[<-",
        "[[<-",
    }
)

# ---------------------------------------------------------------------------
# glibc (C.UTF-8) character classes
# ---------------------------------------------------------------------------

_BLANK = frozenset({0x09, 0x20, 0x1680, *range(0x2000, 0x2007), 0x2008, 0x2009, 0x200A})
_BLANK = _BLANK | {0x205F, 0x3000}


def _iswalpha(wc: int) -> bool:
    """glibc's ``alpha`` class: Unicode Alphabetic, plus non-ASCII decimal digits."""
    if wc < 0x80:
        return (0x41 <= wc <= 0x5A) or (0x61 <= wc <= 0x7A)
    import regex

    ch = chr(wc)
    return regex.match(r"[\p{Alphabetic}\p{Nd}]", ch) is not None


def _iswalnum(wc: int) -> bool:
    return (0x30 <= wc <= 0x39) or _iswalpha(wc)


def _utf8clen(c: int) -> int:
    """R's ``utf8clen()``."""
    if c < 0xC0:
        return 1
    if c < 0xE0:
        return 2
    if c < 0xF0:
        return 3
    if c < 0xF8:
        return 4
    if c < 0xFC:
        return 5
    return 6


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------


@dataclass
class _Loc:
    first_line: int = 0
    first_column: int = 0
    last_line: int = 0
    last_column: int = 0

    def copy(self) -> _Loc:
        return _Loc(self.first_line, self.first_column, self.last_line, self.last_column)


_KEYWORDS: dict[str, tuple[int, Any]] = {
    "NULL": (NULL_CONST, NULL),
    "NA": (NUM_CONST, Const(None, "logical", True)),
    "TRUE": (NUM_CONST, Const(True, "logical")),
    "FALSE": (NUM_CONST, Const(False, "logical")),
    "Inf": (NUM_CONST, Const(float("inf"), "double")),
    "NaN": (NUM_CONST, Const(float("nan"), "double")),
    "NA_integer_": (NUM_CONST, Const(None, "integer", True)),
    "NA_real_": (NUM_CONST, Const(None, "double", True)),
    "NA_character_": (NUM_CONST, Const(None, "character", True)),
    "NA_complex_": (NUM_CONST, Const(None, "complex", True)),
    "function": (FUNCTION, None),
    "while": (WHILE, None),
    "repeat": (REPEAT, None),
    "for": (FOR, None),
    "if": (IF, None),
    "in": (IN, None),
    "else": (ELSE, None),
    "next": (NEXT, None),
    "break": (BREAK, None),
    "...": (SYMBOL, None),
}


class _Abort(Exception):
    """Internal: stop parsing (R's ``YYABORT``)."""


# ---------------------------------------------------------------------------
# the lexer and parser
# ---------------------------------------------------------------------------


class _Parser:
    """R's lexer (``gram.y``) and bison driver for one ``parse(text = )`` call."""

    def __init__(self, data: bytes, filename: str = "<text>") -> None:
        self.data = data
        self.n = len(data)
        self.pos = 0
        self.filename = filename
        self.pushback: list[int] = []
        self.prevpos = 0
        self.prevlines = [0] * _PUSHBACK
        self.prevcols = [0] * _PUSHBACK
        self.prevbytes = [0] * _PUSHBACK
        self.prevparse = [0] * _PUSHBACK
        # R_InitSrcRefState
        self.lineno = 1
        self.colno = 0
        self.byteno = 0
        self.parseno = 1
        # ParseContextInit
        self.ctx = [0] * _CONTEXT_SIZE
        self.ctx_last = 0
        self.ctx_line = 0
        self.charcount = 0
        self.end_of_file = 0
        self.yylloc = _Loc()
        self.yylval: Any = None
        self.have_placeholder = False
        self._parse_init()

    def _parse_init(self) -> None:
        self.contextstack: list[str] = [" "]
        self.saved_token = 0
        self.saved_lval: Any = None
        self.saved_loc = (0, 0)
        self.eat_lines = False
        self.end_of_file = 0
        self.charcount = 0
        self.pushback = []
        self.have_pipebind = False

    # -- character input ---------------------------------------------------

    def getc(self) -> int:
        if self.pushback:
            c = self.pushback.pop()
        elif self.pos < self.n:
            c = self.data[self.pos]
            self.pos += 1
        else:
            c = R_EOF
        self.prevpos = (self.prevpos + 1) % _PUSHBACK
        self.prevbytes[self.prevpos] = self.byteno
        self.prevlines[self.prevpos] = self.lineno
        self.prevparse[self.prevpos] = self.parseno
        self.prevcols[self.prevpos] = self.colno
        if c == R_EOF:
            self.end_of_file = 1
            return R_EOF
        self.ctx_last = (self.ctx_last + 1) % _CONTEXT_SIZE
        self.ctx[self.ctx_last] = c
        if c == 0x0A:
            self.lineno += 1
            self.colno = 0
            self.byteno = 0
            self.parseno += 1
        else:
            if c < 0x80 or c >= 0xC0:
                self.colno += 1
            self.byteno += 1
        if c == 0x09:
            self.colno = (self.colno + 7) & ~7
        self.ctx_line = self.lineno
        self.charcount += 1
        return c

    def ungetc(self, c: int) -> int:
        p = self.prevpos
        self.lineno = self.prevlines[p]
        self.byteno = self.prevbytes[p]
        self.colno = self.prevcols[p]
        self.parseno = self.prevparse[p]
        self.prevpos = (p + _PUSHBACK - 1) % _PUSHBACK
        self.ctx_line = self.lineno
        self.charcount -= 1
        self.ctx[self.ctx_last] = 0
        self.ctx_last = (self.ctx_last + _CONTEXT_SIZE - 1) % _CONTEXT_SIZE
        if len(self.pushback) >= _PUSHBACK:
            return R_EOF
        self.pushback.append(c)
        return c

    # -- errors ------------------------------------------------------------

    def lex_error(self, message: str) -> RParseError:
        """``raiseLexError()``: *message* gets ``(file:line:col)`` appended."""
        return RParseError(f"{message} ({self.filename}:{self.lineno}:{self.colno})")

    def parse_error_at(self, message: str, loc: _Loc | tuple[int, int]) -> RParseError:
        line, col = (loc.first_line, loc.first_column) if isinstance(loc, _Loc) else loc
        return RParseError(f"{message} ({self.filename}:{line}:{col})")

    def mbcs_get_next(self, c: int) -> tuple[int, int]:
        """``mbcs_get_next()``: ``(clen, wc)``; ``clen == -1`` at end of input."""
        if c < 0x80:
            return 1, c
        clen = _utf8clen(c)
        s = [c]
        for _ in range(1, clen):
            c2 = self.getc()
            if c2 == R_EOF:
                for b in reversed(s[1:]):
                    self.ungetc(b)
                return -1, 0
            s.append(c2)
        try:
            text = bytes(s).decode("utf-8")
            if len(text) != 1:
                raise UnicodeDecodeError("utf-8", bytes(s), 0, len(s), "too long")
        except UnicodeDecodeError:
            raise self.lex_error("invalid multibyte character in parser") from None
        for b in reversed(s[1:]):
            self.ungetc(b)
        return clen, ord(text)

    # -- token helpers -------------------------------------------------------

    def skip_space(self) -> int:
        while True:
            c = self.getc()
            if c in (0x20, 0x09, 0x0C):
                continue
            if c == 0x0A or c == R_EOF:
                break
            if c < 0x80:
                break
            clen, wc = self.mbcs_get_next(c)
            if clen == -1:
                self.ungetc(c)
                c = R_EOF
                break
            if wc not in _BLANK:
                break
            for _ in range(1, clen):
                c = self.getc()
        return c

    def skip_comment(self) -> int:
        c = 0x23
        maybe_line = self.colno == 1
        if maybe_line:
            for expected in b"line":
                c = self.getc()
                if c != expected:
                    maybe_line = False
                    break
            if maybe_line:
                c = self.process_line_directive()
        while c != 0x0A and c != R_EOF:
            c = self.getc()
        if c == R_EOF:
            self.end_of_file = 2
        return c

    def process_line_directive(self) -> int:
        c = self.skip_space()
        if not (0x30 <= c <= 0x39):
            return c
        self.numeric_value(c)
        linenumber = int(self._num_text) if self._num_text.isdigit() else 0
        c = self.skip_space()
        if c == 0x22:
            tok = self.string_value(c, False)
            if tok == STR_CONST and isinstance(self.yylval, Const):
                self.filename = str(self.yylval.value)
        else:
            self.ungetc(c)
        while True:
            c = self.getc()
            if c == 0x0A or c == R_EOF:
                break
        self.lineno = linenumber
        self.ctx[self.ctx_last] = 0
        return c

    def typeofnext(self) -> int:
        c = self.getc()
        k = 1 if 0x30 <= c <= 0x39 else 2
        self.ungetc(c)
        return k

    def nextchar(self, expect: int) -> bool:
        c = self.getc()
        if c == expect:
            return True
        self.ungetc(c)
        return False

    _num_text = ""

    def numeric_value(self, c: int) -> int:
        def isdigit(x: int) -> bool:
            return 0x30 <= x <= 0x39

        seendot = 1 if c == 0x2E else 0
        seenexp = 0
        last = c
        nd = 0
        count = 1
        text = [c]
        while True:
            c = self.getc()
            if not (isdigit(c) or c in (0x2E, 0x65, 0x45, 0x78, 0x58, 0x4C)):
                break
            count += 1
            if c == 0x4C:  # L
                text.append(c)
                break
            if c in (0x78, 0x58):  # x X
                if count > 2 or last != 0x30:
                    break
                text.append(c)
                while True:
                    c = self.getc()
                    if not (isdigit(c) or 0x61 <= c <= 0x66 or 0x41 <= c <= 0x46 or c == 0x2E):
                        break
                    if c == 0x2E:
                        if seendot:
                            return ERROR
                        seendot = 1
                    text.append(c)
                    nd += 1
                if nd == 0:
                    return ERROR
                if c in (0x70, 0x50):  # p P
                    seenexp = 1
                    text.append(c)
                    c = self.getc()
                    if not isdigit(c) and c not in (0x2B, 0x2D):
                        return ERROR
                    if c in (0x2B, 0x2D):
                        text.append(c)
                        c = self.getc()
                    nd = 0
                    while isdigit(c):
                        text.append(c)
                        c = self.getc()
                        nd += 1
                    if nd == 0:
                        return ERROR
                if c == 0x4C:
                    text.append(c)
                    break
                break
            if c in (0x45, 0x65):  # E e
                if seenexp:
                    break
                seenexp = 1
                seendot = seendot if seendot == 1 else 2
                text.append(c)
                c = self.getc()
                if not isdigit(c) and c not in (0x2B, 0x2D):
                    return ERROR
                if c in (0x2B, 0x2D):
                    text.append(c)
                    c = self.getc()
                    if not isdigit(c):
                        return ERROR
            if c == 0x2E:
                if seendot:
                    break
                seendot = 1
            text.append(c)
            last = c
        self._num_text = bytes(text).decode("latin-1")
        value = _r_atof(self._num_text.rstrip("L"))
        if c == 0x69:  # i
            self.yylval = Const(complex(0, value), "complex")
        elif c == 0x4C and value == int(value) and abs(value) < 2**31:
            self.yylval = Const(int(value), "integer")
        else:
            if c != 0x4C:
                self.ungetc(c)
            self.yylval = Const(value, "double")
        return NUM_CONST

    def string_value(self, c: int, for_symbol: bool) -> int:
        quote = c
        out: list[str] = []
        oct_or_hex = False
        use_wcs = False
        while True:
            c = self.getc()
            if c in (R_EOF, quote):
                break
            if c == 0x0A:
                self.ungetc(c)
                c = 0x5C  # pretend we saw a backslash
            if c == 0x5C:
                c = self.getc()
                if c == R_EOF:
                    break
                if 0x30 <= c <= 0x37:
                    octal = c - 0x30
                    c = self.getc()
                    if c == R_EOF:
                        break
                    if 0x30 <= c <= 0x37:
                        octal = 8 * octal + c - 0x30
                        c = self.getc()
                        if c == R_EOF:
                            break
                        if 0x30 <= c <= 0x37:
                            octal = 8 * octal + c - 0x30
                        else:
                            self.ungetc(c)
                    else:
                        self.ungetc(c)
                    if not octal:
                        raise self.lex_error("nul character not allowed")
                    if octal > 0xFF:
                        raise self.lex_error(
                            f"\\{octal:o} exceeds maximum allowed octal value \\377"
                        )
                    out.append(chr(octal))
                    oct_or_hex = True
                    continue
                if c == 0x78:  # x
                    val = 0
                    for i in range(2):
                        c = self.getc()
                        if c == R_EOF:
                            break
                        ext = _hexval(c)
                        if ext is None:
                            self.ungetc(c)
                            if i == 0:
                                raise self.lex_error(
                                    "'\\x' used without hex digits in character string"
                                )
                            break
                        val = 16 * val + ext
                    if c == R_EOF:
                        break
                    if not val:
                        raise self.lex_error("nul character not allowed")
                    out.append(chr(val))
                    oct_or_hex = True
                    continue
                if c in (0x75, 0x55):  # u U
                    big = c == 0x55
                    ndig = 8 if big else 4
                    if for_symbol:
                        seq = "\\Uxxxxxxxx" if big else "\\uxxxx"
                        raise self.lex_error(f"{seq} sequences not supported inside backticks")
                    c = self.getc()
                    if c == R_EOF:
                        break
                    delim = False
                    if c == 0x7B:
                        delim = True
                    else:
                        self.ungetc(c)
                    val = 0
                    for i in range(ndig):
                        c = self.getc()
                        if c == R_EOF:
                            break
                        ext = _hexval(c)
                        if ext is None:
                            self.ungetc(c)
                            if i == 0:
                                letter = "U" if big else "u"
                                raise self.lex_error(
                                    f"'\\{letter}' used without hex digits in character string"
                                )
                            break
                        val = 16 * val + ext
                    if c == R_EOF:
                        break
                    if delim:
                        c = self.getc()
                        if c == R_EOF:
                            break
                        if c != 0x7D:
                            if big:
                                raise self.lex_error("invalid \\U{xxxxxxxx} sequence")
                            # R prints an uninitialised argument as the line
                            raise RParseError("invalid \\u{xxxx} sequence (line NA)")
                    if not val:
                        raise self.lex_error("nul character not allowed")
                    if big and val > 0x10FFFF:
                        shape = "\\U{xxxxxxxx}" if delim else "\\Uxxxxxxxx"
                        raise self.lex_error(f"invalid {shape} value {val:6x}")
                    out.append(chr(val) if val <= 0x10FFFF else "�")
                    use_wcs = True
                    continue
                escapes = {
                    0x61: "\a",
                    0x62: "\b",
                    0x66: "\f",
                    0x6E: "\n",
                    0x72: "\r",
                    0x74: "\t",
                    0x76: "\v",
                    0x5C: "\\",
                    0x22: '"',
                    0x27: "'",
                    0x60: "`",
                    0x20: " ",
                    0x0A: "\n",
                }
                if c in escapes:
                    out.append(escapes[c])
                    continue
                if c >= 0x80:
                    # R quotes the lone lead byte of a multibyte character
                    ch = bytes([c]).decode("utf-8", "surrogateescape")
                    raise self.lex_error(f"'\\{ch}' is an unrecognized escape in character string")
                raise self.lex_error(f"'\\{chr(c)}' is an unrecognized escape in character string")
            if c >= 0x80:
                clen, wc = self.mbcs_get_next(c)
                if clen == -1:
                    self.ungetc(c)
                    c = R_EOF
                    break
                if 0x202A <= wc <= 0x2069 and not (0x202E < wc < 0x2066):
                    raise self.lex_error(
                        f"bidi formatting not allowed, use escapes instead (\\u{wc:04x})"
                    )
                self.byteno += clen - 1
                for _ in range(clen - 1):
                    c = self.getc()
                    if c == R_EOF:
                        break
                if c == R_EOF:
                    break
                out.append(chr(wc))
                continue
            out.append(chr(c))
        if c == R_EOF:
            self.yylval = None
            return INCOMPLETE_STRING
        text = "".join(out)
        if for_symbol:
            if not text:  # install("")
                raise RParseError("attempt to use zero-length variable name")
            self.yylval = Sym(text)
            return SYMBOL
        if use_wcs and oct_or_hex:
            raise self.lex_error("mixing Unicode and octal/hex escapes in a string is not allowed")
        self.yylval = Const(text, "character")
        return STR_CONST

    def raw_string_value(self, c0: int, c: int) -> int:  # noqa: ARG002 - as in R
        quote = c
        ndash = 0
        while self.nextchar(0x2D):
            ndash += 1
        c = self.getc()
        delims = {0x28: 0x29, 0x5B: 0x5D, 0x7B: 0x7D, 0x7C: 0x7C}
        if c not in delims:
            raise self.lex_error("malformed raw string literal")
        delim = delims[c]
        out: list[str] = []
        while True:
            c = self.getc()
            if c == R_EOF:
                break
            if c == delim:
                nd = 0
                while nd < ndash and self.nextchar(0x2D):
                    nd += 1
                if nd == ndash and self.nextchar(quote):
                    break
                out.append(chr(delim) + "-" * nd)
                continue
            if c >= 0x80:
                clen, wc = self.mbcs_get_next(c)
                if clen == -1:
                    self.ungetc(c)
                    c = R_EOF
                    break
                if 0x202A <= wc <= 0x2069 and not (0x202E < wc < 0x2066):
                    raise self.lex_error(
                        f"bidi formatting not allowed, use escapes instead (\\u{wc:04x})"
                    )
                self.byteno += clen - 1
                for _ in range(clen - 1):
                    c = self.getc()
                    if c == R_EOF:
                        break
                if c == R_EOF:
                    break
                out.append(chr(wc))
                continue
            out.append(chr(c))
        if c == R_EOF:
            self.yylval = None
            return INCOMPLETE_STRING
        self.yylval = Const("".join(out), "character")
        return STR_CONST

    def special_value(self, c: int) -> int:
        text = [c]
        while True:
            c = self.getc()
            if c == R_EOF or c == 0x25:
                break
            if c == 0x0A:
                self.ungetc(c)
                return ERROR
            text.append(c)
        if c == 0x25:
            text.append(c)
        self.yylval = Sym(bytes(text).decode("utf-8", "replace"))
        return SPECIAL

    def symbol_value(self, c: int) -> int:
        text = []
        clen, wc = self.mbcs_get_next(c)
        while clen != -1:
            for _ in range(clen):
                text.append(c)
                c = self.getc()
            if c == R_EOF:
                break
            if c in (0x2E, 0x5F):
                clen = 1
                continue
            clen, wc = self.mbcs_get_next(c)
            if clen == -1:
                break
            if not _iswalnum(wc):
                break
        self.ungetc(c)
        name = bytes(text).decode("utf-8", "replace")
        kw = _KEYWORDS.get(name)
        if kw is not None:
            tok, value = kw
            self.yylval = Sym(name) if value is None else value
            return tok
        self.yylval = Sym(name)
        return SYMBOL

    # -- token() -------------------------------------------------------------

    def token(self) -> int:
        if self.saved_token:
            c = self.saved_token
            self.yylval = self.saved_lval
            self.saved_lval = None
            self.saved_token = 0
            self.yylloc.first_line, self.yylloc.first_column = self.saved_loc
            return c
        c = self.skip_space()
        if c == 0x23:
            c = self.skip_comment()
        self.yylloc.first_line = self.lineno
        self.yylloc.first_column = self.colno
        if c == R_EOF:
            return END_OF_INPUT
        if c == 0x2E and self.typeofnext() >= 2:
            return self.symbol_value(c)
        if c == 0x2E or 0x30 <= c <= 0x39:
            return self.numeric_value(c)
        if c in (0x72, 0x52):  # r R
            if self.nextchar(0x22):
                return self.raw_string_value(c, 0x22)
            if self.nextchar(0x27):
                return self.raw_string_value(c, 0x27)
        if c in (0x22, 0x27):
            return self.string_value(c, False)
        if c == 0x25:
            return self.special_value(c)
        if c == 0x60:
            return self.string_value(c, True)
        if c == 0x5F:
            self.have_placeholder = True
            self.yylval = PLACEHOLDER_TOKEN
            return PLACEHOLDER
        clen, wc = self.mbcs_get_next(c)
        if clen == -1:
            return END_OF_INPUT
        if _iswalpha(wc):
            return self.symbol_value(c)
        return self.compound(c)

    def compound(self, c: int) -> int:
        nc = self.nextchar
        ch = chr(c) if c < 0x80 else ""
        if ch == "<":
            if nc(0x3D):
                self.yylval = Sym("<=")
                return LE
            if nc(0x2D):
                self.yylval = Sym("<-")
                return LEFT_ASSIGN
            if nc(0x3C):
                if nc(0x2D):
                    self.yylval = Sym("<<-")
                    return LEFT_ASSIGN
                return ERROR
            self.yylval = Sym("<")
            return LT
        if ch == "-":
            if nc(0x3E):
                self.yylval = Sym("<<-") if nc(0x3E) else Sym("<-")
                return RIGHT_ASSIGN
            self.yylval = Sym("-")
            return 0x2D
        if ch == ">":
            if nc(0x3D):
                self.yylval = Sym(">=")
                return GE
            self.yylval = Sym(">")
            return GT
        if ch == "!":
            if nc(0x3D):
                self.yylval = Sym("!=")
                return NE
            self.yylval = Sym("!")
            return 0x21
        if ch == "=":
            if nc(0x3D):
                self.yylval = Sym("==")
                return EQ
            if nc(0x3E):
                self.yylval = Sym("=>")
                self.have_pipebind = True
                return PIPEBIND
            self.yylval = Sym("=")
            return EQ_ASSIGN
        if ch == ":":
            if nc(0x3A):
                if nc(0x3A):
                    self.yylval = Sym(":::")
                    return NS_GET_INT
                self.yylval = Sym("::")
                return NS_GET
            if nc(0x3D):
                self.yylval = Sym(":=")
                return LEFT_ASSIGN
            self.yylval = Sym(":")
            return 0x3A
        if ch == "&":
            if nc(0x26):
                self.yylval = Sym("&&")
                return AND2
            self.yylval = Sym("&")
            return AND
        if ch == "|":
            if nc(0x7C):
                self.yylval = Sym("||")
                return OR2
            if nc(0x3E):
                self.yylval = Sym("|>")
                return PIPE
            self.yylval = Sym("|")
            return OR
        if ch and ch in "{(?":
            self.yylval = Sym(ch)
            return c
        if ch and ch in "})]":
            return c
        if ch == "[":
            if nc(0x5B):
                self.yylval = Sym("[[")
                return LBB
            self.yylval = Sym("[")
            return c
        if ch == "*":
            self.yylval = Sym("^") if nc(0x2A) else Sym("*")
            return 0x5E if self.yylval is Sym("^") else c
        if ch and ch in "+/^~$@\\":
            self.yylval = Sym(ch)
            return c
        if ch and ch in "\n,;":
            return c
        clen = 1
        if c >= 0x80:
            clen, _ = self.mbcs_get_next(c)
            if clen == -1:
                return END_OF_INPUT
        for _ in range(1, clen):
            self.getc()
        return c if clen == 1 else ERROR

    # -- yylex() -------------------------------------------------------------

    def _token(self) -> int:
        self.yylloc.first_line = self.lineno
        self.yylloc.first_column = self.colno
        return self.token()

    def _setlastloc(self) -> None:
        self.yylloc.last_line = self.lineno
        self.yylloc.last_column = self.colno

    def _ifpop(self) -> None:
        if self.contextstack[-1] == "i":
            self.contextstack.pop()

    def _pop_context(self) -> None:
        # *contextp-- = 0 never pops the bottom ' ' sentinel's slot below it
        if len(self.contextstack) > 1:
            self.contextstack.pop()
        else:
            self.contextstack[0] = "\0"

    def yylex(self) -> int:
        while True:
            tok = self._token()
            if tok == 0x0A:
                top = self.contextstack[-1]
                if self.eat_lines or top in ("[", "("):
                    continue
                if top == "i":
                    while tok == 0x0A:
                        tok = self._token()
                    if tok in (0x7D, 0x29, 0x5D):
                        while self.contextstack[-1] == "i":
                            self._ifpop()
                        self._pop_context()
                        self._setlastloc()
                        return tok
                    if tok == 0x2C:
                        self._ifpop()
                        self._setlastloc()
                        return tok
                    if tok == ELSE:
                        self.eat_lines = True
                        self._ifpop()
                        self._setlastloc()
                        return ELSE
                    self._ifpop()
                    self.saved_token = tok
                    self.saved_loc = (self.yylloc.first_line, self.yylloc.first_column)
                    self.saved_lval = self.yylval
                    self._setlastloc()
                    return 0x0A
                self._setlastloc()
                return 0x0A
            break

        if tok in _EAT_AFTER:
            self.eat_lines = True
        elif tok == IF:
            if self.contextstack[-1] in ("{", "[", "(", "i"):
                self.contextstack.append("i")
            self.eat_lines = True
        elif tok == ELSE:
            self._ifpop()
            self.eat_lines = True
        elif tok in (0x3B, 0x2C):
            self._ifpop()
        elif tok in (SYMBOL, PLACEHOLDER, STR_CONST, NUM_CONST, NULL_CONST, NEXT, BREAK):
            self.eat_lines = False
        elif tok == LBB:
            self.contextstack.extend(("[", "["))
        elif tok in (0x5B, 0x28):
            self.contextstack.append(chr(tok))
        elif tok == 0x7B:
            self.contextstack.append("{")
            self.eat_lines = True
        elif tok in (0x5D, 0x29):
            while self.contextstack[-1] == "i":
                self._ifpop()
            self._pop_context()
            self.eat_lines = False
        elif tok == 0x7D:
            while self.contextstack[-1] == "i":
                self._ifpop()
            self._pop_context()
        self._setlastloc()
        return tok

    # -- the bison driver ----------------------------------------------------

    def parse1(self) -> tuple[int, Any]:
        """``R_Parse1()``: ``(status, expr)`` -- 0 EOF, 2 empty, 3/4 an expression."""
        states = [0]
        values: list[Any] = [None]
        locs: list[_Loc] = [self.yylloc.copy()]
        yychar = _YYEMPTY
        while True:
            state = states[-1]
            if state == T.YYFINAL:
                raise AssertionError("parser accepted without a program")  # pragma: no cover
            yyn = T.YYPACT[state]
            action_reduce = None
            if yyn != T.YYPACT_NINF:
                if yychar == _YYEMPTY:
                    yychar = self.yylex()
                if yychar <= _YYEOF:
                    yychar = _YYEOF
                    yytoken = 0
                elif yychar == _YYERROR:
                    yytoken = _SYM_YYERROR
                else:
                    yytoken = T.YYTRANSLATE[yychar] if 0 <= yychar <= T.YYMAXUTOK else _SYM_YYUNDEF
                idx = yyn + yytoken
                if 0 <= idx <= T.YYLAST and T.YYCHECK[idx] == yytoken:
                    act = T.YYTABLE[idx]
                    if act <= 0:
                        if act == T.YYTABLE_NINF:
                            raise self._syntax_error(yytoken)
                        action_reduce = -act
                    else:
                        states.append(act)
                        values.append(self.yylval)
                        locs.append(self.yylloc.copy())
                        yychar = _YYEMPTY
                        continue
            if action_reduce is None:
                rule = T.YYDEFACT[state]
                if rule == 0:
                    yytoken = (
                        -2
                        if yychar == _YYEMPTY
                        else (T.YYTRANSLATE[yychar] if 0 <= yychar <= T.YYMAXUTOK else 2)
                    )
                    raise self._syntax_error(yytoken)
                action_reduce = rule
            rule = action_reduce
            length = T.YYR2[rule]
            rhs_vals = values[len(values) - length :] if length else []
            rhs_locs = locs[len(locs) - length :] if length else []
            if length:
                loc = _Loc(
                    rhs_locs[0].first_line,
                    rhs_locs[0].first_column,
                    rhs_locs[-1].last_line,
                    rhs_locs[-1].last_column,
                )
            else:
                prev = locs[-1]
                loc = _Loc(prev.last_line, prev.last_column, prev.last_line, prev.last_column - 1)
            result = self._action(rule, rhs_vals, rhs_locs, loc)
            if rule in (2, 3, 4, 5):
                return typing.cast("tuple[int, Any]", result)
            if length:
                del states[-length:]
                del values[-length:]
                del locs[-length:]
            lhs = T.YYR1[rule] - T.YYNTOKENS
            top = states[-1]
            goto = T.YYPGOTO[lhs] + top
            if 0 <= goto <= T.YYLAST and T.YYCHECK[goto] == top:
                new_state = T.YYTABLE[goto]
            else:
                new_state = T.YYDEFGOTO[lhs]
            states.append(new_state)
            values.append(result)
            locs.append(loc)

    def _syntax_error(self, yytoken: int) -> RParseError:
        """``yyerror()`` + ``parseError()`` for a bison syntax error."""
        if yytoken == -2:
            msg = "syntax error"
        else:
            name = _yytnamerr(T.YYTNAME[yytoken])
            msg = f"unexpected {_TRANSLATIONS.get(name, name)}"
        self._finish_mbcs_in_parse_context()
        return RParseError(self._format_parse_error(msg))

    def _finish_mbcs_in_parse_context(self) -> None:
        if self.end_of_file:
            return
        nbytes = 0
        i = self.ctx_last
        while self.ctx[i]:
            nbytes += 1
            if nbytes == _CONTEXT_SIZE:
                return
            i = (i + _CONTEXT_SIZE - 1) % _CONTEXT_SIZE
        if not nbytes:
            return
        first = (i + 1) % _CONTEXT_SIZE
        k = 0
        while k < nbytes:
            c = self.ctx[(first + k) % _CONTEXT_SIZE]
            if c >= 0x80:
                k += _utf8clen(c) - 1
                if k >= nbytes:
                    while k >= nbytes:
                        if self.pushback:
                            b = self.pushback.pop()
                        elif self.pos < self.n:
                            b = self.data[self.pos]
                            self.pos += 1
                        else:
                            return
                        self.ctx_last = (self.ctx_last + 1) % _CONTEXT_SIZE
                        self.ctx[self.ctx_last] = b
                        nbytes += 1
                    return
            k += 1

    def _context_lines(self) -> tuple[list[bytes], int]:
        """``getParseContext()``: the last lines read, and their last line number."""
        chars: list[int] = []
        i = self.ctx_last
        for _ in range(_CONTEXT_SIZE):
            c = self.ctx[i]
            if not c:
                break
            chars.append(c)
            i = (i + _CONTEXT_SIZE - 1) % _CONTEXT_SIZE
        chars.reverse()
        text = bytes(chars)
        line = self.ctx_line
        if not text:
            return [], line
        lines = text.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
            line -= 1
        return lines, line

    def _format_parse_error(self, msg: str) -> str:
        """``parseError()`` with R's error line/column and context."""
        linenum = self.yylloc.first_line
        col = self.yylloc.first_column
        lines, ctx_line = self._context_lines()
        lines = [_tab_expand(s) for s in lines]
        prefix = f"{self.filename}:" if self.filename else ""
        if not linenum:
            if not lines:
                return msg
            if len(lines) == 1:
                return f'{msg} in "{_decode(lines[0])}"'
            return f'{msg} in:\n"{_decode(lines[-2])}\n{_decode(lines[-1])}"'
        head = f"{prefix}{linenum}:{col}: {msg}"
        if not lines:
            return head
        if len(lines) == 1:
            width = len(f"{ctx_line}: ")
            caret = "^".rjust(width + col + 1)
            return f"{head}\n{ctx_line}: {_decode(lines[0])}\n{caret}"
        width = len(f"{ctx_line}:")
        caret = "^".rjust(width + col + 1)
        return (
            f"{head}\n{ctx_line - 1}: {_decode(lines[-2])}\n"
            f"{ctx_line}: {_decode(lines[-1])}\n{caret}"
        )

    # -- grammar actions -----------------------------------------------------

    def _action(self, rule: int, v: list[Any], locs: list[_Loc], loc: _Loc) -> Any:  # noqa: ARG002
        if rule == 2:
            return (0, None)
        if rule == 3:
            return (2, None)
        if rule in (4, 5):
            return (3 if rule == 4 else 4, v[0])
        if rule == 6:  # pragma: no cover - errors are raised before recovery
            raise _Abort
        if rule in (7, 10, 12, 13, 14, 15, 16):
            return v[0]
        if rule in (8, 9, 11):
            return Lang(v[1], [[None, v[0]], [None, v[2]]])
        if rule == 17:
            self.eat_lines = False
            return Lang(v[0], [[None, e] for e in v[1]])
        if rule == 18:
            return Lang(v[0], [[None, v[1]]])
        if rule in (19, 20, 21, 22, 23):
            return Lang(v[0], [[None, v[1]]])
        if 24 <= rule <= 41 or rule == 44:
            return Lang(v[1], [[None, v[0]], [None, v[2]]])
        if rule == 42:
            return self._pipe(v[0], v[2], locs[2])
        if rule == 43:
            raise self.parse_error_at(
                "'=>' is disabled; set '_R_USE_PIPEBIND_' envvar to a true value to enable it",
                locs[1],
            )
        if rule == 45:
            return Lang(v[1], [[None, v[2]], [None, v[0]]])
        if rule in (46, 47):
            return Lang(Sym("function"), [[None, v[2]], [None, v[5]], [None, None]])
        if rule == 48:
            fun = v[0]
            if isinstance(fun, Const) and fun.kind == "character":
                fun = Sym("NA" if fun.na else fun.value)
            args = v[2]
            if len(args) == 1 and args[0][0] is None and args[0][1] is MISSING:
                args = []
            return Lang(fun, args)
        if rule == 49:
            return Lang(v[0], [[None, v[1]], [None, v[2]]])
        if rule == 50:
            return Lang(v[0], [[None, v[1]], [None, v[2]], [None, v[4]]])
        if rule == 51:
            return Lang(v[0], [[None, v[1][0]], [None, v[1][1]], [None, v[2]]])
        if rule == 52:
            return Lang(v[0], [[None, v[1]], [None, v[2]]])
        if rule == 53:
            return Lang(v[0], [[None, v[1]]])
        if rule in (54, 55):
            return Lang(v[1], [[None, v[0]], *v[2]])
        if 56 <= rule <= 67:
            return Lang(v[1], [[None, v[0]], [None, v[2]]])
        if rule in (68, 69):
            return Lang(v[0], [])
        if rule in (70, 71):
            self.eat_lines = True
            return v[1]
        if rule == 72:
            self.eat_lines = True
            return (v[1], v[3])
        if rule == 73:
            return []
        if rule == 74:
            return [v[0]]
        if rule in (75, 77):
            return [*v[0], v[2]]
        if rule in (76, 78):
            return v[0]
        if rule == 79:
            return [v[0]] if v[0] is not None else [[None, MISSING]]
        if rule == 80:
            return [*v[0], v[3] if v[3] is not None else [None, MISSING]]
        if rule == 81:
            return None
        if rule == 82:
            return [None, v[0]]
        if rule in (83, 84, 85, 86):
            tag = v[0]
            if isinstance(tag, Const):
                tag = Sym("NA" if tag.na else str(tag.value))
            return [tag, MISSING if rule in (83, 85) else v[2]]
        if rule in (87, 88):
            return [Sym("NULL"), MISSING if rule == 87 else v[2]]
        if rule == 89:
            return Formals([])
        if rule == 90:
            return Formals([[v[0], MISSING]])
        if rule == 91:
            return Formals([[v[0], v[2]]])
        if rule in (92, 93):
            formals: Formals = v[0]
            if not formals.items:  # NextArg() on R_NilValue: SETCDR() fails
                raise RParseError("bad value")
            for name, _ in formals.items:
                if name is v[2]:
                    raise self.parse_error_at(f"repeated formal argument '{v[2].name}'", locs[2])
            default = MISSING if rule == 92 else v[4]
            return Formals([*formals.items, [v[2], default]])
        if rule == 94:
            self.eat_lines = True
            return None
        raise AssertionError(f"unknown rule {rule}")  # pragma: no cover

    def _pipe(self, lhs: Any, rhs: Any, loc: _Loc) -> Any:
        """``xxpipe()``."""
        if not isinstance(rhs, Lang):
            raise self.parse_error_at("The pipe operator requires a function call as RHS", loc)
        if _has_placeholder(rhs.fun):
            raise self.parse_error_at("pipe placeholder cannot be used in the RHS function", loc)
        phcell = self._extractor_chain(rhs, rhs, loc)
        if phcell is not None:
            phcell[1] = lhs
            return rhs
        for i, arg in enumerate(rhs.args):
            if arg[1] is PLACEHOLDER_TOKEN:
                if arg[0] is None:
                    raise self.parse_error_at(
                        "pipe placeholder can only be used as a named argument", loc
                    )
                for later in rhs.args[i + 1 :]:
                    if later[1] is PLACEHOLDER_TOKEN:
                        raise self.parse_error_at("pipe placeholder may only appear once", loc)
                arg[1] = lhs
                return rhs
        fun = rhs.fun
        if isinstance(fun, Sym) and fun.name in _SPECIAL_SYMBOLS:
            raise self.parse_error_at(
                f"function '{fun.name}' not supported in RHS call of a pipe", loc
            )
        return Lang(fun, [[None, lhs], *rhs.args])

    def _extractor_chain(self, rhs: Lang, expr: Any, loc: _Loc) -> list[Any] | None:
        if not isinstance(expr, Lang) or not isinstance(expr.fun, Sym):
            return None
        if expr.fun.name not in ("[", "[[", "$", "@") or not expr.args:
            return None
        arg1 = expr.args[0]
        phcell = arg1 if arg1[1] is PLACEHOLDER_TOKEN else self._extractor_chain(rhs, arg1[1], loc)
        if phcell is not None and any(_has_placeholder(a[1]) for a in expr.args[1:]):
            raise self.parse_error_at("pipe placeholder may only appear once", loc)
        return phcell

    # -- R_Parse() -------------------------------------------------------------

    def parse_all(self) -> list[Any]:
        exprs: list[Any] = []
        while True:
            self._parse_init()
            status, expr = self.parse1()
            if status == 0:
                return exprs
            if status == 2:
                continue
            if _has_placeholder(expr):
                line = self.lineno - (1 if status == 3 else 0)
                raise self.parse_error_at("invalid use of pipe placeholder", (line, self.colno))
            exprs.append(expr)


_EAT_AFTER = frozenset(
    {
        0x2B,
        0x2D,
        0x2A,
        0x2F,
        0x5E,
        LT,
        LE,
        GE,
        GT,
        EQ,
        NE,
        OR,
        AND,
        OR2,
        AND2,
        PIPE,
        PIPEBIND,
        SPECIAL,
        FUNCTION,
        WHILE,
        REPEAT,
        FOR,
        IN,
        0x3F,
        0x21,
        0x3D,
        0x3A,
        0x7E,
        0x24,
        0x40,
        LEFT_ASSIGN,
        RIGHT_ASSIGN,
        EQ_ASSIGN,
    }
)

_TRANSLATIONS = {
    "$undefined": "input",
    "END_OF_INPUT": "end of input",
    "ERROR": "input",
    "STR_CONST": "string constant",
    "NUM_CONST": "numeric constant",
    "SYMBOL": "symbol",
    "LEFT_ASSIGN": "assignment",
    "'\\n'": "end of line",
    "NULL_CONST": "'NULL'",
    "FUNCTION": "'function'",
    "EQ_ASSIGN": "'='",
    "RIGHT_ASSIGN": "'->'",
    "LBB": "'[['",
    "FOR": "'for'",
    "IN": "'in'",
    "IF": "'if'",
    "ELSE": "'else'",
    "WHILE": "'while'",
    "NEXT": "'next'",
    "BREAK": "'break'",
    "REPEAT": "'repeat'",
    "GT": "'>'",
    "GE": "'>='",
    "LT": "'<'",
    "LE": "'<='",
    "EQ": "'=='",
    "NE": "'!='",
    "AND": "'&'",
    "OR": "'|'",
    "AND2": "'&&'",
    "OR2": "'||'",
    "NS_GET": "'::'",
    "NS_GET_INT": "':::'",
    "PIPE": "'|>'",
    "PIPEBIND": "'=>'",
    "PLACEHOLDER": "input",
}


def _yytnamerr(name: str) -> str:
    """bison's ``yytnamerr()``: strip unnecessary double quotes."""
    if name.startswith('"'):
        out = []
        i = 1
        while i < len(name):
            ch = name[i]
            if ch in ("'", ","):
                return name
            if ch == "\\":
                i += 1
                if i >= len(name) or name[i] != "\\":
                    return name
                out.append("\\")
            elif ch == '"':
                return "".join(out)
            else:
                out.append(ch)
            i += 1
    return name


def _hexval(c: int) -> int | None:
    if 0x30 <= c <= 0x39:
        return c - 0x30
    if 0x41 <= c <= 0x46:
        return c - 0x41 + 10
    if 0x61 <= c <= 0x66:
        return c - 0x61 + 10
    return None


def _r_atof(s: str) -> float:
    """``R_atof()`` for the numerals the lexer accepts (decimal and hex)."""
    try:
        if s[:2].lower() == "0x":
            body = s[2:]
            if "p" in body.lower():
                return float.fromhex("0x" + body)
            if "." in body:
                return float.fromhex("0x" + body + "p0")
            return float(int(body, 16)) if body else 0.0
        return float(s)
    except ValueError:
        return float("nan")


def _tab_expand(line: bytes) -> bytes:
    """``tabExpand()``: tabs to multiples of 8 bytes, at most 192 bytes kept."""
    out = bytearray()
    for b in line:
        if len(out) >= 192:
            break
        if b == 0x09:
            out.append(0x20)
            while len(out) & 7:
                out.append(0x20)
        else:
            out.append(b)
    return bytes(out)


def _decode(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def _has_placeholder(x: Any) -> bool:
    """``checkForPlaceholder()``: in a call, its function or its arguments."""
    if x is PLACEHOLDER_TOKEN:
        return True
    if isinstance(x, Lang):
        return _has_placeholder(x.fun) or any(_has_placeholder(a[1]) for a in x.args)
    return False


def _source_bytes(lines: Sequence[str | None]) -> bytes:
    """What R's text buffer feeds the parser: every element followed by ``"\\n"``."""
    return b"".join(
        ("NA" if s is None else s).encode("utf-8", "surrogateescape") + b"\n" for s in lines
    )


def parse_exprs(lines: Sequence[str | None], filename: str = "<text>") -> list[Any]:
    """``parse(text = lines)``: the expressions, or :class:`RParseError`."""
    if not lines:
        return []
    return _Parser(_source_bytes(lines), filename).parse_all()


def _parse_error_py(lines: Sequence[str | None]) -> str | None:
    try:
        parse_exprs(lines)
    except RParseError as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# R itself
# ---------------------------------------------------------------------------

_R_SCRIPT = r"""
args <- commandArgs(TRUE)
cases <- jsonlite::fromJSON(args[1], simplifyVector = FALSE)
out <- lapply(cases, function(x) {
  x <- as.character(unlist(x))
  if (!length(x)) return(NULL)
  tryCatch({parse(text = x, keep.source = TRUE); NULL},
           error = function(e) conditionMessage(e))
})
jsonlite::write_json(out, args[2], auto_unbox = TRUE, null = "null")
"""


def rscript(search_path: bool = True) -> str | None:
    """The ``Rscript`` to use: ``PYTACHECK_RSCRIPT``, else (with *search_path*)
    ``Rscript`` on the ``PATH``."""
    env = os.environ.get("PYTACHECK_RSCRIPT")
    if env and os.path.exists(env):
        return env
    return shutil.which("Rscript") if search_path else None


def _parse_errors_r(texts: Sequence[Sequence[str | None]], script: str) -> list[str | None]:
    with tempfile.TemporaryDirectory(prefix="pytacheck-parse-") as tmp:
        src = os.path.join(tmp, "cases.json")
        dst = os.path.join(tmp, "out.json")
        code = os.path.join(tmp, "parse.R")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump([list(t) for t in texts], fh)
        with open(code, "w", encoding="utf-8") as fh:
            fh.write(_R_SCRIPT)
        env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        subprocess.run(  # noqa: S603 - our own script, paths we created
            [script, "--vanilla", code, src, dst],
            check=True,
            capture_output=True,
            env=env,
            timeout=600,
        )
        with open(dst, encoding="utf-8") as fh:
            out = json.load(fh)
    return [None if m is None else str(m) for m in out]


def parse_errors(
    texts: Sequence[Sequence[str | None]], engine: str | None = None
) -> list[str | None]:
    """R's ``parse(text = x)`` error message (or ``None``) for each text.

    *engine* ``"python"`` uses this module's port of R 4.5.3's parser; ``"r"``
    runs R's own parser, all texts in one ``Rscript`` process
    (``PYTACHECK_RSCRIPT``, else ``Rscript`` on the ``PATH``), falling back to
    Python when no R is found. ``None`` (default) takes ``PYTACHECK_R_PARSER``
    if set, else R when the reference R is configured (``PYTACHECK_RSCRIPT``),
    else Python -- an arbitrary ``Rscript`` on the ``PATH`` may be a different
    R version whose messages differ.
    """
    engine = engine or os.environ.get("PYTACHECK_R_PARSER") or None
    if engine is None:
        engine = "r" if rscript(search_path=False) is not None else "python"
    if engine == "r":
        script = rscript()
        if script is not None:
            try:
                return _parse_errors_r(texts, script)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
    return [_parse_error_py(t) for t in texts]


def parse_error(lines: Sequence[str | None], engine: str | None = None) -> str | None:
    """R's ``parse(text = lines)`` error message, or ``None`` when it parses."""
    return parse_errors([list(lines)], engine)[0]
