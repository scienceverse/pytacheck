"""Fixtures for the io tests: recorded API replay plus ad-hoc routes."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from tests.httpmock import UPSTREAM_TESTS, fixture_response, mock_path

HERE = Path(__file__).resolve().parent
IO_FIXTURES = HERE / "fixtures"
GROBID_URL = "https://grobid.hti.ieis.tue.nl"
SERVERS_URL = "https://www.scienceverse.org/metacheck/convert.json"


@contextlib.contextmanager
def api(
    *mock_dirs: str,
    routes: Mapping[tuple[str, str], Callable[[httpx.Request], httpx.Response]] | None = None,
) -> Iterator[respx.MockRouter]:
    """Serve ``routes`` first, then metacheck's recorded responses (404 otherwise)."""
    roots = [UPSTREAM_TESTS / d for d in mock_dirs or ("apis",)]

    def replay(request: httpx.Request) -> httpx.Response:
        path = mock_path(request)
        for root in roots:
            resp = fixture_response(root, path)
            if resp is not None:
                return resp
        return httpx.Response(404, json={"error": f"no recorded fixture for {path}"})

    with respx.mock(assert_all_called=False) as router:
        for (method, url), side_effect in (routes or {}).items():
            router.route(method=method, url=url).mock(side_effect=side_effect)
        router.route().mock(side_effect=replay)
        yield router


def json_response(body: Any, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(status, json=body)


@pytest.fixture
def io_fixtures() -> Path:
    return IO_FIXTURES


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the network is up (``online()`` does a DNS lookup)."""
    import pytacheck.utils

    monkeypatch.setattr(pytacheck.utils, "online", lambda *a, **k: True)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary corpus cache directory."""
    import pytacheck.io.corpus

    d = tmp_path / "papers"
    d.mkdir()
    monkeypatch.setattr(pytacheck.io.corpus, "_papers_cache_dir", lambda: d)
    return d
