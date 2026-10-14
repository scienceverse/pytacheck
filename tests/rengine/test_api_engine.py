"""``engine=r`` on the REST API's ``/paper/check`` (with the stand-in Rscript)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import metacheck as pc
from metacheck._version import UPSTREAM
from metacheck.api.app import ApiConfigError, create_app, default_engine


def upload() -> dict:
    return {"file": ("paper.json", pc.demofile("json").read_bytes(), "application/json")}


@pytest.fixture
def client(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("METACHECK_API_ENGINE", raising=False)
    return TestClient(create_app())


def test_modules_of_each_engine(client: TestClient) -> None:
    r = client.get("/paper/modules", params={"engine": "r"}).json()
    assert r == {"modules": ["marginal", "stat_check", "data_check"], "count": 3}
    python = client.get("/paper/modules").json()
    assert "author_check" in python["modules"]
    assert client.get("/paper/modules", params={"engine": "julia"}).status_code == 400


def test_check_with_r(client: TestClient) -> None:
    r = client.post(
        "/paper/check", files=upload(), data={"engine": "r", "modules": "marginal,stat_check"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "r" and body["metacheck_version"] == "9.9.9"
    assert body["modules_run"] == ["marginal", "stat_check"]
    result = body["results"]["marginal"]
    # R read the uploaded file itself
    assert result["summary_text"] == "marginal ran on paper.json"
    assert result["traffic_light"] == "yellow"
    assert result["table"] == [{"text": "a", "p": 0.04}, {"text": None, "p": None}]
    assert result["report"][1] == [{"x": "cell"}]  # the table block, as rows
    assert "9.9.9 (R metacheck on R 4.5.3" in body["report_html"]
    # the paper's metadata comes from pytacheck's reading, as with the Python engine
    assert [a["family"] for a in body["authors"]] == ["Lakens", "Debruine", "Werner"]


def test_check_without_modules_runs_every_r_module(client: TestClient) -> None:
    body = client.post("/paper/check", files=upload(), data={"engine": "r", "report": "false"})
    assert body.json()["modules_run"] == ["marginal", "stat_check", "data_check"]


def test_r_refuses_what_it_does_not_have(client: TestClient) -> None:
    r = client.post(
        "/paper/check", files=upload(), data={"engine": "r", "modules": "marginal,author_check"}
    )
    assert r.status_code == 400
    assert "Invalid modules for the R engine: author_check" in r.json()["error"]
    bad = client.post("/paper/check", files=upload(), data={"engine": "julia"})
    assert bad.status_code == 400 and "Invalid engine: julia" in bad.json()["error"]


def test_python_stays_the_default(client: TestClient) -> None:
    body = client.post("/paper/check", files=upload(), data={"modules": "marginal"}).json()
    assert body["engine"] == "python"
    assert body["metacheck_version"] == UPSTREAM["version"]


def test_r_failures(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.get("/paper/modules", params={"engine": "r"})  # R's modules are known now
    monkeypatch.setenv("FAKE_R_MODE", "error")
    r = client.post("/paper/check", files=upload(), data={"engine": "r", "modules": "marginal"})
    assert r.status_code == 500
    assert "The R engine failed: cannot open file" in r.json()["error"]


def test_the_server_default(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METACHECK_API_ENGINE", "r")
    assert default_engine() == "r"
    client = TestClient(create_app())
    body = client.post("/paper/check", files=upload(), data={"modules": "marginal"}).json()
    assert body["engine"] == "r"
    monkeypatch.setenv("METACHECK_API_ENGINE", "julia")
    with pytest.raises(ApiConfigError, match="use one of python, r"):
        default_engine()


def test_no_r_no_r_default(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_R_MODE", "crash")
    monkeypatch.setenv("METACHECK_API_ENGINE", "r")
    with pytest.raises(ApiConfigError, match="The R engine is the default, but"):
        create_app()
    monkeypatch.delenv("METACHECK_API_ENGINE")
    client = TestClient(create_app())
    r = client.post("/paper/check", files=upload(), data={"engine": "r"})
    assert r.status_code == 400 and "cannot run the R engine" in r.json()["error"]
