"""Atomic file writes for the shared download cache."""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
from collections.abc import Iterator
from typing import BinaryIO


@contextlib.contextmanager
def atomic_write(path: str | bytes) -> Iterator[BinaryIO]:
    """Open *path* for binary writing; it appears only once the write completed.

    The bytes go to a temporary file beside *path* and are moved onto it on
    success. Any exception (a Stop request is not an ``Exception``) deletes the
    temporary file and re-raises, so an interrupted run never leaves a partial
    file at *path* for a later run to reuse, and two writers never interleave.
    """
    final = os.fsdecode(path)
    if os.path.isdir(final):  # open(path, "wb") refuses a folder; so must this
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), final)
    # a short name of its own, so a long cache path gets no longer (Windows
    # stops at 260 characters)
    tmp = os.path.join(os.path.dirname(final), f".{secrets.token_hex(6)}.part")
    fh = open(tmp, "xb")  # noqa: SIM115 - closed below
    try:
        yield fh
        fh.close()
        os.replace(tmp, final)
    except BaseException:
        fh.close()
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
