"""The page: one upload, two buttons, a results table and the report."""

from __future__ import annotations

import atexit
import html
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import gradio as gr

from metacheck.app import bibr
from metacheck.app import hosted as hosting
from metacheck.app.checks import validated_checks
from metacheck.app.run import Analysis, DataJob, Row, Run, UserError, begin, protect, scratch_dir

__all__ = [
    "ABOUT_DATA",
    "ABOUT_EXPERIMENTAL",
    "ABOUT_VALIDATED",
    "BLOCKS_KWARGS",
    "MAIN_PAGE_NAME",
    "MOUNT_KWARGS",
    "QUEUE_KWARGS",
    "REPORT_SANDBOX",
    "Sessions",
    "build_app",
    "report_frame",
]

#: the name of the paper page in the links between pages (the local app has a second page)
MAIN_PAGE_NAME = "Check a paper"
REPO_URL = "https://github.com/scienceverse/pytacheck"
SCIENCEVERSE_URL = "https://www.scienceverse.org/"
BIBR_DEMO_URL = "https://try.bibr.org/"
#: The links at the top and bottom of the page: (label, url).
LINKS = (
    ("ScienceVerse", SCIENCEVERSE_URL),
    ("bibr demo", BIBR_DEMO_URL),
    ("Metacheck (R) on GitHub", "https://github.com/scienceverse/metacheck"),
    ("Pytacheck on GitHub", REPO_URL),
    ("bibr on GitHub", "https://github.com/scienceverse/bibr"),
)
#: [seconds between sweeps, age in seconds] for files Gradio made
BLOCKS_KWARGS: dict[str, Any] = {
    "analytics_enabled": False,
    "title": "metacheck",
    "delete_cache": (300, 1800),
}
#: no public API, one check at a time
QUEUE_KWARGS: dict[str, Any] = {"api_open": False, "default_concurrency_limit": 1}
#: settings for ``gr.mount_gradio_app``
MOUNT_KWARGS: dict[str, Any] = {
    "footer_links": [],
    "run_history": False,
    "ssr_mode": False,
    "enable_monitoring": False,
    "mcp_server": False,
    "max_file_size": "50mb",
}
#: The report runs its own script (tables, dark mode) and opens its links in new tabs.
#: Nothing more: no same-origin access, no forms, no navigation of this page. Tested in
#: Chromium: with "allow-scripts" alone the report's script works but its links do not.
REPORT_SANDBOX = "allow-scripts allow-popups allow-popups-to-escape-sandbox"

ABOUT_VALIDATED = (
    "Error rates as published in the documentation of R metacheck 0.3.1. The R version "
    "and PDF pipeline they were measured with are not recorded. Not yet re-measured on "
    "this version."
)
ABOUT_EXPERIMENTAL = (
    "Ported from R metacheck and tested against it, but not validated. "
    "Read their results as pointers, not verdicts."
)
ABOUT_DATA = (
    "**Shared data files.** This check is experimental too. It downloads the data files "
    "that the paper links to (on OSF and other repositories) and checks them. That can take "
    "a few minutes, and downloaded files are kept on this computer for the next run. "
    "Optional: set the OSF_PAT setting to an OSF access token before you start the app. "
    "OSF then allows more requests."
)
INTRO = (
    "Upload a research paper and get a report on its reporting practices: "
    "statistics, power, open science and references."
)
PRIVACY = (
    "PDFs are turned into text by the public GROBID server at TU Eindhoven, which R "
    "metacheck also uses, so a PDF you upload is sent there. If that server does not "
    "answer, the PDF goes to a public GROBID server hosted on Hugging Face instead. "
    "XML and JSON files are checked on this computer."
)
PRIVACY_BIBR = (
    "PDFs are sent to the scienceverse bibr service to be turned into text. "
    "XML and JSON files are checked on this computer."
)
READERS = [
    ("GROBID (public server at TU Eindhoven)", "grobid"),
    ("bibr (the scienceverse service; needs a key)", "bibr"),
]
DATA_LABEL = (
    "Check the shared data files (downloads them from OSF and other repositories; "
    "this can take a few minutes)"
)
KEY_ENV_NOTE = f"This computer supplies the key ({bibr.KEY_ENV}). You do not need to enter one."
KEY_ONLY_BIBR = "Your key is sent to the bibr service only."
STOPPED = "The data check was stopped. The other results are on this page."
DATA_FAILED = "The data check did not work. The other results are on this page."
CREDIT = (
    "metacheck is by Lisa DeBruine, Cristian Mesquida, Jakub Werner, Daniel Lakens and "
    f"contributors. This is the Python version: [{REPO_URL}]({REPO_URL})."
)
CSS = (
    ".gradio-container{max-width:1100px !important;margin:0 auto !important} #report-frame iframe{width:100%} "
    "#download-report a{display:inline-block;padding:8px 16px;border:1px solid "
    "var(--border-color-primary);border-radius:8px;text-decoration:none;font-weight:600} "
    "#mc-header{padding:12px 0 4px} "
    "#mc-header h1{font-size:2.2rem;line-height:1.15;margin:0;letter-spacing:-0.02em} "
    "#mc-header .mc-version{display:inline-block;vertical-align:middle;margin-left:10px;"
    "font-size:0.8rem;font-weight:600;padding:2px 10px;border-radius:999px;"
    "border:1px solid var(--color-accent);color:var(--color-accent)} "
    "#mc-header p.mc-intro{font-size:1.05rem;margin:8px 0 0;max-width:46rem} "
    ".mc-links{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0 0 !important;"
    "padding:0 !important;list-style:none !important;font-size:0.9rem} "
    ".mc-links li{margin:0 !important;padding:0 !important} "
    ".mc-links li::before{content:none !important} "
    ".mc-links a{color:var(--link-text-color, var(--color-accent));text-decoration:none;"
    "font-weight:600} .mc-links a:hover{text-decoration:underline} "
    "#mc-footer{border-top:1px solid var(--border-color-primary);margin-top:8px;"
    "padding-top:12px;font-size:0.9rem;color:var(--body-text-color-subdued)} "
    "#mc-footer p{margin:0 0 6px}"
)
EXPIRED = "Your upload is no longer on the server. Upload the paper again."
DATA_TOO_SLOW = (
    "The data check took longer than this server allows, so it was stopped. "
    "The other results are complete."
)
FAILED = "Something went wrong while checking this paper. Try the demo paper or another file."


def _links() -> str:
    items = "".join(
        f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(label)}</a>'
        "</li>"
        for label, url in LINKS
    )
    return f'<ul class="mc-links">{items}</ul>'


def header(version: str) -> str:
    """The page's title, version, one line on what it does, and the project links."""
    return (
        f'<h1>metacheck <span class="mc-version">Python preview {html.escape(version)}</span>'
        f'</h1><p class="mc-intro">{html.escape(INTRO)}</p>{_links()}'
    )


def here(text: str, hosted: bool) -> str:
    """A text for the page; on a shared server the files are checked there, not locally."""
    if not hosted:
        return text
    return text.replace("this computer", "this server").replace("This computer", "This server")


def data_budget(time_limit: float) -> tuple[float, float]:
    """Seconds a hosted run gives its data check (from the start of the run's process), and
    seconds to wait for it to stop. The rest of the time limit writes the report."""
    budget = max(time_limit - 60, time_limit * 0.8)
    return budget, min(30.0, (time_limit - budget) / 2)


def analyse_upload(
    path: str,
    online: bool,
    data: bool,
    reader: str,
    key: str,
    workdir: str,
    stop_data_at: float,
    grace: float,
    progress: Callable[[float, str], None],
) -> Analysis:
    """One hosted run, in its own process. The server makes ``workdir`` and removes it, so a
    run ended at the time limit leaves no report behind.

    The data check runs here too, after the fast checks, and its progress text goes to
    ``progress``. At ``stop_data_at`` (a ``time.time()`` value, set before the process
    started) it is stopped, waited for ``grace`` seconds, and the other results come back
    with its row saying so.
    """
    if not Path(path).is_file():
        raise UserError(EXPIRED)
    started = begin(
        path,
        online=online,
        data=data,
        workdir=Path(workdir),
        progress=progress,
        pdf=reader,
        bibr_key=key,
    )
    if not data:
        return started.analysis
    job = started.data_job()
    begun = time.monotonic()
    job.start()
    # the wait ends at stop_data_at at the latest, so the stop is never late
    while not job.wait(min(1.0, max(0.0, stop_data_at - time.time()))):
        if time.time() >= stop_data_at:
            job.stop()
            job.wait(grace)
            if job.output is None:
                job.error = TimeoutError(DATA_TOO_SLOW)
            break
        progress(0.95, data_status(job, begun, hosted=True))
    return started.finish(job)


def report_frame(page: str) -> str:
    """The report inside a sandboxed iframe, under a policy that lets only its own script run."""
    return (
        f'<iframe title="Report" sandbox="{REPORT_SANDBOX}" '
        'style="width:100%;height:75vh;border:0;background:#fff" '
        f'srcdoc="{html.escape(protect(page), quote=True)}"></iframe>'
    )


class Sessions:
    """One temporary folder per browser session for the report file to download."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="metacheck-app-reports-"))
        self._lock = threading.Lock()
        atexit.register(shutil.rmtree, self.root, ignore_errors=True)

    @staticmethod
    def safe(session: str) -> str:
        return "".join(c for c in session if c.isalnum()) or "session"

    def folder(self, session: str) -> Path:
        """A fresh folder for this session; the session's previous one is removed."""
        with self._lock:
            shutil.rmtree(self.root / self.safe(session), ignore_errors=True)
            return self.root / self.safe(session)

    def file(self, session: str, name: str) -> Path | None:
        """The report ``name`` of this session, if it is there."""
        path = (self.root / self.safe(session) / name).resolve()
        if path.is_relative_to(self.root.resolve()) and path.is_file():
            return path
        return None


def _download_link(session: str, path: Path) -> str:
    href = f"/report/{Sessions.safe(session)}/{quote(path.name)}"
    return f'<a href="{href}" download="{html.escape(path.name)}">Download the report</a>'


#: the traffic light word at the start of a result -> its cell colours (background, text). The
#: backgrounds are see-through so they read on a light and on a dark page; the word stays, so
#: the colour is never the only signal.
LIGHT_COLOURS = {
    "Red": ("rgba(220, 38, 38, 0.30)", "inherit"),
    "Yellow": ("rgba(234, 179, 8, 0.35)", "inherit"),
    "Green": ("rgba(34, 197, 94, 0.30)", "inherit"),
    "Failed": ("rgba(168, 85, 247, 0.25)", "inherit"),
}


def _light_style(result: str) -> str:
    colours = LIGHT_COLOURS.get(result.split(":", 1)[0].strip())
    return f"background-color: {colours[0]}; color: {colours[1]}" if colours else ""


def table_value(rows: list[Row]) -> Any:
    """The rows for the results table, the result cells coloured by their traffic light."""
    import pandas as pd

    frame = pd.DataFrame([row.cells() for row in rows], columns=["Check", "Status", "Result"])
    return frame.style.apply(
        lambda column: [_light_style(str(cell)) for cell in column], subset=["Result"]
    )


def _summary(analysis: Analysis) -> str:
    n = len(analysis.rows)
    return f"Ran {n} checks on **{analysis.name}** in {analysis.seconds:.1f} s."


def default_reader() -> str:
    """The reader a page starts with: bibr where this computer supplies the bibr key
    (``SCIVRS_API_KEY``, as a hosted server does), else GROBID, which needs no key."""
    return "bibr" if os.environ.get(bibr.KEY_ENV, "").strip() else "grobid"


def privacy_text(reader: str, hosted: bool = False) -> str:
    return here(PRIVACY_BIBR if reader == "bibr" else PRIVACY, hosted)


def key_note(remembered: bool = True, hosted: bool = False) -> str:
    """What to say under the key field."""
    if os.environ.get(bibr.KEY_ENV, "").strip():
        return here(KEY_ENV_NOTE, hosted)
    saved = bibr.load_key() if remembered else ""
    if saved:
        return f"A key is saved on this computer ({bibr.show_key(saved)}). {KEY_ONLY_BIBR}"
    return KEY_ONLY_BIBR


def plain_markdown(text: str) -> str:
    """Text from outside as Markdown that shows it as it is: no tags, links or emphasis."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>&])", r"\\\1", text)


def data_status(job: DataJob, started: float, hosted: bool = False) -> str:
    """The line under the results while the data check runs.

    On a shared server the line is the progress text of the run, which is not Markdown.
    """
    seconds = f"{time.monotonic() - started:.0f} s"
    if hosted:
        text = f"Checking the shared data files ({seconds})."
        return f"{text} {job.message}" if job.message else text
    text = f"The other results are ready. The data check is still running ({seconds})."
    return f"{text} {plain_markdown(job.message)}" if job.message else text


@dataclass(frozen=True)
class Pending:
    """A data check that runs for one browser session, after its fast results are shown."""

    run: Run
    job: DataJob
    started: float


def build_app(
    sessions: Sessions | None = None,
    hosted: hosting.HostedConfig | None = None,
    *,
    remember_keys: bool = True,
) -> gr.Blocks:
    """The page. ``hosted`` adds the limits and texts of the shared server.

    ``remember_keys=False`` never saves a bibr key and never offers one that was saved; a
    shared server (``hosted``) never does either.
    """
    from metacheck._version import __version__

    sessions = sessions or Sessions()
    pending: dict[str, Pending] = {}
    jobs = hosting.JobRunner(hosted.job_timeout) if hosted else None
    shared = hosted is not None
    remember_keys = remember_keys and not shared

    def page(analysis: Analysis, session: str) -> tuple[Any, ...]:
        return (
            gr.update(visible=True),
            _summary(analysis),
            table_value(analysis.rows),
            report_frame(analysis.html),
            hosting.data_link(analysis.report_path.name, protect(analysis.html))
            if shared
            else _download_link(session, analysis.report_path),
        )

    def run_hosted(
        path: str, online: bool, data: bool, reader: str, key: str, progress: Any
    ) -> tuple[Any, ...]:
        """The whole run, data check included, in its own process under the time limit."""
        assert jobs is not None and hosted is not None
        try:
            budget, grace = data_budget(hosted.job_timeout)
            # made here, not in the run's process: a process ended at the limit cleans up nothing
            with scratch_dir() as work:
                analysis = jobs.run(
                    analyse_upload,
                    path,
                    online,
                    data,
                    reader,
                    key,
                    work,
                    time.time() + budget,
                    grace,
                    # the demo paper is part of the package: only an upload is deleted
                    upload=None if Path(path).resolve() == _demo_path() else path,
                    on_timeout=lambda: gr.Error(hosting.TOO_SLOW, print_exception=False),
                    on_progress=lambda fraction, text: progress(fraction, desc=text),
                )
        except UserError as exc:
            raise gr.Error(str(exc), print_exception=False) from exc
        except gr.Error:
            raise
        except Exception as exc:
            raise gr.Error(FAILED) from exc
        return (*page(analysis, ""), "", gr.update(visible=False))

    def leave(session: str) -> None:
        """End the data check this session has running (a new check or Stop replaces it)."""
        old = pending.pop(session, None)
        if old is not None:
            old.job.stop()

    def run(
        path: str | None,
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        session: str,
        progress: Any,
    ) -> tuple[Any, ...]:
        if not path:
            raise gr.Error("Upload a paper first, or use the demo paper.", print_exception=False)
        use_bibr = reader == "bibr"
        if shared:
            key = bibr.resolve_key(key, remembered=False) if use_bibr else ""
            return run_hosted(path, online, data, reader, key, progress)
        leave(session)
        try:
            with scratch_dir() as work:
                started = begin(
                    path,
                    online=online,
                    data=data,
                    workdir=Path(work),
                    report_dir=sessions.folder(session),
                    progress=lambda fraction, text: progress(fraction, desc=text),
                    pdf=reader,
                    bibr_key=bibr.resolve_key(key, remembered=remember_keys) if use_bibr else "",
                )
        except UserError as exc:
            raise gr.Error(str(exc), print_exception=False) from exc
        except Exception as exc:
            raise gr.Error(FAILED) from exc
        # only a key that just worked for a PDF is saved; the key from the settings never is
        env_key = os.environ.get(bibr.KEY_ENV, "").strip()
        if (
            remember_keys
            and use_bibr
            and remember
            and key.strip()
            and path.lower().endswith(".pdf")
            and not env_key
        ):
            bibr.save_key(key.strip())
        if not data:
            return (*page(started.analysis, session), "", gr.update(visible=False))
        job = started.data_job()
        entry = Pending(started, job, time.monotonic())
        pending[session] = entry
        job.start()
        return (
            *page(started.analysis, session),
            data_status(job, entry.started),
            gr.update(visible=True),
        )

    def follow(request: gr.Request) -> Iterator[tuple[Any, ...]]:
        """The data check of this session: keep the status line alive, then show the result."""
        session = request.session_hash or ""
        entry = pending.get(session)
        skip = tuple(gr.skip() for _ in range(5))
        if entry is None:
            yield (*skip, gr.skip(), gr.skip())
            return
        job = entry.job
        try:
            while not job.wait(1.0):
                if pending.get(session) is not entry:
                    return  # a new check or Stop took over
                yield (*skip, data_status(job, entry.started), gr.skip())
            mine = pending.get(session) is entry
        finally:
            if pending.get(session) is entry:
                del pending[session]
            if not job.done:
                job.stop()  # the person left
        if not mine:
            return
        if job.stopped:
            yield (*skip, STOPPED, gr.update(visible=False))
            return
        final = entry.run.finish(job)
        failed = job.error is not None or (
            job.output is not None and job.output.traffic_light == "fail"
        )
        yield (*page(final, session), DATA_FAILED if failed else "", gr.update(visible=False))

    def on_check(
        upload: str | None,
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        shown = run(
            upload, online, data, reader, key, remember, request.session_hash or "", progress
        )
        # hosted: the upload is deleted after its run, so the field is cleared as well
        return (*shown, None) if shared else shown

    def on_demo(
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        from metacheck.papers.io import demofile

        shown = run(
            str(demofile("json")),
            online,
            data,
            reader,
            key,
            remember,
            request.session_hash or "",
            progress,
        )
        return (*shown, gr.skip()) if shared else shown

    def on_stop(request: gr.Request) -> tuple[Any, ...]:
        leave(request.session_hash or "")
        return STOPPED, gr.update(visible=False)

    def on_reader(reader: str) -> tuple[Any, ...]:
        return (
            gr.update(visible=reader == "bibr"),
            privacy_text(reader, shared),
            key_note(remember_keys, shared),
            gr.update(visible=remember_keys and bool(bibr.load_key())),
        )

    def on_forget() -> tuple[Any, ...]:
        bibr.forget_key()
        return key_note(remember_keys, shared), gr.update(visible=False)

    blocks_kwargs = (
        {**BLOCKS_KWARGS, "delete_cache": hosting.sweep(hosted.job_timeout)}
        if hosted
        else BLOCKS_KWARGS
    )
    with gr.Blocks(**blocks_kwargs) as app:
        if not shared:
            gr.Navbar(main_page_name=MAIN_PAGE_NAME)
        gr.HTML(header(__version__), elem_id="mc-header", js_on_load=None)
        upload = gr.File(
            label="Your paper (PDF, GROBID XML or bibr JSON)",
            file_types=[".pdf", ".xml", ".json"],
            type="filepath",
        )
        if hosted:
            gr.Markdown(hosting.HOSTED_NOTE)
        first = default_reader()
        reader = gr.Radio(READERS, value=first, label="Read PDFs with")
        with gr.Group(visible=first == "bibr") as key_group:
            # the key of this computer wins over a typed one, so there is nothing to type then
            key = gr.Textbox(
                label="Your bibr key",
                type="password",
                visible=not os.environ.get(bibr.KEY_ENV, "").strip(),
            )
            remember = gr.Checkbox(
                label=here("Remember the key on this computer", shared),
                value=False,
                visible=remember_keys,
            )
            note = gr.Markdown(
                key_note(remember_keys, shared) if first == "bibr" else KEY_ONLY_BIBR
            )
            forget = gr.Button(
                "Forget the saved key",
                size="sm",
                visible=first == "bibr" and remember_keys and bool(bibr.load_key()),
            )
        # a visitor's own computer is not where the files are checked
        privacy = gr.Markdown(privacy_text(first, shared))
        online = gr.Checkbox(
            label="Also run the online checks (slower: they look things up on the web)",
            value=False,
        )
        data = gr.Checkbox(label=DATA_LABEL, value=True)
        with gr.Row():
            check = gr.Button("Check my paper", variant="primary", size="lg")
            demo = gr.Button("Try the demo paper", size="lg")
        with gr.Column(visible=False) as results:
            summary = gr.Markdown()
            status = gr.Markdown()
            stop = gr.Button("Stop the data check", visible=False)
            table = gr.Dataframe(
                headers=["Check", "Status", "Result"],
                datatype="str",
                label="Results",
                interactive=False,
                wrap=True,
                column_widths=["22%", "12%", "66%"],
            )
            frame = gr.HTML(label="Report", elem_id="report-frame", js_on_load=None)
            download = gr.HTML(
                label="Download the report", elem_id="download-report", js_on_load=None
            )
        with gr.Accordion("About these checks", open=False):
            gr.Markdown(
                f"**Validated checks** ({', '.join(validated_checks())}). {ABOUT_VALIDATED}"
            )
            gr.Markdown(f"**Experimental checks.** {ABOUT_EXPERIMENTAL}")
            gr.Markdown(here(ABOUT_DATA, shared))
        with gr.Column(elem_id="mc-footer"):
            gr.Markdown(CREDIT)
            if hosted:
                gr.Markdown(hosting.footer(__version__, hosted.commit))
            gr.HTML(_links(), js_on_load=None)

        outputs = [results, summary, table, frame, download, status, stop]
        inputs = [online, data, reader, key, remember]
        keys = [key_group, privacy, note, forget]
        # hosted: one shared limit, since each button on its own would allow the full
        # number of runs; the field is cleared because the upload is deleted after its run
        limits: dict[str, Any] = (
            {"concurrency_id": "paper", "concurrency_limit": hosting.MAX_RUNS} if hosted else {}
        )
        shown = [*outputs, upload] if shared else outputs
        clicked = [
            check.click(on_check, [upload, *inputs], shown, api_visibility="private", **limits),
            demo.click(on_demo, inputs, shown, api_visibility="private", **limits),
        ]
        for event in clicked:
            event.success(on_reader, reader, keys, concurrency_limit=None, api_visibility="private")
        if not shared:
            # a click ends with the fast results; the data check goes on in a second event,
            # so that the same button works again and a new check or Stop can end it. On a
            # shared server the data check is part of the run and its time limit.
            for event in clicked:
                event.success(
                    follow, None, outputs, concurrency_limit=None, api_visibility="private"
                )
            # Stop ends the job, and the following event then ends within a second by
            # itself. Cancelling that event as well races with its end inside Gradio,
            # which then prints a KeyError traceback in the person's terminal.
            stop.click(
                on_stop,
                None,
                [status, stop],
                concurrency_limit=None,
                api_visibility="private",
            )
        reader.change(on_reader, reader, keys, api_visibility="private")
        if remember_keys:  # a shared server has no saved key, and nobody may remove one
            forget.click(on_forget, None, [note, forget], api_visibility="private")
    if not shared:
        from metacheck.app.ui_package import add_package_page

        # a folder is named by a path on this computer: only the local app has the page
        add_package_page(app, sessions)
    if hosted:
        app.queue(
            **{
                **QUEUE_KWARGS,
                "default_concurrency_limit": hosting.MAX_RUNS,
                "max_size": hosting.MAX_QUEUE,
            }
        )
    else:
        app.queue(**QUEUE_KWARGS)
    return cast(gr.Blocks, app)


def _demo_path() -> Path:
    from metacheck.papers.io import demofile

    return Path(demofile("json")).resolve()


def theme() -> Any:
    return gr.themes.Default()
