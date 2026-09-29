"""Guards for the local server: Host check, per-launch token, remote-file block.

A pure ASGI middleware, so it also covers websockets and Gradio's own routes.
"""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, quote, urlencode

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["DENIED_PAGE", "TokenGuard", "cookie_name"]

DENIED_PAGE = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>metacheck</title>"
    "<body style='font-family:sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
    "<p>Open metacheck from the link that the metacheck-app command printed.</p></body></html>"
)
HEALTH_PATH = "/healthz"
#: Gradio fetches a remote URL when asked for ``/gradio_api/file=<url>``
FILE_ROUTE = "/gradio_api/file="


def cookie_name(port: int) -> str:
    """Cookies are shared between ports on one host, so each port gets its own name."""
    return f"metacheck_{port}"


class TokenGuard:
    def __init__(self, app: ASGIApp, *, port: int, token: str) -> None:
        self.app = app
        self.token = token
        self.cookie = cookie_name(port)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if headers.get("host", "").lower() not in self.hosts:
            await self._deny(scope, receive, send)
            return
        path = scope["path"]
        if scope["type"] == "http" and path == HEALTH_PATH:
            await JSONResponse({"ok": True})(scope, receive, send)
            return
        query = parse_qsl(scope["query_string"].decode("latin-1"), keep_blank_values=True)
        given = next((v for k, v in query if k == "token"), None)
        if given is not None and self._same(given):
            if scope["type"] == "http":
                await self._redirect(scope, query)(scope, receive, send)
            else:
                await self._deny(scope, receive, send)
            return
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(headers.get("cookie", ""))
        except Exception:
            jar = SimpleCookie()
        morsel = jar.get(self.cookie)
        if morsel is None or not self._same(morsel.value):
            await self._deny(scope, receive, send)
            return
        tail = path.split(FILE_ROUTE, 1)[1] if FILE_ROUTE in path else ""
        if tail and ("http:" in tail or "https:" in tail or "//" in tail):
            await self._not_found(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _same(self, value: str) -> bool:
        return secrets.compare_digest(value.encode("utf-8"), self.token.encode("utf-8"))

    def _redirect(self, scope: Scope, query: list[tuple[str, str]]) -> Response:
        rest = urlencode([(k, v) for k, v in query if k != "token"])
        target = quote(scope["path"]) + (f"?{rest}" if rest else "")
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            self.cookie, self.token, httponly=True, samesite="strict", path="/", secure=False
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def _deny(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._refuse(scope, receive, send, HTMLResponse(DENIED_PAGE, status_code=403))

    async def _not_found(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._refuse(scope, receive, send, JSONResponse({"detail": "Not found"}, 404))

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, response: Response) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await response(scope, receive, send)
