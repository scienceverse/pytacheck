"""The page: one upload, two buttons, a results table and the report."""

from __future__ import annotations

import atexit
import html
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

import gradio as gr

from pytacheck.app.checks import validated_checks
from pytacheck.app.run import Analysis, UserError, check_paper, scratch_dir

__all__ = [
    "ABOUT_EXPERIMENTAL",
    "ABOUT_VALIDATED",
    "BLOCKS_KWARGS",
    "MOUNT_KWARGS",
    "QUEUE_KWARGS",
    "REPORT_SANDBOX",
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
    "metacheck also uses, so a PDF you upload is sent there. XML and JSON files are "
    "checked on this computer."
)
CREDIT = (
    "metacheck is by Lisa DeBruine, Cristian Mesquida, Jakub Werner, Daniel Lakens and "
    f"contributors. This is the Python version: [{REPO_URL}]({REPO_URL})."
)
CSS = ".gradio-container{max-width:1100px !important} #report-frame iframe{width:100%}"
FAILED = "Something went wrong while checking this paper. Try the demo paper or another file."


def report_frame(page: str) -> str:
    """The report inside a sandboxed iframe."""
    return (
        f'<iframe title="Report" sandbox="{REPORT_SANDBOX}" '
        'style="width:100%;height:75vh;border:0;background:#fff" '
        f'srcdoc="{html.escape(page, quote=True)}"></iframe>'
    )


class _Sessions:
    """One temporary folder per browser session for the report file to download."""

    def __init__(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="metacheck-app-reports-"))
        self._lock = threading.Lock()
        atexit.register(shutil.rmtree, self._root, ignore_errors=True)

    def folder(self, session: str) -> Path:
        """A fresh folder for this session; the session's previous one is removed."""
        safe = "".join(c for c in session if c.isalnum()) or "session"
        with self._lock:
            shutil.rmtree(self._root / safe, ignore_errors=True)
            return self._root / safe


def _summary(analysis: Analysis) -> str:
    n = len(analysis.rows)
    return f"Ran {n} checks on **{analysis.name}** in {analysis.seconds:.1f} s."


def build_app() -> gr.Blocks:
    from pytacheck._version import __version__

    sessions = _Sessions()

    def run(path: str | None, online: bool, session: str, progress: Any) -> tuple[Any, ...]:
        if not path:
            raise gr.Error("Upload a paper first, or use the demo paper.")
        try:
            with scratch_dir() as work:
                analysis = check_paper(
                    path,
                    online=online,
                    workdir=Path(work),
                    report_dir=sessions.folder(session),
                    progress=lambda fraction, text: progress(fraction, desc=text),
                )
        except UserError as exc:
            raise gr.Error(str(exc)) from exc
        except Exception as exc:
            raise gr.Error(FAILED) from exc
        return (
            gr.update(visible=True),
            _summary(analysis),
            [row.cells() for row in analysis.rows],
            report_frame(analysis.html),
            gr.DownloadButton(value=str(analysis.report_path), visible=True),
        )

    def on_check(
        upload: str | None,
        online: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        return run(upload, online, request.session_hash or "", progress)

    def on_demo(
        online: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        from pytacheck.papers.io import demofile

        return run(str(demofile("json")), online, request.session_hash or "", progress)

    with gr.Blocks(**BLOCKS_KWARGS) as app:
        gr.Markdown(f"# metacheck\n\nPython version, preview {__version__}")
        gr.Markdown(INTRO)
        upload = gr.File(
            label="Your paper (PDF, GROBID XML or bibr JSON)",
            file_types=[".pdf", ".xml", ".json"],
            type="filepath",
        )
        gr.Markdown(PRIVACY)
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
            frame = gr.HTML(label="Report", elem_id="report-frame")
            download = gr.DownloadButton("Download the report", visible=False)
        with gr.Accordion("About these checks", open=False):
            gr.Markdown(
                f"**Validated checks** ({', '.join(validated_checks())}). {ABOUT_VALIDATED}"
            )
            gr.Markdown(f"**Experimental checks.** {ABOUT_EXPERIMENTAL}")
        gr.Markdown(CREDIT)

        outputs = [results, summary, table, frame, download]
        check.click(on_check, [upload, online], outputs, api_visibility="private")
        demo.click(on_demo, [online], outputs, api_visibility="private")
    app.queue(**QUEUE_KWARGS)
    return cast(gr.Blocks, app)


def theme() -> Any:
    return gr.themes.Default()
