"""The data check in the app: its own box, run after the fast checks, with progress and stop.

The check itself is replaced by a fake: nothing here reaches the network.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import metacheck as pc
from metacheck import http
from metacheck.app import run, ui
from metacheck.app.launch import main
from metacheck.config import verbose
from metacheck.module import ModuleOutput
from metacheck.report import report as report_module
from metacheck.report.report import ReportOutput
from metacheck.utils import get_option, message


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
        monkeypatch.setattr(run, "_online", lambda: True)


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
    assert (
        analysis.report_path.read_text(encoding="utf-8").strip()
        == run.protect(analysis.html).strip()
    )
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


def _handlers() -> dict[str, Any]:
    blocks = ui.build_app()
    return {fn.name: fn.fn for fn in blocks.fns.values()}


#: what a button's function sends, in order (the local page has no box among them)
PARTS = ("panel", "results", "summary", "table", "checklist", "frame", "download", "status", "stop")


def _named(sent: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(PARTS, sent, strict=True))


def _skipped(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"__type__"}


def _demo(fns: dict[str, Any], session: str, data: bool = True) -> dict[str, Any]:
    """Press the demo button: what it sent last (the click ends with the fast results)."""
    request = SimpleNamespace(session_hash=session)
    sent = list(fns["on_demo"](False, data, "grobid", "", False, None, "", "", True, request))
    return _named(sent[-1])


def test_the_click_ends_with_the_fast_results(fake: Fake) -> None:
    release = threading.Event()

    def body() -> ModuleOutput:
        message("Downloading https://osf.io/pngda as zip (25 files)...")
        release.wait(10)
        return _output()

    fake.body = body
    fns = _handlers()
    first = _demo(fns, "s1")
    assert first["results"]["visible"] is True and first["stop"]["visible"] is True
    assert "Ran 19 checks" in first["summary"] and len(first["table"].data) == 19
    assert first["status"] == ""
    assert "Data Check" not in first["frame"]
    # the steps stay on the page: the data check is the one still running
    assert "mc-step-running" in first["panel"] and "Check the shared data files" in first["panel"]
    assert "The other results are ready below" in first["panel"]
    steps = fns["follow"](SimpleNamespace(session_hash="s1"))
    later = _named(next(steps))  # its step is drawn while it runs
    assert "Downloading https://osf.io/pngda as zip (25 files)..." in later["panel"]
    assert all(_skipped(later[part]) for part in PARTS[1:-1])  # the results stay as they are
    release.set()
    final = _named(list(steps)[-1])
    table = final["table"]
    assert table.data.iloc[-1, 0] == "Data Check" and table.data.iloc[-1, 2].startswith("Green")
    assert len(table.data) == 20 and "Ran 20 checks" in final["summary"]
    assert "The data check found 3 files." in final["frame"]
    assert final["panel"] == "" and final["status"] == "" and final["stop"]["visible"] is False


def test_the_stop_button_keeps_the_fast_results(fake: Fake) -> None:
    def body() -> ModuleOutput:
        while True:
            http.check_interrupt()
            time.sleep(0.01)

    fake.body = body
    fns = _handlers()
    first = _demo(fns, "s2")
    assert first["stop"]["visible"] is True
    steps = fns["follow"](SimpleNamespace(session_hash="s2"))
    assert "mc-step-running" in _named(next(steps))["panel"]
    panel, status, stop = fns["on_stop"](SimpleNamespace(session_hash="s2"))
    assert panel == "" and status == ui.STOPPED and stop["visible"] is False
    # the last word on the steps is the data check's: they go, and nothing else changes
    (last,) = [_named(sent) for sent in steps]
    assert last["panel"] == "" and last["stop"]["visible"] is False
    assert all(_skipped(last[part]) for part in PARTS[1:-1])


def test_a_failed_data_check_says_so_and_keeps_the_page(fake: Fake) -> None:
    def boom() -> ModuleOutput:
        raise OSError("no route")

    fake.body = boom
    fns = _handlers()
    _demo(fns, "s3")
    final = _named(list(fns["follow"](SimpleNamespace(session_hash="s3")))[-1])
    assert final["table"].data.iloc[-1, 2] == "Failed: no route"
    assert final["status"] == ui.DATA_FAILED and final["panel"] == ""


def test_the_same_button_works_again_and_a_new_check_ends_the_old_one(fake: Fake) -> None:
    started: list[threading.Event] = []

    def body() -> ModuleOutput:
        me = threading.Event()
        started.append(me)
        while not me.is_set():
            http.check_interrupt()
            time.sleep(0.01)
        return _output()

    fake.body = body
    fns = _handlers()
    _demo(fns, "s4")
    old = fns["follow"](SimpleNamespace(session_hash="s4"))
    assert "mc-step-running" in _named(next(old))["panel"]
    begun = time.monotonic()
    again = _demo(fns, "s4", data=False)  # the click returned before, so it can be pressed again
    assert time.monotonic() - begun < 30
    assert again["panel"] == "" and "Ran 19 checks" in again["summary"]
    # the old check is over and draws nothing more: the new run's steps are not taken away
    assert list(old) == []
    assert _skipped(next(fns["follow"](SimpleNamespace(session_hash="s4")))[0])


def test_status_text_from_outside_is_shown_as_text() -> None:
    from metacheck.app.progress import paper_steps

    steps = paper_steps("paper.json", "grobid", True, data_in_run=False)
    steps(1.0, "Done")
    steps.data('Could not get <img src="https:evil.example/p.png">.csv [x](y)')
    drawn = steps.html()
    assert "<img" not in drawn
    assert "&lt;img src=&quot;https:evil.example/p.png&quot;&gt;.csv [x](y)" in drawn


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


# -- what the job does to the library ----------------------------------------------------


def _job(tmp_path: Path) -> run.DataJob:
    return run.begin(pc.demofile("json"), data=True, workdir=tmp_path).data_job()


def test_progress_reaches_the_job_although_messages_are_off(fake: Fake, tmp_path: Path) -> None:
    seen: list[str] = []

    def body() -> ModuleOutput:
        message("Downloaded files are cached in /some/where and reused (repo_cache_clear())")
        message("Downloading https://osf.io/pngda as zip (25 files)...")
        seen.append(job.message)
        return _output()

    fake.body = body
    verbose(False)  # as the running app has it
    try:
        job = _job(tmp_path)
        job.start()
        job.wait()
        assert verbose() is False  # put back
    finally:
        verbose(True)
    assert seen == ["Downloading https://osf.io/pngda as zip (25 files)..."]


def test_stop_ends_a_check_that_prints_nothing(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTACHECK_NO_SLEEP", raising=False)

    def body() -> ModuleOutput:
        while True:
            http.sleep(30)  # a wait between tries, or a slow download

    fake.body = body
    verbose(False)
    try:
        job = _job(tmp_path)
        job.start()
        assert not job.wait(0.3)
        job.stop()
        assert job.wait(5)
    finally:
        verbose(True)
    assert job.stopped and job.output is None and job.error is None


def test_the_fast_checks_do_not_wait_for_the_data_check(fake: Fake, tmp_path: Path) -> None:
    release = threading.Event()

    def body() -> ModuleOutput:
        release.wait(20)
        return _output()

    fake.body = body
    job = _job(tmp_path)
    job.start()
    try:
        assert not job.wait(0.3)
        begun = time.monotonic()
        run.check_paper(pc.demofile("json"), workdir=tmp_path / "other")
        assert time.monotonic() - begun < 15 and not job.done
    finally:
        release.set()
        job.wait(5)


def test_a_second_data_check_waits_its_turn_and_can_be_stopped(fake: Fake, tmp_path: Path) -> None:
    release = threading.Event()
    fake.body = lambda: (release.wait(20), _output())[1]
    first, second = _job(tmp_path), _job(tmp_path)
    first.start()
    second.start()
    try:
        deadline = time.monotonic() + 5
        while second.message != run.WAITING and time.monotonic() < deadline:
            time.sleep(0.05)
        assert second.message == run.WAITING
        second.stop()
        assert second.wait(5) and second.stopped
        assert not first.done
    finally:
        release.set()
        first.wait(5)


def test_all_caches_are_in_the_user_folder_not_the_working_folder(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.archives.cache import _metacheck_cache_root
    from metacheck.archives.info_cache import _repo_info_cache_dir

    monkeypatch.delenv("PYTACHECK_CACHE_DIR", raising=False)
    seen: dict[str, Any] = {}

    def body() -> ModuleOutput:
        seen["root"] = _metacheck_cache_root()
        seen["listings"] = _repo_info_cache_dir()
        return _output()

    fake.body = body
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert Path(seen["root"]) == tmp_path / "cache"
    assert not Path(seen["listings"]).is_relative_to(work)
    assert not Path(seen["listings"]).exists()  # listings are not kept
    assert list(work.iterdir()) == []
    assert get_option("metacheck.cache.dir") is None
    assert get_option("metacheck.repo_info_cache.dir") is None


def test_no_internet_is_a_failed_row(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "_online", lambda: False)
    analysis = run.check_paper(pc.demofile("json"), data=True, workdir=tmp_path)
    assert not fake.calls
    assert analysis.rows[-1].result == f"Failed: {run.OFFLINE}"
    assert len(analysis.rows) > 5


def test_a_warning_is_not_a_progress_text(fake: Fake, tmp_path: Path) -> None:
    seen: list[str] = []

    def body() -> ModuleOutput:
        message("Downloading a file")
        sys.stderr.write("/x/y.py:3: DeprecationWarning: old\n  warnings.warn(x)\n")
        seen.append(job.message)
        return _output()

    fake.body = body
    job = _job(tmp_path)
    job.start()
    job.wait()
    assert seen == ["Downloading a file"]


class _Halt(BaseException):
    pass


def _halt() -> None:
    raise _Halt


def test_a_request_or_a_download_ends_when_the_check_says_so() -> None:
    import respx

    from metacheck.archives.download import _storage_request

    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://files.example.test/a.csv").respond(200, content=b"a,b\n1,2\n")
        assert http.request("GET", "https://files.example.test/a.csv") is not None
        with http.interruptible(_halt):
            with pytest.raises(_Halt):
                http.request("GET", "https://files.example.test/a.csv")
            with pytest.raises(_Halt):
                _storage_request("GET", "https://files.example.test/a.csv")
        assert route.call_count == 1
    http.check_interrupt()  # outside the block nothing happens
