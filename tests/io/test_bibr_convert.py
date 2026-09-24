"""Port of tests/testthat/test-import-bibr.R (remote bibr client and author helpers)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.io.bibr_convert import (
    _bibr_isalive,
    _bibr_save_result,
    _coerce_bib_authors,
    _parse_author_string,
    convert_bibr,
    format_bib_authors,
)
from tests.io.conftest import api

SCIVRS_URL = "https://platform.metacheck.app"
SELFHOSTED_URL = "http://localhost:8000"


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


class Recorder:
    """A mock bibr server (both backends) that records requests."""

    def __init__(self, status: str = "complete") -> None:
        self.urls: list[str] = []
        self.fields: dict[str, str] | None = None
        self.headers: list[httpx.Headers] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        self.headers.append(request.headers)
        if request.method == "POST":
            self.fields = _form_fields(request)
        if url.endswith("/jobs"):
            return httpx.Response(200, json={"job_id": "test-job"})
        if url.endswith("/result"):
            return httpx.Response(200, json={"text": "mock"})
        if url.endswith("/papers/extract"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": self.status, "stage": "parsing"})


@pytest.fixture
def server() -> Any:
    rec = Recorder()
    with api(routes={("GET", re.compile(".*")): rec, ("POST", re.compile(".*")): rec}):
        yield rec


@pytest.fixture
def pdf() -> Path:
    return pc.demofile("pdf")


def test_convert_bibr_bad_arg() -> None:
    with pytest.raises(TypeError):
        convert_bibr()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="should be one of"):
        convert_bibr(pc.demofile("pdf"), backend="cloud")


def test_selfhosted_include_figures(server: Recorder, pdf: Path, tmp_path: Path) -> None:
    out = convert_bibr(pdf, tmp_path, backend="selfhosted", include_figures=True)
    assert server.fields["include_figures"] == "true"
    assert out == f"{tmp_path}/to_err_is_human.json"
    assert Path(out).read_bytes() == b'{"status":"ok"}'

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
    failed = Recorder(status="failed")
    with api(routes={("GET", re.compile(".*")): failed, ("POST", re.compile(".*")): failed}):
        with pytest.raises(RuntimeError, match="Job test-job failed: parsing"):
            convert_bibr(pdf, tmp_path, backend="scivrs")
    queued = Recorder(status="queued")
    with api(routes={("GET", re.compile(".*")): queued, ("POST", re.compile(".*")): queued}):
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
        (httpx.Response(200, text="<html>login</html>"), "API key is not valid"),
        (httpx.Response(200, json={"status": "starting", "checks": {}}), "not ready200"),
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
