"""Port of tests/testthat/test-text-json_expand.R, plus R-semantics unit tests."""

from __future__ import annotations

import math
from collections.abc import Iterator

import pandas as pd
import pytest

from pytacheck.text.json_expand import (
    _deparse,
    _parse_json,
    _simplify,
    as_numeric,
    json_expand,
    type_convert,
)


def _vals(s: pd.Series) -> list:
    return [None if pd.isna(v) else v for v in s.tolist()]


ANSWERS = [
    '{"number": 1, "letter": "A", "bool": true}',
    '{"number": 2, "letter": "B", "bool": "FALSE"}',
    '{"number": 3, "letter": "", "bool": null}',
    "oh no, the LLM misunderstood",
    '{"number": 5, "letter": ["E", "F"], "bool": false}',
]


def test_json_expand() -> None:
    table = pd.DataFrame({"paper_id": range(1, 6), "answer": ANSWERS})
    expanded = json_expand(table)
    expanded2 = json_expand(table["answer"])
    pd.testing.assert_frame_equal(
        expanded.iloc[:, 2:6].reset_index(drop=True), expanded2.iloc[:, 1:5].reset_index(drop=True)
    )
    assert expanded.columns[1] == "answer"
    assert expanded2.columns[0] == "json"

    assert list(expanded.columns) == ["paper_id", "answer", "number", "letter", "bool", "error"]
    assert str(expanded["number"].dtype) == "Int64"
    assert str(expanded["letter"].dtype) == "string"
    assert str(expanded["bool"].dtype) == "boolean"
    assert _vals(expanded["letter"]) == ["A", "B", "", None, "E;F"]
    assert _vals(expanded["bool"]) == [True, False, None, None, False]


def test_json_expand_nulls() -> None:
    table = pd.DataFrame(
        {
            "id": range(1, 10),
            "answer": [
                '{"number": 1, "letter": "A", "bool": true}',
                "{null}",
                "[]",
                "[{}]",
                "{}",
                "null",
                '""',
                '"Hi"',
                "",
            ],
        }
    )
    err = _vals(json_expand(table)["error"])
    assert [err[i] for i in (0, 2, 3, 4, 5)] == [None] * 5
    assert err[6:8] == ["not a list", "not a list"]
    assert [err[1], err[8]] == ["parsing error", "parsing error"]


def test_json_expand_name_conflict() -> None:
    table = pd.DataFrame({"id": range(1, 6), "number": range(1, 6), "answer": ANSWERS})
    assert {"number", "number.json"} <= set(json_expand(table).columns)
    expanded = json_expand(table, suffix=("_orig", "_x"))
    assert {"number_orig", "number_x"} <= set(expanded.columns)


def test_json_expand_multiline() -> None:
    table = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "answer": [
                """[
        {"number": 1, "letter": "A", "bool": true},
        {"number": 2, "letter": "B", "bool": null}
      ]""",
                '[{"number": 3, "letter": "C", "bool": false}]',
                "[]",
            ],
        }
    )
    expanded = json_expand(table)
    assert _vals(expanded["number"]) == [1, 2, 3, None]
    assert _vals(expanded["letter"]) == ["A", "B", "C", None]
    assert _vals(expanded["bool"]) == [True, None, False, None]
    assert expanded["id"].tolist() == [1, 1, 2, 3]


def test_json_expand_remove_json_fences() -> None:
    table = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "answer": [
                """```json
      [
        {"number": 1, "letter": "A", "bool": true},
        {"number": 2, "letter": "B", "bool": null}
      ]
      ```""",
                """Some gibber
      ```json
      [{"number": 3, "letter": "C", "bool": false}]
      ```
      more gibber""",
                """```json
      ```""",
            ],
        }
    )
    expanded = json_expand(table)
    assert _vals(expanded["number"]) == [1, 2, 3, None]
    assert _vals(expanded["letter"]) == ["A", "B", "C", None]
    assert _vals(expanded["bool"]) == [True, None, False, None]


def test_json_expand_errors() -> None:
    with pytest.raises(ValueError, match="Argument 3 must be a data frame"):
        json_expand(pd.DataFrame({"id": [1, 2, 3], "answer": ['{"a":1}', '{"b":2}', "[1,[2]]"]}))
    with pytest.raises(ValueError, match="must not be duplicated"):
        json_expand(pd.DataFrame({"id": [1], "answer": ['{"a":"x","a":"y"}']}))
    with pytest.raises(ValueError, match="Join columns"):
        json_expand(pd.DataFrame({"id": [], "answer": []}))
    with pytest.raises(ValueError, match="suffix"):
        json_expand(["{}"], suffix=("a",))


def test_json_expand_col_index_and_missing_col() -> None:
    table = pd.DataFrame({"id": [1, 2], "answer": ['{"a":1}', '{"b":2}']})
    assert list(json_expand(table, col=2).columns) == ["id", "answer", "a", "b"]
    table = pd.DataFrame({"resp": ['{"a":1}'], "id": [1]})
    assert list(json_expand(table, col="nope").columns) == ["resp", "id", "a"]


def test_json_expand_deparse_and_types() -> None:
    table = pd.DataFrame(
        {
            "id": [1, 2],
            "answer": [
                '{"a": {"b": [1,3], "c": null, "h": {"i": 1}, "m": []}, "n": 1e5, "x": " 12"}',
                '{"a": [[1,2],[3,4]], "n": 100000, "x": "7"}',
            ],
        }
    )
    out = json_expand(table)
    assert _vals(out["a"]) == ["c(1, 3);NULL;list(i = 1);list()", "1;3;2;4"]
    assert out["n"].tolist() == [100000.0, 100000.0]
    assert str(out["n"].dtype) == "float64"
    assert _vals(out["x"]) == [12, 7]


def test_json_expand_does_not_mutate_input() -> None:
    table = pd.DataFrame({"id": [1], "answer": ['{"a": 1}']})
    before = table.copy()
    json_expand(table)
    pd.testing.assert_frame_equal(table, before)


# -- R semantics building blocks ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (".05", 0.05),
        ("5.0e-2", 0.05),
        (" 12", 12.0),
        ("12 ", 12.0),
        ("0x1A", 26.0),
        ("0x1p3", 8.0),
        ("Infinity", math.inf),
        ("-inf", -math.inf),
        ("1e", None),
        ("1e+", None),
        ("ns", None),
        ("n.s.", None),
        ("", None),
        (" NA", None),
        ("1,5", None),
    ],
)
def test_as_numeric(text: str, expected: float | None) -> None:
    value = as_numeric(text)
    if expected is None:
        assert math.isnan(value)
    else:
        assert value == pytest.approx(expected)


def test_as_numeric_nan() -> None:
    assert math.isnan(as_numeric("NaN"))
    assert math.isnan(as_numeric(None))


def test_type_convert() -> None:
    assert str(type_convert(["T", "FALSE", None, ""]).dtype) == "boolean"
    assert _vals(type_convert(["T", "FALSE", None, ""])) == [True, False, None, None]
    assert str(type_convert(["true"]).dtype) == "string"
    assert _vals(type_convert([" 12", "3", " "])) == [12, 3, None]
    assert str(type_convert(["12 ", "3"]).dtype) == "float64"
    assert str(type_convert(["-2147483648"]).dtype) == "float64"
    assert str(type_convert(["NA", None]).dtype) == "boolean"
    assert _vals(type_convert(["", "a", "NA"])) == ["", "a", None]
    cx = type_convert(["1+2i", "3", "0"])
    assert str(cx.dtype) == "complex128"
    assert cx.tolist() == [complex(1, 2), complex(3, 0), complex(0, 0)]


def test_json_parser_follows_yajl() -> None:
    assert _deparse(_simplify(_parse_json('{"a": 1} // comment'))) == "list(a = 1)"
    assert _deparse(_simplify(_parse_json('/* x */ {"a": [1, 2]} /* open'))) == "list(a = 1:2)"
    assert _deparse(_simplify(_parse_json('[1, "NA"]'))) == "c(1, NA)"
    assert _deparse(_simplify(_parse_json('["Inf", null]'))) == "c(Inf, NA)"
    assert _deparse(_simplify(_parse_json("[[1,2],[3,4]]"))) == "c(1, 3, 2, 4)"
    assert _deparse(_simplify(_parse_json('{"a": "x\\u0000y"}'))) == 'list(a = "x")'
    for bad in ('{"a": 1,}', "[NaN]", "{'a': 1}", '{"a":1} x', "tru", ""):
        with pytest.raises(ValueError):
            _parse_json(bad)


# --- review: R's typeconvert() passes, BOM, Mongo-style dates -----------------


@pytest.mark.parametrize(
    ("values", "dtype", "expected"),
    [
        # a field must start with a number to be complex
        (["i"], "string", ["i"]),
        (["+i"], "string", ["+i"]),
        (["NAi"], "string", ["NAi"]),
        (["NA+1i"], "string", ["NA+1i"]),
        (["1i", "i"], "string", ["1i", "i"]),
        # "NA " is never a number; "NAN" only once the integer pass has failed
        (["NA "], "string", ["NA "]),
        (["1.5", "NA "], "string", ["1.5", "NA "]),
        (["NAN"], "string", ["NAN"]),
        (["NAN", "1.5"], "string", ["NAN", "1.5"]),
        (["1", "NAN"], "string", ["1", "NAN"]),
        (["1.5", "NAN"], "float64", [1.5, None]),
        (["1.5", "2", "NAN"], "float64", [1.5, 2.0, None]),
        # hexadecimal: an exponent needs digits, a second "." restarts it
        (["0x1p"], "string", ["0x1p"]),
        (["0x1p1"], "float64", [2.0]),
        (["0x1.8.8"], "float64", [24.5]),
        (["0xp1"], "float64", [0.0]),
        (["TRUE", None, "F"], "boolean", [True, None, False]),
        (["T", "1"], "string", ["T", "1"]),
    ],
)
def test_type_convert_r_passes(values: list, dtype: str, expected: list) -> None:
    out = type_convert(values)
    assert str(out.dtype) == dtype
    assert _vals(out) == expected


def test_type_convert_complex_values() -> None:
    cx = type_convert(["1i", " 1+2i ", "Infi", "0x10+0x1p2i", None, "1.5e3-2.5e-3i"])
    assert str(cx.dtype) == "complex128"
    vals = cx.tolist()
    assert vals[0] == complex(0, 1)
    assert vals[1] == complex(1, 2)
    assert vals[2] == complex(0, math.inf)
    assert vals[3] == complex(16, 4)
    assert math.isnan(vals[4].real) and math.isnan(vals[4].imag)
    assert vals[5] == complex(1500, -0.0025)
    # "1i", "NAN": complex, with NaN for "NAN" (the complex pass reads it)
    both = type_convert(["1i", "NAN"]).tolist()
    assert both[0] == complex(0, 1) and math.isnan(both[1].real) and both[1].imag == 0


def test_json_expand_byte_order_mark() -> None:
    table = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "answer": ['﻿{"a": 1}', '```json\n﻿{"a": 2}\n```', '﻿﻿{"a": 3}', "﻿[1]"],
        }
    )
    with pytest.warns(UserWarning, match="byte-order-mark"):
        out = json_expand(table)
    assert list(out.columns) == ["id", "answer", "a", "error"]
    assert _vals(out["a"]) == [1, 2, None, None]
    assert _vals(out["error"]) == [None, None, "parsing error", "not a list"]


@pytest.fixture
def utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run in UTC: numeric dates print in the local time zone, as in R."""
    import time

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("utc")
def test_json_expand_mongo_dates() -> None:
    table = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "answer": [
                '{"a": {"$date": 1234567890123}, "b": 1}',
                '{"a": {"$date": 1234567890000}}',
                '[{"x": 1, "d": {"$date": 86400000}}, {"x": 2, "d": {"$date": 1500}}]',
                '{"a": {"$date": ["2020-01-01T10:11:12.345Z", "2020-02-30T00:00:00Z"]}, "b": 2}',
            ],
        }
    )
    out = json_expand(table)
    assert list(out.columns) == ["id", "answer", "a", "b", "error", "x", "d"]
    # a POSIXct is pasted as R prints it; one that is the whole answer is "not a list"
    assert _vals(out["a"]) == [
        "2009-02-13 23:31:30.123",
        None,
        None,
        None,
        "2020-01-01 10:11:12.345;NA",
    ]
    assert _vals(out["error"]) == [None, "not a list", None, None, None]
    assert _vals(out["d"]) == [None, None, "1970-01-02", "1970-01-01 00:00:01.5", None]
