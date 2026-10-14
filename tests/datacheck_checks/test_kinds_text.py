"""``as.character()`` of a column: the whole-column paths agree with the value-by-value one."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from metacheck._r.base import format_num
from metacheck.datacheck._kinds import _r_double, _text, as_text, as_text_head, row_texts

DOUBLES = [
    0.0,
    -0.0,
    1.0,
    -2.5,
    0.1 + 0.2,
    1 / 3,
    1e5,
    123456.0,
    1e15,
    1e16,
    123456789012345.0,
    1234567890123456.0,
    2.0**53,
    0.0001,
    0.00011,
    1e-5,
    2.5e-7,
    -1e-300,
    5e-324,
    1.7976931348623157e308,
    math.inf,
    -math.inf,
]


@pytest.mark.parametrize("v", DOUBLES)
def test_a_double_prints_as_r_prints_it(v: float) -> None:
    assert _r_double(v) == _text(v)
    if not math.isinf(v):
        assert _r_double(v) == format_num(v, 15)


def test_double_column() -> None:
    assert as_text(pd.Series([1.0, np.nan, 1e5, 0.1])) == ["1", None, "1e+05", "0.1"]
    assert as_text(pd.Series([1.5, None], dtype="Float64")) == ["1.5", None]


def test_logical_column() -> None:
    assert as_text(pd.Series([True, False])) == ["TRUE", "FALSE"]
    assert as_text(pd.Series([True, None], dtype="boolean")) == ["TRUE", None]


def test_date_time_column() -> None:
    col = pd.Series(pd.to_datetime(["2020-01-01", "2020-01-01 12:30:01", None], format="ISO8601"))
    assert as_text(col) == ["2020-01-01", "2020-01-01 12:30:01", None]
    local = col.dt.tz_localize("Europe/Amsterdam")
    assert as_text(local) == ["2020-01-01", "2020-01-01 12:30:01", None]
    # fractions of a second take the value-by-value path
    frac = pd.Series(pd.to_datetime(["2020-01-01 00:00:00.5", None], format="ISO8601"))
    assert as_text(frac) == ["2020-01-01 00:00:00.5", None]


def test_missing_date_time_in_a_row() -> None:
    df = pd.DataFrame({"a": [1, 2], "t": pd.to_datetime(["1970-01-02", None])})
    assert row_texts(df) == [["1", "86400"], ["2", "NA"]]


@pytest.mark.parametrize(
    "col",
    [
        pd.Series([None, 4, None, 5, 6], dtype="Int64"),
        pd.Series([3_000_000_000, None, 1], dtype="Int64"),
        pd.Series([np.nan, 0.5, 1e5, np.inf]),
        pd.Series([None, "", "a", "b"], dtype="string"),
        pd.Series(["x", None, "y"], dtype="category"),
        pd.Series(pd.to_datetime([None, "2020-01-01"])),
        pd.Series([None, True, False], dtype="boolean"),
        pd.Series([1, "x", None, 2.5], dtype=object),
    ],
)
@pytest.mark.parametrize("k", [1, 2, 5])
def test_head_of_the_text(col: pd.Series, k: int) -> None:
    assert as_text_head(col, k) == [s for s in as_text(col) if s is not None][:k]
