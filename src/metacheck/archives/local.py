"""Files in a local folder, shaped like a repository listing (port of ``R/archive-local.R``)."""

from __future__ import annotations

import os
import warnings
from typing import Any, cast

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

    Hidden files are listed only when they do not belong in a repository (``.DS_Store``,
    ``.Rhistory``, ``.git/``, ``.Rproj.user/``, ... see ``.useless_file_kind()``), after the
    others, so repo_check can report them; other hidden files stay out, as before. Names are
    sorted as R sorts them.
    """
    from metacheck._r import r_sorted
    from metacheck.archives._useless import useless_file_kind

    def hidden_part(rel: str) -> bool:
        return any(part.startswith(".") for part in rel.split("/"))

    if recursive:
        found: list[str] = []
        for dirpath, _dirs, files in os.walk(path):
            rel = os.path.relpath(dirpath, path)
            found.extend(
                f if rel == "." else os.path.join(rel, f).replace("\\", "/") for f in files
            )
        visible = r_sorted([f for f in found if not hidden_part(f)])
        hidden = r_sorted([f for f in found if hidden_part(f)])
    else:
        listed = os.listdir(path)
        visible = r_sorted([n for n in listed if not n.startswith(".")])
        hidden = r_sorted([n for n in listed if n.startswith(".")])
    hidden = [h for h, k in zip(hidden, useless_file_kind(hidden), strict=True) if k is not None]
    sep = "" if path.endswith("/") else "/"
    full = [f"{path}{sep}{n}" for n in [*visible, *hidden]]
    return [f for f in full if not os.path.isdir(f)]


def local_files(path: Any, recursive: bool = False) -> pd.DataFrame:
    """Port of R/archive-local.R::local_files(): list local files like a repository.

    Returns ``repo_url`` (the *path* given), ``file_name``, ``file_url``
    (always missing), ``file_location`` (absolute), ``file_size`` and
    ``file_type`` (from :func:`~metacheck.fileinfo.category.file_category`),
    for use with ``code_check()``. Several paths are combined, each listed
    with *recursive* (metacheck lists them non-recursively: U49); a path that
    does not exist warns and contributes no rows.
    """
    from metacheck._r import bind_rows, slashed

    if not isinstance(path, str | os.PathLike):
        paths = list(path)
        if not paths:
            # R: `if (dir.exists(character(0)))`
            raise ValueError("argument is of length zero")
        if len(paths) != 1:
            return bind_rows([local_files(p, recursive=recursive) for p in paths])
        path = paths[0]
    path = os.fspath(path)
    # R's file functions expand a leading "~"; `repo_url` keeps the path as given
    expanded = os.path.expanduser(path)

    if os.path.isdir(expanded):
        all_paths = _list_files(expanded, recursive)
    elif os.path.exists(expanded):
        all_paths = [expanded]
    else:
        all_paths = []
        warnings.warn(f"This path does not exist: {path}", stacklevel=2)

    if not all_paths:
        return _empty()

    from metacheck.fileinfo.category import file_category

    names = [os.path.basename(p) for p in all_paths]
    sizes = [float(os.path.getsize(p)) if os.path.exists(p) else float("nan") for p in all_paths]
    categories = cast("pd.DataFrame", file_category(names))["file_category"].tolist()
    n = len(all_paths)
    return pd.DataFrame(
        {
            "repo_url": pd.Series([path] * n, dtype="string"),
            "file_name": pd.Series(names, dtype="string"),
            "file_url": pd.Series([None] * n, dtype="string"),
            "file_location": pd.Series(
                [slashed(os.path.realpath(p)) for p in all_paths], dtype="string"
            ),
            "file_size": pd.Series(sizes, dtype="float64"),
            "file_type": pd.Series(categories, dtype="string"),
        }
    )
