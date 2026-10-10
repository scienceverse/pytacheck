"""Regression tests for divergences from R found in the review of the datacheck_checks port.

Every expected value below was checked against R 4.5.3 + metacheck (the
matching parity cases live in ``parity/cases/datacheck_checks_review.yaml``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from metacheck.datacheck import checks as C
from metacheck.datacheck._checks_facets import _strptime_ok
from metacheck.datacheck._checks_format import _utf8_bom_connection
from metacheck.datacheck._kinds import (
    as_numbers,
    as_text,
    is_integer,
    kind,
    row_texts,
    trimmed_counts,
)

DATA = Path(__file__).parent / "data"


# -- strptime(): R's get_number() reads up to the field width, with no early stop ------------


@pytest.mark.parametrize(
    ("value", "fmt", "ok"),
    [
        ("2020-05-45", "%Y-%m-%d", False),  # glibc would read day 4 and ignore "5"
        ("2020-05-4", "%Y-%m-%d", True),
        ("2020-05-19 10:65", "%Y-%m-%d %H:%M", False),
        ("2020-05-19 10:00:75", "%Y-%m-%d %H:%M:%S", False),
        ("2020-05-19 10:00:60", "%Y-%m-%d %H:%M:%S", True),
        ("2020-05-19 10:00:61", "%Y-%m-%d %H:%M:%S", False),
        ("2022_Sep_08_523", "%Y_%b_%d_%H%M", False),  # hour 52
        ("2022_Sep_08_052", "%Y_%b_%d_%H%M", True),
        ("2022_Sep_08_1523", "%Y_%b_%d_%H%M", True),
        ("2020-05-19_16h65", "%Y-%m-%d_%Hh%M", False),
        ("5/4/205", "%m/%d/%y", True),  # %y takes two digits, the rest is ignored
        ("2020-1-011", "%Y-%m-%d", True),
        ("12345-01-01", "%Y-%m-%d", False),
    ],
)
def test_strptime_digit_fields_take_their_full_width(value: str, fmt: str, ok: bool) -> None:
    assert _strptime_ok(value, fmt) is ok


def test_concepts_follow_the_strptime_fix() -> None:
    days = ["2020-05-45", "2020-05-46", "2020-05-47", "2020-05-19"]
    assert C._parse_frac(days, C._DATE_FMTS) == 0.25
    assert C.data_col_concept("test_date", days) is None
    stamps = ["2020-05-19 10:65", "2020-05-19 11:75", "2020-05-19 12:95"]
    assert C.data_col_concept("timestamp", stamps) is None


# -- the string "NaN" is a value like any other (R's table() drops it; D77) ---------------------


def test_constant_counts_nan_strings() -> None:
    res = C.data_check_constant(["a", "a", "NaN"])
    assert res["problem"] is False
    assert C.data_check_constant(["NaN", "NaN"])["values"] == "NaN"
    fct = C.data_check_constant(pd.Series(pd.Categorical(["NaN", "NaN", "a"])))
    assert fct["problem"] is False
    assert C.data_check_constant(["NA", "NA", "NA", "b"])["problem"] is False


def test_constant_ties_and_label_collisions() -> None:
    near = C.data_check_constant([2.0, 1.0, 1.0, 2.0], threshold=0.5)
    assert near["values"] == "1"  # the first tied level in sorted order
    assert C.data_check_constant([0.1 + 0.2, 0.3, 0.3])["values"] == "0.3"  # one level
    assert C.data_check_constant(["b", "B", "a", "A"], threshold=0.2)["values"] == "a"
    assert C.data_check_constant([True, False], threshold=0.5)["values"] == "FALSE"


# -- .scale_typo_of(): a whole number's digits are its decimal digits -------------------------


def test_scale_values_integer_typo_path() -> None:
    base = list(range(2, 11)) * 3
    as_int = C.data_check_scale_values(
        pd.Series([*base, 100000], dtype="Int64"), valid_range=[2, 10]
    )
    assert as_int["classes"] == ["unexplained"]
    assert as_int["values"] == [100000]
    # U59: a double gets the same digits (R read as.character(1e5) = "1e+05"
    # and called 100000 a typo of 5)
    as_dbl = C.data_check_scale_values([float(v) for v in [*base, 100000]], valid_range=[2, 10])
    assert as_dbl["classes"] == ["unexplained"]


# -- as.character(<POSIXct>) keeps fractional seconds -----------------------------------------


def test_posixct_as_character() -> None:
    secs = [1577872800.000001, 1577872800 + 4e-7, 1577836800.5, 1577836800, 1577872809.25]
    got = as_text(pd.Series(pd.to_datetime(secs, unit="s")))
    assert got == [
        "2020-01-01 10:00:00.000001",
        "2020-01-01 10:00:00",
        "2020-01-01 00:00:00.5",
        "2020-01-01",
        "2020-01-01 10:00:09.25",
    ]
    res = C.data_check_constant(pd.Series(pd.to_datetime([1577872809.25] * 3, unit="s")))
    assert res["values"] == "2020-01-01 10:00:09.25"


# -- scalar-name error paths -------------------------------------------------------------------


def test_scalar_name_error_paths() -> None:
    with pytest.raises(ValueError, match="length = 2"):
        C.data_check_pii_name(["email", "phone"])
    with pytest.raises(ValueError):
        C.data_check_pii_name([])
    with pytest.raises(IndexError):
        C.data_check_pii_geo([], [1.0, 2.0, 3.0])
    with pytest.raises(IndexError):
        C._pii_split_name([])
    with pytest.raises(ValueError, match="length > 1"):
        C._concept_is_rt(["rt", "x"], [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="length zero"):
        C._concept_is_date([], ["2020-01-01"])
    assert C._concept_is_rt(None, [1.0]) is False
    assert C._concept_is_condition(["cond", "x", "Group"], [1]) == [True, False, True]
    assert C._concept_is_condition([], [1]) == []
    assert C.data_check_pii_geo(["lat", "x"], [52.1, 4.3, 51.9])["problem"] is True


# -- header rows ---------------------------------------------------------------------------------


def test_detect_header_row_returns_the_rows_as_given() -> None:
    rows = [
        [1.0, None, None, None],
        ["id", "a", "b", "c"],
        [1.0, 2.0, 3.0, 4.0],
        [5.0, 6.0, 7.0, 8.0],
    ]
    det = C._detect_header_row(rows)
    assert det["header_row"] == 1
    assert det["stripped"] == [[1.0, None, None, None]]


def test_row_scan_types_object_columns_from_all_values() -> None:
    col = pd.Series([None, None, None, None, None, "z"], dtype=object)
    two = pd.DataFrame({"a": col, "b": pd.Series([1, None, 3, 4, 5, 6], dtype="Int64")})
    # a text NA stays NA in a row; any other NA is the string "NA"
    assert row_texts(two, 2) == [[None, "1"], [None, "NA"]]
    assert row_texts(two.iloc[:, :1], 1) == [[None]]
    df = pd.DataFrame(
        {
            "...1": ["Participant", "1", "2", "3", "4", "5"],
            "...2": ["Score", "3", "4", "5", "6", "7"],
            "...3": col,
        }
    )
    res = C.data_promote_header_row(df)
    assert res["promoted"] == 1
    assert list(res["df"].columns) == ["Participant", "Score", "V3"]
    strip = C.data_strip_qualtrics_header(
        pd.DataFrame(
            {
                "StartDate": ["Start Date", "1", "2"],
                "EndDate": ["End Date", "3", "4"],
                "Q1": [None, None, "x"],
            }
        )
    )
    assert len(strip) == 2


# -- read.csv(fileEncoding = "UTF-8-BOM") header sniff ----------------------------------------


def test_utf8_bom_connection() -> None:
    assert _utf8_bom_connection(b"\xef\xbb\xbf\na,b\n", complete=True) == b"\na,b\n"
    assert _utf8_bom_connection(b"a,tr\xefal,b\n1,2,3\n", complete=True) == b"a,tr"
    assert _utf8_bom_connection(b"a,b\n1,2\n3,", complete=False) == b"a,b\n1,2\n"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("jspsych_bom_blank.csv", True),
        ("behaverse_latin1.csv", False),
        ("jspsych_latin1_body.csv", True),
    ],
)
def test_trial_level_sniff_encoding(name: str, expected: bool) -> None:
    assert C._bh_is_trial_level_file(str(DATA / name)) is expected


def test_trial_level_sniff_accepts_a_single_path_vector() -> None:
    assert C._bh_is_trial_level_file([str(DATA / "jspsych.csv")]) is True
    assert C._bh_is_trial_level_file([str(DATA / "jspsych.csv")] * 2) is False


# -- vectorised helpers ------------------------------------------------------------------------


def test_column_kinds() -> None:
    assert kind(pd.Series([1, None, 3], dtype="Int64")) == "numeric"
    assert kind([True, None]) == "logical"
    assert kind([None, None]) == "logical"  # all missing, as in R
    assert kind(pd.Series(["a", None], dtype="string")) == "text"
    assert kind([1, "a"]) == "text"
    assert kind(pd.Series(["a"], dtype="category")) == "categorical"
    assert kind(pd.to_datetime(pd.Series(["2020-01-01"]))) == "datetime"
    assert is_integer([1, None]) and not is_integer([1.5])


def test_text_and_numbers() -> None:
    assert as_text([1.0, None, 0.1 + 0.2, 1e5, True]) == ["1", None, "0.3", "1e+05", "TRUE"]
    assert as_text(pd.Series([3, 3 * 10**9], dtype="Int64")) == ["3", "3e+09"]  # a double in R
    a = as_numbers(pd.Series(["1", " 2 ", "x", None], dtype="string"))
    assert a[:2].tolist() == [1.0, 2.0] and np.isnan(a[2:]).all()
    assert as_numbers(pd.Series(["b", "a", None], dtype="category"))[:2].tolist() == [2.0, 1.0]
    assert trimmed_counts([" a", "a ", "", "  ", None, "b"]) == {"a": 2, "b": 1}


# -- a column's R class by position (F30 / F32) ------------------------------------------------


def test_facets_use_the_given_class_for_a_repeated_column_name(tmp_path: Path) -> None:
    """``d,d`` read by fread: an IDate column and an integer column sharing a name.

    The Series still carries its frame's ``col_attrs``, where the name finds the
    first column's class; the class passed by position must win all the way down
    to ``data_col_type()`` (R: the second ``d`` is numeric).
    """
    from metacheck.datacheck._colattrs import col_attrs_at
    from metacheck.datacheck.files import data_read_head

    p = tmp_path / "dup.csv"
    p.write_text("d,d\n" + "".join(f"2020-01-{i:02d},{i}\n" for i in range(1, 25)))
    df = data_read_head(str(p), n_rows=float("inf"))
    facets = [
        C.data_col_facets("d", df.iloc[:, j], col_class=col_attrs_at(df, j).get("class", []))
        for j in range(2)
    ]
    assert [f["representation"] for f in facets] == ["datetime", "numeric"]
