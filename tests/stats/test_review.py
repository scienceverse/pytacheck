"""Edge cases found in the review of the stats port; expected values come from R 4.5.3.

Covers R's numeric corner cases (infinite degrees of freedom, "NaNs produced",
overflowing powers of ten, round() with many digits), statcheck's `== TRUE`
flag semantics and stats()'s R argument matching of `...`.
"""

from __future__ import annotations

import math
import warnings

import pandas as pd
import pytest

from metacheck.stats import _rmath
from metacheck.stats._rmath import pchisq, pf, pt, r_pow, r_round
from metacheck.stats.core import stats
from metacheck.stats.statcheck import (
    RError,
    StatcheckWarning,
    decision_error_test,
    error_test,
    process_stats,
    statcheck,
)

BIG = "1" + "0" * 310  # parses as Inf


def sc(texts, **kwargs):
    return statcheck(texts, messages=False, **kwargs)


# -- numeric primitives ---------------------------------------------------------


def test_pchisq_infinite_df() -> None:
    seen: list[str] = []
    # R: pchisq(c(0, 0.2, 2, 1e300, Inf), Inf, lower.tail = FALSE) -> 1 NaN 1 1 0 (+ warning)
    values = [pchisq(x, math.inf, False, seen.append) for x in (0, 0.2, 2, 1e300, math.inf)]
    assert values[0] == 1.0
    assert math.isnan(values[1])
    assert values[2:] == [1.0, 1.0, 0.0]
    assert seen == ["NaNs produced"]


def test_pf_and_pt_infinite_df() -> None:
    assert pf(2, math.inf, 3, False) == pytest.approx(0.3177297, rel=1e-6)
    assert pf(2, 3, math.inf, False) == pytest.approx(0.1116102, rel=1e-6)
    assert pf(2, math.inf, math.inf, False) == 0.0
    assert pt(-2, math.inf) == pytest.approx(0.02275013, rel=1e-6)
    # both df infinite: a point mass at 1, no NaN
    seen: list[str] = []
    assert math.isnan(pf(0.5, math.inf, math.inf, False, seen.append)) is False
    assert seen == []


def test_nans_produced_only_for_non_nan_arguments() -> None:
    seen: list[str] = []
    assert math.isnan(pt(math.nan, 5, warn=seen.append))
    assert math.isnan(pchisq(1.0, math.nan, warn=seen.append))
    assert seen == []
    assert math.isnan(pt(1.0, 0.0, warn=seen.append))
    assert math.isnan(pf(1.0, -1.0, 20.0, warn=seen.append))
    assert seen == ["NaNs produced", "NaNs produced"]


@pytest.mark.parametrize(
    ("x", "digits", "expected"),
    [
        (9.5e-17, 23, 9.5e-17),
        (9.5e-11, 23, 9.500000000000001e-11),
        (0.0094999999999999, 3, 0.009),
        (9.500000000000001e-07, 7, 9.9999999999999995e-07),
        (0.15, 1, 0.1),
        (0.715, 2, 0.72),
        (0.12355, 4, 0.1235),
    ],
)
def test_r_round_matches_r(x: float, digits: int, expected: float) -> None:
    assert r_round(x, digits) == expected


def test_r_round_nan_digits_and_r_pow() -> None:
    assert math.isnan(r_round(0.5, math.nan))
    assert r_pow(10.0, 400.0) == math.inf
    assert r_pow(10.0, 2.0) == 100.0
    assert 0.5 / r_pow(10.0, 400.0) == 0.0


# -- statcheck: overflow and infinite df ----------------------------------------


def test_test_value_with_hundreds_of_decimals() -> None:
    # R: 10^400 is Inf, so the rounding margin of the test statistic is 0
    res = sc("t(28) = 2." + "0" * 400 + "1, p = .04")
    assert res["error"].tolist() == [True]
    assert error_test(0.04, "t", 2.0, math.nan, 28.0, "=", "=", 2.0, 400.0, True, 0.05, True)


def test_chi2_with_infinite_df() -> None:
    # U5: R fails the whole call on if (NA); the unverifiable result is dropped
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert sc(f"χ2({BIG}) = 1.5, p = .05") is None
        both = sc(f"χ2({BIG}) = 1.5, p = .05 and t(28) = 2.20, p = .03")
    assert both["raw"].tolist() == ["t(28) = 2.20, p = .03"]
    res = sc(f"χ2({BIG}) = 3.5, p = .05")
    assert res["computed_p"].tolist() == [1.0]
    assert res["error"].tolist() == [True]
    d = pd.DataFrame({"text": [f"A, χ2({BIG}) = 1.5, p = .05.", "B, t(28) = 2.20, p = .03."]})
    assert stats(d)["raw"].tolist() == ["t(28) = 2.20, p = .03"]


# -- statcheck: `== TRUE` flags --------------------------------------------------


def test_flags_that_are_neither_true_nor_false() -> None:
    # decision_error_test() itself still falls through both branches (NULL)
    assert decision_error_test(0.13, 0.036, "=", "=", 0.05, 2) is None
    # U149: statcheck() rejects such a pEqualAlphaSig up front (R failed only
    # for inconsistent results: "arguments imply differing number of rows")
    for texts in ("t(28) = 2.20, p = .13", "t(28) = 2.20, p = .04"):
        with pytest.raises(ValueError, match="pEqualAlphaSig"):
            sc(texts, pEqualAlphaSig=2)
    assert sc("t(28) = 2.20, p = .13", pEqualAlphaSig=1)["error"].tolist() == [True]
    # OneTailedTests = 2 is not TRUE: two-tailed p-values
    two = sc("t(28) = 2.20, p = .03", OneTailedTests=2)["computed_p"].iloc[0]
    assert two == pytest.approx(0.03622548, rel=1e-6)
    # pZeroError = 2 is not TRUE: p = 0.000 is judged by rounding
    assert sc("t(28) = 2.20, p = 0.000", pZeroError=2)["error"].tolist() == [True]
    assert sc("t(28) = 5.20, p = 0.000", pZeroError=2)["error"].tolist() == [False]


def test_na_flags_are_rejected_up_front() -> None:
    # U149: R fails on if (NA) only where a flag is tested (some texts only)
    for flag in ("OneTailedTests", "AllPValues", "pEqualAlphaSig", "pZeroError", "OneTailedTxt"):
        for texts in ("t(28) = 2.20, p = .03", "t(28) = 2.20, p = .04", "no stats"):
            with pytest.raises(ValueError, match=flag):
                sc(texts, **{flag: None})
    with pytest.raises(ValueError, match="messages"):
        statcheck("t(28) = 2.20, p = .03", messages=None)
    with pytest.raises(ValueError, match="alpha"):
        sc("t(28) = 2.20, p = .03", alpha=math.nan)
    with pytest.raises(RError):
        process_stats("t", 2.2, math.nan, 28.0, 0.13, "=", "=", 2.0, 2.0, False, True,
                      0.05, True, 2, False, False)  # fmt: skip


def test_empty_texts_warn_like_r() -> None:
    with pytest.warns(StatcheckWarning) as record:
        assert sc([]) is None
    assert [str(w.message) for w in record] == [
        "no non-missing arguments to max; returning -Inf",
        "NaNs produced",
    ]


def test_named_series_keeps_duplicate_names() -> None:
    texts = pd.Series(
        ["t(28) = 2.20, p = .03", "p = .01", "t(28) = 2.20, p = .13"], index=["a", "a", "b"]
    )
    res = sc(texts)
    assert res["source"].tolist() == ["a", "b"]
    assert res["apa_factor"].tolist() == [0.5, 1.0]


# -- ignore.case = TRUE: Unicode simple case folding (D29) ---------------------------


def test_case_insensitive_patterns_fold_unicode_case() -> None:
    # PCRE2 matches "ßnſ" as "ns" (s ~ U+017F) but TRE's grepl() does not, so R
    # treats it as a p-value without a comparison sign and fails (U149);
    # pytacheck folds ſ in both engines (D29), so "ns" is found consistently
    res = sc("t(28) = 2.20, p = .04 and ßnſ.")
    assert res["raw"].tolist() == ["t(28) = 2.20, p = .04"]
    assert res["apa_factor"].tolist() == [0.5]
    assert sc("p = .01 and ßnſ", AllPValues=True)["p_comp"].tolist() == ["=", "ns"]
    # D29: U+0130 folds to i (not for R's PCRE2 or TRE), so "İnsan" is a word,
    # not an "ns", and "İz" is not a z test
    res = sc("t(28) = 2.20, p = .04 İnsan.")
    assert res["raw"].tolist() == ["t(28) = 2.20, p = .04"]
    assert res["apa_factor"].tolist() == [1.0]
    assert sc("Test z = 1.96, p = .05.")["test_type"].tolist() == ["Z"]
    assert sc("Test İz = 1.96, p = .05.") is None
    # the Kelvin sign is a case variant of k: not a [^a-z] character, so the
    # result has no test name (R: "argument is of length zero"; U5)
    assert sc("Test \u212az = 1.96, p = .05.") is None
    # D29: extract_1tail folds U+017F to s (R's TRE does not)
    res = sc("t(28) = 1.80, p = .04 (one-ſided)", OneTailedTxt=True)
    assert res["one_tailed_in_txt"].tolist() == [True]
    assert res["error"].tolist() == [False]


# -- stats(): R's matching of `...` -----------------------------------------------

TEXTS = pd.DataFrame(
    {
        "text": [
            "A t-test, t(28) = 2.20, p = .03.",
            "An F-test, F(1, 20) = 5.1, p = .5.",
            "Also p = .01 here, 2 of them.",
        ]
    }
)


@pytest.mark.parametrize(
    ("args", "kwargs", "nrow"),
    [
        ((), {"alp": 0.6}, 2),  # partial name -> alpha
        ((), {"AllP": True}, 3),
        ((), {"OneTailedTe": True}, 2),
        ((), {"s": "t"}, 1),
        (("F",), {}, 1),  # positional -> stat
        (("F", True, 0.6), {}, 1),
        ((), {"texts": "t(2) = 5, p = .9"}, 0),  # the sentence becomes `stat`
        ((), {"texts": "p = .01", "AllPValues": True}, 3),
        ((), {"AllPValues": 2}, 3),
    ],
)
def test_stats_argument_matching(args, kwargs, nrow) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert len(stats(TEXTS, *args, **kwargs)) == nrow


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"One": True}, TypeError),  # ambiguous prefix
        ({"mess": True}, TypeError),  # matches `messages`, which stats() already sets
        ({"alpha": 0.1, "alp": 0.2}, TypeError),
        ({"pZ": False, "pZero": False}, TypeError),
        ({"typo": 1}, TypeError),
        ({"AllPValues": None}, ValueError),
        ({"pEqualAlphaSig": 2}, ValueError),
    ],
)
def test_stats_rejects_invalid_arguments(kwargs, error) -> None:
    # U4: R's every statcheck() call errors and stats() silently returns an empty table
    with pytest.raises(error):
        stats(TEXTS, **kwargs)


def test_rmath_module_exports() -> None:
    assert set(_rmath.__all__) >= {"pt", "pf", "pchisq", "pnorm", "r_round", "as_numeric"}
