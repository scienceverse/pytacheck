"""Port of metacheck tests/testthat/test-db-pubpeer.R."""

from __future__ import annotations

import httpx
import pandas as pd
import pytest
import respx

from pytacheck.db.pubpeer import _request_body, pubpeer_comments


def test_request_body_matches_jsonlite() -> None:
    # auto_unbox = TRUE: a single DOI is sent as a string, not an array
    assert _request_body([]) == '{"dois":[]}'
    assert _request_body(["10.1/a"]) == '{"dois":"10.1/a"}'
    assert _request_body(["10.1/a", "10.1/b"]) == '{"dois":["10.1/a","10.1/b"]}'


def test_defaults(apis) -> None:
    # both with comments
    doi = ["10.1038/s41598-025-24662-9", "10.1177/0146167211398138"]
    pp = pubpeer_comments(doi)
    assert pp["doi"].tolist() == doi
    assert pp["total_comments"].iat[0] >= 16

    # one with comments
    doi = ["10.1038/s41598-025-24662-9", "10.1016/j.tics.2006.06.010"]
    pp = pubpeer_comments(doi)
    assert pp["doi"].tolist() == doi
    assert pp["total_comments"].iat[1] == 0

    # none with comments
    pp = pubpeer_comments(["10.1016/j.tics.2006.06.010"])
    assert pp["doi"].tolist() == ["10.1016/j.tics.2006.06.010"]
    assert pp["total_comments"].iat[0] == 0

    # invalid DOI
    pp = pubpeer_comments(["nope/hasd"])
    assert pp["doi"].tolist() == ["nope/hasd"]
    assert pp["total_comments"].iat[0] == 0

    # one NA
    pp = pubpeer_comments(["10.1038/s41598-025-24662-9", None])
    assert pp["doi"].tolist()[0] == "10.1038/s41598-025-24662-9"
    assert pd.isna(pp["doi"].iat[1])
    assert pd.isna(pp["url"].iat[1])
    assert pp["total_comments"].iat[1] == 0

    # empty doi
    assert pubpeer_comments([]) is None


def test_case_insensitive_join_keeps_input_case() -> None:
    body = {
        "feedbacks": [
            {
                "id": "10.1/abc",
                "total_comments": 3,
                "url": "https://pubpeer.com/p/1",
                "users": [" Ann ", "Bob"],
            }
        ]
    }
    with respx.mock() as router:
        route = router.post("https://pubpeer.com/v3/publications").mock(
            return_value=httpx.Response(200, json=body)
        )
        pp = pubpeer_comments(["10.1/ABC", "10.1/other"])
    assert route.calls.last.request.content == b'{"dois":["10.1/abc","10.1/other"]}'
    assert pp["doi"].tolist() == ["10.1/ABC", "10.1/other"]
    assert pp["total_comments"].tolist() == [3, 0]
    assert pp["users"].tolist()[0] == "Ann, Bob"


def test_connection_failure_raises() -> None:
    with respx.mock() as router:
        router.post("https://pubpeer.com/v3/publications").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(ConnectionError):
            pubpeer_comments(["10.1/abc"])
