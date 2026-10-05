"""Delimited text files: pandas' C parser, then R's column types.

metacheck reads a data file with ``data.table::fread()`` and falls back to
``utils::read.delim()``. pytacheck lets pandas' C tokenizer split the text (every
field a string) and gives each column the type ``fread`` would have given it:
logical, integer (``integer64`` beyond 32 bits), double, ``IDate``, ``POSIXct`` or
character. What it keeps of ``fread``'s way of finding the table: lines before the
first block of lines with one number of fields are skipped, reading stops at the
first line with another number of fields (a footer, a ragged line, a blank line),
blanks around an unquoted field are dropped, ``NA`` is missing (text when quoted, in a
text column), and every quote is read where it stands: one opens a field only at the start
of one and closes it at the first quote that a separator or a line end follows, any other
(``"a"b``, or one that never closes) is an ordinary character. Where such a quote ends the
table, the file is also read as pandas reads it (the field closes there and the text after it
joins it), and that reading is kept if it has more rows at the same width.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import os
import re
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from metacheck.datacheck._colattrs import ColAttrs
from metacheck.datacheck._files_time import posixct_series
from metacheck.datacheck._strings import RAW_STRING

__all__ = ["read_delim"]

_SAMPLE_LINES = 100  # fread looks at this many lines to find the table
_NO_SEP = "\x7f"  # the separator of a single-column table: none
_QUOTED_NA = "\x1aNA"  # a quoted NA: missing in a number, text in a text column
_NUL = "\x1c"  # a NUL byte
_STRAY = "\x1d"  # a quote that is an ordinary character, while pandas reads the text
_NONE = np.empty(0, np.intp)
_PREFIX = 1 << 16  # the bytes a head scans first
_INT32_MAX = 2**31 - 1

_LOGICAL = (("TRUE", "FALSE"), ("True", "False"), ("true", "false"))
_BOOLS = frozenset(w for pair in _LOGICAL for w in pair)
_INTEGER = re.compile(r"[+-]?[0-9]+")


def _double_grammar(dec: str) -> re.Pattern[str]:
    """What ``fread`` reads as a double, with *dec* as the decimal mark."""
    d = re.escape(dec)
    return re.compile(
        rf"[+-]?(?:[0-9]+{d}[0-9]*|{d}[0-9]+|[0-9]+(?=[eE])|0*[0-9]{{1,18}})(?:[eE][+-]?[0-9]{{1,3}})?"
        rf"|[+-]?(?:nan|NaN|NAN|inf|INF|Inf|Infinity|1{d}#(?:SNAN|QNAN|IND|INF)"
        r"|#DIV/0!|#VALUE!|#NULL!|#NAME\?|#NUM!|#REF!|#N/A"
        r"|0[xX][01](?:\.[0-9a-fA-F]{0,13})?[pP][+-]?[0-9]{1,4})"  # the hexadecimal form of C's %a
    )


_DOUBLE = {".": _double_grammar("."), ",": _double_grammar(",")}
_DATE = re.compile(r"[0-9]+-[0-9]+-[0-9]+")
_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:Z|[+-][0-9]{2}(?::?[0-9]{2})?)?)?"
)


# -----------------------------------------------------------------------------
# Finding the table
# -----------------------------------------------------------------------------


class _Quotes(NamedTuple):
    outside: np.ndarray  # which bytes lie outside quoted fields
    plain: np.ndarray  # the quotes that are ordinary characters, though pandas would not think so
    odd: np.ndarray  # those that stand where a field opens (``"a"b``)
    unclosed: int  # where the first of them has no quote to close it (``len(buf)``: none)


def _quotes(buf: np.ndarray, sep: int, escape: bool, loose: bool) -> _Quotes:
    """Which bytes of *buf* lie outside a quoted field and which quotes are ordinary characters.

    A quote opens a field only at the start of one (blanks apart).  The field closes at the
    first quote that is not half of a doubled one (``""``), and only if a separator or a line
    end follows it; otherwise (``"a"b``, or no such quote at all) the opening quote is an
    ordinary character, and so is every quote inside a field.  With *escape* a quote after an
    odd number of backslashes is an ordinary character too.  With *loose* the field closes at
    that quote whatever follows it, as pandas reads it, and the text after it joins the field.
    """
    quote = np.flatnonzero(buf == 34)
    plain: list[int] = []
    odd: list[int] = []
    if escape:
        for q in quote[buf[np.maximum(quote - 1, 0)] == 92].tolist():
            run = 1
            while q - run - 1 >= 0 and buf[q - run - 1] == 92:
                run += 1
            if run % 2:
                plain.append(q)
        quote = np.setdiff1d(quote, plain)
    last = len(buf) - 1
    before, after = buf[np.maximum(quote - 1, 0)], buf[np.minimum(quote + 1, last)]
    start = (quote == 0) | np.isin(before, (sep, 10, 13))
    end = (quote == last) | np.isin(after, (sep, 10, 13))
    for k in np.flatnonzero(before == 32).tolist():  # blanks before the quote
        j = int(quote[k])
        while j > 0 and buf[j - 1] == 32:
            j -= 1
        start[k] = j == 0 or buf[j - 1] in (sep, 10, 13)
    for k in np.flatnonzero((after == 32) & (quote < last)).tolist():  # blanks after it
        j = int(quote[k]) + 1
        while j <= last and buf[j] == 32:
            j += 1
        end[k] = j > last or buf[j] in (sep, 10, 13)
    toggle = np.zeros(len(buf), bool)
    if (
        len(quote) % 2 == 0  # a regular file: quotes in pairs, as pandas reads them
        and (start | (before == 34))[0::2].all()
        and (end | (after == 34))[1::2].all()
    ):
        toggle[quote] = True
        return _Quotes(
            ~np.bitwise_xor.accumulate(toggle), np.array(plain, dtype=np.intp), _NONE, len(buf)
        )
    pos, opens, closes = quote.tolist(), start.tolist(), end.tolist()
    marks: list[int] = []
    i, unclosed = 0, len(buf)
    while i < len(pos):
        if not opens[i]:
            i += 1
            continue
        j = i + 1
        while j + 1 < len(pos) and pos[j + 1] == pos[j] + 1:  # a doubled quote
            j += 2
        if j < len(pos) and (loose or closes[j]):
            marks += (pos[i], pos[j])
            i = j + 1
        else:
            odd.append(pos[i])
            if j >= len(pos):
                unclosed = min(unclosed, pos[i])
            i += 1
    toggle[marks] = True
    return _Quotes(
        ~np.bitwise_xor.accumulate(toggle),
        np.sort(np.array(plain + odd, dtype=np.intp)),
        np.array(odd, dtype=np.intp),
        unclosed,
    )


def _lines(
    buf: np.ndarray, sep: int, escape: bool, loose: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, _Quotes]:
    """Where each line of *buf* starts, how many fields it has, whether it is blank, and its
    quotes.  A separator or a line end inside a quoted field does not count.
    """
    quotes = _quotes(buf, sep, escape, loose)
    outside = quotes.outside
    after = np.append(buf[1:], 0)
    eol = ((buf == 10) | ((buf == 13) & (after != 10))) & outside
    starts = np.append(0, np.flatnonzero(eol) + 1)
    starts = starts[starts < len(buf)]
    seps = np.add.reduceat(((buf == sep) & outside).view(np.uint8), starts, dtype=np.int64)
    space = np.zeros(256, bool)
    space[[10, 13, 32] + ([9] if sep != 9 else [])] = True
    text = np.add.reduceat((~space[buf]).view(np.uint8), starts, dtype=np.int64)
    return starts, seps + 1, text == 0, quotes


def _table(counts: np.ndarray, blank: np.ndarray, jump: int) -> tuple[int, int]:
    """``(line where the table starts, fields)``, as ``fread`` finds them in its first *jump* lines.

    The table is the first block of two or more lines with one number of fields; a blank line
    ends a block of one line only by restarting it after the blank line.  A file whose block has
    one field is a single column, and starts at its first line.
    """
    n = len(counts)
    first = int(np.argmax(~blank))
    start, run, last = first, 1, int(counts[first])
    k = first + 1
    while k < n and k - first < jump:
        if not blank[k] and counts[k] == last:
            run += 1
            k += 1
            continue
        if last > 1 and run > 1:
            break
        while k < n and blank[k]:
            k += 1
        if k < n:
            start, run, last = k, 1, int(counts[k])
            k += 1
    return (first if last == 1 else start), last


class _Plan(NamedTuple):
    """The lines of a file that hold its table: ``first`` to ``last``, ``ncol`` fields wide."""

    starts: np.ndarray  # where each line starts
    outside: np.ndarray  # which bytes lie outside quotes
    plain: np.ndarray  # the quotes that are ordinary characters
    first: int
    last: int
    ncol: int
    single: bool  # one column: the separator is part of the value, a blank line a row
    rows: int  # lines that hold a row
    bad: int  # odd quotes up to the end of the table, and in the line that ends it (``"a"b``)
    complete: bool  # the lines beyond the scanned ones cannot change this plan


def _plan(
    buf: np.ndarray,
    sep: int,
    header: bool,
    nrows: float,
    jump: int,
    fill: bool,
    escape: bool = False,
    loose: bool = False,
) -> _Plan | None:
    starts, counts, blank, quotes = _lines(buf, sep, escape, loose)
    if blank.all():
        return None
    n = len(starts)
    if fill:  # from the first line, as wide as the widest line read
        first, ncol = int(np.argmax(~blank)), 0
    else:
        first, ncol = _table(counts, blank, jump)
    single = ncol == 1
    last, ended = n, False
    if single and blank[-1] and buf[-1] not in (10, 13):  # blanks after the final newline go
        last = n - 1
    if not (single or fill):  # a line of another width, or a blank one, ends the table
        end = n - int(np.argmax(~blank[::-1]))
        bad = (counts != ncol)[first:end] | blank[first:end]
        last = first + (int(bad.argmax()) if bad.any() else end - first)
        ended = bool(bad.any()) and last < n - 1
    rows = np.flatnonzero(np.ones(last - first, bool) if single else ~blank[first:last])
    limit = header + nrows  # lines to read: the header and *nrows* data rows
    truncated = len(rows) > limit
    if truncated:
        last = first + (int(rows[int(limit) - 1]) + 1 if limit else 0)
        rows = rows[: int(limit)]
    if fill:
        ncol = int(counts[first + rows].max()) if len(rows) else 1
    top = starts[last + 1] if last + 1 < n else len(buf)  # the line that ends the table too
    complete = (truncated or ended) and first + jump < n - 1 and quotes.unclosed >= top
    n_bad = int(np.count_nonzero(quotes.odd < top))
    return _Plan(
        starts, quotes.outside, quotes.plain, first, last, ncol, single, len(rows), n_bad, complete
    )


# -----------------------------------------------------------------------------
# Reading it
# -----------------------------------------------------------------------------


def _unpadded(chunk: np.ndarray, outside: np.ndarray, sep: int, tabs: bool) -> bytes:
    """*chunk* without the blanks around its fields, except in a quoted one (``strip.white``);
    a tab is a blank unless it separates fields (*tabs*)."""
    blank = (chunk == 32) | ((chunk == 9) & tabs)
    pos = np.flatnonzero(blank & outside)
    solid = np.flatnonzero(~blank)
    if not len(pos) or not len(solid):
        return chunk.tobytes()
    edge = np.isin(chunk, (sep, 10, 13))
    k = np.searchsorted(solid, pos)  # the solid bytes on both sides of each blank
    at_start = (k == 0) | edge[solid[np.maximum(k - 1, 0)]]
    at_end = (k == len(solid)) | edge[solid[np.minimum(k, len(solid) - 1)]]
    return np.delete(chunk, pos[at_start | at_end]).tobytes()


def _read_strings(chunk: bytes, sep: str, ncol: int, latin1: bool, blanks: bool) -> pd.DataFrame:
    try:
        return pd.read_csv(
            io.BytesIO(chunk),
            sep=sep,
            header=None,
            names=range(ncol),
            dtype=object,
            keep_default_na=False,
            na_values=[],
            engine="c",
            encoding="latin-1" if latin1 else "utf-8",
            encoding_errors="surrogateescape",
            quoting=csv.QUOTE_MINIMAL,
            skip_blank_lines=not blanks,
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=range(ncol), dtype=object)


def _read_plan(
    buf: np.ndarray, plan: _Plan, sep: str, fill: bool, latin1: bool
) -> tuple[pd.DataFrame, int, bool] | None:
    """The table of *plan* as strings, its width, and whether *buf* held all it needed;
    ``None`` if pandas does not find the rows the plan did."""
    starts, outside, plain, first, last, ncol, single, rows, _, complete = plan
    lo, hi = starts[first], starts[last] if last < len(starts) else len(buf)
    piece = buf[lo:hi]
    own = plain[(plain >= lo) & (plain < hi)] - lo
    if len(own):  # pandas must not take these quotes for quoting
        piece = piece.copy()
        piece[own] = ord(_STRAY)
    chunk = _unpadded(
        piece, outside[lo:hi], ord(_NO_SEP if single else sep), not single and sep != "\t"
    )
    if b"\r" in chunk:  # pandas' parser overflows on some lines that end in a lone CR
        chunk = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not fill:  # a quoted NA is text in a text column: mark it, pandas cannot tell
        around = b"[^" + re.escape(sep.encode()) + b"\r\n]"
        chunk = re.sub(rb'(?<!%b)"NA"(?!%b)' % (around, around), b'"\x1aNA"', chunk)
    try:
        raw = _read_strings(chunk, _NO_SEP if single else sep, ncol, latin1, single and not fill)
    except pd.errors.ParserError:
        return None
    if len(raw) != rows:
        return None
    if len(own):
        raw = raw.apply(lambda col: col.str.replace(_STRAY, '"', regex=False))
    return raw, ncol, complete


def _split(
    buf: np.ndarray, sep: str, header: bool, nrows: float, jump: int, fill: bool, latin1: bool
) -> tuple[pd.DataFrame, int, bool] | None:
    """The table in *buf* as strings, its width, and whether *buf* held all it needed;
    ``None`` for a blank *buf*."""
    args = (header, nrows, jump, fill)
    plan = _plan(buf, ord(sep), *args)
    if plan is None:
        return None
    plans = [plan]
    if plan.bad:  # a quote that is not CSV quoting: read the file other ways too
        if ((buf[:-1] == 92) & (buf[1:] == 34)).any():  # a backslash escapes a quote
            escaped = _plan(buf, ord(sep), *args, escape=True)
            plans += [escaped] if escaped is not None else []
        closed = _plan(buf, ord(sep), *args, loose=True)  # a quoted field with text after it
        if closed is not None and closed.ncol == plan.ncol:  # the same table, read further
            plans.append(closed._replace(bad=plan.bad))  # a tie keeps the quotes ordinary
    for each in sorted(plans, key=lambda p: (-p.rows, p.bad)):  # most rows, then fewest odd quotes
        found = _read_plan(buf, each, sep, fill, latin1)
        if found is not None:
            return found
    raise ValueError("cannot tokenize the file")


def read_delim(
    path: str | os.PathLike[str],
    sep: str,
    header: bool,
    nrows: float = math.inf,
    encoding: str | None = None,
    fill: bool = False,
) -> pd.DataFrame:
    """Read a delimited file: a data frame of typed columns.

    *nrows* counts data rows (not the header row). ``encoding="latin1"`` reads the
    bytes as Latin-1, otherwise they are UTF-8 with undecodable bytes kept as lone
    surrogates (``errors="surrogateescape"``), which ``_utf8_repair_df()`` repairs.
    An empty file is an empty frame.  With *fill* the table starts at the first line,
    has as many columns as the widest line read, and a short line is padded
    (``read.delim()``'s way) instead of ending the table.  A head (finite *nrows*) scans
    a prefix of the file, as much of it as the head needs.
    """
    data = Path(path).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16, which R reads as bytes
        data = data.decode("utf-16", errors="replace").encode("utf-8")
    elif data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    nul = b"\0" in data
    if nul:  # a name ends at a NUL byte, a value loses it
        if not data.strip(b"\0"):
            raise ValueError("empty beginning of file")
        data = data.replace(b"\0", _NUL.encode())
    jump = _SAMPLE_LINES if not 0 < nrows < _SAMPLE_LINES else int(nrows)
    size = _PREFIX if math.isfinite(nrows) else len(data)
    while True:
        buf = np.frombuffer(data, np.uint8, count=min(size, len(data)))
        try:
            found = _split(buf, sep, header, nrows, jump, fill, encoding == "latin1")
        except ValueError:
            if size >= len(data):
                raise
            found = None
        if size >= len(data) or (found is not None and found[2]):
            break
        size *= 16
    if found is None:
        return pd.DataFrame()
    raw, ncol, _ = found
    if header:
        given = [str(v).replace(_QUOTED_NA, "NA").split(_NUL)[0] for v in raw.iloc[0]]
        names = [nm if nm not in ("", "NA") else f"V{j + 1}" for j, nm in enumerate(given)]
        raw = raw.iloc[1:]
    else:
        names = [f"V{j + 1}" for j in range(ncol)]
    if nul:  # values lose their NUL bytes
        raw = raw.apply(lambda col: col.str.replace(_NUL, "", regex=False))
    dec = "." if sep == "," else _decimal_mark(raw, jump)
    return _typed(raw, names, dec)


# -----------------------------------------------------------------------------
# Typing the columns
# -----------------------------------------------------------------------------


def _decimal_mark(raw: pd.DataFrame, jump: int) -> str:
    """``fread``'s vote on ``dec="auto"``: a comma if more numbers in the first rows spell
    one with a comma than with a point (a number's first value that is no integer, and every
    value after it, votes)."""
    votes = 0
    for col in raw.iloc[:jump].T.to_numpy():
        numeric = False
        for v in col:
            if v in ("", "NA"):
                continue
            if _DOUBLE["."].fullmatch(v):
                if numeric or not _INTEGER.fullmatch(v):
                    votes += 1
                    numeric = True
            elif _DOUBLE[","].fullmatch(v):
                votes -= 1
                numeric = True
            elif not (_INTEGER.fullmatch(v) or v in _BOOLS):
                break
    return "," if votes < 0 else "."


def _typed(raw: pd.DataFrame, names: list[str], dec: str) -> pd.DataFrame:
    """The columns of *raw* (strings) as R types them; ``df.attrs["col_attrs"]`` the classes."""
    cells = raw.to_numpy()
    columns: dict[int, pd.Series] = {}
    classes: list[dict[str, Any] | None] = []
    for j in range(cells.shape[1]):
        columns[j], cls = _typed_column(cells[:, j], dec)
        classes.append(cls)
    out = pd.DataFrame(columns, index=range(len(raw)))
    out.columns = pd.Index(names, dtype=object)
    attrs = ColAttrs(names, classes)
    if attrs.any():
        out.attrs["col_attrs"] = attrs
    return out


def _typed_column(cells: np.ndarray, dec: str) -> tuple[pd.Series, dict[str, Any] | None]:
    """One column of strings as the lowest of R's types that reads all of it.

    The type is decided on the distinct values, which a survey column has few of."""
    quoted_na = cells == _QUOTED_NA
    gap = quoted_na | (cells == "NA") | (cells == "")
    values = cells[~gap]
    if not len(values):
        return pd.Series([None] * len(cells), dtype="boolean"), None
    kinds = pd.unique(values)

    def spread(found: Any, dtype: Any) -> pd.Series:
        out = np.full(len(cells), None, dtype=object)
        out[~gap] = found
        typed: pd.Series = pd.Series(out, dtype=dtype)
        return typed

    for yes, no in _LOGICAL:
        if set(kinds) <= {yes, no}:
            return spread(values == yes, "boolean"), None
    if _all_match(kinds, _INTEGER):
        try:
            ints = values.astype("int64")
        except OverflowError:
            ints = None
        if ints is not None:
            big = bool(np.abs(ints).max() > _INT32_MAX)
            return spread(ints, "Int64"), ({"class": "integer64"} if big else None)
    if _all_match(kinds, _DOUBLE[dec]):
        found = _doubles(values, kinds, dec)
        if found is not None:
            nums = np.full(len(cells), np.nan)
            nums[~gap] = found
            return pd.Series(nums), None
    if _all_match(kinds, _DATE):
        try:
            days = {v: dt.date(*map(int, v.split("-"))) for v in kinds}
        except ValueError:
            days = None
        if days is not None:
            return spread([days[v] for v in values], object), {"class": ["IDate", "Date"]}
    if _all_match(kinds, _TIME):
        try:
            stamps = {v: dt.datetime.fromisoformat(v.replace("Z", "+00:00")) for v in kinds}
        except ValueError:
            stamps = None
        if stamps is not None:
            seconds = {
                v: (t if t.tzinfo else t.replace(tzinfo=dt.UTC)).timestamp()
                for v, t in stamps.items()
            }
            full = np.full(len(cells), np.nan)
            full[~gap] = [seconds[v] for v in values]
            return posixct_series(full), {"class": ["POSIXct", "POSIXt"], "tzone": "UTC"}
    text = cells.copy()
    text[cells == "NA"] = None
    text[quoted_na] = "NA"
    return pd.Series(text, dtype=RAW_STRING), None


def _doubles(values: np.ndarray, kinds: np.ndarray, dec: str) -> np.ndarray | None:
    """The doubles *values* spell (Excel's ``#N/A`` and ``#DIV/0!`` are not a number); ``None``
    when one is beyond a double (``1e400``).  *kinds* are the distinct *values*."""
    joined = "\0".join(kinds)
    text = values
    if dec == ",":
        text = np.array([v.replace(",", ".") for v in text], dtype=object)
    if "#" in joined:
        text = np.array([_excel_special(v) for v in text], dtype=object)
    if "x" in joined or "X" in joined:
        nums = np.array([float.fromhex(v) if "x" in v.lower() else float(v) for v in text])
    else:
        nums = text.astype("float64")
    inf = np.isinf(nums)
    if inf.any() and not all(re.search("inf|Inf|INF", v) for v in text[inf]):
        return None
    return nums


def _excel_special(v: str) -> str:
    """``1.#INF`` is infinity and the other ``#`` codes are not a number."""
    if "#" not in v:
        return v
    if "1.#INF" in v:
        return "-inf" if v.startswith("-") else "inf"
    return "nan"


def _all_match(values: np.ndarray, pattern: re.Pattern[str]) -> bool:
    return all(map(pattern.fullmatch, values))
