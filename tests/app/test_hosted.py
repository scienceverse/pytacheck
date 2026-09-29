"""Hosted mode: settings, tokens, cookie, hosts, limits, clean-up and texts."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

import pytacheck as pc
from pytacheck.app import hosted as hosting
from pytacheck.app import launch, ui
from pytacheck.app.security import HOSTED_COOKIE, HOSTED_DENIED_PAGE
from pytacheck.app.server import create_hosted_app

TOKEN_A = "a" * 32
TOKEN_B = "b-second-token-" + "x" * 20
HOST = "metacheck.example.org"
BASE = f"https://{HOST}"
PORT = 7861


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("TOKENS", "HOSTS", "JOB_TIMEOUT", "COMMIT"):
        monkeypatch.delenv(f"METACHECK_APP_{name}", raising=False)
    monkeypatch.delenv("SPACE_HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    return monkeypatch


def config(**kw: Any) -> hosting.HostedConfig:
    return hosting.HostedConfig(
        kw.pop("tokens", (TOKEN_A, TOKEN_B)), kw.pop("hosts", (HOST,)), **kw
    )


# --- settings -----------------------------------------------------------------------


def test_refuses_to_start_without_tokens(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.setenv("METACHECK_APP_HOSTS", HOST)
    assert launch.main(["--hosted"]) == 2
    assert "METACHECK_APP_TOKENS" in capsys.readouterr().err


def test_refuses_short_tokens_without_printing_them(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.setenv("METACHECK_APP_HOSTS", HOST)
    env.setenv("METACHECK_APP_TOKENS", f"{TOKEN_A},shorttoken")
    assert launch.main(["--hosted"]) == 2
    err = capsys.readouterr().err
    assert "Token 2" in err and "shorttoken" not in err


def test_refuses_to_start_without_hosts(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.setenv("METACHECK_APP_TOKENS", TOKEN_A)
    assert launch.main(["--hosted"]) == 2
    assert "METACHECK_APP_HOSTS" in capsys.readouterr().err


def test_space_host_is_the_fallback_for_hosts(env: pytest.MonkeyPatch) -> None:
    env.setenv("METACHECK_APP_TOKENS", f"{TOKEN_A}, {TOKEN_B}")
    env.setenv("SPACE_HOST", "Team-App.hf.space")
    cfg = hosting.HostedConfig.from_env()
    assert cfg.hosts == ("team-app.hf.space",)
    assert cfg.tokens == (TOKEN_A, TOKEN_B)
    env.setenv("METACHECK_APP_HOSTS", "one.example.org,two.example.org")
    assert hosting.HostedConfig.from_env().hosts == ("one.example.org", "two.example.org")


def test_timeout_setting(env: pytest.MonkeyPatch) -> None:
    env.setenv("METACHECK_APP_TOKENS", TOKEN_A)
    env.setenv("METACHECK_APP_HOSTS", HOST)
    assert hosting.HostedConfig.from_env().job_timeout == 900
    env.setenv("METACHECK_APP_JOB_TIMEOUT", "30")
    assert hosting.HostedConfig.from_env().job_timeout == 30
    env.setenv("METACHECK_APP_JOB_TIMEOUT", "soon")
    with pytest.raises(hosting.HostedError):
        hosting.HostedConfig.from_env()


def test_port_and_host_arguments(env: pytest.MonkeyPatch) -> None:
    assert hosting.default_port() == 7860
    env.setenv("PORT", "8081")
    assert hosting.default_port() == 8081
    env.setenv("PORT", "nope")
    assert hosting.default_port() == 7860
    env.setenv("METACHECK_APP_TOKENS", TOKEN_A)
    env.setenv("METACHECK_APP_HOSTS", HOST)
    seen: list[tuple[Any, ...]] = []
    env.setattr(launch, "_serve", lambda *a: seen.append(a) or 0)
    env.setenv("PORT", "8081")
    assert launch.main(["--hosted", "--host", "0.0.0.0"]) == 0
    assert launch.main(["--hosted", "--port", "9000"]) == 0
    assert [(a[0], a[1], a[3]) for a in seen] == [
        (8081, False, "0.0.0.0"),
        (9000, False, "127.0.0.1"),
    ]


def test_host_needs_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    assert launch.main(["--host", "0.0.0.0"]) == 2
    assert "--hosted" in capsys.readouterr().err


# --- the app behind the guard -------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> Any:
    return create_hosted_app(PORT, config(commit="0123abcd"))


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE, follow_redirects=False) as tc:
        yield tc


def test_every_token_works_and_a_wrong_one_is_refused(client: TestClient, app: Any) -> None:
    for token in (TOKEN_A, TOKEN_B):
        with TestClient(app, base_url=BASE, follow_redirects=False) as fresh:
            assert fresh.get("/").status_code == 403
            resp = fresh.get(f"/?token={token}")
            assert resp.status_code == 303 and resp.headers["location"] == "/"
            assert fresh.get("/").status_code == 200
    assert client.get("/").text == HOSTED_DENIED_PAGE
    for wrong in ("x" * 32, TOKEN_A[:-1], TOKEN_A + "x", ""):
        assert client.get(f"/?token={wrong}").status_code == 403, wrong
    assert client.get("/", headers={"cookie": f"{HOSTED_COOKIE}=nope"}).status_code == 403


def test_the_cookie_is_secure_in_hosted_mode_and_not_in_local_mode(client: TestClient) -> None:
    cookie = client.get(f"/?token={TOKEN_B}").headers["set-cookie"]
    assert cookie.startswith(f"{HOSTED_COOKIE}={TOKEN_B}")
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/" in cookie and "Domain" not in cookie
    from pytacheck.app.server import create_app

    with TestClient(
        create_app(4400, "local-token"), base_url="http://127.0.0.1:4400", follow_redirects=False
    ) as local:
        assert "Secure" not in local.get("/?token=local-token").headers["set-cookie"]


def test_opened_from_a_link_on_another_site(client: TestClient) -> None:
    """A link in a mail or chat is a cross-site page load: the cookie must still land."""
    load = {
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "document",
    }
    resp = client.get(f"/?token={TOKEN_A}", headers=load)
    assert resp.status_code == 200 and "http-equiv=refresh" in resp.text
    assert "Secure" in resp.headers["set-cookie"]
    assert client.get("/", headers=load).status_code == 200  # the cookie is now stored


def test_host_and_origin_checks_use_the_configured_hosts(client: TestClient) -> None:
    client.get(f"/?token={TOKEN_A}")
    assert client.get("/", headers={"host": HOST.upper()}).status_code == 200
    for host in ("evil.example", f"{HOST}.evil.example", "127.0.0.1:7861", f"{HOST}:8080"):
        assert client.get("/", headers={"host": host}).status_code == 403, host
        assert client.get(f"/?token={TOKEN_A}", headers={"host": host}).status_code == 403, host
    assert client.get("/", headers={"origin": BASE}).status_code == 200
    for origin in (f"http://{HOST}", "https://evil.example", "null", "http://127.0.0.1:7861"):
        assert client.get("/", headers={"origin": origin}).status_code == 403, origin


def test_health_is_open_and_minimal(app: Any) -> None:
    with TestClient(app, base_url="http://10.0.0.5:7860") as tc:
        resp = tc.get("/healthz")
        assert resp.status_code == 200 and resp.json() == {"ok": True}
        assert tc.get("/").status_code == 403


def test_the_hosted_texts(client: TestClient) -> None:
    client.get(f"/?token={TOKEN_A}")
    text = client.get("/config").text
    assert "Your paper is processed on this server and deleted when the report is ready." in text
    assert "https://github.com/scienceverse/pytacheck/tree/0123abcd" in text.replace("\\/", "/")
    from pytacheck._version import __version__

    assert __version__ in text


def test_footer_falls_back_to_the_repository() -> None:
    assert "tree/" not in hosting.footer("1.0", None)
    assert hosting.footer("1.0", None).endswith("(https://github.com/scienceverse/pytacheck)")
    assert "tree/" not in hosting.footer("1.0", "not a commit; rm -rf")


def test_the_local_page_has_no_hosted_texts() -> None:
    blocks = ui.build_app()
    assert hosting.HOSTED_NOTE not in str(blocks.get_config_file())


def test_limits_are_two_runs_and_ten_waiting() -> None:
    blocks = ui.build_app(hosted=config())
    assert blocks._queue.max_size == 10
    assert blocks._queue.default_concurrency_limit == 2
    assert blocks.delete_cache == (60, 300)
    assert ui.build_app()._queue.default_concurrency_limit == 1


def test_no_state_file_is_written(client: TestClient, state_dir: Path) -> None:
    client.get(f"/?token={TOKEN_A}")
    assert not state_dir.exists() or not any(state_dir.iterdir())


# --- runs ---------------------------------------------------------------------------


def _join(client: TestClient, fn_index: int, path: str, session: str) -> str:
    body = {
        "data": [{"path": path, "meta": {"_type": "gradio.FileData"}}, False],
        "fn_index": fn_index,
        "session_hash": session,
    }
    assert client.post("/gradio_api/queue/join", json=body).status_code == 200
    text = ""
    with client.stream("GET", "/gradio_api/queue/data", params={"session_hash": session}) as s:
        for line in s.iter_lines():
            text += line
            if '"process_completed"' in line:
                break
    return text


def _fn_index(client: TestClient) -> int:
    deps = client.get("/config").json()["dependencies"]
    (dep,) = (d for d in deps if d["targets"][0][1] == "click" and len(d["inputs"]) == 2)
    return int(dep["id"])


def _upload(client: TestClient) -> str:
    files = {"files": ("paper.json", pc.demofile("json").read_bytes(), "application/json")}
    (path,) = client.post("/gradio_api/upload", files=files).json()
    return str(path)


def test_a_run_leaves_nothing_behind(client: TestClient) -> None:
    client.get(f"/?token={TOKEN_A}")
    path = _upload(client)
    assert Path(path).exists()
    events = _join(client, _fn_index(client), path, "h1")
    assert "Ran 16 checks" in events
    assert "data:text/html" in events  # the report comes with the page, not from a file
    assert not Path(path).exists()
    assert client.get("/report/h1/paper_report.html").status_code == 404


def test_a_run_over_the_time_limit_gets_a_plain_message(
    env: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def slow(*_a: Any, **_k: Any) -> Any:
        release.wait(20)
        raise RuntimeError("late")

    env.setattr(ui, "check_paper", slow)
    tight = create_hosted_app(PORT + 1, config(job_timeout=0.3))
    with TestClient(tight, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        path = _upload(tc)
        start = time.perf_counter()
        events = _join(tc, _fn_index(tc), path, "h2")
        assert time.perf_counter() - start < 10
        release.set()
    assert hosting.TOO_SLOW in events
    deadline = time.time() + 5
    while Path(path).exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not Path(path).exists()


def test_job_runner_returns_the_result_and_deletes_the_upload(tmp_path: Path) -> None:
    upload = tmp_path / "up.pdf"
    upload.write_text("x")
    runner = hosting.JobRunner(5)
    assert runner.run(lambda a, b: a + b, 1, 2, upload=upload, on_timeout=TimeoutError) == 3
    assert not upload.exists()
    upload.write_text("x")
    with pytest.raises(ValueError, match="boom"):
        runner.run(
            lambda: (_ for _ in ()).throw(ValueError("boom")),
            upload=upload,
            on_timeout=TimeoutError,
        )
    assert not upload.exists()


# --- the log ------------------------------------------------------------------------


def test_the_access_log_is_off() -> None:
    assert launch.uvicorn_config(object(), hosted=True).access_log is False
    assert launch.uvicorn_config(object(), hosted=False).access_log is False
    assert launch.uvicorn_config(object(), hosted=True).forwarded_allow_ips == "*"


@pytest.mark.parametrize("hosted", [True, False])
def test_no_token_reaches_the_log(
    env: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    state_dir: Path,
    hosted: bool,
) -> None:
    from pytacheck import config as lib_config

    env.setitem(lib_config._state, "verbose", lib_config.verbose())
    env.setattr(launch, "_warm_up", lambda: None)
    caplog.set_level(logging.DEBUG)
    cfg = config(tokens=(TOKEN_A,)) if hosted else None
    before = _servers()
    result: list[int] = []
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    thread = threading.Thread(
        target=lambda: result.append(launch._serve(port, False, cfg)), daemon=True
    )
    thread.start()
    from pytacheck.app import state as saved

    deadline = time.time() + 30
    while time.time() < deadline and not any(server.started for server in _fresh(before)):
        time.sleep(0.05)
    assert any(server.started for server in _fresh(before))
    found = port
    token = TOKEN_A if hosted else (saved.read_state() or {})["token"]
    headers = {"host": HOST if hosted else f"127.0.0.1:{found}"}
    with httpx.Client(trust_env=False, follow_redirects=False) as tc:
        resp = tc.get(f"http://127.0.0.1:{found}/?token={token}&a=1", headers=headers)
        assert resp.status_code == 303
        tc.get(f"http://127.0.0.1:{found}/healthz", headers=headers)
    for server in _fresh(before):
        server.should_exit = True
    thread.join(15)
    assert result == [0]
    out = capfd.readouterr()
    server_log = "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))
    )
    assert token not in server_log
    assert "HTTP/1.1" not in out.out + out.err  # no access-log line
    if hosted:  # the local app prints its link on purpose
        assert token not in out.out + out.err
    assert not any(r.name == "uvicorn.access" for r in caplog.records)
    if hosted:
        assert not state_dir.exists() or not any(state_dir.iterdir())


def _servers() -> list[Any]:
    import gc

    import uvicorn

    return [o for o in gc.get_objects() if isinstance(o, uvicorn.Server)]


def _fresh(before: list[Any]) -> list[Any]:
    return [s for s in _servers() if not any(s is old for old in before)]
