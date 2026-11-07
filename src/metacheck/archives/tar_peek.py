"""Listing a plain (uncompressed) ``.tar`` archive (port of ``R/tar-peek.R``, #487).

Unlike a zip, a tar has no index at its end (see :func:`~metacheck.archives.zip_peek.zip_peek`):
every member is preceded by its own 512-byte header holding its name and size,
so the names are spread through the whole archive. Reading them one header at a
time costs one request per member (134 requests for a 5 MB Zenodo archive of 133
files, after which Zenodo answered 429), so a tar is instead downloaded ONCE,
whole, when its size is known and at most ``max_bytes``, and its headers are read
from that copy. The copy is kept for the rest of the session, so taking members
out of it later (:func:`_tar_fetch_members`, ``download_repo_files()``) costs no
further request. A compressed tar (``.tar.gz``, ...) is not listed here: its
headers are compressed with everything else.

The headers are read by hand rather than with :mod:`tarfile`, as R does, so a
damaged archive gives ``None`` (the listing is trusted only when every header's
checksum holds) and the three long-name forms (POSIX ``ustar`` prefix, pax
``path``/``size`` records and GNU long-name entries) are read the same way.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from typing import TYPE_CHECKING, Any

from metacheck.archives._atomic import atomic_write
from metacheck.archives.zip_peek import _is_local_file, _num, _safe_member_path

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["_tar_fetch_members", "_tar_list_file", "_tar_peek"]

#: url -> listing (or ``None``), for this session
_TAR_PEEK_CACHE: dict[str, Any] = {}
#: url -> path of the archive's copy downloaded while listing it this session
_TAR_PEEK_COPIES: dict[str, str] = {}

#: The largest .tar that is downloaded to list it (50 MB).
_TAR_PEEK_MAX_BYTES = 50 * 1024**2

_BLOCK = 512


def _tar_str(r: bytes) -> str:
    """Text of a NUL-padded header field (UTF-8; bytes that are not stay as escapes)."""
    z = r.find(b"\0")
    if z >= 0:
        r = r[:z]
    return r.decode("utf-8", errors="surrogateescape")


def _tar_num(r: bytes) -> float:
    """A numeric header field: octal digits, or base-256 when the first byte's high bit is set.

    GNU tar writes sizes of 8 GB and more in the second form.
    """
    if r and r[0] & 0x80:
        v = bytearray(r)
        v[0] &= 0x7F
        return float(int.from_bytes(bytes(v), "big"))
    s = re.sub("[^0-7]", "", _tar_str(r))
    return float(int(s, 8)) if s else 0.0


def _tar_checksum_ok(h: bytes) -> bool:
    """Whether a 512-byte block is a valid tar header.

    Its checksum (the sum of its bytes, with the checksum field itself counted as
    spaces) must match the stored value. Old tar programs summed signed bytes, so
    that sum is accepted too.
    """
    stored = _tar_num(h[148:156])
    v = bytearray(h)
    v[148:156] = b"        "
    total = sum(v)
    signed = sum(b - 256 if b > 127 else b for b in v)
    return stored in (total, signed)


def _tar_pax(r: bytes) -> dict[str, str]:
    """The records of a pax extended header (``"<length> <key>=<value>\\n"`` ...)."""
    out: dict[str, str] = {}
    p, n = 0, len(r)
    while p < n:
        sp = r.find(b" ", p)
        if sp < 0:
            break
        digits = r[p:sp].decode("latin-1")
        if not re.fullmatch(r"\s*[+-]?[0-9]+\s*", digits):
            break
        length = int(digits)
        if length <= 0 or p + length > n:
            break
        rec = r[sp + 1 : p + length - 1]  # without the trailing newline
        eq = rec.find(b"=")
        if eq >= 0:
            out[rec[:eq].decode("latin-1")] = _tar_str(rec[eq + 1 :])
        p += length
    return out


def _tar_entries(path: str) -> list[tuple[str, float, int]] | None:
    """The regular files of a ``.tar`` on disk as ``(stored name, size, data offset)``.

    ``None`` when the file is not a tar (its first header fails the checksum) or a
    later header is damaged. See :func:`_tar_list_file`.
    """
    try:
        total = os.path.getsize(path)
    except OSError:
        return None
    if total < _BLOCK:
        return None

    out: list[tuple[str, float, int]] = []
    pos = 0
    pax: dict[str, str] = {}
    long_name: str | None = None
    with open(path, "rb") as fh:
        while pos + _BLOCK <= total:
            fh.seek(pos)
            h = fh.read(_BLOCK)
            if len(h) < _BLOCK or not any(h):
                break  # end of the archive
            if not _tar_checksum_ok(h):
                return None
            type_ = "0" if h[156] == 0 else chr(h[156])
            length = _tar_num(h[124:136])
            data_at = pos + _BLOCK
            data = fh.read(int(length)) if length > 0 and type_ in ("x", "L") else b""

            if type_ == "x":
                pax = _tar_pax(data)
            elif type_ == "L":
                long_name = _tar_str(data)
            elif type_ in ("0", "7"):
                nm = _tar_str(h[0:100])
                # POSIX ustar ("ustar\0" + "00") keeps the start of a long path in the
                # prefix field; GNU tar ("ustar  ") uses those bytes for other things.
                if h[257:263] == b"ustar\0":
                    prefix = _tar_str(h[345:500])
                    if prefix:
                        nm = f"{prefix}/{nm}"
                if long_name is not None:
                    nm = long_name
                if "path" in pax:
                    nm = pax["path"]
                try:
                    sz = float(pax["size"]) if "size" in pax else math.nan
                except ValueError:
                    sz = math.nan
                out.append((nm, length if math.isnan(sz) else sz, data_at))
                if not math.isnan(sz):
                    length = sz
                pax, long_name = {}, None
            elif type_ != "g":
                # a directory, link or device: the extended name was for it
                pax, long_name = {}, None
            pos += _BLOCK + math.ceil(length / _BLOCK) * _BLOCK
    return out


def _tar_list_file(path: str) -> pd.DataFrame | None:
    """Port of ``R/tar-peek.R::.tar_list_file()``: the regular files of a ``.tar`` on disk.

    Columns ``name`` (the path inside the archive, without a leading ``./``),
    ``size`` (bytes) and ``member`` (the name exactly as stored). Long names are
    read from the POSIX ``ustar`` prefix field, from a pax extended header
    (``path``, also ``size``) and from GNU tar's long-name entries; pax global
    headers, directories and links are skipped. ``None`` when the file is not a
    tar (its first header fails the checksum) or a later header is damaged.
    """
    import pandas as pd

    entries = _tar_entries(path)
    if entries is None:
        return None
    return pd.DataFrame(
        {
            "name": pd.Series([re.sub(r"^(\./)+", "", e[0]) for e in entries], dtype="string"),
            "size": pd.Series([e[1] for e in entries], dtype="float64"),
            "member": pd.Series([e[0] for e in entries], dtype="string"),
        }
    )


def _tar_peek(
    url: str,
    size: Any = None,
    max_bytes: float = _TAR_PEEK_MAX_BYTES,
    cache: bool = False,
    skip_on_api_limit: bool = False,
) -> pd.DataFrame | None:
    """Port of ``R/tar-peek.R::.tar_peek()``: the files inside a remote plain ``.tar``.

    Downloads the archive once, whole, when its size is known and at most
    *max_bytes*, and reads its file names and sizes from that copy (a tar has no
    index that a partial download could read). The copy is kept for the session,
    so members taken from it later need no further request. *url* may also be the
    path of a tar on disk, read directly. With *cache* the listing is also kept on
    disk for later sessions. ``None`` when the archive was not listed.
    """
    from metacheck.archives import download as dlm
    from metacheck.archives.zip_peek_cache import (
        _zip_peek_cache_get,
        _zip_peek_cache_has,
        _zip_peek_cache_put,
    )

    if url in _TAR_PEEK_CACHE:
        return _TAR_PEEK_CACHE[url]  # type: ignore[no-any-return]
    if _is_local_file(url):
        return _tar_list_file(url)

    # Keyed apart from zip_peek()'s entries for the same URL.
    key = f"tar:{url}"
    if cache is True and _zip_peek_cache_has(key):
        listing = _zip_peek_cache_get(key)
        _TAR_PEEK_CACHE[url] = listing
        return listing  # type: ignore[no-any-return]

    known = _num(size[0] if isinstance(size, list | tuple) and size else size)
    if not (known > 0 and known <= max_bytes):
        return None

    fd, copy = tempfile.mkstemp(suffix=".tar", dir=dlm._session_tempdir())
    os.close(fd)
    os.unlink(copy)
    err = dlm._download_one(url, copy, skip_on_api_limit=skip_on_api_limit, expected_bytes=known)
    # A short file is a broken transfer, not a short archive.
    complete = err is None and os.path.isfile(copy) and os.path.getsize(copy) == known
    listing = _tar_list_file(copy) if complete else None
    if listing is None:
        if os.path.exists(copy):
            os.unlink(copy)
    else:
        _TAR_PEEK_COPIES[url] = copy
    _TAR_PEEK_CACHE[url] = listing
    # A failed download may succeed later, so only what a complete download
    # showed is kept on disk.
    if cache is True and complete:
        _zip_peek_cache_put(key, listing)
    return listing


def _tar_fetch_members(
    url: str,
    names: Any,
    dest: str,
    archive_path: str | None = None,
    size: Any = None,
    max_whole_bytes: float = math.inf,
    skip_on_api_limit: bool = False,
) -> pd.DataFrame:
    """Port of ``R/tar-peek.R::.tar_fetch_members()``: take members out of a plain ``.tar``.

    *names* are the members as stored (the ``member`` column of :func:`_tar_peek`).
    The return value is :func:`~metacheck.archives.zip_peek._zip_fetch_members`'s: a
    table of ``name``, ``path``, ``size``, ``ok`` and ``error``. The archive is read
    from, in order: *url* itself when it is a path on disk; the copy
    :func:`_tar_peek` downloaded this session; *archive_path* when an earlier run
    left a readable copy there; or one new download of the whole archive to
    *archive_path* (a temporary file when ``None``), when its size is at most
    *max_whole_bytes*.
    """
    import pandas as pd

    from metacheck.archives import download as dlm

    wanted = [names] if isinstance(names, str) else [str(n) for n in names]

    def fail(msg: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "name": pd.Series(wanted, dtype="string"),
                "path": pd.Series([None] * len(wanted), dtype="string"),
                "size": pd.Series([math.nan] * len(wanted), dtype="float64"),
                "ok": pd.Series([False] * len(wanted), dtype="boolean"),
                "error": pd.Series([msg] * len(wanted), dtype="string"),
            }
        )

    copy = _TAR_PEEK_COPIES.get(url)
    own_copy: str | None = None
    src: str | None
    if _is_local_file(url):
        src = url
    elif copy is not None and os.path.exists(copy):
        src = copy
    elif (
        archive_path is not None
        and os.path.exists(archive_path)
        and _tar_list_file(archive_path) is not None
    ):
        src = archive_path
    else:
        src = None

    try:
        if src is None:
            known = _num(size[0] if isinstance(size, list | tuple) and size else size)
            if known > max_whole_bytes:
                return fail("the archive is larger than the download budget")
            if archive_path is None:
                fd, archive_path = tempfile.mkstemp(suffix=".tar", dir=dlm._session_tempdir())
                os.close(fd)
                own_copy = archive_path
            if os.path.exists(archive_path):
                os.unlink(archive_path)
            err = dlm._download_one(
                url, archive_path, skip_on_api_limit=skip_on_api_limit, expected_bytes=known
            )
            if err is not None:
                return fail(err)
            src = archive_path

        listing = _tar_list_file(src)
        if listing is None:
            return fail("could not read the archive")
        wanted_set = set(wanted)
        rows = [
            (nm, sz, mem)
            for nm, sz, mem in zip(
                listing["name"].tolist(),
                listing["size"].tolist(),
                listing["member"].tolist(),
                strict=True,
            )
            if mem in wanted_set
        ]
        if not rows:
            return fail("not found in the archive")

        out_path: list[str | None] = []
        out_err: list[str | None] = []
        by_member = _read_members(src, {mem for _, _, mem in rows})
        for nm, sz, mem in rows:
            rel = _safe_member_path(nm)
            if rel is None:
                out_path.append(None)
                out_err.append("entry path escapes the archive (path traversal)")
                continue
            target = f"{dest}/{rel}"
            info = by_member.get(mem)
            if info is None or info[0] != sz:
                out_path.append(None)
                out_err.append("could not extract the member from the archive")
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with atomic_write(target) as out, open(src, "rb") as fh:
                    fh.seek(info[1])
                    view = _Limited(fh, int(sz))
                    shutil.copyfileobj(view, out)
                    if view.left > 0:  # the archive ends before the member does
                        raise OSError("short member")
            except OSError:
                out_path.append(None)
                out_err.append("could not extract the member from the archive")
                continue
            out_path.append(target)
            out_err.append(None)
        return pd.DataFrame(
            {
                "name": pd.Series([mem for _, _, mem in rows], dtype="string"),
                "path": pd.Series(out_path, dtype="string"),
                "size": pd.Series([sz for _, sz, _ in rows], dtype="float64"),
                "ok": pd.Series([p is not None for p in out_path], dtype="boolean"),
                "error": pd.Series(out_err, dtype="string"),
            }
        )
    finally:
        if own_copy is not None and os.path.exists(own_copy):
            os.unlink(own_copy)


class _Limited:
    """A read-only view of the next *n* bytes of a file object (for ``copyfileobj``)."""

    def __init__(self, fh: Any, n: int) -> None:
        self._fh = fh
        self.left = n

    def read(self, size: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        want = self.left if size < 0 else min(size, self.left)
        data: bytes = self._fh.read(want)
        self.left -= len(data)
        return data


def _read_members(path: str, members: set[str]) -> dict[str, tuple[float, int]]:
    """``member -> (size, data offset)`` for the wanted members of a tar.

    A member named twice keeps its last entry, as a tar extraction does.
    """
    return {nm: (sz, at) for nm, sz, at in _tar_entries(path) or [] if nm in members}
