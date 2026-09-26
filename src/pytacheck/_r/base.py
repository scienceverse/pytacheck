"""R's base-library conventions that reach users.

Number formatting and rounding (``format()``, ``as.character()``, ``round()``,
``signif()``), ``paste()``, ``trimws()`` and R's string collation for sorted
listings: text produced by ported modules (summary texts, report sentences,
table cells) uses these so that it reads as metacheck's does. Prefer them over
``str()``/f-string formatting of numbers wherever the R code relied on
``paste()``, ``as.character()`` or ``format()`` of a double.
"""

from __future__ import annotations

import functools
import math
import os
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from pytacheck._r.regex import _vectorize, is_na

__all__ = [
    "as_character",
    "format_num",
    "is_na",
    "local_epoch",
    "local_utc_offset",
    "paste",
    "plural",
    "r_round",
    "r_sort_key",
    "r_sorted",
    "signif",
    "slashed",
    "trimws",
]


def slashed(path: str) -> str:
    """*path* with ``"/"`` between its parts, as R's file functions write paths
    (``file.path()``, ``list.files()``) on every platform: Windows' ``"\\"``
    becomes ``"/"``; elsewhere a backslash is part of a name and is kept."""
    return path if os.sep == "/" else path.replace(os.sep, "/")


def _tz_zone() -> Any:
    """The zone ``TZ`` names where the C library cannot be told about it.

    R reads the wall clock in the zone ``TZ`` names on every platform. Where
    :func:`time.tzset` exists (POSIX) the time module follows ``TZ`` and this
    is ``None``; on Windows it does not, so a set ``TZ`` is looked up in the
    IANA database (``None`` when unset or unknown: the system's zone).
    """
    import time

    tz = os.environ.get("TZ", "").lstrip(":")
    if hasattr(time, "tzset") or not tz:
        return None
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(tz)
    except (KeyError, ValueError, OSError):  # ZoneInfoNotFoundError is a KeyError
        return None


def local_utc_offset(secs: float) -> float:
    """The session time zone's UTC offset (seconds) at the POSIX time *secs*."""
    import time

    zone = _tz_zone()
    if zone is None:
        return float(time.localtime(math.floor(secs)).tm_gmtoff)
    import datetime as dt

    off = dt.datetime.fromtimestamp(math.floor(secs), zone).utcoffset()
    return off.total_seconds() if off is not None else 0.0


def local_epoch(naive: Any) -> float:
    """The POSIX time of the naive wall time *naive* (a :class:`datetime.datetime`)
    read in the session time zone, as ``as.POSIXct()`` reads it."""
    import time

    zone = _tz_zone()
    if zone is None:
        return float(time.mktime(naive.timetuple()) + naive.microsecond / 1e6)
    return float(naive.replace(tzinfo=zone).timestamp())


def format_num(x: Any, digits: int = 7) -> str:
    """R ``format(x)`` for a single number (default ``digits = 7``).

    R chooses between fixed and scientific notation by width: the shortest
    representation that shows *digits* significant digits wins, ties going
    to fixed notation (``scipen = 0``).
    """
    if is_na(x):
        return "NaN" if isinstance(x, float) and math.isnan(x) else "NA"
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return str(x)  # R integers never use scientific notation
    x = float(x)
    if math.isinf(x):
        return "Inf" if x > 0 else "-Inf"
    if x == 0:
        return "0"
    sci = f"{x:.{digits - 1}e}"
    mantissa, exp_str = sci.split("e")
    exponent = int(exp_str)
    mantissa = mantissa.rstrip("0").rstrip(".") if "." in mantissa else mantissa
    sig = len(mantissa.replace("-", "").replace(".", ""))
    exp_part = f"e{'-' if exponent < 0 else '+'}{abs(exponent):02d}"
    sci_repr = mantissa + exp_part
    decimals = max(0, sig - 1 - exponent)
    fixed_repr = f"{x:.{decimals}f}"
    return fixed_repr if len(fixed_repr) <= len(sci_repr) else sci_repr


def as_character(x: Any) -> str | None:
    """R ``as.character()`` for a single value (``None`` for ``NA``).

    Doubles use 15 significant digits, e.g. ``1/3 -> "0.333333333333333"``,
    ``1e5 -> "1e+05"``, ``123456.7 -> "123456.7"``; logicals become
    ``"TRUE"``/``"FALSE"``.
    """
    if is_na(x):
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, bool | np.bool_):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int | np.integer):
        return str(int(x))
    if isinstance(x, float | np.floating):
        return format_num(float(x), digits=15)
    return str(x)


def _paste_str(x: Any) -> str:
    s = as_character(x)
    return "NA" if s is None else s


def paste(*args: Any, sep: str = " ", collapse: str | None = None) -> Any:
    """R ``paste()``: vectorised with recycling; ``NA`` becomes ``"NA"``.

    Returns a list of strings, or a single string when *collapse* is given
    (or when every argument is a scalar).
    """
    vectors = [[a] if isinstance(a, str) or not isinstance(a, Iterable) else list(a) for a in args]
    if not any(vectors):
        return "" if collapse is not None else []
    # zero-length arguments recycle as "" (R: paste("A", character(0)) is "A ")
    vectors = [v or [""] for v in vectors]
    n = max(len(v) for v in vectors)
    out = [sep.join(_paste_str(v[i % len(v)]) for v in vectors) for i in range(n)]
    if collapse is not None:
        return collapse.join(out)
    if all(isinstance(a, str) or not isinstance(a, Iterable) for a in args):
        return out[0]
    return out


def plural(n: Any, singular: str = "", plural_: str = "s") -> Any:
    """metacheck's ``plural()``: ``singular`` when ``n == 1`` else ``plural_``."""
    if isinstance(n, Iterable) and not isinstance(n, str):
        return [singular if v == 1 else plural_ for v in n]
    return singular if n == 1 else plural_


#: R's trimws() default whitespace, "[ \t\r\n]"
_TRIM = " \t\r\n"
_STRIP = {"both": str.strip, "left": str.lstrip, "right": str.rstrip}


def trimws(x: Any, which: str = "both") -> Any:
    """R ``trimws()`` with its default whitespace (space, tab, CR, LF)."""
    strip = _STRIP[which]
    return _vectorize(x, lambda v: None if is_na(v) else strip(str(v), _TRIM))


_LOG10_2 = math.log10(2.0)
_MAX10E = 308
_MAX_DIGITS = 308 + 15


def _r_pow_di(x: float, n: int) -> float:
    """R's ``R_pow_di()``: x^n by repeated squaring (matches R bit for bit)."""
    xn, neg, n = 1.0, n < 0, abs(n)
    while n:
        if n & 1:
            xn *= x
        n >>= 1
        if n:
            x *= x
    return 1.0 / xn if neg else xn


def r_round(x: Any, digits: float = 0) -> Any:
    """R ``round()`` (R >= 4.0 algorithm, ``src/nmath/fround.c``).

    Python's :func:`round` rounds the exact binary value, R picks the nearer
    of the two decimal candidates computed in double arithmetic (ties to
    even); they disagree on a few percent of "half" cases such as
    ``round(0.12355, 4)`` (R: 0.1236, Python: 0.1235). Use this wherever the
    R code calls ``round()``. ``NA`` is returned unchanged.
    """
    if is_na(x):
        return x
    x = float(x)
    if math.isinf(x) or x == 0.0 or digits > _MAX_DIGITS:
        return x
    if digits < -_MAX10E:
        return 0.0
    if digits == 0:
        return float(round(x))
    dig = math.floor(digits + 0.5)
    sgn = 1.0
    if x < 0:
        sgn, x = -1.0, -x
    if _LOG10_2 * (0.5 + (math.frexp(x)[1] - 1)) + dig > 15:
        return sgn * x
    if dig <= _MAX10E:
        pow10 = _r_pow_di(10.0, dig)
        x10 = x * pow10
        i10 = math.floor(x10)
        xd = i10 / pow10
        xu = math.ceil(x10) / pow10
    else:
        e10 = dig - _MAX10E
        p10 = _r_pow_di(10.0, e10)
        pow10 = _r_pow_di(10.0, _MAX10E)
        x10 = (x * pow10) * p10
        i10 = math.floor(x10)
        xd = i10 / pow10 / p10
        xu = math.ceil(x10) / pow10 / p10
    du = xu - x
    dd = x - xd
    return sgn * (xu if (du < dd or (math.fmod(i10, 2.0) == 1 and du == dd)) else xd)


def signif(x: Any, digits: float = 6) -> Any:
    """R ``signif()`` (``src/nmath/fprec.c``)."""
    if is_na(x):
        return x
    x = float(x)
    if math.isinf(x) or x == 0.0:
        return x
    dig = round(digits)
    if dig > _MAX_DIGITS:
        return x
    dig = max(dig, 1)
    sgn = 1.0
    if x < 0:
        sgn, x = -1.0, -x
    l10 = math.log10(x)
    e10 = dig - 1 - math.floor(l10)
    if abs(l10) < _MAX10E - 2:
        p10 = 1.0
        if e10 > _MAX10E:
            p10 = _r_pow_di(10.0, e10 - _MAX10E)
            e10 = _MAX10E
        if e10 > 0:
            pow10 = _r_pow_di(10.0, e10)
            return sgn * (round((x * pow10) * p10) / pow10) / p10
        pow10 = _r_pow_di(10.0, -e10)
        return sgn * (round(x / pow10) * pow10)
    do_round = math.log10(1.7976931348623157e308) - l10 >= _r_pow_di(10.0, -dig)
    e2 = dig + (1 if e10 > 0 else -1) * _MAX_DIGITS
    p10 = _r_pow_di(10.0, e2)
    big_p10 = _r_pow_di(10.0, e10 - e2)
    x *= p10
    x *= big_p10
    if do_round:
        x += 0.5
    x = math.floor(x) / p10
    return sgn * x / big_p10


# ---------------------------------------------------------------------------
# Collation
# ---------------------------------------------------------------------------

_CATEGORY_RANK = {"Z": 0, "C": 0, "P": 1, "S": 2, "N": 3, "L": 4, "M": 5}
# ICU root collation order of ASCII punctuation and symbols (R's sort() in a UTF-8
# locale), which is not code-point order: e.g. "_" < "-" < "." < "/".
_ICU_PUNCT = {c: i for i, c in enumerate("_-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$")}


def r_sort_key(s: Any) -> tuple[Any, ...]:
    """Sort key approximating R's base ``sort()``/``order()`` on strings.

    R (built with ICU, in a UTF-8 locale) collates with the ICU root
    collation: punctuation < digits < letters, letters compared
    case-insensitively and accent-insensitively first, then by accents, then
    lowercase before uppercase. Example: ``["_x", "1", "a", "A", "b", "B"]``.
    Accents are ranked in ICU's order (acute < grave < ... < diaeresis), not
    by code point; compatibility characters (fullwidth, superscripts,
    ligatures) sort as their NFKD form; ø ł đ ħ ð are variants of their base
    letter, æ œ ß expand to ae oe ss, and ı ŋ ŧ þ ... are separate letters.

    ``dplyr::arrange()``/``count()`` sort in the C locale instead: use a
    plain ``sorted()`` (code point order) for those.
    """
    if is_na(s):
        return (1,)
    primary: list[tuple[int, str]] = []
    secondary: list[str] = []
    tertiary: list[int] = []
    for ch in str(s):
        for rank, prim, sec, ter in _collation_units(ch):
            primary.append((rank, prim))
            secondary.append(sec)
            tertiary.append(ter)
    return (0, tuple(primary), tuple(secondary), tuple(tertiary))


# Latin letters without a canonical decomposition that ICU root collation does
# not sort by code point (checked against R's order()):
# - variants of a base letter, ranked as that letter plus an overlay mark (ø is
#   o + U+0338, ł is l + U+0335), or after all of its accented forms ("\uffff")
# - expansions after their plain spelling: æ = ae, œ = oe, ß = ss
# fmt: off
_ICU_VARIANT = {
    "ø": ("o", "\u0338"), "đ": ("d", "\u0335"), "ł": ("l", "\u0335"), "ħ": ("h", "\u0335"),
    "ð": ("d", "\uffff"), "æ": ("ae", "\uffff"), "œ": ("oe", "\uffff"), "ß": ("ss", "\uffff"),
    "ſ": ("s", "\uffff"),
}
# - separate letters sorted after every word of their base letter: (base, rank)
_ICU_AFTER = {
    "ı": ("i", 1), "ĸ": ("q", 1), "ŋ": ("n", 1), "ŧ": ("t", 1), "ǝ": ("e", 1), "ə": ("e", 2),
    "ɛ": ("e", 3), "ƒ": ("f", 1), "ɔ": ("o", 1), "ƶ": ("z", 1), "ʒ": ("z", 2), "þ": ("z", 3),
}
# fmt: on


# ICU root order of the combining diacritics U+0300-U+036F (R's order() of "a" + mark),
# which is not code-point order: acute < grave < breve < circumflex < caron < ring <
# diaeresis ... Secondary keys remap each mark to U+0300 + its rank.
_ICU_MARK_ORDER = (
    "034F 0332 0313 0343 0314 0301 0341 0300 0340 0306 0302 030C 030A 0342 0308 0344 "
    "030B 0303 0307 0338 0327 0328 0304 030D 030E 0312 0315 031A 033D 033E 033F 0346 "
    "034A 034B 034C 0350 0351 0352 0357 035B 035D 035E 0316 0317 0318 0319 031C 031D "
    "031E 031F 0320 0329 032A 032B 032C 032F 0333 033A 033B 033C 0347 0348 0349 034D "
    "034E 0353 0354 0355 0356 0359 035A 035C 035F 0362 0336 0337 0335 0305 0309 030F "
    "0310 0311 031B 0321 0322 0323 0324 0325 0326 032D 032E 0330 0331 0334 0339 0345 "
    "0358 0360 0361 0363 0368 0369 0364 036A 0365 036B 0366 036C 036D 0367 036E 036F"
)
_ICU_MARK = str.maketrans(
    {chr(int(h, 16)): chr(0x300 + i) for i, h in enumerate(_ICU_MARK_ORDER.split())}
)


@functools.lru_cache(maxsize=4096)
def _collation_units(ch: str) -> tuple[tuple[int, str, str, int], ...]:
    """``(rank, primary, secondary, tertiary)`` collation units of one character."""
    decomposed = unicodedata.normalize("NFD", ch)
    compat = unicodedata.normalize("NFKD", ch)
    if compat != decomposed and ch not in _ICU_VARIANT:
        # compatibility characters (fullwidth, superscripts, ligatures, fractions)
        # sort as their NFKD expansion, after it at the tertiary level
        units: list[tuple[int, str, str, int]] = []
        for c in compat:
            if units and unicodedata.category(c)[0] == "M":
                rank, prim, sec, _ = units[-1]
                units[-1] = (rank, prim, sec + c.translate(_ICU_MARK), 2)
            else:
                units += [(rank, prim, sec, 2) for rank, prim, sec, _ in _collation_units(c)]
        return tuple(units)
    base = decomposed[0]
    marks = decomposed[1:].translate(_ICU_MARK)
    lower = base.lower()
    tertiary = 0 if base == lower else 1
    if lower in _ICU_VARIANT:
        letters, mark = _ICU_VARIANT[lower]
        mark = mark.translate(_ICU_MARK)
        return tuple(
            (4, c, (mark + marks) if i == 0 else "", tertiary) for i, c in enumerate(letters)
        )
    if lower in _ICU_AFTER:
        letter, n = _ICU_AFTER[lower]
        return ((4, f"{letter}\U0010ffff{n:d}", marks, tertiary),)
    rank = _CATEGORY_RANK.get(unicodedata.category(base)[0], 6)
    folded = base.casefold()
    if rank >= 3:
        return ((rank, folded, marks, 0 if base == folded else 1),)
    return ((rank, f"{_ICU_PUNCT.get(base, 99):02d}{base}", marks, 0 if base == folded else 1),)


def r_sorted(x: Sequence[Any], decreasing: bool = False) -> list[Any]:
    """R ``sort()`` for a character vector (``NA`` values are dropped, as in R)."""
    kept = [v for v in x if not is_na(v)]
    return sorted(kept, key=r_sort_key, reverse=decreasing)
