"""Fixtures for the ref_pubpeer / ref_summary module tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.httpmock import replay
from tests.mod_ref_pubpeer_summary.helpers import MOCK_DIR


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use the bundled databases only (no refreshed copies from the real data dir)."""
    data = tmp_path / "data"
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(data))
    return data


@pytest.fixture
def apis(upstream_dir: Path) -> Iterator[None]:
    """Replay metacheck's recorded API responses (testthat's ``"mock"`` tests)."""
    with replay("apis"):
        yield


@pytest.fixture
def mock_pubpeer() -> Iterator[None]:
    """Replay the PubPeer responses recorded for the synthetic papers (make_mocks.py)."""
    with replay(MOCK_DIR):
        yield
