"""Byte-level emulation of metacheck's ``code_read()`` reading pipeline.

``code_read()`` in R is::

    enc <- readr::guess_encoding(file_path)$encoding[[1]]
    lines <- tryCatch(readr::read_lines(file_path, locale = locale(encoding = enc)),
                      error = NULL, warning = NULL)
    if (is.null(lines)) lines <- readLines(file_path, warn = FALSE, skipNul = TRUE)
    lines <- iconv(lines, to = "UTF-8", sub = "byte")

Each stage is reproduced here on the raw bytes:

* :func:`read_lines_raw` -- readr's ``read_lines_raw()`` (BOM skipped, lines
  split on LF, CRLF or CR, a last line without terminator kept when non-empty);
* :func:`guess_encoding` -- ``readr::guess_encoding()``: pure ASCII (bytes
  1..127) short-cuts to ``"ASCII"``, anything else goes through ICU's charset
  detector (:mod:`._icu`), keeping guesses with confidence > 0.2;
* :func:`vroom_lines` -- ``readr::read_lines()`` (which is
  ``vroom::vroom_lines()``): the line-ending style is taken from the first line
  ending in the file, lines are split on that character only, one trailing CR
  is stripped per line, and an unterminated remainder is kept only when the file
  does not end in a line ending at all. Text is converted from the guessed
  encoding with glibc ``iconv`` semantics; a conversion failure, an embedded NUL
  or a ``\\x01`` byte (vroom's internal delimiter) makes it fail;
* :func:`read_lines_base` -- base ``readLines(warn = FALSE, skipNul = TRUE)``;
* :func:`iconv_sub_byte` -- ``iconv(x, to = "UTF-8", sub = "byte")``.

Upstream divergence: when the conversion fails inside vroom's worker threads, R
crashes with a segfault rather than raising an error (see the report); pytacheck
takes the fallback path that ``code_read()`` was written to take.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable

__all__ = [
    "code_read_bytes",
    "guess_encoding",
    "iconv_sub_byte",
    "read_lines_base",
    "read_lines_raw",
    "vroom_lines",
]


class _ReadFailed(Exception):
    """``readr::read_lines()`` raised an error or a warning."""


# ---------------------------------------------------------------------------
# readr::read_lines_raw() and guess_encoding()
# ---------------------------------------------------------------------------


def _skip_bom(data: bytes) -> int:
    """readr/vroom ``skipBom()``: offset past a UTF-32/16/8 byte order mark."""
    if data[:4] == b"\x00\x00\xfe\xff":
        return 4
    if data[:3] == b"\xef\xbb\xbf":
        return 3
    if data[:2] == b"\xfe\xff":
        return 2
    if data[:2] == b"\xff\xfe":
        return 4 if data[2:4] == b"\x00\x00" else 2
    return 0


_EOL = re.compile(rb"\r\n|\r|\n")


def read_lines_raw(data: bytes, n_max: int = -1) -> list[bytes]:
    """``readr::read_lines_raw()`` on the bytes of a file."""
    body = data[_skip_bom(data) :]
    lines = _EOL.split(body)
    if lines and lines[-1] == b"":
        lines.pop()  # a trailing terminator does not start a new line
    if n_max >= 0:
        lines = lines[:n_max]
    return lines


def guess_encoding(
    data: bytes, n_max: int = 10000, threshold: float = 0.2
) -> list[tuple[str, float]]:
    """``readr::guess_encoding()``: ``(encoding, confidence)`` pairs, best first.

    Raises ``ValueError`` when the file has no lines at all (R fails with
    "missing value where TRUE/FALSE needed").
    """
    from pytacheck.codecheck._icu import detect_all

    lines = read_lines_raw(data, n_max)
    if not lines:
        raise ValueError("missing value where TRUE/FALSE needed")
    joined = b"".join(lines)
    if all(0 < b < 0x80 for b in set(joined)):
        return [("ASCII", 1.0)]
    return [(name, conf / 100) for name, _lang, conf in detect_all(joined) if conf / 100 > threshold]


# ---------------------------------------------------------------------------
# glibc iconv decoding
# ---------------------------------------------------------------------------

# ICU name -> Python codec for the encodings where Python and glibc agree
_SIMPLE_CODECS = {
    "UTF-8": "utf-8",
    "ASCII": "ascii",
    "ISO-8859-1": "latin-1",
    "ISO-8859-2": "iso8859_2",
    "ISO-8859-5": "iso8859_5",
    "ISO-8859-6": "iso8859_6",
    "ISO-8859-7": "iso8859_7",
    "ISO-8859-8": "iso8859_8",
    "ISO-8859-9": "iso8859_9",
    "windows-1250": "cp1250",
    "windows-1251": "cp1251",
    "windows-1252": "cp1252",
    "windows-1253": "cp1253",
    "windows-1254": "cp1254",
    "windows-1256": "cp1256",
    "KOI8-R": "koi8_r",
    "ISO-2022-JP": "iso2022_jp",
    "ISO-2022-KR": "iso2022_kr",
}

# encodings glibc converts from but whose output is not ASCII-compatible:
# vroom re-encodes the whole file to UTF-8 before reading it
_WIDE_CODECS = {
    "UTF-16BE": "utf-16-be",
    "UTF-16LE": "utf-16-le",
    "UTF-32BE": "utf-32-be",
    "UTF-32LE": "utf-32-le",
}
_WIDE_UNIT = {"UTF-16BE": 2, "UTF-16LE": 2, "UTF-32BE": 4, "UTF-32LE": 4}

# glibc's CJK tables differ from Python's in a few places (checked code by
# code against R's iconv()): user-defined areas map to the Private Use Area,
# Shift_JIS 0x5C/0x7E are YEN SIGN/OVERLINE, and a handful of single codes.
_EUC_KR_EXTRA = {b"\xa2\xe8": "㉾", b"\xa4\xd4": "ㅤ"}
_BIG5_INVALID = frozenset(
    bytes.fromhex(h) for h in ("a15a", "a1c3", "a1c5", "a1fe", "a240", "a2cc", "a2ce")
)
_GB18030_EXTRA = {
    bytes.fromhex(k): chr(v)
    for k, v in {
        "a6d9": 65040,
        "a6da": 65042,
        "a6db": 65041,
        "a6dc": 65043,
        "a6dd": 65044,
        "a6de": 65045,
        "a6df": 65046,
        "a6ec": 65047,
        "a6ed": 65048,
        "a6f3": 65049,
        "a8bc": 7743,
        "fe51": 131207,
        "fe52": 131209,
        "fe53": 131276,
        "fe59": 40884,
        "fe61": 40885,
        "fe66": 40886,
        "fe67": 40887,
        "fe6c": 136663,
        "fe6d": 40888,
        "fe76": 141711,
        "fe7e": 40889,
        "fe90": 40890,
        "fe91": 147966,
        "fea0": 40891,
    }.items()
}

_CJK_TOKENS = {
    "Shift_JIS": re.compile(rb"[\x00-\x7f]+|[\x81-\x9f\xe0-\xfc][\x40-\x7e\x80-\xfc]|.", re.S),
    "EUC-JP": re.compile(
        rb"[\x00-\x7f]+|\x8e[\xa1-\xdf]|\x8f[\xa1-\xfe][\xa1-\xfe]|[\xa1-\xfe][\xa1-\xfe]|.", re.S
    ),
    "EUC-KR": re.compile(rb"[\x00-\x7f]+|[\xa1-\xfe][\xa1-\xfe]|.", re.S),
    "Big5": re.compile(rb"[\x00-\x7f]+|[\x81-\xfe][\x40-\x7e\xa1-\xfe]|.", re.S),
    "GB18030": re.compile(
        rb"[\x00-\x7f]+|[\x81-\xfe][\x30-\x39][\x81-\xfe][\x30-\x39]|[\x81-\xfe][\x40-\x7e\x80-\xfe]|.",
        re.S,
    ),
}
_PY_CJK = {
    "Shift_JIS": "shift_jis",
    "EUC-JP": "euc_jp",
    "EUC-KR": "euc_kr",
    "Big5": "big5",
    "GB18030": "gb18030",
}
_SJIS_ASCII = str.maketrans({"\\": "¥", "~": "‾"})


def _cjk_token(enc: str, tok: bytes) -> str:
    if enc == "Shift_JIS":
        if len(tok) == 1 and 0xA1 <= tok[0] <= 0xDF:
            return chr(0xFF61 + tok[0] - 0xA1)
        if len(tok) == 2 and 0xF0 <= tok[0] <= 0xF9 and tok[1] not in (0x7F, 0xFD, 0xFE):
            trail = tok[1]
            return chr(0xE000 + (tok[0] - 0xF0) * 188 + trail - 0x40 - (trail >= 0x80))
    elif enc == "EUC-JP":
        if len(tok) == 2 and tok[0] >= 0xF5:
            return chr(0xE000 + (tok[0] - 0xF5) * 94 + tok[1] - 0xA1)
    elif enc == "EUC-KR":
        if tok in _EUC_KR_EXTRA:
            return _EUC_KR_EXTRA[tok]
    elif enc == "Big5":
        if tok in _BIG5_INVALID:
            raise UnicodeDecodeError("big5", tok, 0, len(tok), "undefined in glibc")
    elif enc == "GB18030" and tok in _GB18030_EXTRA:
        return _GB18030_EXTRA[tok]
    return tok.decode(_PY_CJK[enc])


def _decode_cjk(data: bytes, enc: str) -> str:
    out: list[str] = []
    for m in _CJK_TOKENS[enc].finditer(data):
        tok = m.group()
        if tok[0] < 0x80:
            text = tok.decode("ascii")
            out.append(text.translate(_SJIS_ASCII) if enc == "Shift_JIS" else text)
        else:
            out.append(_cjk_token(enc, tok))
    return "".join(out)


def _decoder(enc: str) -> Callable[[bytes], str]:
    """A strict glibc-like decoder for an ICU charset name (``LookupError`` if none)."""
    if enc in _SIMPLE_CODECS:
        codec = _SIMPLE_CODECS[enc]
        return lambda b: b.decode(codec)
    if enc == "windows-1255":
        # glibc's CP1255 composes base letters with following points; only
        # the uncomposed mapping is reproduced (plus glibc's 0xCA)
        return lambda b: b.replace(b"\xca", b"\x00\xca").decode("cp1255", "strict").replace(
            "\x00�", "ֺ"
        )
    if enc in _PY_CJK:
        return lambda b: _decode_cjk(b, enc)
    raise LookupError(enc)


# ---------------------------------------------------------------------------
# vroom::vroom_lines()
# ---------------------------------------------------------------------------


def _reencode_wide(data: bytes, enc: str) -> bytes:
    """vroom ``reencode_file()``: whole-file conversion, trailing partial unit dropped."""
    unit = _WIDE_UNIT[enc]
    usable = len(data) - len(data) % unit
    try:
        return data[:usable].decode(_WIDE_CODECS[enc]).encode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ReadFailed(str(exc)) from exc


def vroom_lines(data: bytes, encoding: str) -> list[str]:
    """``readr::read_lines(file, locale = locale(encoding = encoding))``.

    Raises ``_ReadFailed`` where R raises an error or a warning.
    """
    if encoding in _WIDE_CODECS:
        data = _reencode_wide(data, encoding)
        encoding = "UTF-8"
    else:
        try:
            decode = _decoder(encoding)
        except LookupError as exc:  # iconv: unsupported conversion
            raise _ReadFailed(f"unsupported conversion from 'ASCII' to '{encoding}'") from exc
    n = len(data)
    if n == 0:
        return []
    ends_nl = data[-1] in b"\r\n"
    start = _skip_bom(data)
    if ends_nl and start >= n - 1:
        return []  # an empty file, or a file with only a newline
    # the newline type comes from the first line ending (strcspn stops at NUL)
    first = re.compile(rb"[\r\n\x00]").search(data, start)
    if first is not None and data[first.start()] == 0:
        return []  # vroom cannot find the first line: zero rows
    if b"\x00" in data or b"\x01" in data:
        raise _ReadFailed("One or more parsing issues")
    nl = b"\n"
    if first is not None and data[first.start()] == 13:
        nxt = data[first.start() + 1 : first.start() + 2]
        nl = b"\n" if nxt == b"\n" else b"\r"
    rows = data[start:].split(nl)
    last = rows.pop()
    if not ends_nl:
        # no final line ending: vroom reads through a connection and keeps
        # the unterminated remainder as the last line (with a line ending,
        # anything after the last newline of the detected type is dropped)
        rows.append(last)
    rows = [r[:-1] if r.endswith(b"\r") else r for r in rows]
    if encoding == "UTF-8":
        return [r.decode("utf-8", "surrogateescape") for r in rows]
    try:
        return [decode(r) for r in rows]
    except (UnicodeDecodeError, UnicodeError) as exc:
        raise _ReadFailed(str(exc)) from exc


# ---------------------------------------------------------------------------
# base::readLines() and iconv(sub = "byte")
# ---------------------------------------------------------------------------


def read_lines_base(data: bytes) -> list[str]:
    """``readLines(con, warn = FALSE, skipNul = TRUE)`` (bytes kept as they are)."""
    data = data.replace(b"\x00", b"")
    lines = _EOL.split(data)
    if lines and lines[-1] == b"":
        lines.pop()
    return [line.decode("utf-8", "surrogateescape") for line in lines]


def _sub_byte(exc: UnicodeError) -> tuple[str, int]:
    assert isinstance(exc, UnicodeDecodeError)
    bad = exc.object[exc.start : exc.end]
    return "".join(f"<{b:02x}>" for b in bad), exc.end


codecs.register_error("pytacheck_sub_byte", _sub_byte)


def iconv_sub_byte(x: str) -> str:
    """``iconv(x, to = "UTF-8", sub = "byte")`` on text holding raw bytes.

    Strings read without conversion carry undecodable bytes as surrogate
    escapes; each such byte becomes ``"<xx>"``.
    """
    if not any("\udc80" <= ch <= "\udcff" for ch in x):
        return x
    raw = x.encode("utf-8", "surrogateescape")
    return raw.decode("utf-8", "pytacheck_sub_byte")


# ---------------------------------------------------------------------------
# code_read()
# ---------------------------------------------------------------------------


def code_read_bytes(data: bytes) -> list[str]:
    """The lines metacheck's ``code_read()`` returns for a file with these bytes."""
    guesses = guess_encoding(data)
    if not guesses:
        raise IndexError("subscript out of bounds")
    try:
        lines = vroom_lines(data, guesses[0][0])
    except _ReadFailed:
        lines = read_lines_base(data)
    return [iconv_sub_byte(line) for line in lines]
