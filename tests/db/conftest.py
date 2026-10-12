"""Fixtures for the metacheck.db tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

#: unset for every test here, and restored after it
_REGCHECK_VARS = ("REGCHECK_BASE_URL", "REGCHECK_API_TOKEN", "REGCHECK_APP_DIR")


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Refreshed databases and the RegCheck app go to a temporary data directory;
    no test sees the RegCheck variables of the environment or of another test."""
    data = tmp_path / "data"
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(data))
    for name in _REGCHECK_VARS:
        # `regcheck_start_local()` sets REGCHECK_API_TOKEN itself (R's
        # Sys.setenv()). delenv() of an unset variable records nothing to undo,
        # so the token would outlive the test (and make a later hosted-client
        # call reach the network instead of asking for a token); setenv() first
        # records the variable, and the teardown then removes it again.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return data


@pytest.fixture
def apis(upstream_dir: Path) -> Iterator[None]:
    """metacheck's recorded API responses, with ``online()`` true (like the R tests)."""
    from tests.db.parity_replay import replaying

    with replaying("apis"):
        yield


@pytest.fixture
def psychsci_papers(fixtures_dir: Path):
    import metacheck as pc

    return pc.read(fixtures_dir / "psychsci")


DATA = Path(__file__).resolve().parent / "data"
