"""Edge cases found in the review of the stats port; R's values come from R 4.5.3.

Covers numeric corner cases (infinite degrees of freedom, overflowing powers of
ten, round() with many digits), statcheck's flags and stats()'s arguments. The
statcheck port computes with plain Python values and scipy (D78).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from metacheck._r import r_round
from metacheck.stats.core import stats
from metacheck.stats.statcheck import (
    compute_p,
    decision_error_test,
    error_test,
    process_stats,
    statcheck,
)

BIG = "1" + "0" * 310  # parses as Inf


def sc(texts, **kwargs):
    return statcheck(texts, messages=False, **kwargs)


# -- p-values -------------------------------------------------------------------------


def test_compute_p_infinite_df() -> None:
    # D78: t is the normal distribution, chi-square and Q get their limit 1, and
    # F (scipy's fdtrc has no value; R: the chi-square limit) cannot be computed
    assert compute_p("t", 2.0, None, math.inf, True) == pytest.approx(0.04550026, rel=1e-6)
    assert compute_p("r", 0.3, None, math.inf, True) == 0.0
    for x in (0.2, 2.0, 1e300):
        assert compute_p("Chi2", x, math.inf, None, True) == 1.0
        assert compute_p("Q", x, math.inf, None, True) == 1.0
    assert compute_p("F", 2.0, math.inf, 3.0, True) is None
    assert compute_p("F", 2.0, 3.0, math.inf, True) is None


def test_compute_p_without_a_value() -> None:
    assert compute_p("t", 2.0, None, 0.0, True) is None
    assert compute_p("r", 0.3, None, 0.0, True) is None
    assert compute_p("F", 2.0, 0.0, 20.0, True) is None
    assert compute_p("t", None, None, 28.0, True) is None
    assert compute_p("F", 2.0, None, 20.0, True) is None
    # as in R: chi-square with 0 df is a point mass at 0, and a statistic below
    # the support has p = 1
    assert compute_p("Chi2", 3.84, 0.0, None, True) == 0.0
    assert compute_p("F", -1.0, 1.0, 20.0, True) == 1.0
    assert compute_p("Chi2", -1.0, 2.0, None, True) == 1.0
    with pytest.raises(ValueError, match="test_type"):
        compute_p("W", 1.0, 1.0, 1.0, True)


def test_compute_p_matches_r() -> None:
    # R 4.5: print(c(2 * pt(-2.2, 28), pf(2.2, 2, 28, lower.tail = FALSE),
    #   pchisq(22.2, 28, lower.tail = FALSE), 2 * pnorm(-1.96)), digits = 15)
    assert compute_p("t", 2.2, None, 28.0, True) == pytest.approx(0.0362254847788378, rel=1e-14)
    assert compute_p("F", 2.2, 2.0, 28.0, True) == pytest.approx(0.129593224474093, rel=1e-14)
    assert compute_p("Chi2", 22.2, 28.0, None, True) == pytest.approx(0.771948704032936, rel=1e-14)
    assert compute_p("Z", 1.96, None, None, True) == pytest.approx(0.0499957902964409, rel=1e-14)
    # far tails, where the reduction R uses keeps its precision
    assert compute_p("t", 40.0, None, 5000.0, False) == pytest.approx(4.20742523596789e-304)
    assert compute_p("t", 1e-8, None, 10.0, True) == pytest.approx(0.99999999220, rel=1e-9)


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


# -- statcheck: overflow and infinite df ----------------------------------------


def test_test_value_with_hundreds_of_decimals() -> None:
    # R: 10^400 is Inf, so the rounding margin of the test statistic is 0
    res = sc("t(28) = 2." + "0" * 400 + "1, p = .04")
    assert res["error"].tolist() == [True]
    assert error_test(0.04, "t", 2.0, math.nan, 28.0, "=", "=", 2.0, 400.0, True, 0.05, True)


def test_results_with_infinite_df() -> None:
    # D78 (R fails the whole call on if (NA) for a small chi-square statistic)
    res = sc(f"χ2({BIG}) = 1.5, p = .05 and t(28) = 2.20, p = .03")
    assert res["raw"].tolist() == [f"χ2({BIG}) = 1.5, p = .05", "t(28) = 2.20, p = .03"]
    assert res["computed_p"].tolist()[0] == 1.0
    assert res["error"].tolist() == [True, True]
    assert sc(f"t({BIG}) = 2.1, p = .04")["computed_p"].tolist() == [
        pytest.approx(0.03572884, rel=1e-6)
    ]
    # F with an infinite degree of freedom cannot be checked and is dropped
    assert sc(f"F(2, {BIG}) = 0.5, p = .61") is None
    d = pd.DataFrame({"text": [f"A, F(2, {BIG}) = 0.5, p = .61.", "B, t(28) = 2.20, p = .03."]})
    assert stats(d)["raw"].tolist() == ["t(28) = 2.20, p = .03"]


# -- statcheck: flags ---------------------------------------------------------------


def test_flags_must_be_true_or_false() -> None:
    # D78: R compares flags with == TRUE (2 means FALSE); here they raise
    for flag in ("OneTailedTests", "AllPValues", "pEqualAlphaSig", "pZeroError", "OneTailedTxt"):
        for value in (2, None, math.nan, pd.NA, "TRUE"):
            for texts in ("t(28) = 2.20, p = .03", "no stats"):
                with pytest.raises(ValueError, match=flag):
                    sc(texts, **{flag: value})
    with pytest.raises(ValueError, match="pEqualAlphaSig"):
        decision_error_test(0.13, 0.036, "=", "=", 0.05, 2)
    with pytest.raises(ValueError, match="messages"):
        statcheck("t(28) = 2.20, p = .03", messages=None)
    for alpha in (math.nan, None, True, "0.05"):
        with pytest.raises(ValueError, match="alpha"):
            sc("t(28) = 2.20, p = .03", alpha=alpha)
    with pytest.raises(ValueError, match="pEqualAlphaSig"):
        process_stats("t", 2.2, None, 28.0, 0.13, "=", "=", 2.0, 2.0, False, True,
                      0.05, True, 2, False, False)  # fmt: skip
    # 1 and 0 are TRUE and FALSE
    assert sc("t(28) = 2.20, p = .13", pEqualAlphaSig=1)["error"].tolist() == [True]
    assert sc("t(28) = 2.20, p = 0.000", pZeroError=0)["error"].tolist() == [True]
    assert sc("t(28) = 5.20, p = 0.000", pZeroError=0)["error"].tolist() == [False]


def test_undecidable_tests_give_none() -> None:
    args = ("t", 2.2, None, 28.0, "=", "=", 2.0, 2.0, True, 0.05, True)
    assert error_test(None, *args) is None
    assert error_test(0.04, "t", 2.2, None, 0.0, "=", "=", 2.0, 2.0, True, 0.05, True) is None
    # p = 0 is an error whatever the statistic (pZeroError)
    assert error_test(0.0, "t", 2.2, None, 0.0, "=", "=", 3.0, 2.0, True, 0.05, True) is True
    assert decision_error_test(None, 0.04, "=", "=", 0.05, True) is None
    assert decision_error_test(0.04, None, "=", "=", 0.05, True) is None
    # a "<" statistic with a "> p" (or the reverse) is never an error, as in R
    assert decision_error_test(0.04, None, "<", ">", 0.05, True) is False
    out = process_stats("t", 2.2, None, 0.0, 0.03, "=", "=", 2.0, 2.0, False, True,
                        0.05, True, True, False, False)  # fmt: skip
    assert out.isna().all(axis=None)


def test_empty_texts(capsys) -> None:
    assert sc([]) is None
    assert "did not find any results" in capsys.readouterr().out


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


# -- stats(): statcheck's settings ----------------------------------------------------

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
        ((), {"alpha": 0.6}, 2),
        ((), {"AllPValues": True}, 3),
        ((), {"OneTailedTests": True}, 2),
        ((), {"stat": "t"}, 1),
        (("F",), {}, 1),  # positional, in statcheck's order -> stat
        (("F", True, 0.6), {}, 1),
    ],
)
def test_stats_arguments(args, kwargs, nrow) -> None:
    assert len(stats(TEXTS, *args, **kwargs)) == nrow


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        # D78: no R argument matching (R: partial names, `texts =` replaces the text)
        ({"alp": 0.6}, TypeError),
        ({"One": True}, TypeError),
        ({"texts": "p = .01"}, TypeError),
        ({"messages": False}, TypeError),
        ({"typo": 1}, TypeError),
        ({"AllPValues": None}, ValueError),
        ({"AllPValues": 2}, ValueError),
        ({"pEqualAlphaSig": 2}, ValueError),
    ],
)
def test_stats_rejects_invalid_arguments(kwargs, error) -> None:
    # U4: R's every statcheck() call errors and stats() silently returns an empty table
    with pytest.raises(error):
        stats(TEXTS, **kwargs)
