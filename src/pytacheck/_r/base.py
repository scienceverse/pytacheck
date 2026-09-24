"""Small R base-library equivalents with R's exact output conventions.

These exist so that text produced by ported modules (summary texts, report
sentences, table cells) is byte-identical to metacheck's, which the parity
suite checks. Prefer them over ad-hoc ``str()``/f-string formatting of
numbers whenever the R code relied on ``paste()``, ``as.character()`` or
``format()`` of a double.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any

from pytacheck._r.regex import compile_r, is_na

__all__ = [
    "as_character",
    "format_num",
    "is_na",
    "nchar",
    "paste",
    "paste0",
    "plural",
    "r_round",
    "r_sort_key",
    "r_sorted",
    "signif",
    "substr",
    "trimws",
]


def _is_whole(x: float) -> bool:
    return math.isfinite(x) and x == math.floor(x)


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
        return _format_int(x)
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
    if len(fixed_repr) <= len(sci_repr):
        return fixed_repr
    return sci_repr


def _format_int(x: int) -> str:
    # R integers never use scientific notation in as.character()/paste().
    return str(x)


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
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return format_num(x, digits=15)
    try:
        import numpy as np

        if isinstance(x, np.bool_):
            return "TRUE" if bool(x) else "FALSE"
        if isinstance(x, np.integer):
            return str(int(x))
        if isinstance(x, np.floating):
            return format_num(float(x), digits=15)
    except ImportError:  # pragma: no cover
        pass
    return str(x)


def _paste_str(x: Any) -> str:
    s = as_character(x)
    return "NA" if s is None else s


def _as_vector(x: Any) -> list[Any]:
    if isinstance(x, str) or not isinstance(x, Iterable):
        return [x]
    return list(x)


def paste(*args: Any, sep: str = " ", collapse: str | None = None) -> Any:
    """R ``paste()``: vectorised with recycling; ``NA`` becomes ``"NA"``.

    Returns a list of strings, or a single string when *collapse* is given
    (or when every argument is a scalar).
    """
    vectors = [_as_vector(a) for a in args]
    vectors = [v for v in vectors if len(v) > 0]
    if not vectors:
        return "" if collapse is not None else []
    n = max(len(v) for v in vectors)
    out = [sep.join(_paste_str(v[i % len(v)]) for v in vectors) for i in range(n)]
    if collapse is not None:
        return collapse.join(out)
    if all(isinstance(a, str) or not isinstance(a, Iterable) for a in args):
        return out[0]
    return out


def paste0(*args: Any, collapse: str | None = None) -> Any:
    """R ``paste0()``."""
    return paste(*args, sep="", collapse=collapse)


def plural(n: Any, singular: str = "", plural_: str = "s") -> Any:
    """metacheck's ``plural()``: ``singular`` when ``n == 1`` else ``plural_``."""
    if isinstance(n, Iterable) and not isinstance(n, str):
        return [singular if v == 1 else plural_ for v in n]
    return singular if n == 1 else plural_


def trimws(x: Any, which: str = "both", whitespace: str = "[ \t\r\n]") -> Any:
    """R ``trimws()``; *whitespace* is a TRE regex (default: space, tab, CR, LF)."""
    from pytacheck._r.regex import _vectorize

    left = compile_r(f"^{whitespace}+", perl=False)
    right = compile_r(f"{whitespace}+$", perl=False)

    def trim(v: Any) -> str | None:
        if is_na(v):
            return None
        s = str(v)
        if which in ("both", "left"):
            s = left.sub("", s, count=1)
        if which in ("both", "right"):
            s = right.sub("", s, count=1)
        return s

    return _vectorize(x, trim)


def nchar(x: Any) -> int:
    """R ``nchar(type = "chars")`` for one value (``NA`` gives 2, as in R)."""
    if is_na(x):
        return 2
    return len(str(x))


def substr(x: str, start: int, stop: int) -> str:
    """R ``substr()`` with 1-based inclusive positions."""
    start = max(start, 1)
    if stop < start:
        return ""
    return x[start - 1 : stop]


_LOG10_2 = math.log10(2.0)
_MAX10E = 308
_MAX_DIGITS = 308 + 15


def _r_pow_di(x: float, n: int) -> float:
    """R's ``R_pow_di()``: x^n by repeated squaring (matches R bit for bit)."""
    xn = 1.0
    if n != 0:
        neg = n < 0
        if neg:
            n = -n
        while True:
            if n & 1:
                xn *= x
            n >>= 1
            if n:
                x *= x
            else:
                break
        if neg:
            xn = 1.0 / xn
    return xn


def r_round(x: Any, digits: int = 0) -> Any:
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


def signif(x: Any, digits: int = 6) -> Any:
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


def r_sort_key(s: Any) -> tuple[Any, ...]:
    """Sort key approximating R's base ``sort()``/``order()`` on strings.

    R (built with ICU, in a UTF-8 locale) collates with the ICU root
    collation: punctuation < digits < letters, letters compared
    case-insensitively and accent-insensitively first, then by accents, then
    lowercase before uppercase. Example: ``["_x", "1", "a", "A", "b", "B"]``.

    ``dplyr::arrange()``/``count()`` sort in the C locale instead: use a
    plain ``sorted()`` (code point order) for those.
    """
    if is_na(s):
        return (1,)
    text = str(s)
    primary: list[tuple[int, str]] = []
    secondary: list[str] = []
    tertiary: list[int] = []
    for ch in text:
        decomposed = unicodedata.normalize("NFD", ch)
        base = decomposed[0]
        rank = _CATEGORY_RANK.get(unicodedata.category(base)[0], 6)
        folded = base.casefold()
        primary.append((rank, folded if rank >= 3 else base))
        secondary.append(decomposed[1:])
        tertiary.append(0 if base == folded else 1)
    return (0, tuple(primary), tuple(secondary), tuple(tertiary))


def r_sorted(x: Sequence[Any], decreasing: bool = False) -> list[Any]:
    """R ``sort()`` for a character vector (``NA`` values are dropped, as in R)."""
    kept = [v for v in x if not is_na(v)]
    return sorted(kept, key=r_sort_key, reverse=decreasing)
