"""Files in a local folder, shaped like a repository listing (port of ``R/archive-local.R``)."""

from __future__ import annotations

import os
import warnings
from typing import Any

import pandas as pd

__all__ = ["local_files"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": pd.Series([], dtype="string"),
            "file_name": pd.Series([], dtype="string"),
            "file_url": pd.Series([], dtype="string"),
            "file_location": pd.Series([], dtype="string"),
            "file_size": pd.Series([], dtype="float64"),
            "file_type": pd.Series([], dtype="string"),
        }
    )


def _list_files(path: str, recursive: bool) -> list[str]:
    """R ``list.files(path, full.names = TRUE, recursive)`` minus directories.

    Hidden files are skipped (``all.files = FALSE``) and names are sorted as
    R sorts them.
    """
    from pytacheck._r import r_sorted

    if recursive:
        found: list[str] = []
        for dirpath, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel = os.path.relpath(dirpath, path)
            found.extend(
                f if rel == "." else os.path.join(rel, f).replace("\\", "/")
                for f in files
                if not f.startswith(".")
            )
        names = r_sorted(found)
    else:
        names = r_sorted([n for n in os.listdir(path) if not n.startswith(".")])
    sep = "" if path.endswith("/") else "/"
    full = [f"{path}{sep}{n}" for n in names]
    return [f for f in full if not os.path.isdir(f)]


def local_files(path: Any, recursive: bool = False) -> pd.DataFrame:
    """Port of R/archive-local.R::local_files(): list local files like a repository.

    Returns ``repo_url`` (the *path* given), ``file_name``, ``file_url``
    (always missing), ``file_location`` (absolute), ``file_size`` and
    ``file_type`` (from :func:`~pytacheck.fileinfo.category.file_category`),
    for use with ``code_check()``. Several paths are combined; a path that
    does not exist warns and contributes no rows.

    As in metacheck, *recursive* is not passed on when several paths are
    given (each is then listed non-recursively).
    """
    from pytacheck._r import bind_rows

    if not isinstance(path, str | os.PathLike):
        paths = list(path)
        if len(paths) != 1:
            return bind_rows([local_files(p) for p in paths]) if paths else _empty()
        path = paths[0]
    path = os.fspath(path)

    if os.path.isdir(path):
        all_paths = _list_files(path, recursive)
    elif os.path.exists(path):
        all_paths = [path]
    else:
        all_paths = []
        warnings.warn(f"This path does not exist: {path}", stacklevel=2)

    if not all_paths:
        return _empty()

    from pytacheck.fileinfo.category import file_category

    names = [os.path.basename(p) for p in all_paths]
    sizes = [float(os.path.getsize(p)) if os.path.exists(p) else float("nan") for p in all_paths]
    categories = file_category(names)["file_category"].tolist()
    n = len(all_paths)
    return pd.DataFrame(
        {
            "repo_url": pd.Series([path] * n, dtype="string"),
            "file_name": pd.Series(names, dtype="string"),
            "file_url": pd.Series([None] * n, dtype="string"),
            "file_location": pd.Series([os.path.realpath(p) for p in all_paths], dtype="string"),
            "file_size": pd.Series(sizes, dtype="float64"),
            "file_type": pd.Series(categories, dtype="string"),
        }
    )
