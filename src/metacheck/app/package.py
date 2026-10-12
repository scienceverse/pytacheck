"""Check a data package: the code behind the "Check a data package" page.

A data package is the folder (or the zip of it) of data, code and documentation that
comes with a paper. This module finds the package the person named, runs the
``datapackage`` checks on it with :func:`metacheck.datapackage.report_package` and turns
the result into two tables: one row per check, and the checklist (one row per
requirement). It does not import gradio, and it never uploads anything: the checks run on
this computer, and no check looks anything up online. The one download is the local
classifier's model (about 840 MB, once), which is on by default and needs the ``concepts``
extra.

Only the local app offers this. A folder is named by a path on the computer that runs the
app, so a shared server never builds the page. The page reads a folder only inside the
person's home folder, or inside a folder named in ``METACHECK_APP_ROOTS``: another program on
this computer that holds the app's token cookie could otherwise ask it to read any folder
and return the README (see :func:`resolve_source` and ``security.py``).
"""

from __future__ import annotations

import os
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metacheck._env import env_get, env_name
from metacheck.app.run import _RUN_LOCK, Progress, UserError, _plain, protect, report_filename

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "BIGGER_THAN_UPLOAD",
    "CHECKLIST_COLUMNS",
    "CHECK_COLUMNS",
    "MAX_FILES",
    "MAX_UNPACKED_BYTES",
    "NO_CLASSIFIER",
    "ROOTS_ENV",
    "UPLOAD_LIMIT",
    "PackageAnalysis",
    "allowed_roots",
    "check_package_source",
    "checklist_table",
    "checks_table",
    "classifier_installed",
    "counts_line",
    "package_name",
    "package_presets",
    "resolve_source",
    "status_light",
]

#: Limits for a zip or tar the person uploads, lower than the library's (20 GB, 200,000
#: files): the size and the number of files the archive says it holds, and what is really
#: written while it is unpacked, are both counted.
MAX_UNPACKED_BYTES = 5 * 1024**3
MAX_FILES = 100_000

#: what the local app accepts as an upload (the shared server keeps ``MOUNT_KWARGS``' 50 MB)
UPLOAD_LIMIT = "1gb"
BIGGER_THAN_UPLOAD = "A zip can be up to 1 GB. For a bigger package, give its folder instead."
NO_SOURCE_FOLDER = "Type the path of the package's folder first."
NO_SOURCE_ZIP = "Upload a zip of the package first, or use a folder."
NOT_A_FOLDER = (
    "No folder was found at that path. Give the path of the package's folder on this computer."
)
#: the variable that adds folders to the ones the page may read (see :func:`allowed_roots`)
ROOTS_ENV = env_name("APP_ROOTS")
EXPIRED = "Your upload is no longer on the server. Upload the zip again."
BAD_ARCHIVE = "This file is not a zip or tar archive. Upload a zip, or give the package's folder."
FAILED = "Something went wrong while checking this package."
NO_CHECKS = "This preset runs no checks on a package."
#: What the page says (as Markdown) when the classifier is asked for and the ``concepts`` extra
#: is missing.
NO_CLASSIFIER = (
    "The local classifier is not installed, so the concepts of the data columns come from "
    'rules only. To get it, install the concepts extra: `pip install "metacheck[concepts]"`. '
    "Its model, about 840 MB, is downloaded the first time it is used."
)

#: The two ways to name a package (the page's choice).
FOLDER = "folder"
ZIP = "zip"
SOURCES = [("A folder on this computer", FOLDER), ("A zip file", ZIP)]

CHECK_COLUMNS = ["Check", "Status", "Result"]
CHECKLIST_COLUMNS = ["Check", "Item", "Status", "Detail"]

#: checklist status -> the word shown, and the traffic light of a whole check -> the same words
STATUS_WORDS = {
    "fail": "Fail",
    "warn": "Warning",
    "pass": "Pass",
    "manual": "Manual",
    "na": "Not applicable",
}
LIGHT_WORDS = {
    "red": "Fail",
    "yellow": "Warning",
    "green": "Pass",
    "info": "Info",
    "na": "Not applicable",
    "fail": "Did not run",
}
#: the word shown -> the traffic light word the result cells of the paper page use (a word
#: that is not here, such as "Not applicable", stays uncoloured; the word is always shown)
STATUS_LIGHTS = {
    "Fail": "Red",
    "Warning": "Yellow",
    "Manual": "Yellow",
    "Pass": "Green",
    "Did not run": "Failed",
}


def status_light(word: str) -> str | None:
    """The traffic light word (``"Red"``, ...) that colours a status word, or ``None``."""
    return STATUS_LIGHTS.get(word)


# -- what to check -----------------------------------------------------------------------


def package_presets() -> list[tuple[str, str]]:
    """``(label, ref)`` of the presets that check a data package, for the page's choice.

    These are the presets of every installed pack and of the person's config whose modules
    include a check of the ``datapackage`` pack (``datapackage::default`` and any preset
    that extends it), found through the pack registry. A preset for papers is not offered.
    ``datapackage::default`` comes first.
    """
    from metacheck.datapackage import DEFAULT_PRESET
    from metacheck.module import ModuleError
    from metacheck.presets import expand, preset_list

    found: list[tuple[str, str]] = []
    table = preset_list()
    for ref, description in zip(table["ref"], table["description"], strict=True):
        ref = str(ref)
        try:
            modules = [str(m) for m, _ in expand(ref)]
        except ModuleError:
            continue  # a preset that cannot run is not offered
        if any(m.startswith("datapackage::") for m in modules):
            text = str(description) if description == description and description else ""
            found.append((f"{ref}: {text}" if text else ref, ref))
    found.sort(key=lambda item: item[1] != DEFAULT_PRESET)
    return found


# -- finding the package -----------------------------------------------------------------


def _clean_path(text: str) -> str:
    """A pasted path without the quotes a file manager puts around it."""
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def allowed_roots() -> list[Path]:
    """The folders the page may read from, each with its links followed.

    The person's home folder, and the folders in ``METACHECK_APP_ROOTS`` (separated by
    ``os.pathsep``: ``:`` on Linux and macOS, ``;`` on Windows).
    """
    roots: list[Path] = []
    try:
        roots.append(Path.home())
    except RuntimeError:  # no home folder is known: then only the variable allows anything
        pass
    listed = (env_get("APP_ROOTS") or "").split(os.pathsep)
    roots.extend(Path(_clean_path(item)).expanduser() for item in listed if item.strip())
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(root.resolve())  # the home folder can be a link, too
        except (OSError, RuntimeError, ValueError):
            continue
    return resolved


def _outside_message(roots: list[Path]) -> str:
    where = " or ".join(f"{root}" for root in roots) or "a folder you name in the settings"
    return (
        f"That folder is outside the places this page may read: {where}. The page reads only "
        "there, so that no other program on this computer can use it to read the rest of your "
        "files. Move or copy the package into your home folder, or upload a zip of it. To allow "
        f"another folder, start the app with {ROOTS_ENV} set to it."
    )


def resolve_source(kind: str, folder: str | None, upload: str | None) -> Path:
    """The package to check: the folder that was typed, or the uploaded archive.

    A folder must lie inside the person's home folder or a folder of ``METACHECK_APP_ROOTS``.
    The path is resolved first (``~`` expanded, ``..`` removed, links followed) and the
    folder that comes out is the one tested, so a link inside home that points out of it is
    refused. The test comes before the one for existence, so the answer never says whether
    a folder outside exists. An upload is the app's own file and is not tested.

    Raises :class:`~metacheck.app.run.UserError` when nothing was given, the folder is not
    there or is outside the allowed folders, or the upload is gone or is not an archive.
    """
    from metacheck.datapackage import is_archive

    if kind == ZIP:
        if not upload:
            raise UserError(NO_SOURCE_ZIP)
        path = Path(upload)
        if not path.is_file():
            raise UserError(EXPIRED)
        if not is_archive(path):
            raise UserError(BAD_ARCHIVE)
        return path
    text = _clean_path(folder or "")
    if not text:
        raise UserError(NO_SOURCE_FOLDER)
    try:
        path = Path(text).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):  # an unknown ~user, a link loop, a bad name
        raise UserError(NOT_A_FOLDER) from None
    roots = allowed_roots()
    if not any(path.is_relative_to(root) for root in roots):
        raise UserError(_outside_message(roots))
    if not path.is_dir():
        raise UserError(NOT_A_FOLDER)
    return path


def classifier_installed() -> bool:
    """Whether ``data_check``'s local concept classifier can run here (the ``concepts`` extra)."""
    from metacheck.datacheck.concepts import classifier_available

    return classifier_available()


# -- the tables --------------------------------------------------------------------------


def _frame(rows: list[list[str]], columns: list[str]) -> pd.DataFrame:
    import pandas as pd

    return pd.DataFrame(rows, columns=columns, dtype="string").astype(object)


def _has_checklist(output: Any) -> bool:
    return hasattr(getattr(output, "extras", {}).get("checklist"), "to_dict")


def _result_text(output: Any) -> str:
    """One line on what a check found; for a check that did not run, why."""
    if output.traffic_light == "fail":
        return _plain(output.report)[:300] or _plain(output.summary_text)
    return _plain(output.summary_text)


def checks_table(outputs: list[Any]) -> pd.DataFrame:
    """One row per check: its title, its traffic light as a status word, and a summary."""
    rows = []
    for out in outputs:
        light = str(out.traffic_light)
        rows.append(
            [str(out.title or ""), LIGHT_WORDS.get(light, light.capitalize()), _result_text(out)]
        )
    return _frame(rows, CHECK_COLUMNS)


def checklist_table(outputs: list[Any]) -> pd.DataFrame:
    """The checklists of all checks as one table: check, item, status word and detail.

    A check that returns no checklist (the ones that read the data files) has one row of
    its own, with the whole check as its item.
    """
    rows = []
    for out in outputs:
        title = str(out.title or "")
        if not _has_checklist(out):
            word = LIGHT_WORDS.get(str(out.traffic_light), str(out.traffic_light).capitalize())
            rows.append([title, "Whole check", word, _result_text(out)])
            continue
        for item in out.extras["checklist"].fillna("").to_dict(orient="records"):
            status = str(item.get("status", ""))
            rows.append(
                [
                    title,
                    str(item.get("title") or item.get("item") or ""),
                    STATUS_WORDS.get(status, status.capitalize()),
                    _plain(item.get("detail", "")),
                ]
            )
    return _frame(rows, CHECKLIST_COLUMNS)


def counts_line(checklist: pd.DataFrame) -> str:
    """``"2 fail, 3 warnings, ..."``: how many checklist rows there are of each status."""
    if len(checklist) == 0:
        return "no checklist items"
    counts = checklist["Status"].value_counts()
    order = ["Fail", "Warning", "Manual", "Pass", "Not applicable", "Info", "Did not run"]
    parts = [f"{counts[w]} {w.lower()}" for w in order if w in counts]
    return ", ".join(parts)


# -- the run -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageAnalysis:
    """One package after its checks: what the page shows."""

    name: str  # the package's folder or archive name
    preset: str
    checks: pd.DataFrame  # one row per check
    checklist: pd.DataFrame  # one row per requirement
    html: str  # the report page
    report_path: Path
    seconds: float
    note: str = ""  # something the person should know about this run; empty if nothing


def package_name(source: str | os.PathLike[str]) -> str:
    """A name for the package: the folder's, or the archive's name without its ending."""
    from metacheck.datapackage import ARCHIVE_SUFFIXES

    path = Path(source)
    name = path.name or path.resolve().name
    low = name.lower()
    if path.is_file():
        for suffix in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
            if low.endswith(suffix):
                return name[: -len(suffix)] or name
    return name or "package"


def check_package_source(
    source: str | os.PathLike[str],
    preset: str,
    *,
    report_dir: Path,
    local_classifier: bool = True,
    progress: Progress | None = None,
    max_bytes: int = MAX_UNPACKED_BYTES,
    max_files: int = MAX_FILES,
) -> PackageAnalysis:
    """Run *preset* on the data package at *source* and write its report into *report_dir*.

    *source* is a folder, or a zip or tar archive that is unpacked to a private folder
    for the run (members that would leave it, links and device files are skipped; more
    than *max_files* members or *max_bytes* unpacked is refused). ``local_classifier=True``
    (the default, as for ``metacheck package``) leaves the concepts of the data columns to
    ``data_check``'s own default, its local classifier, which downloads a model of about
    840 MB the first time. Without the ``concepts`` extra the concepts come from rules only
    instead, and the result's ``note`` says so. ``False`` names them by rules only, and
    nothing is downloaded.

    The report is saved as ``<name>_report.html`` in *report_dir*, under the same
    Content-Security-Policy as the paper page's report. Raises
    :class:`~metacheck.app.run.UserError` for a package that cannot be opened.
    """
    from metacheck.datapackage import PackageError, report_package
    from metacheck.module import ModuleError

    tick = progress or (lambda _fraction, _text: None)
    start = time.perf_counter()
    source = Path(source)
    name = package_name(source)
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / report_filename(name)
    tick(0.1, "Checking the package")
    use_classifier = local_classifier and classifier_installed()
    note = NO_CLASSIFIER if local_classifier and not use_classifier else ""
    args = None if use_classifier else {"data_check": {"concepts": "rules"}}
    with _RUN_LOCK, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            report = report_package(
                source,
                target,
                "html",
                preset=preset,
                args=args,
                max_bytes=max_bytes,
                max_files=max_files,
            )
        except (PackageError, ValueError, ModuleError) as exc:
            raise UserError(str(exc)) from exc
    outputs = list(report.values())
    if not outputs:
        raise UserError(NO_CHECKS)
    tick(0.9, "Writing the report")
    written = Path(str(report.save_path))
    if written.suffix != ".html":  # a report that cannot be rendered is saved as .qmd
        raise UserError(FAILED)
    # the file the library wrote has no policy: it holds names and text from the package
    page = written.read_text(encoding="utf-8")
    target.write_text(protect(page), encoding="utf-8")
    tick(1.0, "Done")
    return PackageAnalysis(
        name=name,
        preset=preset,
        checks=checks_table(outputs),
        checklist=checklist_table(outputs),
        html=page,
        report_path=target,
        seconds=time.perf_counter() - start,
        note=note,
    )
