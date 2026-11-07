"""Decoding a code file's bytes into lines of text.

metacheck's ``code_read()`` guesses the encoding with ``readr::guess_encoding()``
(ICU's charset detector), reads the lines with vroom and falls back to base
``readLines()``. pytacheck does not reproduce that pipeline (D74); it decodes
the way a Python reader would:

* a byte order mark picks UTF-8, UTF-16 or UTF-32;
* text that is valid UTF-8 (ASCII included) is read as UTF-8;
* anything else is read in the encoding chardet names when it is fairly sure
  (confidence 0.2, readr's threshold) and the bytes decode in it, else as
  Windows-1252, the usual encoding of a non-UTF-8 script in Western Europe;
  bytes that fit neither are read as UTF-8 with each invalid byte shown as
  ``<xx>``, as ``iconv(sub = "byte")`` shows it in R.

Lines end at LF, CRLF or CR; a final line terminator does not start an empty
line (so a file holding one line end has one empty line, as in R's
``strsplit()``), and NUL characters are dropped. A compressed file is not text:
``code_read()`` answers it with no lines (metacheck #568), so nothing is
decompressed here.
"""

from __future__ import annotations

import codecs
import re

__all__ = ["decode_lines"]

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
    text = _decode(data).replace("\x00", "")
    lines = _EOL.split(text)
    if lines[-1] == "":
        lines.pop()
    return lines
