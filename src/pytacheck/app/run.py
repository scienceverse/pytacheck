"""Check one paper: read it (through GROBID for a PDF), run the checks, write the report.

This is the code behind both buttons and ``metacheck-app --self-test``. It does not
import gradio; problems the person can fix are raised as :class:`UserError`.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pytacheck.app.checks import selected_checks, status_labels

__all__ = [
    "GROBID_SERVERS",
    "Analysis",
    "Row",
    "UserError",
    "check_paper",
    "find_grobid",
    "grobid_servers",
    "live_servers",
    "read_paper",
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

#: checks run one at a time: the button and the warm-up thread share the library's caches
_RUN_LOCK = threading.Lock()

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


def report_filename(name: str) -> str:
    """``<paper name>_report.html`` with only safe characters in the name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "paper"
    return f"{stem[:80]}_report.html"


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


def read_paper(path: str | os.PathLike[str], workdir: Path) -> Any:
    """Read a bibr JSON, GROBID XML or PDF file into a paper."""
    import pytacheck as pc

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in (".pdf", ".xml", ".json"):
        raise UserError(BAD_TYPE)
    if suffix == ".pdf":
        source = _convert_pdf(source, workdir)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            paper = pc.read(str(source))
    except Exception as exc:
        raise UserError(BAD_PAPER) from exc
    if not isinstance(paper, pc.Paper):
        raise UserError(BAD_PAPER)
    if len(pc.search_text(paper, ".*")) == 0:
        raise UserError(NO_TEXT_PDF if suffix == ".pdf" else NO_TEXT)
    return paper


def check_paper(
    path: str | os.PathLike[str],
    *,
    online: bool = False,
    workdir: Path,
    report_dir: Path | None = None,
    progress: Progress | None = None,
) -> Analysis:
    """Run the demo checks on one file and write the report into ``report_dir``.

    ``workdir`` is scratch space for the PDF conversion. ``report_dir`` (default
    ``workdir``) gets the report file.
    """
    import pytacheck as pc
    from pytacheck.module import run_session, use
    from pytacheck.report.report import render_module_outputs, report_module_run

    tick = progress or (lambda _fraction, _text: None)
    start = time.perf_counter()
    source = Path(path)
    tick(0.05, "Reading the paper")
    paper = read_paper(source, workdir)
    tick(0.3, "Running the checks")
    names = selected_checks(online)
    if "ref_accuracy" in names and len(pc.paper_table(paper, "bib_match")) == 0:
        # only a paper file that carries CrossRef matches (the bundled JSON) can be checked
        names.remove("ref_accuracy")
    with _RUN_LOCK, use(allow_local=False), run_session(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        outputs = report_module_run(paper, names)
        tick(0.85, "Writing the report")
        page = render_module_outputs(outputs, paper)
    rows = [_row(name, outputs[name]) for name in names if name in outputs]
    target = Path(report_dir or workdir)
    target.mkdir(parents=True, exist_ok=True)
    report_path = target / report_filename(source.stem)
    report_path.write_text(page, encoding="utf-8")
    tick(1.0, "Done")
    return Analysis(source.stem, rows, page, report_path, time.perf_counter() - start)


def scratch_dir() -> tempfile.TemporaryDirectory[str]:
    """A temporary folder for one run; the caller removes it when the run ends."""
    return tempfile.TemporaryDirectory(prefix="metacheck-app-")
