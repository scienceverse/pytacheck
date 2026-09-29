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
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pytacheck.app import bibr
from pytacheck.app.checks import DATA, selected_checks, status_labels

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
GROBID_ENV = "PYTACHECK_GROBID_URL"
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

Progress = Callable[[float, str], None]


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


def report_filename(name: str) -> str:
    """``<paper name>_report.html`` with only safe characters in the name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "paper"
    return f"{stem[:80]}_report.html"


def _script_hash() -> str:
    """The CSP source for the report's one inline script (the template's, not the paper's)."""
    from pytacheck.report.render import _asset

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
    override = os.environ.get(GROBID_ENV, "").strip()
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
    import pytacheck as pc

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
) -> Any:
    """Read a bibr JSON, GROBID XML or PDF file into a paper.

    A PDF is turned into text by GROBID, or by bibr with ``pdf="bibr"`` and a key.
    """
    import pytacheck as pc

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in (".pdf", ".xml", ".json"):
        raise UserError(BAD_TYPE)
    from_bibr = suffix == ".pdf" and pdf == "bibr"
    if from_bibr:
        source = bibr.convert_pdf(source, workdir, bibr_key)
    elif suffix == ".pdf":
        source = _convert_pdf(source, workdir)
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
    return paper


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
    if os.environ.get("PYTACHECK_CACHE_DIR"):
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
    """The data check, running in a thread of its own so that the page can show its progress."""

    def __init__(self, paper: Any) -> None:
        self.paper = paper
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
        from pytacheck import http
        from pytacheck.config import verbose
        from pytacheck.module import run_session, use
        from pytacheck.report.report import report_module_run
        from pytacheck.utils import local_options

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


class Run:
    """One paper after its fast checks. ``analysis`` is what the page shows first."""

    def __init__(
        self, source: Path, paper: Any, outputs: Any, names: list[str], target: Path, start: float
    ) -> None:
        self.source = source
        self.paper = paper
        self.outputs = outputs
        self.names = names
        self.target = target
        self.start = start
        self.analysis: Analysis

    def data_job(self) -> DataJob:
        return DataJob(self.paper)

    def finish(self, job: DataJob) -> Analysis:
        """The analysis with the data check's result (or its error) added."""
        from pytacheck.module import SECTION_LEVELS
        from pytacheck.report.report import ReportOutput

        rows = [_row(name, self.outputs[name]) for name in self.names if name in self.outputs]
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
        from pytacheck.report.report import render_module_outputs

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            page = render_module_outputs(outputs, self.paper)
        self.target.mkdir(parents=True, exist_ok=True)
        report_path = self.target / report_filename(self.source.stem)
        report_path.write_text(protect(page), encoding="utf-8")
        seconds = time.perf_counter() - self.start
        return Analysis(self.source.stem, rows, page, report_path, seconds, pending)


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
) -> Run:
    """Read the paper and run the fast checks. With ``data``, the analysis says the data check is due.

    ``workdir`` is scratch space for the PDF conversion. ``report_dir`` (default
    ``workdir``) gets the report file.
    """
    import pytacheck as pc
    from pytacheck.module import run_session, use
    from pytacheck.report.report import report_module_run

    tick = progress or (lambda _fraction, _text: None)
    start = time.perf_counter()
    source = Path(path)
    tick(0.05, "Reading the paper")
    paper = read_paper(source, workdir, pdf=pdf, bibr_key=bibr_key)
    tick(0.3, "Running the checks")
    names = selected_checks(online)
    if "ref_accuracy" in names and len(pc.paper_table(paper, "bib_match")) == 0:
        # only a paper file that carries CrossRef matches (the bundled JSON) can be checked
        names.remove("ref_accuracy")
    with _RUN_LOCK, use(allow_local=False), run_session(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        outputs = report_module_run(paper, names)
    tick(0.85, "Writing the report")
    run = Run(source, paper, outputs, names, Path(report_dir or workdir), start)
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
) -> Analysis:
    """Run the demo checks on one file and write the report into ``report_dir``.

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
