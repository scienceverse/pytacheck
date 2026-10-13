"""The page: one upload, two buttons, a results table and the report."""

from __future__ import annotations

import atexit
import html
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import gradio as gr

from metacheck.app import bibr
from metacheck.app import checks as catalog
from metacheck.app import hosted as hosting
from metacheck.app import package as pk
from metacheck.app.checks import validated_checks
from metacheck.app.progress import Background, Steps, package_steps, paper_steps, starting
from metacheck.app.run import Analysis, DataJob, Row, Run, UserError, begin, protect, scratch_dir

__all__ = [
    "ABOUT_DATA",
    "ABOUT_EXPERIMENTAL",
    "ABOUT_VALIDATED",
    "BLOCKS_KWARGS",
    "MAIN_PAGE_NAME",
    "MODES",
    "MOUNT_KWARGS",
    "QUEUE_KWARGS",
    "REPORT_SANDBOX",
    "Sessions",
    "analyse_package",
    "box_kind",
    "build_app",
    "coloured",
    "demo_package_archive",
    "ready_line",
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
    "Check the reporting practices of a research paper: statistics, power, open science and "
    "references. Or switch to Data package to check the data, code and documentation that "
    "come with a paper."
)
#: the switch at the top of the page: what it checks
PAPER_MODE = "paper"
PACKAGE_MODE = "package"
MODES = [("Paper", PAPER_MODE), ("Data package", PACKAGE_MODE)]
MODE_LABEL = "What to check"
#: the box of each mode: its label, what it says while it is empty, and what it takes
BOX_LABEL = "Your paper"
BOX_PLACEHOLDER = "Drop your paper here, or press the clip to choose it"
BOX_INFO = "A PDF, a GROBID XML file or a bibr JSON file."
PACKAGE_BOX_LABEL = "Your data package"
PACKAGE_BOX_PLACEHOLDER = "Drop its archive here, or paste the link of its repository"
PACKAGE_BOX_INFO = (
    "A zip or tar archive, or the link of a public repository on GitHub, GitLab, OSF or Zenodo."
)
PACKAGE_BOX_INFO_LOCAL = (
    "A zip or tar archive, the link of a public repository on GitHub, GitLab, OSF or Zenodo, "
    "or the path of its folder on this computer."
)
#: what the box accepts as a file: the paper formats, and the archives of a data package
BOX_FILE_TYPES = [*pk.PAPER_SUFFIXES, ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"]
#: the box when nothing is in it
EMPTY_BOX = {"text": "", "files": []}
#: the line under the box, by what it holds
READY = {
    pk.PAPER: "Ready to check **{name}** as a paper.",
    pk.ARCHIVE: "Ready to check the archive **{name}** as a data package.",
    pk.LINK: "Ready to check **{name}** as a data package. The repository is downloaded when "
    "you press Check.",
    pk.FOLDER: "Ready to check the folder **{name}** as a data package.",
}
#: added to that line when the switch moved to follow the box
SWITCHED = {
    PAPER_MODE: "The switch above moved to Paper to match.",
    PACKAGE_MODE: "The switch above moved to Data package to match.",
}
#: the demo data package: a made-up study in ``metacheck/resources/demos``
DEMO_PACKAGE = "sleep_study"
DEMO_PACKAGE_BUTTON = "Try the demo package"
#: seconds between two drawings of the steps of a run
TICK = 0.5
#: what the browser draws when a button is pressed, before the run draws its own steps; the
#: page then scrolls just enough to show them
START_JS = (
    "() => { setTimeout(() => document.getElementById('mc-run-panel')"
    "?.scrollIntoView({block: 'nearest', behavior: 'smooth'}), 150); "
    f"return {json.dumps(starting())}; }}"
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
PRIVACY_PACKAGE = (
    "A data package is checked on this computer, and none of it is sent anywhere. For a "
    "repository link, this computer downloads the repository from its host (GitHub, GitLab, "
    "OSF or Zenodo)."
)
READERS = [
    ("GROBID (public server at TU Eindhoven)", "grobid"),
    ("bibr (the scienceverse service; needs a key)", "bibr"),
]
PICKER_TITLE = "Choose the checks"
PACKAGE_PRESET_LABEL = "Checks for the data package"
PACKAGE_PRESET_INFO = (
    "Which checks run on the package. Presets from the packs installed on this computer are "
    "listed too."
)
CLASSIFIER_LABEL = "Name the concept of each data column with the local classifier"
#: the box is ticked by default, as the classifier is the default of ``metacheck package``
CLASSIFIER_INFO = (
    "It needs the concepts extra, and its model (about 840 MB) is downloaded the first time. "
    "Untick it to name the concepts by rules only, with no download."
)
#: the same, when the extra is not installed: the box can stay ticked, the run falls back
CLASSIFIER_MISSING_INFO = (
    "Not installed on this computer, so the concepts will come from rules only. To get it, "
    'install the concepts extra: `pip install "metacheck[concepts]"`.'
)
PICKER_INTRO = (
    "The ticked checks run on your paper. Checks that take minutes, or need a key or a "
    "server of their own, are not offered here; run them with the pytacheck command."
)
PRESET_INFO = (
    "Tick the checks of a preset. Presets from the packs installed on this computer are listed too."
)
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
    "#mc-footer p{margin:0 0 6px} "
    # the switch at the top: two buttons side by side, the chosen one filled
    "#mc-mode-row .form,#mc-mode{border:0 !important;background:transparent !important;"
    "padding:0 !important;box-shadow:none !important} "
    "#mc-mode .wrap{display:inline-flex;gap:4px;padding:4px;border-radius:999px;"
    "border:1px solid var(--border-color-primary);background:var(--block-background-fill)} "
    "#mc-mode label{margin:0 !important;padding:9px 22px !important;border-radius:999px "
    "!important;border:0 !important;background:transparent !important;box-shadow:none "
    "!important;cursor:pointer;font-size:1rem;font-weight:600} "
    "#mc-mode label.selected{background:var(--color-accent) !important} "
    "#mc-mode label.selected span{color:#fff !important} "
    "#mc-mode label input{position:absolute;opacity:0;pointer-events:none} "
    "#mc-mode label:has(input:focus-visible){outline:2px solid var(--color-accent);"
    "outline-offset:2px} "
    "#mc-found:not(:has(p)),#mc-status:not(:has(p)),#mc-run-panel:not(:has(.mc-run))"
    "{display:none !important} "
    "#mc-found p{margin:0;color:var(--body-text-color-subdued)} "
    # the steps of a run
    ".mc-run{border:1px solid var(--border-color-primary);border-radius:12px;"
    "padding:14px 18px;background:var(--block-background-fill)} "
    ".mc-run-failed{border-color:rgba(220,38,38,0.6)} "
    ".mc-run-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;"
    "margin-bottom:8px;font-size:1.05rem} "
    ".mc-run-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap} "
    ".mc-run-clock,.mc-step-time{font-variant-numeric:tabular-nums;"
    "color:var(--body-text-color-subdued)} "
    ".mc-run-wait{margin:0 0 10px !important;color:var(--body-text-color-subdued)} "
    ".mc-run-error{margin:10px 0 0 !important;color:#b91c1c} "
    ".dark .mc-run-error{color:#fca5a5} "
    "ol.mc-steps{list-style:none !important;margin:0 !important;padding:0 !important} "
    ".mc-step{display:grid;grid-template-columns:20px 1fr auto;column-gap:12px;row-gap:6px;"
    "align-items:center;margin:0 !important;padding:5px 0 !important} "
    ".mc-step::before,.mc-step::marker{content:none !important} "
    ".mc-step-waiting .mc-step-title{color:var(--body-text-color-subdued)} "
    ".mc-step-running .mc-step-title{font-weight:600} "
    ".mc-step-icon{width:18px;height:18px;box-sizing:border-box;border-radius:50%;"
    "border:2px solid var(--border-color-primary);display:inline-flex;align-items:center;"
    "justify-content:center;color:#fff;font-size:12px;font-weight:700;line-height:1} "
    ".mc-step-running .mc-step-icon{border-right-color:var(--color-accent);"
    "border-bottom-color:var(--color-accent);border-left-color:var(--color-accent);"
    "border-top-color:transparent;animation:mc-turn .9s linear infinite} "
    ".mc-step-done .mc-step-icon{background:#16a34a;border-color:#16a34a} "
    '.mc-step-done .mc-step-icon::after{content:"";width:4px;height:8px;border:solid #fff;'
    "border-width:0 2px 2px 0;transform:translateY(-1px) rotate(45deg)} "
    ".mc-step-failed .mc-step-icon{background:#dc2626;border-color:#dc2626} "
    '.mc-step-failed .mc-step-icon::after{content:"!"} '
    ".mc-step-detail,.mc-step .mc-bar,.mc-step-note{grid-column:2 / 4} "
    ".mc-step-note{font-size:0.85rem;color:var(--body-text-color-subdued)} "
    ".mc-bar{display:block;position:relative;height:6px;border-radius:999px;overflow:hidden;"
    "background:var(--border-color-primary)} "
    ".mc-bar>span{display:block;height:100%;border-radius:999px;"
    "background:var(--color-accent)} "
    ".mc-bar-moving>span{width:35%;animation:mc-sweep 1.6s ease-in-out infinite} "
    "@keyframes mc-sweep{from{transform:translateX(-100%)}to{transform:translateX(290%)}} "
    "@keyframes mc-turn{to{transform:rotate(360deg)}} "
    "@media (prefers-reduced-motion:reduce){.mc-bar-moving>span{animation:none;width:100%;"
    "opacity:0.35}.mc-step-running .mc-step-icon{animation:none;"
    "border-top-color:var(--color-accent)}} "
    ".mc-sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;"
    "clip:rect(0,0,0,0);white-space:nowrap;border:0}"
)
EXPIRED = "Your upload is no longer on the server. Upload it again."
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
    picked: list[str] | None,
    preset: str,
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
        checks=picked,
        args=catalog.preset_args(preset),
        hosted=True,
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
        progress(0.0, data_status(job, begun))
    return started.finish(job)


def analyse_package(
    source: str,
    preset: str,
    workdir: str,
    limits: pk.Limits,
    progress: Callable[[float, str], None],
) -> pk.PackageAnalysis:
    """One hosted run on a data package (an uploaded archive or a link), in its own process.

    The server makes ``workdir`` and removes it, so a run ended at the time limit leaves no
    report behind. Everything this run downloads and unpacks goes into it as well: a process
    that is killed cleans up nothing itself. The concepts of the data columns come from rules
    (the local classifier's model is not for a shared server).
    """
    import tempfile

    from metacheck.datapackage import is_link

    if not is_link(source) and not Path(source).is_file():
        raise UserError(EXPIRED)
    tempfile.tempdir = workdir
    return pk.check_package_source(
        source,
        preset,
        report_dir=Path(workdir) / "report",
        local_classifier=False,
        progress=progress,
        limits=limits,
    )


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


def coloured(frame: Any, column: str = "Status") -> Any:
    """*frame* for a table, the cells of its status column coloured by their traffic light.

    The word stays in the cell, so the colour is never the only signal. Colours are those
    of the paper page's results.
    """
    return frame.style.apply(
        lambda col: [_light_style(pk.status_light(str(cell)) or "") for cell in col],
        subset=[column],
    )


def package_summary(analysis: pk.PackageAnalysis) -> str:
    """The line above the results of a data package: its name, the time and the counts."""
    text = (
        f"Checked **{plain_markdown(analysis.name)}** in {analysis.seconds:.1f} s: "
        f"{pk.counts_line(analysis.checklist)}."
    )
    if analysis.note:
        text += f"\n\n{analysis.note}"  # our own text, not the package's
    return text


def _summary(analysis: Analysis) -> str:
    n = len(analysis.rows)
    return (
        f"Ran {n} check{'' if n == 1 else 's'} on **{analysis.name}** in {analysis.seconds:.1f} s."
    )


def default_reader(remembered: bool = False) -> str:
    """The reader a page starts with: bibr where this computer supplies the bibr key
    (``SCIVRS_API_KEY``, as a hosted server does) or the setup saved a key or chose bibr on
    this computer, else GROBID, which needs no key."""
    if os.environ.get(bibr.KEY_ENV, "").strip() or (remembered and bibr.setup_chose_bibr()):
        return "bibr"
    return "grobid"


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


def data_status(job: DataJob, started: float) -> str:
    """What a shared server's run says while its data check runs: the progress text of the run
    (the page draws it as text, in the step of the data check)."""
    text = f"Checking the shared data files ({time.monotonic() - started:.0f} s)."
    return f"{text} {job.message}" if job.message else text


def box_kind(box: Any) -> str | None:
    """What the box holds: a paper (:data:`PAPER_MODE`), a data package (:data:`PACKAGE_MODE`)
    or nothing. A paper is always a file: a text is the link or the folder of a package."""
    text, files = pk.box_parts(box)
    if files:
        return PAPER_MODE if files[0].lower().endswith(pk.PAPER_SUFFIXES) else PACKAGE_MODE
    return PACKAGE_MODE if text.strip() else None


def ready_line(entry: pk.Entry) -> str:
    """The line under the box: what it holds, and how that will be checked."""
    if entry.kind == pk.LINK:
        name = re.sub(r"^https?://(www\.)?", "", entry.value.strip(), flags=re.IGNORECASE)
    else:
        name = Path(entry.value).name if entry.kind != pk.FOLDER else entry.value.strip()
    return READY[entry.kind].format(name=plain_markdown(name.rstrip("/")[:200]))


def _gradio_error(exc: BaseException, failed: str) -> gr.Error:
    """What the page says about an error of a run: its own words for one the person can act on."""
    if isinstance(exc, gr.Error):
        return exc
    if isinstance(exc, UserError):
        return gr.Error(str(exc), print_exception=False)
    return gr.Error(failed)


@dataclass(frozen=True)
class Pending:
    """A data check that runs for one browser session, after its fast results are shown."""

    run: Run
    job: DataJob
    steps: Steps
    #: set when the person pressed Stop (and not when a new check took over)
    stopped: threading.Event


def build_app(
    sessions: Sessions | None = None,
    hosted: hosting.HostedConfig | None = None,
    *,
    remember_keys: bool = True,
    start_preset: str | None = None,
) -> gr.Blocks:
    """The page. ``hosted`` adds the limits and texts of the shared server.

    ``start_preset`` is the preset the page starts from, when the page offers it: a paper
    preset ticks its checks, a data package preset opens the page on Data package (and, in the
    local app, is chosen on the package page too, see
    :func:`~metacheck.app.ui_package.add_package_page`).

    ``remember_keys=False`` never saves a bibr key and never offers one that was saved; a
    shared server (``hosted``) never does either.

    While a check runs, the page draws its steps (:mod:`metacheck.app.progress`): the button's
    function runs the check in the background and draws them every :data:`TICK` seconds.
    """
    from metacheck._version import __version__

    sessions = sessions or Sessions()
    pending: dict[str, Pending] = {}
    jobs = hosting.JobRunner(hosted.job_timeout) if hosted else None
    shared = hosted is not None
    remember_keys = remember_keys and not shared

    def shown(
        panel: Any = None,
        parts: tuple[Any, ...] | None = None,
        status: Any = None,
        stop: Any = None,
        box: Any = None,
    ) -> tuple[Any, ...]:
        """What a button's function sends: the steps, the six parts of the results, the status
        line, the Stop button and, on a shared server, the box. ``None`` leaves a part as it is."""

        def keep(value: Any) -> Any:
            return gr.skip() if value is None else value

        middle = parts if parts is not None else (gr.skip(),) * 6
        tail = (keep(box),) if shared else ()
        return (keep(panel), *middle, keep(status), keep(stop), *tail)

    def drawn(steps: Steps, *, start: bool = False, stop: bool = False) -> tuple[Any, ...]:
        """The steps as they are now. A run's first drawing hides the results of the run before."""
        parts = (gr.update(visible=False), *(gr.skip(),) * 5) if start else None
        return shown(steps.html(), parts, stop=gr.update(visible=stop))

    def watch(
        steps: Steps,
        work: Callable[[], Any],
        failed: str,
        cancel: threading.Event | None = None,
    ) -> Generator[tuple[Any, ...], None, Any]:
        """Run *work* in the background and draw *steps* until it ends; its result.

        When it fails, the step it failed in says so and a :class:`gradio.Error` says why. When
        the person leaves the page, ``cancel`` is set (a run in its own process ends then).
        """
        job = Background(work).start()
        ended = False
        try:
            yield drawn(steps, start=True)
            while not job.wait(TICK):
                yield drawn(steps)
            ended = True
        finally:
            if not ended and cancel is not None:
                cancel.set()
        if job.error is not None:
            error = _gradio_error(job.error, failed)
            steps.fail(error.message)
            yield drawn(steps)
            if error is job.error:
                raise error
            raise error from job.error
        return job.result

    def guarded(run: Iterator[tuple[Any, ...]], failed: str) -> Iterator[tuple[Any, ...]]:
        """*run*, with its errors in the person's words. An error before the run drew its steps
        also takes away the panel the button drew in the browser."""
        try:
            yield from run
        except gr.Error:
            raise  # the steps say where it failed
        except UserError as exc:
            yield shown("")
            raise gr.Error(str(exc), print_exception=False) from exc
        except Exception as exc:
            yield shown("")
            raise gr.Error(failed) from exc

    def download_link(report: Path, page: str, session: str) -> str:
        """The link to the report: on a shared server the page itself, else the file."""
        if shared:
            return hosting.data_link(report.name, protect(page))
        return _download_link(session, report)

    def page(analysis: Analysis, session: str) -> tuple[Any, ...]:
        """What the results show for a paper: the checklist of a data package is hidden."""
        return (
            gr.update(visible=True),
            _summary(analysis),
            table_value(analysis.rows),
            gr.update(visible=False, value=None),
            report_frame(analysis.html),
            download_link(analysis.report_path, analysis.html, session),
        )

    def package_page(analysis: pk.PackageAnalysis, session: str) -> tuple[Any, ...]:
        """What the results show for a data package: its checks, and its checklist as well."""
        return (
            gr.update(visible=True),
            package_summary(analysis),
            table_value(list(analysis.rows)),
            gr.update(visible=True, value=coloured(analysis.checklist)),
            report_frame(analysis.html),
            download_link(analysis.report_path, analysis.html, session),
        )

    def done(parts: tuple[Any, ...], status: str = "", box: Any = None) -> tuple[Any, ...]:
        """The results of a run that has ended: the steps go, the results come. ``box`` is
        what the box holds then (``None``: what it held)."""
        return shown("", parts, status, gr.update(visible=False), box)

    def run_hosted(
        path: str,
        online: bool,
        data: bool,
        reader: str,
        key: str,
        picked: list[str] | None,
        preset: str,
        steps: Steps,
        box: Any = None,
    ) -> Iterator[tuple[Any, ...]]:
        """The whole run, data check included, in its own process under the time limit."""
        assert jobs is not None and hosted is not None
        budget, grace = data_budget(hosted.job_timeout)
        cancel = threading.Event()

        def work() -> Analysis:
            # made here, not in the run's process: a process ended at the limit cleans up nothing
            with scratch_dir() as folder:
                return jobs.run(
                    analyse_upload,
                    path,
                    online,
                    data,
                    reader,
                    key,
                    folder,
                    time.time() + budget,
                    grace,
                    picked,
                    preset,
                    # the demo paper is part of the package: only an upload is deleted
                    upload=None if Path(path).resolve() == _demo_path() else path,
                    on_timeout=lambda: gr.Error(hosting.TOO_SLOW, print_exception=False),
                    on_progress=steps,
                    cancel=cancel,
                )

        analysis = yield from watch(steps, work, FAILED, cancel)
        yield done(page(analysis, ""), box=box)

    def leave(session: str) -> None:
        """End the data check this session has running (a new check or Stop replaces it)."""
        old = pending.pop(session, None)
        if old is not None:
            old.job.stop()

    def run(
        path: str,
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        picked: list[str] | None,
        preset: str,
        session: str,
        box: Any = None,
    ) -> Iterator[tuple[Any, ...]]:
        """Check the paper at *path*. On this computer the click ends with the fast results and
        the data check goes on in the background (see ``follow``). ``box`` is what the box
        holds after the run (``None``: what it held)."""
        use_bibr = reader == "bibr"
        steps = paper_steps(path, reader, data, data_in_run=shared)
        if shared:
            key = bibr.resolve_key(key, remembered=False) if use_bibr else ""
            yield from run_hosted(path, online, data, reader, key, picked, preset, steps, box)
            return
        leave(session)
        bibr_key = bibr.resolve_key(key, remembered=remember_keys) if use_bibr else ""

        def work() -> Run:
            with scratch_dir() as folder:
                return begin(
                    path,
                    online=online,
                    data=data,
                    workdir=Path(folder),
                    report_dir=sessions.folder(session),
                    progress=steps,
                    pdf=reader,
                    bibr_key=bibr_key,
                    checks=picked,
                    args=catalog.preset_args(preset),
                )

        started: Run = yield from watch(steps, work, FAILED)
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
            yield done(page(started.analysis, session))
            return
        job = started.data_job()
        pending[session] = Pending(started, job, steps, threading.Event())
        job.start()
        steps.data(job.message)
        yield shown(steps.html(), page(started.analysis, session), "", gr.update(visible=True))

    def run_package(
        source: str | Path,
        kind: str,
        preset: str,
        classifier: bool,
        session: str,
        upload: str | Path | None = None,
        box: Any = None,
    ) -> Iterator[tuple[Any, ...]]:
        """A data package: an archive (uploaded, or the demo's), a repository link or a folder.
        On a shared server ``upload`` is deleted after the run. ``box`` is what the box holds
        after the run (``None``: what it held)."""
        ref = pk.valid_preset(preset, shared)
        steps = package_steps(str(source), kind)
        cancel = threading.Event()
        if shared:
            assert jobs is not None

            def work() -> pk.PackageAnalysis:
                # everything the run's process makes is removed with this folder; a link is
                # fetched inside the process (without any token of this server)
                with scratch_dir() as folder:
                    return jobs.run(
                        analyse_package,
                        str(source),
                        ref,
                        folder,
                        pk.HOSTED,
                        upload=upload,
                        on_timeout=lambda: gr.Error(
                            hosting.TOO_SLOW_PACKAGE, print_exception=False
                        ),
                        on_progress=steps,
                        cancel=cancel,
                    )

            analysis = yield from watch(steps, work, pk.FAILED, cancel)
            yield done(package_page(analysis, ""), box=box)
            return
        leave(session)

        def work_here() -> pk.PackageAnalysis:
            return pk.check_package_source(
                source,
                ref,
                report_dir=sessions.folder(session),
                local_classifier=classifier,
                progress=steps,
                limits=pk.LOCAL,
            )

        analysis = yield from watch(steps, work_here, pk.FAILED)
        yield done(package_page(analysis, session))

    def follow(request: gr.Request) -> Iterator[tuple[Any, ...]]:
        """The data check of this session: draw its step while it runs, then show the result."""
        session = request.session_hash or ""
        entry = pending.get(session)
        if entry is None:
            yield shown()
            return
        job = entry.job
        try:
            while not job.wait(TICK):
                if pending.get(session) is not entry:
                    break  # Stop, or a new check, took over
                entry.steps.data(job.message)
                yield drawn(entry.steps, stop=True)
            mine = pending.get(session) is entry
        finally:
            if pending.get(session) is entry:
                del pending[session]
            if not job.done:
                job.stop()  # the person left
        if entry.stopped.is_set():
            # the last word on the steps is this function's, so that none comes after Stop's
            yield shown("", stop=gr.update(visible=False))
            return
        if not mine:
            return  # a new check took over: it draws its own steps
        if job.stopped:
            yield shown("", status=STOPPED, stop=gr.update(visible=False))
            return
        final = entry.run.finish(job)
        failed = job.error is not None or (
            job.output is not None and job.output.traffic_light == "fail"
        )
        yield done(page(final, session), DATA_FAILED if failed else "")

    def on_check(
        box: Any,
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        picked: list[str] | None,
        preset: str,
        package_preset: str,
        classifier: bool,
        request: gr.Request,
    ) -> Iterator[tuple[Any, ...]]:
        """The box holds a paper, or a data package (an archive, a link or a folder)."""
        session = request.session_hash or ""
        _text, uploads = pk.box_parts(box)

        # on a shared server the upload is deleted after its run, so the box is emptied too
        after = EMPTY_BOX if shared else None

        def checking() -> Iterator[tuple[Any, ...]]:
            entry = pk.classify_entry(box, hosted=shared)
            if entry.kind == pk.PAPER:
                yield from run(
                    entry.value,
                    online,
                    data,
                    reader,
                    key,
                    remember,
                    picked,
                    preset,
                    session,
                    after,
                )
                return
            source = pk.resolve_entry(entry)
            yield from run_package(
                source,
                entry.kind,
                package_preset,
                classifier,
                session,
                upload=source if entry.kind == pk.ARCHIVE else None,
                box=after,
            )

        failed = pk.FAILED if box_kind(box) == PACKAGE_MODE else FAILED
        try:
            yield from guarded(checking(), failed)
        finally:
            if shared:  # whatever came of the run, an upload does not stay on a shared server
                for upload in uploads:
                    hosting.remove_upload(upload)

    def on_demo(
        online: bool,
        data: bool,
        reader: str,
        key: str,
        remember: bool,
        picked: list[str] | None,
        preset: str,
        _package_preset: str,
        _classifier: bool,
        request: gr.Request,
    ) -> Iterator[tuple[Any, ...]]:
        from metacheck.papers.io import demofile

        session = request.session_hash or ""
        yield from guarded(
            run(
                str(demofile("json")), online, data, reader, key, remember, picked, preset, session
            ),
            FAILED,
        )

    def on_demo_package(
        preset: str, classifier: bool, request: gr.Request
    ) -> Iterator[tuple[Any, ...]]:
        """The demo data package, as an archive like one that was uploaded."""
        session = request.session_hash or ""
        with tempfile.TemporaryDirectory(prefix="metacheck-demo-package-") as folder:
            archive = demo_package_archive(Path(folder))
            yield from guarded(
                run_package(archive, pk.ARCHIVE, preset, classifier, session), pk.FAILED
            )

    def on_stop(request: gr.Request) -> tuple[Any, ...]:
        session = request.session_hash or ""
        entry = pending.get(session)
        if entry is not None:
            entry.stopped.set()  # first, so that the data check's function sees why it ended
        leave(session)
        return "", STOPPED, gr.update(visible=False)

    def on_reader(reader: str) -> tuple[Any, ...]:
        return (
            gr.update(visible=reader == "bibr"),
            privacy_text(reader, shared),
            key_note(remember_keys, shared),
            gr.update(visible=remember_keys and bool(bibr.load_key())),
        )

    def on_online(online: bool, picked: list[str] | None) -> list[str]:
        """The online box ticks the online checks, or unticks them."""
        kept = [n for n in picked or [] if n not in catalog.ONLINE]
        return catalog.chosen_checks([*kept, *catalog.ONLINE] if online else kept, shared, extra)

    def on_preset(preset: str, online: bool) -> tuple[Any, ...]:
        """A preset ticks its checks (and the data box, if it has the data check)."""
        names, with_data = catalog.preset_checks(preset, online)
        names = catalog.chosen_checks(names, shared, extra)
        return names, with_data, any(n in catalog.ONLINE for n in names)

    def on_forget() -> tuple[Any, ...]:
        bibr.forget_key()
        return key_note(remember_keys, shared), gr.update(visible=False)

    def on_mode(mode: str) -> tuple[Any, ...]:
        """The switch at the top: the box, the options and the demo button of its mode."""
        package = mode == PACKAGE_MODE
        if package:
            box = gr.update(
                label=PACKAGE_BOX_LABEL,
                placeholder=PACKAGE_BOX_PLACEHOLDER,
                info=PACKAGE_BOX_INFO if shared else PACKAGE_BOX_INFO_LOCAL,
            )
        else:
            box = gr.update(label=BOX_LABEL, placeholder=BOX_PLACEHOLDER, info=BOX_INFO)
        return (
            box,
            gr.update(visible=not package),
            gr.update(visible=package),
            gr.update(visible=not package),
            gr.update(visible=package),
        )

    def on_mode_input(mode: str, box: Any) -> tuple[Any, ...]:
        """The person moved the switch: a paper leaves the box of a data package, and the
        other way round."""
        kind = box_kind(box)
        if kind is None or kind == mode:
            return gr.skip(), gr.skip()
        if shared:  # the file leaves the server too
            for upload in pk.box_parts(box)[1]:
                hosting.remove_upload(upload)
        return EMPTY_BOX, ""

    def on_box(box: Any, mode: str) -> tuple[Any, ...]:
        """The switch follows what the box holds, and a line says how it will be checked.

        A file that cannot be checked is named at once; a text is a link or a folder still
        being typed, so it is answered when Check is pressed."""
        kind = box_kind(box)
        if kind is None:
            return gr.skip(), ""
        try:
            line = ready_line(pk.classify_entry(box, hosted=shared))
        except UserError as exc:
            line = str(exc) if pk.box_parts(box)[1] else ""
        if kind != mode:
            line = f"{line} {SWITCHED[kind]}".strip()
            return kind, line
        return gr.skip(), line

    # installed packs' modules are offered on this computer only: a shared server runs built-ins
    extra = () if shared else tuple(catalog.pack_checks())
    presets = catalog.paper_presets(shared)
    first_preset = (
        start_preset if start_preset in {ref for _, ref in presets} else catalog.APP_DEFAULT
    )
    first_checks, first_data = catalog.preset_checks(first_preset)
    first_checks = catalog.chosen_checks(first_checks, shared, extra)
    package_choices = pk.package_presets(shared)
    first_package = (
        start_preset
        if start_preset in {ref for _, ref in package_choices}
        else pk.valid_preset(None, shared)
    )
    # a preset given at the start opens its mode, and the checks of a paper preset are shown
    first_mode = PACKAGE_MODE if first_package == start_preset else PAPER_MODE
    package_first = first_mode == PACKAGE_MODE

    blocks_kwargs = (
        {**BLOCKS_KWARGS, "delete_cache": hosting.sweep(hosted.job_timeout)}
        if hosted
        else BLOCKS_KWARGS
    )
    with gr.Blocks(**blocks_kwargs) as app:
        if not shared:
            gr.Navbar(main_page_name=MAIN_PAGE_NAME)
        gr.HTML(header(__version__), elem_id="mc-header", js_on_load=None)
        with gr.Row(elem_id="mc-mode-row"):
            mode = gr.Radio(
                MODES, value=first_mode, label=MODE_LABEL, show_label=False, elem_id="mc-mode"
            )
        # one box for everything: a paper, the archive of a data package, the link of a
        # repository (or, on this computer, the path of a folder); the page tells them apart
        # and the switch above follows
        upload = gr.MultimodalTextbox(
            label=PACKAGE_BOX_LABEL if package_first else BOX_LABEL,
            info=(PACKAGE_BOX_INFO if shared else PACKAGE_BOX_INFO_LOCAL)
            if package_first
            else BOX_INFO,
            placeholder=PACKAGE_BOX_PLACEHOLDER if package_first else BOX_PLACEHOLDER,
            file_types=BOX_FILE_TYPES,
            file_count="single",
            sources=["upload"],
            submit_btn=False,
            lines=2,
            max_lines=4,
            elem_id="mc-box",
        )
        found = gr.Markdown("", elem_id="mc-found")
        with gr.Column(visible=not package_first) as paper_options:
            if hosted:
                gr.Markdown(hosting.HOSTED_NOTE)
            first = default_reader(remember_keys)
            reader = gr.Radio(READERS, value=first, label="Read PDFs with")
            with gr.Group(visible=first == "bibr") as key_group:
                # the key of this computer wins over a typed one, so there is nothing to type
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
                value=any(n in catalog.ONLINE for n in first_checks),
            )
            data = gr.Checkbox(label=DATA_LABEL, value=first_data)
            # a preset chosen when the app was started is shown open, so the person sees it
            with gr.Accordion(PICKER_TITLE, open=first_preset != catalog.APP_DEFAULT):
                gr.Markdown(here(PICKER_INTRO, shared))
                preset = gr.Dropdown(
                    presets,
                    value=first_preset,
                    label="Start from",
                    info=here(PRESET_INFO, shared),
                    visible=len(presets) > 1,
                )
                picked = gr.CheckboxGroup(
                    catalog.choices(shared, extra),
                    value=first_checks,
                    label="Checks to run",
                )
        with gr.Column(visible=package_first) as package_options:
            gr.Markdown(hosting.PACKAGE_NOTE if hosted else PRIVACY_PACKAGE)
            package_preset = gr.Dropdown(
                package_choices,
                value=first_package,
                label=PACKAGE_PRESET_LABEL,
                info=here(PACKAGE_PRESET_INFO, shared),
            )
            # a shared server names the concepts by rules: the model is not for a server, so
            # the box is not shown there and its value is not used
            classifier = gr.Checkbox(
                label=CLASSIFIER_LABEL,
                info=here(
                    CLASSIFIER_INFO if pk.classifier_installed() else CLASSIFIER_MISSING_INFO,
                    shared,
                ),
                value=True,
                visible=not shared,
            )
        with gr.Row():
            check = gr.Button("Check", variant="primary", size="lg")
            demo = gr.Button("Try the demo paper", size="lg", visible=not package_first)
            demo_package = gr.Button(DEMO_PACKAGE_BUTTON, size="lg", visible=package_first)
        # the steps of a run, drawn while it runs; empty (and so not shown) otherwise
        panel = gr.HTML("", elem_id="mc-run-panel", js_on_load=None, padding=False)
        stop = gr.Button("Stop the data check", visible=False, size="sm")
        with gr.Column(visible=False) as results:
            summary = gr.Markdown()
            status = gr.Markdown(elem_id="mc-status")
            table = gr.Dataframe(
                headers=["Check", "Status", "Result"],
                datatype="str",
                label="Results",
                interactive=False,
                wrap=True,
                column_widths=["22%", "12%", "66%"],
            )
            checklist = gr.Dataframe(
                headers=pk.CHECKLIST_COLUMNS,
                datatype="str",
                label="Checklist of the data package",
                interactive=False,
                wrap=True,
                column_widths=["18%", "22%", "10%", "50%"],
                visible=False,
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

        outputs = [panel, results, summary, table, checklist, frame, download, status, stop]
        inputs = [online, data, reader, key, remember, picked, preset, package_preset, classifier]
        keys = [key_group, privacy, note, forget]
        # hosted: one shared limit, since each button on its own would allow the full
        # number of runs; the field is cleared because the upload is deleted after its run
        limits: dict[str, Any] = (
            {"concurrency_id": "paper", "concurrency_limit": hosting.MAX_RUNS} if hosted else {}
        )
        quiet: dict[str, Any] = {"api_visibility": "private", "show_progress": "hidden"}
        sent = [*outputs, upload] if shared else outputs
        for button in (check, demo, demo_package):
            # the browser draws the panel at once: the run draws its own steps when it starts
            button.click(None, None, panel, js=START_JS, **quiet)
        clicked = [
            check.click(on_check, [upload, *inputs], sent, **quiet, **limits),
            demo.click(on_demo, inputs, sent, **quiet, **limits),
            demo_package.click(
                on_demo_package, [package_preset, classifier], sent, **quiet, **limits
            ),
        ]
        for event in clicked:
            event.success(on_reader, reader, keys, concurrency_limit=None, **quiet)
        if not shared:
            # a click ends with the fast results; the data check goes on in a second event,
            # so that the same button works again and a new check or Stop can end it. On a
            # shared server the data check is part of the run and its time limit.
            for event in clicked[:2]:
                event.success(follow, None, outputs, concurrency_limit=None, **quiet)
            # Stop ends the job, and the following event then ends within a second by
            # itself. Cancelling that event as well races with its end inside Gradio,
            # which then prints a KeyError traceback in the person's terminal.
            stop.click(on_stop, None, [panel, status, stop], concurrency_limit=None, **quiet)
        # the switch and the box follow each other; nothing here waits for a running check
        ui: dict[str, Any] = {**quiet, "concurrency_limit": None}
        mode.change(
            on_mode, mode, [upload, paper_options, package_options, demo, demo_package], **ui
        )
        mode.input(on_mode_input, [mode, upload], [upload, found], **ui)
        upload.change(on_box, [upload, mode], [mode, found], trigger_mode="always_last", **ui)
        reader.change(on_reader, reader, keys, **ui)
        # .input, not .change: a preset that sets the online box must not untick its checks
        online.input(on_online, [online, picked], picked, **ui)
        preset.input(on_preset, [preset, online], [picked, data, online], **ui)
        if remember_keys:  # a shared server has no saved key, and nobody may remove one
            forget.click(on_forget, None, [note, forget], **ui)
    if not shared:
        from metacheck.app.ui_package import add_package_page

        # a folder is named by a path on this computer: only the local app has the page
        add_package_page(app, sessions, start_preset=start_preset)
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


def demo_package_archive(folder: Path) -> Path:
    """The demo data package as a zip archive in *folder*, its files in one top folder as
    when a person zips a folder."""
    import zipfile
    from importlib import resources

    root = resources.files("metacheck.resources.demos").joinpath(DEMO_PACKAGE)
    target = folder / f"{DEMO_PACKAGE}.zip"

    def add(archive: zipfile.ZipFile, item: Any, name: str) -> None:
        for child in sorted(item.iterdir(), key=lambda c: c.name):
            inside = f"{name}/{child.name}"
            if child.is_dir():
                add(archive, child, inside)
            else:
                archive.writestr(inside, child.read_bytes())

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        add(archive, root, DEMO_PACKAGE)
    return target


def theme() -> Any:
    return gr.themes.Default()
