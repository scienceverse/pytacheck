"""Hosted mode: settings, tokens, cookie, hosts, limits, clean-up and texts."""

from __future__ import annotations

import functools
import importlib
import json
import logging
import multiprocessing
import operator
import os
import re
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

import metacheck as pc
from metacheck.app import bibr, launch, ui
from metacheck.app import hosted as hosting
from metacheck.app.security import HOSTED_COOKIE, HOSTED_DENIED_PAGE, PROXY_DENIED_PAGE
from metacheck.app.server import create_hosted_app

TOKEN_A = "a" * 32
TOKEN_B = "b-second-token-" + "x" * 20
HOST = "metacheck.example.org"
BASE = f"https://{HOST}"
PORT = 7861


@pytest.fixture(autouse=True, scope="module")
def _runs() -> Iterator[None]:
    """Runs start by spawn, as on the server. Fork is not safe in a process with threads
    (filelock refuses it while another thread holds a lock), so it stays a choice for a
    quick local run. The stand-ins in ``_hosted_jobs`` reach the run's process either way."""
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(Path(__file__).parent))
        # METACHECK_TEST_START=fork is faster where there is fork
        mp.setattr(hosting, "START_METHOD", os.environ.get("METACHECK_TEST_START", "spawn"))
        yield


def stand_in(env: pytest.MonkeyPatch, kind: str) -> Any:
    """Make the next hosted runs use the stand-in ``kind`` of ``_hosted_jobs`` for ``begin``."""
    module = importlib.import_module("_hosted_jobs")
    env.setattr(ui, "analyse_upload", functools.partial(module.run_as, kind))
    return module


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


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ({bibr.BACKEND_ENV: "platform"}, bibr.BAD_BACKEND),
        ({bibr.URL_ENV: "bibr.example.org"}, bibr.BAD_URL),
        ({bibr.URL_ENV: "http://bibr.example.org"}, bibr.PLAIN_HTTP),
    ],
)
def test_refuses_to_start_with_a_bibr_setting_that_cannot_work(
    env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    settings: dict[str, str],
    problem: str,
) -> None:
    env.setenv("METACHECK_APP_HOSTS", HOST)
    env.setenv("METACHECK_APP_TOKENS", TOKEN_A)
    for name, value in settings.items():
        env.setenv(name, value)
    assert launch.main(["--hosted"]) == 2
    assert capsys.readouterr().err.strip() == problem


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
    from metacheck.app.server import create_app

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
    from metacheck._version import __version__

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
    clicks = [fn for fn in blocks.fns.values() if fn.name in ("on_check", "on_demo")]
    assert len(clicks) == 2
    assert {fn.concurrency_id for fn in clicks} == {"paper"}
    assert {fn.concurrency_limit for fn in clicks} == {2}
    assert ui.build_app()._queue.default_concurrency_limit == 1


def test_no_state_file_is_written(client: TestClient, state_dir: Path) -> None:
    client.get(f"/?token={TOKEN_A}")
    assert not state_dir.exists() or not any(state_dir.iterdir())


# --- runs ---------------------------------------------------------------------------


#: the other inputs of both buttons: online, data check, PDF reader, bibr key, remember
SETTINGS = [False, False, "grobid", "", False]


def _join(
    client: TestClient, fn_index: int, path: str, session: str, settings: list[Any] = SETTINGS
) -> str:
    body = {
        "data": [{"path": path, "meta": {"_type": "gradio.FileData"}}, *settings],
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
    (dep,) = (d for d in deps if d["targets"][0][1] == "click" and len(d["inputs"]) == 6)
    return int(dep["id"])


def _upload(client: TestClient, extra: str) -> str:
    """An upload; a different ``extra`` makes a different file (Gradio names it by its hash).

    Each test uses its own ``extra``: a hosted run deletes its upload, and an upload with
    the same content would be the same file.
    """
    body = pc.demofile("json").read_bytes() + extra.encode()
    files = {"files": ("paper.json", body, "application/json")}
    (path,) = client.post("/gradio_api/upload", files=files).json()
    return str(path)


def test_a_run_leaves_nothing_behind(client: TestClient) -> None:
    client.get(f"/?token={TOKEN_A}")
    path = _upload(client, " " * 11)  # whitespace, so that the JSON stays valid
    assert Path(path).exists()
    events = _join(client, _fn_index(client), path, "h1")
    assert "Ran 16 checks" in events
    assert "data:text/html" in events  # the report comes with the page, not from a file
    assert not Path(path).exists()
    assert client.get("/report/h1/paper_report.html").status_code == 404


def test_a_run_over_the_time_limit_gets_a_plain_message(env: pytest.MonkeyPatch) -> None:
    stand_in(env, "sleep")
    tight = create_hosted_app(PORT + 1, config(job_timeout=0.5))
    with TestClient(tight, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        path = _upload(tc, "h2")
        start = time.perf_counter()
        events = _join(tc, _fn_index(tc), path, "h2")
        assert time.perf_counter() - start < 10
    assert hosting.TOO_SLOW in events
    assert not Path(path).exists()
    assert not multiprocessing.active_children()  # the run was stopped, not abandoned


def test_both_buttons_share_the_limit_of_two_runs(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = stand_in(env, "counted")
    log = tmp_path / "runs"
    log.mkdir()
    env.setenv(module.LOG_ENV, str(log))
    shared = create_hosted_app(PORT + 2, config())
    answers: list[str] = []
    tc = TestClient(shared, base_url=BASE, follow_redirects=False)
    tc.__enter__()
    tc.get(f"/?token={TOKEN_A}")
    deps = tc.get("/config").json()["dependencies"]
    (check_fn,) = (int(d["id"]) for d in deps if len(d["inputs"]) == 6)
    (demo_fn,) = (int(d["id"]) for d in deps if len(d["inputs"]) == 5)
    uploads = [_upload(tc, f"shared{n}") for n in range(3)]

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
    running = peak = 0
    events = sorted(
        (float(note.read_text()), 1 if note.name.startswith("start") else -1)
        for note in log.iterdir()
    )
    for _stamp, step in events:
        running += step
        peak = max(peak, running)
    assert len(events) == 12 and peak == 2


def _join_demo(client: TestClient, fn_index: int, session: str) -> str:
    body = {"data": SETTINGS, "fn_index": fn_index, "session_hash": session}
    assert client.post("/gradio_api/queue/join", json=body).status_code == 200
    text = ""
    with client.stream("GET", "/gradio_api/queue/data", params={"session_hash": session}) as s:
        for line in s.iter_lines():
            text += line
            if '"process_completed"' in line:
                break
    return text


def test_the_upload_field_is_cleared_after_a_run(env: pytest.MonkeyPatch) -> None:
    stand_in(env, "started")
    shared = create_hosted_app(PORT + 3, config())
    with TestClient(shared, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        fn = _fn_index(tc)
        events = _join(tc, fn, _upload(tc, "h3"), "h3")
        done = next(ln for ln in events.split("data: ") if '"process_completed"' in ln)
        output = json.loads(done)["output"]["data"]
        assert len(output) == 8 and output[-1] is None
        # a second click on a file that is gone says so in plain words
        gone = _upload(tc, "h4")
        Path(gone).unlink()
        assert ui.EXPIRED in _join(tc, fn, gone, "h4")


def _join_with_data(client: TestClient, path: str, session: str) -> str:
    body = {
        "data": [
            {"path": path, "meta": {"_type": "gradio.FileData"}},
            False,
            True,  # the data check
            "grobid",
            "",
            False,
        ],
        "fn_index": _fn_index(client),
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


def test_a_hosted_run_includes_the_data_check(env: pytest.MonkeyPatch) -> None:
    """On a shared server the data check is part of the run: one answer, with its row."""
    stand_in(env, "with_data")
    shared = create_hosted_app(PORT + 4, config())
    with TestClient(shared, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        text = _join_with_data(tc, _upload(tc, "h5"), "h5")
    done = next(ln for ln in text.split("data: ") if '"process_completed"' in ln)
    output = json.loads(done)["output"]["data"]
    assert "2 data files checked" in json.dumps(output)
    assert "Checking the shared data files" in text  # its progress reached the page
    assert "Downloading data.csv" in text
    assert output[-3] == "" and output[-2]["visible"] is False  # no status line, no Stop


def test_a_slow_data_check_is_stopped_before_the_limit(env: pytest.MonkeyPatch) -> None:
    """The other results come back, and the data check's row says why it has no result."""
    stand_in(env, "slow_data")
    budget, grace = ui.data_budget(10)
    assert (budget, grace) == (8, 1)
    assert ui.data_budget(900) == (840, 30)
    tight = create_hosted_app(PORT + 5, config(job_timeout=10))
    with TestClient(tight, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        text = _join_with_data(tc, _upload(tc, "h6"), "h6")
    assert hosting.TOO_SLOW not in text
    done = next(ln for ln in text.split("data: ") if '"process_completed"' in ln)
    assert ui.DATA_TOO_SLOW in json.dumps(json.loads(done)["output"]["data"])


def test_a_run_at_the_limit_leaves_no_report_behind(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scratch folder is the server's: it goes even when the run's process is killed."""
    made: list[Path] = []
    real = ui.scratch_dir

    def scratch() -> Any:
        folder = real()
        made.append(Path(folder.name))
        return folder

    env.setattr(ui, "scratch_dir", scratch)
    stand_in(env, "sleep")
    tight = create_hosted_app(PORT + 6, config(job_timeout=0.5))
    with TestClient(tight, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        assert hosting.TOO_SLOW in _join(tc, _fn_index(tc), _upload(tc, "h7"), "h7")
    assert made and not any(folder.exists() for folder in made)


def test_a_shared_server_never_offers_a_saved_key(env: pytest.MonkeyPatch) -> None:
    env.delenv(bibr.KEY_ENV, raising=False)
    bibr.save_key("saved-key-1234")
    text = json.dumps(ui.build_app(hosted=config()).get_config_file())
    assert "saved on this" not in text and "1234" not in text
    local = json.dumps(ui.build_app().get_config_file())
    assert "Remember the key on this computer" in local
    assert bibr.resolve_key("", remembered=False) == ""


@pytest.fixture
def bibr_serve() -> Iterator[tuple[str, list[tuple[str, str, str, bytes]]]]:
    """A stand-in for bibr serve on a port of this computer: its job API, for one paper.

    Gives its address and what it was asked: (method, path, authorization, body)."""
    paper = (Path(__file__).parents[1] / "bibr12" / "fixtures" / "bibr_12_1_full.json").read_bytes()
    seen: list[tuple[str, str, str, bytes]] = []

    class BibrServe(BaseHTTPRequestHandler):
        def _body(self) -> bytes:
            if "chunked" not in self.headers.get("Transfer-Encoding", "").lower():
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b""
            while size := int(self.rfile.readline().split(b";")[0].strip() or b"0", 16):
                body += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
            return body

        def _answer(self, status: int, body: Any) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _seen(self, body: bytes = b"") -> None:
            seen.append((self.command, self.path, self.headers.get("Authorization", ""), body))

        def do_POST(self) -> None:
            self._seen(self._body())
            if self.path == "/papers/jobs":
                queued = {"job_id": "j1", "status": "queued", "status_url": "/papers/jobs/j1"}
                self._answer(202, queued)
            else:
                self._answer(404, {"detail": "Not Found"})

        def do_GET(self) -> None:
            self._seen()
            if self.path == "/papers/jobs/j1":
                self._answer(200, {"job_id": "j1", "status": "succeeded"})
            elif self.path == "/papers/jobs/j1/result":
                self._answer(200, paper)
            else:
                self._answer(404, {"detail": "Not Found"})

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), BibrServe)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_a_hosted_server_sends_a_pdf_to_bibr_serve(
    env: pytest.MonkeyPatch, bibr_serve: tuple[str, list[tuple[str, str, str, bytes]]]
) -> None:
    """With ``PYTACHECK_BIBR_URL`` and the key of the settings, a PDF goes to bibr serve's job
    API from the run's process, and the checks run on the paper that comes back."""
    url, seen = bibr_serve
    key = "server-key-" + "k" * 24
    env.setenv(bibr.URL_ENV, url)
    env.setenv(bibr.KEY_ENV, key)
    shared = create_hosted_app(PORT + 7, config())
    with TestClient(shared, base_url=BASE, follow_redirects=False) as tc:
        tc.get(f"/?token={TOKEN_A}")
        pdf = {"files": ("paper.pdf", b"%PDF-1.4 h8 not really", "application/pdf")}
        (path,) = tc.post("/gradio_api/upload", files=pdf).json()
        events = _join(tc, _fn_index(tc), path, "h8", [False, False, "bibr", "", False])
    assert re.search(r"Ran \d+ checks", events), events[-500:]
    assert key not in events
    assert [(method, where) for method, where, _, _ in seen] == [
        ("POST", "/papers/jobs"),
        ("GET", "/papers/jobs/j1"),
        ("GET", "/papers/jobs/j1/result"),
    ]
    assert all(auth == f"Bearer {key}" for _, _, auth, _ in seen)
    assert b"%PDF-1.4 h8" in seen[0][3]
    assert not Path(path).exists()


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
    from metacheck.app.security import TokenGuard

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
    from metacheck import config as lib_config

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
    import uvicorn

    servers: list[uvicorn.Server] = []
    up = threading.Event()

    class Recorded(uvicorn.Server):
        """The server ``_serve`` makes, handed to the test as it is made and once it is up."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            servers.append(self)

        async def startup(self, sockets: Any = None) -> None:
            await super().startup(sockets)
            if self.started:
                up.set()

    env.setattr(uvicorn, "Server", Recorded)
    result: list[int] = []
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            result.append(launch._serve(0, False, cfg))
        except BaseException as exc:  # shown by the assertion, not lost on the thread
            errors.append(exc)

    # port 0: the server's own socket gets a free port, so no other socket can take it
    # between a probe and the bind
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    from metacheck.app import state as saved

    assert up.wait(30), (thread.is_alive(), result, errors, capfd.readouterr())
    found = servers[0].servers[0].sockets[0].getsockname()[1]
    token = TOKEN_A if hosted else (saved.read_state() or {})["token"]
    headers = {"host": HOST if hosted else f"127.0.0.1:{found}"}
    with httpx.Client(trust_env=False, follow_redirects=False) as tc:
        resp = tc.get(f"http://127.0.0.1:{found}/?token={token}&a=1", headers=headers)
        assert resp.status_code == 303
        tc.get(f"http://127.0.0.1:{found}/healthz", headers=headers)
    for server in servers:
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
