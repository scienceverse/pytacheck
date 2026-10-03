"""Guards for the local server: Host check, per-launch token, remote-file block.

A pure ASGI middleware, so it also covers websockets and Gradio's own routes.

Browsers send a cookie for 127.0.0.1 to every port, so another program on this computer
that a person visits in the same browser can see the token cookie. Nothing in a plain-http
cookie prevents that. The guard therefore also refuses requests that another local page
starts (``Origin`` and ``Sec-Fetch-Site``), and the app only reads files that were
uploaded through the page. The one exception is the local app's "Check a data package"
page, which reads the folder that the person types (see ``package.py``); the guard above
is what keeps another page or program from asking for it. A shared server has no such page.
"""

from __future__ import annotations

import html
import secrets
from collections.abc import Sequence
from urllib.parse import parse_qsl, quote, urlencode

from starlette.requests import cookie_parser
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = [
    "DENIED_PAGE",
    "HOSTED_COOKIE",
    "HOSTED_DENIED_PAGE",
    "PROXY_DENIED_PAGE",
    "TokenGuard",
    "cookie_name",
]

DENIED_PAGE = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>metacheck</title>"
    "<body style='font-family:sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
    "<p>Open metacheck from the link that the metacheck-app command printed.</p></body></html>"
)
HOSTED_DENIED_PAGE = DENIED_PAGE.replace(
    "from the link that the metacheck-app command printed", "from the link you were sent"
)
PROXY_DENIED_PAGE = DENIED_PAGE.replace(
    "Open metacheck from the link that the metacheck-app command printed.",
    "Sign in first, then open metacheck again.",
)
#: The ``__Host-`` prefix makes a browser refuse the cookie unless it is Secure, has no
#: Domain and covers the whole site.
HOSTED_COOKIE = "__Host-metacheck"
HEALTH_PATH = "/healthz"
#: Gradio fetches a remote URL when asked for ``/gradio_api/file=<url>`` or, on its
#: deprecated route, ``/gradio_api/file/<url>``
FILE_ROUTES = ("/gradio_api/file=", "/gradio_api/file/")
#: ``Sec-Fetch-Site`` values of a request that another page starts
FOREIGN_SITES = ("same-site", "cross-site")


def cookie_name(port: int) -> str:
    """Cookies are shared between ports on one host, so each port gets its own name."""
    return f"metacheck_{port}"


class TokenGuard:
    """Guards for the local app (one ``token``, ``127.0.0.1``) or the hosted one.

    The hosted app passes ``hosts`` (names that are served over https), several
    ``tokens`` and ``secure=True``. A person gets a cookie with the token that they used.
    Behind a sign-in proxy it passes ``proxy_auth=True`` and no tokens: the proxy decides
    who gets in, and ``user_header``, when given, must be on every request.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        port: int = 0,
        token: str | None = None,
        tokens: Sequence[str] = (),
        hosts: Sequence[str] | None = None,
        secure: bool = False,
        proxy_auth: bool = False,
        user_header: str | None = None,
    ) -> None:
        self.app = app
        self.tokens = [*tokens, *([token] if token else [])]
        if proxy_auth and (self.tokens or hosts is None):
            raise ValueError("proxy_auth takes hosts and no tokens")
        if not self.tokens and not proxy_auth:
            raise ValueError("TokenGuard needs at least one token")
        self.proxy_auth = proxy_auth
        self.user_header = user_header.lower() if user_header else None
        self.secure = secure
        self.hosted = hosts is not None
        self.cookie = HOSTED_COOKIE if secure else cookie_name(port)
        if hosts is None:
            self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            self.origins = {f"http://{host}" for host in self.hosts}
            self.denied = DENIED_PAGE
        else:
            self.hosts = {host.lower() for host in hosts}
            self.origins = {f"https://{host}" for host in self.hosts}
            self.denied = PROXY_DENIED_PAGE if proxy_auth else HOSTED_DENIED_PAGE

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        path = scope["path"]
        health = scope["type"] == "http" and path == HEALTH_PATH
        # A hosted health probe comes from the platform, under any Host. It answers a
        # fixed {"ok": true} and reads nothing.
        if health and self.hosted:
            await JSONResponse({"ok": True})(scope, receive, send)
            return
        if headers.get("host", "").lower() not in self.hosts:
            await self._deny(scope, receive, send)
            return
        if health:
            await JSONResponse({"ok": True})(scope, receive, send)
            return
        if self._foreign(scope, headers):
            await self._deny(scope, receive, send)
            return
        if self.proxy_auth:
            if self.user_header and not headers.get(self.user_header, "").strip():
                await self._deny(scope, receive, send)
            elif self._remote_file(path):
                await self._not_found(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        query = parse_qsl(scope["query_string"].decode("latin-1"), keep_blank_values=True)
        given = next((v for k, v in query if k == "token"), None)
        if given is not None and self._same(given):
            if scope["type"] == "http":
                await self._redirect(scope, query, headers, given)(scope, receive, send)
            else:
                await self._deny(scope, receive, send)
            return
        # lenient, like Starlette's own: another program's cookie must not lock us out
        value = cookie_parser(headers.get("cookie", "")).get(self.cookie)
        if value is None or not self._same(value):
            await self._deny(scope, receive, send)
            return
        if self._remote_file(path):
            await self._not_found(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _foreign(self, scope: Scope, headers: dict[str, str]) -> bool:
        """True for a request that a page on another origin started."""
        origin = headers.get("origin")
        if origin is not None and origin.lower() not in self.origins:
            return True
        if headers.get("sec-fetch-site") in FOREIGN_SITES:
            # A plain page load may come from elsewhere (the launcher's local redirect
            # page, or a link). The page cannot read the answer.
            page_load = (
                scope["type"] == "http"
                and scope["method"] in ("GET", "HEAD")
                and headers.get("sec-fetch-mode") == "navigate"
                and headers.get("sec-fetch-dest") == "document"
            )
            return not page_load
        return False

    @staticmethod
    def _remote_file(path: str) -> bool:
        """True for a file route that is asked to fetch a URL."""
        lower = path.lower()
        for route in FILE_ROUTES:
            if route in lower:
                tail = lower.split(route, 1)[1]
                return "http:" in tail or "https:" in tail or "//" in tail
        return False

    def _same(self, value: str) -> bool:
        """Whether ``value`` is one of the tokens; every token is compared, in constant time."""
        raw = value.encode("utf-8")
        found = False
        for token in self.tokens:
            found |= secrets.compare_digest(raw, token.encode("utf-8"))
        return found

    def _redirect(
        self, scope: Scope, query: list[tuple[str, str]], headers: dict[str, str], token: str
    ) -> Response:
        rest = urlencode([(k, v) for k, v in query if k != "token"])
        # always a path on this site: "//host/" would send the browser to another site
        target = "/" + quote(scope["path"]).lstrip("/") + (f"?{rest}" if rest else "")
        response: Response
        if headers.get("sec-fetch-site") in FOREIGN_SITES:
            # A redirect keeps the site of the request that started it, so the browser
            # would not send the strict cookie on the next step. A page of ours starts it.
            page = (
                f"<!doctype html><meta charset=utf-8>"
                f'<meta http-equiv=refresh content="0;url={html.escape(target, quote=True)}">'
            )
            response = HTMLResponse(page)
        else:
            response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            self.cookie, token, httponly=True, samesite="strict", path="/", secure=self.secure
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def _deny(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._refuse(scope, receive, send, HTMLResponse(self.denied, status_code=403))

    async def _not_found(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._refuse(scope, receive, send, JSONResponse({"detail": "Not found"}, 404))

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, response: Response) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await response(scope, receive, send)
