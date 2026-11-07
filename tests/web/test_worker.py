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


# -- the layout ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "paper", "repository"),
    [("", "full", "brief"), ("checklist", "checklist", "checklist")],
)
def test_the_layout_of_each_report_type(asked: str, paper: str, repository: str) -> None:
    assert worker.report_layout(asked, repository=False) == paper
    assert worker.report_layout(asked, repository=True) == repository


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


def test_a_checklist_is_asked_for_and_made(real: TestClient, demo_json: bytes) -> None:
    r = send(
        real, "demo.json", demo_json, "/api/report", modules="marginal", report_type="checklist"
    )
    assert r.status_code == 200
    assert r.headers["x-metacheck-report-type"] == "checklist"
    assert '<nav class="counts"' in r.text


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


def test_a_visitors_keys_are_used_in_the_job_only(
    real: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from metacheck.archives import api_keys
    from metacheck.utils import get_option

    secret = "ghp_" + "S" * 36
    for name in ("GITHUB_PAT_GITHUB_COM", "OSF_PAT", "RESEARCHBOX_API_KEY", "OPENALEX_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def probe(spec: dict[str, Any], folder: Path, report: Any) -> dict[str, Any]:
        from metacheck.archives.osf_helpers import osf_pat

        out = folder / "result.json"
        out.write_text(
            json.dumps(
                {
                    "github": api_keys.github_pat() == secret,
                    "researchbox": api_keys.rbox_token(),
                    "openalex": api_keys.openalex_key(),
                    "osf": osf_pat(),
                    "shared": get_option("metacheck.keys.shared"),
                    "tmp_in_job": tempfile.gettempdir().startswith(str(folder)),
                }
            )
        )
        return {"result": out.name, "type": "application/json", "converter": None}

    monkeypatch.setattr(worker, "_paper_job", probe)
    r = send(
        real,
        "demo.json",
        b'{"a": 1}',
        "/api/check",
        key_github=secret,
        key_researchbox="rb-key",
        key_openalex="oa-key",
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "github": True,
        "researchbox": "rb-key",
        "openalex": "oa-key",
        "osf": "",
        "shared": True,
        "tmp_in_job": True,
    }
    # this process never had them
    for service in ("github", "researchbox", "openalex"):
        assert get_option(api_keys.KEY_SERVICES[service].option) is None
    assert "GITHUB_PAT_GITHUB_COM" not in os.environ
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


def test_a_repository_checklist(real: TestClient) -> None:
    r = send(real, "study.zip", study_zip(), "/api/repocheck", report_type="checklist")
    assert r.status_code == 200
    assert r.headers["x-metacheck-report-type"] == "checklist"
    assert '<nav class="counts"' in r.text
    assert str(real.app.state.manager.root) not in r.text  # type: ignore[attr-defined]


# -- a ResearchBox box ---------------------------------------------------------------------


@pytest.fixture
def researchbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[..., Callable[[], dict[str, Any]]]:
    """ResearchBox, faked: a box's file list and its download. Returns a function that sets
    what the box holds; that returns what the job asked for (the job runs in a process of
    its own, so it is written to a file)."""
    import pandas as pd

    from metacheck.archives import researchbox as rbox

    record = tmp_path / "researchbox.json"
    monkeypatch.setenv("RESEARCHBOX_API_KEY", "the-servers-key")

    def make(
        files: dict[str, str] | None = None,
        sizes: list[float] | None = None,
        error: str | None = None,
        failed: int = 0,
    ) -> Callable[[], dict[str, Any]]:
        box = files if files is not None else {"Data/data.csv": "id,x\n1,2\n", "code.R": "1\n"}

        def rbox_files(url: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
            if error is not None:
                return pd.DataFrame({"rb_url": [url], "error": [error]})
            return pd.DataFrame(
                {
                    "rb_url": [url] * len(box),
                    "name": list(box),
                    "size": sizes if sizes is not None else [float(len(v)) for v in box.values()],
                }
            )

        def rbox_file_download(url: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
            from metacheck.archives.download import _repo_session_dir

            out = Path(_repo_session_dir()) / "researchbox.org_1234" / "unzipped"
            for name, text in box.items():
                (out / name).parent.mkdir(parents=True, exist_ok=True)
                (out / name).write_text(text)
            record.write_text(
                json.dumps({"url": url, "cache": kwargs.get("cache"), "where": str(out)})
            )
            got = pd.DataFrame(
                {"name": list(box), "file_location": [f"{out}/{name}" for name in box]}
            )
            got.attrs["failed"] = pd.DataFrame(
                {"rb_url": [url] * failed, "name": ["code.R"] * failed, "error": ["md5"] * failed}
            )
            return got

        monkeypatch.setattr(rbox, "rbox_files", rbox_files)
        monkeypatch.setattr(rbox, "rbox_file_download", rbox_file_download)
        return lambda: json.loads(record.read_text())  # type: ignore[no-any-return]

    return make


def test_a_researchbox_box_is_downloaded_into_the_job_and_checked(
    real: TestClient, researchbox: Callable[..., Callable[[], dict[str, Any]]]
) -> None:
    asked = researchbox()
    link = "https://researchbox.org/1234&PEER_REVIEW_passcode=ABC123"
    r = form(real, "/api/repocheck", {"url": link, "report_type": "checklist"})
    assert r.status_code == 200, r.text
    assert r.headers["x-metacheck-report-type"] == "checklist"
    assert "researchbox-1234" in r.text
    seen = asked()
    assert seen["url"] == link and seen["cache"] is False
    manager = real.app.state.manager  # type: ignore[attr-defined]
    assert seen["where"].startswith(str(manager.root))  # the download stayed in the job
    assert str(manager.root) not in r.text
    assert not Path(seen["where"]).exists()  # and is gone


@pytest.mark.parametrize(
    ("box", "message"),
    [
        ({"sizes": [400e6, 200e6]}, "more than the 20,000 files or 500 MB"),
        ({"sizes": [float("nan"), 1.0]}, "did not say how big"),
        ({"error": "unfound"}, "no public box with that number"),
        ({"error": "API rate limit exhausted: wait"}, "limit on requests is used up"),
        ({"files": {}}, "no files to check"),
        ({"failed": 1}, "did not arrive intact"),
    ],
)
def test_a_researchbox_box_that_cannot_be_checked_fails_with_a_message(
    real: TestClient,
    researchbox: Callable[..., Callable[[], dict[str, Any]]],
    box: dict[str, Any],
    message: str,
) -> None:
    researchbox(**box)
    r = form(real, "/api/repocheck", {"url": "https://researchbox.org/1234"})
    assert r.status_code == 422, r.text
    assert message in r.json()["error"]


def test_a_pdf_is_not_a_repository(real: TestClient) -> None:
    assert send(real, path="/api/repocheck", data=PDF).status_code == 400
