"""Which checks run, what the run function returns, the PDF path and the server order."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gradio as gr
import httpx
import pytest
import respx

import pytacheck as pc
from pytacheck.app import checks, run, ui
from pytacheck.module import module_find
from pytacheck.status import status_table

VALIDATED = {"power", "stat_p_exact", "stat_p_nonsig", "marginal", "stat_effect_size"}


def test_validated_checks_come_from_the_status_table() -> None:
    assert set(checks.validated_checks()) == VALIDATED
    table = status_table()
    labelled = {
        r.removeprefix("metacheck::") for r in table["module"][table["label"] == "validated"]
    }
    assert labelled == set(checks.validated_checks())


def test_the_sets_are_disjoint_real_modules() -> None:
    sets = [set(checks.FAST_OFFLINE), set(checks.ONLINE), set(checks.NEVER), VALIDATED]
    for i, a in enumerate(sets):
        for b in sets[i + 1 :]:
            assert not a & b
    for name in (*checks.FAST_OFFLINE, *checks.ONLINE, *checks.NEVER):
        assert module_find(name) is not None
    assert all(checks.FAST_OFFLINE.values()) and all(checks.NEVER.values())
    for slow in ("code_check", "data_check", "codebook_check", "psychds_check", "reg_check"):
        assert slow in checks.NEVER


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
    assert analysis.report_path.read_text(encoding="utf-8").strip() == analysis.html.strip()
    assert analysis.seconds < 10


def test_report_matches_the_library_report(tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("json"), workdir=tmp_path)
    paper = pc.read(str(pc.demofile("json")))
    out = pc.report(paper, modules=checks.selected_checks(), output_file=tmp_path / "lib.html")
    assert analysis.html.strip() == Path(out.save_path).read_text(encoding="utf-8").strip()


def test_xml_input(tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("xml"), workdir=tmp_path)
    assert analysis.rows


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
    monkeypatch.setattr(run, "find_grobid", lambda: "https://grobid.example")
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
    monkeypatch.setattr(run, "find_grobid", lambda: "https://grobid.example")

    def down(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("boom")

    monkeypatch.setattr(pc, "convert", down)
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


def _button_function(name: str) -> Any:
    blocks = ui.build_app()
    fns = {fn.name: fn.fn for fn in blocks.fns.values()}
    return fns[name]


def test_buttons_return_the_page_parts(tmp_path: Path) -> None:
    request = SimpleNamespace(session_hash="abc123")
    steps: list[str] = []
    progress = lambda _f, desc=None: steps.append(desc)  # noqa: E731
    on_demo = _button_function("on_demo")
    results, summary, table, frame, download = on_demo(False, request, progress)
    assert results["visible"] is True
    assert "Ran 16 checks" in summary
    assert [row[1] for row in table].count("Validated") == 5
    assert frame.startswith('<iframe title="Report" sandbox="allow-scripts')
    assert "srcdoc=" in frame
    path = Path(download.value["path"])  # Gradio's own copy, which delete_cache cleans
    assert path.name == "to_err_is_human_report.html" and path.is_file()
    assert steps[0] == "Reading the paper"
    on_demo(False, request, progress)  # a second run of the same session works


def test_check_button_needs_a_file_and_words_errors_plainly(tmp_path: Path) -> None:
    request = SimpleNamespace(session_hash="abc123")
    on_check = _button_function("on_check")
    with pytest.raises(gr.Error, match="Upload a paper first"):
        on_check(None, False, request, lambda *_a, **_k: None)
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(gr.Error, match="not supported"):
        on_check(str(txt), False, request, lambda *_a, **_k: None)


@pytest.mark.network
def test_real_grobid_converts_the_demo_pdf(tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("pdf"), workdir=tmp_path)
    assert analysis.rows
    assert not list(Path.cwd().glob("to_err_is_human*.xml"))
