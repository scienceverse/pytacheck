"""Data Package Files: Junk and temporary files, file formats and file and folder names in a data package."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package Files",
    description="Junk and temporary files, file formats and file and folder names in a data package.",
    details="""
        Looks at the files and folders of a data package, the folder (or zip or tar archive)
        of data, code and documentation that comes with a paper, and reports four things a
        data steward checks before the package is archived:

        1. **Junk and temporary files**: files the operating system, an editor or a
           programming tool left behind (`.DS_Store`, `__MACOSX`, `Thumbs.db`, `~$` lock
           files, backups, `__pycache__`, ...) and version-control or editor folders
           (`.git`, `.idea`). A junk folder is reported once, and junk is left out of the
           other checks.
        2. **File formats**: files that are not in a preferred archival format. The default
           list is based on the DANS list of preferred formats (a PDF is preferred only if
           it is PDF/A); a format that is not on the list is reported as information.
        3. **File and folder names**: spaces, special characters, accents and names or paths
           that are too long.
        4. **Consistent names**: names that separate words differently from most of the
           package, dates that are not written as YYYY-MM-DD, and numbers that are not
           padded with zeros (`trial1`, `trial10`).

        The result is a table of findings (one row per thing to fix, with a severity of
        problem, suggestion or info) and a checklist with one row per item. Each
        policy option takes a dict or list, or the path of a JSON file; the defaults are the
        files in `metacheck/datapackage/data`.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object (may be empty)",
        "local_path": "the data package: a folder, or a zip or tar archive of one",
        "formats": (
            "the preferred file formats: a dict, or the path of a JSON file "
            '({"name", "source", "formats": {"<ext>": {"level": "preferred" or '
            '"non-preferred", "label", "alternative", "requires"}}}); the default is based '
            "on the DANS list"
        ),
        "junk": (
            "the junk rules: a list, or the path of a JSON file, of "
            '{"rule", "match" (name, prefix, suffix, glob or folder), "pattern", '
            '"severity", "explanation"}; the default lists the usual junk files'
        ),
        "severity": (
            'a dict of rule id to "problem", "suggestion", "info" or "ignore" that '
            "overrides the default severity of a rule"
        ),
        "limits": (
            "the length limits for names, a dict or the path of a JSON file: "
            "path_too_long (255), path_long (228), folder_path_long (100), file_name_long (50)"
        ),
        "min_names_for_style": (
            "the number of names with several words that is needed before the word-separator "
            "style of the names is compared (default 5)"
        ),
    },
)
def package_files(
    paper: Any,
    local_path: Any = None,
    formats: Any = None,
    junk: Any = None,
    severity: Any = None,
    limits: Any = None,
    min_names_for_style: int = 5,
) -> dict[str, Any]:
    """Junk and temporary files, file formats and file and folder names in a data package."""
    from metacheck.datapackage import package_for, traffic_light
    from metacheck.datapackage.files import (
        check_files,
        files_report,
        files_summary_text,
    )

    pkg = package_for(local_path)
    if pkg is None:
        return no_package()
    findings, checklist = check_files(
        pkg,
        formats=formats,
        junk=junk,
        severity=severity,
        limits=limits,
        min_names_for_style=int(min_names_for_style),
    )
    n_files = len(pkg.files())
    counts = findings["check"].value_counts().to_dict()
    sev = findings["severity"].value_counts().to_dict()
    return {
        "table": findings,
        "summary_table": summary_table(
            paper,
            files=n_files,
            junk_files=counts.get("junk_files", 0),
            file_formats=counts.get("file_formats", 0),
            file_names=counts.get("file_names", 0),
            naming_convention=counts.get("naming_convention", 0),
            problems=sev.get("problem", 0),
            suggestions=sev.get("suggestion", 0),
        ),
        "traffic_light": traffic_light(checklist),
        "summary_text": files_summary_text(findings, checklist, n_files),
        "report": files_report(pkg, findings, checklist, formats=formats),
        "checklist": checklist,
    }
