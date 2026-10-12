"""Shared cache locations (port of ``R/cache.R``).

metacheck keeps its on-disk caches in folders named ``.metacheck_*`` under
one root: the current working directory by default, so a project's cache
lives with the project. ``options({"metacheck.cache.dir": path})`` relocates
all of them; pytacheck also honours the ``PYTACHECK_CACHE_DIR`` environment
variable (used by the test suite to keep caches out of the project).
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from metacheck._env import env_get

__all__ = ["metacheck_cache_info"]


def _metacheck_cache_root() -> str:
    """Port of R/cache.R::.metacheck_cache_root(): the shared cache root."""
    from metacheck.utils import get_option

    opt = get_option("metacheck.cache.dir")
    if opt:
        return str(opt)
    return env_get("CACHE_DIR") or os.getcwd()


def _metacheck_cache_subdir(subdir: str, override: str | os.PathLike[str] | None = None) -> str:
    """Port of R/cache.R::.metacheck_cache_subdir(): one named cache folder.

    *override* (a cache-specific option or environment variable) wins when
    non-empty; otherwise the folder is ``<root>/<subdir>``. Created if needed;
    returned as an absolute path with forward slashes.
    """
    if override is not None and str(override) != "":
        path = Path(override)
    else:
        path = Path(_metacheck_cache_root()) / subdir
    path.mkdir(parents=True, exist_ok=True)
    return os.path.realpath(path).replace("\\", "/")


def _metacheck_dir_size(dir: str | os.PathLike[str]) -> float:
    """Port of R/cache.R::.metacheck_dir_size(): total bytes of a folder's files."""
    root = Path(dir)
    if not root.is_dir():
        return 0.0
    total = 0.0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def _format_big_mark(x: float) -> str:
    """R ``format(x, big.mark = ",")`` for one number (7 significant digits)."""
    from metacheck._r import format_num

    s = format_num(x, 7)
    if "e" in s:
        return s
    sign = "-" if s.startswith("-") else ""
    s = s.lstrip("-")
    whole, _, frac = s.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + ",".join(groups) + (f".{frac}" if frac else "")


def metacheck_cache_info() -> pd.DataFrame:
    """Port of R/cache.R::metacheck_cache_info(): where the caches live, and their size.

    Reports the repository-file cache, the repository-listing cache and the
    LLM-response cache (``cache``, ``path``, ``size_mb``).
    """
    from metacheck.archives import _message as message
    from metacheck.archives.download import _repo_cache_dir
    from metacheck.archives.info_cache import _repo_info_cache_dir
    from metacheck.llm.cache import _llm_cache_dir
    from metacheck.utils import get_option

    repo_dir = _repo_cache_dir()
    info_dir = _repo_info_cache_dir()
    llm_dir = _llm_cache_dir()
    paths = [str(repo_dir), str(info_dir), str(llm_dir)]
    info = pd.DataFrame(
        {
            "cache": pd.Series(["repo_files", "repo_info", "llm"], dtype="string"),
            "path": pd.Series(paths, dtype="string"),
            "size_mb": pd.Series(
                [round(_metacheck_dir_size(p) / 1024**2, 1) for p in paths], dtype="float64"
            ),
        }
    )
    message("metacheck caches:")
    for cache, path, size in zip(info["cache"], info["path"], info["size_mb"], strict=True):
        message(f"  {cache:<11} {path}  ({_format_big_mark(size)} MB)")
    opt = get_option("metacheck.cache.dir")
    in_wd = opt is None or os.path.realpath(str(opt)) == os.path.realpath(os.getcwd())
    if in_wd and os.path.isdir(".git"):
        message("  tip: add '.metacheck_*' to your .gitignore so these are not committed.")
    return info
