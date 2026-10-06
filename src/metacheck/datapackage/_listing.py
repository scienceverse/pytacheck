"""List a data package's files and folders, hidden ones included.

The package checks look at everything a researcher would hand in, so unlike
:func:`metacheck.archives.local.local_files` (which follows R and skips names
starting with ``.``) these listings keep hidden files such as ``.DS_Store`` and
folders such as ``.git``. Links are listed but never followed.

Paths are POSIX paths relative to the package's root. Depth and the top-level
folder are counted from the package's base (see
:class:`~metacheck.datapackage.OpenedPackage`); a file outside the base (next
to an archive's wrapper folder) is counted from the root.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = ["DIR_COLUMNS", "FILE_COLUMNS", "list_dirs", "list_files"]

#: Columns of :func:`list_files`.
FILE_COLUMNS = (
    "path",  # relative to the root, "/"-separated
    "name",  # the file name
    "folder",  # the folder it is in, relative to the root ("" at the root)
    "rel",  # relative to the base (the same as path for a file outside the base)
    "in_base",  # inside the base folder
    "top",  # first folder of rel ("" for a file directly in the base)
    "depth",  # folders between the base and the file (0 for a file in the base)
    "ext",  # last extension, lower case, without the dot ("" when there is none)
    "size",  # bytes
    "hidden",  # a part of rel starts with "."
    "link",  # a symbolic link
    "data_type",  # data_check's type: data, code, documentation, materials, output, unknown
    "doc_role",  # readme, license, codebook, supplemental or NA
)

#: Columns of :func:`list_dirs`.
DIR_COLUMNS = (
    "path",
    "name",
    "parent",  # relative to the root ("" at the root)
    "rel",
    "in_base",
    "depth",  # folders from the base to this one, itself included (1 for a top folder)
    "n_files",  # files directly in it
    "n_files_total",  # files in it and below
    "size_total",  # bytes in it and below
    "hidden",
    "link",
    "empty",  # no files in it or below
)


def _ext(name: str) -> str:
    stem = name.lstrip(".")
    return stem.rsplit(".", 1)[1].lower() if "." in stem else ""


def _readable(text: str) -> str:
    """*text* with undecodable bytes (surrogate escapes) shown as U+FFFD."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return text


def _rel(path: str, base: str) -> tuple[str, bool]:
    if not base:
        return path, True
    prefix = base + "/"
    return (path[len(prefix) :], True) if path.startswith(prefix) else (path, False)


def _walk(root: Path) -> tuple[list[tuple[str, int, bool]], list[tuple[str, bool]]]:
    """(files, dirs) under *root*: ``(path, size, link)`` and ``(path, link)``."""
    files: list[tuple[str, int, bool]] = []
    dirs: list[tuple[str, bool]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for d in dirnames:
            full = os.path.join(dirpath, d)
            dirs.append((f"{rel_dir}/{d}" if rel_dir else d, os.path.islink(full)))
        for f in filenames:
            full = os.path.join(dirpath, f)
            link = os.path.islink(full)
            try:
                size = os.lstat(full).st_size if link else os.path.getsize(full)
            except OSError:
                size = 0
            files.append((f"{rel_dir}/{f}" if rel_dir else f, size, link))
    files.sort(key=lambda t: (t[0].casefold(), t[0]))
    dirs.sort(key=lambda t: (t[0].casefold(), t[0]))
    return files, dirs


def list_files(root: str | os.PathLike[str], base: str = "") -> Any:
    """Every file under *root*, one row each (:data:`FILE_COLUMNS`), sorted by path."""
    import pandas as pd

    from metacheck.datacheck.files import _data_doc_role, data_classify_files

    walked, _ = _walk(Path(root))
    rows: dict[str, list[Any]] = {c: [] for c in FILE_COLUMNS}
    for path, size, link in walked:
        rel, in_base = _rel(path, base)
        parts = PurePosixPath(rel).parts
        rows["path"].append(path)
        rows["name"].append(parts[-1])
        rows["folder"].append(path.rsplit("/", 1)[0] if "/" in path else "")
        rows["rel"].append(rel)
        rows["in_base"].append(in_base)
        rows["top"].append(parts[0] if len(parts) > 1 else "")
        rows["depth"].append(len(parts) - 1)
        rows["ext"].append(_ext(parts[-1]))
        rows["size"].append(size)
        rows["hidden"].append(any(p.startswith(".") for p in parts))
        rows["link"].append(link)
    # a name that is not valid UTF-8 (os.walk keeps its bytes as surrogates) would
    # stop the classifier; classify a readable copy, and keep the real path
    names = [_readable(n) for n in rows["name"]]
    rels = [_readable(r) for r in rows["rel"]]
    rows["data_type"] = data_classify_files(names, rels) if names else []
    rows["doc_role"] = _data_doc_role(names) if names else []
    # names keep their surrogates, which Arrow-backed strings (pandas' default
    # with pyarrow installed) cannot hold
    raw = pd.StringDtype("python")
    dtypes: dict[str, Any] = {
        "path": raw,
        "name": raw,
        "folder": raw,
        "rel": raw,
        "in_base": "boolean",
        "top": raw,
        "depth": "Int64",
        "ext": raw,
        "size": "Int64",
        "hidden": "boolean",
        "link": "boolean",
        "data_type": "string",
        "doc_role": "string",
    }
    return pd.DataFrame({c: pd.Series(rows[c], dtype=dtypes[c]) for c in FILE_COLUMNS})


def list_dirs(root: str | os.PathLike[str], base: str = "", files: Any = None) -> Any:
    """Every folder under *root*, one row each (:data:`DIR_COLUMNS`), sorted by path.

    *files* is :func:`list_files`'s table for the same root, if already made.
    """
    import pandas as pd

    if files is None:
        files = list_files(root, base)
    _, walked = _walk(Path(root))
    file_paths = [str(p) for p in files["path"]]
    file_sizes = [int(s) for s in files["size"]]
    direct: dict[str, int] = {}
    total: dict[str, int] = {}
    size_total: dict[str, int] = {}
    for path, size in zip(file_paths, file_sizes, strict=True):
        parts = path.split("/")[:-1]
        if parts:
            folder = "/".join(parts)
            direct[folder] = direct.get(folder, 0) + 1
        for i in range(1, len(parts) + 1):
            anc = "/".join(parts[:i])
            total[anc] = total.get(anc, 0) + 1
            size_total[anc] = size_total.get(anc, 0) + size
    rows: dict[str, list[Any]] = {c: [] for c in DIR_COLUMNS}
    for path, link in walked:
        rel, in_base = _rel(path, base)
        rel_parts: tuple[str, ...] = PurePosixPath(rel).parts
        if base and path == base:
            rel, in_base, rel_parts = "", True, ()
        rows["path"].append(path)
        rows["name"].append(PurePosixPath(path).name)
        rows["parent"].append(path.rsplit("/", 1)[0] if "/" in path else "")
        rows["rel"].append(rel)
        rows["in_base"].append(in_base)
        rows["depth"].append(len(rel_parts))
        rows["n_files"].append(direct.get(path, 0))
        rows["n_files_total"].append(total.get(path, 0))
        rows["size_total"].append(size_total.get(path, 0))
        rows["hidden"].append(any(p.startswith(".") for p in rel_parts))
        rows["link"].append(link)
        rows["empty"].append(total.get(path, 0) == 0)
    dtypes = {
        "path": "string",
        "name": "string",
        "parent": "string",
        "rel": "string",
        "in_base": "boolean",
        "depth": "Int64",
        "n_files": "Int64",
        "n_files_total": "Int64",
        "size_total": "Int64",
        "hidden": "boolean",
        "link": "boolean",
        "empty": "boolean",
    }
    return pd.DataFrame({c: pd.Series(rows[c], dtype=dtypes[c]) for c in DIR_COLUMNS})
