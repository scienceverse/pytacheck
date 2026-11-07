"""The web interface under /api: routes, fields, status codes and what a job is given."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from metacheck.web import app as web_app
from metacheck.web import uploads
from metacheck.web.config import ALLOWED_MODULES, DEFAULT_MODULES, RESEARCHBOX_NO_KEY
from tests.web.helpers import BIBR, PDF, form, send, wait_for, zip_bytes

# -- the answer to a job ------------------------------------------------------------------


def test_job_is_accepted_with_where_to_follow_it(client: TestClient) -> None:
    r = send(client)
    assert r.status_code == 202
    body = r.json()
    assert str(uuid.UUID(body["id"])) == body["id"]
    assert body["status"] in ("queued", "running")
    assert body["status_url"] == f"http://testserver/api/jobs/{body['id']}"
    assert body["result_url"] == f"http://testserver/api/jobs/{body['id']}/result"
    assert r.headers["cache-control"] == "no-store"


def test_urls_follow_the_proxy_in_front(client: TestClient) -> None:
    r = client.post(
        "/api/jobs",
        files={"file": ("paper.pdf", PDF)},
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/metacheck"},
    )
    assert r.json()["status_url"].startswith("https://testserver/metacheck/api/jobs/")
    bad = client.post(
        "/api/jobs",
        files={"file": ("paper.pdf", PDF)},
        headers={"X-Forwarded-Proto": "javascript", "X-Forwarded-Prefix": "/a b<"},
    )
    assert bad.json()["status_url"].startswith("http://testserver/api/jobs/")


def test_done_job_has_a_result_and_no_empty_fields(client: TestClient) -> None:
    job = send(client).json()
    done = wait_for(client, job["id"], "done")
    assert done["converter"] == "grobid"
    assert done["step"] == "done"
    for gone in ("error", "position", "progress", "run_code"):
        assert gone not in done
    for there in ("id", "created", "started", "finished", "status_url", "result_url"):
        assert there in done
    result = client.get(job["result_url"])
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/html")
    assert result.headers["content-security-policy"] == "sandbox allow-scripts"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["x-metacheck-report-type"] == "full"
    assert "report" in result.text


def test_result_before_the_end_is_409(client: TestClient) -> None:
    job = send(client, "hold.pdf").json()
    wait_for(client, job["id"], "running")
    early = client.get(f"/api/jobs/{job['id']}/result")
    assert early.status_code == 409
    assert early.json()["status"] == "running"
    # the job is running once a place is free; its process tells its progress soon after
    end = time.monotonic() + 20
    running = client.get(f"/api/jobs/{job['id']}").json()
    while "progress" not in running and time.monotonic() < end:
        time.sleep(0.05)
        running = client.get(f"/api/jobs/{job['id']}").json()
    assert running["progress"] == 0.5
    assert running["step"] == "working"
    assert "position" not in running


def test_failed_job_is_422_with_its_error(client: TestClient) -> None:
    job = send(client, "fail.pdf").json()
    failed = wait_for(client, job["id"], "failed")
    assert failed["error"] == "The paper could not be read."
    result = client.get(job["result_url"])
    assert result.status_code == 422
    assert result.json() == {"error": "The paper could not be read."}


def test_unknown_job_is_404(client: TestClient) -> None:
    missing = str(uuid.uuid4())
    assert client.get(f"/api/jobs/{missing}").status_code == 404
    assert client.get(f"/api/jobs/{missing}/result").status_code == 404
    assert "one hour" in client.get("/api/jobs/not-an-id").json()["error"]


# -- the one-call routes ------------------------------------------------------------------


def test_report_route_answers_with_the_report(client: TestClient) -> None:
    r = send(client, path="/api/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_check_and_convert_routes_answer_with_json(client: TestClient) -> None:
    assert send(client, path="/api/check").json() == {}
    assert send(client, path="/api/convert").headers["content-type"] == "application/json"


def test_repocheck_route_takes_a_zip(client: TestClient) -> None:
    r = send(client, "study.zip", zip_bytes({"study/README.md": "hello"}), "/api/repocheck")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_one_call_route_passes_on_a_failure(client: TestClient) -> None:
    r = send(client, "fail.pdf", path="/api/report")
    assert r.status_code == 422
    assert r.json()["error"] == "The paper could not be read."


# -- what is refused ----------------------------------------------------------------------


def refused(client: TestClient, status: int, text: str, **kwargs: object) -> None:
    r = send(client, **kwargs)  # type: ignore[arg-type]
    assert r.status_code == status, r.text
    assert text in r.json()["error"]


def test_a_request_must_hold_a_file_or_a_link(client: TestClient) -> None:
    r = client.post("/api/jobs", files={"x": ("a", b"b")})
    assert r.status_code == 400
    assert "'file'" in r.json()["error"]
    r = client.post("/api/jobs", json={"file": "x"})
    assert r.status_code == 400
    assert "multipart" in r.json()["error"]


def test_empty_and_unknown_files(client: TestClient) -> None:
    refused(client, 400, "empty", data=b"")
    refused(client, 400, "not a PDF", data=b"just some text")


def test_word_files_are_refused(client: TestClient) -> None:
    docx = zip_bytes({"[Content_Types].xml": "x", "word/document.xml": "y"})
    refused(client, 400, "Word files", name="paper.docx", data=docx)


@pytest.mark.parametrize(
    ("name", "data", "fields", "text"),
    [
        ("p.pdf", PDF, {"kind": "nonsense"}, "kind must be"),
        ("p.json", BIBR, {"kind": "convert"}, "convert needs a PDF"),
        ("p.pdf", PDF, {"kind": "repocheck"}, "repocheck needs a .zip"),
        ("p.zip", zip_bytes({"a/b.txt": "x"}), {"kind": "report"}, "kind=repocheck"),
        ("p.pdf", PDF, {"modules": "power,nonsense"}, "nonsense"),
        ("p.pdf", PDF, {"modules": "ref_accuracy"}, "ref_accuracy"),
        ("p.pdf", PDF, {"report_type": "poster"}, "report_type"),
        (
            "p.zip",
            zip_bytes({"a/b.txt": "x"}),
            {"kind": "repocheck", "modules": "power"},
            "installed",
        ),
    ],
)
def test_bad_fields_are_400(
    client: TestClient, name: str, data: bytes, fields: dict[str, str], text: str
) -> None:
    refused(client, 400, text, name=name, data=data, **fields)


def test_a_link_needs_kind_repocheck(client: TestClient) -> None:
    r = form(client, "/api/jobs", {"url": "https://github.com/a/b", "kind": "report"})
    assert r.status_code == 400
    assert "repository" in r.json()["error"]


@pytest.mark.parametrize(
    "link",
    [
        "https://example.com/a/b",
        "http://github.com/../../etc/passwd",
        "https://researchbox.org/abc",
        "https://notresearchbox.org/1234",
        "file:///etc/passwd",
        "https://github.com/" + "a" * 600,
    ],
)
def test_a_link_to_somewhere_else_is_refused(client: TestClient, link: str) -> None:
    r = form(client, "/api/jobs", {"url": link, "kind": "repocheck"})
    assert r.status_code == 400
    assert "error" in r.json()


def test_a_public_repository_link_is_accepted(client: TestClient) -> None:
    r = form(client, "/api/repocheck", {"url": "https://github.com/scienceverse/pytacheck"})
    assert r.status_code == 200


def test_a_researchbox_box_needs_a_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCHBOX_API_KEY", raising=False)
    r = form(client, "/api/jobs", {"url": "https://researchbox.org/1234", "kind": "repocheck"})
    assert r.status_code == 400
    assert r.json()["error"] == RESEARCHBOX_NO_KEY


@pytest.mark.parametrize(
    ("link", "url"),
    [
        ("https://researchbox.org/1234", "https://researchbox.org/1234"),
        ("researchbox.org/1234/", "https://researchbox.org/1234"),
        ("http://www.ResearchBox.org/1234", "https://researchbox.org/1234"),
        (
            "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS",
            "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS",
        ),
    ],
)
def test_a_researchbox_box_is_taken_with_the_senders_or_the_servers_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, link: str, url: str
) -> None:
    monkeypatch.delenv("RESEARCHBOX_API_KEY", raising=False)
    r = form(client, "/api/jobs", {"url": link, "kind": "repocheck", "key_researchbox": "k"})
    assert r.status_code == 202, r.text
    spec = spec_of(client, r)
    assert spec["url"] == url
    assert "passcode" not in str(spec["name"])  # the job's name does not carry it
    monkeypatch.setenv("RESEARCHBOX_API_KEY", "the-servers-key")
    r = form(client, "/api/jobs", {"url": link, "kind": "repocheck"})
    assert r.status_code == 202, r.text


def test_checklist_report_type_is_accepted(client: TestClient) -> None:
    job = send(client, report_type="checklist").json()
    spec = client.app.state.manager.get(job["id"]).spec  # type: ignore[attr-defined]
    assert spec["report_type"] == "checklist"
    assert send(client, report_type="").status_code == 202


# -- the fields a job is given ------------------------------------------------------------


def spec_of(client: TestClient, response: object) -> dict[str, object]:
    job_id = response.json()["id"]  # type: ignore[attr-defined]
    return client.app.state.manager.get(job_id).spec  # type: ignore[no-any-return,attr-defined]


def test_the_default_modules_and_crossref(client: TestClient) -> None:
    spec = spec_of(client, send(client))
    assert spec["modules"] == [m for m in ALLOWED_MODULES if m in DEFAULT_MODULES]
    assert spec["crossref"] is False
    assert spec["report"] is False
    assert spec["kind"] == "report"
    assert spec["input_kind"] == "pdf"
    assert spec["name"] == "paper"


def test_fields_are_read(client: TestClient) -> None:
    spec = spec_of(
        client,
        send(
            client,
            "my paper.json",
            BIBR,
            kind="check",
            modules="power, marginal ,power",
            report="true",
            crossref="1",
        ),
    )
    assert spec["kind"] == "check"
    assert spec["modules"] == ["power", "marginal"]
    assert spec["report"] is True
    assert spec["crossref"] is True
    assert spec["input_kind"] == "json"
    assert spec["name"] == "my paper"


def test_a_zip_is_a_repository_check_by_default(client: TestClient) -> None:
    spec = spec_of(client, send(client, "study.zip", zip_bytes({"study/a.R": "x <- 1"})))
    assert spec["kind"] == "repocheck"
    assert spec["modules"] == []
    assert spec["name"] == "study"


def test_the_name_of_an_upload_never_holds_a_path(client: TestClient) -> None:
    spec = spec_of(client, send(client, "../../etc/pass\nwd.pdf"))
    assert "/" not in str(spec["name"])
    assert "\n" not in str(spec["name"])


def test_a_convert_job_ignores_the_other_fields(client: TestClient) -> None:
    r = send(client, kind="convert", modules="nonsense", report_type="checklist")
    assert r.status_code == 202
    assert spec_of(client, r)["modules"] == []


# -- modules and health -------------------------------------------------------------------


def test_modules(client: TestClient) -> None:
    body = client.get("/api/modules").json()
    assert body["allowed"] == [m["name"] for m in body["info"]]
    assert set(body["default"]) <= set(body["allowed"])
    assert set(body["default"]) == set(DEFAULT_MODULES)
    assert body["installed"] == []
    assert "ref_accuracy" not in body["allowed"]
    first = body["info"][0]
    assert set(first) == {"name", "title", "description", "default"}
    assert first["title"]


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["run_code"] is False
    assert body["sandbox"] == "none"
    assert body["jobs_running"] == 0
    assert body["jobs_waiting"] == 0
    assert body["workers"] == 2
    assert body["grobid"] in ("local", "remote")
    assert isinstance(body["bibr"], bool)
    assert body["key_problems"] == []
    assert body["metacheck_version"]


def test_health_lists_the_servers_key_problems(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.archives.api_keys import key_problem_record

    monkeypatch.setenv("OSF_PAT", "the-servers-token")
    key_problem_record("osf", "rejected")
    problems = client.get("/api/health").json()["key_problems"]
    assert [(p["service"], p["problem"], p["source"]) for p in problems] == [
        ("osf", "rejected", "server")
    ]
    assert problems[0]["message"]
    assert "the-servers-token" not in str(problems)
    assert problems[0]["until"].endswith(" UTC")


@pytest.mark.parametrize(("docker", "expected"), [(True, True), (False, False)])
def test_run_code_needs_docker(
    make_client: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
    docker: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(web_app, "_docker_ready", lambda: docker)
    c = make_client(run_code=True)
    body = c.get("/api/health").json()
    assert body["run_code"] is expected
    assert body["sandbox"] == ("docker" if expected else "none")


def test_run_code_is_off_unless_the_server_offers_it(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    off = make_client()
    zipped = zip_bytes({"a/b.R": "x"})
    assert spec_of(off, send(off, "a.zip", zipped, run_code="true"))["run_code"] is False
    monkeypatch.setattr(web_app, "_docker_ready", lambda: True)
    on = make_client(run_code=True)
    # a repository runs its code unless the visitor says no; a paper only if asked
    assert spec_of(on, send(on, "a.zip", zipped))["run_code"] is True
    assert spec_of(on, send(on, "a.zip", zipped, run_code="false"))["run_code"] is False
    assert spec_of(on, send(on))["run_code"] is False
    assert spec_of(on, send(on, run_code="true"))["run_code"] is True
    assert spec_of(on, send(on, kind="convert", run_code="true"))["run_code"] is False
    job = send(on, "a.zip", zipped).json()
    assert wait_for(on, job["id"], "done")["run_code"] is True


# -- keys ---------------------------------------------------------------------------------

SECRET = "ghp_SECRET0123456789abcdefghijklmnop"


def everything_under(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


def test_a_visitors_key_reaches_the_job_and_nothing_else(client: TestClient) -> None:
    manager = client.app.state.manager  # type: ignore[attr-defined]
    job = send(client, "keys.pdf", key_github=SECRET, key_osf=" osf-key ", key_other="x").json()
    stored = manager.get(job["id"])
    assert "key_github" not in repr(stored.spec) and SECRET not in repr(stored)
    done = wait_for(client, job["id"], "done")
    # the job saw both keys (their lengths), and the one it is not given a name for is dropped
    assert client.get(done["result_url"]).json() == {"github": len(SECRET), "osf": 7}
    assert stored.keys == {}
    assert SECRET.encode() not in everything_under(manager.root)
    assert SECRET not in client.get(job["status_url"]).text


def test_a_key_is_not_written_while_the_job_waits(client: TestClient) -> None:
    manager = client.app.state.manager  # type: ignore[attr-defined]
    job = send(client, "hold.pdf", key_github=SECRET).json()
    wait_for(client, job["id"], "running")
    assert SECRET.encode() not in everything_under(manager.root)
    assert manager.get(job["id"]).keys == {}


# -- zips ---------------------------------------------------------------------------------


def test_a_zip_with_too_many_files_is_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads, "MAX_ZIP_FILES", 3)
    ok = zip_bytes({f"a/{i}.txt": "x" for i in range(3)})
    assert send(client, "a.zip", ok).status_code == 202
    many = zip_bytes({f"a/{i}.txt": "x" for i in range(4)})
    r = send(client, "a.zip", many)
    assert r.status_code == 413
    assert "more than 3 files" in r.json()["error"]


def test_a_zip_that_unpacks_too_big_is_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads, "MAX_UNPACKED_BYTES", 1000)
    r = send(client, "a.zip", zip_bytes({"a/big.csv": "0" * 5000}))
    assert r.status_code == 413
    assert "1000 MB" in r.json()["error"]


@pytest.mark.parametrize(
    "member", ["../evil.txt", "a/../../evil.txt", "/etc/evil.txt", "C:/evil.txt"]
)
def test_a_zip_may_not_leave_its_folder(client: TestClient, member: str) -> None:
    r = send(client, "a.zip", zip_bytes({"a/ok.txt": "x", member: "boo"}))
    assert r.status_code == 400
    assert "outside" in r.json()["error"]


def test_a_damaged_zip_is_400(client: TestClient) -> None:
    refused(client, 400, "cannot be read", name="a.zip", data=b"PK\x03\x04 and then nothing useful")


def test_the_upload_size_limits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 100)
    r = send(client, data=PDF + b"x" * 200)
    assert r.status_code == 413
    assert "50 MB" in r.json()["error"]
    monkeypatch.setattr(web_app, "MAX_ZIP_BYTES", 100)
    r = send(client, "a.zip", zip_bytes({"a/b.txt": "x" * 4000}))
    assert r.status_code == 413
    assert "500 MB" in r.json()["error"]


def test_a_refused_upload_leaves_nothing_behind(client: TestClient) -> None:
    manager = client.app.state.manager  # type: ignore[attr-defined]
    send(client, data=b"nonsense")
    send(client, "a.zip", zip_bytes({"../x": "y"}))
    time.sleep(0.1)
    assert [p for p in manager.root.iterdir() if p.is_dir()] == []


# -- PsyArXiv -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "abcde",
        "https://osf.io/preprints/psyarxiv/abcde_v2/",
        "https://next.psyarxiv.org/preprint/abcde",
        "10.31234/osf.io/abcde",
        "https://doi.org/10.31234/osf.io/ABCDE",
    ],
)
def test_psyarxiv_links_become_an_id(client: TestClient, text: str) -> None:
    r = form(client, "/api/jobs", {"psyarxiv": text})
    assert r.status_code == 202, r.text
    spec = spec_of(client, r)
    assert spec["psyarxiv"] == "abcde"
    assert spec["kind"] == "report"
    assert spec["input_file"] is None
    assert spec["psyarxiv_api"].startswith("https://")  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "text",
    ["ab", "../../etc/passwd", "abc de", "https://example.com/x/abc!d", "x" * 400, "abcde;rm -rf"],
)
def test_bad_psyarxiv_ids_are_400(client: TestClient, text: str) -> None:
    r = form(client, "/api/jobs", {"psyarxiv": text})
    assert r.status_code == 400
    assert "PsyArXiv" in r.json()["error"]


def test_a_preprint_is_checked_as_a_paper(client: TestClient) -> None:
    r = form(client, "/api/jobs", {"psyarxiv": "abcde", "kind": "repocheck"})
    assert r.status_code == 400
    r = form(client, "/api/jobs", {"psyarxiv": "abcde", "kind": "convert"})
    assert r.status_code == 400


def test_the_psyarxiv_api_is_a_setting(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METACHECK_PSYARXIV_API", "https://staging.example.org/api/v1")
    c = make_client()
    spec = spec_of(c, form(c, "/api/jobs", {"psyarxiv": "abcde"}))
    assert spec["psyarxiv_api"] == "https://staging.example.org/api/v1"
