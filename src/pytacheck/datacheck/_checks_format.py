"""Source-format detection and header repair (private; see :mod:`pytacheck.datacheck.checks`).

Port of the Qualtrics, "leading metadata-row / offset-header repair" and
trial-level (Behaverse / Inquisit / jsPsych / PsychoPy / E-Prime) sections of
``R/data_check_helpers.R``.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from pytacheck._r.regex import grepl, regexec
from pytacheck.datacheck._checks_rvec import (
    RVec,
    as_numeric_str,
    chr,
    df_columns,
    row_as_character,
    rvec,
    scalar_chr,
    tolower,
    toupper,
    trim,
)

__all__ = [
    "_QUALTRICS_META_COLS",
    "_as_num_safe",
    "_bh_is_trial_level_file",
    "_detect_header_row",
    "_eprime_is_export",
    "_is_junk_above_header",
    "_is_placeholder_name",
    "_numeric_col_fraction",
    "_qualtrics_col_stem",
    "_qualtrics_is_display_order",
    "_qualtrics_is_header_row",
    "_qualtrics_key",
    "_qualtrics_tag_cols",
    "_row_duplication",
    "data_check_is_behaverse",
    "data_check_is_inquisit",
    "data_check_is_jspsych",
    "data_check_is_psychopy",
    "data_check_is_qualtrics",
    "data_promote_header_row",
    "data_strip_qualtrics_header",
]

#: R: .qualtrics_meta_cols -- reserved Qualtrics metadata column keys -> tag
_QUALTRICS_META_COLS: dict[str, str] = {
    "startdate": "qualtrics_start",
    "enddate": "qualtrics_end",
    "status": "qualtrics_status",
    "ipaddress": "qualtrics_ip",
    "progress": "qualtrics_progress",
    "durationinseconds": "qualtrics_duration",
    "finished": "qualtrics_finished",
    "recordeddate": "qualtrics_recorded",
    "responseid": "qualtrics_responseid",
    "recipientlastname": "qualtrics_recipient",
    "recipientfirstname": "qualtrics_recipient",
    "recipientemail": "qualtrics_email",
    "externaldatareference": "qualtrics_externalref",
    "externalreference": "qualtrics_externalref",
    "locationlatitude": "qualtrics_lat",
    "locationlongitude": "qualtrics_lon",
    "distributionchannel": "qualtrics_channel",
    "userlanguage": "qualtrics_language",
}


def _key(nm: str | None) -> str | None:
    if nm is None:
        return None
    low = tolower(nm) or ""
    return "".join(c for c in low if "a" <= c <= "z" or "0" <= c <= "9")


def _qualtrics_key(nm: Any) -> Any:
    """``gsub("[^a-z0-9]", "", tolower(nm))``: a name's Qualtrics lookup key.

    Port of ``R/data_check_helpers.R::.qualtrics_key()`` (vectorised: a
    string gives a string, a vector a list).
    """
    if isinstance(nm, str) or nm is None:
        return _key(nm)
    return [_key(s) for s in chr(nm)]


def _qualtrics_tag_cols(col_names: Any) -> list[str | None]:
    """The Qualtrics metadata tag of each column name (``None`` when not metadata).

    Port of ``R/data_check_helpers.R::.qualtrics_tag_cols()``.
    """
    return [None if k is None else _QUALTRICS_META_COLS.get(k) for k in map(_key, chr(col_names))]


def _qualtrics_col_stem(nm: Any) -> str | None:
    """The scale-block stem of a ``<stem>_<int>`` Qualtrics column (``TIPI_1`` -> ``TIPI``).

    Port of ``R/data_check_helpers.R::.qualtrics_col_stem()``; ``None`` for
    metadata columns and names whose stem has fewer than two letters.
    """
    if nm is not None and not isinstance(nm, str):
        v = chr(nm)
        if len(v) > 1:  # `is.na(nm) || ...` on a vector
            raise ValueError(f"'length = {len(v)}' in coercion to 'logical(1)'")
    nm = scalar_chr(nm)
    if nm is None or nm == "":
        return None
    if _qualtrics_tag_cols([nm])[0] is not None:
        return None
    m = regexec("^(.*[A-Za-z].*)_([0-9]+)$", nm)
    if len(m) != 3:
        return None
    stem = m[1]
    if sum(1 for c in stem if "A" <= c <= "Z" or "a" <= c <= "z") < 2:
        return None
    return stem


def _qualtrics_is_display_order(col_names: Any) -> list[bool]:
    """Which columns are Qualtrics display-order (``<Question>_DO_...``) metadata?

    Port of ``R/data_check_helpers.R::.qualtrics_is_display_order()``.
    """
    return list(grepl(r"_DO(_|$)", chr(col_names), perl=True))


def _col_by_name(df: pd.DataFrame, name: str) -> pd.Series:
    """``df[[name]]`` (the first column of that name)."""
    pos = [j for j, c in enumerate(df.columns) if c == name]
    return df.iloc[:, pos[0]]


def data_check_is_qualtrics(df: Any, min_meta: int = 4) -> bool:
    """Detect whether a data frame is a Qualtrics survey export.

    Port of ``R/data_check_helpers.R::data_check_is_qualtrics()``: at least
    *min_meta* distinct reserved metadata columns, or two plus a
    ``ResponseId`` column of ``R_...`` ids or a leftover ``ImportId`` cell.
    """
    if df is None or df.shape[1] == 0:
        return False
    names = [str(c) for c in df.columns]
    tags = _qualtrics_tag_cols(names)
    n_meta = len({t for t in tags if t is not None})
    if n_meta >= min_meta:
        return True
    if n_meta >= 2:
        rid = [nm for nm in names if _key(nm) == "responseid"]
        if rid:
            v = [s for s in chr(_col_by_name(df, rid[0])) if s is not None and s != ""]
            if v and sum(grepl("^R_[A-Za-z0-9]{6,}$", v)) / len(v) >= 0.5:
                return True
        for col in df_columns(df):
            if any(s is not None and "ImportId" in s for s in chr(col.iloc[:3])):
                return True
    return False


def _qualtrics_is_header_row(row_vals: Any) -> bool:
    """Is a row a Qualtrics secondary-header row (question text or ``ImportId`` JSON)?

    Port of ``R/data_check_helpers.R::.qualtrics_is_header_row()``.
    """
    vals = [t for s in chr(row_vals) if s is not None and (t := trim(s)) != ""]  # type: ignore[misc]
    if not vals:
        return False
    if any("ImportId" in s for s in vals):
        return True
    n_meta = sum(1 for s in vals if _key(s) in _QUALTRICS_META_COLS)
    return n_meta >= 4 if len(vals) >= 4 else n_meta == len(vals)


class _Rows:
    """``as.character(df[i, , drop = TRUE])`` for rows of *df* (0-based *i*).

    A one-column frame gives the cell itself (``drop = TRUE``); otherwise the
    row is a list whose elements ``as.character()`` deparses.
    """

    __slots__ = ("cols", "kinds", "levels")

    def __init__(self, df: pd.DataFrame, nrows: int | None = None) -> None:
        # Only the first *nrows* rows are read. A typed column is sliced before
        # conversion (a categorical keeps its levels); an object column's R type
        # comes from all of its elements, so it is converted whole.
        self.cols = [
            rvec(c) if nrows is None else _head_rvec(c, max(nrows, 0)) for c in df_columns(df)
        ]
        self.kinds = [c.kind for c in self.cols]
        self.levels = [c.levels for c in self.cols]

    def __call__(self, i: int) -> list[str | None]:
        if len(self.cols) == 1:
            c = self.cols[0]
            return chr(RVec(c.kind, [c.values[i]], c.levels))
        return row_as_character([c.values[i] for c in self.cols], self.kinds, self.levels)


def _head_rvec(col: pd.Series, k: int) -> RVec:
    """The first *k* values of ``rvec(col)``, converting only those where the type allows.

    An object column's R type depends on all of its elements, and an integer
    column holding a value beyond R's integer range is a double column, so
    those are typed from the whole column.
    """
    dtype = col.dtype
    whole = pd.api.types.is_object_dtype(dtype)
    if not whole and pd.api.types.is_integer_dtype(dtype):
        s = col.dropna()
        whole = bool(len(s)) and max(abs(int(s.max())), abs(int(s.min()))) > 2147483647
    if whole:
        v = rvec(col)
        return RVec(v.kind, v.values[:k], v.levels)
    return rvec(col.iloc[:k])


def _is_character_column(col: pd.Series) -> bool:
    return rvec(col).kind == "character"


def _numeric_column(values: list[str | None]) -> pd.Series:
    return pd.Series(
        [f if (f := as_numeric_str(v)) is not None else float("nan") for v in values],
        dtype="float64",
    )


def data_strip_qualtrics_header(df: Any, max_strip: int = 2) -> Any:
    """Strip Qualtrics secondary-header rows and re-type the columns.

    Port of ``R/data_check_helpers.R::data_strip_qualtrics_header()``: drops
    leading rows that look like Qualtrics header rows (at most *max_strip*)
    and turns character columns that are now fully numeric into numbers.
    Returns a new data frame (the input is not modified).
    """
    if df is None or len(df) == 0:
        return df
    n_scan = min(int(max_strip), len(df))
    row = _Rows(df, n_scan)
    drop = 0
    for i in range(n_scan):
        if _qualtrics_is_header_row(row(i)):
            drop = i + 1
        else:
            break
    if drop == 0:
        return df
    out = df.iloc[drop:].reset_index(drop=True)
    return _retype_numeric(out)


def _retype_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce character columns whose non-blank values all parse as numbers."""
    cols: dict[int, pd.Series] = {}
    for j in range(df.shape[1]):
        col = df.iloc[:, j]
        if not _is_character_column(col):
            continue
        v = [trim(s) for s in chr(col)]
        nonempty = [s for s in v if s is not None and s != ""]
        if not nonempty:
            continue
        if all((f := as_numeric_str(s)) is not None and f == f for s in nonempty):
            cols[j] = _numeric_column(v)
    if not cols:
        return df
    out = df.copy(deep=False)
    for j, s in cols.items():
        s.index = out.index
        out.isetitem(j, s)
    return out


# -- offset-header repair -------------------------------------------------------------


def _as_num_safe(x: Any) -> list[float | None]:
    """``as.numeric(iconv(x, to = "UTF-8", sub = ""))`` (NaN for ``"NaN"``).

    Port of ``R/data_check_helpers.R::.as_num_safe()``.
    """
    out: list[float | None] = []
    for s in chr(x):
        if s is not None:
            try:
                s.encode("utf-8")
            except UnicodeEncodeError:
                s = s.encode("utf-8", "surrogateescape").decode("utf-8", "ignore")
        out.append(as_numeric_str(s))
    return out


_PLACEHOLDER_RES = (
    (r"^.*\.\.\.\d+$", False),
    (r"^(V\d+|X(\.\d+)?|col_\d+)$", False),
    (r"^Unnamed:?\.?\s*\d+$", True),
)


def _is_placeholder_name(x: Any) -> list[bool]:
    """Which names are reader-invented placeholders (``...N``, ``V3``, ``Unnamed: 2``, blank)?

    Port of ``R/data_check_helpers.R::.is_placeholder_name()``.
    """
    vals = [trim(s) for s in chr(x)]
    out = [s == "" for s in vals]
    for rx, ic in _PLACEHOLDER_RES:
        hits = grepl(rx, vals, ignore_case=ic)
        out = [a or bool(b) for a, b in zip(out, hits, strict=True)]
    return out


def _is_num_ok(s: str) -> bool:
    f = _as_num_safe([s])[0]
    return f is not None and f == f


def _numeric_col_fraction_rows(rows: list[list[str | None]], ncols: int) -> float:
    """``.numeric_col_fraction()`` of the frame built from *rows* (padded to *ncols*)."""
    if ncols == 0 or not rows:
        return 0
    ok = 0
    for j in range(ncols):
        v = [trim(r[j]) if j < len(r) else None for r in rows]
        v2 = [s for s in v if s is not None and s != ""]
        if v2 and all(_is_num_ok(s) for s in v2):
            ok += 1
    return ok / ncols


def _numeric_col_fraction(df: Any) -> float:
    """Fraction of columns whose non-empty cells are all numeric.

    Port of ``R/data_check_helpers.R::.numeric_col_fraction()``.
    """
    if df is None or df.shape[1] == 0 or len(df) == 0:
        return 0
    ok = []
    for col in df_columns(df):
        v = [s for s in (trim(t) for t in chr(col)) if s is not None and s != ""]
        ok.append(bool(v) and all(_is_num_ok(s) for s in v))
    return sum(ok) / len(ok)


def _row_duplication(vals: Any) -> float:
    """Fraction of a row's non-empty cells that repeat another cell of the row.

    Port of ``R/data_check_helpers.R::.row_duplication()``.
    """
    v = [s for s in (trim(t) for t in chr(vals)) if s is not None and s != ""]
    if not v:
        return 0
    return 1 - len(set(v)) / len(v)


def _mean(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else float("nan")


def _is_junk_above_header(
    vals: Any,
    body_numeric: float,
    max_filled: float = 0.5,
    min_dup: float = 0.6,
    min_placeholder: float = 0.5,
) -> bool:
    """Is a row junk above the real header (near-empty, a banner, or placeholders)?

    Port of ``R/data_check_helpers.R::.is_junk_above_header()``; only in the
    context of a reasonably well-typed body (*body_numeric* >= 0.3).
    """
    cells = chr(vals)
    filled = _mean([s is not None and trim(s) != "" for s in cells])
    dup = _row_duplication(cells)
    ph = _mean(_is_placeholder_name(cells))
    if body_numeric < 0.3:
        return False
    # R's `||` with NA (an empty row: mean(logical(0)) is NaN)
    if filled == filled and filled <= max_filled:
        return True
    if dup >= min_dup:
        return True
    if ph == ph and ph >= min_placeholder:
        return True
    if filled != filled or ph != ph:
        return None  # type: ignore[return-value]
    return False


_NA_LIKE = frozenset(("NA", "NAN", "NULL", "N/A", "INF", "-INF", "."))


def _detect_header_row(rows: Any, max_scan: int = 4) -> dict[str, Any]:
    """Locate the true header row of a table read WITHOUT a header.

    Port of ``R/data_check_helpers.R::.detect_header_row()``. *rows* is a list
    of character vectors (the first physical rows). Returns ``header_row``
    (the 0-based index of the header in *rows*: 0 means the first row is
    already the header -- R's ``header_row`` minus one), ``stripped`` (the rows
    above it) and ``improved`` (the body type-consistency gained).
    """
    none: dict[str, Any] = {"header_row": 0, "stripped": [], "improved": 0}
    orig = list(rows)
    rows = [chr(r) for r in orig]
    n = len(rows)
    if n < 2:
        return none
    ncols = len(rows[0])
    if ncols < 2:
        return none
    scan_n = min(int(max_scan) + 1, n - 1)
    # body_numeric[h - 1] = consistency of the rows below candidate header h (1-based)
    body_numeric = [_numeric_col_fraction_rows(rows[h:n], ncols) for h in range(1, scan_n + 1)]
    strip = 0
    while strip < scan_n - 1:
        junk = _is_junk_above_header(rows[strip], body_numeric[strip + 1])
        if junk is None:
            raise ValueError("missing value where TRUE/FALSE needed")
        if not junk:
            break
        strip += 1
    if strip < 1:
        return none
    hdr_vals = [trim(s) for s in rows[strip]]
    ph = _is_placeholder_name(hdr_vals)
    hdr_real = [
        s
        for s, p in zip(hdr_vals, ph, strict=True)
        if s is not None and s != "" and toupper(s) not in _NA_LIKE and not p
    ]
    hdr_text = [s for s, f in zip(hdr_real, _as_num_safe(hdr_real), strict=True) if f is None]
    if len(hdr_text) < 2:
        return none
    base_numeric = body_numeric[0]
    new_numeric = body_numeric[strip]
    if new_numeric < base_numeric:
        return none
    return {
        "header_row": strip,
        "stripped": orig[:strip],  # R returns the rows as given
        "improved": new_numeric - base_numeric,
    }


def _make_unique(names: list[str]) -> list[str]:
    """R ``make.unique(names)`` (``sep = "."``)."""
    seen = set(names)
    first: set[str] = set()
    cnts: dict[str, int] = {}
    out = list(names)
    for i, nm in enumerate(names):
        if nm not in first:
            first.add(nm)
            continue
        cnt = cnts.get(nm, 1)
        while f"{nm}.{cnt}" in seen:
            cnt += 1
        new = f"{nm}.{cnt}"
        out[i] = new
        seen.add(new)
        cnts[nm] = cnt + 1
    return out


def _r_rows(df: pd.DataFrame, start: int) -> pd.DataFrame:
    """``df[start:nrow(df), , drop = FALSE]`` (1-based *start*, R's ``:``)."""
    n = len(df)
    idx = list(range(start, n + 1)) if start <= n else list(range(start, n - 1, -1))
    if all(1 <= i <= n for i in idx):
        return df.iloc[[i - 1 for i in idx]]
    # rows past the end are all-NA rows, as in R
    return df.reset_index(drop=True).reindex([i - 1 for i in idx])


def data_promote_header_row(df: Any, raw_rows: Any = None, max_scan: int = 4) -> dict[str, Any]:
    """Promote a mis-placed header row and drop leading metadata rows.

    Port of ``R/data_check_helpers.R::data_promote_header_row()``. *raw_rows*
    are the first rows of the file read without a header (a list of character
    vectors); without them the reader-assigned names and *df*'s first rows are
    the scan window. Returns ``{"df": ..., "promoted": n, "stripped": [...]}``
    (``promoted`` = number of metadata rows removed above the header).
    """
    unchanged = {"df": df, "promoted": 0, "stripped": []}
    if df is None or len(df) < 2 or df.shape[1] < 2:
        return unchanged
    use_raw = raw_rows is not None and len(raw_rows) >= 2
    if use_raw:
        rows = [chr(r) for r in raw_rows]
    else:
        n_scan = min(int(max_scan), len(df))
        row = _Rows(df, n_scan)
        header_as_row = [str(c) for c in df.columns]
        body_rows = [row(i) for i in range(n_scan)]
        rows = [header_as_row, *body_rows]
    det = _detect_header_row(rows, max_scan=max_scan)
    if det["header_row"] < 1:
        return unchanged
    hdr = det["header_row"] + 1  # R's 1-based header row
    new_names_raw = [trim(s) for s in rows[hdr - 1]]
    n_strip = len(det["stripped"])
    new_names = [
        s if s is not None and s != "" else f"V{j + 1}" for j, s in enumerate(new_names_raw)
    ]
    new_names = _make_unique(new_names)
    candidate = _r_rows(df, hdr)
    if len(new_names) == candidate.shape[1]:
        candidate = candidate.copy(deep=False)
        candidate.columns = pd.Index(new_names, dtype=object)
    candidate = candidate.reset_index(drop=True)
    candidate = _retype_numeric_safe(candidate)
    return {"df": candidate, "promoted": n_strip, "stripped": det["stripped"]}


def _retype_numeric_safe(df: pd.DataFrame) -> pd.DataFrame:
    """The re-coercion loop of ``data_promote_header_row()`` (``.as_num_safe()``)."""
    cols: dict[int, pd.Series] = {}
    for j in range(df.shape[1]):
        col = df.iloc[:, j]
        if not _is_character_column(col):
            continue
        v = [trim(s) for s in chr(col)]
        ne = [s for s in v if s is not None and s != ""]
        if not ne:
            continue
        if all(f is not None and f == f for f in _as_num_safe(ne)):
            nums = _as_num_safe(v)
            cols[j] = pd.Series([float("nan") if f is None else f for f in nums], dtype="float64")
    if not cols:
        return df
    out = df.copy(deep=False)
    for j, s in cols.items():
        s.index = out.index
        out.isetitem(j, s)
    return out


# -- trial-level formats ----------------------------------------------------------------


def _eprime_is_export(path: Any) -> bool:
    """Does a file's content look like an E-Prime text export?

    Port of ``R/data_check_helpers.R::.eprime_is_export()``.
    """
    from pytacheck.datacheck.files import text_peek

    head_lines = text_peek(path, n=30)
    if not head_lines:
        return False
    if any(grepl(r"^\*\*\*\s*Header Start", head_lines)):
        return True
    return any(grepl("^(Experiment|Subject):", head_lines)) and any(
        grepl("^LevelName:", head_lines)
    )


_BH_SNIFF_EXTS = ("csv", "tsv", "dat", "iqdat", "txt")


def _file_ext(path: str) -> str:
    """``tools::file_ext(path)``."""
    m = regexec(r"\.([[:alnum:]]+)$", path)
    return m[1] if len(m) == 2 else ""


def _bh_is_trial_level_file(path: Any) -> bool:
    """Is a file a trial-level format (Behaverse / Inquisit / E-Prime / jsPsych)?

    Port of ``R/data_check_helpers.R::.bh_is_trial_level_file()``: E-Prime by
    content, delimited text by a one-row header read.
    """
    if isinstance(path, list | tuple):  # a character vector: only a single path
        if len(path) != 1:
            return False
        path = path[0]
    if path is None:
        return False
    p = os.fspath(path)
    if not os.path.exists(p):
        return False
    ext = tolower(_file_ext(p)) or ""
    if ext in ("txt", "edat", "edat2") and _eprime_is_export(p):
        return True
    if ext not in _BH_SNIFF_EXTS:
        return False
    try:
        hdr = _read_csv_header(p, "\t" if ext == "iqdat" else ",")
    except Exception:
        return False
    if hdr is None or hdr.shape[1] == 0:
        return False
    return data_check_is_behaverse(hdr) or data_check_is_inquisit(hdr) or data_check_is_jspsych(hdr)


def _read_csv_header(path: str, sep: str) -> pd.DataFrame | None:
    """``utils::read.csv(path, check.names = FALSE, nrows = 1, fileEncoding = "UTF-8-BOM", sep)``.

    The ``read.table()`` port drops a leading UTF-8 byte-order mark itself.
    """
    from pytacheck.datacheck._files_readtable import read_table

    return read_table(path, sep=sep, header=True, nrows=1)


def _names(df: Any) -> list[str]:
    return [str(c) for c in df.columns]


_BH_CHANNELS = (
    "response_numeric",
    "response_time",
    "response_validation_time",
    "trial_index",
    "response_option_index",
    "stimulus_type",
)


def data_check_is_behaverse(df: Any) -> bool:
    """Is *df* a native Behaverse tidy (or wide-pivot) export?

    Port of ``R/data_check_helpers.R::data_check_is_behaverse()``.
    """
    if df is None or df.shape[1] == 0:
        return False
    nm = _names(df)
    if "instrument_id" in nm and any(c in nm for c in _BH_CHANNELS):
        return True
    return any(grepl("_response_numeric_i[0-9]+$", nm)) and any(
        grepl("_(response_time|trial_index)_i[0-9]+$", nm)
    )


def data_check_is_inquisit(df: Any) -> bool:
    """Is *df* a Millisecond Inquisit ``.iqdat`` export?

    Port of ``R/data_check_helpers.R::data_check_is_inquisit()``.
    """
    if df is None or df.shape[1] == 0:
        return False
    nm = {tolower(c) for c in _names(df)}
    return sum(1 for c in ("subject", "blockcode", "trialcode", "latency") if c in nm) >= 3


def data_check_is_jspsych(df: Any) -> bool:
    """Is *df* a jsPsych export (``trial_type``, ``rt`` and a trial counter)?

    Port of ``R/data_check_helpers.R::data_check_is_jspsych()``.
    """
    if df is None or df.shape[1] == 0:
        return False
    nm = _names(df)
    return (
        "trial_type" in nm
        and "rt" in nm
        and any(c in nm for c in ("trial_index", "time_elapsed", "internal_node_id"))
    )


def data_check_is_psychopy(df: Any) -> bool:
    """Is *df* a PsychoPy Builder export (loop/timing suffixes or run metadata)?

    Port of ``R/data_check_helpers.R::data_check_is_psychopy()``.
    """
    if df is None or df.shape[1] == 0:
        return False
    nm = _names(df)
    return (
        any(grepl("[.]this(N|Index|RepN|TrialN)$", nm))
        or any(grepl("[.](started|stopped)$", nm))
        or any(c in nm for c in ("psychopyVersion", "frameRate", "expName"))
    )
