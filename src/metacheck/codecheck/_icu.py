"""A pure-Python port of ICU's charset detector (``ucsdet``).

metacheck's ``code_read()`` guesses a file's encoding with
``readr::guess_encoding()``, which calls ``stringi::stri_enc_detect()``, which
runs ICU's ``CharsetDetector::detectAll()``. This module reproduces that
detector recognizer by recognizer (ICU 78: ``csdetect.cpp``, ``inputext.cpp``,
``csrutf8.cpp``, ``csrucode.cpp``, ``csrsbcs.cpp``, ``csrmbcs.cpp``,
``csr2022.cpp``), including its quirks:

* every recognizer runs, including the IBM420/IBM424 ones ICU documents as
  disabled by default (``detectAll()`` never consults the enabled flags);
* the single-byte recognizers look at the first 8192 bytes only, the UTF-8,
  UTF-16/32 and multi-byte (CJK) recognizers at the whole input;
* results are stably sorted by decreasing confidence, so ties keep the
  recognizer order (UTF-8, UTF-16BE, UTF-16LE, UTF-32BE, UTF-32LE, ISO-8859-1,
  ...).

The n-gram and common-character tables live in :mod:`._icu_tables`.
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable, Sequence
from typing import Literal

from metacheck.codecheck import _icu_tables as T

__all__ = ["detect_all"]

_BUFFER_SIZE = 8192

Match = tuple[str, str, int]  # (charset name, language, confidence 0-100)


class _Input:
    """ICU's ``InputText`` after ``MungeInput()`` (tag stripping is off in stringi)."""

    __slots__ = ("c1_bytes", "input", "raw")

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.input = raw[:_BUFFER_SIZE]
        self.c1_bytes = any(0x80 <= b <= 0x9F for b in set(self.input))


# ---------------------------------------------------------------------------
# UTF-8, UTF-16, UTF-32 (csrutf8.cpp, csrucode.cpp)
# ---------------------------------------------------------------------------

_HIGH = re.compile(rb"[\x80-\xff]")


def _utf8(det: _Input) -> Match | None:
    data = det.raw
    n = len(data)
    has_bom = n >= 3 and data[:3] == b"\xef\xbb\xbf"
    valid = invalid = 0
    i = 0
    search = _HIGH.search
    while True:
        m = search(data, i)
        if m is None:
            break
        i = m.start()
        b = data[i]
        if (b & 0xE0) == 0xC0:
            trail = 1
        elif (b & 0xF0) == 0xE0:
            trail = 2
        elif (b & 0xF8) == 0xF0:
            trail = 3
        else:
            invalid += 1
            i += 1
            continue
        while True:
            i += 1
            if i >= n:
                break
            if (data[i] & 0xC0) != 0x80:
                invalid += 1
                break
            trail -= 1
            if trail == 0:
                valid += 1
                break
        i += 1
    if has_bom and invalid == 0:
        conf = 100
    elif has_bom and valid > invalid * 10:
        conf = 80
    elif valid > 3 and invalid == 0:
        conf = 100
    elif valid > 0 and invalid == 0:
        conf = 80
    elif valid == 0 and invalid == 0:
        conf = 15
    elif valid > invalid * 10:
        conf = 25
    else:
        conf = 0
    return ("UTF-8", "", conf) if conf > 0 else None


def _adjust16(unit: int, conf: int) -> int:
    if unit == 0:
        conf -= 10
    elif 0x20 <= unit <= 0xFF or unit == 0x0A:
        conf += 10
    return min(max(conf, 0), 100)


def _utf16(det: _Input, big_endian: bool) -> Match | None:
    data = det.raw
    length = len(data)
    conf = 10
    n = min(length, 30)
    for i in range(0, n - 1, 2):
        unit = (data[i] << 8 | data[i + 1]) if big_endian else (data[i] | data[i + 1] << 8)
        if i == 0 and unit == 0xFEFF:
            conf = 100
            if not big_endian and length >= 4 and data[2] == 0 and data[3] == 0:
                conf = 0  # a UTF-32LE BOM
            break
        conf = _adjust16(unit, conf)
        if conf in (0, 100):
            break
    if n < 4 and conf < 100:
        conf = 0
    if conf <= 0:
        return None
    return ("UTF-16BE" if big_endian else "UTF-16LE", "", conf)


def _utf32(det: _Input, big_endian: bool) -> Match | None:
    data = det.raw
    limit = (len(data) // 4) * 4
    order: Literal["big", "little"] = "big" if big_endian else "little"
    valid = invalid = 0
    has_bom = False

    def char(i: int) -> int:
        v = int.from_bytes(data[i : i + 4], order)
        return v - (1 << 32) if v >= 1 << 31 else v  # int32_t

    if limit > 0 and char(0) == 0xFEFF:
        has_bom = True
    for i in range(0, limit, 4):
        ch = char(i)
        if ch < 0 or ch >= 0x10FFFF or 0xD800 <= ch <= 0xDFFF:
            invalid += 1
        else:
            valid += 1
    if has_bom and invalid == 0:
        conf = 100
    elif has_bom and valid > invalid * 10:
        conf = 80
    elif valid > 3 and invalid == 0:
        conf = 100
    elif valid > 0 and invalid == 0:
        conf = 80
    elif valid > invalid * 10:
        conf = 25
    else:
        conf = 0
    if conf <= 0:
        return None
    return ("UTF-32BE" if big_endian else "UTF-32LE", "", conf)


# ---------------------------------------------------------------------------
# Single-byte n-gram recognizers (csrsbcs.cpp)
# ---------------------------------------------------------------------------


def _found_set(table: Sequence[int]) -> frozenset[int]:
    """Values ICU's fixed 64-entry binary search actually finds in *table*.

    ``NGramParser::search()`` assumes a sorted table; computing the found set
    with the very same probe sequence keeps any unsorted entry unfindable,
    exactly as in ICU.
    """

    def search(value: int) -> bool:
        index = 0
        for step in (32, 16, 8, 4, 2, 1):
            if table[index + step] <= value:
                index += step
        if table[index] > value:
            index -= 1
        return index >= 0 and table[index] == value

    return frozenset(v for v in table if search(v))


@functools.cache
def _tables() -> dict[str, frozenset[int]]:
    out: dict[str, frozenset[int]] = {}
    for name in dir(T):
        if name.startswith("NGRAMS_"):
            val = getattr(T, name)
            if isinstance(val[0], tuple):  # NGramsPlusLang
                for lang, grams in val:
                    out[f"{name}:{lang}"] = _found_set(grams)
            else:
                out[name] = _found_set(val)
    return out


_SPACE_RUN = re.compile(rb"  +")


def _ngram_stream(data: bytes, charmap: bytes) -> bytes:
    """The byte stream an ``NGramParser`` feeds to ``addByte()``."""
    mapped = data.translate(charmap).replace(b"\x00", b"")
    mapped = _SPACE_RUN.sub(b" ", mapped)
    return mapped + b" "  # parse() always adds a final 0x20


def _ibm420_stream(data: bytes, charmap: bytes) -> bytes:
    """``NGramParser_IBM420``: stops at NUL, un-shapes, splits lam-alef."""
    nul = data.find(b"\x00")
    if nul >= 0:
        data = data[:nul]
    unshape = T.UNSHAPEMAP_IBM420
    lamalef = {0xB2: 0x47, 0xB3: 0x47, 0xB4: 0x49, 0xB5: 0x49, 0xB8: 0x56, 0xB9: 0x56}
    out = bytearray()
    ignore_space = False
    for b in data:
        alef = lamalef.get(b, 0)
        nxt = 0xB1 if alef else unshape[b]
        for mb in (charmap[nxt], charmap[alef] if alef else None):
            if mb is None:
                continue
            if mb != 0:
                if not (mb == 0x20 and ignore_space):
                    out.append(mb)
                ignore_space = mb == 0x20
    out.append(0x20)
    return bytes(out)


def _ngram_counts(stream: bytes) -> tuple[int, dict[int, int]]:
    """Number of ``lookup()`` calls and the multiset of 3-byte n-grams."""
    padded = b"\x00\x00" + stream
    if len(stream) < 256:
        counts: dict[int, int] = {}
        for a, b, c in zip(padded, padded[1:], padded[2:], strict=False):
            g = (a << 16) | (b << 8) | c
            counts[g] = counts.get(g, 0) + 1
        return len(stream), counts
    import numpy as np

    arr = np.frombuffer(padded, dtype=np.uint8).astype(np.int32)
    grams = (arr[:-2] << 16) | (arr[1:-1] << 8) | arr[2:]
    values, freq = np.unique(grams, return_counts=True)
    return len(stream), dict(zip(values.tolist(), freq.tolist(), strict=True))


def _score(total: int, counts: dict[int, int], found: frozenset[int]) -> int:
    """``NGramParser::parse()``: the share of n-grams found in the table."""
    hits = sum(counts.get(v, 0) for v in found)
    raw_percent = hits / total
    if raw_percent > 0.33:
        return 98
    return int(raw_percent * 300.0)


def _sbcs(
    det: _Input,
    name: str | Callable[[_Input], str],
    lang_tables: Sequence[tuple[str, str]],
    charmap: bytes,
    streams: dict[int, tuple[int, dict[int, int]]],
    ibm420: bool = False,
) -> Match | None:
    key = id(charmap) * 2 + ibm420
    stats = streams.get(key)
    if stats is None:
        stream = (_ibm420_stream if ibm420 else _ngram_stream)(det.input, charmap)
        stats = streams[key] = _ngram_counts(stream)
    tables = _tables()
    best = -1
    best_lang = ""
    for lang, table in lang_tables:
        conf = _score(stats[0], stats[1], tables[table])
        if conf > best:
            best, best_lang = conf, lang
    if best <= 0:
        return None
    return (name(det) if callable(name) else name, best_lang, best)


def _c1(latin: str, windows: str) -> Callable[[_Input], str]:
    return lambda det: windows if det.c1_bytes else latin


# ---------------------------------------------------------------------------
# Multi-byte recognizers (csrmbcs.cpp)
# ---------------------------------------------------------------------------


def _binary_search(array: Sequence[int], value: int) -> bool:
    start, end = 0, len(array) - 1
    mid = (start + end) // 2
    while start <= end:
        if array[mid] == value:
            return True
        if array[mid] < value:
            start = mid + 1
        else:
            end = mid - 1
        mid = (start + end) // 2
    return False


def _next_sjis(data: bytes, i: int, n: int) -> tuple[int, int, bool]:
    first = data[i]
    i += 1
    if first <= 0x7F or 0xA0 < first <= 0xDF:
        return first, i, False
    second = data[i] if i < n else -1
    if second >= 0:
        i += 1
    value = (first << 8) | second if second >= 0 else first
    error = not (0x40 <= second <= 0x7F or 0x80 <= second <= 0xFE)
    return value, i, error


def _next_euc(data: bytes, i: int, n: int) -> tuple[int, int, bool]:
    first = data[i]
    i += 1
    if first <= 0x8D:
        return first, i, False
    second = data[i] if i < n else -1
    if second >= 0:
        i += 1
    value = (first << 8) | second if second >= 0 else first
    if 0xA1 <= first <= 0xFE:
        return value, i, second < 0xA1
    if first == 0x8E:
        return value, i, second < 0xA1
    if first == 0x8F:
        third = data[i] if i < n else -1
        if third >= 0:
            i += 1
        value = (value << 8) | third if third >= 0 else -1
        return value, i, third < 0xA1
    return value, i, False


def _next_big5(data: bytes, i: int, n: int) -> tuple[int, int, bool]:
    first = data[i]
    i += 1
    if first <= 0x7F or first == 0xFF:
        return first, i, False
    second = data[i] if i < n else -1
    if second >= 0:
        i += 1
    value = (first << 8) | second if second >= 0 else first
    return value, i, second < 0x40 or second in (0x7F, 0xFF)


def _next_gb18030(data: bytes, i: int, n: int) -> tuple[int, int, bool]:
    first = data[i]
    i += 1
    if first <= 0x80:
        return first, i, False
    second = data[i] if i < n else -1
    if second >= 0:
        i += 1
    value = (first << 8) | second if second >= 0 else first
    if 0x81 <= first <= 0xFE:
        # ICU writes "secondByte >= 80" (decimal) here: reproduced.
        if 0x40 <= second <= 0x7E or 80 <= second <= 0xFE:
            return value, i, False
        if 0x30 <= second <= 0x39:
            third = data[i] if i < n else -1
            if third >= 0:
                i += 1
            if 0x81 <= third <= 0xFE:
                fourth = data[i] if i < n else -1
                if fourth >= 0:
                    i += 1
                if 0x30 <= fourth <= 0x39:
                    return (value << 16) | (third << 8) | fourth, i, False
        return value, i, True
    return value, i, False


# bytes a recognizer treats as single-byte characters without further look-ahead
_MBCS_SINGLE = {
    "sjis": re.compile(rb"[\x80-\xa0\xe0-\xff]"),
    "euc": re.compile(rb"[\x8e-\xff]"),
    "big5": re.compile(rb"[\x80-\xfe]"),
    "gb18030": re.compile(rb"[\x81-\xff]"),
}


def _mbcs(
    det: _Input,
    kind: str,
    next_char: Callable[[bytes, int, int], tuple[int, int, bool]],
    common: Sequence[int],
) -> int:
    data = det.raw
    n = len(data)
    double = common_count = bad = total = 0
    lead = _MBCS_SINGLE[kind].search
    i = 0
    while i < n:
        m = lead(data, i)
        if m is None:
            total += n - i
            break
        total += m.start() - i
        i = m.start()
        value, i, error = next_char(data, i, n)
        total += 1
        if error:
            bad += 1
        elif value > 0xFF:
            double += 1
            if _binary_search(common, value & 0xFFFF):
                common_count += 1
        if bad >= 2 and bad * 5 >= double:
            return 0
    if double <= 10 and bad == 0:
        if double == 0 and total < 10:
            return 0
        return 10
    if double < 20 * bad:
        return 0
    max_val = math.log(double / 4)
    scale = 90.0 / max_val
    conf = int(math.log(common_count + 1) * scale + 10.0)
    conf = min(conf, 100)
    return max(conf, 0)


# ---------------------------------------------------------------------------
# ISO-2022 (csr2022.cpp)
# ---------------------------------------------------------------------------

_ESC_2022JP = (
    b"\x1b$(C",
    b"\x1b$(D",
    b"\x1b$@",
    b"\x1b$A",
    b"\x1b$B",
    b"\x1b&@",
    b"\x1b(B",
    b"\x1b(H",
    b"\x1b(I",
    b"\x1b(J",
    b"\x1b.A",
    b"\x1b.F",
)
_ESC_2022KR = (b"\x1b$)C",)
_ESC_2022CN = (
    b"\x1b$)A",
    b"\x1b$)G",
    b"\x1b$*H",
    b"\x1b$)E",
    b"\x1b$+I",
    b"\x1b$+J",
    b"\x1b$+K",
    b"\x1b$+L",
    b"\x1b$+M",
    b"\x1bN",
    b"\x1bO",
)


def _c_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _match_2022(text: bytes, escapes: Sequence[bytes]) -> int:
    hits = misses = shifts = 0
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == 0x1B:
            for seq in escapes:
                if n - i >= len(seq) and text[i + 1 : i + len(seq)] == seq[1:]:
                    hits += 1
                    i += len(seq) - 1
                    break
            else:
                misses += 1
                if c in (0x0E, 0x0F):  # pragma: no cover - c is ESC here
                    shifts += 1
            i += 1
            continue
        if c in (0x0E, 0x0F):
            shifts += 1
        i += 1
    if hits == 0:
        return 0
    quality = _c_div(100 * hits - 100 * misses, hits + misses)
    if hits + shifts < 5:
        quality -= (5 - (hits + shifts)) * 10
    return max(quality, 0)


# ---------------------------------------------------------------------------
# detectAll()
# ---------------------------------------------------------------------------


def detect_all(raw: bytes) -> list[Match]:
    """``ucsdet_detectAll()``: every match with confidence > 0, best first.

    Returns ``(name, language, confidence)`` tuples; confidence is ICU's
    integer percentage (stringi reports ``confidence / 100``).
    """
    det = _Input(raw)
    streams: dict[int, tuple[int, dict[int, int]]] = {}
    results: list[Match] = []

    def add(m: Match | None) -> None:
        if m is not None and m[2] > 0:
            results.append(m)

    add(_utf8(det))
    add(_utf16(det, True))
    add(_utf16(det, False))
    add(_utf32(det, True))
    add(_utf32(det, False))

    l1 = [(lang, f"NGRAMS_8859_1:{lang}") for lang, _ in T.NGRAMS_8859_1]
    l2 = [(lang, f"NGRAMS_8859_2:{lang}") for lang, _ in T.NGRAMS_8859_2]
    add(_sbcs(det, _c1("ISO-8859-1", "windows-1252"), l1, T.CHARMAP_8859_1, streams))
    add(_sbcs(det, _c1("ISO-8859-2", "windows-1250"), l2, T.CHARMAP_8859_2, streams))
    add(_sbcs(det, "ISO-8859-5", [("ru", "NGRAMS_8859_5_RU")], T.CHARMAP_8859_5, streams))
    add(_sbcs(det, "ISO-8859-6", [("ar", "NGRAMS_8859_6_AR")], T.CHARMAP_8859_6, streams))
    add(
        _sbcs(
            det,
            _c1("ISO-8859-7", "windows-1253"),
            [("el", "NGRAMS_8859_7_EL")],
            T.CHARMAP_8859_7,
            streams,
        )
    )
    add(
        _sbcs(
            det,
            _c1("ISO-8859-8-I", "windows-1255"),
            [("he", "NGRAMS_8859_8_I_HE")],
            T.CHARMAP_8859_8,
            streams,
        )
    )
    add(
        _sbcs(
            det,
            _c1("ISO-8859-8", "windows-1255"),
            [("he", "NGRAMS_8859_8_HE")],
            T.CHARMAP_8859_8,
            streams,
        )
    )
    add(
        _sbcs(det, "windows-1251", [("ru", "NGRAMS_WINDOWS_1251")], T.CHARMAP_WINDOWS_1251, streams)
    )
    add(
        _sbcs(det, "windows-1256", [("ar", "NGRAMS_WINDOWS_1256")], T.CHARMAP_WINDOWS_1256, streams)
    )
    add(_sbcs(det, "KOI8-R", [("ru", "NGRAMS_KOI8_R")], T.CHARMAP_KOI8_R, streams))
    add(
        _sbcs(
            det,
            _c1("ISO-8859-9", "windows-1254"),
            [("tr", "NGRAMS_8859_9_TR")],
            T.CHARMAP_8859_9,
            streams,
        )
    )

    for name, lang, kind, fn, common in (
        ("Shift_JIS", "ja", "sjis", _next_sjis, T.COMMONCHARS_SJIS),
        ("GB18030", "zh", "gb18030", _next_gb18030, T.COMMONCHARS_GB_18030),
        ("EUC-JP", "ja", "euc", _next_euc, T.COMMONCHARS_EUC_JP),
        ("EUC-KR", "ko", "euc", _next_euc, T.COMMONCHARS_EUC_KR),
        ("Big5", "zh", "big5", _next_big5, T.COMMONCHARS_BIG5),
    ):
        conf = _mbcs(det, kind, fn, common)
        if conf > 0:
            results.append((name, lang, conf))

    for name, escapes in (
        ("ISO-2022-JP", _ESC_2022JP),
        ("ISO-2022-KR", _ESC_2022KR),
        ("ISO-2022-CN", _ESC_2022CN),
    ):
        conf = _match_2022(det.input, escapes)
        if conf > 0:
            results.append((name, "", conf))

    add(_sbcs(det, "IBM424_rtl", [("he", "NGRAMS_IBM424_HE_RTL")], T.CHARMAP_IBM424_HE, streams))
    add(_sbcs(det, "IBM424_ltr", [("he", "NGRAMS_IBM424_HE_LTR")], T.CHARMAP_IBM424_HE, streams))
    add(
        _sbcs(
            det, "IBM420_rtl", [("ar", "NGRAMS_IBM420_AR_RTL")], T.CHARMAP_IBM420_AR, streams, True
        )
    )
    add(
        _sbcs(
            det, "IBM420_ltr", [("ar", "NGRAMS_IBM420_AR_LTR")], T.CHARMAP_IBM420_AR, streams, True
        )
    )

    # uprv_sortArray(..., sortStable = true): Python's sort is stable too
    results.sort(key=lambda m: -m[2])
    return results
