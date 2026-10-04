"""The "Check a data package" page of the local app.

A second page of the same Gradio app, at ``/package``. It is built only for the local app:
a folder is named by a path on the computer that runs the app, which a shared server must
never offer. The logic is in :mod:`metacheck.app.package`; this module is the page.
"""

from __future__ import annotations

from typing import Any

import gradio as gr

from metacheck.app import package as pk
from metacheck.app.run import UserError
from metacheck.app.ui import (
    MAIN_PAGE_NAME,
    Sessions,
    _download_link,
    _light_style,
    plain_markdown,
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
    "package should have. Everything runs on this computer and nothing is uploaded. The one "
    "download is the model of the local classifier (see below), the first time it is used."
)
ABOUT = (
    "**Status.** *Fail* means the package does not meet a requirement, *Warning* that it could "
    "be better, *Manual* that a person has to decide, *Pass* that it meets it. A check that "
    "could not run says *Did not run*; the others still ran."
)
CLASSIFIER_LABEL = "Name the concept of each data column with the local classifier"
#: the checkbox is ticked by default, as the classifier is the default of ``metacheck package``
CLASSIFIER_INFO = (
    "It needs the concepts extra, and its model (about 840 MB) is downloaded the first time. "
    "Untick it to name the concepts by rules only, with no download."
)
#: the same, when the extra is not installed: the box can stay ticked, the run falls back
CLASSIFIER_MISSING_INFO = (
    "Not installed on this computer, so the concepts will come from rules only. To get it, "
    'install the concepts extra: `pip install "metacheck[concepts]"`.'
)
PRESET_INFO = "Which checks to run. Presets from the packs you have installed are listed too."


def report_key(session: str) -> str:
    """The key of this session's package report folder, apart from the paper page's, so that
    a new report on one page does not remove the other page's file."""
    return f"{session}package"


def coloured(frame: Any, column: str = "Status") -> Any:
    """*frame* for a table, the cells of its status column coloured by their traffic light.

    The word stays in the cell, so the colour is never the only signal. Colours are those
    of the paper page's results.
    """
    return frame.style.apply(
        lambda col: [_light_style(pk.status_light(str(cell)) or "") for cell in col],
        subset=[column],
    )


def add_package_page(app: gr.Blocks, sessions: Sessions) -> None:
    """Add the page to *app*. Call it after the app's own ``with gr.Blocks()`` block."""

    def on_check_package(
        kind: str,
        folder: str,
        upload: str | None,
        preset: str,
        classifier: bool,
        request: gr.Request,
        progress: gr.Progress = gr.Progress(),  # noqa: B008 - Gradio reads this default
    ) -> tuple[Any, ...]:
        key = report_key(request.session_hash or "")
        try:
            source = pk.resolve_source(kind, folder, upload)
            analysis = pk.check_package_source(
                source,
                preset,
                report_dir=sessions.folder(key),
                local_classifier=classifier,
                progress=lambda fraction, text: progress(fraction, desc=text),
            )
        except UserError as exc:
            raise gr.Error(str(exc), print_exception=False) from exc
        except Exception as exc:
            raise gr.Error(pk.FAILED) from exc
        summary = (
            f"Checked **{plain_markdown(analysis.name)}** in {analysis.seconds:.1f} s: "
            f"{pk.counts_line(analysis.checklist)}."
        )
        if analysis.note:
            summary += f"\n\n{analysis.note}"  # our own text, not the package's
        return (
            gr.update(visible=True),
            summary,
            coloured(analysis.checks),
            coloured(analysis.checklist),
            _download_link(key, analysis.report_path),
            report_frame(analysis.html),
        )

    def on_source(kind: str) -> tuple[Any, ...]:
        return gr.update(visible=kind == pk.FOLDER), gr.update(visible=kind == pk.ZIP)

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
        gr.Markdown(pk.BIGGER_THAN_UPLOAD)
        presets = pk.package_presets()
        preset = gr.Dropdown(
            presets,
            value=presets[0][1] if presets else None,
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

        kind.change(on_source, kind, [folder, upload], api_visibility="private")
        check.click(
            on_check_package,
            [kind, folder, upload, preset, classifier],
            [results, summary, checks, checklist, download, frame],
            api_visibility="private",
        )
        # There is no ``app.load`` here, on purpose: a load event runs at page load, and a click
        # that lands as it completes loses its answer (Gradio closes the event stream between
        # the two, so the result never arrives and nothing says so). The presets are found
        # once, when the page is built, so a pack installed while the app runs is offered
        # after the app is restarted.
