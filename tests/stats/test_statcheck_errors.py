"""Port of statcheck 1.5.0's p-value, error and decision-error tests.

tests/testthat/test-compute-pvalues.R, test-error.R, test-decisionerror.R and
test-S3-methods.R. The critical t values the R tests build with ``qt()`` and
``paste()`` are written out as the strings R produces.
"""

from __future__ import annotations

import math

import pytest

from pytacheck.stats._rmath import pchisq, pf, pnorm, pt
from pytacheck.stats.statcheck import (
    VAR_COMPUTED_P,
    VAR_DEC_ERROR,
    VAR_DF1,
    VAR_ERROR,
    VAR_NR_DEC_ERRORS,
    VAR_NR_ERRORS,
    VAR_NR_PVALUES,
    VAR_RAW,
    VAR_SOURCE,
    r2t,
    statcheck,
    summary_statcheck,
    trim,
)


def sc(texts, **kwargs):
    return statcheck(texts, messages=False, **kwargs)


def error(txt: str, **kwargs) -> list[bool]:
    return sc(txt, **kwargs)[VAR_ERROR].tolist()


def dec_error(txt: str, **kwargs) -> bool:
    (value,) = sc(txt, **kwargs)[VAR_DEC_ERROR].tolist()
    return value


# -- test-compute-pvalues.R ---------------------------------------------------------


def test_p_values_for_t_tests() -> None:
    computed = pt(-1 * abs(2.20), 28) * 2
    assert sc("t(28) = 2.20, p = .03")[VAR_COMPUTED_P].iloc[0] == pytest.approx(computed)


def test_p_values_for_f_tests() -> None:
    computed = pf(2.20, 2, 28, lower_tail=False)
    assert sc("F(2, 28) = 2.20, p = .15")[VAR_COMPUTED_P].iloc[0] == pytest.approx(computed)


def test_p_values_for_correlations() -> None:
    computed = min(pt(-1 * abs(r2t(0.22, 28)), 28) * 2, 1)
    assert sc("r(28) = .22, p = .26")[VAR_COMPUTED_P].iloc[0] == pytest.approx(computed)


def test_p_values_for_z_tests() -> None:
    computed = pnorm(abs(2.20), lower_tail=False) * 2
    assert sc(" z = 2.20, p = .04")[VAR_COMPUTED_P].iloc[0] == pytest.approx(computed)


def test_p_values_for_chi2_and_q_tests() -> None:
    computed = pchisq(22.20, 28, lower_tail=False)
    assert sc("chi2(28) = 22.20, p = .79")[VAR_COMPUTED_P].iloc[0] == pytest.approx(computed)
    assert sc("Q(28) = 22.20, p = .79")[VAR_COMPUTED_P].iloc[0] == pytest.approx(computed)


def test_p_values_match_r() -> None:
    # print(..., digits = 15) in R 4.5
    assert sc("t(28) = 2.20, p = .03")[VAR_COMPUTED_P].iloc[0] == pytest.approx(
        0.0362254847788378, rel=1e-13
    )
    assert pf(2.2, 2, 28, lower_tail=False) == pytest.approx(0.129593224474093, rel=1e-13)
    assert pchisq(22.2, 28, lower_tail=False) == pytest.approx(0.771948704032936, rel=1e-13)
    assert pnorm(2.2, lower_tail=False) * 2 == pytest.approx(0.0278068950269972, rel=1e-13)
    assert pt(-40, 5000) * 2 == pytest.approx(8.41485047193754e-304, rel=1e-10)
    assert pt(-1e60, 3) == pytest.approx(1.10265779084355e-180, rel=1e-10)  # the nx > 1e100 branch


# -- test-S3-methods.R --------------------------------------------------------------

SIX = [
    "t(28) = 2.20, p = .06",
    "F(2, 28) = 2.20, p = .15",
    "r(28) = .22, p = .26",
    "chi2(28) = 22.20, p = .77",
    " z = 2.20, p = .04",
    "Q(28) = 22.20, p = .03",
]


def test_summary_for_a_single_source() -> None:
    summary = summary_statcheck(sc(" ".join(SIX)))
    assert len(summary) == 2
    assert summary[VAR_SOURCE].tolist() == ["01", "Total"]
    assert summary[VAR_NR_PVALUES].tolist() == [6, 6]
    assert summary[VAR_NR_ERRORS].tolist() == [5, 5]
    assert summary[VAR_NR_DEC_ERRORS].tolist() == [2, 2]


def test_summary_for_multiple_sources() -> None:
    summary = summary_statcheck(sc(SIX))
    assert len(summary) == 7
    assert summary[VAR_SOURCE].tolist() == [str(i) for i in range(1, 7)] + ["Total"]
    assert summary[VAR_NR_PVALUES].tolist() == [1] * 6 + [6]
    assert summary[VAR_NR_ERRORS].tolist() == [1, 1, 1, 0, 1, 1, 5]
    assert summary[VAR_NR_DEC_ERRORS].tolist() == [1, 0, 0, 0, 0, 1, 2]


def test_trim() -> None:
    result = sc("t(28) = 2.20, p = .06")
    concise = trim(result)
    assert list(concise.columns) == [VAR_SOURCE, VAR_RAW, VAR_COMPUTED_P, VAR_ERROR, VAR_DEC_ERROR]
    assert len(concise) == len(result)


# -- test-error.R -------------------------------------------------------------------


def test_simple_errors() -> None:
    for txt in [
        "t(28) = 2.20, p = .03",
        "F(2, 28) = 2.20, p = .15",
        "r(28) = .22, p = .26",
        "chi2(28) = 22.20, p = .79",
        " z = 2.20, p = .04",
        "Q(28) = 22.20, p = .79",
    ]:
        assert error(txt) == [True], txt


def test_negative_tests() -> None:
    assert error(" Z = -2.42, p = 0.016") == [False]
    assert error("t(28) = -2.20, p = .03") == [True]


def test_inexactly_reported_p_values() -> None:
    assert error("t(28) = 2.20, ns") == [True]
    assert error("t(28) = 2.20, p > .05") == [True]
    assert error("t(28) = 2.0, p < .05") == [False]


def test_decision_errors_are_also_errors() -> None:
    assert error("t(28) = 1.20, p = .03") == [True]
    assert error("t(28) = 2.20, p = .30") == [True]


def test_correctly_rounded_p_values() -> None:
    for txt in [
        "t(28) = 2, p = .02",
        "t(28) = 2, p = .14",
        "t(28) = 2.2, p = .03",  # rounded lower bound p-value
        "t(28) = 2.2, p = .04",
        "t(28) = 2.20, p = .036",
        "t(28) = 2.20, p = .037",
    ]:
        assert error(txt) == [False], txt


def test_one_tailed_tests_option() -> None:
    assert error("t(28) = 2.20, p = .02", OneTailedTests=True) == [False]
    assert error("t(28) = 2.20, p = .04", OneTailedTests=True) == [True]
    txt3 = (
        "this test is one-tailed: t(28) = 2.20, p = .02, but this one is not: t(28) = 2.20, p = .04"
    )
    assert error(txt3, OneTailedTests=True) == [False, True]


def test_automated_one_tailed_detection() -> None:
    txt1 = "t(28) = 2.20, p = .018"
    txt2 = "t(28) = 2.20, p = .01, one-tailed"
    txt3 = "t(28) = 2.20, p = .018, one-tailed"
    txt4 = "t(28) = 2.20, p = .018, one-sided"
    txt5 = "t(28) = 2.20, p = .018, directional"
    assert error(txt1) == [True]
    assert error(txt1, OneTailedTxt=True) == [True]
    assert error(txt2, OneTailedTxt=True) == [True]
    assert error(txt3) == [True]
    assert error(txt3, OneTailedTxt=True) == [False]
    assert error(txt4, OneTailedTxt=True) == [False]
    assert error(txt5, OneTailedTxt=True) == [False]
    p_1tail = pt(2.20, 28, lower_tail=False)
    computed = sc([txt3, txt4, txt5], OneTailedTxt=True)[VAR_COMPUTED_P].tolist()
    assert computed == pytest.approx([p_1tail] * 3)


def test_p_zero_error_option() -> None:
    txt1 = "t(28) = 22.20, p = .000"
    txt2 = "t(28) = 22.20, p < .000"  # this is always an error
    assert error(txt1) == [True]
    assert error(txt1, pZeroError=False) == [False]
    assert error(txt2) == [True]
    assert error(txt2, pZeroError=False) == [True]


# paste(pt(2.15, 28, lower.tail = FALSE) * 2) and paste(pt(2.25, ...) * 2) in R
UPP = "0.0403381832647296"
LOWP = "0.0324934361512091"
# "p < LOWP" is an error only if LOWP is below the lowest p that t = 2.2 allows, which
# LOWP (15 digits) misses by 1-2 ulps; scipy's pbeta is not R's toms708 to the last ulp
# on every platform, so this row uses the value just below that bound instead
BELOW_LOWP = repr(math.nextafter(pt(-2.25, 28) * 2, 0))


@pytest.mark.parametrize(
    ("txt", "expected"),
    [
        # t = ...
        ("t(28) = 2.2, p = .036", False),
        ("t(28) = 2.2, p < .08", False),
        ("t(28) = 2.2, p > .02", False),
        (f"t(28) = 2.2, p > {UPP}", True),
        (f"t(28) = 2.2, p < {BELOW_LOWP}", True),
        ("t(28) = 2.2, p = .08", True),
        ("t(28) = 2.2, p = .02", True),
        ("t(28) = 2.2, p > .08", True),
        ("t(28) = 2.2, p < .02", True),
        # t < ...
        (f"t(28) < 2.20, p > {UPP}", False),
        ("t(28) < 2.2, p = .08", False),
        ("t(28) < 2.2, p > .08", False),
        ("t(28) < 2.2, p < .08", False),
        ("t(28) < 2.2, p > .02", False),
        (f"t(28) < 2.2, p = {LOWP}", True),
        (f"t(28) < 2.2, p < {LOWP}", True),
        ("t(28) < 2.2, p < .02", True),
        ("t(28) < 2.2, p = .02", True),
        # t > ...
        (f"t(28) > 2.20, p < {UPP}", False),
        ("t(28) > 2.2, p = .02", False),
        ("t(28) > 2.2, p > .02", False),
        ("t(28) > 2.2, p < .02", False),
        ("t(28) > 2.2, p < .08", False),
        (f"t(28) > 2.2, p = {UPP}", True),
        (f"t(28) > 2.2, p > {UPP}", True),
        ("t(28) > 2.2, p > .08", True),
        ("t(28) > 2.2, p = .08", True),
    ],
)
def test_error_by_comparison(txt: str, expected: bool) -> None:
    assert error(txt) == [expected]


# -- test-decisionerror.R -----------------------------------------------------------


def test_simple_decision_errors() -> None:
    for txt in [
        "t(28) = 2.20, p = .06",
        "t(28) = 1.20, p = .03",
        "F(2, 28) = 22.20, p = .06",
        "F(2, 28) = 2.20, p = .03",
        "r(28) = .22, p = .03",
        "r(28) = .52, p = .06",
        "chi2(28) = 2.20, p = .03",
        "chi2(28) = 52.20, p = .06",
        " z = 1.20, p = .03",
        " z = 2.20, p = .06",
        "Q(28) = 2.20, p = .03",
        "Q(28) = 52.20, p = .06",
    ]:
        assert dec_error(txt), txt


def test_decision_errors_with_other_alpha_levels() -> None:
    assert dec_error("t(28) = 2.20, p = .06", alpha=0.05)
    assert dec_error("t(28) = 1.20, p = .03", alpha=0.05)
    assert dec_error("t(28) = 2.20, p = .11", alpha=0.10)
    assert dec_error("t(28) = 1.20, p = .09", alpha=0.10)
    assert dec_error("t(28) = 5.20, p = .02", alpha=0.01)
    assert dec_error("t(28) = 1.20, p = .005", alpha=0.01)


def test_decision_errors_with_p_equal_alpha() -> None:
    txt1 = "t(28) = 2.20, p = .05"
    txt2 = "t(28) = 2.20, p = .10"
    assert not dec_error(txt1)
    assert dec_error(txt1, pEqualAlphaSig=False)
    assert not dec_error(txt2, alpha=0.10)
    assert dec_error(txt2, pEqualAlphaSig=False, alpha=0.10)


# paste0(qt(p / 2, 28, lower.tail = FALSE) + delta) in R, for p = .04, .05, .06
T = {
    ".04": "2.15393486769497",
    ".04+.001": "2.15493486769497",
    ".04-.001": "2.15293486769497",
    ".05": "2.04840714179524",
    ".05+.001": "2.04940714179524",
    ".05-.001": "2.04740714179525",
    ".05+.0001": "2.04850714179525",
    ".05-.0001": "2.04830714179524",
    ".06": "1.9601364678398",
    ".06+.001": "1.9611364678398",
    ".06-.001": "1.9591364678398",
}

# (test comparison, [(t, "p ..." , default, pEqualAlphaSig = FALSE)]); None = not tested
DECISION_CASES = {
    "t = ..., p = ...": (
        "=",
        [
            (".04", "p = .04", False, False),
            (".05+.0001", "p = .04", False, None),
            (".05-.0001", "p = .04", None, True),
            (".06", "p = .04", True, True),
            (".04", "p = .05", False, True),
            (".05", "p = .05", False, False),
            (".06", "p = .05", True, False),
            (".04", "p = .06", True, True),
            (".05+.0001", "p = .06", True, None),
            (".05-.0001", "p = .06", None, False),
            (".06", "p = .06", False, False),
        ],
    ),
    "t = ..., p < ...": (
        "=",
        [
            (".04-.001", "p < .04", False, False),
            (".05+.0001", "p < .04", False, None),
            (".05-.0001", "p < .04", None, True),
            (".06", "p < .04", True, True),
            (".04", "p < .05", False, False),
            (".05+.0001", "p < .0500", False, None),
            (".05-.0001", "p < .0500", None, True),
            (".06", "p < .05", True, True),
            (".04", "p < .06", False, False),
            (".05+.0001", "p < .06", False, None),
            (".05-.0001", "p < .06", None, False),
            (".06", "p < .06", False, False),
        ],
    ),
    "t = ..., p > ...": (
        "=",
        [
            (".04+.001", "p > .04", False, False),
            (".05+.0001", "p > .04", False, None),
            (".05-.0001", "p > .04", None, False),
            (".06", "p > .04", False, False),
            (".04", "p > .05", True, True),
            (".05+.0001", "p > .05", True, None),
            (".05-.0001", "p > .05", None, False),
            (".06", "p > .05", False, False),
            (".04", "p > .06", True, True),
            (".05+.0001", "p > .06", True, None),
            (".05-.0001", "p > .06", None, False),
            (".06", "p > .06", False, False),
        ],
    ),
    "t < ..., p = ...": (
        "<",
        [
            (".04-.001", "p = .0400", False, False),
            (".05-.0001", "p = .04", True, True),
            (".06", "p = .04", True, True),
            (".04", "p = .05", False, False),
            (".05-.001", "p = .0500", True, False),
            (".06", "p = .05", True, False),
            (".04", "p = .06", False, False),
            (".05-.0001", "p = .0600", False, False),
            (".06-.001", "p = .0600", False, False),
        ],
    ),
    "t < ..., p < ...": (
        "<",
        [
            (".04", "p < .04", False, False),
            (".05-.0001", "p < .04", True, True),
            (".06", "p < .04", True, True),
            (".04", "p < .05", False, False),
            (".05-.0001", "p < .05", True, True),
            (".06", "p < .05", True, True),
            (".04", "p < .06", False, False),
            (".05-.0001", "p < .06", False, False),
            (".06", "p < .06", False, False),
        ],
    ),
    "t < ..., p > ...": (
        "<",
        [
            (".04", "p > .04", False, False),
            (".05-.0001", "p > .04", False, False),
            (".06", "p > .04", False, False),
            (".04", "p > .05", False, False),
            (".05-.0001", "p > .05", False, False),
            (".06", "p > .05", False, False),
            (".04", "p > .06", False, False),
            (".05-.0001", "p > .06", False, False),
            (".06", "p > .06", False, False),
        ],
    ),
    "t > ..., p = ...": (
        ">",
        [
            (".04+.001", "p = .0400", False, False),
            (".05+.0001", "p = .04", False, False),
            (".06", "p = .04", False, False),
            (".04", "p = .05", False, True),
            (".05+.001", "p = .0500", False, True),
            (".06", "p = .05", False, False),
            (".04", "p = .06", True, True),
            (".05+.001", "p = .0600", True, True),
            (".06+.001", "p = .0600", False, False),
        ],
    ),
    "t > ..., p < ...": (
        ">",
        [
            (".04+.001", "p < .0400", False, False),
            (".05+.0001", "p < .04", False, False),
            (".06", "p < .04", False, False),
            (".04", "p < .05", False, False),
            (".05+.001", "p < .0500", False, False),
            (".06", "p < .05", False, False),
            (".04", "p < .06", False, False),
            (".05+.001", "p < .0600", False, False),
            (".06+.001", "p < .0600", False, False),
        ],
    ),
    "t > ..., p > ...": (
        ">",
        [
            (".04+.001", "p > .0400", False, False),
            (".05+.0001", "p > .04", False, False),
            (".06", "p > .04", False, False),
            (".04", "p > .05", True, True),
            (".05+.001", "p > .0500", True, True),
            (".06", "p > .05", False, False),
            (".04", "p > .06", True, True),
            (".05+.001", "p > .0600", True, True),
            (".06+.001", "p > .0600", False, False),
        ],
    ),
}


def _decision_params() -> list[tuple[str, str, bool, bool]]:
    params = []
    for block, (comp, rows) in DECISION_CASES.items():
        for t, p, default, strict in rows:
            txt = f"t(28) {comp} {T[t]}, {p}"
            if default is not None:
                params.append(pytest.param(txt, True, default, id=f"{block}|{txt}"))
            if strict is not None:
                params.append(pytest.param(txt, False, strict, id=f"{block}|{txt}|strict"))
    return params


@pytest.mark.parametrize(("txt", "p_equal_alpha_sig", "expected"), _decision_params())
def test_decision_error_by_comparison(txt: str, p_equal_alpha_sig: bool, expected: bool) -> None:
    assert dec_error(txt, pEqualAlphaSig=p_equal_alpha_sig) is expected


def test_df1_of_misread_f_test() -> None:
    assert sc("F(I, 20) = 4.35, p = .05")[VAR_DF1].tolist() == [1.0]
