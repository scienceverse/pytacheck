"""Python side of the ``archives_osf_review`` parity cases.

The R side of those cases runs metacheck inside ``httptest2::with_mock_dir()``
with metacheck's test redactor (``page[size]=100`` stripped) and
``TESTTHAT=true`` (so ``osf_get_all_pages()`` fetches later pages one by one
through the mock). :func:`run_mocked` sets up the same thing for pytacheck:
recorded responses (unrecorded requests fail, as under httptest2), no session
listing cache, no token, no sleeping.

``filetype()`` (``R/file_category.R``) belongs to another work item; until
``pytacheck.fileinfo.category`` exists a stub stands in and the cases ignore
the ``filetype`` column.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

UPSTREAM_APIS = (
    Path(__file__).resolve().parents[2] / "upstream" / "metacheck" / "tests" / "testthat" / "apis"
)


@contextlib.contextmanager
def _filetype_stub() -> Iterator[None]:
    try:
        __import__("pytacheck.fileinfo.category")
    except ImportError:
        pass
    else:
        yield
        return
    pkg = types.ModuleType("pytacheck.fileinfo")
    pkg.__path__ = []  # type: ignore[attr-defined]
    mod = types.ModuleType("pytacheck.fileinfo.category")
    mod.filetype = lambda filename: [None for _ in filename]  # type: ignore[attr-defined]
    saved = {k: sys.modules.get(k) for k in ("pytacheck.fileinfo", "pytacheck.fileinfo.category")}
    sys.modules["pytacheck.fileinfo"] = pkg
    sys.modules["pytacheck.fileinfo.category"] = mod
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@contextlib.contextmanager
def _env(**values: str) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_mocked(x: Callable[[], Any]) -> Any:
    """Call *x* against metacheck's recorded API responses (see module docstring)."""
    from pytacheck import utils
    from tests.archives_osf.osfmock import replay_osf

    with (
        tempfile.TemporaryDirectory() as tmp,
        _env(OSF_PAT="", PYTACHECK_NO_SLEEP="1"),
        utils.local_options(
            {
                "metacheck.osf.cache": False,
                "metacheck.osf.pat": None,
                "metacheck.osf.delay": 0,
                "metacheck.cache.dir": tmp,
            }
        ),
        _filetype_stub(),
        replay_osf(missing="error"),
    ):
        return x()


def run(x: Callable[[], Any]) -> Any:
    """Call *x* with the ``filetype()`` stub in place (no HTTP)."""
    with _filetype_stub():
        return x()


def fixture(path: str) -> Any:
    """The recorded httr2 response ``apis/<path>`` (R: ``source(<path>.R)$value``)."""
    from tests.httpmock import fixture_response

    return fixture_response(UPSTREAM_APIS, path)
