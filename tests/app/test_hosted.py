"""Hosted mode: settings, tokens, cookie, hosts, limits, clean-up and texts."""

from __future__ import annotations

import json
import logging
import multiprocessing
import operator
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
from pytacheck.app.security import HOSTED_COOKIE, HOSTED_DENIED_PAGE, PROXY_DENIED_PAGE
from pytacheck.app.server import create_hosted_app

TOKEN_A = "a" * 32
TOKEN_B = "b-second-token-" + "x" * 20
HOST = "metacheck.example.org"
BASE = f"https://{HOST}"
PORT = 7861


@pytest.fixture(autouse=True, scope="module")
def _fork_runs() -> Iterator[None]:
    """Runs start by fork here, so a patched ``ui.check_paper`` reaches the run's process."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hosting, "START_METHOD", "fork")
        yield


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("TOKENS", "HOSTS", "JOB_TIMEOUT", "COMMIT", "AUTH", "USER_HEADER"):
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


@pytest.mark.parametrize("bad", ["a+b/c=" * 6, "x" * 31 + " ", "x" * 31 + "?"])
def test_refuses_tokens_that_do_not_survive_a_link(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    env.setenv("METACHECK_APP_HOSTS", HOST)
    env.setenv("METACHECK_APP_TOKENS", f"{TOKEN_A},{bad}")
    assert launch.main(["--hosted"]) == 2
    err = capsys.readouterr().err
    assert "Token 2" in err and bad not in err
    env.setenv("METACHECK_APP_TOKENS", "Ab_-" * 8)
    assert hosting.HostedConfig.from_env().tokens == ("Ab_-" * 8,)


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


def test_proxy_sign_in_needs_no_tokens(env: pytest.MonkeyPatch) -> None:
    env.setenv("METACHECK_APP_HOSTS", HOST)
    env.setenv("METACHECK_APP_AUTH", "proxy")
    cfg = hosting.HostedConfig.from_env()
    assert cfg.proxy_auth and cfg.tokens == () and cfg.user_header is None
    env.setenv("METACHECK_APP_USER_HEADER", "X-Forwarded-User")
    assert hosting.HostedConfig.from_env().user_header == "X-Forwarded-User"


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        ({"METACHECK_APP_AUTH": "proxy", "METACHECK_APP_TOKENS": TOKEN_A}, "Remove one"),
        ({"METACHECK_APP_AUTH": "open"}, "tokens (the default) or proxy"),
        ({"METACHECK_APP_TOKENS": TOKEN_A, "METACHECK_APP_USER_HEADER": "X-User"}, "only works"),
        ({"METACHECK_APP_AUTH": "proxy", "METACHECK_APP_USER_HEADER": "X User"}, "one header"),
        ({"METACHECK_APP_AUTH": "proxy", "METACHECK_APP_HOSTS": ""}, "METACHECK_APP_HOSTS"),
    ],
)
def test_proxy_sign_in_settings_are_checked(
    env: pytest.MonkeyPatch, settings: dict[str, str], named: str
) -> None:
    env.setenv("METACHECK_APP_HOSTS", HOST)
    for name, value in settings.items():
        env.setenv(name, value)
    with pytest.raises(hosting.HostedError, match=named.replace("(", r"\(").replace(")", r"\)")):
        hosting.HostedConfig.from_env()


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


def test_the_token_redirect_stays_on_this_site(client: TestClient) -> None:
    for path in ("/%2Fevil.example/", "/%2F%2Fevil.example/x", "/a"):
        resp = client.get(f"{path}?token={TOKEN_A}&b=1")
        location = resp.headers["location"]
        assert resp.status_code == 303 and location.startswith("/"), location
        assert not location.startswith("//"), location
    load = {
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "document",
    }
    page = client.get(f"/%2Fevil.example/?token={TOKEN_A}", headers=load).text
    assert "url=/evil.example/" in page


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


def test_the_privacy_text_names_the_server(client: TestClient) -> None:
    client.get(f"/?token={TOKEN_A}")
    text = client.get("/config").text
    assert "XML and JSON files are checked on this server." in text
    assert "this computer" not in text
    assert "checked on this computer" in str(ui.build_app().get_config_file())


def test_the_local_page_has_no_hosted_texts() -> None:
    blocks = ui.build_app()
    assert hosting.HOSTED_NOTE not in str(blocks.get_config_file())


def test_limits_are_two_runs_and_ten_waiting() -> None:
    blocks = ui.build_app(hosted=config())
    assert blocks._queue.max_size == 10
    assert blocks._queue.default_concurrency_limit == 2
    interval, age = blocks.delete_cache
    # an upload may wait behind ten others (two at a time) and then run
    assert interval == 60 and age > 6 * 900
    assert hosting.sweep(10)[1] > 6 * 10
    assert blocks.delete_cache == hosting.sweep(900)
    # one shared limit for both buttons: each listener would otherwise get its own
    clicks = [fn for fn in blocks.fns.values() if fn.targets and fn.targets[0][1] == "click"]
    assert len(clicks) == 2
    assert {fn.concurrency_id for fn in clicks} == {"paper"}
    assert {fn.concurrency_limit for fn in clicks} == {2}
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


def _upload(client: TestClient, extra: str = "") -> str:
    """An upload; a different ``extra`` makes a different file (Gradio names it by its hash)."""
    body = pc.demofile("json").read_bytes() + extra.encode()
    files = {"files": ("paper.json", body, "application/json")}
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


def test_a_run_over_the_time_limit_gets_a_plain_message(env: pytest.MonkeyPatch) -> None:
    env.setattr(ui, "check_paper", lambda *_a, **_k: time.sleep(30))
    tight = create_hosted_app(PORT + 1, config(job_timeout=0.5))
    with TestClient(tight, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        path = _upload(tc)
        start = time.perf_counter()
        events = _join(tc, _fn_index(tc), path, "h2")
        assert time.perf_counter() - start < 10
    assert hosting.TOO_SLOW in events
    assert not Path(path).exists()
    assert not multiprocessing.active_children()  # the run was stopped, not abandoned


def _analysis(*_a: Any, **_k: Any) -> Any:
    from pytacheck.app.run import Analysis

    return Analysis("x", [], "<p>x</p>", Path("x_report.html"), 0.1)


def test_both_buttons_share_the_limit_of_two_runs(env: pytest.MonkeyPatch) -> None:
    running = multiprocessing.Value("i", 0)
    peak = multiprocessing.Value("i", 0)

    def counted(*_a: Any, **_k: Any) -> Any:
        with running.get_lock():
            running.value += 1
            peak.value = max(peak.value, running.value)
        time.sleep(0.6)
        with running.get_lock():
            running.value -= 1
        return _analysis()

    env.setattr(ui, "check_paper", counted)
    shared = create_hosted_app(PORT + 2, config())
    answers: list[str] = []
    tc = TestClient(shared, base_url=BASE, follow_redirects=False)
    tc.__enter__()
    tc.get(f"/?token={TOKEN_A}")
    deps = tc.get("/config").json()["dependencies"]
    (check_fn,) = (int(d["id"]) for d in deps if len(d["inputs"]) == 2)
    (demo_fn,) = (int(d["id"]) for d in deps if len(d["inputs"]) == 1)
    uploads = [_upload(tc, str(n)) for n in range(3)]

    def click(button: str, number: int) -> None:
        if button == "check":
            answers.append(_join(tc, check_fn, uploads[number], f"{button}{number}"))
        else:
            answers.append(_join_demo(tc, demo_fn, f"{button}{number}"))

    threads = [
        threading.Thread(target=click, args=(button, n))
        for n in range(3)
        for button in ("check", "demo")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    tc.__exit__(None, None, None)
    assert len(answers) == 6, answers
    assert all("Ran 0 checks" in a for a in answers), [
        a[-300:] for a in answers if "Ran 0 checks" not in a
    ]
    assert peak.value == 2


def _join_demo(client: TestClient, fn_index: int, session: str) -> str:
    body = {"data": [False], "fn_index": fn_index, "session_hash": session}
    assert client.post("/gradio_api/queue/join", json=body).status_code == 200
    text = ""
    with client.stream("GET", "/gradio_api/queue/data", params={"session_hash": session}) as s:
        for line in s.iter_lines():
            text += line
            if '"process_completed"' in line:
                break
    return text


def test_the_upload_field_is_cleared_after_a_run(env: pytest.MonkeyPatch) -> None:
    env.setattr(ui, "check_paper", _analysis)
    shared = create_hosted_app(PORT + 3, config())
    with TestClient(shared, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        fn = _fn_index(tc)
        events = _join(tc, fn, _upload(tc), "h3")
        done = next(ln for ln in events.split("data: ") if '"process_completed"' in ln)
        output = json.loads(done)["output"]["data"]
        assert len(output) == 6 and output[-1] is None
        # a second click on a file that is gone says so in plain words
        gone = _upload(tc)
        Path(gone).unlink()
        assert ui.EXPIRED in _join(tc, fn, gone, "h4")


def _double(x: int, report: Any = None) -> int:
    if report:
        report(0.5, "half")
    return x * 2


def _fail(message: str) -> None:
    raise ValueError(message)


class _Odd(Exception):
    def __init__(self, a: str, b: str) -> None:  # cannot be rebuilt from its args
        super().__init__(f"{a}{b}")


def _fail_odd() -> None:
    raise _Odd("o", "dd")


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def test_job_runner_returns_the_result_and_deletes_the_upload(tmp_path: Path) -> None:
    upload = tmp_path / "up.pdf"
    upload.write_text("x")
    runner = hosting.JobRunner(20)
    assert runner.run(operator.add, 1, 2, upload=upload, on_timeout=TimeoutError) == 3
    assert not upload.exists()
    upload.write_text("x")
    with pytest.raises(ValueError, match="boom"):
        runner.run(_fail, "boom", upload=upload, on_timeout=TimeoutError)
    assert not upload.exists()
    with pytest.raises(RuntimeError, match="odd"):
        runner.run(_fail_odd, on_timeout=TimeoutError)
    seen: list[tuple[float, str]] = []
    assert (
        runner.run(_double, 4, on_timeout=TimeoutError, on_progress=lambda *a: seen.append(a)) == 8
    )
    assert seen == [(0.5, "half")]


def test_job_runner_works_with_spawn_the_way_the_server_runs(env: pytest.MonkeyPatch) -> None:
    env.setattr(hosting, "START_METHOD", "spawn")
    assert hosting.JobRunner(60).run(operator.add, 1, 2, on_timeout=TimeoutError) == 3


def test_job_runner_kills_a_run_at_the_limit(tmp_path: Path) -> None:
    upload = tmp_path / "up.pdf"
    upload.write_text("x")
    runner = hosting.JobRunner(0.5)
    for _ in range(4):  # runs that hang do not pile up, and do not hold up the next one
        start = time.perf_counter()
        with pytest.raises(TimeoutError):
            runner.run(_sleep, 30, upload=upload, on_timeout=TimeoutError)
        assert time.perf_counter() - start < 5
        assert not multiprocessing.active_children()
    assert not upload.exists()
    assert hosting.JobRunner(20).run(_double, 2, on_timeout=TimeoutError) == 4


def test_remove_upload_takes_its_folder_when_it_is_in_the_gradio_folder(
    tmp_path: Path, env: pytest.MonkeyPatch
) -> None:
    env.setenv("GRADIO_TEMP_DIR", str(tmp_path))
    folder = tmp_path / "5380f9d8"
    folder.mkdir()
    (folder / "paper.pdf").write_text("x")
    hosting.remove_upload(folder / "paper.pdf")
    assert not folder.exists() and tmp_path.exists()
    loose = tmp_path / "loose.pdf"
    loose.write_text("x")
    hosting.remove_upload(loose)
    assert tmp_path.exists() and not loose.exists()
    elsewhere = tmp_path.parent / f"{tmp_path.name}-other"
    elsewhere.mkdir()
    (elsewhere / "p.pdf").write_text("x")
    hosting.remove_upload(elsewhere / "p.pdf")
    assert elsewhere.exists()
    (folder).mkdir()
    (folder / "a.pdf").write_text("x")
    (folder / "b.pdf").write_text("x")
    hosting.remove_upload(folder / "a.pdf")
    assert (folder / "b.pdf").exists()


# --- behind a sign-in proxy ---------------------------------------------------------


def test_behind_a_sign_in_proxy_there_is_no_token() -> None:
    app = create_hosted_app(PORT, config(tokens=(), proxy_auth=True))
    with TestClient(app, base_url=BASE, follow_redirects=False) as tc:
        resp = tc.get("/")
        assert resp.status_code == 200 and "set-cookie" not in resp.headers
        # a token in the address is not needed and sets no cookie
        assert "set-cookie" not in tc.get(f"/?token={TOKEN_A}").headers
        # the other guards stay
        assert tc.get("/", headers={"host": "evil.example"}).status_code == 403
        assert tc.get("/", headers={"origin": "https://evil.example"}).status_code == 403
        assert tc.get("/gradio_api/file=https://example.com/x").status_code == 404


def test_behind_a_sign_in_proxy_the_user_header_is_required() -> None:
    app = create_hosted_app(PORT, config(tokens=(), proxy_auth=True, user_header="X-Auth-User"))
    with TestClient(app, base_url=BASE, follow_redirects=False) as tc:
        resp = tc.get("/")
        assert resp.status_code == 403 and resp.text == PROXY_DENIED_PAGE
        assert tc.get("/", headers={"x-auth-user": " "}).status_code == 403
        assert tc.get("/", headers={"X-Auth-User": "someone"}).status_code == 200
        assert tc.get("/healthz").json() == {"ok": True}


def test_the_proxy_guard_takes_no_tokens() -> None:
    from pytacheck.app.security import TokenGuard

    with pytest.raises(ValueError, match="no tokens"):
        TokenGuard(object(), tokens=(TOKEN_A,), hosts=(HOST,), proxy_auth=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="no tokens"):
        TokenGuard(object(), proxy_auth=True)  # type: ignore[arg-type]


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
    printed: list[dict[str, Any]] = []
    real_print = print

    def spy(*args: Any, **kwargs: Any) -> None:
        if args and str(args[0]).startswith("metacheck is serving"):
            printed.append(kwargs)
        real_print(*args, **kwargs)

    env.setattr(launch, "print", spy, raising=False)
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
        assert printed and printed[0].get("flush") is True  # a pipe shows it at once
        assert not state_dir.exists() or not any(state_dir.iterdir())


def _servers() -> list[Any]:
    import gc

    import uvicorn

    return [o for o in gc.get_objects() if isinstance(o, uvicorn.Server)]


def _fresh(before: list[Any]) -> list[Any]:
    return [s for s in _servers() if not any(s is old for old in before)]
