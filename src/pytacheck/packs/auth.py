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

URLs that carry a credential themselves (a password or user info, a secret
query parameter such as ``?private_token=``, see :func:`has_credentials`) are
refused wherever they would be written (store URLs, pack sources); one read
from an older config is shown redacted and recorded without it
(:func:`strip_credentials`).

A store index on another host that needs a login reads it from ``~/.netrc``
(or the file ``$NETRC`` names): only an explicit ``machine`` entry for the
exact host, only over https (or http to this machine), only to the origin
asked (see :func:`netrc_login`).

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from pytacheck.packs.manifest import PackError

if TYPE_CHECKING:
    import httpx

__all__ = [
    "AUTH_HELP",
    "NETRC_HELP",
    "SECRET_PARAMS",
    "TOKEN_HOSTS",
    "TOKEN_VARS",
    "DownloadError",
    "Fetched",
    "clean_source",
    "credentials_help",
    "get",
    "github_token",
    "has_credentials",
    "may_send_token",
    "netrc_login",
    "redact",
    "strip_credentials",
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
NETRC_HELP = (
    "put the login in ~/.netrc (a line `machine HOST login USER password SECRET`; "
    "pytacheck reads it for store indexes on hosts other than GitHub)"
)
MAX_REDIRECTS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_UNSAFE_URL = re.compile(r"[\s\\\x00-\x1f\x7f]")
#: query (or fragment) parameters whose value is a credential
SECRET_PARAMS = frozenset(
    {"access_token", "token", "private_token", "x-amz-security-token", "sig", "signature"}
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&;#](?:access_token|token|private_token|x-amz-security-token|sig|signature)=)"
    r"[^&;#\s'\"<>]+"
)
_HTTP_USERINFO = re.compile(r"(?i)\b(https?://)[^/@\s'\"<>]+@")
_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/:@\s'\"<>]+):[^/@\s'\"<>]+@")
#: scp-like git addresses with a password: ``user:secret@host:owner/repo``
_SCP_PASSWORD = re.compile(r"(?<![\w.:/@-])([\w.-]+):[^/@\s'\"<>:]+@([\w.-]+:)")
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
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
    out = _PASSWORD.sub(r"\1:***@", out)
    return _SCP_PASSWORD.sub(r"\1:***@\2", out)


def _has_secret_param(text: str) -> bool:
    for pair in re.split(r"[&;]", text):
        key, sep, value = pair.partition("=")
        if sep and value and unquote_plus(key).strip().lower() in SECRET_PARAMS:
            return True
    return False


def _drop_secret_params(text: str) -> str:
    return "&".join(
        pair
        for pair in re.split(r"[&;]", text)
        if pair and unquote_plus(pair.partition("=")[0]).strip().lower() not in SECRET_PARAMS
    )


def has_credentials(url: object) -> bool:
    """Whether *url* carries a credential: a password, user info in an http(s) URL
    (``https://TOKEN@host``), a secret query or fragment parameter (``token``,
    ``access_token``, ``private_token``, ``sig``, ...), or a token value from the
    environment.

    A user name alone in an ssh or scp-like URL (``ssh://git@host``,
    ``git@host:owner/repo``) is not a credential.
    """
    if not isinstance(url, str):
        return False
    text = url.strip()
    if any(secret in text for secret in _secrets()):
        return True
    text = text.removeprefix("git+")
    if "://" not in text:
        return bool(re.match(r"^[\w.-]+:[^/@\s:]*@[\w.-]+:", text))
    try:
        parts = urlsplit(text)
        password = parts.password
    except ValueError:
        return redact(text) != text  # unparseable: whatever looks like a secret counts
    if password:
        return True
    if parts.scheme.lower() in ("http", "https") and "@" in parts.netloc:
        return True
    return _has_secret_param(parts.query) or _has_secret_param(parts.fragment)


def strip_credentials(url: str) -> str:
    """*url* without its credentials (see :func:`has_credentials`).

    http(s) URLs lose their user info; other schemes keep the user name
    (``ssh://git@host``) and lose the password; secret query and fragment
    parameters are dropped; token values from the environment become ``***``.
    """
    text = str(url)
    if "://" in text:
        try:
            parts = urlsplit(text)
            netloc = parts.netloc
            if "@" in netloc:
                userinfo, _, hostport = netloc.rpartition("@")
                if parts.scheme.lower().removeprefix("git+") in ("http", "https"):
                    netloc = hostport
                elif parts.password is not None:
                    netloc = f"{userinfo.partition(':')[0]}@{hostport}"
            text = urlunsplit(
                (
                    parts.scheme,
                    netloc,
                    parts.path,
                    _drop_secret_params(parts.query),
                    _drop_secret_params(parts.fragment),
                )
            )
        except ValueError:
            pass
    else:
        m = re.match(r"^([\w.-]+):[^/@\s:]*@([\w.-]+:.*)$", text, re.DOTALL)
        if m:
            text = f"{m.group(1)}@{m.group(2)}"
    return redact(text)


def clean_source(source: object) -> dict[str, Any]:
    """A pack source (``{"git": url, ...}``) with :func:`strip_credentials` applied to its URLs."""
    if not isinstance(source, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in source.items():
        if isinstance(value, str) and (key == "git" or "://" in value or "@" in value):
            value = strip_credentials(value)
        out[key] = value
    return out


def _github_host(url: str) -> bool:
    try:
        host = (urlsplit(url.removeprefix("git+")).hostname or "").lower()
    except ValueError:
        return False
    return host in TOKEN_HOSTS or host == "www.github.com"


def credentials_help(url: str, *, index: bool = False) -> str:
    """How to give access to *url* without putting a credential in it."""
    if _github_host(url):
        return (
            "set PYTACHECK_GITHUB_TOKEN (or GH_TOKEN / GITHUB_TOKEN) or configure git "
            "credentials for github.com (`gh auth setup-git`)"
        )
    if index:
        return f"{NETRC_HELP}, or configure git credentials for a GitLab store"
    return "configure git credentials for the host (a git credential helper, or ~/.netrc)"


def netrc_login(url: str) -> tuple[str, str] | None:
    """``(login, password)`` for *url* from ``~/.netrc`` (or ``$NETRC``), if it may be sent.

    Only for ``https://`` URLs (or ``http://`` to this machine), never GitHub's
    hosts (they use a token), never a URL with user info, and only an explicit
    ``machine`` entry for exactly that host (a ``default`` entry is ignored).
    """
    import netrc

    import httpx

    if not isinstance(url, str) or _UNSAFE_URL.search(url):
        return None
    try:
        parts = urlsplit(url)
        parsed = httpx.URL(url)
    except (ValueError, httpx.InvalidURL):
        return None
    host = (parts.hostname or "").lower()
    if not host or "@" in parts.netloc or parsed.userinfo or host in TOKEN_HOSTS:
        return None
    if not (parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK)):
        return None
    path = os.environ.get("NETRC", "").strip()
    file = os.path.expanduser(path) if path else None
    if file is None and not os.path.isfile(os.path.expanduser("~/.netrc")):
        return None
    try:
        rc = netrc.netrc(file)
    except FileNotFoundError:
        return None
    except (OSError, netrc.NetrcParseError):
        import warnings

        # the parser's message can quote the file's contents: never shown
        warnings.warn(
            f"{path or '~/.netrc'} cannot be read (malformed, or readable by other users); "
            "its logins are not used",
            stacklevel=3,
        )
        return None
    entry = rc.hosts.get(host)
    if not entry:
        return None
    login, _account, password = entry
    if not login and not password:
        return None
    return login or "", password or ""


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
    answer is then from the retry without it). ``login`` says a ``~/.netrc``
    login was sent.
    """

    url: str
    status: int
    content: bytes = field(default=b"", repr=False)
    token: bool = False
    rejected: bool = False
    rate_limited: bool = False
    login: bool = False

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
        if self.login and self.status in (401, 403):
            text += " (the server rejected the ~/.netrc login for its host)"
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
    authorization: str | None,
    allowed: Callable[[str], bool],
    *,
    accept: str | None,
    limit: int,
    tries: int,
    timeout: float,
) -> tuple[int, bytes, bool]:
    """Follow redirects by hand; *authorization* goes only to *url*'s own origin.

    It is sent to a hop only when that hop has the same scheme, host and port
    as *url* and *allowed* accepts it. A redirect to a URL with user info is
    refused (it would carry a credential of its own).
    """
    import httpx

    origin = _origin(url)
    hop = url
    for _ in range(MAX_REDIRECTS + 1):
        headers = {"Accept": accept} if accept else {}
        if authorization is not None and _origin(hop) == origin and allowed(hop):
            headers["Authorization"] = authorization
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
        if nxt.userinfo:
            raise DownloadError(f"Downloading {url} failed: a redirect to a URL with credentials")
        hop = str(nxt)
    raise DownloadError(f"Downloading {url} failed: too many redirects")


def _basic(login: str, password: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode("ascii")


def get(
    url: str,
    *,
    token: bool = False,
    netrc: bool = False,
    accept: str | None = None,
    limit: int = 100 * 1024 * 1024,
    tries: int = 2,
    timeout: float = 60.0,
) -> Fetched:
    """GET *url*, following redirects by hand; with ``token=True`` a GitHub token may be sent.

    The token (see :func:`github_token`) is sent only when :func:`may_send_token`
    allows *url*, and only to that origin: redirects elsewhere are followed
    without it. A 401 to a request with a token is retried once without it.
    With ``netrc=True`` a ``~/.netrc`` login (see :func:`netrc_login`) is sent
    to a host that is not GitHub's, again only to that origin.
    Error statuses are returned, not raised; a connection failure raises
    :class:`DownloadError` and a body over *limit* bytes :class:`PackError`.
    """
    opts: dict[str, Any] = {"accept": accept, "limit": limit, "tries": tries, "timeout": timeout}
    secret = github_token() if token and may_send_token(url) else None
    login = netrc_login(url) if netrc and secret is None else None
    if login is not None:

        def same_login(hop: str) -> bool:
            return netrc_login(hop) == login

        status, body, limited = _get(url, _basic(*login), same_login, **opts)
        return Fetched(url, status, body, rate_limited=limited, login=True)
    bearer = f"Bearer {secret}" if secret is not None else None
    status, body, limited = _get(url, bearer, may_send_token, **opts)
    if secret is None or status != 401:
        return Fetched(url, status, body, token=secret is not None, rate_limited=limited)
    status, body, limited = _get(url, None, may_send_token, **opts)
    return Fetched(url, status, body, rejected=True, rate_limited=limited)
