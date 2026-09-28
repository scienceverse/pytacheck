"""The API key: Bearer auth on every route but /health, and the ``serve`` bind check."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pytacheck.api import app as app_module
from pytacheck.api.app import ApiConfigError, api_key, create_app, is_loopback
from pytacheck.cli import main

KEY = "k" * 32
GUARDED = [
    "/paper/modules",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/no/such/route",
    "/healthz",  # /health is matched exactly
    "/health/x",
]


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PYTACHECK_API_KEY", KEY)
    return TestClient(create_app())


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_without_a_key_the_api_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTACHECK_API_KEY", raising=False)
    assert TestClient(create_app()).get("/paper/modules").status_code == 200


def test_health_needs_no_key(keyed: TestClient) -> None:
    assert keyed.get("/health").status_code == 200


def test_health_needs_no_key_under_a_path_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", KEY)
    c = TestClient(create_app(), root_path="/api")
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/paper/modules").status_code == 401


def test_startup_and_shutdown_do_not_need_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", KEY)
    with TestClient(create_app()) as c:  # runs the lifespan
        assert c.get("/health").status_code == 200
        assert c.get("/paper/modules").status_code == 401


@pytest.mark.parametrize("path", GUARDED)
def test_every_other_route_needs_the_key(keyed: TestClient, path: str) -> None:
    r = keyed.get(path)
    assert r.status_code == 401
    assert r.json() == {"error": "Missing or invalid API key. Send 'Authorization: Bearer <key>'."}
    assert r.headers["www-authenticate"] == "Bearer"
    assert keyed.get(path, headers=bearer(KEY)).status_code != 401


def test_uploads_are_refused_before_they_are_read(keyed: TestClient) -> None:
    # without the key the answer is 401, not the 400 for a missing file
    assert keyed.post("/paper/authors").status_code == 401
    assert keyed.post("/paper/authors", headers=bearer(KEY)).status_code == 400


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {'x' * 32}",  # same length, wrong key
        f"Bearer {KEY[:-1]}",  # a prefix of the key
        f"Bearer {KEY}x",
        "Bearer",
        "Bearer ",
        KEY,  # no scheme
        f"Basic {KEY}",
        f"Token {KEY}",
        "",
    ],
)
def test_wrong_credentials_are_refused(keyed: TestClient, header: str) -> None:
    assert keyed.get("/paper/modules", headers={"Authorization": header}).status_code == 401


def test_scheme_is_case_insensitive(keyed: TestClient) -> None:
    r = keyed.get("/paper/modules", headers={"Authorization": f"bearer {KEY}"})
    assert r.status_code == 200


def test_key_is_compared_in_constant_time(
    keyed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = app_module.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(app_module.hmac, "compare_digest", spy)
    keyed.get("/paper/modules", headers=bearer("y" * 32))
    assert calls == [(b"y" * 32, KEY.encode())]


def test_keys_are_never_logged(keyed: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    guess = "guess-" + "g" * 32
    with caplog.at_level(logging.DEBUG, logger="pytacheck.api"):
        keyed.get("/paper/modules", headers=bearer(guess))
        keyed.get("/paper/modules", headers=bearer(KEY))
    assert "Rejected GET '/paper/modules'" in caplog.text
    assert KEY not in caplog.text
    assert guess not in caplog.text


def test_a_newline_in_the_path_cannot_forge_a_log_line(
    keyed: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="pytacheck.api"):
        keyed.get("/x%0aINFO forged line")
    assert "\n" not in caplog.records[-1].getMessage()
    assert "Rejected GET" in caplog.text


def test_websockets_need_the_key_too(keyed: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as info, keyed.websocket_connect("/socket"):
        pass
    assert info.value.code == 1008


def test_a_short_key_stops_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", "k" * 31)
    with pytest.raises(ApiConfigError, match="at least 32"):
        create_app()
    monkeypatch.setenv("PYTACHECK_API_KEY", "k" * 32)
    assert api_key() == "k" * 32


def test_the_key_ignores_surrounding_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", f"  {KEY}\n")
    assert api_key() == KEY
    monkeypatch.setenv("PYTACHECK_API_KEY", "   ")
    assert api_key() is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.1.2.3", True),
        ("::1", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("::", False),
        ("", False),
        ("192.168.1.5", False),
        ("example.org", False),
    ],
)
def test_is_loopback(host: str, expected: bool) -> None:
    assert is_loopback(host) is expected


# --- pytacheck serve ---------------------------------------------------------


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Calls to ``uvicorn.run`` (nothing is started)."""
    uvicorn = pytest.importorskip("uvicorn")
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    monkeypatch.delenv("PYTACHECK_API_KEY", raising=False)
    return calls


def output(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr()
    return " ".join((out.out + out.err).split())  # the console wraps long lines


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_serve_on_loopback_needs_no_key(served: list[dict], host: str) -> None:
    assert main(["serve", "--host", host]) == 0
    assert [c["host"] for c in served] == [host]


def test_serve_defaults_to_loopback(served: list[dict]) -> None:
    assert main(["serve"]) == 0
    assert served[0]["host"] == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.org"])
def test_serve_refuses_a_public_host_without_a_key(
    served: list[dict], capsys: pytest.CaptureFixture[str], host: str
) -> None:
    assert main(["serve", "--host", host]) == 1
    assert served == []
    text = output(capsys)
    assert "PYTACHECK_API_KEY" in text
    assert "--behind-authenticating-proxy" in text


def test_serve_starts_on_a_public_host_with_a_key(
    served: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", KEY)
    assert main(["serve", "--host", "0.0.0.0"]) == 0
    assert served[0]["host"] == "0.0.0.0"


def test_serve_opt_out_allows_a_public_host_without_a_key(
    served: list[dict], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["serve", "--host", "0.0.0.0", "--behind-authenticating-proxy"]) == 0
    assert served[0]["host"] == "0.0.0.0"
    assert "without an API key" in output(capsys)


def test_serve_refuses_a_short_key_even_with_the_opt_out(
    served: list[dict], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", "short")
    assert main(["serve", "--behind-authenticating-proxy"]) == 1
    assert served == []
    assert "at least 32" in output(capsys)
