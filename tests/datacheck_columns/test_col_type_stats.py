"""Column typing / statistics tests.

Ports of upstream tests/testthat/test-data-checks.R ("data_col_type applies the
rule ladder", "data_col_stats returns numeric summaries") and
test-data-read.R ("data_col_type survives an invalid-UTF-8 column name").
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from pytacheck.datacheck.columns import _scale_typo_of, data_col_stats, data_col_type


def test_data_col_type_applies_the_rule_ladder() -> None:
    assert data_col_type("subject_id", ["s01", "s02", "s03"])["col_type"] == "id"
    assert data_col_type("x", [1.0] * 10)["col_type"] == "constant"
    assert data_col_type("cond", ["a", "b", "a", "b"])["col_type"] == "binary"
    assert data_col_type("age", [23.5, 45.1, 31.9, 29.2, 55.7])["col_type"] == "continuous"
    # empty
    assert data_col_type("e", [None, None])["col_type"] == "empty"


def test_data_col_type_survives_an_invalid_utf8_column_name() -> None:
    bad = b"\xef" + b"PeronalData_fullname"
    res = data_col_type(bad, list(range(1, 11)))
    assert isinstance(res, dict)
    # surrogate-escaped (undecodable) str names are repaired the same way
    res2 = data_col_type(bad.decode("utf-8", "surrogateescape"), list(range(1, 11)))
    assert res2["col_type"] == res["col_type"]


def test_data_col_type_ambiguous_integer_and_comma_decimal() -> None:
    res = data_col_type("rating", [1.0, 2.0, 3.0, 4.0, 5.0, 3.0, None])
    assert res["col_type"] is None
    assert res["ambiguous"] is True
    assert res["is_numeric"] is True
    res = data_col_type("price", ["1,50", "2,30", "4,10", "5,00", "3,25", "6,60", "2,10", "1,90"])
    assert res["col_type"] == "continuous_comma_decimal"
    assert res["numeric_values"][:2] == [1.5, 2.3]
    assert res["n_coerced"] == 0


def test_data_col_type_accepts_pandas_and_numpy_columns() -> None:
    assert (
        data_col_type("age", pd.Series([23.5, 45.1, np.nan, 29.2, 55.7]))["col_type"]
        == "continuous"
    )
    assert data_col_type("n", np.arange(1, 30))["col_type"] == "continuous"
    fac = pd.Series(pd.Categorical(["lo", "hi", "mid", "lo"]))
    assert data_col_type("level", fac)["col_type"] is None  # a factor is not numeric
    assert (
        data_col_type("flag", pd.array([True, False, None, True], dtype="boolean"))["col_type"]
        == "binary"
    )


def test_data_col_stats_returns_numeric_summaries() -> None:
    s = data_col_stats([1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["n"].iloc[0] == 5
    assert s["mean"].iloc[0] == 3
    assert s["min"].iloc[0] == 1
    assert s["max"].iloc[0] == 5
    # NULL numeric values -> empty stats but n/n_missing from raw
    s2 = data_col_stats(None, ["a", "b", None])
    assert math.isnan(s2["mean"].iloc[0])
    assert s2["n_missing"].iloc[0] == 1


def test_data_col_stats_matches_r_estimators() -> None:
    x = [7.0, 1.0, 3.0, 5.0, 9.0, 11.0, 2.0, 8.0]
    s = data_col_stats(x, x).iloc[0]
    # R: quantile(x, c(.25, .75)) (type 7) and sd() with n - 1
    assert s["p25"] == pytest.approx(2.75)
    assert s["p75"] == pytest.approx(8.25)
    assert s["iqr"] == pytest.approx(5.5)
    assert s["median"] == pytest.approx(6.0)
    assert s["sd"] == pytest.approx(3.575711717366808, rel=1e-12)
    assert s["se"] == pytest.approx(3.575711717366808 / math.sqrt(8), rel=1e-12)
    assert list(data_col_stats(x, x).columns) == [
        "n", "n_missing", "n_unique", "mean", "sd", "se", "median", "min", "max",
        "range", "p25", "p75", "iqr", "skewness", "kurtosis",
    ]  # fmt: skip


def test_data_col_stats_nested_column_is_unsummarised() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    s = data_col_stats(None, df).iloc[0]
    assert s["n"] == 3
    assert s["n_missing"] == 0
    assert pd.isna(s["n_unique"])
    assert pd.isna(s["mean"])


@pytest.mark.parametrize(
    ("v", "lo", "hi", "want"),
    [(33, 1, 7, 3.0), (25, 1, 7, 5.0), (-3, 1, 7, 3.0), (4, 1, 7, None), (float("nan"), 1, 7, None),
     (77, 1, 5, None), (1e5, 1, 7, 1.0)],
)  # fmt: skip
def test_scale_typo_of(v: float, lo: float, hi: float, want: float | None) -> None:
    assert _scale_typo_of(v, lo, hi) == want
