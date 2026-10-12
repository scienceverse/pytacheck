"""Stand-ins for the process of a hosted run.

A test puts this folder on ``sys.path`` and replaces ``ui.analyse_upload`` with
``functools.partial(run_as, <kind>)``. The run's process then runs the real
``analyse_upload`` with ``begin`` replaced by the stand-in. That works when the process
starts by fork (it inherits the test's objects) and by spawn (the only way on Windows,
where it imports this module).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from metacheck.app import ui
from metacheck.app.run import Analysis, Row

#: the real one, taken before a test replaces it
REAL = ui.analyse_upload
#: a folder each counted run writes its start and end to, one file each: processes that
#: append to one file at the same moment can overwrite each other's line on Windows
LOG_ENV = "METACHECK_TEST_RUN_LOG"


def analysis() -> Analysis:
    return Analysis("x", [], "<p>x</p>", Path("x_report.html"), 0.1)


def started(*_a: Any, **_k: Any) -> Any:
    """What ``begin`` gives back, for a run without the data check."""
    return SimpleNamespace(analysis=analysis())


def sleep(*_a: Any, **_k: Any) -> Any:
    time.sleep(30)


def _note(what: str) -> None:
    (Path(os.environ[LOG_ENV]) / f"{what}-{uuid.uuid4().hex}").write_text(str(time.time()))


def counted(*_a: Any, **_k: Any) -> Any:
    _note("start")
    # longer than a spawned process takes to start, so two runs surely overlap
    time.sleep(2.5)
    _note("end")
    return started()


class Job:
    """A data check that takes three waits of half a second."""

    message = "Downloading data.csv"

    def __init__(self, waits: int = 3) -> None:
        self.waits = 0
        self.limit = waits
        self.output: Any = None
        self.error: Exception | None = None

    def start(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> bool:
        # long enough for Gradio to send the progress text of each wait
        time.sleep(0.5 if timeout is None else min(0.5, timeout))
        self.waits += 1
        return self.waits > self.limit

    def stop(self) -> None:
        self.limit = 0


def _finish(job: Job) -> Analysis:
    result = "2 data files checked" if job.error is None else f"Failed: {job.error}"
    row = Row("data_check", "Data check", "Experimental", result)
    return Analysis("x", [row], "<p>x</p>", Path("x_report.html"), 0.1)


def with_data(*_a: Any, **_k: Any) -> Any:
    return SimpleNamespace(analysis=analysis(), data_job=Job, finish=_finish)


def slow_data(*_a: Any, **_k: Any) -> Any:
    """A data check that would go on for a minute."""
    return SimpleNamespace(analysis=analysis(), data_job=lambda: Job(120), finish=_finish)


BEGINS = {
    "sleep": sleep,
    "counted": counted,
    "started": started,
    "with_data": with_data,
    "slow_data": slow_data,
}


def run_as(kind: str, *args: Any) -> Any:
    """The real hosted run, with ``begin`` replaced by the stand-in ``kind``."""
    ui.begin = BEGINS[kind]  # type: ignore[assignment]
    return REAL(*args)
