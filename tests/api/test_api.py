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
    assert r.json()["status"] == "ok"  # plain JSON: scalars are not boxed (D79)


def test_modules(client: TestClient) -> None:
    body = client.get("/paper/modules").json()
    assert "marginal" in body["modules"]
    assert body["count"] == len(body["modules"])


def test_info_default_fields(client: TestClient, demo_json: bytes) -> None:
    rows = client.post("/paper/info", files=upload(demo_json)).json()
    assert rows[0]["title"] == "To Err is Human: An Empirical Investigation"
    assert rows[0]["paper_id"] == "to_err_is_human"
    # D79: every column is present, missing cells are null
    assert list(rows[0]) == ["paper_id", "title", "keywords", "doi", "description"]
    assert rows[0]["description"] is None


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
    assert r.json()["traffic_light"] == expected
    bad = client.post("/paper/module", files=upload(demo_json), data={"name": "nope"})
    assert bad.status_code == 400
    check = client.post(
        "/paper/check", files=upload(demo_json), data={"modules": "marginal", "report": "false"}
    ).json()
    assert check["modules_run"] == ["marginal"]
    assert check["results"]["marginal"]["traffic_light"] == expected
    assert check["report_html"] == ""
    assert isinstance(check["metacheck_version"], str)


def _spy_module(seen: list) -> object:
    """A module_find() whose function records the key options it runs under."""
    from types import SimpleNamespace

    from metacheck.utils import get_option

    def func(_paper: object) -> dict:
        seen.append(
            {
                "github": get_option("metacheck.github.pat"),
                "osf": get_option("metacheck.osf.pat"),
                "shared": get_option("metacheck.keys.shared"),
            }
        )
        return {"traffic_light": "na"}

    return lambda _name, *a, **k: SimpleNamespace(func=func)


def test_module_uses_the_senders_own_keys_for_that_request_only(
    client: TestClient, demo_json: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.utils import get_option, options

    old = options({"metacheck.github.pat": None, "metacheck.osf.pat": "server-osf"})
    seen: list = []
    try:
        monkeypatch.setattr("metacheck.module.module_find", _spy_module(seen))
        data = {"name": "marginal", "key_github": " my-gh ", "key_osf": ""}
        r = client.post("/paper/module", files=upload(demo_json), data=data)
        assert r.status_code == 200
        # the sender's key replaces the server's; an empty field keeps the server's
        assert seen[0] == {"github": "my-gh", "osf": "server-osf", "shared": True}
        # and it is gone when the request ends
        assert get_option("metacheck.github.pat") is None
        assert get_option("metacheck.osf.pat") == "server-osf"
        r = client.post("/paper/module", files=upload(demo_json), data={"name": "marginal"})
        assert r.status_code == 200
        assert seen[1]["github"] is None
    finally:
        options(old)


def test_a_net_client_may_send_content_type_before_content_disposition(
    client: TestClient, demo_json: bytes
) -> None:
    # .NET's MultipartFormDataContent writes a part's Content-Type first (#498)
    boundary = "----NetBoundary"
    head = (
        f"--{boundary}\r\nContent-Type: application/json\r\n"
        f'Content-Disposition: form-data; name="file"; filename="paper.json"\r\n\r\n'
    )
    tail = (
        f"\r\n--{boundary}\r\nContent-Type: text/plain\r\n"
        f'Content-Disposition: form-data; name="name"\r\n\r\nmarginal'
        f"\r\n--{boundary}--\r\n"
    )
    r = client.post(
        "/paper/module",
        content=head.encode() + demo_json + tail.encode(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 200, r.text
    assert "traffic_light" in r.json()


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


def test_plain_json_values() -> None:
    """D79: full-precision numbers, null for missing and non-finite values, every column."""
    import numpy as np
    import pandas as pd

    from metacheck.api.app import _json

    frame = pd.DataFrame(
        {
            "x": [0.123456789012, np.nan, np.inf],
            "n": pd.array([1, None, 3], dtype="Int64"),
            "s": pd.array(["a", None, "c"], dtype="string"),
            "b": pd.array([True, None, False], dtype="boolean"),
            "l": [[1, 2], [], None],
        }
    )
    body = {"table": frame, "none": None, "scalar": np.float64(2.5), "big": 1e300}
    assert orjson.loads(_json(body).body) == {
        "table": [
            {"x": 0.123456789012, "n": 1, "s": "a", "b": True, "l": [1, 2]},
            {"x": None, "n": None, "s": None, "b": None, "l": []},
            {"x": None, "n": 3, "s": "c", "b": False, "l": None},
        ],
        "none": None,
        "scalar": 2.5,
        "big": 1e300,
    }
