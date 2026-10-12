"""POST /package/check: a data package sent as an archive or as a repository link."""

from __future__ import annotations

import dataclasses
import io
import logging
import socket
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
import respx
from fastapi.testclient import TestClient

from metacheck import http
from metacheck.api.app import create_app
from metacheck.app import package as hosted_package
from metacheck.datapackage import PackageError

KEY = "k" * 32
PUBLIC = "140.82.112.3"
GITHUB_API = "https://api.github.com/repos/o/repo/zipball"
CODELOAD = "https://codeload.github.com/o/repo/legacy.zip/refs/heads/main"
PACKAGE = {
    "study/README.md": "# Study\n\nA small study of reaction times.\n",
    "study/data/results.csv": "id,score\n1,0.5\n2,0.7\n",
}
CHECKS = {"package_files", "package_structure", "package_docs", "package_pii"}


def zip_bytes(members: dict[str, str] = PACKAGE) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def upload(data: bytes, name: str = "study.zip") -> dict[str, Any]:
    return {"file": (name, data, "application/zip")}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No name is looked up: a test that reaches for the network fails here."""

    def refuse(host: str, *args: Any, **kwargs: Any) -> None:
        raise OSError(f"the tests do not reach {host}")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("PYTACHECK_API_KEY", raising=False)
    monkeypatch.delenv("METACHECK_API_KEY", raising=False)
    return TestClient(create_app())


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The temporary folder of this process. Nothing is left in it after a request."""
    folder = tmp_path / "scratch"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture
def hermetic_link(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A public address for every name, no proxy, and a fresh HTTP client, for a mocked fetch."""
    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("GITHUB_PAT_GITHUB_COM", "ghp_this_servers_own_token")

    def lookup(host: str, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    http.close_client()
    yield
    http.close_client()


def test_an_archive_is_checked_and_answered(client: TestClient, scratch: Path) -> None:
    r = client.post("/package/check", files=upload(zip_bytes()))
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "study"
    assert {row["check"] for row in body["checks"]} >= CHECKS
    assert all(set(row) == {"check", "status", "result"} for row in body["checks"])
    assert {row["check"]: row["status"] for row in body["checks"]}["package_files"] == "green"
    assert "<html" in body["report_html"]
    # data_check names the package by its local folder in the report: that folder must not
    # reach the answer, and the folders the upload and the unpacking used are removed
    assert str(scratch) not in r.text
    assert "metacheck-package-" not in r.text
    assert list(scratch.iterdir()) == []


def test_the_checks_run_under_the_hosted_limits_and_without_a_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.datapackage as datapackage

    seen: dict[str, Any] = {}
    real = datapackage.check_package

    def spy(target: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real(target, **kwargs)

    monkeypatch.setattr(datapackage, "check_package", spy)
    assert client.post("/package/check", files=upload(zip_bytes())).status_code == 200
    limits = hosted_package.HOSTED
    assert seen["max_bytes"] == limits.unpacked
    assert seen["max_files"] == limits.files
    assert seen["max_ratio"] == limits.ratio
    assert seen["max_depth"] == limits.depth
    assert seen["max_download"] == limits.download
    assert seen["download_timeout"] == limits.download_seconds
    assert seen["credentials"] is False
    # the concepts come from rules: a shared server downloads no classifier model
    assert seen["args"] == {"data_check": {"concepts": "rules"}}


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/owner/repo",
        "https://localhost/owner/repo",
        "https://user:secret@github.com/owner/repo",
        "https://github.com:8443/owner/repo",
        "/etc/passwd",
        "file:///etc/passwd",
    ],
)
def test_a_link_to_a_host_that_is_not_allowed_is_refused(client: TestClient, text: str) -> None:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        r = client.post("/package/check", data={"link": text})
    assert r.status_code == 400
    assert isinstance(r.json()["error"], str) and r.json()["error"]
    assert router.calls.call_count == 0  # refused before anything is fetched


def test_a_link_to_a_repository_is_fetched_and_checked(
    client: TestClient, scratch: Path, hermetic_link: None
) -> None:
    with respx.mock(assert_all_called=False) as router:
        api = router.get(GITHUB_API).respond(302, headers={"location": CODELOAD})
        router.get(CODELOAD).respond(
            200, content=zip_bytes(), headers={"content-type": "application/zip"}
        )
        r = client.post("/package/check", data={"link": "https://github.com/o/repo"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "repo"
    assert {row["check"] for row in body["checks"]} >= CHECKS
    assert "authorization" not in api.calls[0].request.headers  # no token of this server
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    ("files", "data"),
    [
        (
            [
                ("file", ("a.zip", zip_bytes(), "application/zip")),
                ("file", ("b.zip", zip_bytes(), "application/zip")),
            ],
            None,
        ),
        (None, {"link": ["https://github.com/o/repo", "https://github.com/o/other"]}),
        (None, {"file": "not an upload"}),
    ],
)
def test_one_file_or_one_link_at_a_time(
    client: TestClient, files: Any, data: dict[str, Any] | None
) -> None:
    r = client.post("/package/check", files=files, data=data)
    assert r.status_code == 400
    assert r.json()["error"]


def test_a_blank_link_next_to_a_file_is_ignored(client: TestClient) -> None:
    r = client.post("/package/check", files=upload(zip_bytes()), data={"link": "   "})
    assert r.status_code == 200


def test_a_blank_link_on_its_own_is_neither(client: TestClient) -> None:
    r = client.post("/package/check", data={"link": "   "})
    assert r.status_code == 400
    assert "'file'" in r.json()["error"]


def test_a_request_with_both_says_so(client: TestClient) -> None:
    r = client.post(
        "/package/check",
        files=upload(zip_bytes()),
        data={"link": "https://github.com/o/repo"},
    )
    assert r.status_code == 400
    assert r.json() == {"error": "Give a file or a link, not both."}


def test_a_request_with_neither_says_so(client: TestClient) -> None:
    r = client.post("/package/check")
    assert r.status_code == 400
    assert "'file'" in r.json()["error"] and "'link'" in r.json()["error"]


@pytest.mark.parametrize("size", [1000, 1001])
def test_an_archive_over_the_hosted_byte_limit_is_413(
    client: TestClient, scratch: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    # the limit is lowered to 1000 bytes here, rather than uploading 50 MB
    small = dataclasses.replace(hosted_package.HOSTED, download=1000)
    monkeypatch.setattr(hosted_package, "HOSTED", small)
    r = client.post("/package/check", files=upload(b"x" * size))
    if size == 1000:  # exactly the limit is not over it: it reaches the archive check
        assert r.status_code == 400
        assert "cannot be read as an archive" in r.json()["error"]
    else:
        assert r.status_code == 413
        assert r.json()["error"] == "The package is bigger than the 1,000 bytes this server checks."
    assert list(scratch.iterdir()) == []


def test_a_body_far_over_the_limit_is_refused_before_it_is_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = dataclasses.replace(hosted_package.HOSTED, download=1000)
    monkeypatch.setattr(hosted_package, "HOSTED", small)
    r = client.post("/package/check", files=upload(b"x" * 1_100_000))
    assert r.status_code == 413
    assert r.json()["error"].startswith("The package is bigger than")


def test_a_key_is_needed_when_one_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTACHECK_API_KEY", KEY)
    c = TestClient(create_app())
    assert c.post("/package/check", files=upload(zip_bytes())).status_code == 401
    r = c.post(
        "/package/check",
        files=upload(zip_bytes()),
        headers={"Authorization": f"Bearer {KEY}"},
    )
    assert r.status_code == 200


def test_a_package_error_is_400_with_its_message(
    client: TestClient, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(target: Any, **kwargs: Any) -> None:
        raise PackageError("The archive holds more than 20,000 files")

    monkeypatch.setattr("metacheck.datapackage.check_package", refuse)
    r = client.post("/package/check", files=upload(zip_bytes()))
    assert r.status_code == 400
    assert r.json() == {"error": "The archive holds more than 20,000 files"}
    assert list(scratch.iterdir()) == []


def test_a_long_package_error_is_cut_to_300_characters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(target: Any, **kwargs: Any) -> None:
        raise PackageError("x" * 1000)

    monkeypatch.setattr("metacheck.datapackage.check_package", refuse)
    r = client.post("/package/check", files=upload(zip_bytes()))
    assert r.status_code == 400
    assert len(r.json()["error"]) == 300


def test_a_package_error_that_names_the_upload_folder_gives_a_fixed_message(
    client: TestClient, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(target: Any, **kwargs: Any) -> None:
        raise PackageError(f"No folder or archive found at {target}")

    monkeypatch.setattr("metacheck.datapackage.check_package", refuse)
    r = client.post("/package/check", files=upload(zip_bytes()))
    assert r.status_code == 400
    assert r.json() == {"error": "The archive could not be opened as a data package."}
    assert str(scratch) not in r.text
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (b"not a zip at all", "study.zip", "cannot be read as an archive"),
        (zip_bytes(), "paper.pdf", "neither a folder nor an archive"),
    ],
    ids=["not-a-zip", "pdf-name"],
)
def test_an_archive_that_cannot_be_opened_is_400(
    client: TestClient, scratch: Path, data: bytes, name: str, message: str
) -> None:
    r = client.post("/package/check", files=upload(data, name))
    assert r.status_code == 400
    assert message in r.json()["error"]
    assert list(scratch.iterdir()) == []


def test_an_unexpected_exception_is_500_with_a_fixed_message(
    client: TestClient,
    scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(target: Any, **kwargs: Any) -> None:
        raise RuntimeError("secret detail /srv/keys/platform.env")

    monkeypatch.setattr("metacheck.datapackage.check_package", broken)
    with caplog.at_level(logging.DEBUG):
        r = client.post("/package/check", files=upload(zip_bytes()))
    assert r.status_code == 500
    assert r.json() == {"error": "The package could not be checked."}
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text and "/srv/keys" not in caplog.text
    assert list(scratch.iterdir()) == []


def test_the_report_is_empty_when_it_cannot_be_rendered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.report.report as report

    def fail(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("no template")

    monkeypatch.setattr(report, "render_module_outputs", fail)
    r = client.post("/package/check", files=upload(zip_bytes()))
    assert r.status_code == 200
    assert r.json()["report_html"] == ""
    assert {row["check"] for row in r.json()["checks"]} >= CHECKS
