"""Python side of the ``archives_d1`` parity cases that need HTTP mocks or files on disk.

The R side of those cases runs metacheck inside ``httptest2::with_mock_dir()``
on ``tests/archives_d1/mocks`` (httptest2-format recorded responses written
for these tests: metacheck records none for Dryad, Figshare or Dataverse),
with ``online()`` mocked to ``TRUE``; the harness replays the same directory
for Python (``mock_dir``) and :func:`run` patches ``online()`` the same way.

Case code receives a namespace ``m`` with the three archive modules
(``m.dryad``, ``m.figshare``, ``m.dataverse``) and :func:`tmp`.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import tempfile
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

MOCKS = Path(__file__).resolve().parent / "mocks"


@contextlib.contextmanager
def online_and_no_sleep() -> Iterator[None]:
    """``online()`` reports every host as reachable; courtesy delays are skipped."""
    from pytacheck import utils

    saved = utils.online
    utils.online = lambda *args, **kwargs: True  # type: ignore[assignment]
    old = os.environ.get("PYTACHECK_NO_SLEEP")
    os.environ["PYTACHECK_NO_SLEEP"] = "1"
    try:
        yield
    finally:
        utils.online = saved  # type: ignore[assignment]
        if old is None:
            os.environ.pop("PYTACHECK_NO_SLEEP", None)
        else:
            os.environ["PYTACHECK_NO_SLEEP"] = old


def tmp(fn: Callable[[str], Any]) -> Any:
    """``fn(dir)`` with a fresh, existing temporary directory (R: ``tempfile(); dir.create()``)."""
    with tempfile.TemporaryDirectory() as d:
        return fn(d)


def write_files(folder: str, files: dict[str, str]) -> str:
    """Write ``{relative path: text}`` under *folder* (R: ``writeLines(text, path)``)."""
    for rel, text in files.items():
        p = Path(folder) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    return folder


def _namespace() -> types.SimpleNamespace:
    from pytacheck.archives import dataverse, dryad, figshare

    return types.SimpleNamespace(
        dryad=dryad, figshare=figshare, dataverse=dataverse, tmp=tmp, write_files=write_files
    )


def run(x: Callable[..., Any]) -> Any:
    """Call *x* (with the module namespace if it takes an argument), ``online()`` mocked."""
    with online_and_no_sleep():
        if len(inspect.signature(x).parameters) == 1:
            return x(_namespace())
        return x()
