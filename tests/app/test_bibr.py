"""The bibr option: address lookup, key handling and the plain errors, against a fake server."""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import orjson
import pytest
import respx

import pytacheck as pc
from pytacheck.app import bibr, run, ui
from pytacheck.io.convert import SERVERS_URL

BIBR = "https://bibr.example.test"
OTHER = "https://other.example.test"
KEY = "test-key-1234567890"
FIXTURES = Path(__file__).resolve().parents[1] / "bibr12" / "fixtures"


def _list(*entries: dict[str, str]) -> httpx.Response:
    return httpx.Response(200, json=list(entries))


GROBID = {"id": "g", "service": "grobid", "url": "https://grobid.example.test"}


def _pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "Some Paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really")
    return pdf


class FakeBibr:
    """A bibr service: the job queue of the platform, answering with ``result`` bytes."""

    def __init__(self, router: respx.MockRouter, result: bytes | None = None) -> None:
        self.result = (
            result if result is not None else (FIXTURES / "bibr_12_1_full.json").read_bytes()
        )
        self.submit = httpx.Response(200, json={"job_id": "j1"})
        self.status = httpx.Response(200, json={"status": "complete"})
        self.requests: list[httpx.Request] = []
        router.route(host="bibr.example.test").mock(side_effect=self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/jobs":
            if isinstance(self.submit, Exception):
                raise self.submit
            return self.submit
        if path == "/jobs/j1":
            return self.status
        if path == "/jobs/j1/result":
            return httpx.Response(200, content=self.result)
        return httpx.Response(404)


@pytest.fixture
def router() -> Any:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SERVERS_URL).mock(
            return_value=_list(GROBID, {"id": "b", "service": "bibr", "url": BIBR})
        )
        yield mock


@pytest.fixture
def server(router: respx.MockRouter) -> FakeBibr:
    return FakeBibr(router)


def check(tmp_path: Path, key: str = KEY) -> run.Analysis:
    return run.check_paper(_pdf(tmp_path), workdir=tmp_path, pdf="bibr", bibr_key=key)


def test_a_pdf_goes_to_bibr_and_the_answer_is_checked(server: FakeBibr, tmp_path: Path) -> None:
    analysis = check(tmp_path)
    assert analysis.rows
    assert all(not r.result.startswith("Failed") for r in analysis.rows)
    assert analysis.name == "Some Paper"
    submit = server.requests[0]
    assert submit.method == "POST" and str(submit.url) == f"{BIBR}/jobs"
    assert submit.headers["authorization"] == f"Bearer {KEY}"
    assert b"%PDF-1.4" in submit.read()
    assert all(r.url.host == "bibr.example.test" for r in server.requests)


def test_the_key_goes_to_the_bibr_service_only(
    router: respx.MockRouter, server: FakeBibr, tmp_path: Path
) -> None:
    check(tmp_path)
    for call in router.calls:
        sent = KEY in "".join(str(v) for v in call.request.headers.values())
        assert sent == (call.request.url.host == "bibr.example.test")
    assert not any(c.request.url.host.startswith("grobid") for c in router.calls)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key(server: FakeBibr, tmp_path: Path, status: int) -> None:
    server.submit = httpx.Response(status, json={"error": "no"})
    with pytest.raises(run.UserError) as info:
        check(tmp_path)
    assert str(info.value) == "The bibr service did not accept this key."


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_service_that_is_down(server: FakeBibr, tmp_path: Path, status: int) -> None:
    server.submit = httpx.Response(status)
    with pytest.raises(run.UserError) as info:
        check(tmp_path)
    assert str(info.value) == "The bibr service is not available right now. Use GROBID for now."


def test_a_service_that_cannot_be_reached(server: FakeBibr, tmp_path: Path) -> None:
    server.submit = httpx.ConnectError("down")  # type: ignore[assignment]
    with pytest.raises(run.UserError, match="not available right now"):
        check(tmp_path)


def test_a_format_this_version_cannot_read(router: respx.MockRouter, tmp_path: Path) -> None:
    FakeBibr(router, orjson.dumps({"schema_version": "99.0", "text": "x"}))
    with pytest.raises(run.UserError) as info:
        check(tmp_path)
    assert str(info.value) == (
        "The bibr service sent a format this version cannot read yet. Use GROBID for now."
    )


def test_there_is_no_fallback_to_grobid(server: FakeBibr, tmp_path: Path) -> None:
    server.submit = httpx.Response(503)
    with respx.mock(assert_all_called=False) as other:
        other.route().mock(side_effect=AssertionError("GROBID must not be asked"))
        with pytest.raises(run.UserError):
            check(tmp_path)


def test_a_job_that_fails_is_a_pdf_problem(server: FakeBibr, tmp_path: Path) -> None:
    server.status = httpx.Response(200, json={"status": "failed", "stage": "parsing"})
    with pytest.raises(run.UserError, match="could not read this PDF"):
        check(tmp_path)


def test_a_key_is_needed(server: FakeBibr, tmp_path: Path) -> None:
    with pytest.raises(run.UserError) as info:
        check(tmp_path, key="")
    assert str(info.value) == "Enter your bibr key, or use GROBID."
    assert not server.requests


def test_bibr_json_and_xml_files_are_not_sent_anywhere(server: FakeBibr, tmp_path: Path) -> None:
    run.check_paper(pc.demofile("json"), workdir=tmp_path, pdf="bibr", bibr_key=KEY)
    assert not server.requests


def test_no_bibr_service_in_the_list(router: respx.MockRouter, tmp_path: Path) -> None:
    router.get(SERVERS_URL).mock(return_value=_list(GROBID))
    with pytest.raises(run.UserError) as info:
        check(tmp_path)
    assert str(info.value) == "The bibr service could not be found. Use GROBID for now."


def test_a_list_that_cannot_be_read(router: respx.MockRouter, tmp_path: Path) -> None:
    router.get(SERVERS_URL).mock(side_effect=httpx.ConnectError("offline"))
    with pytest.raises(run.UserError, match="could not be found"):
        check(tmp_path)


# -- the address ---------------------------------------------------------------------


def test_the_address_comes_from_the_environment_first(
    router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bibr.URL_ENV, f"{OTHER}/")
    assert bibr.bibr_url() == OTHER
    assert not router.calls  # the list is not even read


def test_the_first_bibr_entry_of_the_list_is_used_and_kept(router: respx.MockRouter) -> None:
    listing = router.get(SERVERS_URL).mock(
        return_value=_list(
            GROBID,
            {"id": "b1", "service": "bibr", "url": f"{BIBR}/"},
            {"id": "b2", "service": "bibr", "url": OTHER},
        )
    )
    assert bibr.bibr_url() == BIBR
    assert bibr.bibr_url() == BIBR
    assert listing.call_count == 1  # read once per process


def test_an_unreadable_list_is_asked_again_next_time(router: respx.MockRouter) -> None:
    listing = router.get(SERVERS_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert bibr.bibr_url() is None
    listing.mock(return_value=_list({"id": "b", "service": "bibr", "url": BIBR}))
    assert bibr.bibr_url() == BIBR


def test_an_address_without_https_is_not_used_for_a_key(router: respx.MockRouter) -> None:
    router.get(SERVERS_URL).mock(
        return_value=_list(
            {"id": "b1", "service": "bibr", "url": "http://bibr.example.test"},
            {"id": "b2", "service": "bibr", "url": OTHER},
        )
    )
    assert bibr.bibr_url() == OTHER


# -- the key -------------------------------------------------------------------------


def test_the_saved_key_file_is_private(tmp_path: Path) -> None:
    path = bibr.save_key(KEY)
    assert path == tmp_path / "config" / "app-keys.json"
    assert orjson.loads(path.read_bytes()) == {"bibr": KEY}
    if sys.platform != "win32":  # Windows keeps it in the private user profile folder
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert bibr.load_key() == KEY
    bibr.save_key("another-key-abcdef")  # replaced, still private
    assert bibr.load_key() == "another-key-abcdef"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))
    bibr.forget_key()
    assert bibr.load_key() == "" and not path.exists()
    bibr.forget_key()  # nothing to forget is fine


def test_a_broken_key_file_is_no_key(tmp_path: Path) -> None:
    path = bibr.key_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert bibr.load_key() == ""
    path.write_text('{"bibr": 5}')
    assert bibr.load_key() == ""


def test_the_key_is_never_shown_in_full() -> None:
    assert KEY not in bibr.show_key(KEY)
    assert bibr.show_key(KEY) == "…7890"
    assert bibr.show_key("short") == "…"


def test_the_environment_key_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    bibr.save_key("saved-key-abcdef")
    assert bibr.resolve_key("") == "saved-key-abcdef"
    assert bibr.resolve_key("typed-key-abcdef") == "typed-key-abcdef"
    monkeypatch.setenv(bibr.KEY_ENV, "env-key-abcdef")
    assert bibr.resolve_key("typed-key-abcdef") == "env-key-abcdef"
    assert ui.key_note() == ui.KEY_ENV_NOTE
    assert "env-key" not in ui.key_note()


def test_the_key_is_not_logged(
    server: FakeBibr, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        check(tmp_path)
        server.submit = httpx.Response(401)
        with pytest.raises(run.UserError) as info:
            check(tmp_path)
    assert KEY not in caplog.text and KEY not in str(info.value)


# -- the page ------------------------------------------------------------------------


def _handlers() -> dict[str, Any]:
    blocks = ui.build_app()
    return {fn.name: fn.fn for fn in blocks.fns.values()}


def _on_check(fns: dict[str, Any], path: Path, key: str, remember: bool) -> list[tuple[Any, ...]]:
    request = SimpleNamespace(session_hash="b1")
    steps = fns["on_check"](
        str(path), False, False, "bibr", key, remember, request, lambda *_a, **_k: None
    )
    return list(steps)


def test_the_privacy_text_follows_the_choice() -> None:
    assert ui.privacy_text("grobid") == ui.PRIVACY
    assert "GROBID server at TU Eindhoven" in ui.PRIVACY
    assert ui.privacy_text("bibr") == (
        "PDFs are sent to the scienceverse bibr service to be turned into text. "
        "XML and JSON files are checked on this computer."
    )
    shown = _handlers()["on_reader"]("bibr")
    assert shown[0]["visible"] is True and shown[1] == ui.PRIVACY_BIBR
    assert _handlers()["on_reader"]("grobid")[0]["visible"] is False


def test_the_choices_are_named() -> None:
    assert [label for label, _ in ui.READERS] == [
        "GROBID (public server at TU Eindhoven)",
        "bibr (the scienceverse service; needs a key)",
    ]
    assert ui.READERS[0][1] == "grobid"  # the default


def test_a_remembered_key_is_saved_after_it_worked(server: FakeBibr, tmp_path: Path) -> None:
    fns = _handlers()
    steps = _on_check(fns, _pdf(tmp_path), KEY, remember=True)
    assert steps[-1][1].startswith("Ran ")
    assert bibr.load_key() == KEY
    assert "…7890" in ui.key_note() and KEY not in ui.key_note()
    assert fns["on_forget"]()[1]["visible"] is False
    assert bibr.load_key() == ""


def test_a_key_is_not_saved_unless_asked(server: FakeBibr, tmp_path: Path) -> None:
    _on_check(_handlers(), _pdf(tmp_path), KEY, remember=False)
    assert not bibr.key_path().exists()


def test_a_key_that_fails_is_not_saved(server: FakeBibr, tmp_path: Path) -> None:
    server.submit = httpx.Response(401)
    import gradio as gr

    with pytest.raises(gr.Error, match="did not accept this key"):
        _on_check(_handlers(), _pdf(tmp_path), KEY, remember=True)
    assert not bibr.key_path().exists()


def test_the_key_of_the_settings_is_used_and_never_copied(
    server: FakeBibr, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bibr.KEY_ENV, "env-key-abcdef")
    _on_check(_handlers(), _pdf(tmp_path), "", remember=True)
    assert server.requests[0].headers["authorization"] == "Bearer env-key-abcdef"
    assert not bibr.key_path().exists()
