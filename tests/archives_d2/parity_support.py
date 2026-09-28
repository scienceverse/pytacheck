"""Python side of the ``archives_d2`` parity cases that need HTTP mocks or files on disk.

The R side of those cases runs metacheck inside ``httptest2::with_mock_dir()``
(on ``tests/archives_d2/mocks`` -- see ``make_mocks.py`` -- or on metacheck's
own ``apis`` recordings for PsychArchives and ResearchBox) with ``online()``
mocked to ``TRUE``; the harness replays the same directory for Python
(``mock_dir``) and :func:`run` patches ``online()`` the same way, points the
metacheck caches at a temporary directory and switches courtesy delays off.

Case code receives a namespace ``m`` with the archive modules (``m.dataone``,
``m.dspace7``, ``m.fourtu``, ``m.psycharchives``, ``m.researchbox``,
``m.reshare``, ``m.mendeley``, ``m.fsd``), ``m.pc`` (pytacheck), ``m.pd``
(pandas), :func:`tmp`, :func:`offline` and :func:`repo_cache`.
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
def online_and_no_sleep(value: bool = True) -> Iterator[None]:
    """``online()`` reports every host as reachable (or, with *value* False, none)."""
    from pytacheck import utils

    saved = utils.online
    utils.online = lambda *args, **kwargs: value  # type: ignore[assignment]
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


def offline(fn: Callable[[], Any]) -> Any:
    """``fn()`` with ``online()`` reporting every host as unreachable."""
    with online_and_no_sleep(False):
        return fn()


def repo_cache(files: dict[str, str], fn: Callable[[str], Any]) -> Any:
    """``fn(dir)`` with the repository file cache in a temporary *dir* holding *files*.

    *files* maps paths relative to the cache (``"researchbox.org_801/unzipped/x.csv"``)
    to text written as R's ``writeLines()`` does.
    """
    from pytacheck.utils import local_options

    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            p = Path(d) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text + "\n", encoding="utf-8")
        with local_options({"metacheck.repo_cache.dir": d}):
            return fn(d)


def _namespace() -> types.SimpleNamespace:
    import pandas as pd

    import pytacheck as pc
    from pytacheck.archives import (
        dataone,
        dspace7,
        fourtu,
        fsd,
        mendeley,
        psycharchives,
        researchbox,
        reshare,
    )

    return types.SimpleNamespace(
        dataone=dataone,
        dspace7=dspace7,
        fourtu=fourtu,
        fsd=fsd,
        mendeley=mendeley,
        psycharchives=psycharchives,
        researchbox=researchbox,
        reshare=reshare,
        pc=pc,
        pd=pd,
        tmp=tmp,
        offline=offline,
        repo_cache=repo_cache,
    )


def run(x: Callable[..., Any]) -> Any:
    """Call *x* (with the module namespace if it takes an argument), ``online()`` mocked.

    The metacheck caches live in a temporary directory for the call (the R
    runner does the same with ``metacheck.cache.dir``).
    """
    from pytacheck.utils import local_options

    with tempfile.TemporaryDirectory() as cache, local_options({"metacheck.cache.dir": cache}):
        with online_and_no_sleep():
            if inspect.signature(x).parameters:
                return x(_namespace())
            return x()
