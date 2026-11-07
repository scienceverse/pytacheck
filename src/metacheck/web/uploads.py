"""What an upload is, and whether a zip may be unpacked. Nothing here imports FastAPI.

A file is told by its first bytes, not by its name or the type the sender gave. A zip is
looked at before a job is made: how many files it says it holds, how big they say they are
and whether a path leads out of the folder. The unpacking has its own limits as well
(``metacheck.datapackage.open_package``), which count what is really written.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from metacheck.web.config import (
    MAX_UNPACKED_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_ZIP_BYTES,
    MAX_ZIP_FILES,
)

__all__ = ["Refused", "check_zip", "file_kind", "folder_name", "safe_name", "size_limit"]


class Refused(Exception):
    """A request that is turned down: the HTTP status and the message for the sender."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def size_limit(kind: str) -> int:
    """The most bytes a file of this kind may have."""
    return MAX_ZIP_BYTES if kind == "zip" else MAX_UPLOAD_BYTES


def file_kind(head: bytes, names: tuple[str, ...] = ()) -> str:
    """``pdf``, ``json`` (bibr), ``xml`` (GROBID), ``zip`` or ``docx`` from the first bytes of
    a file; *names* are the member names of a zip, which tell a Word file from a folder's zip.

    Raises :class:`Refused` (400) for anything else.
    """
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return "docx"
        return "zip"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if text[:1] in (b"{", b"["):
        return "json"
    if text.startswith((b"<?xml", b"<TEI")):
        return "xml"
    raise Refused(400, "The file is not a PDF, bibr JSON, GROBID XML or a .zip file.")


def safe_name(filename: str | None) -> str:
    """The sender's file name without folders, extension and control characters, at most 120
    characters: it names the report, and never a place on this server."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.[^.]*$", "", name) if "." in name.lstrip(".") else name
    return re.sub(r"[\x00-\x1f\x7f]", "", stem).strip()[:120]


def folder_name(name: str) -> str:
    """The name a checked folder is given: *name* made safe as a single folder name."""
    cleaned = re.sub(r"[/\\\x00-\x1f\x7f]", "_", name).strip()
    return "folder" if cleaned in ("", ".", "..") else cleaned


def _leaves_folder(name: str) -> bool:
    """Whether a zip member's path is absolute or climbs out of the folder."""
    path = name.replace("\\", "/")
    if path.startswith("/") or re.match(r"[A-Za-z]:", path):
        return True
    return ".." in PurePosixPath(path).parts


def check_zip(path: Path) -> str:
    """Refuse a zip that is not a zip, that holds too many files, says it unpacks to too much
    or has a path that leads out of its folder. Returns ``"zip"``, or ``"docx"`` for a Word
    file, which is a zip too.

    Raises :class:`Refused`: 413 for a size, 400 for the rest.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = tuple(m.filename for m in members)
    except (zipfile.BadZipFile, OSError):
        raise Refused(400, "The .zip file cannot be read. Is it damaged?") from None
    if file_kind(b"PK\x03\x04", names) == "docx":
        return "docx"
    files = [m for m in members if not m.is_dir()]
    if len(files) > MAX_ZIP_FILES:
        raise Refused(
            413,
            f"The .zip file holds more than {MAX_ZIP_FILES:,} files. Leave out folders that do "
            "not need checking, such as renv/ or node_modules/.",
        )
    if sum(m.file_size for m in files) > MAX_UNPACKED_BYTES:
        raise Refused(
            413,
            "The .zip file unpacks to more than 1000 MB. Leave out large data or output files "
            "that do not need checking, and try again.",
        )
    if any(_leaves_folder(m.filename) for m in members):
        raise Refused(400, "The .zip file has paths that point outside its folder.")
    return "zip"
