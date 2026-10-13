"""Bounded fetches share successful in-flight requests, never failures."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import httpx
import pytest

from metacheck import http


def test_metadata_requests_share_in_flight_work():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def handle(request):
        calls.append(request.url)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={"value": 42})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client, http._request_session():
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(
                    copy_context().run, http.request, "GET", "https://example.org/data", http=client
                )
                for _ in range(4)
            ]
            try:
                assert entered.wait(5)
            finally:
                release.set()
            assert [future.result().json() for future in futures] == [{"value": 42}] * 4
        assert len(calls) == 1
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        http.request("GET", "https://example.org/data", http=client)
    assert len(calls) == 2


def test_failed_metadata_requests_can_retry_and_credentials_do_not_share():
    calls = []

    def handle(request):
        calls.append(request.headers.get("Authorization"))
        return httpx.Response(503 if len(calls) == 1 else 200, text=calls[-1])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client, http._request_session():
        args = {"http": client, "max_tries": 1, "headers": {"Authorization": "one"}}
        assert http.request("GET", "https://example.org/data", **args).status_code == 503
        assert http.request("GET", "https://example.org/data", **args).status_code == 200
        assert http.request("GET", "https://example.org/data", **args).text == "one"
        args["headers"] = {"Authorization": "two"}
        assert http.request("GET", "https://example.org/data", **args).text == "two"
    assert calls == ["one", "one", "two"]


def test_nested_pools_cannot_exceed_osf_host_limit():
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def handle(request):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                entered.set()
        try:
            assert release.wait(5)
            return httpx.Response(200)
        finally:
            with lock:
                active -= 1

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [
                pool.submit(http.request, "GET", f"https://api.osf.io/{i}", http=client)
                for i in range(12)
            ]
            try:
                assert entered.wait(5)
                assert maximum == 4
            finally:
                release.set()
            assert all(future.result().status_code == 200 for future in futures)
    assert maximum == 4


def test_invalid_json_is_not_reused():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            content=b"{" if len(calls) == 1 else b"{}",
            headers={"Content-Type": "application/json"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client, http._request_session():
        assert http.request("GET", "https://example.org/data", http=client).content == b"{"
        assert http.request("GET", "https://example.org/data", http=client).json() == {}
    assert len(calls) == 2


def test_waiting_for_a_host_slot_can_be_cancelled(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(http, "_host_slots", {"example.org": slots})
    attempts = []

    def cancelled():
        attempts.append(True)
        if len(attempts) > 1:
            raise RuntimeError("cancelled")

    def forbidden(request):
        raise AssertionError("A cancelled request must not be sent")

    with (
        httpx.Client(transport=httpx.MockTransport(forbidden)) as client,
        http.interruptible(cancelled),
        pytest.raises(RuntimeError, match="cancelled"),
    ):
        http.request("GET", "https://example.org/data", http=client)


@pytest.mark.parametrize("raise_errors", [False, True])
def test_queued_request_respects_a_new_rate_limit(monkeypatch, raise_errors):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(http, "_host_slots", {"example.org": slots})
    checks = []

    def ahead_of_us():
        checks.append(True)
        if len(checks) == 2:  # waiting for the occupied slot
            http._record_reset("example.org", http.time.time() + 10)
            slots.release()

    def forbidden(request):
        raise AssertionError("A queued request must not spend an exhausted quota")

    with (
        httpx.Client(transport=httpx.MockTransport(forbidden)) as client,
        http.interruptible(ahead_of_us),
        http.skip_on_api_limit(),
    ):
        if raise_errors:
            with pytest.raises(http.RateLimited):
                http.request("GET", "https://example.org/data", http=client, raise_errors=True)
        else:
            assert http.request("GET", "https://example.org/data", http=client) is None
