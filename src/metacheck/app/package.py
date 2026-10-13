"""Check a data package: the code behind the "Check a data package" page.

A data package is the folder (or the zip of it) of data, code and documentation that
comes with a paper. This module finds the package the person named, runs the
``datapackage`` checks on it with :func:`metacheck.datapackage.report_package` and turns
the result into two tables: one row per check, and the checklist (one row per
requirement). It does not import gradio, and it never uploads anything: the checks run on
this computer, and no check looks anything up online. The one download is the local
classifier's model (about 840 MB, once), which is on by default and needs the ``concepts``
extra.

A package is named in one of four ways: an archive that was uploaded, the link of a public
repository (GitHub, GitLab, OSF or Zenodo; the app downloads it), or, in the local app only,
the path of a folder. :func:`classify_entry` decides which of them the paper page's one input
box was given, and which of them is a paper instead. A folder is named by a path on the
computer that runs the app, so a shared server never accepts one. The local app reads a folder
only inside the person's home folder, or inside a folder named in ``METACHECK_APP_ROOTS``:
another program on this computer that holds the app's token cookie could otherwise ask it to
read any folder and return the README (see :func:`resolve_source` and ``security.py``).

What a package may be is limited (:class:`Limits`), tighter on a shared server: the archive is
unpacked under limits on bytes, files, depth and compression ratio, and a link is fetched only
from the hosts the library knows, without any token of the server (``datapackage/_remote.py``).
"""

from __future__ import annotations

import os
import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metacheck._env import env_get, env_name
from metacheck.app.run import (
    _RUN_LOCK,
    LIGHTS,
    Progress,
    Row,
    UserError,
    _plain,
    checks_progress,
    protect,
    report_filename,
)
from metacheck.datapackage._limits import setting

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "ARCHIVE",
    "BIGGER_THAN_UPLOAD",
    "CHECKLIST_COLUMNS",
    "CHECK_COLUMNS",
    "HOSTED",
    "LINK",
    "LOCAL",
    "MAX_FILES",
    "MAX_UNPACKED_BYTES",
    "NO_CLASSIFIER",
    "PAPER",
    "ROOTS_ENV",
    "UPLOAD_LIMIT",
    "Entry",
    "Limits",
    "PackageAnalysis",
    "allowed_roots",
    "box_parts",
    "check_package_source",
    "checklist_table",
    "checks_table",
    "classifier_installed",
    "classify_entry",
    "counts_line",
    "package_name",
    "package_presets",
    "resolve_entry",
    "resolve_link",
    "resolve_source",
    "status_light",
    "valid_preset",
]

#: Limits for a zip or tar the person uploads, lower than the library's (20 GB, 200,000
#: files): the size and the number of files the archive says it holds, and what is really
#: written while it is unpacked, are both counted. The ``METACHECK_PACKAGE_*`` settings
#: (see :mod:`metacheck.datapackage._limits`) replace these defaults on either kind of server.
MAX_UNPACKED_BYTES = setting("PACKAGE_MAX_UNPACKED", 5 * 1024**3, "size")
MAX_FILES = setting("PACKAGE_MAX_FILES", 100_000)

#: what the local app accepts as an upload (the shared server keeps ``MOUNT_KWARGS``' 50 MB)
UPLOAD_LIMIT = "1gb"
BIGGER_THAN_UPLOAD = "A zip can be up to 1 GB. For a bigger package, give its folder instead."


@dataclass(frozen=True)
class Limits:
    """What a package may be, and how a link to one may be fetched, on one kind of server."""

    download: int  # bytes of the archive a link leads to
    download_seconds: float  # how long that download may take
    unpacked: int  # bytes an archive may unpack to
    files: int  # files (and folders) an archive may hold
    ratio: float  # how many times its own size an archive may grow, past a floor
    depth: int  # how deep the folders may go
    credentials: bool  # whether tokens on this computer may be sent to the host of a link

    def kwargs(self) -> dict[str, Any]:
        """The limits as the arguments of :func:`metacheck.datapackage.report_package`."""
        return {
            "max_download": self.download,
            "download_timeout": self.download_seconds,
            "max_bytes": self.unpacked,
            "max_files": self.files,
            "max_ratio": self.ratio,
            "max_depth": self.depth,
            "credentials": self.credentials,
        }


#: the local app: the person's own computer and connection, and their own tokens for private
#: repositories they can read
LOCAL = Limits(
    download=setting("PACKAGE_MAX_DOWNLOAD", 5 * 1024**3, "size"),
    download_seconds=setting("PACKAGE_DOWNLOAD_SECONDS", 900.0, "float"),
    unpacked=MAX_UNPACKED_BYTES,
    files=MAX_FILES,
    ratio=setting("PACKAGE_MAX_RATIO", 250.0, "float"),
    depth=setting("PACKAGE_MAX_DEPTH", 40),
    credentials=True,
)
#: a shared server: anyone with a token can upload an archive or name a link, so less may be
#: fetched and unpacked, and the server's own tokens are never used for what a visitor named
#: The defaults of a shared server are stricter than the local app's. An operator raises them
#: with the same ``METACHECK_PACKAGE_*`` settings, which then also apply to the local app if the
#: same process runs it.
HOSTED = Limits(
    download=setting("PACKAGE_MAX_DOWNLOAD", 50 * 1024**2, "size"),
    download_seconds=setting("PACKAGE_DOWNLOAD_SECONDS", 300.0, "float"),
    unpacked=setting("PACKAGE_MAX_UNPACKED", 1024**3, "size"),
    files=setting("PACKAGE_MAX_FILES", 20_000),
    ratio=setting("PACKAGE_MAX_RATIO", 100.0, "float"),
    depth=setting("PACKAGE_MAX_DEPTH", 20),
    credentials=False,
)
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
NOTHING_GIVEN = (
    "Upload a paper or an archive of a data package, or paste the link of a repository on "
    "GitHub, GitLab, OSF or Zenodo. You can also try the demo paper."
)
NOTHING_GIVEN_LOCAL = (
    "Upload a paper or an archive of a data package, paste the link of a repository on "
    "GitHub, GitLab, OSF or Zenodo, or type the path of a package's folder. "
    "You can also try the demo paper."
)
TOO_LONG = "That is too long to be a link or the path of a folder."
#: the longest text the box may hold: a link or a path
MAX_TEXT = 2000
BOTH_GIVEN = "Give a file or a link, not both. Remove one of them."
ONE_FILE = "Give one file at a time."
BAD_FILE = (
    "This file type is not supported. Upload a paper (a PDF, a GROBID XML file or a bibr JSON "
    "file) or a zip or tar archive of a data package."
)
NOT_A_LINK = (
    "That is not a link this server can check. Paste the link of a public repository on "
    "GitHub, GitLab, OSF or Zenodo, or upload a paper or an archive."
)
#: What the page says (as Markdown) when the classifier is asked for and the ``concepts`` extra
#: is missing.
NO_CLASSIFIER = (
    "The local classifier is not installed, so the concepts of the data columns come from "
    'rules only. To get it, install the concepts extra: `pip install "metacheck[concepts]"`. '
    "Its model, about 840 MB, is downloaded the first time it is used."
)

#: The ways to name a package (the package page's choice), and what the paper page's box can hold.
FOLDER = "folder"
ZIP = "zip"
LINK = "link"
SOURCES = [
    ("A folder on this computer", FOLDER),
    ("A zip file", ZIP),
    ("The link of a repository", LINK),
]
PAPER = "paper"
ARCHIVE = "archive"

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


def package_presets(hosted: bool = False) -> list[tuple[str, str]]:
    """``(label, ref)`` of the presets that check a data package, for the page's choice.

    These are the presets of every installed pack and of the person's config whose modules
    include a check of the ``datapackage`` pack (``datapackage::default`` and any preset
    that extends it), found through the pack registry. A preset for papers is not offered.
    ``datapackage::default`` comes first. A shared server (``hosted``) offers only the presets
    made of its own checks, as for papers: a pack's code is run on the person's computer only.
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
        if not any(m.startswith("datapackage::") for m in modules):
            continue
        if hosted and not all(m.startswith(("datapackage::", "metacheck::")) for m in modules):
            continue
        text = str(description) if description == description and description else ""
        found.append((f"{ref}: {text}" if text else ref, ref))
    found.sort(key=lambda item: item[1] != DEFAULT_PRESET)
    return found


def valid_preset(ref: str | None, hosted: bool = False) -> str:
    """*ref* if the page offers it for a package, else the first preset it offers: the value
    comes from the browser, which may send anything."""
    from metacheck.datapackage import DEFAULT_PRESET

    refs = [r for _, r in package_presets(hosted)]
    return ref if ref in refs else (refs[0] if refs else DEFAULT_PRESET)


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


NO_SOURCE_LINK = "Type the link of the repository first."


def resolve_link(text: str | None) -> str:
    """The link to fetch, once it is read as the link of a repository this app can fetch from:
    as typed, without quotes and spaces around it, and with ``https://`` in front when it was
    typed without (``github.com/owner/repo``, a bare DOI), so that the run sees a link.

    Raises :class:`~metacheck.app.run.UserError` with what is wrong with it, or when it is empty.
    """
    from metacheck.datapackage import PackageError, parse_link
    from metacheck.datapackage._remote import _normalise

    link = _clean_path(text or "")
    if not link:
        raise UserError(NO_SOURCE_LINK)
    try:
        parse_link(link)
    except PackageError as exc:
        raise UserError(str(exc)) from exc
    return _normalise(link)


def resolve_entry(entry: Entry) -> str | Path:
    """The package to check for what the paper page's box held: the archive's path (the app's
    own file), the link, or the folder (inside the allowed places, as for :func:`resolve_source`)."""
    if entry.kind == LINK:
        return resolve_link(entry.value)
    if entry.kind == ARCHIVE:
        return resolve_source(ZIP, None, entry.value)
    return resolve_source(FOLDER, entry.value, None)


# -- what the one input box was given ------------------------------------------------------

#: what a paper's file ends with (the paper page's three formats)
PAPER_SUFFIXES = (".pdf", ".xml", ".json")
#: a link typed without its scheme: the hosts the library knows, or a DOI
_LINKISH = re.compile(
    r"(?:www\.)?(?:github\.com|gitlab\.com|osf\.io|zenodo\.org|doi\.org|dx\.doi\.org)/"
    r"|(?:doi:\s*)?10\.\d{4,9}/",
    re.IGNORECASE,
)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


@dataclass(frozen=True)
class Entry:
    """What the paper page's box holds: ``kind`` is ``PAPER``, ``ARCHIVE``, ``LINK`` or ``FOLDER``,
    and ``value`` the file's path, the link or the folder's path."""

    kind: str
    value: str


def box_parts(box: Any) -> tuple[str, list[str]]:
    """The text and the file paths of the box's value (a string is a text, nothing a file)."""
    if box is None:
        return "", []
    if isinstance(box, str):
        return box, []
    text = box.get("text") if isinstance(box, dict) else None
    files = box.get("files") if isinstance(box, dict) else None
    paths: list[str] = []
    for item in files or []:
        path = item.get("path") if isinstance(item, dict) else item
        if path:
            paths.append(str(path))
    return text if isinstance(text, str) else "", paths


def classify_entry(box: Any, *, hosted: bool = False) -> Entry:
    """Decide what the page's one input box was given: a paper, an archive of a data package,
    the link of a repository or (the local app only) the path of a folder.

    *box* is the value of the box: ``{"text": ..., "files": [...]}``. A file is told by its
    ending (a zip is also what a spreadsheet is, so the contents say nothing); a text by its
    shape. A link is read here already, so that a wrong one is answered before the run is
    queued, and a link that is not understood is never taken for a folder.

    Raises :class:`~metacheck.app.run.UserError`, with a message for the person, when the box is
    empty, holds a file and a text, a file of another kind, or on a shared server a text
    that is no link.
    """
    from metacheck.datapackage import ARCHIVE_SUFFIXES, PackageError, is_link, parse_link

    text, files = box_parts(box)
    text = _clean_path(text)
    if len(files) > 1:
        raise UserError(ONE_FILE)
    if files:
        if text:
            raise UserError(BOTH_GIVEN)
        low = files[0].lower()
        if low.endswith(PAPER_SUFFIXES):
            return Entry(PAPER, files[0])
        if low.endswith(tuple(ARCHIVE_SUFFIXES)):
            return Entry(ARCHIVE, files[0])
        raise UserError(BAD_FILE)
    if not text:
        raise UserError(NOTHING_GIVEN if hosted else NOTHING_GIVEN_LOCAL)
    if len(text) > MAX_TEXT:
        raise UserError(TOO_LONG)
    if is_link(text) or _SCHEME.match(text) or _LINKISH.match(text):
        try:
            parse_link(text)
        except PackageError as exc:
            raise UserError(str(exc)) from exc
        return Entry(LINK, text)
    if hosted:
        raise UserError(NOT_A_LINK)
    return Entry(FOLDER, text)


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
    rows: tuple[Row, ...] = ()  # one row per check, as the paper page's table shows them


def package_name(source: str | os.PathLike[str]) -> str:
    """A name for the package: the folder's, the archive's name without its ending, or the
    repository's (for a link)."""
    from metacheck.datapackage import ARCHIVE_SUFFIXES, PackageError, is_link, parse_link

    if is_link(source):
        try:
            return parse_link(str(source)).name
        except PackageError:
            return "package"
    path = Path(source)
    name = path.name or path.resolve().name
    low = name.lower()
    if path.is_file():
        for suffix in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
            if low.endswith(suffix):
                return name[: -len(suffix)] or name
    return name or "package"


def _rows(outputs: list[Any]) -> tuple[Row, ...]:
    """The checks as the paper page's table has them: the traffic light, then the summary."""
    rows = []
    for out in outputs:
        light = LIGHTS.get(str(out.traffic_light), str(out.traffic_light).capitalize())
        text = _result_text(out)
        title = str(out.title or "")
        rows.append(Row(title, title, "Data package", f"{light}: {text}" if text else light))
    return tuple(rows)


def check_package_source(
    source: str | os.PathLike[str],
    preset: str,
    *,
    report_dir: Path,
    local_classifier: bool = True,
    progress: Progress | None = None,
    limits: Limits = LOCAL,
    max_bytes: int | None = None,
    max_files: int | None = None,
) -> PackageAnalysis:
    """Run *preset* on the data package at *source* and write its report into *report_dir*.

    *source* is a folder, a zip or tar archive that is unpacked to a private folder for the run
    (members that would leave it, links and device files are skipped), or the link of a
    repository, which is downloaded first. *limits* say how much may be downloaded and
    unpacked (an archive with more than ``limits.files`` members, or unpacking to more than
    ``limits.unpacked`` bytes or ``limits.ratio`` times its size, is refused), and whether a
    token on this computer may be sent to the host of a link; *max_bytes* and *max_files*
    replace two of them. ``local_classifier=True``
    (the default, as for ``metacheck package``) leaves the concepts of the data columns to
    ``data_check``'s own default, its local classifier, which downloads a model of about
    840 MB the first time. Without the ``concepts`` extra the concepts come from rules only
    instead, and the result's ``note`` says so. ``False`` names them by rules only, and
    nothing is downloaded.

    The report is saved as ``<name>_report.html`` in *report_dir*, under the same
    Content-Security-Policy as the paper page's report. Raises
    :class:`~metacheck.app.run.UserError` for a package that cannot be opened.
    """
    from metacheck.core.scope import module_progress
    from metacheck.datapackage import PackageError, is_link, report_package
    from metacheck.module import ModuleError

    tick = progress or (lambda _fraction, _text: None)
    start = time.perf_counter()
    path: str | Path = str(source).strip() if is_link(source) else Path(source)
    name = package_name(path)
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / report_filename(name)
    tick(0.0, "Fetching the repository" if isinstance(path, str) else "Opening the package")
    use_classifier = local_classifier and classifier_installed()
    note = NO_CLASSIFIER if local_classifier and not use_classifier else ""
    args = None if use_classifier else {"data_check": {"concepts": "rules"}}
    given = limits.kwargs()
    if max_bytes is not None:
        given["max_bytes"] = max_bytes
    if max_files is not None:
        given["max_files"] = max_files
    with _RUN_LOCK, module_progress(checks_progress(tick)), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            report = report_package(
                path,
                target,
                "html",
                preset=preset,
                args=args,
                progress=tick,
                **given,
            )
        except (PackageError, ValueError, ModuleError) as exc:
            raise UserError(str(exc)) from exc
    outputs = list(report.values())
    if not outputs:
        raise UserError(NO_CHECKS)
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
        rows=_rows(outputs),
    )
