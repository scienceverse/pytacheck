"""The steps the page shows while a check runs (:mod:`metacheck.app.progress`)."""

from __future__ import annotations

import re
import threading

import pytest

from metacheck.app import progress as pg
from metacheck.app import run
from metacheck.app.hosted import WAITING_FOR_PLACE


class Clock:
    """A clock the test moves on."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def states(steps: pg.Steps) -> list[str]:
    return [step.state for step in steps.steps]


def pdf_steps(clock: Clock) -> pg.Steps:
    return pg.Steps(
        "my paper.pdf",
        [("read", "Read"), ("extract", "Extract"), ("checks", "Checks"), ("report", "Report")],
        {"extract": "It says nothing."},
        clock,
    )


@pytest.mark.parametrize(
    ("text", "stage"),
    [
        ("Reading the PDF", "read"),
        ("Reading the paper", "read"),
        ("Extracting with GROBID (it does not report how far it is)", "extract"),
        ("Extracting with bibr: waiting in its queue", "extract"),
        ("Reading the extracted text", "extract"),
        ("Running check 3 of 19: StatCheck", "checks"),
        ("Building the report", "report"),
        ("Checking the shared data files (13 s). Downloading files (3/19)", "data"),
        (run.WAITING, "data"),
        ("Fetching the repository", "fetch"),
        ("Downloading (2.1 MB)", "fetch"),
        ("Opening the package", "open"),
        ("Unpacking the archive", "open"),
        ("Done", "done"),
        (WAITING_FOR_PLACE, None),
    ],
)
def test_each_progress_text_names_its_step(text: str, stage: str | None) -> None:
    assert pg.stage_of(text) == stage


def test_the_steps_of_a_paper_name_its_reader_and_the_data_check() -> None:
    local = pg.paper_steps("/up/x/my paper.pdf", "bibr", True, data_in_run=False)
    assert local.name == "my paper.pdf"
    assert [(s.key, s.title) for s in local.steps] == [
        ("read", "Read the PDF"),
        ("extract", "Extract the text with bibr"),
        ("checks", "Run the checks"),
        ("report", "Write the report"),
        ("data", "Check the shared data files"),
    ]
    assert local.steps[1].note == pg.NO_BAR["bibr"]
    assert "ready below" in local.steps[-1].note  # on this computer it runs after the results
    shared = pg.paper_steps("p.pdf", "grobid", True, data_in_run=True)
    assert shared.steps[1].title == "Extract the text with GROBID"
    assert shared.steps[1].note == pg.NO_BAR["grobid"]
    assert "downloads the data files" in shared.steps[-1].note
    text = pg.paper_steps("p.json", "grobid", False, data_in_run=False)
    assert [(s.key, s.title) for s in text.steps][:1] == [("read", "Read the paper")]
    assert [s.key for s in text.steps] == ["read", "checks", "report"]  # nothing to extract


def test_the_steps_of_a_data_package_start_where_it_is() -> None:
    link = pg.package_steps("https://www.github.com/o/repo/", "link")
    assert link.name == "github.com/o/repo"
    assert [s.key for s in link.steps] == ["fetch", "open", "checks", "report"]
    archive = pg.package_steps("/tmp/upload/study.zip", "archive")
    assert archive.name == "study.zip"
    assert [s.title for s in archive.steps] == [
        "Open the archive",
        "Run the package checks",
        "Write the report",
    ]
    folder = pg.package_steps("/home/me/study", "folder")
    assert folder.name == "study" and folder.steps[0].title == "Read the folder"


def test_the_steps_follow_the_progress_calls_and_time_each_step() -> None:
    clock = Clock()
    steps = pdf_steps(clock)
    assert states(steps) == ["running", "waiting", "waiting", "waiting"]
    clock.now += 2
    steps(0.0, "Extracting with GROBID (it does not report how far it is)")
    assert states(steps) == ["done", "running", "waiting", "waiting"]
    page = steps.html()
    # nothing says how far it is: a moving bar, the step's note, and no made-up percentage
    assert "mc-bar-moving" in page and "It says nothing." in page
    assert "aria-valuenow" not in page
    assert steps.steps[1].detail == ""  # the text only named the step
    clock.now += 5
    steps(2 / 19, "Running check 3 of 19: StatCheck")
    assert states(steps) == ["done", "done", "running", "waiting"]
    page = steps.html()
    assert "3 of 19: StatCheck" in page
    assert 'aria-valuenow="11"' in page and "width:10.5%" in page  # 2 of 19 checks finished
    assert "It says nothing." not in page  # a note is said only while its step runs
    times = re.findall(r'<span class="mc-step-time">([^<]*)</span>', page)
    assert times == ["2 s", "5 s", "0 s", ""]  # the step that waits has no time yet
    clock.now += 70
    steps(1.0, "Building the report")
    clock.now += 1
    steps(1.0, "Done")
    assert states(steps) == ["done", "done", "done", "done"]
    page = steps.html()
    assert re.findall(r'<span class="mc-step-time">([^<]*)</span>', page) == [
        "2 s",
        "5 s",
        "1:10",
        "1 s",
    ]
    assert '<span class="mc-run-clock">1:18</span>' in page
    assert "Checking <strong>my paper.pdf</strong>" in page and "mc-bar" not in page


def test_a_step_never_goes_back() -> None:
    clock = Clock()
    steps = pdf_steps(clock)
    steps(0.5, "Running check 2 of 4: Power")
    steps(0.0, "Reading the extracted text")
    assert states(steps) == ["done", "done", "running", "waiting"]


def test_what_each_step_says_under_its_title() -> None:
    clock = Clock()
    steps = pdf_steps(clock)
    steps(0.0, WAITING_FOR_PLACE)  # a text no step knows is said as it is
    assert steps.steps[0].detail == WAITING_FOR_PLACE
    steps(0.0, "Extracting with bibr: running (it does not report how far it is)")
    assert steps.steps[1].detail == "Running" and steps.steps[1].fraction is None
    steps(0.0, "Extracting with bibr: waiting in its queue")
    assert steps.steps[1].detail == "Waiting in its queue"
    link = pg.Steps("repo", [("fetch", "Download"), ("open", "Unpack")], clock=clock)
    link(0.25, "Downloading (2.1 MB)")
    assert (link.steps[0].detail, link.steps[0].fraction) == ("2.1 MB downloaded", 0.25)
    link(0.0, "Downloading (300 kB)")  # the host did not say how big it is
    assert (link.steps[0].detail, link.steps[0].fraction) == ("300 kB downloaded", None)


def test_the_data_check_runs_after_the_results_on_this_computer() -> None:
    clock = Clock()
    steps = pg.Steps(
        "p.json",
        [("read", "Read"), ("checks", "Checks"), ("report", "Report"), ("data", "Data")],
        clock=clock,
    )
    steps(1.0, "Done")  # the fast results are ready; the data check is still to come
    assert states(steps) == ["done", "done", "done", "running"]
    # the step has its own clock, so the seconds go; a count fills the bar; a spinner's
    # frame says nothing
    steps.data("Checking the shared data files (13 s). ⠋ Downloading files (3/12)")
    data = steps.steps[-1]
    assert (data.detail, data.fraction) == ("Downloading files (3/12)", 0.25)
    steps.data("Listing the files")
    assert (data.detail, data.fraction) == ("Listing the files", None)
    page = steps.html()
    assert "Listing the files" in page and "mc-bar-moving" in page
    without = pg.Steps("p.json", [("read", "Read"), ("checks", "Checks")], clock=clock)
    without.data("Downloading files (1/2)")  # a run without the data check has no such step
    assert states(without) == ["running", "waiting"]


def test_a_failed_run_says_where_it_stopped_and_why() -> None:
    clock = Clock()
    steps = pdf_steps(clock)
    steps(0.0, "Extracting with GROBID (it does not report how far it is)")
    clock.now += 3
    steps.fail("The PDF server did not answer. <Try again>")
    assert states(steps) == ["done", "failed", "waiting", "waiting"]
    page = steps.html()
    assert 'class="mc-run mc-run-failed"' in page and "Stopped <strong>" in page
    assert '<p class="mc-run-error">The PDF server did not answer. &lt;Try again&gt;</p>' in page
    assert '<span class="mc-sr">failed: </span>' in page
    assert "mc-bar" not in page  # nothing runs any more


def test_the_page_escapes_names_and_texts() -> None:
    clock = Clock()
    steps = pg.Steps("<img src=x onerror=alert(1)>.pdf", [("read", "Read")], clock=clock)
    steps(0.0, "<script>alert(1)</script>")
    page = steps.html()
    assert "<img" not in page and "<script>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;.pdf" in page


def test_animations_carry_on_where_they_were_when_the_steps_are_drawn_again() -> None:
    steps = pdf_steps(Clock())
    page = steps.html()
    delays = [float(d) for d in re.findall(r"animation-delay:-([0-9.]+)s", page)]
    assert len(delays) == 2  # the running step's circle and its moving bar
    assert 0 <= delays[0] < pg._TURN and 0 <= delays[1] < pg._SWEEP


def test_an_animation_delay_stays_below_its_period(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1000 * 1.6 + 1.597: the remainder rounded to two decimals would be 1.60
    monkeypatch.setattr(pg.time, "time", lambda: 1601.597)
    assert pg._phase(pg._SWEEP) == "animation-delay:-1.59s"


def test_the_panel_says_the_check_is_starting_before_it_has_steps() -> None:
    page = pg.starting()
    assert pg.STARTING in page and "mc-bar-moving" in page and "mc-run-clock" not in page


def test_the_work_runs_in_the_background() -> None:
    gate = threading.Event()
    job = pg.Background(lambda: gate.wait(5) and 42).start()
    assert job.wait(0.05) is False
    gate.set()
    assert job.wait(5) is True and job.result == 42 and job.error is None
    failed = pg.Background(lambda: 1 / 0).start()
    assert failed.wait(5) and isinstance(failed.error, ZeroDivisionError)
