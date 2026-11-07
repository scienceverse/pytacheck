"""One job of the web interface, run in a process of its own (see ``jobs.JobManager``).

``run_job`` is what the child process runs: it reads the paper (or unpacks the repository),
runs the checks and writes the result into the job's folder. It tells the parent how far
it is through the ``report(fraction, text)`` function the process runner gives it; a
fraction below 0 means "not known". The visitor's own API keys arrive as an argument and are
set for this process only (``api_keys_use()``): they are not written anywhere.

Nothing here imports FastAPI.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from metacheck.web.config import (
    MAX_UNPACKED_BYTES,
    MAX_ZIP_BYTES,
    MAX_ZIP_FILES,
    REPOSITORY_MODULES,
    RESEARCHBOX_NO_KEY,
)
from metacheck.web.uploads import folder_name

__all__ = [
    "JobFailed",
    "convert_pdf",
    "report_layout",
    "run_job",
    "scrub_paths",
]

Report = Callable[[float, str], None]
#: the checks of a repository that a paper's "run the code" adds, in the order they run
CODE_CHAIN = (*REPOSITORY_MODULES, "reproducibility_check")
#: data_check names the concepts of data columns by rules: the local classifier would download
#: a model of about 840 MB on a server that anyone can send a job to
DATA_ARGS: dict[str, Any] = {"concepts": "rules"}
#: what reproducibility_check is told when a job may run code (always in Docker)
CODE_ARGS: dict[str, Any] = {
    "execute": True,
    "sandbox": "docker",
    "install_missing": True,
    "timeout": 300,
}
NO_PAPER = "The file could not be read as a paper. Send a PDF, bibr JSON or GROBID XML."
NO_TEXT = "No text was found in this file, so there is nothing to check."
STOPPED = (
    "The check stopped unexpectedly (the process that runs it ended, perhaps because the "
    "server ran out of memory). Please report it at "
    "https://github.com/scienceverse/pytacheck/issues, with the file if you can share it."
)


class JobFailed(Exception):
    """The job cannot be finished; the message is for the person who sent it."""


#: the report layout of each ``report_type`` that has one of its own: ``checklist`` is every
#: check sorted by its result, with what to fix first (``metacheck.report.checklist``)
LAYOUTS: dict[str, str] = {"checklist": "checklist"}


def report_layout(requested: str, repository: bool) -> str:
    """The report layout a job gets, from the ``report_type`` field: the checklist report when
    it is asked for (the site always asks), else the full report (a paper) or the brief report
    (a repository), as on the site on SURF Research Cloud."""
    return LAYOUTS.get(requested, "brief" if repository else "full")


def scrub_paths(text: str, *folders: Path) -> str:
    """*text* without the places on this server that a check names: each folder in
    *folders*, as given and with its links followed, becomes nothing, written with this
    system's separator or with ``/`` (as R and the reports write paths on Windows too)."""
    places: set[str] = set()
    for folder in folders:
        for where in (folder, folder.resolve()):
            places |= {str(where), where.as_posix()}
    for place in sorted(places, key=len, reverse=True):
        for sep in dict.fromkeys(("/", os.sep)):
            text = text.replace(place + sep, "")
        text = text.replace(place, "")
    return text


# -- converting a PDF ----------------------------------------------------------------------


def _grobid(pdf: Path, workdir: Path, crossref: bool) -> Path:
    """The PDF as bibr JSON, from the first GROBID server that answers."""
    import metacheck as pc
    from metacheck.app.run import BAD_PDF, BUSY, NO_SERVER, UserError, live_servers

    for server in live_servers():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out = pc.convert(
                    pdf,
                    save_path=workdir,
                    method="grobid",
                    api_url=server,
                    crossref_lookup=crossref,
                )
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


def convert_pdf(pdf: Path, workdir: Path, crossref: bool, report: Report) -> tuple[Path, str]:
    """Turn a PDF into bibr JSON. Returns the file and what converted it, ``bibr`` or ``grobid``.

    bibr is tried first when this server has one (``METACHECK_BIBR_URL``); when it does not
    answer or cannot read the PDF, a GROBID server converts it. ``crossref`` has the
    references completed at Crossref when GROBID converts; bibr's own conversion is not asked
    (pytacheck's bibr client has no such option).
    """
    from metacheck._logging import get_logger
    from metacheck.app import bibr
    from metacheck.app.run import UserError

    try:
        configured = bibr._configured()
    except ValueError:
        configured = None  # a wrong setting: GROBID, as when bibr does not answer
    if configured is not None:
        report(-1.0, "converting the PDF with bibr")
        try:
            key = bibr.resolve_key(None, remembered=False)
            json_path = bibr.convert_pdf(pdf, workdir, key, lambda _f, text: report(-1.0, text))
            return json_path, "bibr"
        except UserError as exc:
            get_logger("web").info("bibr did not convert the PDF (%s), trying GROBID", exc)
    report(-1.0, "converting the PDF with GROBID")
    return _grobid(pdf, workdir, crossref), "grobid"


# -- the paper -----------------------------------------------------------------------------


def _read_paper(source: Path) -> Any:
    import metacheck as pc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            paper = pc.read(str(source))
    except Exception as exc:
        raise JobFailed(NO_PAPER) from exc
    if not isinstance(paper, pc.Paper):
        raise JobFailed(NO_PAPER)
    if len(pc.search_text(paper, ".*")) == 0:
        raise JobFailed(NO_TEXT)
    return paper


def _module_steps(
    report: Report, first: float = 0.1, last: float = 0.9
) -> Callable[[int, int, str], None]:
    """The function ``module_progress`` calls: "running module 3 of 7: stat_check"."""

    def step(done: int, total: int, label: str) -> None:
        if done >= total:
            report(last, "rendering the report")
        else:
            fraction = first + (last - first) * done / total
            report(fraction, f"running module {done + 1} of {total}: {label}")

    return step


@contextlib.contextmanager
def _checking(report: Report) -> Iterator[None]:
    """Run modules as the hosted checks do: no local module files, one run session, and the
    progress told to *report*."""
    from metacheck.core.scope import module_progress
    from metacheck.module import run_session, use

    with (
        use(allow_local=False),
        run_session(),
        module_progress(_module_steps(report)),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        yield


def _paper_job(spec: dict[str, Any], folder: Path, report: Report) -> dict[str, Any]:
    import orjson

    from metacheck.web.psyarxiv import fetch_preprint

    kind = spec["kind"]
    converter: str | None = None
    if spec["input_kind"] == "psyarxiv":
        report(-1.0, "fetching the preprint from PsyArXiv")
        source, _title = fetch_preprint(spec["psyarxiv"], folder, spec["psyarxiv_api"])
    else:
        source = folder / spec["input_file"]
    if source.suffix.lower() == ".pdf":
        pdf = source
        source, converter = convert_pdf(pdf, folder, bool(spec["crossref"]), report)
        pdf.unlink(missing_ok=True)  # the PDF is no longer needed
    if kind == "convert":
        result = folder / "result.json"
        shutil.move(source, result)
        return {"result": result.name, "type": "application/json", "converter": converter}
    report(0.05, "reading the paper")
    paper = _read_paper(source)
    source.unlink(missing_ok=True)

    requested = list(spec["modules"])
    args: dict[str, dict[str, Any]] = {"data_check": dict(DATA_ARGS)}
    if spec["run_code"]:
        requested = [m for m in requested if m not in CODE_CHAIN] + list(CODE_CHAIN)
        args["reproducibility_check"] = {**CODE_ARGS, "local_only": False}
    with _checking(report):
        from metacheck.report.report import report_module_run

        outputs = report_module_run(paper, requested, args=args)
    layout = report_layout(spec["report_type"], repository=False)
    html = ""
    if kind == "report" or spec["report"]:
        html = _render(outputs, paper, layout)
    if kind == "report":
        result = folder / "result.html"
        result.write_text(html, encoding="utf-8")
        return {
            "result": result.name,
            "type": "text/html; charset=utf-8",
            "converter": converter,
            "report_type": layout,
        }
    from metacheck._version import __version__
    from metacheck.api.app import _plain, _report_text, info_fields

    modules = {
        name: {
            "module": o.module,
            "title": o.title,
            "traffic_light": o.traffic_light,
            "summary_text": o.summary_text,
            "table": o.table,
            "summary_table": o.summary_table,
            "section": o.section,
            "report": _report_text(o.report),
        }
        for name, o in outputs.items()
    }
    info = _plain(info_fields(paper, ["title", "doi"]))
    payload = {
        "metacheck_version": __version__,
        "converter": converter,
        "paper_info": info[0] if info else {},
        "modules": modules,
        "report_html": html,
    }
    result = folder / "result.json"
    result.write_bytes(orjson.dumps(_plain(payload)))
    return {
        "result": result.name,
        "type": "application/json",
        "converter": converter,
        "report_type": layout if html else None,
    }


def _render(outputs: Any, paper: Any, layout: str) -> str:
    from metacheck.app.run import protect
    from metacheck.report.report import render_module_outputs

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return protect(render_module_outputs(outputs, paper, report_type=layout))


# -- the repository ------------------------------------------------------------------------

#: a ResearchBox link as the site takes it (``app._repository_link``): the box's number, and
#: the passcode of a box that is not public yet
RBOX_LINK = re.compile(r"https://researchbox\.org/(\d{1,7})(?:&PEER_REVIEW_passcode=[A-Za-z0-9]+)?")


def _rbox_problem(error: str) -> str:
    """What to tell the sender about a box that ResearchBox did not list or give."""
    if error == "unfound":
        return (
            "ResearchBox has no public box with that number. A box that is not public yet is "
            "checked with its peer-review link."
        )
    if error.startswith("API rate limit exhausted"):
        return (
            "ResearchBox's limit on requests is used up. Try again later, or download the box "
            "and send it as a .zip file."
        )
    return (
        f"ResearchBox did not give the box ({error}). Try again later, or download the box and "
        "send it as a .zip file."
    )


@contextlib.contextmanager
def _researchbox(url: str, folder: Path, report: Report) -> Iterator[Any]:
    """The files of the ResearchBox box at *url*, downloaded into the job's folder (a context
    manager, as ``open_link``). The box is listed first, so one with more files or bytes than
    this server takes from a link is not downloaded, and what arrives is counted again."""
    from types import SimpleNamespace

    import pandas as pd

    from metacheck.archives.api_keys import rbox_token
    from metacheck.archives.researchbox import _rbox_id, rbox_file_download, rbox_files
    from metacheck.datapackage._open import _size
    from metacheck.utils import options

    if not rbox_token():
        raise JobFailed(RESEARCHBOX_NO_KEY)
    report(-1.0, "listing the box's files at ResearchBox")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        listed = rbox_files(url)
    errors = listed["error"].dropna() if "error" in listed.columns else pd.Series([], dtype=object)
    if len(errors) > 0:
        raise JobFailed(_rbox_problem(str(errors.iloc[0])))
    if len(listed) == 0:
        raise JobFailed("The box has no files to check.")
    sizes = pd.to_numeric(listed["size"], errors="coerce")
    if sizes.isna().any():
        raise JobFailed(
            "ResearchBox did not say how big the box's files are, so it was not downloaded. "
            "Download the box and send it as a .zip file."
        )
    if len(listed) > MAX_ZIP_FILES or float(sizes.sum()) > MAX_ZIP_BYTES:
        raise JobFailed(
            f"The box holds {len(listed):,} files, {_size(float(sizes.sum()))}: more than the "
            f"{MAX_ZIP_FILES:,} files or {_size(MAX_ZIP_BYTES)} this server takes from a link. "
            "Download it yourself and send it as a .zip file."
        )
    report(-1.0, "downloading the box from ResearchBox")
    box = folder / "box"
    options({"metacheck.repo_cache.session_dir": str(box)})  # the download stays in the job
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            got = rbox_file_download(url, cache=False)
        failed = got.attrs.get("failed")
        if len(got) == 0:
            reasons = [] if failed is None else failed["error"].dropna().tolist()
            raise JobFailed(_rbox_problem(str(reasons[0]) if reasons else "no files"))
        if failed is not None and len(failed) > 0:
            raise JobFailed(
                f"{len(failed)} of the box's files did not arrive intact. Try again later."
            )
        first, name = str(got["file_location"].iloc[0]), str(got["name"].iloc[0])
        base = Path(first.removesuffix("/" + name))
        arrived = [p for p in base.rglob("*") if p.is_file()]
        if (
            len(arrived) > MAX_ZIP_FILES
            or sum(p.stat().st_size for p in arrived) > MAX_UNPACKED_BYTES
        ):
            raise JobFailed(
                "The box ResearchBox sent is bigger than this server checks. Download it "
                "yourself and send it as a .zip file."
            )
        yield SimpleNamespace(
            name=f"researchbox-{_rbox_id(url)}", base=None, remote=True, base_dir=base
        )
    finally:
        shutil.rmtree(box, ignore_errors=True)


def _open_repository(
    spec: dict[str, Any], folder: Path, keys: dict[str, str], report: Report
) -> Any:
    """The package to check: the unpacked zip, or the repository a link names (a context
    manager). The upload is deleted once it is unpacked."""
    from metacheck.app.package import HOSTED
    from metacheck.datapackage import open_link, open_package, parse_link
    from metacheck.datapackage._remote import _normalise

    limits: dict[str, Any] = {
        "max_bytes": MAX_UNPACKED_BYTES,
        "max_files": MAX_ZIP_FILES,
        "max_ratio": HOSTED.ratio,
        "max_depth": HOSTED.depth,
    }
    if spec["input_kind"] == "zip":
        report(-1.0, "unpacking the .zip file")
        return open_package(folder / spec["input_file"], **limits)
    if RBOX_LINK.fullmatch(spec["url"]):
        return _researchbox(spec["url"], folder, report)
    link = parse_link(_normalise(spec["url"]))
    # a token goes to a link's host only when the visitor sent their own for that host
    own = bool(keys.get(link.kind))
    return open_link(
        link,
        max_download=MAX_ZIP_BYTES,
        timeout=HOSTED.download_seconds,
        credentials=own,
        progress=lambda f, text: report(min(0.1, max(0.0, f) * 0.1), text.lower()),
        **limits,
    )


def _repository_job(
    spec: dict[str, Any], folder: Path, keys: dict[str, str], report: Report
) -> dict[str, Any]:
    from metacheck.report.report import report_repository

    with _open_repository(spec, folder, keys, report) as opened:
        if spec.get("input_file"):
            (folder / spec["input_file"]).unlink(missing_ok=True)  # read: the upload is deleted
        name = opened.name if opened.base or opened.remote else spec.get("name") or "folder"
        root = folder / "repo"
        root.mkdir()
        target = root / folder_name(str(name))
        shutil.move(str(opened.base_dir), target)
    if not any(p.is_file() for p in target.rglob("*")):
        raise JobFailed("The folder has no files to check.")
    modules = list(REPOSITORY_MODULES)
    args: dict[str, dict[str, Any]] = {"data_check": dict(DATA_ARGS)}
    if spec["run_code"]:
        modules.append("reproducibility_check")
        args["reproducibility_check"] = dict(CODE_ARGS)
    layout = report_layout(spec["report_type"], repository=True)
    page = folder / "report.html"
    with _checking(report):
        report_repository(target, output_file=page, modules=modules, args=args, report_type=layout)
    if not page.is_file() or page.suffix != ".html":
        raise JobFailed("The report could not be made.")
    from metacheck.app.run import protect

    html = protect(scrub_paths(page.read_text(encoding="utf-8"), root, folder))
    result = folder / "result.html"
    result.write_text(html, encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    page.unlink(missing_ok=True)
    return {"result": result.name, "type": "text/html; charset=utf-8", "report_type": layout}


# -- the process ---------------------------------------------------------------------------


def _use_keys(keys: dict[str, str]) -> None:
    """The visitor's own keys, in place of the server's for this process only, as metacheck's
    apps do (``api_keys_use()``): the server is shared, so a message about the server's key
    calls it the key the server provides."""
    from metacheck.archives.api_keys import api_keys_use

    api_keys_use(keys, shared=True)


def _message(exc: BaseException) -> str:
    """What to tell the sender about an exception: our own messages as they are, any other
    one by its kind only (its text may name a place on this server)."""
    from metacheck.app.run import UserError
    from metacheck.datapackage import PackageError
    from metacheck.web.psyarxiv import PsyArxivError

    if isinstance(exc, JobFailed | UserError | PackageError | PsyArxivError):
        return " ".join(str(exc).split())
    return (
        f"The check failed ({type(exc).__name__}). Please report it at "
        "https://github.com/scienceverse/pytacheck/issues, with the file if you can share it."
    )


def run_job(
    spec: dict[str, Any], folder: str, keys: dict[str, str], report: Report
) -> dict[str, Any]:
    """The job in *spec*, in the job's *folder*. Returns ``{"result", "type", "converter",
    "report_type"}``; raises :class:`JobFailed` with a message for the sender.

    Runs in a process of its own, so changing the environment and the temporary folder here
    reaches no other job.
    """
    work = Path(folder)
    scratch = work / "tmp"
    scratch.mkdir(exist_ok=True)
    tempfile.tempdir = str(scratch)  # unpacked archives land in the job's own folder
    _use_keys(keys)
    try:
        if spec["kind"] != "convert":
            from metacheck.api.app import _configure_llm

            _configure_llm()
        if spec["kind"] == "repocheck":
            return _repository_job(spec, work, keys, report)
        return _paper_job(spec, work, report)
    except JobFailed:
        raise
    except Exception as exc:
        raise JobFailed(_message(exc)) from None
    finally:
        for upload in work.glob("input.*"):
            upload.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(work / "repo", ignore_errors=True)
