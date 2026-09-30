"""Tests for the local RegCheck server helpers (R/regcheck-local.R); processes mocked."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from metacheck.db import regcheck_local as rl


def test_bundled_app_matches_upstream(upstream_dir: Path, tmp_path: Path) -> None:
    """The bundled archive is metacheck's inst/regcheck, byte for byte."""
    rebuilt = rl._build_app_archive(upstream_dir / "inst" / "regcheck", tmp_path / "app.tar.gz")
    assert rebuilt.read_bytes() == rl._archive_bytes()


def test_app_dir_is_unpacked_once(tmp_path: Path) -> None:
    app = rl._regcheck_app_dir()
    assert app.parent == tmp_path / "data" / "regcheck"
    for name in ("Dockerfile", "docker-compose.yml", "requirements.txt", "backend/main.py"):
        assert (app / name).exists()
    marker = app / "marker"
    marker.write_text("x")
    assert rl._regcheck_app_dir() == app
    assert marker.exists()  # not re-extracted
    assert rl._regcheck_venv_dir() == app / ".venv"


def test_app_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REGCHECK_APP_DIR", str(tmp_path / "custom"))
    assert rl._regcheck_app_dir() == tmp_path / "custom"


def test_setup_local(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append([str(c) for c in cmd])
        if cmd[1:3] == ["-m", "venv"]:
            Path(cmd[3]).mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="(3, 12)", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    venv = rl.regcheck_setup_local(python="/usr/bin/python3")
    assert venv == rl._regcheck_venv_dir()
    assert calls[1] == ["/usr/bin/python3", "-m", "venv", str(venv)]
    assert calls[2][1:3] == ["install", "-r"]
    assert calls[2][3].endswith("requirements.txt")
    assert "nltk.download('punkt')" in calls[3][2]

    # a failing pip install is an error
    def failing(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1 if "install" in cmd else 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(RuntimeError, match="pip install failed"):
        rl.regcheck_setup_local(python="/usr/bin/python3")


class FakeProc:
    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9


def _ollama_router(router: respx.MockRouter) -> None:
    router.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "llama3.2:latest", "size": 2000},
                    {"name": "big-embed:latest", "size": 9000},
                    {"name": "mistral:latest", "size": 4000},
                ]
            },
        )
    )

    def show(request: httpx.Request) -> httpx.Response:
        import json

        name = json.loads(request.content)["model"]
        caps = ["embedding"] if "embed" in name else ["completion", "tools"]
        return httpx.Response(200, json={"capabilities": caps})

    router.post("http://localhost:11434/api/show").mock(side_effect=show)


def test_start_and_stop_local_python(monkeypatch: pytest.MonkeyPatch) -> None:
    rl._regcheck_venv_dir().mkdir(parents=True)
    started: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProc:
        started["cmd"] = cmd
        started.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rl, "_server_up", lambda url: url == "http://localhost:8123")
    with respx.mock() as router:
        _ollama_router(router)
        proc = rl.regcheck_start_local(method="python", port=8123)
    assert started["cmd"][1:] == [
        "backend.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
    ]
    assert Path(started["cmd"][0]).stem == "uvicorn"  # uvicorn.exe on Windows
    # the largest *language* model is selected, embeddings are skipped
    assert started["env"]["OLLAMA_MODEL"] == "mistral:latest"
    assert started["env"]["REGCHECK_API_TOKEN"] == "metacheck-local"
    assert os.environ["REGCHECK_API_TOKEN"] == "metacheck-local"
    assert started["cwd"] == rl._regcheck_app_dir()

    rl.regcheck_stop_local()
    assert proc.poll() is not None
    rl.regcheck_stop_local()  # nothing running: just a message


def test_start_local_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProc:
        started["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rl.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(rl, "_server_up", lambda url: True)
    rl.regcheck_start_local(model="llama3.2")
    assert started["cmd"] == ["/usr/bin/docker", "compose", "up", "--build", "--force-recreate"]
    rl.regcheck_stop_local()
    # U14: metacheck ignores `port` with Docker; an override file publishes it
    rl.regcheck_start_local(model="llama3.2", port=8123)
    assert started["cmd"] == [
        "/usr/bin/docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.port.yml",
        "up",
        "--build",
        "--force-recreate",
    ]
    override = rl._regcheck_app_dir() / "docker-compose.port.yml"
    assert '- "8123:8000"' in override.read_text(encoding="utf-8")
    rl.regcheck_stop_local()


def test_start_local_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="should be one of"):
        rl.regcheck_start_local(method="conda", model="m")
    with pytest.raises(RuntimeError, match="regcheck_setup_local"):
        rl.regcheck_start_local(method="python", model="m")
    monkeypatch.setattr(rl.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Docker not found"):
        rl.regcheck_start_local(method="docker", model="m")
    with respx.mock() as router:
        router.get("http://localhost:11434/api/tags").mock(side_effect=httpx.ConnectError("x"))
        with pytest.raises(RuntimeError, match="Ollama is not running"):
            rl.regcheck_start_local()


def test_start_local_process_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    rl._regcheck_venv_dir().mkdir(parents=True)

    def dead(cmd: list[str], **kwargs: Any) -> FakeProc:
        kwargs["stdout"].write(b"boom: cannot bind")
        proc = FakeProc()
        proc.returncode = 1
        return proc

    monkeypatch.setattr(subprocess, "Popen", dead)
    monkeypatch.setattr(rl, "_server_up", lambda url: False)
    with pytest.raises(RuntimeError, match="failed to start"):
        rl.regcheck_start_local(method="python", model="m")
