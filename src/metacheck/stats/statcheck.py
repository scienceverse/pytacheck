"""Port of the statcheck R package (version 1.5.0), which metacheck's ``stats()`` wraps.

statcheck (Nuijten & Epskamp) extracts APA-style null-hypothesis significance
tests (``t(28) = 2.20, p = .03``) from text with regular expressions,
recomputes the p-value from the test statistic and degrees of freedom, and
flags inconsistencies (``error``) and inconsistencies that change the
significance decision (``decision_error``).

The functions mirror statcheck's (``extract_stats``, ``extract_df``,
``extract_test_stats``, ``extract_p_value``, ``compute_p``, ``error_test``,
``decision_error_test``, ``process_stats``, ``calc_APA_factor`` ...), and every
regular expression runs on the engine R runs it on (PCRE for
``extract_pattern()`` and the ``perl = TRUE`` substitutions, TRE for the
``grepl``/``gsub``/``strsplit``/``regexpr`` calls). The arithmetic is plain
Python: a missing value is ``None``, p-values come from :mod:`scipy.special`,
and R's warnings are not reproduced.

**Not checkable is not consistent** (docs/UPSTREAM_ISSUES.md D78). A result
whose p-value cannot be computed (zero degrees of freedom in a t, r or F
test, an infinite F degree of freedom) or whose consistency cannot be
decided (an unparseable p-value such as ``p = .05-.10``) is dropped;
statcheck failed the whole call on such results, or reported some of them
as consistent. Infinite degrees of freedom follow scipy: a t test (and a
correlation) uses the normal distribution, and a chi-square or Q test's
p-value is its limit, 1; scipy's F distribution has no value there, so an F
test with an infinite degree of freedom is not checkable.

Where statcheck 1.5.0 is wrong, pytacheck also fixes it (U5, U149): a result
without a test name is skipped instead of failing the call; each result keeps
its own p-value (statcheck could give one result another's); a correlation's
rounding interval stops at +-1 (``r = 1.00``); the Q-test subtype is read from
the ``Q`` token (``Q-Between`` is ``Qb``); ``ns`` is recognised by the engine
that found it; and invalid flag values are rejected up front.

The file-reading front ends (``checkPDF``, ``checkHTML``, ``checkdir`` ...),
the plotting methods and ``statcheckReport()`` are not ported: metacheck
never calls them.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from metacheck._r import r_round
from metacheck._r.regex import compile_r, gsub, strsplit
from metacheck._values import as_float

__all__ = [
    "calc_APA_factor",
    "compute_p",
    "decision_error_test",
    "error_test",
    "extract_1tail",
    "extract_df",
    "extract_p_value",
    "extract_pattern",
    "extract_stats",
    "extract_test_stats",
    "process_stats",
    "r2t",
    "recover_minus_sign",
    "remove_1000_sep",
    "statcheck",
    "summary_statcheck",
    "trim",
]

# ---------------------------------------------------------------------------
# Constants (statcheck R/constants.R and R/regex.R), as R strings after
# string-literal unescaping.
# ---------------------------------------------------------------------------

VAR_SOURCE = "source"
VAR_TYPE = "test_type"
VAR_DF1 = "df1"
VAR_DF2 = "df2"
VAR_TEST_COMPARISON = "test_comp"
VAR_TEST_VALUE = "test_value"
VAR_P_COMPARISON = "p_comp"
VAR_REPORTED_P = "reported_p"
VAR_P_DEC = "p_decimals"
VAR_COMPUTED_P = "computed_p"
VAR_RAW = "raw"
VAR_ERROR = "error"
VAR_DEC_ERROR = "decision_error"
VAR_1TAILTXT = "one_tailed_in_txt"
VAR_APAFACTOR = "apa_factor"
VAR_NR_PVALUES = "nr_p_values"
VAR_NR_ERRORS = "nr_errors"
VAR_NR_DEC_ERRORS = "nr_decision_errors"

RGX_T = r"t"
RGX_R = r"r"
RGX_Q = r"Q\s?-?\s?(w|W|(w|W)ithin|b|B|(b|B)etween)?"
RGX_F = r"F"
RGX_CHI2 = r"((\s[^trFzZQWnD ]\s?)|([^trFzZQWnD ]2\s?))2?"
RGX_Z = r"([^a-z](z|Z))"
RGX_DF_T_R_Q = r"\(\s?\d*\.?\d+\s?\)"
RGX_DF_F = r"\(\s?\d*\.?(I|l|\d+)\s?,\s?\d*\.?\d+\s?\)"
RGX_DF_CHI2 = r"\(\s?\d*\.?\d+\s?(,\s?(N|n)\s?\=\s?\d*\,?\d*\,?\d+\s?)?\)"
RGX_T_DF = rf"({RGX_T}\s?{RGX_DF_T_R_Q})"
RGX_R_DF = rf"({RGX_R}\s?{RGX_DF_T_R_Q})"
RGX_Q_DF = rf"({RGX_Q}\s?{RGX_DF_T_R_Q})"
RGX_F_DF = rf"({RGX_F}\s?{RGX_DF_F})"
RGX_CHI2_DF = rf"({RGX_CHI2}\s?{RGX_DF_CHI2})"
RGX_TEST_DF = rf"({RGX_T_DF}|{RGX_R_DF}|{RGX_Q_DF}|{RGX_F_DF}|{RGX_CHI2_DF}|{RGX_Z})"
RGX_TEST_VALUE = r"[<>=]\s?[^a-zA-Z\d\.]{0,3}\s?\d*,?\d*\.?\d+\s?,"
RGX_NS = r"([^a-z]n\.?s\.?)"
RGX_P = r"(p\s?[<>=]\s?\d?\.\d+e?-?\d*)"
RGX_P_NS = rf"({RGX_NS}|{RGX_P})"
RGX_NHST = rf"{RGX_TEST_DF}\s?{RGX_TEST_VALUE}\s?{RGX_P_NS}"
RGX_OPEN_BRACKET = r"(.+?(?=\())"
RGX_TEST_TYPE = rf"{RGX_Z}|{RGX_OPEN_BRACKET}"
RGX_DF = rf"({RGX_DF_T_R_Q})|({RGX_DF_F})|({RGX_DF_CHI2})"
RGX_DF1_I_L = r"I|l"
RGX_COMP = r"[<>=]"
RGX_DEC = r"\.\d+"
RGX_1000_SEP = r"(?<=\d),(?=\d+)"
RGX_WEIRD_MINUS = r"\s?[^\d\.\s]+(?=\d|\.)"

_ALL_STATS = ("t", "F", "cor", "chisq", "Z", "Q")
_TEST_TYPES = ("t", "F", "Z", "r", "Chi2", "Q", "Qb", "Qw")

# R's trimws() default whitespace: "[ \t\r\n]"
_WS = " \t\r\n"

# TRE (perl = FALSE) patterns used by grepl()/gsub()/regexpr() in statcheck.
_TRE_Q = compile_r(RGX_Q, posix=False)
_TRE_Z = compile_r(RGX_Z, posix=False)
_TRE_CHI2 = compile_r(RGX_CHI2, posix=False)
_TRE_DF1_I_L = compile_r(RGX_DF1_I_L, posix=False)
_TRE_DEC = compile_r(RGX_DEC)

_PCRE_NS_ICASE = compile_r(RGX_NS, True, True)
_TRE_1TAIL = compile_r("one.?sided|one.?tailed|directional", True, posix=False)
_NHST = compile_r(RGX_NHST, False, True)


def _pcre(pattern: str, ignore_case: bool) -> Any:
    """``gregexpr(pattern, perl = TRUE, ignore.case)``'s compiled pattern."""
    return compile_r(pattern, ignore_case, True)


def _missing(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def _float(x: Any) -> float:
    """A number for scipy: a missing value is NaN."""
    return math.nan if _missing(x) else float(x)


def _decimals(s: str) -> float:
    """The number of decimals of a number as written (``.05`` has 2)."""
    m = _TRE_DEC.search(s)
    return float(len(m.group(0)) - 1) if m else 0.0


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_pattern(
    txt: str | Sequence[str | None] | None, pattern: str, ignore_case: bool = True
) -> list[str | None] | None:
    """Port of ``statcheck:::extract_pattern()``: all PCRE matches, or ``None`` (R ``NULL``).

    Like R, the match positions come from the *first* element of *txt* and
    are then applied (recycled) to every element with ``substring()``.
    statcheck's own patterns are matched case-insensitively exactly as PCRE2
    does; for other patterns ``ignore_case`` uses the ``regex`` module's
    folding, which differs from PCRE2 only for U+0130/U+0131 (dotted/dotless i).
    """
    texts: list[str | None] = [txt] if isinstance(txt, str) or txt is None else list(txt)
    if not texts:
        raise ValueError("extract_pattern(): `txt` is empty")
    first = texts[0]
    if first is None:
        return None
    spans = [(m.start(), m.end()) for m in _finditer(_pcre(pattern, ignore_case), first)]
    if not spans:
        return None
    out: list[str | None] = []
    for i in range(max(len(texts), len(spans))):
        s = texts[i % len(texts)]
        start, end = spans[i % len(spans)]
        out.append(None if s is None else s[start:end])
    return out


def _extract(txt: str | None, pattern: str, ignore_case: bool = True) -> list[str] | None:
    """``extract_pattern()`` for a single string (``None`` is R's ``NULL``)."""
    if txt is None:
        return None
    found = [m.group(0) for m in _finditer(_pcre(pattern, ignore_case), txt)]
    return found or None


def _finditer(rx: Any, s: str) -> list[Any]:
    """gregexpr() matches: non-overlapping, no empty match right after a match."""
    out = []
    prev_end = -1
    for m in rx.finditer(s):
        if m.start() == m.end() == prev_end:
            continue
        out.append(m)
        prev_end = m.end() if m.end() > m.start() else -1
    return out


def remove_1000_sep(raw: Any) -> Any:
    """Port of ``statcheck:::remove_1000_sep()``."""
    return gsub(RGX_1000_SEP, "", raw, perl=True)


def recover_minus_sign(raw: Any) -> Any:
    """Port of ``statcheck:::recover_minus_sign()``."""
    return gsub(RGX_WEIRD_MINUS, " -", raw, perl=True)


def extract_1tail(txt: str | None) -> bool | None:
    """Port of ``statcheck:::extract_1tail()``: is a one-sided test mentioned?"""
    return None if txt is None else _TRE_1TAIL.search(txt) is not None


def _extract_df(raw: str, test_type: str) -> tuple[float | None, float | None] | None:
    """``(df1, df2)`` of one result; ``None`` when it has no degrees of freedom."""
    if test_type == "Z":
        return None, None
    found = _extract(raw, RGX_DF)
    if found is None:
        return None
    df = [s.strip(_WS) for s in strsplit(gsub(r"\(|\)", "", found[0]), ",")]
    if test_type == "F" and df and _TRE_DF1_I_L.search(df[0]):
        df[0] = "1"  # F(I, 20): an OCR'd 1
    if test_type in ("t", "r"):
        df1, df2 = None, df[0] if len(df) == 1 else None
    elif test_type == "F":
        df1, df2 = df[0] if df else None, df[1] if len(df) > 1 else None
    else:
        df1, df2 = df[0] if df else None, None
    return (None if df1 is None else as_float(df1)), (None if df2 is None else as_float(df2))


def extract_df(raw: str, test_type: str) -> pd.DataFrame:
    """Port of ``statcheck:::extract_df()``: degrees of freedom of one raw result."""
    found = _extract_df(raw, test_type)
    if found is None:
        raise ValueError(f"no degrees of freedom in {raw!r}")
    return pd.DataFrame({"df1": [found[0]], "df2": [found[1]]}, dtype="float64")


@dataclass(slots=True)
class _Test:
    comp: str
    value: float | None
    dec: float


def _test_stats(raw: str) -> list[_Test]:
    """The test values of one raw result: comparison, value and decimals.

    Empty when there is none, or when R's ``data.frame()`` could not recycle
    the comparisons and values into rows.
    """
    test_raw = _extract(gsub(RGX_DF_CHI2, "", raw), RGX_TEST_VALUE)
    if test_raw is None:
        return []
    # as in R, the comparisons' positions come from the first value
    comps = [c for c in extract_pattern(test_raw, RGX_COMP) or [] if c is not None]
    values = recover_minus_sign(remove_1000_sep(gsub(RGX_COMP, "", test_raw)))
    values = gsub(",$", "", [v.strip(_WS) for v in values])
    n = max(len(comps), len(values))
    if not comps or n % len(comps) or n % len(values):
        return []
    return [
        _Test(comps[i % len(comps)], as_float(v), _decimals(v))
        for i, v in ((i, values[i % len(values)]) for i in range(n))
    ]


def extract_test_stats(raw: str) -> pd.DataFrame:
    """Port of ``statcheck:::extract_test_stats()``: comparison, value and decimals."""
    tests = _test_stats(raw)
    if not tests:
        raise ValueError(f"no test statistic in {raw!r}")
    return pd.DataFrame(
        {
            "test_comp": pd.Series([t.comp for t in tests], dtype="string"),
            "test_value": pd.Series([t.value for t in tests], dtype="float64"),
            "test_dec": pd.Series([t.dec for t in tests], dtype="float64"),
        }
    )


@dataclass(slots=True)
class _P:
    comp: str | None
    value: float | None
    dec: float | None


def _extract_p_value(raw: str | None) -> list[_P]:
    out = []
    for pr in _extract(raw, RGX_P_NS) or []:
        # "ns" is tested with the engine that found it: statcheck's TRE test
        # disagreed with PCRE on case folding (a long s, "nſ"; U149)
        if _PCRE_NS_ICASE.search(pr):
            out.append(_P("ns", None, None))
            continue
        comp = _extract(pr, RGX_COMP)
        parts = strsplit(pr, RGX_COMP)
        value = parts[1].strip(_WS) if len(parts) > 1 else ""
        out.append(_P(comp[0] if comp else None, as_float(value), _decimals(value)))
    return out


def extract_p_value(raw: str | None) -> pd.DataFrame:
    """Port of ``statcheck:::extract_p_value()``: every p-value (or ``ns``) in *raw*."""
    ps = _extract_p_value(raw)
    return pd.DataFrame(
        {
            "p_comp": pd.Series([p.comp for p in ps], dtype="string"),
            "p_value": pd.Series([p.value for p in ps], dtype="float64"),
            "p_dec": pd.Series([p.dec for p in ps], dtype="float64"),
        }
    )


@dataclass(slots=True)
class _Row:
    raw: str
    statistic: str
    df1: float | None
    df2: float | None
    test_comp: str
    value: float
    testdec: float | None
    p_comp: str
    p_value: float | None
    dec: float | None


def _test_type(test_raw: list[str] | None) -> str | None:
    """The if/else chain of ``extract_stats()`` classifying one result (``None``: unknown).

    Differs from statcheck 1.5.0 (U5): no test name (``MZ = 2.1``) is an
    unclassified result rather than an error; with several candidates
    (``t(20) = (2.1``) the first, which the result starts with, is used
    rather than failing on a length-2 ``if ()``; and the Q-test subtype is
    read from the ``Q`` token's suffix, ignoring case (statcheck looks for a
    lowercase ``b`` and then ``w`` anywhere, so ``Q-Between`` became ``Qw``
    and ``QWithin`` plain ``Q``).
    """
    if not test_raw:
        return None
    tr = test_raw[0]
    q = _TRE_Q.search(tr)
    if q:
        sub = (q.group(1) or "")[:1].lower()
        return {"b": "Qb", "w": "Qw"}.get(sub, "Q")
    if RGX_T in tr:
        return "t"
    if RGX_F in tr:
        return "F"
    if RGX_R in tr:
        return "r"
    if _TRE_Z.search(tr):
        return "Z"
    if _TRE_CHI2.search(tr):
        return "Chi2"
    return None


def _stat_family(statistic: str) -> str:
    return {"r": "cor", "Chi2": "chisq", "Qw": "Q", "Qb": "Q"}.get(statistic, statistic)


def _extract_stats_rows(txt: str | None, stat: Sequence[str]) -> list[_Row] | None:
    """``extract_stats()`` as a list of rows; ``None`` when *txt* has no NHST result.

    Differs from statcheck 1.5.0 (U5): a result that cannot be parsed (no or
    several test names, no degrees of freedom, no or several test values,
    no or several p-values) is skipped instead of failing the whole text,
    and every result keeps its own p-value. statcheck collects the p-values
    of all results in one vector and ``data.frame()`` recycles it when a
    result has none (its case-insensitive ``RGX_P_NS`` misses ``Nns`` that
    the case-sensitive NHST pattern accepted), which gave results each
    other's p-values.
    """
    nhst_raw = _extract(txt, RGX_NHST, ignore_case=False)
    if nhst_raw is None:
        return None
    rows: list[_Row] = []
    for raw in nhst_raw:
        test_type = _test_type(_extract(raw, RGX_TEST_TYPE))
        if test_type is None or _stat_family(test_type) not in stat:
            continue
        df = _extract_df(raw, test_type)
        tests = _test_stats(raw)
        ps = _extract_p_value(raw)
        if df is None or len(tests) != 1 or len(ps) != 1:
            continue
        (test,), (p,) = tests, ps
        if test.value is None or test.comp is None or p.comp is None:
            continue
        if p.value is not None and p.value > 1:
            continue
        if test_type == "r" and not -1 <= test.value <= 1:
            continue
        row = _Row(
            raw.strip(_WS),
            test_type,
            df[0],
            df[1],
            test.comp,
            test.value,
            test.dec,
            p.comp,
            p.value,
            p.dec,
        )
        rows.append(row)
    return rows


def extract_stats(txt: str | None, stat: str | Iterable[str] = _ALL_STATS) -> pd.DataFrame:
    """Port of ``statcheck:::extract_stats()``: parse every APA result in *txt*."""
    rows = _extract_stats_rows(txt, _stat_arg(stat))
    if rows is None:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "Raw": pd.Series([r.raw for r in rows], dtype="string"),
            "Statistic": pd.Series([r.statistic for r in rows], dtype="string"),
            "df1": pd.Series([r.df1 for r in rows], dtype="float64"),
            "df2": pd.Series([r.df2 for r in rows], dtype="float64"),
            "Test.Comparison": pd.Series([r.test_comp for r in rows], dtype="string"),
            "Value": pd.Series([r.value for r in rows], dtype="float64"),
            "testdec": pd.Series([r.testdec for r in rows], dtype="float64"),
            "Reported.Comparison": pd.Series([r.p_comp for r in rows], dtype="string"),
            "Reported.P.Value": pd.Series([r.p_value for r in rows], dtype="float64"),
            "dec": pd.Series([r.dec for r in rows], dtype="float64"),
        }
    )


def _stat_arg(stat: str | Iterable[str]) -> tuple[str, ...]:
    return (stat,) if isinstance(stat, str) else tuple(stat)


# ---------------------------------------------------------------------------
# p-value computation and error tests
# ---------------------------------------------------------------------------


def r2t(r: float | None, df: float | None) -> float:
    """Port of ``statcheck:::r2t()``: a correlation as a t statistic (IEEE arithmetic)."""
    import numpy as np

    with np.errstate(all="ignore"):
        return float(np.float64(_float(r)) / np.sqrt((1 - np.float64(_float(r)) ** 2) / _float(df)))


def _t_two_sided(x: Any, n: Any) -> Any:
    """``P(|T| >= |x|)`` for Student's t with *n* degrees of freedom (numpy floats).

    The reductions to the regularized incomplete beta function that R's
    ``pt()`` uses: on the realistic corpus they are closer to the exact value
    than ``scipy.special.stdtr`` (whose last digits differ). With infinite
    *n* the t distribution is the normal distribution (scipy's ``stdtr``
    gives the same limit).
    """
    from scipy import special

    x = abs(x)
    if not n > 0:
        return math.nan
    if math.isinf(n):
        return 2 * special.ndtr(-x)
    if n > x * x:
        return special.betaincc(0.5, n / 2, x * x / (n + x * x))
    return special.betainc(n / 2, 0.5, 1 / (1 + (x / n) * x))


def _f_upper(x: Any, df1: Any, df2: Any) -> Any:
    """``P(F >= x)`` (numpy floats), as R's ``pf(lower.tail = FALSE)`` reduces it.

    NaN, as ``scipy.special.fdtrc`` gives, for zero or infinite degrees of
    freedom; 1 for a statistic below the distribution's support (``x <= 0``).
    """
    from scipy import special

    if not (0 < df1 < math.inf and 0 < df2 < math.inf):
        return math.nan
    if x <= 0:
        return 1.0
    if df1 * x > df2:
        return special.betainc(df2 / 2, df1 / 2, df2 / (df2 + df1 * x))
    return special.betaincc(df1 / 2, df2 / 2, df1 * x / (df2 + df1 * x))


def compute_p(
    test_type: str,
    test_stat: float | None,
    df1: float | None,
    df2: float | None,
    two_tailed: bool,
) -> float | None:
    """Port of ``statcheck:::compute_p()``: the p-value implied by a test statistic.

    ``None`` when the p-value cannot be computed: a missing value, zero
    degrees of freedom (except for chi-square, whose p is then 0) or an
    infinite F degree of freedom.
    A t test (or correlation) with infinite degrees of freedom uses the
    normal distribution, and a chi-square or Q test gets its limit, 1 (D78).
    """
    import numpy as np
    from scipy import special

    if test_type not in _TEST_TYPES:
        raise ValueError(f"`test_type` must be one of {', '.join(_TEST_TYPES)}, not {test_type!r}")
    x, d1, d2 = (np.float64(_float(v)) for v in (test_stat, df1, df2))
    with np.errstate(all="ignore"):
        if test_type in ("t", "r"):
            t = x if test_type == "t" else np.float64(r2t(x, d2))
            p = _t_two_sided(t, d2) / 2
        elif test_type == "F":
            p = _f_upper(x, d1, d2)
        elif test_type == "Z":
            p = special.ndtr(-abs(x))
        else:
            p = special.chdtrc(d1, np.maximum(x, 0.0))  # P(X >= x) = 1 below zero
    p = float(p)
    if math.isnan(p):
        return None
    return p * 2 if two_tailed and test_type in ("t", "Z", "r") else p


def _ns(reported_p: float | None, p_comparison: str, alpha: float) -> tuple[float | None, str]:
    """``ns`` is ``p > alpha``."""
    return (alpha, ">") if p_comparison == "ns" else (reported_p, p_comparison)


def error_test(
    reported_p: float | None,
    test_type: str,
    test_stat: float | None,
    df1: float | None,
    df2: float | None,
    p_comparison: str,
    test_comparison: str,
    p_dec: float | None,
    test_dec: float | None,
    two_tailed: bool,
    alpha: float,
    pZeroError: bool,
) -> bool | None:
    """Port of ``statcheck:::error_test()``: is the reported p inconsistent?

    ``None`` when that cannot be decided: a value is missing or the p-value
    cannot be computed (D78).
    """
    rp, pc = _ns(reported_p, p_comparison, alpha)
    if rp is None or _missing(rp):
        return None
    if pZeroError and rp <= 0:
        return True
    if test_stat is None or test_dec is None or _missing(test_stat) or _missing(test_dec):
        return None
    # the statistic's rounding interval; low_stat is the end nearer zero
    half = 0.5 * 10.0 ** -float(test_dec)
    low_stat, up_stat = test_stat - half, test_stat + half
    if test_stat < 0:
        low_stat, up_stat = up_stat, low_stat
    if test_type == "r":
        # a correlation cannot pass +-1: r = 1.00 is anything in [.995, 1] (U5)
        low_stat, up_stat = (max(-1.0, min(1.0, x)) for x in (low_stat, up_stat))
    up_p = compute_p(test_type, low_stat, df1, df2, two_tailed)
    low_p = compute_p(test_type, up_stat, df1, df2, two_tailed)
    if up_p is None or low_p is None:
        return None
    if (test_comparison, pc) in (("<", ">"), (">", "<")):
        return False
    if pc == "=":
        if p_dec is None or _missing(p_dec):
            return None
        up_p, low_p = r_round(up_p, p_dec), r_round(low_p, p_dec)
    checks = {
        ("=", "="): lambda: rp > up_p or rp < low_p,
        ("=", "<"): lambda: rp < low_p,
        ("=", ">"): lambda: rp > up_p,
        ("<", "="): lambda: rp < up_p,
        ("<", "<"): lambda: rp < up_p,
        (">", "="): lambda: rp > low_p,
        (">", ">"): lambda: rp > low_p,
    }
    check = checks.get((test_comparison, pc))
    return None if check is None else bool(check())


# statcheck's decision-error rules, by (pEqualAlphaSig, test comparison, p
# comparison): f(reported p, computed p, alpha) is True for a decision error.
_DECISION = {
    (True, "=", "="): lambda rp, cp, a: (rp <= a < cp) or (cp <= a < rp),
    (True, "=", "<"): lambda rp, cp, a: rp <= a < cp,
    (True, "=", ">"): lambda rp, cp, a: rp >= a >= cp,
    (True, "<", "="): lambda rp, cp, a: rp <= a <= cp,
    (True, "<", "<"): lambda rp, cp, a: rp <= a <= cp,
    (True, ">", "="): lambda rp, cp, a: rp > a >= cp,
    (True, ">", ">"): lambda rp, cp, a: rp >= a >= cp,
    (False, "=", "="): lambda rp, cp, a: (rp < a <= cp) or (cp < a <= rp),
    (False, "=", "<"): lambda rp, cp, a: rp <= a <= cp,
    (False, "=", ">"): lambda rp, cp, a: rp >= a > cp,
    (False, "<", "="): lambda rp, cp, a: rp < a <= cp,
    (False, "<", "<"): lambda rp, cp, a: rp <= a <= cp,
    (False, ">", "="): lambda rp, cp, a: rp >= a >= cp,
    (False, ">", ">"): lambda rp, cp, a: rp >= a >= cp,
}


def decision_error_test(
    reported_p: float | None,
    computed_p: float | None,
    test_comparison: str,
    p_comparison: str,
    alpha: float,
    pEqualAlphaSig: bool,
) -> bool | None:
    """Port of ``statcheck:::decision_error_test()``: does the inconsistency change significance?

    ``None`` when that cannot be decided (a missing value). A *pEqualAlphaSig*
    other than ``TRUE``/``FALSE`` raises :class:`ValueError`.
    """
    _check_flags(pEqualAlphaSig=pEqualAlphaSig)
    rp, pc = _ns(reported_p, p_comparison, alpha)
    if (test_comparison, pc) in (("<", ">"), (">", "<")):
        return False
    if _missing(rp) or _missing(computed_p) or _missing(alpha):
        return None
    rule = _DECISION.get((bool(pEqualAlphaSig), test_comparison, pc))
    return None if rule is None else bool(rule(rp, computed_p, alpha))


@dataclass(slots=True)
class _Flags:
    """statcheck()'s settings that decide a result."""

    alpha: float
    pZeroError: bool
    pEqualAlphaSig: bool
    OneTailedTxt: bool = False
    OneTailedTests: bool = False


def _decide(r: _Row, two_tailed: bool, f: _Flags) -> tuple[float | None, bool | None, bool | None]:
    """``(computed_p, error, decision_error)`` for one tail setting."""
    computed_p = compute_p(r.statistic, r.value, r.df1, r.df2, two_tailed)
    error = error_test(
        r.p_value,
        r.statistic,
        r.value,
        r.df1,
        r.df2,
        r.p_comp,
        r.test_comp,
        r.dec,
        r.testdec,
        two_tailed,
        f.alpha,
        f.pZeroError,
    )
    if not error:
        return computed_p, error, error
    decision = decision_error_test(
        r.p_value, computed_p, r.test_comp, r.p_comp, f.alpha, f.pEqualAlphaSig
    )
    return computed_p, error, decision


def _process_stats(
    r: _Row, one_tailed_in_txt: bool, two_tailed: bool, f: _Flags
) -> tuple[float | None, bool | None, bool | None]:
    """``process_stats()`` as a tuple ``(computed_p, error, decision_error)``."""
    out = _decide(r, two_tailed, f)
    if f.OneTailedTxt and not f.OneTailedTests and out[1] and one_tailed_in_txt:
        # an error in a text that mentions one-sided testing: is it one-tailed?
        one_tailed = _decide(r, False, f)
        if one_tailed[1] is not None and one_tailed[1] != out[1]:
            return one_tailed
    return out


def process_stats(
    test_type: str,
    test_stat: float,
    df1: float | None,
    df2: float | None,
    reported_p: float | None,
    p_comparison: str,
    test_comparison: str,
    p_dec: float | None,
    test_dec: float | None,
    OneTailedInTxt: bool,
    two_tailed: bool,
    alpha: float,
    pZeroError: bool,
    pEqualAlphaSig: bool,
    OneTailedTxt: bool,
    OneTailedTests: bool,
) -> pd.DataFrame:
    """Port of ``statcheck:::process_stats()``: recompute p, test for (decision) errors.

    Returns a one-row DataFrame with ``computed_p``, ``error`` and
    ``decision_error`` (missing when they cannot be computed); with
    ``OneTailedTxt=True`` an error in a text that mentions one-sided testing
    is re-evaluated as a one-tailed test.
    """
    _check_flags(
        pZeroError=pZeroError,
        pEqualAlphaSig=pEqualAlphaSig,
        OneTailedTxt=OneTailedTxt,
        OneTailedTests=OneTailedTests,
    )
    row = _Row(
        "",
        test_type,
        df1,
        df2,
        test_comparison,
        test_stat,
        test_dec,
        p_comparison,
        reported_p,
        p_dec,
    )
    flags = _Flags(
        alpha, bool(pZeroError), bool(pEqualAlphaSig), bool(OneTailedTxt), bool(OneTailedTests)
    )
    computed_p, error, decision_error = _process_stats(row, OneTailedInTxt, two_tailed, flags)
    return pd.DataFrame(
        {
            "computed_p": pd.Series([computed_p], dtype="float64"),
            "error": pd.Series([error], dtype="boolean"),
            "decision_error": pd.Series([decision_error], dtype="boolean"),
        }
    )


# ---------------------------------------------------------------------------
# statcheck()
# ---------------------------------------------------------------------------


def _apa_factors(sources: list[str], p_sources: list[str]) -> list[float]:
    """``calc_APA_factor()`` for result sources vs p-value sources."""
    n_res, n_p = Counter(sources), Counter(p_sources)
    return [float(r_round(n_res[s] / n_p[s], 2)) for s in sources]


def calc_APA_factor(pRes: pd.DataFrame, Res: pd.DataFrame) -> list[float]:
    """Port of ``statcheck:::calc_APA_factor()``.

    The share of the p-values in each source that are part of a complete
    APA-reported result, rounded to 2 decimals, for each row of *Res*.
    """
    return _apa_factors(
        [str(s) for s in Res["Source"].tolist()], [str(s) for s in pRes["Source"].tolist()]
    )


@dataclass(slots=True)
class _Result:
    source: Any
    row: _Row
    one_tailed: bool
    computed: float
    error: bool
    decision_error: bool
    apa: float = math.nan


def _check_texts(
    items: Iterable[tuple[Any, str | None]], stat: Sequence[str], flags: _Flags
) -> tuple[list[_Result], list[tuple[Any, _P]]]:
    """The body of ``statcheck()`` for (source, text) pairs.

    A result whose p-value cannot be computed, or whose consistency cannot be
    decided, is dropped (D78; statcheck failed on ``if (NA)`` or reported it
    as consistent), and the other results of the text are kept.
    """
    results: list[_Result] = []
    pres: list[tuple[Any, _P]] = []
    for source, txt in items:
        pres.extend((source, p) for p in _extract_p_value(txt))
        rows = _extract_stats_rows(txt, stat)
        if not rows:
            continue
        one_tailed = bool(extract_1tail(txt))
        for r in rows:
            computed, error, decision = _process_stats(
                r, one_tailed, not flags.OneTailedTests, flags
            )
            if computed is None or error is None or decision is None:
                continue
            results.append(_Result(source, r, one_tailed, computed, error, decision))
    apa = _apa_factors([str(r.source) for r in results], [str(s) for s, _ in pres])
    for res, a in zip(results, apa, strict=True):
        res.apa = a
    return results, pres


def _results_columns(results: list[_Result]) -> dict[str, pd.Series]:
    """statcheck's output columns (without ``source``), R's names and order."""
    rows = [r.row for r in results]
    return {
        VAR_TYPE: pd.Series([r.statistic for r in rows], dtype="string"),
        VAR_DF1: pd.Series([r.df1 for r in rows], dtype="float64"),
        VAR_DF2: pd.Series([r.df2 for r in rows], dtype="float64"),
        VAR_TEST_COMPARISON: pd.Series([r.test_comp for r in rows], dtype="string"),
        VAR_TEST_VALUE: pd.Series([r.value for r in rows], dtype="float64"),
        VAR_P_COMPARISON: pd.Series([r.p_comp for r in rows], dtype="string"),
        VAR_REPORTED_P: pd.Series([r.p_value for r in rows], dtype="float64"),
        VAR_COMPUTED_P: pd.Series([r.computed for r in results], dtype="float64"),
        VAR_RAW: pd.Series([r.raw for r in rows], dtype="string"),
        VAR_ERROR: pd.Series([r.error for r in results], dtype="boolean"),
        VAR_DEC_ERROR: pd.Series([r.decision_error for r in results], dtype="boolean"),
        VAR_1TAILTXT: pd.Series([r.one_tailed for r in results], dtype="boolean"),
        VAR_APAFACTOR: pd.Series([r.apa for r in results], dtype="float64"),
    }


def _pvalue_columns(pres: list[tuple[Any, _P]]) -> dict[str, pd.Series]:
    ps = [p for _, p in pres]
    return {
        VAR_P_COMPARISON: pd.Series([p.comp for p in ps], dtype="string"),
        VAR_REPORTED_P: pd.Series([p.value for p in ps], dtype="float64"),
        VAR_P_DEC: pd.Series([p.dec for p in ps], dtype="float64"),
    }


def _source_names(texts: Any) -> tuple[list[str], list[str | None]]:
    names: list[str] | None = None
    if isinstance(texts, str) or texts is None:
        values: list[Any] = [texts]
    elif isinstance(texts, Mapping):
        names, values = [str(k) for k in texts], list(texts.values())
    elif isinstance(texts, pd.Series):
        # a named character vector: string labels are names, a numeric index is not
        values = texts.tolist()
        if texts.index.inferred_type in ("string", "unicode"):
            names = [str(k) for k in texts.index]
    else:
        values = list(texts)
    values = [None if _missing(v) else str(v) for v in values]
    if names is None:
        # formatC(seq_along(texts), width = ceiling(log10(n)), flag = "0"): at least 2 wide
        width = math.ceil(math.log10(max(len(values), 1))) or 2
        names = [f"{i:0{width}d}" for i in range(1, len(values) + 1)]
    return names, values


def _is_flag(value: Any) -> bool:
    try:
        return value in (True, False)
    except TypeError:  # pd.NA
        return False


def _check_flags(**flags: Any) -> None:
    for name, value in flags.items():
        if not _is_flag(value):
            raise ValueError(f"`{name}` must be TRUE or FALSE, not {value!r}")


def _check_args(
    OneTailedTests: Any,
    alpha: Any,
    pEqualAlphaSig: Any,
    pZeroError: Any,
    OneTailedTxt: Any,
    AllPValues: Any,
    messages: Any = False,
) -> _Flags:
    """Reject invalid arguments up front (U149, D78), and return the settings.

    statcheck 1.5.0 failed half-way on an ``NA`` flag or ``alpha`` (on some
    texts only) and compared other flag values with ``== TRUE``; here every
    flag must be ``True``/``False`` (or 1/0) and ``alpha`` a number.
    """
    _check_flags(
        OneTailedTests=OneTailedTests,
        pEqualAlphaSig=pEqualAlphaSig,
        pZeroError=pZeroError,
        OneTailedTxt=OneTailedTxt,
        AllPValues=AllPValues,
        messages=messages,
    )
    try:
        a = math.nan if isinstance(alpha, bool | str) else float(alpha)
    except (TypeError, ValueError):
        a = math.nan
    if math.isnan(a):
        raise ValueError(f"`alpha` must be a number, not {alpha!r}")
    return _Flags(
        a, bool(pZeroError), bool(pEqualAlphaSig), bool(OneTailedTxt), bool(OneTailedTests)
    )


def statcheck(
    texts: str | Sequence[str | None] | Mapping[str, str] | pd.Series,
    stat: str | Iterable[str] = _ALL_STATS,
    OneTailedTests: bool = False,
    alpha: float = 0.05,
    pEqualAlphaSig: bool = True,
    pZeroError: bool = True,
    OneTailedTxt: bool = False,
    AllPValues: bool = False,
    messages: bool = True,
) -> pd.DataFrame | None:
    """Port of ``statcheck::statcheck()`` (statcheck 1.5.0).

    Extract APA-reported NHST results (t, F, r, chi-square, Z and Q tests)
    from *texts* (a string, a sequence of strings, a mapping of source names
    to strings, or a pandas Series whose index holds the names -- R's named
    character vector), recompute their p-values and flag inconsistencies.

    Returns a DataFrame with columns ``source``, ``test_type``, ``df1``,
    ``df2``, ``test_comp``, ``test_value``, ``p_comp``, ``reported_p``,
    ``computed_p``, ``raw``, ``error``, ``decision_error``,
    ``one_tailed_in_txt`` and ``apa_factor``; with ``AllPValues=True``, every
    p-value (``source``, ``p_comp``, ``reported_p``, ``p_decimals``). Like R,
    returns ``None`` (and prints a message) when nothing is found. Results
    that cannot be checked are left out (D78).
    """
    names, values = _source_names(texts)
    flags = _check_args(
        OneTailedTests, alpha, pEqualAlphaSig, pZeroError, OneTailedTxt, AllPValues, messages
    )
    results, pres = _check_texts(zip(names, values, strict=True), _stat_arg(stat), flags)
    if not AllPValues:
        if results:
            cols = {VAR_SOURCE: pd.Series([r.source for r in results], dtype="string")}
            cols.update(_results_columns(results))
            return pd.DataFrame(cols)
        print("statcheck did not find any results")  # R cat()s this regardless of messages
        return None
    if pres:
        cols = {VAR_SOURCE: pd.Series([s for s, _ in pres], dtype="string")}
        cols.update(_pvalue_columns(pres))
        return pd.DataFrame(cols)
    print("statcheck did not find any p-values")
    return None


def _has_comparator(txt: str) -> bool:
    return "=" in txt or "<" in txt or ">" in txt


def _statcheck_quiet(
    texts: Sequence[str | None],
    stat: str | Iterable[str] = _ALL_STATS,
    OneTailedTests: bool = False,
    alpha: float = 0.05,
    pEqualAlphaSig: bool = True,
    pZeroError: bool = True,
    OneTailedTxt: bool = False,
    AllPValues: bool = False,
) -> tuple[list[int], dict[str, pd.Series]]:
    """statcheck() on each text separately, as metacheck's ``stats()`` runs it.

    Returns the 0-based index of the text each output row came from, and the
    columns. Texts that cannot contain a result are skipped up front with a
    single regex search (statcheck would return ``NULL`` for them).

    metacheck runs each text inside ``tryCatch(error = , warning = )``, so an
    error or the first R warning (``NAs introduced by coercion`` for an
    unrelated ``p = .05-.10``) discarded every result of the sentence (U4).
    Here a result that cannot be checked is dropped on its own (see
    :func:`_check_texts`).
    """
    flags = _check_args(OneTailedTests, alpha, pEqualAlphaSig, pZeroError, OneTailedTxt, AllPValues)
    stats_ = _stat_arg(stat)
    probe = _pcre(RGX_P_NS, True) if AllPValues else _NHST
    sources: list[int] = []
    results: list[_Result] = []
    pvals: list[tuple[Any, _P]] = []
    for i, txt in enumerate(texts):
        # every NHST result contains a comparator: checking for one first
        # skips most sentences without running the (slow) regex
        if txt is None or (not AllPValues and not _has_comparator(txt)):
            continue
        if probe.search(txt) is None:
            continue
        res, pres = _check_texts([("1", txt)], stats_, flags)
        found = pres if AllPValues else res
        sources.extend([i] * len(found))
        if AllPValues:
            pvals.extend(pres)
        else:
            results.extend(res)
    cols = _pvalue_columns(pvals) if AllPValues else _results_columns(results)
    return sources, cols


# ---------------------------------------------------------------------------
# S3 methods
# ---------------------------------------------------------------------------


def summary_statcheck(x: pd.DataFrame) -> pd.DataFrame:
    """Port of ``statcheck:::summary.statcheck()``: counts per source plus a total row."""
    sources = sorted({str(s) for s in x[VAR_SOURCE].tolist()})
    rows = []
    for s in sources:
        sub = x.loc[x[VAR_SOURCE].astype(str) == s]
        rows.append((s, len(sub), int(sub[VAR_ERROR].sum()), int(sub[VAR_DEC_ERROR].sum())))
    rows.append(("Total", len(x), int(x[VAR_ERROR].sum()), int(x[VAR_DEC_ERROR].sum())))
    return pd.DataFrame(
        {
            VAR_SOURCE: pd.Series([r[0] for r in rows], dtype="string"),
            VAR_NR_PVALUES: pd.Series([r[1] for r in rows], dtype="Int64"),
            VAR_NR_ERRORS: pd.Series([r[2] for r in rows], dtype="Int64"),
            VAR_NR_DEC_ERRORS: pd.Series([r[3] for r in rows], dtype="Int64"),
        }
    )


def trim(x: pd.DataFrame) -> pd.DataFrame:
    """Port of ``statcheck::trim()``: the concise columns of a statcheck result."""
    return x.loc[:, [VAR_SOURCE, VAR_RAW, VAR_COMPUTED_P, VAR_ERROR, VAR_DEC_ERROR]]
