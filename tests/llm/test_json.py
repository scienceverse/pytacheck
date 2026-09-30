"""jsonlite-compatible JSON writing and yajl-compatible parsing."""

from __future__ import annotations

import pytest

from metacheck.llm._json import JSONParseError, Vec, parse_json, to_json
from metacheck.llm._rds import RInt
from tests.llm.support import load_json


@pytest.mark.parametrize("case", load_json("yajl_errors.json"), ids=lambda c: repr(c["input"])[:30])
def test_parse_errors_match_jsonlite(case: dict[str, str | None]) -> None:
    text = case["input"]
    assert isinstance(text, str)
    if case["error"] is None:
        parse_json(text)
        return
    with pytest.raises(JSONParseError) as err:
        parse_json(text)
    assert str(err.value) == case["error"]


def test_parse_values() -> None:
    out = parse_json('{"a": [1, 2.5, null, true, "x"], "b": {}, "c": 123456789012, "a": 9}')
    assert out == {"a": [1, 2.5, None, True, "x"], "b": {}, "c": 123456789012.0}
    assert isinstance(out["a"][0], RInt)  # integers are R integers
    assert isinstance(out["c"], float)  # too big for an R integer
    assert parse_json("/* comment */ [1] // trailing") == [1]
    assert parse_json('"\\u00e9\\n"') == "é\n"
    assert parse_json("1e999") == float("inf")


def test_to_json_like_jsonlite() -> None:
    body = {
        "a": 0.0,
        "b": 4096,
        "c": 0.1,
        "d": 1 / 3,
        "e": 1e-20,
        "f": 1e22,
        "g": True,
        "h": None,
        "i": [],
        "j": {},
        "k": Vec(["x"]),
        "l": ["x"],
        "m": 'q"\\\n\t\x01é/ ',
        "n": 123456.7,
        "o": float("nan"),
    }
    assert to_json(body) == (
        '{"a":0,"b":4096,"c":0.10000000000000001,"d":0.33333333333333331,'
        '"e":9.9999999999999995e-21,"f":1e+22,"g":true,"h":null,"i":[],"j":{},'
        '"k":"x","l":["x"],"m":"q\\"\\\\\\n\\t\\u0001é/ ","n":123456.7,"o":"NA"}'
    )
