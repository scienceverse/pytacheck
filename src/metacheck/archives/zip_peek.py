"""Peek inside remote ZIP archives without downloading them (port of ``R/zip-peek.R``).

A ZIP stores its file listing (the "central directory") at the end of the
archive, so an HTTP range request for the tail is enough to read every entry's
name and uncompressed size (:func:`zip_peek`). That lets the downloader decide
whether an archive is worth fetching (:func:`zip_decision`) and fetch single
members by byte range (:func:`_zip_fetch_members`) -- each member of a zip is
compressed on its own, which is what makes this possible for ``.zip`` and not
for ``.7z`` or ``.tar.gz``. The zip format itself is read by :mod:`zipfile`,
over a :class:`_RangeFile` that answers its reads with range requests (Zip64,
a central directory larger than the first tail, bzip2 and lzma members all
work); metacheck's own reader of the format (R's ``zip``/minizip) is not
reproduced.

Also here: the archive-format classification (:func:`_is_zip`,
:func:`_is_tar_archive`, :func:`_is_single_compress`,
:func:`_is_readable_archive`) and the expansion of downloaded archives into
``data_check`` rows (:func:`_expand_zip`, :func:`_expand_tar`,
:func:`_expand_compressed`).

All requests go through the storage retry policy and host authentication of
:mod:`metacheck.archives.download` (``.auth_for_url()``,
``.storage_is_transient_factory()`` ...).
"""

from __future__ import annotations

import contextlib
import io
import math
import os
import re
import shutil
import threading
import warnings
import zipfile
import zlib
from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from metacheck._r import grepl, gsub, strsplit, sub
from metacheck._values import is_missing
from metacheck.archives._atomic import atomic_write

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["zip_decision", "zip_peek"]

#: R: .zip_peek_cache -- same-session cache of zip_peek() results (a success
#: or a failure, ``None``), keyed by URL. Never persisted.
_ZIP_PEEK_CACHE: dict[Any, Any] = {}  # a URL -> its listing; (URL, "tail") -> its last bytes
_CACHE_LOCK = threading.Lock()

_ZIP64 = 0xFFFFFFFF  # the Zip64 sentinel in a 4-byte field


# -- helpers -------------------------------------------------------------------


def _num(x: Any) -> float:
    """A number as R double (``NA`` -> ``nan``)."""
    if is_missing(x):
        return float("nan")
    return float(x)


def _chr_list(x: Any) -> list[str | None]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, Iterable):
        return [None if is_missing(v) else str(v) for v in x]
    return [None if is_missing(x) else str(x)]


def _grepl(pattern: str, x: Sequence[str | None], ignore_case: bool = False) -> list[bool]:
    """``grepl()`` (``FALSE`` for ``NA``)."""
    return [bool(h) for h in grepl(pattern, x, ignore_case=ignore_case)]


# -- member names ---------------------------------------------------------------


def _decode_zip_name(raw: bytes, utf8_flag: bool) -> str:
    """A zip member name as text.

    Flagged UTF-8 (general purpose bit 11) is UTF-8. An unflagged name is
    UTF-8 when it decodes as UTF-8 (many tools write UTF-8 without the flag),
    else CP437, the zip format's default (older Windows tools). metacheck
    reads every name as UTF-8, and its string functions then fail on a CP437
    name (U76). A flagged name that is not valid UTF-8 keeps its undecodable
    bytes as lone surrogates.
    """
    if not utf8_flag:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp437")
    return raw.decode("utf-8", errors="surrogateescape")


# -- HTTP range requests -------------------------------------------------------
#
# A HEAD request is the usual way to learn a file's size, but Dryad, Figshare
# and Harvard Dataverse redirect downloads to Amazon S3, which answers HEAD
# with 403 and a ranged GET with 206. A 206 or 416 answer carries the total
# size in its Content-Range header ("bytes 0-0/2545568", "bytes */2246"), so
# a ranged GET is the fallback whenever HEAD gives no usable size. The ranged
# GETs that probe a size read the status first and only read a 206 body, so a
# host that ignores the range (whole-archive endpoints stream the whole zip
# with no size) cannot make them transfer a whole archive.


def _content_length(resp: Any) -> float:
    """``suppressWarnings(as.numeric(resp_header(h, "content-length")))``.

    ``None`` stands for R's ``numeric(0)`` (no such header).
    """
    from metacheck._values import as_float

    value = resp.headers.get("content-length")
    if value is None:
        return None  # type: ignore[return-value]
    num = as_float(value)
    return math.nan if num is None else num


class _RangeBody(bytes):
    """The bytes of a tail request, with the file's size (R: ``attr(, "total")``)."""

    total: float = math.nan


def _with_total(body: bytes, total: Any) -> _RangeBody:
    out = _RangeBody(body)
    out.total = _num(total)
    return out


#: Set while :func:`zip_peek` runs: a request of the peek failed for a
#: reason that can pass (a rate-limit skip, a connection failure, a 429 or
#: 5xx answer), so a failed peek is not written to the disk cache (U210).
_PEEK_TRANSIENT: ContextVar[list[bool] | None] = ContextVar("zip_peek_transient", default=None)


def _note_transient(status: Any = None) -> None:
    """Flag the running peek's failure as one that can pass.

    No *status*: a rate-limit skip or a connection failure. A status counts
    when the storage retry rule retries it (403, 429, 5xx).
    """
    from metacheck.archives.download import _STORAGE_TRANSIENT

    flag = _PEEK_TRANSIENT.get()
    if flag is None:
        return
    if status is None or int(status) in _STORAGE_TRANSIENT:
        flag[0] = True


def _size_probe_is_transient(resp: Any) -> bool:
    """Port of ``R/zip-peek.R::.size_probe_is_transient_factory()``.

    The retry rule of a size request: the storage rule
    (:func:`metacheck.archives.download._storage_is_transient`) minus 403,
    which S3 answers to every HEAD (retrying it only adds backoff).
    """
    from metacheck.archives.download import _storage_is_transient

    return int(resp.status_code) != 403 and _storage_is_transient(resp)


def _content_range_total(resp: Any) -> float:
    """Port of ``R/zip-peek.R::.content_range_total()``: the size in ``Content-Range``.

    ``nan`` (R ``NA``) when the header is absent or its total is ``*``.
    """
    cr = resp.headers.get("content-range")
    if cr is None:
        return math.nan
    m = re.search(r"/([0-9]+)\s*$", cr)
    return float(m.group(1)) if m else math.nan


#: hosts whose firewall check was already warned about (R: ``.waf_challenge_warned``)
_WAF_CHALLENGE_WARNED: set[str] = set()


def _waf_challenge_check(resp: Any, url: str) -> bool:
    """Port of R/zip-peek.R::.waf_challenge_check(): is *resp* an AWS WAF browser check?

    A host behind an Amazon web firewall can answer a request with a browser
    check instead of the file: HTTP 202, an empty body and the header
    ``x-amzn-waf-action: challenge``, which only a browser running JavaScript
    can pass. Such an answer gives no size, and a file of unknown size is left
    out of the downloads without a word, so this warns once per host and
    session, saying why.
    """
    action = resp.headers.get("x-amzn-waf-action")
    if action is None or action.lower() != "challenge":
        return False
    try:
        host = (urlsplit(url).hostname or "").lower() or None
    except ValueError:
        host = None
    if host is not None and host not in _WAF_CHALLENGE_WARNED:
        _WAF_CHALLENGE_WARNED.add(host)
        warnings.warn(
            f"{host}'s firewall answered with a browser check (AWS WAF challenge) "
            "instead of the file, usually after many requests in a short time. "
            "Its files cannot be sized or downloaded until it lifts; try again later.",
            stacklevel=2,
        )
    return True


def _head_size(url: str, skip_on_api_limit: bool = False) -> float:
    """Port of ``R/zip-peek.R::.head_size()``: the size from a HEAD request.

    Only a 2xx answer counts (an error answer's Content-Length is the error
    page's); a missing, non-numeric or non-positive Content-Length gives
    ``nan`` (R ``NA``), as do a known rate limit under *skip_on_api_limit*
    and any error. 403 is not retried.

    A rate limit, a connection failure or a 429/5xx answer flags the running
    peek's failure as one that can pass (U210), so a listing the size would
    have made possible is not cached as a failure. 403 is not flagged: S3
    hosts always refuse HEAD.
    """
    from metacheck.archives.download import _storage_request

    try:
        resp = _storage_request(
            "HEAD",
            url,
            skip_on_api_limit=skip_on_api_limit,
            is_transient=_size_probe_is_transient,
        )
        if _waf_challenge_check(resp, url):
            return math.nan
        status = int(resp.status_code)
        if status < 200 or status >= 300:
            if status != 403:
                _note_transient(status)
            return math.nan
        cl = _content_length(resp)
        if cl is None or is_missing(cl) or cl <= 0:
            return math.nan
        return float(cl)
    except Exception:
        _note_transient()
        return math.nan


def _http_range_get(
    url: str, rng: str, skip_on_api_limit: bool = False
) -> tuple[int | None, float, bytes | None]:
    """Port of ``R/zip-peek.R::.http_range_get()``: one ranged GET for a size probe.

    *rng* is the Range header value (R: ``range``). Returns ``(status,
    total, body)``: *total* from ``Content-Range``, *body*
    read only for a 206 answer (any other answer is closed unread). A known
    rate limit under *skip_on_api_limit* gives ``(None, nan, None)``. 403 is
    not retried. A connection failure raises.
    """
    from metacheck.archives.download import _RateLimitSkipped, _storage_request

    try:
        resp = _storage_request(
            "GET",
            url,
            headers={"Range": rng},
            skip_on_api_limit=skip_on_api_limit,
            is_transient=_size_probe_is_transient,
            read_body=lambda r: int(r.status_code) == 206,
        )
    except _RateLimitSkipped:
        _note_transient()
        return None, math.nan, None
    except Exception:
        _note_transient()
        raise
    if _waf_challenge_check(resp, url):
        return int(resp.status_code), math.nan, None
    status = int(resp.status_code)
    _note_transient(status)
    body = bytes(resp.content) if status == 206 else None
    return status, _content_range_total(resp), body


def _range_size(url: str, skip_on_api_limit: bool = False) -> float:
    """Port of ``R/zip-peek.R::.range_size()``: the size from a one-byte ranged GET.

    ``bytes=0-0``; a 206 or 416 answer with a positive total in its
    ``Content-Range`` gives the size, anything else ``nan`` (R ``NA``).
    """
    try:
        status, total, _body = _http_range_get(url, "bytes=0-0", skip_on_api_limit)
        if status in (206, 416) and math.isfinite(total) and total > 0:
            return total
        return math.nan
    except Exception:
        return math.nan


def _known_size(size: Any) -> float:
    """A size a caller passed (``zip_peek(size = )``): a positive number, else ``nan``."""
    try:
        value = _num(size)
    except (TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) and value > 0 else math.nan


# -- hosts that ignore Range -----------------------------------------------------
#
# Some hosts answer a ranged GET with 200 and the whole file: PsychArchives'
# /rest/bitstreams/<id>/retrieve, the OSF's proxy for files in a GitHub add-on
# and Zenodo's whole-record files-archive. Once a host has shown it, later
# peeks and member fetches of that kind of URL give up for the session before
# sending anything (metacheck d52b3fd). The record is keyed on the host and the
# shape of the path, each segment holding a digit replaced by "*" (so
# ".../bitstreams/<uuid>/retrieve" covers every PsychArchives file), not on the
# host alone: zenodo.org honours ranges on its per-file /content URLs. A 200
# that came from another host after a redirect is not recorded: osf.io/download/
# <id>/ redirects files with and without range support alike. Only a range
# starting after byte 0 shows it, since a range from byte 0 may cover the whole
# file, which a host may answer with a plain 200. The record lives in the
# session cache, so clearing that clears it too.

#: A 200 answer to a tail request is read only when the file is at most this
#: large (or the tail asked for, if larger); a larger one is closed unread.
_WHOLE_BODY_MAX = 2 * 1048576

_RANGE_IGNORED = "range ignored"  # the session cache key's tag: (_RANGE_IGNORED, key)


def _range_ignored_key(url: str) -> str | None:
    """Port of ``R/zip-peek.R::.range_ignored_key()``: the host and the shape of the path."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(url))
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    segs = ["*" if re.search("[0-9]", s) else s for s in parts.path.split("/") if s]
    return f"{host.lower()}/{'/'.join(segs)}"


def _range_ignored_known(url: str) -> bool:
    """Port of ``R/zip-peek.R::.range_ignored_known()``: has this kind of URL ignored a range?"""
    key = _range_ignored_key(url)
    with _CACHE_LOCK:
        return key is not None and (_RANGE_IGNORED, key) in _ZIP_PEEK_CACHE


def _range_ignored_record(url: str, final_url: Any = None) -> bool:
    """Port of ``R/zip-peek.R::.range_ignored_record()``: remember that *url* ignored a range.

    Not when the answer came from another host (*final_url*, the URL after
    redirects). Returns whether it was recorded.
    """
    from urllib.parse import urlsplit

    key = _range_ignored_key(url)
    if key is None:
        return False
    if final_url is not None:
        try:
            same = urlsplit(str(final_url)).hostname == urlsplit(str(url)).hostname
        except ValueError:
            same = False
        if not same:
            return False
    with _CACHE_LOCK:
        _ZIP_PEEK_CACHE[(_RANGE_IGNORED, key)] = True
    return True


def _final_url(resp: Any) -> Any:
    """The URL an answer came from, after redirects (``None`` when unknown)."""
    try:
        return resp.url
    except Exception:
        return None


def _http_range_tail(
    url: str,
    n: float,
    total: Any = None,
    skip_on_api_limit: bool = False,
    _resized: bool = False,
    keep_whole: bool = False,
) -> _RangeBody | None:
    """Port of ``R/zip-peek.R::.http_range_tail()``: the last *n* bytes of *url*.

    *total* is the file's size when known, ``None`` to ask with a HEAD
    request first, or ``nan`` (R ``NA``) when HEAD gave none: the tail is then
    asked for by its distance from the end (:func:`_http_range_suffix`).
    Returns the bytes -- possibly fewer than *n* for a small file -- with the
    file's size as ``.total``, or ``None`` on failure.

    A host that ignores the range answers 200 with the whole file. That answer
    is read (and its tail kept) only when the file is at most
    ``max(n, 2 MB)``, by its Content-Length or else *total*; a larger one is
    closed unread and ``None`` returned, and a range starting after byte 0
    records the host (:func:`_range_ignored_record`). With *keep_whole* such
    a (small) answer is returned whole rather than its last *n* bytes, so a
    zip's members are all in hand (:func:`_open_zip`). When the host reports
    a different size than *total* (a size from a repository's listing that is
    out of date), the tail is asked for once more at the reported size; a 206
    answer shorter than asked, with no size reported, says the file ends there.
    """
    from metacheck.archives.download import _storage_request

    try:
        if total is None:
            total = _head_size(url, skip_on_api_limit)
        if is_missing(total):
            return _http_range_suffix(url, n, skip_on_api_limit)
        total = float(total)
        if total <= 0:
            return None
        start = max(0.0, total - n)
        keep = max(0, int(n))
        got = bytearray()
        too_big = [False]

        def read(r: Any) -> None:
            # a 206 body is the tail; a 200 (range ignored) is the whole file, of which
            # only the last *keep* bytes are held at a time
            got.clear()
            if r.status_code not in (200, 206):
                return
            if r.status_code == 200:
                if start > 0:
                    _range_ignored_record(url, _final_url(r))
                cl = _content_length(r)
                body_size = total if cl is None or is_missing(cl) else cl
                if not body_size <= max(keep, _WHOLE_BODY_MAX):
                    too_big[0] = True
                    return  # closed unread
            whole = keep_whole and r.status_code == 200
            for chunk in r.iter_bytes():
                got.extend(chunk)
                if whole and len(got) > max(keep, _WHOLE_BODY_MAX):
                    whole = False  # longer than it said: only its tail is kept
                if r.status_code == 200 and not whole and len(got) > keep:
                    del got[: len(got) - keep]

        try:
            r = _storage_request(
                "GET",
                url,
                headers={"Range": f"bytes={start:.0f}-{total - 1:.0f}"},
                skip_on_api_limit=skip_on_api_limit,
                sink=read,
            )
        except Exception:
            _note_transient()
            raise
        _note_transient(r.status_code)
        if r.status_code in (206, 416) and not _resized:
            reported = _content_range_total(r)
            if math.isfinite(reported) and reported > 0 and reported != total:
                return _http_range_tail(
                    url, n, reported, skip_on_api_limit, _resized=True, keep_whole=keep_whole
                )
        if r.status_code == 206 and len(got) < total - start:
            # shorter than asked, with no size to correct *total* by: the file ends there
            total = start + len(got)
        if r.status_code == 206 or (r.status_code == 200 and not too_big[0]):
            return _with_total(bytes(got), total)
        return None
    except Exception:
        return None


def _http_range_suffix(url: str, n: float, skip_on_api_limit: bool = False) -> _RangeBody | None:
    """Port of ``R/zip-peek.R::.http_range_suffix()``: the tail of a file of unknown size.

    ``bytes=-<n>``; only a 206 answer is accepted (a host ignoring the range
    would send the whole file). A 416 (the file is shorter than *n*, on a
    host that refuses an over-long suffix, as GitHub does) still reports the
    size, and the whole small file is then asked for by position.
    """
    status, total, body = _http_range_get(url, f"bytes=-{float(n):.0f}", skip_on_api_limit)
    if status == 206 and body is not None:
        return _with_total(body, total)
    if status == 416 and math.isfinite(total) and total > 0:
        return _http_range_tail(url, n, total=total, skip_on_api_limit=skip_on_api_limit)
    return None


def _range_status_is_transient(resp: Any) -> bool:
    """Port of ``R/zip-peek.R::.range_status_is_transient()``: retry all but 200 and 206.

    A member's byte-range request retries any other status (an intermittent
    401 is not on the storage rule's list); 200 means the host ignores ranges,
    which a retry cannot change.
    """
    return int(resp.status_code) not in (200, 206)


def _set_reason(reason: dict[str, str] | None, msg: str) -> None:
    if reason is not None:
        reason["msg"] = msg


def _http_range_bytes(
    url: str,
    from_: float,
    to: float,
    skip_on_api_limit: bool = False,
    reason: dict[str, str] | None = None,
) -> bytes | None:
    """Port of ``R/zip-peek.R::.http_range_bytes()``: bytes ``[from, to]`` of *url*.

    0-based and inclusive. Only a 206 answer of exactly the requested length
    counts: a 200 (range ignored, the whole archive) is a failure here, closed
    unread, and for a range starting after byte 0 it records the host
    (:func:`_range_ignored_record`); a request to a host so recorded fails at
    once. Any status but 200/206 is retried. On failure *reason*, when given,
    gets a one-line explanation under ``"msg"``.
    """
    from metacheck.archives.download import _RateLimitSkipped, _storage_request

    try:
        if from_ is None or to is None or is_missing(from_) or is_missing(to):
            _set_reason(reason, "invalid byte range")
            return None
        f, t = float(from_), float(to)
        if not math.isfinite(f) or not math.isfinite(t) or f < 0 or t < f:
            _set_reason(reason, "invalid byte range")
            return None
        if _range_ignored_known(url):
            _set_reason(
                reason,
                "HTTP 200 (range not honoured; host seen ignoring ranges earlier this session)",
            )
            return None
        r = _storage_request(
            "GET",
            url,
            headers={"Range": f"bytes={f:.0f}-{t:.0f}"},
            skip_on_api_limit=skip_on_api_limit,
            is_transient=_range_status_is_transient,
            read_body=lambda r: int(r.status_code) == 206,  # a 200 is the whole file: unread
        )
        _note_transient(r.status_code)
        if r.status_code != 206:
            if r.status_code == 200 and f > 0:
                _range_ignored_record(url, _final_url(r))
            _set_reason(reason, f"HTTP {int(r.status_code)} (range not honoured)")
            return None
        body = bytes(r.content)
        if len(body) != (t - f + 1):  # short/over-long read
            _set_reason(reason, "short read (range request returned the wrong length)")
            return None
        return body
    except _RateLimitSkipped:
        _note_transient()
        _set_reason(reason, "host is rate-limited (waiting skipped by skip_on_api_limit)")
        return None
    except Exception as e:
        _note_transient()
        _set_reason(reason, str(e))
        return None


# -- the remote archive as a file ------------------------------------------------

#: What :class:`_RangeFile` asks for when a read misses what it holds.
_READ_AHEAD = 65536

#: The larger tail :func:`_open_zip` tries once for a host that ignores ranges.
_WHOLE_BODY_TAIL = 1048576.0

#: The longest central directory :class:`_RangeFile` fetches (a damaged or hostile
#: archive can claim any length).
_MAX_DIRECTORY = 268435456

#: The most of an archive's end kept for the session once its listing is read
#: (a longer central directory is read again when a member is fetched).
_MAX_HELD = 33554432


class _RangeFile(io.RawIOBase):
    """A read-only, seekable view of a remote file that :class:`zipfile.ZipFile` can read.

    It holds the *tail* already fetched, and one more span. A read outside
    both fetches the next ``max(read, _READ_AHEAD)`` bytes (up to the tail) with
    one range request (:func:`_http_range_bytes`), so a central directory larger
    than the tail costs one request, and :meth:`prefetch` brings a whole member
    in with one. A failed request raises :class:`OSError` saying why.
    """

    def __init__(self, url: str, tail: bytes, size: float, skip_on_api_limit: bool = False) -> None:
        super().__init__()
        self.url = url
        self.size = (
            len(tail) if is_missing(size) else int(size)
        )  # no size: the tail is all there is
        self._skip = skip_on_api_limit
        self._tail = bytes(tail)
        self._tail_start = max(0, self.size - len(tail))
        self._buf = b""
        self._start = 0
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        base = (0, self._pos, self.size)[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def prefetch(self, start: int, end: int) -> None:
        """Hold the bytes ``[start, end)`` of the file (up to its end), fetching them if needed.

        Bytes that continue the span held are added to it (up to
        ``_MAX_HELD``), so the members of one request (:func:`_range_plan`)
        stay in hand when one of them needs a few bytes more.
        """
        end = min(end, self.size, self._tail_start)  # the tail holds the rest
        held_end = self._start + len(self._buf)
        if start >= end or (self._start <= start and end <= held_end):
            return
        if self._buf and self._start <= start <= held_end and end - self._start <= _MAX_HELD:
            start = held_end  # only what is missing
        reason: dict[str, str] = {}
        data = _http_range_bytes(self.url, start, end - 1, self._skip, reason)
        if data is None:
            raise OSError(reason.get("msg", "the range request failed"))
        if self._buf and start == held_end:
            self._buf += data
        else:
            self._buf, self._start = data, start

    def held_tail(self) -> bytes:
        """The end of the file held so far: the tail, with the span before it when they touch.

        Once zipfile has read a central directory longer than the tail, this
        holds all of it, so opening the archive again costs no request.
        """
        touching = self._buf and self._start + len(self._buf) == self._tail_start
        if touching and len(self._buf) + len(self._tail) <= _MAX_HELD:
            return self._buf + self._tail
        return self._tail

    def readinto(self, b: Any) -> int:
        n = min(len(b), self.size - self._pos)
        if n <= 0:
            return 0
        if self._pos >= self._tail_start:
            buf, start = self._tail, self._tail_start
        else:
            if not self._start <= self._pos < self._start + len(self._buf):
                if n > _MAX_DIRECTORY:
                    raise OSError("the archive's central directory is too large to read")
                self.prefetch(self._pos, self._pos + max(n, _READ_AHEAD))
            buf, start = self._buf, self._start
        i = self._pos - start
        chunk = buf[i : i + n]
        b[: len(chunk)] = chunk
        self._pos += len(chunk)
        return len(chunk)


def _open_zip(
    url: str, tail_bytes: float = 131072, skip_on_api_limit: bool = False, size: Any = None
) -> tuple[zipfile.ZipFile, _RangeFile] | None:
    """The remote zip at *url* as a :class:`zipfile.ZipFile`, or ``None`` if it cannot be read.

    The size is *size* when the caller knows it, else a ``HEAD`` request's
    (when the host refuses it, the tail is asked for by its distance from the
    end and its ``Content-Range`` gives the size); then a range request
    fetches the tail (*tail_bytes*); zipfile reads the central directory from
    it, and asks for more only when the directory is longer. Zip64, a comment
    after the directory, bzip2 and lzma members are zipfile's.
    """
    memo = (url, "tail")  # in the session cache, so that clearing it clears this too
    with _CACHE_LOCK:
        held = _ZIP_PEEK_CACHE.get(memo)
    if held is not None:  # members after the listing: no request to open the archive again
        with contextlib.suppress(Exception):
            return _wrap_zip(url, held[0], held[1], skip_on_api_limit)
    total: Any = _known_size(size)
    if is_missing(total):
        total = _head_size(url, skip_on_api_limit)
    # A host that ignores ranges sends the whole file for every request: a file
    # of up to 2 MB is then held whole (every member in hand), a larger one is
    # not read (_http_range_tail). Should a later range be refused all the same,
    # a larger tail is tried once (1 MB).
    for nb in dict.fromkeys([float(tail_bytes), _WHOLE_BODY_TAIL]):
        raw = _http_range_tail(
            url, nb, total=total, skip_on_api_limit=skip_on_api_limit, keep_whole=True
        )
        if raw is None:
            return None
        if is_missing(total) or raw.total != total:  # unknown, or the size given was stale
            total = raw.total
        try:
            opened = _wrap_zip(url, bytes(raw), total, skip_on_api_limit)
        except OSError as e:
            if "range not honoured" not in str(e):
                return None
        except Exception:  # not a zip, or a damaged one
            return None
        else:
            with _CACHE_LOCK:
                _ZIP_PEEK_CACHE[memo] = (opened[1].held_tail(), total)
            return opened
        if not is_missing(total) and nb >= total:
            break  # the whole file was in hand
    return None


def _wrap_zip(
    url: str, tail: bytes, total: float, skip_on_api_limit: bool
) -> tuple[zipfile.ZipFile, _RangeFile]:
    rf = _RangeFile(url, tail, total, skip_on_api_limit)
    return zipfile.ZipFile(io.BufferedReader(rf, _READ_AHEAD)), rf


def _entry_table(infos: Sequence[zipfile.ZipInfo]) -> pd.DataFrame:
    """``zip_peek()``'s table from a zip's entries, directories left out.

    ``name`` (:func:`_zip_name`), ``size`` (uncompressed bytes), ``method``
    (0 stored, 8 deflate, ...), ``csize`` (compressed bytes), ``offset``
    (where the member's local header starts) and ``crc``. A field that still
    holds the Zip64 sentinel (``0xFFFFFFFF``) because the archive gave no
    Zip64 value for it is unknown (``NA``).
    """
    import pandas as pd

    def known(v: int) -> float:
        return math.nan if v == _ZIP64 else float(v)

    kept = [(_zip_name(i), i) for i in infos]
    kept = [(n, i) for n, i in kept if not n.endswith("/")]
    return pd.DataFrame(
        {
            "name": pd.Series([n for n, _ in kept], dtype="string"),
            "size": pd.Series([known(i.file_size) for _, i in kept], dtype="float64"),
            "method": pd.Series([float(i.compress_type) for _, i in kept], dtype="float64"),
            "csize": pd.Series([known(i.compress_size) for _, i in kept], dtype="float64"),
            "offset": pd.Series([known(i.header_offset) for _, i in kept], dtype="float64"),
            "crc": pd.Series([float(i.CRC) for _, i in kept], dtype="float64"),
            "name_bytes": pd.Series(
                [float(_zip_stored_name_len(i)) for _, i in kept], dtype="float64"
            ),
        }
    )


_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _is_local_file(x: Any) -> bool:
    """Port of ``R/zip-peek.R::.is_local_file()``: an existing file, not a URL.

    Anything with a URL scheme (``https://``, ``file://``) is a URL.
    """
    if not isinstance(x, str | os.PathLike):
        return False
    s = os.fspath(x)
    if not isinstance(s, str) or not s or _URL_SCHEME.match(s):
        return False
    return os.path.isfile(s)


def _zip_local_source(files: pd.DataFrame) -> list[str | None]:
    """Port of ``R/zip-peek.R::.zip_local_source()``: each row's zip on disk, if any.

    ``file_location`` when it names an existing file, else ``None``. A row with
    a local copy is listed and read from disk (:func:`zip_peek` and
    :func:`_zip_fetch_members` take a path) instead of through its
    ``file_url``: a ResearchBox row has the box's web page as ``file_url``,
    which answers a range request with 406, and the copy unpacked from the
    box's zip as ``file_location``.
    """
    if "file_location" not in files.columns:
        return [None] * len(files)
    return [
        str(p) if not is_missing(p) and _is_local_file(str(p)) else None
        for p in files["file_location"].tolist()
    ]


def _zip_list_local(path: str) -> pd.DataFrame | None:
    """Port of ``R/zip-peek.R::.zip_list_local()``: :func:`zip_peek`'s table for a zip on disk."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except Exception:  # not a zip, or a damaged one
        return None
    return _entry_table(infos) if infos else None


def zip_peek(
    url: str,
    tail_bytes: float = 131072,
    cache: bool = False,
    skip_on_api_limit: bool = False,
    size: Any = None,
) -> pd.DataFrame | None:
    """Peek at the contents of a remote ZIP file without downloading it.

    Port of ``R/zip-peek.R::zip_peek()``. The archive's size is *size* when
    the caller knows it (a repository's file listing gives it); otherwise a
    ``HEAD`` request gives it (when the host refuses HEAD, the tail is asked
    for by its distance from the end and its ``Content-Range`` gives the
    size). Then a range request fetches the tail (*tail_bytes*) and
    :mod:`zipfile` reads the central directory from it, asking for the rest of
    a larger one (see :func:`_open_zip`). *url* may also be the path of a zip
    on disk, which is then read directly, with no request and no caching.
    A URL of a kind seen ignoring range requests this session
    (:func:`_range_ignored_known`) gives ``None`` without a request.

    Returns a data frame with ``name`` (entry path inside the zip) and
    ``size`` (uncompressed bytes), excluding directory entries, plus
    ``method``, ``csize``, ``offset`` and ``crc`` (where each member's bytes
    sit, for fetching one member alone; ``NA`` where a Zip64 archive left a
    field unresolved); or
    ``None`` when the host does not support range requests or the listing
    cannot be read. Results -- successes and failures -- are cached per URL
    for the session. With *cache* they are also kept on disk for later
    sessions (:func:`~metacheck.archives.zip_peek_cache.zip_peek_cache_clear`);
    a failure that can pass (rate limit, connection, 429/5xx) is not (U210).
    *skip_on_api_limit* gives up on a host known to be rate-limited instead
    of waiting out its reset.
    """
    from metacheck.archives import zip_peek_cache as disk

    if _is_local_file(url):  # a local file can change: not cached
        return _zip_list_local(os.fspath(url))

    with _CACHE_LOCK:
        if url in _ZIP_PEEK_CACHE:
            return _ZIP_PEEK_CACHE[url]  # type: ignore[no-any-return]

    if _range_ignored_known(url):
        with _CACHE_LOCK:
            _ZIP_PEEK_CACHE[url] = None
        return None

    if cache is True and disk._zip_peek_cache_has(url):
        hit, cd = disk._zip_peek_cache_lookup(url)
        if hit:
            with _CACHE_LOCK:
                _ZIP_PEEK_CACHE[url] = cd
            return cd  # type: ignore[no-any-return]

    flag = [False]
    token = _PEEK_TRANSIENT.set(flag)

    def done(value: pd.DataFrame | None) -> pd.DataFrame | None:
        with _CACHE_LOCK:
            _ZIP_PEEK_CACHE[url] = value
        if cache is True and (value is not None or not flag[0]):
            disk._zip_peek_cache_put(url, value)
        return value

    try:
        opened = _open_zip(url, tail_bytes, skip_on_api_limit, size=size)
        if opened is None:
            return done(None)
        with opened[0] as zf:
            infos = zf.infolist()
        return done(_entry_table(infos) if infos else None)
    finally:
        _PEEK_TRANSIENT.reset(token)


# -- single-member extraction --------------------------------------------------


class _RecordMismatch(Exception):
    """A member's bytes are not what the archive's listing records."""


def _member_error(e: Exception, info: zipfile.ZipInfo) -> str:
    """Why a member could not be read, for ``_zip_fetch_members()``'s ``error`` column."""
    if isinstance(e, NotImplementedError):
        return f"unsupported compression method ({info.compress_type})"
    if isinstance(e, zipfile.BadZipFile) and "CRC" in str(e):
        return "CRC32 mismatch (corrupt download)"
    if isinstance(e, RuntimeError) and "encrypted" in str(e):
        return "the member is encrypted"
    if isinstance(e, _RecordMismatch | OSError):
        return str(e) or "the range request failed"  # a _RangeFile says why
    return f"decompression failed ({e})" if str(e) else "decompression failed"


def _member_bytes(zf: zipfile.ZipFile, info: zipfile.ZipInfo, verify: bool) -> bytes:
    """One member's bytes, read through the archive's :class:`_RangeFile`.

    The caller has fetched the member's range (:func:`_range_plan`); a few
    bytes still missing (a local extra field longer than the margin) are
    fetched by the file itself. zipfile inflates the data and, with *verify*,
    checks the CRC32 stored in the archive; the size must be the archive's.
    Raises what :func:`_member_error` words.
    """
    with zf.open(info) as src:
        if not verify:
            src._expected_crc = None  # type: ignore[attr-defined]  # as _unzip_all() does
        data = src.read()
    if len(data) != info.file_size:
        raise _RecordMismatch("decompressed size did not match the archive's record")
    return data


def _safe_member_path(name: str) -> str | None:
    """The member's path relative to the destination, or ``None`` if it climbs out."""
    rel = gsub(r"\\", "/", name)
    rel = sub("^([A-Za-z]:)?/+", "", rel)
    if any(part == ".." for part in strsplit(rel, "/", fixed=True)):
        return None
    from metacheck.utils import _portable_path

    return str(_portable_path([str(rel)])[0])


# -- byte ranges and whole archives ----------------------------------------------
#
# A member costs one range request: its local header (30 bytes), its name, its
# compressed data and a margin for the local extra field, whose length only the
# local header gives. Members close together share one request (metacheck
# d52b3fd: the 7 code files of Harvard Dataverse doi:10.7910/DVN/SVVULN lie in
# one 23 KB stretch of its zip). When many members are wanted, downloading the
# whole archive once can be cheaper still (metacheck #471): see
# :func:`_zip_whole_is_cheaper`.

#: Room a member's range leaves for its local extra field.
_MEMBER_MARGIN = 1024
#: Members at most this far apart share one range request (cheaper than a round trip).
_MAX_GAP = 65536
#: The longest range one request asks for when it covers several members.
_MAX_SPAN = 16 * 1048576
#: What one request costs, counted in bytes of transfer, when weighing a whole
#: download against range requests: a range request took about 0.6 s on Figshare,
#: about as long as 1 MB took to arrive (metacheck's measurements, 2026-10-13).
_REQUEST_BYTES = 1048576.0


def _name_bytes(name: Any) -> int:
    return len(str(name).encode("utf-8", "surrogateescape"))


def _stored_name_bytes(cd: pd.DataFrame) -> list[int]:
    """Each listed name's stored length: the ``name_bytes`` column, else the UTF-8 length."""
    if "name_bytes" in cd.columns:
        return [
            int(v) if not is_missing(v) else _name_bytes(n)
            for v, n in zip(cd["name_bytes"].tolist(), cd["name"].tolist(), strict=True)
        ]
    return [_name_bytes(v) for v in cd["name"].tolist()]


def _range_plan(
    spans: Sequence[tuple[int, float, float]],
    bounds: Iterable[Any] = (),
    margin: int = _MEMBER_MARGIN,
    max_gap: int = _MAX_GAP,
    max_span: int = _MAX_SPAN,
) -> list[tuple[list[int], int, int]]:
    """Port of ``R/zip-peek.R::.zip_range_plan()``: the range requests for some members.

    *spans* holds ``(key, start, need_end)`` per member: where its local
    header starts and where its data ends without the local extra field
    (0-based, inclusive). A member's range runs *margin* bytes further, cut at
    the next of *bounds* (the other local headers and the start of the
    central directory) or of the members' own starts, which its bytes cannot
    run past. Members are merged
    into one request when the gap between them is at most *max_gap* and the
    merged range stays within *max_span*; a member larger than that still
    gets a request of its own. Returns ``(keys, start, end)`` per request, in
    the order of the archive.
    """
    import bisect

    cuts = sorted({int(b) for b in bounds if not is_missing(b)} | {int(s[1]) for s in spans})
    ranged: list[tuple[int, int, int]] = []
    for key, start, need_end in spans:
        first, need = int(start), int(need_end)
        end = need + margin
        k = bisect.bisect_right(cuts, first)  # the first bound after the member's start
        if k < len(cuts) and cuts[k] - 1 < end:
            end = max(need, cuts[k] - 1)
        ranged.append((first, end, key))
    plan: list[tuple[list[int], int, int]] = []
    for first, end, key in sorted(ranged, key=lambda r: r[0]):
        if plan:
            keys, s, e = plan[-1]
            if first - e - 1 <= max_gap and max(e, end) - s + 1 <= max_span:
                keys.append(key)
                plan[-1] = (keys, s, max(e, end))
                continue
        plan.append(([key], first, end))
    return plan


def _zip_whole_size(cd: pd.DataFrame) -> float:
    """Port of ``R/zip-peek.R::.zip_whole_size()``: a zip's size estimated from its listing.

    The end of the last member's data, plus the central directory and its end
    record. Extra fields are not in the listing, so this can be short by a few
    dozen bytes per member. ``nan`` when an offset or compressed size is unknown.
    """
    offsets = [_num(v) for v in cd["offset"].tolist()]
    csizes = [_num(v) for v in cd["csize"].tolist()]
    if not offsets or any(math.isnan(v) for v in offsets + csizes):
        return math.nan
    names = _stored_name_bytes(cd)
    data_end = max(o + 30 + nb + c for o, nb, c in zip(offsets, names, csizes, strict=True))
    zip64 = 76 if data_end >= _ZIP64 or len(cd) >= 65535 else 0  # its own end records
    return data_end + sum(46 + nb for nb in names) + 22 + zip64


def _zip_whole_is_cheaper(
    n_members: int,
    size: float,
    plan: Sequence[tuple[list[int], int, int]],
    max_whole_bytes: Any,
    min_whole_members: int = 3,
    request_bytes: float = _REQUEST_BYTES,
) -> bool:
    """Port of ``R/zip-peek.R::.zip_whole_is_cheaper()``: download the archive instead?

    Yes when at least *min_whole_members* members need fetching, the archive's
    *size* is at most *max_whole_bytes* (0 turns this off) and one download
    costs no more than the range requests of *plan*, each request counted as
    *request_bytes* bytes of transfer. ``request_bytes = inf`` counts requests
    only, so the archive is taken whenever it fits the budget (Dryad: every
    request counts against a daily quota).
    """
    budget = _num(max_whole_bytes) if max_whole_bytes is not None else 0.0
    if n_members < min_whole_members or not budget > 0:
        return False
    if not size <= budget:  # nan: unknown
        return False
    if math.isinf(request_bytes):
        return True
    ranged = sum(e - s + 1 for _, s, e in plan)
    return request_bytes + size <= len(plan) * request_bytes + ranged


def _whole_request_bytes(url: Any) -> float:
    """What a request costs for :func:`_zip_whole_is_cheaper`: ``inf`` on Dryad.

    Dryad counts every request against a strict daily quota, so an archive
    from Dryad is downloaded whole whenever it fits the budget (metacheck #471).
    """
    return math.inf if re.search(r"datadryad\.org", str(url), re.IGNORECASE) else _REQUEST_BYTES


def _zip_readable(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return bool(zf.infolist())
    except Exception:
        return False


def _zip_extract_members(
    archive: str,
    names: Sequence[str],
    targets: Sequence[str | None],
    sizes: Sequence[float],
    crcs: Sequence[float],
    verify: bool = True,
) -> list[str | None]:
    """Port of ``R/zip-peek.R::.zip_extract_members()``: members of a zip on disk.

    Writes each of *names* to its target (``None``: refused) and returns the
    written path per member, or ``None`` for one not delivered: missing from
    the archive, or with another size or CRC32 than the listing records (the
    archive changed since it was listed) or unreadable. zipfile checks the
    data's CRC32 with *verify*.
    """
    out: list[str | None] = [None] * len(names)
    try:
        zf = zipfile.ZipFile(archive)
    except Exception:
        return out
    with zf:
        by_name: dict[str, zipfile.ZipInfo] = {}
        for entry in zf.infolist():
            by_name.setdefault(_zip_name(entry), entry)
        for k, (name, target) in enumerate(zip(names, targets, strict=True)):
            info = by_name.get(name)
            if target is None or info is None:
                continue
            if (math.isfinite(sizes[k]) and sizes[k] != info.file_size) or (
                math.isfinite(crcs[k]) and crcs[k] != info.CRC
            ):
                continue
            try:
                with contextlib.suppress(OSError):
                    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                with zf.open(info) as src, atomic_write(target) as fh:
                    if not verify:
                        src._expected_crc = None  # type: ignore[attr-defined]
                    shutil.copyfileobj(src, fh)
            except Exception:  # noqa: S112 - the member is left to the range fetch
                continue
            out[k] = target
    return out


def _zip_fetch_whole(
    url: str,
    size: float,
    names: Sequence[str],
    targets: Sequence[str | None],
    sizes: Sequence[float],
    crcs: Sequence[float],
    max_bytes: float,
    archive_path: str | None = None,
    skip_on_api_limit: bool = False,
    verify: bool = True,
) -> list[str | None] | None:
    """Port of ``R/zip-peek.R::.zip_fetch_whole()``: download the zip once, extract members.

    Returns :func:`_zip_extract_members`'s paths, or ``None`` when the archive
    was not used: larger than *max_bytes* or of unknown *size*, or the
    download failed. The archive is kept at *archive_path* when given, so a
    restarted run reuses it instead of requesting it again (one that cannot be
    opened, a partial download, is fetched again); else it is a temporary file.
    """
    from metacheck.archives.download import _download_one

    if not size <= max_bytes:
        return None
    with contextlib.ExitStack() as stack:
        if archive_path is None:
            import tempfile

            archive_path = os.path.join(stack.enter_context(tempfile.TemporaryDirectory()), "a.zip")
        if not _zip_readable(archive_path):
            with contextlib.suppress(OSError):
                os.remove(archive_path)
            err = _download_one(
                url, archive_path, skip_on_api_limit=skip_on_api_limit, expected_bytes=size
            )
            if err is not None or not _zip_readable(archive_path):
                with contextlib.suppress(OSError):
                    os.remove(archive_path)
                return None
        return _zip_extract_members(archive_path, names, targets, sizes, crcs, verify)
    return None  # pragma: no cover - the with block returns


def _held_tail(url: str) -> tuple[float, float]:
    """``(start, total)`` of the archive's end held since its listing (start ``inf``: none)."""
    with _CACHE_LOCK:
        held = _ZIP_PEEK_CACHE.get((url, "tail"))
    if held is None:
        return math.inf, math.nan
    tail, total = held
    total = _num(total)
    if math.isnan(total):
        total = float(len(tail))
    return max(0.0, total - len(tail)), total


def _zip_fetch_members(
    url: str,
    names: Sequence[str | None] | str | None = None,
    dest: str = ".",
    verify: bool = True,
    cache: bool = False,
    skip_on_api_limit: bool = False,
    max_whole_bytes: Any = 0,
    archive_path: str | None = None,
    min_whole_members: int = 3,
    request_bytes: float = _REQUEST_BYTES,
    size: Any = None,
) -> pd.DataFrame | None:
    """Port of ``R/zip-peek.R::.zip_fetch_members()``: download selected zip members.

    Lists the archive with :func:`zip_peek` (given *cache*,
    *skip_on_api_limit* and *size*, the archive's size when the caller knows
    it) and fetches the members named in *names* (all when ``None``) into
    *dest*, following their paths inside the archive (a member that would
    land outside *dest*, e.g. ``../x``, is refused). A member already in
    *dest* with the listing's size and CRC32 is kept.

    Members come by range requests, one per member or per run of nearby
    members (:func:`_range_plan`); members in the end of the archive the
    listing already holds cost none. With *max_whole_bytes* above 0 the
    archive is downloaded once instead, and the members extracted from it,
    when at least *min_whole_members* members need fetching, the archive is
    no larger than *max_whole_bytes* and that is cheaper
    (:func:`_zip_whole_is_cheaper`, with *request_bytes*); it is kept at
    *archive_path* when given. A member the whole archive did not deliver is
    fetched by range. *url* may also be the path of a zip on disk, whose
    members are then extracted with no request.

    Returns ``name``, ``path`` (on disk, ``NA`` when not fetched), ``size``,
    ``ok`` and ``error`` (``NA`` when fetched, else why not) per wanted
    member; a 0-row ``name``/``size`` table when nothing is wanted; or
    ``None`` when the archive cannot be listed -- the signal to download it
    whole instead.
    """
    import pandas as pd

    try:
        cd = zip_peek(url, cache=cache, skip_on_api_limit=skip_on_api_limit, size=size)
    except Exception:
        cd = None
    if cd is None or len(cd) == 0:
        return None

    if names is None:
        want = cd.reset_index(drop=True)
    else:
        wanted = set(_chr_list(names))
        want = cd.loc[[n in wanted for n in cd["name"].tolist()]].reset_index(drop=True)
    if len(want) == 0:
        return want[["name", "size"]]

    n = len(want)
    member_names = want["name"].tolist()
    stored_len = _stored_name_bytes(want)
    sizes = [_num(v) for v in want["size"].tolist()]
    crcs = [_num(v) for v in want["crc"].tolist()]
    offsets = [_num(v) for v in want["offset"].tolist()]
    csizes = [_num(v) for v in want["csize"].tolist()]
    out_path: list[str | None] = [None] * n
    ok = [False] * n
    error: list[str | None] = [None] * n

    def delivered(i: int, path: str) -> None:
        out_path[i], ok[i], error[i] = path, True, None

    def write(i: int, data: bytes) -> None:
        target = targets[i]
        assert target is not None
        try:  # R: dir.create(showWarnings = FALSE) fails silently (a file is in the way)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        except OSError:
            pass
        try:
            with atomic_write(target) as fh:
                fh.write(data)
        except OSError:
            error[i] = "could not write the extracted member to disk"
            return
        delivered(i, target)

    targets: list[str | None] = []
    for i, name in enumerate(member_names):
        rel = _safe_member_path(name)
        target = None if rel is None else f"{dest}/{rel}"
        targets.append(target)
        if target is None:
            error[i] = "entry path escapes the archive (path traversal)"
        # Reuse only bytes matching the current listing's size and CRC.
        # A partial write or a caller's edit causes a fresh member fetch.
        elif _cached_member_ok(target, sizes[i], crcs[i], verify):
            delivered(i, target)
    todo = [i for i in range(n) if targets[i] is not None and not ok[i]]

    def table() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "name": pd.Series(member_names, dtype="string"),
                "path": pd.Series(out_path, dtype="string"),
                "size": pd.Series(want["size"].tolist(), dtype="float64"),
                "ok": pd.Series(ok, dtype="boolean"),
                "error": pd.Series(error, dtype="string"),
            }
        )

    def extracted(got: Sequence[str | None] | None, failure: str | None) -> None:
        for i, path in zip(todo, got or [None] * len(todo), strict=True):
            if path is not None:
                delivered(i, path)
            elif failure is not None:
                error[i] = failure

    def pick(values: Sequence[Any]) -> list[Any]:
        return [values[i] for i in todo]

    if _is_local_file(url):  # a zip on disk: extracted, no request
        extracted(
            _zip_extract_members(
                os.fspath(url), pick(member_names), pick(targets), pick(sizes), pick(crcs), verify
            ),
            "could not extract the member from the local archive",
        )
        return table()

    if todo and max_whole_bytes is not None and _num(max_whole_bytes) > 0:
        tail_start, held_total = _held_tail(url)
        need = [i for i in todo if not offsets[i] >= tail_start]  # the rest is in hand
        spans = [
            (i, offsets[i], offsets[i] + 30 + stored_len[i] + csizes[i] - 1)
            for i in need
            if math.isfinite(offsets[i]) and math.isfinite(csizes[i]) and csizes[i] > 0
        ]
        plan = _range_plan(spans, cd["offset"].tolist())
        whole = _known_size(size)
        if math.isnan(whole):
            whole = held_total if math.isfinite(held_total) else _zip_whole_size(cd)
        if _zip_whole_is_cheaper(
            len(need), whole, plan, max_whole_bytes, min_whole_members, request_bytes
        ):
            try:
                got = _zip_fetch_whole(
                    url,
                    whole,
                    pick(member_names),
                    pick(targets),
                    pick(sizes),
                    pick(crcs),
                    _num(max_whole_bytes),
                    archive_path=archive_path,
                    skip_on_api_limit=skip_on_api_limit,
                    verify=verify,
                )
            except Exception:
                got = None
            extracted(got, None)

    fetch: list[int] = []
    for i in todo:
        if ok[i]:
            continue
        if is_missing(offsets[i]) or is_missing(csizes[i]):
            error[i] = "Zip64 entry whose size/offset could not be read from the archive's listing"
        elif csizes[i] == 0:
            write(i, b"")  # an empty member has nothing to fetch
        else:
            fetch.append(i)
    if not fetch:
        return table()

    opened = _open_zip(url, skip_on_api_limit=skip_on_api_limit, size=size)
    if opened is None:
        for i in fetch:
            error[i] = "could not read the archive's central directory"
        return table()
    zf, rf = opened
    with zf:
        infos = zf.infolist()
        entries = {(_zip_name(info), info.header_offset): info for info in infos}
        found: dict[int, zipfile.ZipInfo] = {}
        for i in fetch:
            info = entries.get((member_names[i], int(offsets[i])))
            if info is None:
                error[i] = "the archive has no such entry (it changed since it was listed)"
            else:
                found[i] = info
        spans = [
            (
                i,
                info.header_offset,
                info.header_offset
                + 30
                + len(info.orig_filename.encode("utf-8", "replace"))
                + info.compress_size
                - 1,
            )
            for i, info in found.items()
        ]
        bounds = [info.header_offset for info in infos] + [zf.start_dir]
        for keys, start, end in _range_plan(spans, bounds):
            try:
                rf.prefetch(start, end + 1)
            except OSError as e:
                for i in keys:
                    error[i] = str(e) or "the range request failed"
                continue
            for i in keys:
                try:
                    data = _member_bytes(zf, found[i], verify)
                except Exception as e:
                    error[i] = _member_error(e, found[i])
                    continue
                write(i, data)
    return table()


def _cached_member_ok(path: str, size: float, crc: float, verify: bool) -> bool:
    if not math.isfinite(size) or (verify and not math.isfinite(crc)):
        return False
    try:
        if os.path.getsize(path) != size or os.path.islink(path):
            return False
        if not verify:
            return True
        checksum = 0
        with open(path, "rb") as fh:
            while chunk := fh.read(1024 * 1024):
                checksum = zlib.crc32(chunk, checksum)
        return checksum == int(crc)
    except OSError:
        return False


# -- archive-format classification --------------------------------------------

_TAR_RX = "[.](tar|tar[.]gz|tgz|tar[.]bz2|tbz2?|tar[.]xz|txz)$"


def _is_zip(name: Any) -> list[bool]:
    """Port of ``R/zip-peek.R::.is_zip()``: names ending in ``.zip``."""
    return _grepl("[.]zip$", _chr_list(name), ignore_case=True)


def _is_tar_archive(name: Any) -> list[bool]:
    """Port of ``R/zip-peek.R::.is_tar_archive()``: ``.tar``, ``.tar.gz``, ``.tgz``, ..."""
    return _grepl(_TAR_RX, _chr_list(name), ignore_case=True)


def _is_single_compress(name: Any) -> list[bool]:
    """Port of ``R/zip-peek.R::.is_single_compress()``: a bare ``.gz``/``.bz2``/``.xz``."""
    names = _chr_list(name)
    comp = _grepl("[.](gz|bz2|xz)$", names, ignore_case=True)
    return [bool(c) and not t for c, t in zip(comp, _is_tar_archive(names), strict=True)]


def _is_readable_archive(name: Any) -> list[bool]:
    """Port of ``R/zip-peek.R::.is_readable_archive()``: zip, tar or single-file compression."""
    names = _chr_list(name)
    return [
        a or b or c
        for a, b, c in zip(
            _is_zip(names), _is_tar_archive(names), _is_single_compress(names), strict=True
        )
    ]


# -- expanding downloaded archives ---------------------------------------------


def _list_files_all(root: str) -> list[str]:
    """``list.files(root, recursive = TRUE, all.files = TRUE)``, sorted as R sorts them.

    Like R, links to folders are followed (a link back into one of its own
    parent folders is not, where R would recurse until the path is too long).
    """
    from metacheck._r import r_sorted

    out: list[str] = []

    def walk(d: str, rel: str, parents: frozenset[str]) -> None:
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=True)
            except OSError:
                is_dir = False
            if not is_dir:
                out.append(rel + e.name)
                continue
            real = os.path.realpath(e.path)
            if real not in parents:
                walk(e.path, f"{rel}{e.name}/", parents | {real})

    walk(root, "", frozenset({os.path.realpath(root)}))
    return list(r_sorted(out))


def _file_ext(x: list[str]) -> list[str]:
    """``tools::file_ext()`` (a name with undecodable bytes has its extension too:
    R's ``file_ext()`` fails on it, U76)."""
    from metacheck._r import regextract

    found = regextract(r"\.([[:alnum:]]+)$", x)
    return ["" if m is None else m[1:] for m in found]


def _tolower(s: str) -> str:
    from metacheck.fileinfo.category import _tolower as tolower

    return tolower(s)


def _archive_rows(
    dest: str,
    archive_row: pd.DataFrame,
    label: str,
    skip_types: Any,
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.archive_rows()``: rows for the files of an extracted archive.

    Walks what is on disk under *dest*, classifies each file
    (``data_classify_files()``), and keeps data and code files and
    documentation with a ``readme``/``codebook`` role -- never types in
    *skip_types*. Each kept file gets a copy of *archive_row*'s first row with
    ``file_name``, ``file_path`` (``<label>/<inner path>``),
    ``file_location``, ``file_size`` and, where the columns exist,
    ``data_type``, ``doc_role``, ``data_format`` and ``file_url`` (``NA``).
    A 0-row frame when nothing worth keeping is inside.

    ``file_type`` is the file's own extension type when the extension has
    one (``NA`` otherwise); R keeps the archive's type, so a CSV from a zip
    was typed "archive" (UPSTREAM_ISSUES U213).
    """
    import pandas as pd

    from metacheck.datacheck.files import _data_doc_role, data_classify_files, data_format

    empty = archive_row.iloc[0:0].copy()
    if not os.path.isdir(dest):
        return empty
    rel = _list_files_all(dest)
    macosx = _grepl("(^|/)__MACOSX/", rel)
    rel = [r for r, m in zip(rel, macosx, strict=True) if not m]
    if not rel:
        return empty
    loc = [f"{dest}/{r}" for r in rel]
    base = [os.path.basename(p) for p in loc]
    types = data_classify_files(base)
    roles = _data_doc_role(base)
    fmt = data_format([_tolower(e) for e in _file_ext(loc)])
    keep = _archive_keep(rel, skip_types)
    if not any(keep):
        return empty

    def pick(values: list[Any]) -> list[Any]:
        return [v for v, k in zip(values, keep, strict=True) if k]

    loc, rel, base = pick(loc), pick(rel), pick(base)
    types, roles, fmt = pick(types), pick(roles), pick(fmt)

    rows = archive_row.iloc[[0] * len(loc)].reset_index(drop=True)
    sizes = []
    for p in loc:
        try:
            sizes.append(float(os.path.getsize(p)))
        except OSError:
            sizes.append(float("nan"))
    rows["file_name"] = pd.Series(base, dtype="string")
    rows["file_path"] = pd.Series([f"{label}/{r}" for r in rel], dtype="string")
    rows["file_location"] = pd.Series(loc, dtype="string")
    rows["file_size"] = pd.Series(sizes, dtype="float64")
    if "data_type" in rows.columns:
        rows["data_type"] = pd.Series(types, dtype="string")
    if "doc_role" in rows.columns:
        rows["doc_role"] = pd.Series(roles, dtype="string")
    if "data_format" in rows.columns:
        rows["data_format"] = pd.Series(fmt, dtype="string")
    if "file_url" in rows.columns:
        rows["file_url"] = pd.Series([None] * len(loc), dtype="string")
    if "file_type" in rows.columns:
        rows["file_type"] = pd.Series(_ext_type(base), dtype="string")
    return rows


def _ext_type(names: Sequence[str]) -> list[str | None]:
    """The ``file_types`` type of each name's extension, ``None`` for none or several."""
    from metacheck.fileinfo.types import ext_rows

    lookup = ext_rows()
    out: list[str | None] = []
    for name in names:
        ext = name.rsplit(".", 1)[1].lower() if "." in name else None
        types = lookup.get(ext, ()) if ext is not None else ()
        out.append(types[0][1] if len(types) == 1 else None)
    return out


def _missing_path(path: Any) -> bool:
    return path is None or is_missing(path) or not os.path.exists(str(path))


def _zip_raw_name(info: Any) -> bytes:
    """A member's name as the bytes stored in the archive, up to the first NUL (a C string)."""
    name: str = info.orig_filename
    raw = name.encode("utf-8" if info.flag_bits & 0x800 else "cp437")
    return raw.split(b"\x00", 1)[0]


def _zip_stored_name_len(info: Any) -> int:
    """The name's length as stored in the archive, with any NUL bytes it holds
    (``name_bytes``: the central directory's own length field)."""
    name: str = info.orig_filename
    return len(name.encode("utf-8" if info.flag_bits & 0x800 else "cp437"))


def _zip_names(zf: Any) -> list[str]:
    """Member names as text (see :func:`_decode_zip_name`)."""
    return [_zip_name(info) for info in zf.infolist()]


def _zip_name(info: Any) -> str:
    return _decode_zip_name(_zip_raw_name(info), bool(info.flag_bits & 0x800))


def _r_member_path(raw: bytes, windows: bool = os.name == "nt") -> bytes:
    """R's internal unzip drops ``../`` path components (with a warning).

    On Windows a ``"\\"`` in a name separates folders (``..\\x`` would climb out
    of the destination) and a drive (``C:/``) cannot be a folder name, so
    backslashes are read as ``"/"`` and a leading drive is dropped first.
    """
    if windows:
        raw = raw.replace(b"\\", b"/")
        if len(raw) >= 2 and raw[1:2] == b":" and raw[:1].isalpha():
            raw = raw[2:].lstrip(b"/")
    if raw.startswith(b"../") or b"/../" in raw:
        shown = raw.decode("utf-8", "replace")
        warnings.warn(f"skipped \"../\" path component(s) in '{shown}'", stacklevel=4)
        while raw.startswith(b"../"):
            raw = raw[3:]
        while b"/../" in raw:
            raw = raw.replace(b"/../", b"/", 1)
    return raw


def _unzip_all(zip_path: str, exdir: str, members: set[str] | None = None) -> None:
    """``utils::unzip(zip_path, exdir = exdir)``: extract a zip below *exdir*.

    Member names are used byte for byte (a backslash is part of the file name,
    ``C:/x`` makes a folder ``C:``, a leading ``/`` is harmless), except that
    ``../`` components are dropped. Parent folders are made as needed; a
    member that cannot be written (a file is in the way) is an error that
    ends the extraction. A member :mod:`zipfile` cannot read (a compression
    method it lacks, a damaged local header) is skipped with a warning, and
    the CRC of the data itself is not checked, as R's unzip does not check it.
    With *members*, only the members of those names (``unzip(files = ...)``).
    """
    ex = os.fsencode(exdir)
    os.makedirs(ex, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if members is not None and _zip_name(info) not in members:
                continue
            # a CP437 name is written as UTF-8, so the files on disk have
            # readable names (R writes the raw bytes: U76)
            name = _zip_name(info).encode("utf-8", "surrogateescape")
            out = ex + b"/" + _r_member_path(name)
            if out.endswith(b"/"):  # a directory entry
                if not os.path.exists(out):
                    try:
                        os.makedirs(out, exist_ok=True)
                    except OSError:
                        pass
                continue
            pp = len(ex) + 1
            while (k := out.find(b"/", pp)) >= 0:
                parent = out[:k]
                if not os.path.exists(parent):
                    try:
                        os.mkdir(parent)
                    except OSError:
                        pass
                pp = k + 1
            try:
                src = zf.open(info)
            except (NotImplementedError, RuntimeError, zipfile.BadZipFile):
                warnings.warn(f"zip file is corrupt: cannot read '{_zip_name(info)}'", stacklevel=3)
                continue
            try:
                fout = open(out, "wb")  # noqa: SIM115
            except OSError as e:
                src.close()
                raise RuntimeError(f"cannot open file '{os.fsdecode(out)}': {e.strerror}") from e
            with fout, src:
                src._expected_crc = None  # type: ignore[attr-defined]  # R ignores CRCs
                shutil.copyfileobj(src, fout)


def _in_download_folder(path: str) -> bool:
    """Port of ``R/zip-peek.R::.in_download_folder()``: is *path* a file metacheck downloaded?

    In the repository cache, or in this session's download folder when the
    cache is off (:func:`~metacheck.archives.download._repo_session_dir`).
    metacheck writes next to such a file (an unpacked archive, code recovered
    from an output file); any other file is the user's own, and nothing is
    written into its folder.
    """
    from metacheck.archives.download import _repo_cache_location, _session_tempdir
    from metacheck.utils import get_option

    session = get_option("metacheck.repo_cache.session_dir")
    if session is None or session == "":
        session = os.path.join(_session_tempdir(), "metacheck-repo-files")
    roots = [os.path.realpath(str(r)) for r in (session, _repo_cache_location())]
    norm = os.path.realpath(path)
    if os.name == "nt":
        roots = [r.lower() for r in roots]
        norm = norm.lower()
    return any(norm.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _file_md5(path: str) -> str:
    """``unname(tools::md5sum(path))``."""
    import hashlib

    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _archive_dest(path: str) -> str:
    """Port of ``R/zip-peek.R::.archive_dest()``: the folder an archive is extracted into.

    A downloaded archive (in the repository cache, or this session's download
    folder when the cache is off) is extracted beside itself, to
    ``<archive>.contents``, which a later run reuses. Any other archive is the
    user's own (``local_path``): extracting beside it wrote into the user's
    folder, and the next run listed the extracted files as files of their own
    and extracted the archive again, so every file counted twice (U214). It is
    extracted into the session's temporary folder instead, under the archive's
    checksum. A folder of the user's that is itself in the temporary folder
    (an upload unpacked there, a test) is the user's too.
    """
    from metacheck.archives.download import _session_tempdir

    if _in_download_folder(path):
        return f"{path}.contents"
    return os.path.join(_session_tempdir(), "metacheck-archives", _file_md5(os.path.realpath(path)))


def _archive_keep(name: Sequence[str | None], skip_types: Any) -> list[bool]:
    """Port of ``R/zip-peek.R::.archive_keep()``: which inner files of an archive are kept.

    Data and code, and documentation that is a codebook or README; never a type
    in *skip_types*, never a file that does not belong in a repository
    (:func:`~metacheck.archives._useless.useless_file_kind`). Names are
    classified by base name, all together (so a GeoTIFF sees the GIS files
    beside it). Applied to an archive's listing before unpacking (#472): a
    file that would be dropped right after is never written.
    """
    from metacheck.archives._useless import is_useless_file
    from metacheck.datacheck.files import _data_doc_role, data_classify_files

    if len(name) == 0:
        return []
    path = [gsub(r"\\", "/", n) if n is not None else None for n in name]
    base = [os.path.basename(str(p)) for p in path]
    types = data_classify_files(base)
    roles = _data_doc_role(base)
    skip = set(_chr_list(skip_types))
    useless = is_useless_file(path)
    return [
        (t in ("data", "code") or (t == "documentation" and r in ("codebook", "readme")))
        and t not in skip
        and not re.search("(^|/)__MACOSX/", str(p))
        and not u
        for t, r, p, u in zip(types, roles, path, useless, strict=True)
    ]


def _climbs(name: str) -> bool:
    """Does a member name have a ``..`` folder part (a backslash separates folders on Windows only)?"""
    return ".." in re.split(r"[/\\]" if os.name == "nt" else "/", name)


def _move_extracted(part: str, dest: str, members: Sequence[str]) -> None:
    """Port of ``R/zip-peek.R::.move_extracted()``: move extracted members from *part* into *dest*.

    Extracting straight into *dest* left a half-written file there when the
    run was stopped, which the next run reused as complete (U203); a member
    that was not extracted is simply not moved. *part* is removed.
    """
    for m in members:
        if _climbs(m):  # a name that climbs out of its folder is never moved
            continue
        src = f"{part}/{m}"  # (not os.path.join: an absolute name would replace *part*)
        if not os.path.isfile(src):
            continue
        to = f"{dest}/{m}"
        os.makedirs(os.path.dirname(to), exist_ok=True)
        os.replace(src, to)
    shutil.rmtree(part, ignore_errors=True)


def _with_oversize(
    rows: pd.DataFrame,
    archive_row: pd.DataFrame,
    label: str,
    name: Sequence[str],
    file_size: Sequence[float],
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.with_oversize()``: *rows* plus a row per inner file not unpacked.

    A file larger than the per-file limit (#472) has no local copy
    (``file_location`` ``NA``), the way a file too large to download is
    listed. The same files are attached as ``attrs["oversize"]`` (``file_name``,
    ``file_size`` in bytes, ``NaN`` when unknown), which ``data_check`` adds to
    its record of files over the limit.
    """
    import pandas as pd

    if len(name) == 0:
        return rows
    from metacheck.datacheck.files import _data_doc_role, data_classify_files, data_format

    n = len(name)
    extra = archive_row.iloc[[0] * n].reset_index(drop=True)
    base = [os.path.basename(x) for x in name]
    extra["file_name"] = pd.Series(base, dtype="string")
    extra["file_path"] = pd.Series([f"{label}/{x}" for x in name], dtype="string")
    extra["file_location"] = pd.Series([None] * n, dtype="string")
    extra["file_size"] = pd.Series([float(s) for s in file_size], dtype="float64")
    if "data_type" in extra.columns:
        extra["data_type"] = pd.Series(data_classify_files(base), dtype="string")
    if "doc_role" in extra.columns:
        extra["doc_role"] = pd.Series(_data_doc_role(base), dtype="string")
    if "data_format" in extra.columns:
        extra["data_format"] = pd.Series(
            data_format([_tolower(e) for e in _file_ext(list(name))]), dtype="string"
        )
    if "file_url" in extra.columns:
        extra["file_url"] = pd.Series([None] * n, dtype="string")
    if "file_type" in extra.columns:
        extra["file_type"] = pd.Series(_ext_type(base), dtype="string")
    out = pd.concat([rows, extra], ignore_index=True) if len(rows) else extra
    out.attrs["oversize"] = pd.DataFrame(
        {
            "file_name": pd.Series(base, dtype="string"),
            "file_size": pd.Series([float(s) for s in file_size], dtype="float64"),
        }
    )
    return out


def _expand_zip(
    zip_path: Any,
    zip_row: pd.DataFrame,
    skip_types: Any = "materials",
    max_bytes: float = math.inf,
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_zip()``: rows for the data files inside a zip.

    Extracts the zip once to :func:`_archive_dest` (``<zip>.contents/`` for a
    downloaded zip, reused when it exists) and returns :func:`_archive_rows`
    for it, inheriting *zip_row*'s columns; a 0-row frame when the zip is
    missing, unreadable or holds nothing worth keeping.

    Only the members that will be kept (:func:`_archive_keep`) and are no
    larger than *max_bytes* are unpacked (#472); the listing gives each
    member's size before anything is written. A member over the limit is
    reported in ``attrs["oversize"]``, like a file too large to download, and
    a copy left by an earlier run is removed, so the cache never holds more
    than the limits.
    """
    empty = zip_row.iloc[0:0].copy()
    if _missing_path(zip_path):
        return empty
    zip_path = str(zip_path)
    dest = _archive_dest(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
    except Exception:
        return empty
    entries = [(_zip_name(i), float(i.file_size)) for i in infos]
    entries = [(n, s) for n, s in entries if not n.endswith("/")]
    if not infos or not entries:
        return empty
    names = [n for n, _ in entries]
    sizes = [s for _, s in entries]
    keep = _archive_keep(names, skip_types)
    over = [k and s > max_bytes for k, s in zip(keep, sizes, strict=True)]
    for n, o in zip(names, over, strict=True):
        if o:
            _unlink(dest, n)
    want = [n for n, k, o in zip(names, keep, over, strict=True) if k and not o]
    missing = [n for n in want if not os.path.exists(f"{dest}/{n}")]
    if missing:
        part = f"{dest}.part"
        shutil.rmtree(part, ignore_errors=True)
        try:
            _unzip_all(zip_path, part, members=set(missing))
        except Exception:  # noqa: S110 - R: tryCatch(unzip(...), error = NULL)
            pass
        except BaseException:  # a stop: the half-written folder is not left in the cache
            shutil.rmtree(part, ignore_errors=True)
            raise
        os.makedirs(dest, exist_ok=True)
        _move_extracted(part, dest, missing)
    label = os.path.basename(zip_path)
    rows = _archive_rows(dest, zip_row, label, skip_types) if want else empty
    return _with_oversize(
        rows,
        zip_row,
        label,
        [n for n, o in zip(names, over, strict=True) if o],
        [s for s, o in zip(sizes, over, strict=True) if o],
    )


def _unlink(dest: str, name: str) -> None:
    """Remove the copy of an archive member in *dest* (never one that climbs out of it)."""
    if _climbs(name):
        return
    with contextlib.suppress(OSError):
        os.unlink(f"{dest}/{name}")


def _untar_all(tar_path: str, exdir: str, members: set[str] | None = None) -> None:
    """``utils::untar(tar_path, exdir = exdir)`` as metacheck runs it (GNU ``tar -xf``).

    Member by member, as GNU tar does: a member whose name has a ``..``
    component is skipped ("Member name contains '..'"), a leading ``/`` is
    removed, and a member that cannot be extracted does not stop the others.
    Unlike GNU tar, links that point outside *exdir* are not extracted
    (:mod:`tarfile`'s ``data`` filter): an archive from a repository must not
    write outside the cache. With *members*, only those members.
    """
    import tarfile

    os.makedirs(exdir, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf:
            if ".." in member.name.split("/"):
                continue
            if members is not None and member.name not in members:
                continue
            try:
                tf.extract(member, exdir, filter="data")
            except Exception:  # noqa: S112 - GNU tar reports the member and goes on
                continue


def _expand_tar(
    tar_path: Any,
    tar_row: pd.DataFrame,
    skip_types: Any = "materials",
    max_bytes: float = math.inf,
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_tar()``: rows for the data files inside a tarball.

    The mirror of :func:`_expand_zip` for ``.tar``/``.tar.gz``/``.tgz``/
    ``.tar.bz2``/``.tar.xz`` archives. Peeking is impossible for a tar (no
    tail index), so the caller must have downloaded it first. An unreadable
    tar gives a 0-row frame.

    As in :func:`_expand_zip`, only members that will be kept and are no
    larger than *max_bytes* are unpacked (#472). A plain ``.tar`` gives each
    member's size from its headers before anything is written; a compressed
    tar gives only names without reading it all, so its kept members are
    unpacked and any over the limit removed again straight away.
    """
    import tarfile

    from metacheck.archives.tar_peek import _tar_list_file

    empty = tar_row.iloc[0:0].copy()
    if _missing_path(tar_path):
        return empty
    tar_path = str(tar_path)
    dest = _archive_dest(tar_path)
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            members = [m for m in tf.getmembers() if not m.isdir()]
    except Exception:
        return empty
    if not members:
        return empty
    names = [m.name for m in members]
    plain = bool(re.search(r"[.]tar$", tar_path, re.IGNORECASE))
    listing = _tar_list_file(tar_path) if plain else None
    if listing is None:
        sizes = [math.nan] * len(members)
    else:
        # the sizes of a plain tar come from its headers (a link has none)
        by_member = dict(zip(listing["member"].tolist(), listing["size"].tolist(), strict=True))
        sizes = [float(by_member.get(n, math.nan)) for n in names]
    keep = _archive_keep(names, skip_types)
    over = [k and not math.isnan(s) and s > max_bytes for k, s in zip(keep, sizes, strict=True)]
    for n, o in zip(names, over, strict=True):
        if o:
            _unlink(dest, n)
    want = [n for n, k, o in zip(names, keep, over, strict=True) if k and not o]
    missing = [n for n in want if not os.path.exists(f"{dest}/{n}")]
    if missing:
        part = f"{dest}.part"
        shutil.rmtree(part, ignore_errors=True)
        try:
            _untar_all(tar_path, part, members=set(missing))
        except Exception:  # noqa: S110 - R: tryCatch(untar(...), error = NULL)
            pass
        except BaseException:  # a stop: the half-written folder is not left in the cache
            shutil.rmtree(part, ignore_errors=True)
            raise
        os.makedirs(dest, exist_ok=True)
        _move_extracted(part, dest, missing)
    # sizes only known once unpacked (compressed tar)
    for i, n in enumerate(names):
        got = f"{dest}/{n}"
        if n in want and os.path.isfile(got) and os.path.getsize(got) > max_bytes:
            sizes[i] = float(os.path.getsize(got))
            over[i] = True
            _unlink(dest, n)
    label = os.path.basename(tar_path)
    rows = (
        _archive_rows(dest, tar_row, label, skip_types)
        if any(k and not o for k, o in zip(keep, over, strict=True))
        else empty
    )
    return _with_oversize(
        rows,
        tar_row,
        label,
        [n for n, o in zip(names, over, strict=True) if o],
        [s for s, o in zip(sizes, over, strict=True) if o],
    )


def _open_compressed(path: str, ext: str) -> Any:
    """R's ``gzfile()``/``bzfile()``/``xzfile()`` for reading.

    ``gzfile()`` also reads bzip2, xz and uncompressed files transparently.
    """
    import bz2
    import gzip
    import lzma

    if ext == "bz2":
        return bz2.open(path, "rb")
    if ext == "xz":
        return lzma.open(path, "rb")
    with open(path, "rb") as fh:
        magic = fh.read(6)
    if magic[:2] == b"\x1f\x8b":
        return gzip.open(path, "rb")
    if magic[:3] == b"BZh":
        return bz2.open(path, "rb")
    if magic[:6] == b"\xfd7zXZ\x00":
        return lzma.open(path, "rb")
    return open(path, "rb")


def _expand_compressed(
    gz_path: Any,
    gz_row: pd.DataFrame,
    skip_types: Any = "materials",
    max_bytes: float = math.inf,
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_compressed()``: a single-file ``.gz``/``.bz2``/``.xz``.

    Decompresses the one file inside to ``<file>.contents/<name without the
    compression suffix>`` (``results.csv.gz`` -> ``results.csv``) and returns
    :func:`_archive_rows` for it; a 0-row frame when it cannot be read.

    Decompressed only when the inner file will be kept (:func:`_archive_keep`,
    by its name), and only up to *max_bytes* (#472): the download limits are
    checked on the compressed size, and a 74 MB ``go.txt.gz`` became a 1.2 GB
    copy. The uncompressed size is not stored in the file, so decompression
    stops once the limit is passed, the partial copy is deleted, and the file
    is reported in ``attrs["oversize"]`` (size ``NaN``, since only "more than
    the limit" is known). A copy over the limit left by an earlier run is
    removed.
    """
    empty = gz_row.iloc[0:0].copy()
    if _missing_path(gz_path):
        return empty
    gz_path = str(gz_path)
    ext = _tolower(_file_ext([gz_path])[0])
    if ext not in ("gz", "bz2", "xz"):
        return empty
    dest = _archive_dest(gz_path)
    inner_name = sub("[.](gz|bz2|xz)$", "", os.path.basename(gz_path), ignore_case=True)
    if not _archive_keep([inner_name], skip_types)[0]:
        return empty
    out = os.path.join(dest, inner_name)
    if os.path.exists(out) and os.path.getsize(out) > max_bytes:
        _unlink(dest, inner_name)
    label = os.path.basename(gz_path)
    if not os.path.exists(out):
        # written beside `dest` and moved in when complete: a decompression that
        # was stopped left a partial file, reused as complete (U203)
        part = f"{dest}.part"
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part, exist_ok=True)
        status = "ok"
        try:
            written = 0
            with (
                _open_compressed(gz_path, ext) as con,
                open(os.path.join(part, inner_name), "wb") as oc,
            ):
                while True:
                    chunk = con.read(1048576)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        status = "over"
                        break
                    oc.write(chunk)
        except Exception:
            status = "failed"
        except BaseException:  # a stop: the half-written folder is not left in the cache
            shutil.rmtree(part, ignore_errors=True)
            raise
        if status != "ok":
            shutil.rmtree(part, ignore_errors=True)
            if status == "over":
                return _with_oversize(empty, gz_row, label, [inner_name], [math.nan])
            return empty
        os.makedirs(dest, exist_ok=True)
        _move_extracted(part, dest, [inner_name])
    # the file is closed before its size is read (metacheck reads it before the
    # last buffer is flushed, so the size is rounded down to the block size,
    # 0 for a small file: U69)
    return _archive_rows(dest, gz_row, label, skip_types)


# -- zip_decision() ------------------------------------------------------------


def _table(values: list[str]) -> dict[str, int]:
    """R ``table()`` of a character vector: counts by value, names sorted as R sorts."""
    from metacheck._r import r_sorted

    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return {k: counts[k] for k in r_sorted(list(counts))}


def zip_decision(
    url: str,
    skip_types: Any = "materials",
    cache: bool = False,
    skip_on_api_limit: bool = False,
    size: Any = None,
) -> dict[str, Any]:
    """Decide whether a remote ZIP is worth downloading for a data archive.

    Port of ``R/zip-peek.R::zip_decision()``. Peeks inside the zip
    (:func:`zip_peek`, given *cache*, *skip_on_api_limit* and *size*, the
    zip's size when already known; *url* may be the path of a zip on disk) and classifies
    its entries: a zip is worth downloading if it holds actual data or a
    codebook, and not only types in *skip_types* (e.g. ``"materials"``).

    Returns a dict with ``worth`` (``True``/``False``, or ``None`` -- R
    ``NA`` -- when the peek failed so the caller can download to inspect),
    ``reason``, ``n_entries``, ``types`` (entry counts per type, R's
    ``table()``) and ``contents`` (the peeked listing, or ``None``).
    """
    from metacheck.datacheck.files import _data_doc_role, data_classify_files

    peek = zip_peek(url, cache=cache, skip_on_api_limit=skip_on_api_limit, size=size)
    if peek is None:
        return {
            "worth": None,
            "reason": "could not peek (download to inspect)",
            "n_entries": None,
            "types": None,
            "contents": None,
        }
    names = [
        os.path.basename(str(n).rstrip("/")) if n is not None else None
        for n in _chr_list(peek["name"])
    ]
    types = data_classify_files(names)
    roles = _data_doc_role(names)
    skip = set(_chr_list(skip_types))
    worth = any(
        t == "data" or (t == "documentation" and r == "codebook")
        for t, r in zip(types, roles, strict=True)
    ) and not all(t in skip for t in types)
    return {
        "worth": worth,
        "reason": "contains archivable content"
        if worth
        else "only skipped types inside (link, do not mirror)",
        "n_entries": len(peek),
        "types": _table(types),
        "contents": peek,
    }
