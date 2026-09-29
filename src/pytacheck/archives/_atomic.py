"""Atomic file and folder writes for the shared download cache."""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import shutil
import time
from collections.abc import Iterator
from typing import BinaryIO

# a temporary name: ``.~`` and 4 to 12 hex digits (see :func:`_temp_name`)
_TEMP_NAME = re.compile(r"^\.~[0-9a-f]{4,12}$")
_TRIES = 5


def _temp_name(final: str) -> str:
    """A temporary name for *final*: ``.~<hex>``, as short as its base name allows.

    The hex part is two characters shorter than the base name of *final*, but
    at least 4 and at most 12 long, so the name is no longer than the base name
    of *final* except for a very short one (a long cache path must not grow:
    Windows stops at 260 characters by default).
    """
    n = min(12, max(4, len(os.path.basename(final)) - 2))
    return f".~{secrets.token_hex(6)[:n]}"


def _named(e: OSError, final: str) -> OSError:
    """*e* again, naming *final* (not a temporary path) and keeping errno and strerror."""
    return OSError(e.errno, e.strerror, final)  # maps to the matching subclass


def _replace(src: str, dst: str) -> None:
    """``os.replace``; on Windows retried for about a second while another process holds a file."""
    for attempt in range(_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _TRIES - 1:
                raise
            time.sleep(0.2)


@contextlib.contextmanager
def atomic_write(path: str | bytes) -> Iterator[BinaryIO]:
    """Open *path* for binary writing; it appears only once the write completed.

    The bytes go to a temporary file beside *path* and are moved onto it on
    success. Any exception (a Stop request is not an ``Exception``) deletes the
    temporary file and re-raises, so an interrupted run never leaves a partial
    file at *path* for a later run to reuse, and two writers never interleave.
    An ``OSError`` names *path*, not the temporary file.
    """
    final = os.fsdecode(path)
    if os.path.isdir(final):  # open(path, "wb") refuses a folder; so must this
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), final)
    folder = os.path.dirname(final)
    tmp = ""
    for _ in range(_TRIES):
        tmp = os.path.join(folder, _temp_name(final))
        try:
            fh = open(tmp, "xb")  # noqa: SIM115 - closed below
            break
        except FileExistsError:
            continue
        except OSError as e:
            raise _named(e, final) from None
    else:
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), final)
    try:
        yield fh
        fh.close()
        try:
            _replace(tmp, final)
        except OSError as e:
            raise _named(e, final) from None
    except BaseException:
        fh.close()
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def staged_dir(dest: str) -> Iterator[str]:
    """Yield a new folder beside *dest* to be filled; it becomes *dest* when the block ends.

    * normal end: the folder is moved to *dest*;
    * an ordinary ``Exception``: it is moved to *dest* all the same (a failed
      extraction keeps what it wrote, as R does), then the error is re-raised;
    * any other ``BaseException`` (a Stop request, ``KeyboardInterrupt``): the
      folder is deleted and the error re-raised, so nothing is at *dest*;
    * if *dest* exists by then (another process finished first) it is kept and
      the folder is deleted.

    An ``OSError`` from making the folder names *dest*.
    """
    dest = os.fsdecode(dest)
    folder = os.path.dirname(dest)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = ""
    for _ in range(_TRIES):
        tmp = os.path.join(folder, _temp_name(dest))
        try:
            os.mkdir(tmp)
            break
        except FileExistsError:
            continue
        except OSError as e:
            raise _named(e, dest) from None
    else:
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dest)
    try:
        yield tmp
    except Exception:
        _move_into_place(tmp, dest)
        raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    else:
        _move_into_place(tmp, dest)


def _move_into_place(tmp: str, dest: str) -> None:
    try:
        os.rename(tmp, dest)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if not os.path.exists(dest):  # not a lost race: the move itself failed
            raise _named(e, dest) from None


def sweep_stale(root: str, max_age_s: float) -> None:
    """Remove temporary files and folders under *root* older than *max_age_s* seconds.

    A run that was killed mid-write leaves one behind. Only names that match the
    temporary pattern exactly are touched; errors are ignored.
    """
    cutoff = time.time() - max_age_s
    try:
        for dirpath, dirs, files in os.walk(root):
            for name in list(dirs):
                if _TEMP_NAME.match(name):
                    dirs.remove(name)  # removed whole, not walked
                    p = os.path.join(dirpath, name)
                    with contextlib.suppress(OSError):
                        if os.lstat(p).st_mtime < cutoff:
                            shutil.rmtree(p, ignore_errors=True)
            for name in files:
                if _TEMP_NAME.match(name):
                    p = os.path.join(dirpath, name)
                    with contextlib.suppress(OSError):
                        if os.lstat(p).st_mtime < cutoff:
                            os.unlink(p)
    except Exception:  # noqa: S110 - a sweep must never fail a run
        pass
