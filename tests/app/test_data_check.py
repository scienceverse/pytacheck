"""The data check in the app: its own box, run after the fast checks, with progress and stop.

The check itself is replaced by a fake: nothing here reaches the network.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import pytacheck as pc
from pytacheck.app import run, ui
from pytacheck.app.launch import main
from pytacheck.module import ModuleOutput
from pytacheck.report import report as report_module
from pytacheck.report.report import ReportOutput
from pytacheck.utils import get_option, message


def _output(light: str = "green", summary: str = "We classified 3 files.") -> ModuleOutput:
    return ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        report="The data check found 3 files.",
        traffic_light=light,
        summary_text=summary,
    )


class Fake:
    """Stands in for the data check inside ``report_module_run``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cache_dir: Any = None
        self.body: Any = lambda: _output()
        real = report_module_run = report_module.report_module_run

        def fake(paper: Any, modules: Any, args: Any = None) -> Any:
            if list(modules) != ["data_check"]:
                assert "data_check" not in modules
                return real(paper, modules, args)
            self.calls.append(dict(args or {}))
            self.cache_dir = get_option("metacheck.repo_cache.dir")
            out = self.body()
            return ReportOutput({"data_check": out}, paper=paper)

        assert report_module_run is real
        monkeypatch.setattr(report_module, "report_module_run", fake)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    return Fake(monkeypatch)


def test_the_fast_checks_alone_never_start_it(fake: Fake, tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("json"), workdir=tmp_path)
    assert not fake.calls
    assert "data_check" not in {r.module for r in analysis.rows}
    assert analysis.data_pending is False


def test_the_self_test_makes_no_downloads(fake: Fake) -> None:
    assert main(["--self-test"]) == 0
    assert not fake.calls


def test_the_result_is_a_row_and_part_of_the_report(fake: Fake, tmp_path: Path) -> None:
    analysis = run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    (call,) = fake.calls
    assert call == {"data_check": {"cache": True}}
    assert [r.module for r in analysis.rows][-1] == "data_check"
    row = analysis.rows[-1]
    assert row.status == "Experimental"
    assert row.result == "Green: We classified 3 files."
    assert len(analysis.rows) == len(run.selected_checks()) + 1
    assert "The data check found 3 files." in analysis.html
    assert analysis.report_path.read_text(encoding="utf-8").strip() == analysis.html.strip()
    assert analysis.data_pending is False


def test_downloads_are_kept_in_the_user_cache(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTACHECK_CACHE_DIR", raising=False)
    run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert Path(fake.cache_dir) == tmp_path / "cache" / "repo-files"
    assert get_option("metacheck.repo_cache.dir") is None  # the setting is put back
    monkeypatch.setenv("PYTACHECK_CACHE_DIR", str(tmp_path / "mine"))
    run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert fake.cache_dir is None  # the library's own place, as the person set it


def test_a_failing_data_check_keeps_the_other_rows(fake: Fake, tmp_path: Path) -> None:
    fake.body = lambda: ModuleOutput(
        module="data_check",
        title="data_check",
        section="results",
        report="HTTP 500: OSF said no.",
        traffic_light="fail",
        summary_text="This module failed to run",
    )
    analysis = run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert analysis.rows[-1].result == "Failed: HTTP 500: OSF said no."
    others = [r for r in analysis.rows if r.module != "data_check"]
    assert others and all(not r.result.startswith("Failed") for r in others)
    assert "<html" in analysis.html.lower()


def test_an_error_outside_the_check_is_a_row_too(fake: Fake, tmp_path: Path) -> None:
    def boom() -> ModuleOutput:
        raise OSError("disk full")

    fake.body = boom
    analysis = run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert analysis.rows[-1].result == "Failed: disk full"
    assert len(analysis.rows) > 5


def test_progress_texts_reach_the_job(fake: Fake, tmp_path: Path) -> None:
    seen: list[str] = []

    def body() -> ModuleOutput:
        message("Downloading https://osf.io/pngda as zip (25 files)...")
        seen.append(job.message)
        return _output()

    fake.body = body
    started = run.begin(pc.demofile("json"), data=True, workdir=tmp_path)
    assert started.analysis.data_pending is True
    job = started.data_job()
    job.start()
    job.wait()
    assert seen == ["Downloading https://osf.io/pngda as zip (25 files)..."]
    assert job.output is not None and job.error is None and not job.stopped


def test_reading_what_the_library_prints() -> None:
    assert run._latest("\x1b[32mDownloading x (25 files)\x1b[39m\n") == "Downloading x (25 files)"
    assert (
        run._latest("\r- Listing files: 3 folders to check") == "Listing files: 3 folders to check"
    )
    assert run._latest("Downloading files [====     ] 3/19 0:00:02") == "Downloading files (3/19)"
    assert run._latest("Running modules [=====]") == "Running modules"
    assert run._latest("\n") == ""


def test_stop_ends_the_check_at_its_next_message(fake: Fake, tmp_path: Path) -> None:
    def body() -> ModuleOutput:
        while True:
            message("Downloading a file")
            time.sleep(0.01)

    fake.body = body
    job = run.begin(pc.demofile("json"), data=True, workdir=tmp_path).data_job()
    job.start()
    assert not job.wait(0.2)
    job.stop()
    assert job.wait(5)
    assert job.stopped and job.output is None and job.error is None


def test_stderr_is_put_back(fake: Fake, tmp_path: Path) -> None:
    import sys

    before = sys.stderr
    run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert sys.stderr is before


def _fn(name: str) -> Any:
    blocks = ui.build_app()
    return {fn.name: fn.fn for fn in blocks.fns.values()}[name]


def _handlers() -> tuple[Any, Any]:
    blocks = ui.build_app()
    fns = {fn.name: fn.fn for fn in blocks.fns.values()}
    return fns["on_demo"], fns["on_stop"]


def test_the_page_shows_the_fast_results_first(fake: Fake) -> None:
    release = threading.Event()

    def body() -> ModuleOutput:
        message("Downloading https://osf.io/pngda as zip (25 files)...")
        release.wait(10)
        return _output()

    fake.body = body
    on_demo, _stop = _handlers()
    request = SimpleNamespace(session_hash="s1")
    steps = on_demo(False, True, "grobid", "", False, request, lambda *_a, **_k: None)
    results, summary, table, frame, _download, status, stop = next(steps)
    assert results["visible"] is True and stop["visible"] is True
    assert "Ran 16 checks" in summary and len(table) == 16
    assert "The other results are ready" in status and "still running" in status
    assert "Data Check" not in frame
    later = next(steps)  # the status line is kept alive while it runs
    assert "still running" in later[5]
    assert "Downloading https://osf.io/pngda as zip (25 files)..." in later[5]
    release.set()
    final = list(steps)[-1]
    _results, summary, table, frame, _download, status, stop = final
    assert table[-1][0] == "Data Check" and table[-1][2].startswith("Green")
    assert len(table) == 17 and "Ran 17 checks" in summary
    assert "The data check found 3 files." in frame
    assert status == "" and stop["visible"] is False


def test_the_stop_button_keeps_the_fast_results(fake: Fake) -> None:
    def body() -> ModuleOutput:
        while True:
            message("Downloading a file")
            time.sleep(0.01)

    fake.body = body
    on_demo, on_stop = _handlers()
    request = SimpleNamespace(session_hash="s2")
    steps = on_demo(False, True, "grobid", "", False, request, lambda *_a, **_k: None)
    first = next(steps)
    assert first[6]["visible"] is True
    status, stop = on_stop(request)
    assert status == ui.STOPPED and stop["visible"] is False
    rest = list(steps)
    assert rest[-1][5] == ui.STOPPED  # nothing replaces the fast results
    assert all("Data Check" not in str(step[2]) for step in rest)


def test_a_failed_data_check_says_so_and_keeps_the_page(fake: Fake) -> None:
    def boom() -> ModuleOutput:
        raise OSError("no route")

    fake.body = boom
    on_demo, _stop = _handlers()
    request = SimpleNamespace(session_hash="s3")
    steps = list(on_demo(False, True, "grobid", "", False, request, lambda *_a, **_k: None))
    *_, table, _frame, _download, status, _stop_button = steps[-1]
    assert table[-1][2] == "Failed: no route"
    assert status == ui.DATA_FAILED


def test_the_box_is_ticked_and_named(fake: Fake) -> None:
    blocks = ui.build_app()
    boxes = [b for b in blocks.blocks.values() if type(b).__name__ == "Checkbox"]
    (data,) = [b for b in boxes if b.label == ui.DATA_LABEL]
    assert data.value is True
    assert data.label == (
        "Check the shared data files (downloads them from OSF and other repositories; "
        "this can take a few minutes)"
    )
    buttons = [b.value for b in blocks.blocks.values() if type(b).__name__ == "Button"]
    assert "Stop the data check" in buttons
