"""Port of tests/testthat/test-import-bibr.R (remote bibr client and author helpers)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.io.bibr_convert import (
    BibrRequestError,
    _bibr_isalive,
    _bibr_save_result,
    _coerce_bib_authors,
    _parse_author_string,
    convert_bibr,
    format_bib_authors,
)
from tests.io.conftest import api, json_response

SCIVRS_URL = "https://platform.metacheck.app"
SELFHOSTED_URL = "http://localhost:8000"
HOSTED_URL = "https://bibr.example"
TOKEN = "bibr_pat_0123456789abcdef"  # a made-up personal token
#: what bibr serve RC (main 7483212) returns for a paper, cut down to its root keys
PAPER = {"paper_id": "to_err_is_human", "schema_version": "12.1", "metadata": {"title": "T"}}


def _form_fields(request: httpx.Request) -> dict[str, str]:
    """The non-file fields of a multipart request."""
    body = request.read()
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields = {}
    for part in body.split(b"--" + boundary)[1:-1]:
        head, _, content = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]*)"', head)
        if name and b"filename=" not in head:
            fields[name.group(1).decode()] = content[:-2].decode()
    return fields


def _scripted(answer: httpx.Response | Exception) -> httpx.Response:
    """A scripted answer: a response, or a transport failure to raise."""
    if isinstance(answer, Exception):
        raise answer
    return answer


class Recorder:
    """A mock bibr server that records requests, with two dialects.

    * The Scienceverse platform (backend ``scivrs``): ``POST /jobs`` answers 200 with a job
      id, ``GET /jobs/{id}`` a ``status`` of ``complete`` (or *status*), ``/result`` the paper.
    * bibr serve RC's API (bibr-gate mirrors it; backends ``bibr`` and ``selfhosted``), as
      ``bibr/serve/jobs.py``, ``ingress.py`` and ``admission.py`` answer it:
      ``POST /papers/extract`` (200, the paper); ``POST /papers/jobs`` (202
      ``{job_id, status: queued, status_url}``); ``GET /papers/jobs/{id}`` (a status of
      queued, running, succeeded or failed, plus ``result_url`` or ``error``);
      ``GET /papers/jobs/{id}/result`` (409 while the job is not done, else the paper or
      the job's own error status); ``GET /ready`` (anonymous: ``{"status": "ready"}``).
      With *key* every ``/papers/...`` route wants ``Authorization: Bearer <key>`` (401
      and ``WWW-Authenticate`` otherwise).

    *states* are the statuses successive polls of a job report (the last one repeats);
    *submit_errors* are answers (or exceptions) the POST routes give before they accept a
    document; *poll_errors* the same for status polls; *result_409* is how many result
    requests answer 409 even for a finished job; *result_errors* are answers (or exceptions)
    the result request gives before it serves the paper; *job_id* and *status_url* are what
    an accepted submission returns.
    """

    def __init__(
        self,
        status: str = "complete",
        *,
        key: str | None = None,
        states: tuple[str, ...] = ("queued", "running", "succeeded"),
        submit_errors: list[httpx.Response | Exception] | None = None,
        poll_errors: list[httpx.Response | Exception] | None = None,
        result_409: int = 0,
        result_errors: list[httpx.Response | Exception] | None = None,
        error: dict[str, Any] | None = None,
        error_status: int = 422,
        job_id: str = "job-1",
        status_url: str = "/papers/jobs/job-1",
        paper: Any = PAPER,
    ) -> None:
        self.urls: list[str] = []
        self.fields: dict[str, str] | None = None
        self.headers: list[httpx.Headers] = []
        self.status = status
        self.key = key
        self.states = states
        self.submit_errors = list(submit_errors or [])
        self.poll_errors = list(poll_errors or [])
        self.result_409 = result_409
        self.result_errors = list(result_errors or [])
        self.error = error or {
            "message": "The model ran out of tokens",
            "error_code": "llm_truncated",
        }
        self.error_status = error_status
        self.job_id = job_id
        self.status_url = status_url
        self.paper = paper
        self.polls = 0

    @property
    def posts(self) -> list[str]:
        return [u for u in self.urls if u.endswith(("/jobs", "/papers/extract"))]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        self.urls.append(url)
        self.headers.append(request.headers)
        if request.method == "POST":
            self.fields = _form_fields(request)
        if path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if "/papers/" in path:
            return self._rc(request, path)
        if url.endswith("/jobs"):
            return httpx.Response(200, json={"job_id": "test-job"})
        if url.endswith("/result"):
            return httpx.Response(200, json={"text": "mock"})
        return httpx.Response(200, json={"status": self.status, "stage": "parsing"})

    def _rc(self, request: httpx.Request, path: str) -> httpx.Response:
        if self.key is not None:
            auth = request.headers.get("authorization", "")
            detail = (
                "Missing bearer token"
                if not auth.lower().startswith("bearer ")
                else None
                if auth[7:] == self.key
                else "Invalid bearer token"
            )
            if detail:
                return httpx.Response(
                    401, json={"detail": detail}, headers={"WWW-Authenticate": "Bearer"}
                )
        if path.endswith(("/papers/extract", "/papers/jobs")):
            if self.submit_errors:
                return _scripted(self.submit_errors.pop(0))
            if path.endswith("/papers/extract"):
                return httpx.Response(200, json=self.paper)
            return httpx.Response(
                202,
                json={
                    "job_id": self.job_id,
                    "status": "queued",
                    "status_url": self.status_url,
                },
            )
        result = path.endswith("/result")
        job = path.split("/papers/jobs/")[1].removesuffix("/result")
        if not result:
            if self.poll_errors:
                return _scripted(self.poll_errors.pop(0))
            self.polls += 1
        state = self.states[min(max(self.polls, 1), len(self.states)) - 1]
        if not result:
            body: dict[str, Any] = {"job_id": job, "status": state, "filename": "a.pdf"}
            if state == "succeeded":
                body["result_url"] = f"/papers/jobs/{job}/result"
            if state == "failed":
                body["error"] = self.error
            return httpx.Response(200, json=body)
        if self.result_errors:
            return _scripted(self.result_errors.pop(0))
        if state in ("queued", "running") or self.result_409 > 0:
            self.result_409 -= 1
            return httpx.Response(409, json={"detail": "job not finished", "status": state})
        if state == "failed":
            return httpx.Response(self.error_status, json=self.error)
        return httpx.Response(200, json=self.paper)


def _serve(rec: Recorder) -> Any:
    return api(routes={("GET", re.compile(".*")): rec, ("POST", re.compile(".*")): rec})


@pytest.fixture
def server() -> Any:
    rec = Recorder()
    with _serve(rec):
        yield rec


@pytest.fixture
def pdf() -> Path:
    return pc.demofile("pdf")


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The waits the client asks for (nothing really sleeps)."""
    import pytacheck.http as http

    waited: list[float] = []
    monkeypatch.setattr(http, "sleep", waited.append)
    return waited


def test_convert_bibr_bad_arg() -> None:
    with pytest.raises(TypeError):
        convert_bibr()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="should be one of"):
        convert_bibr(pc.demofile("pdf"), backend="cloud")


def test_selfhosted_include_figures(server: Recorder, pdf: Path, tmp_path: Path) -> None:
    out = convert_bibr(pdf, tmp_path, backend="selfhosted", include_figures=True)
    assert server.fields["include_figures"] == "true"
    assert out == f"{tmp_path}/to_err_is_human.json"
    assert json.loads(Path(out).read_bytes()) == PAPER

    convert_bibr(pdf, tmp_path, backend="selfhosted", include_figures=False)
    assert server.fields["include_figures"] == "false"
    assert server.urls[-1] == f"{SELFHOSTED_URL}/papers/extract"
    assert "authorization" not in server.headers[-1]


def test_selfhosted_page_params(server: Recorder, pdf: Path, tmp_path: Path) -> None:
    convert_bibr(pdf, tmp_path, backend="selfhosted", start_page=3, end_page=10)
    # zero-based: start_page 3 -> 2, end_page 10 -> 9
    assert server.fields["start_page"] == "2"
    assert server.fields["end_page"] == "9"

    # defaults omit page fields entirely
    convert_bibr(pdf, tmp_path, backend="selfhosted")
    assert "start_page" not in server.fields
    assert "end_page" not in server.fields

    # start_page = 1 is the default, still omitted
    convert_bibr(pdf, tmp_path, backend="selfhosted", start_page=1, end_page=5)
    assert "start_page" not in server.fields
    assert server.fields["end_page"] == "4"


def test_scivrs_include_figures(
    server: Recorder, pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test_key")
    out = convert_bibr(pdf, tmp_path, backend="scivrs", include_figures=True)
    assert server.fields["include_figures"] == "true"
    assert server.urls == [
        f"{SCIVRS_URL}/jobs",
        f"{SCIVRS_URL}/jobs/test-job",
        f"{SCIVRS_URL}/jobs/test-job/result",
    ]
    assert all(h["authorization"] == "Bearer sv_test_key" for h in server.headers)
    assert Path(out).read_bytes() == b'{"text":"mock"}'

    convert_bibr(pdf, tmp_path, backend="scivrs", include_figures=False)
    assert server.fields["include_figures"] == "false"


def test_scivrs_page_params(
    server: Recorder, pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test_key")
    convert_bibr(pdf, tmp_path, backend="scivrs", start_page=2, end_page=5)
    assert server.fields["start_page"] == "1"
    assert server.fields["end_page"] == "4"

    convert_bibr(pdf, tmp_path, backend="scivrs")
    assert "start_page" not in server.fields
    assert "end_page" not in server.fields


def test_scivrs_needs_key(pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIVRS_API_KEY", "")
    with pytest.raises(ValueError, match="API key not set"):
        convert_bibr(pdf, tmp_path, backend="scivrs")


def test_scivrs_job_failure_and_timeout(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test_key")
    with _serve(Recorder(status="failed")):
        with pytest.raises(RuntimeError, match="Job test-job failed: parsing"):
            convert_bibr(pdf, tmp_path, backend="scivrs")
    with _serve(Recorder(status="queued")):
        with pytest.raises(TimeoutError, match=r"timed out after 6s \(last status: queued\)"):
            convert_bibr(pdf, tmp_path, backend="scivrs", poll_interval=2, timeout=6)


def test_auto_detects_backend(
    server: Recorder, pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # no key set -> selfhosted (no auth needed)
    monkeypatch.setenv("SCIVRS_API_KEY", "")
    convert_bibr(pdf, tmp_path)
    assert server.urls[-1].startswith(SELFHOSTED_URL)

    # SCIVRS_API_KEY set -> scivrs
    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test")
    convert_bibr(pdf, tmp_path)
    assert server.urls[-1].startswith(SCIVRS_URL)

    # explicit api_key -> scivrs
    monkeypatch.setenv("SCIVRS_API_KEY", "")
    convert_bibr(pdf, tmp_path, api_key="sv_explicit")
    assert server.urls[-1].startswith(SCIVRS_URL)


# the "bibr" backend: bibr serve RC's job API, also bibr-gate's ------------------------------


def _bibr(pdf: Any, tmp_path: Path, **kwargs: Any) -> Any:
    """``convert_bibr(backend="bibr")`` against the hosted service, with a token."""
    kwargs.setdefault("api_url", HOSTED_URL)
    kwargs.setdefault("api_key", TOKEN)
    return convert_bibr(pdf, tmp_path, backend="bibr", **kwargs)


def _busy(retry_after: str | None = "3") -> httpx.Response:
    """RC's 429 (upload admission or job cap); the job cap sends no Retry-After."""
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(429, json={"detail": "Too many active uploads"}, headers=headers)


def test_bibr_happy_path(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    rec = Recorder(key=TOKEN)
    with _serve(rec):
        out = _bibr(pdf, tmp_path, include_figures=True, start_page=2, end_page=5)
    assert out == f"{tmp_path}/to_err_is_human.json"
    assert json.loads(Path(out).read_bytes()) == PAPER
    job = f"{HOSTED_URL}/papers/jobs/job-1"
    assert rec.urls == [f"{HOSTED_URL}/papers/jobs", job, job, job, f"{job}/result"]
    assert all(h["authorization"] == f"Bearer {TOKEN}" for h in rec.headers)
    # pages are zero-based, as in the other backends; nothing else is sent
    assert rec.fields == {"include_figures": "true", "start_page": "1", "end_page": "4"}
    # queued, running, succeeded: the wait grows between polls (2 s, then 1.5 times that)
    assert sleeps == [2, 3]


def test_bibr_poll_wait_grows_to_a_ceiling(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    states = ("queued",) * 8 + ("succeeded",)
    with _serve(Recorder(states=states)):
        _bibr(pdf, tmp_path, api_key=None)
    assert sleeps == [2, 3, 4.5, 6.75, 10, 10, 10, 10]


def test_bibr_keyless_local_server(pdf: Path, tmp_path: Path) -> None:
    """A local bibr serve without AUTH_API_KEY: no token, no Authorization, plain http."""
    rec = Recorder()
    with _serve(rec):
        convert_bibr(pdf, tmp_path, backend="bibr")
    assert rec.urls[0] == f"{SELFHOSTED_URL}/papers/jobs"
    assert all("authorization" not in h for h in rec.headers)


def test_bibr_environment(pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIBR_URL", f"{HOSTED_URL}/")
    monkeypatch.setenv("BIBR_API_KEY", TOKEN)
    rec = Recorder(key=TOKEN)
    with _serve(rec):
        convert_bibr(pdf, tmp_path, backend="bibr")
    assert rec.urls[0] == f"{HOSTED_URL}/papers/jobs"

    # the arguments beat the environment
    other = Recorder(key="another-token-value")
    with _serve(other):
        convert_bibr(
            pdf,
            tmp_path,
            backend="bibr",
            api_url="https://other.example",
            api_key="another-token-value",
        )
    assert other.urls[0] == "https://other.example/papers/jobs"


def test_bibr_environment_names(pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BIBR_API_URL is the name bibr's own example clients read; BIBR_URL wins over it."""
    monkeypatch.setenv("BIBR_API_URL", "https://second.example")
    rec = Recorder()
    with _serve(rec):
        convert_bibr(pdf, tmp_path, backend="bibr")
    assert rec.urls[0] == "https://second.example/papers/jobs"

    monkeypatch.setenv("BIBR_URL", "https://first.example")
    rec = Recorder()
    with _serve(rec):
        convert_bibr(pdf, tmp_path, backend="bibr")
    assert rec.urls[0] == "https://first.example/papers/jobs"


def test_bibr_api_url_alone_does_not_pick_a_backend(
    server: Recorder, pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BIBR_API_URL is set for bibr's own notebooks, so it must not move backend="auto"."""
    monkeypatch.setenv("BIBR_API_URL", "http://192.168.1.20:8000")
    monkeypatch.setenv("BIBR_API_KEY", TOKEN)
    convert_bibr(pdf, tmp_path, backend="auto", api_url=SELFHOSTED_URL)  # nothing is refused
    server.urls.clear()
    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test")  # the platform user's setup, as before
    convert_bibr(pdf, tmp_path)
    assert server.urls[0] == f"{SCIVRS_URL}/jobs"
    monkeypatch.delenv("SCIVRS_API_KEY")


def test_auto_backend_and_bibr_environment(
    server: Recorder, pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test")  # the environment's BIBR_* still wins
    monkeypatch.setenv("BIBR_URL", HOSTED_URL)
    monkeypatch.setenv("BIBR_API_KEY", TOKEN)
    convert_bibr(pdf, tmp_path)
    assert server.urls[0] == f"{HOSTED_URL}/papers/jobs"
    assert server.headers[0]["authorization"] == f"Bearer {TOKEN}"

    # an api_url that was passed is the caller's word: BIBR_URL does not pick the backend
    server.urls.clear()
    convert_bibr(pdf, tmp_path, api_url="http://localhost:9000")
    assert server.urls[0] == "http://localhost:9000/jobs"

    # BIBR_API_KEY alone (no BIBR_URL): a SCIVRS_API_KEY chose the platform before the "bibr"
    # backend existed, and still does
    monkeypatch.delenv("BIBR_URL")
    server.urls.clear()
    convert_bibr(pdf, tmp_path)
    assert server.urls[0] == f"{SCIVRS_URL}/jobs"

    # ... and where there is no other key it is bibr serve on this machine, with the token
    # (loopback http is fine)
    monkeypatch.delenv("SCIVRS_API_KEY")
    server.urls.clear()
    server.headers.clear()
    convert_bibr(pdf, tmp_path)
    assert server.urls[0] == f"{SELFHOSTED_URL}/papers/jobs"
    assert server.headers[0]["authorization"] == f"Bearer {TOKEN}"

    # a key that was passed is the caller's word too (the platform's, as before)
    server.urls.clear()
    convert_bibr(pdf, tmp_path, api_key="sv_explicit", api_url=SCIVRS_URL)
    assert server.urls[0] == f"{SCIVRS_URL}/jobs"


def test_bibr_429_retry_after(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    rec = Recorder(key=TOKEN, submit_errors=[_busy("3"), _busy("1")])
    with _serve(rec):
        out = _bibr(pdf, tmp_path)
    assert len(rec.posts) == 3
    assert sleeps[:2] == [3, 1]  # then the polls' waits
    assert Path(out).exists()


def test_bibr_429_without_retry_after_backs_off(
    pdf: Path, tmp_path: Path, sleeps: list[float]
) -> None:
    rec = Recorder(submit_errors=[_busy(None)] * 3)
    with _serve(rec):
        _bibr(pdf, tmp_path, api_key=None)
    assert sleeps[:3] == [1, 2, 4]


def test_bibr_429_gives_up_after_max_retries(
    pdf: Path, tmp_path: Path, sleeps: list[float]
) -> None:
    rec = Recorder(submit_errors=[_busy("1")] * 10)
    with _serve(rec), pytest.raises(BibrRequestError, match="HTTP 429 Too Many Requests") as err:
        _bibr(pdf, tmp_path, max_retries=2)
    assert len(rec.posts) == 3
    assert sleeps == [1, 1]
    assert err.value.status_code == 429
    assert "Too many active uploads" in str(err.value)


def test_bibr_429_long_retry_after_is_not_waited_out(
    pdf: Path, tmp_path: Path, sleeps: list[float]
) -> None:
    """A quota that resets in an hour is an error, not a sleep (the shared layer would sleep)."""
    rec = Recorder(submit_errors=[_busy("3600")])
    with _serve(rec), pytest.raises(BibrRequestError, match=r"asks to wait 3600 s") as err:
        _bibr(pdf, tmp_path, timeout=10**6)  # the budget is not what stops it
    assert len(rec.posts) == 1
    assert sleeps == []
    assert err.value.retry_after == 3600

    # the longest wait for one retry is a setting
    rec = Recorder(submit_errors=[_busy("6"), _busy("5")])
    with _serve(rec), pytest.raises(BibrRequestError, match="asks to wait 6 s"):
        _bibr(pdf, tmp_path, max_retry_wait=5)
    assert sleeps == []
    rec = Recorder(submit_errors=[_busy("5")])
    with _serve(rec):
        _bibr(pdf, tmp_path, max_retry_wait=5)
    assert sleeps[0] == 5


def test_bibr_429_waits_stay_within_timeout(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    rec = Recorder(submit_errors=[_busy("8"), _busy("8")])
    with _serve(rec), pytest.raises(BibrRequestError, match="HTTP 429"):
        _bibr(pdf, tmp_path, timeout=10)
    assert sleeps == [8]
    assert len(rec.posts) == 2


def test_bibr_429_on_a_status_poll(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    rec = Recorder(poll_errors=[_busy("5")])
    with _serve(rec):
        _bibr(pdf, tmp_path)
    assert sleeps[0] == 5


def test_bibr_host_reset_from_another_caller_is_bounded(
    pdf: Path, tmp_path: Path, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared layer remembers a 429 that any caller got (the readiness probe retries one)
    and sleeps until the reset before every request to the host: bound it like a Retry-After."""
    import pytacheck.http as http

    monkeypatch.setattr(http, "_host_reset", {})
    http._record_reset("bibr.example", time.time() + 7200)
    rec = Recorder()
    with _serve(rec), pytest.raises(BibrRequestError, match=r"asked to wait 7[12]\d\d s") as err:
        _bibr(pdf, tmp_path, timeout=10**6)
    assert err.value.status_code == 429
    assert err.value.retry_after is not None and err.value.retry_after > 7000
    assert rec.urls == []  # nothing was sent
    assert sleeps == []

    # within the bounds it is slept here, and used up from the session's budget
    monkeypatch.setattr(http, "_host_reset", {})
    http._record_reset("bibr.example", time.time() + 30)
    with _serve(Recorder()):
        assert Path(_bibr(pdf, tmp_path)).exists()
    assert 29 < sleeps[0] <= 30
    monkeypatch.setattr(http, "_host_reset", {})
    http._record_reset("bibr.example", time.time() + 30)
    with _serve(Recorder()), pytest.raises(BibrRequestError, match="asked to wait"):
        _bibr(pdf, tmp_path, timeout=20)  # 30 s would overrun the whole timeout


def test_retry_after_forms() -> None:
    import email.utils
    import time

    from pytacheck.io.bibr_convert import _retry_after

    def value(header: str | None) -> float | None:
        headers = {} if header is None else {"Retry-After": header}
        return _retry_after(httpx.Response(429, headers=headers))

    assert value("5") == 5
    assert value(" 0 ") == 0
    assert value("2.5") == 2.5
    assert value(None) is None
    assert value("") is None
    assert value("soon") is None
    assert value("-3") is None
    assert value("nan") is None
    assert value("inf") is None
    assert value("Sun, 06 Nov 1994 08:49:37 GMT") == 0  # a date in the past: no wait
    ahead = value(email.utils.formatdate(time.time() + 30, usegmt=True))
    assert ahead is not None
    assert 25 < ahead <= 30


def test_bibr_result_409_means_not_ready(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    """The job says succeeded, the result says 409 (twice): keep polling, do not fail."""
    rec = Recorder(key=TOKEN, states=("succeeded",), result_409=2)
    with _serve(rec):
        out = _bibr(pdf, tmp_path)
    assert json.loads(Path(out).read_bytes()) == PAPER
    assert [u for u in rec.urls if u.endswith("/result")] == [
        f"{HOSTED_URL}/papers/jobs/job-1/result"
    ] * 3
    assert sleeps == [2, 3]


def test_bibr_failed_job(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(states=("running", "failed"))
    with _serve(rec):
        with pytest.raises(
            RuntimeError, match=r"Job job-1 failed: The model ran out of tokens \(llm_truncated\)"
        ):
            _bibr(pdf, tmp_path, api_key=None)
    assert not rec.urls[-1].endswith("/result")  # a failed job is not fetched

    plain = Recorder(states=("failed",), error={"detail": "internal job error"})
    with _serve(plain), pytest.raises(RuntimeError, match="Job job-1 failed: internal job error"):
        _bibr(pdf, tmp_path, api_key=None)


def test_bibr_timeout(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    with _serve(Recorder(states=("queued",))):
        with pytest.raises(
            TimeoutError, match=r"timed out after 10s \(last status: queued\)"
        ) as err:
            _bibr(pdf, tmp_path, api_key=None, poll_interval=2, timeout=10)
    # the job runs on: it keeps one of the user's active-job slots until it ends
    assert "limit on active jobs" in str(err.value)
    assert sleeps == [2, 3, 4.5, 0.5]  # the last wait ends at the deadline
    assert sum(sleeps) == 10


def test_bibr_server_text_is_clean_in_failure_and_timeout(pdf: Path, tmp_path: Path) -> None:
    """The job id comes from the server: control characters must not reach the messages."""
    dirty = "j\x1b[2J\nFAKE LINE"
    with _serve(Recorder(job_id=dirty, states=("failed",))):
        with pytest.raises(RuntimeError, match=r"Job j \[2J FAKE LINE failed") as err:
            _bibr(pdf, tmp_path, api_key=None)
    assert str(err.value).isprintable()
    with _serve(Recorder(job_id=dirty, states=("queued",))):
        with pytest.raises(TimeoutError, match=r"Job j \[2J FAKE LINE timed out") as err:
            _bibr(pdf, tmp_path, api_key=None, timeout=1)
    assert str(err.value).isprintable()


@pytest.mark.parametrize("poll_interval", [0, -1, float("nan"), float("inf")])
def test_bibr_poll_interval_must_be_positive(
    poll_interval: float, pdf: Path, tmp_path: Path
) -> None:
    """0 polled the server in a tight loop (10 000 requests a second) until the timeout."""
    rec = Recorder()
    with _serve(rec), pytest.raises(ValueError, match="poll_interval must be a positive"):
        _bibr(pdf, tmp_path, poll_interval=poll_interval)
    assert rec.urls == []  # refused before anything is sent
    with _serve(Recorder(states=("succeeded",))):
        assert Path(_bibr(pdf, tmp_path, poll_interval=0.01)).exists()


def test_bibr_session_repr_keeps_the_token_out() -> None:
    from pytacheck.io.bibr_convert import _Session

    sess = _Session(HOSTED_URL, TOKEN, 600, 5, 120)
    assert TOKEN not in repr(sess)
    assert HOSTED_URL in repr(sess)
    assert sess.headers["Authorization"] == f"Bearer {TOKEN}"


def test_bibr_unknown_status_keeps_polling(pdf: Path, tmp_path: Path) -> None:
    """A later bibr may add states; a job in an unknown one is waited for, not failed."""
    with _serve(Recorder(states=("starting", "succeeded"))):
        assert Path(_bibr(pdf, tmp_path, api_key=None)).exists()


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (400, "malformed"),
        (403, "refused"),
        (404, "BIBR_URL"),
        (413, "50 MiB"),
        (415, "PDF files only"),
        (422, "fails the same way"),
        (500, "failed while handling"),
        (502, "not available"),
        (503, "try again later"),
        (507, "no room"),
    ],
)
def test_bibr_submit_errors_are_explained(
    status: int, words: str, pdf: Path, tmp_path: Path
) -> None:
    body = {"detail": "the server's detail"}
    rec = Recorder(submit_errors=[httpx.Response(status, json=body)])
    with _serve(rec), pytest.raises(BibrRequestError) as err:
        _bibr(pdf, tmp_path)
    message = str(err.value)
    assert message.startswith(f"HTTP {status} ")
    assert "while submitting the job" in message
    assert words in message
    assert 'The server said: "the server\'s detail".' in message
    assert err.value.status_code == status
    assert TOKEN not in message
    assert len(rec.posts) == 1  # no retry: the upload may have been taken


def test_bibr_gate_error_body(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(submit_errors=[httpx.Response(502, json={"error": "upstream_unavailable"})])
    with _serve(rec), pytest.raises(BibrRequestError, match='said: "upstream_unavailable"'):
        _bibr(pdf, tmp_path)


def test_bibr_401(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(key="the-real-token-value")
    with _serve(rec), pytest.raises(BibrRequestError) as err:
        _bibr(pdf, tmp_path)  # the wrong token
    message = str(err.value)
    assert err.value.status_code == 401
    assert "HTTP 401 Unauthorized while submitting the job" in message
    assert "BIBR_API_KEY" in message
    assert 'The server said: "Invalid bearer token"' in message
    assert TOKEN not in message
    assert len(rec.urls) == 1

    with _serve(rec), pytest.raises(BibrRequestError, match="No API token was sent") as err:
        _bibr(pdf, tmp_path, api_key=None)
    assert err.value.status_code == 401


def test_bibr_error_text_is_clean_and_keeps_the_token_out(pdf: Path, tmp_path: Path) -> None:
    hostile = {"detail": f"bad {TOKEN}\n\x1b[31m{'x' * 1000}"}
    rec = Recorder(submit_errors=[httpx.Response(400, json=hostile)])
    with _serve(rec), pytest.raises(BibrRequestError) as err:
        _bibr(pdf, tmp_path)
    message = str(err.value)
    assert TOKEN not in message
    assert "\x1b" not in message
    assert "\n" not in message
    assert len(message) < 700


def test_bibr_401_while_polling(pdf: Path, tmp_path: Path) -> None:
    """The token is revoked (or expires) after the job was accepted."""
    rec = Recorder(poll_errors=[httpx.Response(401, json={"detail": "Invalid bearer token"})])
    with (
        _serve(rec),
        pytest.raises(BibrRequestError, match=r"HTTP 401 .* while checking job job-1"),
    ):
        _bibr(pdf, tmp_path)


def test_bibr_job_gone(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(poll_errors=[httpx.Response(404, json={"detail": "job not found"})])
    with _serve(rec), pytest.raises(BibrRequestError, match="expired or was evicted") as err:
        _bibr(pdf, tmp_path)
    assert err.value.status_code == 404


def test_bibr_flaky_polls_are_tolerated(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    bad = [
        httpx.Response(502, json={"error": "upstream_unavailable"}),
        httpx.ConnectError("boom"),
        httpx.Response(504),
    ]
    with _serve(Recorder(poll_errors=bad)):
        out = _bibr(pdf, tmp_path)
    assert Path(out).exists()


def test_bibr_flaky_result_fetch_is_tolerated(
    pdf: Path, tmp_path: Path, sleeps: list[float]
) -> None:
    """The job is done on the server (and counted against a quota): one 502 or one timeout
    on the fetch must not throw the paper away."""
    for hiccup in (
        httpx.Response(502, json={"error": "upstream_unavailable"}),
        httpx.Response(503),
        httpx.Response(504),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("boom"),
    ):
        rec = Recorder(result_errors=[hiccup])
        with _serve(rec):
            out = _bibr(pdf, tmp_path)
        assert json.loads(Path(out).read_bytes()) == PAPER
        assert len([u for u in rec.urls if u.endswith("/result")]) == 2, hiccup
        assert len(rec.posts) == 1  # the file was uploaded once

    # a few in a row are fine too, mixed with 409
    rec = Recorder(result_errors=[httpx.Response(502)] * 3 + [httpx.ConnectError("boom")])
    with _serve(rec):
        assert Path(_bibr(pdf, tmp_path)).exists()


def test_bibr_result_fetch_gives_up_on_a_dead_server(pdf: Path, tmp_path: Path) -> None:
    down = [httpx.Response(502, json={"error": "upstream_unavailable"})] * 6
    rec = Recorder(result_errors=down)
    with _serve(rec):
        with pytest.raises(
            BibrRequestError, match="HTTP 502 Bad Gateway while fetching the result"
        ):
            _bibr(pdf, tmp_path)
    # the polls in between succeed, yet the failed fetches still add up (they were not reset)
    assert len([u for u in rec.urls if u.endswith("/result")]) == 6
    gone: list[httpx.Response | Exception] = [httpx.ConnectError("boom")] * 6
    with _serve(Recorder(result_errors=gone)):
        with pytest.raises(ConnectionError, match="Failed to perform HTTP request"):
            _bibr(pdf, tmp_path)


def test_bibr_result_fetch_errors_that_are_not_outages_raise(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(result_errors=[httpx.Response(404, json={"detail": "job not found"})])
    with _serve(rec), pytest.raises(BibrRequestError, match="expired or was evicted") as err:
        _bibr(pdf, tmp_path)
    assert err.value.status_code == 404
    assert len([u for u in rec.urls if u.endswith("/result")]) == 1  # not retried


def test_bibr_polls_give_up_on_a_dead_server(pdf: Path, tmp_path: Path) -> None:
    down = [httpx.Response(502, json={"error": "upstream_unavailable"})] * 6
    with _serve(Recorder(poll_errors=down)):
        with pytest.raises(BibrRequestError, match="HTTP 502 Bad Gateway while checking job"):
            _bibr(pdf, tmp_path)
    gone: list[httpx.Response | Exception] = [httpx.ConnectError("boom")] * 6
    with _serve(Recorder(poll_errors=gone)):
        with pytest.raises(ConnectionError, match="Failed to perform HTTP request"):
            _bibr(pdf, tmp_path)


def test_bibr_no_answer_to_the_upload(pdf: Path, tmp_path: Path) -> None:
    with _serve(Recorder(submit_errors=[httpx.ConnectError("boom")])):
        with pytest.raises(ConnectionError, match="Failed to perform HTTP request"):
            _bibr(pdf, tmp_path)


def test_bibr_never_leaves_the_configured_host(pdf: Path, tmp_path: Path) -> None:
    """The token goes only where BIBR_URL says: the server's status_url is not followed."""
    rec = Recorder(key=TOKEN, status_url="https://evil.example/papers/jobs/job-1")
    with _serve(rec):
        _bibr(pdf, tmp_path)
    assert rec.urls and all(u.startswith(f"{HOSTED_URL}/") for u in rec.urls)


def test_bibr_job_id_is_one_path_segment(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(job_id="../../admin x")
    with _serve(rec):
        _bibr(pdf, tmp_path, api_key=None)
    assert rec.urls[1] == f"{HOSTED_URL}/papers/jobs/..%2F..%2Fadmin%20x"


def test_bibr_redirects_are_not_followed(pdf: Path, tmp_path: Path) -> None:
    moved = httpx.Response(301, headers={"location": "https://elsewhere.example/papers/jobs?a=b"})
    rec = Recorder(submit_errors=[moved])
    with _serve(rec), pytest.raises(BibrRequestError, match="redirected") as err:
        _bibr(pdf, tmp_path)
    assert "https://elsewhere.example/papers/jobs" in str(err.value)
    assert "a=b" not in str(err.value)
    assert len(rec.urls) == 1


def test_bibr_result_must_be_a_paper(pdf: Path, tmp_path: Path) -> None:
    for junk in ("<html>login</html>", ["not", "a", "paper"], {}):
        with _serve(Recorder(paper=junk)):
            with pytest.raises(RuntimeError, match="not a paper"):
                _bibr(pdf, tmp_path, api_key=None)
    assert not (tmp_path / "to_err_is_human.json").exists()


def test_bibr_submission_without_a_job_id(pdf: Path, tmp_path: Path) -> None:
    ok = httpx.Response(200, text="<html>a login page</html>")
    with _serve(Recorder(submit_errors=[ok])):
        with pytest.raises(RuntimeError, match=r"Job submission failed \(HTTP 200\)"):
            _bibr(pdf, tmp_path, api_key=None)


@pytest.mark.parametrize(
    "url",
    [
        "http://bibr.example",
        "http://192.168.1.20:8000",
        "http://10.0.0.5",
        "http://localhost.example.com",
        "http://bibr.localhost",  # a resolver may send *.localhost anywhere
        "http://0.0.0.0:8000",
    ],
)
def test_bibr_refuses_a_token_over_plain_http(url: str, pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(key=TOKEN)
    with _serve(rec), pytest.raises(ValueError, match="Refusing to send the bibr API token"):
        _bibr(pdf, tmp_path, api_url=url)
    assert rec.urls == []  # nothing was sent, not even the file
    # a list of files fails at once too, instead of logging one refusal per file
    with _serve(rec), pytest.raises(ValueError, match="plain http"):
        _bibr([pdf, pdf], tmp_path, api_url=url)
    assert rec.urls == []


def test_bibr_plain_http_refusal_comes_from_the_environment_too(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIBR_URL", "http://bibr.example")
    monkeypatch.setenv("BIBR_API_KEY", TOKEN)
    with _serve(Recorder()), pytest.raises(ValueError, match=r"over plain http to bibr\.example"):
        convert_bibr(pdf, tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://127.5.6.7",
        "http://[::1]:8001",
        "http://localhost.:8001",
        "https://bibr.example",
        "https://10.0.0.5:8443",
        f"{HOSTED_URL}/prefix",
    ],
)
def test_bibr_token_goes_to_https_and_loopback(url: str, pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(key=TOKEN)
    with _serve(rec):
        assert Path(_bibr(pdf, tmp_path, api_url=url)).exists()
    assert rec.urls[0] == f"{url}/papers/jobs"
    assert all(h["authorization"] == f"Bearer {TOKEN}" for h in rec.headers)


def test_bibr_plain_http_without_a_token_is_allowed(pdf: Path, tmp_path: Path) -> None:
    with _serve(Recorder()):
        assert Path(_bibr(pdf, tmp_path, api_url="http://bibr.example", api_key=None)).exists()
        assert Path(_bibr(pdf, tmp_path, api_url="http://bibr.example", api_key="")).exists()


@pytest.mark.parametrize("url", ["bibr.example", "ftp://bibr.example", "http://", "//bibr.example"])
def test_bibr_needs_a_real_address(url: str, pdf: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must start with http:// or https://"):
        _bibr(pdf, tmp_path, api_url=url)


def test_bibr_list_of_files(pdf: Path, tmp_path: Path) -> None:
    import shutil

    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    shutil.copy(pdf, a)
    shutil.copy(pdf, b)
    out = tmp_path / "json"

    # one document the server cannot process: None for it, the others are converted
    rec = Recorder(submit_errors=[httpx.Response(422, json={"detail": "no text"})])
    with _serve(rec):
        result = _bibr([a, b], out)
    assert result[0] is None
    assert result[1] == f"{out}/b.json"

    # a rejected token would be rejected for every file: stop at the first
    rec = Recorder(key="the-real-token-value")
    with _serve(rec), pytest.raises(BibrRequestError) as err:
        _bibr([a, b], out)
    assert err.value.status_code == 401
    assert len(rec.posts) == 1


# selfhosted against RC ---------------------------------------------------------------------


def test_selfhosted_sends_the_token(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC answers 401 without a bearer token when AUTH_API_KEY is set."""
    rec = Recorder(key=TOKEN)
    with _serve(rec):
        out = convert_bibr(pdf, tmp_path, backend="selfhosted", api_url=HOSTED_URL, api_key=TOKEN)
        assert json.loads(Path(out).read_bytes()) == PAPER
        assert rec.urls == [f"{HOSTED_URL}/papers/extract"]
        assert rec.headers[0]["authorization"] == f"Bearer {TOKEN}"

        monkeypatch.setenv("BIBR_API_KEY", TOKEN)  # or from the environment
        convert_bibr(pdf, tmp_path, backend="selfhosted", api_url=HOSTED_URL)
    assert rec.headers[1]["authorization"] == f"Bearer {TOKEN}"


def test_selfhosted_401_is_explained(pdf: Path, tmp_path: Path) -> None:
    with _serve(Recorder(key=TOKEN)):
        with pytest.raises(BibrRequestError, match="No API token was sent") as err:
            convert_bibr(pdf, tmp_path, backend="selfhosted", api_url=HOSTED_URL)
        assert err.value.status_code == 401
        with pytest.raises(RuntimeError, match="HTTP 401 Unauthorized while extracting"):
            convert_bibr(pdf, tmp_path, backend="selfhosted", api_url=HOSTED_URL, api_key="wrong")


def test_selfhosted_waits_out_a_429(pdf: Path, tmp_path: Path, sleeps: list[float]) -> None:
    rec = Recorder(submit_errors=[_busy("2")])
    with _serve(rec):
        out = convert_bibr(pdf, tmp_path, backend="selfhosted")
    assert sleeps == [2]
    assert len(rec.posts) == 2
    assert json.loads(Path(out).read_bytes()) == PAPER


def test_selfhosted_refuses_a_token_over_plain_http(pdf: Path, tmp_path: Path) -> None:
    rec = Recorder(key=TOKEN)
    with _serve(rec), pytest.raises(ValueError, match=r"plain http to bibr\.example"):
        convert_bibr(
            pdf, tmp_path, backend="selfhosted", api_url="http://bibr.example", api_key=TOKEN
        )
    assert rec.urls == []
    with _serve(Recorder()):  # no token, no refusal: as before
        convert_bibr(pdf, tmp_path, backend="selfhosted", api_url="http://bibr.example")


# convert() ---------------------------------------------------------------------------------


def test_convert_uses_the_bibr_url_without_probing(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytacheck.io.convert as conv

    def fail(*_a: object, **_k: object) -> None:
        raise AssertionError("BIBR_URL is used as given: no server list, no probe")

    monkeypatch.setattr(conv, "_online", fail)
    monkeypatch.setattr(conv, "_server_list", fail)
    monkeypatch.setenv("BIBR_URL", HOSTED_URL)
    monkeypatch.setenv("BIBR_API_KEY", TOKEN)
    for method in ("auto", "bibr"):
        rec = Recorder(key=TOKEN)
        with _serve(rec):
            out = conv.convert(pdf, tmp_path, method=method)
        assert json.loads(Path(out).read_bytes()) == PAPER
        assert rec.urls[0] == f"{HOSTED_URL}/papers/jobs"
        assert not any(u.endswith(("/ready", "isalive")) for u in rec.urls)


def test_convert_passes_bibr_options(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytacheck.io.convert import convert

    monkeypatch.setenv("BIBR_URL", HOSTED_URL)
    rec = Recorder()
    with _serve(rec):
        convert(pdf, tmp_path, start_page=3, include_figures=True)
    assert rec.fields == {"include_figures": "true", "start_page": "2"}


def test_convert_arguments_beat_bibr_url(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytacheck.io.convert import convert

    monkeypatch.setenv("BIBR_URL", HOSTED_URL)
    rec = Recorder()
    with _serve(rec):
        convert(pdf, tmp_path, "bibr", api_url="https://mine.example")
    assert rec.urls[0].startswith("https://mine.example/")

    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test")
    rec = Recorder()
    with _serve(rec):
        convert(pdf, tmp_path, "bibr", backend="scivrs", api_url=SCIVRS_URL)
    assert rec.urls[0] == f"{SCIVRS_URL}/jobs"


def test_convert_finds_a_local_bibr_serve(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An anonymous RC /ready says only {"status": "ready"}: enough to be found."""
    from pytacheck.io.convert import convert

    monkeypatch.delenv("SCIVRS_API_KEY", raising=False)
    rec = Recorder()
    routes = {
        ("GET", f"{SELFHOSTED_URL}/ready"): rec,
        ("POST", f"{SELFHOSTED_URL}/papers/extract"): rec,
    }
    with api("apis", routes=routes):
        out = convert(pdf, tmp_path)
    assert json.loads(Path(out).read_bytes()) == PAPER
    assert rec.urls == [f"{SELFHOSTED_URL}/ready", f"{SELFHOSTED_URL}/papers/extract"]


def test_convert_local_bibr_serve_is_not_the_platform(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform user (SCIVRS_API_KEY) with a bibr serve on this machine: the server that
    was found takes the synchronous request, not the platform's /jobs queue (405 on bibr)."""
    from pytacheck.io.convert import convert

    monkeypatch.setenv("SCIVRS_API_KEY", "sv_test")
    rec = Recorder()
    routes = {
        ("GET", f"{SELFHOSTED_URL}/ready"): rec,
        ("POST", f"{SELFHOSTED_URL}/papers/extract"): rec,
    }
    with api("apis", routes=routes):
        out = convert(pdf, tmp_path)
    assert json.loads(Path(out).read_bytes()) == PAPER
    assert rec.urls == [f"{SELFHOSTED_URL}/ready", f"{SELFHOSTED_URL}/papers/extract"]

    # a backend that was asked for stays: the caller's word beats the guess
    rec = Recorder()
    local = re.compile(r"http://localhost:8000/.*")
    with api("apis", routes={("GET", local): rec, ("POST", local): rec}):
        convert(pdf, tmp_path, backend="bibr")
    assert rec.urls[1] == f"{SELFHOSTED_URL}/papers/jobs"


def test_convert_ignores_bibr_api_url(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BIBR_API_URL (set for bibr's own notebooks) must not steer convert(): a LAN address
    with a key would be refused, and the message would name BIBR_URL."""
    from pytacheck.io.convert import convert

    monkeypatch.setenv("BIBR_API_URL", "http://192.168.1.20:8000")
    monkeypatch.setenv("BIBR_API_KEY", TOKEN)
    rec = Recorder()
    routes = {
        ("GET", f"{SELFHOSTED_URL}/ready"): rec,
        ("POST", f"{SELFHOSTED_URL}/papers/extract"): rec,
    }
    with api("apis", routes=routes) as router:
        out = convert(pdf, tmp_path)
    assert json.loads(Path(out).read_bytes()) == PAPER
    assert rec.urls == [f"{SELFHOSTED_URL}/ready", f"{SELFHOSTED_URL}/papers/extract"]
    assert not any("192.168.1.20" in str(call.request.url) for call in router.calls)


def test_multiple_files_and_failures(server: Recorder, tmp_path: Path, fixtures_dir: Path) -> None:
    files = [fixtures_dir / "formats" / "preprint.pdf", fixtures_dir / "formats" / "published.pdf"]
    out = convert_bibr(files, tmp_path, backend="selfhosted")
    assert [Path(p).name for p in out] == ["preprint.json", "published.json"]
    assert all(Path(p).exists() for p in out)

    out = convert_bibr([files[0], tmp_path / "missing.pdf"], tmp_path, backend="selfhosted")
    assert out[0].endswith("preprint.json")
    assert out[1] is None


def test_bibr_save_result(tmp_path: Path) -> None:
    save = tmp_path / "new" / "dir"
    assert _bibr_save_result(b"{}", "a.b.docx", save) == f"{save}/a.b.json"
    assert _bibr_save_result(b"{}", "noext", save) == f"{save}/noext"
    assert (save / "a.b.json").read_bytes() == b"{}"


def test_bibr_isalive() -> None:
    with pytest.raises(TypeError):
        _bibr_isalive()  # type: ignore[call-arg]
    with api("apis"):
        # not a url
        with pytest.raises(ConnectionError, match="Connection to the BIBR server failed"):
            _bibr_isalive("bibr")
        assert _bibr_isalive("bibr", error=False) is False
        # url, not bibr
        with pytest.raises(RuntimeError, match="does not appear up and running"):
            _bibr_isalive("https://google.com")
        assert _bibr_isalive("https://google.com", error=False) is False


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, json={"status": "ready", "checks": {"bibr": "ok"}}), None),
        # bibr serve RC: anonymous callers get the status word and nothing else ...
        (httpx.Response(200, json={"status": "ready"}), None),
        # ... a valid token adds the checks of its own dependencies and the build
        (
            httpx.Response(
                200,
                json={"status": "ready", "checks": {"ocr": "ok"}, "build_sha": "7483212"},
            ),
            None,
        ),
        (httpx.Response(200, text="<html>login</html>"), "API key is not valid"),
        (httpx.Response(200, json={"status": "starting", "checks": {}}), "not ready200"),
        (httpx.Response(200, json={"status": "not_ready"}), "not ready200"),
        (httpx.Response(200, json={"status": "ready", "checks": {"bibr": "down"}}), "not ready200"),
        (httpx.Response(200, json={"checks": {"bibr": "ok"}}), "not ready200"),
        (httpx.Response(200, json=["ready"]), "not ready200"),
        (httpx.Response(200, json={"status": "ready", "checks": "fine"}), None),
        (httpx.Response(503, json={"status": "not_ready"}), "Status: 503"),
    ],
)
def test_bibr_isalive_responses(response: httpx.Response, message: str | None) -> None:
    url = "https://bibr.example"
    with api(routes={("GET", f"{url}/ready"): lambda _r: response}) as router:
        if message is None:
            assert _bibr_isalive(url, "key") is True
        else:
            with pytest.raises(RuntimeError, match=message):
                _bibr_isalive(url, "key")
            assert _bibr_isalive(url, "key", error=False) is False
        auth = router.calls.last.request.headers["authorization"]
    assert auth == "Bearer key"


def test_bibr_isalive_keeps_the_token_off_plain_http() -> None:
    """/ready is public: the probe works without the token where it must not be sent."""
    ready = json_response({"status": "ready"})
    for url, sent in (
        ("http://bibr.example", False),
        ("http://localhost:8000", True),
        ("http://127.0.0.1:8000", True),
        ("https://bibr.example", True),
    ):
        with api(routes={("GET", f"{url}/ready"): ready}) as router:
            assert _bibr_isalive(url, TOKEN) is True
            headers = router.calls.last.request.headers
        assert (headers.get("authorization") == f"Bearer {TOKEN}") is sent, url


# authors ----------------------------------------------------------------------------


def test_format_bib_authors() -> None:
    assert format_bib_authors(None) is None
    empty = pd.DataFrame({"given": pd.Series([], dtype=str), "family": pd.Series([], dtype=str)})
    assert format_bib_authors(empty) is None

    authors = pd.DataFrame({"given": ["Alice H.", "Wendy"], "family": ["Eagly", "Wood"]})
    assert format_bib_authors(authors) == "Eagly, Alice H.; Wood, Wendy"

    two = [authors, pd.DataFrame({"given": ["Lisa"], "family": ["DeBruine"]})]
    assert format_bib_authors(two) == ["Eagly, Alice H.; Wood, Wendy", "DeBruine, Lisa"]

    assert format_bib_authors(["DeBruine, L", "Lakens, D"]) == "DeBruine, L; Lakens, D"
    assert format_bib_authors([["DeBruine, L", "Lakens, D"], "Werner, J"]) == [
        "DeBruine, L; Lakens, D",
        "Werner, J",
    ]


def test_parse_author_string() -> None:
    obs = _parse_author_string("Eagly, Alice H.; Wood, Wendy")
    assert obs.to_dict("list") == {"given": ["Alice H.", "Wendy"], "family": ["Eagly", "Wood"]}
    obs = _parse_author_string("Smith, John, and Jane Doe")
    assert obs.to_dict("list") == {"given": ["John", "Jane"], "family": ["Smith", "Doe"]}
    assert len(_parse_author_string(None)) == 0
    assert len(_parse_author_string("  ")) == 0


def test_coerce_bib_authors() -> None:
    assert _coerce_bib_authors(None) is None
    out = _coerce_bib_authors(
        [
            "Eagly, Alice H.",
            None,
            pd.DataFrame({"family": ["Doe"], "given": ["Jane"], "orcid": ["x"]}),
            ["a", "b"],
        ]
    )
    assert [len(x) for x in out] == [1, 0, 1, 0]
    assert list(out[2].columns) == ["given", "family"]
    # a zero-column data frame (jsonlite's simplified empty arrays) gives empty tables
    assert [len(x) for x in _coerce_bib_authors(pd.DataFrame(index=range(2)))] == [0, 0]
