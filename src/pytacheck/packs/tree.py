"""Content hashes of pack folders (``pytacheck-tree-v1``) and files.

The tree hash covers the regular files of a pack folder, excluding
``.git/``, ``__pycache__/``, ``*.pyc`` and ``.pytacheck-install.json``.
Paths are sorted bytewise as POSIX paths relative to the pack root and
hashed as ``sha256sum`` lines, so it equals::

    find . -type f ! -path '*/.git/*' ! -path '*/__pycache__/*' ! -name '*.pyc' \\
        ! -name .pytacheck-install.json -printf '%P\\0' \\
      | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pytacheck.provenance import _sha_cache, file_sha256

__all__ = ["INSTALL_RECORD", "TREE_ALGORITHM", "file_sha256", "tree_files", "tree_sha256"]

TREE_ALGORITHM = "pytacheck-tree-v1"
INSTALL_RECORD = ".pytacheck-install.json"
_SKIP_DIRS = frozenset({".git", "__pycache__"})


def clear_cache() -> None:
    """Forget cached file hashes (:func:`pytacheck.packs.refresh` calls this)."""
    _sha_cache.clear()


def tree_files(root: str | os.PathLike[str]) -> list[str]:
    """The hashed files of a pack folder: POSIX paths, sorted bytewise."""
    base = os.fspath(root)
    out: list[str] = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name.endswith(".pyc") or name == INSTALL_RECORD:
                continue
            full = os.path.join(folder, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue  # regular files only, like `find -type f`
            out.append(Path(os.path.relpath(full, base)).as_posix())
    return sorted(out, key=lambda p: p.encode("utf-8", "surrogateescape"))


def tree_sha256(root: str | os.PathLike[str]) -> str:
    """The ``pytacheck-tree-v1`` hash of a pack folder."""
    base = Path(root)
    h = hashlib.sha256()
    for rel in tree_files(base):
        h.update(f"{file_sha256(base / rel)}  {rel}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()
