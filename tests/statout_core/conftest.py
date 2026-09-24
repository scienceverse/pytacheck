"""Fixtures for the statout_core tests (R output parsing, stat tables, stat output)."""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def rscript() -> str:
    """Path of an ``Rscript`` executable (tests needing R are skipped without one)."""
    exe = os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")
    if not exe:
        pytest.skip("Rscript not available")
    return exe


@pytest.fixture
def stato_vocab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the capture helpers buildable even before ``stato_map`` is ported."""
    if importlib.util.find_spec("pytacheck.statout.stato_map") is not None:
        try:
            from pytacheck.statout.r_capture import _stato_vocab_keys

            _stato_vocab_keys()
            return
        except ImportError:
            pass
    import pytacheck.statout.r_capture as rc

    monkeypatch.setattr(
        rc, "_stato_vocab_keys", lambda: (["mean", "sd", "median", "t", "p"], ["iqr"])
    )


@pytest.fixture
def stato() -> None:
    """Skip when the STATO typing layer (``pytacheck.statout.stato_map``) is absent."""
    if importlib.util.find_spec("pytacheck.statout.stato_map") is None:
        pytest.skip("pytacheck.statout.stato_map is not available yet")
