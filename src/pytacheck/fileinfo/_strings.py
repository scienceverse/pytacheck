"""R's handling of strings that are not valid UTF-8.

File names read as bytes -- zip members without the UTF-8 flag (CP437 names
from older Windows tools), files extracted from such archives -- are kept as
Python strings with the undecodable bytes as lone surrogates
(``errors="surrogateescape"``), the way R keeps them as invalid UTF-8
strings. R's string functions treat those specially, and the ports reproduce
that where metacheck feeds them such names: ``grepl()`` is ``FALSE`` (with a
warning), ``strsplit()`` gives ``NA``, and ``sub()``/``gsub()``/``regexec()``
raise ``input string 1 is invalid``.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["as_bytes_text", "invalid_utf8", "raise_if_invalid"]


def invalid_utf8(x: object) -> bool:
    """Is *x* a string holding bytes that are not valid UTF-8 (lone surrogates)?"""
    if not isinstance(x, str) or x.isascii():
        return False
    try:
        x.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def raise_if_invalid(values: Iterable[object], msg: str = "input string {i} is invalid") -> None:
    """Raise ``ValueError`` as R's ``sub()``/``gsub()`` do for the first invalid string."""
    for i, v in enumerate(values, start=1):
        if invalid_utf8(v):
            raise ValueError(msg.format(i=i))


def as_bytes_text(x: str) -> str:
    """*x* with every undecodable byte shown as ``<xx>`` (R's ``iconv(sub = "byte")``)."""
    if not invalid_utf8(x):
        return x
    return "".join(f"<{ord(c) - 0xDC00:02x}>" if 0xDC80 <= ord(c) <= 0xDCFF else c for c in x)
