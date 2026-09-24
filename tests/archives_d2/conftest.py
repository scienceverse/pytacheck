"""Fixtures for the DataONE / DSpace 7 / 4TU / PsychArchives / ResearchBox / ReShare /
Mendeley / FSD tests.

``mocks/`` holds httptest2-format responses written for these tests (see
``make_mocks.py``); metacheck's own ``apis`` recordings cover PsychArchives
and ResearchBox. The parity cases replay the same directories on the R side.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import respx

from tests.httpmock import replay

MOCKS = Path(__file__).resolve().parent / "mocks"

_TOKEN_ENV = ("RESEARCHDATA4TU_PAT", "FIGSHARE_PAT")
_TOKEN_OPTIONS = ("metacheck.researchdata4tu.pat", "metacheck.figshare.pat")


@pytest.fixture(autouse=True)
def _archive_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """No tokens from the environment; caches in a temp dir; no courtesy delays."""
    from pytacheck import utils

    for var in _TOKEN_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PYTACHECK_NO_SLEEP", "1")
    values: dict[str, object] = dict.fromkeys(_TOKEN_OPTIONS)
    values["metacheck.cache.dir"] = str(tmp_path / "cache")
    values["metacheck.repo_cache.dir"] = str(tmp_path / "repo_cache")
    with utils.local_options(values):
        yield


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    """``online()`` reports every host as reachable (no DNS lookups)."""
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: True)


@pytest.fixture
def mock_api(online: None) -> Iterator[respx.MockRouter]:
    """Replay ``tests/archives_d2/mocks`` (unrecorded requests get a 404)."""
    with replay(MOCKS) as router:
        yield router


@pytest.fixture
def apis(online: None) -> Iterator[respx.MockRouter]:
    """Replay metacheck's own ``tests/testthat/apis`` recordings."""
    with replay("apis") as router:
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
