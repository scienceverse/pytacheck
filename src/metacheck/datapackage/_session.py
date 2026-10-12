"""Share one opened package between the checks of a run.

:func:`~metacheck.datapackage.check_package` opens the package once and runs
every check inside :func:`using_package`. Each check's ``local_path`` argument
names the package's base folder, and :func:`package_for` hands back the opened
package, with its listings already made and the files next to an archive's
wrapper folder included. A check run on its own (``local_path`` naming a
folder or an archive) opens the package itself.
"""

from __future__ import annotations

import atexit
import contextlib
import os
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from metacheck.datapackage._open import OpenedPackage, PackageError, is_archive, open_package

__all__ = ["package_for", "using_package"]

_ACTIVE: ContextVar[dict[str, OpenedPackage] | None] = ContextVar(
    "metacheck_datapackage_active", default=None
)

#: Archives opened by a check run on its own, kept until the process ends.
_OPENED: dict[tuple[str, float], OpenedPackage] = {}
_STACK = contextlib.ExitStack()
atexit.register(_STACK.close)


def _key(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.path.expanduser(os.fspath(path)))


@contextlib.contextmanager
def using_package(opened: OpenedPackage) -> Iterator[OpenedPackage]:
    """Make *opened* the package that :func:`package_for` returns for its base folder."""
    active = dict(_ACTIVE.get() or {})
    active[_key(opened.base_dir)] = opened
    token = _ACTIVE.set(active)
    try:
        yield opened
    finally:
        _ACTIVE.reset(token)


def package_for(local_path: Any) -> OpenedPackage | None:
    """The data package a check's ``local_path`` argument names, or ``None``.

    ``None`` (no package given) gives ``None``. A list or tuple must hold one
    path. A folder or archive that does not exist raises
    :class:`~metacheck.datapackage.PackageError`.
    """
    if local_path is None:
        return None
    if isinstance(local_path, list | tuple):
        if len(local_path) != 1:
            raise PackageError("Give one data package (a folder or an archive)")
        local_path = local_path[0]
    key = _key(local_path)
    active = _ACTIVE.get() or {}
    if key in active:
        return active[key]
    p = Path(key)
    if p.is_dir():
        return OpenedPackage(source=os.fspath(local_path), root=p)
    if not p.exists():
        raise PackageError(f"No folder or archive found at {os.fspath(local_path)}")
    if not is_archive(p):
        raise PackageError(f"{p.name} is neither a folder nor an archive")
    stamp = (key, p.stat().st_mtime)
    if stamp not in _OPENED:
        _OPENED[stamp] = _STACK.enter_context(open_package(p))
    return _OPENED[stamp]
