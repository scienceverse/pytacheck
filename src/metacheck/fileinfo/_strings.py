"""Strings that are not valid UTF-8.

File names read as bytes that are not valid UTF-8 (a local file name in
another encoding) are kept as Python strings with the undecodable bytes as
lone surrogates (``errors="surrogateescape"``), the way R keeps them as
invalid UTF-8 strings. Zip member names are decoded instead (UTF-8, else
CP437: ``metacheck.archives.zip_peek._decode_zip_name``).
"""

from __future__ import annotations

__all__ = ["as_bytes_text", "invalid_utf8"]


def invalid_utf8(x: object) -> bool:
    """Is *x* a string holding bytes that are not valid UTF-8 (lone surrogates)?"""
    if not isinstance(x, str) or x.isascii():
        return False
    try:
        x.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def as_bytes_text(x: str) -> str:
    """*x* with every undecodable byte shown as ``<xx>`` (R's ``iconv(sub = "byte")``)."""
    if not invalid_utf8(x):
        return x
    return "".join(f"<{ord(c) - 0xDC00:02x}>" if 0xDC80 <= ord(c) <= 0xDCFF else c for c in x)
