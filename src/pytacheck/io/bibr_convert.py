"""Remote bibr conversion client (port of ``R/import-bibr.R``).

``convert_bibr()`` sends documents (PDF, DOC, DOCX) to a bibr extraction
server and saves the returned bibr JSON. Three kinds of server are spoken to:

* ``"scivrs"``: the Scienceverse platform (its own ``/jobs`` queue, API key);
* ``"selfhosted"``: one synchronous ``POST /papers/extract`` to ``bibr serve``;
* ``"bibr"``: the job API of ``bibr serve`` (``POST /papers/jobs``, then
  ``GET /papers/jobs/{id}`` and ``/result``) with a bearer token, which is
  also what the hosted service (bibr-gate: the same paths, personal tokens)
  offers. Configured with ``BIBR_URL`` and ``BIBR_API_KEY``. This backend is
  pytacheck's addition (docs/UPSTREAM_ISSUES.md D59); metacheck has none.

The in-process bibr integration (no server) lives in :mod:`pytacheck.io.bibr`.

Also here: the author helpers ``format_bib_authors()``,
``.coerce_bib_authors()`` and ``.parse_author_string()``.
"""

from __future__ import annotations

import email.utils
import math
import os
import re
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import pandas as pd

from pytacheck._r.base import as_character, trimws
from pytacheck._r.regex import grepl, gsub, strsplit
from pytacheck.log import logger

__all__ = ["BibrRequestError", "convert_bibr", "format_bib_authors"]

PathLikeStr = str | PathLike[str]

_BACKENDS = ("auto", "scivrs", "selfhosted", "bibr")

#: environment variables of the ``"bibr"`` backend (``BIBR_API_URL`` is the name bibr's own
#: example clients read; ``BIBR_URL`` wins when both are set). Only ``BIBR_URL`` steers the
#: choice of backend (``backend="auto"``, ``convert()``): ``BIBR_API_URL`` is often set for
#: other tools, and is read only once the ``"bibr"`` backend has been asked for.
BIBR_URL_ENV = ("BIBR_URL", "BIBR_API_URL")
BIBR_KEY_ENV = "BIBR_API_KEY"
_BIBR_DEFAULT_URL = "http://localhost:8000"
#: consecutive failed status polls (no answer, 502, 503, 504) before a job is given up on
_MAX_POLL_FAILURES = 5


def _na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and math.isnan(x)


def _match_arg(value: Any, choices: Sequence[str]) -> str:
    """R's ``match.arg()`` (exact or unique partial match)."""
    from pytacheck.utils import match_arg

    return match_arg(value, choices)


# httr2's status descriptions (``httr2:::http_statuses``), for resp_status_desc()
_HTTR2_STATUSES: dict[int, str] = {
    100: "Continue",
    101: "Switching Protocols",
    102: "Processing",
    103: "Early Hints",
    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non-Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    207: "Multi-Status",
    208: "Already Reported",
    226: "IM Used",
    300: "Multiple Choice",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    305: "Use Proxy",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Payload Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    416: "Range Not Satisfiable",
    417: "Expectation Failed",
    418: "I'm a teapot",
    421: "Misdirected Request",
    422: "Unprocessable Entity",
    423: "Locked",
    424: "Failed Dependency",
    425: "Too Early",
    426: "Upgrade Required",
    428: "Precondition Required",
    429: "Too Many Requests",
    451: "Unavailable For Legal Reasons",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negotiates",
    507: "Insufficient Storage",
    508: "Loop Detected",
    510: "Not Extended",
    511: "Network Authentication Required",
}


def _status_desc(status: int) -> str | None:
    """``httr2::resp_status_desc()``: the reason phrase, ``None`` (``NA``) if unknown."""
    return _HTTR2_STATUSES.get(int(status))


def _http_error(resp: Any) -> RuntimeError:
    """httr2's error for an HTTP error status (``req_perform()`` default)."""
    desc = _status_desc(resp.status_code)
    return RuntimeError(f"HTTP {resp.status_code} {desc}." if desc else f"HTTP {resp.status_code}.")


def _url_append(api_url: str, *parts: str) -> str:
    """``httr2::req_url_path_append()``."""
    u = urlsplit(api_url)
    path = u.path.rstrip("/") + "/" + "/".join(p.strip("/") for p in parts)
    return urlunsplit((u.scheme, u.netloc, path, u.query, u.fragment))


# ---------------------------------------------------------------------------
# bibr serve and bibr-gate (backends "bibr" and "selfhosted")
# ---------------------------------------------------------------------------


class BibrRequestError(RuntimeError):
    """An HTTP error from a bibr server: ``status_code``, the server's ``detail`` text and,
    for a rate limit, the ``retry_after`` seconds it asked for."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        detail: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _bibr_env_url() -> str:
    """The server named in ``BIBR_URL`` (or ``BIBR_API_URL``); ``""`` when neither is set."""
    for name in BIBR_URL_ENV:
        value = _env(name)
        if value:
            return value
    return ""


def _bibr_steering_url() -> str:
    """The server named in ``BIBR_URL``: the only address that picks the ``"bibr"`` backend
    for ``backend="auto"`` and ``convert()`` (``BIBR_API_URL`` does not); ``""`` when unset."""
    return _env(BIBR_URL_ENV[0])


def _is_loopback(host: str) -> bool:
    """``localhost`` or a loopback address (``127.0.0.0/8``, ``::1``). Not ``*.localhost``: a
    resolver may send such a name anywhere, and the token would go along in clear."""
    import ipaddress

    host = host.strip("[]").rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _may_send_token(api_url: str) -> bool:
    """A bearer token may go to https, or to http on a loopback host, and nowhere else."""
    parts = urlsplit(api_url)
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and _is_loopback(parts.hostname or "")


def _check_bibr_target(api_url: str, api_key: str | None) -> None:
    """Refuse an address that is not http(s), and a token that would cross the network in clear."""
    parts = urlsplit(api_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            "The bibr server address must start with http:// or https:// and name a host "
            "(set BIBR_URL, or pass api_url)."
        )
    if api_key and not _may_send_token(api_url):
        raise ValueError(
            f"Refusing to send the bibr API token over plain http to {parts.hostname}. "
            "Use an https:// address; plain http is accepted only for localhost and "
            "loopback addresses (127.0.0.1, ::1)."
        )


def _clean(text: object, limit: int = 300) -> str:
    """One printable line of at most *limit* characters (text from a server is untrusted)."""
    line = re.sub(r"\s+", " ", "".join(c if c.isprintable() else " " for c in str(text))).strip()
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _json_object(resp: Any) -> dict[str, Any]:
    """The JSON object in a response body; ``{}`` for anything else."""
    from pytacheck._json import loads

    try:
        body = loads(resp.content)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _server_detail(resp: Any) -> str:
    """What the server said about an error: the ``detail``, ``message`` or ``error`` of a
    JSON body (bibr answers ``{"detail": ...}``; its job errors add an ``error_code``)."""
    body = _json_object(resp)
    for key in ("detail", "message", "error"):
        value = body.get(key)
        code = None
        if isinstance(value, dict):
            code = value.get("error_code")
            value = value.get("message") or value.get("detail") or value.get("error")
        if value:
            return _clean(f"{value} ({code})" if code else value)
    return ""


_HINTS: dict[int, str] = {
    400: "The server rejected the request as malformed.",
    401: "The token was rejected: it is missing, mistyped, expired or revoked. "
    "Check BIBR_API_KEY (or api_key).",
    403: "The request was refused: the token is not allowed to use this server, or a proxy "
    "in front of it blocked the request.",
    404: "Not found: the job has expired or was evicted (bibr keeps jobs for an hour by "
    "default), or BIBR_URL is not the address of the bibr API.",
    413: "The file is larger than the server accepts (bibr's limit is 50 MiB per file).",
    415: "The server does not accept this kind of file (the hosted service takes PDF files only).",
    422: "bibr could not process this document; sending the same file again fails the same way.",
    429: "The server is busy, or a quota is used up.",
    500: "The server failed while handling the request.",
    502: "The server, or the service behind it, is not available.",
    503: "The server is not available (or its job store is); try again later.",
    504: "The server timed out.",
    507: "The server has no room to store the upload.",
}


def _bibr_error(
    resp: Any,
    doing: str,
    api_key: str | None,
    *,
    retry_after: float | None = None,
    gave_up: bool = False,
) -> BibrRequestError:
    """The error for an unwanted response, in words: status, what it means, what the server said.

    *doing* completes "while ...". The token never appears in the message.
    """
    status = int(resp.status_code)
    desc = _status_desc(status)
    parts = [f"HTTP {status} {desc} while {doing}." if desc else f"HTTP {status} while {doing}."]
    if 300 <= status < 400:
        target = urlsplit(resp.headers.get("location", ""))
        shown = f"{target.scheme}://{target.hostname or ''}{target.path}" if target.scheme else ""
        parts.append(
            f"The server redirected the request{' to ' + _clean(shown, 120) if shown else ''}. "
            "pytacheck does not follow redirects here, so the token stays with the host you "
            "configured: set BIBR_URL to the address it redirects to."
        )
    elif status == 401 and not api_key:
        parts.append("No API token was sent: set BIBR_API_KEY or pass api_key.")
    elif status in _HINTS:
        parts.append(_HINTS[status])
    if gave_up and status == 429:
        parts.append(
            f"It asks to wait {retry_after:g} s, which is more than pytacheck will."
            if retry_after is not None
            else "Still busy after the allowed number of retries."
        )
    detail = _server_detail(resp)
    if detail and api_key and len(api_key) >= 8:
        detail = detail.replace(api_key, "***")
    if detail:
        parts.append(f'The server said: "{detail}".')
    return BibrRequestError(" ".join(parts), status, detail, retry_after)


def _retry_after(resp: Any) -> float | None:
    """Seconds a ``Retry-After`` header asks for (delay-seconds or an HTTP date), else ``None``."""
    value = (resp.headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, when.timestamp() - time.time())
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


@dataclass
class _Session:
    """One document's dealings with a bibr server: address, token and the waiting budget.

    ``timeout`` is the total time (seconds) to wait for a job, back-offs included.
    ``waited`` counts requested sleeps, so the budget is exact when sleeping is switched
    off (``PYTACHECK_NO_SLEEP``); real elapsed time counts too.
    """

    api_url: str
    api_key: str | None = field(repr=False)  # a token must not turn up in a log or traceback
    timeout: float
    max_retries: int
    max_retry_wait: float
    follow_redirects: bool = True
    waited: float = 0.0
    started: float = field(default_factory=time.monotonic)

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @property
    def elapsed(self) -> float:
        return max(self.waited, time.monotonic() - self.started)

    def sleep(self, seconds: float) -> None:
        from pytacheck import http

        http.sleep(seconds)
        self.waited += seconds


def _honour_host_reset(sess: _Session, url: str, doing: str) -> None:
    """Bound, and count, the wait the shared layer would do for this host on its own.

    ``pytacheck.http`` remembers a 429 that any caller got from a host (the readiness probe
    retries one, for instance) and sleeps until its reset before every later request to the
    host, however far off. Here that wait gets the same limits as a ``Retry-After`` on this
    client's own requests: too long is an error, else it is slept here and used up from the
    session's budget.
    """
    from pytacheck import http

    reset = http.host_reset_at(urlsplit(url).hostname or "")
    if reset is None:
        return
    wait = max(0.0, reset - time.time())
    if wait > sess.max_retry_wait or sess.elapsed + wait > sess.timeout:
        raise BibrRequestError(
            f"HTTP 429 Too Many Requests while {doing}. The server rate-limited an earlier "
            f"request and asked to wait {wait:.0f} s more, which is more than pytacheck will.",
            429,
            "",
            wait,
        )
    sess.sleep(wait)


def _bibr_call(
    sess: _Session,
    method: str,
    url: str,
    doing: str,
    *,
    allow_no_response: bool = False,
    **kwargs: Any,
) -> Any:
    """One request to a bibr server.

    A 429 is retried after the ``Retry-After`` it names (else after 1, 2, 4, ... s), at most
    ``max_retries`` times, never waiting longer than ``max_retry_wait`` for one retry nor
    past the session's ``timeout``. A 429 means nothing was done, so a retry cannot
    duplicate work. Every other status is returned as it is: the shared layer's own retries
    are switched off, because they wait out any ``Retry-After``, however long it is (and a
    reset it remembers from another caller is bounded by :func:`_honour_host_reset`).
    Returns ``None`` for no answer when *allow_no_response*, else raises.
    """
    from pytacheck import http

    retries = 0
    while True:
        _honour_host_reset(sess, url, doing)
        resp = http.request(
            method,
            url,
            max_tries=1,
            retry_statuses=(),
            headers=sess.headers,
            follow_redirects=sess.follow_redirects,
            **kwargs,
        )
        if resp is None:
            if allow_no_response:
                return None
            raise ConnectionError(f"Failed to perform HTTP request to {sess.api_url}")
        if 300 <= resp.status_code < 400 and not sess.follow_redirects:
            raise _bibr_error(resp, doing, sess.api_key)
        if resp.status_code != 429:
            return resp
        asked = _retry_after(resp)
        wait = asked if asked is not None else min(2.0**retries, 30.0)
        if (
            retries >= sess.max_retries
            or wait > sess.max_retry_wait
            or sess.elapsed + wait > sess.timeout
        ):
            raise _bibr_error(resp, doing, sess.api_key, retry_after=asked, gave_up=True)
        sess.sleep(wait)
        retries += 1


def _bibr_submit(
    sess: _Session,
    file_path: str,
    include_figures: bool,
    start_page: float | None,
    end_page: float | None,
) -> str:
    """``POST /papers/jobs``: 202 ``{job_id, status, status_url}``. Returns the job id."""
    resp = _bibr_call(
        sess,
        "POST",
        _url_append(sess.api_url, "papers", "jobs"),
        "submitting the job",
        files=_form(file_path, include_figures, start_page, end_page),
        timeout=60,
    )
    if resp.status_code >= 400:
        raise _bibr_error(resp, "submitting the job", sess.api_key)
    job_id = _json_object(resp).get("job_id")
    if resp.status_code not in (200, 202) or not isinstance(job_id, str | int) or not job_id:
        logger("convert_bibr", "submission failed")
        raise RuntimeError(
            f"Job submission failed (HTTP {resp.status_code}): {_clean(resp.text, 200)}"
        )
    return str(job_id)


def _job_error(status: dict[str, Any]) -> str:
    """Why a job failed, from its status (``error`` holds bibr's ``{message, error_code}``
    or ``{detail}``)."""
    error = status.get("error")
    code = None
    if isinstance(error, dict):
        code = error.get("error_code") or error.get("kind")
        error = error.get("message") or error.get("detail") or error.get("error")
    text = _clean(error) if error else "unknown error"
    return f"{text} ({code})" if code else text


def _bibr_result(resp: Any) -> bytes:
    """The body of a finished job's result, checked to be a JSON object (a paper)."""
    if not _json_object(resp):
        raise RuntimeError(
            "The bibr server answered the result request with something that is not a paper "
            "(a JSON object was expected). Is BIBR_URL the address of the bibr API?"
        )
    return bytes(resp.content)


def _unavailable(resp: Any) -> bool:
    """No answer, or one that says the server (or what is behind it) is briefly unavailable."""
    return resp is None or resp.status_code in (502, 503, 504)


def _bibr_await_result(sess: _Session, job_id: str, poll_interval: float) -> bytes:
    """Poll ``GET /papers/jobs/{id}`` (queued, running, succeeded or failed) with a growing
    delay, then fetch ``/result``. A 409 from ``/result`` means not finished yet: keep
    polling. A status poll or a result fetch that gets no answer, or a 502, 503 or 504, is
    tolerated a few times in a row (the job is done on the server: giving up on one hiccup
    would lose a paper that already counted against a quota).

    The job path is built from the job id and the configured address; the ``status_url`` in
    the server's answer is not followed, which keeps the token on the host it was meant for.
    """
    job_url = _url_append(sess.api_url, "papers", "jobs", quote(job_id, safe=""))
    shown = _clean(job_id, 60)  # the id comes from the server: no control characters
    doing = f"checking job {shown}"
    fetching = f"fetching the result of job {shown}"
    delay = poll_interval
    failures = 0
    state: Any = None

    def outage(resp: Any, what: str) -> None:
        """Count one more failed request in a row; give up after too many."""
        nonlocal failures
        failures += 1
        if failures > _MAX_POLL_FAILURES:
            if resp is None:
                raise ConnectionError(f"Failed to perform HTTP request to {sess.api_url}")
            raise _bibr_error(resp, what, sess.api_key)

    while True:
        resp = _bibr_call(sess, "GET", job_url, doing, allow_no_response=True, timeout=30)
        if _unavailable(resp):
            outage(resp, doing)
        elif resp.status_code >= 400:
            raise _bibr_error(resp, doing, sess.api_key)
        else:
            status = _json_object(resp)
            state = status.get("status")
            if state == "failed":
                error = _job_error(status)
                logger("convert_bibr", {"job_id": shown, "error": error})
                raise RuntimeError(f"Job {shown} failed: {error}")
            if state == "succeeded":
                rresp = _bibr_call(
                    sess,
                    "GET",
                    _url_append(job_url, "result"),
                    fetching,
                    allow_no_response=True,
                    timeout=300,
                )
                if rresp is not None and rresp.status_code == 200:
                    return _bibr_result(rresp)
                if _unavailable(rresp):
                    outage(rresp, fetching)  # the failures of the polls in between do not reset it
                elif rresp.status_code != 409:
                    raise _bibr_error(rresp, fetching, sess.api_key)
                else:
                    failures = 0
            else:
                failures = 0
        if sess.elapsed >= sess.timeout:
            logger("convert_bibr", {"job_id": shown, "error": "timeout"})
            raise TimeoutError(
                f"Job {shown} timed out after {as_character(sess.timeout)}s "
                f"(last status: {as_character(state) if state is not None else ''}); "
                "it may still finish on the server, and counts against your limit on active "
                "jobs there until it does"
            )
        # the last wait ends at the deadline, so that one final poll can still see the job done
        sess.sleep(min(delay, max(sess.timeout - sess.elapsed, 0.0)))
        delay = min(delay * 1.5, max(poll_interval, 10.0))


def _bibr_request_bibr(
    file_path: str,
    api_url: str,
    api_key: str | None,
    include_figures: bool,
    start_page: float | None,
    end_page: float | None,
    poll_interval: float,
    timeout: float,
    max_retries: int = 5,
    max_retry_wait: float = 120,
) -> bytes:
    """The ``"bibr"`` backend: submit a job to ``bibr serve`` (or bibr-gate), poll it, get the
    result. Not a port: metacheck has no such backend."""
    sess = _Session(
        api_url, api_key or None, timeout, max_retries, max_retry_wait, follow_redirects=False
    )
    job_id = _bibr_submit(sess, file_path, include_figures, start_page, end_page)
    return _bibr_await_result(sess, job_id, poll_interval)


# ---------------------------------------------------------------------------
# convert_bibr()
# ---------------------------------------------------------------------------


def convert_bibr(
    file_path: PathLikeStr | Sequence[PathLikeStr],
    save_path: PathLikeStr = ".",
    backend: str = "auto",
    api_key: str | None = None,
    api_url: str | None = None,
    include_figures: bool = False,
    start_page: float = 1,
    end_page: float = math.inf,
    poll_interval: float = 2,
    timeout: float = 600,
    max_retries: int = 5,
    max_retry_wait: float = 120,
) -> Any:
    """Port of ``R/import-bibr.R::convert_bibr()``: convert documents with a bibr server.

    ``backend`` names the kind of server:

    * ``"scivrs"``: the Scienceverse platform (its ``/jobs`` queue, ``api_key`` or
      ``SCIVRS_API_KEY``; default ``https://platform.metacheck.app``);
    * ``"selfhosted"``: one synchronous ``POST /papers/extract`` to a ``bibr serve``
      (default ``http://localhost:8000``), with a bearer token when ``api_key`` (or
      ``BIBR_API_KEY``) is set;
    * ``"bibr"``: the job API of ``bibr serve`` and of the hosted service in front of it
      (bibr-gate): ``POST /papers/jobs``, poll ``/papers/jobs/{id}``, fetch ``/result``.
      Address and token come from ``api_url``/``api_key`` or ``BIBR_URL``/``BIBR_API_KEY``
      (default address ``http://localhost:8000``, no token; ``BIBR_API_URL`` is read here
      too, after ``BIBR_URL``). A token is never sent over plain http, except to localhost
      and loopback addresses (127.0.0.1, ::1). ``poll_interval`` must be positive.

    ``backend="auto"`` uses ``"bibr"`` when ``BIBR_URL`` is set and no ``api_url`` is given,
    or when ``BIBR_API_KEY`` is the only key there is (no ``api_key`` argument, no
    ``SCIVRS_API_KEY``); else ``"scivrs"`` when an API key is given or ``SCIVRS_API_KEY``
    is set; else ``"selfhosted"``. ``BIBR_API_URL`` alone does not pick a backend.

    A 429 answer (``"bibr"`` and ``"selfhosted"``) is retried after its ``Retry-After``
    seconds, up to ``max_retries`` times and never waiting more than ``max_retry_wait``
    seconds for one retry; ``timeout`` (seconds) bounds the whole wait for a job.

    Pages are 1-based; ``end_page=math.inf`` means all pages. Returns the saved JSON
    path, or a list of paths (``None`` for failures) for several files or a directory;
    a 401 or 403 (a token every later file would be refused with) stops a list at once.
    """
    backend = _match_arg(backend, _BACKENDS)
    env_key = os.environ.get("SCIVRS_API_KEY", "")
    if backend == "auto":
        backend = _auto_backend(api_key, api_url, env_key)

    if backend == "scivrs" and api_key is None:
        api_key = env_key
        if not api_key:
            raise ValueError(
                "API key not set. Set the SCIVRS_API_KEY environment variable or pass "
                "api_key directly."
            )
    elif backend in ("bibr", "selfhosted") and api_key is None:
        api_key = _env(BIBR_KEY_ENV) or None

    if api_url is None:
        if backend == "scivrs":
            api_url = "https://platform.metacheck.app"
        elif backend == "bibr":
            api_url = _bibr_env_url() or _BIBR_DEFAULT_URL
        else:
            api_url = _BIBR_DEFAULT_URL
    # once, before any file: a list would otherwise log the same refusal for each file
    if backend == "bibr" or (backend == "selfhosted" and api_key):
        _check_bibr_target(api_url, api_key)
    if backend == "bibr" and not 0 < poll_interval < math.inf:  # NaN fails this too
        raise ValueError(
            "poll_interval must be a positive number of seconds (0 would poll the server in "
            f"a tight loop), not {poll_interval!r}."
        )

    paths: list[str] = (
        [os.fspath(file_path)]
        if isinstance(file_path, str | PathLike)
        else [os.fspath(f) for f in file_path]
    )
    if len(paths) == 1 and Path(paths[0]).is_dir():
        from pytacheck.io._files import list_files

        paths = list_files(paths[0], r"\.(docx?|pdf)$")

    if len(paths) > 1:
        out: list[Any] = []
        for fp in paths:
            try:
                out.append(
                    convert_bibr(
                        fp,
                        save_path=save_path,
                        backend=backend,
                        api_key=api_key,
                        api_url=api_url,
                        include_figures=include_figures,
                        start_page=start_page,
                        end_page=end_page,
                        poll_interval=poll_interval,
                        timeout=timeout,
                        max_retries=max_retries,
                        max_retry_wait=max_retry_wait,
                    )
                )
            except BibrRequestError as exc:
                if exc.status_code in (401, 403):
                    raise
                logger("convert_bibr", str(exc))
                out.append(None)
            except Exception as exc:
                logger("convert_bibr", str(exc))
                out.append(None)
        return out

    # zero-based page values (None = omit from the request)
    zb_start: float | None = start_page - 1 if start_page > 1 else None
    zb_end: float | None = end_page - 1 if math.isfinite(end_page) else None

    if backend == "scivrs":
        contents = _bibr_request_scivrs(
            paths[0],
            api_url,
            str(api_key),
            include_figures,
            zb_start,
            zb_end,
            poll_interval,
            timeout,
        )
    elif backend == "bibr":
        contents = _bibr_request_bibr(
            paths[0],
            api_url,
            api_key,
            include_figures,
            zb_start,
            zb_end,
            poll_interval,
            timeout,
            max_retries,
            max_retry_wait,
        )
    else:
        contents = _bibr_request_selfhosted(
            paths[0],
            api_url,
            include_figures,
            zb_start,
            zb_end,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            max_retry_wait=max_retry_wait,
        )
    return _bibr_save_result(contents, paths[0], save_path)


def _auto_backend(api_key: str | None, api_url: str | None, env_key: str) -> str:
    """The ``backend="auto"`` choice (see :func:`convert_bibr`). Arguments beat the
    environment: a ``BIBR_*`` variable picks ``"bibr"`` only for what was not passed, and a
    ``BIBR_API_KEY`` alone does not take over from a ``SCIVRS_API_KEY``, which chose the
    platform before the ``"bibr"`` backend existed."""
    if api_url is None and (
        _bibr_steering_url() or (api_key is None and not env_key and _env(BIBR_KEY_ENV))
    ):
        return "bibr"
    return "scivrs" if api_key is not None or env_key else "selfhosted"


def _form(
    file_path: str, include_figures: bool, start_page: float | None, end_page: float | None
) -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = [
        ("file", (Path(file_path).name, Path(file_path).read_bytes())),
        ("include_figures", (None, str(bool(include_figures)).lower())),
    ]
    if start_page is not None:
        fields.append(("start_page", (None, as_character(start_page))))
    if end_page is not None:
        fields.append(("end_page", (None, as_character(end_page))))
    return fields


def _bibr_request_scivrs(
    file_path: str,
    api_url: str,
    api_key: str,
    include_figures: bool,
    start_page: float | None,
    end_page: float | None,
    poll_interval: float,
    timeout: float,
) -> bytes:
    """Port of ``R/import-bibr.R::.bibr_request_scivrs()``: submit a job and poll it."""
    from pytacheck import http

    auth = {"Authorization": f"Bearer {api_key}"}
    resp = http.request(
        "POST",
        _url_append(api_url, "jobs"),
        max_tries=1,
        headers=auth,
        files=_form(file_path, include_figures, start_page, end_page),
        timeout=60,
    )
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {api_url}")
    if resp.status_code >= 400:
        raise _http_error(resp)
    if resp.status_code != 200:
        logger("convert_bibr", "submission failed")
        raise RuntimeError(f"Job submission failed (HTTP {resp.status_code}): {resp.text}")
    job_id = resp.json().get("job_id")

    status_url = f"{api_url}/jobs/{job_id}"
    elapsed = 0.0
    while True:
        http.sleep(poll_interval)
        elapsed += poll_interval
        sresp = http.request("GET", status_url, max_tries=1, headers=auth, timeout=30)
        if sresp is None:
            raise ConnectionError(f"Failed to perform HTTP request to {status_url}")
        if sresp.status_code >= 400:
            raise _http_error(sresp)
        status = sresp.json()
        state = status.get("status")
        if state == "complete":
            break
        if state == "failed":
            err = status.get("stage")
            if err is None:  # status$stage %||% "unknown error"
                err = "unknown error"
            logger("convert_bibr", {"job_id": job_id, "error": err})
            raise RuntimeError(f"Job {job_id} failed: {err}")
        if elapsed >= timeout:
            logger("convert_bibr", {"job_id": job_id, "error": "timeout"})
            raise TimeoutError(
                f"Job {job_id} timed out after {as_character(timeout)}s "
                f"(last status: {as_character(state) if state is not None else ''})"
            )

    rresp = http.request(
        "GET",
        _url_append(api_url, "jobs", str(job_id), "result"),
        max_tries=1,
        headers=auth,
        timeout=120,
    )
    if rresp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {api_url}")
    if rresp.status_code >= 400:
        raise _http_error(rresp)
    if rresp.status_code != 200:
        logger("convert_bibr", "download failed")
        raise RuntimeError(f"Result download failed (HTTP {rresp.status_code})")
    return rresp.content


def _bibr_request_selfhosted(
    file_path: str,
    api_url: str,
    include_figures: bool,
    start_page: float | None,
    end_page: float | None,
    *,
    api_key: str | None = None,
    timeout: float = 600,
    max_retries: int = 5,
    max_retry_wait: float = 120,
) -> bytes:
    """Port of ``R/import-bibr.R::.bibr_request_selfhosted()``: one direct extraction.

    Sends ``Authorization: Bearer`` when *api_key* is given (a ``bibr serve`` with
    ``AUTH_API_KEY`` answers 401 without it; metacheck sends none) and waits out a 429
    (upload admission limit) as the ``"bibr"`` backend does.
    """
    sess = _Session(api_url, api_key or None, timeout, max_retries, max_retry_wait)
    resp = _bibr_call(
        sess,
        "POST",
        _url_append(api_url, "papers", "extract"),
        "extracting the paper",
        files=_form(file_path, include_figures, start_page, end_page),
        timeout=300,
    )
    if resp.status_code >= 400:
        raise _bibr_error(resp, "extracting the paper", sess.api_key)
    if resp.status_code != 200:
        msg = _status_desc(resp.status_code) or "NA"
        raise RuntimeError(f"Bibr request failed with status code: {resp.status_code}\n{msg}")
    return bytes(resp.content)


def _bibr_save_result(contents: bytes, file_path: PathLikeStr, save_path: PathLikeStr) -> str:
    """Port of ``R/import-bibr.R::.bibr_save_result()``: write the JSON, return its path."""
    Path(save_path).mkdir(parents=True, exist_ok=True)
    name = gsub(r"\..{1,4}$", r"\.json", Path(file_path).name)
    json_path = f"{os.fspath(save_path)}/{name}"
    Path(json_path).write_bytes(contents)
    return json_path


def _bibr_isalive(
    api_url: str,
    api_key: str | None = "__env__",
    error: bool = True,
) -> bool:
    """Port of ``R/import-bibr.R::.bibr_isalive()``: is a bibr server up and ready?

    ``api_key`` defaults to the ``SCIVRS_API_KEY`` environment variable (an
    empty key still sends an ``Authorization`` header, as in R); ``None``
    sends none. A non-empty key is not sent over plain http to a host other than
    localhost: ``/ready`` is public, so the probe works without it.

    Ready means ``status == "ready"``. An anonymous ``bibr serve`` answers only that
    (``{"status": "ready"}``: ``checks`` and ``build_sha`` are for authenticated callers,
    as is the platform's ``checks.bibr``), so a ``checks.bibr`` is only held against the
    server when it is there and not ``"ok"`` (metacheck requires it).
    """
    from pytacheck import http

    if api_key == "__env__":
        api_key = os.environ.get("SCIVRS_API_KEY", "")
    headers = {} if api_key is None else {"Authorization": f"Bearer {api_key}"}
    if api_key and not _may_send_token(api_url):
        headers = {}
    failure = None
    try:
        resp = http.request(
            "GET",
            _url_append(api_url, "ready"),
            max_tries=3,
            retry_statuses=(429, 503),
            headers=headers,
        )
    except Exception as exc:
        resp, failure = None, str(exc)
    if resp is None:
        if error:
            raise ConnectionError(
                "Connection to the BIBR server failed. Please check your connection or the "
                f"URL: {api_url} ({failure or 'Could not connect to server'})"
            )
        return False

    status = resp.status_code
    if status != 200:
        if error:
            raise RuntimeError(
                f"The BIBR server does not appear up and running on the URL {api_url}. "
                f"Status: {status}"
            )
        return False

    ctype = resp.headers.get("content-type")
    if ctype is None:
        raise ValueError("argument is of length zero")
    if not grepl("json", ctype):
        if error:
            raise RuntimeError("The server is running, but the API key is not valid")
        return False

    body = _json_object(resp)
    checks = body.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    if body.get("status") != "ready" or checks.get("bibr", "ok") != "ok":
        if error:
            raise RuntimeError(f"The server is running, but BIBR is not ready{status}")
        return False
    return True


# ---------------------------------------------------------------------------
# authors
# ---------------------------------------------------------------------------


def _is_character(x: Any) -> bool:
    if isinstance(x, str):
        return True
    if isinstance(x, pd.Series):
        return pd.api.types.is_string_dtype(x.dtype) or all(isinstance(v, str) or _na(v) for v in x)
    if isinstance(x, list | tuple):
        return (
            len(x) > 0
            and all(isinstance(v, str) or _na(v) for v in x)
            and any(isinstance(v, str) for v in x)
        )
    return False


def _paste_na(v: Any) -> str:
    """``as.character()`` of one value as ``paste()`` writes it (``NA`` -> ``"NA"``)."""
    if _na(v):
        return "NA"
    out = as_character(v)
    return "NA" if out is None else str(out)


def _df_column(df: pd.DataFrame, name: str) -> list[Any]:
    """``df$name``: exact column, else a unique partial match (with R's warning)."""
    if name in df.columns:
        return list(df[name])
    hits = [c for c in df.columns if isinstance(c, str) and c.startswith(name)]
    if len(hits) == 1:
        warnings.warn(f"Partial match of '{name}' to '{hits[0]}' in data frame", stacklevel=3)
        return list(df[hits[0]])
    return []


def format_bib_authors(authors: Any) -> Any:
    """Port of ``R/import-bibr.R::format_bib_authors()``: authors as ``"Family, Given; ..."``.

    ``authors`` is a data frame with ``given``/``family`` columns, a
    character vector (joined with ``"; "``), or a list of those (formatted
    one by one). An empty table or ``None`` gives ``None`` (``NA``).
    """
    if isinstance(authors, list | tuple) and not _is_character(authors):
        return [format_bib_authors(a) for a in authors]
    if authors is None or (isinstance(authors, pd.DataFrame) and len(authors) == 0):
        return None
    if _is_character(authors):
        values = [authors] if isinstance(authors, str) else list(authors)
        return "; ".join(_paste_na(v) for v in values)
    if isinstance(authors, dict):
        authors = pd.DataFrame(authors)
    if not isinstance(authors, pd.DataFrame):
        raise TypeError("$ operator is invalid for atomic vectors")
    # paste(family, given, sep = ", ", collapse = "; "): a missing column is
    # a zero-length vector, which paste() recycles as ""
    family = _df_column(authors, "family")
    given = _df_column(authors, "given")
    n = max(len(family), len(given))
    if n == 0:
        return ""
    fam = [_paste_na(v) for v in family] or [""]
    giv = [_paste_na(v) for v in given] or [""]
    return "; ".join(f"{fam[i % len(fam)]}, {giv[i % len(giv)]}" for i in range(n))


def _authors_frame(given: Sequence[str | None], family: Sequence[str | None]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            # pandas-stubs: dtype="string" wants Sequence[str]; None is stored as <NA>
            "given": pd.Series(given, dtype="string"),  # type: ignore[arg-type]
            "family": pd.Series(family, dtype="string"),  # type: ignore[arg-type]
        }
    )


def _parse_author_string(s: Any) -> pd.DataFrame:
    """Port of ``R/import-bibr.R::.parse_author_string()``.

    Parses ``"Family, Given; Family2, Given2"`` or ``"Family, Given, and
    Given2 Family2"`` into a ``given``/``family`` table.
    """
    if _na(s) or len(trimws(str(s))) == 0:
        return _authors_frame([], [])
    s = str(s)
    given: list[str] = []
    family: list[str] = []
    if grepl(";", s):
        for part in trimws(strsplit(s, ";")):
            fg = trimws(strsplit(part, ","))
            if not fg:
                raise IndexError("subscript out of bounds")
            if len(fg) >= 2:
                given.append(fg[1])
                family.append(fg[0])
            else:
                given.append("")
                family.append(fg[0])
        return _authors_frame(given, family)

    for part in trimws(strsplit(s, r"\band\b|&")):
        fg = trimws(strsplit(part, ","))
        if len(fg) >= 2:
            given.append(trimws(fg[1]))
            family.append(trimws(fg[0]))
            continue
        words = trimws(strsplit(trimws(part), r"\s+"))
        if len(words) >= 2:
            given.append(" ".join(words[:-1]))
            family.append(words[-1])
        elif len(words) == 1:
            given.append("")
            family.append(words[0])
    return _authors_frame(given, family)


def _coerce_author_cell(x: Any) -> pd.DataFrame:
    if x is None or (not isinstance(x, list | tuple | pd.DataFrame | pd.Series) and _na(x)):
        return _authors_frame([], [])
    if isinstance(x, pd.DataFrame):
        return x.loc[:, [c for c in ("given", "family") if c in x.columns]]
    if hasattr(x, "ndim") and getattr(x, "ndim", 1) == 2:  # legacy matrix [given, family]
        return _authors_frame(
            [as_character(v) for v in x[:, 0]], [as_character(v) for v in x[:, 1]]
        )
    if isinstance(x, list | tuple) and len(x) == 1 and isinstance(x[0], str):
        x = x[0]
    if isinstance(x, str):
        return _parse_author_string(x)
    return _authors_frame([], [])


def _coerce_bib_authors(col: Any) -> Any:
    """Port of ``R/import-bibr.R::.coerce_bib_authors()``.

    Turns an authors column (structured ``[{given, family}]`` tables, legacy
    matrices or ``"Family, Given; ..."`` strings) into a list of
    ``given``/``family`` data frames.
    """
    if col is None:
        return None
    if isinstance(col, pd.DataFrame):
        if col.shape[1] == 0:
            cells: list[Any] = [None] * len(col)
        else:
            cells = [list(col[c]) for c in col.columns]
    else:
        cells = list(col)
    return [_coerce_author_cell(x) for x in cells]
