"""JSON parsing shared by every part of pytacheck.

:func:`loads` is the one JSON parser: API bodies, LLM replies, notebooks,
lock files and metadata all go through it instead of private copies. It
parses with orjson and falls back to the standard library for the few valid
documents orjson refuses, so the result never depends on which parser read
it:

* a UTF-8 byte-order mark before the document is ignored;
* a lone or mismatched surrogate escape (``"\\ud800"``) becomes U+FFFD, so
  the text can always be written to a file, a table or a report;
* integers beyond 64 bits are read as floats, whichever orjson version is
  installed (older versions refuse them);
* a number beyond the double range is read as an infinity.

Everything else follows RFC 8259: no comments, ``NaN`` or ``Infinity``,
bytes must be UTF-8, and a repeated key keeps its last value.
"""

from __future__ import annotations

import json
import re
from typing import Any

import orjson

__all__ = ["JSONDecodeError", "loads"]

#: raised for invalid JSON; a ``ValueError``
JSONDecodeError = json.JSONDecodeError

_BOM = "\ufeff"
_BOM_UTF8 = _BOM.encode()
_SURROGATE = re.compile(r"[\ud800-\udfff]")
# a surrogate character, or a \uD800-\uDFFF escape that may decode to one
_MAY_HAVE_SURROGATE = re.compile(r"[\ud800-\udfff]|\\u[dD][89a-fA-F]")
# orjson reads integers in [-2**63, 2**64 - 1] exactly
_INT_MIN = -(2**63)
_INT_MAX = 2**64 - 1


def _parse_int(text: str) -> int | float:
    # 21 or more characters are always outside the 64-bit range
    if len(text) <= 20:
        value = int(text)
        if _INT_MIN <= value <= _INT_MAX:
            return value
    return float(text)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


_DECODER = json.JSONDecoder(parse_int=_parse_int, parse_constant=_reject_constant)


def loads(data: str | bytes | bytearray | memoryview) -> Any:
    """Parse a JSON document into dicts, lists, strings, numbers, booleans and ``None``.

    Raises :class:`JSONDecodeError` (a ``ValueError``) when *data* is not
    valid JSON, and ``TypeError`` when it is not text or bytes.
    """
    if isinstance(data, str):
        data = data.removeprefix(_BOM)
    elif isinstance(data, bytes | bytearray | memoryview):
        data = bytes(data).removeprefix(_BOM_UTF8)
    else:  # bytes(5) would be five NUL bytes, bytes([1]) one byte
        raise TypeError(f"JSON must be str or bytes, not {type(data).__name__}")
    try:
        return orjson.loads(data)
    except orjson.JSONDecodeError as exc:
        error = exc
    try:
        text = data if isinstance(data, str) else data.decode("utf-8")
        value = _DECODER.decode(text)
    except (ValueError, RecursionError):
        raise error from None
    return _replace_surrogates(value) if _MAY_HAVE_SURROGATE.search(text) else value


def _clean(text: str) -> str:
    """*text* with each lone surrogate replaced by U+FFFD (pairs are combined)."""
    if _SURROGATE.search(text) is None:
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _replace_surrogates(value: Any) -> Any:
    """Apply :func:`_clean` to every string, key included, of a parsed document."""
    if isinstance(value, str):
        return _clean(value)
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, str):
                    node[i] = _clean(item)
                elif isinstance(item, list | dict):
                    stack.append(item)
        elif isinstance(node, dict):
            items = list(node.items())
            node.clear()
            for key, item in items:
                if isinstance(item, str):
                    item = _clean(item)
                elif isinstance(item, list | dict):
                    stack.append(item)
                node[_clean(key)] = item
    return value
