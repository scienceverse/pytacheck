"""The harnesses fold a METACHECK_* twin into its old name before they set up their folders.

They set and read only the old names (``parity.lockfile.watch_run`` turns the data and
cache folders into ``<data>`` and ``<cache>``). A twin left in a developer's shell would
win over them: the cases would share a cache and the folders would show in the output.
No case's Python output holds those folders, so comparing fingerprints could not catch a
missing fold; these tests look at the environment itself.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from parity import lockfile
from parity.__main__ import _hermetic_env
from parity.cases import ROOT


@pytest.fixture
def twins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """The four twins set in the environment, with their old names cleared first, so that
    the undo at the end restores whatever the function under test changed."""
    values = {
        "DATA_DIR": str(tmp_path / "data"),
        "CACHE_DIR": str(tmp_path / "cache"),
        "CONFIG": "none",
        "API_KEY": "k" * 40,
    }
    for key, value in values.items():
        monkeypatch.setenv(f"PYTACHECK_{key}", "set-first-and-replaced")
        monkeypatch.setenv(f"METACHECK_{key}", value)
    return values


def _folded(values: dict[str, str]) -> None:
    left = [k for k in values if f"METACHECK_{k}" in os.environ]
    assert not left, f"the twins of {left} are still set"
    for key, value in values.items():
        assert os.environ[f"PYTACHECK_{key}"] == value


def _script() -> ModuleType:
    path = ROOT / "scripts" / "record_snapshots.py"
    spec = importlib.util.spec_from_file_location("record_snapshots", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_parity_harness_folds_the_twins(twins: dict[str, str]) -> None:
    _hermetic_env()
    _folded(twins)
    data, cache = twins["DATA_DIR"], twins["CACHE_DIR"]
    with lockfile.watch_run() as run:
        pass
    for folder, placeholder in ((data, "<data>"), (cache, "<cache>")):
        assert run.dirs[folder] == placeholder
        assert run.dirs[os.path.realpath(folder)] == placeholder


def test_the_snapshot_recorder_folds_the_twins(twins: dict[str, str]) -> None:
    _script()._hermetic_env()
    _folded(twins)
    data, cache = twins["DATA_DIR"], twins["CACHE_DIR"]
    with lockfile.watch_run() as run:
        pass
    for folder, placeholder in ((data, "<data>"), (cache, "<cache>")):
        assert run.dirs[folder] == placeholder
        assert run.dirs[os.path.realpath(folder)] == placeholder


def test_the_root_conftest_folds_the_twins_and_the_api_key_is_dropped(
    twins: dict[str, str],
) -> None:
    code = (
        "import runpy, os; runpy.run_path('tests/conftest.py'); "
        "print(sorted(k for k in os.environ if k.startswith("
        "('METACHECK_DATA_DIR', 'METACHECK_CACHE_DIR', 'METACHECK_CONFIG', "
        "'METACHECK_API_KEY', 'PYTACHECK_API_KEY'))))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"
