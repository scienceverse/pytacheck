"""The website: its pages and assets are served, they need nothing from outside, and the
``pytacheck site`` command refuses an open server unless asked."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from metacheck import cli
from metacheck.web import app as web_app
from metacheck.web.app import PAGE_POLICY, SITE_DIR

PAGES = ["/", "/researchbox/", "/psyarxiv/"]
ASSETS = [
    "/site/site.css",
    "/site/common.js",
    "/site/site.js",
    "/site/linkcheck.js",
    "/site/vendor/jszip.min.js",
    "/site/vendor/LICENSE.markdown",
    "/site/fonts/Cabin.ttf",
    "/site/fonts/OFL-Cabin.txt",
    "/site/images/logo.png",
]
# the files as recorded in NOTICE and docs/SITE.md
JSZIP_SHA256 = "acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e"
JSZIP_LICENSE_SHA256 = "566c953c6090b1218ca6217dd7359d45dde46581968586dc607d59a78af6a9c4"


@pytest.mark.parametrize("path", PAGES)
def test_pages_are_served(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>Metacheck" in r.text


def test_the_main_page_has_what_the_site_has(client: TestClient) -> None:
    text = client.get("/").text
    for expected in [
        "An Automated Tool to Check for Best Practices in Scientific Articles",
        "Run Metacheck on a Paper",
        "Run Metacheck on a Repository",
        "More checks",
        "What happens to your files",
        "Use it from your own code",
        'href="/researchbox/"',
        'href="/psyarxiv/"',
        'id="version"',
        "/site/vendor/jszip.min.js",
    ]:
        assert expected in text, expected
    assert 'name="viewport"' in text


@pytest.mark.parametrize("path", ASSETS)
def test_assets_are_served(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert len(r.content) > 100


def test_asset_types(client: TestClient) -> None:
    assert "javascript" in client.get("/site/site.js").headers["content-type"]
    assert client.get("/site/site.css").headers["content-type"].startswith("text/css")
    assert client.get("/site/images/logo.png").headers["content-type"] == "image/png"


def test_the_vendored_files_are_the_recorded_ones(client: TestClient) -> None:
    jszip = client.get("/site/vendor/jszip.min.js").content
    assert hashlib.sha256(jszip).hexdigest() == JSZIP_SHA256
    licence = client.get("/site/vendor/LICENSE.markdown").content
    assert hashlib.sha256(licence).hexdigest() == JSZIP_LICENSE_SHA256
    assert b"3.10.1" in jszip[:400]
    assert b"Cabin" in client.get("/site/fonts/OFL-Cabin.txt").content


def test_unknown_paths_are_404_and_cannot_leave_the_site_folder(client: TestClient) -> None:
    assert client.get("/nothing").status_code == 404
    assert client.get("/site/nothing.js").status_code == 404
    assert client.get("/site/../worker.py").status_code == 404
    assert client.get("/site/%2e%2e/worker.py").status_code == 404
    assert client.get("/site/..%2fworker.py").status_code == 404
    assert client.get("/researchbox/x").status_code == 404


def test_pages_may_load_only_from_themselves(client: TestClient) -> None:
    r = client.get("/")
    assert r.headers["content-security-policy"] == PAGE_POLICY
    for part in ("default-src 'none'", "script-src 'self'", "connect-src 'self'"):
        assert part in PAGE_POLICY
    assert "unsafe" not in PAGE_POLICY
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert client.get("/site/site.js").headers["content-security-policy"] == PAGE_POLICY
    # the web interface answers data, not pages: no page policy there
    assert "content-security-policy" not in client.get("/api/health").headers
    assert client.get("/api/health").headers["x-content-type-options"] == "nosniff"


def test_no_page_needs_another_site() -> None:
    for page in SITE_DIR.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        # a link to another site is a link; nothing may load from one
        loads = re.findall(
            r"<(?:script|link|img|iframe|source|video|audio)\b[^>]*(?:src|href)=[\"']?(?:https?:)?//",
            text,
        )
        assert not loads, (page.name, loads)
        assert "<style" not in text and "style=" not in text, page.name
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), page.name  # no inline script
        assert not re.search(r"\son[a-z]+=", text), page.name  # no inline handlers
    for script in SITE_DIR.glob("*.js"):
        text = script.read_text(encoding="utf-8")
        assert not re.search(r"fetch\(\s*[\"'`]https?:", text), script.name
        assert not re.search(r"\.open\([^)]*[\"'`]https?:", text), script.name
        assert "import(" not in text and "importScripts" not in text, script.name
        assert "eval(" not in text and "innerHTML" not in text, script.name
    css = (SITE_DIR / "site.css").read_text(encoding="utf-8")
    assert not re.search(r"url\(\s*[\"']?(?:https?:)?//", css)
    assert "@import" not in css


def test_everything_a_page_loads_is_served(client: TestClient) -> None:
    for page in PAGES:
        text = client.get(page).text
        for reference in re.findall(r"(?:src|href)=\"(/site/[^\"]+)\"", text):
            assert client.get(reference).status_code == 200, (page, reference)
    css = client.get("/site/site.css").text
    for reference in re.findall(r"url\(\"?(/site/[^)\"]+)", css):
        assert client.get(reference).status_code == 200, reference


def test_the_scripts_use_the_documented_routes() -> None:
    common = (SITE_DIR / "common.js").read_text(encoding="utf-8")
    assert 'const API = "/api"' in common
    assert '"/jobs"' in common and "/health" in common
    assert "POLL_MS = 2000" in common
    page = (SITE_DIR / "site.js").read_text(encoding="utf-8")
    assert "/modules" in page
    assert "report_type" in page and "checklist" in page
    assert "20000" in page and "1000 * MB" in page and "500 * MB" in page
    boxes = (SITE_DIR / "linkcheck.js").read_text(encoding="utf-8")
    assert "researchbox.org/" in boxes and "psyarxiv" in boxes


def test_the_pages_work_at_phone_width() -> None:
    css = (SITE_DIR / "site.css").read_text(encoding="utf-8")
    assert "min-width: 900px" in css  # two columns only when there is room
    assert "overflow-wrap" in css
    assert "box-sizing: border-box" in css


# -- the command ---------------------------------------------------------------------------


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    import uvicorn

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))
    monkeypatch.setattr(web_app, "_docker_ready", lambda: False)
    yield calls
    for call in calls:
        call["app"].state.manager.close()


def test_site_serves_on_the_loopback_by_default(served: list[dict[str, Any]]) -> None:
    assert cli.main(["site"]) == 0
    assert served[0]["host"] == "127.0.0.1"
    assert served[0]["port"] == 8080
    assert served[0]["app"].state.run_code is False


def test_site_refuses_an_open_address_unless_asked(
    served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["site", "--host", "0.0.0.0"]) != 0
    assert not served
    assert "--public" in capsys.readouterr().err
    assert cli.main(["site", "--public", "--port", "9000"]) == 0
    assert (served[0]["host"], served[0]["port"]) == ("0.0.0.0", 9000)
    assert cli.main(["site", "--host", "0.0.0.0", "--public"]) == 0


def test_run_code_without_docker_is_said_and_stays_off(
    served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["site", "--run-code"]) == 0
    assert "Docker is not running" in capsys.readouterr().err
    assert served[0]["app"].state.run_code is False


def test_run_code_with_docker_is_on(
    served: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_app, "_docker_ready", lambda: True)
    assert cli.main(["site", "--run-code"]) == 0
    assert served[0]["app"].state.run_code is True
