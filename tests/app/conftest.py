from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the app's state file out of the real user directory."""
    import platformdirs

    folder = tmp_path / "state"
    monkeypatch.setattr(platformdirs, "user_state_dir", lambda *_a, **_k: str(folder))
    return folder
