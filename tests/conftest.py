"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # makes the `parity` harness importable

UPSTREAM = ROOT / "upstream" / "metacheck"
FIXTURES = UPSTREAM / "tests" / "testthat" / "fixtures"


@pytest.fixture(scope="session")
def upstream_dir() -> Path:
    """The pinned metacheck checkout (git submodule)."""
    if not (UPSTREAM / "DESCRIPTION").exists():
        pytest.skip("upstream/metacheck submodule not checked out")
    return UPSTREAM


@pytest.fixture(scope="session")
def fixtures_dir(upstream_dir: Path) -> Path:
    """metacheck's testthat fixtures directory."""
    return upstream_dir / "tests" / "testthat" / "fixtures"


@pytest.fixture
def demo():
    import pytacheck as pc

    return pc.demopaper()


@pytest.fixture
def psychsci(fixtures_dir: Path):
    import pytacheck as pc

    return pc.read(fixtures_dir / "psychsci")


@pytest.fixture(autouse=True)
def _isolated_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep logs and caches out of the user's home directory."""
    base = tmp_path_factory.getbasetemp()
    monkeypatch.setenv("PYTACHECK_LOG", str(base / "pytacheck.log.jsonl"))
    monkeypatch.setenv("PYTACHECK_CACHE_DIR", str(base / "cache"))


@pytest.fixture(autouse=True)
def _no_http_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Courtesy delays and retry backoff are pointless against mocks."""
    monkeypatch.setenv("PYTACHECK_NO_SLEEP", "1")
