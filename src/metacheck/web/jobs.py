"""The job queue of the web interface.

A job is made when a request is accepted: it waits in one queue, and at most
``max_running`` run at the same time, each in a process of its own (``app.hosted.JobRunner``,
which also ends a job at its time limit). At most ``max_waiting`` jobs wait; a request
beyond that is refused (:class:`QueueFull`, HTTP 503). A finished job's result is kept for
``keep`` seconds and then deleted with its folder.

A job lives in memory (its status) and in a folder of its own (its upload and its result).
A visitor's own API keys live in memory only, until the job starts: they are handed to its
process and never written to the folder. Nothing here imports FastAPI.
"""

from __future__ import annotations

import collections
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metacheck._logging import get_logger
from metacheck.app.hosted import JobRunner
from metacheck.web.config import SiteConfig
from metacheck.web.worker import STOPPED, JobFailed, run_job

__all__ = ["Job", "JobManager", "QueueFull"]

LOG = get_logger("web")
#: how often a finished job is looked at for deletion, in seconds
SWEEP_EVERY = 5.0


class QueueFull(Exception):
    """No new job is taken now (the queue is full or the disk is short); the message says so."""


@dataclass
class Job:
    id: str
    kind: str
    spec: dict[str, Any]
    folder: Path
    run_code: bool = False
    #: the visitor's own API keys, by service. In memory only, until the job starts.
    keys: dict[str, str] = field(default_factory=dict, repr=False)
    status: str = "queued"
    step: str = "waiting in the queue"
    progress: float | None = None
    error: str | None = None
    converter: str | None = None
    report_type: str | None = None
    created: float = 0.0
    started: float | None = None
    finished: float | None = None
    result: str | None = None
    result_type: str | None = None

    @property
    def is_over(self) -> bool:
        return self.status in ("done", "failed")


class JobManager:
    """The queue, the places that run jobs and the clean-up of finished ones."""

    def __init__(
        self,
        config: SiteConfig,
        job: Callable[..., Any] = run_job,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._job = job
        self._clock = clock
        if config.jobs_dir is None:
            self.root = Path(tempfile.mkdtemp(prefix="pytacheck-site-"))
            self._owns_root = True
        else:
            self.root = Path(config.jobs_dir)
            self.root.mkdir(parents=True, exist_ok=True)
            self._owns_root = False
        self.root.chmod(0o700)
        self._runner = JobRunner(config.timeout, places=config.max_running)
        self._jobs: dict[str, Job] = {}
        self._queue: collections.deque[str] = collections.deque()
        self._cond = threading.Condition()
        self._threads: list[threading.Thread] = []
        self._stopping = False

    # -- taking jobs ---------------------------------------------------------------------

    def check_capacity(self) -> None:
        """Raise :class:`QueueFull` when a new job would be refused, before its upload is read."""
        with self._cond:
            if len(self._queue) >= self.config.max_waiting:
                raise QueueFull("The queue is full; try again in a few minutes.")
        if shutil.disk_usage(self.root).free < self.config.min_free_bytes:
            raise QueueFull("The server is short of disk space; try again later.")

    def new_folder(self) -> tuple[str, Path]:
        """A random id and a private folder for a job that is about to be made."""
        job_id = str(uuid.uuid4())
        folder = self.root / job_id
        folder.mkdir(mode=0o700)
        return job_id, folder

    def submit(self, job: Job) -> None:
        """Put *job* in the queue (it keeps its folder). Raises :class:`QueueFull`."""
        with self._cond:
            if len(self._queue) >= self.config.max_waiting:
                raise QueueFull("The queue is full; try again in a few minutes.")
            job.created = self._clock()
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._start_threads()
            # all of them: the sweeper waits on this condition too, and must not take the call
            self._cond.notify_all()

    def discard_folder(self, folder: Path) -> None:
        """Delete the folder of a job that was never submitted."""
        shutil.rmtree(folder, ignore_errors=True)

    # -- asking about jobs ---------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._cond:
            return self._jobs.get(job_id)

    def position(self, job: Job) -> int | None:
        """The place in the queue, from 1, or ``None`` when the job is not waiting."""
        with self._cond:
            try:
                return list(self._queue).index(job.id) + 1
            except ValueError:
                return None

    def counts(self) -> tuple[int, int]:
        """``(running, waiting)``."""
        with self._cond:
            running = sum(1 for j in self._jobs.values() if j.status == "running")
            return running, len(self._queue)

    # -- running -------------------------------------------------------------------------

    def _start_threads(self) -> None:
        if self._threads:
            return
        for number in range(self.config.max_running):
            thread = threading.Thread(target=self._work, name=f"web-job-{number}", daemon=True)
            thread.start()
            self._threads.append(thread)
        sweeper = threading.Thread(target=self._sweep_loop, name="web-sweep", daemon=True)
        sweeper.start()
        self._threads.append(sweeper)

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                job = self._jobs[self._queue.popleft()]
                job.status, job.step, job.started = "running", "starting", self._clock()
                keys, job.keys = job.keys, {}  # handed to the process, then forgotten here
            self._run(job, keys)

    def _progress(self, job: Job, fraction: float, text: str) -> None:
        with self._cond:
            job.progress = round(fraction, 2) if fraction >= 0 else None
            job.step = text

    def _run(self, job: Job, keys: dict[str, str]) -> None:
        minutes = int(self.config.timeout // 60)
        error: str | None = None
        outcome: dict[str, Any] = {}
        try:
            outcome = self._runner.run(
                self._job,
                job.spec,
                str(job.folder),
                keys,
                on_timeout=lambda: JobFailed(
                    f"The job took longer than {minutes} minutes and was stopped."
                ),
                on_progress=lambda fraction, text: self._progress(job, fraction, text),
            )
        except JobFailed as exc:
            error = str(exc)
        except Exception as exc:
            LOG.warning("job %s ended with %s", job.id, type(exc).__name__)
            error = STOPPED
        finally:
            for upload in job.folder.glob("input.*"):
                upload.unlink(missing_ok=True)
        with self._cond:
            job.finished = self._clock()
            job.progress = None
            if error is not None:
                job.status, job.step, job.error = "failed", "failed", error
            else:
                job.status, job.step = "done", "done"
                job.result = outcome.get("result")
                job.result_type = outcome.get("type")
                job.converter = outcome.get("converter")
                job.report_type = outcome.get("report_type")

    # -- clean-up ------------------------------------------------------------------------

    def sweep(self) -> int:
        """Delete the jobs that finished more than ``keep`` seconds ago; how many."""
        now = self._clock()
        with self._cond:
            old = [
                j
                for j in self._jobs.values()
                if j.is_over and j.finished is not None and now - j.finished > self.config.keep
            ]
            for job in old:
                del self._jobs[job.id]
        for job in old:
            shutil.rmtree(job.folder, ignore_errors=True)
        return len(old)

    def _sweep_loop(self) -> None:
        while True:
            with self._cond:
                if self._cond.wait_for(lambda: self._stopping, timeout=SWEEP_EVERY):
                    return
            try:
                self.sweep()
            except Exception:  # a failure to delete is tried again at the next sweep
                LOG.warning("clean-up of finished jobs failed", exc_info=True)

    def close(self) -> None:
        """Stop taking jobs and delete the folder this manager made."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._owns_root:
            shutil.rmtree(self.root, ignore_errors=True)
