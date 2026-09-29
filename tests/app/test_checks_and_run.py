"""Which checks run, what the run function returns, the PDF path and the server order."""

from __future__ import annotations

import html
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gradio as gr
import httpx
import pytest
import respx
from gradio.utils import get_upload_folder

import metacheck as pc
from metacheck.app import checks, run, ui
from metacheck.module import module_find
from metacheck.status import status_table

VALIDATED = {"power", "stat_p_exact", "stat_p_nonsig", "marginal", "stat_effect_size"}


def test_validated_checks_come_from_the_status_table() -> None:
    assert set(checks.validated_checks()) == VALIDATED
    table = status_table()
    labelled = {
        r.removeprefix("metacheck::") for r in table["module"][table["label"] == "validated"]
    }
    assert labelled == set(checks.validated_checks())


def test_the_sets_are_disjoint_real_modules() -> None:
    sets = [
        set(checks.FAST_OFFLINE),
        set(checks.ONLINE),
        set(checks.DATA),
        set(checks.NEVER),
        set(checks.NOT_SHOWN),
        VALIDATED,
    ]
    for i, a in enumerate(sets):
        for b in sets[i + 1 :]:
            assert not a & b
    for name in (
        *checks.FAST_OFFLINE,
        *checks.ONLINE,
        *checks.DATA,
        *checks.NEVER,
        *checks.NOT_SHOWN,
    ):
        assert module_find(name) is not None
    assert all(checks.FAST_OFFLINE.values())
    assert (
        all(checks.DATA.values()) and all(checks.NEVER.values()) and all(checks.NOT_SHOWN.values())
    )
    assert set(checks.DATA) == {"data_check"}
    assert checks.status_labels()["data_check"] == "experimental"
    for slow in ("code_check", "codebook_check", "psychds_check", "reg_check"):
        assert slow in checks.NEVER
    # the data check has its own box: the fast selection never holds it
    assert "data_check" not in checks.selected_checks(online=True)


def test_selection_orders_the_summary_last() -> None:
    plain = checks.selected_checks()
    online = checks.selected_checks(online=True)
    assert plain[-1] == online[-1] == "ref_summary"
    assert set(checks.ONLINE) <= set(online)
    assert not set(checks.ONLINE) & set(plain)
    assert set(online) - set(plain) == set(checks.ONLINE)


def test_demo_run(tmp_path: Path) -> None:
    steps: list[str] = []
    analysis = run.check_paper(
        pc.demofile("json"),
        workdir=tmp_path,
        report_dir=tmp_path / "out",
        progress=lambda _f, text: steps.append(text),
    )
    assert steps[:3] == ["Reading the paper", "Running the checks", "Writing the report"]
    by_module = {r.module: r for r in analysis.rows}
    assert set(by_module) == set(checks.selected_checks())
    for name in VALIDATED:
        assert by_module[name].status == "Validated"
    assert by_module["stat_check"].status == "Experimental"
    assert by_module["marginal"].result.startswith("Red: ")
    assert all(not r.result.startswith("Failed") for r in analysis.rows)
    assert "<html" in analysis.html.lower()
    assert analysis.report_path == tmp_path / "out" / "to_err_is_human_report.html"
    saved = analysis.report_path.read_text(encoding="utf-8")
    assert saved.strip() == run.protect(analysis.html).strip()  # the report, under its policy
    assert analysis.seconds < 10


def test_report_matches_the_library_report(tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("json"), workdir=tmp_path)
    paper = pc.read(str(pc.demofile("json")))
    out = pc.report(paper, modules=checks.selected_checks(), output_file=tmp_path / "lib.html")
    assert analysis.html.strip() == Path(out.save_path).read_text(encoding="utf-8").strip()


def test_xml_input(tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("xml"), workdir=tmp_path)
    assert analysis.rows
    # GROBID output has no CrossRef matches, so ref_accuracy has nothing to check
    assert "ref_accuracy" not in {r.module for r in analysis.rows}
    assert all(not r.result.startswith("Failed") for r in analysis.rows)


def test_a_file_without_text_is_not_a_paper(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"a": 1}')
    with pytest.raises(run.UserError, match="No text was found in this file"):
        run.check_paper(empty, workdir=tmp_path)
    page = tmp_path / "page.xml"
    page.write_text("<html><body><p>hello</p></body></html>")
    with pytest.raises(run.UserError, match="No text was found in this file"):
        run.check_paper(page, workdir=tmp_path)


def test_no_text_in_a_pdf_is_worded_for_pdfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"a": 1}')
    monkeypatch.setattr(run, "_convert_pdf", lambda _path, _work: empty)
    with pytest.raises(run.UserError, match="scanned image"):
        run.check_paper(_fake_pdf(tmp_path), workdir=tmp_path)


def test_markup_in_a_paper_cannot_load_anything_in_the_report(tmp_path: Path) -> None:
    text = pc.demofile("json").read_text(encoding="utf-8")
    needle = "This paper demonstrates"
    assert needle in text
    evil = tmp_path / "evil.json"
    evil.write_text(
        text.replace(needle, "<script>fetch('http://x.example')</script>", 1), encoding="utf-8"
    )
    analysis = run.check_paper(evil, workdir=tmp_path)
    assert "<script>fetch('http://x.example')</script>" in analysis.html  # unescaped, as in R
    assert "<script>fetch('http://x.example')</script>" in analysis.html
    csp = run.report_csp()
    # only the report's own script may run: the paper's tag is not in the policy
    assert "script-src 'sha256-" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert "default-src 'none'" in csp and "connect-src" not in csp and "img-src data:" in csp
    saved = analysis.report_path.read_text(encoding="utf-8")
    assert f'content="{csp}"' in saved.split("</head>")[0]  # before any paper text
    frame = ui.report_frame(analysis.html)
    assert html.escape(f'content="{csp}"') in frame


def test_the_policy_allows_exactly_the_reports_own_script(tmp_path: Path) -> None:
    import base64
    import hashlib
    import re

    page = run.check_paper(pc.demofile("json"), workdir=tmp_path).html
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}';" in run.report_csp()
    assert not re.search(r"<\w+[^>]*\son[a-z]+\s*=", page)  # no event handler of its own


def test_report_filename() -> None:
    assert run.report_filename("My paper (v2)") == "My_paper_v2_report.html"
    assert run.report_filename("../../etc") == "etc_report.html"
    assert run.report_filename("???") == "paper_report.html"


def test_unsupported_and_unreadable_files(tmp_path: Path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(run.UserError, match="not supported"):
        run.check_paper(txt, workdir=tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(run.UserError, match="could not be read as a paper"):
        run.check_paper(bad, workdir=tmp_path)


def _fake_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "Some Paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really")
    return pdf


def test_pdf_goes_through_convert_with_a_temporary_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_convert(path: Any, **kwargs: Any) -> str:
        calls.append({"path": Path(path), **kwargs})
        return str(pc.demofile("json"))

    monkeypatch.setattr(pc, "convert", fake_convert)
    monkeypatch.setattr(run, "live_servers", lambda: iter(["https://grobid.example"]))
    pdf = _fake_pdf(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    analysis = run.check_paper(pdf, workdir=work)
    (call,) = calls
    assert call["path"] == pdf
    assert call["save_path"] == work
    assert call["method"] == "grobid"
    assert call["api_url"] == "https://grobid.example"
    assert analysis.name == "Some Paper"
    assert analysis.rows


def test_pdf_conversion_errors_are_plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "live_servers", lambda: iter(["https://grobid.example"]))

    def down(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("boom")

    monkeypatch.setattr(pc, "convert", down)
    with pytest.raises(run.UserError, match="PDF server did not answer"):
        run.check_paper(_fake_pdf(tmp_path), workdir=tmp_path)

    def busy(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("Service Unavailable")

    monkeypatch.setattr(pc, "convert", busy)  # a busy server is not a broken PDF
    with pytest.raises(run.UserError, match="PDF server did not answer"):
        run.check_paper(_fake_pdf(tmp_path), workdir=tmp_path)

    def broken(*_a: Any, **_k: Any) -> Any:
        raise ValueError("bad pdf")

    monkeypatch.setattr(pc, "convert", broken)
    with pytest.raises(run.UserError, match="could not read this PDF"):
        run.check_paper(_fake_pdf(tmp_path), workdir=tmp_path)


def test_server_order_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(run.GROBID_ENV, raising=False)
    assert run.grobid_servers() == [
        "https://grobid.hti.ieis.tue.nl",
        "https://grobidOrg-grobid.hf.space",
        "https://grobidOrg-grobid-crf.hf.space",
    ]
    monkeypatch.setenv(run.GROBID_ENV, "http://a.example/, http://b.example")
    assert run.grobid_servers() == ["http://a.example", "http://b.example"]


@respx.mock
def test_first_live_server_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(run.GROBID_ENV, raising=False)
    first = respx.get("https://grobid.hti.ieis.tue.nl/api/isalive").mock(
        side_effect=httpx.ConnectTimeout("slow")
    )
    second = respx.get("https://grobidOrg-grobid.hf.space/api/isalive").mock(
        return_value=httpx.Response(503)
    )
    third = respx.get("https://grobidOrg-grobid-crf.hf.space/api/isalive").mock(
        return_value=httpx.Response(200, text="true")
    )
    assert run.find_grobid() == "https://grobidOrg-grobid-crf.hf.space"
    assert first.called and second.called and third.called


@respx.mock
def test_no_server_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(run.GROBID_ENV, raising=False)
    respx.get(url__regex=r".*/api/isalive").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(run.UserError) as info:
        run.find_grobid()
    assert str(info.value) == (
        "The PDF server did not answer. Try again in a minute, or use the demo paper."
    )


def test_a_busy_server_hands_over_to_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tried: list[str] = []

    def convert(_path: Any, **kwargs: Any) -> str:
        tried.append(kwargs["api_url"])
        if len(tried) < 3:
            raise RuntimeError(("Too Many Requests", "Bad Gateway")[len(tried) - 1])
        return str(pc.demofile("json"))

    monkeypatch.setattr(pc, "convert", convert)
    monkeypatch.setattr(run, "live_servers", lambda: iter(["http://a", "http://b", "http://c"]))
    assert run.check_paper(_fake_pdf(tmp_path), workdir=tmp_path).rows
    assert tried == ["http://a", "http://b", "http://c"]


def _button_function(name: str) -> Any:
    blocks = ui.build_app()
    fns = {fn.name: fn.fn for fn in blocks.fns.values()}
    return fns[name]


def test_buttons_return_the_page_parts(tmp_path: Path) -> None:
    request = SimpleNamespace(session_hash="abc123")
    steps: list[str] = []
    progress = lambda _f, desc=None: steps.append(desc)  # noqa: E731
    on_demo = _button_function("on_demo")
    page = on_demo(False, False, "grobid", "", False, request, progress)
    results, summary, table, frame, download, status, stop = page
    assert results["visible"] is True
    assert status == "" and stop["visible"] is False
    assert "Ran 16 checks" in summary
    assert [row[1] for row in table].count("Validated") == 5
    assert frame.startswith('<iframe title="Report" sandbox="allow-scripts')
    assert "srcdoc=" in frame
    assert 'href="/report/abc123/to_err_is_human_report.html"' in download
    assert "Download the report" in download
    assert steps[0] == "Reading the paper"
    on_demo(False, False, "grobid", "", False, request, progress)  # a second run works


def test_check_button_needs_a_file_and_words_errors_plainly() -> None:
    request = SimpleNamespace(session_hash="abc123")
    on_check = _button_function("on_check")
    with pytest.raises(gr.Error, match="Upload a paper first") as info:
        on_check(None, False, False, "grobid", "", False, request, lambda *_a, **_k: None)
    assert info.value.print_exception is False
    folder = Path(get_upload_folder()) / "test-app-uploads"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        txt = folder / "a.txt"
        txt.write_text("x")
        with pytest.raises(gr.Error, match="not supported") as info:
            on_check(str(txt), False, False, "grobid", "", False, request, lambda *_a, **_k: None)
        assert info.value.print_exception is False
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.network
def test_real_grobid_converts_the_demo_pdf(tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("pdf"), workdir=tmp_path)
    assert analysis.rows
    assert all(not r.result.startswith("Failed") for r in analysis.rows)
    assert not list(Path.cwd().glob("to_err_is_human*.xml"))
