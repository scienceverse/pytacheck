"""The whole app behind the guards, without a network: uploads and the report download."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from gradio.utils import get_upload_folder
from starlette.testclient import TestClient

import pytacheck as pc
from pytacheck.app.security import cookie_name
from pytacheck.app.server import create_app

PORT = 4322
TOKEN = "s3cret-token"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(
        create_app(PORT, TOKEN), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    ) as tc:
        assert tc.get(f"/?token={TOKEN}").status_code == 303
        assert cookie_name(PORT) in tc.cookies
        yield tc


def _join(client: TestClient, fn_index: int, path: str) -> str:
    """Send the check button's event with this file and read the event stream."""
    body = {
        "data": [{"path": path, "meta": {"_type": "gradio.FileData"}}, False],
        "fn_index": fn_index,
        "session_hash": "t1",
    }
    joined = client.post("/gradio_api/queue/join", json=body)
    assert joined.status_code == 200, joined.text
    with client.stream("GET", "/gradio_api/queue/data", params={"session_hash": "t1"}) as stream:
        text = ""
        for line in stream.iter_lines():
            text += line
            if '"process_completed"' in line:
                break
    return text


def _check_fn_index(client: TestClient) -> int:
    config = client.get("/config").json()
    (dep,) = (
        d for d in config["dependencies"] if d["targets"][0][1] == "click" and len(d["inputs"]) == 2
    )
    return int(dep["id"])


def test_a_path_outside_the_upload_folder_is_refused(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "secret.json"
    shutil.copy(pc.demofile("json"), outside)
    events = _join(client, _check_fn_index(client), str(outside))
    assert "it was not uploaded by a user" in events
    assert "Ran 16 checks" not in events
    assert "This paper demonstrates" not in events


def test_an_uploaded_file_is_checked(client: TestClient) -> None:
    files = {"files": ("paper.json", pc.demofile("json").read_bytes(), "application/json")}
    (path,) = client.post("/gradio_api/upload", files=files).json()
    assert Path(path).resolve().is_relative_to(Path(get_upload_folder()).resolve())
    events = _join(client, _check_fn_index(client), path)
    assert "Ran 16 checks" in events
    assert "/report/t1/paper_report.html" in events.replace("\\/", "/") or "_report.html" in events


def test_the_report_downloads_from_the_apps_own_folder(client: TestClient) -> None:
    files = {"files": ("paper.json", pc.demofile("json").read_bytes(), "application/json")}
    (path,) = client.post("/gradio_api/upload", files=files).json()
    _join(client, _check_fn_index(client), path)
    resp = client.get("/report/t1/paper_report.html")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "paper_report.html" in resp.headers["content-disposition"]
    assert resp.headers["content-security-policy"] == "sandbox"
    assert "<html" in resp.text.lower()
    assert client.get("/report/t1/nothing.html").status_code == 404
    assert client.get("/report/t1/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/report/nobody/paper_report.html").status_code == 404
    guard = TestClient(client.app, base_url=f"http://127.0.0.1:{PORT}")
    assert guard.get("/report/t1/paper_report.html").status_code == 403
