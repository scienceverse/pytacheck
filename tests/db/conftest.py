"""Fixtures for the pytacheck.db tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Refreshed databases and the RegCheck app go to a temporary data directory."""
    data = tmp_path / "data"
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(data))
    monkeypatch.delenv("REGCHECK_BASE_URL", raising=False)
    monkeypatch.delenv("REGCHECK_API_TOKEN", raising=False)
    monkeypatch.delenv("REGCHECK_APP_DIR", raising=False)
    return data


@pytest.fixture
def apis(upstream_dir: Path) -> Iterator[None]:
    """metacheck's recorded API responses, with ``online()`` true (like the R tests)."""
    from tests.db.parity_replay import replaying

    with replaying("apis"):
        yield


@pytest.fixture
def psychsci_papers(fixtures_dir: Path):
    import pytacheck as pc

    return pc.read(fixtures_dir / "psychsci")


DATA = Path(__file__).resolve().parent / "data"
