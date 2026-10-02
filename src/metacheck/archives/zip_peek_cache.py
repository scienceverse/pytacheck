"""On-disk cache of ``zip_peek()`` results (port of ``R/zip-peek-cache.R``).

:func:`metacheck.archives.zip_peek.zip_peek` keeps its results in memory for
the session; with ``cache=True`` it also keeps them here, so a restarted run
does not peek again at an archive it already listed (a zip's contents cannot
change). Off by default; ``repo_check()``/``data_check()`` pass their own
``cache`` argument on.

metacheck stores entries with ``saveRDS()`` as ``<sha1 of the URL>.rds``;
pytacheck stores versioned, typed JSON as ``<sha1 of the URL>.json`` (the
codec of :mod:`metacheck.repro.tables`, never pickle), written atomically. An
unreadable entry is a miss, not a cached failure, and a failed peek that can
pass (a rate limit, a connection failure, a 403/429/5xx answer) is not
stored at all (U205).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
from typing import Any

__all__ = ["zip_peek_cache_clear"]

_SUFFIX = ".json"
_FORMAT = "metacheck.zip_peek_cache"
_VERSION = 1


def zip_peek_cache_clear() -> int:
    """Port of ``R/zip-peek-cache.R::zip_peek_cache_clear()``: delete all cached zip listings.

    Safe at any time: a cleared entry is peeked again over HTTP when next
    needed. Returns the number of entries removed.
    """
    root = Path(_zip_peek_cache_dir())
    files = [f for f in root.iterdir() if f.is_file() and f.name.endswith(_SUFFIX)]
    for f in files:
        f.unlink(missing_ok=True)
    return len(files)


def _zip_peek_cache_dir() -> str:
    """Port of ``R/zip-peek-cache.R::.zip_peek_cache_dir()``: ``.metacheck_zip_peek_cache``.

    ``options({"metacheck.zip_peek_cache.dir": path})`` relocates just this
    cache; ``metacheck.cache.dir`` relocates all caches together.
    """
    from metacheck.archives.cache import _metacheck_cache_subdir
    from metacheck.utils import get_option

    return _metacheck_cache_subdir(
        ".metacheck_zip_peek_cache", override=get_option("metacheck.zip_peek_cache.dir")
    )


def _zip_peek_cache_key(url: str) -> str:
    """Port of ``R/zip-peek-cache.R::.zip_peek_cache_key()``: the SHA-1 of *url*.

    A download URL can be long and carry tokens, so it is hashed rather than
    used as a file name. (metacheck uses ``digest::digest(url, "sha1")``,
    which hashes R's serialization of the string, so the names differ.)
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()  # noqa: S324 -- a file name, not security


def _zip_peek_cache_path(url: str) -> str:
    """Port of ``R/zip-peek-cache.R::.zip_peek_cache_path()``."""
    return os.path.join(_zip_peek_cache_dir(), _zip_peek_cache_key(url) + _SUFFIX)


def _zip_peek_cache_lookup(url: str) -> tuple[bool, Any]:
    """``(True, listing)`` for a readable entry (the listing may be ``None``, a
    cached failure); ``(False, None)`` when there is none or it cannot be read."""
    import orjson

    from metacheck.repro.tables import _decode

    path = _zip_peek_cache_path(url)
    if not os.path.exists(path):
        return False, None
    try:
        with open(path, "rb") as fh:
            payload = orjson.loads(fh.read())
        if (
            not isinstance(payload, dict)
            or payload.get("format") != _FORMAT
            or payload.get("version") != _VERSION
        ):
            return False, None
        return True, _decode(payload["value"])
    except Exception:
        return False, None


def _zip_peek_cache_get(url: str) -> Any:
    """Port of ``R/zip-peek-cache.R::.zip_peek_cache_get()``: a cached listing, or ``None``."""
    return _zip_peek_cache_lookup(url)[1]


def _zip_peek_cache_put(url: str, value: Any) -> Any:
    """Port of ``R/zip-peek-cache.R::.zip_peek_cache_put()``: store a listing or ``None``.

    A failed peek (``None``) is stored too: a host's support for range
    requests does not change. Write errors are ignored (a caching miss is
    never worse than the uncached behaviour).
    """
    import orjson

    from metacheck.repro.tables import _encode

    with contextlib.suppress(Exception):
        # the URL itself is not stored: it can carry a token
        data = orjson.dumps({"format": _FORMAT, "version": _VERSION, "value": _encode(value)})
        path = _zip_peek_cache_path(url)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    return value


def _zip_peek_cache_has(url: str) -> bool:
    """Port of ``R/zip-peek-cache.R::.zip_peek_cache_has()``: is there an entry for *url*?"""
    return os.path.exists(_zip_peek_cache_path(url))
