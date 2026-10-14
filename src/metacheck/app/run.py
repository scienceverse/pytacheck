"""Check one paper: read it (through GROBID for a PDF), run the checks, write the report.

This is the code behind both buttons and ``metacheck-app --self-test``. It does not
import gradio; problems the person can fix are raised as :class:`UserError`.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from metacheck._env import env_get, env_names
from metacheck.app import bibr
from metacheck.app.checks import DATA, chosen_checks, selected_checks, status_labels, title_of

__all__ = [
    "GROBID_SERVERS",
    "Analysis",
    "DataJob",
    "Row",
    "Run",
    "UserError",
    "begin",
    "check_paper",
    "find_grobid",
    "grobid_servers",
    "live_servers",
    "protect",
    "read_paper",
    "report_csp",
    "report_filename",
]

#: Tried in this order. ``PYTACHECK_GROBID_URL`` (comma-separated) replaces the list.
GROBID_SERVERS: tuple[str, ...] = (
    "https://grobid.hti.ieis.tue.nl",
    "https://grobidOrg-grobid.hf.space",
    "https://grobidOrg-grobid-crf.hf.space",
)
#: the variables of the GROBID list, in lookup order; ``GROBID_ENV`` is the old name, as before
GROBID_ENVS = env_names("GROBID_URL")
GROBID_ENV = GROBID_ENVS[-1]
#: seconds to wait for a server's liveness answer
LIVENESS_TIMEOUT = 6.0

NO_SERVER = "The PDF server did not answer. Try again in a minute, or use the demo paper."
BAD_TYPE = "This file type is not supported. Upload a PDF, a GROBID XML file or a bibr JSON file."
BAD_PAPER = (
    "This file could not be read as a paper. Check that it is a PDF, GROBID XML or bibr JSON."
)
BAD_PDF = "The PDF server could not read this PDF. Try another copy of the paper."
NO_TEXT = "No text was found in this file, so there is nothing to check."
NO_TEXT_PDF = "No text was found in this PDF. Is it a scanned image?"
NO_CHECKS = 'No check is ticked. Tick at least one check under "Choose the checks".'
R_FAILED = "R metacheck could not run the checks: {}"
#: what the data check says while R runs it (R does not say how far it is)
R_DATA = "Checking the shared data files with R metacheck (it does not say how far it is)"

#: status texts of a busy server (the reason phrases of 429, 502, 503 and 504): try the next
BUSY = ("Too Many Requests", "Bad Gateway", "Service Unavailable", "Gateway Timeout")

#: traffic light -> the word shown in the table
LIGHTS = {
    "red": "Red",
    "yellow": "Yellow",
    "green": "Green",
    "info": "Info",
    "na": "Not applicable",
    "fail": "Failed",
}

#: the fast checks run one at a time: the button and the warm-up thread share the library's caches
_RUN_LOCK = threading.Lock()
#: one data check at a time: it sets library options for its whole run. The fast checks never
#: wait for it.
_DATA_LOCK = threading.Lock()

#: ``progress(fraction, text)`` tells the page what is happening. The text names the stage:
#: reading the PDF, extracting it with bibr or GROBID, "Running check 3 of 19: ...", building
#: the report. The fraction is how far that stage is, and only when it is known: the checks
#: finished out of the checks to run, or the share of an announced download. While a stage
#: reports nothing (bibr and GROBID say nothing while they extract) it is 0.0 and the page
#: shows the text and its own running clock, not a made-up percentage.
Progress = Callable[[float, str], None]


def checks_progress(tick: Progress) -> Callable[[int, int, str], None]:
    """The function that ``metacheck.core.scope.module_progress`` calls as the checks run:
    "Running check 3 of 19: <title>" with the fraction 2/19 (the checks finished: counted, not
    estimated), then "Building the report" when the last one has finished."""

    def step(done: int, total: int, label: str) -> None:
        if done >= total:
            tick(1.0, "Building the report")
        else:
            tick(done / total, f"Running check {done + 1} of {total}: {title_of(label)}")

    return step


class UserError(Exception):
    """A problem with the file or the PDF server, worded for the person using the app."""


@dataclass(frozen=True)
class Row:
    module: str
    check: str
    status: str  # "Validated" or "Experimental"
    result: str  # the traffic light as a word, then the summary text

    def cells(self) -> list[str]:
        return [self.check, self.status, self.result]


@dataclass(frozen=True)
class Analysis:
    name: str  # the paper's file name without its extension
    rows: list[Row]
    html: str  # the report page
    report_path: Path
    seconds: float
    data_pending: bool = False  # the data check has not finished
    engine: str = ""  # what ran the checks, when not pytacheck: "R metacheck 0.3.3.1"


def report_filename(name: str) -> str:
    """``<paper name>_report.html`` with only safe characters in the name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "paper"
    return f"{stem[:80]}_report.html"


def _script_hash() -> str:
    """The CSP source for the report's one inline script (the template's, not the paper's)."""
    from metacheck.report.render import _asset

    digest = hashlib.sha256(f"\n{_asset('report.js')}\n".encode()).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


def report_csp() -> str:
    """The report needs no outside resource. Only its own script may run, so a script or an
    event handler that came in with a paper or a data file does not."""
    return (
        f"default-src 'none'; script-src {_script_hash()}; style-src 'unsafe-inline'; "
        "img-src data:; font-src data:"
    )


def protect(page: str) -> str:
    """The report page with its Content-Security-Policy, at the top of its head."""
    meta = f'<meta http-equiv="Content-Security-Policy" content="{report_csp()}">'
    head = re.search(r"<head[^>]*>", page, re.IGNORECASE)
    if head is None:
        return meta + page
    return page[: head.end()] + meta + page[head.end() :]


def _plain(text: Any) -> str:
    """A summary text on one line, without Markdown marks."""
    if text is None:
        return ""
    line = re.sub(r"\s*\n\s*-?\s*", " ", str(text)).strip()
    return re.sub(r"[*`]", "", line).strip()


def _row(module: str, output: Any) -> Row:
    label = status_labels().get(module, "experimental")
    light = LIGHTS.get(str(output.traffic_light), str(output.traffic_light).capitalize())
    summary = _plain(output.summary_text)
    if output.traffic_light == "fail":
        summary = _plain(output.report)[:300] or summary
    return Row(
        module=module,
        check=str(output.title or module),
        status="Validated" if label == "validated" else "Experimental",
        result=f"{light}: {summary}" if summary else light,
    )


def grobid_servers() -> list[str]:
    override = (env_get("GROBID_URL") or "").strip()
    if override:
        return [u.strip().rstrip("/") for u in override.split(",") if u.strip()]
    return list(GROBID_SERVERS)


def live_servers(servers: Sequence[str] | None = None) -> Iterator[str]:
    """The GROBID servers that say they are alive, in order (a short timeout for each)."""
    import httpx

    for url in servers if servers is not None else grobid_servers():
        try:
            resp = httpx.get(f"{url}/api/isalive", timeout=LIVENESS_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            yield url


def find_grobid(servers: Sequence[str] | None = None) -> str:
    """The first GROBID server that says it is alive."""
    for url in live_servers(servers):
        return url
    raise UserError(NO_SERVER)


def _convert_pdf(path: Path, workdir: Path) -> Path:
    import metacheck as pc

    for server in live_servers():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out = pc.convert(path, save_path=workdir, method="grobid", api_url=server)
        except (ConnectionError, OSError):
            continue
        except RuntimeError as exc:
            if str(exc) in BUSY:
                continue
            raise UserError(BAD_PDF) from exc
        except Exception as exc:
            raise UserError(BAD_PDF) from exc
        if not out:
            raise UserError(BAD_PDF)
        return Path(str(out[0] if isinstance(out, list | tuple) else out))
    raise UserError(NO_SERVER)


def read_paper(
    path: str | os.PathLike[str],
    workdir: Path,
    *,
    pdf: str = "grobid",
    bibr_key: str = "",
    progress: Progress | None = None,
) -> Any:
    """Read a bibr JSON, GROBID XML or PDF file into a paper.

    A PDF is turned into text by GROBID, or by bibr with ``pdf="bibr"`` and a key.
    ``progress`` is told which stage this is (see :data:`Progress`).
    """
    return _read(path, workdir, pdf=pdf, bibr_key=bibr_key, progress=progress)[0]


def _read(
    path: str | os.PathLike[str],
    workdir: Path,
    *,
    pdf: str = "grobid",
    bibr_key: str = "",
    progress: Progress | None = None,
) -> tuple[Any, Path]:
    """:func:`read_paper`, and the file the paper was read from (a PDF's extracted text, in
    *workdir*): R metacheck reads that same file."""
    import metacheck as pc

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in (".pdf", ".xml", ".json"):
        raise UserError(BAD_TYPE)
    tick = progress or (lambda _fraction, _text: None)
    from_bibr = suffix == ".pdf" and pdf == "bibr"
    tick(0.0, "Reading the PDF" if suffix == ".pdf" else "Reading the paper")
    if from_bibr:
        source = bibr.convert_pdf(source, workdir, bibr_key, progress)
    elif suffix == ".pdf":
        tick(0.0, "Extracting with GROBID (it does not report how far it is)")
        source = _convert_pdf(source, workdir)
    if suffix == ".pdf":
        tick(0.0, "Reading the extracted text")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            paper = pc.read(str(source))
    except Exception as exc:
        raise UserError(bibr.BAD_FORMAT if from_bibr else BAD_PAPER) from exc
    if not isinstance(paper, pc.Paper):
        raise UserError(bibr.BAD_FORMAT if from_bibr else BAD_PAPER)
    if len(pc.search_text(paper, ".*")) == 0:
        # an answer without text may be a format this version does not know
        raise UserError(
            bibr.BAD_FORMAT if from_bibr else NO_TEXT_PDF if suffix == ".pdf" else NO_TEXT
        )
    return paper, source


class Stopped(BaseException):
    """Raised inside the data check when the person stops it (not an ``Exception``, so that no
    ``except Exception`` in the library swallows it)."""


#: the data check whose thread (or a pool thread it started) is running this code
_JOB: contextvars.ContextVar[DataJob | None] = contextvars.ContextVar("app_data_job", default=None)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: a warning as Python prints it: ``file:line: XWarning: text`` and the source line
_WARNING = re.compile(r"^.*:\d+: \w*Warning: ", re.MULTILINE)
#: what the library prints that says nothing to the person
_NOISE = ("are cached in", "repo_cache_clear")

WAITING = "Waiting for another data check to finish."
OFFLINE = "The data files could not be reached. Check the internet connection and try again."
#: a place that answers if the computer is online; any answer will do
PROBES = ("https://api.osf.io/v2/", "https://github.com")


class _Stderr:
    """``sys.stderr`` while a data check runs: what it prints goes to the job, not the console."""

    def __init__(self, real: Any, quiet: bool) -> None:
        self.real = real
        self.quiet = quiet  # drop what other threads print: the app runs with messages off

    def write(self, text: str) -> int:
        job = _JOB.get()
        if job is None:
            return len(text) if self.quiet else int(self.real.write(text))
        if not _WARNING.search(text):
            job.note(text)
        return len(text)

    def isatty(self) -> bool:
        # progress bars only draw on a terminal; the job reads what they draw
        return _JOB.get() is not None or bool(self.real.isatty())

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


def _latest(text: str) -> str:
    """The last line the library printed, as a status text (a progress bar becomes ``name (3/19)``)."""
    line = _ANSI.sub("", text).replace("\r", "\n").strip().splitlines()
    last = line[-1].strip() if line else ""
    bar = re.match(r"^(.*?)\s*\[[^\]]*\]\s*(\d+/\d+)?", last)
    if bar and "[" in last:
        last = f"{bar.group(1)} ({bar.group(2)})" if bar.group(2) else bar.group(1)
    last = re.sub(r"^\(?[-\\|/]\)?\s+", "", last)[:160].strip()
    if last.startswith(":") or any(mark in last for mark in _NOISE):
        return ""  # a bar with no name, or a note about the cache folder
    return last


def cache_root() -> Path | None:
    """The folder for all the library's caches (``None``: as the person set it up)."""
    if env_get("CACHE_DIR"):
        return None
    import platformdirs

    return Path(platformdirs.user_cache_dir("pytacheck"))


def data_cache_dir() -> Path | None:
    """Where downloaded data files are kept between runs (``None``: the library's own place)."""
    root = cache_root()
    return root / "repo-files" if root else None


def _online() -> bool:
    """Whether the computer reaches the internet (an answer of any kind counts)."""
    import httpx

    for url in PROBES:
        try:
            httpx.head(url, timeout=5.0, follow_redirects=True)
        except httpx.HTTPError:
            continue
        return True
    return False


class DataJob:
    """The data check, running in a thread of its own so that the page can show its progress.

    With ``r_source`` (the paper's file, in a folder of its own that the job removes when it
    ends) R metacheck runs the check.
    """

    def __init__(self, paper: Any, r_source: Path | None = None) -> None:
        self.paper = paper
        self.r_source = r_source
        self.message = ""  # the library's latest progress text
        self.output: Any = None  # the module's output
        self.error: Exception | None = None
        self.stopped = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="data-check", daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the check to end (at most ``timeout`` seconds); whether it has ended."""
        self._thread.join(timeout)
        return self.done

    def stop(self) -> None:
        """Ask the check to stop. It stops before its next request, download chunk or wait."""
        self._stop.set()

    def interrupt(self) -> None:
        if self._stop.is_set():
            raise Stopped

    def note(self, text: str) -> None:
        self.interrupt()
        latest = _latest(text)
        if latest:
            self.message = latest

    def _run(self) -> None:
        token = _JOB.set(self)
        try:
            self._take_turn()
            try:
                self._check()
            finally:
                _DATA_LOCK.release()
        except Stopped:
            self.stopped = True
        except Exception as exc:
            self.error = exc
        finally:
            _JOB.reset(token)
            if self.r_source is not None:
                shutil.rmtree(self.r_source.parent, ignore_errors=True)

    def _take_turn(self) -> None:
        if not _DATA_LOCK.acquire(blocking=False):
            self.message = WAITING
            while not _DATA_LOCK.acquire(timeout=0.25):
                self.interrupt()
        try:
            self.interrupt()
        except Stopped:
            _DATA_LOCK.release()
            raise
        self.message = ""

    def _check(self) -> None:
        if self.r_source is not None:
            self._check_r(self.r_source)
            return
        from metacheck import http
        from metacheck.config import verbose
        from metacheck.module import run_session, use
        from metacheck.report.report import report_module_run
        from metacheck.utils import local_options

        if not _online():
            raise OSError(OFFLINE)
        root, folder = cache_root(), data_cache_dir()
        options = {"metacheck.cache.dir": str(root)} if root else {}
        if folder:
            options["metacheck.repo_cache.dir"] = str(folder)
        real, was = sys.stderr, verbose()
        with contextlib.ExitStack() as stack:
            # listings are fetched fresh every run: only the downloaded files are kept
            listings = stack.enter_context(tempfile.TemporaryDirectory(prefix="metacheck-app-"))
            options["metacheck.repo_info_cache.dir"] = listings
            stack.enter_context(local_options(options))
            stack.enter_context(use(allow_local=False))
            stack.enter_context(run_session())
            stack.enter_context(http.interruptible(self.interrupt))
            # the library prints its progress to stderr only when messages are on; the job
            # reads them there and the console does not see them
            verbose(True)
            sys.stderr = _Stderr(real, quiet=not was)
            try:
                outputs = report_module_run(
                    self.paper, list(DATA), args={"data_check": {"cache": True}}
                )
            finally:
                sys.stderr = real
                verbose(was)
        self.output = outputs.get("data_check")

    def _check_r(self, source: Path) -> None:
        from metacheck import rengine

        if not _online():
            raise OSError(OFFLINE)
        self.message = R_DATA
        try:
            r_run = rengine.run(
                source, ["data_check"], args={"data_check": {"cache": True}}, stop=self._stop.is_set
            )
        except rengine.REngineStopped as exc:
            raise Stopped from exc
        except rengine.REngineError as exc:
            raise RuntimeError(R_FAILED.format(exc)) from exc
        self.output = r_run.outputs.get("data_check")


class Run:
    """One paper after its fast checks. ``analysis`` is what the page shows first."""

    def __init__(
        self,
        source: Path,
        paper: Any,
        outputs: Any,
        names: list[str],
        target: Path,
        start: float,
        *,
        version: str | None = None,
        r_source: Path | None = None,
    ) -> None:
        self.source = source
        self.paper = paper
        self.outputs = outputs
        self.names = names
        self.target = target
        self.start = start
        #: the versions the report names, when R metacheck ran the checks
        self.version = version
        #: R's copy of the paper for the data check (the run's own folder is gone by then)
        self.r_source = r_source
        self.analysis: Analysis

    def data_job(self) -> DataJob:
        return DataJob(self.paper, r_source=self.r_source)

    def finish(self, job: DataJob) -> Analysis:
        """The analysis with the data check's result (or its error) added."""
        from metacheck.module import SECTION_LEVELS
        from metacheck.presets import label
        from metacheck.report.report import ReportOutput

        rows = [
            _row(label(name), self.outputs[label(name)])
            for name in self.names
            if label(name) in self.outputs
        ]
        outputs = self.outputs
        if job.output is not None:
            merged = {**self.outputs, "data_check": job.output}
            ordered = sorted(
                merged.items(),
                key=lambda kv: (
                    SECTION_LEVELS.index(kv[1].section)
                    if kv[1].section in SECTION_LEVELS
                    else len(SECTION_LEVELS)
                ),
            )
            outputs = ReportOutput(ordered, paper=self.paper)
            rows.append(_row("data_check", job.output))
        else:
            reason = _plain(job.error)[:300] if job.error else "The data check did not finish."
            rows.append(Row("data_check", DATA_TITLE, "Experimental", f"Failed: {reason}"))
        return self._write(rows, outputs, pending=False)

    def _write(self, rows: list[Row], outputs: Any, pending: bool) -> Analysis:
        from metacheck.report.report import render_module_outputs

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            page = render_module_outputs(outputs, self.paper, version=self.version)
        self.target.mkdir(parents=True, exist_ok=True)
        report_path = self.target / report_filename(self.source.stem)
        report_path.write_text(protect(page), encoding="utf-8")
        seconds = time.perf_counter() - self.start
        engine = f"R metacheck {self.version.split(' ', 1)[0]}" if self.version else ""
        return Analysis(self.source.stem, rows, page, report_path, seconds, pending, engine)


DATA_TITLE = "Data check"


def begin(
    path: str | os.PathLike[str],
    *,
    online: bool = False,
    data: bool = False,
    workdir: Path,
    report_dir: Path | None = None,
    progress: Progress | None = None,
    pdf: str = "grobid",
    bibr_key: str = "",
    checks: Sequence[str] | None = None,
    args: dict[str, Any] | None = None,
    hosted: bool = False,
    engine: str = "python",
) -> Run:
    """Read the paper and run the fast checks. With ``data``, the analysis says the data check is due.

    ``workdir`` is scratch space for the PDF conversion. ``report_dir`` (default
    ``workdir``) gets the report file. ``checks`` are the checks the person ticked (only
    those this server offers run, see :func:`~metacheck.app.checks.chosen_checks`); without
    them the app's own checks run, with the online ones if ``online``. ``args`` are a
    preset's arguments, by check name. With ``engine="r"`` R metacheck runs the checks (only
    those it has), and later the data check too.
    """
    import metacheck as pc
    from metacheck.core.scope import module_progress
    from metacheck.module import run_session, use
    from metacheck.presets import label
    from metacheck.report.report import report_module_run

    tick = progress or (lambda _fraction, _text: None)
    start = time.perf_counter()
    source = Path(path)
    paper, read_from = _read(source, workdir, pdf=pdf, bibr_key=bibr_key, progress=tick)
    if checks is None:
        names = selected_checks(online)
    else:
        from metacheck.app.checks import pack_checks

        names = chosen_checks(checks, hosted, () if hosted else pack_checks())
        if not names and not data:
            raise UserError(NO_CHECKS)
    if "ref_accuracy" in names and len(pc.paper_table(paper, "bib_match")) == 0:
        # only a paper file that carries CrossRef matches (the bundled JSON) can be checked
        names.remove("ref_accuracy")
    if engine == "r":
        target = Path(report_dir or workdir)
        return _begin_r(source, read_from, paper, names, data, args, target, start, tick)
    with (
        _RUN_LOCK,
        use(allow_local=False),
        run_session(),
        module_progress(checks_progress(tick)),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        outputs = report_module_run(paper, names, args=args or {})  # ends: "Building the report"
    run = Run(source, paper, outputs, names, Path(report_dir or workdir), start)
    rows = [_row(label(name), outputs[label(name)]) for name in names if label(name) in outputs]
    run.analysis = run._write(rows, outputs, pending=data)
    tick(1.0, "Done")
    return run


def _begin_r(
    source: Path,
    read_from: Path,
    paper: Any,
    names: list[str],
    data: bool,
    args: dict[str, Any] | None,
    target: Path,
    start: float,
    tick: Progress,
) -> Run:
    """:func:`begin`'s checks, run by R metacheck on the file the paper was read from."""
    from metacheck import rengine
    from metacheck.report.report import ReportOutput

    try:
        found = rengine.info()
    except rengine.REngineError as exc:
        raise UserError(R_FAILED.format(exc)) from exc
    names = [n for n in names if n in found.modules]  # R has no packs and no Python-only checks
    if not names and not data:
        raise UserError(NO_CHECKS)
    version = rengine.report_version(found.metacheck_version, found.r_version)
    outputs = ReportOutput(paper=paper)
    if names:
        tick(0.0, "Running the checks with R metacheck")
        try:
            r_run = rengine.run(read_from, names, args=args or {})
        except rengine.REngineError as exc:
            raise UserError(R_FAILED.format(exc)) from exc
        outputs = r_run.outputs
        outputs.paper = paper
        version = rengine.report_version(r_run.metacheck_version, r_run.r_version)
    r_source = None
    if data:  # the data check runs after this run's folder is removed: R gets its own copy
        folder = Path(tempfile.mkdtemp(prefix="metacheck-app-r-"))
        r_source = Path(shutil.copy2(read_from, folder / read_from.name))
    tick(1.0, "Building the report")
    run = Run(source, paper, outputs, names, target, start, version=version, r_source=r_source)
    rows = [_row(name, outputs[name]) for name in names if name in outputs]
    run.analysis = run._write(rows, outputs, pending=data)
    tick(1.0, "Done")
    return run


def check_paper(
    path: str | os.PathLike[str],
    *,
    online: bool = False,
    data: bool = False,
    workdir: Path,
    report_dir: Path | None = None,
    progress: Progress | None = None,
    pdf: str = "grobid",
    bibr_key: str = "",
    checks: Sequence[str] | None = None,
    args: dict[str, Any] | None = None,
) -> Analysis:
    """Run the demo checks (or the ticked ``checks``) on one file and write the report into
    ``report_dir``.

    With ``data`` the data check runs too and this waits for it: it downloads the paper's
    data files. The self-test leaves it out.
    """
    run = begin(
        path,
        online=online,
        data=data,
        workdir=workdir,
        report_dir=report_dir,
        progress=progress,
        pdf=pdf,
        bibr_key=bibr_key,
        checks=checks,
        args=args,
    )
    if not data:
        return run.analysis
    job = run.data_job()
    job.start()
    job.wait()
    return run.finish(job)


def scratch_dir() -> tempfile.TemporaryDirectory[str]:
    """A temporary folder for one run; the caller removes it when the run ends."""
    return tempfile.TemporaryDirectory(prefix="metacheck-app-")
