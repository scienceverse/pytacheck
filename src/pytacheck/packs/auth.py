"""GitHub credentials for stores and packs: which token, which hosts, and requests carrying it.

A private store (or pack repository) on GitHub is read with a token from the
environment: ``PYTACHECK_GITHUB_TOKEN``, then ``GH_TOKEN``, then
``GITHUB_TOKEN`` (the first one that is set and not empty). The token

* goes only as ``Authorization: Bearer`` over https to ``api.github.com``,
  ``github.com``, ``raw.githubusercontent.com`` and ``codeload.github.com``
  (the exact host, the default port, no user info in the URL), and only to the
  origin the request was sent to: a redirect to another host is followed
  without it. (GitHub's tarball endpoint redirects to a short-lived
  ``codeload.github.com/...?token=...`` URL, which carries its own credential.)
* is dropped for one retry when GitHub answers 401, so an expired token never
  breaks a public store;
* is never written anywhere: not in pins, install records, run records, index
  caches, warnings or errors. Neither is a redirect URL: errors name the URL
  that was asked for, and :func:`redact` scrubs token values, ``token=`` query
  values and URL passwords from the log lines of httpx/httpcore and from git's
  messages.

These requests use their own HTTP/1.1 client with redirects followed by hand
(so the rule above is ours, not a library default), and no HTTP/2 header
compression debug logging ever sees a header.
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pytacheck.packs.manifest import PackError

if TYPE_CHECKING:
    import httpx

__all__ = [
    "AUTH_HELP",
    "TOKEN_HOSTS",
    "TOKEN_VARS",
    "DownloadError",
    "Fetched",
    "get",
    "github_token",
    "may_send_token",
    "redact",
    "token_var",
]

#: environment variables read for a GitHub token, in order
TOKEN_VARS = ("PYTACHECK_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
#: the only hosts a token is ever sent to (https, default port, exact match)
TOKEN_HOSTS = frozenset(
    {"api.github.com", "github.com", "raw.githubusercontent.com", "codeload.github.com"}
)
AUTH_HELP = (
    "If the repository is private, give pytacheck read access: set PYTACHECK_GITHUB_TOKEN "
    "(or GH_TOKEN / GITHUB_TOKEN) to a GitHub token that can read it (for example "
    "`export PYTACHECK_GITHUB_TOKEN=$(gh auth token)`), or configure git credentials for "
    "github.com (for example `gh auth setup-git`)"
)
MAX_REDIRECTS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_UNSAFE_URL = re.compile(r"[\s\\\x00-\x1f\x7f]")
_QUERY_SECRET = re.compile(
    r"(?i)([?&;](?:access_token|token|private_token|x-amz-security-token|sig|signature)=)"
    r"[^&#\s'\"<>]+"
)
_HTTP_USERINFO = re.compile(r"(?i)\b(https?://)[^/@\s'\"<>]+@")
_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/:@\s'\"<>]+):[^/@\s'\"<>]+@")
#: loggers of the HTTP stack whose records can carry a redirect URL
_LOGGERS = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)

_client: httpx.Client | None = None
_lock = threading.Lock()


def token_var() -> str | None:
    """The name of the variable a GitHub token is read from (never its value), if any."""
    for var in TOKEN_VARS:
        if os.environ.get(var, "").strip():
            return var
    return None


def github_token() -> str | None:
    """The GitHub token: ``PYTACHECK_GITHUB_TOKEN``, ``GH_TOKEN`` or ``GITHUB_TOKEN``."""
    var = token_var()
    return os.environ[var].strip() if var else None


def may_send_token(url: str) -> bool:
    """Whether a GitHub token may be sent to *url*.

    Only ``https://`` URLs whose host is exactly one of :data:`TOKEN_HOSTS`,
    with no port, no user info and nothing odd (backslashes, whitespace,
    control characters) that two URL parsers could read differently.
    """
    import httpx

    if not isinstance(url, str) or _UNSAFE_URL.search(url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
        parsed = httpx.URL(url)
    except (ValueError, httpx.InvalidURL):
        return False
    host = parts.hostname or ""
    return (
        parts.scheme == "https"
        and parsed.scheme == "https"
        and parts.netloc.lower() == host  # no user info, no port, no trailing dot
        and port is None
        and parsed.port is None
        and not parsed.userinfo
        and host in TOKEN_HOSTS
        and parsed.host == host
    )


def _secrets() -> list[str]:
    values = {os.environ.get(var, "").strip() for var in TOKEN_VARS}
    return sorted((v for v in values if len(v) >= 4), key=len, reverse=True)


def redact(text: object) -> str:
    """*text* without token values, secret query values (``token=``) or URL passwords."""
    out = str(text)
    for secret in _secrets():
        out = out.replace(secret, "***")
    out = _QUERY_SECRET.sub(r"\1***", out)
    out = _HTTP_USERINFO.sub(r"\1***@", out)
    return _PASSWORD.sub(r"\1:***@", out)


class DownloadError(PackError):
    """A source could not be downloaded (network or HTTP error): git may still work.

    ``status`` is the last HTTP status (``None`` for a connection failure).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _Redact(logging.Filter):
    """Scrubs secrets from the HTTP stack's log records (a redirect URL's ``?token=``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a broken record is logging's problem
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


_FILTER = _Redact()


def _install_log_filter() -> None:
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)


def _http() -> httpx.Client:
    """The HTTP/1.1 client for GitHub requests (redirects are followed by hand)."""
    global _client
    with _lock:
        if _client is None or _client.is_closed:
            import httpx

            from pytacheck.http import _user_agent

            _install_log_filter()
            _client = httpx.Client(
                http2=False,
                follow_redirects=False,
                timeout=httpx.Timeout(60.0, connect=20.0),
                headers={"User-Agent": _user_agent()},
            )
        return _client


@dataclass(frozen=True)
class Fetched:
    """The answer to :func:`get`: status and body of the URL that was asked for.

    ``url`` is always that URL, never a redirect target. ``token`` says a token
    was sent and not rejected; ``rejected`` that GitHub answered 401 to it (the
    answer is then from the retry without it).
    """

    url: str
    status: int
    content: bytes = field(default=b"", repr=False)
    token: bool = False
    rejected: bool = False
    rate_limited: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    @property
    def denied(self) -> bool:
        """401, 403 or 404: what a private repository answers without credentials."""
        return self.status in (401, 403, 404)

    def problem(self) -> str:
        """``"HTTP 404"``, with why when it is known (a rejected token, a rate limit)."""
        text = f"HTTP {self.status}"
        if self.rate_limited:
            text += " (GitHub's rate limit; a token raises it)"
        if self.rejected:
            text += f" (GitHub rejected the token in {token_var() or 'the environment'})"
        return text


def _origin(url: str) -> tuple[str, str, int | None]:
    import httpx

    u = httpx.URL(url)
    default = {"https": 443, "http": 80}.get(u.scheme)
    return u.scheme, u.host, u.port or default


def _send(
    hop: str, headers: dict[str, str], *, shown: str, limit: int, tries: int, timeout: float
) -> tuple[int, str | None, bytes, bool]:
    """GET one hop: ``(status, location, body, rate_limited)``; the body only for 200.

    Retries connection failures and 429/5xx answers (*tries* in all). Errors
    name *shown* (the URL asked for), never *hop* (perhaps a redirect URL).
    """
    import httpx

    from pytacheck.http import RETRY_STATUSES, sleep

    last = ""
    status = 0
    for attempt in range(1, tries + 1):
        if attempt > 1:
            sleep(float(attempt))
        try:
            with _http().stream("GET", hop, headers=headers, timeout=timeout) as resp:
                status = resp.status_code
                if status in _REDIRECTS:
                    return status, resp.headers.get("Location"), b"", False
                if status != 200:
                    limited = resp.headers.get("X-RateLimit-Remaining") == "0"
                    if status in RETRY_STATUSES and attempt < tries and not limited:
                        continue
                    return status, None, b"", limited
                try:
                    size = int(resp.headers.get("Content-Length") or 0)
                except ValueError:
                    size = 0
                if size > limit:
                    raise PackError(f"{shown} is too large ({size} bytes; limit {limit})")
                buf = io.BytesIO()
                for chunk in resp.iter_bytes():
                    buf.write(chunk)
                    if buf.tell() > limit:
                        raise PackError(f"{shown} is too large (limit {limit} bytes)")
                return 200, None, buf.getvalue(), False
        except httpx.HTTPError as exc:
            last = redact(str(exc) or type(exc).__name__)
    if status:
        return status, None, b"", False
    raise DownloadError(f"Downloading {shown} failed: {last}")


def _get(
    url: str,
    secret: str | None,
    *,
    accept: str | None,
    limit: int,
    tries: int,
    timeout: float,
) -> tuple[int, bytes, bool]:
    """Follow redirects by hand; the token goes only to *url*'s own (allowed) origin."""
    import httpx

    origin = _origin(url)
    hop = url
    for _ in range(MAX_REDIRECTS + 1):
        headers = {"Accept": accept} if accept else {}
        if secret is not None and _origin(hop) == origin and may_send_token(hop):
            headers["Authorization"] = f"Bearer {secret}"
        status, location, body, limited = _send(
            hop, headers, shown=url, limit=limit, tries=tries, timeout=timeout
        )
        if status not in _REDIRECTS:
            return status, body, limited
        if not location:
            return status, b"", False
        try:
            nxt = httpx.URL(hop).join(location)
        except (httpx.InvalidURL, ValueError):
            raise DownloadError(f"Downloading {url} failed: an invalid redirect") from None
        if nxt.scheme not in ("http", "https"):
            raise DownloadError(f"Downloading {url} failed: a redirect to a non-HTTP URL")
        hop = str(nxt)
    raise DownloadError(f"Downloading {url} failed: too many redirects")


def get(
    url: str,
    *,
    token: bool = False,
    accept: str | None = None,
    limit: int = 100 * 1024 * 1024,
    tries: int = 2,
    timeout: float = 60.0,
) -> Fetched:
    """GET *url*, following redirects by hand; with ``token=True`` a GitHub token may be sent.

    The token (see :func:`github_token`) is sent only when :func:`may_send_token`
    allows *url*, and only to that origin: redirects elsewhere are followed
    without it. A 401 to a request with a token is retried once without it.
    Error statuses are returned, not raised; a connection failure raises
    :class:`DownloadError` and a body over *limit* bytes :class:`PackError`.
    """
    secret = github_token() if token and may_send_token(url) else None
    status, body, limited = _get(
        url, secret, accept=accept, limit=limit, tries=tries, timeout=timeout
    )
    if secret is None or status != 401:
        return Fetched(url, status, body, token=secret is not None, rate_limited=limited)
    status, body, limited = _get(
        url, None, accept=accept, limit=limit, tries=tries, timeout=timeout
    )
    return Fetched(url, status, body, rejected=True, rate_limited=limited)
