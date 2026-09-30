"""The FastAPI port of metacheck's plumber API (tests mirror test-plumber-*.R)."""

from __future__ import annotations

import orjson
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import metacheck as pc
from metacheck.api.app import create_app, parse_bool


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(scope="module")
def demo_json() -> bytes:
    return pc.demofile("json").read_bytes()


def upload(data: bytes, name: str = "paper.json") -> dict:
    return {"file": (name, data, "application/json")}


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == ["ok"]  # jsonlite boxes scalars


def test_modules(client: TestClient) -> None:
    body = client.get("/paper/modules").json()
    assert "marginal" in body["modules"]
    assert body["count"] == [len(body["modules"])]


def test_info_default_fields(client: TestClient, demo_json: bytes) -> None:
    rows = client.post("/paper/info", files=upload(demo_json)).json()
    assert rows[0]["title"] == "To Err is Human: An Empirical Investigation"
    assert rows[0]["paper_id"] == "to_err_is_human"
    assert "description" not in rows[0]  # NA cells are omitted, like jsonlite


def test_authors_and_references(client: TestClient, demo_json: bytes) -> None:
    authors = client.post("/paper/authors", files=upload(demo_json)).json()
    assert [a["family"] for a in authors] == ["Lakens", "Debruine", "Werner"]
    refs = client.post("/paper/references", files=upload(demo_json)).json()
    assert len(refs) == 5
    xrefs = client.post("/paper/cross-references", files=upload(demo_json)).json()
    assert len(xrefs) == 5


def test_search(client: TestClient, demo_json: bytes) -> None:
    r = client.post("/paper/search", files=upload(demo_json), data={"pattern": "significant"})
    assert r.status_code == 200
    assert all("significant" in row["text"].lower() for row in r.json())
    missing = client.post("/paper/search", files=upload(demo_json))
    assert missing.status_code == 400
    assert missing.json() == {"error": "Query parameter 'pattern' is required"}


def test_search_section(client: TestClient, demo_json: bytes) -> None:
    # U2: metacheck forwards `section` to text_search(), which fails ("unused argument")
    def search(**data: str) -> list[dict]:
        r = client.post("/paper/search", files=upload(demo_json), data=data)
        assert r.status_code == 200
        return r.json()

    everywhere = search(pattern="significant")
    assert {row["section_type"] for row in everywhere} == {"abstract", "method"}
    method = search(pattern="significant", section="method")
    assert method == [row for row in everywhere if row["section_type"] == "method"]
    assert search(pattern="significant", section="Method") == method
    both = search(pattern="significant", section="abstract, method", **{"return": "paragraph"})
    assert [row["section_type"] for row in both] == ["abstract", "method", "method"]
    assert search(pattern="significant", section="results") == []
    # the references are searched when asked for
    refs = search(pattern="Psych", section="references")
    assert refs and {row["section_type"] for row in refs} == {"references"}


def test_module_and_check(client: TestClient, demo_json: bytes) -> None:
    r = client.post("/paper/module", files=upload(demo_json), data={"name": "marginal"})
    assert r.status_code == 200
    expected = pc.module_run(pc.demopaper(), "marginal").traffic_light
    assert r.json()["traffic_light"] == [expected]
    bad = client.post("/paper/module", files=upload(demo_json), data={"name": "nope"})
    assert bad.status_code == 400
    check = client.post(
        "/paper/check", files=upload(demo_json), data={"modules": "marginal", "report": "false"}
    ).json()
    assert check["modules_run"] == ["marginal"]
    assert check["results"]["marginal"]["traffic_light"] == [expected]
    assert check["report_html"] == [""]


def test_upload_validation(client: TestClient, demo_json: bytes) -> None:
    assert client.post("/paper/authors").status_code == 400
    too_many = client.post(
        "/paper/authors", files=[("file", ("a.json", demo_json)), ("file", ("b.json", demo_json))]
    )
    assert too_many.json() == {"error": "Please upload only one file at a time."}
    broken = client.post("/paper/authors", files=upload(b"{not json"))
    assert broken.status_code == 400


def test_parse_bool() -> None:
    assert parse_bool("Yes") is True
    assert parse_bool("0") is False
    assert parse_bool("maybe", default=False) is False
    assert parse_bool(None) is True


def test_numbers_follow_jsonlite(demo_json: bytes) -> None:
    from metacheck.api.jsonlite import to_json

    assert orjson.loads(to_json([0.123456])) == [0.1235]
