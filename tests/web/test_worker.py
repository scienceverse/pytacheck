"""The work of a job, with the real checks: a paper, a repository, and what is stubbed."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import metacheck as pc
from metacheck.app.run import UserError
from metacheck.web import worker
from tests.web.helpers import PDF, form, send, wait_for, zip_bytes


@pytest.fixture(scope="module")
def demo_json() -> bytes:
    return pc.demofile("json").read_bytes()


@pytest.fixture
def real(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(fake=False)


# -- the layout (the one place a checklist report would plug in) --------------------------


@pytest.mark.parametrize("asked", ["", "checklist"])
def test_checklist_falls_back_to_the_default_layout(asked: str) -> None:
    assert worker.report_layout(asked, repository=False) == "full"
    assert worker.report_layout(asked, repository=True) == "brief"


def test_scrub_paths_removes_the_servers_places(tmp_path: Path) -> None:
    folder = tmp_path / "job"
    folder.mkdir()
    link = tmp_path / "link"
    link.symlink_to(folder)
    text = f"see {folder}/repo/a.R and {link}/b.R and {folder}"
    assert worker.scrub_paths(text, folder, link) == "see repo/a.R and b.R and "


# -- converting a PDF ----------------------------------------------------------------------


def test_a_pdf_goes_to_bibr_when_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.app import bibr

    monkeypatch.setattr(bibr, "_configured", lambda: "https://bibr.example.org")
    monkeypatch.setattr(bibr, "resolve_key", lambda *a, **k: "key")
    monkeypatch.setattr(bibr, "convert_pdf", lambda pdf, wd, key, progress: wd / "a.json")
    monkeypatch.setattr(worker, "_grobid", lambda *a: pytest.fail("GROBID was not needed"))
    path, converter = worker.convert_pdf(tmp_path / "a.pdf", tmp_path, False, lambda f, t: None)
    assert (path.name, converter) == ("a.json", "bibr")


def test_a_pdf_goes_to_grobid_without_bibr_or_when_bibr_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.app import bibr

    seen: list[bool] = []
    monkeypatch.setattr(
        worker, "_grobid", lambda pdf, wd, crossref: seen.append(crossref) or wd / "g.json"
    )
    monkeypatch.setattr(bibr, "_configured", lambda: None)
    path, converter = worker.convert_pdf(tmp_path / "a.pdf", tmp_path, True, lambda f, t: None)
    assert (path.name, converter, seen) == ("g.json", "grobid", [True])

    def refuse(*args: Any) -> None:
        raise UserError("bibr said no")

    monkeypatch.setattr(bibr, "_configured", lambda: "https://bibr.example.org")
    monkeypatch.setattr(bibr, "resolve_key", lambda *a, **k: "key")
    monkeypatch.setattr(bibr, "convert_pdf", refuse)
    assert worker.convert_pdf(tmp_path / "a.pdf", tmp_path, False, lambda f, t: None)[1] == "grobid"

    def wrong() -> None:
        raise ValueError("a wrong setting")

    monkeypatch.setattr(bibr, "_configured", wrong)
    assert worker.convert_pdf(tmp_path / "a.pdf", tmp_path, False, lambda f, t: None)[1] == "grobid"


def test_the_converter_is_told_with_the_finished_job(
    real: TestClient, monkeypatch: pytest.MonkeyPatch, demo_json: bytes
) -> None:
    def convert(pdf: Path, workdir: Path, crossref: bool, report: Any) -> tuple[Path, str]:
        out = workdir / "converted.json"
        out.write_bytes(demo_json)
        return out, "bibr"

    monkeypatch.setattr(worker, "convert_pdf", convert)
    job = send(real, kind="convert").json()
    done = wait_for(real, job["id"], "done")
    assert done["converter"] == "bibr"
    assert json.loads(real.get(job["result_url"]).content)  # the converted JSON
    # the PDF is deleted once it is converted
    manager = real.app.state.manager  # type: ignore[attr-defined]
    assert not list(manager.get(job["id"]).folder.glob("input.*"))


# -- a paper -------------------------------------------------------------------------------


def test_a_paper_report(real: TestClient, demo_json: bytes) -> None:
    r = send(real, "demo.json", demo_json, "/api/report", modules="power,marginal")
    assert r.status_code == 200
    assert r.headers["x-metacheck-report-type"] == "full"
    assert r.headers["content-security-policy"] == "sandbox allow-scripts"
    assert "To Err is Human" in r.text
    # a report holds no place on this server
    assert str(real.app.state.manager.root) not in r.text  # type: ignore[attr-defined]


def test_a_checklist_is_asked_for_and_the_full_report_comes(
    real: TestClient, demo_json: bytes
) -> None:
    r = send(
        real, "demo.json", demo_json, "/api/report", modules="marginal", report_type="checklist"
    )
    assert r.status_code == 200
    assert r.headers["x-metacheck-report-type"] == "full"


def test_a_paper_check(real: TestClient, demo_json: bytes) -> None:
    r = send(real, "demo.json", demo_json, "/api/check", modules="power,marginal", report="true")
    assert r.status_code == 200
    body = r.json()
    assert body["converter"] is None  # the paper came as JSON
    assert set(body["modules"]) == {"power", "marginal"}
    assert body["paper_info"]["title"] == "To Err is Human: An Empirical Investigation"
    assert "<html" in body["report_html"].lower()
    assert body["metacheck_version"]


def test_the_steps_are_told_as_the_modules_run() -> None:
    told: list[tuple[float, str]] = []
    step = worker._module_steps(lambda fraction, text: told.append((round(fraction, 2), text)))
    step(0, 4, "power")
    step(2, 4, "stat_check")
    step(4, 4, "")
    assert told == [
        (0.1, "running module 1 of 4: power"),
        (0.5, "running module 3 of 4: stat_check"),
        (0.9, "rendering the report"),
    ]


def test_a_file_without_a_paper_in_it_fails_with_a_message(real: TestClient) -> None:
    r = send(real, "x.json", b'{"nothing": "here"}', "/api/report")
    assert r.status_code == 422
    assert "could not be read as a paper" in r.json()["error"] or "No text" in r.json()["error"]


def test_a_visitors_key_is_in_the_jobs_environment_only(
    real: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "ghp_" + "S" * 36
    monkeypatch.delenv("GITHUB_PAT_GITHUB_COM", raising=False)
    monkeypatch.delenv("OSF_PAT", raising=False)

    def probe(spec: dict[str, Any], folder: Path, report: Any) -> dict[str, Any]:
        out = folder / "result.json"
        out.write_text(
            json.dumps(
                {
                    "github_seen": os.environ.get("GITHUB_PAT_GITHUB_COM") == secret,
                    "osf_seen": os.environ.get("OSF_PAT"),
                    "tmp_in_job": tempfile.gettempdir().startswith(str(folder)),
                }
            )
        )
        return {"result": out.name, "type": "application/json", "converter": None}

    monkeypatch.setattr(worker, "_paper_job", probe)
    r = send(real, "demo.json", b'{"a": 1}', "/api/check", key_github=secret, key_researchbox="x")
    assert r.status_code == 200, r.text
    assert r.json() == {"github_seen": True, "osf_seen": None, "tmp_in_job": True}
    assert "GITHUB_PAT_GITHUB_COM" not in os.environ  # this process never had it
    manager = real.app.state.manager  # type: ignore[attr-defined]
    assert secret.encode() not in b"".join(
        p.read_bytes() for p in manager.root.rglob("*") if p.is_file()
    )


# -- a repository --------------------------------------------------------------------------


def study_zip() -> bytes:
    return zip_bytes(
        {
            "study/README.md": "# Study\n\nData and code of the study.\n",
            "study/analysis.R": "data <- read.csv('data.csv')\nprint(mean(data$x))\n",
            "study/data.csv": "id,x\n1,2\n2,3\n3,4\n",
        }
    )


def test_a_repository_report(real: TestClient) -> None:
    r = send(real, "study.zip", study_zip(), "/api/repocheck")
    assert r.status_code == 200
    assert r.headers["x-metacheck-report-type"] == "brief"
    assert r.headers["content-security-policy"] == "sandbox allow-scripts"
    assert "study" in r.text
    manager = real.app.state.manager  # type: ignore[attr-defined]
    assert str(manager.root) not in r.text
    assert "tmp" not in {p.name for p in manager.root.glob("*/*")}


def test_a_zip_with_nothing_in_it_fails_with_a_message(real: TestClient) -> None:
    r = send(real, "empty.zip", zip_bytes({"empty/": ""}), "/api/repocheck")
    assert r.status_code == 422
    assert "no files" in r.json()["error"]


def test_a_repository_is_checked_in_a_folder_of_the_zips_name(real: TestClient) -> None:
    flat = zip_bytes({"README.md": "# Flat\n", "run.R": "1 + 1\n"})
    r = send(real, "my data.zip", flat, "/api/repocheck")
    assert r.status_code == 200
    assert "my data" in r.text


def test_a_link_is_fetched_with_the_repository_code(
    real: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A link is opened by ``open_link`` (mocked here); a key counts only for its own host."""
    import contextlib
    import shutil

    record = tmp_path / "seen.json"  # the job runs in a process of its own

    def seen() -> dict[str, Any]:
        return json.loads(record.read_text())  # type: ignore[no-any-return]

    @contextlib.contextmanager
    def open_link(link: Any, **kwargs: Any) -> Any:
        record.write_text(json.dumps({"kind": link.kind, "credentials": kwargs["credentials"]}))
        made = Path(tempfile.mkdtemp(dir=tempfile.gettempdir()))
        (made / "README.md").write_text("# From a link\n")

        class Opened:
            name = "repository"
            remote = True
            base = False
            base_dir = made

        try:
            yield Opened
        finally:
            shutil.rmtree(made, ignore_errors=True)

    monkeypatch.setattr("metacheck.datapackage.open_link", open_link)
    url = "https://github.com/scienceverse/pytacheck"
    r = form(real, "/api/repocheck", {"url": url})
    assert r.status_code == 200, r.text
    assert seen() == {"kind": "github", "credentials": False}
    r = form(real, "/api/repocheck", {"url": url, "key_github": "ghp_" + "x" * 36})
    assert r.status_code == 200
    assert seen()["credentials"] is True
    r = form(real, "/api/repocheck", {"url": url, "key_osf": "osfkey"})
    assert r.status_code == 200
    assert seen()["credentials"] is False


def test_a_pdf_is_not_a_repository(real: TestClient) -> None:
    assert send(real, path="/api/repocheck", data=PDF).status_code == 400
