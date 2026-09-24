"""On-disk cache of repository LISTING results (port of ``R/repo-info-cache.R``).

Off by default; :func:`repo_info_cache` turns it on. When on, the archive
``*_info()`` functions store each repository's listing (metadata + file list)
and reuse it on a later call instead of re-querying the host, so restarting
an interrupted corpus run does not re-spend a host's API quota.

metacheck stores entries with ``saveRDS()`` as ``<key>.rds``; pytacheck
stores the same values pickled as ``<key>.pkl`` (the cache is local to one
machine and one package, so only the key scheme needs to match).
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

from pytacheck._r import as_character, gsub, is_na

__all__ = ["repo_info_cache", "repo_info_cache_clear"]

_SUFFIX = ".pkl"


def repo_info_cache(enabled: bool | None = None) -> bool:
    """Port of R/repo-info-cache.R::repo_info_cache(): get or set whether listings are cached."""
    from pytacheck.utils import get_option, options

    if enabled is None:
        return get_option("metacheck.repo_info.cache", False) is True
    if not isinstance(enabled, bool):
        raise ValueError("Set repo_info_cache with TRUE or FALSE")
    options({"metacheck.repo_info.cache": enabled})
    return enabled


def repo_info_cache_clear() -> int:
    """Port of R/repo-info-cache.R::repo_info_cache_clear(): delete all cached listings.

    Returns the number of entries removed.
    """
    root = Path(_repo_info_cache_dir())
    files = [f for f in root.iterdir() if f.is_file() and f.name.endswith(_SUFFIX)]
    for f in files:
        f.unlink(missing_ok=True)
    return len(files)


def _repo_info_cache_dir() -> str:
    """Port of R/repo-info-cache.R::.repo_info_cache_dir(): ``.metacheck_repo_info_cache``.

    ``options({"metacheck.repo_info_cache.dir": path})`` relocates just this cache.
    """
    from pytacheck.archives.cache import _metacheck_cache_subdir
    from pytacheck.utils import get_option

    return _metacheck_cache_subdir(
        ".metacheck_repo_info_cache", override=get_option("metacheck.repo_info_cache.dir")
    )


def _repo_info_cache_key(host: str, id: Any) -> str:  # noqa: A002 - R name
    """Port of R/repo-info-cache.R::.repo_info_cache_key(): filesystem-safe key."""
    ident = "unknown" if id is None else ("NA" if is_na(id) else as_character(id))
    key = f"{host}_{ident}"
    key = gsub("[^A-Za-z0-9._-]+", "_", key)
    key = gsub("^_+|_+$", "", key)
    if key == "":
        key = f"{host}_unknown"
    return key


def _repo_info_cache_path(host: str, id: Any) -> str:  # noqa: A002 - R name
    """Port of R/repo-info-cache.R::.repo_info_cache_path()."""
    return os.path.join(_repo_info_cache_dir(), _repo_info_cache_key(host, id) + _SUFFIX)


def _repo_info_cache_get(host: str, id: Any) -> Any:  # noqa: A002 - R name
    """Port of R/repo-info-cache.R::.repo_info_cache_get(): a cached value, or ``None``."""
    path = _repo_info_cache_path(host, id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            return pickle.load(fh)  # noqa: S301 - our own local cache
    except Exception:  # noqa: BLE001 - an unreadable entry is a miss
        return None


def _repo_info_cache_put(host: str, id: Any, value: Any) -> Any:  # noqa: A002 - R name
    """Port of R/repo-info-cache.R::.repo_info_cache_put(): store a value (errors ignored)."""
    try:
        path = _repo_info_cache_path(host, id)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - a caching miss is never worse than no cache
        pass
    return value


def _repo_info_ok(value: Any) -> bool:
    """Port of R/repo-info-cache.R::.repo_info_ok(): is a listing worth caching?

    Only a data frame with no ``error`` column, or an all-missing one.
    """
    import pandas as pd

    if value is None or not isinstance(value, pd.DataFrame):
        return False
    if "error" not in value.columns:
        return True
    return bool(value["error"].isna().all())


def _repo_info_list_ok(value: Any) -> bool:
    """Port of R/repo-info-cache.R::.repo_info_list_ok(): a list result not marked ``gated``."""
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        if "gated" not in value.columns or len(value) != 1:
            return True
        return value["gated"].iloc[0] is not True and not (
            not is_na(value["gated"].iloc[0]) and bool(value["gated"].iloc[0]) is True
        )
    if isinstance(value, dict):
        gated = value.get("gated")
        if isinstance(gated, list | tuple):
            gated = gated[0] if len(gated) == 1 else None
        return gated is not True
    return isinstance(value, list | tuple)
