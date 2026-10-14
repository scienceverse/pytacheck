"""The integer shortcuts in ``as.character()`` / ``as.numeric()`` of a column."""

from __future__ import annotations

import numpy as np
import pandas as pd

from metacheck.datacheck._kinds import as_text, integer_numbers


def test_integer_column_prints_its_digits() -> None:
    assert as_text(pd.Series([1, -20, 300])) == ["1", "-20", "300"]
    assert as_text(pd.Series([1, None, 3], dtype="Int64")) == ["1", None, "3"]
    assert as_text(pd.Series([7, None, 9], dtype=object)) == ["7", None, "9"]


def test_integer_beyond_r_range_prints_as_a_double() -> None:
    assert as_text(pd.Series([3_000_000_000, 1])) == ["3e+09", "1"]
    assert integer_numbers(pd.Series([3_000_000_000, 1])) is None


def test_integer_numbers() -> None:
    assert integer_numbers(pd.Series([1, None, 3], dtype="Int64")) == [1.0, None, 3.0]
    assert integer_numbers(pd.Series([4, 5], dtype=object)) == [4.0, 5.0]
    # anything else goes through the text
    assert integer_numbers(pd.Series([1.5, 2.0])) is None
    assert integer_numbers(pd.Series([True, False])) is None
    assert integer_numbers(pd.Series(["1", "2"])) is None
    assert integer_numbers(pd.Series([1, 2], dtype="category")) is None
    assert integer_numbers(pd.Series(np.array([], dtype="int64"))) == []
