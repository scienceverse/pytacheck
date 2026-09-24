"""Fixtures for the Dryad / Figshare / Dataverse tests.

metacheck records no API responses for these archives, so ``mocks/`` holds
httptest2-format responses written for these tests (see ``make_mocks.py``);
the parity cases replay the same directory on the R side.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import respx

from tests.httpmock import replay

MOCKS = Path(__file__).resolve().parent / "mocks"

_TOKEN_ENV = ("DRYAD_PAT", "DRYAD_CLIENT_ID", "DRYAD_CLIENT_SECRET", "FIGSHARE_PAT")
_TOKEN_OPTIONS = (
    "metacheck.dryad.pat",
    "metacheck.dryad.client_id",
    "metacheck.dryad.client_secret",
    "metacheck.dryad.token_url",
    "metacheck.figshare.pat",
)


@pytest.fixture(autouse=True)
def _archive_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """No tokens from the environment; caches in a temp dir; no cached OAuth tokens."""
    from pytacheck import utils
    from pytacheck.archives import dryad

    for var in _TOKEN_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dryad, "_TOKENS", {})
    values = dict.fromkeys(_TOKEN_OPTIONS)
    values["metacheck.cache.dir"] = str(tmp_path / "cache")
    with utils.local_options(values):
        yield


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    """``online()`` reports every host as reachable (no DNS lookups)."""
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: True)


@pytest.fixture
def mock_api(online: None) -> Iterator[respx.MockRouter]:
    """Replay ``tests/archives_d1/mocks`` (unrecorded requests get a 404)."""
    with replay(MOCKS) as router:
        yield router


@pytest.fixture
def serve() -> Iterator[Callable[[dict[str, httpx.Response]], list[httpx.Request]]]:
    """``serve({url: response})``: answer those URLs, 404 for anything else.

    Returns the list of requests made (filled as the test runs).
    """
    with respx.mock(assert_all_called=False) as router:
        responses: dict[str, httpx.Response] = {}
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            resp = responses.get(str(request.url))
            return resp if resp is not None else httpx.Response(404)

        router.route().mock(side_effect=handler)

        def install(mapping: dict[str, httpx.Response]) -> list[httpx.Request]:
            responses.update(mapping)
            return requests

        yield install
