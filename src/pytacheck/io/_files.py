"""R's ``list.files()`` for the importers (``read()``, ``grobid_to_bibr()``, ...)."""

from __future__ import annotations

import os
from os import PathLike

__all__ = ["list_files"]


def list_files(
    path: str | PathLike[str], pattern: str, recursive: bool = False, ignore_case: bool = False
) -> list[str]:
    """``list.files(path, pattern, recursive, ignore.case, full.names = TRUE)``.

    As in R (``all.files = FALSE``): names starting with a dot are skipped and
    hidden directories are not searched; *pattern* is a TRE regex matched
    against each file's name; without *recursive*, directories whose name
    matches are listed too; with it, directories (symbolic links included)
    are searched and never listed. Full names paste the tilde-expanded
    directory, ``"/"`` and the path below it (``"dir/"`` gives ``"dir//a.xml"``),
    and are sorted as R's ``sort()`` sorts them (ICU root collation).
    """
    from pytacheck._r.base import r_sort_key
    from pytacheck._r.regex import grepl

    def walk(d: str) -> list[str]:
        out: list[str] = []
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            return out
        for name in entries:
            if name.startswith("."):
                continue
            full = f"{d}/{name}"
            if recursive and os.path.isdir(full):
                out.extend(walk(full))
            elif grepl(pattern, name, ignore_case=ignore_case):
                out.append(full)
        return out

    root = os.path.expanduser(os.fspath(path))
    return sorted(walk(root), key=r_sort_key)
