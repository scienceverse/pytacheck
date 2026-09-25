"""Port of the data-quality tests in tests/testthat/test-data-checks.R.

R's ``set.seed()`` + ``sample()`` draws are replaced by deterministic vectors
with the same properties (the same value set, the same contaminants).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from pytacheck.datacheck.checks import (
    data_check_case_issues,
    data_check_colname,
    data_check_colname_collisions,
    data_check_constant,
    data_check_design_name,
    data_check_empty,
    data_check_numeric_in_text,
    data_check_outliers,
    data_check_scale_values,
    data_check_spss_filter,
    data_check_whitespace,
)


def rep(values: list[float], times: int) -> list[float]:
    return [float(v) for v in values] * times


# -- data_check_outliers --------------------------------------------------------------


def test_outliers_flags_tukey_outliers() -> None:
    r = data_check_outliers([*range(1, 11), 500])
    assert r["problem"]
    assert 500 in r["values"]
    assert r["message"].startswith("1 outlier value outside [")
    # no outliers in clean symmetric data
    assert not data_check_outliers(list(range(1, 21)))["problem"]
    # too few / constant -> no problem
    assert not data_check_outliers([1, 2])["problem"]
    assert not data_check_outliers([5] * 20)["problem"]
    # non-numeric -> no problem
    assert not data_check_outliers(["a", "b"])["problem"]


def test_outliers_bounds_and_message() -> None:
    r = data_check_outliers([*range(10, 20), 100, 200, 300, -100], n_max=2)
    assert r["problem"]
    assert r["message"].endswith(", ...")
    assert r["lower"] < 10 < 19 < r["upper"]
    none = data_check_outliers(["a"])
    assert math.isnan(none["lower"]) and math.isnan(none["upper"])


def test_outliers_infinite_quartiles() -> None:
    # U55: both quartiles Inf (IQR = Inf - Inf = NaN) means no spread, like
    # IQR 0 (R fails with "missing value where TRUE/FALSE needed")
    inf = float("inf")
    r = data_check_outliers([1.0, inf, inf, inf])
    assert not r["problem"]
    assert r["values"] is None
    # a lone Inf beyond finite quartiles is still an outlier
    r = data_check_outliers([1.0, 2.0, 3.0, 4.0, 5.0, inf])
    assert r["values"] == [inf]


# -- data_check_scale_values -------------------------------------------------------------


def test_scale_values_flags_and_classifies() -> None:
    # Likert 1-5 with a stray 55 (typo of 5) and -99 (missing code)
    r = data_check_scale_values([*rep([1, 2, 3, 4, 5], 40), 55, -99])
    assert r["problem"]
    assert set(r["values"]) == {-99, 55}
    assert (r["lower"], r["upper"]) == (1, 5)
    cls = dict(zip(r["values"], r["classes"], strict=True))
    assert cls[-99] == "missing"
    assert cls[55] == "typo:5"
    assert r["message"].startswith("2 values outside the 1-5 scale: -99 (looks like a missing")

    # A mistyped 33 on a 1-7 scale is a typo of 3.
    r = data_check_scale_values([*rep(range(1, 8), 30), 33])
    assert r["classes"] == ["typo:3"]
    # A stray 9 on a 1-7 scale is unexplained.
    assert data_check_scale_values([*rep(range(1, 8), 30), 9])["classes"] == ["unexplained"]


def test_scale_values_clean_and_non_scales() -> None:
    assert not data_check_scale_values(rep(range(1, 8), 30))["problem"]
    # a lone adjacent overshoot extends the scale
    assert not data_check_scale_values([*rep(range(1, 6), 40), 6])["problem"]
    # blood pressure, non-integer, age, counts: no fixed range
    assert not data_check_scale_values(rep([118, 121, 125, 130, 112, 140, 99, 135], 25))["problem"]
    assert not data_check_scale_values(rep([1.5, 2.25, 3.75, 4.5], 50))["problem"]
    assert not data_check_scale_values(rep(range(18, 66), 5))["problem"]


def test_scale_values_ground_truth() -> None:
    x = [*rep(range(1, 6), 40), 6]
    assert data_check_scale_values(x, valid_values=[1, 2, 3, 4, 5])["problem"]
    assert not data_check_scale_values(x, valid_values=[1, 2, 3, 4, 5, 6])["problem"]
    assert data_check_scale_values([*rep(range(1, 8), 9), 9], valid_range=[1, 7])["problem"]

    # declared missing codes
    r = data_check_scale_values([*rep(range(1, 6), 40), 77], declared=77)
    assert r["classes"] == ["missing"]
    assert data_check_scale_values([*rep(range(1, 8), 30), -8], declared=-8)["problem"]
    assert not data_check_scale_values(rep(range(1, 6), 40), declared=77)["problem"]

    # endpoint-only valid values become a contiguous range, floor anchored to 1
    r = data_check_scale_values(rep(range(2, 9), 30), valid_values=[2, 8])
    assert not r["problem"]
    assert (r["lower"], r["upper"]) == (1, 8)
    r = data_check_scale_values([*rep(range(2, 9), 30), 9999], valid_values=[2, 8])
    assert r["problem"]
    assert r["values"] == [9999]
    assert (r["lower"], r["upper"]) == (1, 8)
    r = data_check_scale_values(rep(range(2, 9), 30), valid_values=list(range(2, 9)))
    assert not r["problem"]
    assert (r["lower"], r["upper"]) == (1, 8)


def test_scale_values_ground_truth_coverage() -> None:
    # a declared range explaining almost none of the data is discarded
    r = data_check_scale_values([*rep(range(1, 6), 40), -99], valid_values=[50, 60])
    assert (r["lower"], r["upper"]) == (1, 5)
    assert r["values"] == [-99]


def test_scale_values_guards() -> None:
    assert not data_check_scale_values(["a", "b"])["problem"]
    assert not data_check_scale_values([])["problem"]
    assert not data_check_scale_values([None, None])["problem"]
    assert not data_check_scale_values(pd.Series([True, False, True]))["problem"]



def test_scale_values_fractional_range_is_an_interval() -> None:
    # U55: a ground-truth range with fractional bounds is a continuous interval
    # (R builds 1.5:3.5 = {1.5, 2.5, 3.5} and then fails on sprintf("%d"))
    r = data_check_scale_values([*rep([1.5, 2.5, 3.5], 20), 99], valid_range=[1.5, 3.5])
    assert r["values"] == [99]
    assert r["message"] == (
        "1 value outside the 1.5-3.5 scale: 99 (looks like a missing-data code -> recode to NA)"
    )
    # 2.0 and 3.0 lie inside the interval: not flagged
    r = data_check_scale_values([*rep([1.5, 2.0, 3.0, 3.5], 20), 4.0], valid_range=[1.5, 3.5])
    assert r["values"] == [4.0]
    assert (r["lower"], r["upper"]) == (1.5, 3.5)
    # fractional value lists print their bounds as decimals too
    r = data_check_scale_values([*rep([0.5, 1.5, 2.5], 20), 99], valid_values=[0.5, 1.5, 2.5])
    assert r["message"].startswith("1 value outside the 0.5-2.5 scale: 99")
    # whole-number ranges keep their rating-scale levels (3.5 is off-scale)
    r = data_check_scale_values([*rep(range(1, 6), 20), 3.5], valid_range=[1, 5])
    assert r["values"] == [3.5]


# -- constant / empty / design / spss ---------------------------------------------------


def test_constant_flags_constant_and_near_constant() -> None:
    r = data_check_constant([5] * 10)
    assert r["problem"]
    assert not r["near"]
    assert r["message"] == 'Column is constant: every value is "5".'
    r = data_check_constant(["a"] * 99 + ["b"])
    assert r["problem"]
    assert r["near"]
    assert r["message"] == 'Near-constant: 99% of values are "a".'
    assert not data_check_constant(["a"] * 5 + ["b"] * 5)["problem"]
    assert not data_check_constant([None, None])["problem"]


def test_constant_factor_uses_level_order() -> None:
    x = pd.Series(pd.Categorical(["x", "x"], categories=["x", "y"]))
    r = data_check_constant(x)
    # an unused level still counts in table(): one of two levels covers 100%
    assert r["problem"] and r["near"]


def test_empty_flags_all_missing_columns() -> None:
    assert data_check_empty([math.nan] * 5)["problem"]
    assert data_check_empty([None, "", "  "])["problem"]
    assert not data_check_empty([None, 1])["problem"]
    assert not data_check_empty([])["problem"]
    assert data_check_empty([None])["message"] == "Column is empty: all 1 value is missing."


def test_design_name() -> None:
    for nm in ("condition", "exp_cond", "Group", "treatment_arm", "cond1"):
        assert data_check_design_name(nm), nm
    for nm in ("age", "charm", "response"):
        assert not data_check_design_name(nm), nm
    assert data_check_design_name(["arm", "charm"]) == [True, False]


def test_spss_filter() -> None:
    r = data_check_spss_filter("filter_$", [1] * 10)
    assert r["problem"]
    assert "pre-filtered" in r["message"]
    r = data_check_spss_filter("filter_.", [1, 1, 1, 0, 0])
    assert r["problem"]
    assert "3 of 5" in r["message"]
    assert r["values"] == {"selected": 3, "total": 5}
    assert not data_check_spss_filter("excluded", [1, 1, 0])["problem"]
    assert not data_check_spss_filter(None, [1, 1])["problem"]


# -- text checks -------------------------------------------------------------------------


def test_case_issues() -> None:
    r = data_check_case_issues(["Male", "male", "Female"])
    assert r["problem"]
    assert r["values"] == ["Male/male"]
    assert not data_check_case_issues(["Male", "Female"])["problem"]
    assert not data_check_case_issues([1, 2, 3])["problem"]


def test_whitespace() -> None:
    r = data_check_whitespace(["Male ", "Male", "Female"])
    assert r["problem"]
    assert r["message"] == '1 value with leading/trailing whitespace: "Male "'
    assert not data_check_whitespace(["Male", "Female"])["problem"]
    assert not data_check_whitespace([1, 2, 3])["problem"]


def test_numeric_in_text() -> None:
    r = data_check_numeric_in_text([*(str(i) for i in range(1, 21)), "n/a", ">100"])
    assert r["problem"]
    assert "n/a" in r["values"]
    assert r["message"].startswith("Column is 91% numeric but 2 values cannot be parsed")
    assert not data_check_numeric_in_text(["apple", "pear", "kiwi", "plum", "fig"])["problem"]
    assert not data_check_numeric_in_text([str(i) for i in range(1, 21)])["problem"]


# -- column names ------------------------------------------------------------------------


def test_colname_flags_illegal_padded_and_long_names() -> None:
    emergent = "_H:\t$Name\t%Input[4:0,0,0,0]<4:1,8,1,1>\t%Input[4:0,1,0,0]"
    r = data_check_colname(emergent)
    assert r["problem"]
    assert ":" in r["values"]
    assert "\t" in r["values"]
    for nm in ("score:pre", "a\tb", 'he said "hi"', "ratio a/b", " score ", "a" * 65):
        assert data_check_colname(nm)["problem"], nm
    for nm in ("a" * 64, "reaction_time", "orginal hip.go value", "âge"):
        assert not data_check_colname(nm)["problem"], nm
    assert not data_check_colname([None])["problem"]
    assert not data_check_colname(None)["problem"]


def test_colname_collisions() -> None:
    r = data_check_colname_collisions(["t'", "t\u032a", "score", "kw"])
    assert set(r) == {"t'", "t\u032a"}
    assert "cannot tell these columns apart" in r["t'"]
    d = data_check_colname_collisions(["id", "id", "x"])
    assert "id" in d
    assert "identically named column" in d["id"]
    assert data_check_colname_collisions(["a", "b", "a_1"]) == {}
    assert data_check_colname_collisions(["k", "k\u02b7"]) == {}
    # U56: blank names collide like any other name (R's out[[""]] entries were
    # unreachable by name, so a blank column's collision was never reported)
    b = data_check_colname_collisions(["", "", "x"])
    assert list(b) == [""]
    assert "1 other identically named column" in b[""]
