"""Files that do not belong in a repository (port of ``R/file-naming.R::.useless_file_kind()``).

Software creates these by itself, never the authors, and they hold nothing a
reader can use: the operating system's files (``__MACOSX/``, ``._`` copies,
``.DS_Store``, ``Thumbs.db``), version-control folders, editor settings,
RStudio session files, Jupyter checkpoints, compiled Python files, lock files
of open documents, R history, editor backups and temporary files. Archive
expansion leaves them out (``zip_peek._archive_keep``) and a local folder
listing reports them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

__all__ = ["is_useless_file", "useless_file_kind", "useless_file_severity"]

_OS_BASENAMES = frozenset(
    {
        ".ds_store",
        ".localized",
        "icon\r",
        "thumbs.db",
        "ehthumbs.db",
        "ehthumbs_vista.db",
        "desktop.ini",
        ".directory",
    }
)


def _in_dir(path: str, folder: str) -> bool:
    return re.search(rf"(^|/){folder}(/|$)", path) is not None


def useless_file_kind(path: Sequence[str | None]) -> list[str | None]:
    """The kind of useless file of each path, ``None`` for any other file (and for ``None``)."""
    out: list[str | None] = []
    for raw in path:
        if raw is None:
            out.append(None)
            continue
        p = raw.replace("\\", "/")
        base = re.sub("^.*/", "", p)  # not basename(): a leading "~" would be expanded
        lower = base.lower()
        if (
            _in_dir(p, "__MACOSX")
            or base.startswith("._")
            or lower in _OS_BASENAMES
            or any(
                _in_dir(p, f)
                for f in (
                    r"\.AppleDouble",
                    r"\.Spotlight-V100",
                    r"\.Trashes",
                    r"\.fseventsd",
                    r"\.TemporaryItems",
                    r"\$RECYCLE\.BIN",
                    r"\.Trash-[0-9]+",
                )
            )
        ):
            kind: str | None = "operating system"
        elif _in_dir(p, r"\.git") or _in_dir(p, r"\.svn") or _in_dir(p, r"\.hg"):
            kind = "version control"
        elif _in_dir(p, r"\.idea") or _in_dir(p, r"\.vscode"):
            kind = "editor settings"
        elif _in_dir(p, r"\.Rproj\.user"):
            kind = "RStudio session files"
        elif _in_dir(p, r"\.ipynb_checkpoints"):
            kind = "Jupyter checkpoints"
        elif _in_dir(p, "__pycache__") or re.search(r"\.py[co]$", base):
            kind = "compiled Python files"
        elif base.startswith("~$") or re.search(r"^\.~lock\..*#$", base):
            kind = "open-document lock files"
        elif base == ".Rhistory":
            kind = "R history"
        elif re.search(r"(~|\.bak|\.swp|\.swo)$", base, re.IGNORECASE):
            kind = "editor backups"
        elif re.search(r"\.(tmp|temp)$", base, re.IGNORECASE):
            kind = "temporary files"
        else:
            kind = None
        out.append(kind)
    return out


def useless_file_severity(kind: Sequence[str | None]) -> list[str | None]:
    """``"problem"`` (remove it) or ``"suggestion"`` (version control and editor settings,
    which can be kept on purpose) for each kind; ``None`` for ``None``."""
    return [
        None
        if k is None
        else "suggestion"
        if k in ("version control", "editor settings")
        else "problem"
        for k in kind
    ]


def is_useless_file(path: Sequence[str | None]) -> list[bool]:
    """Whether each path is a useless file."""
    return [k is not None for k in useless_file_kind(path)]
