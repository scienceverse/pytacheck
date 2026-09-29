"""State file, reuse of a running app, the command line and a real server."""

from __future__ import annotations

import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import orjson
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route

from pytacheck import cli
from pytacheck.app import launch, main
from pytacheck.app import state as saved
from pytacheck.app.security import TokenGuard


def test_state_file_is_private_and_holds_port_token_pid(state_dir: Path) -> None:
    path = saved.write_state(4000, "tok")
    assert path == state_dir / "app.json"
    assert saved.read_state() == {"port": 4000, "token": "tok", "pid": os.getpid()}
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved.write_state(4001, "tok2")  # replaces, still private
    assert saved.read_state()["port"] == 4001  # type: ignore[index]
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(state_dir.glob("*.tmp"))


def test_bad_state_files_are_ignored(state_dir: Path) -> None:
    assert saved.read_state() is None
    state_dir.mkdir()
    for text in ("not json", "[]", '{"port": "x", "token": "t", "pid": 1}', '{"port": 1}'):
        (state_dir / "app.json").write_text(text)
        assert saved.read_state() is None
        assert saved.find_running() is None


def test_remove_only_removes_our_own_file(state_dir: Path) -> None:
    saved.write_state(4000, "tok", pid=os.getpid() + 1)
    saved.remove_state(4000)
    assert (state_dir / "app.json").exists()
    saved.write_state(4000, "tok")
    saved.remove_state(4001)
    assert (state_dir / "app.json").exists()
    saved.remove_state(4000)
    assert not (state_dir / "app.json").exists()


@pytest.fixture
def guarded_server() -> Any:
    """A real server on a free port behind the token guard."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    app = Starlette(routes=[Route("/", lambda _r: PlainTextResponse("ok"))])
    app.add_middleware(TokenGuard, port=port, token="right")
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield port
    server.should_exit = True
    thread.join(5)
    sock.close()


def test_find_running_needs_the_right_token(guarded_server: int) -> None:
    saved.write_state(guarded_server, "wrong")
    assert saved.find_running() is None
    saved.write_state(guarded_server, "right")
    assert saved.find_running() == {"port": guarded_server, "token": "right", "pid": os.getpid()}


def test_a_dead_process_makes_the_state_stale(guarded_server: int, state_dir: Path) -> None:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    saved.write_state(guarded_server, "right", pid=gone.pid)
    assert saved.find_running() is None
    assert not (state_dir / "app.json").exists()


@pytest.fixture
def foreign_server() -> Any:
    """Some other program on the port: it answers 303 and sets a cookie of its own."""

    async def anything(_request: Any) -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("x", "y")
        return response

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(
        uvicorn.Config(Starlette(routes=[Route("/", anything)]), log_level="error")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield sock.getsockname()[1]
    server.should_exit = True
    thread.join(5)
    sock.close()


def test_another_program_on_the_port_is_not_our_app(foreign_server: int, state_dir: Path) -> None:
    saved.write_state(foreign_server, "stale-token")  # the pid is alive: it is this process
    assert saved.find_running() is None
    assert not (state_dir / "app.json").exists()


def test_find_running_when_nothing_listens(state_dir: Path) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    saved.write_state(port, "tok")
    assert saved.find_running() is None


def test_second_start_reuses_the_running_app(
    guarded_server: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved.write_state(guarded_server, "right")
    opened: list[str] = []
    monkeypatch.setattr(launch.webbrowser, "open", opened.append)
    monkeypatch.setattr(launch, "_serve", lambda *_a: pytest.fail("started a second app"))
    assert main([]) == 0
    url = f"http://127.0.0.1:{guarded_server}/?token=right"
    page = saved.state_path().with_name("open.html")
    assert opened == [page.as_uri()]  # the token link never goes into a process list
    assert url not in opened[0]
    assert url in page.read_text(encoding="utf-8")
    if sys.platform != "win32":
        assert stat.S_IMODE(page.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert f"metacheck is running at {url}" in out
    assert "Keep this window open while you use it. Press Ctrl+C here to stop it." in out
    opened.clear()
    assert main(["--no-browser"]) == 0
    assert opened == []


def test_stale_state_starts_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    saved.write_state(9, "gone")
    started: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        launch, "_serve", lambda port, browser: started.append((port, browser)) or 0
    )
    assert main(["--no-browser"]) == 0
    assert started == [(0, False)]


def test_self_test_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--self-test"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Self-test passed: 16 checks in ")


def test_self_test_fails_with_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pytacheck.app import run

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no good")

    monkeypatch.setattr(run, "check_paper", broken)
    assert main(["--self-test"]) == 1
    assert "Self-test failed: no good" in capsys.readouterr().err


def test_self_test_fails_when_a_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from pytacheck.app import run

    real = run.check_paper

    def one_failed(*a: Any, **k: Any) -> Any:
        analysis = real(*a, **k)
        rows = [replace(analysis.rows[0], result="Failed: This module failed to run")]
        return replace(analysis, rows=rows)

    monkeypatch.setattr(run, "check_paper", one_failed)
    assert main(["--self-test"]) == 1


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("metacheck-app ")


def test_missing_extra_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(launch.importlib.util, "find_spec", lambda _name: None)
    assert main([]) == 2
    assert capsys.readouterr().err.strip() == (
        "The app needs the app extra: pip install 'metacheck[app]>=0.4.0a1'"
    )


def test_bad_port(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--port", "70000"]) == 2


def test_pytacheck_app_forwards(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["app", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "usage: metacheck-app" in out
    assert "--no-browser" in out and "--self-test" in out
    assert cli.main(["app", "--version"]) == 0


def test_pytacheck_app_is_listed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "open the local app in your browser" in capsys.readouterr().out


def test_real_app_end_to_end(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Start the app in this process, talk to it over HTTP, stop it like Ctrl+C."""
    from pytacheck import config

    monkeypatch.setitem(config._state, "verbose", config.verbose())  # the app turns it off
    monkeypatch.setattr(launch, "_warm_up", lambda: None)
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(launch._serve(0, False)), daemon=True)
    thread.start()
    deadline = time.time() + 30
    while time.time() < deadline and saved.read_state() is None:
        time.sleep(0.05)
    state = saved.read_state()
    assert state is not None
    port, token = state["port"], state["token"]
    base = f"http://127.0.0.1:{port}"
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        while time.time() < deadline:
            try:
                if client.get(f"{base}/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        assert client.get(f"{base}/healthz").json() == {"ok": True}
        assert client.get(f"{base}/").status_code == 403
        assert client.get(f"{base}/", headers={"host": "evil.example"}).status_code == 403
        resp = client.get(f"{base}/?token={token}")
        assert resp.status_code == 303 and resp.headers["location"] == "/"
        page = client.get(f"{base}/")
        assert page.status_code == 200
        assert '"title":"metacheck"' in page.text
        assert '"analytics_enabled":false' in page.text
    assert saved.find_running() is not None
    # stop the server the way Ctrl+C does
    server_thread_stop(thread)
    assert result == [0]
    assert saved.read_state() is None


def server_thread_stop(thread: threading.Thread) -> None:
    """uvicorn stops when its ``should_exit`` flag is set; find the server object."""
    import gc

    for obj in gc.get_objects():
        if isinstance(obj, uvicorn.Server) and obj.started:
            obj.should_exit = True
    thread.join(15)
    assert not thread.is_alive()


def test_gradio_keeps_its_files_in_a_private_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    from gradio.utils import get_upload_folder

    monkeypatch.setenv("GRADIO_TEMP_DIR", "unset-by-the-test")
    folder = launch.private_gradio_dir()
    try:
        assert get_upload_folder() == str(folder)
        if sys.platform != "win32":
            assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    finally:
        folder.rmdir()


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP on this system")
def test_closing_the_terminal_cleans_up(tmp_path: Path) -> None:
    """SIGHUP is what a closed terminal window sends."""
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "TMPDIR": str(tmp_path)}
    code = "from pytacheck.app import main; raise SystemExit(main(['--no-browser']))"
    proc = subprocess.Popen(
        [sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    state_file = tmp_path / "state" / "pytacheck" / "app.json"
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not state_file.exists():
            time.sleep(0.05)
        assert state_file.exists()
        port = orjson.loads(state_file.read_bytes())["port"]
        while time.time() < deadline:
            try:
                if (
                    httpx.get(f"http://127.0.0.1:{port}/healthz", trust_env=False).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        assert list(tmp_path.glob("metacheck-app-gradio-*"))
        proc.send_signal(signal.SIGHUP)
        assert proc.wait(30) == 0
    finally:
        proc.kill()
    assert not state_file.exists()
    assert not list(tmp_path.glob("metacheck-app-gradio-*"))
