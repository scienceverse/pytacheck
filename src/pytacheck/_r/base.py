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


def signif(x: float, digits: int = 6) -> float:
    """R ``signif()``."""
    if is_na(x) or x == 0 or not math.isfinite(x):
        return x
    return float(f"{x:.{max(digits, 1) - 1}e}")


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
