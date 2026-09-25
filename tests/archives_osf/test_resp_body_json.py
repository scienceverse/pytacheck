"""``osf_helpers._resp_body_json()``: ``httr2::resp_body_json()`` with jsonlite's parser.

Every expectation was checked with jsonlite 2.0.0 / httr2 1.3.0 (the
``archives_gz_review`` ``.zenodo_info.review.json_*`` and
``archives_osf_review`` ``.osf_parse_response.review.json_*`` parity cases
pin the same behaviour end to end).
"""

from __future__ import annotations

import httpx
import pytest

from pytacheck.archives.osf_helpers import _from_json, _resp_body_json


def _resp(body: bytes, ctype: str | None = "application/json") -> httpx.Response:
    headers = {} if ctype is None else {"content-type": ctype}
    return httpx.Response(200, content=body, headers=headers)


def test_comments_and_yajl_whitespace() -> None:
    assert _from_json('{"a": /* c */ 1, // x\n "b": 2}') == {"a": 1, "b": 2}
    assert _from_json('{"a":\x0b1}') == {"a": 1}
    # comment markers inside strings are text
    assert _from_json('{"u": "http://x/*y*/"} // c') == {"u": "http://x/*y*/"}
    with pytest.raises(ValueError):
        _from_json("[1, /* unterminated")


def test_repeated_keys_keep_the_first() -> None:
    assert _from_json('{"a": 1, "a": 2}') == {"a": 1}


def test_nul_and_surrogate_escapes() -> None:
    assert _from_json('{"a": "x\\u0000y"}') == {"a": "x"}
    assert _from_json('{"\\u0000k": 1}') == {"": 1}
    assert _from_json('{"k\\u0000": 1, "k": 2}') == {"k": 1}
    assert _from_json('{"a": "x\\ud800y"}') == {"a": "x?y"}
    # yajl combines a high surrogate with any following \u escape
    assert _from_json('{"a": "x\\ud800\\u0041y"}') == {"a": "x\U00010041y"}
    assert _from_json('{"a": "\\ud83d\\ude00"}') == {"a": "\U0001f600"}


def test_numbers() -> None:
    out = _from_json('{"a": 3000000000, "b": 2147483647, "c": -2147483648, "d": 1E400}')
    assert out == {"a": 3e9, "b": 2147483647, "c": -2147483648.0, "d": float("inf")}
    assert isinstance(out["a"], float) and isinstance(out["b"], int)
    with pytest.raises(ValueError, match="invalid char"):
        _from_json('{"a": NaN}')
    with pytest.raises(ValueError):
        _from_json('{"a": Infinity}')


def test_byte_order_mark_warns() -> None:
    with pytest.warns(UserWarning, match="byte-order-mark"):
        assert _from_json('﻿{"a": 1}') == {"a": 1}


def test_body_is_read_as_utf8_up_to_the_first_nul() -> None:
    body = '{"t": "Café €"}'.encode()
    assert _resp_body_json(_resp(body, "application/json; charset=ISO-8859-1")) == {"t": "Café €"}
    assert _resp_body_json(_resp(b'{"a": 1}\x00garbage')) == {"a": 1}
    with pytest.raises(ValueError, match="missing value"):
        _resp_body_json(_resp('{"t": "Café"}'.encode("latin-1")))
    with pytest.raises(ValueError, match="empty body"):
        _resp_body_json(_resp(b""))


def test_content_type_check() -> None:
    assert _resp_body_json(_resp(b"[1]", "application/vnd.api+json; charset=utf-8")) == [1]
    for ctype in ("text/html", "Application/JSON", None):
        with pytest.raises(ValueError, match="Unexpected content type"):
            _resp_body_json(_resp(b"[1]", ctype))
