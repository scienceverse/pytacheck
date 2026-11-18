"""metacheck 0.4.4.4 to 0.4.4.6: impossible p-values, subscripts of p, the raised decimal point, chi-squared with a space.

Port of the new tests of ``tests/testthat/test-text-extractors.R`` and ``test-module-stat.R``
(#351, #645, #648).
"""

from __future__ import annotations

import pytest

import metacheck as pc
from metacheck.module import module_run
from metacheck.stats.helpers import _TEST_STAT_PATTERN
from metacheck.text.extract import _p_extract, extract_eq, extract_p_values

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def test_stat_p_exact_lists_impossible_p_values() -> None:
    s = [
        "The groups differed, t(134) = -2.32, p = -.13, d = -0.4.",
        "Controls and the BMS group differed (Z = 3.091, P = 3.091).",
        "Age did not differ, χ 2 (10) = 9.61, p = 7.15.",
        "Less garbage was found (p = 3.23, CI = 2.11-4.61).",  # no statistic: a proportion
        "The model fit, z = 4.61, p = 8.06 × 10 -06.",  # read right
        "An effect, z = 7.68, p = 6.57 � 10 -14.",  # power of ten garbled
        "Both were negative, p < -0.6 -0.4 -0.2 0.0 0.2.",  # a figure's axis
        "The ratio was N: P = À0.23*P concentration + 1.17.",  # a formula
        "Group A differed, p < .05.",
    ]
    mo = module_run(pc.test_paper(s), "stat_p_exact")
    t = mo["table"]
    assert t.loc[t["impossible"].fillna(False).astype(bool), "text"].tolist() == [
        "P = 3.091",
        "p = 7.15",
        "p = -.13",
    ]
    assert mo["summary_table"]["n_impossible"].tolist() == [3]
    # an impossible p-value is not also imprecise
    assert mo["summary_table"]["n_imprecise"].tolist() == [1]
    assert mo["traffic_light"] == "red"
    assert "3 impossible *p* values" in mo["summary_text"]
    assert any("cannot be right" in str(r) for r in mo["report"])


def test_stat_p_exact_gives_each_p_value_its_own_row() -> None:
    s = "The indirect effect was significant (p < .01), as was the direct effect (p < .01)."
    mo = module_run(pc.test_paper(s), "stat_p_exact")
    assert mo["summary_table"]["n_imprecise"].tolist() == [2]
    assert "2 imprecise" in mo["summary_text"]
    p1 = pc.test_paper(s)
    p1["paper_id"] = "a"
    p2 = pc.test_paper(s)
    p2["paper_id"] = "b"
    mo = module_run(pc.PaperList([p1, p2]), "stat_p_exact")
    assert int(mo["table"]["imprecise"].sum()) == 4
    assert "4 imprecise" in mo["summary_text"]


def test_extract_eq_is_not_emptied_by_a_bracket_full_of_numbers() -> None:
    txt = [
        "We found t(20) = 2.1, p = .04.",
        "Amino acid distances were binned as shown on the figure (n = 308, 1,168, 564, 654, 1,492).",
    ]
    eq = extract_eq(txt)
    assert eq["lhs"].tolist() == ["t", "p", "n"]
    assert eq["rhs"].tolist() == ["2.1", ".04", "308"]
    eq = extract_eq("chi2(1, N = 200) = 4.2, F(2, 57) = 3.1, t (n = 30, 28) = 2.0.")
    assert eq["df"].tolist() == ["(1, N = 200)", "(2, 57)", "(n = 30, 28)"]


def test_a_raised_dot_between_two_digits_is_a_decimal_point() -> None:
    x = ["Mortality was lower (p = 0•0079).", "p<0·001 in all groups", "items 2 · 3"]
    assert extract_p_values(x)["p_value"].tolist() == [0.0079, 0.001]
    assert extract_eq(x)["rhs"].tolist() == ["0.0079", "0.001"]
    # also in each paper of a paper list
    pl = pc.PaperList(
        [
            pc.test_paper("The effect, t(20) = 3.1, p = 0·0079."),
            pc.test_paper("Another, t(20) = 2.2, p = 0·039."),
        ]
    )
    assert extract_p_values(pl)["p_value"].tolist() == [0.0079, 0.039]


def test_a_p_that_is_the_subscript_of_another_symbol_is_not_a_p_value() -> None:
    x = [
        "A correlation emerged (r p = 0.51, P < 0.001).",  # Pearson's r
        "Refinement gave R p = 3.83% and R wp = 4.13%.",  # a reliability factor
        "F(2, 54) = 0.77, ²p = 0.02, p = 0.46.",  # partial eta squared, eta lost
        "The contingency is Δp = .33.",  # a contingency
        "Reliability was α s = 0.67, α p = 0.64.",  # Cronbach's alpha
        "Significant: a p < 0.05, b p < 0.01.",  # markers of a note
        "ΔΔ p < 0.01, α p < 0.05.",  # markers of a note
        "Mann-Whitney U p = .199, or p = .03.",
    ]
    pv = extract_p_values(x)
    assert pv["text_id"].tolist() == [1, 3, 6, 6, 7, 7, 8, 8]
    assert pv["text"].tolist() == [
        "P < 0.001",
        "p = 0.46",
        "p < 0.05",
        "p < 0.01",
        "p < 0.01",
        "p < 0.05",
        "p = .199",
        "p = .03",
    ]


def test_a_value_above_1_with_a_unit_a_logarithm_or_in_a_ratio_is_not_a_p_value() -> None:
    x = [
        "The depth was constant for P = 2.9 μm.",
        "A cut-off (-log 10 P > 1.3) was applied.",
        "Intrafibrillar apatite (Ca/P = 1.53) was found.",
        "The pressure was P = 6.02 MPa.",
        "Groups differed (Z = 3.091, P = 3.091).",
    ]
    pv = extract_p_values(x)
    assert pv["text_id"].tolist() == [5]
    assert pv["p_value"].tolist() == [3.091]


def test_p_values_written_with_a_minus_are_found_apart() -> None:
    x = [
        "Trials differed, t(134) = -2.32, p = -.13, d = -0.4.",
        "An effect (β = 0.94; |t| = 5.70; p < -0.001).",
        "It was p = −.05 and p = À0.41 here.",
        "Coordinates were A/P = -2.90 mm.",
        "In p 1-p = -4.42 + 8.47a.",
        "A correlation (r p = -.64, p < .001).",
        "The contingency Δp = -.33.",
        "Not negative, p = .05.",
    ]
    assert not (extract_p_values(x)["p_value"] < 0).any()
    neg = _p_extract(x, negative=True)
    assert neg["text_id"].tolist() == [1, 2, 3, 3]
    assert neg["p_value"].tolist() == [-0.13, -0.001, -0.05, -0.41]


def test_chi_squared_is_read_with_a_space_before_its_2() -> None:
    x = [
        "χ 2 (10) = 9.61, p = .02",
        "χ ² (1) = 3.20, p = .07",
        "X 2 (1) = 3.20, p = .07",
        "χ 2 (1, N = 200) = 4.10, p = .04",
        "Pearson's χ 2 test (χ 2 = 3.9, p = .048)",
        "chi-square(2) = 5.10, p = .08",
    ]
    eq = extract_eq(x)
    eq = eq[eq["lhs"] != "p"]
    assert eq["lhs"].tolist() == ["χ2", "χ²", "X2", "χ2", "χ2", "chi-square"]
    assert eq["rhs"].tolist() == ["9.61", "3.20", "3.20", "4.10", "3.9", "5.10"]
    # a statistic before a p-value, for _p_not_test() and _reports_test()
    import re

    before = [re.sub(r",? p = .*$", "", s) for s in x]
    from metacheck._r.regex import compile_r

    rx = compile_r(_TEST_STAT_PATTERN, perl=True)
    assert all(rx.search(b) for b in before)
