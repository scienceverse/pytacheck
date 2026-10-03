"""Data packages: check the folder or archive of files that comes with a paper.

A data package is what a researcher archives or shares with a paper: data,
code and documentation. This package opens one (a folder, or a zip or tar
archive of it) and lists its files and folders; the checks themselves are the
modules of the ``datapackage`` pack in :mod:`metacheck.datapackage.modules`,
which every installation has (``datapackage::package_files`` and so on).
:func:`check_package` runs them on a package and :func:`report_package` writes
the report; ``metacheck package PATH`` is the command line for both.

These checks are pytacheck's own: metacheck has no counterpart, so they are
not part of the ``metacheck::`` modules that mirror it.
"""

from __future__ import annotations

from metacheck.datapackage._check import (
    DEFAULT_PRESET,
    check_package,
    package_selection,
    report_package,
)
from metacheck.datapackage._findings import (
    CHECKLIST_COLUMNS,
    FINDING_COLUMNS,
    checklist_frame,
    findings_frame,
    status_from_findings,
    traffic_light,
)
from metacheck.datapackage._listing import DIR_COLUMNS, FILE_COLUMNS, list_dirs, list_files
from metacheck.datapackage._open import (
    ARCHIVE_SUFFIXES,
    OpenedPackage,
    PackageError,
    is_archive,
    open_package,
)
from metacheck.datapackage._session import package_for, using_package

__all__ = [
    "ARCHIVE_SUFFIXES",
    "CHECKLIST_COLUMNS",
    "DEFAULT_PRESET",
    "DIR_COLUMNS",
    "FILE_COLUMNS",
    "FINDING_COLUMNS",
    "OpenedPackage",
    "PackageError",
    "check_package",
    "checklist_frame",
    "findings_frame",
    "is_archive",
    "list_dirs",
    "list_files",
    "open_package",
    "package_for",
    "package_selection",
    "report_package",
    "status_from_findings",
    "traffic_light",
    "using_package",
]
