"""The "Check a data package" page of the local app.

A second page of the same Gradio app, at ``/package``. It is built only for the local app:
a folder is named by a path on the computer that runs the app, which a shared server must
never offer. The logic is in :mod:`metacheck.app.package`; this module is the page.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import gradio as gr

from metacheck.app import package as pk
from metacheck.app.progress import Background, package_steps
from metacheck.app.run import UserError
from metacheck.app.ui import (
    CLASSIFIER_INFO,
    CLASSIFIER_LABEL,
    CLASSIFIER_MISSING_INFO,
    MAIN_PAGE_NAME,
    START_JS,
    TICK,
    Sessions,
    _download_links,
    _gradio_error,
    coloured,
    package_summary,
    report_frame,
)

__all__ = [
    "ABOUT",
    "INTRO",
    "PAGE_NAME",
    "PAGE_PATH",
    "add_package_page",
    "coloured",
    "report_key",
]

PAGE_NAME = "Check a data package"
PAGE_PATH = "package"
INTRO = (
    "Check the folder of data, code and documentation that comes with a paper before it is "
    "archived: junk files, file formats and names, the folder tree, the README and the parts a "
    "package should have. Everything runs on this computer and nothing is uploaded. The "
    "downloads are the repository you give a link to, if you do, and the model of the local "
    "classifier (see below), the first time it is used. The main page checks a package too: "
    "give it the same archive or link next to a paper."
)
ABOUT = (
    "**Status.** *Fail* means the package does not meet a requirement, *Warning* that it could "
    "be better, *Manual* that a person has to decide, *Pass* that it meets it. A check that "
    "could not run says *Did not run*; the others still ran."
)
PRESET_INFO = "Which checks to run. Presets from the packs you have installed are listed too."


def report_key(session: str) -> str:
    """The key of this session's package report folder, apart from the paper page's, so that
    a new report on one page does not remove the other page's file."""
    return f"{session}package"


def add_package_page(app: gr.Blocks, sessions: Sessions, start_preset: str | None = None) -> None:
    """Add the page to *app*. Call it after the app's own ``with gr.Blocks()`` block.

    ``start_preset`` is chosen at first when it is one of the page's presets."""

    def on_check_package(
        kind: str,
        folder: str,
        upload: str | None,
        link: str,
        preset: str,
        classifier: bool,
        request: gr.Request,
    ) -> Iterator[tuple[Any, ...]]:
        """Check the package in the background, and draw its steps (the last output) while it
        runs, as the main page does."""
        key = report_key(request.session_hash or "")
        keep = (gr.skip(),) * 6
        try:
            source = (
                pk.resolve_link(link)
                if kind == pk.LINK
                else pk.resolve_source(kind, folder, upload)
            )
        except UserError as exc:
            yield (*keep, "")  # takes away the panel the button drew
            raise gr.Error(str(exc), print_exception=False) from exc
        steps = package_steps(str(source), pk.ARCHIVE if kind == pk.ZIP else kind)

        def work() -> pk.PackageAnalysis:
            return pk.check_package_source(
                source,
                preset,
                report_dir=sessions.folder(key),
                local_classifier=classifier,
                progress=steps,
            )

        job = Background(work).start()
        yield (gr.update(visible=False), *keep[1:], steps.html())  # the last results go
        while not job.wait(TICK):
            yield (*keep, steps.html())
        if job.error is not None:
            error = _gradio_error(job.error, pk.FAILED)
            steps.fail(error.message)
            yield (*keep, steps.html())
            if error is job.error:
                raise error
            raise error from job.error
        analysis: pk.PackageAnalysis = job.result
        yield (
            gr.update(visible=True),
            package_summary(analysis),
            coloured(analysis.checks),
            coloured(analysis.checklist),
            _download_links(key, analysis),
            report_frame(analysis.html),
            "",
        )

    def on_source(kind: str) -> tuple[Any, ...]:
        return (
            gr.update(visible=kind == pk.FOLDER),
            gr.update(visible=kind == pk.ZIP),
            gr.update(visible=kind == pk.LINK),
        )

    with app.route(PAGE_NAME, PAGE_PATH):
        gr.Navbar(main_page_name=MAIN_PAGE_NAME)
        gr.HTML(
            f'<div id="mc-header"><h1>{PAGE_NAME}</h1><p class="mc-intro">{INTRO}</p></div>',
            js_on_load=None,
        )
        kind = gr.Radio(pk.SOURCES, value=pk.FOLDER, label="The package is")
        folder = gr.Textbox(
            label="Folder",
            info="The path of the package's folder on this computer, for example "
            "~/data/my_study. It must be inside your home folder. The folder is read where "
            "it is and never changed.",
            placeholder="/path/to/my_package",
        )
        upload = gr.File(
            label="Zip of the package",
            file_types=[".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"],
            type="filepath",
            visible=False,
        )
        link = gr.Textbox(
            label="Repository link",
            info="The link of a public repository on GitHub, GitLab, OSF or Zenodo, or a "
            "Zenodo DOI. It is downloaded to this computer (a token of yours for the host is "
            "used if you have set one) and removed after the run.",
            placeholder="https://github.com/owner/repository",
            visible=False,
        )
        gr.Markdown(pk.BIGGER_THAN_UPLOAD)
        presets = pk.package_presets()
        refs = [ref for _, ref in presets]
        first = start_preset if start_preset in refs else (refs[0] if refs else None)
        preset = gr.Dropdown(
            presets,
            value=first,
            label="Checks",
            info=PRESET_INFO,
            interactive=True,
        )
        classifier = gr.Checkbox(
            label=CLASSIFIER_LABEL,
            info=CLASSIFIER_INFO if pk.classifier_installed() else CLASSIFIER_MISSING_INFO,
            value=True,
        )
        check = gr.Button("Check the package", variant="primary", size="lg")
        # the steps of a run, drawn while it runs; empty (and so not shown) otherwise
        panel = gr.HTML("", elem_id="mc-run-panel", js_on_load=None, padding=False)
        with gr.Column(visible=False) as results:
            summary = gr.Markdown()
            checks = gr.Dataframe(
                headers=pk.CHECK_COLUMNS,
                datatype="str",
                label="Checks",
                interactive=False,
                wrap=True,
                column_widths=["24%", "12%", "64%"],
            )
            checklist = gr.Dataframe(
                headers=pk.CHECKLIST_COLUMNS,
                datatype="str",
                label="Checklist",
                interactive=False,
                wrap=True,
                column_widths=["18%", "22%", "10%", "50%"],
            )
            download = gr.HTML(
                label="Download the report", elem_id="download-report", js_on_load=None
            )
            with gr.Accordion("The full report", open=False):
                frame = gr.HTML(label="Report", elem_id="report-frame", js_on_load=None)
        with gr.Accordion("About these checks", open=False):
            gr.Markdown(ABOUT)

        kind.change(on_source, kind, [folder, upload, link], api_visibility="private")
        quiet: dict[str, Any] = {"api_visibility": "private", "show_progress": "hidden"}
        # the browser draws the panel at once: the run draws its own steps when it starts
        check.click(None, None, panel, js=START_JS, **quiet)
        check.click(
            on_check_package,
            [kind, folder, upload, link, preset, classifier],
            [results, summary, checks, checklist, download, frame, panel],
            **quiet,
        )
        # There is no ``app.load`` here, on purpose: a load event runs at page load, and a click
        # that lands as it completes loses its answer (Gradio closes the event stream between
        # the two, so the result never arrives and nothing says so). The presets are found
        # once, when the page is built, so a pack installed while the app runs is offered
        # after the app is restarted.
