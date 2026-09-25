"""Byte-level emulation of metacheck's ``code_read()`` reading pipeline.

``code_read()`` in R is::

    enc <- readr::guess_encoding(file_path)$encoding[[1]]
    lines <- tryCatch(readr::read_lines(file_path, locale = locale(encoding = enc)),
                      error = NULL, warning = NULL)
    if (is.null(lines)) lines <- readLines(file_path, warn = FALSE, skipNul = TRUE)
    lines <- iconv(lines, to = "UTF-8", sub = "byte")

Each stage is reproduced here on the raw bytes of the file:

* :func:`read_lines_raw` -- readr's ``read_lines_raw()`` (``TokenizerLine``:
  byte order mark skipped, lines split on LF, CRLF or CR, a last line without
  terminator kept when non-empty);
* :func:`guess_encoding` -- ``readr::guess_encoding()``: pure ASCII (bytes
  1..127) short-cuts to ``"ASCII"``, anything else goes through ICU's charset
  detector (:mod:`._icu`), keeping guesses with confidence > 0.2 (an input ICU
  cannot place at all gives stringi's single ``NA`` row);
* :func:`vroom_lines` -- ``readr::read_lines()``, i.e. ``vroom::vroom_lines()``
  (vroom 1.7.1): a faithful emulation of vroom's indexer (``delimited_index``
  for memory-mapped files that end in ``"\\n"``, ``delimited_index_connection``
  -- 128 KiB chunks -- for every other file and for decompressed input), of
  its cell extraction (one trailing CR dropped, strings cut at an embedded NUL
  with the "embedded null" parse problem when the cut string is shorter than
  the raw cell) and of the glibc ``iconv`` conversion from the guessed
  encoding. Everything R reports as an error or a warning (unknown encoding,
  conversion failure, parse problems) raises :class:`ReadFailed`, which is
  what ``code_read()``'s ``tryCatch()`` turns into the fallback;
* :func:`read_lines_base` -- base ``readLines(warn = FALSE, skipNul = TRUE)``;
* :func:`iconv_sub_byte` -- ``iconv(x, to = "UTF-8", sub = "byte")``.

Known limits (documented in ``porting/map/codecheck.toml``): vroom indexes
large files on several threads, each of which re-detects the newline type at
its own chunk boundary; files that mix CR-only line endings with LF/CRLF can
split differently there than in this single-threaded emulation. R crashes
(segfaults) on a few malformed inputs when a conversion error is raised inside
vroom; pytacheck takes the fallback path ``code_read()`` was written to take.
``ISO-2022-CN`` (glibc can decode it, Python cannot) also takes the fallback.
"""

from __future__ import annotations

import bz2
import codecs
import io
import lzma
import re
import zipfile
import zlib
from collections.abc import Callable

__all__ = [
    "ReadFailed",
    "code_read_bytes",
    "decompress",
    "detect_compression",
    "guess_encoding",
    "iconv_sub_byte",
    "read_lines_base",
    "read_lines_raw",
    "vroom_lines",
]


class ReadFailed(Exception):
    """``readr::read_lines()`` raised an error or a warning."""


# ---------------------------------------------------------------------------
# compression (vroom/readr detect_compression(), R connections)
# ---------------------------------------------------------------------------


def detect_compression(data: bytes) -> str | None:
    """``detect_compression()`` (identical in readr and vroom): magic bytes."""
    if data[:2] == b"\x1f\x8b":
        return "gz"
    if data[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if data[:3] == b"BZh":
        return "bz2"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    return None


def decompress(data: bytes, kind: str) -> bytes:
    """What R's ``gzfile()``/``bzfile()``/``xzfile()``/``unz()`` connections read.

    ``gzfile()`` and ``xzfile()`` read an uncompressed file as it is; a zip
    archive yields its first member (vroom/readr ``zipfile()``). Raises
    :class:`ReadFailed` where R's connection fails.
    """
    try:
        if kind == "gz":
            if data[:2] != b"\x1f\x8b":
                return data
            return _gunzip(data)
        if kind == "xz":
            if data[:6] != b"\xfd7zXZ\x00":
                return data
            return lzma.decompress(data)
        if kind == "bz2":
            return bz2.decompress(data)
        if kind == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if not names:
                    raise ReadFailed("subscript out of bounds")
                return zf.read(names[0])
    except (OSError, EOFError, ValueError, lzma.LZMAError, zipfile.BadZipFile) as exc:
        raise ReadFailed(str(exc)) from exc
    return data


def _gunzip(data: bytes) -> bytes:
    """R's gzfile: concatenated members are read in turn, trailing junk ignored."""
    out = []
    while data[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(31)
        try:
            out.append(d.decompress(data))
        except zlib.error:
            break
        data = d.unused_data
    return b"".join(out)


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


_NON_ASCII = re.compile(rb"[\x00\x80-\xff]")


def guess_encoding(
    data: bytes, n_max: int = 10000, threshold: float = 0.2
) -> list[tuple[str | None, float | None]]:
    """``readr::guess_encoding()``: ``(encoding, confidence)`` pairs, best first.

    Raises ``ValueError`` when the file has no lines at all (R fails with
    "missing value where TRUE/FALSE needed"). When ICU recognises nothing,
    stringi returns one ``NA`` row, which the confidence filter keeps.
    """
    from pytacheck.codecheck._icu import detect_all

    lines = read_lines_raw(data, n_max)
    if not lines:
        raise ValueError("missing value where TRUE/FALSE needed")
    joined = b"".join(lines)
    if _NON_ASCII.search(joined) is None:
        return [("ASCII", 1.0)]
    matches = detect_all(joined)
    if not matches:
        return [(None, None)]
    return [(name, conf / 100) for name, _lang, conf in matches if conf / 100 > threshold]


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

# encodings readr's locale() does not know ("Unknown encoding" warning)
_UNKNOWN_ENCODINGS = frozenset(
    {"ISO-8859-8-I", "IBM424_rtl", "IBM424_ltr", "IBM420_rtl", "IBM420_ltr"}
)

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
        return lambda b: (
            b.replace(b"\xca", b"\x00\xca").decode("cp1255", "strict").replace("\x00�", "ֺ")
        )
    if enc in _PY_CJK:
        return lambda b: _decode_cjk(b, enc)
    raise LookupError(enc)


def _reencode_wide(data: bytes, enc: str) -> bytes:
    """vroom ``convert_connection()`` to UTF-8: an incomplete trailing unit is dropped."""
    unit = _WIDE_UNIT[enc]
    usable = len(data) - len(data) % unit
    body = data[:usable]
    if unit == 2 and usable >= 2:
        # a high surrogate with nothing after it is an incomplete sequence too
        last = body[-2:]
        code = int.from_bytes(last, "big" if enc == "UTF-16BE" else "little")
        if 0xD800 <= code <= 0xDBFF:
            body = body[:-2]
    try:
        return body.decode(_WIDE_CODECS[enc]).encode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadFailed("iconv failed") from exc


# ---------------------------------------------------------------------------
# vroom's delimited indexer (delim "\1", no quote, no comment, no escapes)
# ---------------------------------------------------------------------------

_CHUNK = (1 << 17) - 1  # VROOM_CONNECTION_SIZE - 1 bytes per R_ReadConnection()
_LF, _CR = 0x0A, 0x0D
_ARCHIVE_EXTS = frozenset(
    {
        "7z",
        "cpio",
        "iso",
        "mtree",
        "tar",
        "tgz",
        "taz",
        "tar.gz",
        "tbz",
        "tbz2",
        "tz2",
        "tar.bz2",
        "tlz",
        "tar.lzma",
        "txz",
        "tar.xz",
        "tzo",
        "taZ",
        "tZ",
        "tar.zst",
        "warc",
        "jar",
        "Z",
        "zst",
    }
)


def _find_next_newline(buf: bytes, start: int) -> tuple[int, str]:
    """``find_next_newline()`` (type NA, no quotes): strcspn for CR, LF or NUL."""
    n = len(buf)
    if start >= n:
        return n - 1, "NA"
    pos = start
    while pos < n and buf[pos] not in (_LF, _CR, 0):
        pos += 1
    if pos >= n:  # ran into the zero padding after the mapped file
        return pos, "NA"
    c = buf[pos]
    if c == _LF:
        return pos, "LF"
    if c == _CR:
        if pos + 1 < n and buf[pos + 1] == _LF:
            return pos + 1, "CRLF"
        return pos, "CR"
    return pos, "NA"


class _Indexer:
    """``index_region()`` state carried across calls (and connection chunks)."""

    __slots__ = ("errors", "num_delims", "record_start")

    def __init__(self) -> None:
        self.record_start = True
        self.num_delims = 0
        self.errors = False

    def region(
        self,
        buf: bytes,
        dest: list[int],
        newline: int,
        start: int,
        end: int,
        offset: int,
        num_cols: int,
    ) -> int:
        n = len(buf)
        stops = re.compile(b"[\\x01\\\\\\x00" + (b"\\r" if newline == _CR else b"\\n") + b"]")
        pos = start
        lines = 0
        while pos < end:
            c = buf[pos] if pos < n else 0
            if self.record_start:
                dest.append(pos + offset)
            if c == 0x01:
                self.record_start = False
                dest.append(pos + offset)
                self.num_delims += 1
            elif c == newline:
                if num_cols > 0 and pos > start:
                    self._resolve(pos + offset, num_cols, dest)
                self.record_start = True
                self.num_delims = 0
                dest.append(pos + offset)
                lines += 1
            else:
                self.record_start = False
                pos += 1
                if pos < end:
                    m = stops.search(buf, pos)
                    pos = m.start() if m is not None else n
                continue
            pos += 1
        return lines

    def _resolve(self, pos: int, num_cols: int, dest: list[int]) -> None:
        if self.num_delims != num_cols - 1:
            self.errors = True
        while self.num_delims > 0 and self.num_delims >= num_cols:
            dest.pop()
            self.num_delims -= 1
        while self.num_delims < num_cols - 1:
            dest.append(pos)
            self.num_delims += 1


def _index_mmap(data: bytes) -> tuple[list[list[int]], int, bool]:
    """``delimited_index`` on a memory-mapped file (which ends in ``"\\n"``)."""
    size = len(data)
    start = _skip_bom(data)
    if start >= size - 1:  # an empty file, or a file with only a newline
        return [], 0, False
    first_nl, nl = _find_next_newline(data, start)
    newline = _CR if nl == "CR" else _LF
    ix = _Indexer()
    idx0: list[int] = []
    ix.region(data, idx0, newline, start, first_nl + 1, 0, 0)
    columns = len(idx0) - 1 if idx0 else 0
    idx1: list[int] = []
    ix.region(data, idx1, newline, first_nl + 1, size, 0, columns)
    return [idx0, idx1], columns, ix.errors


def _index_connection(data: bytes) -> tuple[list[list[int]], int, bool]:
    """``delimited_index_connection``: the input read in 128 KiB chunks."""
    chunks = [data[i : i + _CHUNK] for i in range(0, len(data), _CHUNK)] or [b""]
    first = chunks[0]
    sz = len(first)
    if sz == 0:
        return [], 0, False
    buf = first + b"\x00"
    start = _skip_bom(buf)
    first_nl, nl = _find_next_newline(buf, start)
    single_line = first_nl == len(buf) - 1
    ending = buf[first_nl] if first_nl < len(buf) else 0
    expected = ending == _LF or (nl == "CR" and ending == _CR)
    if sz > 1 and not expected and len(chunks) > 1:
        raise ReadFailed("The size of the connection buffer (131072) was not large enough")
    newline = _CR if nl == "CR" else _LF
    ix = _Indexer()
    idx0: list[int] = []
    ix.region(buf, idx0, newline, start, first_nl + 1, 0, 0)
    columns = len(idx0) - 1 if idx0 else 0
    idx1: list[int] = []
    total = 0
    region_start = first_nl + 1
    for chunk in chunks:
        cbuf = chunk + b"\x00"
        ix.region(cbuf, idx1, newline, region_start, len(chunk), total, columns)
        total += len(chunk)
        region_start = 0
    last = data[-1]
    if not (last == _LF or (nl == "CR" and last == _CR)):
        if columns == 0 or single_line:
            idx0.append(len(data))
            columns += 1
        else:
            idx1.append(len(data))
    return [idx0, idx1], columns, ix.errors


def _cells(idx: list[list[int]], columns: int) -> list[list[tuple[int, int]]]:
    """``get_cell()`` for every row and column: ``[(begin, end), ...]`` per column."""
    if columns <= 0:
        return []
    total = sum(len(v) for v in idx)
    rows = total // (columns + 1)
    out: list[list[tuple[int, int]]] = [[] for _ in range(columns)]
    for row in range(rows):
        for col in range(columns):
            i = row * (columns + 1) + col
            for v in idx:
                if i + 1 < len(v):
                    begin, end = v[i], v[i + 1]
                    if begin != end and col > 0:
                        begin += 1  # skip the delimiter
                    out[col].append((begin, end))
                    break
                i -= len(v)
            else:  # pragma: no cover - vroom throws std::out_of_range here
                raise ReadFailed("Failure to retrieve index")
    return out


def _archive_ext(name: str) -> bool:
    """vroom ``connection_or_filepath()``: an extension that needs the archive package."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base:
        return False
    ext = base.split(".", 1)[1]
    while True:
        if ext in _ARCHIVE_EXTS:
            return True
        if "." not in ext:
            return False
        ext = ext.split(".", 1)[1]


def _file_ext(name: str) -> str:
    """``tools::file_ext()``: the alphanumeric extension, case kept."""
    m = re.search(r"\.([A-Za-z0-9]+)$", name)
    return m.group(1) if m else ""


def vroom_lines(data: bytes, encoding: str | None, name: str = "", url: bool = False) -> list[str]:
    """``readr::read_lines(file, locale = locale(encoding = encoding))``.

    *data* are the bytes of the file called *name* (its extension matters to
    vroom); *url* marks a downloaded URL. Raises :class:`ReadFailed` where R
    raises an error or a warning.
    """
    if encoding is None:
        raise ReadFailed("`encoding` must be a string.")
    if encoding in _UNKNOWN_ENCODINGS:
        raise ReadFailed(f'Unknown encoding "{encoding}".')
    decode: Callable[[bytes], str] | None = None
    if encoding in _WIDE_CODECS:
        # reencode_file(): the whole file through iconv into a temporary file,
        # read from base R's file() connection (which decompresses gzip/bzip2/
        # xz by their magic bytes), or for a compressed URL from the
        # gzfile()/bzfile()/xzfile() standardise_path() wrapped around it
        if url:
            ext = _file_ext(name).lower()
            if ext in ("gz", "bz2", "xz", "zip"):
                data = decompress(data, ext)
        else:
            data = _r_text_connection(data)
        data = _reencode_wide(data, encoding)
        encoding, name, url = "UTF-8", "vroom-reencode-file", False
    elif encoding != "UTF-8":
        try:
            decode = _decoder(encoding)
        except LookupError as exc:
            raise ReadFailed(f"Can't convert from {encoding} to UTF-8") from exc

    ext = _file_ext(name)
    if url:
        if ext.lower() in ("gz", "bz2", "xz", "zip"):
            data = decompress(data, ext.lower())
        idx, columns, errors = _index_connection(data)
    else:
        if _archive_ext(name):
            raise ReadFailed("The package `archive` is required")
        kind = detect_compression(data) or (ext if ext in ("gz", "bz2", "xz", "zip") else None)
        if kind is not None:
            data = decompress(data, kind)
            idx, columns, errors = _index_connection(data)
        elif data[-1:] != b"\n":
            idx, columns, errors = _index_connection(data)
        else:
            idx, columns, errors = _index_mmap(data)

    cells = _cells(idx, columns)
    if not cells:
        return []
    out: list[str] = []
    for col, spans in enumerate(cells):
        is_last = col == columns - 1
        values: list[str] = []
        for begin, end in spans:
            raw = data[begin:end]
            if is_last and raw.endswith(b"\r"):
                raw = raw[:-1]
            if decode is None:
                nul = raw.find(b"\x00")
                if nul >= 0:
                    errors = True  # "embedded null"
                    raw = raw[:nul]
                values.append(raw.decode("utf-8", "surrogateescape"))
                continue
            try:
                text = decode(raw)
            except (UnicodeDecodeError, UnicodeError) as exc:
                raise ReadFailed("Invalid multibyte sequence") from exc
            nul = text.find("\x00")
            if nul >= 0:
                text = text[:nul]
                if len(text.encode("utf-8")) < len(raw):
                    errors = True
            values.append(text)
        if col == 0:
            out = values
    if errors:
        raise ReadFailed("One or more parsing issues")
    return out


# ---------------------------------------------------------------------------
# base::readLines() and iconv(sub = "byte")
# ---------------------------------------------------------------------------


def _r_text_connection(data: bytes) -> bytes:
    """R's ``file(path, "r")``: gzip/bzip2/xz/lzma files are read decompressed.

    Port of ``comp_type_from_memory()`` (R 4.5 ``connections.c``); zstd is not
    supported by the Python standard library and is read as it is.
    """
    kind: str | None = None
    if data[:2] == b"\x1f\x8b":
        kind = "gz"
    elif (
        len(data) >= 10
        and data[:3] == b"BZh"
        and 0x31 <= data[3] <= 0x39
        and data[4:10] in (b"\x31\x41\x59\x26\x53\x59", b"\x17\x72\x45\x38\x50\x90")
    ):
        kind = "bz2"
    elif data[:5] in (b"\xfd7zXZ", b"\xffLZMA") or data[:5] == b"]\x00\x00\x80\x00":
        kind = "lzma"
    if kind is None:
        return data
    try:
        if kind == "lzma":
            return lzma.decompress(data)
        return decompress(data, kind)
    except (ReadFailed, lzma.LZMAError):
        return data


_R_CR = re.compile(rb"\r\n|\r\r|\r")


def _r_cr(m: re.Match[bytes]) -> bytes:
    return b"\n\n" if m.group() == b"\r\r" else b"\n"


def read_lines_base(data: bytes, decompress_input: bool = True) -> list[str]:
    """``readLines(con, warn = FALSE, skipNul = TRUE)`` in a UTF-8 locale.

    Port of ``do_readLines()`` and ``Rconn_fgetc()``: CR and CRLF map to LF
    (a CR directly after a CR becomes a LF without looking further), NULs are
    dropped, an incomplete last line is kept, and a UTF-8 byte order mark is
    removed from the first line. Bytes are kept as they are (undecodable ones
    as surrogate escapes).
    """
    if decompress_input:
        data = _r_text_connection(data)
    if b"\r" in data:
        data = _R_CR.sub(_r_cr, data)
    data = data.replace(b"\x00", b"")
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    if lines and lines[0].startswith(b"\xef\xbb\xbf"):
        lines[0] = lines[0][3:]
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


def _guess_input(data: bytes, name: str, url: bool) -> bytes:
    """The bytes readr's ``read_lines_raw()`` sees (its own ``standardise_path()``)."""
    if url:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in ("bz2", "xz"):
            raise ValueError(
                f"Reading from remote `{ext}` compressed files is not supported,\n"
                "  download the files locally first."
            )
        if ext == "gz":
            try:
                return decompress(data, "gz")
            except ReadFailed as exc:
                raise ValueError(str(exc)) from exc
        return data
    kind = detect_compression(data)
    if kind is None:
        return data
    try:
        return decompress(data, kind)
    except ReadFailed as exc:
        raise ValueError(str(exc)) from exc


def code_read_bytes(data: bytes, name: str = "", url: bool = False) -> list[str]:
    """The lines metacheck's ``code_read()`` returns for a file with these bytes.

    *name* is the file's path or URL (vroom and readr look at its extension).
    """
    guesses = guess_encoding(_guess_input(data, name, url))
    if not guesses:
        raise IndexError("subscript out of bounds")
    try:
        lines = vroom_lines(data, guesses[0][0], name, url)
    except ReadFailed:
        lines = read_lines_base(data, decompress_input=not url)
    return [iconv_sub_byte(line) for line in lines]
