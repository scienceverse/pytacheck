"""Open a data package: a folder as it is, an archive extracted to a private folder.

A data package is the folder (or the zip or tar archive of it) a researcher
hands in with a paper: data, code, documentation. :func:`open_package` turns
either into an :class:`OpenedPackage` whose files the package checks list with
:mod:`metacheck.datapackage._listing`.

Archives are extracted safely. A member whose path is absolute or climbs out
with ``..``, is too long or too deep, or repeats an earlier name, a link and a
device file are skipped (and listed in ``OpenedPackage.skipped``). An archive is
refused when it holds more than ``max_files`` members, expands to more than
``max_bytes``, expands to more than ``max_ratio`` times its own size (a zip
bomb), is encrypted, or does not fit on the disk. What is written is counted as
it is written, so a header that lies about its sizes cannot get past the
limits, and a tar is read as a stream, so that a compressed tar is never
unpacked just to be counted. After unpacking, the files that are themselves
zip containers (a nested zip, an ``.xlsx``, an ``.ods``) or gzip, bzip2 or xz
streams are looked at too, because the checks open them later: one that
expands past the same limits refuses the package (see :func:`_scan_nested`).

Most zips of a folder hold that one folder at the top. Its name is not part of
the package's structure, so the checks count folder depth and layout from
inside it (``OpenedPackage.base``). Files next to it, such as macOS's
``__MACOSX`` folder, are still listed. A folder the user chose is never
stepped into: what they picked is the package.
"""

from __future__ import annotations

import bz2
import contextlib
import gzip
import lzma
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

__all__ = [
    "ARCHIVE_SUFFIXES",
    "MAX_BYTES",
    "MAX_DEPTH",
    "MAX_FILES",
    "MAX_RATIO",
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
#: An archive that expands to more than this many times its own size is refused, but only once
#: it expands to more than ``RATIO_FLOOR`` (a small file of zeros is harmless). Text and tables
#: compress 5 to 50 times; a zip bomb a thousand times and more.
MAX_RATIO = 250.0
RATIO_FLOOR = 256 * 1024**2
#: Folders deep, and the longest path and path part (in bytes) of a member that is unpacked.
MAX_DEPTH = 40
MAX_PATH = 1024
MAX_NAME = 255
#: A file inside the package that is a zip container (a nested zip, an ``.xlsx``) may expand
#: to this much at most (and to no more than ``max_bytes``): the checks read such a file into
#: memory.
MAX_NESTED = 1024**3
#: The most of a gzip, bzip2 or xz file inside the package that is decompressed to see how far
#: it expands, and the most of all of them together.
PROBE_BYTES = 128 * 1024**2
PROBE_BUDGET = 2 * 1024**3
PROBE_FLOOR = 32 * 1024**2
FREE_MARGIN = 64 * 1024**2

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
    the archive's single wrapper folder). ``source`` is what the user gave: a
    path, or a link (``remote``), in which case ``label`` is the repository's name.
    """

    source: str
    root: Path
    base: str = ""
    archive: bool = False
    skipped: tuple[str, ...] = ()
    label: str = ""
    remote: bool = False
    _cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def base_dir(self) -> Path:
        """The folder the package's structure starts in."""
        return self.root / self.base if self.base else self.root

    @property
    def name(self) -> str:
        """A name for the package: the repository's, the wrapper folder's, else the folder's or archive's."""
        if self.label:
            return self.label
        if self.base:
            return PurePosixPath(self.base).name
        if not self.archive:
            return self.root.name  # resolved, so "." gives the folder's own name
        name = Path(self.source).name
        low = name.lower()
        for suffix in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
            if low.endswith(suffix):
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


@dataclass(frozen=True)
class _Limits:
    """The limits of one extraction (see :func:`open_package`)."""

    max_bytes: int = MAX_BYTES
    max_files: int = MAX_FILES
    max_ratio: float = MAX_RATIO
    max_depth: int = MAX_DEPTH

    def too_many(self) -> PackageError:
        return PackageError(f"The archive holds more than {self.max_files:,} files")

    def too_big(self) -> PackageError:
        return PackageError(f"The archive is bigger than {_size(self.max_bytes)} when extracted")

    def bomb(self, what: str, produced: int, packed: int) -> PackageError:
        return PackageError(
            f"{what} expands to {_size(produced)} from {_size(packed)}, more than "
            f"{self.max_ratio:g} times its own size. That is what a zip bomb looks like, so it "
            "was not unpacked. If it is a real package, give its folder instead."
        )

    def over_ratio(self, produced: int, packed: int) -> bool:
        """Whether *produced* bytes from *packed* is a zip bomb's ratio (and large enough to matter)."""
        return produced > RATIO_FLOOR and produced > self.max_ratio * max(packed, 1)

    @property
    def nested_bytes(self) -> int:
        return min(self.max_bytes, MAX_NESTED)


def _size(n: float) -> str:
    """A byte count for a message: ``512 B``, ``3.2 MB``, ``20 GB``."""
    for unit, step in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= step:
            value = n / step
            return (
                f"{value:.0f} {unit}"
                if value >= 10
                else f"{value:.1f}".removesuffix(".0") + f" {unit}"
            )
    return f"{int(n)} B"


@contextlib.contextmanager
def open_package(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_BYTES,
    max_files: int = MAX_FILES,
    max_ratio: float = MAX_RATIO,
    max_depth: int = MAX_DEPTH,
) -> Iterator[OpenedPackage]:
    """Open the folder or archive at *path* as a data package.

    A folder is used in place. An archive (:data:`ARCHIVE_SUFFIXES`) is
    extracted to a private temporary folder that is removed when the ``with``
    block ends. Raises :class:`PackageError` for a missing path, a file that is
    not a supported archive, or an archive over the limits: more than
    *max_files* members, more than *max_bytes* once extracted, more than
    *max_ratio* times its own size once extracted (a zip bomb; small archives
    are exempt), or members more than *max_depth* folders deep (those are
    skipped, not refused). A file inside the archive that is a zip container or
    a compressed stream is held to the same limits.
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
    limits = _Limits(max_bytes, max_files, max_ratio, max_depth)
    tmp = Path(tempfile.mkdtemp(prefix="metacheck-package-"))
    try:
        dest = tmp / "package"
        dest.mkdir()
        skipped = _extract(p, dest, limits)
        _scan_nested(dest, limits)
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


def _unpackable(rel: str, limits: _Limits) -> bool:
    """Whether a member's path is one to unpack: not too long, not too deep, no control characters.

    Such a path is what a file system refuses or a program trips over, so the member is skipped
    and listed, like an unsafe one.
    """
    parts = rel.split("/")
    if len(parts) > limits.max_depth or len(rel.encode("utf-8", "replace")) > MAX_PATH:
        return False
    if any(len(part.encode("utf-8", "replace")) > MAX_NAME for part in parts):
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in rel)


def _extract(archive: Path, dest: Path, limits: _Limits) -> list[str]:
    low = archive.name.lower()
    try:
        if low.endswith(".zip"):
            return _extract_zip(archive, dest, limits)
        return _extract_tar(archive, dest, limits)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, lzma.LZMAError, zlib.error) as exc:
        raise PackageError(f"{archive.name} cannot be read as an archive: {exc}") from exc
    except NotImplementedError as exc:  # a zip compression method the standard library lacks
        raise PackageError(
            f"{archive.name} uses a compression method that is not supported"
        ) from exc
    except OSError as exc:  # no space left, a name the file system refuses, a name used twice
        raise PackageError(f"{archive.name} cannot be unpacked: {exc.strerror or exc}") from exc


def _check_space(dest: Path, needed: int) -> None:
    """Refuse an archive that cannot fit: better now than half way through."""
    try:
        free = shutil.disk_usage(dest).free
    except OSError:
        return
    if needed + FREE_MARGIN > free:
        raise PackageError(
            f"The archive expands to {_size(needed)}, and there is only {_size(free)} of free "
            "space to unpack it in"
        )


def _extract_zip(archive: Path, dest: Path, limits: _Limits) -> list[str]:
    from metacheck.archives.zip_peek import _zip_name

    skipped: list[str] = []
    packed = archive.stat().st_size
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > limits.max_files:
            raise limits.too_many()
        declared = sum(i.file_size for i in infos)
        if declared > limits.max_bytes:
            raise limits.too_big()
        if limits.over_ratio(declared, packed):
            raise limits.bomb("The archive", declared, packed)
        if any(i.flag_bits & 0x1 for i in infos):
            raise PackageError(
                "The archive is password-protected, and an encrypted package cannot be checked. "
                "Give an unencrypted copy."
            )
        _check_space(dest, declared)
        written = 0
        seen: set[str] = set()
        for info in infos:
            name = _zip_name(info)
            rel = _safe_relpath(name)
            mode = info.external_attr >> 16
            if rel is None or stat.S_ISLNK(mode) or not _unpackable(rel, limits) or rel in seen:
                skipped.append(name)
                continue
            seen.add(rel)
            out = dest / rel
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                # the declared sizes were checked; count what is really written too
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > limits.max_bytes:
                        raise limits.too_big()
                    dst.write(chunk)
    return skipped


class _Counting:
    """A file that counts the bytes read from it: how much of a compressed tar is used so far."""

    def __init__(self, raw: IO[bytes]) -> None:
        self.raw = raw
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.count += len(data)
        return data

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)


def _extract_tar(archive: Path, dest: Path, limits: _Limits) -> list[str]:
    """Unpack a tar as a stream, counting members, bytes and the ratio as they go by."""
    skipped: list[str] = []
    _check_space(dest, 0)
    members = 0
    declared = 0
    written = 0
    seen: set[str] = set()
    with open(archive, "rb") as raw:
        counting = _Counting(raw)
        with tarfile.open(fileobj=counting, mode="r|*") as tf:  # type: ignore[call-overload]
            for m in tf:
                members += 1
                if members > limits.max_files:
                    raise limits.too_many()
                rel = _safe_relpath(m.name)
                if (
                    rel is None
                    or not (m.isfile() or m.isdir())
                    or not _unpackable(rel, limits)
                    or rel in seen
                ):
                    skipped.append(m.name)
                    continue
                seen.add(rel)
                out = dest / rel
                if m.isdir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                declared += m.size  # a sparse file's size is what it expands to
                if declared > limits.max_bytes:
                    raise limits.too_big()
                out.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    skipped.append(m.name)
                    continue
                with src, open(out, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        written += len(chunk)
                        if written > limits.max_bytes:
                            raise limits.too_big()
                        if limits.over_ratio(written, counting.count):
                            raise limits.bomb("The archive", written, counting.count)
                        dst.write(chunk)
        _check_space(dest, 0)
    return skipped


# -- what is inside the package -------------------------------------------------------------


def _scan_nested(root: Path, limits: _Limits) -> None:
    """Refuse a package that holds a nested bomb: a file that expands past the limits when the checks read it.

    The checks open a file inside the package as a zip when it is an ``.xlsx``, ``.ods`` or
    ``.docx`` (they read its parts into memory), and as a gzip, bzip2 or xz stream when it is an
    ``.rds`` or ``.RData``. A small file of either kind can expand to many gigabytes, so the
    extractor looks at them, by what they start with (not by their name): the sizes a zip says
    it holds are read from its listing (nothing is unpacked), and a stream is decompressed up to
    ``PROBE_BYTES``, and no more than ``PROBE_BUDGET`` for all of them. Only a package that was
    unpacked from an archive is looked at: a folder is the person's own.
    """
    budget = PROBE_BUDGET
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            try:
                packed = path.stat().st_size
                if packed < 18:
                    continue
                with open(path, "rb") as fh:
                    head = fh.read(6)
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
                _check_nested_zip(path, rel, packed, limits)
            elif budget > 0 and (
                head[:2] == b"\x1f\x8b" or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00"
            ):
                budget -= _probe_stream(path, rel, head, limits)


def _check_nested_zip(path: Path, rel: str, packed: int, limits: _Limits) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, EOFError):
        return  # the checks that read it say it is damaged
    declared = sum(i.file_size for i in infos)
    if len(infos) > limits.max_files:
        raise PackageError(f"{rel} is itself an archive with more than {limits.max_files:,} files")
    if declared > limits.nested_bytes:
        raise PackageError(
            f"{rel} is itself an archive that expands to {_size(declared)}, more than the "
            f"{_size(limits.nested_bytes)} the checks open, so the package was not checked. "
            "Give the package's folder instead."
        )
    if limits.over_ratio(declared, packed):
        raise limits.bomb(rel, declared, packed)


def _probe_stream(path: Path, rel: str, head: bytes, limits: _Limits) -> int:
    """Decompress the start of a gzip, bzip2 or xz file; the bytes that came out."""

    def opener(stream: Any) -> Any:
        if head[:2] == b"\x1f\x8b":
            return gzip.GzipFile(fileobj=stream)
        return bz2.BZ2File(stream) if head[:3] == b"BZh" else lzma.LZMAFile(stream)

    produced = 0
    try:
        with open(path, "rb") as raw:
            counting = _Counting(raw)
            with opener(counting) as stream:
                while produced < PROBE_BYTES and (chunk := stream.read(1 << 20)):
                    produced += len(chunk)
                    if produced > PROBE_FLOOR and produced > limits.max_ratio * max(
                        counting.count, 1
                    ):
                        raise limits.bomb(rel, produced, counting.count)
    except (OSError, EOFError, lzma.LZMAError, zlib.error):
        pass  # a damaged stream: the checks that read it say so
    return produced
