"""Unit tests for the fixes made in the adversarial review of the archives_d2 port.

Each test pins down R behaviour checked against metacheck (and recorded as a
parity case in ``parity/cases/archives_d2_review.yaml``).
"""

from __future__ import annotations

import warnings

import httpx
import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.fsd import fsd_links
from pytacheck.archives.osf_helpers import _resp_body_json
from pytacheck.archives.psycharchives import (
    PasteLengthError,
    _paste_json,
    _resp_json,
    _url_piece,
)


def _resp(body: bytes, ctype: str | None = "application/json") -> httpx.Response:
    headers = {} if ctype is None else {"content-type": ctype}
    return httpx.Response(200, headers=headers, content=body)


# --------------------------------------------------------------------------- resp_body_json


def test_json_is_read_as_utf8_whatever_the_charset() -> None:
    # httr2: resp_body_string(resp, "UTF-8"), so a declared Latin-1 charset is ignored
    utf8 = _resp('{"a": "café"}'.encode(), "application/json; charset=ISO-8859-1")
    assert _resp_body_json(utf8) == {"a": "café"}
    latin1 = _resp('{"a": "café"}'.encode("latin-1"), "application/json; charset=ISO-8859-1")
    with pytest.raises(ValueError):
        _resp_body_json(latin1)  # R: iconv() gives NA, fromJSON(NA) fails
    assert _resp_json(latin1) is None


def test_json_bom_is_dropped_with_a_warning() -> None:
    with pytest.warns(UserWarning, match="byte-order-mark"):
        assert _resp_body_json(_resp(b'\xef\xbb\xbf{"a": 1}')) == {"a": 1}


def test_json_body_ends_at_a_nul_byte() -> None:
    assert _resp_body_json(_resp(b'{"a": 1}\x00{"junk"')) == {"a": 1}


def test_json_constants_are_refused() -> None:
    for body in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}'):
        assert _resp_json(_resp(body)) is None


def test_json_repeated_key_keeps_the_first_value() -> None:
    # R keeps both elements; `$` finds the first
    assert _resp_body_json(_resp(b'{"a": 1, "b": 2, "a": 3}')) == {"a": 1, "b": 2}


def test_json_escaped_nul_ends_a_string() -> None:
    assert _resp_body_json(_resp(b'{"a": "x\\u0000y", "b": ["p\\u0000q"]}')) == {
        "a": "x",
        "b": ["p"],
    }


def test_json_content_type_is_checked() -> None:
    assert _resp_body_json(_resp(b'{"a": 1}', "application/hal+json;charset=UTF-8")) == {"a": 1}
    for ctype in ("text/plain", "text/html; charset=utf-8", None):
        assert _resp_json(_resp(b'{"a": 1}', ctype)) is None
    assert _resp_json(_resp(b"", "application/json")) is None


# --------------------------------------------------------------------------- paste0()


def test_paste_json_follows_as_character_of_a_list() -> None:
    assert _paste_json("x") == "x"
    assert _paste_json(None) == "NA"
    assert _paste_json(3) == "3"
    assert _paste_json(3000000000) == "3e+09"
    assert _paste_json(1.5) == "1.5"
    assert _paste_json(True) == "TRUE"
    assert _paste_json([]) == ""  # paste0() drops a zero-length argument
    assert _paste_json({}) == ""
    assert _paste_json(["a"]) == "a"
    assert _paste_json({"k": 2}) == "2"
    assert _paste_json([None]) == "NULL"
    assert _paste_json([[1, 2]]) == "list(1, 2)"
    assert _paste_json([{"a": 1, "b": "s"}]) == 'list(a = 1, b = "s")'
    assert _paste_json([[]]) == "list()"
    with pytest.raises(PasteLengthError):
        _paste_json(["a", "b"])


def test_url_piece_of_several_ids_is_no_request() -> None:
    assert _url_piece(["u1"]) == "u1"
    assert _url_piece(["u1", "u2"]) is None


# --------------------------------------------------------------------------- empty paper list


def test_fsd_links_empty_paper_list() -> None:
    links = fsd_links(pc.PaperList([]))
    assert isinstance(links, pd.DataFrame)
    assert len(links) == 0
    assert "href" in links.columns


def test_json_field_arrays_follow_r_replacement_rules() -> None:
    from pytacheck.archives.dataverse import _field_cell

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _field_cell(["T"]).tolist() == [["T"]]
    with pytest.raises(ValueError, match="replacement has 2 rows"):
        _field_cell(["a", "b"])
