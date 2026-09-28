"""pytacheck._values: missing values, truth tests, scalar coercion and field access."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

from pytacheck._values import as_float, as_int, as_str, field, is_missing, is_true

NA = None  # R's NA in the expectations below
NAN = math.nan
INF = math.inf


# -- is_missing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "x",
    [
        None,
        pd.NA,
        pd.NaT,
        math.nan,
        np.float32("nan"),
        np.float64("nan"),
        np.datetime64("NaT"),
        np.timedelta64("NaT", "s"),
        pd.Series([None], dtype="Float64").iloc[0],
        pd.Series([None], dtype="string").iloc[0],
        pd.Series([None], dtype="boolean").iloc[0],
        pd.to_datetime(pd.Series([None])).iloc[0],
    ],
)
def test_is_missing(x: object) -> None:
    assert is_missing(x) is True


@pytest.mark.parametrize(
    "x",
    [
        0,
        0.0,
        -math.inf,
        "",
        "NA",
        "NaN",
        False,
        np.int64(0),
        np.float32(1.5),
        np.bool_(False),
        np.datetime64("2020-01-01"),
        pd.Timestamp("2020-01-01"),
        dt.date(2020, 1, 1),
        [],
        [None],
        {},
        (math.nan,),
        np.array([math.nan]),
        np.array([]),
        pd.Series([None]),
        pd.DataFrame(),
        b"",
    ],
)
def test_is_not_missing(x: object) -> None:
    assert is_missing(x) is False


# -- is_true -------------------------------------------------------------------


@pytest.mark.parametrize(
    "x",
    [
        True,
        np.bool_(True),
        np.array([True]),
        pd.Series([True]),
        pd.Series([True], dtype="boolean"),
        pd.Index([True]),
    ],
)
def test_is_true(x: object) -> None:
    assert is_true(x) is True


@pytest.mark.parametrize(
    "x",
    [
        False,
        np.bool_(False),
        None,
        pd.NA,
        math.nan,
        1,
        1.0,
        np.int64(1),
        "TRUE",
        "true",
        [True],  # an R list, not a logical vector
        (True,),
        {"a": True},
        np.array([True, True]),
        np.array([], dtype=bool),
        pd.Series([True, False]),
        pd.Series([None], dtype="boolean"),
        pd.Series([1]),
    ],
)
def test_is_not_true(x: object) -> None:
    assert is_true(x) is False


# -- as_float --------------------------------------------------------------------

# R 4.5.3: suppressWarnings(as.numeric(x)) for each string (NA and NaN apart)
R_AS_NUMERIC = [
    ("1", 1.0),
    (" 1 ", 1.0),
    ("\t1.5\n", 1.5),
    ("\v1\f", 1.0),
    ("\r1\r", 1.0),
    ("+1", 1.0),
    ("-1", -1.0),
    ("+.5", 0.5),
    (".5", 0.5),
    ("5.", 5.0),
    ("00012", 12.0),
    ("0.1", 0.1),
    ("1e5", 1e5),
    ("1E-5", 1e-5),
    ("1.e5", 1e5),
    ("1e400", INF),
    ("-1e400", -INF),
    ("1e-400", 0.0),
    ("123456789012345678901234567890", 1.2345678901234568e29),
    ("0x1A", 26.0),
    ("0X1a", 26.0),
    ("-0x10", -16.0),
    ("0x1p3", 8.0),
    ("0x1.8p1", 3.0),
    ("0x.8", 0.5),
    ("0x1P-2", 0.25),
    ("0x1p1024", INF),
    ("NaN", NAN),
    ("nan", NAN),
    ("-nan", NAN),
    ("+NaN", NAN),
    ("Inf", INF),
    ("inf", INF),
    ("-Inf", -INF),
    ("+inf", INF),
    ("INFINITY", INF),
    ("-Infinity", -INF),
    (".", NA),
    ("", NA),
    ("  ", NA),
    ("NA", NA),
    ("na", NA),
    ("infinit", NA),
    ("infx", NA),
    ("1e", NA),
    ("1e+", NA),
    ("e5", NA),
    ("0x", NA),
    ("0x1p", NA),
    ("0xg", NA),
    ("1,5", NA),
    ("1_000", NA),
    ("1 2", NA),
    ("1L", NA),
    ("1d5", NA),
    ("--1", NA),
    ("+-1", NA),
    ("1.2.3", NA),
    ("TRUE", NA),
    ("T", NA),
    (" 12", NA),  # no-break space
    ("12 ", NA),
    ("١٢", NA),  # Arabic-Indic digits
    ("１", NA),  # fullwidth digit
]


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return (math.isnan(a) and math.isnan(b)) or a == b


@pytest.mark.parametrize(("text", "expected"), R_AS_NUMERIC)
def test_as_float_reads_strings_like_r(text: str, expected: float | None) -> None:
    assert _same(as_float(text), expected)
    assert _same(as_float(np.str_(text)), expected)


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (True, 1.0),
        (False, 0.0),
        (np.bool_(True), 1.0),
        (3, 3.0),
        (np.int32(-7), -7.0),
        (2.5, 2.5),
        (np.float32(0.5), 0.5),
        (math.nan, NAN),  # a NaN number stays NaN
        (-math.inf, -INF),
        (Decimal("1.25"), 1.25),
        (Fraction(1, 4), 0.25),
        (10**400, INF),
        (-(10**400), -INF),
        (None, NA),
        (pd.NA, NA),
        (pd.NaT, NA),
        (Decimal("sNaN"), NA),
        ([1], NA),
        ({"a": 1}, NA),
        (np.array([1.0]), NA),
        (b"1", NA),
        (1 + 2j, NA),
        (pd.Timestamp("2020-01-01"), NA),
    ],
)
def test_as_float_of_other_values(x: object, expected: float | None) -> None:
    value = as_float(x)
    assert _same(value, expected)
    assert value is None or type(value) is float


# -- as_int ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        # R 4.5.3: as.integer(x)
        ("1.9", 1),
        ("-1.9", -1),
        ("2147483647", 2147483647),
        (" 7 ", 7),
        ("0x1A", 26),
        ("1.5e0", 1),
        ("NaN", NA),
        ("Inf", NA),
        ("abc", NA),
        # beyond R's 32-bit integers: the value (R gives NA)
        ("2147483648", 2147483648),
        ("1e10", 10_000_000_000),
        ("123456789012345678901234567890", 123456789012345678901234567890),
        ("+007", 7),
        (1.9, 1),
        (-1.9, -1),
        (True, 1),
        (np.bool_(False), 0),
        (np.int64(5), 5),
        (10**30, 10**30),
        (math.nan, NA),
        (math.inf, NA),
        (None, NA),
        (pd.NA, NA),
        ("", NA),
        ([1], NA),
    ],
)
def test_as_int(x: object, expected: int | None) -> None:
    value = as_int(x)
    assert value == expected
    assert value is None or type(value) is int


# -- as_str ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        # R 4.5.3: as.character(x)
        (0.1 + 0.2, "0.3"),
        (1 / 3, "0.333333333333333"),
        (1e5, "1e+05"),
        (123456.7, "123456.7"),
        (1e15, "1e+15"),
        (1e-5, "1e-05"),
        (100.0, "100"),
        (2.0**53, "9007199254740992"),
        (-0.5, "-0.5"),
        (math.inf, "Inf"),
        (-math.inf, "-Inf"),
        (1e-20, "1e-20"),
        (123456789012345.0, "123456789012345"),
        (0.1234567890123456, "0.123456789012346"),
        (True, "TRUE"),
        (False, "FALSE"),
        (1, "1"),
        (-5, "-5"),
        (np.int64(5), "5"),
        (np.bool_(True), "TRUE"),
        (np.float64(0.25), "0.25"),
        ("text", "text"),
        ("", ""),
        ("NA", "NA"),
        (np.str_("x"), "x"),
        (b"caf\xc3\xa9", "café"),
        (b"\xff", "�"),
        (None, NA),
        (pd.NA, NA),
        (math.nan, NA),
        (pd.NaT, NA),
        ([1], NA),
        ({"a": 1}, NA),
        (np.array(["a"]), NA),
        (pd.Series(["a"]), NA),
    ],
)
def test_as_str(x: object, expected: str | None) -> None:
    value = as_str(x)
    assert value == expected
    assert value is None or type(value) is str


# -- field -----------------------------------------------------------------------

RECORD = {
    "attributes": {"title": "A title", "tags": ["a", "b"], "empty": None, "zero": 0},
    "authors": [{"name": "Ann"}, {"name": None}],
    "flag": False,
}


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (("attributes", "title"), "A title"),
        (("attributes", "tags", 0), "a"),
        (("attributes", "tags", -1), "b"),
        (("authors", 0, "name"), "Ann"),
        (("attributes", "zero"), 0),
        (("flag",), False),
        ((), RECORD),
    ],
)
def test_field_finds_values(keys: tuple[str | int, ...], expected: object) -> None:
    assert field(RECORD, *keys) == expected
    assert field(RECORD, *keys, default="d") == expected


@pytest.mark.parametrize(
    "keys",
    [
        ("missing",),
        ("attributes", "titl"),  # no partial matching
        ("Attributes",),  # keys match exactly
        ("attributes", "empty"),  # JSON null
        ("authors", 1, "name"),
        ("authors", 2, "name"),  # index out of range
        ("authors", -3),
        ("authors", "0"),  # a list takes integer indexes
        ("authors", True),
        ("attributes", 0),  # a mapping takes names
        ("attributes", "title", "x"),  # a string has no fields
        ("attributes", "title", 0),
        ("attributes", "zero", "x"),
        ("flag", "x"),
    ],
)
def test_field_default_when_a_step_is_missing(keys: tuple[str | int, ...]) -> None:
    assert field(RECORD, *keys) is None
    assert field(RECORD, *keys, default="d") == "d"


def test_field_of_missing_or_scalar_objects() -> None:
    assert field(None, "a") is None
    assert field(None) is None
    assert field(None, default=[]) == []
    assert field("text", "a", default=0) == 0
    assert field([1, 2], 1) == 2
    assert field((1, 2), 0) == 1
    assert field({1: "int key"}, 1) == "int key"


# -- edge values found in review ----------------------------------------------------


@pytest.mark.parametrize("x", [complex("nan"), np.complex128(complex("nan")), Decimal("NaN")])
def test_nan_of_other_numeric_types_is_missing(x: complex | Decimal) -> None:
    assert is_missing(x) is True
    assert pd.isna(x)  # type: ignore[arg-type]  # the same answer as pandas


def test_signalling_decimal_nan_is_missing() -> None:
    assert is_missing(Decimal("sNaN")) is True  # pd.isna() raises InvalidOperation


def test_is_true_of_a_pandas_array() -> None:
    assert is_true(pd.array([True], dtype="boolean")) is True
    assert is_true(pd.array([pd.NA], dtype="boolean")) is False
    assert is_true(pd.array([True, True], dtype="boolean")) is False


@pytest.mark.parametrize(
    ("text", "r_value"),
    # R 4.5.3 reads a hexadecimal number without digits as 0; these are not numbers
    [("0x.", 0.0), ("0x.p1", 0.0), ("0xp1", 0.0), ("-0x.", 0.0)],
)
def test_as_float_needs_a_digit_in_a_hexadecimal_number(text: str, r_value: float) -> None:
    assert as_float(text) is None
    assert as_int(text) is None


def test_as_float_is_correctly_rounded() -> None:
    # R 4.5.3's long-double reading gives Inf and 0 for these
    assert as_float("1.7976931348623158e308") == 1.7976931348623157e308
    assert as_float("0x1p-1074") == 5e-324


def test_as_float_of_huge_fractions_is_infinite() -> None:
    assert as_float(Fraction(10**400, 3)) == INF
    assert as_float(Fraction(-(10**400), 3)) == -INF


def test_as_int_of_integer_strings_beyond_python_digit_limit() -> None:
    assert as_int("1" * 4300) == int("1" * 4300)
    # longer digit strings are beyond the double range as numbers: NA, never an error
    assert as_int("1" * 5000) is None
    assert as_int("-" + "1" * 5000) is None
    assert as_float("1" * 5000) == INF


@pytest.mark.parametrize(
    "x",
    [
        pd.DataFrame({"a": [1]}),
        pd.array([1]),
        pd.Categorical(["a"]),
        range(3),
        {"a": 1}.keys(),
        {"a": 1}.values(),
        (i for i in range(2)),
        iter([1]),
        np.array(1.5),
    ],
)
def test_as_str_of_containers_and_iterators_is_missing(x: object) -> None:
    assert as_str(x) is None


def test_as_str_of_a_memoryview_decodes_it() -> None:
    assert as_str(memoryview(b"caf\xc3\xa9")) == "café"


def test_field_takes_numpy_integer_indexes() -> None:
    assert field(RECORD, "authors", np.int64(0), "name") == "Ann"
    assert field(RECORD, "attributes", "tags", np.int32(-1)) == "b"
    assert field(RECORD, "authors", np.int64(5), default="d") == "d"
    assert field(RECORD, "authors", np.bool_(True), default="d") == "d"  # type: ignore[arg-type]
