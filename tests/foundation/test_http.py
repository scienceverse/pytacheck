from __future__ import annotations

import httpx
import respx

from pytacheck import http
from tests.httpmock import mock_path, r_digest, replay


def test_r_digest_matches_r() -> None:
    # digest::digest("query=hello&rows=2") in R
    assert r_digest("query=hello&rows=2") == "8a1e7aae5ba13058036107f7db19d3b6"


def test_mock_path_follows_httptest2() -> None:
    req = httpx.Request("GET", "https://api.github.com/repos/scienceverse/metacheck/readme")
    assert mock_path(req) == "api.github.com/repos/scienceverse/metacheck/readme"


def test_replay_recorded_fixture(upstream_dir) -> None:
    with replay("apis"):
        resp = http.request("GET", "https://api.github.com/repos/scienceverse/metacheck/readme")
    assert resp is not None and resp.status_code == 200
    assert resp.json()["name"].lower().startswith("readme")


def test_replay_full_r_response(upstream_dir) -> None:
    with replay("apis"):
        resp = http.request("GET", "https://httpbin.org/status/429", max_tries=1)
    assert resp is not None and resp.status_code == 429


def test_retries_then_succeeds() -> None:
    with respx.mock() as router:
        route = router.get("https://example.org/x").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(503),
                httpx.Response(200, json={"ok": 1}),
            ]
        )
        resp = http.request("GET", "https://example.org/x")
    assert resp is not None and resp.json() == {"ok": 1}
    assert route.call_count == 3


def test_rate_limit_reset_is_remembered_and_skippable() -> None:
    with respx.mock() as router:
        router.get("https://limited.example/a").mock(
            return_value=httpx.Response(429, headers={"X-RateLimit-Reset": "3600"})
        )
        with http.skip_on_api_limit():
            first = http.request("GET", "https://limited.example/a")
            assert first is not None and first.status_code == 429
            assert http.host_reset_at("limited.example") is not None
            assert http.request("GET", "https://limited.example/a") is None


def test_batch_query_aligns_results() -> None:
    with respx.mock() as router:
        router.get("https://example.org/1").mock(return_value=httpx.Response(200, json=1))
        router.get("https://example.org/2").mock(return_value=httpx.Response(404))
        out = http.batch_query(
            ["https://example.org/1", "not a url", "https://example.org/2"], msg=None
        )
    assert [r.status_code if r is not None else None for r in out] == [200, None, 404]
