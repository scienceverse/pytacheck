"""Selective, cached download of repository files (port of ``R/repo-download.R``).

``repo_check`` lists a paper's repository files without fetching them; the
modules that read file contents (``data_check``, ``code_check``) call
:func:`download_repo_files` to fetch only what they need into a cache shared
by all modules: a per-session temporary directory by default, or the
persistent on-disk cache (:func:`repo_cache_dir`, :func:`repo_cache_size`,
:func:`repo_cache_clear`) with ``cache=True``.

Where a host offers a whole-repository archive (OSF's Waterbutler ``?zip=``,
Zenodo's ``files-archive``, Dataverse's ``/api/access/dataset``, Dryad's
dataset download, GitHub's zipball, GitLab's ``archive.zip``) it is used
instead of one request per file when the size/worth-it gate allows;
everything else -- and every archive download that fails -- is fetched file
by file (OSF osfstorage and Zenodo files in parallel, other hosts through a
per-host throttle).

Every storage request follows metacheck's policy (:func:`_storage_request`):
host authentication (:func:`_auth_for_url`), 3 tries with retries on
403/429/5xx and connection failures, ``min(2^attempt, 30)`` s backoff, and a
confirmed exhausted rate limit (``RateLimit-Remaining: 0`` + reset) waited
out -- or, with ``skip_on_api_limit``, given up on at once. Confirmed resets
are remembered per host in :mod:`metacheck.http`'s rate-limit memory, so
later requests to that host wait up front instead of rediscovering the 429.
"""

from __future__ import annotations

import atexit
import functools
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from metacheck._env import env_get
from metacheck._r import grepl, gsub, is_na, plural, sub
from metacheck.archives._atomic import atomic_write

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["download_repo_files", "repo_cache_clear", "repo_cache_dir", "repo_cache_size"]

_MB = 1024 * 1024

#: ``.storage_is_transient()``: statuses worth retrying when fetching file bytes.
_STORAGE_TRANSIENT = (403, 429, 500, 502, 503, 504)

#: R: ``req_throttle(capacity = 10, fill_time_s = 10, realm = host)`` in
#: ``.download_one()``: a burst of 10, then ~1 request/second per host.
_THROTTLE: Any = None


def _message(*parts: Any) -> None:
    from metacheck.archives import _message as message

    message(*parts)


def _is_missing(x: Any) -> bool:
    return x is None or is_na(x)


def _num(x: Any) -> float:
    """One value as R double (``NA``/unparseable -> ``nan``)."""
    if _is_missing(x):
        return math.nan
    if isinstance(x, str):
        from metacheck.stats._rmath import as_numeric

        return as_numeric(x)[0]
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _nums(values: Iterable[Any]) -> list[float]:
    """``as.numeric()`` of a vector (``NA`` -> ``nan``)."""
    return [_num(v) for v in values]


def _chr(x: Any) -> str | None:
    if _is_missing(x):
        return None
    if isinstance(x, str):
        return x
    from metacheck._r import as_character

    return as_character(x)


def _fmt_int(x: float) -> str:
    """``sprintf("%d", x)`` for a count; a fractional value is shown as it is
    (R's ``sprintf("%d")`` fails on it, U73)."""
    from metacheck._r import as_character

    if _is_missing(x):
        return "NA"
    v = float(x)
    if not v.is_integer():
        return as_character(v) or "NA"
    return str(int(v))


# ---------------------------------------------------------------------------
# cache locations
# ---------------------------------------------------------------------------


def _repo_cache_dir() -> str:
    """Port of ``R/repo-download.R::.repo_cache_dir()``: the persistent cache root.

    ``.metacheck_repo_cache`` under the shared cache root (see
    :mod:`metacheck.archives.cache`), or the ``metacheck.repo_cache.dir``
    option when set. Created if needed.
    """
    from metacheck.archives.cache import _metacheck_cache_subdir
    from metacheck.utils import get_option

    return _metacheck_cache_subdir(
        ".metacheck_repo_cache", override=get_option("metacheck.repo_cache.dir")
    )


def _repo_cache_location() -> str:
    """Where :func:`_repo_cache_dir` is, without creating it."""
    from metacheck.archives.cache import _metacheck_cache_root
    from metacheck.utils import get_option

    override = get_option("metacheck.repo_cache.dir")
    if override is not None and str(override) != "":
        return str(override)
    return os.path.join(_metacheck_cache_root(), ".metacheck_repo_cache")


_STALE_TEMP_AGE_S = 6 * 3600
_swept = False


def _sweep_repo_cache_once() -> None:
    """Once per process, remove temporary files that a killed run left in the repo cache.

    A hosted run's process can be ended mid-write; its temporary file (see
    :mod:`metacheck.archives._atomic`) would otherwise stay for good. Only files
    older than 6 hours are removed; the cache folder is not created; never raises.
    """
    global _swept
    if _swept:
        return
    _swept = True
    try:
        from metacheck.archives._atomic import sweep_stale

        root = _repo_cache_location()
        if os.path.isdir(root):
            sweep_stale(root, _STALE_TEMP_AGE_S)
    except Exception:  # noqa: S110 - a sweep must never fail a run
        pass


def _scalar(x: Any, what: str = "x") -> Any:
    """A length-1 vector argument as its value (R's scalar ``if()`` conditions)."""
    if isinstance(x, str | bytes) or x is None or not isinstance(x, Sequence | Iterable):
        return x
    if isinstance(x, Mapping):
        return x
    values = list(x)
    if len(values) == 1:
        return values[0]
    if not values:
        raise ValueError("argument is of length zero")
    raise ValueError(f"the condition has length > 1 ({what})")


def _repo_key(repo_url: Any) -> str:
    """The filesystem-safe cache key of a repository URL (``NULL`` -> ``"unknown"``)."""
    if repo_url is not None and not isinstance(repo_url, str):
        repo_url = _scalar(repo_url, "repo_url")
        if repo_url is None:
            repo_url = float("nan")  # an element of a vector: NA, not NULL
    if repo_url is None:
        key: str = "unknown"
    elif is_na(repo_url):
        return "NA"  # R: gsub() keeps NA, and nzchar(NA) is TRUE
    else:
        key = repo_url if isinstance(repo_url, str) else (_chr(repo_url) or "NA")
    key = gsub("^https?://", "", key)  # scheme is noise
    key = gsub("[^A-Za-z0-9._-]+", "_", key)  # filesystem-safe
    key = gsub("^_+|_+$", "", key)
    return key if key != "" else "unknown"


def _repo_cache_subdir(repo_url: Any) -> str:
    """Port of ``R/repo-download.R::.repo_cache_subdir()``: a repository's cache folder.

    Keyed by a filesystem-safe encoding of the repository URL (scheme
    dropped, runs of other characters turned into ``_``), so different
    repositories never collide and the same one always resolves to the same
    folder.
    """
    return f"{_repo_cache_dir()}/{_repo_key(repo_url)}"


def _file_path(*parts: str) -> str:
    """R ``file.path()`` for scalars."""
    return "/".join(parts)


def _rel_one(key: str, file_path: Any) -> str:
    if _is_missing(file_path):
        rel = "NA"
    else:
        rel = file_path if isinstance(file_path, str) else (_chr(file_path) or "NA")
        rel = gsub(r"\\", "/", rel)
        rel = gsub("^/+", "", rel)
    return _file_path(key, rel)


def _repo_cache_rel(repo_url: Any, file_path: Any) -> Any:
    """Port of ``R/repo-download.R::.repo_cache_rel()``: ``<repo key>/<repo-relative path>``.

    Backslashes become ``/`` and leading slashes are dropped. Vectorised over
    *file_path* (a sequence gives a list).
    """
    key = _repo_key(repo_url)
    if isinstance(file_path, str) or _is_missing(file_path):
        return _rel_one(key, file_path)
    return [_rel_one(key, p) for p in file_path]


def _repo_cache_path(repo_url: Any, file_path: Any) -> Any:
    """Port of ``R/repo-download.R::.repo_cache_path()``: a file's persistent cache path."""
    root = _repo_cache_dir()
    rel = _repo_cache_rel(repo_url, file_path)
    if isinstance(rel, list):
        return [_file_path(root, r) for r in rel]
    return _file_path(root, rel)


@functools.cache
def _session_tempdir() -> str:
    """R's ``tempdir()``: a per-process temporary directory, removed at exit."""
    path = tempfile.mkdtemp(prefix="pytacheck-")
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def _repo_session_dir() -> str:
    """Port of ``R/repo-download.R::.repo_session_dir()``: the ``cache = FALSE`` directory.

    ``<tempdir>/metacheck-repo-files`` (created once per session and removed
    when the process exits), or the ``metacheck.repo_cache.session_dir``
    option; the choice is stored in that option.
    """
    from metacheck.utils import get_option, options

    d = get_option("metacheck.repo_cache.session_dir")
    if d is None:
        d = _file_path(_session_tempdir(), "metacheck-repo-files")
    os.makedirs(d, exist_ok=True)
    options({"metacheck.repo_cache.session_dir": d})
    return str(d)


def repo_cache_dir() -> str:
    """Locate the downloaded-repository file cache.

    Port of ``R/repo-download.R::repo_cache_dir()``. Files fetched by
    ``data_check`` and ``code_check`` with ``cache=True`` are kept here and
    reused across modules and sessions. The cache is never cleared
    automatically (see :func:`repo_cache_clear`). The location is
    ``.metacheck_repo_cache`` in the working directory by default; set the
    ``metacheck.repo_cache.dir`` option (or ``metacheck.cache.dir`` for all
    caches) to move it.
    """
    return _repo_cache_dir()


def repo_cache_size() -> float:
    """Size of the downloaded-repository file cache, in bytes (0 when empty or absent).

    Port of ``R/repo-download.R::repo_cache_size()``.
    """
    from metacheck.archives.cache import _metacheck_dir_size

    return float(_metacheck_dir_size(_repo_cache_dir()))


def _format_object_size(x: float) -> str:
    """``format(structure(x, class = "object_size"), units = "auto")`` (legacy units)."""
    from metacheck._r import as_character, r_round

    units = ["b", "Kb", "Mb", "Gb", "Tb", "Pb"]
    power = 0 if x <= 0 else min(int(math.log(x) / math.log(1024)), len(units) - 1)
    unit = "bytes" if power == 0 else units[power]
    return f"{as_character(float(r_round(x / 1024**power, 1)))} {unit}"


def _dir_files_size(d: str) -> float:
    total = 0.0
    for dirpath, _dirs, names in os.walk(d):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def _in_tests() -> bool:
    return os.environ.get("TESTTHAT") == "true" or "PYTEST_CURRENT_TEST" in os.environ


def repo_cache_clear(repo_url: Any = None, quiet: bool = False) -> float:
    """Delete the downloaded-repository file cache.

    Port of ``R/repo-download.R::repo_cache_clear()``. Removes the whole
    cache, or with *repo_url* (one URL or several) only those repositories'
    files. Anything deleted is simply downloaded again when next needed.
    Unless *quiet*, reports how much was freed. Returns the number of bytes
    freed.

    As in R, emptying the whole cache while tests run is refused unless the
    cache has been redirected (the ``metacheck.repo_cache.dir`` or
    ``metacheck.cache.dir`` option, or ``PYTACHECK_CACHE_DIR``).
    """
    from metacheck.utils import get_option

    if (
        repo_url is None
        and _in_tests()
        and get_option("metacheck.repo_cache.dir") is None
        and get_option("metacheck.cache.dir") is None
        and not env_get("CACHE_DIR")
    ):
        raise RuntimeError(
            "repo_cache_clear() refused to empty the cache during tests without a "
            "redirected location. Set one first, e.g. "
            "withr::local_options(metacheck.cache.dir = withr::local_tempdir())."
        )

    if repo_url is None:
        candidates = [_repo_cache_dir()]
    else:
        import pandas as pd

        urls = [repo_url] if isinstance(repo_url, str) else list(repo_url)
        # a missing element of a vector is R's NA (key "NA"), not NULL ("unknown")
        candidates = [_repo_cache_subdir(pd.NA if u is None else u) for u in urls]
    targets = list(dict.fromkeys(d for d in candidates if os.path.isdir(d)))

    freed = float(sum(_dir_files_size(d) for d in targets))
    for d in targets:
        shutil.rmtree(d, ignore_errors=True)
    # Recreate the (empty) root so later downloads have somewhere to write.
    if repo_url is None:
        os.makedirs(_repo_cache_dir(), exist_ok=True)

    if not quiet:
        human = _format_object_size(freed)
        if repo_url is None:
            scope = "the repository file cache"
        else:
            scope = f"{len(targets)} cached repositor{'y' if len(targets) == 1 else 'ies'}"
        _message(f"Cleared {scope} ({human} freed).")
    return freed


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------


class _RequestError(Exception):
    """httr2's ``Failed to perform HTTP request.`` (a connection-level failure)."""


class _HttpError(Exception):
    """httr2's ``httr2_http_<status>`` error (``req_perform()`` without ``req_error()``).

    The message is httr2's: ``HTTP 404 Not Found.``, with its status
    descriptions, plus a bullet per ``WWW-Authenticate: Bearer`` parameter
    (httr2's ``resp_auth_message()``), formatted as cli formats them.
    """

    def __init__(self, resp: Any) -> None:
        self.resp = resp
        status = int(resp.status_code)
        phrase = _reason_phrase(status)
        lines = [f"HTTP {status} {phrase}." if phrase else f"HTTP {status}."]
        lines += [_cli_bullet(m) for m in _resp_auth_message(resp)]
        super().__init__("\n".join(lines))


def _reason_phrase(status: int) -> str:
    """``httr2::resp_status_desc()`` (``""`` for R's ``NA``)."""
    import httpx

    from metacheck.archives.zenodo_upload import _HTTR2_STATUS_DESC

    if status in _HTTR2_STATUS_DESC:
        return _HTTR2_STATUS_DESC[status] or ""
    phrase = httpx.codes.get_reason_phrase(status)
    return phrase if phrase else ""


def _scan_fields(text: str) -> list[str]:
    """``scan(text = x, what = "", sep = ",", quote = '"', strip.white = TRUE)``."""
    if text == "":
        return []
    fields: list[str] = []
    buf: list[str] = []
    quoted = False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            fields.append("".join(buf).strip(" \t"))
            buf = []
        else:
            buf.append(ch)
    fields.append("".join(buf).strip(" \t"))
    return fields


def _resp_auth_message(resp: Any) -> list[str]:
    """httr2's ``resp_auth_message()``: the OAuth details of a ``WWW-Authenticate: Bearer``."""
    www = resp.headers.get("www-authenticate")
    if www is None:
        return []
    scheme, sep, rest = www.partition(" ")
    if not sep:
        rest = ""
    params: list[tuple[str, str]] = []
    for field in _scan_fields(rest):
        name, eq, value = field.partition("=")
        params.append((name, value if eq else ""))
    if scheme != "Bearer":
        return []
    named = dict(reversed(params))  # `$`: the first of duplicated names
    if "error" in named:
        msg = f"OAuth error: {named['error']}"
        if "error_description" in named:
            msg = f"{msg} - {named['error_description']}"
    else:
        msg = "OAuth error"
    others = [(n, v) for n, v in params if not (n.startswith("error") or n == "scheme")]
    # paste0(names(x), ": ", x) of nothing is still ": "
    return [msg, *([f"{n}: {v}" for n, v in others] or [": "])]


def _cli_bullet(text: str, width: int = 79) -> str:
    """A cli ``*`` bullet: whitespace collapsed, wrapped to 80 columns, indented 2."""
    words = text.split()
    lines: list[str] = []
    line = "\u2022"
    for w in words:
        if len(line) + 1 + len(w) > width and line.strip() not in ("", "\u2022"):
            lines.append(line)
            line = " "
        line = f"{line} {w}"
    lines.append(line)
    return "\n".join(lines)


def _host(url: Any) -> str | None:
    """``httr2::url_parse(url)$hostname`` (``None`` when there is none)."""
    try:
        host = urlsplit(str(url)).hostname
    except ValueError:
        return None
    return host or None


def _file_url_path(url: str) -> str:
    from urllib.parse import unquote
    from urllib.request import url2pathname

    parts = urlsplit(url)
    path = unquote(parts.path)
    if parts.netloc and parts.netloc != "localhost":
        path = f"//{parts.netloc}{path}"
    return url2pathname(path) if os.name == "nt" else path


def _file_response(method: str, url: str, path: str | None) -> Any:
    """What curl does with a ``file://`` URL: status 0, the file's bytes, no ranges."""
    import httpx

    local = _file_url_path(url)
    try:
        size = os.path.getsize(local)
        with open(local, "rb") as fh:
            body = b"" if method == "HEAD" else fh.read()
    except OSError as e:
        raise _RequestError(
            "Failed to perform HTTP request.\n"
            "Caused by error in `curl::curl_fetch_disk()`:\n"
            f"! Could not read a file:// file:\nCould not open file {local}"
        ) from e
    if path is not None:
        with atomic_write(path) as fh:
            fh.write(body)
        body = b""
    resp = httpx.Response(
        0,
        headers={"Content-Length": str(size), "Accept-ranges": "bytes"},
        content=body,
        request=httpx.Request(method, url),
    )
    return resp


def _perform_once(
    spec: Mapping[str, Any],
    timeout: float | None = None,
    path: str | None = None,
    read_body: Callable[[Any], bool] | None = None,
) -> Any:
    """Send one request (no retries); the body goes to *path* when given.

    ``spec`` is a request spec (``{"method", "url", "headers",
    "unrestricted_auth"}``). ``unrestricted_auth`` re-sends the headers
    (the token) on every redirect hop, as curl's option of that name does.
    *read_body*, given the response before its body arrives, decides
    whether the body is read at all (httr2 ``req_perform_connection()``: the
    status is read first and an unwanted body is closed unread). Raises
    :class:`_RequestError` on a connection failure.
    """
    import httpx

    from metacheck import http

    method = str(spec.get("method") or "GET")
    url = str(spec["url"])
    if url.lower().startswith("file:"):
        return _file_response(method, url, path)
    headers = dict(spec.get("headers") or {})
    to = httpx.Timeout(timeout if timeout is not None else 60.0, connect=20.0)
    client = http.client_for(url)
    follow = not spec.get("unrestricted_auth")
    try:
        for _hop in range(20):
            http.check_interrupt()
            req = client.build_request(method, url, headers=headers, timeout=to)
            resp = client.send(req, stream=True, follow_redirects=follow)
            if not follow and resp.is_redirect and resp.next_request is not None:
                url = str(resp.next_request.url)
                if resp.status_code == 303:
                    method = "GET"
                resp.close()
                continue
            break
        try:
            if read_body is not None and not read_body(resp):
                pass  # closed unread
            elif path is not None:
                with atomic_write(path) as fh:
                    for chunk in resp.iter_bytes(chunk_size=1 << 20):
                        http.check_interrupt()
                        fh.write(chunk)
            else:
                resp.read()
        finally:
            resp.close()
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise _RequestError(
            f"Failed to perform HTTP request.\nCaused by error in `curl::curl_fetch_disk()`:\n! {e}"
        ) from e
    return resp


def _skip(skip_on_api_limit: Any) -> bool:
    """``isTRUE(skip_on_api_limit) || isTRUE(getOption("metacheck.skip_on_api_limit"))``.

    :func:`metacheck.http.skip_on_api_limit` blocks count too.
    """
    from metacheck import http
    from metacheck.utils import get_option

    return (
        skip_on_api_limit is True
        or get_option("metacheck.skip_on_api_limit", False) is True
        or http.skipping_api_limits()
    )


def _storage_request(
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    skip_on_api_limit: bool = False,
    timeout: float | None = None,
    path: str | None = None,
    max_tries: int = 3,
    auth: bool = True,
    throttle: Any = None,
    error: bool = False,
    is_transient: Callable[[Any], bool] | None = None,
    read_body: Callable[[Any], bool] | None = None,
) -> Any:
    """A storage request with metacheck's retry policy (httr2 ``req_retry()`` as R sets it).

    ``request(url) |> .auth_for_url() |> req_retry(max_tries = 3,
    retry_on_failure = TRUE, is_transient = .storage_is_transient_factory(),
    backoff = .storage_backoff, after = .storage_retry_after_factory())``.
    With *error* ``False`` (R: ``req_error(is_error = \\(r) FALSE)``) every
    status is returned; with ``True`` a status >= 400 raises
    :class:`_HttpError`. A connection failure on the last try raises
    :class:`_RequestError`. *path* streams the body to a file; *throttle* is a
    :class:`metacheck.http.Throttle` applied once per call. *is_transient*
    replaces the storage rule for which answers are retried; *read_body* is
    passed on to :func:`_perform_once`.
    """
    from metacheck import http

    spec: dict[str, Any] = {"method": method, "url": url, "headers": dict(headers or {})}
    if auth:
        spec = _auth_for_url(spec)
    if is_transient is None:
        is_transient = _storage_is_transient_factory(skip_on_api_limit)
    after = _storage_retry_after_factory(skip_on_api_limit)
    if throttle is not None:
        throttle.acquire(_host(url) or "local")
    tries = 0
    delay = 0.0
    resp: Any = None
    err: Exception | None = None
    while tries < max_tries:
        http.sleep(delay)
        try:
            resp, err = (
                _perform_once(spec, timeout=timeout, path=path, read_body=read_body),
                None,
            )
        except _RequestError as e:
            resp, err = None, e
        if err is not None:
            tries += 1
            delay = _storage_backoff(tries)
        elif is_transient(resp):
            tries += 1
            wait = after(resp)
            delay = _storage_backoff(tries) if is_na(wait) else float(wait)
        else:
            break
    if err is not None:
        raise err
    if error and resp is not None and resp.status_code >= 400:
        raise _HttpError(resp)
    return resp


def _remote_size(url: str) -> float:
    """Port of ``R/repo-download.R::.remote_size()``: a remote file's size before download.

    A HEAD request first, then a one-byte ranged GET whose ``Content-Range``
    carries the size (:func:`metacheck.archives.zip_peek._head_size`,
    :func:`~metacheck.archives.zip_peek._range_size`): S3-backed hosts
    (Dryad, Figshare, Harvard Dataverse) refuse HEAD with 403. Both are
    authenticated per host; ``nan`` (R ``NA``) when neither gives a size.
    """
    from metacheck.archives.zip_peek import _head_size, _range_size

    size = _head_size(url)
    if is_na(size):
        size = _range_size(url)
    return size


def _archive_size_estimate(files: pd.DataFrame, repo: Any) -> float:
    """Port of ``R/repo-download.R::.archive_size_estimate()``.

    The estimated size (bytes) of a repository's whole-archive download: the
    sum of the file sizes listed for *repo* in *files*, or ``nan`` (R ``NA``)
    when none are listed. No whole-archive endpoint reports its size ahead of
    time; the byte limit of the download is what actually bounds it.
    """
    repos = files["repo_url"].tolist()
    sizes = _nums(files["file_size"].tolist()) if "file_size" in files.columns else []
    est = sum(
        s for r, s in zip(repos, sizes, strict=False) if not is_na(r) and r == repo and not is_na(s)
    )
    return float(est) if est > 0 else math.nan


def _archive_size_refusal(zip_bytes: float, max_download_size: float, mb: float) -> str | None:
    """Port of ``R/repo-download.R::.archive_size_refusal()``.

    Why a whole-archive download is refused on size, or ``None`` (R ``NA``)
    when it is allowed: its size is unknown, or above 2x the per-repository
    budget (a one-request transport earns a 2x allowance).
    """
    from metacheck.report.blocks import _cap_num

    if is_na(zip_bytes):
        return "archive size unknown: no file sizes listed"
    if math.isfinite(max_download_size) and zip_bytes > 2 * max_download_size * mb:
        return (
            f"estimated archive size {_cap_num(_rround(zip_bytes / mb))} MB exceeds 2x the "
            f"{_cap_num(max_download_size)} MB budget"
        )
    return None


def _with_headers(spec: dict[str, Any], extra: Mapping[str, str]) -> dict[str, Any]:
    out = dict(spec)
    headers = dict(out.get("headers") or {})
    headers.update(extra)
    out["headers"] = headers
    return out


def _auth_for_url(req: Mapping[str, Any]) -> dict[str, Any]:
    """Port of ``R/repo-download.R::.auth_for_url()``: host authentication for a file request.

    *req* is a request spec (``{"method", "url", "headers", ...}``); a new one
    is returned. OSF and Zenodo get their bearer token (kept across the
    redirect to their storage hosts: ``unrestricted_auth``), Dataverse,
    Figshare, Dryad and ReShare their own header functions, and
    4TU.ResearchData its ``Authorization: token`` -- so a private file is
    fetched instead of a sign-in page.
    """
    spec = dict(req)
    url = str(spec.get("url") or "")
    host = (_host(url) or "").lower()
    if host == "osf.io" or host.endswith(".osf.io"):
        # the host, not the whole URL: a path or a look-alike host must not get the token
        from metacheck.archives.osf_helpers import osf_pat

        try:
            pat = osf_pat()
        except Exception:
            pat = ""
        if pat:
            spec = _with_headers(spec, {"Authorization": f"Bearer {pat}"})
            spec["unrestricted_auth"] = True
    elif grepl(r"zenodo\.org", url, ignore_case=True):
        from metacheck.archives.zenodo_upload import zenodo_pat

        sandbox = bool(grepl(r"sandbox\.zenodo\.org", url, ignore_case=True))
        try:
            pat = zenodo_pat(sandbox=sandbox)
        except Exception:
            pat = ""
        if pat:
            spec = _with_headers(spec, {"Authorization": f"Bearer {pat}"})
            spec["unrestricted_auth"] = True
    else:
        from metacheck.archives.dataverse import _dataverse_headers, _dataverse_host_regex

        if grepl(_dataverse_host_regex(), url, ignore_case=True):
            spec = _dataverse_headers(spec)
        elif grepl(r"figshare\.com", url, ignore_case=True):
            from metacheck.archives.figshare import _figshare_headers

            spec = _figshare_headers(spec)
        elif grepl(r"datadryad\.org", url, ignore_case=True):
            from metacheck.archives.dryad import _dryad_headers

            spec = _dryad_headers(spec)
        elif grepl(r"reshare\.ukdataservice\.ac\.uk", url, ignore_case=True):
            from metacheck.archives.reshare import _reshare_headers

            spec = _reshare_headers(spec)
        elif grepl(r"data\.4tu\.nl", url, ignore_case=True):
            from metacheck.archives.fourtu import _researchdata4tu_pat

            try:
                pat = _researchdata4tu_pat()
            except Exception:
                pat = ""
            spec = _with_headers(spec, {"User-Agent": "metacheck"})
            if pat:
                spec = _with_headers(spec, {"Authorization": f"token {pat}"})
    return spec


def _is_login_page(path: str, expected_size: float = math.nan) -> bool:  # noqa: ARG001
    """Port of ``R/repo-download.R::.is_login_page()``: did we get a sign-in page?

    A private OSF file requested without a token comes back as HTTP 200 with
    an HTML sign-in page. True for a small (<= 200 kB) file whose first 400
    bytes hold no NUL, an HTML start and "sign in"/"log in"/"osf | sign".
    """
    if not os.path.exists(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size == 0 or size > 200000:
        return False
    try:
        with open(path, "rb") as fh:
            raw_head = fh.read(400)
    except OSError:
        return False
    if not raw_head or b"\x00" in raw_head:
        return False
    # useBytes = TRUE: compare bytes (latin-1 keeps one character per byte)
    head_txt = raw_head.decode("latin-1")
    return bool(grepl("<!DOCTYPE html|<html", head_txt, ignore_case=True)) and bool(
        grepl(r"sign in|log in|osf \| sign", head_txt, ignore_case=True)
    )


def _storage_is_transient(resp: Any) -> bool:
    """Port of ``R/repo-download.R::.storage_is_transient()``: 403, 429 or 5xx (500/502-504)."""
    return int(resp.status_code) in _STORAGE_TRANSIENT


def _storage_backoff(attempt: int) -> float:
    """Port of ``R/repo-download.R::.storage_backoff()``: ``min(2^attempt, 30)`` seconds."""
    return float(min(2**attempt, 30))


def _rate_limit_wait(resp: Any) -> float:
    """Port of ``R/repo-download.R::.rate_limit_wait()``: seconds until a confirmed reset.

    Reads ``RateLimit-Remaining``/``RateLimit-Reset`` (Dryad, GitLab) or
    ``X-RateLimit-*`` (Zenodo, GitHub). ``nan`` (R ``NA``) unless the bucket
    is confirmed exhausted (remaining exactly 0) with a numeric reset time; a
    reset already in the past gives 0.
    """
    from metacheck.stats._rmath import as_numeric

    headers = resp.headers
    remaining = headers.get("ratelimit-remaining")
    if remaining is None:
        remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("ratelimit-reset")
    if reset is None:
        reset = headers.get("x-ratelimit-reset")
    if remaining is None or reset is None:
        return math.nan
    if as_numeric(remaining)[0] != 0:
        return math.nan
    reset_time = as_numeric(reset)[0]
    if is_na(reset_time):
        return math.nan
    wait = reset_time - time.time()
    return 0.0 if is_na(wait) or wait < 0 else float(wait)


def _host_rate_limit_record(host: str | None, wait: float) -> None:
    """Port of ``R/repo-download.R::.host_rate_limit_record()``.

    Records that *host* is rate-limited for *wait* more seconds, in
    :mod:`metacheck.http`'s per-host memory (the later reset wins).
    """
    from metacheck import http

    if host is None or is_na(wait):
        return
    http._record_reset(host, time.time() + float(wait))


def _host_rate_limit_remaining(host: str | None) -> float:
    """Port of ``R/repo-download.R::.host_rate_limit_remaining()``.

    Seconds until *host*'s recorded rate-limit reset, or ``nan`` (R ``NA``)
    when nothing is recorded or the window has passed (a passed window is
    forgotten).
    """
    from metacheck import http

    if host is None:
        return math.nan
    reset_at = http.host_reset_at(host)
    if reset_at is None:
        return math.nan
    remaining = reset_at - time.time()
    if remaining <= 0:
        with http._reset_lock:
            http._host_reset.pop(host, None)
        return math.nan
    return remaining


def _format_wait_duration(seconds: float) -> str:
    """Port of ``R/repo-download.R::.format_wait_duration()``: ``47s``, ``12.3 min``, ``1.4 hours``."""
    if seconds < 60:
        return f"{math.ceil(seconds)}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} hours"


def _announce_rate_limit_wait(wait: float, resp: Any = None, host: str | None = None) -> None:
    """Port of ``R/repo-download.R::.announce_rate_limit_wait()``: say a wait over 5 s."""
    if wait > 5:
        if host is None and resp is not None:
            try:
                host = _host(resp.request.url)
            except Exception:
                host = None
        host_part = f" from {host}" if host is not None else ""
        _message(
            f"Rate limit reached{host_part}; waiting {_format_wait_duration(wait)} for the "
            "host's own reset before retrying (Ctrl+C to stop, or use skip_on_api_limit = "
            "TRUE to skip this file instead of waiting)."
        )


def _storage_retry_after_factory(skip_on_api_limit: bool = False) -> Callable[[Any], float]:
    """Port of ``R/repo-download.R::.storage_retry_after_factory()``.

    The ``after`` callback of the storage retry: the confirmed rate-limit wait
    of a response (recorded for its host and announced), or ``nan`` to fall
    back to the backoff. Always ``nan`` under ``skip_on_api_limit`` (argument
    or option).
    """
    skip = _skip(skip_on_api_limit)

    def after(resp: Any) -> float:
        if skip:
            return math.nan
        wait = _rate_limit_wait(resp)
        if not is_na(wait):
            try:
                host = _host(resp.request.url)
            except Exception:
                host = None
            _host_rate_limit_record(host, wait)
            _announce_rate_limit_wait(wait, resp)
        return wait

    return after


def _storage_is_transient_factory(skip_on_api_limit: bool = False) -> Callable[[Any], bool]:
    """Port of ``R/repo-download.R::.storage_is_transient_factory()``.

    :func:`_storage_is_transient`, except that under ``skip_on_api_limit`` a
    429 with a confirmed exhausted bucket is not transient (give up now); a
    plain 429 still retries.
    """
    skip = _skip(skip_on_api_limit)

    def is_transient(resp: Any) -> bool:
        if not _storage_is_transient(resp):
            return False
        return not (skip and int(resp.status_code) == 429 and not is_na(_rate_limit_wait(resp)))

    return is_transient


def _wait_out_known_rate_limit(url: str, skip_on_api_limit: bool = False) -> bool:
    """Port of ``R/repo-download.R::.wait_out_known_rate_limit()``.

    Before a request to *url*: when its host is known to be rate-limited,
    wait out the remaining time (``True``) -- or, under ``skip_on_api_limit``,
    give up at once (``False``). ``True`` straight away for any other host.
    """
    from metacheck import http

    skip = _skip(skip_on_api_limit)
    host = _host(url)
    remaining = _host_rate_limit_remaining(host)
    if is_na(remaining):
        return True
    if skip:
        return False
    _announce_rate_limit_wait(remaining, host=host)
    http.sleep(remaining)
    return True


def _zip_timeout_for_size(
    timeout_s: float, expected_bytes: float = math.nan, min_bytes_per_s: float = 200 * 1024
) -> float:
    """Port of ``R/repo-download.R::.zip_timeout_for_size()``: scale a timeout to the size.

    *timeout_s* is a floor; with a known *expected_bytes* the timeout is at
    least the time the transfer takes at *min_bytes_per_s* (200 KB/s).
    """
    expected_bytes = _scalar(expected_bytes, "expected_bytes")
    if _is_missing(expected_bytes) or expected_bytes <= 0:
        return timeout_s
    return max(timeout_s, expected_bytes / min_bytes_per_s)


# ---------------------------------------------------------------------------
# downloading files
# ---------------------------------------------------------------------------


def _throttle() -> Any:
    global _THROTTLE
    if _THROTTLE is None:
        from metacheck import http

        _THROTTLE = http.Throttle(10, fill_time_s=10)
    return _THROTTLE


def _dir_create(path: str) -> None:
    """``dir.create(path, showWarnings = FALSE, recursive = TRUE)``: never an error."""
    try:
        os.makedirs(path or ".", exist_ok=True)
    except OSError:
        pass


def _unlink(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            return
        os.remove(path)
    except OSError:
        pass


def _download_one(
    url: str,
    dest: str,
    skip_on_api_limit: bool = False,
    expected_bytes: float = math.nan,
) -> str | None:
    """Port of ``R/repo-download.R::._download_one()``: download one file to *dest*.

    Authenticated per host, throttled per host (a burst of 10, then ~1/s),
    timed out by size (:func:`_zip_timeout_for_size` with a 60 s floor) and
    retried on transient refusals. Returns ``None`` on success or a short
    error description; a failure given up on because of ``skip_on_api_limit``
    starts with ``"API rate limit exhausted: "``.
    """
    _dir_create(os.path.dirname(dest))
    try:
        if not _wait_out_known_rate_limit(url, skip_on_api_limit):
            return "API rate limit exhausted: known rate-limited host, skip_on_api_limit"
        _storage_request(
            "GET",
            url,
            skip_on_api_limit=skip_on_api_limit,
            timeout=_zip_timeout_for_size(60, expected_bytes),
            path=dest,
            throttle=_throttle(),
            error=True,
        )
        if _is_login_page(dest):
            _unlink(dest)
            return "not authorised (the OSF returned a sign-in page; see ?osf_pat)"
        if os.path.exists(dest) and (os.path.getsize(dest) > 0 or expected_bytes == 0):
            return None
        # no file is left behind: the next run would take it as cached (metacheck
        # keeps the 0-byte file: U75)
        _unlink(dest)
        return "empty response"
    except Exception as e:
        if os.path.exists(dest):
            _unlink(dest)
        if (
            skip_on_api_limit is True  # R: isTRUE(skip_on_api_limit), the argument only
            and isinstance(e, _HttpError)
            and int(e.resp.status_code) == 429
            and not is_na(_rate_limit_wait(e.resp))
        ):
            return f"API rate limit exhausted: {e}"
        return str(e)


def _parallel_one(url: Any, dest: str, expected: float, skip: bool) -> tuple[Any, Exception | None]:
    if _is_missing(url):
        return "bad URL", None
    try:
        resp = _storage_request(
            "GET",
            str(url),
            skip_on_api_limit=skip,
            timeout=_zip_timeout_for_size(60, expected),
            path=dest,
        )
        return resp, None
    except Exception as e:
        return None, e


def _download_many_parallel(
    urls: Sequence[Any],
    dests: Sequence[str],
    expected_size: Any = math.nan,
    _retried: bool = False,
    skip_on_api_limit: bool = False,
) -> list[str | None]:
    """Port of ``R/repo-download.R::.download_many_parallel()``: download files concurrently.

    Only for hosts verified not to need a throttle (OSF osfstorage and Zenodo
    files resolve to pre-signed cloud-storage URLs). Returns one entry per
    URL: ``None`` on success, else an error (``"HTTP 404"``, ``"truncated (x
    of y bytes)"`` when *expected_size* is known and not met -- retried once --,
    ``"not authorised (...)"`` for a sign-in page, ...).
    """
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    urls = list(urls)
    dests = [str(d) for d in dests]
    n = len(urls)
    if n == 0:
        return []
    if isinstance(expected_size, str) or not isinstance(expected_size, Iterable):
        expected_size = [expected_size]
    exp_in = _nums(expected_size)
    expected = [exp_in[i % len(exp_in)] for i in range(n)] if exp_in else [math.nan] * n
    for d in dests:
        _dir_create(os.path.dirname(d))

    with ThreadPoolExecutor(max_workers=min(8, n)) as pool:
        futures = [
            pool.submit(
                copy_context().run, _parallel_one, urls[i], dests[i], expected[i], skip_on_api_limit
            )
            for i in range(n)
        ]
        results = [f.result() for f in futures]

    errs: list[str | None] = []
    for i, (resp, exc) in enumerate(results):
        dest = dests[i]
        if isinstance(resp, str):
            errs.append(resp)  # "bad URL"
            continue
        if exc is not None:
            if os.path.exists(dest):
                _unlink(dest)
            errs.append(str(exc))
            continue
        sc = int(resp.status_code)
        if sc not in (200, 0):
            if os.path.exists(dest):
                _unlink(dest)
            if skip_on_api_limit is True and sc == 429 and not is_na(_rate_limit_wait(resp)):
                errs.append(f"API rate limit exhausted: HTTP {sc}")
            else:
                errs.append(f"HTTP {sc:d}")
            continue
        if not os.path.exists(dest) or (os.path.getsize(dest) == 0 and expected[i] != 0):
            _unlink(dest)  # not left to be taken as cached next time (U75)
            errs.append("empty response")
            continue
        if _is_login_page(dest, expected[i]):
            _unlink(dest)
            errs.append("not authorised (the OSF returned a sign-in page; see ?osf_pat)")
            continue
        exp = expected[i]
        if not is_na(exp) and exp > 0:
            try:
                got = float(os.path.getsize(dest))
            except OSError:
                errs.append("download failed (nothing was written)")
                continue
            if got != exp:
                _unlink(dest)
                errs.append(f"truncated ({got:.0f} of {exp:.0f} bytes)")
                continue
        errs.append(None)

    # One retry pass for size mismatches only (transient under a parallel burst).
    if not _retried:
        retry = [i for i, e in enumerate(errs) if e is not None and e.startswith("truncated (")]
        if retry:
            again = _download_many_parallel(
                [urls[i] for i in retry],
                [dests[i] for i in retry],
                [expected[i] for i in retry],
                _retried=True,
                skip_on_api_limit=skip_on_api_limit,
            )
            for i, e in zip(retry, again, strict=True):
                errs[i] = e
    return errs


def _zip_names(zf: Any) -> list[str]:
    from metacheck.archives.zip_peek import _zip_names as names

    return names(zf)


def _download_zip_to_cache(
    files: pd.DataFrame,
    row_idx: Sequence[int],
    zip_url: str,
    strip_dir: bool = False,
    req_func: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    timeout_s: float = 120,
    max_bytes: float = math.inf,
    skip_on_api_limit: bool = False,
    expected_bytes: float = math.nan,
    _inplace: bool = False,
) -> pd.DataFrame:
    """Port of ``R/repo-download.R::.download_zip_to_cache()``.

    Downloads a whole repository as one zip archive and extracts the rows
    *row_idx* (0-based positions in *files*) into their ``.cache_path``,
    filling ``file_location`` for each file found in the archive (matched on
    ``file_path``, or ``file_name`` where that is empty; *strip_dir* drops
    the archive's top-level folder first, as GitHub/GitLab add one). The
    transfer is streamed and aborted past *max_bytes* or the (size-scaled)
    *timeout_s*; a 429 with a confirmed reset is waited out once (unless
    ``skip_on_api_limit``). Rows not filled are left for the caller's
    file-by-file fallback. *req_func* adds the host's headers to the request
    spec.
    """
    import zipfile

    import httpx

    from metacheck import http
    from metacheck._r import r_round
    from metacheck.report.blocks import _cap_num

    if "file_path" not in files.columns and "file_name" not in files.columns:
        return files  # nothing to match the archive's members on: no download
    timeout_s = _zip_timeout_for_size(timeout_s, expected_bytes)
    skip = skip_on_api_limit is True  # R: isTRUE(skip_on_api_limit), the argument only

    fd, zip_tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    extract_dir: str | None = None
    try:
        resp: Any = None
        conn_err: Exception | None = None
        spec: dict[str, Any] = {"method": "GET", "url": zip_url, "headers": {}}
        if req_func is not None:
            spec = req_func(spec)
        client = http.client()
        dl_err: str | None = None
        for attempt in (1, 2):
            try:
                req = client.build_request(
                    "GET",
                    zip_url,
                    headers=dict(spec.get("headers") or {}),
                    timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 20.0)),
                )
                attempt_resp = client.send(req, stream=True, follow_redirects=True)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                conn_err = e
                break
            if attempt_resp.status_code == 429:
                wait = _rate_limit_wait(attempt_resp)
                host = _host(attempt_resp.request.url)
                attempt_resp.close()
                if not skip and attempt == 1 and not is_na(wait) and wait > 0:
                    _announce_rate_limit_wait(wait, host=host)
                    http.sleep(wait)
                    continue
                return files
            resp = attempt_resp
            break
        if resp is None:
            if conn_err is not None:
                _message(
                    f"Zip download failed ({zip_url}): Failed to perform HTTP request.\n"
                    f"Caused by error: {conn_err}"
                )
            return files

        try:
            written = 0
            over_cap = False
            timed_out = False
            deadline = time.monotonic() + timeout_s
            with open(zip_tmp, "wb") as con:
                for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                    if time.monotonic() > deadline:
                        timed_out = True
                        break
                    con.write(chunk)
                    written += len(chunk)
                    if math.isfinite(max_bytes) and written > max_bytes:
                        over_cap = True
                        break
            if over_cap:
                cap = _cap_num(float(r_round(max_bytes / _MB)))
                dl_err = f"archive exceeded the {cap} MB cap during download and was aborted"
            elif timed_out:
                # a whole number of seconds (metacheck's sprintf("%d") fails on the
                # size-scaled fractional timeout: U73)
                dl_err = f"archive download exceeded the {math.ceil(timeout_s):d}s timeout"
            elif not os.path.exists(zip_tmp) or os.path.getsize(zip_tmp) == 0:
                dl_err = "empty response"
        except Exception as e:
            dl_err = str(e)
        finally:
            resp.close()
        if dl_err is not None:
            _message(f"Zip download failed ({zip_url}): {dl_err}")
            return files

        try:
            zf = zipfile.ZipFile(zip_tmp)
        except Exception:
            return files
        with zf:
            infos = zf.infolist()
            names = _zip_names(zf)
            if not infos:
                return files
            entries = [
                (nm, info) for nm, info in zip(names, infos, strict=True) if not nm.endswith("/")
            ]
            zip_entries = [nm for nm, _ in entries]
            lookup = (
                [sub("^[^/]*/", "", nm) for nm in zip_entries] if strip_dir else list(zip_entries)
            )
            lookup = [gsub("^/+", "", gsub(r"\\", "/", p)) for p in lookup]
            first: dict[str, int] = {}
            for k, p in enumerate(lookup):
                first.setdefault(p, k)

            # without a file_path column the file names are matched (metacheck
            # matches nothing, having downloaded the whole archive: U73)
            paths = (
                files["file_path"].tolist() if "file_path" in files.columns else [None] * len(files)
            )
            fnames = (
                files["file_name"].tolist() if "file_name" in files.columns else [None] * len(files)
            )
            cache = files[".cache_path"].tolist()
            loc_col = int(files.columns.get_loc("file_location"))  # type: ignore[arg-type]
            matched: list[int | None] = []
            for i in row_idx:
                rel = paths[i]
                if _is_missing(rel) or rel == "":
                    rel = fnames[i]
                if _is_missing(rel):
                    matched.append(None)  # R: match(NA, lookup_paths) is NA
                    continue
                rel = gsub("^/+", "", gsub(r"\\", "/", str(rel)))
                matched.append(first.get(rel))
            if all(m is None for m in matched):
                return files

            if not _inplace:  # download_repo_files() passes its own copy
                files = files.copy()
            for i, m in zip(row_idx, matched, strict=True):
                if m is None:
                    continue
                info = entries[m][1]
                dest = str(cache[i])
                try:
                    with zf.open(info) as src:
                        data = src.read()
                except Exception:  # noqa: S112 - R: an entry unzip() could not extract
                    continue
                if not data:
                    continue
                try:
                    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                    with atomic_write(dest) as fh:
                        fh.write(data)
                except OSError:
                    continue
                files.iat[i, loc_col] = dest
        return files
    finally:
        _unlink(zip_tmp)
        if extract_dir is not None:
            shutil.rmtree(extract_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# download_repo_files()
# ---------------------------------------------------------------------------


def _loc(df: pd.DataFrame) -> int:
    """The position of the ``file_location`` column."""
    return int(df.columns.get_loc("file_location"))  # type: ignore[arg-type]


def _frame(cols: dict[str, tuple[list[Any], str]]) -> pd.DataFrame:
    import pandas as pd

    return pd.DataFrame({k: pd.Series(v, dtype=t) for k, (v, t) in cols.items()})


def _cap_report(msg: str) -> None:
    """``.cap_report()`` (R/cap-prompt.R): show *msg* now and raise it as a warning."""
    from metacheck.llm import cap_prompt

    report = getattr(cap_prompt, "_cap_report", None) or cap_prompt.cap_report
    report(msg)


def _col(files: pd.DataFrame, name: str) -> list[Any] | None:
    if name not in files.columns:
        return None
    return [None if _is_missing(v) else v for v in files[name].tolist()]


def _headers_fn(
    fn: Callable[[dict[str, str] | None], dict[str, str]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt a headers function (``.github_config``/``.gitlab_config``) to a request spec."""

    def req_func(spec: dict[str, Any]) -> dict[str, Any]:
        out = dict(spec)
        out["headers"] = fn(dict(spec.get("headers") or {}))
        return out

    return req_func


def _url_encode_reserved(x: str) -> str:
    """``utils::URLencode(x, reserved = TRUE)``."""
    from metacheck.archives.github import _url_encode

    return _url_encode(x, reserved=True)


def _r_order(keys: list[tuple[float, str]]) -> list[int]:
    """``order(size, path)``: by size, ties by path in R's string collation."""
    from metacheck._r import r_sort_key

    return sorted(range(len(keys)), key=lambda k: (keys[k][0], r_sort_key(keys[k][1])))


def download_repo_files(
    files: pd.DataFrame | None,
    max_file_size: float = 100,
    max_download_size: float = 500,
    max_files_per_repo: float = math.inf,
    repo_file_counts: Mapping[str, float] | None = None,
    zip_timeout_s: float = 120,
    cache: bool = False,
    skip_on_api_limit: bool = False,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Download the files listed by ``repo_check()``.

    Port of ``R/repo-download.R::download_repo_files()``. Fetches the bytes
    of a table of repository files (``repo_url``, ``file_url``, and
    ``file_path`` or ``file_name``; ``file_size`` in bytes when known) into
    the per-session temporary directory (``cache=False``) or the persistent
    cache (``cache=True``, see :func:`repo_cache_dir`), reusing anything
    already there, and fills ``file_location`` for each file retrieved.
    Rows from ``repo_check``'s zip-peek expansion (``archive_url`` +
    ``archive_member``, no ``file_url``) are fetched from their archive by
    byte range.

    Size gates: ``max_file_size`` (MB) skips oversize files individually;
    ``max_download_size`` (MB) budgets a repository's total cached footprint
    (cached files count for free, then the smallest missing files are added
    while they fit); ``max_files_per_repo`` refuses a repository with more
    files outright -- counted from ``repo_file_counts`` (``repo_url`` ->
    the full listing's count) when given.

    Returns a copy of *files* with ``file_location`` filled, and
    ``attrs["gated"]`` (``repo_url``, ``message``: repositories refused or
    partially filled by the caps), ``attrs["oversize_skipped"]``
    (``repo_url``, ``file_name``, ``file_size``) and ``attrs["failed"]``
    (``repo_url``, ``file_name``, ``file_url``, ``paper_id``, ``error``:
    downloads that errored; with ``skip_on_api_limit`` an exhausted host
    quota is reported with an ``"API rate limit exhausted: "`` prefix
    instead of being waited out).
    """
    import pandas as pd

    from metacheck.report.blocks import _cap_num
    from metacheck.utils import _safe_write_path, get_option, options

    if files is None or len(files) == 0:
        return files
    index = files.index
    df = files.copy().reset_index(drop=True)
    n = len(df)
    if "file_location" not in df.columns:
        df["file_location"] = pd.Series([None] * n, dtype=object)
    else:
        df["file_location"] = pd.Series(
            [None if _is_missing(v) else v for v in df["file_location"].tolist()], dtype=object
        )

    cache_root = None if cache is True else _repo_session_dir()

    def cache_path(repo_url: Any, file_path: Any) -> str:
        if cache_root is None:
            return str(_repo_cache_path(repo_url, file_path))
        return _file_path(cache_root, str(_repo_cache_rel(repo_url, file_path)))

    # a missing repo_url column is R's NULL (cache key "unknown"); a missing value is NA
    repo_urls: list[Any] = (
        [pd.NA if _is_missing(v) else v for v in df["repo_url"].tolist()]
        if ("repo_url" in df.columns)
        else [None] * n
    )
    file_names = _col(df, "file_name") or [None] * n
    rel_path = _col(df, "file_path")
    if rel_path is None:
        rel_path = list(file_names)
    rel_path = [fn if p is None else p for p, fn in zip(rel_path, file_names, strict=True)]
    _sweep_repo_cache_once()
    cache_paths = [_safe_write_path(cache_path(repo_urls[i], rel_path[i])) or "" for i in range(n)]
    df[".cache_path"] = pd.Series(cache_paths, dtype=object)

    already = [os.path.exists(p) for p in cache_paths]
    loc_col = int(df.columns.get_loc("file_location"))  # type: ignore[arg-type]
    for i in range(n):
        if already[i]:
            df.iat[i, loc_col] = cache_paths[i]

    gated_rows: list[tuple[Any, str]] = []
    oversize_rows: list[tuple[Any, Any, float]] = []
    # defined before the archive-member block, which records into it too (#429)
    failed_rows: list[tuple[Any, Any, Any, Any, str | None]] = []
    paper_ids = _col(df, "paper_id")

    def paper_id(i: int) -> Any:
        return None if paper_ids is None else paper_ids[i]

    # which(files$repo_url == repo), computed once: rows per repository (NA rows in none)
    rows_of: dict[Any, list[int]] = {}
    for i, v in enumerate(repo_urls):
        if not _is_missing(v):
            rows_of.setdefault(v, []).append(i)

    def rows_for(repo: Any) -> list[int]:
        return [] if _is_missing(repo) else rows_of.get(repo, [])

    sizes_col = _col(df, "file_size")

    # -- archive members (repo_check's zip-peek expansion) --------------------
    if "archive_url" in df.columns:
        from metacheck.archives.zip_peek import _zip_fetch_members

        arcs = _col(df, "archive_url") or [None] * n
        members = _col(df, "archive_member") or [None] * n
        is_member = [
            not already[i] and arcs[i] is not None and str(arcs[i]) != "" and members[i] is not None
            for i in range(n)
        ]
        by_arc: dict[Any, list[int]] = {}
        for i in range(n):
            if is_member[i]:
                by_arc.setdefault(arcs[i], []).append(i)
        for arc, idx in by_arc.items():
            if sizes_col is None:
                continue  # R: as.numeric(NULL) leaves no candidates
            member_sizes = [_num(sizes_col[i]) for i in idx]
            over = [not is_na(s) and s > max_file_size * _MB for s in member_sizes]
            for i, s, o in zip(idx, member_sizes, over, strict=True):
                if o:
                    oversize_rows.append((repo_urls[i], file_names[i], s))
            cand = [
                i for i, s, o in zip(idx, member_sizes, over, strict=True) if not o and not is_na(s)
            ]
            cand_size = [
                s for s, o in zip(member_sizes, over, strict=True) if not o and not is_na(s)
            ]
            if not cand:
                continue
            cap_bytes = max_download_size * _MB if math.isfinite(max_download_size) else math.inf
            order = sorted(range(len(cand)), key=lambda k: cand_size[k])
            used = 0.0
            keep = [False] * len(cand)
            for k in order:
                if used + cand_size[k] <= cap_bytes:
                    keep[k] = True
                    used += cand_size[k]
            if not all(keep) and math.isfinite(cap_bytes):
                n_out = keep.count(False)
                msg = (
                    f"An archive in repository {_chr(repo_urls[idx[0]]) or 'NA'} exceeds the "
                    f"{_cap_num(max_download_size)} MB per-repository budget: fetched the "
                    f"smallest members up to the cap, {n_out} member{plural(n_out)} omitted. "
                    "Raise `max_download_size` to include more."
                )
                _cap_report(msg)
                gated_rows.append((repo_urls[idx[0]], msg))
            idx = [i for i, k in zip(cand, keep, strict=True) if k]
            if not idx:
                continue
            arc_key = gsub("[^A-Za-z0-9._-]+", "_", gsub("^https?://", "", str(arc)))
            member_dest = cache_path(repo_urls[idx[0]], f".archive_members/{arc_key}") + ".contents"
            member_dest = _safe_write_path(member_dest) or member_dest
            _dir_create(member_dest)
            # why a member was not fetched is recorded in `failed` (#429)
            try:
                fetched = _zip_fetch_members(
                    str(arc),
                    names=[str(members[i]) for i in idx],
                    dest=member_dest,
                    cache=cache,
                    skip_on_api_limit=skip_on_api_limit,
                )
            except Exception as e:
                why: str | None = str(e)
                fetched = None
            else:
                why = "could not list the archive (host may not support range requests)"
            if fetched is None:
                # R: repo_url of the archive's first row, the rest per row
                failed_rows.extend(
                    (repo_urls[idx[0]], file_names[i], arc, paper_id(i), why) for i in idx
                )
                continue
            f_names = fetched["name"].tolist()
            f_ok = fetched["ok"].tolist() if "ok" in fetched.columns else [None] * len(fetched)
            f_path = (
                fetched["path"].tolist() if "path" in fetched.columns else [None] * len(fetched)
            )
            f_error = fetched["error"].tolist() if "error" in fetched.columns else None
            # fetched[fetched$name == member, ][1, ]
            first: dict[Any, int] = {}
            for j, name in enumerate(f_names):
                first.setdefault(name, j)
            for k in idx:
                j = first.get(members[k])  # type: ignore[assignment]
                if j is None:
                    continue
                if f_ok[j] is not True or _is_missing(f_path[j]):
                    why = "unknown failure" if f_error is None else _chr(f_error[j])
                    failed_rows.append((repo_urls[k], file_names[k], arc, paper_id(k), why))
                    continue
                df.iat[k, loc_col] = f_path[j]

    file_urls = _col(df, "file_url") or [None] * n
    has_url = [u is not None and str(u) != "" for u in file_urls]
    to_get: list[int] = []

    # -- per-repository budget + per-file size filter -------------------------
    for repo in dict.fromkeys(repo_urls[i] for i in range(n) if has_url[i]):
        idx = [i for i in rows_for(repo) if has_url[i]]
        if not idx:
            continue
        if repo_file_counts is not None and repo in repo_file_counts:
            true_n = _num(repo_file_counts[repo])
        else:
            true_n = float(len(idx))
        if math.isfinite(max_files_per_repo) and true_n > max_files_per_repo:
            msg = (
                f"Repository {repo} holds {_fmt_int(true_n)} files, exceeding the "
                f"{_fmt_int(max_files_per_repo)}-file cap: skipped entirely (not size-capped, "
                "file-count-capped). Raise `max_files_per_repo` to include it."
            )
            _cap_report(msg)
            gated_rows.append((repo, msg))
            continue

        is_cached = [already[i] for i in idx]
        if sizes_col is None:
            continue  # R: as.numeric(NULL) leaves no candidates
        sizes = [_num(sizes_col[i]) for i in idx]
        for k, s in enumerate(sizes):
            if not is_na(s):
                continue
            if is_cached[k]:
                try:
                    sz = float(os.path.getsize(cache_paths[idx[k]]))
                except OSError:
                    sz = math.nan
                sizes[k] = sz if math.isfinite(sz) else math.nan
            else:
                sizes[k] = _remote_size(str(file_urls[idx[k]]))

        if math.isfinite(max_file_size):
            over = [not is_na(s) and s > max_file_size * _MB for s in sizes]
        else:
            over = [False] * len(idx)
        for k, i in enumerate(idx):
            if over[k] and not is_cached[k]:
                oversize_rows.append((repo, file_names[i], sizes[k]))

        cand = [not o and not is_na(s) for o, s in zip(over, sizes, strict=True)]
        if not any(cand):
            continue
        c_idx = [i for i, c in zip(idx, cand, strict=True) if c]
        c_size = [s for s, c in zip(sizes, cand, strict=True) if c]
        c_cached = [x for x, c in zip(is_cached, cand, strict=True) if c]

        cap_bytes = max_download_size * _MB if math.isfinite(max_download_size) else math.inf
        used = sum(s for s, cc in zip(c_size, c_cached, strict=True) if cc)
        missing = [k for k, cc in enumerate(c_cached) if not cc]
        order = _r_order([(c_size[k], cache_paths[c_idx[k]]) for k in missing])
        missing_order = [missing[o] for o in order]
        take_missing: list[int] = []
        for k in missing_order:
            if used + c_size[k] <= cap_bytes:
                take_missing.append(k)
                used += c_size[k]
        to_get.extend(c_idx[k] for k in take_missing)

        taken = set(take_missing)
        omitted = [k for k in missing_order if k not in taken]
        if omitted and math.isfinite(cap_bytes):
            msg = (
                f"Repository {repo} exceeds the {_cap_num(max_download_size)} MB per-repository "
                "budget: downloaded the smallest files up to the cap, "
                f"{len(omitted)} file{plural(len(omitted))} omitted. "
                "Raise `max_download_size` to include more."
            )
            _cap_report(msg)
            gated_rows.append((repo, msg))

    # -- report the individually skipped oversize files -----------------------
    if oversize_rows:
        from metacheck._r import r_round

        for repo in dict.fromkeys(r[0] for r in oversize_rows):
            rows = [r for r in oversize_rows if not _is_missing(r[0]) and r[0] == repo]
            if _is_missing(repo):
                rows = [r for r in oversize_rows if _is_missing(r[0])]
            k = len(rows)
            largest = sorted(rows, key=lambda r: -r[2])[0]
            _message(
                f"{k} file{plural(k)} in {_chr(repo) or 'NA'} exceeded the "
                f"{_cap_num(max_file_size)} MB per-file limit and {'was' if k == 1 else 'were'} "
                "skipped (the rest of the repository was downloaded). Largest: "
                f"{_chr(largest[1]) or 'NA'} "
                f"({_cap_num(float(r_round(max(r[2] for r in rows) / _MB)))} MB). "
                "Raise max_file_size to include them."
            )

    # -- download what passed the gates ----------------------------------------
    # Dryad, GitHub and GitLab use a whole-repository archive; every other host
    # (and any repository whose archive is not used or fails) goes file by
    # file. OSF ?zip=, Zenodo files-archive and Dataverse's dataset download
    # never report their size, so metacheck removed those archive routes. No
    # archive reports its size, so it is estimated from the listed file sizes:
    # no estimate, or one above 2x the budget, skips the archive, and the
    # download itself stops past 2x the budget (archive_cap).
    zip_kw: dict[str, Any] = {
        "timeout_s": zip_timeout_s,
        "skip_on_api_limit": skip_on_api_limit,
    }
    archive_cap = 2 * max_download_size * _MB  # inf when there is no budget

    def location(i: int) -> Any:
        return df.iat[i, _loc(df)]

    if to_get:
        # `remaining` keeps to_get's order; rows leave it as zips fill them
        remaining_set = set(to_get)
        pos = {i: k for k, i in enumerate(to_get)}
        repo_list = list(repo_urls)
        providers = _col(df, "provider")

        def remaining_rows() -> list[int]:
            return [i for i in to_get if i in remaining_set]

        def in_remaining(pattern: str) -> list[Any]:
            rows = remaining_rows()
            hits = grepl(pattern, [_chr(repo_list[i]) for i in rows], ignore_case=True)
            return list(dict.fromkeys(repo_list[i] for i, h in zip(rows, hits, strict=True) if h))

        def is_osfstorage(i: int) -> bool:
            # picks the downloads that are safe to run in parallel
            if providers is not None and providers[i] is not None:
                return str(providers[i]).lower() == "osfstorage"
            url = file_urls[i]
            if url is None or str(url) == "":
                return False
            return bool(grepl("/providers/osfstorage/", str(url), ignore_case=True))

        def record_count(repo: Any) -> float:
            """The rows of *repo* (metacheck's ``sum(files$repo_url == repo)`` is NA
            when any repo_url is NA, which skips the zip: U74)."""
            return float(len(rows_for(repo)))

        def in_repo(repo: Any) -> list[int]:
            """``intersect(remaining, which(files$repo_url == repo))``."""
            return sorted((i for i in rows_for(repo) if i in remaining_set), key=pos.__getitem__)

        def expected_of(rows: list[int]) -> float:
            if sizes_col is None:
                return 0.0
            vals = [_num(sizes_col[i]) for i in rows]
            return float(sum(v for v in vals if not is_na(v)))

        def filled(rows: list[int]) -> set[int]:
            # rows the zip wrote to their cache path (metacheck counts any
            # file_location, so a row that arrived with a stale one is never
            # fetched: U74)
            return {i for i in rows if location(i) == cache_paths[i]}

        def run_zip(rows: list[int], zip_url: str, **kw: Any) -> None:
            nonlocal df
            df = _download_zip_to_cache(df, rows, zip_url, _inplace=True, **kw)
            remaining_set.difference_update(filled(rows))

        # Dryad: whole-dataset archive (quota-aware)
        from metacheck.archives.dryad import _dryad_headers

        for repo in in_remaining(r"datadryad\.org|doi\.org/10\.5061/dryad"):
            ridx = in_repo(repo)
            if not ridx:
                continue
            try:
                from metacheck.archives.dryad import _dryad_doi

                doi = _dryad_doi(repo)
            except Exception:
                doi = None
            if not isinstance(doi, str) or doi == "":
                continue
            encoded = _url_encode_reserved(f"doi:{doi}")
            zip_url = f"https://datadryad.org/api/v2/datasets/{encoded}/download"
            zip_bytes = _archive_size_estimate(df, repo)
            expected = expected_of(ridx)
            n_wanted = len(ridx)
            record_n = record_count(repo)
            size_why = _archive_size_refusal(zip_bytes, max_download_size, _MB)
            # Dryad's zip quota (100/day) is 5x stricter than its per-file one
            quota_worth_it = n_wanted > 12
            worth_it = (n_wanted > 50 or record_n <= 2 * n_wanted) and quota_worth_it
            if size_why is not None or not worth_it:
                if size_why is not None:
                    why = size_why
                elif not quota_worth_it:
                    why = (
                        f"only {n_wanted} file{plural(n_wanted)} wanted -- Dryad's zip quota "
                        "(100/day) is 5x stricter than its per-file quota (500/day), not worth "
                        "spending on a small dataset"
                    )
                else:
                    why = (
                        f"dataset holds {_fmt_int(record_n)} files for {n_wanted} wanted (>2x) "
                        "and wanted <= 50"
                    )
                _message(f"Skipping zip for {repo} ({why}); downloading its files individually.")
                continue
            _message(f"Downloading {repo} as zip ({n_wanted} file{plural(n_wanted)})...")
            run_zip(
                ridx,
                zip_url,
                strip_dir=False,
                req_func=_dryad_headers,
                max_bytes=archive_cap,
                expected_bytes=expected,
                **zip_kw,
            )

        # GitHub: API zipball; GitLab: repository archive.zip
        from metacheck.archives.github import _github_config
        from metacheck.archives.gitlab import _gitlab_config

        for pattern, host_kind in ((r"github\.com", "github"), (r"gitlab\.com", "gitlab")):
            for repo in in_remaining(pattern):
                ridx = in_repo(repo)
                if not ridx:
                    continue
                if host_kind == "github":
                    try:
                        from metacheck.archives.github import github_repo

                        clean_repo = github_repo(repo)
                    except Exception:
                        clean_repo = None
                    if clean_repo is None:
                        continue
                    zip_url = f"https://api.github.com/repos/{clean_repo}/zipball"
                    req_func = _headers_fn(_github_config)
                else:
                    try:
                        from metacheck.archives.gitlab import gitlab_repo

                        clean_repo = gitlab_repo(repo)
                    except Exception:
                        clean_repo = None
                    if clean_repo is None:
                        continue
                    from metacheck.archives.gitlab import _gitlab_project_id

                    proj_id = _gitlab_project_id(clean_repo)
                    zip_url = f"https://gitlab.com/api/v4/projects/{proj_id}/repository/archive.zip"
                    req_func = _headers_fn(_gitlab_config)
                size_why = _archive_size_refusal(
                    _archive_size_estimate(df, repo), max_download_size, _MB
                )
                if size_why is not None:
                    _message(
                        f"Skipping zip for {repo} ({size_why}); downloading its files individually."
                    )
                    continue
                expected = expected_of(ridx)
                _message(f"Downloading {repo} as zip ({len(ridx)} file{plural(len(ridx))})...")
                run_zip(
                    ridx,
                    zip_url,
                    strip_dir=True,
                    req_func=req_func,
                    max_bytes=archive_cap,
                    expected_bytes=expected,
                    **zip_kw,
                )

        # File by file: ResearchBox and every zip fallback
        remaining = remaining_rows()
        if remaining:
            own_pb = pb is None
            if own_pb:
                from metacheck.utils import pb as make_pb

                pb = make_pb(len(remaining), "Downloading files [:bar] :current/:total")
            try:
                cache_now = df[".cache_path"].tolist()
                zen = grepl(
                    r"zenodo\.org", [_chr(file_urls[i]) for i in remaining], ignore_case=True
                )
                parallel_safe = [
                    is_osfstorage(i) or bool(z) for i, z in zip(remaining, zen, strict=True)
                ]
                remaining_parallel = [i for i, p in zip(remaining, parallel_safe, strict=True) if p]
                remaining_seq = [i for i, p in zip(remaining, parallel_safe, strict=True) if not p]

                def fail(i: int, err: str) -> None:
                    failed_rows.append(
                        (repo_urls[i], file_names[i], file_urls[i], paper_id(i), err)
                    )

                if remaining_parallel:
                    errs = _download_many_parallel(
                        [file_urls[i] for i in remaining_parallel],
                        [cache_now[i] for i in remaining_parallel],
                        [
                            _num(sizes_col[i]) if sizes_col is not None else math.nan
                            for i in remaining_parallel
                        ],
                        skip_on_api_limit=skip_on_api_limit,
                    )
                    for i, err in zip(remaining_parallel, errs, strict=True):
                        if err is None:
                            df.iat[i, _loc(df)] = cache_now[i]
                        else:
                            fail(i, err)
                        if pb is not None:
                            pb.tick()
                for i in remaining_seq:
                    err = _download_one(
                        str(file_urls[i]),
                        cache_now[i],
                        skip_on_api_limit=skip_on_api_limit,
                        expected_bytes=_num(sizes_col[i]) if sizes_col is not None else math.nan,
                    )
                    if err is None:
                        df.iat[i, _loc(df)] = cache_now[i]
                    else:
                        fail(i, err)
                    if pb is not None:
                        pb.tick()
            finally:
                if own_pb and pb is not None:
                    pb.terminate()

    # -- report failures ------------------------------------------------------
    if failed_rows:
        for repo in dict.fromkeys(r[0] for r in failed_rows):
            frows = [
                r for r in failed_rows if r[0] is repo or (not _is_missing(r[0]) and r[0] == repo)
            ]
            k = len(frows)
            first_err = sub("\n.*", "", frows[0][4])
            _message(
                f"{k} download{plural(k)} from {_chr(repo) or 'NA'} failed after retries "
                f"(e.g. {_chr(frows[0][1]) or 'NA'}: {first_err}). Re-run to retry: cached "
                "files are reused, only the missing files are fetched."
            )

    if cache is True and to_get and get_option("metacheck.repo_cache.notified") is not True:
        _message(
            f"Downloaded files are cached in {_repo_cache_dir()} and reused on re-runs (never "
            "cleared automatically). Free the space with repo_cache_clear()."
        )
        options({"metacheck.repo_cache.notified": True})

    out = df.drop(columns=[".cache_path"])
    out["file_location"] = pd.Series(
        [None if _is_missing(v) else str(v) for v in out["file_location"].tolist()],
        dtype="string",
    )
    out.index = index
    out.attrs["gated"] = _frame(
        {
            "repo_url": ([_chr(r[0]) for r in gated_rows], "string"),
            "message": ([r[1] for r in gated_rows], "string"),
        }
    )
    out.attrs["oversize_skipped"] = _frame(
        {
            "repo_url": ([_chr(r[0]) for r in oversize_rows], "string"),
            "file_name": ([_chr(r[1]) for r in oversize_rows], "string"),
            "file_size": ([r[2] for r in oversize_rows], "float64"),
        }
    )
    out.attrs["failed"] = _frame(
        {
            "repo_url": ([_chr(r[0]) for r in failed_rows], "string"),
            "file_name": ([_chr(r[1]) for r in failed_rows], "string"),
            "file_url": ([_chr(r[2]) for r in failed_rows], "string"),
            "paper_id": ([_chr(r[3]) for r in failed_rows], "string"),
            "error": ([r[4] for r in failed_rows], "string"),
        }
    )
    return out


def _rround(x: float) -> float:
    from metacheck._r import r_round

    return float(r_round(x))
