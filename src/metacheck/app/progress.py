"""What the page shows while a check runs: the steps of the run, the one that is running, how
far it is when that is known, and how long each step took.

The checking code tells the page what it is doing with ``progress(fraction, text)`` (see
:data:`metacheck.app.run.Progress`). :class:`Steps` is such a function: it knows the steps
of one run (:func:`paper_steps`, :func:`package_steps`), moves to the step a text names, and
:meth:`Steps.html` draws them. A step with a known fraction (the checks done, the share of
an announced download) gets a bar that fills; a step that reports nothing (bibr and GROBID
while they extract) gets a moving bar and its own clock, never a made-up percentage.

:class:`Background` runs the check in a thread, so that the page can draw the steps while it
waits. Nothing here imports gradio.
"""

from __future__ import annotations

import html
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "STARTING",
    "Background",
    "Step",
    "Steps",
    "package_steps",
    "paper_steps",
    "stage_of",
    "starting",
]

#: the step a progress text belongs to, by how the text starts
_STAGES: tuple[tuple[str, str], ...] = (
    ("Reading the PDF", "read"),
    ("Reading the paper", "read"),
    ("Extracting with ", "extract"),
    ("Reading the extracted text", "extract"),
    ("Running check ", "checks"),
    ("Building the report", "report"),
    ("Checking the shared data files", "data"),
    ("Waiting for another data check", "data"),
    ("Fetching the repository", "fetch"),
    ("Downloading", "fetch"),
    ("Opening the package", "open"),
    ("Unpacking the archive", "open"),
    ("Done", "done"),
)
#: "Running check 3 of 19: StatCheck"
_CHECK = re.compile(r"Running check (\d+) of (\d+): (.*)", re.DOTALL)
#: what the library prints while it downloads data files: "Downloading files (3/19)"
_COUNT = re.compile(r"\((\d+)/(\d+)\)")
#: the frames of the spinner the library draws in a terminal (braille patterns)
_SPINNER = re.compile("[\u2800-\u28ff]")

#: what a step that reports nothing says about itself, under its moving bar
NO_BAR = {
    "bibr": "bibr says only whether the PDF is waiting or being read, so the bar only shows "
    "that it is still working.",
    "grobid": "GROBID does not say how far it is, so the bar only shows that it is still working.",
}
#: seconds one sweep of a moving bar, and one turn of the running step's circle, take
_SWEEP = 1.6
_TURN = 0.9


#: said from the moment a button is pressed until the run draws its steps
STARTING = "Waiting for the server to start the check."


def starting() -> str:
    """The panel from the moment a button is pressed until the run draws its steps: the browser
    shows it at once, and on a shared server a run may first wait for a free place."""
    return (
        '<div class="mc-run"><div class="mc-run-head"><span class="mc-run-name">Starting</span>'
        f'</div><p class="mc-run-wait">{html.escape(STARTING)}</p>'
        '<span class="mc-bar mc-bar-moving" role="progressbar" aria-label="Starting">'
        "<span></span></span></div>"
    )


def stage_of(text: str) -> str | None:
    """The step a progress text belongs to (``read``, ``extract``, ``checks``, ...), or ``None``."""
    for start, stage in _STAGES:
        if text.startswith(start):
            return stage
    return None


def _clock(seconds: float) -> str:
    """``42 s`` or ``3:07``."""
    whole = max(0, int(seconds))
    return f"{whole} s" if whole < 60 else f"{whole // 60}:{whole % 60:02d}"


def _phase(period: float) -> str:
    """A CSS delay that starts an animation where the clock says it is.

    The page replaces the steps each time they are drawn, which would start every animation
    again; begun at the same point of the clock, it runs on as if it had never stopped.
    """
    return f"animation-delay:-{time.time() % period:.2f}s"


@dataclass
class Step:
    key: str
    title: str
    state: str = "waiting"  # waiting, running, done or failed
    detail: str = ""
    fraction: float | None = None  # how far the step is, when that is known
    note: str = ""  # said under a step that reports nothing, while it runs
    started: float | None = None
    ended: float | None = None


class Steps:
    """The steps of one run, moved on by the progress calls of the checking code.

    Called from the thread that runs the check, drawn from the page's thread: every change and
    every drawing holds the lock.
    """

    def __init__(
        self,
        name: str,
        steps: list[tuple[str, str]],
        notes: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.steps = [Step(key, title, note=(notes or {}).get(key, "")) for key, title in steps]
        self._clock = clock
        self._lock = threading.Lock()
        self.start = clock()
        self.message = ""  # said under the steps when the run fails
        self.current = 0
        self.steps[0].state = "running"
        self.steps[0].started = self.start

    def _index(self, key: str) -> int | None:
        return next((i for i, s in enumerate(self.steps) if s.key == key), None)

    def _go(self, index: int, now: float) -> None:
        """Make step *index* the running one; the steps before it are done."""
        for step in self.steps[self.current : index]:
            step.state, step.fraction = "done", None
            step.started = step.started if step.started is not None else now
            step.ended = now
        if index != self.current or self.steps[index].started is None:
            self.steps[index].started = now
        self.current = index
        self.steps[index].state = "running"

    def __call__(self, fraction: float, text: str) -> None:
        """A progress call: ``(fraction, text)`` as :data:`metacheck.app.run.Progress` says."""
        stage = stage_of(text)
        now = self._clock()
        with self._lock:
            if stage == "done":
                self._done_until(len(self.steps) if self._index("data") is None else -1, now)
                return
            index = self._index(stage) if stage else None
            if index is not None and index >= self.current:
                self._go(index, now)
            step = self.steps[self.current]
            step.detail, step.fraction = _detail(step.key, fraction, text)

    def _done_until(self, stop: int, now: float) -> None:
        """Every step before *stop* (all but the last with ``-1``) is done; the rest waits."""
        last = len(self.steps) + stop if stop < 0 else stop
        if last > self.current:
            self._go(min(last, len(self.steps) - 1), now)
        for step in self.steps[:last]:
            if step.state != "done":
                step.state, step.fraction, step.ended = "done", None, now

    def data(self, text: str) -> None:
        """The data check runs after the fast results: its step runs, with *text* as detail."""
        now = self._clock()
        with self._lock:
            index = self._index("data")
            if index is None:
                return
            if index >= self.current:
                self._go(index, now)
            step = self.steps[index]
            step.detail, step.fraction = _detail("data", 0.0, text)

    def fail(self, message: str) -> None:
        """The run stopped: the running step failed, and *message* says why."""
        now = self._clock()
        with self._lock:
            step = self.steps[self.current]
            step.state, step.fraction, step.ended = "failed", None, now
            self.message = message

    def html(self) -> str:
        """The steps as the page shows them."""
        with self._lock:
            now = self._clock()
            rows = "".join(_row(step, now) for step in self.steps)
            failed = any(step.state == "failed" for step in self.steps)
            head = "Stopped" if failed else "Checking"
            message = (
                f'<p class="mc-run-error">{html.escape(self.message)}</p>' if self.message else ""
            )
            return (
                f'<div class="mc-run{" mc-run-failed" if failed else ""}">'
                f'<div class="mc-run-head"><span class="mc-run-name">{head} '
                f"<strong>{html.escape(self.name)}</strong></span>"
                f'<span class="mc-run-clock">{_clock(now - self.start)}</span></div>'
                f'<ol class="mc-steps" role="list">{rows}</ol>{message}</div>'
            )


def _detail(key: str, fraction: float, text: str) -> tuple[str, float | None]:
    """What to say under step *key* for a progress text, and its fraction if it is known.

    A text that only names the step says nothing more than the step's title, so it adds no
    detail; a text the step does not know is shown as it is.
    """
    if key == "checks":
        found = _CHECK.match(text)
        # the checks are counted from 0 of N, so their fraction is always real
        return (f"{found.group(1)} of {found.group(2)}: {found.group(3)}" if found else ""), (
            fraction if found else None
        )
    if key == "extract":
        if text.startswith("Extracting with bibr: "):
            rest = text.removeprefix("Extracting with bibr: ")
            rest = re.sub(r"\s*\(it does not report how far it is\)", "", rest).strip()
            return rest[:1].upper() + rest[1:], None
        if text.startswith("Reading the extracted text"):
            return "Reading the extracted text", None
    if key == "fetch":
        got = re.fullmatch(r"Downloading \((.+)\)", text)
        if got:  # the share of the size the host announced; 0.0 when it announced none
            return f"{got.group(1)} downloaded", fraction if fraction > 0 else None
    if key == "data":
        # "Checking the shared data files (13 s). Downloading files (3/19)": the step has its own
        # clock, so only what the library said stays, and a count in it fills the bar
        rest = re.sub(r"^Checking the shared data files \(\d+ s\)\.?\s*", "", text)
        rest = _SPINNER.sub("", rest).strip()  # the frames of a terminal spinner say nothing
        count = _COUNT.search(rest)
        total = int(count.group(2)) if count else 0
        return rest, (min(int(count.group(1)), total) / total if count and total else None)
    return ("" if stage_of(text) else text), None


def _row(step: Step, now: float) -> str:
    title = html.escape(step.title)
    took = ""
    if step.started is not None and step.state in ("running", "done", "failed"):
        took = _clock((step.ended if step.ended is not None else now) - step.started)
    turning = f' style="{_phase(_TURN)}"' if step.state == "running" else ""
    parts = [
        f'<span class="mc-step-icon" aria-hidden="true"{turning}></span>'
        f'<span class="mc-step-title">{title}</span>'
        f'<span class="mc-step-time">{took}</span>'
    ]
    if step.state == "running":
        if step.detail:
            parts.append(f'<span class="mc-step-detail">{html.escape(step.detail)}</span>')
        if step.fraction is not None:
            share = max(0.0, min(1.0, step.fraction))
            parts.append(
                f'<span class="mc-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" '
                f'aria-valuenow="{round(share * 100)}" aria-label="{title}">'
                f'<span style="width:{share * 100:.1f}%"></span></span>'
            )
        else:
            parts.append(
                f'<span class="mc-bar mc-bar-moving" role="progressbar" aria-label="{title}">'
                f'<span style="{_phase(_SWEEP)}"></span></span>'
            )
            if step.note:
                parts.append(f'<span class="mc-step-note">{html.escape(step.note)}</span>')
    words = {"waiting": "to do", "running": "running", "done": "done", "failed": "failed"}
    return (
        f'<li class="mc-step mc-step-{step.state}">'
        f'<span class="mc-sr">{words[step.state]}: </span>{"".join(parts)}</li>'
    )


def paper_steps(path: str, reader: str, data: bool, *, data_in_run: bool) -> Steps:
    """The steps of checking the paper at *path*: a PDF is extracted with *reader* first.

    With ``data`` the data check is the last step: part of the run on a shared server
    (``data_in_run``), after the fast results on this computer.
    """
    pdf = path.lower().endswith(".pdf")
    steps = [("read", "Read the PDF" if pdf else "Read the paper")]
    notes: dict[str, str] = {}
    if pdf:
        name = "bibr" if reader == "bibr" else "GROBID"
        steps.append(("extract", f"Extract the text with {name}"))
        notes["extract"] = NO_BAR["bibr" if reader == "bibr" else "grobid"]
    steps += [("checks", "Run the checks"), ("report", "Write the report")]
    if data:
        steps.append(("data", "Check the shared data files"))
        notes["data"] = (
            "It downloads the data files the paper links to."
            if data_in_run
            else "The other results are ready below. This can take a few minutes."
        )
    return Steps(_shown_name(path), steps, notes)


def package_steps(source: str, kind: str) -> Steps:
    """The steps of checking a data package: a link is downloaded first, an archive opened."""
    if kind == "link":
        steps = [("fetch", "Download the repository"), ("open", "Unpack it")]
        name = re.sub(r"^https?://(www\.)?", "", source.strip()).rstrip("/")
    elif kind == "archive":
        steps = [("open", "Open the archive")]
        name = _shown_name(source)
    else:
        steps = [("open", "Read the folder")]
        name = Path(source).name or source
    steps += [("checks", "Run the package checks"), ("report", "Write the report")]
    return Steps(name[:120], steps)


def _shown_name(path: str) -> str:
    """The file's own name (an upload keeps it in its folder)."""
    return Path(path).name[:120] or "file"


class Background:
    """``work()`` in a thread of its own; its result, or the exception it raised."""

    def __init__(self, work: Callable[[], Any]) -> None:
        self._work = work
        self.result: Any = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="app-check", daemon=True)

    def _run(self) -> None:
        try:
            self.result = self._work()
        except BaseException as exc:  # handed to the page's thread, which raises it
            self.error = exc

    def start(self) -> Background:
        self._thread.start()
        return self

    def wait(self, timeout: float) -> bool:
        """Wait for the work to end (at most *timeout* seconds); whether it has ended."""
        self._thread.join(timeout)
        return not self._thread.is_alive()
