"""Open a data package: a folder as it is, an archive extracted to a private folder.

A data package is the folder (or the zip or tar archive of it) a researcher
hands in with a paper: data, code, documentation. :func:`open_package` turns
either into an :class:`OpenedPackage` whose files the package checks list with
:mod:`metacheck.datapackage._listing`.

Archives are extracted safely: a member whose path is absolute or climbs out
with ``..``, a link and a device file are skipped (and listed in
``OpenedPackage.skipped``), and an archive bigger than ``max_bytes`` once
extracted, or with more than ``max_files`` members, is refused.

Most zips of a folder hold that one folder at the top. Its name is not part of
the package's structure, so the checks count folder depth and layout from
inside it (``OpenedPackage.base``). Files next to it, such as macOS's
``__MACOSX`` folder, are still listed. A folder the user chose is never
stepped into: what they picked is the package.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = [
    "ARCHIVE_SUFFIXES",
    "OpenedPackage",
    "PackageError",
    "is_archive",
    "open_package",
]

#: Archive types :func:`open_package` extracts (lower-case name endings).
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")

#: Default limits for an extracted archive.
MAX_BYTES = 20 * 1024**3
MAX_FILES = 200_000

#: Top-level entries ignored when deciding whether an archive holds a single
#: wrapper folder (they are still listed and reported).
_WRAPPER_IGNORED = frozenset({"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"})

#: A single top-level folder with one of these names is a part of the package,
#: not a wrapper around it, so it is not stepped into.
_COMPONENT_NAMES = frozenset(
    {
        "data",
        "raw",
        "processed",
        "code",
        "codes",
        "script",
        "scripts",
        "src",
        "analysis",
        "docs",
        "doc",
        "documentation",
        "materials",
        "figures",
        "output",
        "outputs",
        "results",
    }
)


class PackageError(Exception):
    """A path that cannot be opened as a data package."""


@dataclass
class OpenedPackage:
    """A data package ready to be listed.

    ``root`` is the folder on disk that holds everything (for an archive, the
    private folder it was extracted to). ``base`` is the folder the package's
    structure starts in, relative to ``root`` (``""`` for ``root`` itself, or
    the archive's single wrapper folder). ``source`` is what the user gave.
    """

    source: str
    root: Path
    base: str = ""
    archive: bool = False
    skipped: tuple[str, ...] = ()
    _cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def base_dir(self) -> Path:
        """The folder the package's structure starts in."""
        return self.root / self.base if self.base else self.root

    @property
    def name(self) -> str:
        """A name for the package: the wrapper folder's, else the folder's or archive's."""
        if self.base:
            return PurePosixPath(self.base).name
        name = Path(self.source).name
        low = name.lower()
        for suffix in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
            if self.archive and low.endswith(suffix):
                return name[: -len(suffix)] or name
        return name

    def files(self) -> Any:
        """Every file of the package (see :func:`~metacheck.datapackage.list_files`)."""
        if "files" not in self._cache:
            from metacheck.datapackage._listing import list_files

            self._cache["files"] = list_files(self.root, base=self.base)
        return self._cache["files"]

    def dirs(self) -> Any:
        """Every folder of the package (see :func:`~metacheck.datapackage.list_dirs`)."""
        if "dirs" not in self._cache:
            from metacheck.datapackage._listing import list_dirs

            self._cache["dirs"] = list_dirs(self.root, base=self.base, files=self.files())
        return self._cache["dirs"]


def is_archive(path: str | os.PathLike[str]) -> bool:
    """Whether *path* names an archive type :func:`open_package` extracts."""
    low = os.fspath(path).lower()
    return any(low.endswith(s) for s in ARCHIVE_SUFFIXES)


@contextlib.contextmanager
def open_package(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_BYTES,
    max_files: int = MAX_FILES,
) -> Iterator[OpenedPackage]:
    """Open the folder or archive at *path* as a data package.

    A folder is used in place. An archive (:data:`ARCHIVE_SUFFIXES`) is
    extracted to a private temporary folder that is removed when the ``with``
    block ends. Raises :class:`PackageError` for a missing path, a file that is
    not a supported archive, or an archive over the limits.
    """
    p = Path(path).expanduser()
    if p.is_dir():
        yield OpenedPackage(source=os.fspath(path), root=p.resolve())
        return
    if not p.exists():
        raise PackageError(f"No folder or archive found at {os.fspath(path)}")
    if not is_archive(p):
        raise PackageError(
            f"{p.name} is neither a folder nor an archive "
            f"({', '.join(ARCHIVE_SUFFIXES)}). Give the package's folder, or a zip of it."
        )
    tmp = Path(tempfile.mkdtemp(prefix="metacheck-package-"))
    try:
        dest = tmp / "package"
        dest.mkdir()
        skipped = _extract(p, dest, max_bytes=max_bytes, max_files=max_files)
        yield OpenedPackage(
            source=os.fspath(path),
            root=dest,
            base=_wrapper(dest),
            archive=True,
            skipped=tuple(skipped),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _wrapper(root: Path) -> str:
    """The single top-level folder of an extracted archive, or ``""``."""
    entries = [e for e in os.scandir(root) if e.name not in _WRAPPER_IGNORED]
    if len(entries) != 1 or not entries[0].is_dir(follow_symlinks=False):
        return ""
    name = entries[0].name
    return "" if name.lower() in _COMPONENT_NAMES else name


def _safe_relpath(name: str) -> str | None:
    """A member name as a relative POSIX path, or ``None`` if it is unsafe.

    Backslashes count as separators (zips made on Windows use them). Absolute
    paths, drive letters and ``..`` components are unsafe.
    """
    rel = name.replace("\\", "/")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return None
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _extract(archive: Path, dest: Path, *, max_bytes: int, max_files: int) -> list[str]:
    low = archive.name.lower()
    try:
        if low.endswith(".zip"):
            return _extract_zip(archive, dest, max_bytes=max_bytes, max_files=max_files)
        return _extract_tar(archive, dest, max_bytes=max_bytes, max_files=max_files)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        raise PackageError(f"{archive.name} cannot be read as an archive: {exc}") from exc


def _check_limits(n_files: int, n_bytes: int, *, max_bytes: int, max_files: int) -> None:
    if n_files > max_files:
        raise PackageError(f"The archive holds more than {max_files:,} files")
    if n_bytes > max_bytes:
        raise PackageError(
            f"The archive is bigger than {max_bytes / 1024**3:.0f} GB when extracted"
        )


def _extract_zip(archive: Path, dest: Path, *, max_bytes: int, max_files: int) -> list[str]:
    from metacheck.archives.zip_peek import _zip_name

    skipped: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        _check_limits(
            len(infos), sum(i.file_size for i in infos), max_bytes=max_bytes, max_files=max_files
        )
        written = 0
        for info in infos:
            name = _zip_name(info)
            rel = _safe_relpath(name)
            mode = info.external_attr >> 16
            if rel is None or stat.S_ISLNK(mode):
                skipped.append(name)
                continue
            out = dest / rel
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                # the declared sizes were checked; count what is really written too
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > max_bytes:
                        raise PackageError(
                            f"The archive is bigger than {max_bytes / 1024**3:.0f} GB when extracted"
                        )
                    dst.write(chunk)
    return skipped


def _extract_tar(archive: Path, dest: Path, *, max_bytes: int, max_files: int) -> list[str]:
    skipped: list[str] = []
    with tarfile.open(archive, mode="r:*") as tf:
        members = tf.getmembers()
        _check_limits(
            len(members),
            sum(m.size for m in members if m.isfile()),
            max_bytes=max_bytes,
            max_files=max_files,
        )
        for m in members:
            rel = _safe_relpath(m.name)
            if rel is None or not (m.isfile() or m.isdir()):
                skipped.append(m.name)
                continue
            out = dest / rel
            if m.isdir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                skipped.append(m.name)
                continue
            with src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return skipped
