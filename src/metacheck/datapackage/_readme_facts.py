"""What a README can say about a package, found by rules: files, formats, naming, variables, licence.

These are the facts that :mod:`metacheck.datapackage.readme` writes into a drafted README. Each
function reads the package's file listing, or, for the variables, the header and the types of the
data files, and returns plain values; the wording is the draft's business.

**No cell value leaves this module.** A data file is read to learn its variables' names, their
types and how many cells are empty, and the objects that carry that (:class:`Variable`,
:class:`TableProfile`) have no place for a value. A name that is itself a value (a file without a
header row has its first data row there) is replaced by its position.
"""

from __future__ import annotations

import datetime as _dt
import re
import warnings
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metacheck.datapackage._listing import _readable

__all__ = [
    "LicenceFacts",
    "TableProfile",
    "TableScan",
    "Variable",
    "candidate_files",
    "describe_licence",
    "file_tree",
    "format_counts",
    "human_size",
    "naming_notes",
    "scan_tables",
    "top_level_entries",
]

#: Folders that hold more files than this are shown with a summary of the rest.
_FILES_PER_FOLDER = 12
_FILES_SHOWN = 8
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def show_name(name: str) -> str:
    """A file or folder name that is safe to put in a line of text (no control characters)."""
    return _CONTROL.sub("?", _readable(name))


def human_size(size: int) -> str:
    """*size* bytes as ``"3.2 MB"``."""
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"  # pragma: no cover


# -- the files ----------------------------------------------------------------------------


def candidate_files(files: Any, readmes: Any) -> Any:
    """The files a README would list: in the package, not hidden or junk, and not its own README.

    *readmes* is :func:`~metacheck.datapackage.docs.find_readmes`'s table. The READMEs at the top
    are left out (the file being written is one of them); a README in a folder is listed.
    """
    from metacheck.datapackage.docs import _candidates

    cand = _candidates(files)
    if len(cand) == 0 or len(readmes) == 0:
        return cand
    top = {str(r.rel) for r in readmes.itertuples() if int(r.depth) == 0}
    return cand.loc[[str(rel) not in top for rel in cand["rel"]]]


@dataclass
class _Node:
    folders: dict[str, _Node] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.files) + sum(f.total for f in self.folders.values())


def _build(file_rels: Sequence[str], dir_rels: Sequence[str]) -> _Node:
    root = _Node()
    for rel in dir_rels:
        node = root
        for part in rel.split("/"):
            node = node.folders.setdefault(part, _Node())
    for rel in file_rels:
        *parents, name = rel.split("/")
        node = root
        for part in parents:
            node = node.folders.setdefault(part, _Node())
        node.files.append(name)
    return root


def _render(node: _Node, levels: int, prefix: str = "") -> list[str]:
    entries: list[tuple[str, _Node | None, str]] = [
        (name, node.folders[name], "")
        for name in sorted(node.folders, key=lambda n: (n.casefold(), n))
    ]
    files = sorted(node.files, key=lambda n: (n.casefold(), n))
    shown = files
    rest: list[str] = []
    if len(files) > _FILES_PER_FOLDER:
        shown, rest = files[:_FILES_SHOWN], files[_FILES_SHOWN:]
    entries.extend((name, None, "") for name in shown)
    if rest:
        kinds = Counter(_ext_label(n).lower() for n in rest)
        summary = ", ".join(f"{k} {v}" for k, v in kinds.most_common(4))
        more = f" and {len(kinds) - 4} more kinds" if len(kinds) > 4 else ""
        entries.append((f"... {len(rest)} more files ({summary}{more})", None, "summary"))
    lines: list[str] = []
    for i, (name, child, kind) in enumerate(entries):
        last = i == len(entries) - 1
        branch = "└── " if last else "├── "
        if child is None:
            lines.append(f"{prefix}{branch}{name if kind else show_name(name)}")
            continue
        if levels <= 1 and (child.folders or child.files):
            lines.append(f"{prefix}{branch}{show_name(name)}/  ({child.total} files)")
            continue
        lines.append(f"{prefix}{branch}{show_name(name)}/")
        lines.extend(_render(child, levels - 1, prefix + ("    " if last else "│   ")))
    return lines


def file_tree(file_rels: Sequence[str], dir_rels: Sequence[str], max_lines: int = 120) -> list[str]:
    """The folder tree as lines (``├── data/``), at most about *max_lines* of them.

    Folders come first, then files, each sorted by name. A folder with more than 12 files shows
    8 and says how many more there are and of what kind. When the tree is too long its deepest
    folders are folded into one line with their number of files, level by level.
    A line never starts with a file name, so the tree cannot be taken for a code fence.
    """
    root = _build(file_rels, dir_rels)
    levels = 99
    lines = _render(root, levels)
    while len(lines) > max_lines and levels > 1:
        levels = min(levels, _depth(root)) - 1
        lines = _render(root, max(levels, 1))
    if len(lines) > max_lines:
        cut = len(lines) - max_lines + 1
        lines = [*lines[: max_lines - 1], f"... {cut} more lines left out"]
    return [".", *lines]


def _depth(node: _Node) -> int:
    return 1 + max((_depth(f) for f in node.folders.values()), default=0)


def _ext_label(name: str) -> str:
    stem = name.lstrip(".")
    ext = stem.rsplit(".", 1)[1] if "." in stem else ""
    return f".{ext}" if ext else "no extension"


def format_counts(files: Any) -> list[tuple[str, int]]:
    """The file formats present, as ``(".csv", 12)``, most files first, then by name.

    ``""`` stands for files without an extension. Extensions are counted without regard to case and
    shown as most files spell them (``.R`` if more files have ``.R`` than ``.r``).
    """
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    for name in files["name"]:
        label = _ext_label(str(name))
        counts[label.lower()] += 1
        spellings.setdefault(label.lower(), Counter())[label] += 1
    shown = [(spellings[k].most_common(1)[0][0], n) for k, n in counts.items()]
    return sorted(
        ((("" if e == "no extension" else e), n) for e, n in shown),
        key=lambda kv: (-kv[1], kv[0].lower()),
    )


def top_level_entries(
    files: Any, dirs: Any, licence_names: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """The folders and files at the top of the package: what a README is expected to describe.

    Each entry is ``{"name", "kind": "folder"|"file", "files": n}`` (``files`` counts the files in
    a folder). Licence files are left out, as the README checks do.
    """
    from metacheck.datapackage.docs import _candidates

    skip = set(licence_names)
    out: list[dict[str, Any]] = [
        {"name": str(row.name), "kind": "folder", "files": int(row.n_files_total)}
        for row in _candidates(dirs).itertuples()
        if int(row.depth) == 1
    ]
    out.extend(
        {"name": str(row.name), "kind": "file", "files": 1}
        for row in files.itertuples()
        if int(row.depth) == 0 and str(row.name) not in skip
    )
    return out


# -- naming convention ------------------------------------------------------------------------

#: Share of the names with several words that must use one separator style for it to be a convention.
_STYLE_SHARE = 0.8
_MIN_NAMES = 5


def naming_notes(files: Any, dirs: Any) -> list[str]:
    """What the names of files and folders have in common, as sentences; empty when nothing is evident.

    * how words are separated (underscores, hyphens, both or capitals), when at least five names have
      several words and at least 80% of them use one style (the style rules of the file names check);
    * that dates in names are written ``YYYY-MM-DD``, when there are dates and all are;
    * that names are in lower case, when at least five names have letters and none has a capital.
    """
    from metacheck.datapackage.docs import _candidates
    from metacheck.datapackage.files import _STYLE_LABEL, _mask, _scan_dates, _stem, _style

    stems = [_stem(show_name(str(n))) for n in files["name"]]
    stems += [show_name(str(n)) for n in _candidates(dirs)["name"]]
    spans = [_scan_dates(s) for s in stems]
    notes: list[str] = []

    styles = [_style(_mask(s, sp)) for s, sp in zip(stems, spans, strict=True)]
    counts = Counter(s for s in styles if s is not None)
    total = sum(counts.values())
    if total >= _MIN_NAMES:
        order = ("_", "-", "-_", "camel")
        dominant, share = max(counts.items(), key=lambda kv: (kv[1], -order.index(kv[0])))
        if share >= _STYLE_SHARE * total:
            notes.append(f"Words in names are separated with {_STYLE_LABEL[dominant]}.")

    dates = [sp for group in spans for sp in group if sp.kind == "date"]
    if dates and all(not sp.problem for sp in dates):
        notes.append("Dates in names are written as YYYY-MM-DD.")

    lettered = [s for s in stems if any(c.isalpha() for c in s)]
    if len(lettered) >= _MIN_NAMES and all(s == s.lower() for s in lettered):
        notes.append("Names are in lower case.")
    return notes


# -- licence -----------------------------------------------------------------------------------

_CC_VARIANT = re.compile(
    r"\bCC[ -]BY((?:[ -](?:NC|SA|ND))*)(?:[ -](\d\.\d))?(?![A-Za-z0-9])", re.IGNORECASE
)
_CC_CONDITIONS = re.compile(r"non-?\s?commercial|share-?\s?alike|no[ -]?derivativ", re.IGNORECASE)


@dataclass
class LicenceFacts:
    """What the package says about its licence."""

    files: list[str] = field(default_factory=list)  # licence files, relative to the package
    names: list[str] = field(default_factory=list)  # licences recognised in them
    named_in_readme: list[str] = field(
        default_factory=list
    )  # ... in the README, if there is no file


def _refine(names: list[str], text: str) -> list[str]:
    """Make a recognised ``CC BY`` as exact as *text* lets it be.

    The licence table recognises "creative commons" and ``CC BY`` as ``CC BY``, which would be wrong
    for a ``CC BY-NC`` or a ``CC0`` licence, so the text decides: ``CC BY-NC-SA 4.0`` as written, "a
    Creative Commons licence" when it only mentions conditions, and no ``CC BY`` at all next to a ``CC0``.
    """
    out: list[str] = []
    for name in names:
        if name != "CC BY":
            out.append(name)
            continue
        found = list(_CC_VARIANT.finditer(text))
        if found:
            m = max(found, key=lambda x: len(x.group(0)))
            conditions = "".join("-" + c.upper() for c in re.findall(r"NC|SA|ND", m.group(1), re.I))
            out.append("CC BY" + conditions + (f" {m.group(2)}" if m.group(2) else ""))
        elif "CC0" in names:
            continue  # "Creative Commons" is in the CC0 text too
        elif _CC_CONDITIONS.search(text):
            out.append("a Creative Commons licence")
        else:
            out.append(name)
    return list(dict.fromkeys(out))


def describe_licence(pkg: Any, files: Any, readme_text: str | None, licences: Any) -> LicenceFacts:
    """The licence files of *pkg* and the licences they, or else the README, name.

    A licence is recognised by the licence table of the README check
    (``docs_licences.json``). A ``CC BY`` that the text qualifies (``-NC``, ``-SA``) is
    named as the text has it.
    """
    from metacheck.datapackage._docs_readme import _licences_named, read_readme_text
    from metacheck.datapackage.docs import _licence_files

    facts = LicenceFacts()
    found = _licence_files(files)
    for row in found.itertuples():
        facts.files.append(str(row.rel))
        if bool(row.link):
            continue
        text = read_readme_text(Path(pkg.root) / str(row.path)) or ""
        names = _refine(_licences_named(text, licences, context_needed=False), text)
        facts.names.extend(n for n in names if n not in facts.names)
    if not facts.files and readme_text:
        named = _licences_named(readme_text, licences, context_needed=True)
        facts.named_in_readme = _refine(named, readme_text)
    return facts


# -- the variables of the data files ------------------------------------------------------------


@dataclass(frozen=True)
class Variable:
    """A column of a data file: its name, its type and how many of its cells are empty."""

    name: str
    type: str
    missing: int


@dataclass
class TableProfile:
    """A data file (or a sheet of one): its size and its variables, and nothing of its content."""

    path: str
    sheet: str
    rows: int
    capped: bool
    variables: list[Variable]
    names_hidden: bool = False  # the names looked like values, so positions are shown instead


@dataclass
class TableScan:
    """The data files that were read, and how many could not be."""

    tables: list[TableProfile] = field(default_factory=list)
    n_files: int = 0  # tabular data files in the package
    not_read: int = 0  # too big, unreadable, empty or beyond the limit
    not_read_files: list[str] = field(default_factory=list)  # their paths in the package


#: Cells that mean "no value" in a text file.
_MISSING = frozenset({"", "na", "n/a", "nan", "null", "none"})
_INTEGER = re.compile(r"[+-]?(?:0|[1-9]\d*)")
_LEADING_ZERO = re.compile(r"[+-]?0\d+")
_DECIMAL = re.compile(r"[+-]?(?:\d+[.,]\d*|[.,]\d+|\d+)(?:[eE][+-]?\d+)?")
_LOGICAL = re.compile(r"true|false", re.IGNORECASE)
_DATE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}")
_DATETIME = re.compile(
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})[T ]\d{1,2}:\d{2}"
    r"(?::\d{2}(?:\.\d+)?)?\s?(?:Z|[+-]\d{2}:?\d{2})?"
)
_TIME = re.compile(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?")
_NUMERIC_TAGS = frozenset({"integer", "decimal", "date", "date-time", "time"})


def _tag_of_text(text: str) -> str | None:
    """The type of one cell of text; ``None`` for an empty one."""
    value = text.strip()
    if value.lower() in _MISSING:
        return None
    if _INTEGER.fullmatch(value):
        return "integer"
    if _LEADING_ZERO.fullmatch(value):
        return "text"  # an identifier such as 007, not a number
    if _DECIMAL.fullmatch(value):
        return "decimal"
    if _LOGICAL.fullmatch(value):
        return "logical"
    if _DATETIME.fullmatch(value):
        return "date-time"
    if _DATE.fullmatch(value):
        return "date"
    if _TIME.fullmatch(value):
        return "time"
    return "text"


def _tag_of(value: Any) -> str | None:
    """The type of one cell of any kind; ``None`` for an empty one."""
    import numpy as np
    import pandas as pd

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, str):
        return _tag_of_text(value)
    if isinstance(value, bool | np.bool_):
        return "logical"
    if isinstance(value, int | np.integer):
        return "integer"
    if isinstance(value, float | np.floating):
        number = float(value)
        if number != number:
            return None
        return "integer" if number.is_integer() else "decimal"
    if isinstance(value, _dt.datetime):  # also pandas' Timestamp
        midnight = value.hour == value.minute == value.second == value.microsecond == 0
        return "date" if midnight else "date-time"
    if isinstance(value, _dt.date):
        return "date"
    if isinstance(value, _dt.time):
        return "time"
    return "text"


def _combine(tags: set[str]) -> str:
    if not tags:
        return "empty"
    if len(tags) == 1:
        return next(iter(tags))
    if tags <= {"integer", "decimal"}:
        return "decimal"
    if tags <= {"date", "date-time"}:
        return "date-time"
    return "text"


def column_type(values: Iterable[Any]) -> tuple[str, int]:
    """``(type, number of empty cells)`` of a column's cells. The values are not kept."""
    tags: set[str] = set()
    missing = 0
    for value in values:
        tag = _tag_of(value)
        if tag is None:
            missing += 1
        elif (
            "text" not in tags
        ):  # once it is text nothing else matters, but empty cells are still counted
            tags.add(tag)
    return _combine(tags), missing


def _frame_column(series: Any) -> tuple[str, int]:
    """:func:`column_type` of a pandas column, with fast paths for numbers, flags and dates."""
    import numpy as np
    from pandas.api import types as pt

    missing = int(series.isna().sum())
    non = series.dropna()
    if len(non) == 0:
        return "empty", missing
    if pt.is_bool_dtype(series):
        return "logical", missing
    if pt.is_integer_dtype(series):
        return "integer", missing
    if pt.is_float_dtype(series):
        values = non.to_numpy(dtype=float)
        whole = bool(np.all(np.isfinite(values)) and np.all(values == np.floor(values)))
        return ("integer" if whole else "decimal"), missing
    if pt.is_datetime64_any_dtype(series):
        midnight = bool((non.dt.normalize() == non).all())
        return ("date" if midnight else "date-time"), missing
    if pt.is_timedelta64_dtype(series):
        return "time", missing
    return column_type(series.tolist())


def _names_are_values(names: Sequence[str]) -> bool:
    """Whether most names are numbers or dates: a file without a header row has data there."""
    if not names:
        return False
    numeric = sum(1 for n in names if _tag_of_text(n) in _NUMERIC_TAGS)
    return numeric * 2 >= len(names)


def _display_names(names: Sequence[str], *, header_known: bool) -> tuple[list[str], bool]:
    """The names to show, and whether any was replaced by its position.

    A name that looks like a value (see the personal data check's rule) is shown as ``column 3``;
    so are all of them when most are numbers or dates and the header row is not known to be one.
    """
    from metacheck.datapackage.pii import _label

    if not header_known and _names_are_values(names):
        return [f"column {j}" for j in range(1, len(names) + 1)], True
    safe = [show_name(n) for n in names]
    shown = [_label(n, j) for j, n in enumerate(safe, 1)]
    return shown, any(a != b for a, b in zip(shown, safe, strict=True))


def _profile_strings(
    sheet: str,
    names: list[str],
    columns: list[list[str | None]],
    rows: int,
    capped: bool,
    path: str,
) -> TableProfile:
    shown, hidden = _display_names(names, header_known=False)
    variables = []
    for name, cells in zip(shown, columns, strict=True):
        if rows == 0:
            variables.append(Variable(name, "unknown", 0))
            continue
        kind, missing = column_type(cells)
        variables.append(Variable(name, kind, missing))
    return TableProfile(path, sheet, rows, capped, variables, hidden)


def _profile_frame(sheet: str, df: Any, capped: bool, path: str, stat_file: bool) -> TableProfile:
    names = [str(c) for c in df.columns]
    shown, hidden = _display_names(names, header_known=stat_file)
    variables = []
    for j, name in enumerate(shown):
        if len(df) == 0:
            variables.append(Variable(name, "unknown", 0))
            continue
        kind, missing = _frame_column(df.iloc[:, j])
        variables.append(Variable(name, kind, missing))
    return TableProfile(path, sheet, len(df), capped, variables, hidden)


def _read_profiles(root: Path, disk: str, path: str, ext: str, max_rows: int) -> list[TableProfile]:
    from metacheck.datapackage.pii import (
        _MAX_SHEETS,
        _label,
        _read_delimited,
        _sheet_names,
        _Unreadable,
    )

    full = root / disk
    if ext in ("csv", "tsv"):
        try:
            tables = _read_delimited(full, ext, max_rows)
        except _Unreadable:
            return []
        return [
            _profile_strings(t.sheet, t.names, t.columns, t.rows, t.capped, path) for t in tables
        ]

    from metacheck.datacheck.files import data_read_head

    sheets: list[str | None] = [None]
    labels = [""]
    if ext in ("xlsx", "xls"):
        names = _sheet_names(full)
        if len(names) > 1:
            sheets = [None, *names[1:_MAX_SHEETS]]
            labels = [_label(names[i], i + 1, "sheet") for i in range(len(sheets))]
    profiles: list[TableProfile] = []
    for i, sheet in enumerate(sheets):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = data_read_head(full, n_rows=max_rows + 1, sheet=sheet)
        except Exception:
            if i == 0:
                return []
            continue
        if df is None or df.shape[1] == 0:
            continue
        capped = len(df) > max_rows
        profiles.append(
            _profile_frame(
                labels[i],
                df.iloc[:max_rows],
                capped,
                path,
                ext in ("sav", "dta", "por", "sas7bdat"),
            )
        )
    return profiles


def scan_tables(
    pkg: Any, *, max_rows: int = 100_000, max_file_size: float = 50, max_files: int = 30
) -> TableScan:
    """Read the data files of *pkg* for their variables: names, types and empty cells; never a value.

    Reads csv, tsv, Excel, ODS, SPSS, Stata and SAS files (the files the personal data check
    reads), at most *max_rows* rows of each. Files over *max_file_size* MB and files beyond
    the first *max_files* are not read; with the unreadable and the empty ones they are counted in
    ``not_read``. Hidden files, junk and codebooks are left out.
    """
    from metacheck.datapackage.docs import _is_junk
    from metacheck.datapackage.pii import _data_files

    scan = TableScan()
    data = [d for d in _data_files(pkg) if not _is_junk(d[1].rsplit("/", 1)[-1])]
    scan.n_files = len(data)
    root = Path(pkg.root)
    for index, (disk, rel, ext, size) in enumerate(data):
        if index >= max_files or size > max_file_size * 1024 * 1024:
            scan.not_read += 1
            scan.not_read_files.append(rel)
            continue
        try:
            profiles = _read_profiles(root, disk, rel, ext, max_rows)
        except Exception:  # a data file that cannot be read must not stop the draft
            profiles = []
        if not profiles:
            scan.not_read += 1
            scan.not_read_files.append(rel)
            continue
        scan.tables.extend(profiles)
    return scan
