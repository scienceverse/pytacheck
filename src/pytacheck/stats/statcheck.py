"""Port of the statcheck R package (version 1.5.0), which metacheck's ``stats()`` wraps.

statcheck (Nuijten & Epskamp) extracts APA-style null-hypothesis significance
tests (``t(28) = 2.20, p = .03``) from text with regular expressions,
recomputes the p-value from the test statistic and degrees of freedom, and
flags inconsistencies (``error``) and inconsistencies that change the
significance decision (``decision_error``).

Everything here mirrors the *installed* statcheck 1.5.0 function by function
(``statcheck:::extract_stats``, ``extract_df``, ``extract_test_stats``,
``extract_p_value``, ``compute_p``, ``error_test``, ``decision_error_test``,
``process_stats``, ``calc_APA_factor`` ...), including its quirks:

* every regular expression runs on the engine R runs it on (PCRE for
  ``extract_pattern()`` and the ``perl = TRUE`` substitutions, TRE for the
  ``grepl``/``gsub``/``strsplit``/``regexpr`` calls), with the same
  case-sensitivity;
* R's three-valued logic: an ``NA`` reaching an ``if ()`` is an error
  (:class:`RError`), exactly where R stops;
* R warnings (``NAs introduced by coercion``, ``NaNs produced``) are
  reported with :func:`warnings.warn` by :func:`statcheck` (and computation
  goes on, as in R), while metacheck's ``stats()`` discards a sentence on
  its first warning (its ``tryCatch(warning = ...)``) -- see
  :func:`_statcheck_quiet`.

The file-reading front ends (``checkPDF``, ``checkHTML``, ``checkdir`` ...),
the plotting methods and ``statcheckReport()`` are not ported: metacheck
never calls them.
"""

from __future__ import annotations

import contextvars
import math
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from pytacheck._r.regex import compile_r, gsub, strsplit
from pytacheck.stats._rmath import as_numeric, pchisq, pf, pnorm, pt, r_pow, r_round
from pytacheck.stats._rmath import sqrt as r_sqrt

__all__ = [
    "RError",
    "StatcheckWarning",
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
RGX_QW = r"w"
RGX_QB = r"b"
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

_TRE_NS_ICASE = compile_r(RGX_NS, True, posix=False)
_TRE_1TAIL = compile_r("one.?sided|one.?tailed|directional", True, posix=False)


def _pcre(pattern: str, ignore_case: bool) -> Any:
    """``gregexpr(pattern, perl = TRUE, ignore.case)``'s compiled pattern."""
    return compile_r(pattern, ignore_case, True)


class RError(RuntimeError):
    """An error R itself would raise while running statcheck (e.g. ``if (NA)``)."""


class StatcheckWarning(RuntimeWarning):
    """A warning R would emit while running statcheck."""


class _Abort(Exception):
    """Raised on the first R warning inside metacheck's ``tryCatch(warning = )``."""


# How R warnings are handled: None -> warnings.warn (statcheck() called
# directly); a callable -> called with the message (stats() makes it raise).
_ON_WARNING: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "pytacheck_statcheck_on_warning", default=None
)


def _r_warning(msg: str) -> None:
    handler = _ON_WARNING.get()
    if handler is None:
        warnings.warn(msg, StatcheckWarning, stacklevel=3)
    else:
        handler(msg)


# ---------------------------------------------------------------------------
# R three-valued logic (None is NA)
# ---------------------------------------------------------------------------

Lgl = bool | None


def _isna(x: Any) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _gt(a: float, b: float) -> Lgl:
    return None if _isna(a) or _isna(b) else a > b


def _ge(a: float, b: float) -> Lgl:
    return None if _isna(a) or _isna(b) else a >= b


def _lt(a: float, b: float) -> Lgl:
    return None if _isna(a) or _isna(b) else a < b


def _le(a: float, b: float) -> Lgl:
    return None if _isna(a) or _isna(b) else a <= b


def _and(a: Lgl, b: Lgl) -> Lgl:
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def _or(a: Lgl, b: Lgl) -> Lgl:
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def _eq_true(x: Any) -> Lgl:
    """R ``x == TRUE`` for a logical/numeric flag (``None`` is NA)."""
    return None if _isna(x) else bool(x == 1)


def _eq_false(x: Any) -> Lgl:
    """R ``x == FALSE`` for a logical/numeric flag (``None`` is NA)."""
    return None if _isna(x) else bool(x == 0)


class _RNull:
    """R ``NULL`` returned by ``decision_error_test()`` for a flag that is neither TRUE nor FALSE."""

    def __repr__(self) -> str:  # pragma: no cover
        return "NULL"


_NULL = _RNull()


def _if(cond: Lgl) -> bool:
    """R ``if (cond)``: an ``NA`` condition is an error."""
    if cond is None:
        raise RError("missing value where TRUE/FALSE needed")
    return cond


def _as_num(s: str | None) -> float:
    """``as.numeric()`` of one string, warning like R."""
    value, warned = as_numeric(s)
    if warned:
        _r_warning("NAs introduced by coercion")
    return value


def _as_num_quiet(s: str | None) -> float:
    """``suppressWarnings(as.numeric())``."""
    return as_numeric(s)[0]


def _tre_match_length(rx: Any, s: str | None) -> int:
    """``attr(regexpr(p, s), "match.length")``: -1 when there is no match."""
    if s is None:
        return -1
    m = rx.search(s)
    return -1 if m is None else m.end() - m.start()


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
        # gregexpr(pattern, character(0))[[1]]
        raise RError("subscript out of bounds")
    first = texts[0]
    if first is None:
        return None
    rx = _pcre(pattern, ignore_case)
    spans = [(m.start(), m.end()) for m in _finditer(rx, first)]
    if not spans:
        return None
    n = max(len(texts), len(spans))
    out: list[str | None] = []
    for i in range(n):
        s = texts[i % len(texts)]
        start, end = spans[i % len(spans)]
        out.append(None if s is None else s[start:end])
    return out


def _extract(txt: str | None, pattern: str, ignore_case: bool = True) -> list[str] | None:
    """``extract_pattern()`` for a single string (``None`` is R's ``NULL``)."""
    if txt is None:
        return None
    rx = _pcre(pattern, ignore_case)
    found = [m.group(0) for m in _finditer(rx, txt)]
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


def extract_1tail(txt: str | None) -> bool:
    """Port of ``statcheck:::extract_1tail()``: is a one-sided test mentioned?"""
    if txt is None:
        raise RError("missing value where TRUE/FALSE needed")
    return _TRE_1TAIL.search(txt) is not None


def _extract_df(raw: str, test_type: str | None) -> tuple[float, float]:
    if test_type is None:
        raise RError("missing value where TRUE/FALSE needed")
    if test_type == "Z":
        return math.nan, math.nan
    found = _extract(raw, RGX_DF)
    if found is None:
        raise RError("subscript out of bounds")
    df_str = gsub(r"\(|\)", "", found[0])
    df = [s.strip(_WS) if s is not None else None for s in strsplit(df_str, ",")]
    df1: list[str | None]
    df2: list[str | None]
    if test_type in ("t", "r"):
        df1, df2 = [None], df
    elif test_type == "F":
        first = df[0] if df else None
        if first is not None and _TRE_DF1_I_L.search(first):
            df = ["1", *df[1:]]
        df1 = [df[0] if len(df) > 0 else None]
        df2 = [df[1] if len(df) > 1 else None]
    elif test_type in ("Chi2", "Q", "Qw", "Qb"):
        df1, df2 = [df[0] if df else None], [None]
    else:
        df1, df2 = [None], [None]
    if len(df1) != 1 or len(df2) != 1:
        raise RError("more than one set of degrees of freedom")  # pragma: no cover
    return _as_num(df1[0]), _as_num(df2[0])


def extract_df(raw: str, test_type: str) -> pd.DataFrame:
    """Port of ``statcheck:::extract_df()``: degrees of freedom of one raw result."""
    df1, df2 = _extract_df(raw, test_type)
    return pd.DataFrame({"df1": [df1], "df2": [df2]}, dtype="float64")


@dataclass(slots=True)
class _Test:
    comp: str | None
    value: float
    dec: float


def _recycled_rows(*lengths: int) -> int:
    """``data.frame()`` row count with R's recycling rule (or R's error)."""
    n = max(lengths)
    for k in lengths:
        if k == 0 or n % k != 0:
            raise RError(
                "arguments imply differing number of rows: " + ", ".join(str(x) for x in lengths)
            )
    return n


def _test_stats(raw: str) -> tuple[list[str], list[float], list[float], int]:
    """The vectors ``extract_test_stats()`` builds its data frame from, and its row count."""
    raw_non = gsub(RGX_DF_CHI2, "", raw)
    test_raw = _extract(raw_non, RGX_TEST_VALUE)
    if test_raw is None:
        # extract_pattern(NULL, RGX_COMP): gregexpr(p, NULL)[[1]]
        raise RError("subscript out of bounds")
    comps = extract_pattern(test_raw, RGX_COMP)
    if comps is None:  # pragma: no cover - RGX_TEST_VALUE starts with a comparator
        raise RError("numbers of columns of arguments do not match")
    test_comp = [c for c in comps if c is not None]
    values = gsub(RGX_COMP, "", test_raw)
    values = recover_minus_sign(remove_1000_sep(values))
    values = gsub(",$", "", [v.strip(_WS) for v in values])
    decs = [float(max(_tre_match_length(_TRE_DEC, v) - 1, 0)) for v in values]
    nums = [_as_num_quiet(v) for v in values]
    nrow = _recycled_rows(len(test_comp), len(nums), len(decs))
    return test_comp, nums, decs, nrow


def _extract_test_stats(raw: str) -> tuple[int, _Test]:
    """(number of rows R's data frame has, its first row)."""
    test_comp, nums, decs, nrow = _test_stats(raw)
    return nrow, _Test(test_comp[0], nums[0], decs[0])


def extract_test_stats(raw: str) -> pd.DataFrame:
    """Port of ``statcheck:::extract_test_stats()``: comparison, value and decimals."""
    test_comp, nums, decs, n = _test_stats(raw)
    return pd.DataFrame(
        {
            "test_comp": pd.Series(
                [test_comp[i % len(test_comp)] for i in range(n)], dtype="string"
            ),
            "test_value": pd.Series([nums[i % len(nums)] for i in range(n)], dtype="float64"),
            "test_dec": pd.Series([decs[i % len(decs)] for i in range(n)], dtype="float64"),
        }
    )


@dataclass(slots=True)
class _P:
    comp: str | None
    value: float
    dec: float


def _extract_p_value(raw: str | None) -> list[_P]:
    p_raw = _extract(raw, RGX_P_NS)
    if p_raw is None:
        return []
    comps: list[str | None] = []
    strs: list[str | None] = []
    decs: list[float] = []
    for pr in p_raw:
        if _TRE_NS_ICASE.search(pr):
            comps.append("ns")
            strs.append(None)
            decs.append(math.nan)
        else:
            comp = _extract(pr, RGX_COMP)
            if comp is None or len(comp) != 1:  # pragma: no cover - RGX_P has one comparator
                raise RError("replacement has length zero")
            comps.append(comp[0])
            parts = strsplit(pr, RGX_COMP)
            value = parts[1] if len(parts) > 1 else None
            value = None if value is None else value.strip(_WS)
            strs.append(value)
            if value is None:  # pragma: no cover - RGX_P always has a value
                decs.append(math.nan)
            else:
                decs.append(float(max(_tre_match_length(_TRE_DEC, value) - 1, 0)))
    # as.numeric(p_value) on the whole vector, with its warning
    values, warned = [], False
    for s in strs:
        v, w = as_numeric(s)
        values.append(v)
        warned = warned or w
    if warned:
        _r_warning("NAs introduced by coercion")
    return [_P(c, v, d) for c, v, d in zip(comps, values, decs, strict=True)]


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
    statistic: str | None
    df1: float
    df2: float
    test_comp: str | None
    value: float
    testdec: float
    p_comp: str | None
    p_value: float
    dec: float


def _test_type(test_raw: list[str] | None) -> str | None:
    """The if/else chain of ``extract_stats()`` classifying one result."""
    if test_raw is None:
        raise RError("argument is of length zero")
    if len(test_raw) > 1:
        raise RError("the condition has length > 1")
    tr = test_raw[0]
    if _TRE_Q.search(tr):
        if RGX_QB in tr:
            return "Qb"
        if RGX_QW in tr:
            return "Qw"
        return "Q"
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


def _stat_family(statistic: str | None) -> str | None:
    return {"r": "cor", "Chi2": "chisq", "Qw": "Q", "Qb": "Q"}.get(statistic or "", statistic)


_NHST = compile_r(RGX_NHST, False, True)


def _extract_stats_rows(txt: str | None, stat: Sequence[str]) -> list[_Row] | None:
    """``extract_stats()`` as a list of rows; ``None`` is R's ``data.frame(NULL)``."""
    nhst_raw = _extract(txt, RGX_NHST, ignore_case=False)
    if nhst_raw is None:
        return None
    parsed: list[tuple[str, str | None, float, float, _Test]] = []
    pvals: list[_P] = []
    for raw in nhst_raw:
        # an unclassified result makes extract_df() fail on `if (NA == "Z")`
        test_type = _test_type(_extract(raw, RGX_TEST_TYPE))
        df1, df2 = _extract_df(raw, test_type)
        n_test, test = _extract_test_stats(raw)
        if n_test > 1:
            test = _Test(None, math.nan, math.nan)
        parsed.append((raw, test_type, df1, df2, test))
        ps = _extract_p_value(raw)
        # rbind(pvals, p): an NA row for several p-values, nothing for none
        if len(ps) > 1:
            pvals.append(_P(None, math.nan, math.nan))
        elif ps:
            pvals.append(ps[0])
    # data.frame(Raw = ..., Reported.Comparison = pvals$p_comp, ...): a raw
    # whose p-value the case-insensitive RGX_P_NS misses (e.g. "Nns") leaves
    # the p-value columns short; R recycles them when it can, else errors
    n, m = len(parsed), len(pvals)
    if m != n and (m == 0 or n % m != 0):
        raise RError(f"arguments imply differing number of rows: {n}, {m}")
    rows = [
        _Row(
            raw=raw.strip(_WS),
            statistic=test_type,
            df1=df1,
            df2=df2,
            test_comp=test.comp,
            value=test.value,
            testdec=test.dec,
            p_comp=pvals[i % m].comp,
            p_value=pvals[i % m].value,
            dec=pvals[i % m].dec,
        )
        for i, (raw, test_type, df1, df2, test) in enumerate(parsed)
    ]
    out = []
    for r in rows:
        if not (_isna(r.p_value) or r.p_value <= 1):
            continue
        if _isna(r.value):
            continue
        if r.statistic == "r" and (r.value > 1 or r.value < -1):
            continue
        if r.test_comp is None or r.p_comp is None:
            continue
        if _stat_family(r.statistic) not in stat:
            continue
        out.append(r)
    return out


def _rows_frame(rows: list[_Row]) -> pd.DataFrame:
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


def extract_stats(txt: str | None, stat: str | Iterable[str] = _ALL_STATS) -> pd.DataFrame:
    """Port of ``statcheck:::extract_stats()``: parse every APA result in *txt*."""
    rows = _extract_stats_rows(txt, _stat_arg(stat))
    if rows is None:
        return pd.DataFrame()
    return _rows_frame(rows)


# ---------------------------------------------------------------------------
# p-value computation and error tests
# ---------------------------------------------------------------------------


def r2t(r: float, df: float) -> float:
    """Port of ``statcheck:::r2t()``: a correlation as a t statistic."""
    q = 1 - r**2
    if _isna(q) or _isna(df):
        denom = math.nan
    elif df == 0:
        denom = math.nan if q == 0 else math.copysign(math.inf, q)
    else:
        denom = q / df
    s = r_sqrt(denom, _r_warning)
    if _isna(s) or _isna(r):
        return math.nan
    if s == 0:
        return math.nan if r == 0 else math.copysign(math.inf, r)
    return r / s


def compute_p(test_type: str, test_stat: float, df1: float, df2: float, two_tailed: bool) -> float:
    """Port of ``statcheck:::compute_p()``: the p-value implied by a test statistic."""
    if test_type not in _TEST_TYPES:
        raise RError('test_type %in% c("t", "F", "Z", "r", "Chi2", "Q", "Qb", "Qw") is not TRUE')
    if test_type == "t":
        computed = pt(-1 * abs(test_stat), df2, True, _r_warning)
    elif test_type == "F":
        computed = pf(test_stat, df1, df2, False, _r_warning)
    elif test_type == "Z":
        computed = pnorm(abs(test_stat), False)
    elif test_type == "r":
        t = r2t(test_stat, df2)
        computed = pt(-1 * abs(t), df2, True, _r_warning)
    else:
        computed = pchisq(test_stat, df1, False, _r_warning)
    if not _isna(computed) and test_type in ("t", "Z", "r") and two_tailed:
        computed = computed * 2
    return computed


def _ns(reported_p: float, p_comparison: str, alpha: float) -> tuple[float, str]:
    if p_comparison == "ns":
        return alpha, ">"
    return reported_p, p_comparison


def error_test(
    reported_p: float,
    test_type: str,
    test_stat: float,
    df1: float,
    df2: float,
    p_comparison: str,
    test_comparison: str,
    p_dec: float,
    test_dec: float,
    two_tailed: bool,
    alpha: float,
    pZeroError: bool,
) -> Lgl:
    """Port of ``statcheck:::error_test()``: is the reported p inconsistent? (``None`` = NA)."""
    reported_p, p_comparison = _ns(reported_p, p_comparison, alpha)
    half = 0.5 / r_pow(10.0, test_dec)
    if _if(_ge(test_stat, 0)):
        low_stat, up_stat = test_stat - half, test_stat + half
    elif _if(_lt(test_stat, 0)):
        low_stat, up_stat = test_stat + half, test_stat - half
    else:  # pragma: no cover
        raise RError("object 'low_stat' not found")
    up_p = compute_p(test_type, low_stat, df1, df2, two_tailed)
    low_p = compute_p(test_type, up_stat, df1, df2, two_tailed)
    if _if(_and(_eq_true(pZeroError), _le(reported_p, 0))):
        return True
    if test_comparison == "=":
        if p_comparison == "=":
            return _or(
                _gt(reported_p, r_round(up_p, p_dec)), _lt(reported_p, r_round(low_p, p_dec))
            )
        if p_comparison == "<":
            return _lt(reported_p, low_p)
        if p_comparison == ">":
            return _gt(reported_p, up_p)
    elif test_comparison == "<":
        if p_comparison == "=":
            return _lt(reported_p, r_round(up_p, p_dec))
        if p_comparison == "<":
            return _lt(reported_p, up_p)
        if p_comparison == ">":
            return False
    elif test_comparison == ">":
        if p_comparison == "=":
            return _gt(reported_p, r_round(low_p, p_dec))
        if p_comparison == "<":
            return False
        if p_comparison == ">":
            return _gt(reported_p, low_p)
    return None


def decision_error_test(
    reported_p: float,
    computed_p: float,
    test_comparison: str,
    p_comparison: str,
    alpha: float,
    pEqualAlphaSig: bool,
) -> Lgl:
    """Port of ``statcheck:::decision_error_test()`` (``None`` = NA).

    As in R, a *pEqualAlphaSig* that is neither ``TRUE`` nor ``FALSE`` (e.g.
    ``2``) gives ``NULL`` (``None``) and ``NA`` is an error.
    """
    out = _decision_error_test(
        reported_p, computed_p, test_comparison, p_comparison, alpha, pEqualAlphaSig
    )
    return None if out is _NULL else out  # type: ignore[return-value]


def _decision_error_test(
    reported_p: float,
    computed_p: float,
    test_comparison: str,
    p_comparison: str,
    alpha: float,
    pEqualAlphaSig: Any,
) -> Lgl | _RNull:
    rp, pc = _ns(reported_p, p_comparison, alpha)
    cp, a = computed_p, alpha
    if _if(_eq_true(pEqualAlphaSig)):
        if test_comparison == "=":
            if pc == "=":
                return _or(_and(_le(rp, a), _gt(cp, a)), _and(_gt(rp, a), _le(cp, a)))
            if pc == "<":
                return _and(_le(rp, a), _gt(cp, a))
            if pc == ">":
                return _and(_ge(rp, a), _le(cp, a))
        elif test_comparison == "<":
            if pc in ("=", "<"):
                return _and(_le(rp, a), _ge(cp, a))
            if pc == ">":
                return False
        elif test_comparison == ">":
            if pc == "=":
                return _and(_gt(rp, a), _le(cp, a))
            if pc == "<":
                return False
            if pc == ">":
                return _and(_ge(rp, a), _le(cp, a))
        return None
    if not _if(_eq_false(pEqualAlphaSig)):
        return _NULL
    if test_comparison == "=":
        if pc == "=":
            return _or(_and(_lt(rp, a), _ge(cp, a)), _and(_ge(rp, a), _lt(cp, a)))
        if pc == "<":
            return _and(_le(rp, a), _ge(cp, a))
        if pc == ">":
            return _and(_ge(rp, a), _lt(cp, a))
    elif test_comparison == "<":
        if pc == "=":
            return _and(_lt(rp, a), _ge(cp, a))
        if pc == "<":
            return _and(_le(rp, a), _ge(cp, a))
        if pc == ">":
            return False
    elif test_comparison == ">":
        if pc == "=":
            return _and(_ge(rp, a), _le(cp, a))
        if pc == "<":
            return False
        if pc == ">":
            return _and(_ge(rp, a), _le(cp, a))
    return None


def _process_stats(
    test_type: str,
    test_stat: float,
    df1: float,
    df2: float,
    reported_p: float,
    p_comparison: str,
    test_comparison: str,
    p_dec: float,
    test_dec: float,
    OneTailedInTxt: bool,
    two_tailed: bool,
    alpha: float,
    pZeroError: bool,
    pEqualAlphaSig: bool,
    OneTailedTxt: bool,
    OneTailedTests: bool,
) -> tuple[float, bool, Lgl]:
    """``process_stats()`` as a tuple ``(computed_p, error, decision_error)``."""
    computed_p = compute_p(test_type, test_stat, df1, df2, two_tailed)
    error = error_test(
        reported_p,
        test_type,
        test_stat,
        df1,
        df2,
        p_comparison,
        test_comparison,
        p_dec,
        test_dec,
        two_tailed,
        alpha,
        pZeroError,
    )
    decision_error: Lgl | _RNull
    if _if(None if error is None else not error):
        decision_error = False
    else:
        decision_error = _decision_error_test(
            reported_p, computed_p, test_comparison, p_comparison, alpha, pEqualAlphaSig
        )
    if _if(_and(_eq_true(OneTailedTxt), _eq_false(OneTailedTests))) and _if(
        _and(error, OneTailedInTxt)
    ):
        computed_p_1tail = compute_p(test_type, test_stat, df1, df2, False)
        error_1tail = error_test(
            reported_p,
            test_type,
            test_stat,
            df1,
            df2,
            p_comparison,
            test_comparison,
            p_dec,
            test_dec,
            False,
            alpha,
            pZeroError,
        )
        decision_error_1tail: Lgl | _RNull
        if _if(None if error_1tail is None else not error_1tail):
            decision_error_1tail = False
        else:
            decision_error_1tail = _decision_error_test(
                reported_p, computed_p_1tail, test_comparison, p_comparison, alpha, pEqualAlphaSig
            )
        if _if(None if error is None or error_1tail is None else error != error_1tail):
            computed_p = computed_p_1tail
            error = error_1tail
            decision_error = decision_error_1tail
    assert error is not None
    if decision_error is _NULL:
        # data.frame(computed_p = ., error = ., decision_error = NULL)
        raise RError("arguments imply differing number of rows: 1, 0")
    return computed_p, error, decision_error  # type: ignore[return-value]


def process_stats(
    test_type: str,
    test_stat: float,
    df1: float,
    df2: float,
    reported_p: float,
    p_comparison: str,
    test_comparison: str,
    p_dec: float,
    test_dec: float,
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
    ``decision_error``; with ``OneTailedTxt=True`` an error in a text that
    mentions one-sided testing is re-evaluated as a one-tailed test.
    """
    computed_p, error, decision_error = _process_stats(
        test_type,
        test_stat,
        df1,
        df2,
        reported_p,
        p_comparison,
        test_comparison,
        p_dec,
        test_dec,
        OneTailedInTxt,
        two_tailed,
        alpha,
        pZeroError,
        pEqualAlphaSig,
        OneTailedTxt,
        OneTailedTests,
    )
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
    n_res: dict[str, int] = {}
    n_p: dict[str, int] = {}
    for s in sources:
        n_res[s] = n_res.get(s, 0) + 1
    for s in p_sources:
        n_p[s] = n_p.get(s, 0) + 1
    out = []
    for s in sources:
        if s not in n_p:  # pragma: no cover - every result has its own p-value
            raise RError("(list) object cannot be coerced to type 'double'")
        out.append(r_round(n_res[s] / n_p[s], 2))
    return out


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
    computed: float = math.nan
    error: Lgl = None
    decision_error: Lgl = None
    apa: float = math.nan


def _check_texts(
    items: Iterable[tuple[Any, str | None]],
    stat: Sequence[str],
    OneTailedTests: bool,
    alpha: float,
    pEqualAlphaSig: bool,
    pZeroError: bool,
    OneTailedTxt: bool,
) -> tuple[list[_Result], list[tuple[Any, _P]]]:
    """The body of ``statcheck()`` for (source, text) pairs."""
    results: list[_Result] = []
    pres: list[tuple[Any, _P]] = []
    for source, txt in items:
        pres.extend((source, p) for p in _extract_p_value(txt))
        rows = _extract_stats_rows(txt, stat)
        if rows:
            one_tailed = extract_1tail(txt)
            results.extend(_Result(source, r, one_tailed) for r in rows)
    if results:
        two_tailed = not _if(_eq_true(OneTailedTests))
        for res in results:
            r = res.row
            assert r.statistic is not None and r.test_comp is not None and r.p_comp is not None
            res.computed, res.error, res.decision_error = _process_stats(
                test_type=r.statistic,
                test_stat=r.value,
                df1=r.df1,
                df2=r.df2,
                reported_p=r.p_value,
                p_comparison=r.p_comp,
                test_comparison=r.test_comp,
                p_dec=r.dec,
                test_dec=r.testdec,
                OneTailedInTxt=res.one_tailed,
                two_tailed=two_tailed,
                alpha=alpha,
                pZeroError=pZeroError,
                pEqualAlphaSig=pEqualAlphaSig,
                OneTailedTxt=OneTailedTxt,
                OneTailedTests=OneTailedTests,
            )
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
    if isinstance(texts, str) or texts is None:
        values: list[Any] = [texts]
        names = None
    elif isinstance(texts, Mapping):
        names = [str(k) for k in texts]
        values = list(texts.values())
    elif isinstance(texts, pd.Series):
        # a named character vector: string labels are names, a numeric index is not
        values = texts.tolist()
        named = texts.index.inferred_type in ("string", "unicode")
        names = [str(k) for k in texts.index] if named else None
    else:
        values = list(texts)
        names = None
    values = [None if _isna(v) else str(v) for v in values]
    if names is None:
        if not values:
            # max(integer(0)) and log10(-Inf)
            _r_warning("no non-missing arguments to max; returning -Inf")
            _r_warning("NaNs produced")
        width = math.ceil(math.log10(max(len(values), 1)))
        # formatC(width = 0L, flag = "0") pads to two characters ("01")
        width = width or 2
        names = [f"{i:0{width}d}" for i in range(1, len(values) + 1)]
    return names, values


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
    returns ``None`` (and prints a message when *messages* is true) when
    nothing is found.
    """
    names, values = _source_names(texts)
    _if(_eq_true(messages))  # `if (messages == TRUE)`: NA is an error
    stats_ = _stat_arg(stat)
    results, pres = _check_texts(
        zip(names, values, strict=True),
        stats_,
        OneTailedTests,
        alpha,
        pEqualAlphaSig,
        pZeroError,
        OneTailedTxt,
    )
    if _if(_eq_false(AllPValues)):
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

    Each text is checked inside R's ``tryCatch(error = , warning = )``: an
    error or the first warning discards that text's results. Returns the
    0-based index of the text each output row came from, and the columns.
    Texts that cannot contain a result are skipped up front with a single
    regex search (statcheck would return ``NULL`` for them).
    """
    stats_ = _stat_arg(stat)
    all_p_false = _eq_false(AllPValues)
    if all_p_false is None:
        # `if (AllPValues == FALSE)` fails at the end of every statcheck() call
        return [], _results_columns([])
    AllPValues = not all_p_false
    probe = _pcre(RGX_P_NS, True) if AllPValues else _NHST
    sources: list[int] = []
    results: list[_Result] = []
    pvals: list[tuple[Any, _P]] = []

    def abort(msg: str) -> None:
        raise _Abort(msg)

    token = _ON_WARNING.set(abort)
    try:
        for i, txt in enumerate(texts):
            if txt is None:
                continue
            # every NHST result contains a comparator: checking for one first
            # skips most sentences without running the (slow) regex
            if not AllPValues and not _has_comparator(txt):
                continue
            if probe.search(txt) is None:
                continue
            try:
                res, pres = _check_texts(
                    [("1", txt)],
                    stats_,
                    OneTailedTests,
                    alpha,
                    pEqualAlphaSig,
                    pZeroError,
                    OneTailedTxt,
                )
            except (RError, _Abort):
                continue
            if AllPValues:
                pvals.extend(pres)
                sources.extend([i] * len(pres))
            else:
                results.extend(res)
                sources.extend([i] * len(res))
    finally:
        _ON_WARNING.reset(token)
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
        rows.append(
            (s, len(sub), int(sub[VAR_ERROR].sum(skipna=True)), int(sub[VAR_DEC_ERROR].sum()))
        )
    rows.append(("Total", len(x), int(x[VAR_ERROR].sum(skipna=True)), int(x[VAR_DEC_ERROR].sum())))
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
