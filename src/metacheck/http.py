"""Shared HTTP layer (port of ``.batch_query()`` in ``R/utils.R`` and httr2 defaults).

Every network call in pytacheck goes through this module so that all
clients share:

* one pooled ``httpx.Client`` (HTTP/2, redirects followed, a descriptive
  User-Agent carrying the contact email set with :func:`metacheck.email`);
* httr2-style retries: up to 5 tries on 429/500/502/503/504 and on
  connection failures, jittered exponential backoff, ``Retry-After`` honoured;
* per-host rate-limit memory: a 429 (or any retried status that says the
  bucket is empty, ``RateLimit-Remaining: 0``) with ``RateLimit-Reset`` /
  ``X-RateLimit-Reset`` / ``Retry-After`` makes later requests to that host
  wait for the stated reset instead of rediscovering the limit; a wait of
  more than 5 s is announced;
* streamed bodies (``sink``): a download to disk goes through the same
  retries, and a connection that drops mid-body is a failed try;
* :func:`skip_on_api_limit`: give up immediately on a rate limit instead of
  waiting (metacheck's ``metacheck.skip_on_api_limit`` option);
* optional token-bucket throttling per host (:class:`Throttle`);
* :func:`resp_json`: a JSON body, parsed with :func:`metacheck._json.loads`.

Set ``PYTACHECK_NO_SLEEP=1`` to disable courtesy delays and backoff sleeps
(the test suite does).
"""

from __future__ import annotations

import contextlib
import email.utils
import math
import random
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from metacheck._env import env_get

if TYPE_CHECKING:
    import httpx

__all__ = [
    "RETRY_STATUSES",
    "RateLimited",
    "Throttle",
    "batch_query",
    "check_interrupt",
    "client",
    "client_for",
    "close_client",
    "host_reset_at",
    "interruptible",
    "request",
    "resp_json",
    "skip_on_api_limit",
    "sleep",
]

RETRY_STATUSES = (429, 500, 502, 503, 504)

_client: httpx.Client | None = None
_client_h1: httpx.Client | None = None
#: Hosts that reset HTTP/2 streams when several requests share one connection (seen on OSF with
#: 4 or more at once: instant ``ReadError``). Their requests use an HTTP/1.1 client instead.
_H1_HOSTS = ("osf.io",)
_client_lock = threading.Lock()
_skip_limit: ContextVar[bool] = ContextVar("pytacheck_skip_on_api_limit", default=False)
#: Called before each request, each downloaded chunk and during waits; it raises to end them.
#: Set per context by :func:`interruptible`, so an application can stop one running job.
_interrupt: ContextVar[Callable[[], None] | None] = ContextVar("pytacheck_interrupt", default=None)
_host_reset: dict[str, float] = {}
_reset_lock = threading.Lock()


@contextlib.contextmanager
def interruptible(check: Callable[[], None]) -> Iterator[None]:
    """Run a block whose requests and waits call *check*; *check* raises to end them.

    Threads started with ``copy_context()`` (as the library's own pools are) inherit it.
    """
    token = _interrupt.set(check)
    try:
        yield
    finally:
        _interrupt.reset(token)


def check_interrupt() -> None:
    """Raise what the enclosing :func:`interruptible` block's check raises (else nothing)."""
    check = _interrupt.get()
    if check is not None:
        check()


def sleep(seconds: float) -> None:
    """``Sys.sleep()`` that the test suite can switch off (``PYTACHECK_NO_SLEEP``).

    Inside :func:`interruptible` it wakes every quarter second to look for an interrupt.
    """
    if seconds > 0 and not env_get("NO_SLEEP"):
        if _interrupt.get() is None:
            time.sleep(seconds)
            return
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            check_interrupt()
            time.sleep(min(left, 0.25))
        check_interrupt()


def _user_agent() -> str:
    from metacheck._version import __version__
    from metacheck.config import email

    contact = email()
    mail = f"; mailto:{contact}" if contact else ""
    return f"pytacheck/{__version__} (+https://github.com/scienceverse/pytacheck{mail})"


def client() -> httpx.Client:
    """The shared, pooled HTTP client (created on first use)."""
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            import httpx

            _client = httpx.Client(
                http2=True,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, connect=20.0),
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                headers={"User-Agent": _user_agent()},
            )
        return _client


def client_for(url: str) -> httpx.Client:
    """The shared client for *url*: HTTP/1.1 for the hosts in ``_H1_HOSTS``, else :func:`client`."""
    global _client_h1
    host = urlsplit(url).hostname or ""
    if not any(host == h or host.endswith(f".{h}") for h in _H1_HOSTS):
        return client()
    with _client_lock:
        if _client_h1 is None or _client_h1.is_closed:
            import httpx

            _client_h1 = httpx.Client(
                http2=False,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, connect=20.0),
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                headers={"User-Agent": _user_agent()},
            )
        return _client_h1


def close_client() -> None:
    """Close the shared clients (new ones are created on next use)."""
    global _client, _client_h1
    with _client_lock:
        for c in (_client, _client_h1):
            if c is not None:
                c.close()
        _client = _client_h1 = None


@contextlib.contextmanager
def skip_on_api_limit(enabled: bool = True) -> Iterator[None]:
    """Within this block, a rate-limited request returns at once instead of waiting."""
    token = _skip_limit.set(enabled)
    try:
        yield
    finally:
        _skip_limit.reset(token)


def skipping_api_limits() -> bool:
    """Whether :func:`skip_on_api_limit` is active."""
    return _skip_limit.get()


def _parse_reset(resp: httpx.Response) -> float | None:
    """Absolute time (epoch seconds) at which a rate-limited host resets."""
    now = time.time()
    for name in ("RateLimit-Reset", "X-RateLimit-Reset", "Retry-After"):
        value = resp.headers.get(name)
        if not value:
            continue
        value = value.strip()
        try:
            number = float(value)
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(value) if name == "Retry-After" else None
            if parsed is not None:
                return parsed.timestamp()
            continue
        # epoch timestamps are large; small numbers are delta-seconds
        return number if number > 1e9 else now + number
    return None


def _exhausted(resp: httpx.Response) -> bool:
    """Whether a response says the rate-limit bucket is empty (``RateLimit-Remaining: 0``)."""
    remaining = resp.headers.get("RateLimit-Remaining") or resp.headers.get("X-RateLimit-Remaining")
    try:
        return remaining is not None and float(remaining) == 0
    except ValueError:
        return False


def _format_wait_duration(seconds: float) -> str:
    """Port of ``R/repo-download.R::.format_wait_duration()``: ``47s``, ``12.3 min``, ``1.4 hours``."""
    if seconds < 60:
        return f"{math.ceil(seconds)}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} hours"


def _announce_wait(host: str, seconds: float) -> None:
    """Say, unless quiet, that a wait of over 5 s for *host*'s rate-limit reset begins."""
    if seconds > 5:
        from metacheck.utils import message

        message(
            f"Rate limit reached from {host}; waiting {_format_wait_duration(seconds)} for the "
            "host's own reset before retrying (Ctrl+C to stop, or use skip_on_api_limit = "
            "TRUE to skip this file instead of waiting)."
        )


def host_reset_at(host: str) -> float | None:
    """When a host we have been rate-limited by resets (epoch seconds), if known."""
    with _reset_lock:
        reset = _host_reset.get(host)
    if reset is not None and reset <= time.time():
        with _reset_lock:
            _host_reset.pop(host, None)
        return None
    return reset


def _record_reset(host: str, reset: float) -> None:
    with _reset_lock:
        _host_reset[host] = max(reset, _host_reset.get(host, 0.0))


def _backoff(attempt: int) -> float:
    """httr2's default: a random wait between 1 and 2^attempt seconds, capped at 60."""
    return round(min(random.uniform(1, 2**attempt), 60), 1)  # noqa: S311 - jitter, not crypto


class Throttle:
    """A token bucket shared by all requests to a host (``httr2::req_throttle``)."""

    def __init__(self, capacity: float, fill_time_s: float = 1.0) -> None:
        self.rate = capacity / fill_time_s
        self.capacity = capacity
        self._tokens: dict[str, float] = {}
        self._stamp: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, host: str) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                tokens = self._tokens.get(host, self.capacity)
                tokens = min(self.capacity, tokens + (now - self._stamp.get(host, now)) * self.rate)
                self._stamp[host] = now
                if tokens >= 1:
                    self._tokens[host] = tokens - 1
                    return
                self._tokens[host] = tokens
                wait = (1 - tokens) / self.rate
            sleep(wait)
            if env_get("NO_SLEEP"):
                return


class RateLimited(Exception):
    """:func:`request` (with ``raise_errors``) gave up before sending anything.

    The host is known to be rate-limited and :func:`skip_on_api_limit` is active.
    """


def _send(
    session: httpx.Client,
    method: str,
    url: str,
    kwargs: dict[str, Any],
    *,
    stream: bool,
    resend_headers: bool,
) -> httpx.Response:
    """One request. With *stream* the body is left unread; with *resend_headers* every
    redirect hop is sent the original headers, a token included (httpx drops
    ``Authorization`` when a redirect leaves the origin)."""
    if not stream:
        return session.request(method, url, **kwargs)
    kwargs = dict(kwargs)
    follow = kwargs.pop("follow_redirects", True)
    auth = {"auth": kwargs.pop("auth")} if "auth" in kwargs else {}
    for hop in range(20):
        check_interrupt()
        req = session.build_request(method, url, **kwargs)
        resp = session.send(
            req, stream=True, follow_redirects=follow and not resend_headers, **auth
        )
        if not (resend_headers and resp.is_redirect and resp.next_request is not None) or hop == 19:
            break
        url = str(resp.next_request.url)
        if resp.status_code == 303:
            method = "GET"
        resp.close()
    return resp


def request(
    method: str,
    url: str,
    *,
    max_tries: int = 5,
    retry_statuses: Sequence[int] = RETRY_STATUSES,
    is_transient: Callable[[httpx.Response], bool] | None = None,
    retry_on_failure: bool = True,
    throttle: Throttle | None = None,
    http: httpx.Client | None = None,
    sink: Callable[[httpx.Response], None] | None = None,
    resend_headers: bool = False,
    raise_errors: bool = False,
    **kwargs: Any,
) -> httpx.Response | None:
    """Send a request with metacheck's retry policy.

    Returns the final response (including error statuses, which are not
    raised), or ``None`` after a connection-level failure on every try, or
    when the host is known to be rate-limited and :func:`skip_on_api_limit`
    is active. ``kwargs`` go to :meth:`httpx.Client.request`.

    A response is retried when its status is in *retry_statuses*, or, with
    *is_transient*, when that function says so (it replaces the list). A
    connection failure is retried too, unless *retry_on_failure* is false
    (httr2's ``req_retry(retry_on_failure =)``). A 429, or a retried status
    that says the bucket is empty (GitHub answers 403), makes the host's
    stated reset the wait, remembered for every later request to the host;
    under :func:`skip_on_api_limit` it ends the request at once.

    With *sink* the body is streamed: once a response is not one to retry,
    ``sink(response)`` reads it (``iter_bytes()``; or not at all, to leave an
    unwanted body unread). A connection that drops while it does is a failed
    try like any other, so *sink* must start over on a new try (a download
    writes to a temporary file, as
    :func:`metacheck.archives._atomic.atomic_write` does); any other
    exception it raises ends the request. The returned response is closed
    and its body not kept unless *sink* read it; a response that is retried
    or given up on is closed unread. *resend_headers* sends every redirect
    hop the original headers (an OSF or Zenodo token goes on to their storage
    hosts).

    With *raise_errors* a connection failure on the last try raises its
    :mod:`httpx` exception, and a request that is not sent because the host is
    known to be rate-limited raises :class:`RateLimited`, instead of
    returning ``None``.
    """
    import httpx

    host = urlsplit(url).hostname or ""
    session = http or client_for(url)
    stream = sink is not None or resend_headers
    retry = is_transient or (lambda r: r.status_code in retry_statuses)
    resp: httpx.Response | None = None
    for attempt in range(1, max_tries + 1):
        check_interrupt()
        reset = host_reset_at(host)
        if reset is not None:
            if skipping_api_limits():
                if resp is None and raise_errors:
                    raise RateLimited(f"{host} is rate-limited and waiting was skipped")
                return resp
            _announce_wait(host, reset - time.time())
            sleep(reset - time.time())
        if throttle is not None:
            throttle.acquire(host)
        try:
            resp = _send(session, method, url, kwargs, stream=stream, resend_headers=resend_headers)
            if not retry(resp):
                try:
                    if sink is not None:
                        sink(resp)
                    elif stream:
                        resp.read()
                finally:
                    if stream:
                        resp.close()
                return resp
        except (httpx.TransportError, httpx.TimeoutException):
            if attempt == max_tries or not retry_on_failure:
                if raise_errors:
                    raise
                return None
            sleep(_backoff(attempt))
            continue
        if stream:
            resp.close()
        remembered = False
        if resp.status_code == 429 or _exhausted(resp):
            reset = _parse_reset(resp)
            if reset is not None:
                _record_reset(host, reset)
                remembered = True
            if skipping_api_limits():
                return resp
        if attempt == max_tries:
            return resp
        if not remembered:  # a remembered reset is waited for, and announced, at the top
            sleep(_backoff(attempt))
    return resp


def _is_json_type(media_type: str) -> bool:
    """``application/json`` or a ``+json`` suffix (``application/vnd.api+json``), in any case."""
    kind, _, subtype = media_type.lower().partition("/")
    return bool(kind) and ((kind, subtype) == ("application", "json") or subtype.endswith("+json"))


def resp_json(resp: httpx.Response, *, check_type: bool = True) -> Any:
    """The JSON body of a response (httr2's ``resp_body_json()``).

    With *check_type*, the media type must be ``application/json`` or end in
    ``+json`` (compared case-insensitively, as media types are). The body is
    read as UTF-8 by :func:`metacheck._json.loads`. Raises ``ValueError`` for
    another media type, an empty body or invalid JSON.
    """
    from metacheck._json import loads

    if check_type:
        media_type = resp.headers.get("content-type", "").split(";", 1)[0].strip()
        if not _is_json_type(media_type):
            got = f"content type {media_type!r}" if media_type else "no content type"
            raise ValueError(f"Expected a JSON response{_from(resp)}, got {got}.")
    if not resp.content:
        raise ValueError(f"The response{_from(resp)} has an empty body.")
    return loads(resp.content)


def _from(resp: httpx.Response) -> str:
    try:
        return f" from {resp.request.url}"
    except RuntimeError:  # a response built without a request
        return ""


def batch_query(
    urls: Sequence[str],
    batch_size: int = 5,
    msg: str | None = "Batch Query",
    delay: float = 0.5,
    accept: str = "application/json",
    req_func: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    throttle_capacity: float | None = None,
    throttle_fill_time_s: float = 1.0,
    timeout_s: float = 60,
) -> list[httpx.Response | None]:
    """``.batch_query()``: GET many URLs politely; results align with *urls*.

    Requests run in batches of *batch_size* (concurrently within a batch,
    with a courtesy *delay* between batches). ``req_func`` may modify the
    request arguments (``{"method", "url", "headers", ...}``), e.g. to add
    authentication. Invalid URLs warn and give ``None``.
    """
    if not urls:
        return []
    throttle = Throttle(throttle_capacity, throttle_fill_time_s) if throttle_capacity else None
    specs: list[dict[str, Any] | None] = []
    for url in urls:
        parts = urlsplit(str(url))
        if parts.scheme not in ("http", "https") or not parts.netloc:
            warnings.warn(f"Bad URL: {url}", stacklevel=2)
            specs.append(None)
            continue
        spec: dict[str, Any] = {
            "method": "GET",
            "url": str(url),
            "headers": {"Accept": accept},
            "timeout": timeout_s,
        }
        specs.append(req_func(spec) if req_func else spec)

    def run(spec: dict[str, Any] | None) -> httpx.Response | None:
        if spec is None:
            return None
        spec = dict(spec)
        return request(spec.pop("method"), spec.pop("url"), throttle=throttle, **spec)

    results: list[httpx.Response | None] = [None] * len(specs)
    progress = _progress(len(specs), msg)
    with ThreadPoolExecutor(max_workers=max(1, batch_size)) as pool:
        for start in range(0, len(specs), batch_size):
            idx = list(range(start, min(start + batch_size, len(specs))))
            ctx = [copy_context() for _ in idx]
            futures = [pool.submit(c.run, run, specs[i]) for c, i in zip(ctx, idx, strict=True)]
            for i, fut in zip(idx, futures, strict=True):
                results[i] = fut.result()
            progress(len(idx))
            sleep(delay)
    return results


def _progress(total: int, msg: str | None) -> Callable[[int], None]:
    from metacheck.config import verbose

    if msg is None or not verbose() or total < 2:
        return lambda _n: None
    from rich.progress import Progress

    bar = Progress(transient=True)
    task = bar.add_task(msg, total=total)
    bar.start()
    done = 0

    def tick(n: int) -> None:
        nonlocal done
        done += n
        bar.update(task, advance=n)
        if done >= total:
            bar.stop()

    return tick
