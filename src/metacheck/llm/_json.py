"""jsonlite-compatible JSON: request bodies and response parsing.

metacheck talks to LLM providers through ellmer, which serialises request
bodies with ``httr2::req_body_json()`` (``jsonlite::toJSON(x, auto_unbox =
TRUE, digits = 22, null = "null")``) and parses responses with
``jsonlite::parse_json()`` (yajl). Reproducing both byte for byte matters:

* recorded API fixtures are keyed by a hash of the request body, so the same
  call must produce the same bytes in R and Python;
* a structured-output reply that is not valid JSON fails with yajl's error
  text (``"lexical error: invalid char in json text."``...), which metacheck
  inspects (``.llm_json_retryable()``) and stores in ``.error_msg``.

:func:`to_json` writes JSON the way jsonlite does (compact, doubles with 17
significant digits, non-ASCII kept as is); :class:`Vec` marks an R atomic
vector, which jsonlite unboxes when it has length 1 (a plain Python list is
an R list and is always an array). :func:`parse_json` is a port of yajl's
lexer/parser state machine with jsonlite's value mapping (integers that fit
in 32 bits -> ``int``, other numbers -> ``float``, ``null`` -> ``None``).
"""

from __future__ import annotations

import json as _stdjson
import math
from typing import Any

from metacheck.llm._rds import RInt

__all__ = ["JSONParseError", "Vec", "parse_json", "to_json"]


class Vec(list):  # type: ignore[type-arg]
    """An R atomic vector: serialised as a scalar when it has length 1."""


def _num(x: float) -> str:
    if math.isnan(x) or math.isinf(x):
        # jsonlite writes non-finite doubles as strings ("NA", "Inf", ...)
        if math.isnan(x):
            return '"NA"'
        return '"Inf"' if x > 0 else '"-Inf"'
    return f"{x:.17g}"


def to_json(x: Any) -> str:
    """``jsonlite::toJSON(x, auto_unbox = TRUE, digits = 22, null = "null")``."""
    parts: list[str] = []
    _write(x, parts)
    return "".join(parts)


def _write(x: Any, out: list[str]) -> None:
    if x is None:
        out.append("null")
    elif isinstance(x, bool):
        out.append("true" if x else "false")
    elif isinstance(x, int):
        out.append(str(int(x)))
    elif isinstance(x, float):
        out.append(_num(x))
    elif isinstance(x, str):
        out.append(_stdjson.dumps(x, ensure_ascii=False))
    elif isinstance(x, dict):
        if not x:
            out.append("{}")
            return
        out.append("{")
        first = True
        for k, v in x.items():
            if not first:
                out.append(",")
            first = False
            out.append(_stdjson.dumps(str(k), ensure_ascii=False))
            out.append(":")
            _write(v, out)
        out.append("}")
    elif isinstance(x, Vec):
        if len(x) == 1:
            _write(x[0], out)
            return
        out.append("[")
        for i, v in enumerate(x):
            if i:
                out.append(",")
            _write(v, out)
        out.append("]")
    elif isinstance(x, list | tuple):
        out.append("[")
        for i, v in enumerate(x):
            if i:
                out.append(",")
            _write(v, out)
        out.append("]")
    else:
        try:
            import numpy as np

            if isinstance(x, np.bool_):
                out.append("true" if bool(x) else "false")
                return
            if isinstance(x, np.integer):
                out.append(str(int(x)))
                return
            if isinstance(x, np.floating):
                out.append(_num(float(x)))
                return
        except ImportError:  # pragma: no cover
            pass
        out.append(_stdjson.dumps(str(x), ensure_ascii=False))


# ---------------------------------------------------------------------------
# yajl parser (jsonlite::parse_json)
# ---------------------------------------------------------------------------


class JSONParseError(ValueError):
    """A yajl parse error; the message is exactly jsonlite's."""


_ARROW = "                     (right here) ------^\n"

# lexer tokens
_T_EOF, _T_ERROR = "eof", "error"
_T_LBRACE, _T_RBRACE, _T_LBRACKET, _T_RBRACKET = "{", "}", "[", "]"
_T_COMMA, _T_COLON = ",", ":"
_T_BOOL, _T_NULL, _T_STRING, _T_INTEGER, _T_DOUBLE = "bool", "null", "string", "int", "dbl"

_LEX_ERRORS = {
    "invalid_char": "invalid char in json text.",
    "invalid_string": "invalid string in json text.",
    "invalid_escape": "inside a string, '\\' occurs before a character which it may not.",
    "invalid_json_char": "invalid character inside string.",
    "invalid_hex": "invalid (non-hex) character occurs after '\\u' inside string.",
    "missing_after_minus": "malformed number, a digit is required after the minus sign.",
    "missing_after_decimal": "malformed number, a digit is required after the decimal point.",
    "missing_after_exponent": "malformed number, a digit is required after the exponent.",
    "unallowed_comment": "probable comment found in input text, comments are not enabled.",
}
_ESCAPES = {
    ord('"'): '"',
    ord("\\"): "\\",
    ord("/"): "/",
    ord("b"): "\b",
    ord("f"): "\f",
    ord("n"): "\n",
    ord("r"): "\r",
    ord("t"): "\t",
}
_HEX = set(b"0123456789abcdefABCDEF")
_WS = set(b" \t\n\v\f\r")


class _Lexer:
    """yajl_lex over the whole input plus the single space of yajl_complete_parse()."""

    def __init__(self, data: bytes) -> None:
        self.buf = data + b" "
        self.n = len(data)
        self.error = ""
        self.value: Any = None

    def lex(self, pos: int) -> tuple[str, int, int]:
        """Return ``(token, new_offset, token_length)`` starting at *pos*."""
        buf = self.buf
        end = len(buf)
        while True:
            if pos >= end:
                return _T_EOF, pos, 0
            c = buf[pos]
            start = pos
            pos += 1
            if c in _WS:
                continue
            if c == 0x7B:
                return _T_LBRACE, pos, 1
            if c == 0x7D:
                return _T_RBRACE, pos, 1
            if c == 0x5B:
                return _T_LBRACKET, pos, 1
            if c == 0x5D:
                return _T_RBRACKET, pos, 1
            if c == 0x2C:
                return _T_COMMA, pos, 1
            if c == 0x3A:
                return _T_COLON, pos, 1
            if c in (0x74, 0x66, 0x6E):  # t f n
                want = {0x74: b"rue", 0x66: b"alse", 0x6E: b"ull"}[c]
                for w in want:
                    if pos >= end:
                        return _T_EOF, pos, 0
                    if buf[pos] != w:
                        self.error = "invalid_string"
                        return _T_ERROR, pos, 0
                    pos += 1
                if c == 0x6E:
                    self.value = None
                    return _T_NULL, pos, 4
                self.value = c == 0x74
                return _T_BOOL, pos, pos - start
            if c == 0x22:
                return self._string(pos)
            if c == 0x2D or 0x30 <= c <= 0x39:
                return self._number(start)
            if c == 0x2F:  # comments are allowed by jsonlite
                if pos >= end:
                    return _T_EOF, pos, 0
                c2 = buf[pos]
                pos += 1
                if c2 == 0x2F:
                    while pos < end and buf[pos] != 0x0A:
                        pos += 1
                    if pos >= end:
                        return _T_EOF, pos, 0
                    pos += 1
                    continue
                if c2 == 0x2A:
                    while True:
                        if pos + 1 >= end:
                            return _T_EOF, end, 0
                        if buf[pos] == 0x2A and buf[pos + 1] == 0x2F:
                            pos += 2
                            break
                        pos += 1
                    continue
                self.error = "invalid_char"
                return _T_ERROR, pos, 0
            self.error = "invalid_char"
            return _T_ERROR, pos, 0

    def _string(self, pos: int) -> tuple[str, int, int]:
        buf = self.buf
        end = len(buf)
        start = pos
        chunks: list[bytes] = []
        seg = pos
        while True:
            if pos >= end:
                return _T_EOF, pos, 0
            c = buf[pos]
            pos += 1
            if c == 0x22:
                chunks.append(buf[seg : pos - 1])
                self.value = b"".join(chunks).decode("utf-8", "replace")
                return _T_STRING, pos, pos - 1 - start
            if c == 0x5C:
                chunks.append(buf[seg : pos - 1])
                if pos >= end:
                    return _T_EOF, pos, 0
                e = buf[pos]
                pos += 1
                if e == 0x75:  # \u
                    hexs = bytearray()
                    for _ in range(4):
                        if pos >= end:
                            return _T_EOF, pos, 0
                        h = buf[pos]
                        pos += 1
                        if h not in _HEX:
                            pos -= 1
                            self.error = "invalid_hex"
                            return _T_ERROR, pos, 0
                        hexs.append(h)
                    chunks.append(_utf16_unit(int(hexs.decode(), 16)))
                elif e in _ESCAPES:
                    chunks.append(_ESCAPES[e].encode())
                else:
                    pos -= 1
                    self.error = "invalid_escape"
                    return _T_ERROR, pos, 0
                seg = pos
            elif c < 0x20:
                pos -= 1
                self.error = "invalid_json_char"
                return _T_ERROR, pos, 0

    def _number(self, pos: int) -> tuple[str, int, int]:
        buf = self.buf
        end = len(buf)
        start = pos
        is_double = False

        def read() -> int | None:
            nonlocal pos
            if pos >= end:
                return None
            ch = buf[pos]
            pos += 1
            return ch

        c = read()
        if c == 0x2D:
            c = read()
            if c is None:
                return _T_EOF, pos, 0
        if c == 0x30:
            c = read()
            if c is None:
                return _T_EOF, pos, 0
        elif c is not None and 0x31 <= c <= 0x39:
            while True:
                c = read()
                if c is None:
                    return _T_EOF, pos, 0
                if not 0x30 <= c <= 0x39:
                    break
        else:
            pos -= 1
            self.error = "missing_after_minus"
            return _T_ERROR, pos, 0
        if c == 0x2E:
            c = read()
            if c is None:
                return _T_EOF, pos, 0
            if 0x30 <= c <= 0x39:
                while True:
                    c = read()
                    if c is None:
                        return _T_EOF, pos, 0
                    if not 0x30 <= c <= 0x39:
                        break
            else:
                pos -= 1
                self.error = "missing_after_decimal"
                return _T_ERROR, pos, 0
            is_double = True
        if c in (0x65, 0x45):
            c = read()
            if c is None:
                return _T_EOF, pos, 0
            if c in (0x2B, 0x2D):
                c = read()
                if c is None:
                    return _T_EOF, pos, 0
            if c is not None and 0x30 <= c <= 0x39:
                while True:
                    c = read()
                    if c is None:
                        return _T_EOF, pos, 0
                    if not 0x30 <= c <= 0x39:
                        break
            else:
                pos -= 1
                self.error = "missing_after_exponent"
                return _T_ERROR, pos, 0
            is_double = True
        pos -= 1  # yajl always reads one char too far
        text = buf[start:pos].decode()
        if not is_double:
            v = int(text)
            if -(2**63) <= v < 2**63:
                self.value = RInt(v) if -(2**31) < v < 2**31 else float(v)
                return _T_INTEGER, pos, pos - start
        self.value = float(text)
        return _T_DOUBLE, pos, pos - start


def _utf16_unit(code: int) -> bytes:
    return chr(code).encode("utf-8", "surrogatepass")


_VALUE_TOKENS = (_T_BOOL, _T_NULL, _T_STRING, _T_INTEGER, _T_DOUBLE)
_CONSUMED_ERRORS = ("invalid_char",)  # yajl does not unread the offending char


def parse_json(text: str) -> Any:
    """``jsonlite::parse_json(text)`` (``simplifyVector = FALSE``).

    Objects become ``dict`` (the first of duplicated keys wins, like R's
    ``x[["key"]]``), arrays ``list``, integers that fit in 32 bits ``int``,
    other numbers ``float``, ``null`` ``None``. Raises
    :class:`JSONParseError` with yajl's message on invalid input.
    """
    data = text.encode("utf-8")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    lexer = _Lexer(data)
    n = lexer.n
    # yajl parser states; "complete" once the top-level value is closed
    states = ["start"]
    stack: list[Any] = []  # containers being filled
    keys: list[str | None] = []
    root: list[Any] = []
    pos = 0

    def token_offset(tok: str, end: int) -> int:
        # A number is only terminated by the next char: one that ends at the
        # input's end is completed by the " " of yajl_complete_parse(), and its
        # offset is then relative to that chunk.
        if tok in (_T_INTEGER, _T_DOUBLE) and end >= n:
            return end - n
        return end

    def lexical(end: int) -> JSONParseError:
        off = end
        if lexer.error not in _CONSUMED_ERRORS and end >= n:
            off = end - n
        return JSONParseError(_render("lexical", _LEX_ERRORS[lexer.error], off, data))

    def parse_error(msg: str, off: int) -> JSONParseError:
        return JSONParseError(_render("parse", msg, off, data))

    def add(value: Any) -> None:
        if not stack:
            root.append(value)
            return
        top = stack[-1]
        if isinstance(top, dict):
            if keys[-1] not in top:
                top[keys[-1]] = value
        else:
            top.append(value)

    def value_done() -> None:
        state = states[-1]
        if state == "map_need_val":
            states[-1] = "map_got_val"
        elif state in ("array_need_val", "array_start"):
            states[-1] = "array_got_val"
        elif state == "start":
            states[-1] = "complete"

    def close() -> None:
        stack.pop()
        keys.pop()
        states.pop()
        if len(states) == 1 and states[0] == "start":
            states[0] = "complete"

    while True:
        state = states[-1]
        tok, end, tlen = lexer.lex(pos)
        if state == "complete":
            if tok == _T_EOF:
                return root[0]
            if tok == _T_ERROR:
                off = end
                if lexer.error not in _CONSUMED_ERRORS and end >= n:
                    off = end - n
                raise parse_error("trailing garbage", off)
            raise parse_error("trailing garbage", token_offset(tok, end))
        if tok == _T_EOF:
            # yajl_complete_parse(): input ran out before the value was complete
            raise parse_error("premature EOF", 1)
        if tok == _T_ERROR:
            raise lexical(end)
        pos = end
        if state in ("start", "map_need_val", "array_need_val", "array_start"):
            if tok in _VALUE_TOKENS:
                add(lexer.value)
                value_done()
            elif tok in (_T_LBRACE, _T_LBRACKET):
                new: Any = {} if tok == _T_LBRACE else []
                add(new)
                if state == "start":
                    states[-1] = "start"  # closed by close()
                else:
                    value_done()
                stack.append(new)
                keys.append(None)
                states.append("map_start" if tok == _T_LBRACE else "array_start")
            elif tok == _T_RBRACKET and state == "array_start":
                close()
            else:
                raise parse_error(
                    "unallowed token at this point in JSON text", token_offset(tok, end)
                )
        elif state in ("map_start", "map_need_key"):
            if tok == _T_STRING:
                keys[-1] = lexer.value
                states[-1] = "map_sep"
            elif tok == _T_RBRACE and state == "map_start":
                close()
            else:
                raise parse_error("invalid object key (must be a string)", token_offset(tok, end))
        elif state == "map_sep":
            if tok == _T_COLON:
                states[-1] = "map_need_val"
            else:
                raise parse_error(
                    "object key and value must be separated by a colon (':')",
                    token_offset(tok, end),
                )
        elif state == "map_got_val":
            if tok == _T_RBRACE:
                close()
            elif tok == _T_COMMA:
                states[-1] = "map_need_key"
            else:
                # yajl "tries to restore the error offset" in this state only
                off = token_offset(tok, end)
                off = off - tlen if off >= tlen else 0
                raise parse_error("after key and value, inside map, I expect ',' or '}'", off)
        elif state == "array_got_val":
            if tok == _T_RBRACKET:
                close()
            elif tok == _T_COMMA:
                states[-1] = "array_need_val"
            else:
                raise parse_error(
                    "after array element, I expect ',' or ']'", token_offset(tok, end)
                )


def _render(kind: str, msg: str, offset: int, data: bytes) -> str:
    """yajl_render_error_string(verbose = 1)."""
    spaces = 40 - offset if offset < 30 else 10
    start = offset - 30 if offset >= 30 else 0
    end = min(offset + 30, len(data))
    ctx = data[start:end].replace(b"\n", b" ").replace(b"\r", b" ")
    return f"{kind} error: {msg}\n" + " " * spaces + ctx.decode("utf-8", "replace") + "\n" + _ARROW
