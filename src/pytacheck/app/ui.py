"""The page: one upload, two buttons, a results table and the report."""

from __future__ import annotations

import atexit
import html
import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import gradio as gr

from pytacheck.app import hosted as hosting
from pytacheck.app.checks import validated_checks
from pytacheck.app.run import Analysis, UserError, check_paper, scratch_dir

__all__ = [
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
CREDIT = (
    "metacheck is by Lisa DeBruine, Cristian Mesquida, Jakub Werner, Daniel Lakens and "
    f"contributors. This is the Python version: [{REPO_URL}]({REPO_URL})."
)
CSS = (
    ".gradio-container{max-width:1100px !important} #report-frame iframe{width:100%} "
    "#download-report a{display:inline-block;padding:8px 16px;border:1px solid "
    "var(--border-color-primary);border-radius:8px;text-decoration:none;font-weight:600}"
)
EXPIRED = "Your upload is no longer on the server. Upload the paper again."
FAILED = "Something went wrong while checking this paper. Try the demo paper or another file."


def analyse_upload(path: str, online: bool, progress: Callable[[float, str], None]) -> Analysis:
    """One hosted run, in its own process: the report stays in the scratch folder."""
    if not Path(path).is_file():
        raise UserError(EXPIRED)
    with scratch_dir() as work:
        return check_paper(path, online=online, workdir=Path(work), progress=progress)


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


def build_app(
    sessions: Sessions | None = None, hosted: hosting.HostedConfig | None = None
) -> gr.Blocks:
    """The page. ``hosted`` adds the limits and texts of the shared server."""
    from pytacheck._version import __version__

    sessions = sessions or Sessions()
    jobs = hosting.JobRunner(hosted.job_timeout) if hosted else None

    def analyse(path: str, online: bool, session: str, progress: Any) -> Analysis:
        with scratch_dir() as work:
            return check_paper(
                path,
                online=online,
                workdir=Path(work),
                report_dir=sessions.folder(session),
                progress=lambda fraction, text: progress(fraction, desc=text),
            )

    def run(path: str | None, online: bool, session: str, progress: Any) -> tuple[Any, ...]:
        if not path:
            raise gr.Error("Upload a paper first, or use the demo paper.", print_exception=False)
        try:
            if jobs is None:
                analysis = analyse(path, online, session, progress)
            else:
                analysis = jobs.run(
                    analyse_upload,
                    path,
                    online,
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
        return (
            gr.update(visible=True),
            _summary(analysis),
            [row.cells() for row in analysis.rows],
            report_frame(analysis.html),
            hosting.data_link(analysis.report_path.name, analysis.html)
            if hosted
            else _download_link(session, analysis.report_path),
        )

    def on_check(
        upload: str | None,
        online: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        shown = run(upload, online, request.session_hash or "", progress)
        # hosted: the upload is deleted after its run, so the field is cleared as well
        return (*shown, None) if hosted else shown

    def on_demo(
        online: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        from pytacheck.papers.io import demofile

        shown = run(str(demofile("json")), online, request.session_hash or "", progress)
        return (*shown, gr.skip()) if hosted else shown

    blocks_kwargs = (
        {**BLOCKS_KWARGS, "delete_cache": hosting.sweep(hosted.job_timeout)}
        if hosted
        else BLOCKS_KWARGS
    )
    with gr.Blocks(**blocks_kwargs) as app:
        gr.Markdown(f"# metacheck\n\nPython version, preview {__version__}")
        gr.Markdown(INTRO)
        upload = gr.File(
            label="Your paper (PDF, GROBID XML or bibr JSON)",
            file_types=[".pdf", ".xml", ".json"],
            type="filepath",
        )
        if hosted:
            gr.Markdown(hosting.HOSTED_NOTE)
        # a visitor's own computer is not where the files are checked
        gr.Markdown(PRIVACY.replace("this computer", "this server") if hosted else PRIVACY)
        with gr.Row():
            check = gr.Button("Check my paper", variant="primary")
            demo = gr.Button("Try the demo paper")
        online = gr.Checkbox(
            label="Also run the online checks (slower: they look things up on the web)",
            value=False,
        )
        with gr.Column(visible=False) as results:
            summary = gr.Markdown()
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
        gr.Markdown(CREDIT)
        if hosted:
            gr.Markdown(hosting.footer(__version__, hosted.commit))

        outputs = [results, summary, table, frame, download]
        # one shared limit: each button on its own would allow the full number of runs
        limits: dict[str, Any] = (
            {"concurrency_id": "paper", "concurrency_limit": hosting.MAX_RUNS} if hosted else {}
        )
        check.click(
            on_check,
            [upload, online],
            [*outputs, upload] if hosted else outputs,
            api_visibility="private",
            **limits,
        )
        demo.click(
            on_demo,
            [online],
            [*outputs, upload] if hosted else outputs,
            api_visibility="private",
            **limits,
        )
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
    from pytacheck.papers.io import demofile

    return Path(demofile("json")).resolve()


def theme() -> Any:
    return gr.themes.Default()
