"""Port of statcheck 1.5.0's extraction tests.

tests/testthat/test-extract-{t,F,chi2,Q,z}-tests.R, test-extract-correlations.R,
test-extract-pvalues.R and test-APAfactor.R.
"""

from __future__ import annotations

import math

import pytest

from metacheck.stats.statcheck import (
    VAR_APAFACTOR,
    VAR_DF1,
    VAR_DF2,
    VAR_P_COMPARISON,
    VAR_RAW,
    VAR_REPORTED_P,
    VAR_SOURCE,
    VAR_TEST_COMPARISON,
    VAR_TEST_VALUE,
    VAR_TYPE,
    statcheck,
)


def sc(texts, **kwargs):
    return statcheck(texts, messages=False, **kwargs)


def nothing_found(capsys: pytest.CaptureFixture[str], texts, what="results", **kwargs) -> None:
    assert sc(texts, **kwargs) is None
    assert f"did not find any {what}" in capsys.readouterr().out


def check_row(result, test_type, df1, df2, comp, value, p_comp, p, raw) -> None:
    assert len(result) == 1
    row = result.iloc[0]
    assert row[VAR_TYPE] == test_type
    for col, want in ((VAR_DF1, df1), (VAR_DF2, df2)):
        if want is None:
            assert math.isnan(row[col])
        else:
            assert row[col] == want
    assert row[VAR_TEST_COMPARISON] == comp
    assert row[VAR_TEST_VALUE] == pytest.approx(value)
    assert row[VAR_P_COMPARISON] == p_comp
    assert row[VAR_REPORTED_P] == pytest.approx(p)
    assert row[VAR_RAW] == raw


# -- t-tests ------------------------------------------------------------------------


def test_t_tests_are_correctly_parsed() -> None:
    result = sc("t(28) = 2.20, p = .03")
    check_row(result, "t", None, 28, "=", 2.2, "=", 0.03, "t(28) = 2.20, p = .03")


def test_t_tests_in_sentences() -> None:
    txt1 = "The effect was very significant, t(28) = 2.20, p = .03."
    txt2 = "Both effects were very significant, t(28) = 2.20, p = .03, t(28) = 1.23, p = .04."
    result = sc([txt1, txt2])
    assert len(result) == 3
    assert result[VAR_SOURCE].tolist() == ["1", "2", "2"]


def test_t_tests_with_different_spacing() -> None:
    assert len(sc([" t ( 28 ) = 2.20 , p = .03", "t(28)=2.20,p=.03"])) == 2


def test_variations_in_the_t_statistic() -> None:
    texts = [
        "t(28) = -2.20, p = .03",
        "t(28) = 2,000.20, p = .03",
        "t(28) < 2.20, p = .03",
        "t(28) > 2.20, p = .03",
        "t(28) = %^&2.20, p = .03",  # read as -2.20
    ]
    result = sc(texts)
    assert len(result) == 5
    assert result[VAR_TEST_VALUE].tolist() == [-2.2, 2000.2, 2.2, 2.2, -2.2]


def test_variations_in_the_p_value() -> None:
    texts = [
        "t(28) = 2.20, p = 0.03",
        "t(28) = 2.20, p < .03",
        "t(28) = 2.20, p > .03",
        "t(28) = 2.20, ns",
        "t(28) = 2.20, p = .5e-3",
    ]
    assert len(sc(texts)) == 5


def test_corrected_degrees_of_freedom_in_t_tests() -> None:
    result = sc("t(28.1) = 2.20, p = .03")
    assert len(result) == 1
    assert result[VAR_DF2].iloc[0] == 28.1


def test_missing_input(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, None)


def test_incorrect_punctuation_in_t_tests(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, ["t(28) = 2.20; p = .03", "t[28] = 2.20, p = .03"])


def test_capital_t_is_not_retrieved(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, "T(28) = 2.20, p = .03")


def test_p_values_larger_than_1_are_not_retrieved(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, "t(28) = 2.20, p = 1.03")


def test_t_tests_with_two_dfs_are_not_retrieved(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, "t(2,28) = 2.20, p = .03")


def test_weird_minus_sign_followed_by_space(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, " t(553) = − 4.46, p < .0001")


def test_multiple_comparison_signs(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, ["t(38) >= 2.25, p = .03", "t(38) = 2.25, p >= .03"])


# -- F-tests ------------------------------------------------------------------------


def test_f_tests_are_correctly_parsed() -> None:
    result = sc("F(2, 28) = 2.20, p = .03")
    check_row(result, "F", 2, 28, "=", 2.2, "=", 0.03, "F(2, 28) = 2.20, p = .03")


def test_f_tests_in_sentences() -> None:
    txt1 = "The effect was very significant, F(2, 28) = 2.20, p = .03."
    txt2 = "Both effects were very significant, F(2, 28) = 2.20, p = .03, F(2, 28) = 1.23, p = .04."
    result = sc([txt1, txt2])
    assert len(result) == 3
    assert result[VAR_SOURCE].tolist() == ["1", "2", "2"]


def test_f_tests_with_different_spacing() -> None:
    assert len(sc([" F ( 2 , 28 ) = 2.20 , p = .03", "F(2,28)=2.20,p=.03"])) == 2


def test_corrected_degrees_of_freedom_in_f_tests() -> None:
    # "F(l, 76)": this wrong notation happens occasionally in articles
    result = sc(["F(2.1, 28.1) = 2.20, p = .03", "F(l, 76) = 23.95, p <.001"])
    assert len(result) == 2
    assert result[VAR_DF1].tolist() == [2.1, 1]
    assert result[VAR_DF2].tolist() == [28.1, 76]


# -- correlations -------------------------------------------------------------------


def test_correlations_are_correctly_parsed() -> None:
    result = sc("r(28) = .20, p = .03")
    check_row(result, "r", None, 28, "=", 0.2, "=", 0.03, "r(28) = .20, p = .03")


def test_correlations_in_sentences() -> None:
    txt1 = "The effect was very significant, r(28) = .20, p = .03."
    txt2 = "Both effects were very significant, r(28) = .20, p = .03, r(28) = .23, p = .04."
    result = sc([txt1, txt2])
    assert len(result) == 3
    assert result[VAR_SOURCE].tolist() == ["1", "2", "2"]


def test_correlations_with_different_spacing() -> None:
    assert len(sc([" r ( 28 ) = .20 , p = .03", "r(28)=.20,p=.03"])) == 2


def test_correlations_with_impossible_values(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, ["r(16) = 26.05, p = .10", "r(28) = −59, p = .0008"])


# -- chi-square tests ---------------------------------------------------------------


def test_chi2_tests_are_correctly_parsed() -> None:
    result = sc("chi2(28) = 2.20, p = .03")
    check_row(result, "Chi2", 28, None, "=", 2.2, "=", 0.03, "i2(28) = 2.20, p = .03")


def test_chi2_tests_in_sentences() -> None:
    txt1 = "The effect was very significant, chi2(28) = 2.20, p = .03."
    txt2 = "Both effects were very significant, chi2(28) = 2.20, p = .03, chi2(28) = 1.23, p = .04."
    result = sc([txt1, txt2])
    assert len(result) == 3
    assert result[VAR_SOURCE].tolist() == ["1", "2", "2"]


def test_variations_in_spelling_chi() -> None:
    texts = ["X2(28) = 2.20, p = .03", "x2(28) = 2.20, p = .03", "chi_2(28) = 2.20, p = .03"]
    assert len(sc(texts)) == 3


def test_variations_in_chi2_df() -> None:
    texts = [
        "chi2(28, N = 129) = 2.2, p = .03",
        "chi2(1, N = 11,455) = 16.78, p <.001",
        "chi2(1, n = 81) = 3.25, p = 0.07",  # also extract lowercase n
    ]
    assert len(sc(texts)) == 3


def test_chi2_tests_with_different_spacing() -> None:
    assert len(sc([" chi2 ( 28 ) = 2.20 , p = .03", "chi2(28)=2.20,p=.03"])) == 2


# -- Q-tests ------------------------------------------------------------------------


def test_q_tests_are_correctly_parsed() -> None:
    result = sc("Q(2) = 2.20, p = .03")
    check_row(result, "Q", 2, None, "=", 2.2, "=", 0.03, "Q(2) = 2.20, p = .03")


def test_q_tests_in_sentences() -> None:
    txt1 = "The effect was very significant, Q(2) = 2.20, p = .03."
    txt2 = "Both effects were very significant, Q(2) = 2.20, p = .03, Q(2) = 1.23, p = .04."
    result = sc([txt1, txt2])
    assert len(result) == 3
    assert result[VAR_SOURCE].tolist() == ["1", "2", "2"]


def test_q_tests_with_different_spacing() -> None:
    assert len(sc([" Q ( 2 ) = 2.20 , p = .03", "Q(2)=2.20,p=.03"])) == 2


def test_types_of_q_tests() -> None:
    texts = [
        "Qw(2) = 2.20, p = .03",
        "Qwithin(2) = 2.20, p = .03",
        "Q-within(2) = 2.20, p = .03",
        "Qb(2) = 2.20, p = .03",
        "Qbetween(2) = 2.20, p = .03",
        "Q-between(2) = 2.20, p = .03",
    ]
    result = sc(texts)
    assert result[VAR_TYPE].tolist() == ["Qw"] * 3 + ["Qb"] * 3


def test_q_subtype_is_read_from_the_q_token() -> None:
    # U5: statcheck looks for a lowercase "b" and then "w" anywhere, so
    # "Q-Between" (a "w", no "b") was a Qw test and "QWithin" a plain Q test
    texts = [
        "Q-Between(2) = 2.20, p = .33",
        "QWithin(3) = 2.20, p = .53",
        "QB(2) = 2.20, p = .33",
        "Q-w(3) = 2.20, p = .53",
        "Qbetween(2) = 2.20, p = .33",
        "Q(2) = 2.20, p = .33",
    ]
    assert sc(texts)[VAR_TYPE].tolist() == ["Qb", "Qw", "Qb", "Qw", "Qb", "Q"]


def test_stats_that_only_look_like_q_tests(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, ["Qs(2) = 2.2, p = .03", "Qb(2, N = 187) = 2.20, p = .03"])


# -- z-tests ------------------------------------------------------------------------


def test_z_tests_are_correctly_parsed() -> None:
    result = sc(" z = 2.20, p = .03")
    check_row(result, "Z", None, None, "=", 2.2, "=", 0.03, "z = 2.20, p = .03")


def test_z_tests_in_sentences() -> None:
    txt1 = "The effect was very significant, z = 2.20, p = .03."
    txt2 = "Both effects were very significant, z = 2.20, p = .03, z = 1.23, p = .04."
    result = sc([txt1, txt2])
    assert len(result) == 3
    assert result[VAR_SOURCE].tolist() == ["1", "2", "2"]


def test_z_tests_with_different_spacing() -> None:
    assert len(sc([" z = 2.20 , p = .03", " z=2.20,p=.03"])) == 2


def test_upper_case_z_tests() -> None:
    assert len(sc(" Z = 2.20 , p = .03")) == 1


def test_z_with_degrees_of_freedom_is_not_matched(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, [" z(28) = 2.20, p = .03", " Z(28) = 2.20, p = .03"])


# -- p-values -----------------------------------------------------------------------


def test_p_values_are_correctly_parsed() -> None:
    result = sc(["p = .05", "p < .05", "p > .05"], AllPValues=True)
    assert len(result) == 3
    assert result[VAR_P_COMPARISON].tolist() == ["=", "<", ">"]
    assert result[VAR_REPORTED_P].tolist() == [0.05] * 3


def test_results_reported_as_ns() -> None:
    result = sc("the result was not significant, ns", AllPValues=True)
    assert len(result) == 1
    assert result[VAR_P_COMPARISON].iloc[0] == "ns"
    assert math.isnan(result[VAR_REPORTED_P].iloc[0])


def test_page_numbers_are_not_extracted(capsys: pytest.CaptureFixture[str]) -> None:
    nothing_found(capsys, "see p. 01", what="p-values", AllPValues=True)


# -- APA factor ---------------------------------------------------------------------


def test_correct_apa_factor() -> None:
    txt1 = (
        "This text has 50% of its stats in APA style: t(28) = 2.20, p < .05, some other p = .035."
    )
    txt2 = "This text has 100% of its stats in APA style: t(28) = 2.20, p < .05."
    assert sc(txt1)[VAR_APAFACTOR].tolist() == [0.5]
    assert sc(txt2)[VAR_APAFACTOR].tolist() == [1]
    assert sc([txt1, txt2])[VAR_APAFACTOR].tolist() == [0.5, 1]


def test_apa_factor_when_a_source_has_no_nhst() -> None:
    txt1 = (
        "This text has 50% of its stats in APA style: t(28) = 2.20, p < .05, some other p = .035."
    )
    txt2 = "This text has 0% of its stats in APA style: p < .05."
    result = sc([txt1, txt2])
    assert result[VAR_APAFACTOR].tolist() == [0.5]
    assert len(result) == 1


def test_source_names() -> None:
    # formatC(1, width = 0, flag = "0") is "01"; 10 texts need 1 digit, 11 need 2
    assert sc("t(28) = 2.20, p = .03")[VAR_SOURCE].tolist() == ["01"]
    ten = sc(["t(28) = 2.20, p = .03"] * 10)[VAR_SOURCE].tolist()
    assert ten[0] == "1" and ten[-1] == "10"
    eleven = sc(["t(28) = 2.20, p = .03"] * 11)[VAR_SOURCE].tolist()
    assert eleven[0] == "01" and eleven[-1] == "11"
    named = sc({"a": "t(28) = 2.20, p = .03", "b": "F(2, 28) = 2.20, p = .03"})
    assert named[VAR_SOURCE].tolist() == ["a", "b"]
