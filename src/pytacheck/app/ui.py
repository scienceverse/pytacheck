"""The page: one upload, two buttons, a results table and the report."""

from __future__ import annotations

import atexit
import html
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import gradio as gr

from pytacheck.app import bibr
from pytacheck.app.checks import validated_checks
from pytacheck.app.run import Analysis, DataJob, UserError, begin, scratch_dir

__all__ = [
    "ABOUT_DATA",
    "ABOUT_EXPERIMENTAL",
    "ABOUT_VALIDATED",
    "BLOCKS_KWARGS",
    "MOUNT_KWARGS",
    "QUEUE_KWARGS",
    "REPORT_CSP",
    "REPORT_SANDBOX",
    "Sessions",
    "build_app",
    "report_frame",
]

REPO_URL = "https://github.com/scienceverse/pytacheck"
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

#: The report needs no outside resource. A paper's text reaches it unescaped (as in R), so
#: this stops a script or a stylesheet in a paper from loading or sending anything.
REPORT_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:"
)

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
    ".gradio-container{max-width:1100px !important} #report-frame iframe{width:100%} "
    "#download-report a{display:inline-block;padding:8px 16px;border:1px solid "
    "var(--border-color-primary);border-radius:8px;text-decoration:none;font-weight:600}"
)
FAILED = "Something went wrong while checking this paper. Try the demo paper or another file."


def report_frame(page: str) -> str:
    """The report inside a sandboxed iframe, with a policy that blocks outside requests."""
    meta = f'<meta http-equiv="Content-Security-Policy" content="{REPORT_CSP}">'
    page = meta + page
    return (
        f'<iframe title="Report" sandbox="{REPORT_SANDBOX}" '
        'style="width:100%;height:75vh;border:0;background:#fff" '
        f'srcdoc="{html.escape(page, quote=True)}"></iframe>'
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


def _summary(analysis: Analysis) -> str:
    n = len(analysis.rows)
    return f"Ran {n} checks on **{analysis.name}** in {analysis.seconds:.1f} s."


def privacy_text(reader: str) -> str:
    return PRIVACY_BIBR if reader == "bibr" else PRIVACY


def key_note() -> str:
    """What to say under the key field."""
    if os.environ.get(bibr.KEY_ENV, "").strip():
        return KEY_ENV_NOTE
    saved = bibr.load_key()
    if saved:
        return f"A key is saved on this computer ({bibr.show_key(saved)}). {KEY_ONLY_BIBR}"
    return KEY_ONLY_BIBR


def data_status(job: DataJob, started: float) -> str:
    """The line under the results while the data check runs."""
    text = (
        "The other results are ready. The data check is still running "
        f"({time.monotonic() - started:.0f} s)."
    )
    return f"{text} {job.message}" if job.message else text


def build_app(sessions: Sessions | None = None) -> gr.Blocks:
    from pytacheck._version import __version__

    sessions = sessions or Sessions()
    jobs: dict[str, DataJob] = {}

    def page(analysis: Analysis, session: str) -> tuple[Any, ...]:
        return (
            gr.update(visible=True),
            _summary(analysis),
            [row.cells() for row in analysis.rows],
            report_frame(analysis.html),
            _download_link(session, analysis.report_path),
        )

    def run(
        path: str | None,
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        session: str,
        progress: Any,
    ) -> Iterator[tuple[Any, ...]]:
        if not path:
            raise gr.Error("Upload a paper first, or use the demo paper.", print_exception=False)
        use_bibr = reader == "bibr"
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
                    bibr_key=bibr.resolve_key(key) if use_bibr else "",
                )
        except UserError as exc:
            raise gr.Error(str(exc), print_exception=False) from exc
        except Exception as exc:
            raise gr.Error(FAILED) from exc
        # only a key that just worked for a PDF is saved; the key from the settings never is
        env_key = os.environ.get(bibr.KEY_ENV, "").strip()
        if use_bibr and remember and key.strip() and path.lower().endswith(".pdf") and not env_key:
            bibr.save_key(key.strip())
        if not data:
            yield (*page(started.analysis, session), "", gr.update(visible=False))
            return
        job = started.data_job()
        jobs[session] = job
        job.start()
        clock = time.monotonic()
        try:
            yield (
                *page(started.analysis, session),
                data_status(job, clock),
                gr.update(visible=True),
            )
            while not job.wait(1.0):
                yield (*(gr.skip() for _ in range(5)), data_status(job, clock), gr.skip())
        finally:
            if jobs.get(session) is job:
                del jobs[session]
            if not job.done:
                job.stop()  # the person left or pressed stop
        if job.stopped:
            yield (*(gr.skip() for _ in range(5)), STOPPED, gr.update(visible=False))
            return
        final = started.finish(job)
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
    ) -> Iterator[tuple[Any, ...]]:
        yield from run(
            upload, online, data, reader, key, remember, request.session_hash or "", progress
        )

    def on_demo(
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> Iterator[tuple[Any, ...]]:
        from pytacheck.papers.io import demofile

        yield from run(
            str(demofile("json")),
            online,
            data,
            reader,
            key,
            remember,
            request.session_hash or "",
            progress,
        )

    def on_stop(request: gr.Request) -> tuple[Any, ...]:
        job = jobs.get(request.session_hash or "")
        if job is not None:
            job.stop()
        return STOPPED, gr.update(visible=False)

    def on_reader(reader: str) -> tuple[Any, ...]:
        return (
            gr.update(visible=reader == "bibr"),
            privacy_text(reader),
            key_note(),
            gr.update(visible=bool(bibr.load_key())),
        )

    def on_forget() -> tuple[Any, ...]:
        bibr.forget_key()
        return key_note(), gr.update(visible=False)

    with gr.Blocks(**BLOCKS_KWARGS) as app:
        gr.Markdown(f"# metacheck\n\nPython version, preview {__version__}")
        gr.Markdown(INTRO)
        upload = gr.File(
            label="Your paper (PDF, GROBID XML or bibr JSON)",
            file_types=[".pdf", ".xml", ".json"],
            type="filepath",
        )
        reader = gr.Radio(READERS, value="grobid", label="Read PDFs with")
        with gr.Group(visible=False) as key_group:
            key = gr.Textbox(label="Your bibr key", type="password")
            remember = gr.Checkbox(label="Remember the key on this computer", value=False)
            note = gr.Markdown(KEY_ONLY_BIBR)
            forget = gr.Button("Forget the saved key", size="sm", visible=False)
        privacy = gr.Markdown(PRIVACY)
        with gr.Row():
            check = gr.Button("Check my paper", variant="primary")
            demo = gr.Button("Try the demo paper")
        online = gr.Checkbox(
            label="Also run the online checks (slower: they look things up on the web)",
            value=False,
        )
        data = gr.Checkbox(label=DATA_LABEL, value=True)
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
            gr.Markdown(ABOUT_DATA)
        gr.Markdown(CREDIT)

        outputs = [results, summary, table, frame, download, status, stop]
        inputs = [online, data, reader, key, remember]
        started = [
            check.click(on_check, [upload, *inputs], outputs, api_visibility="private"),
            demo.click(on_demo, inputs, outputs, api_visibility="private"),
        ]
        stop.click(
            on_stop,
            None,
            [status, stop],
            cancels=cast(Any, started),
            concurrency_limit=None,
            api_visibility="private",
        )
        reader.change(
            on_reader, reader, [key_group, privacy, note, forget], api_visibility="private"
        )
        forget.click(on_forget, None, [note, forget], api_visibility="private")
    app.queue(**QUEUE_KWARGS)
    return cast(gr.Blocks, app)


def theme() -> Any:
    return gr.themes.Default()
