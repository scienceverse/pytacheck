"""File-naming convention checks (port of ``R/file-naming.R``).

Checks file names against the metacheck "machine readable FAIR data and code"
guide: no spaces or special characters, no diacritics, valid ``YYYYMMDD``
dates, an actually classifiable name, zero-padded numbering across sibling
files, and path-length budgets. Used by ``repo_check`` to surface naming
problems.

Like metacheck, this deliberately does not check letter case or
underscore-vs-dash separator consistency: metacheck's own classifiers match
keywords anywhere in a name with any separator, so nothing depends on them.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pandas as pd

from pytacheck._r import grepl, regexec, regextract_all, sub

__all__ = ["check_file_naming"]

#: R: .file_naming_severity -- "bad" rules break something real; "suggestion"
#: rules are conventions metacheck does not depend on (never affect the light).
FILE_NAMING_SEVERITY: dict[str, str] = {
    "spaces": "bad",
    "special-characters": "bad",
    "diacritics": "bad",
    "date-format": "bad",
    "unclassifiable": "bad",
    "zero-padding": "suggestion",
    "path-length-255": "bad",
    "path-length-228": "suggestion",
    "directory-length-100": "suggestion",
    "filename-length-50": "suggestion",
}

_SPECIAL_RX = "[^A-Za-z0-9._ -]"
_NON_ASCII_RX = "[^\x01-\x7f]"  # R: "[^\x01-\x7F]" (real control characters)
_DATE_RX = "[0-9]{8}"
_PADDING_RX = "^(.*?)([0-9]+)([^0-9]*)$"

_DETAIL = {
    "spaces": "contains a space",
    "special-characters": ("contains a character other than letters, digits, underscore, or dash"),
    "diacritics": "contains non-ASCII characters (e.g. accented letters)",
    "unclassifiable": (
        "could not be classified by name or extension (data_type is 'unknown'); "
        "add a recognisable keyword (data, code, materials, ...) or a known extension"
    ),
}

_COLUMNS = ["file_name", "rule", "severity", "detail"]

Row = tuple[str | None, str, str]  # (file_name, rule, detail)


# -- R helpers -----------------------------------------------------------------


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    try:
        return bool(x != x)  # NaN
    except (TypeError, ValueError):
        return False


def _chr_list(x: Any) -> list[str | None]:
    """An R character-vector argument as a list of optional strings."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        x = x.tolist()
    if isinstance(x, Iterable) and not isinstance(x, Mapping):
        return [None if _is_na(v) else str(v) for v in x]
    return [None if _is_na(x) else str(x)]


def _expand(path: str) -> str:
    """R's tilde expansion (``path.expand()``), which basename()/dirname() apply."""
    return os.path.expanduser(path) if path.startswith("~") else path


def _r_basename(path: str | None) -> str | None:
    """R ``basename()`` on Unix (trailing slashes dropped; ``NA`` stays ``NA``)."""
    if path is None:
        return None
    return _expand(path).rstrip("/").rpartition("/")[2]


def _r_dirname(path: str | None) -> str | None:
    """R ``dirname()`` on Unix (``"."`` for a bare name; ``NA`` stays ``NA``)."""
    if path is None:
        return None
    path = _expand(path)
    if path == "":
        return ""
    stripped = path.rstrip("/")
    if not stripped:
        return "/"
    head, sep, _ = stripped.rpartition("/")
    if not sep:
        return "."
    head = head.rstrip("/")
    return head if head else "/"


def _file_path_sans_ext(x: list[str | None]) -> list[str | None]:
    """``tools::file_path_sans_ext()``."""
    out: list[str | None] = sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", x)
    return out


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _valid_yyyymmdd(d: str) -> bool:
    """``!is.na(as.Date(d, format = "%Y%m%d"))`` for a run of 8 ASCII digits.

    ``%Y`` takes the first 4 digits (years 0-9999, proleptic Gregorian),
    ``%m`` and ``%d`` two digits each; the day must exist in that month.
    """
    year, month, day = int(d[:4]), int(d[4:6]), int(d[6:8])
    if not 1 <= month <= 12:
        return False
    days = 29 if month == 2 and _is_leap(year) else _MONTH_DAYS[month - 1]
    return 1 <= day <= days


def _frame(rows: Sequence[tuple[Any, ...]], columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {col: pd.Series([r[i] for r in rows], dtype="string") for i, col in enumerate(columns)}
    )


# -- per-file rules ------------------------------------------------------------


def _check_rows(file_names: list[str | None], data_types: list[str | None]) -> list[Row]:
    """``.file_naming_check_one()`` for every file at once, rows in file order.

    A name with bytes that are not valid UTF-8 is checked like any other (its
    undecodable bytes count as special, non-ASCII characters); metacheck's
    ``sub()`` refuses it and the whole check fails (U76).
    """
    bases = [_r_basename(f) for f in file_names]
    stems = _file_path_sans_ext(bases)
    special = grepl(_SPECIAL_RX, bases)
    non_ascii = grepl(_NON_ASCII_RX, bases)
    dates = regextract_all(_DATE_RX, stems)
    rows: list[Row] = []
    for i, base in enumerate(bases):
        f = file_names[i]
        if base is not None and " " in base:
            rows.append((f, "spaces", _DETAIL["spaces"]))
        if special[i]:
            rows.append((f, "special-characters", _DETAIL["special-characters"]))
        if non_ascii[i]:
            rows.append((f, "diacritics", _DETAIL["diacritics"]))
        if data_types[i] is not None and data_types[i] == "unknown":
            rows.append((f, "unclassifiable", _DETAIL["unclassifiable"]))
        rows.extend(
            (f, "date-format", f"'{d}' is not a valid YYYYMMDD date")
            for d in dates[i] or []
            if not _valid_yyyymmdd(d)
        )
    return rows


def _scalar(x: Any, what: str) -> str | None:
    values = _chr_list(x)
    if len(values) == 1:
        return values[0]
    # R: `if (grepl(" ", base, fixed = TRUE))` needs exactly one value
    if not values:
        raise ValueError("argument is of length zero")
    raise ValueError(f"the condition has length > 1 ({what})")


def _file_naming_check_one(file_name: Any, data_type: Any = None) -> pd.DataFrame:
    """Port of ``R/file-naming.R::.file_naming_check_one()``.

    Checks ONE file name (its basename) against the rules that apply to a file
    in isolation -- spaces, special characters, diacritics, classifiability
    (``data_type == "unknown"``) and ``YYYYMMDD`` dates (every run of 8 digits
    in the name without its extension). Returns the violations as a data frame
    with columns ``rule`` and ``detail`` (0 rows when clean).
    """
    name = _scalar(file_name, "file_name")
    dtype = None if data_type is None else _chr_list(data_type)
    rows = _check_rows([name], [dtype[0] if dtype else None])
    return _frame([(r[1], r[2]) for r in rows], ["rule", "detail"])


# -- zero padding --------------------------------------------------------------


def _padding_rows(file_names: list[str | None]) -> list[Row]:
    bases = [_r_basename(f) for f in file_names]
    parts = regexec(_PADDING_RX, bases)
    # families: prefix and suffix around the LAST run of digits, first-seen order
    families: dict[str, list[tuple[int, int]]] = {}
    for i, p in enumerate(parts):
        if p and len(p) == 4:
            families.setdefault(f"{p[1]}\r{p[3]}", []).append((i, len(p[2])))
    rows: list[Row] = []
    for members in families.values():
        if len(members) < 2:
            continue  # nothing to be inconsistent with
        target = max(w for _, w in members)
        detail = (
            "numbered inconsistently with sibling files (pad to "
            f"{target:d} digit{'' if target == 1 else 's'} so names sort in numeric order)"
        )
        rows.extend((file_names[i], "zero-padding", detail) for i, w in members if w < target)
    return rows


def _file_naming_check_padding(file_names: Any) -> pd.DataFrame:
    """Port of ``R/file-naming.R::.file_naming_check_padding()``.

    Within a family of files sharing the text around their last run of digits
    (``data-2.csv``, ``data-11.csv`` -> ``data-<N>.csv``), numbers narrower
    than the family's widest are reported (``rule = "zero-padding"``) so that
    lexicographic sort matches numeric sort. Families of one are never
    reported. Returns ``file_name``, ``rule``, ``detail``.
    """
    return _frame(_padding_rows(_chr_list(file_names)), ["file_name", "rule", "detail"])


# -- path lengths --------------------------------------------------------------


def _length_rows(file_path: list[str | None]) -> list[Row]:
    paths = [None if p is None else p.replace("\\", "/") for p in file_path]
    dir_part = [_r_dirname(p) for p in paths]
    dir_part = ["" if d == "." else d for d in dir_part]
    base_part = [_r_basename(p) for p in paths]

    def nchar(x: list[str | None]) -> list[int | None]:
        return [None if s is None else len(s) for s in x]

    rows: list[Row] = []

    def add_rows(actual: list[int | None], rule: str, budget: int) -> None:
        hit = [None if a is None else a > budget for a in actual]
        if not any(h is True for h in hit):
            if any(h is None for h in hit):
                # R: `if (!any(hit))` with NA
                raise ValueError("missing value where TRUE/FALSE needed")
            return
        for p, h, a in zip(paths, hit, actual, strict=True):
            if h is None:  # x[NA] is NA
                rows.append(
                    (None, rule, f"{rule} is NA characters, over the {budget}-character budget")
                )
            elif h:
                rows.append(
                    (p, rule, f"{rule} is {a:d} characters, over the {budget}-character budget")
                )

    n_path = nchar(paths)
    add_rows(n_path, "path-length-255", 255)
    add_rows(n_path, "path-length-228", 228)
    add_rows(nchar(dir_part), "directory-length-100", 100)
    add_rows(nchar(base_part), "filename-length-50", 50)
    return rows


def _file_naming_check_length(file_path: Any) -> pd.DataFrame:
    """Port of ``R/file-naming.R::.file_naming_check_length()``.

    Path-length budgets of the FAIR guide: 255 characters for the whole
    relative path (``path-length-255``), 228 allowing for a typical Downloads
    prefix (``path-length-228``), 100 for the directory part
    (``directory-length-100``) and 50 for the file name
    (``filename-length-50``). Backslashes count as separators. Returns
    ``file_name`` (the path), ``rule``, ``detail``, grouped by rule.
    """
    return _frame(_length_rows(_chr_list(file_path)), ["file_name", "rule", "detail"])


# -- check_file_naming() -------------------------------------------------------


def _rep_len(x: Any, n: int) -> list[str | None]:
    values = _chr_list(x) if x is not None else [None]
    if not values:
        values = [None]
    return [values[i % len(values)] for i in range(n)]


def check_file_naming(file_name: Any, file_path: Any = None, data_type: Any = None) -> pd.DataFrame:
    """Check repository files against the FAIR file-naming conventions.

    Port of ``R/file-naming.R::check_file_naming()``.

    Parameters
    ----------
    file_name:
        file basenames (a string or a sequence of strings).
    file_path:
        repo-relative paths, used only for the path-length budgets; ``None``
        (the default) means R's default ``file_path = file_name``.
    data_type:
        an optional ``data_check`` semantic type per file (recycled; see
        ``data_classify_files()``), used only for the classifiability rule.
        ``None`` is R's ``NA_character_``.

    Returns
    -------
    A data frame with one row per violation: ``file_name``, ``rule``,
    ``severity`` (``"bad"`` or ``"suggestion"``) and ``detail``; the per-file
    rules come first (in file order), then zero padding, then path lengths.
    0 rows when every file is clean.

    Notes
    -----
    ``"bad"`` rules: spaces, special characters, diacritics, invalid
    ``YYYYMMDD`` dates, ``data_type == "unknown"`` and the hard 255-character
    path limit. ``"suggestion"`` rules: unpadded sibling numbering and the
    228/100/50-character budgets. As in R, a missing path raises
    ``ValueError("missing value where TRUE/FALSE needed")`` unless another
    path breaks every length budget.
    """
    names = _chr_list(file_name)
    n = len(names)
    if n == 0:
        return _frame([], _COLUMNS)
    paths = names if file_path is None else _chr_list(file_path)
    data_types = _rep_len(data_type, n)

    rows = _check_rows(names, data_types)
    rows += _padding_rows(names)
    rows += _length_rows(paths)
    return _frame(
        [(f, rule, FILE_NAMING_SEVERITY.get(rule), detail) for f, rule, detail in rows],
        _COLUMNS,
    )
