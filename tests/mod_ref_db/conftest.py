"""Fixtures for the reference-database module tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use the bundled databases only (no refreshed copies from the real data dir)."""
    data = tmp_path / "data"
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(data))
    return data
