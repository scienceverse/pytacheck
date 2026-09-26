"""``utils::read.delim()`` as R runs it: the fallback reader of ``data_read_head()``.

``.read_delim_fast()`` falls back to ``read.delim(path, sep, header, nrows,
check.names = FALSE)`` when ``data.table::fread()`` fails -- in practice a
single-column file with stray quotes, a UTF-16 file or a whitespace-only one.
This module reproduces that call at the level of R's C code, because its
results depend on details a simpler CSV reader gets wrong:

* the connection (``Rconn_fgetc()``): CR and CRLF become LF (``"\\r\\r"`` is
  two line ends), NUL bytes end the string they are in;
* ``readtablehead()`` reads the first ``min(5, header + nrows)`` non-blank
  lines (quotes may span lines), which are pushed back *twice* in front of
  the rest of the file -- once for the header / column-count scans, once for
  the data scan;
* ``scan()``'s ``fillBuffer()``: with a separator, a ``"`` anywhere in a field
  opens a quoted section whose quotes are dropped (``a"b"c`` is ``abc``),
  ``""`` inside quotes is a literal quote, a field that is just ``""`` on its
  own line makes the line blank, and the first field of each ``scan()`` call
  loses a UTF-8 byte-order mark;
* ``scanFrame()`` with ``fill = TRUE``: short lines are padded with ``""``,
  long lines wrap into the next row, ``nrows`` is checked at line ends only;
* ``read.table()``'s column count (the most fields in the first 5 lines), its
  row-names rule (one more column than header names makes the first column
  the row names -- duplicated or missing ones are an error);
* ``type.convert(as.is = TRUE)``: logical only for ``T``/``F``/``TRUE``/
  ``FALSE``, ``strtol()`` integers (leading blanks allowed, trailing not),
  ``R_strtod()`` doubles, complex numbers, and the "invalid multibyte string"
  error ``isBlankString()`` raises on invalid UTF-8.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pandas as pd

from pytacheck.datacheck._strings import RAW_STRING

_LF = 0x0A
_CR = 0x0D
_QUOTE = 0x22
_EOF = -1
_BOM = b"\xef\xbb\xbf"
_C_SPACE = frozenset(b" \t\n\v\f\r")
# iswspace() in R's UTF-8 locale (glibc): no-break spaces are not blank
_WSPACE = frozenset(
    chr(c)
    for c in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x1680, *range(0x2000, 0x2007),
              0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x205F, 0x3000)
)  # fmt: skip


class ReadTableError(ValueError):
    """An error ``read.table()`` stops with."""


def _map_cr(data: bytes) -> bytes:
    """``Rconn_fgetc()``: CRLF -> LF, a lone CR -> LF, and CR CR -> LF LF."""
    if b"\r" not in data:
        return data
    out = bytearray()
    i, n = 0, len(data)
    while True:
        k = data.find(b"\r", i)
        if k < 0:
            out += data[i:]
            return bytes(out)
        out += data[i:k]
        nxt = data[k + 1] if k + 1 < n else _EOF
        if nxt in (_LF, _CR):
            out += b"\n" if nxt == _LF else b"\n\n"
            i = k + 2
        else:
            out += b"\n"
            i = k + 1


class _Stream:
    """A byte stream read one ``scan()`` call at a time (``LocalData``)."""

    __slots__ = ("buf", "n", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.n = len(buf)
        self.pos = 0


def _readtablehead(st: _Stream, nlines: int) -> list[bytes]:
    """``readtablehead()``: up to *nlines* non-blank lines (quotes may span lines).

    With a separator, a ``"`` anywhere opens a quote; ``""`` inside one is
    kept as is. A NUL right after a closing quote is lost (it is
    ``unscanchar()``-ed, and a saved 0 reads as "nothing saved"); any other
    NUL ends the line's string.
    """
    buf, n = st.buf, st.n
    lines: list[bytes] = []
    quote = False
    while len(lines) < nlines:
        out = bytearray()
        empty = True
        c = _EOF
        while True:
            if quote:  # copy up to the next quote
                k = buf.find(b'"', st.pos)
                if k < 0:
                    seg = buf[st.pos :]
                    st.pos = n
                else:
                    seg = buf[st.pos : k]
                    st.pos = k
                if empty and seg.strip(b"\n"):
                    empty = False
                out += seg
                if k < 0:
                    c = _EOF
                    break
            if st.pos >= n:
                c = _EOF
                break
            c = buf[st.pos]
            st.pos += 1
            if quote:
                nxt = buf[st.pos] if st.pos < n else _EOF
                if nxt == _QUOTE:
                    st.pos += 1
                    out.append(_QUOTE)
                else:
                    quote = False
                    if nxt == 0:
                        st.pos += 1
            elif c == _QUOTE:
                quote = True
            if empty and c != _LF:
                empty = False
            if quote or c != _LF:
                out.append(c)
            else:
                break
        if not empty:
            lines.append(bytes(out).split(b"\0", 1)[0])
        if c == _EOF:
            break
    return lines


def _rspace(c: int) -> bool:
    return c in (0x20, 0x09, _LF, _CR)


def _fill_buffer(st: _Stream, sep: int, strip: bool, at_start: bool) -> tuple[bytes, int]:
    """``fillBuffer()`` with a separator and ``quote = '"'``: ``(field, bch)``."""
    buf, n = st.buf, st.n
    m = bytearray()
    mm = 0
    c = _EOF
    while True:
        if st.pos >= n:
            c = _EOF
            break
        c = buf[st.pos]
        st.pos += 1
        if c in (sep, _LF, _CR):
            break
        if c == _QUOTE:
            while True:  # inside quotes: up to the closing quote (or EOF)
                k = buf.find(b'"', st.pos)
                if k < 0:
                    m += buf[st.pos :]
                    st.pos = n
                    nxt = _EOF
                    break
                m += buf[st.pos : k]
                st.pos = k + 1
                nxt = buf[st.pos] if st.pos < n else _EOF
                if nxt == _QUOTE:
                    m.append(_QUOTE)
                    st.pos += 1
                    continue
                if nxt != _EOF:
                    st.pos += 1
                break
            mm = len(m)
            if nxt in (sep, _LF, _CR, _EOF):
                c = nxt
                break
            if nxt != 0:  # unscanchar(): read it again -- a saved NUL is lost
                st.pos -= 1
            continue
        if not strip or m or not _rspace(c):
            m.append(c)
    if strip and len(m) > mm:
        k = len(m)
        while k > mm and _rspace(m[k - 1]):
            k -= 1
        del m[k:]
    value = bytes(m).split(b"\0", 1)[0]
    if at_start and value.startswith(_BOM):
        value = value[3:]
    return value, c


def _scan_line(st: _Stream, sep: int, strip: bool) -> list[bytes]:
    """``scan(what = "", nlines = 1)``: the fields of the next line."""
    items: list[bytes] = []
    bch = 1
    at_start = True
    while True:
        if bch == _EOF:
            break
        if bch == _LF:
            break
        value, bch = _fill_buffer(st, sep, strip, at_start)
        at_start = False
        if not items and value == b"" and bch in (_LF, _EOF):
            if bch == _EOF:
                break
        else:
            items.append(value)
    return items


def _scan_frame(st: _Stream, sep: int, nc: int, nmax: int) -> list[list[bytes | None]]:
    """``scan(what = <nc columns>, fill = TRUE, na.strings = "NA", nmax)``."""
    cols: list[list[bytes | None]] = [[] for _ in range(nc)]
    n = colsread = 0
    bch = 1
    at_start = True
    while True:
        if bch == _EOF:
            break
        if bch == _LF:
            if colsread:
                for j in range(colsread, nc):
                    cols[j].append(b"")
                n += 1
                colsread = 0
            if nmax > 0 and n >= nmax:
                break
        value, bch = _fill_buffer(st, sep, False, at_start)
        at_start = False
        if colsread == 0 and value == b"" and bch in (_LF, _EOF):
            if bch == _EOF:
                break
            continue
        cols[colsread].append(None if value == b"NA" else value)
        colsread += 1
        if colsread == nc:
            n += 1
            colsread = 0
    if colsread:
        for j in range(colsread, nc):
            cols[j].append(b"")
    return cols


# -----------------------------------------------------------------------------
# type.convert()
# -----------------------------------------------------------------------------


def _mb_error(s: bytes) -> ReadTableError:
    parts = []
    text = s.decode("utf-8", "surrogateescape")
    for ch in text:
        if "\udc80" <= ch <= "\udcff":
            parts.append(f"<{ord(ch) - 0xDC00:02x}>")
        else:
            parts.append(ch)
    return ReadTableError(f"invalid multibyte string at '{''.join(parts)}'")


def _is_blank_string(s: bytes) -> bool:
    """``isBlankString()`` in a UTF-8 locale (errors on invalid UTF-8 before a non-blank)."""
    text = s.decode("utf-8", "surrogateescape")
    for k, ch in enumerate(text):
        if "\udc80" <= ch <= "\udcff":
            raise _mb_error(text[k:].encode("utf-8", "surrogateescape"))
        if ch not in _WSPACE:
            return False
    return True


def _strtoi(s: bytes) -> int | None:
    """R's ``Strtoi(s, 10)``: ``strtol()`` consuming the whole string (``None`` = NA)."""
    p, n = 0, len(s)
    while p < n and s[p] in _C_SPACE:
        p += 1
    q = p
    if q < n and s[q] in b"+-":
        q += 1
    d = q
    while q < n and 0x30 <= s[q] <= 0x39:
        q += 1
    if q == d or q != n:
        return None
    v = int(s[p:q])
    if v > 2147483647 or v <= -2147483648:
        return None
    return v


def _strtod(s: bytes, na: bool) -> tuple[float | None, int]:
    """``R_strtod5(dec = ".", exact = FALSE)``: ``(value, end)``; ``None`` is NA."""
    n = len(s)
    p = 0
    while p < n and s[p] in _C_SPACE:
        p += 1
    if na and s.startswith(b"NA", p):
        return None, p + 2
    sign = 1.0
    if p < n and s[p] == 0x2D:
        sign = -1.0
        p += 1
    elif p < n and s[p] == 0x2B:
        p += 1
    low = s[p : p + 8].lower()
    if low.startswith(b"nan"):
        return math.nan, p + 3
    if low.startswith(b"infinity"):
        return sign * math.inf, p + 8
    if low.startswith(b"inf"):
        return sign * math.inf, p + 3
    if n - p > 2 and s[p] == 0x30 and s[p + 1] in b"xX":
        return _strtod_hex(s, p + 2, sign)
    q = p
    while q < n and 0x30 <= s[q] <= 0x39:
        q += 1
    int_end = q
    frac_end = q
    if q < n and s[q] == 0x2E:
        q += 1
        while q < n and 0x30 <= s[q] <= 0x39:
            q += 1
        frac_end = q
    if (int_end - p) + (frac_end - int_end - (1 if frac_end > int_end else 0)) <= 0:
        return None, 0
    mant = s[p:frac_end]
    exp = b""
    if q < n and s[q] in b"eE":
        r = q + 1
        if r < n and s[r] in b"+-":
            r += 1
        e0 = r
        while r < n and 0x30 <= s[r] <= 0x39:
            r += 1
        if r == e0:
            return None, 0
        exp = s[q:r]
        q = r
    try:
        v = float((mant + exp).decode("ascii"))
    except (ValueError, OverflowError):  # pragma: no cover - validated above
        return None, 0
    return sign * v, q


def _strtod_hex(s: bytes, p: int, sign: float) -> tuple[float | None, int]:
    n = len(s)
    ans = 0.0
    exph = -1
    while p < n:
        c = s[p]
        if 0x30 <= c <= 0x39:
            ans = 16 * ans + (c - 0x30)
        elif 0x61 <= c <= 0x66:
            ans = 16 * ans + (c - 0x61 + 10)
        elif 0x41 <= c <= 0x46:
            ans = 16 * ans + (c - 0x41 + 10)
        elif c == 0x2E:
            exph = 0
            p += 1
            continue
        else:
            break
        if exph >= 0:
            exph += 4
        p += 1
    expn = 0
    if p < n and s[p] in b"pP":
        r = p + 1
        esign = 1
        if r < n and s[r] == 0x2D:
            esign = -1
            r += 1
        elif r < n and s[r] == 0x2B:
            r += 1
        e0 = r
        val = 0
        while r < n and 0x30 <= s[r] <= 0x39:
            val = val * 10 + (s[r] - 0x30) if val < 9999 else val
            r += 1
        if r == e0:
            return None, 0
        expn = esign * val
        p = r
    if ans != 0.0:
        if exph > 0:
            expn -= exph
        try:
            ans = math.ldexp(ans, expn)
        except OverflowError:
            ans = math.inf
    return sign * ans, p


def _strtoc(s: bytes, na: bool) -> tuple[complex | None, int]:
    """R's ``strtoc()``: ``(value, end)``; ``None`` is NA."""
    x, endp = _strtod(s, na)
    if _is_blank_string(s[endp:]):
        return (None if x is None else complex(x, 0)), endp
    if s[endp : endp + 1] == b"i":
        if endp == 0:
            return None, endp
        return (None if x is None else complex(0, x)), endp + 1
    y, e2 = _strtod(s[endp:], na)
    if s[endp + e2 : endp + e2 + 1] == b"i":
        if x is None or y is None:
            return None, endp + e2 + 1
        return complex(x, y), endp + e2 + 1
    return None, 0


def _is_na_elt(v: bytes | None) -> bool:
    """The skip test of every ``typeconvert()`` loop (NA, empty or blank)."""
    return v is None or v == b"" or _is_blank_string(v)


def _ruleout(v: bytes, types: dict[str, bool]) -> None:
    if types["lgl"]:
        if v in (b"F", b"T", b"FALSE", b"TRUE"):
            types["int"] = types["dbl"] = types["cplx"] = False
            return
        types["lgl"] = False
    if types["int"] and _strtoi(v) is None:
        types["int"] = False
    if types["dbl"]:
        _, endp = _strtod(v, True)
        if not _is_blank_string(v[endp:]):
            types["dbl"] = False
    if types["cplx"]:
        _, endp = _strtoc(v, True)
        if not _is_blank_string(v[endp:]):
            types["cplx"] = False


def type_convert(values: list[bytes | None]) -> pd.Series:
    """``type.convert(x, as.is = TRUE, na.strings = character(0))`` of scanned fields."""
    types = {"lgl": True, "int": True, "dbl": True, "cplx": True}
    for v in values:
        if not _is_na_elt(v):
            _ruleout(v, types)  # type: ignore[arg-type]
            break
    if types["lgl"]:
        out_l: list[bool | None] = []
        for v in values:
            if _is_na_elt(v):
                out_l.append(None)
            elif v in (b"F", b"FALSE"):
                out_l.append(False)
            elif v in (b"T", b"TRUE"):
                out_l.append(True)
            else:
                types["lgl"] = False
                _ruleout(v, types)  # type: ignore[arg-type]
                break
        else:
            return pd.Series(out_l, dtype="boolean")
    if types["int"]:
        out_i: list[int | None] = []
        for v in values:
            if _is_na_elt(v):
                out_i.append(None)
                continue
            iv = _strtoi(v)  # type: ignore[arg-type]
            if iv is None:
                types["int"] = False
                _ruleout(v, types)  # type: ignore[arg-type]
                break
            out_i.append(iv)
        else:
            return pd.Series(out_i, dtype="Int64")
    if types["dbl"]:
        out_d: list[float] = []
        for v in values:
            if _is_na_elt(v):
                out_d.append(math.nan)
                continue
            dv, endp = _strtod(v, False)  # type: ignore[arg-type]
            if not _is_blank_string(v[endp:]):  # type: ignore[index]
                types["dbl"] = False
                _ruleout(v, types)  # type: ignore[arg-type]
                break
            out_d.append(math.nan if dv is None else dv)
        else:
            return pd.Series(out_d, dtype="float64")
    if types["cplx"]:
        out_c: list[complex] = []
        for v in values:
            if _is_na_elt(v):
                out_c.append(complex(math.nan, math.nan))
                continue
            cv, endp = _strtoc(v, False)  # type: ignore[arg-type]
            if not _is_blank_string(v[endp:]):  # type: ignore[index]
                types["cplx"] = False
                break
            out_c.append(complex(math.nan, math.nan) if cv is None else cv)
        else:
            return pd.Series(out_c, dtype="complex128")
    return pd.Series(
        [None if v is None else v.decode("utf-8", "surrogateescape") for v in values],
        dtype=RAW_STRING,
    )


# -----------------------------------------------------------------------------
# read.table()
# -----------------------------------------------------------------------------


def read_table(
    path: str | os.PathLike[str],
    sep: str,
    header: bool,
    nrows: float = math.inf,
    encoding: str | None = None,
) -> pd.DataFrame:
    """``utils::read.delim(path, sep, header, nrows, check.names = FALSE)``.

    ``encoding = "latin1"`` is ``fileEncoding = "latin1"``. Raises
    :class:`ReadTableError` with R's message where ``read.table()`` stops.
    """
    raw = Path(path).read_bytes()
    if encoding == "latin1":
        raw = raw.decode("latin-1").encode("utf-8")
    st = _Stream(_map_cr(raw))
    sep_b = ord(sep)
    if math.isfinite(nrows):
        nlines0 = min(5, int(header) + int(nrows)) if nrows >= 0 else 5
    else:
        nlines0 = 5
    if nlines0 <= 0:
        raise ReadTableError("invalid 'nlines' argument")
    lines = _readtablehead(st, nlines0)
    if not lines:
        raise ReadTableError("no lines available in input")
    if all(ln == b"" for ln in lines):
        raise ReadTableError("empty beginning of file")
    pushed = b"".join(ln + b"\n" for ln in lines)
    st = _Stream(pushed + pushed + st.buf[st.pos :])
    first = _scan_line(st, sep_b, strip=True)
    col1 = len(first)
    counts = [len(_scan_line(st, sep_b, strip=False)) for _ in range(len(lines) - 1)]
    cols = max([col1, *counts])
    rlabp = header and (cols - col1) == 1
    if header:
        _readtablehead(st, 1)
        col_names = [f.decode("utf-8", "surrogateescape") for f in first]
    else:  # paste0("V", 1L:cols): 1:0 is c(1, 0)
        col_names = [f"V{j}" for j in (range(1, cols + 1) if cols else (1, 0))]
    if len(col_names) + rlabp < cols:
        raise ReadTableError("more columns than column names")
    cols = max(cols, len(col_names))
    if cols == 0:
        raise ReadTableError("first five rows are empty: giving up")
    if rlabp:
        col_names = ["row.names", *col_names]
    nmax = int(nrows) if math.isfinite(nrows) and nrows > 0 else 0
    data = _scan_frame(st, sep_b, cols, nmax)
    nrow = len(data[0])
    if rlabp:
        row_names = data[0]
        data = data[1:]
        col_names = col_names[1:]
        if len(set(row_names)) != len(row_names):
            raise ReadTableError("duplicate 'row.names' are not allowed")
        if any(r is None for r in row_names):
            raise ReadTableError("missing values in 'row.names' are not allowed")
    series = [type_convert(col) for col in data]
    out = pd.DataFrame(dict(enumerate(series)), index=range(nrow))
    out.columns = pd.Index(col_names, dtype=object)
    return out
