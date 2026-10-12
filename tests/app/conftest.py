from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def gradio_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Gradio's folder for uploads, for this test process only.

    By default every process shares ``/tmp/gradio``, where an upload is named by its hash,
    and an app deletes its uploads when it stops: an app in another worker could delete the
    file a run here is reading. The app itself sets its own folder when it starts.
    """
    folder = tmp_path_factory.mktemp("gradio")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GRADIO_TEMP_DIR", str(folder))
        yield folder


@pytest.fixture(autouse=True)
def gradio_dir_again(gradio_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set for each test too, and so back after it: a launch in this process removes it."""
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(gradio_dir))


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
    """No key, address or backend from the environment, and the server list is read again."""
    from metacheck.app import bibr

    monkeypatch.delenv(bibr.KEY_ENV, raising=False)
    for name in (*bibr.URL_ENVS, *bibr.BACKEND_ENVS):
        monkeypatch.delenv(name, raising=False)
    bibr._forget_list()
