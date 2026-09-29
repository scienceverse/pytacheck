from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the app's state and key files out of the real user directories."""
    import platformdirs

    folder = tmp_path / "state"
    monkeypatch.setattr(platformdirs, "user_state_dir", lambda *_a, **_k: str(folder))
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda *_a, **_k: str(tmp_path / "config"))
    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda *_a, **_k: str(tmp_path / "cache"))
    return folder


@pytest.fixture(autouse=True)
def bibr_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key or address from the environment, and the server list is read again."""
    from pytacheck.app import bibr

    monkeypatch.delenv(bibr.KEY_ENV, raising=False)
    monkeypatch.delenv(bibr.URL_ENV, raising=False)
    bibr._forget_list()
