"""Fixtures for the reproducibility_check module tests."""

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
def repro_fixture(fixtures_dir: Path):
    """``repro_fixture(name)``: a file of metacheck's ``tests/testthat/fixtures/repro``."""

    def get(*parts: str) -> str:
        return str(fixtures_dir.joinpath("repro", *parts))

    return get


@pytest.fixture(scope="session")
def area_fixtures() -> Path:
    return HERE / "fixtures"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No progress chatter from the ``[repro]`` messages."""
    monkeypatch.setenv("PYTACHECK_VERBOSE", "0")
