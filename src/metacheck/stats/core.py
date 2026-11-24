"""``stats()``: check the statistics reported in papers (port of ``R/stats.R``)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from metacheck._r.regex import compile_r

__all__ = ["stats"]

_ALL_STATS = ("t", "F", "cor", "chisq", "Z", "Q")


def stats(
    text: Any,
    stat: str | Iterable[str] = _ALL_STATS,
    OneTailedTests: bool = False,
    alpha: float = 0.05,
    pEqualAlphaSig: bool = True,
    pZeroError: bool = True,
    OneTailedTxt: bool = False,
    AllPValues: bool = False,
) -> pd.DataFrame:
    """Check the statistics in a paper, paper list, text table or strings (``stats()``).

    Port of ``R/stats.R::stats()``: every sentence that contains a digit is
    run through statcheck (:func:`metacheck.stats.statcheck.statcheck`, with
    the settings R passes on through ``...``). Returns statcheck's columns
    (without ``source``) followed by the columns of the matching sentence
    (``text``, ``paper_id``, ``header`` ...), or an empty DataFrame when
    nothing is found. A string or a sequence of strings gives statcheck's
    columns followed by ``text`` and ``text_id`` (the position of the string).

    Differs from metacheck (U4, D78): a sentence keeps its other results when
    statcheck stops on one of them (metacheck drops the whole sentence on the
    first error, e.g. for ``t(20) = (2.1``; since 0.4.3.7 it mutes warnings,
    so an unrelated ``p = .05-.10`` no longer drops a sentence); and the
    settings are ordinary keyword (or positional) arguments, so a misspelt
    or abbreviated name raises :class:`TypeError` (R matched unique prefixes)
    and invalid values :class:`ValueError`.
    """
    from metacheck.stats.statcheck import _statcheck_quiet
    from metacheck.text.search import text_search

    # a character vector is searched as a text table, one row per string
    # (metacheck 0.4.3.7; it stopped with "argument is of length zero", U4)
    if isinstance(text, str):
        text = [text]
    if isinstance(text, (list, tuple)) and all(isinstance(t, str) or pd.isna(t) for t in text):
        text = pd.DataFrame(
            {"text": pd.Series(list(text), dtype="object"), "text_id": range(1, len(text) + 1)}
        )

    # lines with stats must have at least one number
    table: pd.DataFrame = text_search(text, "[0-9]")

    texts = [None if pd.isna(t) else str(t) for t in table["text"].tolist()]
    sources, columns = _statcheck_quiet(
        texts, stat, OneTailedTests, alpha, pEqualAlphaSig, pZeroError, OneTailedTxt, AllPValues
    )
    if not sources:
        return pd.DataFrame()

    # dplyr::left_join(checks, text, by = "source"), then drop `source`
    right = table.drop(columns=["source"], errors="ignore")
    right = right.iloc[sources].reset_index(drop=True)
    left = pd.DataFrame(columns)
    dupes = [c for c in right.columns if c in left.columns]
    if dupes:
        left = left.rename(columns={c: f"{c}.x" for c in dupes})
        right = right.rename(columns={c: f"{c}.y" for c in dupes})
    return pd.concat([left, right], axis=1)


# ---------------------------------------------------------------------------
# One-sided tests, bounds and variance tests (metacheck #695, points 5 and 6)
# ---------------------------------------------------------------------------


def _decimals(s: str) -> int:
    """R ``decimals()`` of ``R/stats.R``: the number of decimals of the first number in *s*."""
    s = re.sub(r"^[^0-9.]*", "", s)
    if "." not in s:
        return 0
    return len(re.sub(r"^[^.]*\.", "", re.sub(r"[^0-9.].*$", "", s, count=1), count=1))


def _num(x: Any) -> float:
    """``suppressWarnings(as.numeric(x))``: NaN for a missing value."""
    if x is None or x is pd.NA:
        return math.nan
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _p_of(test_type: str, df1: float, df2: float, v: float) -> float:
    """The p of a statistic: two-sided for t, upper tail for F (R ``p_of()``)."""
    from metacheck.stats.statcheck import _f_upper, _t_two_sided

    if test_type == "t":
        return float(_t_two_sided(v, df2))
    return float(_f_upper(v, df1, df2))


def _raw_parts(raw: Any) -> tuple[str, str]:
    """The statistic's text and the p's text of a statcheck ``raw`` string."""
    raw = "NA" if raw is None or raw is pd.NA else str(raw)
    test_txt = re.sub(
        r"^.*?[=<>]\s*", "", re.sub(r",\s*[pP]\s*[=<>].*$", "", raw, count=1), count=1
    )
    p_txt = re.sub(r"^.*[pP]\s*[=<>]\s*", "", raw, count=1)
    return test_txt, p_txt


def _fits_range(comp: Any, rp: float, d_p: int, p_rng: tuple[float, float]) -> bool | None:
    """Whether the reported p (``comp`` ``rp``) agrees with the range of possible p-values."""
    if math.isnan(p_rng[0]) or math.isnan(p_rng[1]):
        return None
    if comp == "<":
        return p_rng[0] < rp
    if comp == ">":
        return p_rng[1] > rp
    half_p = 0.5 * 10.0 ** -max(d_p, 1)
    return p_rng[0] <= rp + half_p and p_rng[1] >= rp - half_p


_VAR_SENTENCE = (
    r"\b(?:equal(?:ity)?|homogene\w*|equality)\s+(?:of\s+)?variances?\b"
    r"|\bvariances?\s+(?:were|was|are|is)\s+(?:not\s+)?(?:equal|homogene)"
    r"|\bvariance\s+ratio\b|\bvar\.test\b"
)


def _stat_bound_or_variance_fits(table: pd.DataFrame) -> list[bool | None]:
    """Port of ``.stat_bound_or_variance_fits()`` of ``R/stats.R``.

    For statcheck's rows (t- and F-tests): whether the reported p fits when
    statcheck's reading of the test is too strict, within the rounding of the
    statistic and the p. A statistic written with "<" or ">" is a bound, and an
    F-test for equal variances reports a two-sided p, twice statcheck's. ``None``
    (``NA``) where neither applies.
    """
    n = len(table)
    out: list[bool | None] = [None] * n
    if not n:
        return out
    rx = compile_r(_VAR_SENTENCE, ignore_case=True, perl=True)
    for i in range(n):
        test_type = table["test_type"].iloc[i]
        df1 = _num(table["df1"].iloc[i])
        df2 = _num(table["df2"].iloc[i])
        tv = abs(_num(table["test_value"].iloc[i]))
        rp = _num(table["reported_p"].iloc[i])
        if (
            math.isnan(tv)
            or math.isnan(rp)
            or math.isnan(df2)
            or test_type not in ("t", "F")
            or (test_type == "F" and math.isnan(df1))
        ):
            continue
        tcomp = table["test_comp"].iloc[i] if "test_comp" in table.columns else "="
        tcomp = None if pd.isna(tcomp) else str(tcomp)
        bound = tcomp in ("<", ">")
        text = table["text"].iloc[i] if "text" in table.columns else ""
        variance = test_type == "F" and rx.search("NA" if pd.isna(text) else str(text)) is not None
        if not bound and not variance:
            continue
        test_txt, p_txt = _raw_parts(table["raw"].iloc[i])
        d_t, d_p = _decimals(test_txt), _decimals(p_txt)
        half_t = 0.5 * 10.0**-d_t
        # the statistic's possible values
        if tcomp == "<":
            rng = (0.0, tv + half_t)
        elif tcomp == ">":
            rng = (max(0.0, tv - half_t), math.inf)
        else:
            rng = (max(0.0, tv - half_t), tv + half_t)
        ps = [_p_of(test_type, df1, df2, rng[0]), _p_of(test_type, df1, df2, rng[1])]
        p_rng = (min(ps), max(ps)) if not any(math.isnan(p) for p in ps) else (math.nan, math.nan)
        if variance:
            p_rng = (min(1.0, 2 * p_rng[0]), min(1.0, 2 * p_rng[1]))
        comp = table["p_comp"].iloc[i]
        out[i] = _fits_range(None if pd.isna(comp) else str(comp), rp, d_p, p_rng)
    return out


def _paper_says_one_sided(paper: Any) -> pd.Series:
    """Port of ``.paper_says_one_sided()`` of ``R/stats.R``.

    Whether a paper says its tests are one-sided anywhere in its text, as a
    boolean Series by ``paper_id``: statcheck reads one sentence at a time.
    """
    from metacheck.papers.tables import paper_table

    tx = paper_table(paper, "text", cols=["text"])
    if len(tx) == 0:
        return pd.Series([], dtype=bool, index=pd.Index([], dtype=object))
    rx = compile_r(
        r"\bone[- ]?(?:tailed|sided)\b|\bdirectional\s+(?:tests?|hypothes[ie]s|predictions?)\b|"
        r"\b(?:tests?|hypothes[ie]s)\s+(?:were|was)\s+directional\b",
        ignore_case=True,
        perl=True,
    )
    says = [(not pd.isna(t)) and rx.search(str(t)) is not None for t in tx["text"]]
    frame = pd.DataFrame({"paper_id": tx["paper_id"].tolist(), "says": says})
    out = frame.groupby("paper_id", sort=True, dropna=True)["says"].any()
    return out.astype(bool)


def _stat_one_sided_fits(table: pd.DataFrame) -> list[bool | None]:
    """Port of ``.stat_one_sided_fits()`` of ``R/stats.R``.

    For statcheck's rows (t-tests, and F-tests with one degree of freedom in the
    numerator, which are a t squared): whether the reported p fits a one-sided
    test in either direction, within the rounding of the statistic and the p:
    half the two-sided p, or 1 minus that half for an effect against the
    predicted direction. ``None`` (``NA``) where it does not apply.
    """
    n = len(table)
    out: list[bool | None] = [None] * n
    for i in range(n):
        test_type = table["test_type"].iloc[i]
        df1 = _num(table["df1"].iloc[i])
        df2 = _num(table["df2"].iloc[i])
        tv = abs(_num(table["test_value"].iloc[i]))
        rp = _num(table["reported_p"].iloc[i])
        if (
            math.isnan(tv)
            or math.isnan(rp)
            or math.isnan(df2)
            or not (test_type == "t" or (test_type == "F" and df1 == 1))
        ):
            continue
        test_txt, p_txt = _raw_parts(table["raw"].iloc[i])
        d_t, d_p = _decimals(test_txt), _decimals(p_txt)
        # the statistic's possible values, given its rounding, and the two-sided p
        lo = max(0.0, tv - 0.5 * 10.0**-d_t)
        hi = tv + 0.5 * 10.0**-d_t
        p2 = [_p_of(test_type, 1.0, df2, v) for v in (hi, lo)]
        ranges = [(p2[0] / 2, p2[1] / 2), (1 - p2[1] / 2, 1 - p2[0] / 2)]
        comp = table["p_comp"].iloc[i]
        comp = None if pd.isna(comp) else str(comp)
        fits = [_fits_range(comp, rp, d_p, r) for r in ranges]
        out[i] = _r_any(fits)
    return out


def _r_any(values: Iterable[bool | None]) -> bool | None:
    """R ``any()``: ``TRUE`` wins, else ``NA`` if any ``NA``, else ``FALSE``."""
    na = False
    for v in values:
        if v is None:
            na = True
        elif v:
            return True
    return None if na else False
