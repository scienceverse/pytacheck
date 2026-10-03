"""Personal data by value in the data files of a data package.

:func:`check_personal_data` reads the first rows of every tabular data file of a
package (csv, tsv, Excel, ODS, SPSS, Stata, SAS) and looks at each column for
Dutch citizen service numbers (BSN, with the 11-check), student numbers (in a
column named like one), phone numbers, IPv6 addresses, Dutch postcodes and
people's names (in a column named like one). The detectors and the rules that
make a column are in :mod:`metacheck.datapackage._pii_detect`.

This is a separate check from the PII checks of ``metacheck::data_check`` (the
port of R), which it does not touch: those look for e-mail addresses, IPv4
addresses, US social security numbers and payment cards by value and at column
names, and have no Dutch identifiers.

What is reported is a count: the file, the sheet, the column's name and the
number of cells that matched, never a cell. A column name that itself looks like
a value (a long name, one with an ``@`` or a long number) is shown as its
position. Findings are hints for a person to check, not a verdict.
"""

from __future__ import annotations

import csv
import itertools
import os
import re
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metacheck.datapackage._findings import (
    SEVERITIES,
    checklist_frame,
    findings_frame,
    traffic_light,
)
from metacheck.datapackage._pii_detect import (
    DETECTORS,
    Detector,
    is_bsn,
    is_ipv6,
    is_person_name,
    is_phone,
    is_postcode,
    is_student_number,
    judge_column,
)

__all__ = [
    "CHECK_TITLES",
    "DEFAULT_SEVERITY",
    "HITS_COLUMNS",
    "TABULAR_EXTENSIONS",
    "PiiResult",
    "check_personal_data",
    "load_severity",
    "pii_report",
]

#: Extensions of the files that are read as tables.
TABULAR_EXTENSIONS = frozenset(
    {"csv", "tsv", "xlsx", "xls", "ods", "sav", "dta", "por", "sas7bdat"}
)

#: Checklist items, in report order, with their titles.
CHECK_TITLES: dict[str, str] = {
    **{d.item: d.title for d in DETECTORS},
    "pii_scan": "Data files could be scanned",
}

#: Default severity of each finding rule. A match is a hint, so a suggestion: the checklist
#: then asks a person to look (``manual``). Set a rule to ``problem`` to make it ``fail``.
DEFAULT_SEVERITY: dict[str, str] = {
    **{d.id: "suggestion" for d in DETECTORS},
    "file-too-large": "suggestion",
    "file-unreadable": "suggestion",
    "files-not-scanned": "suggestion",
}

#: Columns of the ``hits`` table: one row per file, sheet and column that was flagged.
HITS_COLUMNS = (
    "path",  # the file
    "sheet",  # the sheet of a workbook with several, else ""
    "column",  # the column's name, or "column N" when the name looks like a value
    "column_index",  # 1-based position
    "rule",  # the detector id: bsn, student-number, phone, ipv6, postcode, person-name
    "hits",  # cells that matched
    "checked",  # filled cells that were looked at
    "named",  # the column's name also points at this kind of data
    "rows_read",  # rows of the file that were read
    "rows_capped",  # the file has more rows than were read
)

_MAX_SHEETS = 10
_OVERRIDE_VALUES = (*SEVERITIES, "ignore")


# -- options ---------------------------------------------------------------------------


def load_severity(severity: Any = None) -> dict[str, str]:
    """The severity overrides as a dict: rule id to ``problem``, ``suggestion``, ``info`` or ``ignore``.

    *severity* is ``None``, a dict or the path of a JSON file holding one. Rule ids
    that are not used here are kept (a pack may share one dict between checks); a
    bad value raises ``ValueError``. A detector set to ``ignore`` is not run.
    """
    if severity is None:
        return {}
    if isinstance(severity, str | os.PathLike):
        from metacheck.datapackage.structure import _read_json

        severity = _read_json(severity)
    if not isinstance(severity, Mapping):
        raise ValueError(
            "severity must be a dict of rule id to severity (or a path to a JSON file)"
        )
    out = {str(k): str(v) for k, v in severity.items()}
    for rule, value in out.items():
        if value not in _OVERRIDE_VALUES:
            raise ValueError(
                f"Severity of {rule!r} must be one of {_OVERRIDE_VALUES}, not {value!r}"
            )
    return out


# -- reading ---------------------------------------------------------------------------


@dataclass
class _Table:
    sheet: str
    names: list[str]
    columns: list[list[str | None]]
    rows: int
    capped: bool


class _Unreadable(Exception):
    """The file could not be read as a table."""


def _csv_rows(path: Path, sep: str, limit: int, encoding: str) -> list[list[str]]:
    with open(path, newline="", encoding=encoding) as handle:
        reader = csv.reader(handle, delimiter=sep)
        return list(itertools.islice(reader, limit))


def _read_delimited(path: Path, ext: str, max_rows: int) -> list[_Table]:
    from metacheck.datacheck.files import _detect_header, _sniff_delimiter

    try:
        sep = "\t" if ext == "tsv" else _sniff_delimiter(path)
        header = _detect_header(path, sep)
        rows: list[list[str]] | None = None
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                rows = _csv_rows(path, sep, max_rows + 2, encoding)
                break
            except UnicodeDecodeError:
                continue
    except (OSError, csv.Error, ValueError) as exc:
        raise _Unreadable(type(exc).__name__) from exc
    if not rows:
        return []
    width = max(len(r) for r in rows)
    if header:
        names, body = [c.strip() for c in rows[0]], rows[1:]
        names += [""] * (width - len(names))
    else:
        names, body = [""] * width, rows
    capped = len(body) > max_rows
    body = body[:max_rows]
    columns: list[list[str | None]] = [
        [r[j] if j < len(r) else None for r in body] for j in range(width)
    ]
    return [_Table("", names, columns, len(body), capped)]


def _cell(value: Any) -> str | None:
    """A cell as text: numbers keep their digits (``6.12e8`` does not), dates and flags are dropped."""
    import numpy as np

    if value is None or isinstance(value, bool | np.bool_):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | np.integer):
        return str(int(value))
    if isinstance(value, float | np.floating):
        number = float(value)
        if number == number and number.is_integer() and abs(number) < 1e15:
            return str(int(number))
    return None


def _frame_table(df: Any, sheet: str, max_rows: int) -> _Table | None:
    if df is None or df.shape[1] == 0:
        return None
    capped = len(df) > max_rows
    df = df.iloc[:max_rows]
    columns = [[_cell(v) for v in df.iloc[:, j].tolist()] for j in range(df.shape[1])]
    return _Table(sheet, [str(c) for c in df.columns], columns, len(df), capped)


def _sheet_names(path: Path) -> list[str]:
    from metacheck.datacheck import _files_readers as readers

    try:
        book = (
            readers._XlsBook(str(path))
            if path.suffix.lower() == ".xls"
            else readers._XlsxBook(str(path))
        )
        return [str(s) for s in book.sheet_names]
    except Exception:
        return []


def _read_with_reader(path: Path, ext: str, max_rows: int) -> list[_Table]:
    from metacheck.datacheck.files import data_read_head

    # ask for one row more than we keep, to know whether the file goes on
    limit = max_rows + 1
    sheets: list[str | None] = [None]
    if ext in ("xlsx", "xls"):
        names = _sheet_names(path)
        sheets = [None, *names[1:_MAX_SHEETS]]
    tables: list[_Table] = []
    for i, sheet in enumerate(sheets):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = data_read_head(path, n_rows=limit, sheet=sheet)
        except Exception as exc:
            if i == 0:
                raise _Unreadable(type(exc).__name__) from exc
            continue
        if df is None and i == 0:
            raise _Unreadable("no table")
        label = "" if i == 0 else _label(str(sheet), i + 1, "sheet")
        table = _frame_table(df, label, max_rows)
        if table is not None:
            tables.append(table)
    return tables


def _read_tables(path: Path, ext: str, max_rows: int) -> list[_Table]:
    if ext in ("csv", "tsv"):
        return _read_delimited(path, ext, max_rows)
    return _read_with_reader(path, ext, max_rows)


# -- scanning --------------------------------------------------------------------------

_VALUE_LIKE = re.compile(r"\d{6,}|@")


def _looks_like_a_value(name: str) -> bool:
    """Whether a column or sheet name is itself a value (a file without a header row has data there)."""
    if len(name) > 40 or _VALUE_LIKE.search(name):
        return True
    return (
        is_bsn(name, True)
        or is_phone(name, True)
        or is_ipv6(name)
        or is_postcode(name, True)
        or is_student_number(name)
        or is_person_name(name, minimum_words=2)
    )


def _label(name: str, index: int, kind: str = "column") -> str:
    """A column's (or sheet's) name for a report; one that looks like a value is shown as its position."""
    if not name or _looks_like_a_value(name):
        return f"{kind} {index}"
    return name


@dataclass
class PiiResult:
    """What :func:`check_personal_data` found."""

    findings: Any
    checklist: Any
    hits: Any
    n_files: int  # tabular data files in the package
    n_scanned: int  # of those, read
    n_tables: int
    rows_read: int
    n_skipped: int
    max_rows: int
    summary_text: str
    traffic_light: str
    flagged: dict[str, int] = field(default_factory=dict)  # detector id -> columns


def _sev(overrides: Mapping[str, str], rule: str) -> str:
    return overrides.get(rule, DEFAULT_SEVERITY[rule])


def _data_files(pkg: Any) -> list[tuple[str, str, str, int]]:
    """``(path, rel, extension, size)`` of the tabular files, not hidden, not links, not codebooks.

    ``path`` is relative to the package's root (where the file is), ``rel`` to its base (what a report shows).
    """
    files = pkg.files()
    out: list[tuple[str, str, str, int]] = []
    for path, rel, ext, size, hidden, link, role in zip(
        files["path"].tolist(),
        files["rel"].tolist(),
        files["ext"].tolist(),
        files["size"].tolist(),
        files["hidden"].tolist(),
        files["link"].tolist(),
        files["doc_role"].tolist(),
        strict=True,
    ):
        if ext in TABULAR_EXTENSIONS and not hidden and not link and str(role) != "codebook":
            out.append((str(path), str(rel), str(ext), int(size)))
    return out


def check_personal_data(
    pkg: Any,
    *,
    max_rows: int = 5000,
    max_file_size: float = 100,
    max_files: int = 500,
    severity: Any = None,
) -> PiiResult:
    """Look for personal data by value in the tabular files of *pkg*.

    At most *max_rows* rows of each file are read (of each sheet, for the first
    10 sheets of a workbook), files over *max_file_size* MB are skipped, and at
    most *max_files* files are read. Skipped files are reported (item
    ``pii_scan``). *severity* overrides :data:`DEFAULT_SEVERITY` by rule id
    (``ignore`` turns a detector off). The result has the findings, the
    checklist (one item per detector and ``pii_scan``) and the ``hits`` table
    (:data:`HITS_COLUMNS`); no table holds a cell's value.
    """
    import pandas as pd

    overrides = load_severity(severity)
    active = [d for d in DETECTORS if overrides.get(d.id) != "ignore"]
    data_files = _data_files(pkg)
    rows: list[dict[str, str]] = []
    hits_rows: list[dict[str, Any]] = []
    scanned = tables_read = rows_read = skipped = 0
    root = Path(pkg.root)

    for index, (disk, path, ext, size) in enumerate(data_files):
        if index >= max_files:
            break
        if size > max_file_size * 1024 * 1024:
            skipped += 1
            rows.append(
                _finding(
                    path,
                    "pii_scan",
                    "file-too-large",
                    _sev(overrides, "file-too-large"),
                    f"Not scanned: the file is over {max_file_size:g} MB.",
                )
            )
            continue
        try:
            tables = _read_tables(root / disk, ext, max_rows)
        except _Unreadable:
            skipped += 1
            rows.append(
                _finding(
                    path,
                    "pii_scan",
                    "file-unreadable",
                    _sev(overrides, "file-unreadable"),
                    "Not scanned: the file could not be read as a table.",
                )
            )
            continue
        scanned += 1
        for table in tables:
            tables_read += 1
            rows_read += table.rows
            for j, (name, cells) in enumerate(zip(table.names, table.columns, strict=True), 1):
                for det, hits, checked, named in judge_column(
                    name, [c for c in cells if c is not None], active
                ):
                    hits_rows.append(
                        {
                            "path": path,
                            "sheet": table.sheet,
                            "column": _label(name, j),
                            "column_index": j,
                            "rule": det.id,
                            "hits": hits,
                            "checked": checked,
                            "named": named,
                            "rows_read": table.rows,
                            "rows_capped": table.capped,
                        }
                    )
    not_read = max(len(data_files) - max_files, 0)
    if not_read:
        skipped += not_read
        rows.append(
            _finding(
                "",
                "pii_scan",
                "files-not-scanned",
                _sev(overrides, "files-not-scanned"),
                f"{not_read} data files were not scanned: only the first {max_files} are read.",
                kind="package",
            )
        )

    by_id = {d.id: d for d in DETECTORS}
    for h in hits_rows:
        det = by_id[h["rule"]]
        rows.append(
            _finding(
                h["path"],
                det.item,
                det.id,
                _sev(overrides, det.id),
                _hit_detail(h, det),
            )
        )
    findings = findings_frame(sorted(rows, key=lambda r: (r["path"].casefold(), r["check"])))
    hits_frame = pd.DataFrame(
        {
            "path": pd.Series([h["path"] for h in hits_rows], dtype="string"),
            "sheet": pd.Series([h["sheet"] for h in hits_rows], dtype="string"),
            "column": pd.Series([h["column"] for h in hits_rows], dtype="string"),
            "column_index": pd.Series([h["column_index"] for h in hits_rows], dtype="Int64"),
            "rule": pd.Series([h["rule"] for h in hits_rows], dtype="string"),
            "hits": pd.Series([h["hits"] for h in hits_rows], dtype="Int64"),
            "checked": pd.Series([h["checked"] for h in hits_rows], dtype="Int64"),
            "named": pd.Series([h["named"] for h in hits_rows], dtype="boolean"),
            "rows_read": pd.Series([h["rows_read"] for h in hits_rows], dtype="Int64"),
            "rows_capped": pd.Series([h["rows_capped"] for h in hits_rows], dtype="boolean"),
        }
    )
    flagged = {d.id: sum(1 for h in hits_rows if h["rule"] == d.id) for d in DETECTORS}
    checklist = _checklist(
        hits_rows, active, overrides, len(data_files), scanned, skipped, max_rows
    )
    result = PiiResult(
        findings=findings,
        checklist=checklist,
        hits=hits_frame,
        n_files=len(data_files),
        n_scanned=scanned,
        n_tables=tables_read,
        rows_read=rows_read,
        n_skipped=skipped,
        max_rows=max_rows,
        summary_text="",
        traffic_light=traffic_light(checklist),
        flagged=flagged,
    )
    result.summary_text = _summary(result, hits_rows)
    return result


def _finding(
    path: str, check: str, rule: str, severity: str, detail: str, *, kind: str = "file"
) -> dict[str, str]:
    return {
        "path": path,
        "kind": kind,
        "check": check,
        "rule": rule,
        "severity": severity,
        "detail": detail,
    }


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _hit_detail(h: Mapping[str, Any], det: Detector) -> str:
    where = (
        f"Column {h['column']!r}"
        if not h["column"].startswith("column ")
        else h["column"].capitalize()
    )
    if h["sheet"]:
        where += f" of sheet {h['sheet']!r}"
    text = f"{where}: {h['hits']} of {h['checked']} filled cells look like {det.noun}."
    if h["named"] and det.id not in ("student-number", "person-name"):
        text += " The column's name points the same way."
    if h["rows_capped"]:
        text += f" Only the first {h['rows_read']} rows were read."
    return text


# -- checklist and summary -------------------------------------------------------------


def _checklist(
    hits_rows: Sequence[Mapping[str, Any]],
    active: Sequence[Detector],
    overrides: Mapping[str, str],
    n_files: int,
    scanned: int,
    skipped: int,
    max_rows: int,
) -> Any:
    rows: list[dict[str, str]] = []
    active_ids = {d.id for d in active}
    for det in DETECTORS:
        item = {"item": det.item, "title": det.title}
        if det.id not in active_ids:
            rows.append({**item, "status": "na", "detail": "Turned off."})
            continue
        if scanned == 0:
            rows.append({**item, "status": "na", "detail": "No data files could be scanned."})
            continue
        mine = [h for h in hits_rows if h["rule"] == det.id]
        if not mine:
            rows.append(
                {
                    **item,
                    "status": "pass",
                    "detail": f"No {det.noun} found in {_n(scanned, 'data file')}.",
                }
            )
            continue
        files = len({h["path"] for h in mine})
        severity = _sev(overrides, det.id)
        status = (
            "fail" if severity == "problem" else "manual" if severity == "suggestion" else "pass"
        )
        rows.append(
            {
                **item,
                "status": status,
                "detail": (
                    f"{_n(len(mine), 'column')} in {_n(files, 'file')} may hold {det.noun}; "
                    "a person has to check them."
                ),
            }
        )
    item = {"item": "pii_scan", "title": CHECK_TITLES["pii_scan"]}
    if n_files == 0:
        rows.append(
            {
                **item,
                "status": "na",
                "detail": "The package has no data files (csv, Excel, SPSS, ...).",
            }
        )
    elif skipped:
        rows.append(
            {
                **item,
                "status": "warn",
                "detail": f"{_n(skipped, 'data file')} of {n_files} not scanned (too big, "
                "unreadable or over the file limit).",
            }
        )
    else:
        rows.append(
            {
                **item,
                "status": "pass",
                "detail": f"Scanned {_n(scanned, 'data file')} (the first {max_rows} rows of each).",
            }
        )
    return checklist_frame(rows)


def _summary(result: PiiResult, hits_rows: Sequence[Mapping[str, Any]]) -> str:
    if result.n_files == 0:
        return "The package has no data files to scan for personal data."
    if result.n_scanned == 0:
        return f"None of the {_n(result.n_files, 'data file')} could be scanned."
    if not hits_rows:
        text = f"No personal data found by value in {_n(result.n_scanned, 'data file')}."
    else:
        parts = [
            f"{_n(n, 'column')} with {d.noun}"
            for d in DETECTORS
            if (n := result.flagged.get(d.id, 0))
        ]
        files = len({h["path"] for h in hits_rows})
        text = (
            f"Possible personal data in {_n(files, 'file')}: {', '.join(parts)} "
            f"({_n(result.n_scanned, 'data file')} scanned). A person has to check these."
        )
    if result.n_skipped:
        text += f" {_n(result.n_skipped, 'data file')} not scanned."
    return text


# -- report ----------------------------------------------------------------------------

_SECTION: dict[str, tuple[str, str]] = {
    "bsn": (
        "Dutch citizen service numbers (BSN)",
        "A BSN identifies a person directly. Remove the column from the data that is shared, "
        "or replace it with a random study number and keep the key that links the two outside "
        "the package.",
    ),
    "student-number": (
        "Student numbers",
        "A student number identifies a person at their institution. Remove it or replace it "
        "with a random study number, and keep the key outside the package.",
    ),
    "phone": (
        "Phone numbers",
        "A phone number identifies a person. Remove the column unless it is needed and covered "
        "by the participants' consent.",
    ),
    "ipv6": (
        "IPv6 addresses",
        "An IP address can identify a person or a household. Remove it, or keep only what the "
        "analysis needs (for example the country).",
    ),
    "postcode": (
        "Dutch postcodes",
        "A full postcode points to a street, and with other details to a person. Keep the "
        "first digits or the municipality instead.",
    ),
    "person-name": (
        "Columns with people's names",
        "Names identify people. Remove the column, or replace the names with codes and keep "
        "the key outside the package.",
    ),
}


def pii_report(result: PiiResult) -> list[Any]:
    """The report blocks: an overview, then a collapsible section for each kind of personal data found.

    No block shows a value, only file, column and counts.
    """
    import pandas as pd

    from metacheck.report import collapse_section, scroll_table

    items = [
        f"- **{r['title']}**: {_STATUS_WORDS[r['status']]}. {r['detail']}"
        for r in result.checklist.to_dict("records")
    ]
    intro = (
        f"We looked at the first {result.max_rows} rows of {_n(result.n_scanned, 'data file')} "
        "for values that look like personal data. These are hints, not a verdict: a person has "
        "to check each one. This report never shows the values it found."
    )
    report: list[Any] = [intro + "\n\n" + "\n".join(items)]
    hits = result.hits
    for rule, (title, fix) in _SECTION.items():
        sub = hits[hits["rule"] == rule]
        if sub.empty:
            continue
        table = pd.DataFrame(
            {
                "File": [
                    p + (f" ({s})" if s else "")
                    for p, s in zip(sub["path"].tolist(), sub["sheet"].tolist(), strict=True)
                ],
                "Column": sub["column"].tolist(),
                "Cells": [
                    f"{h} of {c}"
                    for h, c in zip(sub["hits"].tolist(), sub["checked"].tolist(), strict=True)
                ],
            }
        )
        block = collapse_section(
            [
                "Found by looking at the values of these columns. Check whether they are what "
                "they look like.",
                scroll_table(table, maxrows=10, escape=True),
                f"**What to do:** {fix}",
            ],
            title=f"{title} ({len(table)})",
            callout="note",
            collapse=True,
        )
        report.extend(block) if isinstance(block, list) else report.append(block)
    return report


_STATUS_WORDS = {
    "fail": "needs fixing",
    "warn": "could be improved",
    "manual": "check by hand",
    "pass": "ok",
    "na": "not applicable",
}
