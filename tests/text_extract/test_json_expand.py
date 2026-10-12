"""Port of tests/testthat/test-text-json_expand.R, plus the flatten and type guess (D76)."""

from __future__ import annotations

import pandas as pd
import pytest

from metacheck.text.json_expand import json_expand


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
    # D76: R fails on an array that is not of objects and on a repeated key
    out = json_expand(pd.DataFrame({"id": [1, 2, 3], "answer": ['{"a":1}', '{"b":2}', "[1,[2]]"]}))
    assert _vals(out["error"]) == [None, None, "not a list"]
    out = json_expand(pd.DataFrame({"id": [1], "answer": ['{"a":"x","a":"y"}']}))
    assert _vals(out["a"]) == ["y"]
    # U151: a table without rows is returned as it is (R: "Join columns ...")
    empty = json_expand(pd.DataFrame({"id": [], "answer": []}))
    assert list(empty.columns) == ["id", "answer"] and len(empty) == 0
    with pytest.raises(ValueError, match="suffix"):
        json_expand(["{}"], suffix=("a",))


def test_json_expand_col_index_and_missing_col() -> None:
    table = pd.DataFrame({"id": [1, 2], "answer": ['{"a":1}', '{"b":2}']})
    assert list(json_expand(table, col=2).columns) == ["id", "answer", "a", "b"]
    table = pd.DataFrame({"resp": ['{"a":1}'], "id": [1]})
    assert list(json_expand(table, col="nope").columns) == ["resp", "id", "a"]


def test_json_expand_flattens_nested_values() -> None:
    # D76: nested arrays and objects are their leaves joined by ";" (R deparses them)
    table = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "answer": [
                '{"a": {"b": [1,3], "c": null, "h": {"i": "x"}}, "m": [], "n": 1e5, "x": " 12"}',
                '{"a": [[1,2],[3,4]], "m": {}, "n": 100000, "x": "7"}',
                '{"a": [true, null, 0.5], "n": null, "x": "NA"}',
            ],
        }
    )
    out = json_expand(table)
    assert _vals(out["a"]) == ["1;3;NA;x", "1;2;3;4", "TRUE;NA;0.5"]
    assert _vals(out["m"]) == [None, None, None]
    assert out["n"].tolist()[:2] == [100000.0, 100000.0]
    assert str(out["n"].dtype) == "float64"
    assert _vals(out["x"]) == [12, 7, None]


@pytest.mark.parametrize(
    ("values", "dtype", "expected"),
    [
        ('true, "FALSE", "T", null, ""', "boolean", [True, False, True, None, None]),
        ('1, " 2", "+3", "NA", ""', "Int64", [1, 2, 3, None, None]),
        ('1, 2.5, "1e3", "0x10"', "float64", [1.0, 2.5, 1000.0, 16.0]),
        ('1, "a", true, ""', "string", ["1", "a", "TRUE", ""]),
        ('0.1, "true"', "string", ["0.1", "true"]),
        ("9223372036854775808, 1", "float64", [9223372036854775808.0, 1.0]),
        ("null, null", "boolean", [None, None]),
    ],
)
def test_json_expand_guesses_column_types(values: str, dtype: str, expected: list) -> None:
    answers = [f'{{"v": {v}}}' for v in values.split(", ")]
    out = json_expand(pd.DataFrame({"answer": answers}))
    assert str(out["v"].dtype) == dtype
    assert _vals(out["v"]) == expected


def test_json_expand_reads_strict_json() -> None:
    # D76: comments, single quotes and trailing commas are parsing errors (yajl
    # allowed comments); a byte-order mark is skipped; null and empty are no data
    answers = [
        '{"a": 1} // comment',
        "{'a': 1}",
        '{"a": 1,}',
        '\ufeff{"a": 4}',
        "null",
        '[{}, null, {"a": 7}]',
        '[{"a": 1}, 2]',
        '"text"',
    ]
    out = json_expand(pd.DataFrame({"id": range(1, 9), "answer": answers}))
    assert _vals(out["id"]) == [1, 2, 3, 4, 5, 6, 6, 6, 7, 8]
    assert _vals(out["a"]) == [None, None, None, 4, None, None, None, 7, None, None]
    assert _vals(out["error"]) == ["parsing error"] * 3 + [None] * 5 + ["not a list"] * 2


def test_json_expand_keeps_the_table_types() -> None:
    table = pd.DataFrame(
        {
            "id": pd.Series([1, 2], dtype="Int64"),
            "answer": ['{"a": "x"}', '[{"a": "y"}, {"a": "z"}]'],
        }
    )
    out = json_expand(table)
    assert str(out["id"].dtype) == "Int64"
    assert _vals(out["id"]) == [1, 2, 2]
    assert _vals(out["a"]) == ["x", "y", "z"]


def test_json_expand_does_not_mutate_input() -> None:
    table = pd.DataFrame({"id": [1], "answer": ['{"a": 1}']})
    before = table.copy()
    json_expand(table)
    pd.testing.assert_frame_equal(table, before)


def test_row_key_is_an_ordinary_column() -> None:
    # U9/U151: jsonlite makes a "_row" key the row names: the column vanished,
    # NA labels were mangled through a logical matrix, rows were recycled or
    # truncated, or the element failed to parse; a nested value is flattened (D76)
    answers = [
        '[{"_row": {"a": {"b": null}}, "x": 1}, {"_row": {"a": {"b": "q"}}, "x": 2}]',
        '[{"_row": "r1", "x": 1}, {"_row": "r1", "x": 2}]',
        '[{"_row": null, "x": 1}, {"_row": "r2", "x": 2}]',
        '{"_row": "r3", "x": 4, ".temp_id.": 9}',
    ]
    out = json_expand(pd.DataFrame({"id": [1, 2, 3, 4], "answer": answers}))
    assert list(out.columns) == ["id", "answer", "_row", "x", ".temp_id."]
    assert _vals(out["id"]) == [1, 1, 2, 2, 3, 3, 4]
    assert _vals(out["x"]) == [1, 2, 1, 2, 1, 2, 4]
    assert _vals(out["_row"]) == [None, "q", "r1", "r1", None, "r2", "r3"]
    # a ".temp_id." key is data, not metacheck's internal join key
    assert _vals(out[".temp_id."]) == [None] * 6 + [9]
