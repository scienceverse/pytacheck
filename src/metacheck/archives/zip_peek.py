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

import atexit
import contextlib
import io
import math
import os
import re
import shutil
import threading
import warnings
import zipfile
from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from metacheck._r import grepl, gsub, strsplit, sub
from metacheck._values import is_missing
from metacheck.archives._atomic import atomic_write, staged_dir

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
    from metacheck.stats._rmath import as_numeric

    value = resp.headers.get("content-length")
    if value is None:
        return None  # type: ignore[return-value]
    return as_numeric(value)[0]


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


def _http_range_tail(
    url: str, n: float, total: Any = None, skip_on_api_limit: bool = False
) -> _RangeBody | None:
    """Port of ``R/zip-peek.R::.http_range_tail()``: the last *n* bytes of *url*.

    *total* is the file's size when known, ``None`` to ask with a HEAD
    request first, or ``nan`` (R ``NA``) when HEAD gave none: the tail is then
    asked for by its distance from the end (:func:`_http_range_suffix`).
    Returns the bytes -- possibly fewer than *n* for a small file -- with the
    file's size as ``.total``, or ``None`` on failure. A 200 (range ignored)
    to a request by position still yields the tail of the whole body.
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

        def read(r: Any) -> None:
            # a 206 body is the tail; a 200 (range ignored) is the whole file, of which
            # only the last *keep* bytes are held at a time
            got.clear()
            if r.status_code not in (200, 206):
                return
            for chunk in r.iter_bytes():
                got.extend(chunk)
                if r.status_code == 200 and len(got) > keep:
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
        if r.status_code in (200, 206):
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
    counts: a 200 (range ignored, the whole archive) is a failure here. Any
    status but 200/206 is retried. On failure *reason*, when given, gets a
    one-line explanation under ``"msg"``.
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
        """Hold the bytes ``[start, end)`` of the file (up to its end), fetching them if needed."""
        end = min(end, self.size, self._tail_start)  # the tail holds the rest
        if start >= end or (self._start <= start and end <= self._start + len(self._buf)):
            return
        reason: dict[str, str] = {}
        data = _http_range_bytes(self.url, start, end - 1, self._skip, reason)
        if data is None:
            raise OSError(reason.get("msg", "the range request failed"))
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
    url: str, tail_bytes: float = 131072, skip_on_api_limit: bool = False
) -> tuple[zipfile.ZipFile, _RangeFile] | None:
    """The remote zip at *url* as a :class:`zipfile.ZipFile`, or ``None`` if it cannot be read.

    A ``HEAD`` request gives the size (when the host refuses it, the tail is
    asked for by its distance from the end and its ``Content-Range`` gives
    the size), then a range request fetches the tail (*tail_bytes*); zipfile
    reads the central directory from it, and asks for more only when the
    directory is longer. Zip64, a comment after the directory, bzip2 and lzma
    members are zipfile's.
    """
    memo = (url, "tail")  # in the session cache, so that clearing it clears this too
    with _CACHE_LOCK:
        held = _ZIP_PEEK_CACHE.get(memo)
    if held is not None:  # members after the listing: no request to open the archive again
        with contextlib.suppress(Exception):
            return _wrap_zip(url, held[0], held[1], skip_on_api_limit)
    total: Any = _head_size(url, skip_on_api_limit)
    # A host that ignores ranges sends the whole file for every request: the
    # tail of it is then all this can hold, so a larger one is tried once (1 MB).
    for nb in dict.fromkeys([float(tail_bytes), _WHOLE_BODY_TAIL]):
        raw = _http_range_tail(url, nb, total=total, skip_on_api_limit=skip_on_api_limit)
        if raw is None:
            return None
        if is_missing(total):
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
        }
    )


def zip_peek(
    url: str,
    tail_bytes: float = 131072,
    cache: bool = False,
    skip_on_api_limit: bool = False,
) -> pd.DataFrame | None:
    """Peek at the contents of a remote ZIP file without downloading it.

    Port of ``R/zip-peek.R::zip_peek()``. A ``HEAD`` request gives the size
    (when the host refuses HEAD, the tail is asked for by its distance from
    the end and its ``Content-Range`` gives the size), then a range request
    fetches the tail (*tail_bytes*) and :mod:`zipfile` reads the central
    directory from it, asking for the rest of a larger one (see
    :func:`_open_zip`).

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

    with _CACHE_LOCK:
        if url in _ZIP_PEEK_CACHE:
            return _ZIP_PEEK_CACHE[url]  # type: ignore[no-any-return]

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
        opened = _open_zip(url, tail_bytes, skip_on_api_limit)
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


def _member_bytes(
    zf: zipfile.ZipFile, rf: _RangeFile, info: zipfile.ZipInfo, verify: bool
) -> bytes:
    """One member's bytes: its local header and data come in one range request.

    zipfile inflates the data and, with *verify*, checks the CRC32 stored in
    the archive; the size must be the archive's. Raises what :func:`_member_error` words.
    """
    rf.prefetch(
        info.header_offset,
        # the local header (30 bytes, the name, an extra field of its own) and the data
        info.header_offset
        + 30
        + len(info.orig_filename.encode("utf-8", "replace"))
        + 512
        + info.compress_size,
    )
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
    return str(rel)


def _zip_fetch_members(
    url: str,
    names: Sequence[str | None] | str | None = None,
    dest: str = ".",
    verify: bool = True,
    cache: bool = False,
    skip_on_api_limit: bool = False,
) -> pd.DataFrame | None:
    """Port of ``R/zip-peek.R::.zip_fetch_members()``: download selected zip members.

    Lists the archive with :func:`zip_peek` (given *cache* and
    *skip_on_api_limit*) and fetches the members named in *names* (all when
    ``None``) into *dest*, following their paths inside the archive (a member
    that would land outside *dest*, e.g. ``../x``, is refused). Returns
    ``name``, ``path`` (on disk, ``NA`` when not fetched), ``size``, ``ok``
    and ``error`` (``NA`` when fetched, else why not) per wanted member; a
    0-row ``name``/``size`` table when nothing is wanted; or ``None`` when
    the archive cannot be listed -- the signal to download it whole instead.
    """
    import pandas as pd

    try:
        cd = zip_peek(url, cache=cache, skip_on_api_limit=skip_on_api_limit)
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
    out_path: list[str | None] = [None] * n
    ok = [False] * n
    error: list[str | None] = [None] * n
    member_names = want["name"].tolist()
    opened: tuple[zipfile.ZipFile, _RangeFile] | None = None
    entries: dict[tuple[str, int], zipfile.ZipInfo] = {}
    tried = False  # the archive is opened once, when the first member needs it
    try:
        for i in range(n):
            row = want.iloc[i]
            offset, csize = _num(row.get("offset")), _num(row.get("csize"))
            if is_missing(offset) or is_missing(csize):
                error[i] = "Zip64 entry (size/offset not stored in 32 bits)"
                continue
            rel = _safe_member_path(member_names[i])
            if rel is None:
                error[i] = "entry path escapes the archive (path traversal)"
                continue
            data = b""  # an empty member has nothing to fetch
            if csize != 0:
                if not tried:
                    tried = True
                    opened = _open_zip(url, skip_on_api_limit=skip_on_api_limit)
                    if opened is not None:
                        entries = {
                            (_zip_name(info), info.header_offset): info
                            for info in opened[0].infolist()
                        }
                if opened is None:
                    error[i] = "could not read the archive's central directory"
                    continue
                info = entries.get((member_names[i], int(offset)))
                if info is None:
                    error[i] = "the archive has no such entry (it changed since it was listed)"
                    continue
                try:
                    data = _member_bytes(opened[0], opened[1], info, verify)
                except Exception as e:
                    error[i] = _member_error(e, info)
                    continue
            target = f"{dest}/{rel}"
            try:  # R: dir.create(showWarnings = FALSE) fails silently (a file is in the way)
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            except OSError:
                pass
            try:
                with atomic_write(target) as fh:
                    fh.write(data)
            except OSError:
                error[i] = "could not write the extracted member to disk"
                continue
            out_path[i] = target
            ok[i] = True
    finally:
        if opened is not None:
            opened[0].close()
    return pd.DataFrame(
        {
            "name": pd.Series(member_names, dtype="string"),
            "path": pd.Series(out_path, dtype="string"),
            "size": pd.Series(want["size"].tolist(), dtype="float64"),
            "ok": pd.Series(ok, dtype="boolean"),
            "error": pd.Series(error, dtype="string"),
        }
    )


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
    skip = set(_chr_list(skip_types))
    keep = [
        (t in ("data", "code") or (t == "documentation" and r in ("codebook", "readme")))
        and t not in skip
        for t, r in zip(types, roles, strict=True)
    ]
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


def _unzip_all(zip_path: str, exdir: str) -> None:
    """``utils::unzip(zip_path, exdir = exdir)``: extract a zip below *exdir*.

    Member names are used byte for byte (a backslash is part of the file name,
    ``C:/x`` makes a folder ``C:``, a leading ``/`` is harmless), except that
    ``../`` components are dropped. Parent folders are made as needed; a
    member that cannot be written (a file is in the way) is an error that
    ends the extraction. A member :mod:`zipfile` cannot read (a compression
    method it lacks, a damaged local header) is skipped with a warning, and
    the CRC of the data itself is not checked, as R's unzip does not check it.
    """
    import shutil

    ex = os.fsencode(exdir)
    os.makedirs(ex, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
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


_LOCAL_CONTENTS: dict[tuple[str, int, int], str] = {}
_local_contents_lock = threading.Lock()


def _local_contents_root() -> str:
    """``metacheck-archives`` in the temporary folder, where local archives are extracted.

    The same folder in every session, so the extracted files' locations do not
    change from run to run. When it belongs to someone else (a shared ``/tmp``)
    or is open to other users, a private folder of this session is used instead,
    removed when the session ends. On Windows the temporary folder is the
    user's own, and its permissions are not POSIX modes, so only its being a
    folder is checked.
    """
    import stat
    import tempfile

    root = os.path.join(tempfile.gettempdir(), "metacheck-archives")
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        st = os.lstat(root)
        if os.name == "nt":
            private = True
        else:
            private = st.st_uid == os.getuid() and not st.st_mode & 0o022
        if stat.S_ISDIR(st.st_mode) and private:
            return root
    except OSError:
        pass
    root = tempfile.mkdtemp(prefix="metacheck-archives-")
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root


def _file_sha1(path: str) -> str:
    import hashlib

    h = hashlib.sha1(usedforsecurity=False)
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _contents_dir(archive_path: str) -> str:
    """Where an on-disk archive is extracted (R: ``<archive>.contents/`` beside it).

    Beside it for an archive metacheck downloaded (in the repository cache or
    the temporary folder), as R does. Any other archive -- one in a user's
    ``local_path`` -- is extracted to :func:`_local_contents_root`, in a
    folder named by the archive's SHA-1 (an edited archive is extracted
    afresh): R writes into the user's folder, and the next run then lists the
    extracted files as well as the archive's members, so every member is
    reported twice (U214).
    """
    import tempfile

    from metacheck.archives.download import _repo_cache_location

    real = os.path.realpath(archive_path)
    for root in (_repo_cache_location(), tempfile.gettempdir()):  # neither is created here
        base = os.path.realpath(root)
        if real.startswith(base.rstrip(os.sep) + os.sep):
            return f"{archive_path}.contents"
    st = os.stat(real)
    key = (real, st.st_size, st.st_mtime_ns)
    with _local_contents_lock:
        folder = _LOCAL_CONTENTS.get(key)
        if folder is None:
            folder = os.path.join(_local_contents_root(), _file_sha1(real))
            _LOCAL_CONTENTS[key] = folder
    return os.path.join(folder, f"{os.path.basename(real)}.contents")


def _expand_zip(
    zip_path: Any, zip_row: pd.DataFrame, skip_types: Any = "materials"
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_zip()``: rows for the data files inside a zip.

    Extracts the zip once to ``<zip>.contents/`` (reused when it exists; see
    :func:`_contents_dir`) and returns :func:`_archive_rows` for it, inheriting
    *zip_row*'s columns; a 0-row frame when the zip is missing, unreadable or holds
    nothing worth keeping.
    """
    import zipfile

    empty = zip_row.iloc[0:0].copy()
    if _missing_path(zip_path):
        return empty
    zip_path = str(zip_path)
    dest = _contents_dir(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            n_entries = len(zf.infolist())
    except Exception:
        return empty
    if n_entries == 0:
        return empty
    if not os.path.isdir(dest):
        try:
            with staged_dir(dest) as stage:  # dest appears only once the extraction ended
                _unzip_all(zip_path, stage)
        except Exception:  # noqa: S110 - R: tryCatch(unzip(...), error = NULL)
            pass
    return _archive_rows(dest, zip_row, os.path.basename(zip_path), skip_types)


def _untar_all(tar_path: str, exdir: str) -> None:
    """``utils::untar(tar_path, exdir = exdir)`` as metacheck runs it (GNU ``tar -xf``).

    Member by member, as GNU tar does: a member whose name has a ``..``
    component is skipped ("Member name contains '..'"), a leading ``/`` is
    removed, and a member that cannot be extracted does not stop the others.
    Unlike GNU tar, links that point outside *exdir* are not extracted
    (:mod:`tarfile`'s ``data`` filter): an archive from a repository must not
    write outside the cache.
    """
    import tarfile

    os.makedirs(exdir, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf:
            if ".." in member.name.split("/"):
                continue
            try:
                tf.extract(member, exdir, filter="data")
            except Exception:  # noqa: S112 - GNU tar reports the member and goes on
                continue


def _expand_tar(
    tar_path: Any, tar_row: pd.DataFrame, skip_types: Any = "materials"
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_tar()``: rows for the data files inside a tarball.

    The mirror of :func:`_expand_zip` for ``.tar``/``.tar.gz``/``.tgz``/
    ``.tar.bz2``/``.tar.xz`` archives (extracted to ``<tar>.contents/``). An
    unreadable tar gives a 0-row frame.
    """
    import tarfile

    empty = tar_row.iloc[0:0].copy()
    if _missing_path(tar_path):
        return empty
    tar_path = str(tar_path)
    dest = _contents_dir(tar_path)
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            n_entries = len(tf.getnames())
    except Exception:
        return empty
    if n_entries == 0:
        return empty
    if not os.path.isdir(dest):
        try:
            with staged_dir(dest) as stage:  # dest appears only once the extraction ended
                _untar_all(tar_path, stage)
        except Exception:  # noqa: S110 - R: tryCatch(untar(...), error = NULL)
            pass
    return _archive_rows(dest, tar_row, os.path.basename(tar_path), skip_types)


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
    gz_path: Any, gz_row: pd.DataFrame, skip_types: Any = "materials"
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_compressed()``: a single-file ``.gz``/``.bz2``/``.xz``.

    Decompresses the one file inside to ``<file>.contents/<name without the
    compression suffix>`` (``results.csv.gz`` -> ``results.csv``) and returns
    :func:`_archive_rows` for it; a 0-row frame when it cannot be read.
    """
    empty = gz_row.iloc[0:0].copy()
    if _missing_path(gz_path):
        return empty
    gz_path = str(gz_path)
    ext = _tolower(_file_ext([gz_path])[0])
    if ext not in ("gz", "bz2", "xz"):
        return empty
    dest = _contents_dir(gz_path)
    inner_name = sub("[.](gz|bz2|xz)$", "", os.path.basename(gz_path), ignore_case=True)
    out = f"{dest}/{inner_name}"
    if not os.path.exists(out):
        try:
            os.makedirs(dest, exist_ok=True)
            with _open_compressed(gz_path, ext) as con, atomic_write(out) as oc:
                while True:
                    chunk = con.read(1048576)
                    if not chunk:
                        break
                    oc.write(chunk)
        except Exception:
            return empty
    # the file is closed before its size is read (metacheck reads it before the
    # last buffer is flushed, so the size is rounded down to the block size,
    # 0 for a small file: U69)
    return _archive_rows(dest, gz_row, os.path.basename(gz_path), skip_types)


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
) -> dict[str, Any]:
    """Decide whether a remote ZIP is worth downloading for a data archive.

    Port of ``R/zip-peek.R::zip_decision()``. Peeks inside the zip
    (:func:`zip_peek`, given *cache* and *skip_on_api_limit*) and classifies
    its entries: a zip is worth downloading if it holds actual data or a
    codebook, and not only types in *skip_types* (e.g. ``"materials"``).

    Returns a dict with ``worth`` (``True``/``False``, or ``None`` -- R
    ``NA`` -- when the peek failed so the caller can download to inspect),
    ``reason``, ``n_entries``, ``types`` (entry counts per type, R's
    ``table()``) and ``contents`` (the peeked listing, or ``None``).
    """
    from metacheck.datacheck.files import _data_doc_role, data_classify_files

    peek = zip_peek(url, cache=cache, skip_on_api_limit=skip_on_api_limit)
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
