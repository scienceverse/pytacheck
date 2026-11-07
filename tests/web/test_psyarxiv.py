"""Fetching a PsyArXiv preprint for the web interface (no network: respx)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from metacheck.web.config import PSYARXIV_API
from metacheck.web.psyarxiv import PsyArxivError, fetch_preprint, psyarxiv_id
from tests.web.helpers import PDF

SITE = "https://next.psyarxiv.org"
RECORD = f"{PSYARXIV_API}/preprints/abcde"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abcde", "abcde"),
        ("  ABCDE ", "abcde"),
        ("https://osf.io/preprints/psyarxiv/abcde", "abcde"),
        ("https://osf.io/preprints/psyarxiv/abcde_v3/", "abcde"),
        ("https://next.psyarxiv.org/preprint/abcde?view_only=x#top", "abcde"),
        ("https://psyarxiv.com/abcde/", "abcde"),
        ("10.31234/osf.io/abcde", "abcde"),
        ("doi:10.31234/osf.io/abcde_v2", "abcde"),
        ("https://doi.org/10.31234/osf.io/abcde", "abcde"),
        ("https://dx.doi.org/10.31234/osf.io/28j7s", "28j7s"),
    ],
)
def test_the_id_is_taken_from_a_link(text: str, expected: str) -> None:
    assert psyarxiv_id(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "abcd",
        "x" * 13,
        "abc de",
        "abc/../../x",
        "https://example.com/",
        "abcde!",
        "a" * 301,
    ],
)
def test_a_bad_id_is_refused(text: str | None) -> None:
    with pytest.raises(ValueError, match=r"PsyArXiv|too long"):
        psyarxiv_id(text)


def record(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "A preprint",
        "state": "published",
        "versions": [{"contentType": "application/pdf", "fileUrl": "/files/abcde.pdf"}],
    }
    body.update(changes)
    return body


@respx.mock
def test_the_pdf_and_title_are_fetched(tmp_path: Path) -> None:
    respx.get(RECORD).respond(200, json=record())
    respx.get(f"{SITE}/files/abcde.pdf").respond(200, content=PDF)
    path, title = fetch_preprint("abcde", tmp_path, PSYARXIV_API)
    assert path == tmp_path / "input.pdf"
    assert path.read_bytes() == PDF
    assert title == "A preprint"


@respx.mock
def test_the_api_address_is_a_setting(tmp_path: Path) -> None:
    api = "https://staging.example.org/api/v1"
    respx.get(f"{api}/preprints/abcde").respond(200, json=record())
    respx.get("https://staging.example.org/files/abcde.pdf").respond(200, content=PDF)
    assert fetch_preprint("abcde", tmp_path, api + "/")[0].exists()


@respx.mock
def test_a_redirect_to_the_storage_is_followed(tmp_path: Path) -> None:
    respx.get(RECORD).respond(
        200, json=record(versions=[{"osfFileUrl": "https://osf.io/download/xyz/"}])
    )
    respx.get("https://osf.io/download/xyz/").respond(
        302, headers={"location": "https://files.example.org/stored.pdf"}
    )
    respx.get("https://files.example.org/stored.pdf").respond(200, content=PDF)
    assert fetch_preprint("abcde", tmp_path, PSYARXIV_API)[0].read_bytes() == PDF


@respx.mock
@pytest.mark.parametrize(
    "target",
    [
        "http://files.example.org/x.pdf",
        "https://127.0.0.1/x.pdf",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/x.pdf",
        "https://user:pass@files.example.org/x.pdf",
        "https://files.example.org:8443/x.pdf",
    ],
)
def test_a_redirect_to_a_private_place_is_refused(tmp_path: Path, target: str) -> None:
    respx.get(RECORD).respond(200, json=record())
    respx.get(f"{SITE}/files/abcde.pdf").respond(302, headers={"location": target})
    with pytest.raises(PsyArxivError, match="not allowed"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)
    assert not (tmp_path / "input.pdf").exists()


@respx.mock
def test_a_file_address_on_another_site_is_not_used(tmp_path: Path) -> None:
    respx.get(RECORD).respond(
        200, json=record(versions=[{"fileUrl": "https://evil.example.org/x.pdf"}])
    )
    with pytest.raises(PsyArxivError, match="no file"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)


@respx.mock
def test_redirects_that_never_end_are_stopped(tmp_path: Path) -> None:
    respx.get(RECORD).respond(200, json=record())
    respx.get(f"{SITE}/files/abcde.pdf").respond(
        302, headers={"location": f"{SITE}/files/abcde.pdf"}
    )
    with pytest.raises(PsyArxivError, match="circles"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)


@respx.mock
@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (httpx.Response(404), "no preprint"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, content=b"<html>not json"), "cannot be read"),
        (httpx.Response(200, json=[1, 2]), "cannot be read"),
        (httpx.Response(200, json=record(state="withdrawn")), "withdrawn"),
        (
            httpx.Response(
                200, json=record(versions=[{"contentType": "application/msword", "fileUrl": "/x"}])
            ),
            "only PDF",
        ),
        (httpx.Response(200, json=record(versions=[])), "no file"),
    ],
)
def test_a_record_that_cannot_be_used(tmp_path: Path, answer: httpx.Response, text: str) -> None:
    respx.get(RECORD).mock(return_value=answer)
    with pytest.raises(PsyArxivError, match=text):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)


@respx.mock
def test_a_file_that_is_not_a_pdf_is_refused_and_removed(tmp_path: Path) -> None:
    respx.get(RECORD).respond(200, json=record())
    respx.get(f"{SITE}/files/abcde.pdf").respond(200, content=b"<html>login</html>")
    with pytest.raises(PsyArxivError, match="not the PDF"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)
    assert not (tmp_path / "input.pdf").exists()


@respx.mock
def test_a_file_over_50_mb_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck.web import psyarxiv

    monkeypatch.setattr(psyarxiv, "MAX_UPLOAD_BYTES", 100)
    respx.get(RECORD).respond(200, json=record())
    respx.get(f"{SITE}/files/abcde.pdf").respond(200, content=PDF + b"x" * 500)
    with pytest.raises(PsyArxivError, match="larger than 50 MB"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)
    assert not (tmp_path / "input.pdf").exists()


@respx.mock
def test_a_server_that_does_not_answer(tmp_path: Path) -> None:
    respx.get(RECORD).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(PsyArxivError, match="did not answer"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)
    respx.get(RECORD).respond(200, json=record())
    respx.get(f"{SITE}/files/abcde.pdf").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PsyArxivError, match="did not send the file"):
        fetch_preprint("abcde", tmp_path, PSYARXIV_API)
