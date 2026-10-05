from __future__ import annotations

import httpx
import pytest
import respx

from metacheck import http
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


def test_sink_reads_only_the_final_answer() -> None:
    seen: list[tuple[int, bytes]] = []

    def sink(resp: httpx.Response) -> None:
        seen.append((resp.status_code, b"".join(resp.iter_bytes())))

    with respx.mock() as router:
        route = router.get("https://example.org/s").mock(
            side_effect=[httpx.Response(503, content=b"busy"), httpx.Response(200, content=b"ok")]
        )
        resp = http.request("GET", "https://example.org/s", sink=sink)
    assert resp is not None and resp.status_code == 200 and route.call_count == 2
    assert seen == [(200, b"ok")]
    assert resp.is_closed


def test_a_sink_is_not_given_the_answer_that_ran_out_of_tries() -> None:
    called: list[int] = []
    with respx.mock() as router:
        route = router.get("https://example.org/s").mock(return_value=httpx.Response(503))
        resp = http.request("GET", "https://example.org/s", sink=lambda r: called.append(1))
    assert resp is not None and resp.status_code == 503
    assert route.call_count == 5 and called == [] and resp.is_closed


def test_a_connection_that_drops_in_the_body_is_a_failed_try() -> None:
    attempts: list[int] = []

    def sink(resp: httpx.Response) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadError("connection reset")

    with respx.mock() as router:
        route = router.get("https://example.org/s").respond(200, content=b"ok")
        resp = http.request("GET", "https://example.org/s", sink=sink)
    assert resp is not None and resp.status_code == 200
    assert route.call_count == 2 and len(attempts) == 2


def test_an_exception_of_the_sink_ends_the_request() -> None:
    def sink(resp: httpx.Response) -> None:
        raise ValueError("too big")

    with respx.mock() as router:
        route = router.get("https://example.org/s").respond(200, content=b"ok")
        with pytest.raises(ValueError, match="too big"):
            http.request("GET", "https://example.org/s", sink=sink)
    assert route.call_count == 1


def test_is_transient_decides_which_answers_are_retried() -> None:
    with respx.mock() as router:
        route = router.get("https://example.org/t").mock(
            side_effect=[httpx.Response(403), httpx.Response(404), httpx.Response(503)]
        )
        resp = http.request(
            "GET", "https://example.org/t", is_transient=lambda r: r.status_code in (403, 404)
        )
    assert resp is not None and resp.status_code == 503  # not in the predicate: final
    assert route.call_count == 3


def test_connection_failures_are_retried_unless_told_not_to() -> None:
    with respx.mock() as router:
        route = router.get("https://down.example/a").mock(side_effect=httpx.ConnectError("refused"))
        assert http.request("GET", "https://down.example/a") is None
        assert route.call_count == 5
        route.reset()
        assert http.request("GET", "https://down.example/a", retry_on_failure=False) is None
        assert route.call_count == 1


def test_raise_errors_raises_the_last_connection_failure() -> None:
    with respx.mock() as router:
        route = router.get("https://down.example/b").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(httpx.ConnectError, match="refused"):
            http.request("GET", "https://down.example/b", raise_errors=True, max_tries=2)
        assert route.call_count == 2


def test_raise_errors_names_a_request_that_was_skipped() -> None:
    http._record_reset("skipped.example", http.time.time() + 3600)
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://skipped.example/a").respond(200)
        with http.skip_on_api_limit():
            with pytest.raises(http.RateLimited):
                http.request("GET", "https://skipped.example/a", raise_errors=True)
            assert http.request("GET", "https://skipped.example/a") is None
    assert route.call_count == 0


def test_an_exhausted_bucket_on_a_retried_403_is_remembered() -> None:
    # GitHub answers a spent quota with 403 and these headers, not 429
    reset = str(int(http.time.time()) + 3600)
    spent = httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
    with respx.mock() as router:
        route = router.get("https://api.github.example/a").mock(return_value=spent)
        with http.skip_on_api_limit():
            resp = http.request(
                "GET", "https://api.github.example/a", is_transient=lambda r: r.status_code == 403
            )
    assert resp is not None and resp.status_code == 403 and route.call_count == 1
    assert http.host_reset_at("api.github.example") is not None


def test_a_known_reset_is_waited_for_once_and_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    said: list[str] = []
    monkeypatch.setattr(http, "sleep", slept.append)
    monkeypatch.setattr("metacheck.utils.message", lambda *parts: said.append("".join(parts)))
    reset = str(int(http.time.time()) + 120)
    with respx.mock() as router:
        route = router.get("https://slow.example/a").mock(
            side_effect=[
                httpx.Response(429, headers={"RateLimit-Reset": reset}),
                httpx.Response(200),
            ]
        )
        resp = http.request("GET", "https://slow.example/a")
    assert resp is not None and resp.status_code == 200 and route.call_count == 2
    assert len(slept) == 1 and 115 < slept[0] <= 121
    assert len(said) == 1 and "Rate limit reached from slow.example; waiting 2.0 min" in said[0]


def _hops(router: respx.MockRouter) -> respx.Route:
    """``a.example/f`` redirects to ``b.example/g``; the second hop is a recorded route."""
    router.get("https://a.example/f").respond(302, headers={"Location": "https://b.example/g"})
    return router.get("https://b.example/g").respond(200, content=b"data")


def test_httpx_drops_the_token_when_a_redirect_leaves_the_host() -> None:
    headers = {"Authorization": "Bearer secret"}
    with respx.mock() as router:
        second = _hops(router)
        assert http.request("GET", "https://a.example/f", headers=headers) is not None
        assert "authorization" not in second.calls.last.request.headers
        second.reset()
        resp = http.request("GET", "https://a.example/f", headers=headers, sink=lambda r: r.read())
        assert resp is not None and resp.status_code == 200
        assert "authorization" not in second.calls.last.request.headers


def test_resend_headers_keeps_the_token_on_every_hop() -> None:
    headers = {"Authorization": "Bearer secret", "Accept": "application/json"}
    with respx.mock() as router:
        second = _hops(router)
        got: list[bytes] = []
        resp = http.request(
            "GET",
            "https://a.example/f",
            headers=headers,
            resend_headers=True,
            sink=lambda r: got.append(r.read()),
        )
    assert resp is not None and resp.status_code == 200 and got == [b"data"]
    sent = second.calls.last.request.headers
    assert sent["authorization"] == "Bearer secret" and sent["accept"] == "application/json"


def test_resend_headers_follows_a_303_with_a_get_and_stops_at_a_loop() -> None:
    with respx.mock() as router:
        router.post("https://a.example/p").respond(303, headers={"Location": "https://b.example/g"})
        done = router.get("https://b.example/g").respond(200, content=b"x")
        resp = http.request("POST", "https://a.example/p", content=b"body", resend_headers=True)
    assert resp is not None and resp.status_code == 200 and done.call_count == 1
    with respx.mock() as router:
        router.get("https://a.example/loop").respond(302, headers={"Location": "/loop"})
        resp = http.request("GET", "https://a.example/loop", resend_headers=True, max_tries=1)
    assert resp is not None and resp.status_code == 302  # 20 hops, then the redirect is the answer


def test_batch_query_aligns_results() -> None:
    with respx.mock() as router:
        router.get("https://example.org/1").mock(return_value=httpx.Response(200, json=1))
        router.get("https://example.org/2").mock(return_value=httpx.Response(404))
        out = http.batch_query(
            ["https://example.org/1", "not a url", "https://example.org/2"], msg=None
        )
    assert [r.status_code if r is not None else None for r in out] == [200, None, 404]


def _json_response(body: bytes, content_type: str | None) -> httpx.Response:
    headers = {} if content_type is None else {"Content-Type": content_type}
    request = httpx.Request("GET", "https://api.example.org/x")
    return httpx.Response(200, content=body, headers=headers, request=request)


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        "Application/JSON",  # media types are case-insensitive (httr2's check is not: U152)
        "APPLICATION/JSON;CHARSET=UTF-8",
        "application/vnd.api+json",
        "application/problem+JSON",
    ],
)
def test_resp_json_accepts_json_content_types(content_type: str) -> None:
    resp = _json_response(b'{"data": [1, "\\u00e9"]}', content_type)
    assert http.resp_json(resp) == {"data": [1, "\u00e9"]}


@pytest.mark.parametrize("content_type", ["text/html", "text/plain", "application/xml", None, ""])
def test_resp_json_refuses_other_content_types(content_type: str | None) -> None:
    resp = _json_response(b'{"data": 1}', content_type)
    with pytest.raises(ValueError, match="content type"):
        http.resp_json(resp)
    assert http.resp_json(resp, check_type=False) == {"data": 1}


def test_resp_json_parses_with_the_shared_parser() -> None:
    body = b'\xef\xbb\xbf{"a": "\\ud800", "n": 18446744073709551616}'
    assert http.resp_json(_json_response(body, "application/json")) == {
        "a": "\ufffd",
        "n": 1.8446744073709552e19,
    }


def test_resp_json_errors() -> None:
    with pytest.raises(ValueError, match="empty body"):
        http.resp_json(_json_response(b"", "application/json"))
    with pytest.raises(ValueError):
        http.resp_json(_json_response(b"<html></html>", "application/json"))
    # a response built without a request still gets a clear message
    with pytest.raises(ValueError, match="got content type 'text/html'"):
        http.resp_json(httpx.Response(200, content=b"{}", headers={"Content-Type": "text/html"}))


def test_resp_json_of_a_mocked_request() -> None:
    with respx.mock() as router:
        router.get("https://example.org/j").mock(return_value=httpx.Response(200, json={"ok": 1}))
        resp = http.request("GET", "https://example.org/j")
    assert resp is not None and http.resp_json(resp) == {"ok": 1}
