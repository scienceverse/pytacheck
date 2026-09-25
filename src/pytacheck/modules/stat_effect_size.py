"""Effect Sizes in t-tests and F-tests (port of ``inst/modules/stat_effect_size.R``).

The R module defines its helpers (``parse_t_stats()``, ``parse_d_stats()``,
``parse_f_stats()``, ``parse_eta_stats()``, ``classify_d_coherence()``,
``classify_f_coherence()``, ``label_lhs()``, ``build_rows()`` and
``count_coh()``) inside the module function; they are the private
``_functions`` below. The parsers return lists of tuples (one per row of the
data frame R builds) instead of small data frames, and the per-test coherence
checks run over plain lists, which keeps the module fast on large corpora.

Numbers are read and printed as R does it, not as Python's correctly rounded
conversions do: ``as.numeric()`` is ``R_strtod()`` in long double (``_num()``)
and ``as.character()``/``format()`` find the significant digits in long double
(``_format_real()``). On near-ties both differ from ``float()``/``repr()`` in
the last digit of the implied effect sizes.
"""

from __future__ import annotations

import functools
import math
import warnings
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, NamedTuple

import pandas as pd

from pytacheck.module import module

# ---------------------------------------------------------------------------
# Patterns (R string literals translated to regexes; see docs/PORTING.md)
# ---------------------------------------------------------------------------

_NUM = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"

# parse_t_stats(): perl = TRUE
_T_PATTERN = r"\bt\s*\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)\s*=\s*" + _NUM

# parse_d_stats(): perl = TRUE
_D_PATTERN = (
    r"(?i)\b"
    r"(cohen(?:'|’)?s\s+d\s*z|cohen(?:'|’)?s\s+d|d\s*z|d|ds)"
    r"\b\s*([=≈<>≤≥]{1,3})\s*" + _NUM
)

# parse_f_stats(): perl = TRUE
_F_PATTERN = r"\bF\s*\(\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*([0-9]+(?:\.[0-9]+)?)\s*\)\s*=\s*" + _NUM

# parse_eta_stats(): perl = TRUE
_ETA_SPLIT = r"\s*;\s*"
_ETA_PATTERN = r"(?i)^\s*" r"([^=≈<>≤≥;]+?)" r"\s*([=≈<>≤≥]{1,3})\s*" + _NUM + r"\s*$"

# An effect size reported as an inequality ("d > 0.4", "ηp2 < .001") is a bound:
# metacheck checks it as if it were "=" (U126); see _fits()

# label_lhs(): TRE (perl = FALSE)
_F_DF_PATTERN = r"^\(\s*[0-9]+\s*,\s*[0-9]+\s*\)$"
_ES_PATTERNS = (
    r"^(cohen.{0,3})?d(_?(z|s|av|rm))?$",  # Cohen's d / dz / ds / dav / drm
    r"^(hedge.{0,3})?g$",  # Hedges' g
    r"^f2?$",  # Cohen's f
    r"cohen",
    r"ω|omega",  # omega
    r"η|eta",  # eta family
    r"^ξ$|^β$|^b$|^r$",  # xi, beta, b, r
)

_TOL = 0.01

# R's largest integer (.Machine$integer.max)
_INT_MAX = 2147483647

_TEST_KINDS = ("t-test", "F-test")

_GUIDANCE = [
    "For metascientific articles demonstrating that effect sizes are often not reported:",
    "* Peng, C.-Y. J., Chen, L.-T., Chiang, H.-M., & Chiang, Y.-C. (2013). The Impact of APA "
    "and AERA Guidelines on Effect Size Reporting. Educational Psychology Review, 25(2), "
    "157–209. doi:[10.1007/s10648-013-9218-2](https://doi.org/10.1007/s10648-013-9218-2).",
    "For educational material on reporting effect sizes:",
    "* [Guide to Effect Sizes and Confidence Intervals]"
    "(https://matthewbjane.quarto.pub/guide-to-effect-sizes-and-confidence-intervals/)",
]

_NO_TESTS = "No t-tests or F-tests were detected."


_D_COLUMNS = (
    "d_reported",
    "d_reported_text",
    "t_value",
    "df",
    "d_implied_paired_dz",
    "d_implied_paired_drm_r05",
    "d_implied_indep_equal_n",
    "d_implied_indep_unequal_min",
    "d_implied_indep_unequal_max",
    "d_implied_n",
    "d_coherence",
    "d_coherence_assumption",
    "d_coherence_note",
)

_F_COLUMNS = (
    "f_reported",
    "f_reported_text",
    "df1",
    "df2",
    "eta_implied_partial",
    "omega_implied_partial",
    "eta_coherence",
    "eta_coherence_assumption",
    "eta_coherence_note",
)

_REPORT_COLS = {
    "text": "Sentence",
    "es": "Effect Size",
    "test_text": "Reported Test",
    "test": "Test Type",
    "d_coherence": "d Coherence",
    "d_coherence_assumption": "d Assumption",
    "d_coherence_note": "d Coherence Note",
    "eta_coherence": "eta Coherence",
    "eta_coherence_assumption": "eta Assumption",
    "eta_coherence_note": "eta Coherence Note",
}


# ---------------------------------------------------------------------------
# R scalar semantics
# ---------------------------------------------------------------------------


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and math.isnan(x)


def _tolower(s: str | None) -> str | None:
    """R ``tolower()``: one-to-one lower-casing (``towlower``)."""
    if s is None:
        return None
    if s.isascii():
        return s.lower()
    out = []
    for c in s:
        low = c.lower()
        out.append(low if len(low) == 1 else low[0])
    return "".join(out)


# ---------------------------------------------------------------------------
# as.numeric(): R_strtod() in long double
# ---------------------------------------------------------------------------
#
# R's as.numeric() of a string is R_strtod() (src/main/util.c), which reads the
# digits and scales by powers of ten in 80-bit long double (64-bit mantissa)
# before rounding to double. That double rounding is not always the correctly
# rounded result Python's float() gives: as.numeric("0.8050473192") is one ulp
# above float("0.8050473192"), which can change the 15 significant digits of
# the implied effect sizes. Long double values are ``(m, e)`` = ``m * 2**e``.

_LD_BITS = 64
_DBL_MAX_INT = int(float.fromhex("0x1.fffffffffffffp+1023"))
_LD_TEN = (10, 0)
_LD_ONE = (1, 0)


def _ld_round(
    m: int, e: int, sticky: bool = False, bits: int = _LD_BITS, emin: int | None = None
) -> tuple[int, int]:
    """Round ``m * 2**e`` (plus a nonzero tail if *sticky*) to *bits* bits, half to even."""
    if m == 0:
        return 0, 0
    shift = m.bit_length() - bits
    if emin is not None:
        shift = max(shift, emin - e)
    if shift <= 0:
        return m, e
    hi = m >> shift
    rem = m - (hi << shift)
    half = 1 << (shift - 1)
    if rem > half or (rem == half and (sticky or hi & 1)):
        hi += 1
    return hi, e + shift


def _ld_mul(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    return _ld_round(a[0] * b[0], a[1] + b[1])


def _ld_div(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    (ma, ea), (mb, eb) = a, b
    if ma == 0:
        return 0, 0
    k = max(0, mb.bit_length() - ma.bit_length() + _LD_BITS + 2)
    q, r = divmod(ma << k, mb)
    return _ld_round(q, ea - eb - k, sticky=r != 0)


def _ld_add_int(a: tuple[int, int], d: int) -> tuple[int, int]:
    m, e = a
    if e >= 0:
        return _ld_round((m << e) + d, 0)
    return _ld_round(m + (d << -e), e)


def _ld_pow10(n: int, op: Any, fac: tuple[int, int]) -> tuple[int, int]:
    """R's ``for (n...; n; n >>= 1, p10 *= p10) if (n & 1) fac = op(fac, p10)``."""
    p10 = _LD_TEN
    while n:
        if n & 1:
            fac = op(fac, p10)
        n >>= 1
        if n:
            p10 = _ld_mul(p10, p10)
    return fac


def _ld_pow10_exact(k: int) -> tuple[int, int]:
    """``10^k`` rounded to long double (glibc ``powl(10, k)``, assumed correctly rounded)."""
    if k >= 0:
        return _ld_round(10**k, 0)
    den = 10**-k
    s = den.bit_length() + _LD_BITS + 2
    q, r = divmod(1 << s, den)
    return _ld_round(q, -s, sticky=r != 0)


# format.c's tbl[] of powers of ten: long doubles initialised from *double*
# literals, so 1e23..1e27 are not exact
_TBL = [
    (lambda n, d: (n, -(d.bit_length() - 1)))(*float(10**k).as_integer_ratio()) for k in range(28)
]


def _drop_trailing0(s: str) -> str:
    """The trailing-zero removal of R's ``EncodeRealDrop0()``."""
    dot = s.find(".")
    if dot < 0:
        return s
    end = dot + 1
    while end < len(s) and s[end].isdigit():
        end += 1
    keep = end
    while keep > dot + 1 and s[keep - 1] == "0":
        keep -= 1
    if keep == dot + 1:
        keep = dot
    return s[:keep] + s[end:]


def _format_real(x: float, digits: int, drop0: bool) -> str:
    """R ``formatReal()`` + ``EncodeReal0()`` of one double (``as.character()``/``format()``).

    R finds the significant digits (``scientific()`` in src/main/format.c) by
    scaling ``|x|`` by a power of ten in long double and rounding to an integer,
    which on near-ties is not what correctly rounded decimal conversion gives
    (``as.character(0x1.9714938037f2cp-1)`` is ``"0.79507885875862"``, not
    ``"0.795078858758619"``). Fixed notation is used when it is not wider than
    scientific (``scipen = 0``). ``as.character()`` then drops trailing zeros.
    """
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Inf" if x > 0 else "-Inf"
    if x == 0:
        return "0"
    neg = int(x < 0)
    r = -x if neg else x
    num, den = r.as_integer_ratio()
    r_ld = (num, -(den.bit_length() - 1))  # den is a power of two
    kp = math.floor(math.log10(r)) - digits + 1
    if 0 < kp <= 27:
        r_prec = _ld_div(r_ld, _TBL[kp])
    elif -27 <= kp < 0:
        r_prec = _ld_mul(r_ld, _TBL[-kp])
    elif kp == 0:
        r_prec = r_ld
    else:
        r_prec = _ld_div(r_ld, _ld_pow10_exact(kp))
    m, e = r_prec
    if (m << e if e >= 0 else m) < (10 ** (digits - 1) if e >= 0 else 10 ** (digits - 1) << -e):
        r_prec = _ld_mul(r_prec, _LD_TEN)
        kp -= 1
    # alpha = nearbyintl(r_prec): round half to even
    m, e = r_prec
    if e >= 0:
        alpha = m << e
    else:
        alpha, rem = divmod(m, 1 << -e)
        half = 1 << (-e - 1)
        if rem > half or (rem == half and alpha & 1):
            alpha += 1
    nsig = digits
    for _ in range(digits):
        if alpha % 10 == 0:
            alpha //= 10
            nsig -= 1
        else:
            break
    if nsig == 0 and digits > 0:
        nsig = 1
        kp += 1
    kpower = kp + digits - 1
    # rounding may widen the number: 9996 with 3 digits is 1e+04 in scientific
    rgt = min(max(digits - kpower, 0), 27)
    fuzz = 0.5 / float(10**rgt)
    widens = False
    if 0 < kpower <= 27:
        fm, fe = fuzz.as_integer_ratio()
        fe = -(fe.bit_length() - 1)
        tm, te = _TBL[kpower]
        # tbl[kpower] - fuzz in long double
        lm, le = _ld_round((tm << (te - fe)) - fm, fe)
        # r < lm * 2^le, exactly (r = num / 2^-r_ld[1])
        shift = r_ld[1] - le
        widens = (num << shift) < lm if shift >= 0 else num < (lm << -shift)
    left = kpower + 1 - int(widens)
    sleft = neg + (1 if left <= 0 else left)
    rgt = max(nsig - left, 0)
    if left < 0:
        sleft = 1 + neg
    w_fixed = sleft + rgt + (rgt != 0)
    e_digits = 2 if (left > 100 or left <= -99) else 1
    d = nsig - 1
    w = neg + (d > 0) + d + 4 + e_digits
    if w_fixed <= w:
        s = f"{x:.{rgt}f}".rjust(w_fixed)
    else:
        s = (f"{x:#.{d}e}" if d else f"{x:.0e}").rjust(w)
    return _drop_trailing0(s) if drop0 else s


@functools.lru_cache(maxsize=65536)
def _num(x: str) -> float:
    """R ``as.numeric()`` of a string matched by the number patterns (``R_strtod()``).

    *x* is ``[-+]?(\\d+(\\.\\d*)?|\\.\\d+)([eE][-+]?\\d+)?``. Beyond 4900 digits
    (mantissa or exponent) long double could overflow; ``float()`` is used there.
    """
    sign = -1.0 if x[:1] == "-" else 1.0
    body = x.lstrip("+-")
    mant, _, exp = body.replace("E", "e").partition("e")
    whole, _, frac = mant.partition(".")
    digits = whole + frac
    ndigits = len(digits)
    if ndigits > 4900 or len(exp.lstrip("+-")) > 4:
        return float(x)
    expn = int(exp or "0") - len(frac)
    ans = (0, 0)
    for c in digits:
        ans = _ld_add_int(_ld_mul(_LD_TEN, ans), ord(c) - 48)
    # avoid unnecessary underflow for large negative exponents
    if expn + ndigits < -300:
        for _ in range(ndigits):
            ans = _ld_div(ans, _LD_TEN)
        expn += ndigits
    if expn < -307:  # use underflow, not overflow
        ans = _ld_mul(ans, _ld_pow10(-expn, _ld_div, _LD_ONE))
    elif expn < 0:  # positive powers are exact
        ans = _ld_div(ans, _ld_pow10(-expn, _ld_mul, _LD_ONE))
    elif ans[0] != 0:
        ans = _ld_mul(ans, _ld_pow10(expn, _ld_mul, _LD_ONE))
    m, e = ans
    # explicit overflow to infinity
    if m and (m << e if e >= 0 else m) > (_DBL_MAX_INT if e >= 0 else _DBL_MAX_INT << -e):
        return sign * math.inf
    m, e = _ld_round(m, e, bits=53, emin=-1074)
    return sign * math.ldexp(m, e)


@functools.lru_cache(maxsize=65536)
def _chr(x: float) -> str:
    """R ``as.character()`` of a double (``NaN``/``Inf`` included): 15 significant digits."""
    return _format_real(x, 15, drop0=True)


def _fdiv(a: float, b: float) -> float:
    """IEEE division, as R's ``/`` (no ``ZeroDivisionError``)."""
    try:
        return a / b
    except ZeroDivisionError:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def _le(a: float, b: float) -> bool | None:
    """R ``a <= b`` (``NA`` -> ``None``)."""
    if math.isnan(a) or math.isnan(b):
        return None
    return a <= b


def _any(values: Iterable[bool | None]) -> bool | None:
    """R ``any()``: ``TRUE`` wins, else ``NA`` if any ``NA``, else ``FALSE``."""
    na = False
    for v in values:
        if v is None:
            na = True
        elif v:
            return True
    return None if na else False


def _fits(reported: float, comp: str, lo: float, hi: float, tol: float) -> bool | None:
    """Whether a reported effect size agrees with the implied values ``lo``..``hi``.

    Sizes are compared as absolute values. "=" (and "≈") agrees within *tol*;
    a reported bound agrees when some implied value is on its side ("d > 0.4"
    with an implied 0.55, "ηp2 < .001" with an implied .0002). metacheck
    compares every reported value as if it were "=" (U126). A negative bound
    flips its direction ("d < -0.4" is |d| > 0.4).
    """
    if math.isnan(reported) or math.isnan(lo) or math.isnan(hi):
        return None
    upper = any(c in comp for c in "<≤≪")
    lower = any(c in comp for c in ">≥≫")
    if reported < 0 and upper != lower:
        upper, lower = lower, upper
    value = abs(reported)
    if upper and not lower:
        return lo <= value + tol
    if lower and not upper:
        return hi >= value - tol
    if lo == hi:  # R: abs(a - implied) <= tol
        return abs(value - lo) <= tol
    return lo - tol <= value <= hi + tol  # R: a >= min - tol & a <= max + tol


def _cond(x: bool | None) -> bool:
    """R ``if (x)``: an ``NA`` condition is an error."""
    if x is None:
        raise ValueError("missing value where TRUE/FALSE needed")
    return x


def _round0(x: float) -> float:
    """R ``round(x)`` (half to even; non-finite values unchanged)."""
    return x if not math.isfinite(x) else float(round(x))


def _format(x: float) -> str:
    """R ``format(x)`` of a double (7 significant digits)."""
    return _format_real(x, 7, drop0=False)


# ---------------------------------------------------------------------------
# Parsers (R: parse_t_stats(), parse_d_stats(), parse_f_stats(), parse_eta_stats())
# ---------------------------------------------------------------------------


class _TStat(NamedTuple):
    match_text: str
    t_value: float
    df: float


class _DStat(NamedTuple):
    label: str
    d_value: float
    d_text: str
    comp: str = "="


class _FStat(NamedTuple):
    match_text: str
    f_value: float
    df1: float
    df2: float


class _EtaStat(NamedTuple):
    label: str
    eta_value: float
    eta_text: str
    comp: str = "="


def _hits(pattern: str, text: str | None) -> list[list[str]]:
    """``regmatches(gregexpr())`` hits, each re-matched with ``regexec()`` (``perl = TRUE``).

    Each hit is ``[match, group1, ...]`` (unmatched groups ``""``). The
    patterns cannot match the empty string, so R's ``gregexpr()`` hits are
    exactly ``finditer()``'s, and a hit always re-matches itself at position 0.
    """
    from pytacheck._r.regex import compile_r

    if _is_na(text) or text == "":
        return []
    rx = compile_r(pattern, perl=True)
    out = []
    for m in rx.finditer(text):
        g = rx.search(m.group(0))
        if g is not None:
            out.append([g.group(0), *(x if x is not None else "" for x in g.groups())])
    return out


def _parse_t_stats(test_text: str | None) -> list[_TStat]:
    """Port of ``stat_effect_size.R::parse_t_stats()``: every ``t(df) = value``."""
    return [_TStat(g[0], _num(g[2]), _num(g[1])) for g in _hits(_T_PATTERN, test_text)]


def _parse_d_stats(es_text: str | None) -> list[_DStat]:
    """Port of ``stat_effect_size.R::parse_d_stats()``: every Cohen's d / dz value."""
    from pytacheck._r.base import trimws
    from pytacheck._r.regex import gsub

    out = []
    for g in _hits(_D_PATTERN, es_text):
        label = gsub(r"\s+", " ", _tolower(trimws(g[1])))
        out.append(_DStat(label, _num(g[3]), g[0], g[2]))
    return out


def _parse_f_stats(test_text: str | None) -> list[_FStat]:
    """Port of ``stat_effect_size.R::parse_f_stats()``: every ``F(df1, df2) = value``."""
    return [_FStat(g[0], _num(g[3]), _num(g[1]), _num(g[2])) for g in _hits(_F_PATTERN, test_text)]


@functools.lru_cache(maxsize=4096)
def _eta_label(raw_label: str) -> str:
    """The effect-size type of a ``parse_eta_stats()`` label."""
    from pytacheck._r.regex import grepl

    def has(pattern: str) -> bool:
        return bool(grepl(pattern, raw_label))

    if has("^ξ|^bf"):
        return "non_checkable"
    if has("^f2?$") or has("cohen.{0,3}f"):
        # metacheck's `has("cohen")` also took "Cohen's d" for Cohen's f (U126)
        return "cohens_f"
    if has("ω") or has("omega"):
        # Omega squared (partial or not) depends on the total sample size N which
        # cannot yet be recovered.
        return "non_checkable"
    if has("η") or has("eta"):
        return "partial_eta_squared" if has("partial") or has("p") else "eta_squared"
    # d, g, r, R2, beta, ... next to an F-test: not an eta-squared (metacheck
    # labels them "eta_squared" and says "Eta-squared reported", U126)
    return "non_checkable"


def _after_partial(sentence: str | None, lhs: str) -> bool:
    """Whether *lhs* follows the word "partial" in *sentence* ("partial η2 = .12")."""
    if not isinstance(sentence, str) or not lhs:
        return False
    low, target = sentence.lower(), lhs.lower()
    start = low.find("partial")
    while start >= 0:
        rest = low[start + len("partial") :].lstrip(" -\u00a0")
        if rest.startswith(target):
            return True
        start = low.find("partial", start + 1)
    return False


def _parse_eta_stats(es_text: str | None, sentence: str | None = None) -> list[_EtaStat]:
    """Port of ``stat_effect_size.R::parse_eta_stats()``: labelled eta-family values.

    ``extract_eq()`` gives "η2" as the name in "partial η2 = .12"; with the
    *sentence*, such a value is a partial eta-squared (metacheck reads it as
    eta-squared, U126).
    """
    from pytacheck._r.base import trimws
    from pytacheck._r.regex import gsub, regexec, strsplit

    if _is_na(es_text) or es_text == "":
        return []
    parts = trimws(strsplit(es_text, _ETA_SPLIT, perl=True))
    out = []
    for x in parts:
        g = regexec(_ETA_PATTERN, x, perl=True)
        if len(g) == 0:
            continue
        lhs = trimws(g[1])
        raw_label = _tolower(lhs)
        raw_label = gsub(r"\s+", "", raw_label)
        raw_label = gsub("²", "2", raw_label)
        label = _eta_label(raw_label)
        if label == "eta_squared" and _after_partial(sentence, lhs):
            label = "partial_eta_squared"
        out.append(_EtaStat(label, _num(g[3]), x, g[2]))
    return out


# ---------------------------------------------------------------------------
# Coherence checks (R: classify_d_coherence(), classify_f_coherence())
# ---------------------------------------------------------------------------


# group-size splits evaluated per block, so a huge df needs no huge arrays
_UNEQUAL_BLOCK = 1 << 20


def _unequal_d(n_total: int, abs_t: float) -> Iterator[tuple[int, Any]]:
    """R ``abs_t * sqrt(1 / n1 + 1 / n2)`` for ``n1 <- 2:(n_total - 2)``, ``n2 <- n_total - n1``.

    Yields ``(first n1, d values)`` blocks in order; the same IEEE operations as R.
    """
    import numpy as np

    for start in range(2, n_total - 1, _UNEQUAL_BLOCK):
        n = np.arange(start, min(start + _UNEQUAL_BLOCK, n_total - 1), dtype=np.float64)
        d = np.divide(1.0, n)
        np.subtract(n_total, n, out=n)
        np.divide(1.0, n, out=n)
        d += n
        np.sqrt(d, out=d)
        d *= abs_t
        yield start, d


def _classify_d_coherence(
    test: str | None, test_text: str | None, es_text: str | None, tol: float = _TOL
) -> dict[str, str | None]:
    """Port of ``stat_effect_size.R::classify_d_coherence()``.

    Checks a reported Cohen's d against the d implied by ``t(df)`` under the
    paired (dz), independent equal-n and independent unequal-n designs.
    """
    import numpy as np

    out: dict[str, str | None] = dict.fromkeys(_D_COLUMNS)

    if _is_na(test) or test != "t-test":
        return out

    t_stats = _parse_t_stats(test_text)
    if len(t_stats) == 0:
        out["d_coherence"] = "indeterminate"
        out["d_coherence_assumption"] = "none"
        out["d_coherence_note"] = "No parseable t(df)=value found."
        return out

    d_stats = _parse_d_stats(es_text)
    if len(d_stats) == 0:
        out["d_coherence"] = "indeterminate"
        out["d_coherence_assumption"] = "none"
        out["d_coherence_note"] = "No parseable d effect size found."
        return out

    t_value = t_stats[0].t_value
    df = t_stats[0].df
    out["t_value"] = _chr(t_value)
    out["df"] = _chr(df)

    if math.isnan(df):
        out["d_coherence"] = "indeterminate"
        out["d_coherence_assumption"] = "none"
        out["d_coherence_note"] = "Missing df for t-test."
        return out

    diff = abs(df - _round0(df))
    if _cond(None if math.isnan(diff) else diff > 1e-8):
        out["d_coherence"] = "indeterminate"
        out["d_coherence_assumption"] = "none"
        out["d_coherence_note"] = (
            "Non-integer df indicates Welch's t-test (unequal variances); "
            "sample sizes cannot be determined."
        )
        return out

    abs_t = abs(t_value)

    # Paired-samples dz
    d_paired_dz = abs_t / math.sqrt(df + 1)

    # Paired-samples drm assuming r = 0.5
    r_assumed = 0.5
    d_paired_drm = d_paired_dz / math.sqrt(1 - r_assumed)

    # Independent-samples equal-n
    d_equal = 2 * abs_t / math.sqrt(df + 2)

    out["d_implied_paired_dz"] = _chr(d_paired_dz)
    out["d_implied_paired_drm_r05"] = _chr(d_paired_drm)
    out["d_implied_indep_equal_n"] = _chr(d_equal)

    n_total_f = df + 2
    use_unequal = (
        math.isfinite(n_total_f) and abs(n_total_f - _round0(n_total_f)) < 1e-8 and n_total_f >= 4
    )
    d_unequal_min = math.nan
    d_unequal_max = math.nan
    n_total = 0
    if use_unequal:
        n_total = int(_round0(n_total_f))
        if n_total > _INT_MAX:
            # as.integer() gives NA (with a warning) and `2:(NA - 2)` errors
            warnings.warn("NAs introduced by coercion to integer range", stacklevel=2)
            raise ValueError("NA/NaN argument")
        d_unequal_min, d_unequal_max = math.inf, -math.inf
        for _, d in _unequal_d(n_total, abs_t):
            d_unequal_min = min(d_unequal_min, float(d.min()))
            d_unequal_max = max(d_unequal_max, float(d.max()))
        out["d_implied_indep_unequal_min"] = _chr(d_unequal_min)
        out["d_implied_indep_unequal_max"] = _chr(d_unequal_max)

    abs_d = [abs(d.d_value) for d in d_stats]
    out["d_reported"] = _chr(d_stats[0].d_value)
    out["d_reported_text"] = d_stats[0].d_text

    paired_match = _any(_fits(d.d_value, d.comp, d_paired_dz, d_paired_dz, tol) for d in d_stats)
    equal_match = _any(_fits(d.d_value, d.comp, d_equal, d_equal, tol) for d in d_stats)
    unequal_match: bool | None = False
    in_range: list[bool | None] = []
    if use_unequal:
        in_range = [_fits(d.d_value, d.comp, d_unequal_min, d_unequal_max, tol) for d in d_stats]
        unequal_match = _any(in_range)

    if _cond(paired_match):
        # Within-subjects design: n = df + 1. This is obvious from the df, so it
        # is recorded but not spelled out in the note.
        out["d_coherence"] = "match_under_assumptions"
        out["d_coherence_assumption"] = "paired_dz"
        out["d_implied_n"] = f"n = {int(df + 1):d}"
        out["d_coherence_note"] = "Match under paired-samples dz assumption."
    elif _cond(equal_match):
        # Two equal-sized independent groups: N = df + 2, so n1 = n2 = (df + 2) / 2.
        out["d_coherence"] = "match_under_assumptions"
        out["d_coherence_assumption"] = "independent_equal_n"
        n_total_eq = df + 2
        n_each = n_total_eq / 2
        out["d_implied_n"] = f"n1 = n2 = {_format(n_each)}, N = {_format(n_total_eq)}"
        out["d_coherence_note"] = (
            "Match under independent-samples equal-n assumption "
            f"(n1 = n2 = {_format(n_each)}, N = {_format(n_total_eq)})."
        )
    elif _cond(unequal_match):
        # Report the single group-size split whose implied d is closest to the
        # reported d, so users can manually check the assumed sample sizes.
        out["d_coherence"] = "match_under_assumptions"
        out["d_coherence_assumption"] = "independent_unequal_n_range"
        matched_d = next(a for a, ok in zip(abs_d, in_range, strict=True) if ok)
        # which.min(abs(d_vals - matched_d)): the first split closest to the matched d
        n1, best_dist = 0, math.inf
        for start, d in _unequal_d(n_total, abs_t):
            dist = np.abs(d - matched_d)
            i = int(np.argmin(dist))
            if dist[i] < best_dist:
                n1, best_dist = start + i, float(dist[i])
        split = f"n1 = {n1:d}, n2 = {n_total - n1:d}, N = {n_total:d}"
        out["d_implied_n"] = split
        out["d_coherence_note"] = (
            f"Match under independent-samples unequal-n range assumption (closest split {split})."
        )
    else:
        out["d_coherence"] = "no_match"
        out["d_coherence_assumption"] = "none"
        out["d_coherence_note"] = (
            "No match under tested assumptions (paired dz, independent equal-n, "
            f"independent unequal-n range). Tolerance = {_chr(tol)}. A no-match can occur "
            "when fewer than 2 decimal places are reported."
        )

    return out


def _classify_f_coherence(
    test: str | None,
    test_text: str | None,
    es_text: str | None,
    tol: float = _TOL,
    sentence: str | None = None,
) -> dict[str, str | None]:
    """Port of ``stat_effect_size.R::classify_f_coherence()``.

    Checks a reported partial eta-squared (or partial omega-squared) against
    the value implied by ``F(df1, df2)``.
    """
    out: dict[str, str | None] = dict.fromkeys(_F_COLUMNS)

    if _is_na(test) or test != "F-test":
        return out

    f_stats = _parse_f_stats(test_text)
    if len(f_stats) == 0:
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "none"
        out["eta_coherence_note"] = "No parseable F(df1,df2)=value found."
        return out

    f_value = f_stats[0].f_value
    df1 = f_stats[0].df1
    df2 = f_stats[0].df2
    out["f_reported"] = _chr(f_value)
    out["f_reported_text"] = f_stats[0].match_text
    out["df1"] = _chr(df1)
    out["df2"] = _chr(df2)

    if math.isnan(df1) or math.isnan(df2):
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "none"
        out["eta_coherence_note"] = "Missing df1 or df2 for F-test."
        return out

    eta_implied = _fdiv(df1 * abs(f_value), df1 * abs(f_value) + df2)
    out["eta_implied_partial"] = _chr(eta_implied)

    omega_implied = _fdiv(df1 * (abs(f_value) - 1), df1 * abs(f_value) + df2 + 1)
    out["omega_implied_partial"] = _chr(omega_implied)

    eta_stats = _parse_eta_stats(es_text, sentence)
    if len(eta_stats) == 0:
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "none"
        out["eta_coherence_note"] = "No parseable eta-squared effect size found."
        return out

    # Remove ES types not verifiable from F and dfs:
    # Cohen's f (ambiguous eta basis), ξ, BF, and similar
    cohens_f_present = any(e.label == "cohens_f" for e in eta_stats)
    eta_stats = [e for e in eta_stats if e.label not in ("cohens_f", "non_checkable")]
    if len(eta_stats) == 0:
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "none"
        out["eta_coherence_note"] = (
            "Cohen's f not checked: cannot determine whether based on eta squared or partial "
            "eta squared."
            if cohens_f_present
            else "Effect size reported but not verifiable from F and degrees of freedom alone."
        )
        return out

    # If only eta-squared (not partial) is reported, coherence cannot be assessed
    if all(e.label == "eta_squared" for e in eta_stats):
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "eta_squared"
        out["eta_coherence_note"] = (
            "Eta-squared reported; cannot be reconstructed from F and dfs in factorial or "
            "repeated-measures designs."
        )
        return out

    # Evaluate coherence for partial eta squared and/or partial omega squared
    eta_partial = [e for e in eta_stats if e.label == "partial_eta_squared"]
    omega_partial = [e for e in eta_stats if e.label == "partial_omega_squared"]

    if len(eta_partial) == 0 and len(omega_partial) == 0:
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "none"
        out["eta_coherence_note"] = (
            "Only eta-squared (not partial) reported; cannot test coherence."
        )
        return out

    labels = {e.label for e in eta_stats}
    # metacheck sets this note and then always overwrites it (U125): it is
    # added to the note of the partial eta-squared check below
    both_note = (
        " Both eta-squared and partial eta-squared reported; "
        "coherence evaluated only for partial eta-squared."
        if "partial_eta_squared" in labels and "eta_squared" in labels
        else ""
    )

    # F(0, 0): the implied effect size is 0/0 (metacheck stops on `if (NA)`, U126)
    if math.isnan(eta_implied):
        out["eta_coherence"] = "indeterminate"
        out["eta_coherence_assumption"] = "none"
        out["eta_coherence_note"] = (
            "The implied effect size is undefined for these degrees of freedom."
        )
        return out

    no_match_note = (
        f"Tolerance = {_chr(tol)}. A no-match can occur when fewer than 2 decimal places "
        "are reported."
    )

    # Check partial eta squared coherence
    if len(eta_partial) > 0:
        fits = (_fits(e.eta_value, e.comp, eta_implied, eta_implied, tol) for e in eta_partial)
        if _cond(_any(fits)):
            out["eta_coherence"] = "match_under_assumptions"
            out["eta_coherence_assumption"] = "partial_eta_squared"
            out["eta_coherence_note"] = (
                "Match under partial eta-squared formula from F and dfs." + both_note
            )
        else:
            out["eta_coherence"] = "no_match"
            out["eta_coherence_assumption"] = "partial_eta_squared"
            out["eta_coherence_note"] = (
                "No match under partial eta-squared formula from F and dfs. "
                + no_match_note
                + both_note
            )

    # Check partial omega squared coherence
    # When omega_implied < 0, reporting convention is to report 0; match if reported value is 0.
    if len(omega_partial) > 0:
        if _cond(None if math.isnan(omega_implied) else omega_implied < 0):
            omega_match = _any(_le(abs(e.eta_value), tol) for e in omega_partial)
        else:
            omega_match = _any(_le(abs(e.eta_value - omega_implied), tol) for e in omega_partial)
        if _cond(omega_match):
            out["eta_coherence"] = "match_under_assumptions"
            out["eta_coherence_assumption"] = "partial_omega_squared"
            out["eta_coherence_note"] = "Match under partial omega-squared formula from F and dfs."
        else:
            out["eta_coherence"] = "no_match"
            out["eta_coherence_assumption"] = "partial_omega_squared"
            out["eta_coherence_note"] = (
                "No match under partial omega-squared formula from F and dfs. " + no_match_note
            )

    return out


# ---------------------------------------------------------------------------
# Detecting tests and effect sizes (R: label_lhs(), build_rows())
# ---------------------------------------------------------------------------


def _lhs_kind(lhs: list[str | None]) -> list[str | None]:
    """``label_lhs()`` for unique ``lhs`` values, before the F-test df check.

    ``"t-test"``, ``"F"`` (an F-test if its df is two integers), ``"es"`` or
    ``None``.
    """
    from pytacheck._r.regex import grepl, gsub

    is_t = grepl("^t$", lhs)
    is_f = grepl("^F$", lhs)
    raw = gsub("[[:space:]]+", "", [_tolower(x) for x in lhs])
    raw = gsub("²", "2", raw)  # squared symbol to 2
    is_es = [False] * len(lhs)
    for pattern in _ES_PATTERNS:
        is_es = [a or bool(b) for a, b in zip(is_es, grepl(pattern, raw), strict=True)]
    return [
        "t-test" if t else "F" if f else "es" if e else None
        for t, f, e in zip(is_t, is_f, is_es, strict=True)
    ]


def _label_lhs(lhs: Sequence[str | None], df: Sequence[str | None]) -> list[str | None]:
    """Port of ``stat_effect_size.R::label_lhs()``, vectorised over ``extract_eq()`` rows.

    ``"t-test"``, ``"F-test"`` (only with a two-integer ``(df1, df2)``), ``"es"``
    for an effect size (Cohen's d, Hedges' g, Cohen's f, the eta and omega
    families, xi, beta, b, r), or ``None`` (``NA``) for anything else (p, df,
    ...). Each distinct ``lhs`` is classified once.
    """
    from pytacheck._r.regex import grepl

    lhs = [None if _is_na(x) else str(x) for x in lhs]
    df = [None if _is_na(d) else str(d) for d in df]
    uniq = list(dict.fromkeys(lhs))
    kind_of = dict(zip(uniq, _lhs_kind(uniq), strict=True))
    kinds = [kind_of[x] for x in lhs]
    f_rows = [i for i, k in enumerate(kinds) if k == "F"]
    if f_rows:
        f_dfs = [df[i] for i in f_rows]
        ok = grepl(_F_DF_PATTERN, f_dfs)
        for i, d, good in zip(f_rows, f_dfs, ok, strict=True):
            kinds[i] = "F-test" if d is not None and good else None
    return kinds


def _build_rows(eq: pd.DataFrame, kinds: list[str | None]) -> pd.DataFrame | None:
    """Port of ``stat_effect_size.R::build_rows()`` applied to every sentence.

    One row per t/F test. When a sentence lists several tests and the same
    number of effect sizes they are paired by position; otherwise every test
    carries all the sentence's effect sizes (``"; "``-separated), or ``NA``.
    Rows come grouped by sentence (``split(~ paper_id + text_id)``), in
    ``extract_eq()`` order within a sentence; the caller restores paper order.
    """
    paper_ids = eq["paper_id"].tolist()
    text_ids = eq["text_id"].tolist()
    lhs = eq["lhs"].tolist()
    dfs = eq["df"].tolist()
    comps = eq["comp"].tolist()
    rhs = eq["rhs"].tolist()

    def s(x: Any) -> str:
        return "NA" if _is_na(x) else str(x)

    groups: dict[tuple[Any, Any], tuple[list[int], list[int]]] = {}
    for i, kind in enumerate(kinds):
        if kind is None or _is_na(paper_ids[i]) or _is_na(text_ids[i]):
            # split() drops NA groups; rows that are neither test nor effect size
            # play no part in build_rows()
            continue
        tests, es = groups.setdefault((paper_ids[i], text_ids[i]), ([], []))
        (tests if kind in _TEST_KINDS else es).append(i)

    out_pid: list[Any] = []
    out_tid: list[Any] = []
    out_test: list[str | None] = []
    out_text: list[str] = []
    out_es: list[str | None] = []
    for (pid, tid), (tests, es) in groups.items():
        if len(tests) == 0:
            continue
        es_text = [f"{s(lhs[j])} {s(comps[j])} {s(rhs[j])}" for j in es]
        paired = len(tests) > 1 and len(tests) == len(es_text)
        if paired:
            es_col: list[str | None] = list(es_text)
        elif len(es_text) == 0:
            es_col = [None] * len(tests)
        else:
            es_col = ["; ".join(es_text)] * len(tests)
        for j in tests:
            df_part = "" if _is_na(dfs[j]) else str(dfs[j])
            out_text.append(f"{s(lhs[j])}{df_part} {s(comps[j])} {s(rhs[j])}")
            out_test.append(kinds[j])
        out_pid.extend([pid] * len(tests))
        out_tid.extend([tid] * len(tests))
        out_es.extend(es_col)

    if len(out_pid) == 0:
        return None
    return pd.DataFrame(
        {
            "paper_id": pd.Series(out_pid, dtype=eq["paper_id"].dtype),
            "text_id": pd.Series(out_tid, dtype=eq["text_id"].dtype),
            "test": pd.Series(out_test, dtype="string"),
            "test_text": pd.Series(out_text, dtype="string"),
            "es": pd.Series(out_es, dtype="string"),
        }
    )


def _count_coh(col: Iterable[Any], value: str) -> int:
    """Port of ``stat_effect_size.R::count_coh()``: ``;``-separated occurrences of *value*."""
    n = 0
    for x in col:
        if _is_na(x):
            continue
        # trimws(): R's default whitespace "[ \t\r\n]" at both ends
        n += sum(1 for part in str(x).split(";") if part.strip(" \t\r\n") == value)
    return n


def _format_coherence_text(
    match: int, no_match: int, indet: int, test_label: str, es_label: str
) -> str | None:
    """Port of ``stat_effect_size.R::format_coherence_text()``."""
    if match + no_match + indet == 0:
        return None
    parts = []
    if match > 0:
        parts.append(f"{match:d} match{'' if match == 1 else 'es'} under assumptions")
    if no_match > 0:
        parts.append(f"{no_match:d} no match{'' if no_match == 1 else 'es'}")
    if indet > 0:
        parts.append(f"{indet:d} indeterminate case{'' if indet == 1 else 's'}")
    return (
        f"For {test_label} with a reported {es_label}, coherence checks yielded {', '.join(parts)}."
    )


def _paper_id_frame(paper: Any, paper_cls: type) -> pd.DataFrame:
    """R ``data.frame(paper_id = paper$paper_id)``.

    One row for a paper; a paper list has no ``paper_id`` element, so R builds
    a data frame with no columns. A text table (``module_run()`` also accepts
    the result of ``text_search()``) gives its whole ``paper_id`` column.
    """
    if isinstance(paper, paper_cls):
        return pd.DataFrame({"paper_id": pd.Series([paper.paper_id], dtype="string")})
    if isinstance(paper, pd.DataFrame):
        if "paper_id" not in paper.columns:
            return pd.DataFrame()
        return pd.DataFrame({"paper_id": paper["paper_id"].astype("string").reset_index(drop=True)})
    return pd.DataFrame()


def _icu_rank(values: Sequence[Any]) -> list[int]:
    """Position of each value among R ``levels(factor(values))`` (``sort()``: ICU collation)."""
    from pytacheck._r.base import r_sort_key

    levels = sorted(set(values), key=r_sort_key)
    rank = {v: i for i, v in enumerate(levels)}
    return [rank[v] for v in values]


def _string_frame(rows: list[dict[str, str | None]], columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.Series([r[c] for r in rows], dtype="string") for c in columns},
    )


# ---------------------------------------------------------------------------
# The module
# ---------------------------------------------------------------------------


@module(
    title="Effect Sizes in t-tests and F-tests",
    description=(
        "The Effect Size module checks if effect sizes are correctly reported in t-tests and "
        "F-tests."
    ),
    details="""
        The Effect Size check searches for regular expressions that match typical ways in which effect sizes are reported. It subsequently checks different ways in which Cohen's d, g, ηp2, and ωp2 can be computed against the reported value. If effects are missing, or might be incorrect, you the module provides a warning. The module was validated on APA reported statistical tests, and might miss effect sizes that were reported in other reporting styles. It was validated by the Metacheck team on papers published in Psychological Science.

        This module only checks statistical results reported in the running text of the manuscript. It cannot (yet) process statistics reported only in tables.

        <validation>In a sample of 161 papers with 1469 tests, this module correctly detected 1106 reported effect sizes (true positives) and correctly identified 295 cases where no effect size was present (true negatives). However, it missed 23 that were reported (false negatives), and incorrectly identified 45 effect sizes when none were reported (false positives). Among all instances detected by the module, 96% were true cases (positive predictive value). In a validation against 221 reported Cohen's d effect sizes, it correctly indicated coherence in 218 cases (99%). In a validation against 485 partial eta-squared effect sizes, it correctly indicated coherence in 480 (99%) </validation>
    """,
    keywords=["results"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={"paper": "a paper object or paperlist object"},
)
def stat_effect_size(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/stat_effect_size.R::stat_effect_size()``."""
    import numpy as np

    from pytacheck.papers import Paper
    from pytacheck.report import collapse_section, scroll_table
    from pytacheck.text import extract_eq, text_search

    # detect tests and effect sizes ----
    # extract_eq() already pulls every "name <comparator> value" out of the text,
    # so tests (t, F) and effect sizes are read from there rather than re-scanned.
    by = ["paper_id", "section_id", "paragraph_id", "text_id"]
    text_tbl = text_search(paper, "[0-9]").loc[:, [*by, "text"]]

    eq = extract_eq(paper)

    table: pd.DataFrame
    if len(eq) == 0:
        table = pd.DataFrame()
    else:
        kinds = _label_lhs(eq["lhs"].tolist(), eq["df"].tolist())
        built = _build_rows(eq, kinds)
        if built is None:
            table = pd.DataFrame()
        else:
            table = built.merge(text_tbl, on=["paper_id", "text_id"], how="left", sort=False)
            # restore the original paper/sentence order
            paper_order = {pid: i for i, pid in enumerate(pd.unique(text_tbl["paper_id"]))}
            pids = table["paper_id"].tolist()
            pos = np.array([paper_order.get(pid, np.inf) for pid in pids], dtype="float64")
            tid = np.array(
                [np.inf if _is_na(t) else float(t) for t in table["text_id"].tolist()],
                dtype="float64",
            )
            keys: tuple[Any, ...] = (tid, pos)
            if np.isinf(pos).any():
                # Papers with no sentence matching "[0-9]" are not in paper_order: R's
                # order() puts them last and keeps ties (same text_id) in the order
                # split() built them, i.e. by the factor levels of paper_id, which
                # sort() collates with ICU (extract_eq() sorted them in the C locale).
                keys = (_icu_rank(pids), *keys)
            table = table.iloc[np.lexsort(keys)].reset_index(drop=True)

    # handle no detected t-tests or F-tests ----
    if len(table) == 0:
        return {
            "table": pd.DataFrame(),
            "summary_table": _paper_id_frame(paper, Paper),
            "na_replace": 0,
            "traffic_light": "na",
            "summary_text": _NO_TESTS,
            "report": _NO_TESTS,
        }

    # Coherence checks for effect sizes in t-tests and F-tests ----
    tests = table["test"].tolist()
    test_texts = [None if _is_na(x) else x for x in table["test_text"].tolist()]
    es_list = [None if _is_na(x) else x for x in table["es"].tolist()]
    sentences = [None if _is_na(x) else x for x in table["text"].tolist()]
    rows = list(zip(tests, test_texts, es_list, sentences, strict=True))
    coherence = [_classify_d_coherence(t, tt, e) for t, tt, e, _ in rows]
    f_coherence = [_classify_f_coherence(t, tt, e, sentence=x) for t, tt, e, x in rows]
    table = pd.concat(
        [table, _string_frame(coherence, _D_COLUMNS), _string_frame(f_coherence, _F_COLUMNS)],
        axis=1,
    )

    is_t = table["test"].eq("t-test").fillna(False).to_numpy(dtype=bool)
    is_f = table["test"].eq("F-test").fillna(False).to_numpy(dtype=bool)
    no_es = table["es"].isna().to_numpy(dtype=bool)
    table_missing = table.loc[no_es]

    ## summary table ----
    summary_table = (
        pd.DataFrame(
            {
                "paper_id": table["paper_id"],
                "ttests_with_es": is_t & ~no_es,
                "ttests_without_es": is_t & no_es,
                "Ftests_with_es": is_f & ~no_es,
                "Ftests_without_es": is_f & no_es,
            }
        )
        .groupby("paper_id", sort=False, dropna=False)
        .sum()
        .reset_index()
    )

    # coherence counts (needed for traffic light and report) ----
    t_rows = table.loc[is_t & ~no_es]
    f_rows = table.loc[is_f & ~no_es]
    t_nomatch = _count_coh(t_rows["d_coherence"].tolist(), "no_match")
    f_nomatch = _count_coh(f_rows["eta_coherence"].tolist(), "no_match")
    has_nomatch = (t_nomatch + f_nomatch) > 0

    # traffic light ----
    total_n = len(table)
    noes_n = int(no_es.sum())
    if total_n == 0:
        tl = "na"
    elif noes_n == total_n or has_nomatch:
        tl = "red"
    elif noes_n > 0:
        tl = "yellow"
    else:
        tl = "green"

    # report / summary_text ----
    report: Any
    if tl == "na":
        report = _NO_TESTS
        summary_text = report
    else:
        t_match = _count_coh(t_rows["d_coherence"].tolist(), "match_under_assumptions")
        t_indet = _count_coh(t_rows["d_coherence"].tolist(), "indeterminate")
        f_match = _count_coh(f_rows["eta_coherence"].tolist(), "match_under_assumptions")
        f_indet = _count_coh(f_rows["eta_coherence"].tolist(), "indeterminate")

        coherence_text = _format_coherence_text(t_match, t_nomatch, t_indet, "t-tests", "d")
        f_coherence_text = _format_coherence_text(
            f_match, f_nomatch, f_indet, "F-tests", "eta-squared effect size"
        )

        n_missing = len(table_missing)
        has_missing = n_missing > 0

        # select cols for the report table
        report_table = table.loc[:, list(_REPORT_COLS)].rename(columns=_REPORT_COLS)
        detail_table = collapse_section(
            scroll_table(report_table, maxrows=5), "All detected and assessed stats"
        )
        detail = detail_table if isinstance(detail_table, list) else [detail_table]

        if not has_missing and not has_nomatch:
            summary_text = (
                "All detected t-tests and F-tests had an effect size reported in the same sentence."
            )
            report = [
                x for x in (summary_text, coherence_text, f_coherence_text) if x is not None
            ] + detail
        else:
            if has_missing:
                s = "" if n_missing == 1 else "s"
                summary_text = (
                    f"We found {n_missing:d} t-test{s} and/or F-test{s} where effect sizes are "
                    "not reported. Check these tests in the table below, and consider adding "
                    "effect sizes"
                )
                report_text = (
                    "We recommend checking the sentences below, and add any missing effect sizes."
                )
            else:
                summary_text = (
                    "All effect sizes were reported, but some appear inconsistent with the test "
                    "statistic. This can be because the effect size is not clearly labeled (e.g., "
                    "d, instead of d_rm), because the effect sizes is not reported with enough "
                    "precisions (e.g., 0.3 instead of 0.32), or because the effect size is "
                    "incorrectly reported."
                )
                report_text = (
                    "All tests had effect sizes, but some effect sizes do not match the reported "
                    "test statistic."
                )
            report = [x for x in (report_text, coherence_text, f_coherence_text) if x is not None]
            if has_missing:
                report.append(scroll_table(table_missing["text"].tolist()))
            report.append(collapse_section(_GUIDANCE))
            report.extend(detail)

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
