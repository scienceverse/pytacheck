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

Every storage request goes through :func:`metacheck.http.request`, the one
retry, redirect and rate-limit stack (:func:`_storage_request` adds what is
specific to file bytes): host authentication (:func:`_auth_for_url`), 5 tries
with retries on 403/429/5xx and connection failures, jittered backoff, and a
rate limit's reset waited out -- or, with ``skip_on_api_limit``, given up on
at once. A reset is remembered per host in :mod:`metacheck.http`'s rate-limit
memory, so later requests to that host wait up front instead of
rediscovering the 429. The OSF and Zenodo token is kept on every redirect hop
to their storage hosts (``resend_headers``).
"""

from __future__ import annotations

import atexit
import functools
import math
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from metacheck._env import env_get
from metacheck._r import grepl, gsub, plural, sub
from metacheck._values import as_float, as_str, is_missing
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


def _num(x: Any) -> float:
    """One value as R double (``NA``/unparseable -> ``nan``)."""
    v = as_float(x)
    return math.nan if v is None else v


def _nums(values: Iterable[Any]) -> list[float]:
    """``as.numeric()`` of a vector (``NA`` -> ``nan``)."""
    return [_num(v) for v in values]


def _fmt_int(x: float) -> str:
    """``sprintf("%d", x)`` for a count; a fractional value is shown as it is
    (R's ``sprintf("%d")`` fails on it, U73)."""
    from metacheck._r import as_character

    if is_missing(x):
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
    elif is_missing(repo_url):
        return "NA"  # R: gsub() keeps NA, and nzchar(NA) is TRUE
    else:
        key = repo_url if isinstance(repo_url, str) else (as_str(repo_url) or "NA")
    key = gsub("^https?://", "", key)  # scheme is noise
    key = gsub("[^A-Za-z0-9._-]+", "_", key)  # filesystem-safe
    key = gsub("^_+|_+$", "", key)
    return key if key != "" else "unknown"


def _repo_cache_subdir(repo_url: Any, cache: bool = True) -> str:
    """Port of ``R/repo-download.R::.repo_cache_subdir()``: a repository's cache folder.

    Keyed by a filesystem-safe encoding of the repository URL (scheme
    dropped, runs of other characters turned into ``_``), so different
    repositories never collide and the same one always resolves to the same
    folder. With ``cache=False`` the folder is in the session's temporary
    cache (:func:`_repo_session_dir`), which is removed when the process
    ends: a lasting cache is kept only when the caller asks for it.
    """
    root = _repo_cache_dir() if cache is True else _repo_session_dir()
    return f"{root}/{_repo_key(repo_url)}"


def _file_path(*parts: str) -> str:
    """R ``file.path()`` for scalars."""
    return "/".join(parts)


def _rel_one(key: str, file_path: Any) -> str:
    if is_missing(file_path):
        rel = "NA"
    else:
        rel = file_path if isinstance(file_path, str) else (as_str(file_path) or "NA")
        rel = gsub(r"\\", "/", rel)
        rel = gsub("^/+", "", rel)
    return _file_path(key, rel)


def _repo_cache_rel(repo_url: Any, file_path: Any, storage: Any = None) -> Any:
    """Port of ``R/repo-download.R::.repo_cache_rel()``: ``<repo key>/<repo-relative path>``.

    Backslashes become ``/`` and leading slashes are dropped. Vectorised over
    *file_path* (a sequence gives a list).

    *storage* is an OSF file's storage (``provider``): a storage other than OSF Storage, such as a
    GitHub repository linked to the project, gets a folder of its own (``_storage_github``), as
    its paths can be the same as OSF Storage's under the same repository address. osf.io/haz6u
    holds 458 files under the same paths in both, not always the same file; with one place for
    both, one copy replaced the other (metacheck #679). Any other file keeps its path.
    """
    key = _repo_key(repo_url)
    st = "" if storage is None or is_missing(storage) else str(storage)
    if st != "" and st.lower() != "osfstorage":
        key = _file_path(key, "_storage_" + gsub("[^A-Za-z0-9._-]+", "_", st))
    if isinstance(file_path, str) or is_missing(file_path):
        return _rel_one(key, file_path)
    return [_rel_one(key, p) for p in file_path]


def _repo_cache_path(repo_url: Any, file_path: Any, cache: bool = True, storage: Any = None) -> Any:
    """Port of ``R/repo-download.R::.repo_cache_path()``: a file's cache path.

    In the persistent cache, or with ``cache=False`` in the session's
    temporary one (:func:`_repo_session_dir`), with the same layout.
    """
    root = _repo_cache_dir() if cache is True else _repo_session_dir()
    rel = _repo_cache_rel(repo_url, file_path, storage)
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


class _ZipAborted(Exception):
    """A whole-archive download that went past its byte cap or its time limit."""


class _RateLimitSkipped(Exception):
    """A request that was not sent: its host is rate-limited and ``skip_on_api_limit`` is on."""

    def __init__(self) -> None:
        super().__init__("API rate limit exhausted: known rate-limited host, skip_on_api_limit")


def _storage_request(
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    skip_on_api_limit: bool = False,
    timeout: float | None = None,
    path: str | None = None,
    max_tries: int = 5,
    auth: bool = True,
    throttle: Any = None,
    error: bool = False,
    is_transient: Callable[[Any], bool] | None = None,
    read_body: Callable[[Any], bool] | None = None,
    sink: Callable[[Any], None] | None = None,
    content: bytes | None = None,
) -> Any:
    """A storage request: :func:`metacheck.http.request` with the host's authentication.

    The retry rule is R's ``.storage_is_transient()`` (403, 429, 5xx), or
    *is_transient* in its place; everything else (the tries, the backoff,
    redirects, a rate limit's reset) is :func:`metacheck.http.request`'s. With
    *error* ``False`` (R: ``req_error(is_error = \\(r) FALSE)``) every status is
    returned; with ``True`` a status >= 400 raises :class:`_HttpError`. A
    connection failure on the last try raises :class:`_RequestError`; a host
    known to be rate-limited, under ``skip_on_api_limit``, raises
    :class:`_RateLimitSkipped`. The body is read in full; or streamed to *path*
    (a temporary file moved onto it once complete); or, when *read_body* is
    given, read only if ``read_body(response)`` is true (httr2
    ``req_perform_connection()``: an unwanted body is closed unread); or handed
    to *sink*, which reads it. *throttle* is a :class:`metacheck.http.Throttle`.
    """
    import httpx

    from metacheck import http

    spec: dict[str, Any] = {"method": method, "url": url, "headers": dict(headers or {})}
    if auth:
        spec = _auth_for_url(spec)
        url = str(spec["url"])  # (an osf.io address may be sent to its file server)

    if url.lower().startswith("file:"):
        if throttle is not None:
            throttle.acquire(_host(url) or "local")
        resp = _file_response(method, url, path)
    else:

        def read(r: Any) -> None:
            if sink is not None:
                sink(r)
            elif read_body is not None and not read_body(r):
                return  # closed unread
            elif path is not None:
                with atomic_write(path) as fh:
                    for chunk in r.iter_bytes(chunk_size=1 << 20):
                        http.check_interrupt()
                        fh.write(chunk)
            else:
                r.read()

        try:
            with http.skip_on_api_limit(_skip(skip_on_api_limit)):
                resp = http.request(
                    method,
                    url,
                    headers=spec["headers"],
                    content=content,
                    timeout=httpx.Timeout(timeout if timeout is not None else 60.0, connect=20.0),
                    max_tries=max_tries,
                    is_transient=is_transient or _storage_is_transient,
                    throttle=throttle,
                    sink=read,
                    on_reset=_host_rate_limit_record,
                    raise_errors=True,
                )
        except http.RateLimited as e:
            raise _RateLimitSkipped() from e
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise _RequestError(
                "Failed to perform HTTP request.\n"
                f"Caused by error in `curl::curl_fetch_disk()`:\n! {e}"
            ) from e
        if resp is None:  # raise_errors makes this impossible
            raise _RequestError("Failed to perform HTTP request.")
    if error and resp.status_code >= 400:
        raise _HttpError(resp)
    return resp


def _download_failed_text(failed: Any) -> dict[str, Any]:
    """Port of ``R/repo-download.R::.download_failed_text()`` (metacheck #667).

    The report section and summary sentence for the files whose download
    failed. Until metacheck 0.4.4.12 a failure was said only in a message
    during the run, not in the report or the results, so a fault that lost
    the same kind of file in every repository (an address with a space) went
    unnoticed for three months. *failed* is the ``failed`` attribute of
    :func:`download_repo_files` (``repo_url``, ``file_name``, ``error``).
    Returns ``{"report": [...], "summary": "..."}``, both ``None`` when no
    file failed.
    """
    from metacheck._r import r_sort_key

    if failed is None or not hasattr(failed, "columns") or len(failed) == 0:
        return {"report": None, "summary": None}
    repos = failed["repo_url"].tolist() if "repo_url" in failed.columns else [None] * len(failed)
    names = failed["file_name"].tolist() if "file_name" in failed.columns else [None] * len(failed)
    errors = failed["error"].tolist() if "error" in failed.columns else [None] * len(failed)
    seen: set[str] = set()
    rows: list[tuple[str, str]] = []
    for r, f, e in zip(repos, names, errors, strict=True):
        key = f"{as_str(r) or 'NA'} {as_str(f) or 'NA'}"  # paste() of the two columns
        if key in seen:
            continue
        seen.add(key)
        rows.append((as_str(f) or "NA", as_str(e) or ""))
    n = len(rows)

    # the first line that says why: httr2 starts with "Failed to perform HTTP
    # request." and "Caused by error in ...:", and gives the reason after them
    def why_of(error: str) -> str:
        lines = [re.sub(r"^! ", "", ln).strip(" \t\r\n") for ln in error.split("\n")]
        keep = [
            ln
            for ln in lines
            if ln != ""
            and not re.search(r"^Failed to perform HTTP request\.?$|^Caused by error", ln)
        ]
        first = keep[0] if keep else (lines[0] if lines else "")
        # an empty or missing error text: metacheck stops on "" ("subscript out of bounds")
        # and gives a blank reason for NA (U256)
        return re.sub(r":$", "", first) or "no reason given"

    errs = [why_of(e) for _, e in rows]
    counts: dict[str, int] = {}
    for e in errs:
        counts[e] = counts.get(e, 0) + 1
    # table() sorts the reasons, sort(decreasing = TRUE) keeps ties in that order
    kinds = sorted(counts, key=r_sort_key)
    kinds = sorted(kinds, key=lambda k: -counts[k])
    why = "; ".join(f"{k} ({counts[k]})" for k in kinds[:3])
    shown = [name for name, _ in rows[:10]]
    more = f", and {n - 10} more" if n > 10 else ""
    listed = ", ".join(f"`{s}`" for s in shown)
    report = [
        "#### Files that could not be downloaded",
        f"{n} file{plural(n)} could not be downloaded, so the checks did not see "
        f"{plural(n, 'it', 'them')}: {listed}{more}. "
        f"The reason{plural(len(kinds), '', 's')} given: {why}. "
        "Re-running the check fetches only the missing files.",
    ]
    summary = (
        f"{n} file{plural(n)} could not be downloaded, so the checks did not see "
        f"{plural(n, 'it', 'them')}."
    )
    return {"report": report, "summary": summary}


def _failed_counts(failed: Any, paper_ids: Sequence[Any]) -> list[int]:
    """``files_failed`` and ``code_files_failed``: the files whose download failed, per paper
    (metacheck #667).

    A file is counted once however often it failed (``repo_url`` and ``file_name``); with one
    paper in the table, or no ``paper_id`` in *failed*, every failed file is its own.
    """
    n = len(paper_ids)
    if failed is None or not hasattr(failed, "columns") or len(failed) == 0:
        return [0] * n
    cols = failed.columns
    repos = failed["repo_url"].tolist() if "repo_url" in cols else [None] * len(failed)
    names = failed["file_name"].tolist() if "file_name" in cols else [None] * len(failed)
    seen: set[str] = set()
    keep: list[int] = []
    for i, (r, f) in enumerate(zip(repos, names, strict=True)):
        key = f"{as_str(r) or 'NA'} {as_str(f) or 'NA'}"  # paste() of the two columns
        if key not in seen:
            seen.add(key)
            keep.append(i)
    if n == 1 or "paper_id" not in cols:
        return [len(keep)] * n
    owners = [failed["paper_id"].iloc[i] for i in keep]
    return [sum(1 for o in owners if not is_missing(o) and o == pid) for pid in paper_ids]


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
    if is_missing(size):
        size = _range_size(url)
    return size


def _archive_size_estimate(files: pd.DataFrame, repo: Any) -> float:
    """Port of ``R/repo-download.R::.archive_size_estimate()``.

    The estimated size (bytes) of a repository's whole-archive download: the
    repository's own total where its listing reports one (``repo_size``:
    Dryad's ``storageSize``, carried by repo_check), else the sum of the file
    sizes listed for *repo* in *files*, or ``nan`` (R ``NA``) when none are
    listed. The sum covers only the rows passed in, often just the wanted
    files; the byte limit of the download is what actually bounds it.
    """
    repos = files["repo_url"].tolist()
    if "repo_size" in files.columns:
        totals = [
            s
            for r, s in zip(repos, _nums(files["repo_size"].tolist()), strict=False)
            if not is_missing(r) and r == repo and math.isfinite(s) and s > 0
        ]
        if totals:
            return float(max(totals))
    sizes = _nums(files["file_size"].tolist()) if "file_size" in files.columns else []
    est = sum(
        s
        for r, s in zip(repos, sizes, strict=False)
        if not is_missing(r) and r == repo and not is_missing(s)
    )
    return float(est) if est > 0 else math.nan


def _archive_size_refusal(zip_bytes: float, max_download_size: float, mb: float) -> str | None:
    """Port of ``R/repo-download.R::.archive_size_refusal()``.

    Why a whole-archive download is refused on size, or ``None`` (R ``NA``)
    when it is allowed: its size is unknown, or above 2x the per-repository
    budget (a one-request transport earns a 2x allowance).
    """
    from metacheck.report.blocks import _cap_num

    if is_missing(zip_bytes):
        return "archive size unknown: no file sizes listed"
    if math.isfinite(max_download_size) and zip_bytes > 2 * max_download_size * mb:
        return (
            f"estimated archive size {_cap_num(_rround(zip_bytes / mb))} MB exceeds 2x the "
            f"{_cap_num(max_download_size)} MB budget"
        )
    return None


def _storage_size_estimate(files: pd.DataFrame, idx: Sequence[int]) -> float:
    """Port of ``R/repo-download.R::.storage_size_estimate()`` (metacheck #675).

    The estimated size (bytes) of the archive of the storage that rows *idx*
    belong to: the storage's own listed total (repo_check's ``storage_size``,
    which does not depend on *files* holding every row), else the sum of
    these rows' sizes, or ``nan`` when neither is known.
    """
    if "storage_size" in files.columns:
        sel = files["storage_size"].tolist()
        totals = [t for t in _nums(sel[i] for i in idx) if math.isfinite(t) and t > 0]
        if totals:
            return float(max(totals))
    sizes = files["file_size"].tolist() if "file_size" in files.columns else []
    est = sum(s for s in _nums(sizes[i] for i in idx) if not is_missing(s))
    return float(est) if est > 0 else math.nan


def _archive_worth_it(
    n_wanted: float, record_n: float, wanted_bytes: float, archive_bytes: float
) -> bool:
    """Port of ``R/repo-download.R::.archive_worth_it()`` (metacheck #675).

    Whether the archive of a storage is worth downloading for the files
    wanted from it: it must save requests (more than 50 files wanted, or at
    least half of the storage's files) without bringing much data nobody
    asked for (at most twice the bytes of the wanted files): counting files
    alone would take a storage's whole archive for 51 small files beside one
    large file nobody wants. Wanted files of unknown size, or an archive of
    unknown size, are fetched one by one.
    """
    return bool(
        (n_wanted > 50 or record_n <= 2 * n_wanted)
        and math.isfinite(archive_bytes)
        and wanted_bytes > 0
        and archive_bytes <= 2 * wanted_bytes
    )


def _fmt_big(x: float) -> str:
    """``format(x, big.mark = ",")`` of a number."""
    from metacheck._r import format_num

    out = format_num(x)
    if "e" in out or out in ("NA", "NaN", "Inf", "-Inf"):
        return out
    whole, dot, frac = out.partition(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    return sign + f"{int(digits):,}" + dot + frac


def _storage_archive_skip(
    files: pd.DataFrame, idx: Sequence[int], max_download_size: float, mb: float
) -> str | None:
    """Port of ``R/repo-download.R::.storage_archive_skip()`` (metacheck #675).

    Why the archive of the storage that rows *idx* belong to is not
    downloaded for them, or ``None`` (R ``NA``) when it is: refused on size
    (:func:`_archive_size_refusal`) or not worth it (:func:`_archive_worth_it`).
    Used for the storages of an OSF project, whose size and number of files
    repo_check records (``storage_size``, ``storage_n``; else those of the
    rows passed).
    """
    from metacheck._r import format_num, r_round

    # Without the storage's own totals, the archive's size is not known: the rows passed are only
    # the files wanted, and when the paper linked single files they are not the storage either
    # (#679: 6 linked files, 35 KB, of a storage of 612 files, 8.5 MB). repo_check records the
    # totals only for a storage it listed in full (_osf_storage_totals).
    def _finite(col: str) -> bool:
        if col not in files.columns:
            return False
        vals = files[col].tolist()
        return any(math.isfinite(v) for v in _nums(vals[i] for i in idx) if not is_missing(v))

    if not _finite("storage_size") or not _finite("storage_n"):
        return "the size of the whole storage is not known, as it was not listed in full"
    archive_bytes = _storage_size_estimate(files, idx)
    size_why = _archive_size_refusal(archive_bytes, max_download_size, mb)
    if size_why is not None:
        return size_why
    n_wanted = len(idx)
    record_n = math.nan
    if "storage_n" in files.columns:
        col = files["storage_n"].tolist()
        counts = [c for c in _nums(col[i] for i in idx) if not is_missing(c)]
        if counts:
            record_n = max(counts)
    if not math.isfinite(record_n):
        record_n = float(n_wanted)
    sizes = files["file_size"].tolist() if "file_size" in files.columns else []
    wanted_bytes = sum(s for s in _nums(sizes[i] for i in idx) if not is_missing(s))
    if _archive_worth_it(n_wanted, record_n, wanted_bytes, archive_bytes):
        return None
    return (
        f"the archive holds {_fmt_big(record_n)} files "
        f"({format_num(float(r_round(archive_bytes / mb, 1)))} MB) for {n_wanted} wanted "
        f"({format_num(float(r_round(wanted_bytes / mb, 1)))} MB)"
    )


def _osf_storage_totals(files: Any, complete: Any = None) -> Any:
    """Port of ``R/repo-download.R::.osf_storage_totals()`` (metacheck #675).

    Each OSF file's storage (its component and provider, such as osfstorage or
    a linked GitHub repository): its listed total size (``storage_size``,
    ``NA`` when no size is listed) and number of files (``storage_n``).
    :func:`download_repo_files` decides from these whether to fetch the
    storage as one archive; it is passed only the files a check wants, so it
    cannot count the storage itself. A file listed twice (through a
    registration and its project) counts once. A table without ``osf_node``
    and ``provider`` is returned as it is.

    *complete* (a logical per row; default all ``True``) marks the rows of a listing that holds
    whole storages (a linked project, component or registration listed in full); only those are
    counted (#679). Rows from a link to a single file or folder are not the storage: six linked
    files of 35 KB were counted as a storage that holds 612 files and 8.5 MB, and its zip was
    fetched for them. A storage with no complete rows gets ``NA``, and its files are fetched one
    by one (:func:`_storage_archive_skip`).
    """
    import pandas as pd

    if (
        not isinstance(files, pd.DataFrame)
        or len(files) == 0
        or not {"osf_node", "provider", "file_size"} <= set(files.columns)
    ):
        return files
    node = files["osf_node"].tolist()
    provider = files["provider"].tolist()
    paths = files["file_path"].tolist() if "file_path" in files.columns else [None] * len(files)
    key: list[str | None] = [
        None if is_missing(a) or is_missing(b) else f"{a} {b}"
        for a, b in zip(node, provider, strict=True)
    ]
    sizes = _nums(files["file_size"].tolist())
    comp = [True] * len(files) if complete is None else [v is True or v == 1 for v in complete]
    seen: set[str] = set()
    total: dict[str, float] = {}
    count: dict[str, int] = {}
    for k, p, sz, ok in zip(key, paths, sizes, comp, strict=True):
        # a row that is not counted never hides a counted copy of the same file
        if k is None or not ok:
            continue
        dup_key = (
            f"{k} {as_str(p) or ('NA' if is_missing(p) and 'file_path' in files.columns else '')}"
        )
        dup = dup_key in seen
        seen.add(dup_key)
        if dup:
            continue
        count[k] = count.get(k, 0) + 1
        prev = total.get(k, math.nan)
        if not is_missing(sz):
            total[k] = sz if is_missing(prev) else prev + sz
        else:
            total.setdefault(k, math.nan)
    out = files.copy()
    out["storage_size"] = pd.Series(
        [math.nan if k is None else total.get(k, math.nan) for k in key], dtype="float64"
    )
    out["storage_n"] = pd.Series([pd.NA if k is None else count.get(k) for k in key], dtype="Int64")
    return out


_GITHUB_RAW_RX = re.compile(
    r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _github_raw_parts(urls: Any) -> Any:
    """Port of ``R/repo-download.R::.github_raw_parts()`` (metacheck #675).

    The GitHub repository (``owner/repo``), branch and path in the repository
    of each file address on raw.githubusercontent.com, as GitHub's listing
    gives them (``_osf_github_files()``, ``github_tree_files()``); ``NA`` for
    any other address. The path is decoded, as the address encodes it (#667).
    A branch with a ``/`` in its name cannot be told from the path here; its
    archive then matches no file, and the files are fetched one by one.
    """
    import pandas as pd

    values = [] if urls is None else list(urls)
    repo: list[str | None] = []
    ref: list[str | None] = []
    path: list[str | None] = []
    for u in values:
        m = None if is_missing(u) else _GITHUB_RAW_RX.match(str(u))
        if m is None:
            repo.append(None)
            ref.append(None)
            path.append(None)
        else:
            repo.append(f"{m.group(1)}/{m.group(2)}")
            ref.append(unquote(m.group(3), errors="surrogateescape"))
            path.append(unquote(m.group(4), errors="surrogateescape"))
    return pd.DataFrame(
        {
            "repo": pd.Series(repo, dtype="string"),
            "ref": pd.Series(ref, dtype="string"),
            "path": pd.Series(path, dtype="string"),
        }
    )


def _with_headers(spec: dict[str, Any], extra: Mapping[str, str]) -> dict[str, Any]:
    out = dict(spec)
    headers = dict(out.get("headers") or {})
    headers.update(extra)
    out["headers"] = headers
    return out


def _url_host_is(url: Any, domains: str | Sequence[str]) -> bool:
    """Port of ``R/repo-download.R::.url_host_is()``: is the host name of *url* one of
    *domains*, or a subdomain of one (``files.osf.io`` for ``osf.io``)?

    The host only: a key must not go to an address that merely mentions a service in its
    path or query (a link shortener, a web archive), nor to ``osf.io.example.com``.
    """
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    names = [domains] if isinstance(domains, str) else list(domains)
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in names)


def _osf_file_server_url(url: str, pat: str) -> str:
    """Port of ``R/repo-download.R::.osf_file_server_url()``: the OSF file server a download
    address on osf.io itself redirects to (``osf.io/download/<id>/`` to
    ``files.osf.io/v1/resources/...``), asked for with the token and without following the
    redirect, so that the download can go there directly with the token (see
    :func:`_auth_for_url`). Any other address -- the file servers, the API, a redirect that
    leaves the OSF, no redirect, or a failed request -- is returned as it is.
    """
    import httpx

    from metacheck import http

    host = (urlsplit(url).hostname or "").lower()
    if host not in ("osf.io", "www.osf.io"):
        return url
    known = _osf_file_server_known(url)
    if known is not None:
        return known
    try:
        resp = http.request(
            "GET",
            url,
            headers={"Authorization": f"Bearer {pat}", "User-Agent": "metacheck"},
            follow_redirects=False,
            max_tries=1,
            timeout=httpx.Timeout(60.0, connect=20.0),
        )
    except Exception:
        return url
    if resp is None or resp.status_code not in (301, 302, 303, 307, 308):
        return url
    location = resp.headers.get("location") or ""
    try:
        to = (urlsplit(location).hostname or "").lower()
    except ValueError:
        return url
    if not re.search(r"(^|\.)osf\.io$", to):
        return url
    # asked once per file: a zip whose index needs a second, larger read asked twice before
    _osf_file_server_remember([url], [location])
    return location


# The file server of each file in OSF Storage, by its download address (osf.io/download/<id>/ ->
# files.osf.io/v1/resources/<node>/providers/osfstorage/<id>, or the region's server). Every
# file's listing names it (links$upload), so the request to osf.io that only redirects there can
# be skipped. Only files in OSF Storage: an add-on's file is served by its own provider. Kept for
# the session, like the listings themselves (osf_cache_clear() forgets it).
_OSF_FILE_SERVER_MEMO: dict[str, str] = {}


def _osf_file_server_remember(download: Sequence[Any], server: Sequence[Any]) -> int:
    """Port of ``.osf_file_server_remember()``."""
    n = 0
    for d, s in zip(download, server, strict=False):
        if isinstance(d, str) and d and isinstance(s, str) and s:
            _OSF_FILE_SERVER_MEMO[d] = s
            n += 1
    return n


def _osf_file_server_known(url: Any) -> str | None:
    """Port of ``.osf_file_server_known()``: the remembered file server of a download address."""
    if not isinstance(url, str) or not url:
        return None
    return _OSF_FILE_SERVER_MEMO.get(url)


def _host_rate_limit_record(host: str, reset: float) -> None:
    """Port of the key part of ``R/repo-download.R::.host_rate_limit_record()``: a host whose
    limit belongs to a key metacheck manages (Dryad, GitHub, GitLab, the OSF) has it recorded
    as a key problem, shared with the other processes (see ``api_key_status()``). The reset
    itself is remembered by :func:`metacheck.http.request`."""
    import datetime as dt

    from metacheck.archives.api_keys import key_problem_record, key_service_for_host

    service = key_service_for_host(host)
    if service is not None:
        key_problem_record(service, "limit", dt.datetime.fromtimestamp(reset, dt.UTC))


def _eprints_hosts_list() -> list[str]:
    """The EPrints repositories that take no token (ReShare and the others, metacheck #637)."""
    from metacheck.archives.reshare import _eprints_hosts

    return _eprints_hosts()


def _auth_for_url(req: Mapping[str, Any]) -> dict[str, Any]:
    """Port of ``R/repo-download.R::.auth_for_url()``: host authentication for a file request.

    *req* is a request spec (``{"method", "url", "headers", ...}``); a new one
    is returned. OSF and Zenodo get their bearer token, Dataverse, Figshare,
    Dryad and ReShare their own header functions, and 4TU.ResearchData its
    ``Authorization: token`` -- so a private file is fetched instead of a
    sign-in page. The service is chosen by the address's host name only:
    matching anywhere in the address sent a key to any host whose path or query
    merely mentioned ``osf.io`` or ``zenodo.org``.

    A download address on osf.io is sent straight to the file server it
    redirects to, with the token (:func:`_osf_file_server_url`): the token is
    not sent on to the cloud storage the file server redirects to, which refuses
    a pre-signed address that also has an ``Authorization`` header (#508).
    """
    spec = dict(req)
    url = str(spec.get("url") or "")
    if _url_host_is(url, "osf.io"):
        from metacheck.archives.api_keys import key_use
        from metacheck.archives.osf_helpers import _osf_key_check

        try:
            pat = key_use("osf", check=_osf_key_check)
        except Exception:
            pat = ""
        if not pat:
            # A file in OSF Storage whose file-server address its listing gave goes there
            # directly, without a token: the request to osf.io that only answers with that
            # address is not needed.
            known = _osf_file_server_known(url)
            if known is not None:
                spec["url"] = known
        if pat:
            spec["url"] = _osf_file_server_url(url, pat)
            spec = _with_headers(spec, {"Authorization": f"Bearer {pat}"})
    elif _url_host_is(url, "zenodo.org"):
        from metacheck.archives.zenodo_upload import zenodo_pat

        try:
            pat = zenodo_pat(sandbox=_url_host_is(url, "sandbox.zenodo.org"))
        except Exception:
            pat = ""
        if pat:
            spec = _with_headers(spec, {"Authorization": f"Bearer {pat}"})
    else:
        from metacheck.archives.dataverse import _dataverse_headers, _dataverse_hosts

        if _url_host_is(url, _dataverse_hosts()):
            spec = _dataverse_headers(spec)
        elif _url_host_is(url, "figshare.com"):
            from metacheck.archives.figshare import _figshare_headers

            spec = _figshare_headers(spec)
        elif _url_host_is(url, "datadryad.org"):
            from metacheck.archives.dryad import _dryad_headers

            spec = _dryad_headers(spec)
        elif _url_host_is(url, _eprints_hosts_list()):
            # ReShare and the other EPrints data repositories (metacheck #637)
            from metacheck.archives.reshare import _reshare_headers

            spec = _reshare_headers(spec)
        elif _url_host_is(url, "data.4tu.nl"):
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


def _rate_limit_wait(resp: Any) -> float:
    """Port of ``R/repo-download.R::.rate_limit_wait()``: seconds until a confirmed reset.

    Reads ``RateLimit-Remaining``/``RateLimit-Reset`` (Dryad, GitLab) or
    ``X-RateLimit-*`` (Zenodo, GitHub). ``nan`` (R ``NA``) unless the bucket
    is confirmed exhausted (remaining exactly 0) with a numeric reset time; a
    reset already in the past gives 0.
    """
    from metacheck._values import as_float

    headers = resp.headers
    remaining = headers.get("ratelimit-remaining")
    if remaining is None:
        remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("ratelimit-reset")
    if reset is None:
        reset = headers.get("x-ratelimit-reset")
    if remaining is None or reset is None:
        return math.nan
    if as_float(remaining) != 0:
        return math.nan
    reset_time = as_float(reset)
    if reset_time is None or is_missing(reset_time):
        return math.nan
    wait = reset_time - time.time()
    return 0.0 if is_missing(wait) or wait < 0 else float(wait)


def _zip_timeout_for_size(
    timeout_s: float,
    expected_bytes: float = math.nan,
    min_bytes_per_s: float = 200 * 1024,
    expected_files: float = math.nan,
    s_per_file: float = 0.4,
) -> float:
    """Port of ``R/repo-download.R::.zip_timeout_for_size()``: scale a timeout to the size.

    *timeout_s* is a floor; with a known *expected_bytes* the timeout is at
    least the time the transfer takes at *min_bytes_per_s* (200 KB/s), and
    with a known *expected_files* the time the files take besides: the OSF
    builds its zips file by file, and sent zips of many small files at about
    0.3 to 0.4 seconds per file (612 files, 8.5 MB in 178 s; 458 files, 9.8 MB
    in 190 s; metacheck #679), far below the byte rate.
    """
    expected_bytes = _scalar(expected_bytes, "expected_bytes")
    expected_files = _scalar(expected_files, "expected_files")
    bytes_s = (
        0.0
        if is_missing(expected_bytes) or expected_bytes <= 0
        else expected_bytes / min_bytes_per_s
    )
    files_s = (
        0.0 if is_missing(expected_files) or expected_files <= 0 else expected_files * s_per_file
    )
    return max(timeout_s, bytes_s + files_s)


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
        if not os.path.exists(dest) and expected_bytes == 0:
            # a file the listing says is empty is empty (metacheck #568)
            open(dest, "wb").close()
            return None
        # no file is left behind: the next run would take it as cached (metacheck
        # keeps the 0-byte file: U75)
        _unlink(dest)
        return "empty response"
    except Exception as e:
        # read before the file goes: the response body was written to `dest`
        guestbook = ""
        if isinstance(e, _HttpError) and int(e.resp.status_code) == 400:
            guestbook = _dataverse_guestbook_hint(_body_of(e.resp, dest))
        if os.path.exists(dest):
            _unlink(dest)
        if isinstance(e, _HttpError) and expected_bytes == 0:
            # a file the listing says is empty needs nothing from the host: GitHub answered
            # "HTTP 501 Not Implemented" for empty files (metacheck #575)
            try:
                open(dest, "wb").close()
                return None
            except OSError:
                pass
        if (
            skip_on_api_limit is True  # R: isTRUE(skip_on_api_limit), the argument only
            and isinstance(e, _HttpError)
            and int(e.resp.status_code) == 429
            and not is_missing(_rate_limit_wait(e.resp))
        ):
            return f"API rate limit exhausted: {e}"
        if isinstance(e, _HttpError):
            # a 401 from Dryad without a key set: say so on the line the
            # failure report shows
            from metacheck.archives.dryad import _dryad_401_hint

            first, nl, rest = str(e).partition("\n")
            hint = _dryad_401_hint(url, int(e.resp.status_code))
            return f"{first}{hint}{nl}{rest}{guestbook}"
        return str(e)


def _body_of(resp: Any, dest: str) -> Any:
    """The response *resp* of a request that wrote its body to *dest*, with that body."""
    import httpx

    try:
        with open(dest, "rb") as fh:
            body = fh.read(1 << 20)
    except OSError:
        body = b""
    return httpx.Response(int(resp.status_code), content=body, headers=dict(resp.headers))


def _download_for_run() -> bool:
    """Port of ``R/repo-download.R::.download_for_run()``: will the modules of this check run the
    repository's code (``reproducibility_check`` with execute)?

    ``report_module_run()`` and ``reproducibility_check()`` set the option
    ``metacheck.download_for_run`` for the duration of the check, so the first module that
    downloads (``code_check``, then ``data_check``) fetches what a run needs in the same call,
    and no module asks a host for the same repository twice (metacheck #568).
    """
    from metacheck.utils import get_option

    return get_option("metacheck.download_for_run") is True


_MEDIA_RX = re.compile(
    r"\.(png|jpe?g|gif|bmp|svg|eps|ps|ico|webp|heic|wav|mp3|flac|ogg|m4a|aac|mp4|avi|mov|mkv"
    r"|wmv|webm|pdf|docx?|pptx?|odt|odp|key|pages)$",
    re.IGNORECASE,
)
_WHOLE_ARCHIVE_RX = re.compile(r"github\.com|gitlab\.com|datadryad\.org", re.IGNORECASE)
_GITHUB_COM_RX = re.compile(r"github\.com", re.IGNORECASE)
_GITHUB_RAW_HOST_RX = re.compile(r"^https?://raw\.githubusercontent\.com/", re.IGNORECASE)
_RENV_RX = re.compile(r"(^|/)renv/", re.IGNORECASE)


def _run_download_want(files: pd.DataFrame | None) -> list[bool]:
    """Port of ``R/repo-download.R::.run_download_want()``: the files of a listing a run of the
    code could need.

    Every file, except images, sound, video and documents that hosts send one by one, since
    there each such file is a request of its own and a script rarely reads one; a script that
    names one gets it from ``reproducibility_check()`` before the run. From a host that
    :func:`download_repo_files` fetches as one archive of the whole repository (GitHub, GitLab,
    Dryad), every file is kept: its bytes arrive anyway (metacheck #568). renv's own library is
    left out, as in ``code_check``. The size limits of :func:`download_repo_files` still apply.
    """
    if files is None or len(files) == 0:
        return []
    n = len(files)

    def column(name: str) -> list[Any]:
        return files[name].tolist() if name in files.columns else [None] * n

    names, urls, paths = column("file_name"), column("repo_url"), column("file_path")
    file_urls = column("file_url")
    out: list[bool] = []
    for name, url, path, file_url in zip(names, urls, paths, file_urls, strict=True):
        media = bool(_MEDIA_RX.search("" if is_missing(name) else str(name)))
        # a GitHub repository linked to an OSF project is fetched as one archive too,
        # known by its files' addresses (metacheck #675)
        whole = bool(_WHOLE_ARCHIVE_RX.search("" if is_missing(url) else str(url))) or bool(
            _GITHUB_RAW_HOST_RX.search("" if is_missing(file_url) else str(file_url))
        )
        where = str(name) if is_missing(path) or str(path) == "" else str(path)
        if is_missing(path) and is_missing(name):
            where = "NA"  # R: grepl() on NA is FALSE
        in_renv = bool(_RENV_RX.search(where.replace("\\", "/")))
        out.append((not media or whole) and not in_renv)
    return out


_BUDGET_COUNTED_MEMO: dict[str, list[bool]] = {}


def _budget_counted(listing: pd.DataFrame | None) -> list[bool]:
    """Port of ``R/repo-download.R::.budget_counted()``: the files of a listing that count for a
    repository's download budget.

    The kinds the checks download, judged by name only, so that every check, in any order, makes
    the same choice from the same listing (#699). Code (and the output files code_check recovers
    code from: .spv, .smcl, .out, .html), what data_check downloads with ``download = "data"``
    (tabular data, codebooks and readmes, .txt and .iqdat, archives that can be opened) and, when
    the code will run, what the run needs. Without the language-model step of data_check: a file
    it later calls data is downloaded from what is left of the budget after these. renv's own
    folder is left out, as in code_check. Kept for the session per listing:
    :func:`download_repo_files` asks for it in each call, code_check makes about ten, and sorting
    repo_from_hell's 10,158 entries takes 5 seconds.
    """
    import hashlib

    if listing is None or len(listing) == 0:
        return []
    from metacheck.archives.zip_peek import _is_readable_archive
    from metacheck.codecheck.core import _in_renv_folder, code_lang
    from metacheck.datacheck.files import (
        _data_doc_role,
        _file_ext,
        data_classify_files,
        data_format,
    )

    n = len(listing)

    def column(name: str) -> list[Any] | None:
        return listing[name].tolist() if name in listing.columns else None

    names_col = column("file_name")
    paths_col = column("file_path")
    if names_col is None:
        names_col = [None if is_missing(p) else os.path.basename(str(p)) for p in (paths_col or [])]
    if paths_col is None:
        paths_col = list(names_col)
    name = [None if is_missing(x) else str(x) for x in names_col]
    path = [None if is_missing(x) else str(x) for x in paths_col]
    repo_col = column("repo_url") or [None] * n
    url_col = column("file_url") or [None] * n
    run = _download_for_run()
    key = hashlib.sha1(
        repr((name, path, [as_str(x) for x in repo_col], [as_str(x) for x in url_col], run)).encode(
            "utf-8", "surrogateescape"
        ),
        usedforsecurity=False,
    ).hexdigest()
    hit = _BUDGET_COUNTED_MEMO.get(key)
    if hit is not None:
        return list(hit)
    typ = data_classify_files(name, path, group=[as_str(x) for x in repo_col])
    ext = [(_file_ext(x) or "").lower() if x is not None else "" for x in name]
    fmt = data_format(ext)
    role = _data_doc_role([x or "" for x in name])
    arch = _is_readable_archive(name)
    renv = _in_renv_folder(listing)
    want_run = _run_download_want(listing) if run else [False] * n
    counted: list[bool] = []
    for i in range(n):
        nm = name[i]
        lang = None if nm is None else code_lang(nm)
        c = (
            typ[i] in ("code", "output")
            or lang is not None
            or (typ[i] == "data" and fmt[i] == "tabular")
            or (typ[i] == "documentation" and role[i] in ("codebook", "readme"))
            or (
                nm is not None
                and re.search(r"[.](txt|iqdat|html?)$", nm, re.IGNORECASE) is not None
            )
            or bool(arch[i])
            or want_run[i]
        )
        counted.append(bool(c) and not renv[i])
    _BUDGET_COUNTED_MEMO[key] = counted
    return list(counted)


def _repo_disk_bytes(directory: str) -> float:
    """Port of ``R/repo-download.R::.repo_disk_bytes()``: the bytes a repository's download
    folder holds (*directory*, from :func:`_repo_cache_subdir`): every file, hidden ones included.

    Files unpacked from an archive count at their unpacked size, and a copy of the archive kept
    beside them counts as well, as both are on disk; a member's listed size is its unpacked size
    too (:func:`~metacheck.archives.zip_peek.zip_peek`). Leaving out the unpacked files of an
    archive in the folder let osf.io/nugzb hold 61 MB under a 50 MB budget: a 7.1 MB zip
    counted, the 46.8 MB unpacked from it did not (#699).
    """
    if not os.path.isdir(directory):
        return 0.0
    total = 0.0
    for root, _dirs, names in os.walk(directory):
        for nm in names:
            try:
                total += float(os.path.getsize(os.path.join(root, nm)))
            except OSError:
                continue
    return total


def _budget_row_key(df: pd.DataFrame) -> list[str]:
    """Port of ``.budget_row_key()``: one key per row of a listing, to tell the rows of a
    repository's choice of files (:func:`_repo_budget`) from the others."""
    n = len(df)

    def col(name: str) -> list[str]:
        if name not in df.columns:
            return ["NA"] * n
        return [("NA" if is_missing(v) else str(v)) for v in df[name].tolist()]

    cols = [
        col(c)
        for c in ("repo_url", "file_path", "file_name", "file_url", "archive_url", "archive_member")
    ]
    return ["\r".join(parts) for parts in zip(*cols, strict=True)] if n else []


def _archive_member_paths(arc: Any, repo: Any, cache: bool = False) -> tuple[str, str]:
    """Port of ``.archive_member_paths()``: where the members of an archive at *arc* (its URL, or
    its path on disk) are kept in *repo*'s download folder (``member_dest``), and where a whole
    copy of the archive is kept (``arc_path``).

    The members are kept in ``.archive_members/<key>.contents``, keyed off the archive's own
    address so it cannot collide with a real file's path.
    """
    from metacheck.utils import _safe_write_path

    arc_key = gsub("[^A-Za-z0-9._-]+", "_", gsub("^https?://", "", str(arc)))
    # shortened below the repository's folder only, so its budget counts them
    root = _repo_cache_subdir(repo, cache)
    raw = str(_repo_cache_path(repo, f".archive_members/{arc_key}", cache=cache))
    arc_path = _safe_write_path(raw, root=root) or raw
    member_dest = _safe_write_path(f"{arc_path}.contents", root=root) or f"{arc_path}.contents"
    return arc_path, member_dest


class _Budget:
    """The download budget of one repository (:func:`_repo_budget`).

    ``pick`` holds the keys of the chosen files (:func:`_budget_row_key`), ``reserve`` the bytes
    of the chosen files not yet on disk, ``disk`` the bytes the repository's folder holds plus
    what was taken since, and ``cap`` the budget in bytes. Changed in place as files are taken.
    """

    __slots__ = ("cap", "disk", "pick", "reserve")

    def __init__(self, cap: float) -> None:
        self.cap = cap
        self.pick: set[str] = set()
        self.reserve = 0.0
        self.disk = 0.0


def _repo_budget(
    repo: Any,
    listing: pd.DataFrame | None,
    cache: bool = False,
    max_file_size: float = 100,
    max_download_size: float = 500,
) -> _Budget:
    """Port of ``R/repo-download.R::.repo_budget()``: the download budget of one repository (#699).

    From its full listing (*listing*, repo_check's table), the files of the kinds the checks
    download (:func:`_budget_counted`), with a known size and none over *max_file_size*, are
    chosen smallest first until *max_download_size* is reached. A file of the choice may be
    downloaded while the folder stays within the budget; any other file, or the files unpacked
    from an archive, only from what is left after the whole choice (:func:`_budget_free`), so
    that no step crowds out a chosen file a later step still has to download. Used by
    :func:`download_repo_files` and by data_check's unpacking of archives.
    """
    import warnings

    import pandas as pd

    from metacheck.archives.zip_peek import _safe_member_path
    from metacheck.utils import _safe_write_path

    st = _Budget(max_download_size * _MB if math.isfinite(max_download_size) else math.inf)
    if listing is None or "repo_url" not in listing.columns:
        sub_df = pd.DataFrame({"repo_url": pd.Series([], dtype=object)})
    else:
        keep = [(not is_missing(v)) and v == repo for v in listing["repo_url"].tolist()]
        sub_df = listing.loc[keep].reset_index(drop=True)
    n = len(sub_df)

    def col(name: str, default: Any = None) -> list[Any]:
        return sub_df[name].tolist() if name in sub_df.columns else [default] * n

    size = [_num(v) for v in col("file_size", math.nan)]
    arc_col = col("archive_url")
    mem_col = col("archive_member")
    is_mem = [
        not is_missing(a) and str(a) != "" and not is_missing(m)
        for a, m in zip(arc_col, mem_col, strict=True)
    ]
    url_col = col("file_url")
    has_target = [
        (not is_missing(u) and str(u) != "") or m for u, m in zip(url_col, is_mem, strict=True)
    ]
    restricted = [v is True for v in col("file_restricted", False)]
    counted = _budget_counted(sub_df) if n else []
    ok = [
        has_target[j]
        and not restricted[j]
        and not is_missing(size[j])
        and size[j] <= max_file_size * _MB
        and counted[j]
        for j in range(n)
    ]
    keys = _budget_row_key(sub_df)
    pick = [False] * n
    used = 0.0
    cand = [j for j in range(n) if ok[j]]
    for o in _r_order([(size[j], keys[j]) for j in cand]):
        j = cand[o]
        if used + size[j] <= st.cap:
            pick[j] = True
            used += size[j]
    # which chosen files are on disk: at their ordinary place, or, for a file inside an archive,
    # in the folder its archive's members are kept in
    names = col("file_name")
    paths = col("file_path")
    rel = [nm if is_missing(p) else p for p, nm in zip(paths, names, strict=True)]
    storage_col = col("provider")
    subdir = _repo_cache_subdir(repo, cache)

    def on_disk(j: int) -> bool:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = _safe_write_path(
                str(_repo_cache_path(repo, rel[j], cache=cache, storage=storage_col[j])),
                root=subdir,
            )
        if p and os.path.isfile(p) and os.path.getsize(p) > 0:
            return True
        if not is_mem[j]:
            return False
        rel_m = _safe_member_path(str(mem_col[j]))
        if rel_m is None:
            return False
        m = _file_path(_archive_member_paths(arc_col[j], repo, cache)[1], rel_m)
        return os.path.isfile(m) and os.path.getsize(m) > 0

    chosen = [j for j in range(n) if pick[j]]
    st.pick = {keys[j] for j in chosen}
    st.reserve = sum(size[j] for j in chosen if not on_disk(j))
    st.disk = _repo_disk_bytes(subdir)
    return st


def _budget_free(st: _Budget) -> float:
    """Port of ``.budget_free()``: what a file outside the choice of a repository's budget
    (:func:`_repo_budget`) may still use, in bytes."""
    return max(st.cap - st.disk - st.reserve, 0.0)


def _budget_take(st: _Budget, size: float, chosen: bool = False) -> bool:
    """Port of ``.budget_take()``: whether a file of *size* bytes may be taken under a
    repository's budget (:func:`_repo_budget`), *chosen* if it is one of the choice; if so, it
    is counted."""
    if chosen:
        if st.disk + size > st.cap:
            return False
        st.reserve = max(st.reserve - size, 0.0)
    elif size > _budget_free(st):
        return False
    st.disk += size
    return True


def _dataverse_guestbook_hint(resp: Any) -> str:
    """Port of ``R/repo-download.R::.dataverse_guestbook_hint()``: a guestbook, if the response names one.

    A Dataverse depositor can ask people to fill in a form (a guestbook) before downloading.
    The listing does not say so; the host answers 400 with this message (verified live
    2026-10-31, Harvard Dataverse datafile 3670464). The form asks for a person's details,
    so metacheck does not submit it. ``""`` for any other answer.
    """
    from metacheck.archives.dataone import _resp_body_string

    try:
        txt = _resp_body_string(resp) or ""
    except Exception:
        txt = ""
    if "Guestbook response" in txt:
        return " (the depositor requires a guestbook form to be filled in before download)"
    return ""


def _download_capped(
    url: str,
    dest: str,
    max_bytes: float = math.inf,
    timeout_s: float = 120,
    skip_on_api_limit: bool = False,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    content: bytes | None = None,
    auth: bool = True,
) -> tuple[str | None, bool, int]:
    """Port of ``R/repo-download.R::.download_capped()``: a file of unknown size, with a limit.

    R's *url* may also be a prepared httr2 request (its own method, headers and
    body, such as ResearchBox's download from a box's web page), sent as it is, so
    that every download of unknown size goes through this one function (#699):
    here that is *method*, *headers* and *content* with ``auth=False``.

    Stops the transfer once more than *max_bytes* have arrived (metacheck #583).
    The bytes go to ``<dest>.part`` and the file is moved into place only when
    complete. Returns ``(error, over_cap, bytes)``: ``error`` is ``None`` on
    success, else the reason; a file over the limit has ``over_cap`` set and
    ``bytes`` how many had arrived. A refusal is not retried.
    """
    from metacheck import http

    _dir_create(os.path.dirname(dest))
    part = f"{dest}.part"
    timeout = _zip_timeout_for_size(timeout_s, max_bytes if math.isfinite(max_bytes) else math.nan)
    state: dict[str, Any] = {"written": 0, "why": None, "over": False, "streamed": False}

    def write(resp: Any) -> None:
        if int(resp.status_code) >= 400:
            return
        state["streamed"] = True
        deadline = time.monotonic() + timeout
        with open(part, "wb") as con:
            for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                http.check_interrupt()
                if time.monotonic() > deadline:
                    state["why"] = f"download exceeded the {math.ceil(timeout):d}s timeout"
                    return
                con.write(chunk)
                state["written"] += len(chunk)
                if math.isfinite(max_bytes) and state["written"] > max_bytes:
                    state["over"] = True
                    return

    try:
        is_file = url.lower().startswith("file:")
        try:
            resp = _storage_request(
                method,
                url,
                headers,
                skip_on_api_limit=skip_on_api_limit,
                timeout=timeout,
                path=part if is_file else None,
                max_tries=1,
                auth=auth,
                sink=None if is_file else write,
                content=content,
            )
        except _RateLimitSkipped:
            return (
                "API rate limit exhausted: known rate-limited host, skip_on_api_limit",
                False,
                0,
            )
        except _RequestError as e:
            if is_file:  # a connection (not curl) is what metacheck opens a file:// URL with
                return (
                    "Failed to perform HTTP request.\n"
                    "Caused by error in `open.connection()`:\n! cannot open the connection",
                    False,
                    0,
                )
            return str(e), False, 0
        except Exception as e:
            return str(e), False, 0
        if int(resp.status_code) >= 400:
            return f"HTTP {int(resp.status_code)}", False, 0
        if is_file and os.path.exists(part):
            state["written"] = os.path.getsize(part)
            state["over"] = math.isfinite(max_bytes) and state["written"] > max_bytes
        if state["over"]:
            return "over the byte limit", True, int(state["written"])
        if state["why"] is not None:
            return str(state["why"]), False, 0
        if _is_login_page(part):
            return "not authorised (the OSF returned a sign-in page; see ?osf_pat)", False, 0
        if not (os.path.exists(part) and os.path.getsize(part) > 0):
            return "empty response", False, 0
        try:
            os.replace(part, dest)
        except OSError:
            return "could not move the download into place", False, 0
        return None, False, 0
    finally:
        _unlink(part)


def _parallel_one(url: Any, dest: str, expected: float, skip: bool) -> tuple[Any, Exception | None]:
    if is_missing(url):
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
            if skip_on_api_limit is True and sc == 429 and not is_missing(_rate_limit_wait(resp)):
                errs.append(f"API rate limit exhausted: HTTP {sc}")
            else:
                from metacheck.archives.dryad import _dryad_401_hint

                errs.append(f"HTTP {sc:d}{_dryad_401_hint(urls[i], sc)}")
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
        if not is_missing(exp) and exp > 0:
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
    rel_paths: Sequence[Any] | None = None,
    expected_files: float = math.nan,
    _inplace: bool = False,
) -> pd.DataFrame:
    """Port of ``R/repo-download.R::.download_zip_to_cache()``.

    Downloads a whole repository as one zip archive and extracts the rows
    *row_idx* (0-based positions in *files*) into their ``.cache_path``,
    filling ``file_location`` for each file found in the archive (matched on
    ``file_path``, or ``file_name`` where that is empty; *strip_dir* drops
    the archive's top-level folder first, as GitHub/GitLab add one). The
    transfer is streamed and aborted past *max_bytes* or the (size-scaled)
    *timeout_s*; it is retried like any request (:func:`metacheck.http.request`:
    a 429 waits for the host's reset unless ``skip_on_api_limit``). Rows not filled are left for the caller's
    file-by-file fallback. *req_func* adds the host's headers to the request
    spec. *rel_paths* gives each row's path inside the archive, one per
    *row_idx*, when it is not the row's ``file_path`` (a GitHub repository
    behind an OSF project, whose path is read from the file's address, #675).
    """
    import zipfile

    import httpx
    import pandas as pd

    from metacheck import http
    from metacheck._r import r_round
    from metacheck.archives.zip_peek import (
        _unzip_failed_note,
        _zip_unpack_reason,
        _zip_unsafe_path,
    )
    from metacheck.report.blocks import _cap_num

    if "file_path" not in files.columns and "file_name" not in files.columns:
        return files  # nothing to match the archive's members on: no download
    timeout_s = _zip_timeout_for_size(timeout_s, expected_bytes, expected_files=expected_files)
    fd, zip_tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    extract_dir: str | None = None
    try:
        spec: dict[str, Any] = {"method": "GET", "url": zip_url, "headers": {}}
        if req_func is not None:
            spec = req_func(spec)
        dl_err: str | None = None
        got_body = False
        deadline: list[float] = []  # started with the first body, shared by the tries

        def write_zip(resp: Any) -> None:
            nonlocal got_body
            got_body = True
            if not deadline:
                deadline.append(time.monotonic() + timeout_s)
            written = 0
            with open(zip_tmp, "wb") as con:
                for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                    http.check_interrupt()
                    if time.monotonic() > deadline[0]:
                        # a whole number of seconds (metacheck's sprintf("%d") fails on the
                        # size-scaled fractional timeout: U73)
                        raise _ZipAborted(
                            f"archive download exceeded the {math.ceil(timeout_s):d}s timeout"
                        )
                    con.write(chunk)
                    written += len(chunk)
                    if math.isfinite(max_bytes) and written > max_bytes:
                        cap = _cap_num(float(r_round(max_bytes / _MB)))
                        raise _ZipAborted(
                            f"archive exceeded the {cap} MB cap during download and was aborted"
                        )

        try:
            # R: isTRUE(skip_on_api_limit), the argument only (not the option)
            with http.skip_on_api_limit(skip_on_api_limit is True or http.skipping_api_limits()):
                http.request(
                    "GET",
                    zip_url,
                    headers=dict(spec.get("headers") or {}),
                    timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 20.0)),
                    sink=write_zip,
                    raise_errors=True,
                )
        except http.RateLimited:
            return files
        except (httpx.TransportError, httpx.TimeoutException) as e:
            _message(
                f"Zip download failed ({zip_url}): Failed to perform HTTP request.\n"
                f"Caused by error: {e}"
            )
            return files
        except Exception as e:
            dl_err = str(e)
        if dl_err is None:
            if not got_body:
                return files  # a 429 or a 5xx on every try: nothing to read
            if not os.path.exists(zip_tmp) or os.path.getsize(zip_tmp) == 0:
                dl_err = "empty response"
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
            use_rel = rel_paths is not None and len(rel_paths) == len(row_idx)
            for k, i in enumerate(row_idx):
                rel = rel_paths[k] if use_rel and rel_paths is not None else paths[i]
                if is_missing(rel) or rel == "":
                    rel = fnames[i]
                if is_missing(rel):
                    matched.append(None)  # R: match(NA, lookup_paths) is NA
                    continue
                rel = gsub("^/+", "", gsub(r"\\", "/", str(rel)))
                matched.append(first.get(rel))
            if all(m is None for m in matched):
                return files

            if not _inplace:  # download_repo_files() passes its own copy
                files = files.copy()
            # a member that cannot be unpacked (encrypted, compressed with a method that cannot
            # be read, a path outside the folder) is named, and its row is left for the
            # file-by-file download (.unzip_all(), #680)
            not_unpacked: dict[str, str] = {}
            for i, m in zip(row_idx, matched, strict=True):
                if m is None:
                    continue
                info = entries[m][1]
                dest = str(cache[i])
                name = entries[m][0]
                if name in not_unpacked:
                    continue
                if _zip_unsafe_path(gsub(r"\\", "/", name)):
                    not_unpacked[name] = "its path leads outside the folder"
                    continue
                if info.flag_bits & 1 or info.compress_type == 99:
                    not_unpacked[name] = _zip_unpack_reason(info.compress_type, info.flag_bits)
                    continue
                try:
                    with zf.open(info) as src:
                        data = src.read()
                except Exception:  # an entry that could not be extracted
                    not_unpacked[name] = _zip_unpack_reason(info.compress_type, info.flag_bits)
                    continue
                # an empty member is an empty file, as the archive itself states
                # (metacheck #575, item 5)
                if not data and info.file_size != 0:
                    continue
                try:
                    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                    with atomic_write(dest) as fh:
                        fh.write(data)
                except OSError:
                    continue
                files.iat[i, loc_col] = dest
            if not_unpacked:
                note = _unzip_failed_note(
                    pd.DataFrame(
                        {
                            "name": list(not_unpacked),
                            "path": [None] * len(not_unpacked),
                            "reason": list(not_unpacked.values()),
                        }
                    ),
                    zip_url,
                )
                _message(f"{note} (downloaded file by file instead)")
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
    return [None if is_missing(v) else v for v in files[name].tolist()]


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


def _r_order(keys: Sequence[tuple[Any, ...]]) -> list[int]:
    """``order(size, path, ...)``: by size, ties by the strings in R's string collation."""
    from metacheck._r import r_sort_key

    return sorted(
        range(len(keys)),
        key=lambda k: (keys[k][0], *(r_sort_key(x) for x in keys[k][1:])),
    )


def download_repo_files(
    files: pd.DataFrame | None,
    max_file_size: float = 100,
    max_download_size: float = 500,
    max_files_per_repo: float = math.inf,
    repo_file_counts: Mapping[str, float] | None = None,
    repo_listing: pd.DataFrame | None = None,
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
    ``max_download_size`` (MB) is a budget per repository, for everything its download folder
    holds. From the repository's full listing (*repo_listing*, else the rows of *files*), the
    files of the kinds the checks download (code, tabular data, codebooks and readmes, text
    files, archives) are chosen smallest first, none over ``max_file_size``, until the budget is
    reached. The same listing and limits give the same choice in every call, whichever check
    calls and in whatever order, so code files cannot be crowded out by data files fetched
    earlier. A requested file outside that choice (another kind, or one of unknown size) is
    downloaded only from what is left of the budget after the whole choice. Whatever the choice,
    nothing is downloaded once the folder holds ``max_download_size``: files from earlier calls,
    earlier runs (with ``cache=True``), archive copies and the files unpacked from them all count,
    a file unpacked from an archive at its unpacked size (#699). A repository may be fetched as
    one archive of up to twice this size, to save requests; only the wanted files are kept from
    it. ``max_files_per_repo`` refuses a repository with more files outright -- counted from
    ``repo_file_counts`` (``repo_url`` -> the full listing's count) when given, else from
    *repo_listing*, files repo_check found inside its archives included; a refused repository has
    none of its files fetched, also none from inside its archives (#699).

    Returns a copy of *files* with ``file_location`` filled, and
    ``attrs["gated"]`` (``repo_url``, ``message``: repositories refused or
    partially filled by the caps), ``attrs["oversize_skipped"]``
    (``repo_url``, ``file_name``, ``file_size``), ``attrs["limit_refused"]``
    (one element per row of *files*: why a file was left out by ``max_file_size``,
    ``max_download_size`` or ``max_files_per_repo``, and ``NA`` for every other
    row, so that a caller does not fetch such a file another way: #699) and ``attrs["failed"]``
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
    # Without a file_size column no size was looked up and every file was left out
    # without a record (metacheck #583): an empty column for the call, removed again.
    had_size = "file_size" in df.columns
    if not had_size:
        df["file_size"] = pd.Series([math.nan] * n, dtype="float64")
    if "file_location" not in df.columns:
        df["file_location"] = pd.Series([None] * n, dtype=object)
    else:
        df["file_location"] = pd.Series(
            [None if is_missing(v) else v for v in df["file_location"].tolist()], dtype=object
        )

    cache_root = None if cache is True else _repo_session_dir()

    def cache_path(repo_url: Any, file_path: Any, storage: Any = None) -> str:
        if cache_root is None:
            return str(_repo_cache_path(repo_url, file_path, storage=storage))
        return _file_path(cache_root, str(_repo_cache_rel(repo_url, file_path, storage)))

    storages: list[Any] = df["provider"].tolist() if "provider" in df.columns else [None] * n

    # a missing repo_url column is R's NULL (cache key "unknown"); a missing value is NA
    repo_urls: list[Any] = (
        [pd.NA if is_missing(v) else v for v in df["repo_url"].tolist()]
        if ("repo_url" in df.columns)
        else [None] * n
    )
    file_names = _col(df, "file_name") or [None] * n
    rel_path = _col(df, "file_path")
    if rel_path is None:
        rel_path = list(file_names)
    rel_path = [fn if p is None else p for p, fn in zip(rel_path, file_names, strict=True)]
    _sweep_repo_cache_once()
    # Only the part below the repository's folder is shortened, so the repository's budget, which
    # counts that folder, counts the file (#699).
    cache_paths = [
        _safe_write_path(
            cache_path(repo_urls[i], rel_path[i], storages[i]),
            root=_repo_cache_subdir(repo_urls[i], cache),
        )
        or ""
        for i in range(n)
    ]
    df[".cache_path"] = pd.Series(cache_paths, dtype=object)

    already = [os.path.exists(p) for p in cache_paths]
    # An empty cached file of a file the listing says has bytes is a download that failed,
    # so it is fetched again (metacheck #568).
    for i, listed in enumerate(df["file_size"].tolist()):
        if already[i]:
            lv = _num(listed)
            if not is_missing(lv) and lv > 0:
                try:
                    if os.path.getsize(cache_paths[i]) == 0:
                        already[i] = False
                except OSError:
                    pass
    loc_col = int(df.columns.get_loc("file_location"))  # type: ignore[arg-type]
    for i in range(n):
        if already[i]:
            df.iat[i, loc_col] = cache_paths[i]

    gated_rows: list[tuple[Any, str]] = []
    oversize_rows: list[tuple[Any, Any, float]] = []
    # Why a file was left out by a limit, per row (None for the others): returned as the
    # attribute "limit_refused", so that a caller can tell these files from failed downloads
    # and does not fetch them another way (#699).
    limit_refused: list[str | None] = [None] * n
    refused_size = (
        "it was not downloaded, because it is larger than the "
        f"{_cap_num(max_file_size)} MB per-file limit (max_file_size)"
    )
    refused_budget = (
        "it was not downloaded, because the "
        f"{_cap_num(max_download_size)} MB budget for its repository was spent (max_download_size)"
    )
    # defined before the archive-member block, which records into it too (#429)
    failed_rows: list[tuple[Any, Any, Any, Any, str | None]] = []
    paper_ids = _col(df, "paper_id")

    def paper_id(i: int) -> Any:
        return None if paper_ids is None else paper_ids[i]

    # which(files$repo_url == repo), computed once: rows per repository (NA rows in none)
    rows_of: dict[Any, list[int]] = {}
    for i, v in enumerate(repo_urls):
        if not is_missing(v):
            rows_of.setdefault(v, []).append(i)

    def rows_for(repo: Any) -> list[int]:
        return [] if is_missing(repo) else rows_of.get(repo, [])

    sizes_col: list[Any] = _col(df, "file_size") or []

    # The file count of a repository is its full listing's (#699)
    if repo_file_counts is None and repo_listing is not None and "repo_url" in repo_listing.columns:
        listing_counts: dict[str, float] = {}
        for v in repo_listing["repo_url"].tolist():
            if not is_missing(v):
                listing_counts[v] = listing_counts.get(v, 0) + 1
        repo_file_counts = listing_counts

    # Known listing sizes and cached files need no request. Fetch the remaining
    # distinct URLs concurrently; duplicates share one probe without losing rows.
    size_by_url: dict[str, float] = {}
    if sizes_col is not None:
        from metacheck.archives._parallel import map_ordered

        file_urls_for_size = _col(df, "file_url") or [None] * n
        probe_rows = set()
        for repo, repo_rows in rows_of.items():
            downloadable = [i for i in repo_rows if file_urls_for_size[i]]
            count = (
                _num(repo_file_counts[repo])
                if repo_file_counts is not None and repo in repo_file_counts
                else len(repo_rows)
            )
            if not (math.isfinite(max_files_per_repo) and count > max_files_per_repo):
                probe_rows.update(downloadable)
        unknown = list(
            dict.fromkeys(
                str(url)
                for i, url in enumerate(file_urls_for_size)
                if i in probe_rows and not already[i] and is_missing(_num(sizes_col[i]))
            )
        )
        size_by_url = dict(zip(unknown, map_ordered(_remote_size, unknown), strict=True))

    file_urls = _col(df, "file_url") or [None] * n
    restricted = _col(df, "file_restricted") or [None] * n
    has_url = [
        u is not None and str(u) != "" and restricted[i] is not True
        for i, u in enumerate(file_urls)
    ]
    cap_bytes = max_download_size * _MB if math.isfinite(max_download_size) else math.inf

    # -- one budget per repository, one choice of files (metacheck #698, #699) ------------
    # The budget (max_download_size) is shared by archive members and by files with their own
    # address (#698): it was applied to each archive on its own, from zero, and a Dryad
    # repository with many archives fetched 30 GB in one call under a 2,000 MB budget. It was
    # also counted per call, over the rows passed in only, and code_check, data_check and
    # reproducibility_check make several calls each, so one repository could receive several
    # times the budget; on the Meta-Psychology papers, four repositories held 51 to 53 MB under
    # a 50 MB budget (#699).
    #
    # Now each repository's files are chosen once, from its full listing (repo_listing, else
    # `files`): the kinds the checks download (_budget_counted()), with a known size and none
    # over max_file_size, smallest first, until the budget is reached. Every call makes the same
    # choice, so the order of the checks does not matter. A file of the choice is downloaded as
    # long as the folder stays within the budget; any other file (another kind, a file the
    # listing does not hold, a file of unknown size) only from what is left after the whole
    # choice, the part not yet on disk included. What the repository's folder holds counts first
    # (_repo_disk_bytes()), so no call, rerun or earlier run takes it past the budget.
    listing = repo_listing if repo_listing is not None else df
    files_key = _budget_row_key(df)
    budgets: dict[Any, _Budget] = {}

    def budget_of(repo: Any) -> _Budget:
        """The budget of a repository (:func:`_repo_budget`), worked out on first use and kept
        for the rest of this call."""
        if repo not in budgets:
            budgets[repo] = _repo_budget(
                repo,
                listing,
                cache=cache,
                max_file_size=max_file_size,
                max_download_size=max_download_size,
            )
        return budgets[repo]

    def arc_paths(arc: Any, repo: Any) -> tuple[str, str]:
        return _archive_member_paths(arc, repo, cache)

    # -- repositories over max_files_per_repo ---------------------------------------------
    # A repo with an extreme file count (e.g. a global multi-site database -- confirmed live
    # 2026-09-07 against a real Zenodo record, SAPFLUXNET, with ~4000 files) is refused before
    # anything else, before any per-file size resolution (which would itself mean thousands of
    # HEAD probes / cache-size checks). Beyond the download cost, data_check's own full-content
    # read of every file does not scale to this many files for one paper's repo either.
    #
    # Counted from repo_file_counts (the TRUE total of the repository, worked out from
    # repo_listing), not from the rows of `files`: `files` is routinely a caller-filtered subset
    # (only rows still needing a download), which undercounts a repository whose remaining rows
    # fall under the cap although its real size does not (SAPFLUXNET again: slipped through on
    # the row count, then several hundred files individually hit a host returning a persistent
    # 403, each retried, compounding into a multi-hour stall).
    #
    # Refused here, before the archive members below, for its files inside archives as well:
    # when the refusal came after them, a repository of 41 files under max_files_per_repo = 20
    # was reported as "skipped entirely", while the two scripts in its zip and tar were fetched
    # (#699).
    count_gated: set[Any] = set()
    if math.isfinite(max_files_per_repo):
        for repo in dict.fromkeys(repo_urls[i] for i in range(n) if not is_missing(repo_urls[i])):
            idx = rows_for(repo)
            true_n = (
                _num(repo_file_counts[repo])
                if repo_file_counts is not None and repo in repo_file_counts
                else float(len(idx))
            )
            if true_n <= max_files_per_repo:
                continue
            msg = (
                f"Repository {repo} holds {_fmt_int(true_n)} files, exceeding the "
                f"{_fmt_int(max_files_per_repo)}-file cap: skipped entirely (not size-capped, "
                "file-count-capped). Raise `max_files_per_repo` to include it."
            )
            _cap_report(msg)
            for i in idx:
                if not already[i]:
                    limit_refused[i] = (
                        f"it was not downloaded, because its repository holds {_fmt_int(true_n)} "
                        f"files, more than max_files_per_repo ({_cap_num(max_files_per_repo)})"
                    )
            gated_rows.append((repo, msg))
            count_gated.add(repo)

    def file_bytes(path: str) -> float:
        try:
            return float(os.path.getsize(path))
        except OSError:
            return 0.0

    # -- archive members (repo_check's zip-peek expansion) --------------------
    if "archive_url" in df.columns:
        from metacheck.archives.tar_peek import _tar_fetch_members
        from metacheck.archives.zip_peek import (
            _safe_member_path,
            _whole_request_bytes,
            _zip_fetch_members,
        )

        arcs = _col(df, "archive_url") or [None] * n
        members = _col(df, "archive_member") or [None] * n
        is_arc_row = [
            arcs[i] is not None and str(arcs[i]) != "" and members[i] is not None for i in range(n)
        ]
        # every repository with archive members, also one whose members are all cached, so that
        # they are counted for its files with their own address
        for repo in dict.fromkeys(repo_urls[i] for i in range(n) if is_arc_row[i]):
            if is_missing(repo) or repo in count_gated:
                continue
            idx = [i for i in rows_for(repo) if is_arc_row[i] and not already[i]]
            member_sizes = [_num(sizes_col[i]) for i in idx]
            over = [not is_missing(s) and s > max_file_size * _MB for s in member_sizes]
            for i, s, o in zip(idx, member_sizes, over, strict=True):
                if o:
                    oversize_rows.append((repo_urls[i], file_names[i], s))
                    limit_refused[i] = refused_size
            # A member without a size cannot be held to the caps, so it is not fetched;
            # recorded so that the reason is not lost.
            for i, s in zip(idx, member_sizes, strict=True):
                if is_missing(s):
                    failed_rows.append(
                        (
                            repo_urls[i],
                            file_names[i],
                            arcs[i],
                            paper_id(i),
                            "the archive's list of contents gives no size for this file",
                        )
                    )
            ok = [not o and not is_missing(s) for s, o in zip(member_sizes, over, strict=True)]
            cand = [i for i, k in zip(idx, ok, strict=True) if k]
            cand_size = [s for s, k in zip(member_sizes, ok, strict=True) if k]

            # A member saved by an earlier run is used as it is (metacheck #561): it is stored
            # under its archive's ".contents" folder, where `already` does not look.
            dest_of: dict[Any, str] = {}
            saved = [False] * len(cand)
            for j, i in enumerate(cand):
                if arcs[i] not in dest_of:
                    dest_of[arcs[i]] = arc_paths(arcs[i], repo)[1]
                rel = _safe_member_path(str(members[i]))
                target = None if rel is None else _file_path(dest_of[arcs[i]], rel)
                if target is not None and os.path.isfile(target):
                    size_on_disk = os.path.getsize(target)
                    if size_on_disk > 0 and size_on_disk == cand_size[j]:
                        df.iat[i, loc_col] = target
                        saved[j] = True

            # What the repository's folder holds counts first; then the missing members, smallest
            # first: a member of the repository's choice of files while the folder stays within
            # the budget, any other member from what is left after the whole choice (see
            # budget_of() above, #699)
            st = budget_of(repo)
            chosen = [files_key[i] in st.pick for i in cand]
            miss = [j for j, v in enumerate(saved) if not v]
            miss = [
                miss[o]
                for o in _r_order(
                    [(cand_size[j], str(arcs[cand[j]]), str(members[cand[j]])) for j in miss]
                )
            ]
            take = [False] * len(cand)
            for j in miss:
                take[j] = _budget_take(st, cand_size[j], chosen[j])
            for j, i in enumerate(cand):
                if not saved[j] and not take[j]:
                    limit_refused[i] = refused_budget
            n_omitted = sum(1 for v, t in zip(saved, take, strict=True) if not v and not t)
            if n_omitted > 0 and math.isfinite(cap_bytes):
                msg = (
                    f"Repository {as_str(repo) or 'NA'} exceeds the "
                    f"{_cap_num(max_download_size)} MB per-repository budget: fetched the "
                    f"smallest archive members up to the cap, {n_omitted} "
                    f"member{plural(n_omitted)} omitted. "
                    "Raise `max_download_size` to include more."
                )
                _cap_report(msg)
                gated_rows.append((repo, msg))
            to_fetch = [i for i, t in zip(cand, take, strict=True) if t]

            for arc in dict.fromkeys(arcs[i] for i in to_fetch):
                idx = [i for i in to_fetch if arcs[i] == arc]
                arc_path, member_dest = arc_paths(arc, repo)
                _dir_create(member_dest)
                had_copy = os.path.exists(arc_path)
                # The archive's own size, carried from repo_check's listing, saves the peek a
                # HEAD request in a new session. The archive may be downloaded once instead of
                # fetched by range, when that is cheaper (always on Dryad, which counts every
                # request against a daily quota: metacheck #471); it is kept at `arc_path`, so
                # a restarted run does not request it again. As that copy stays on disk, it is
                # taken only when it fits in what is left of the repository's budget (#698).
                # The members unpacked from the copy are counted already (_budget_take() above,
                # at their unpacked size), and stay on disk beside the copy, so the copy must fit
                # in what is free after them (#699): counting the copy in place of its members let
                # osf.io/nugzb hold 61 MB under a 50 MB budget.
                # The archive is a file like any other, so it is held to max_file_size as well
                # (#699): an OSF zip of 20.5 MB, reported as left out by a 20 MB limit, was still
                # downloaded whole to take its members. A larger archive has its members fetched
                # by byte range.
                whole_bytes = _budget_free(st)
                if math.isfinite(max_file_size):
                    whole_bytes = min(whole_bytes, max_file_size * _MB)
                arc_sizes = _col(df, "archive_size")
                # why a member was not fetched is recorded in `failed` (#429)
                # a member of a plain .tar (listed by downloading the archive whole, see
                # tar_peek._tar_peek) comes from that session copy or one new download:
                # a tar has no index to fetch members by byte range from
                arc_formats = _col(df, "archive_format")
                is_tar_arc = arc_formats is not None and arc_formats[idx[0]] == "tar"
                fetched: pd.DataFrame | None
                try:
                    if is_tar_arc:
                        fetched = _tar_fetch_members(
                            str(arc),
                            names=[str(members[i]) for i in idx],
                            dest=member_dest,
                            archive_path=arc_path,
                            size=None if arc_sizes is None else arc_sizes[idx[0]],
                            max_whole_bytes=whole_bytes,
                            skip_on_api_limit=skip_on_api_limit,
                        )
                    else:
                        fetched = _zip_fetch_members(
                            str(arc),
                            names=[str(members[i]) for i in idx],
                            dest=member_dest,
                            cache=cache,
                            skip_on_api_limit=skip_on_api_limit,
                            max_whole_bytes=whole_bytes,
                            archive_path=arc_path,
                            request_bytes=_whole_request_bytes(arc),
                            size=None if arc_sizes is None else arc_sizes[idx[0]],
                        )
                except Exception as e:
                    why: str | None = str(e)
                    fetched = None
                else:
                    why = "could not list the archive (host may not support range requests)"
                # a whole copy of the archive kept by this fetch counts against the budget, beside
                # its members (#698, #699)
                if not had_copy and os.path.exists(arc_path):
                    st.disk += file_bytes(arc_path)
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
                    if f_ok[j] is not True or is_missing(f_path[j]):
                        why = "unknown failure" if f_error is None else as_str(f_error[j])
                        failed_rows.append((repo_urls[k], file_names[k], arcs[k], paper_id(k), why))
                        continue
                    df.iat[k, loc_col] = f_path[j]

    to_get: list[int] = []

    # the byte limit of a file of unknown size (see below); absent for the others
    byte_cap: dict[int, float] = {}

    # -- per-repository budget + per-file size filter -------------------------
    for repo in dict.fromkeys(repo_urls[i] for i in range(n) if has_url[i]):
        idx = [i for i in rows_for(repo) if has_url[i]]
        if not idx:
            continue
        # refused above for its number of files (see "repositories over max_files_per_repo")
        if repo in count_gated:
            continue

        is_cached = [already[i] for i in idx]
        sizes = [_num(sizes_col[i]) for i in idx]
        for k, s in enumerate(sizes):
            if not is_missing(s):
                continue
            if is_cached[k]:
                try:
                    sz = float(os.path.getsize(cache_paths[idx[k]]))
                except OSError:
                    sz = math.nan
                sizes[k] = sz if math.isfinite(sz) else math.nan
            else:
                sizes[k] = size_by_url[str(file_urls[idx[k]])]

        if math.isfinite(max_file_size):
            over = [not is_missing(s) and s > max_file_size * _MB for s in sizes]
        else:
            over = [False] * len(idx)
        for k, i in enumerate(idx):
            if over[k] and not is_cached[k]:
                oversize_rows.append((repo, file_names[i], sizes[k]))
                limit_refused[i] = refused_size

        # Unknown-size files are not budgeted. A repository with no known-size file goes
        # on too: its unknown-size files are downloaded with a limit below (metacheck #583).
        cand = [not o and not is_missing(s) for o, s in zip(over, sizes, strict=True)]
        c_idx = [i for i, c in zip(idx, cand, strict=True) if c]
        c_size = [s for s, c in zip(sizes, cand, strict=True) if c]
        c_cached = [x for x, c in zip(is_cached, cand, strict=True) if c]

        # What the repository's folder holds counts first (cached files, archive members and
        # archive copies, those fetched above included); then the missing files, smallest first:
        # a file of the repository's choice while the folder stays within the budget, any other
        # file from what is left after the whole choice (see budget_of() above, #698, #699)
        st = budget_of(repo)
        chosen = [files_key[c_idx[k]] in st.pick for k in range(len(c_idx))]
        missing = [k for k, cc in enumerate(c_cached) if not cc]
        order = _r_order([(c_size[k], cache_paths[c_idx[k]]) for k in missing])
        missing_order = [missing[o] for o in order]
        take_missing: list[int] = []
        for k in missing_order:
            if _budget_take(st, c_size[k], chosen[k]):
                take_missing.append(k)  # noqa: PERF401 - each take changes the budget
            # keep scanning: a later, smaller file may still fit under the budget
        to_get.extend(c_idx[k] for k in take_missing)

        # A still-missing file whose size neither the listing nor the host gives is
        # downloaded with a limit: the per-file cap, or what the budget has left if
        # that is less. The transfer stops once the limit is passed and the file is
        # reported as over the limit. Such a file is outside the choice (its size is not
        # known), so it may use what is left after the whole choice; the limit is worked out
        # again for each file as it is downloaded, so that together they stay within it (#699).
        unknown_rows = [
            i
            for k, i in enumerate(idx)
            if not over[k] and is_missing(sizes[k]) and not is_cached[k]
        ]
        if unknown_rows:
            left = _budget_free(st)
            lim = min(max_file_size * _MB if math.isfinite(max_file_size) else math.inf, left)
            if lim > 0:
                for i in unknown_rows:
                    byte_cap[i] = lim
                to_get.extend(unknown_rows)
            else:
                for i in unknown_rows:
                    limit_refused[i] = refused_budget
                msg = (
                    f"Repository {repo}: {len(unknown_rows)} file{plural(len(unknown_rows))} "
                    "of unknown size not downloaded, because the "
                    f"{_cap_num(max_download_size)} MB per-repository budget is spent. "
                    "Raise `max_download_size` to include more."
                )
                _cap_report(msg)
                gated_rows.append((repo, msg))

        taken = set(take_missing)
        omitted = [k for k in missing_order if k not in taken]
        for k in omitted:
            limit_refused[c_idx[k]] = refused_budget
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
        for repo in dict.fromkeys(r[0] for r in oversize_rows):
            rows = [r for r in oversize_rows if not is_missing(r[0]) and r[0] == repo]
            if is_missing(repo):
                rows = [r for r in oversize_rows if is_missing(r[0])]
            k = len(rows)
            largest = sorted(rows, key=lambda r: -r[2])[0]
            _message(
                f"{k} file{plural(k)} in {as_str(repo) or 'NA'} exceeded the "
                f"{_cap_num(max_file_size)} MB per-file limit and {'was' if k == 1 else 'were'} "
                "skipped (the rest of the repository was downloaded). Largest: "
                f"{as_str(largest[1]) or 'NA'} "
                # rounded up to 0.1 MB: rounded to whole MB, a 1.4 MB file over a 1 MB limit read
                # "(1 MB)" (#699)
                f"({_cap_num(math.ceil(max(r[2] for r in rows) / _MB * 10) / 10)} MB). "
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
            hits = grepl(pattern, [as_str(repo_list[i]) for i in rows], ignore_case=True)
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
            return float(sum(v for v in vals if not is_missing(v)))

        def filled(rows: list[int]) -> set[int]:
            # rows the zip wrote to their cache path (metacheck counts any
            # file_location, so a row that arrived with a stale one is never
            # fetched: U74)
            return {i for i in rows if location(i) == cache_paths[i]}

        def run_zip(rows: list[int], zip_url: str, **kw: Any) -> None:
            nonlocal df
            df = _download_zip_to_cache(df, rows, zip_url, _inplace=True, **kw)
            remaining_set.difference_update(filled(rows))

        # Dryad: whole-dataset archive (quota-aware). Every repository a Dryad
        # DOI can be read from is one, whatever the form: metacheck at the pin
        # matched only datadryad.org and doi.org/10.5061/dryad, so a dataset
        # cited under another Dryad prefix (10.25338/...) or as a bare
        # 10.5061/dryad.x went file by file
        from metacheck.archives.dryad import _dryad_doi, _dryad_headers

        def dryad_doi_of(repo: Any) -> str | None:
            try:
                doi = _dryad_doi(repo) if isinstance(repo, str) else None
            except Exception:
                doi = None
            return doi if isinstance(doi, str) and doi != "" else None

        for repo in dict.fromkeys(repo_list[i] for i in remaining_rows()):
            doi = dryad_doi_of(repo)
            if doi is None:
                continue
            ridx = in_repo(repo)
            if not ridx:
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
                # The name only, without github_repo()'s / gitlab_repo()'s HEAD
                # request: the repository was confirmed when it was listed, and
                # the archive request answers 404 by itself.
                if host_kind == "github":
                    from metacheck.archives.github import _github_repo_name

                    clean_repo = _github_repo_name(repo)
                    if clean_repo is None:
                        continue
                    zip_url = f"https://api.github.com/repos/{clean_repo}/zipball"
                    req_func = _headers_fn(_github_config)
                else:
                    from metacheck.archives.gitlab import _gitlab_repo_path

                    clean_repo = _gitlab_repo_path(repo)
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

        # GitHub storage behind an OSF project: API zipball (metacheck #675). A GitHub
        # repository linked to an OSF component is listed through GitHub
        # (``_osf_github_files()``), so each file's address names the repository and
        # branch (raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>), but its
        # repo_url is the OSF project the paper links, which the GitHub loop above does
        # not take. Such files were fetched one request each: 10,313 for osf.io/9526a,
        # at about 80 a minute. They are fetched as the repository's archive, matched by
        # the path in each address, when the archive is worth it
        # (:func:`_storage_archive_skip`: enough files wanted, and not much more data
        # than they hold), else one by one.
        rows_left = remaining_rows()
        gh_raw = _github_raw_parts([file_urls[i] for i in rows_left])
        gh_repo = gh_raw["repo"].tolist()
        gh_ref = gh_raw["ref"].tolist()
        gh_path = gh_raw["path"].tolist()
        behind = [
            not is_missing(gh_repo[k])
            and not bool(_GITHUB_COM_RX.search(as_str(repo_list[i]) or ""))
            for k, i in enumerate(rows_left)
        ]
        gh_key = [f"{gh_repo[k]}@{gh_ref[k]}" for k in range(len(rows_left))]
        for key in dict.fromkeys(gh_key[k] for k, b in enumerate(behind) if b):
            sel = [k for k, b in enumerate(behind) if b and gh_key[k] == key]
            ridx = [rows_left[k] for k in sel]
            repo_name = gh_repo[sel[0]]
            zip_url = (
                f"https://api.github.com/repos/{repo_name}/zipball/"
                f"{_url_encode_reserved(gh_ref[sel[0]])}"
            )
            label = f"github.com/{repo_name} (linked to {as_str(repo_list[ridx[0]]) or 'NA'})"
            # the archive holds the whole repository, not only the files wanted (#679)
            storage_bytes = _storage_size_estimate(df, ridx)
            skip_why = _storage_archive_skip(df, ridx, max_download_size, _MB)
            if skip_why is not None:
                _message(
                    f"Skipping zip for {label} ({skip_why}); downloading its files individually."
                )
                continue  # leaves ridx in `remaining` for the file-by-file loop below
            _message(f"Downloading {label} as zip ({len(ridx)} file{plural(len(ridx))})...")
            run_zip(
                ridx,
                zip_url,
                strip_dir=True,
                req_func=_headers_fn(_github_config),
                max_bytes=archive_cap,
                expected_bytes=storage_bytes,
                rel_paths=[gh_path[k] for k in sel],
                **zip_kw,
            )

        # OSF Storage: one zip per component (metacheck #675). The OSF builds a zip of one
        # component's OSF Storage on request
        # (files.osf.io/v1/resources/<component>/providers/osfstorage/?zip=), with the files
        # under their paths in that storage and no folder above them. It was removed
        # (#424) because it gives no size in advance; it now follows the rule above, with
        # the size repo_check listed for the storage (``storage_size``, which includes any
        # zip file in it, though its members are fetched by byte range). The zip holds
        # only that component's storage, not its components or other storages, so each
        # component is asked for on its own, when the archive is worth it
        # (:func:`_storage_archive_skip`: more than 50 of the storage's files wanted or at
        # least half of them, and an archive at most twice the size of the wanted files):
        # data_check wanting 3 data files of a storage of 500 fetches the 3. Rows of a
        # listing made before repo_check recorded the component (``osf_node``) are fetched
        # one by one.
        from metacheck.archives.osf_helpers import _osf_headers

        osf_node = _col(df, "osf_node")
        if osf_node is not None:
            osf_zip_rows = [
                i for i in remaining_rows() if not is_missing(osf_node[i]) and is_osfstorage(i)
            ]
            for node in dict.fromkeys(osf_node[i] for i in osf_zip_rows):
                ridx = [i for i in osf_zip_rows if osf_node[i] == node]
                n_wanted = len(ridx)
                label = f"OSF Storage of osf.io/{node}"
                skip_why = _storage_archive_skip(df, ridx, max_download_size, _MB)
                if skip_why is not None:
                    _message(
                        f"Skipping zip for {label} ({skip_why}); "
                        "downloading its files individually."
                    )
                    continue  # leaves ridx in `remaining` for the file-by-file loop below
                zip_url = f"https://files.osf.io/v1/resources/{node}/providers/osfstorage/?zip="
                # the time limit follows the whole storage, which the zip holds, and its number
                # of files, as the OSF builds the zip file by file (#679)
                storage_bytes = _storage_size_estimate(df, ridx)
                storage_files = math.nan
                if "storage_n" in df.columns:
                    col = df["storage_n"].tolist()
                    counts = [c for c in _nums(col[i] for i in ridx) if not is_missing(c)]
                    if counts:
                        storage_files = max(counts)
                _message(f"Downloading {label} as zip ({n_wanted} file{plural(n_wanted)})...")
                run_zip(
                    ridx,
                    zip_url,
                    strip_dir=False,
                    req_func=_osf_headers,
                    max_bytes=archive_cap,
                    expected_bytes=storage_bytes,
                    expected_files=storage_files,
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
                    r"zenodo\.org", [as_str(file_urls[i]) for i in remaining], ignore_case=True
                )
                # a file of unknown size is fetched one at a time with its byte limit
                parallel_safe = [
                    (is_osfstorage(i) or bool(z)) and i not in byte_cap
                    for i, z in zip(remaining, zen, strict=True)
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
                    over_cap, got_bytes = False, 0
                    # A file of unknown size gets what is left after the files of unknown size
                    # before it (#699)
                    st_i = budget_of(repo_urls[i]) if i in byte_cap else None
                    if st_i is not None:
                        byte_cap[i] = min(byte_cap[i], _budget_free(st_i))
                        if byte_cap[i] <= 0:
                            limit_refused[i] = refused_budget
                            if pb is not None:
                                pb.tick()
                            continue
                    if i in byte_cap:
                        err, over_cap, got_bytes = _download_capped(
                            str(file_urls[i]),
                            cache_now[i],
                            max_bytes=byte_cap[i],
                            skip_on_api_limit=skip_on_api_limit,
                        )
                    else:
                        err = _download_one(
                            str(file_urls[i]),
                            cache_now[i],
                            skip_on_api_limit=skip_on_api_limit,
                            expected_bytes=_num(sizes_col[i])
                            if sizes_col is not None
                            else math.nan,
                        )
                    if over_cap:
                        # a file of unknown size that passed its limit: reported as over
                        # the limit, like a file whose listed size is over it. Its limit was
                        # the per-file limit, or what the budget had left when that is less.
                        limit_refused[i] = (
                            refused_budget if byte_cap[i] < max_file_size * _MB else refused_size
                        )
                        oversize_rows.append((repo_urls[i], file_names[i], float(got_bytes)))
                        _message(
                            f"{as_str(file_names[i]) or 'NA'} in {as_str(repo_urls[i]) or 'NA'} "
                            "had no known size and was larger than its "
                            f"{_cap_num(_rround(byte_cap[i] / _MB))} MB limit, so it was not "
                            "kept. Raise max_file_size (or max_download_size) to include it."
                        )
                    elif err is None:
                        df.iat[i, _loc(df)] = cache_now[i]
                        if st_i is not None:
                            st_i.disk += file_bytes(cache_now[i])
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
                r for r in failed_rows if r[0] is repo or (not is_missing(r[0]) and r[0] == repo)
            ]
            k = len(frows)
            first_err = sub("\n.*", "", frows[0][4])
            _message(
                f"{k} download{plural(k)} from {as_str(repo) or 'NA'} failed after retries "
                f"(e.g. {as_str(frows[0][1]) or 'NA'}: {first_err}). Re-run to retry: cached "
                "files are reused, only the missing files are fetched."
            )

    if cache is True and to_get and get_option("metacheck.repo_cache.notified") is not True:
        _message(
            f"Downloaded files are cached in {_repo_cache_dir()} and reused on re-runs (never "
            "cleared automatically). Free the space with repo_cache_clear()."
        )
        options({"metacheck.repo_cache.notified": True})

    out = df.drop(columns=[".cache_path"] if had_size else [".cache_path", "file_size"])
    out["file_location"] = pd.Series(
        [None if is_missing(v) else str(v) for v in out["file_location"].tolist()],
        dtype="string",
    )
    out.index = index
    out.attrs["gated"] = _frame(
        {
            "repo_url": ([as_str(r[0]) for r in gated_rows], "string"),
            "message": ([r[1] for r in gated_rows], "string"),
        }
    )
    out.attrs["oversize_skipped"] = _frame(
        {
            "repo_url": ([as_str(r[0]) for r in oversize_rows], "string"),
            "file_name": ([as_str(r[1]) for r in oversize_rows], "string"),
            "file_size": ([r[2] for r in oversize_rows], "float64"),
        }
    )
    out.attrs["failed"] = _frame(
        {
            "repo_url": ([as_str(r[0]) for r in failed_rows], "string"),
            "file_name": ([as_str(r[1]) for r in failed_rows], "string"),
            "file_url": ([as_str(r[2]) for r in failed_rows], "string"),
            "paper_id": ([as_str(r[3]) for r in failed_rows], "string"),
            "error": ([r[4] for r in failed_rows], "string"),
        }
    )
    # a file that is on disk after all (a copy found later) was not refused
    out.attrs["limit_refused"] = pd.Series(
        [
            reason if is_missing(loc) else None
            for loc, reason in zip(out["file_location"].tolist(), limit_refused, strict=True)
        ],
        dtype="string",
    )
    return out


def _rround(x: float) -> float:
    from metacheck._r import r_round

    return float(r_round(x))


def _repos_not_listed_text(partial: Any, gated: Any, repo_urls: Any) -> dict[str, Any]:
    """Port of ``.repos_not_listed_text()``: the report section and summary sentence for repositories
    repo_check could not list in full (metacheck #681), for code_check and data_check.

    OSF projects listed only in part (repo_check's ``partial_repos``, with ``reason`` "request
    limit" or "refused by the OSF"), and repositories that could not be listed at all (its
    ``gated_repos``, with ``repo_error``) and have no files in *repo_urls*, the repositories of the
    files the check has; repo_check's own report names the same ones as "could not be checked".
    Until metacheck 0.4.4.22 these checks said nothing of either, so a check on part of the files
    read as a check on all of them. Returns ``{"report": [...] or None, "summary": str or None}``.
    """
    import pandas as pd

    none: dict[str, Any] = {"report": None, "summary": None}
    if not isinstance(partial, pd.DataFrame) or "repo_url" not in partial:
        partial = None
    if not isinstance(gated, pd.DataFrame) or "repo_url" not in gated:
        gated = None
    if partial is not None:
        partial = partial.drop_duplicates("repo_url")
    if gated is not None:
        have = set(repo_urls) if repo_urls is not None else set()
        listed_part = set(partial["repo_url"]) if partial is not None else set()
        keep = [
            not is_missing(u) and u not in have and u not in listed_part for u in gated["repo_url"]
        ]
        gated = gated[keep].drop_duplicates("repo_url")
    n_part = 0 if partial is None else len(partial)
    n_gated = 0 if gated is None else len(gated)
    if n_part + n_gated == 0:
        return none

    part_line: list[str] = []
    part_why: list[str] = []
    if partial is not None and n_part > 0:
        reasons = (
            partial["reason"].tolist() if "reason" in partial else [None] * n_part  # type: ignore[list-item]
        )
        counts = (
            partial["files_listed"].tolist() if "files_listed" in partial else [None] * n_part  # type: ignore[list-item]
        )
        for url, why, n in zip(partial["repo_url"], reasons, counts, strict=True):
            refused = why == "refused by the OSF"
            listed = "" if is_missing(n) else f" ({int(n)} file{'' if int(n) == 1 else 's'} listed)"
            if refused:
                part_line.append(
                    f"- {url}: the OSF refused part of the listing, also when asked again{listed}. "
                    "Run the check again later."
                )
                part_why.append(f"{url} (the OSF refused part of the listing)")
            else:
                part_line.append(
                    f"- {url}: the listing stopped at the request limit{listed}. Run the check "
                    "again with `cache = TRUE` to list the next part, or raise `osf_max_requests`."
                )
                part_why.append(f"{url} (listed in part: the request limit was reached)")
    gated_line: list[str] = []
    gated_why: list[str] = []
    if gated is not None and n_gated > 0:
        errs = gated["repo_error"].tolist() if "repo_error" in gated else [None] * n_gated  # type: ignore[list-item]
        for url, e in zip(gated["repo_url"], errs, strict=True):
            err = "no reason given" if is_missing(e) or str(e) == "" else str(e)
            gated_line.append(
                f"- {url}: could not be listed ({err}), so none of its files were checked."
            )
            gated_why.append(f"{url} (could not be listed: {err})")
    n_all = n_part + n_gated
    report = [
        "#### Repositories not listed in full",
        " ".join(
            [
                "The checks below did not see every file of",
                "this repository, because it" if n_all == 1 else "these repositories, because they",
                "could not be listed in full:",
            ]
        ),
        *part_line,
        *gated_line,
    ]
    summary = (
        f"We could not list every file of {n_all} {plural(n_all, 'repository', 'repositories')}, "
        f"so the checks did not see all {'its' if n_all == 1 else 'their'} files: "
        f"{'; '.join([*part_why, *gated_why])}."
    )
    return {"report": report, "summary": summary}
