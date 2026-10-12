"""Decoding a code file's bytes into lines of text.

metacheck's ``code_read()`` guesses the encoding with ``readr::guess_encoding()``
(ICU's charset detector), reads the lines with vroom and falls back to base
``readLines()``. pytacheck does not reproduce that pipeline (D74); it decodes
the way a Python reader would:

* a gzip, bzip2 or xz file is decompressed, and a zip archive gives its first
  member, as R's connections do;
* a byte order mark picks UTF-8, UTF-16 or UTF-32;
* text that is valid UTF-8 (ASCII included) is read as UTF-8;
* anything else is read in the encoding chardet names when it is fairly sure
  (confidence 0.2, readr's threshold) and the bytes decode in it, else as
  Windows-1252, the usual encoding of a non-UTF-8 script in Western Europe;
  bytes that fit neither are read as UTF-8 with each invalid byte shown as
  ``<xx>``, as ``iconv(sub = "byte")`` shows it in R.

Lines end at LF, CRLF or CR; a final line terminator does not start an empty
line (so a file holding one line end has no lines, as in R), and NUL
characters are dropped.
"""

from __future__ import annotations

import bz2
import codecs
import io
import lzma
import re
import zipfile
import zlib

__all__ = ["decode_lines", "decompress"]

#: chardet's confidence below which its guess gives way to Windows-1252
_MIN_CONFIDENCE = 0.2

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

_EOL = re.compile(r"\r\n|\r|\n")


def _gunzip(data: bytes) -> bytes:
    """Every member of a gzip stream in turn; trailing bytes are ignored."""
    out = []
    while data[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(31)
        try:
            out.append(d.decompress(data))
        except zlib.error:
            break
        data = d.unused_data
    return b"".join(out)


def decompress(data: bytes) -> bytes:
    """*data* decompressed when its magic bytes say gzip, bzip2, xz or zip.

    A zip archive gives its first member. Anything else, and a compressed
    stream that cannot be read, is returned as it is.
    """
    try:
        if data[:2] == b"\x1f\x8b":
            return _gunzip(data)
        if data[:3] == b"BZh":
            return bz2.decompress(data)
        if data[:6] == b"\xfd7zXZ\x00":
            return lzma.decompress(data)
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                return zf.read(names[0]) if names else b""
    except (OSError, EOFError, ValueError, lzma.LZMAError, zipfile.BadZipFile):
        return data
    return data


def _sub_byte(exc: UnicodeError) -> tuple[str, int]:
    assert isinstance(exc, UnicodeDecodeError)
    bad = exc.object[exc.start : exc.end]
    return "".join(f"<{b:02x}>" for b in bad), exc.end


codecs.register_error("pytacheck_sub_byte", _sub_byte)


def _guess(data: bytes) -> str | None:
    """chardet's encoding for *data* when it is fairly sure, else Windows-1252,
    whichever the bytes decode in."""
    try:
        import chardet
    except ImportError:  # pragma: no cover - a core dependency
        name = None
    else:
        found = chardet.detect(data)
        sure = (found.get("confidence") or 0.0) >= _MIN_CONFIDENCE
        name = found.get("encoding") if sure else None
    for codec in (name, "cp1252"):
        if not codec:
            continue
        try:
            data.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
        return str(codec)
    return None


def _decode(data: bytes) -> str:
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return data[len(bom) :].decode(codec, "pytacheck_sub_byte")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    guessed = _guess(data)
    if guessed is not None:
        return data.decode(guessed)
    return data.decode("utf-8", "pytacheck_sub_byte")


def decode_lines(data: bytes) -> list[str]:
    """The lines of text in the bytes of a code file (see the module docstring)."""
    text = _decode(decompress(data)).replace("\x00", "")
    lines = _EOL.split(text)
    if lines[-1] == "":
        lines.pop()
    return [] if lines == [""] else lines
