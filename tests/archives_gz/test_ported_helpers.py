"""metacheck 0.4.3.x helpers of the download and zip code (R/repo-download.R, R/zip-peek.R,
R/svutils-utils.R) that earlier ports folded into other functions."""

from __future__ import annotations

import math
import warnings
from typing import Any

import httpx
import pytest
import respx

from metacheck._values import field_or_value
from metacheck.archives.dataverse import _omit_for_size
from metacheck.archives.zip_peek import (
    _WAF_CHALLENGE_WARNED,
    _ext_type,
    _head_size,
    _http_range_get,
    _waf_challenge_check,
)

MB = 1024 * 1024


class Bar:
    """A progress bar that keeps what it was told."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def tick(self, n: int, tokens: dict[str, str]) -> None:
        self.said.append(tokens["what"])


# --------------------------------------------------------------------------- .omit_for_size


def test_omit_for_size_leaves_out_files_over_the_file_cap_and_says_so() -> None:
    bar = Bar()
    out = _omit_for_size([1 * MB, 12 * MB, 3 * MB], ["a", "big", "c"], [False] * 3, 10, None, bar)
    assert out == [False, True, False]
    assert bar.said == ["- omitting big (12MB)"]


def test_omit_for_size_never_leaves_out_an_exempt_file() -> None:
    out = _omit_for_size([50 * MB, 50 * MB], ["a.zip", "b"], [True, False], 10, 20, None)
    assert out == [False, True]


def test_omit_for_size_drops_the_largest_until_the_rest_fit() -> None:
    bar = Bar()
    sizes = [30 * MB, 60 * MB, 20 * MB, 40 * MB]
    out = _omit_for_size(sizes, list("abcd"), [False] * 4, None, 100, bar)
    # 150 MB in all: b (60) goes, then 90 MB fit
    assert out == [False, True, False, False]
    assert bar.said == ["- omitting b (60MB)"]
    out = _omit_for_size(sizes, list("abcd"), [False] * 4, None, 50, None)
    assert out == [False, True, False, True]  # 60 MB, then 40 MB: 50 MB remain


def test_omit_for_size_prefers_the_first_of_equal_sizes_and_ignores_unknown_sizes() -> None:
    out = _omit_for_size([8 * MB, 8 * MB, math.nan], ["a", "b", "c"], [False] * 3, None, 10, None)
    assert out == [True, False, False]


@pytest.mark.parametrize("cap", [None, 0, -1, math.inf])
def test_omit_for_size_caps_that_are_off_leave_everything(cap: float | None) -> None:
    assert _omit_for_size([500 * MB, 600 * MB], ["a", "b"], [False] * 2, cap, cap, None) == [
        False,
        False,
    ]


def test_omit_for_size_names_a_missing_key_as_na() -> None:
    bar = Bar()
    _omit_for_size([20 * MB], [None], [False], 10, None, bar)
    assert bar.said == ["- omitting NA (20MB)"]


# --------------------------------------------------------------------------- .waf_challenge_check


def _challenge(url: str = "https://ldbase.org/x") -> httpx.Response:
    return httpx.Response(
        202, headers={"x-amzn-waf-action": "challenge"}, request=httpx.Request("GET", url)
    )


@pytest.fixture(autouse=True)
def _forget_warned_hosts() -> Any:
    _WAF_CHALLENGE_WARNED.clear()
    yield
    _WAF_CHALLENGE_WARNED.clear()


@respx.mock
def test_a_firewall_check_gives_a_head_request_no_size_and_warns_once() -> None:
    url = "https://files.example.org/a.zip"
    route = respx.head(url).mock(return_value=_challenge(url))
    with pytest.warns(UserWarning, match="files.example.org's firewall answered with a browser"):
        assert math.isnan(_head_size(url))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert math.isnan(_head_size(url))  # the host was warned about already
    assert route.called


@respx.mock
def test_a_firewall_check_gives_a_ranged_get_no_total_and_no_body() -> None:
    url = "https://files.example.org/b.zip"
    respx.get(url).mock(return_value=_challenge(url))
    with pytest.warns(UserWarning, match="AWS WAF challenge"):
        status, total, body = _http_range_get(url, "bytes=-1024")
    assert status == 202 and math.isnan(total) and body is None


def test_waf_challenge_check_only_recognises_the_challenge_header() -> None:
    ok = httpx.Response(200, request=httpx.Request("GET", "https://x.org/"))
    other = httpx.Response(
        202, headers={"x-amzn-waf-action": "block"}, request=httpx.Request("GET", "https://x.org/")
    )
    assert _waf_challenge_check(ok, "https://x.org/") is False
    assert _waf_challenge_check(other, "https://x.org/") is False


# --------------------------------------------------------------------------- .inner_file_type


def test_inner_file_type_comes_from_the_extension() -> None:
    assert _ext_type(["a.csv", "B.R", "c.PNG", "d", "e.unknownext"])[:2] == ["data", "code"]
    types = _ext_type(["a.csv", "d", "e.unknownext"])
    assert types[1] is None and types[2] is None


# --------------------------------------------------------------------------- .field_or_value


def test_field_or_value_reads_an_object_or_takes_the_string() -> None:
    assert field_or_value({"name": "CC0"}, "name") == "CC0"
    assert field_or_value("CC0", "name") == "CC0"
    assert field_or_value({"other": 1}, "name") is None
    assert field_or_value(None, "name") is None
    assert field_or_value([{"name": "x"}], "name") is None  # an unnamed list has no names
