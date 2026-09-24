"""Fixtures for the module system v2 tests: a hermetic config/data dir and pack builders."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pytacheck.packs.registry import refresh
from tests.modsys.helpers import ModSys


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
    import pytacheck as pc

    return pc.test_paper("Nothing much. The effect was marginally significant, p = .06.")
