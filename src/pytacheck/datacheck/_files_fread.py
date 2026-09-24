"""An emulation of ``data.table::fread()`` as metacheck calls it.

metacheck's ``.read_delim_fast()`` (``R/data_check_helpers.R``) reads delimited
text with::

    data.table::fread(path, sep = sep, header = header, nrows = n,
                      showProgress = FALSE, data.table = FALSE,
                      check.names = FALSE, encoding = "UTF-8")

Everything downstream (column names, row counts, column types) depends on
fread's exact behaviour, so this module ports its C algorithm
(data.table 1.18.6, ``src/fread.c``) for the case of a *given* separator:

* BOM / trailing ``\\x1A`` handling, ``\\r``-only line endings, the final
  newline and leading blank lines;
* automatic detection of the first row and the quote rule (the largest block
  of lines with a consistent field count among the first ``min(100, nrows)``
  lines), including single-column input;
* the ``dec = "auto"`` vote for ``sep != ","``;
* the type ladder ``logical < integer < integer64 < double < IDate < POSIXct
  < character`` with fread's own field grammars (``TRUE``/``True``/``true``,
  leading zeros, ``Inf``/``NaN``/Excel error literals, ISO-8601 dates and
  UTC timestamps, hexadecimal doubles), quoted numbers and the ``"NA"``
  string;
* fread's quirks: doubled quotes inside quoted fields are kept doubled, empty
  character fields are ``""`` (not ``NA``), unnamed columns become ``V<j>``,
  reading stops early (with a warning) at the first row with a different
  number of fields after trying the more lenient quote rules.

Values are decoded as UTF-8 with ``surrogateescape`` so that invalid bytes
survive (fread marks strings as UTF-8 without validating them); metacheck's
``.utf8_repair_df()`` (``pytacheck.datacheck.files._utf8_repair_df``) then
reinterprets such strings as Latin-1, exactly as R does.
"""

from __future__ import annotations

import datetime as dt
import math
import struct
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.regex import compile_r

__all__ = ["FreadError", "fread"]

# -- column types (fread.h colType) --------------------------------------------
CT_EMPTY = 1
CT_BOOL8_U = 3
CT_BOOL8_T = 4
CT_BOOL8_L = 5
CT_INT32 = 7
CT_INT64 = 8
CT_FLOAT64 = 9
CT_FLOAT64_EXT = 10
CT_FLOAT64_HEX = 11
CT_ISO8601_DATE = 12
CT_ISO8601_TIME = 13
CT_STRING = 14
# enabled types in ladder order (CT_BOOL8_N / CT_BOOL8_Y are disabled by
# fread's defaults logical01 = FALSE and logicalYN = FALSE)
_LADDER = (
    CT_EMPTY,
    CT_BOOL8_U,
    CT_BOOL8_T,
    CT_BOOL8_L,
    CT_INT32,
    CT_INT64,
    CT_FLOAT64,
    CT_FLOAT64_EXT,
    CT_FLOAT64_HEX,
    CT_ISO8601_DATE,
    CT_ISO8601_TIME,
    CT_STRING,
)
_DEC_TYPES = (CT_FLOAT64, CT_FLOAT64_EXT, CT_ISO8601_TIME)

_LF, _CR, _QUOTE, _SPACE, _TAB, _NUL = 10, 13, 34, 32, 9, 0
_ISSPACE = frozenset(b" \t\n\v\f\r")
_INT32_MAX = 2147483647
_INT64_MAX = 9223372036854775807
_JUMPLINES = 100
_NA_STRING = b"NA"


class FreadError(RuntimeError):
    """``fread()`` stopped with an error (``STOP()`` in fread.c)."""


# -----------------------------------------------------------------------------
# Field grammars. A raw field (the bytes between two separators, decoded as
# Latin-1 so that bytes map 1:1 onto characters) is valid for a type when,
# after stripping fread's whitespace, it is blank, the NA string, an empty
# quoted string, or (optionally quoted) one complete token of the type -- the
# same acceptance as fread's detect_types() / read loop (skip_white,
# end_NA_string, quote retry, parser, closing quote, skip_white,
# end_of_field).
# -----------------------------------------------------------------------------

_REGULAR = r"[+-]?(?:[0-9]+(?:{dec}[0-9]*)?|{dec}[0-9]+)(?:[eE][+-]?[0-9]{{1,3}})?"
_EXT_SPECIAL = (
    r"[+-]?(?:nan|inf|INF|Inf(?:inity)?|NaN[%QS]?[0-9]*|NAN[0-9]*|[qs]NaN[0-9]*"
    r"|1\.#(?:SNAN|QNAN|IND|INF)|#DIV/0!|#VALUE!|#NULL!|#NAME\?|#NUM!|#REF!|#N/A)"
)
_HEX = r"[+-]?(?:0[xX][01]\.[0-9a-fA-F]*[pP][+-]?[0-9]*|NaN|Infinity)"
_DATE = r"([+-]?[0-9]+)-([+-]?[0-9]+)-([+-]?[0-9]+)"
_TIME = (
    _DATE + r"(?:[ T]([+-]?[0-9]+):([+-]?[0-9]+):({sec})"
    r"(?:Z|[ ]?([+-][0-9]+(?::[+-]?[0-9]+)?)|[ ]?)?)?"
)
_BOOL = {
    CT_BOOL8_U: ("TRUE", "FALSE", "NA"),
    CT_BOOL8_T: ("True", "False"),
    CT_BOOL8_L: ("true", "false"),
}

#: returned by _Grammar.token() for fields every non-string type reads as NA
_NA_FIELD = object()


def _escape_dec(dec: str) -> str:
    return r"\." if dec == "." else dec


class _Grammar:
    """Field validators/converters for one (whitespace, dec) setting."""

    def __init__(self, white: str, dec: str) -> None:
        self.white = white + "\0"
        self.dec = dec
        d = _escape_dec(dec)
        self.rx_int = compile_r(r"[+-]?[0-9]+", perl=True)
        self.rx_num = compile_r(_REGULAR.format(dec=d), perl=True)
        self.rx_special = compile_r(_EXT_SPECIAL, perl=True)
        self.rx_hex = compile_r(_HEX, perl=True)
        self.rx_date = compile_r(_DATE, perl=True)
        self.rx_time = compile_r(_TIME.format(sec=_REGULAR.format(dec=d)), perl=True)

    def token(self, s: str) -> Any:
        """The token to parse, :data:`_NA_FIELD`, or ``None`` (unbalanced quotes)."""
        s = s.strip(self.white)
        if not s or s == "NA":
            return _NA_FIELD
        if s[0] == '"':
            if len(s) < 2 or s[-1] != '"':
                return None
            s = s[1:-1]
            if not s:
                return _NA_FIELD
        return s

    def valid(self, s: str, t: int) -> bool:
        if t == CT_STRING:
            return True
        tok = self.token(s)
        if tok is _NA_FIELD:
            return True
        if tok is None:
            return False
        return self.parse(tok, t) is not _INVALID

    def parse_all(self, strs: list[str], t: int) -> list[Any] | None:
        """Values of all fields as type *t*, or ``None`` if one is not valid."""
        out: list[Any] = []
        token, parse = self.token, self.parse
        for s in strs:
            tok = token(s)
            if tok is _NA_FIELD:
                out.append(None)
                continue
            if tok is None:
                return None
            v = parse(tok, t)
            if v is _INVALID:
                return None
            out.append(v)
        return out

    def value(self, s: str, t: int) -> Any:
        """The typed value of a field known to be valid for *t* (``None`` is NA)."""
        tok = self.token(s)
        if tok is _NA_FIELD or tok is None:
            return None
        v = self.parse(tok, t)
        return None if v is _INVALID else v

    def parse(self, tok: str, t: int) -> Any:
        """Parse one token as type *t*: the value, ``None`` (NA) or ``_INVALID``."""
        if t == CT_EMPTY:
            return _INVALID
        if t in _BOOL:
            if tok not in _BOOL[t]:
                return _INVALID
            return None if tok == "NA" else tok[0] in "Tt"
        if t in (CT_INT32, CT_INT64):
            if self.rx_int.fullmatch(tok) is None:
                return _INVALID
            max_sf, max_val = (10, _INT32_MAX) if t == CT_INT32 else (19, _INT64_MAX)
            digits = tok.lstrip("+-").lstrip("0")
            if len(digits) > max_sf or (int(digits) if digits else 0) > max_val:
                return _INVALID
            return int(tok)
        if t == CT_FLOAT64:
            return self._regular(tok)
        if t == CT_FLOAT64_EXT:
            if self.rx_special.fullmatch(tok) is not None:
                return _special_value(tok)
            return self._regular(tok)
        if t == CT_FLOAT64_HEX:
            if self.rx_hex.fullmatch(tok) is None:
                return _INVALID
            v = _hex_value(tok)
            return _INVALID if v is _INVALID else v
        if t == CT_ISO8601_DATE:
            m = self.rx_date.fullmatch(tok)
            parts = None if m is None else _date_parts(m.group(1), m.group(2), m.group(3))
            return _INVALID if parts is None else _make_date(*parts)
        if t == CT_ISO8601_TIME:
            m = self.rx_time.fullmatch(tok)
            v = None if m is None else _time_value(m, self.dec)
            return _INVALID if v is None else v
        return _INVALID

    def _regular(self, tok: str) -> Any:
        if self.rx_num.fullmatch(tok) is None:
            return _INVALID
        if len(tok) <= 17 and "e" not in tok and "E" not in tok:
            # at most 17 digits: nothing to truncate, exponent in range
            return float(tok if self.dec == "." else tok.replace(",", "."))
        v = _regular_value(tok, self.dec)
        return _INVALID if v is None else v


_INVALID = object()


def _split_regular(tok: str, dec: str) -> tuple[bool, int, int] | None:
    """fread's parse_double_regular_core(): ``(negative, mantissa, exponent)``.

    At most 18 significant digits are kept (further fractional digits are
    dropped); more than 18 integer digits need a decimal point or an exponent.
    """
    neg = tok.startswith("-")
    body = tok[1:] if tok[:1] in "+-" else tok
    mant, _, exp_part = body.replace("E", "e").partition("e")
    whole, has_dec, frac = mant.partition(dec)
    whole = whole.lstrip("0")
    e = 0
    if len(whole) > 18:
        if not has_dec and not exp_part:
            return None
        e = len(whole) - 18
        whole = whole[:18]
        frac = ""
    elif frac:
        if not whole:
            stripped = frac.lstrip("0")
            e -= len(frac) - len(stripped)
            frac = stripped
        frac = frac[: 18 - len(whole)]
    e -= len(frac)
    acc = int((whole + frac) or "0")
    if exp_part:
        e += int(exp_part)
    if e < -350 or e > 350:
        return None
    return neg, acc, e


def _regular_value(tok: str, dec: str) -> float | None:
    parts = _split_regular(tok, dec)
    if parts is None:
        return None
    neg, acc, e = parts
    v = float(f"{acc}e{e}")
    return -v if neg else v


def _special_value(tok: str) -> float | None:
    """Value of a FLOAT64_EXT literal: NaN, +-Inf, or ``None`` (NA)."""
    body = tok.lstrip("+-")
    if body in ("#NULL!", "#NAME?", "#NUM!", "#REF!", "#N/A"):
        return None
    if body.upper().startswith(("INF", "1.#INF")):
        return -math.inf if tok.startswith("-") else math.inf
    return math.nan


def _hex_value(tok: str) -> Any:
    """fread's parse_double_hexadecimal(): value, ``None`` (NA) or ``_INVALID``."""
    neg = tok.startswith("-")
    body = tok.lstrip("+-")
    if body == "NaN":
        return None
    if body == "Infinity":
        return -math.inf if neg else math.inf
    subnormal = body[2] == "0"
    frac, _, exp = body[4:].replace("P", "p").partition("p")
    if len(frac) > 13:
        return _INVALID
    acc = (int(frac, 16) if frac else 0) << ((13 - len(frac)) * 4)
    exp_neg = exp.startswith("-")
    e = int(exp.lstrip("+-") or "0")
    big_e = 1023 + (-e if exp_neg else e) - (1 if subnormal else 0)
    if (big_e != 0) if subnormal else (big_e < 1 or big_e > 2046):
        return _INVALID
    bits = (int(neg) << 63) | (big_e << 52) | acc
    return float(struct.unpack("<d", struct.pack("<Q", bits))[0])


def _i32(tok: str) -> int | None:
    """fread's str_to_i32_core(): sign, digits (leading zeros ok), <= INT32_MAX."""
    digits = tok.lstrip("+-").lstrip("0")
    if len(digits) > 10:
        return None
    v = int(digits) if digits else 0
    if v > _INT32_MAX:
        return None
    return -v if tok.startswith("-") else v


_NORM_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap(year: int) -> bool:
    # C semantics: % and / truncate towards zero
    def cmod(a: int, b: int) -> int:
        return int(math.fmod(a, b))

    return cmod(year, 4) == 0 and (cmod(year, 100) != 0 or cmod(int(year / 100), 4) == 0)


def _date_parts(ys: str, ms: str, ds: str) -> tuple[int, int, int] | None:
    y, mo, d = _i32(ys), _i32(ms), _i32(ds)
    if y is None or y < -5877640 or y > 5881579:
        return None
    if mo is None or mo < 1 or mo > 12:
        return None
    days = _NORM_DAYS[mo - 1] + (1 if mo == 2 and _is_leap(y) else 0)
    if d is None or d < 1 or d > days:
        return None
    return y, mo, d


def _make_date(y: int, mo: int, d: int) -> dt.date:
    # Python dates stop at years 1..9999; fread's IDate does not (rare in data)
    return dt.date(min(max(y, 1), 9999), mo, d)


def _epoch_days(y: int, mo: int, d: int) -> int:
    return (_make_date(y, mo, d) - dt.date(1970, 1, 1)).days


def _time_value(m: Any, dec: str) -> float | None:
    """fread's parse_iso8601_timestamp(): seconds since the epoch (UTC)."""
    parts = _date_parts(m.group(1), m.group(2), m.group(3))
    if parts is None:
        return None
    days = _epoch_days(*parts)
    if m.group(4) is None:
        return 86400.0 * days
    hour, minute = _i32(m.group(4)), _i32(m.group(5))
    if hour is None or hour < 0 or hour > 23 or minute is None or minute < 0 or minute > 59:
        return None
    sec = _regular_value(m.group(6), dec)
    if sec is None or sec < 0 or sec >= 60:
        return None
    tz_h = tz_m = 0
    tz = m.group(7)
    if tz is not None:
        hpart, colon, mpart = tz.partition(":")
        tz_h_v = _i32(hpart)
        if tz_h_v is None:
            return None
        tz_h = tz_h_v
        if colon and len(hpart) != 3:
            return None  # the ':' is only consumed after a [+-]AA offset
        if len(hpart) == 5 and tz_h != 0:
            if abs(tz_h) > 2400:
                return None
            tz_m = int(math.fmod(tz_h, 100))
            tz_h = int(tz_h / 100)
        elif len(hpart) == 3:
            if abs(tz_h) > 24:
                return None
            if colon:
                tz_m_v = _i32(mpart)
                if tz_m_v is None:
                    return None
                tz_m = tz_m_v
    return 86400.0 * days + 3600 * (hour - tz_h) + 60 * (minute - tz_m) + sec


_GRAMMARS: dict[tuple[str, str], _Grammar] = {}


def _grammar(white: str, dec: str) -> _Grammar:
    key = (white, dec)
    g = _GRAMMARS.get(key)
    if g is None:
        g = _GRAMMARS[key] = _Grammar(white, dec)
    return g


# -----------------------------------------------------------------------------
# The scanner: a port of fread.c's field/line primitives over a bytes buffer.
# -----------------------------------------------------------------------------


class _Scanner:
    def __init__(self, buf: bytes, eof: int, sep: int, eol_one_r: bool) -> None:
        # two NUL sentinels so that ch[1] lookups at eof are safe
        self.buf = buf[:eof] + b"\0\0"
        self.eof = eof
        self.sep = sep
        self.eol_one_r = eol_one_r
        self.rule = 0
        # whiteChar: 0 means skip both ' ' and '\t'
        self.white = _SPACE if sep == _TAB else 0
        self._eolc = (1, 0)  # cache for _next_eol_char(): (from, found)

    @property
    def sep(self) -> int:
        return self._sep

    @sep.setter
    def sep(self, value: int) -> None:
        self._sep = value
        self.sep_b = bytes([value])

    # -- primitives ----------------------------------------------------------
    def eol(self, i: int) -> tuple[bool, int]:
        buf = self.buf
        j = i
        while buf[j] == _CR:
            j += 1
        if buf[j] == _LF:
            while buf[j + 1] == _CR:
                j += 1
            return True, j
        return (self.eol_one_r and buf[i] == _CR), i

    def end_of_field(self, i: int) -> bool:
        c = self.buf[i]
        if c == self.sep:
            return True
        if c <= 13:
            return i == self.eof or self.eol(i)[0]
        return False

    def skip_white(self, i: int) -> int:
        buf, eof = self.buf, self.eof
        if self.white == 0:
            while buf[i] in (_SPACE, _TAB) or (buf[i] == _NUL and i < eof):
                i += 1
        else:
            while buf[i] == self.white or (buf[i] == _NUL and i < eof):
                i += 1
        return i

    def field(self, i: int) -> tuple[int, int, int]:
        """fread's Field(): ``(offset, length, next_position)``; length -1 is NA."""
        buf, eof, quote = self.buf, self.eof, _QUOTE
        ch = i
        if buf[ch] == _SPACE or (buf[ch] == _NUL and ch < eof):
            ch += 1
            while buf[ch] == _SPACE or (buf[ch] == _NUL and ch < eof):
                ch += 1
        start = ch
        if buf[ch] != quote or self.rule == 3:
            ch = self._scan_unquoted(ch)
            nxt = ch
            n = ch - start
            while n > 0 and buf[ch - 1] in (_SPACE, _NUL):
                n -= 1
                ch -= 1
            if n == 2 and buf[start : start + 2] == _NA_STRING:
                n = -1
            return start, n, nxt
        start += 1
        rule = self.rule
        if rule == 0:
            # the closing quote is the first one not doubled; eof if there is none
            k = ch + 1
            while True:
                k = buf.find(b'"', k, eof)
                if k == -1:
                    ch = eof
                    break
                if buf[k + 1] == quote:
                    k += 2
                    continue
                ch = k
                break
        elif rule == 1:
            # backslash-escaped quotes (and backslashes)
            k = ch + 1
            while True:
                kq = buf.find(b'"', k, eof)
                kb = buf.find(b"\\", k, eof if kq == -1 else kq)
                if kb != -1:
                    k = kb + (2 if buf[kb + 1] in (quote, 92) else 1)
                    continue
                ch = eof if kq == -1 else kq
                break
        else:  # rule 2
            ch2 = ch
            while True:
                ch += 1
                if not (buf[ch] or ch < eof) or buf[ch] in (_LF, _CR):
                    break
                if buf[ch] == quote and self.end_of_field(ch + 1):
                    ch2 = ch
                    break
                if buf[ch] == self.sep:
                    ch2 = ch
                    while True:
                        ch2 += 1
                        if not (buf[ch2] or ch2 < eof) or buf[ch2] in (_LF, _CR):
                            break
                        if buf[ch2] == quote and self.end_of_field(ch2 + 1):
                            ch = ch2
                            break
                    break
            if ch != ch2:
                start -= 1
        n = ch - start
        if buf[ch] == quote:
            return start, n, self.skip_white(ch + 1)
        nxt = ch
        if ch == eof and rule != 2:
            start -= 1
            n += 1
        while n > 0 and buf[ch - 1] in (_SPACE, _NUL):
            n -= 1
            ch -= 1
        return start, n, nxt

    def _next_eol_char(self, ch: int) -> int:
        """Position of the next \\n or \\r at or after *ch* (cached; eof if none)."""
        lo, hi = self._eolc
        if lo <= ch <= hi:
            return hi
        buf, eof = self.buf, self.eof
        k1 = buf.find(b"\n", ch, eof)
        k2 = buf.find(b"\r", ch, eof if k1 == -1 else k1)
        k = k2 if k2 != -1 else (k1 if k1 != -1 else eof)
        self._eolc = (ch, k)
        return k

    def _scan_unquoted(self, ch: int) -> int:
        """Advance to the end of an unquoted field (sep, eol or eof)."""
        buf, eof = self.buf, self.eof
        while True:
            limit = self._next_eol_char(ch)
            k = buf.find(self.sep_b, ch, limit)
            if k != -1:
                return k
            ch = limit
            if ch >= eof or self.end_of_field(ch):
                return ch
            ch += 1  # a lone \\r inside a field

    def countfields(self, i: int) -> tuple[int, int]:
        """fread's countfields(): ``(ncol, next_line)``; ncol -1 = invalid line."""
        eof = self.eof
        if i >= eof:
            return 0, i
        ch = self.skip_white(i)
        ok, e = self.eol(ch)
        if ok or ch == eof:
            return 0, e + 1
        ncol = 1
        while ch < eof:
            _, _, ch = self.field(ch)
            if self.buf[ch] == self.sep:
                ch += 1
                ncol += 1
                continue
            ok, e = self.eol(ch)
            if ok:
                return ncol, e + 1
            if ch != eof:
                return -1, ch
            break
        return ncol, ch

    def skip_to_nextline(self, ch: int) -> int:
        buf, eof = self.buf, self.eof
        while ch < eof and buf[ch] not in (_LF, _CR):
            ch += 1
        if ch < eof:
            ok, e = self.eol(ch)
            if ok:
                ch = e
            ch += 1
        return ch

    def next_good_line(self, ch: int, ncol: int) -> int:
        """fread's nextGoodLine(): a line start near *ch* with *ncol* fields."""
        ch = self.skip_to_nextline(ch)
        if ch >= self.eof:
            return self.eof
        simple = ch
        for _ in range(5):
            if ch >= self.eof:
                break
            if self.countfields(ch)[0] == ncol:
                return ch
            ch = self.skip_to_nextline(ch)
        return simple

    def read_row(self, i: int, ncol: int) -> tuple[list[tuple[int, int, int, int]], int] | None:
        """Tokenize one data row: ``([(span_start, span_end, val_off, val_len)], next)``.

        ``None`` when the row does not have exactly *ncol* fields under the current
        quote rule (fread stops early there, or bumps the quote rule).
        """
        buf, eof, sep = self.buf, self.eof, self.sep
        fields: list[tuple[int, int, int, int]] = []
        ch = i
        if ncol > 1 and buf[ch] in (_LF, _CR) and self.eol(ch)[0]:
            return None  # empty line: fread stops (fill = FALSE)
        while True:
            start = ch
            off, n, ch = self.field(ch)
            fields.append((start, ch, off, n))
            if buf[ch] == sep:
                if len(fields) == ncol:
                    return None  # too many fields
                ch += 1
                continue
            break
        if len(fields) != ncol:
            return None
        ok, e = self.eol(ch)
        if ok:
            return fields, e + 1
        if ch >= eof:
            return fields, eof
        return None


# -----------------------------------------------------------------------------
# fread()
# -----------------------------------------------------------------------------


def _signed(b: int) -> int:
    return b - 256 if b > 127 else b


def fread(
    path: str | Path,
    sep: str,
    header: bool,
    nrows: float = math.inf,
) -> pd.DataFrame:
    """Read a delimited file like ``data.table::fread()`` (see the module docstring).

    Raises :class:`FreadError` where fread stops with an error.
    """
    raw = Path(path).read_bytes()
    if not raw:
        warnings.warn(f"File '{path}' has size 0. Returning a NULL data.frame.", stacklevel=2)
        return pd.DataFrame()
    sof, eof = 0, len(raw)
    if raw[:3] == b"\xef\xbb\xbf":
        sof = 3
    elif raw[:4] == b"\x84\x31\x95\x33":
        sof = 4
        warnings.warn(
            "GB-18030 encoding detected, however fread() is unable to decode it. "
            "Some character fields may be garbled.",
            stacklevel=2,
        )
    elif len(raw) >= 2 and _signed(raw[0]) + _signed(raw[1]) == -3:
        raise FreadError(
            "File is encoded in UTF-16, this encoding is not supported by fread(). "
            "Please recode the file to UTF-8."
        )
    if eof > sof and raw[eof - 1] in (0x1A, 0):
        c = raw[eof - 1]
        while eof > sof and raw[eof - 1] == c:
            eof -= 1
    if eof <= sof:
        raise FreadError("Input is empty or only contains BOM or terminal control characters")
    buf = raw[sof:eof]
    eof -= sof

    # [4] line endings: mostly \r-only files treat a lone \r as a line ending
    sample = buf[:100000]
    n_r_only = n_with_n = 0
    k, m = 0, len(sample)
    while k < m:
        c = sample[k]
        if c == _CR:
            while k < m and sample[k] == _CR:
                k += 1
            if k < m and sample[k] == _LF:
                n_with_n += 1
                k += 1
            else:
                n_r_only += 1
        elif c == _LF:
            n_with_n += 1
            k += 1
        else:
            k += 1
    eol_one_r = n_r_only > n_with_n

    # whitespace after the final newline is dropped
    last_eol_replaced = False
    ch = eof - 1
    if eol_one_r:
        while ch >= 0 and buf[ch] != _CR:
            ch -= 1
    else:
        while ch >= 0 and buf[ch] != _LF:
            ch -= 1
        while ch > 0 and buf[ch - 1] == _CR:
            ch -= 1
    if ch >= 0:
        last_nl = ch
        ch += 1
        while ch < eof and buf[ch] in _ISSPACE:
            ch += 1
        if ch == eof:
            eof = last_nl
            last_eol_replaced = True

    # [5] skip blank input at the start
    ch = line_start = 0
    while ch < eof and (buf[ch] in _ISSPACE or buf[ch] == 0):
        if buf[ch] == _LF:
            ch += 1
            line_start = ch
        else:
            ch += 1
    if ch >= eof:
        raise FreadError(
            "Input is either empty, fully whitespace, or skip has been set after the "
            "last non-whitespace."
        )
    pos = line_start

    nrow_limit = nrows if math.isfinite(nrows) else math.inf
    jump_lines = _JUMPLINES if nrow_limit == 0 else int(min(_JUMPLINES, nrow_limit))
    sep_b = ord(sep)
    sc = _Scanner(buf, eof, sep_b, eol_one_r)

    # [6] detect the quote rule, the first line and ncol
    top_lines, top_fields, top_rule, top_start, top_sep = 0, 1, -1, -1, 127
    first_jump_end = -1
    for rule in range(4):
        sc.rule = rule
        ch = pos
        line_start = ch
        lastncol, ch = sc.countfields(ch)
        if lastncol < 0:
            continue
        if lastncol == 0:
            # a \r-only file starting with a blank line: fread's own assertion fails
            raise FreadError(
                "Internal error in freadMain: first non-empty row should always be at "
                f"least one field; {chr(sep_b)} {rule}. Please report to the data.table "
                "issues tracker"
            )
        block_start, block_lines, this_row = line_start, 1, 0
        while ch < eof:
            this_row += 1
            if this_row >= jump_lines:
                break
            line_start = ch
            thisncol, ch = sc.countfields(ch)
            if thisncol < 0:
                break
            if thisncol == lastncol:
                block_lines += 1
                continue
            if lastncol > 1 and block_lines > 1:
                break
            while ch < eof and thisncol == 0:
                line_start = ch
                this_row += 1
                thisncol, ch = sc.countfields(ch)
            if thisncol > 0:
                lastncol = thisncol
                block_lines = 1
                block_start = line_start
        block_has_quote = lastncol == 1 and _QUOTE in sc.buf[block_start:ch]
        single_candidate = lastncol == 1 and block_lines >= 2 and block_has_quote and rule < 3
        better_lines = block_lines > top_lines and (
            lastncol > 1 or (single_candidate and top_fields <= 1)
        )
        promote = top_fields <= 1 and lastncol > top_fields and block_lines >= 2
        better_tie = (
            block_lines == top_lines
            and lastncol > top_fields
            and (rule < 2 or rule <= top_rule)
            and (top_fields <= 1 or sep_b != _SPACE)
        )
        if better_lines or promote or better_tie:
            top_lines, top_fields, top_rule = block_lines, lastncol, rule
            top_sep = 127 if single_candidate else sep_b
            top_start = block_start
            first_jump_end = ch
    if first_jump_end < 0:
        # single column input: pick the quote rule (0 or 1) that reads furthest
        top_sep, top_fields = 127, 1
        sc.sep = 127
        for rule in (0, 1):
            sc.rule = rule
            ch, this_row, thisncol = pos, 0, 0
            while ch < eof:
                this_row += 1
                if this_row >= jump_lines:
                    break
                thisncol, ch = sc.countfields(ch)
                if thisncol < 0:
                    break
            if thisncol < 0:
                continue
            if first_jump_end < 0 or ch > first_jump_end:
                first_jump_end, top_rule = ch, rule
        if first_jump_end < 0:
            raise FreadError(
                "Single column input contains invalid quotes. Self healing only "
                "effective when ncol>1"
            )
        top_start = pos
    elif top_rule > 1:
        warnings.warn(
            f"Found and resolved improper quoting in first {jump_lines} rows. If the "
            "fields are not quoted (e.g. field separator does not appear within any "
            'field), try quote="" to avoid this warning.',
            stacklevel=2,
        )
    sc.rule = top_rule
    sc.sep = top_sep
    sc.white = _SPACE if top_sep == _TAB else 0
    ncol = top_fields
    if top_sep != 127:  # single-column input keeps the first non-blank line
        pos = top_start

    if ncol == 1 and last_eol_replaced and eof > 0 and buf[eof - 1] in (_LF, _CR):
        # multiple newlines at the end are significant for single-column files
        eof += 1
        sc.buf = buf[: eof - 1] + (b"\r" if eol_one_r else b"\n") + b"\0\0"
        sc.eof = eof
        sc._eolc = (1, 0)

    # [7] sample column types (and vote on dec when sep is not ","). Only the
    # first jump is sampled up front: the others can only matter when the read
    # below stops early or has to change the quote rule.
    white = " " if sc.sep == _TAB else " \t"
    sample_args = (sc, pos, header, ncol, jump_lines, first_jump_end, nrow_limit, white)
    first_rule = sc.rule
    dec0 = "." if sc.sep == ord(",") else ""  # "" means dec = "auto": vote
    sampled, tmp_types, dec = _sample(*sample_args, dec0, False)

    # [8] column names
    names: list[str]
    data_start = pos
    if header:
        names = []
        ch = pos - 1
        for _j in range(ncol):
            ch += 1
            off, n, ch = sc.field(ch)
            names.append("" if n <= 0 else _decode(sc.buf[off : off + n]))
            if sc.buf[ch] != sc.sep:
                break
        ok, e = sc.eol(ch)
        data_start = e + 1 if ok else ch
        names += [""] * (ncol - len(names))
        names = [nm if nm else f"V{j + 1}" for j, nm in enumerate(names)]
    else:
        names = [f"V{j + 1}" for j in range(ncol)]

    # [11] read the rows, bumping the quote rule on a bad row. An out-of-sample
    # type bump makes fread re-read the bumped columns from the top with the
    # quote rule in force at the end of the first pass.
    gram = _grammar(white, dec)
    start_types = _start_types(sampled, tmp_types, header)
    rows = _read_rows(
        sc,
        data_start,
        ncol,
        nrow_limit,
        lambda rs: _climb(gram, rs.spans[0], start_types[0]) == CT_STRING,
    )
    if rows.stop is not None or sc.rule != first_rule:
        # sample every jump point, as fread did before reading (with the quote
        # rule detected up front)
        read_rule, sc.rule = sc.rule, first_rule
        sampled, tmp_types, _ = _sample(*sample_args, dec0, True)
        sc.rule = read_rule
        start_types = _start_types(sampled, tmp_types, header)
    types = [_climb(gram, rows.spans[j], start_types[j]) for j in range(ncol)]
    bumped = [t != s0 for t, s0 in zip(types, start_types, strict=True)]
    rows2 = rows
    if any(bumped) and sc.rule != first_rule:
        rows2 = _read_rows(sc, data_start, ncol, nrow_limit, lambda _rs: types[0] == CT_STRING)
        rows.truncate(rows2.n)
        types = [
            _climb(gram, rows2.spans[j], types[j]) if bumped[j] else types[j] for j in range(ncol)
        ]
    if rows2.stop is not None:
        _warn_stopped(sc, rows2.stop, ncol, rows2.n)
    cols = [_column(types[j], rows2 if bumped[j] else rows, j, gram) for j in range(ncol)]
    out = pd.DataFrame(dict(enumerate(cols)))
    out.columns = pd.Index(names, dtype=object)
    return out


def _decode(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def _start_types(sampled: list[int], tmp_types: list[int], header: bool) -> list[int]:
    """Column types after sampling; with ``header = FALSE`` fread also applies
    tmpType (the first row's bumps, and those of a partially sampled bad line)."""
    if header:
        return sampled
    return [max(a, b, key=_LADDER.index) for a, b in zip(sampled, tmp_types, strict=True)]


def _next_type(t: int) -> int:
    return _LADDER[_LADDER.index(t) + 1]


def _climb(gram: _Grammar, spans: list[bytes], start: int) -> int:
    """The lowest type at or above *start* that reads every field."""
    return _climb_values(gram, spans, start)[0]


def _climb_values(
    gram: _Grammar, spans: list[bytes], start: int
) -> tuple[int, dict[bytes, Any] | None]:
    """:func:`_climb` plus the parsed value of each distinct field."""
    uniq = list(dict.fromkeys(spans))
    strs = [b.decode("latin-1") for b in uniq]
    t = start
    while t != CT_STRING:
        vals = gram.parse_all(strs, t)
        if vals is not None:
            return t, dict(zip(uniq, vals, strict=True))
        t = _next_type(t)
    return CT_STRING, None


class _DecState:
    """fread's global ``dec`` while it is still being voted on."""

    def __init__(self, dec: str) -> None:
        self.auto = not dec
        self.dec = dec
        self.trial = ""
        self.votes = 0


def _sample(
    sc: _Scanner,
    pos: int,
    header: bool,
    ncol: int,
    jump_lines: int,
    first_jump_end: int,
    nrow_limit: float,
    white: str,
    dec: str,
    all_jumps: bool,
) -> tuple[list[int], list[int], str]:
    """fread's step [7]: sampled column types, the raw tmpType, and dec.

    Samples ``jump_lines`` lines at the start (and, with *all_jumps*, for larger
    files at 10 or 100 jump points plus the end) with detect_types().
    """
    eof = sc.eof
    st = _DecState(dec)
    tmp = [CT_EMPTY] * ncol
    typ = list(tmp)
    jump0size = first_jump_end - pos
    njumps, sz = 1, eof - pos
    if jump0size > 0:
        if jump0size * 200 < sz:
            njumps = 100
        elif jump0size * 20 < sz:
            njumps = 10
    njumps += 1
    if (math.isfinite(nrow_limit) and nrow_limit > 0) or not all_jumps:
        njumps = 1
    last_row_end = pos
    for jump in range(njumps):
        if jump == 0:
            ch = pos
            if header:
                _, ch = sc.countfields(ch)
        else:
            if jump == njumps - 1:
                ch = eof - int(0.5 * jump0size)
            else:
                ch = pos + jump * ((eof - pos) // (njumps - 1))
            ch = sc.next_good_line(ch, ncol)
        ch = max(ch, last_row_end)
        if ch >= eof:
            break
        bumped = False
        st.votes = 0
        line = 0
        while ch < eof and line < jump_lines:
            line += 1
            nf, ch, b = _detect_types_line(sc, ch, ncol, tmp, st, white)
            bumped = bumped or b
            ok, e = sc.eol(ch)
            if (nf < ncol and ncol > 1) or (not ok and ch != eof):
                bumped = False
                if jump == 0:
                    last_row_end = eof
                break
            ch = e + 1 if ok else ch
            last_row_end = ch
            if jump == 0 and bumped:
                typ = list(tmp)
                bumped = False
        if not st.dec:
            st.dec = "," if st.votes < 0 else "."
            st.auto = False
        if bumped:
            typ = list(tmp)
    return typ, tmp, st.dec or "."


_VALID_CACHE: dict[tuple[str, str, str, int], bool] = {}


def _valid_cached(white: str, dec: str, span: str, t: int) -> bool:
    key = (white, dec, span, t)
    v = _VALID_CACHE.get(key)
    if v is None:
        if len(_VALID_CACHE) > 200_000:
            _VALID_CACHE.clear()
        v = _VALID_CACHE[key] = _grammar(white, dec).valid(span, t)
    return v


def _detect_types_line(
    sc: _Scanner, ch: int, ncol: int, tmp: list[int], st: _DecState, white: str
) -> tuple[int, int, bool]:
    """fread's detect_types() for one line: ``(fields, position, bumped)``."""
    buf = sc.buf
    start = ch
    ch = sc.skip_white(ch)
    if sc.eol(ch)[0]:
        return 0, start, False
    auto = st.auto
    bumped = False
    field = 0
    while field < ncol:
        ch = sc.skip_white(ch)
        field_start = ch
        _off, _n, fend = sc.field(field_start)
        span = buf[field_start:fend].decode("latin-1") if sc.end_of_field(fend) else None
        t = tmp[field]
        trial = ""
        while True:
            if auto and t in _DEC_TYPES and not trial:
                trial = "."
            if span is not None and _valid_cached(white, trial or st.dec or ".", span, t):
                break
            if t == CT_STRING:
                break
            if auto and t in _DEC_TYPES:
                if trial == ".":
                    trial = ","
                    continue
                trial = ""
            t = _next_type(t)
            bumped = True
        tmp[field] = t
        if auto and trial and t in _DEC_TYPES:
            st.votes += 1 if trial == "." else -1
        field += 1
        if span is None:
            ch = field_start
            break
        ch = fend
        if buf[ch] != sc.sep or field == ncol:
            break
        ch += 1
    return field, ch, bumped


class _Rows:
    """Tokenized data rows, column-major: raw field spans and Field() values."""

    def __init__(self, ncol: int) -> None:
        self.spans: list[list[bytes]] = [[] for _ in range(ncol)]
        self.vals: list[list[bytes | None]] = [[] for _ in range(ncol)]
        self.n = 0
        self.stop: int | None = None

    def add(self, buf: bytes, fields: list[tuple[int, int, int, int]]) -> None:
        for j, (s0, s1, off, n) in enumerate(fields):
            self.spans[j].append(buf[s0:s1])
            self.vals[j].append(None if n < 0 else buf[off : off + n])
        self.n += 1

    def truncate(self, n: int) -> None:
        for j in range(len(self.spans)):
            del self.spans[j][n:]
            del self.vals[j][n:]
        self.n = min(self.n, n)


def _fast_rows(sc: _Scanner, ch: int, ncol: int, rows: _Rows, limit: float) -> int:
    """Split quote-free, NUL-free ``\\n`` / ``\\r\\n`` data with bytes.split().

    Adds whole rows until one does not have *ncol* fields and returns where the
    general tokenizer must take over (eof when everything was read).
    """
    buf, eof = sc.buf, sc.eof
    region = buf[ch:eof]
    crlf = b"\r" in region
    lines = region.split(b"\n")
    sep = sc.sep_b
    spans, vals = rows.spans, rows.vals
    pos = ch
    for line in lines:
        if rows.n >= limit or pos >= eof:
            break
        body = line.rstrip(b"\r") if crlf else line
        parts = body.split(sep)
        if len(parts) != ncol:
            return pos
        for j, part in enumerate(parts):
            spans[j].append(part)
            v = part.strip(b" ")
            vals[j].append(None if v == _NA_STRING else v)
        rows.n += 1
        pos += len(line) + 1
    return min(pos, eof)


def _read_rows(
    sc: _Scanner,
    start: int,
    ncol: int,
    nrow_limit: float,
    first_is_string: Callable[[_Rows], bool],
) -> _Rows:
    """Tokenize the data rows as fread's read loop does.

    A row without exactly *ncol* fields makes fread retry it with the next,
    more lenient quote rule, and stop early (``rows.stop``) once none is left.
    *first_is_string* tells whether the first column is character by then: a
    whitespace-only last line ends the data silently unless it is (the first
    column's parser then consumes the blanks).
    """
    rows = _Rows(ncol)
    ch = start
    buf, eof = sc.buf, sc.eof
    region = buf[ch:eof]
    if (
        ncol > 1
        and not sc.eol_one_r
        and b'"' not in region
        and b"\0" not in region
        and region.count(b"\r") == region.count(b"\r\n")
    ):
        ch = _fast_rows(sc, ch, ncol, rows, nrow_limit)
    while ch < eof and rows.n < nrow_limit:
        if sc.skip_white(ch) >= eof and not first_is_string(rows):
            break
        row = sc.read_row(ch, ncol)
        if row is None:
            if sc.rule < 3:
                sc.rule += 1
                continue
            rows.stop = ch
            break
        fields, ch = row
        rows.add(buf, fields)
    return rows


def _warn_stopped(sc: _Scanner, ch: int, ncol: int, nrow: int) -> None:
    buf, eof = sc.buf, sc.eof
    k = ch
    while k < eof and buf[k] in _ISSPACE:
        k += 1
    if k >= eof:
        return
    line_end = k
    while line_end < eof and buf[line_end] not in (_LF, _CR):
        line_end += 1
    text = _decode(buf[k:line_end])[:500]
    rest = line_end
    while rest < eof and buf[rest] in _ISSPACE:
        rest += 1
    if rest >= eof:
        warnings.warn(f"Discarded single-line footer: <<{text}>>", stacklevel=3)
    else:
        tt, _ = sc.countfields(ch)
        warnings.warn(
            f"Stopped early on line {nrow + 1}. Expected {ncol} fields but found {tt}. "
            f"Consider fill=TRUE. First discarded non-empty line: <<{text}>>",
            stacklevel=3,
        )


def _column(t: int, rows: _Rows, j: int, gram: _Grammar) -> pd.Series:
    if t == CT_STRING:
        raw = rows.vals[j]
        text = {b: _decode(b) for b in dict.fromkeys(raw) if b is not None}
        return pd.Series([None if b is None else text[b] for b in raw], dtype="string")
    spans = rows.spans[j]
    t2, lookup = _climb_values(gram, spans, t)
    if t2 != t or lookup is None:  # pragma: no cover - t was climbed on these spans
        raise FreadError("internal error: column type changed while building")
    values = [lookup[b] for b in spans]
    if t in (CT_EMPTY, CT_BOOL8_U, CT_BOOL8_T, CT_BOOL8_L):
        return pd.Series(values, dtype="boolean")
    if t in (CT_INT32, CT_INT64):
        return pd.Series(values, dtype="Int64")
    if t in (CT_FLOAT64, CT_FLOAT64_EXT, CT_FLOAT64_HEX):
        return pd.Series([math.nan if v is None else v for v in values], dtype="float64")
    if t == CT_ISO8601_DATE:
        return pd.Series(values, dtype=object)
    # CT_ISO8601_TIME: POSIXct in UTC
    secs = pd.Series([math.nan if v is None else v for v in values], dtype="float64")
    return pd.to_datetime(secs, unit="s", utc=True)
