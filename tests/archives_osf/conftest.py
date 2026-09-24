"""Fixtures for the OSF / AsPredicted / local-archive tests.

metacheck's OSF fixtures were recorded before listing requests carried
``page[size]=100``; its test helper installs an httptest2 *redactor* that
strips that parameter before computing the mock file name. :func:`replay_osf`
does the same on top of :mod:`tests.httpmock`, and also serves recorded file
downloads whose body httptest2 stored in a separate ``.R-FILE``.
"""

from __future__ import annotations

import contextlib
import importlib
import re
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from tests.httpmock import UPSTREAM_TESTS, fixture_response, r_digest

_REDACT = re.compile(r"[?&]page(%5[Bb]size%5[Dd]|\[size\])=100")
_FILE_REF = re.compile(r'find_mock_file\("([^"]+)"\)')


def osf_mock_path(request: httpx.Request) -> str:
    """``httptest2::build_mock_url()`` after metacheck's ``page[size]`` redactor."""
    url = _REDACT.sub("", str(request.url))
    url = re.sub(r"^.*?://", "", url, count=1)
    base, _, query = url.partition("?")
    path = re.sub(r"/$", "", base).replace(":", "-")
    if query:
        path += "-" + r_digest(query)[:6]
    body = request.content
    if body:
        path += "-" + r_digest(body.decode("utf-8", "replace"))[:6]
    if request.method != "GET":
        path += "-" + request.method
    return path


def _response(root: Path, path: str) -> httpx.Response | None:
    resp = fixture_response(root, path)
    if resp is None:
        return None
    r_file = root / f"{path}.R"
    if r_file.exists():
        ref = _FILE_REF.search(r_file.read_text(encoding="utf-8"))
        if ref and (root / ref.group(1)).exists():
            return httpx.Response(
                resp.status_code, headers=resp.headers, content=(root / ref.group(1)).read_bytes()
            )
    return resp


@contextlib.contextmanager
def replay_osf(*mock_dirs: str) -> Iterator[respx.MockRouter]:
    """Replay metacheck's recorded API responses, as its OSF tests do."""
    roots = [UPSTREAM_TESTS / d for d in (mock_dirs or ("apis",))]

    def handler(request: httpx.Request) -> httpx.Response:
        path = osf_mock_path(request)
        for root in roots:
            resp = _response(root, path)
            if resp is not None:
                return resp
        host = urlsplit(str(request.url)).hostname
        return httpx.Response(404, json={"error": f"no recorded fixture for {path} ({host})"})

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=handler)
        yield router


@pytest.fixture(autouse=True)
def _osf_test_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """metacheck's test setup: no session listing cache, no token, caches in a temp dir."""
    from pytacheck import utils

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
    """Make ``pytacheck.fileinfo.category.filetype`` importable.

    The real port belongs to another work item; until it exists a small
    extension-map stub stands in (returns ``False`` so tests can skip
    assertions about exact types).
    """
    try:
        importlib.import_module("pytacheck.fileinfo.category")
        return True
    except ImportError:
        pass
    pkg = types.ModuleType("pytacheck.fileinfo")
    pkg.__path__ = []  # type: ignore[attr-defined]
    mod = types.ModuleType("pytacheck.fileinfo.category")

    def filetype(filename: list[str]) -> list[str]:
        out = []
        for name in filename:
            ext = str(name).rsplit(".", 1)[-1].lower()
            out.append(_STUB_TYPES.get(ext, "NA"))
        return out

    mod.filetype = filetype  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pytacheck.fileinfo", pkg)
    monkeypatch.setitem(sys.modules, "pytacheck.fileinfo.category", mod)
    return False
