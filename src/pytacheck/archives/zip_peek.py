"""Peek inside remote ZIP archives without downloading them (port of ``R/zip-peek.R``).

A ZIP stores its file listing (the "central directory") at the end of the
archive, so an HTTP range request for the tail is enough to read every entry's
name and uncompressed size (:func:`zip_peek`). That lets the downloader decide
whether an archive is worth fetching (:func:`zip_decision`) and fetch single
members by byte range (:func:`_zip_fetch_members`) -- each member of a zip is
compressed on its own, which is what makes this possible for ``.zip`` and not
for ``.7z`` or ``.tar.gz``.

Also here: the archive-format classification (:func:`_is_zip`,
:func:`_is_tar_archive`, :func:`_is_single_compress`,
:func:`_is_readable_archive`) and the expansion of downloaded archives into
``data_check`` rows (:func:`_expand_zip`, :func:`_expand_tar`,
:func:`_expand_compressed`).

All requests go through the storage retry policy and host authentication of
:mod:`pytacheck.archives.download` (``.auth_for_url()``,
``.storage_is_transient_factory()`` ...).
"""

from __future__ import annotations

import os
import threading
import warnings
import zlib
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from pytacheck._r import grepl, gsub, is_na, strsplit, sub
from pytacheck.archives._atomic import atomic_write, staged_dir

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["zip_decision", "zip_peek"]

#: R: .zip_peek_cache -- same-session cache of zip_peek() results (a success
#: or a failure, ``None``), keyed by URL. Never persisted.
_ZIP_PEEK_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()

_EOCD_SIG = b"PK\x05\x06"  # End-Of-Central-Directory record
_CD_SIG = b"PK\x01\x02"  # central-directory file header
_LOCAL_SIG = b"PK\x03\x04"  # local file header
_ZIP64 = 4294967295.0  # 0xFFFFFFFF: the Zip64 sentinel in a 4-byte field

_COLUMNS = ("name", "size", "method", "csize", "offset", "crc")


# -- helpers -------------------------------------------------------------------


def _is_missing(x: Any) -> bool:
    return x is None or is_na(x)


def _num(x: Any) -> float:
    """A number as R double (``NA`` -> ``nan``)."""
    if _is_missing(x):
        return float("nan")
    return float(x)


def _as_bytes(raw: Any) -> bytes:
    if raw is None:
        return b""
    if isinstance(raw, bytes | bytearray | memoryview):
        return bytes(raw)
    return bytes(int(b) for b in raw)


def _chr_list(x: Any) -> list[str | None]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, Iterable):
        return [None if _is_missing(v) else str(v) for v in x]
    return [None if _is_missing(x) else str(x)]


def _grepl(pattern: str, x: Sequence[str | None], ignore_case: bool = False) -> list[bool]:
    """``grepl()`` (``FALSE`` for ``NA``)."""
    return [bool(h) for h in grepl(pattern, x, ignore_case=ignore_case)]


def _entry_frame(rows: dict[str, list[Any]]) -> pd.DataFrame:
    import pandas as pd

    return pd.DataFrame(
        {
            "name": pd.Series(rows["name"], dtype="string"),
            **{
                col: pd.Series(rows[col], dtype="float64")
                for col in ("size", "method", "csize", "offset", "crc")
            },
        }
    )


# -- binary parsing ------------------------------------------------------------


def _le_int(raw: Any, at: int, n: int) -> float:
    """Port of ``R/zip-peek.R::.le_int()``: little-endian integer at 1-based *at*.

    As in R, bytes past the end of *raw* read as ``00``.
    """
    data = _as_bytes(raw)
    start = int(at) - 1
    chunk = bytes(data[i] if 0 <= i < len(data) else 0 for i in range(start, start + int(n)))
    return float(int.from_bytes(chunk, "little"))


def _raw_to_char(b: bytes, utf8_flag: bool = True) -> str:
    """``rawToChar()`` of a member name, decoded by :func:`_decode_zip_name`.

    Trailing nuls are dropped and an embedded nul is an error, as in R.
    """
    b = b.rstrip(b"\x00")
    if b"\x00" in b:
        shown = "".join(
            "\\0" if c == 0 else (f"\\{c:03o}" if c < 32 or c == 127 else chr(c)) for c in b
        )
        raise ValueError(f"embedded nul in string: '{shown}'")
    return _decode_zip_name(b, utf8_flag)


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


def _parse_zip_central_dir(raw: Any) -> pd.DataFrame | None:
    """Port of ``R/zip-peek.R::.parse_zip_central_dir()``.

    Parses the central directory of a zip from a tail of its bytes that ends
    at the true end of the file. Returns one row per entry -- ``name``,
    ``size`` (uncompressed bytes), ``method`` (0 stored, 8 deflate),
    ``csize`` (compressed bytes), ``offset`` (where the member's local header
    starts) and ``crc`` -- or ``None`` when the End-Of-Central-Directory
    record, or the whole central directory, is not in the tail. Zip64
    sentinels (``0xFFFFFFFF``) in ``size``/``csize``/``offset`` become ``NA``.
    """
    data = _as_bytes(raw)
    n = len(data)
    if n < 22:
        return None

    # Find the EOCD signature, scanning from the end (a comment may follow it).
    eocd: int | None = None
    for i in range(n - 21, 0, -1):
        if data[i - 1 : i + 3] == _EOCD_SIG:
            eocd = i
            break
        if i < n - 21 - 65536:  # a comment can't exceed 64KB; stop early
            break
    if eocd is None:
        return None

    n_entries = int(_le_int(data, eocd + 10, 2))
    cd_size = int(_le_int(data, eocd + 12, 4))
    cd_start = eocd - cd_size
    if cd_start < 1:
        return None  # central directory not fully in the tail

    rows: dict[str, list[Any]] = {col: [] for col in _COLUMNS}
    p = cd_start
    for _ in range(n_entries):
        if p + 46 > n:
            break
        if data[p - 1 : p + 3] != _CD_SIG:
            break
        flags = int(_le_int(data, p + 8, 2))
        method = _le_int(data, p + 10, 2)
        crc = _le_int(data, p + 16, 4)
        csize = _le_int(data, p + 20, 4)
        usize = _le_int(data, p + 24, 4)
        name_len = int(_le_int(data, p + 28, 2))
        extra_len = int(_le_int(data, p + 30, 2))
        comm_len = int(_le_int(data, p + 32, 2))
        offset = _le_int(data, p + 42, 4)
        # an empty name is "" (metacheck's raw[(p+46):(p+45)] reads 2 bytes
        # backwards, which can fail with an embedded-nul error: U72)
        nm = (
            _raw_to_char(data[p + 45 : p + 45 + name_len], bool(flags & 0x800))
            if name_len > 0
            else ""
        )
        rows["name"].append(nm)
        rows["size"].append(usize)
        rows["method"].append(method)
        rows["csize"].append(csize)
        rows["offset"].append(offset)
        rows["crc"].append(crc)
        p = p + 46 + name_len + extra_len + comm_len
    if not rows["name"]:
        return None
    nan = float("nan")
    for col in ("size", "csize", "offset"):
        rows[col] = [nan if v == _ZIP64 else v for v in rows[col]]
    return _entry_frame(rows)


# -- HTTP range requests -------------------------------------------------------


def _content_length(resp: Any) -> float:
    """``suppressWarnings(as.numeric(resp_header(h, "content-length")))``.

    ``None`` stands for R's ``numeric(0)`` (no such header).
    """
    from pytacheck.stats._rmath import as_numeric

    value = resp.headers.get("content-length")
    if value is None:
        return None  # type: ignore[return-value]
    return as_numeric(value)[0]


def _head_total(url: str) -> float | None:
    """The HEAD request of ``zip_peek()`` / ``.http_range_tail()``: ``Content-Length``."""
    from pytacheck.archives.download import _storage_request

    resp = _storage_request("HEAD", url)
    return _content_length(resp)


#: *total* for :func:`_http_range_tail` when a HEAD answered without a Content-Length.
_NO_LENGTH = "no content-length"


def _http_range_tail(url: str, n: float, total: Any = None) -> bytes | None:
    """Port of ``R/zip-peek.R::.http_range_tail()``: the last *n* bytes of *url*.

    Uses an HTTP ``Range`` request (a ``HEAD`` first for the total size when
    *total* is ``None``). When the HEAD answer has no ``Content-Length``
    (*total* :data:`_NO_LENGTH`), a suffix range (``bytes=-<n>``) asks for
    the tail without it (metacheck fails there: U72). Returns the bytes --
    possibly fewer than *n* for a small file -- or ``None`` on failure, when
    the size cannot be read, or when the server answered neither 206 nor 200.
    A 200 (range ignored) still yields the tail of the whole body.
    """
    from pytacheck.archives.download import _storage_request, _wait_out_known_rate_limit

    try:
        if total is None:
            _wait_out_known_rate_limit(url)
            total = _head_total(url)
            if total is None:
                total = _NO_LENGTH
        if isinstance(total, str) and total == _NO_LENGTH:
            rng = f"bytes=-{float(n):.0f}"
        else:
            if is_na(total) or total <= 0:
                return None
            total = float(total)
            start = max(0.0, total - n)
            rng = f"bytes={start:.0f}-{total - 1:.0f}"
        _wait_out_known_rate_limit(url)
        r = _storage_request("GET", url, headers={"Range": rng})
        if r.status_code == 206:
            return bytes(r.content)
        if r.status_code == 200:
            body = bytes(r.content)
            k = int(n)
            return body[-k:] if 0 < k < len(body) else (body if k > 0 else b"")
        return None
    except Exception:
        return None


def _http_range_bytes(url: str, from_: float, to: float) -> bytes | None:
    """Port of ``R/zip-peek.R::.http_range_bytes()``: bytes ``[from, to]`` of *url*.

    0-based and inclusive. Only a 206 answer of exactly the requested length
    counts: a 200 (range ignored, the whole archive) is a failure here.
    """
    import math

    from pytacheck.archives.download import _storage_request, _wait_out_known_rate_limit

    try:
        if from_ is None or to is None or is_na(from_) or is_na(to):
            return None
        f, t = float(from_), float(to)
        if not math.isfinite(f) or not math.isfinite(t) or f < 0 or t < f:
            return None
        _wait_out_known_rate_limit(url)
        r = _storage_request("GET", url, headers={"Range": f"bytes={f:.0f}-{t:.0f}"})
        if r.status_code != 206:
            return None
        body = bytes(r.content)
        if len(body) != (t - f + 1):
            return None  # short/over-long read
        return body
    except Exception:
        return None


def zip_peek(url: str, tail_bytes: float = 131072) -> pd.DataFrame | None:
    """Peek at the contents of a remote ZIP file without downloading it.

    Port of ``R/zip-peek.R::zip_peek()``. A ``HEAD`` request gives the size,
    then a range request fetches the tail (*tail_bytes*, retried once with
    1 MB when the central directory is larger) and its central directory is
    read.

    Returns a data frame with ``name`` (entry path inside the zip) and
    ``size`` (uncompressed bytes), excluding directory entries, plus
    ``method``, ``csize``, ``offset`` and ``crc`` (where each member's bytes
    sit, for fetching one member alone; ``NA`` for a Zip64 archive); or
    ``None`` when the host does not support range requests or the listing
    cannot be read. Results -- successes and failures -- are cached per URL
    for the session.
    """
    with _CACHE_LOCK:
        if url in _ZIP_PEEK_CACHE:
            return _ZIP_PEEK_CACHE[url]  # type: ignore[no-any-return]

    try:
        total: Any = _head_total(url)
    except Exception:
        total = float("nan")
    if total is None:
        total = _NO_LENGTH  # the size is unknown: ask for the tail by a suffix range

    def done(value: pd.DataFrame | None) -> pd.DataFrame | None:
        with _CACHE_LOCK:
            _ZIP_PEEK_CACHE[url] = value
        return value

    for nb in dict.fromkeys([float(tail_bytes), 1048576.0]):  # retry once with 1 MB
        raw = _http_range_tail(url, nb, total=total)
        if raw is None:
            return done(None)
        cd = _parse_zip_central_dir(raw)
        if cd is not None:
            keep = [not d for d in _grepl("/$", cd["name"].tolist())]  # drop directories
            return done(cd.loc[keep].reset_index(drop=True))
        if isinstance(total, str):
            if len(raw) < nb:
                break  # whole file seen
        elif not is_na(total) and nb >= total:
            break  # whole file seen
    return done(None)


# -- single-member extraction --------------------------------------------------


def _zip_inflate_member(comp: Any, method: Any, size: Any = None) -> bytes | None:
    """Port of ``R/zip-peek.R::.zip_inflate_member()``: decompress one member.

    Method 0 (stored) returns *comp* as is, method 8 (deflate) is inflated,
    any other method gives ``None``. With *size* (the member's uncompressed
    size) the output stops one byte past it, so an over-long member shows up
    as a size mismatch; without it the whole member is inflated
    (``zip::inflate()``, as metacheck calls it, silently stops at 32768
    bytes: U71). Undecodable data gives whatever could be decoded (often
    nothing), as ``zip::inflate()`` does.
    """
    data = _as_bytes(comp)
    m = _num(method)
    if m == 0:
        return data
    if m != 8:
        return None
    s = _num(size)
    limit = int(s) + 1 if not is_na(s) and s >= 0 else None
    d = zlib.decompressobj(-15)
    out = bytearray()
    try:
        # feed in pieces so the output decoded before a corrupt block is kept
        for i in range(0, len(data), 65536):
            if limit is None:
                out += d.decompress(data[i : i + 65536])
                continue
            out += d.decompress(data[i : i + 65536], max(limit - len(out), 1))
            if len(out) >= limit or d.unconsumed_tail:
                break
        if limit is None:
            out += d.flush()
    except zlib.error:
        pass
    return bytes(out if limit is None else out[:limit])


def _crc32(bytes: Any) -> float:
    """Port of ``R/zip-peek.R::.crc32()``: CRC32 of raw bytes, as an unsigned double."""
    return float(zlib.crc32(_as_bytes(bytes)))


def _zip_crc_ok(
    bytes: Any,
    crc: Any,
    max_slow_bytes: float = 1048576,  # noqa: ARG001
) -> bool | None:
    """Port of ``R/zip-peek.R::.zip_crc_ok()``: do *bytes* match a stored CRC32?

    ``True``/``False``, or ``None`` (R ``NA``, "not checked") when *crc* is
    missing. metacheck hashes with ``digest`` when it is installed (it is a
    dependency of its test tools), so *max_slow_bytes* -- the limit of its
    pure-R fallback -- never applies here. The CRCs must be equal (metacheck
    compares with ``all.equal()``, whose relative tolerance accepts a stored
    CRC a few dozen units off a large computed one: U70).
    """
    if _is_missing(crc):
        return None
    return _crc32(bytes) == float(crc)


def _entry_row(entry: Any) -> dict[str, Any] | None:
    """One ``zip_peek()`` row (a 1-row data frame, a Series or a dict) as a dict."""
    import pandas as pd

    if entry is None:
        return None
    if isinstance(entry, pd.DataFrame):
        if len(entry) != 1:
            return None
        return {str(k): v for k, v in entry.iloc[0].to_dict().items()}
    if isinstance(entry, pd.Series):
        return {str(k): v for k, v in entry.to_dict().items()}
    if isinstance(entry, dict):
        return dict(entry)
    return None


def _zip_member_fetch(url: str, entry: Any, verify: bool = True) -> bytes | None:
    """Port of ``R/zip-peek.R::.zip_member_fetch()``: one member of a remote zip.

    *entry* is the member's row from :func:`zip_peek`. Reads the member's
    local header (its data starts after a variable-length name and extra
    field), fetches the compressed bytes by range, inflates them and checks
    the uncompressed size and (with *verify*) the CRC32 stored in the
    archive. Returns the decompressed bytes, ``b""`` for an empty member, or
    ``None`` on any failure (including Zip64 entries, whose offsets are
    unknown).
    """
    row = _entry_row(entry)
    if row is None:
        return None
    offset, csize = _num(row.get("offset")), _num(row.get("csize"))
    if is_na(offset) or is_na(csize):
        return None  # Zip64
    if csize == 0:
        return b""  # empty member

    lh = _http_range_bytes(url, offset, offset + 29)
    if lh is None:
        return None
    if lh[:4] != _LOCAL_SIG:
        return None
    data_start = offset + 30 + _le_int(lh, 27, 2) + _le_int(lh, 29, 2)

    comp = _http_range_bytes(url, data_start, data_start + csize - 1)
    if comp is None:
        return None

    size = _num(row.get("size"))
    out = _zip_inflate_member(comp, row.get("method"), size=size)
    if out is None:
        return None
    if not is_na(size) and len(out) != size:
        return None
    if verify and _zip_crc_ok(out, row.get("crc")) is False:
        return None
    return out


def _safe_member_path(name: str) -> str | None:
    """The member's path relative to the destination, or ``None`` if it climbs out."""
    rel = gsub(r"\\", "/", name)
    rel = sub("^([A-Za-z]:)?/+", "", rel)
    if any(part == ".." for part in strsplit(rel, "/", fixed=True)):
        return None
    return str(rel)


def _zip_fetch_members(
    url: str, names: Sequence[str | None] | str | None = None, dest: str = ".", verify: bool = True
) -> pd.DataFrame | None:
    """Port of ``R/zip-peek.R::.zip_fetch_members()``: download selected zip members.

    Lists the archive with :func:`zip_peek` and fetches the members named in
    *names* (all when ``None``) into *dest*, following their paths inside the
    archive (a member that would land outside *dest*, e.g. ``../x``, is
    skipped). Returns ``name``, ``path`` (on disk, ``NA`` when not fetched),
    ``size`` and ``ok`` per wanted member; a 0-row ``name``/``size`` table
    when nothing is wanted; or ``None`` when the archive cannot be listed --
    the signal to download it whole instead.
    """
    import pandas as pd

    try:
        cd = zip_peek(url)
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
    member_names = want["name"].tolist()
    for i in range(n):
        data = _zip_member_fetch(url, want.iloc[[i]], verify=verify)
        if data is None:
            continue
        rel = _safe_member_path(member_names[i])
        if rel is None:
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
            continue
        out_path[i] = target
        ok[i] = True
    return pd.DataFrame(
        {
            "name": pd.Series(member_names, dtype="string"),
            "path": pd.Series(out_path, dtype="string"),
            "size": pd.Series(want["size"].tolist(), dtype="float64"),
            "ok": pd.Series(ok, dtype="boolean"),
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
    from pytacheck._r import r_sorted

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
    from pytacheck._r import regextract

    found = regextract(r"\.([[:alnum:]]+)$", x)
    return ["" if m is None else m[1:] for m in found]


def _tolower(s: str) -> str:
    from pytacheck.fileinfo.category import _tolower as tolower

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
    """
    import pandas as pd

    from pytacheck.datacheck.files import _data_doc_role, data_classify_files, data_format

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
    return rows


def _missing_path(path: Any) -> bool:
    return path is None or is_na(path) or not os.path.exists(str(path))


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


#: Compression methods R's internal unzip (minizip) can extract: stored,
#: deflate and bzip2. Anything else stops the extraction ("zip file is corrupt").
_R_UNZIP_METHODS = (0, 8, 12)


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


def _minizip_can_open(fp: Any, info: Any) -> bool:
    """minizip's ``unzOpenCurrentFile()`` checks, as R's internal unzip runs them.

    The compression method must be one R can read, and the member's local
    header must agree with its central-directory entry on the method, the
    CRC and both sizes (unless the sizes follow in a data descriptor) and the
    name length; otherwise the zip "is corrupt".
    """
    import struct

    if info.compress_type not in _R_UNZIP_METHODS:
        return False
    fp.seek(info.header_offset)
    head = fp.read(30)
    if len(head) < 30 or head[:4] != _LOCAL_SIG:
        return False
    _ver, flags, method, _t, _d, crc, csize, usize, nlen, _x = struct.unpack(
        "<HHHHHIIIHH", head[4:]
    )
    descriptor = bool(flags & 8)
    if method != info.compress_type:
        return False
    if not descriptor and (
        crc != info.CRC
        or (csize != 0xFFFFFFFF and csize != info.compress_size)
        or (usize != 0xFFFFFFFF and usize != info.file_size)
    ):
        return False
    return bool(
        nlen == len(info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "cp437"))
    )


def _unzip_all(zip_path: str, exdir: str) -> None:
    """``utils::unzip(zip_path, exdir = exdir)``: extract as R's internal unzip does.

    Member names are used byte for byte (a backslash is part of the file name,
    ``C:/x`` makes a folder ``C:``, a leading ``/`` is harmless), except that
    ``../`` components are dropped. Parent folders are made as needed; a
    member that cannot be written (a file is in the way) is an error that
    ends the extraction, and so is -- as a warning, "zip file is corrupt" --
    a member minizip will not open (a compression method R cannot read, a
    local header that disagrees with the central directory). The CRC of the
    data itself is not checked.
    """
    import shutil
    import zipfile

    ex = os.fsencode(exdir)
    os.makedirs(ex, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf, open(zip_path, "rb") as fp:
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
            if not _minizip_can_open(fp, info):
                warnings.warn("zip file is corrupt", stacklevel=3)
                return
            try:
                fout = open(out, "wb")  # noqa: SIM115
            except OSError as e:
                raise RuntimeError(f"cannot open file '{os.fsdecode(out)}': {e.strerror}") from e
            with fout, zf.open(info) as src:
                src._expected_crc = None  # type: ignore[attr-defined]  # R ignores CRCs
                shutil.copyfileobj(src, fout)


def _expand_zip(
    zip_path: Any, zip_row: pd.DataFrame, skip_types: Any = "materials"
) -> pd.DataFrame:
    """Port of ``R/zip-peek.R::.expand_zip()``: rows for the data files inside a zip.

    Extracts the zip once to ``<zip>.contents/`` beside it (reused when it
    exists) and returns :func:`_archive_rows` for it, inheriting *zip_row*'s
    columns; a 0-row frame when the zip is missing, unreadable or holds
    nothing worth keeping.
    """
    import zipfile

    empty = zip_row.iloc[0:0].copy()
    if _missing_path(zip_path):
        return empty
    zip_path = str(zip_path)
    dest = f"{zip_path}.contents"
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
    dest = f"{tar_path}.contents"
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
    dest = f"{gz_path}.contents"
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
    from pytacheck._r import r_sorted

    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return {k: counts[k] for k in r_sorted(list(counts))}


def zip_decision(url: str, skip_types: Any = "materials") -> dict[str, Any]:
    """Decide whether a remote ZIP is worth downloading for a data archive.

    Port of ``R/zip-peek.R::zip_decision()``. Peeks inside the zip
    (:func:`zip_peek`) and classifies its entries: a zip is worth downloading
    if it holds actual data or a codebook, and not only types in
    *skip_types* (e.g. ``"materials"``).

    Returns a dict with ``worth`` (``True``/``False``, or ``None`` -- R
    ``NA`` -- when the peek failed so the caller can download to inspect),
    ``reason``, ``n_entries``, ``types`` (entry counts per type, R's
    ``table()``) and ``contents`` (the peeked listing, or ``None``).
    """
    from pytacheck.datacheck.files import _data_doc_role, data_classify_files

    peek = zip_peek(url)
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
