"""Fixtures for the module system v2 tests: a hermetic config/data dir and pack builders.

Every test runs without GitHub tokens (``PYTACHECK_GITHUB_TOKEN``, ``GH_TOKEN``,
``GITHUB_TOKEN``) and with git limited to ``file://`` remotes, so a developer's
credentials never change what the mocked stores see and a git fallback never
reaches the network. The live test in ``test_private_store.py`` gets the token
that was set when the tests started from :func:`live_github_token`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from metacheck.packs.auth import TOKEN_VARS
from metacheck.packs.registry import refresh
from tests.modsys.helpers import ModSys

#: the token of the environment the tests started in (only the live test uses it)
_LIVE_TOKEN = next(
    (os.environ[v].strip() for v in TOKEN_VARS if os.environ.get(v, "").strip()), None
)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in TOKEN_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")


@pytest.fixture
def live_github_token() -> str | None:
    """The GitHub token set when the test session started (``None`` without one)."""
    return _LIVE_TOKEN


@pytest.fixture
def ms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModSys]:
    config = tmp_path / "config.json"
    data = tmp_path / "data"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("PYTACHECK_CONFIG", str(config))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(data))
    monkeypatch.delenv("PYTACHECK_PRESET", raising=False)
    monkeypatch.delenv("PYTACHECK_STORE_URL", raising=False)
    monkeypatch.chdir(work)
    refresh()
    yield ModSys(tmp_path, config, data, work)
    refresh()


@pytest.fixture
def paper():
    import metacheck as pc

    return pc.test_paper("Nothing much. The effect was marginally significant, p = .06.")
