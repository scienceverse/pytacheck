"""Fixtures for the reproducibility-check core tests (``pytacheck.repro``)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def rscript() -> str:
    """An ``Rscript`` (tests that run R code are skipped without one)."""
    exe = os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")
    if not exe:
        pytest.skip("Rscript not available")
    return exe


@pytest.fixture(scope="session")
def repro_fixtures(fixtures_dir: Path) -> Path:
    """metacheck's ``tests/testthat/fixtures/repro``."""
    return fixtures_dir / "repro"


@pytest.fixture(scope="session")
def area_fixtures() -> Path:
    """This area's own fixtures."""
    return HERE / "fixtures"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No progress chatter from the ``[repro]`` messages."""
    monkeypatch.setenv("PYTACHECK_VERBOSE", "0")
