"""Fixtures for the OSF / AsPredicted / local-archive tests.

Recorded OSF responses are replayed with :func:`tests.archives_osf.osfmock.replay_osf`
(metacheck's ``page[size]`` redactor plus ``.R-FILE`` bodies).
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from tests.archives_osf.osfmock import osf_mock_path, replay_osf

__all__ = ["osf_mock_path", "replay_osf"]


@pytest.fixture(autouse=True)
def _osf_test_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """metacheck's test setup: no session listing cache, no token, caches in a temp dir."""
    from metacheck import utils

    monkeypatch.setenv("OSF_PAT", "")
    with utils.local_options(
        {
            "metacheck.osf.cache": False,
            "metacheck.osf.pat": None,
            "metacheck.osf.delay": 0,
            "metacheck.cache.dir": str(tmp_path / "cache"),
        }
    ):
        yield


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with replay_osf() as router:
        yield router


_STUB_TYPES = {
    "r": "code", "py": "code", "sas": "code", "do": "code", "rmd": "code", "qmd": "code",
    "csv": "data", "tsv": "data", "xlsx": "data", "xls": "data", "sav": "data", "rds": "data",
    "txt": "text", "md": "text", "pdf": "text", "docx": "text", "png": "image", "jpg": "image",
}  # fmt: skip


@pytest.fixture
def filetype_available(monkeypatch: pytest.MonkeyPatch) -> bool:
    """Make ``metacheck.fileinfo.category.filetype`` importable.

    The real port belongs to another work item; until it exists a small
    extension-map stub stands in (returns ``False`` so tests can skip
    assertions about exact types).
    """
    try:
        importlib.import_module("metacheck.fileinfo.category")
        return True
    except ImportError:
        pass
    pkg = types.ModuleType("metacheck.fileinfo")
    pkg.__path__ = []  # type: ignore[attr-defined]
    mod = types.ModuleType("metacheck.fileinfo.category")

    def filetype(filename: list[str]) -> list[str]:
        out = []
        for name in filename:
            ext = str(name).rsplit(".", 1)[-1].lower()
            out.append(_STUB_TYPES.get(ext, "NA"))
        return out

    mod.filetype = filetype  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "metacheck.fileinfo", pkg)
    monkeypatch.setitem(sys.modules, "metacheck.fileinfo.category", mod)
    return False


def _stub_download_many_parallel(urls, dests, expected_size=float("nan")):  # type: ignore[no-untyped-def]
    """Minimal stand-in for ``metacheck.archives.download._download_many_parallel``."""
    import math
    import os

    from metacheck import http

    sizes = expected_size if isinstance(expected_size, list) else [expected_size] * len(urls)
    errs: list[str | None] = []
    for url, dest, exp in zip(urls, dests, sizes, strict=True):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        resp = http.request("GET", url, max_tries=1)
        if resp is None:
            errs.append("download failed")
            continue
        if resp.status_code != 200:
            errs.append(f"HTTP {resp.status_code}")
            continue
        with open(dest, "wb") as fh:
            fh.write(resp.content)
        if exp is not None and not (isinstance(exp, float) and math.isnan(exp)) and exp > 0:
            got = os.path.getsize(dest)
            if got != exp:
                os.remove(dest)
                errs.append(f"truncated ({got:.0f} of {exp:.0f} bytes)")
                continue
        errs.append(None)
    return errs


@pytest.fixture
def download_available(monkeypatch: pytest.MonkeyPatch) -> bool:
    """Make ``metacheck.archives.download._download_many_parallel`` importable.

    Uses the real port when it exists; otherwise a small sequential stand-in
    (returns ``False``).
    """
    try:
        mod = importlib.import_module("metacheck.archives.download")
        if hasattr(mod, "_download_many_parallel"):
            return True
    except ImportError:
        pass
    stub = types.ModuleType("metacheck.archives.download")
    stub._download_many_parallel = _stub_download_many_parallel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "metacheck.archives.download", stub)
    return False
