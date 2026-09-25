"""pytacheck._json.loads: orjson with a standard-library fallback."""

from __future__ import annotations

import json
import math

import orjson
import pytest

from pytacheck._json import JSONDecodeError, loads


@pytest.mark.parametrize(
    "data",
    ['{"a": [1, 2.5, "x", true, null]}', b'{"a": [1, 2.5, "x", true, null]}'],
)
def test_plain_documents(data: str | bytes) -> None:
    assert loads(data) == {"a": [1, 2.5, "x", True, None]}
    assert loads(bytearray(data.encode() if isinstance(data, str) else data)) == loads(data)
    assert loads(memoryview(data.encode() if isinstance(data, str) else data)) == loads(data)


def test_valid_surrogate_pair_is_one_character() -> None:
    assert loads(r'"𝐀 😀"') == "\U0001d400 \U0001f600"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r'"a\ud800b"', "a�b"),  # lone high surrogate
        (r'"a\udc00b"', "a�b"),  # lone low surrogate
        (r'"\ud83dA"', "�A"),  # high surrogate followed by another escape
        (r'"\udc00\ud800"', "��"),  # low before high
    ],
)
def test_lone_surrogate_escapes_become_replacement_characters(text: str, expected: str) -> None:
    with pytest.raises(orjson.JSONDecodeError):
        orjson.loads(text)  # the fallback is needed
    assert loads(text) == expected
    assert loads(text.encode()) == expected
    loads(text).encode("utf-8")  # writable


def test_lone_surrogates_are_replaced_everywhere() -> None:
    doc = r'{"k\ud800": ["\udfff", {"x": "\ud800\ud800"}], "ok": "😀", "n": 1}'
    assert loads(doc) == {
        "k�": ["�", {"x": "��"}],
        "ok": "\U0001f600",
        "n": 1,
    }
    assert list(loads(doc)) == ["k�", "ok", "n"]  # key order kept


def test_raw_surrogates_in_a_str_are_replaced() -> None:
    assert loads('"a\ud800b"') == "a�b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("18446744073709551615", 18446744073709551615),  # 2**64 - 1: still exact
        ("-9223372036854775808", -9223372036854775808),  # -2**63
        ("18446744073709551616", 1.8446744073709552e19),  # 2**64
        ("-9223372036854775809", -9.223372036854776e18),
        ("123456789012345678901234567890", 1.2345678901234568e29),
    ],
)
def test_integers_beyond_64_bits_are_floats(text: str, expected: float) -> None:
    value = loads(text)
    assert value == expected
    assert type(value) is type(expected)
    # the same in the fallback parser (a lone surrogate forces it)
    fallback = loads(f'[{text}, "\\ud800"]')
    assert fallback == [expected, "�"]
    assert type(fallback[0]) is type(expected)


def test_huge_integer_in_the_fallback_does_not_hit_int_digit_limits() -> None:
    digits = "9" * 5000
    assert loads(f'[{digits}, "\\ud800"]') == [math.inf, "�"]


def test_numbers_beyond_double_range_are_infinite() -> None:
    assert loads("[1e400, -1e400, 1e-400]") == [math.inf, -math.inf, 0.0]


@pytest.mark.parametrize("data", ['﻿{"a": 1}', b'\xef\xbb\xbf{"a": 1}'])
def test_leading_bom_is_ignored(data: str | bytes) -> None:
    assert loads(data) == {"a": 1}


def test_repeated_key_keeps_the_last_value() -> None:
    assert loads('{"a": 1, "a": 2}') == {"a": 2}
    assert loads('{"a": 1, "a": "\\ud800"}') == {"a": "�"}


def test_nul_escape_is_kept() -> None:
    assert loads(r'"a\u0000b"') == "a\x00b"


@pytest.mark.parametrize(
    "data",
    [
        "",
        "   ",
        "{",
        "[1, 2",
        "1 2",
        "NaN",
        "[Infinity]",
        '{"a": -Infinity}',
        '[NaN, "\\ud800"]',  # the fallback rejects NaN too
        "// comment\n1",
        "[1, /* comment */ 2]",
        "{'a': 1}",
        '{"a": 1,}',
        '"a\x01b"',  # raw control character
        b'"\xff"',  # not UTF-8
        '{"a": 1}'.encode("utf-16"),  # not UTF-8
        "﻿﻿1",  # only one BOM is dropped
    ],
)
def test_invalid_json_raises(data: str | bytes) -> None:
    with pytest.raises(JSONDecodeError):
        loads(data)
    with pytest.raises(ValueError):
        loads(data)


def test_error_is_the_standard_json_error() -> None:
    assert JSONDecodeError is json.JSONDecodeError
    with pytest.raises(json.JSONDecodeError) as info:
        loads('{"a": }')
    assert info.value.pos > 0


def test_very_deep_nesting_raises_a_json_error() -> None:
    with pytest.raises(JSONDecodeError):
        loads("[" * 100_000 + "]" * 100_000)
