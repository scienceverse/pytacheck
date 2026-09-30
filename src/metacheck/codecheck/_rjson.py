"""``jsonlite::fromJSON(txt, simplifyVector = FALSE)`` and R coercions of its values.

jsonlite parses with yajl, which differs from :func:`json.loads` in ways that
decide whether a notebook or an ``renv.lock`` can be read at all, and what
its strings contain (measured with jsonlite 2.0.0):

* ``/* ... */`` and ``// ...`` comments are allowed between tokens (an
  unterminated ``/*`` runs to the end of the text), and ``\\v``/``\\f``
  count as whitespace;
* a byte-order mark at the very start is dropped (with a warning);
* integers outside R's integer range (``-2147483647 .. 2147483647``) become
  doubles;
* ``\\u0000`` ends a string (R strings cannot hold NUL), a high surrogate
  not followed by a ``\\u`` escape becomes ``"?"``, and one followed by any
  ``\\u`` escape is combined with it without checking that it is a low
  surrogate.

Objects are returned as :class:`RList` (a named list: duplicate keys are
kept, ``$`` finds the first), arrays as lists, ``null`` as ``None``.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["RList", "json_load", "r_as_character", "r_unlist_chr"]

_INT_MAX = 2147483647


class RList(list):  # type: ignore[type-arg]
    """A JSON object as jsonlite gives it: a named list (duplicate names kept)."""

    def names(self) -> list[str]:
        return [k for k, _ in self]


# strings (kept, except for their \u escapes), comments and yajl-only whitespace
_TOKENS = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|/\*.*?(?:\*/|\Z)|//[^\n]*|[\v\f]', re.S)
_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|.)", re.S)


def _yajl_escapes(body: str) -> tuple[str, bool]:
    """Rewrite a string token's ``\\u`` escapes the way yajl decodes them.

    Returns the body (still JSON-escaped) and whether it holds a NUL.
    """
    out: list[str] = []
    nul = False
    pos = 0
    skip_to = -1
    for m in _ESCAPE.finditer(body):
        if m.start() < skip_to:
            continue
        hexa = m.group(1)
        if hexa is None:
            continue
        cp = int(hexa, 16)
        out.append(body[pos : m.start()])
        end = m.end()
        if cp & 0xFC00 == 0xD800:
            nxt = re.match(r"\\u([0-9a-fA-F]{4})", body[end:])
            if nxt is not None:
                low = int(nxt.group(1), 16)
                cp = ((cp & 0x3F) << 10) | ((((cp >> 6) & 0xF) + 1) << 16) | (low & 0x3FF)
                end += nxt.end()
                cp -= 0x10000
                out.append(f"\\u{0xD800 + (cp >> 10):04x}\\u{0xDC00 + (cp & 0x3FF):04x}")
            else:
                out.append("?")
        else:
            if cp == 0:
                nul = True
            out.append(m.group(0))
        pos = skip_to = end
    out.append(body[pos:])
    return "".join(out), nul


def _cut_nul(x: Any) -> Any:
    if isinstance(x, str):
        return x.split("\x00", 1)[0]
    if isinstance(x, RList):
        return RList((_cut_nul(k), _cut_nul(v)) for k, v in x)
    if isinstance(x, list):
        return [_cut_nul(v) for v in x]
    return x


def _parse_int(s: str) -> int | float:
    v = int(s)
    return v if -_INT_MAX <= v <= _INT_MAX else float(s)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"lexical error: invalid char in json text ({name})")


def json_load(text: str) -> Any:
    """``tryCatch(jsonlite::fromJSON(text, simplifyVector = FALSE), error = NULL)``.

    ``None`` is returned both for JSON ``null`` and for text that jsonlite
    cannot parse.
    """
    if text.startswith("﻿"):
        text = text[1:]
    if _SPECIAL_ESCAPE.search(text) is None:
        # fast path: plain JSON (no comments, no surrogate/NUL escapes)
        try:
            return _loads(text)
        except (ValueError, RecursionError):
            pass
    has_nul = False

    def token(m: re.Match[str]) -> str:
        nonlocal has_nul
        tok = m.group(0)
        if tok[0] == '"':
            if "\\u" not in tok:
                return tok
            body, nul = _yajl_escapes(tok[1:-1])
            has_nul = has_nul or nul
            return f'"{body}"'
        return " "

    try:
        value = _loads(_TOKENS.sub(token, text))
    except (ValueError, RecursionError):
        return None
    return _cut_nul(value) if has_nul else value


_SPECIAL_ESCAPE = re.compile(r"\\u(?:[dD][89abAB]|0000)")


def _loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=RList,
        parse_int=_parse_int,
        parse_constant=_reject_constant,
    )


# ---------------------------------------------------------------------------
# R coercions of parsed values
# ---------------------------------------------------------------------------


def _deparse(x: Any) -> str:
    """``deparse()`` of a JSON value (as ``as.character()`` shows a list element)."""
    from metacheck._r.base import as_character

    if x is None:
        return "NULL"
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return f"{x}L"
    if isinstance(x, float):
        return str(as_character(x))
    if isinstance(x, str):
        return json.dumps(x, ensure_ascii=False)
    if isinstance(x, RList):
        inner = ", ".join(f"{k} = {_deparse(v)}" if k else _deparse(v) for k, v in x)
        return f"list({inner})"
    if isinstance(x, list | tuple):
        return f"list({', '.join(_deparse(v) for v in x)})"
    return str(x)


def _scalar_chr(x: Any) -> str:
    from metacheck._r.base import as_character

    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, str):
        return x
    return str(as_character(x))


def r_as_character(x: Any) -> list[str | None]:
    """``as.character(x)`` of a parsed JSON/YAML value.

    A scalar gives one string; a list gives one string per element (a scalar
    element as itself, anything else deparsed, as R does); ``NULL`` gives
    none.
    """
    if x is None:
        return []
    if isinstance(x, RList):
        return [_scalar_chr(v) if _is_scalar(v) else _deparse(v) for _, v in x]
    if isinstance(x, dict):
        return [_scalar_chr(v) if _is_scalar(v) else _deparse(v) for v in x.values()]
    if isinstance(x, tuple):  # an R atomic vector (YAML)
        return [_scalar_chr(v) for v in x]
    if isinstance(x, list):
        return [_scalar_chr(v) if _is_scalar(v) else _deparse(v) for v in x]
    return [_scalar_chr(x)]


def _is_scalar(x: Any) -> bool:
    return isinstance(x, str | bool | int | float)


def r_unlist_chr(x: Any) -> list[str]:
    """``unlist(x)`` of a parsed JSON value, shown as character.

    The leaves (``NULL`` dropped) are coerced to R's common type first, so a
    ``true`` next to a number is ``1`` and next to a string ``"TRUE"``.
    """
    from metacheck._r.base import as_character

    leaves: list[Any] = []

    def walk(v: Any) -> None:
        if isinstance(v, RList):
            for _, e in v:
                walk(e)
        elif isinstance(v, list | tuple):
            for e in v:
                walk(e)
        elif v is not None:
            leaves.append(v)

    walk(x)
    if any(isinstance(v, str) for v in leaves):
        return [_scalar_chr(v) for v in leaves]
    if any(isinstance(v, float) for v in leaves):
        return [str(as_character(float(v))) for v in leaves]
    if any(isinstance(v, int) and not isinstance(v, bool) for v in leaves):
        return [str(int(v)) for v in leaves]
    return ["TRUE" if v else "FALSE" for v in leaves]
