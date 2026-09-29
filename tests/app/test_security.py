"""Host, token, cookie, redirect, health and remote-file guards."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from pytacheck.app.security import DENIED_PAGE, TokenGuard, cookie_name

PORT = 4321
TOKEN = "s3cret-token"
HOST = f"127.0.0.1:{PORT}"


async def page(request: Request) -> PlainTextResponse:
    return PlainTextResponse("inner " + request.url.path)


async def socket(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_text("hi")
    await ws.close()


@pytest.fixture
def client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/", page),
            Route("/gradio_api/file={rest:path}", page),
            WebSocketRoute("/ws", socket),
        ]
    )
    app.add_middleware(TokenGuard, port=PORT, token=TOKEN)
    return TestClient(app, base_url=f"http://{HOST}", follow_redirects=False)


def test_no_token_gets_the_plain_403_page(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 403
    assert resp.text == DENIED_PAGE
    assert "Open metacheck from the link that the metacheck-app command printed." in resp.text


def test_token_sets_a_strict_http_only_cookie_and_redirects(client: TestClient) -> None:
    resp = client.get(f"/?token={TOKEN}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{cookie_name(PORT)}={TOKEN}")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_redirect_keeps_other_query_parameters(client: TestClient) -> None:
    resp = client.get(f"/?a=1&token={TOKEN}&b=2")
    assert resp.headers["location"] == "/?a=1&b=2"


def test_cookie_passes(client: TestClient) -> None:
    client.get(f"/?token={TOKEN}")
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text == "inner /"


def test_wrong_token_and_wrong_cookie_are_refused(client: TestClient) -> None:
    assert client.get("/?token=nope").status_code == 403
    assert client.get("/", headers={"cookie": f"{cookie_name(PORT)}=nope"}).status_code == 403
    assert (
        client.get("/", headers={"cookie": f"{cookie_name(PORT + 1)}={TOKEN}"}).status_code == 403
    )


def test_host_must_match_exactly(client: TestClient) -> None:
    good = {"cookie": f"{cookie_name(PORT)}={TOKEN}"}
    assert client.get("/", headers={**good, "host": f"localhost:{PORT}"}).status_code == 200
    for host in (
        "evil.example",
        f"evil.example:{PORT}",
        "127.0.0.1",
        f"127.0.0.1:{PORT + 1}",
        f"127.0.0.1.evil.example:{PORT}",
    ):
        assert client.get("/", headers={**good, "host": host}).status_code == 403, host
        assert client.get(f"/?token={TOKEN}", headers={"host": host}).status_code == 403, host


def test_healthz_needs_no_token_but_still_checks_the_host(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert client.get("/healthz", headers={"host": "evil.example"}).status_code == 403


def test_remote_file_urls_get_404(client: TestClient) -> None:
    client.get(f"/?token={TOKEN}")
    assert client.get("/gradio_api/file=https://example.com/x").status_code == 404
    assert client.get("/gradio_api/file=http://example.com/x").status_code == 404
    assert client.get("/gradio_api/file=%2F%2Fexample.com/x").status_code == 404
    assert client.get("/gradio_api/file=/tmp/x.txt").status_code == 200


def test_websocket_needs_the_cookie(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws", headers={"host": HOST}),
    ):
        pass
    headers = {"host": HOST, "cookie": f"{cookie_name(PORT)}={TOKEN}"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        assert ws.receive_text() == "hi"
