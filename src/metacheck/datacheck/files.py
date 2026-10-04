"""data_check helpers, part 1: files.

Port of ``R/data_check_helpers.R`` (from the top of the file through
``.read_rdata_isolated()``, ``rscript_path()`` and ``.detect_likert_scale()``):

* file classification -- :func:`data_classify_files`, :func:`data_format`,
  ``.is_r_package_file()``, ``.data_doc_role()`` and the extension registry;
* manifests -- :func:`data_is_manifest`, ``.data_check_write_manifest()`` and
  :func:`manifest_merge` (written byte-for-byte as jsonlite does);
* study grouping -- :func:`data_group_llm`, :func:`data_study_roster` and the
  deterministic ``.data_group_*`` passes;
* text classification -- :func:`text_peek`, :func:`txt_classify_content` and
  the delimiter / header sniffers;
* reading data files -- :func:`data_read_head` (fread-, readxl-, readODS-,
  haven- and readRDS-faithful readers live in the ``_files_*`` submodules).

Data frames read from files follow these conventions (R -> pandas):
character -> ``string``; integer -> ``Int64``; double -> ``float64``;
logical -> ``boolean``; factor -> ``category``; ``Date``/``IDate`` ->
``object`` column of :class:`datetime.date`; ``POSIXct`` -> UTC
``datetime64`` (converted to the column's ``tzone`` when R had one);
bit64 ``integer64`` -> ``Int64``. Known limits of these types: a ``Date``
holds whole days, so a fractional R ``Date`` (an SPSS date carrying a time of
day) is floored and a date outside years 1-9999 becomes ``None``; an ODS
date-time outside pandas' ``Timestamp`` range (years 1677-2262) becomes
``NaT``. So out-of-range values count as missing here, and fractional dates
on the same day count as one distinct value (R's ``unique()`` tells them
apart, although its ``as.character()`` shows the floored day too). Per-column
R attributes that matter
downstream (haven's ``label``, ``labels``, ``na_values``, ``na_range``,
``format.*``, ``display_width``, plus ``class``, ``tzone`` and ``units``) are
kept in ``df.attrs["col_attrs"][column]`` exactly as R's ``attributes()``
would show them after reading -- so e.g. ``head()`` on an ``.rds`` frame
drops the ``label`` of an unclassed column, as in R. ``labels`` is a list of
``(label text, code)`` pairs (R's named vector ``c(label = code)``, repeated
label texts included); pyreadstat reports SPSS/Stata value labels as
``{code: label}``, so a label set that repeats a code keeps only one of them
(haven writes such sets itself: string value labels in a ``.dta`` all get
code 0).
The per-column Latin-1 repair counts of ``.utf8_repair_df()`` are in
``df.attrs["utf8_repaired"]``.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import math
import os
import re
import shutil
import sys
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from metacheck._env import env_get
from metacheck._r.base import plural, slashed, trimws
from metacheck._r.regex import compile_r, gregexpr_all, grepl, gsub, regexec, strsplit, sub
from metacheck.datacheck._files_registry import EXT_REGISTRY
from metacheck.datacheck._strings import RAW_STRING, file_ext, tolower_checked
from metacheck.fileinfo._strings import invalid_utf8

__all__ = [
    "data_classify_files",
    "data_format",
    "data_group_llm",
    "data_is_manifest",
    "data_read_head",
    "data_study_roster",
    "manifest_merge",
    "rscript_path",
    "text_peek",
    "txt_classify_content",
]

# -----------------------------------------------------------------------------
# File-type crosswalk and the extension registry
# -----------------------------------------------------------------------------

#: R: .data_check_types
_DATA_CHECK_TYPES = ("data", "code", "documentation", "materials", "output", "unknown")

#: R: .file_type_crosswalk (metacheck::file_types$type -> data_check type)
_FILE_TYPE_CROSSWALK = {
    "data": "data",
    "code": "code",
    "stats": "code",
    "exec": "materials",
    "config": "materials",
    "audio": "materials",
    "video": "materials",
    "image": "materials",
    "3D": "materials",
    "font": "materials",
    "book": "documentation",
    "slide": "documentation",
    "text": "documentation",
    "web": "documentation",
    "archive": "unknown",
}

#: R: .fixed_ext_type -- format-locked data_check type by extension
_FIXED_EXT_TYPE: dict[str, str] = {
    ext: dtype for ext, dtype, _r, _l, _m in EXT_REGISTRY if dtype is not None
}

#: R: .readable_extensions -- every extension data_read_head() can read
_READABLE_EXTENSIONS: tuple[str, ...] = tuple(ext for ext, _t, ok, _l, _m in EXT_REGISTRY if ok)

#: R: .data_check_llm_batch
_DATA_CHECK_LLM_BATCH = 50

#: R: .data_group_seed (``8675309L``)
_DATA_GROUP_SEED = 8675309

#: R: .blob_row_min_bytes
_BLOB_ROW_MIN_BYTES = 4096

#: R: .data_check_col_types
_DATA_CHECK_COL_TYPES = (
    "continuous",
    "binary",
    "categorical",
    "ordinal",
    "likert",
    "date",
    "id",
    "text",
    "continuous_comma_decimal",
    "continuous_outliers_excluded",
    "empty",
    "constant",
    "unknown",
)

#: R: .CODE_READ_FNS -- functions whose first quoted argument names a data file
_CODE_READ_FNS = "|".join(
    (
        "read_csv2?",
        r"read\.csv2?",
        "read_tsv",
        "read_delim",
        r"read\.delim",
        "read_table2?",
        r"read\.table",
        "readRDS",
        "read_rds",
        "read_excel",
        "read_xlsx",
        "read_xls",
        "read_sav",
        "read_dta",
        "read_sas",
        "read_spss",
        "fread",
        "read_json",
        "fromJSON",
        "read_feather",
        "read_parquet",
        "write_csv2?",
        r"write\.csv2?",
        "write_tsv",
        "write_delim",
        "saveRDS",
        "write_rds",
        "write_xlsx",
        "write_sav",
        "write_dta",
        "write_feather",
        "write_parquet",
        "load",
        "save",
    )
)


@functools.cache
def ext_registry() -> pd.DataFrame:
    """R's ``.ext_registry`` as a data frame (ext, data_type, readable, code_lang, mime)."""
    cols = list(zip(*EXT_REGISTRY, strict=True))
    return pd.DataFrame(
        {
            "ext": pd.Series(cols[0], dtype="string"),
            "data_type": pd.Series(cols[1], dtype="string"),
            "readable": pd.Series(cols[2], dtype="boolean"),
            "code_lang": pd.Series(cols[3], dtype="string"),
            "mime": pd.Series(cols[4], dtype="string"),
        }
    )


# -----------------------------------------------------------------------------
# small base-R helpers
# -----------------------------------------------------------------------------


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    return isinstance(x, float) and math.isnan(x)


def _as_list(x: Any) -> list[Any]:
    """An R vector argument as a Python list (``None`` -> empty)."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        return [None if _is_na(v) else v for v in x.tolist()]
    if isinstance(x, Iterable):
        return [None if _is_na(v) else v for v in x]
    return [None if _is_na(x) else x]


def _tolower(s: str) -> str:
    """R's ``tolower()``: ``towlower()`` per character.

    Unlike ``str.lower()`` it maps ``İ`` to ``i`` (not ``i`` + a combining dot)
    and has no final-sigma rule.
    """
    if s.isascii():
        return s.lower()
    return "".join("i" if c == "\u0130" else c.lower() for c in s)


def _toupper(s: str) -> str:
    """R's ``toupper()``: ``towupper()`` per character (``ß`` stays ``ß``)."""
    if s.isascii():
        return s.upper()
    return "".join(u if len(u := c.upper()) == 1 else c for c in s)


def _r_basename(path: str) -> str:
    """R ``basename()`` (trailing slashes are dropped first)."""
    stripped = slashed(path).rstrip("/")
    if not stripped:
        return ""
    return stripped.rsplit("/", 1)[-1]


def _r_dirname(path: str) -> str:
    """R ``dirname()``."""
    if path == "":
        return ""
    stripped = path.rstrip("/")
    if not stripped:
        return "/"
    if "/" not in stripped:
        return "."
    head = stripped.rsplit("/", 1)[0].rstrip("/")
    return head if head else "/"


def _file_ext(x: str | None) -> str | None:
    """``tools::file_ext()`` of one name: the trailing alphanumeric extension, or ``""``.

    ``None`` (``NA``) stays ``None``; a name that is not valid UTF-8 has no
    extension. Vectors go through :func:`metacheck.datacheck._strings.file_ext`,
    which also raises where R's ``substring()`` does.
    """
    if x is None:
        return None
    return file_ext([x])[0]


def _as_logical(x: Any) -> bool | None:
    """R ``as.logical()`` of one value (``None`` for NA)."""
    if _is_na(x):
        return None
    if isinstance(x, bool | np.bool_):
        return bool(x)
    if isinstance(x, int | float | np.integer | np.floating):
        return bool(x)
    if isinstance(x, str):
        if x in ("TRUE", "true", "T", "True"):
            return True
        if x in ("FALSE", "false", "F", "False"):
            return False
    return None


def _r_as_numeric(s: Any) -> float | None:
    """R ``as.numeric()`` of one string (``None`` for NA; NaN stays NaN)."""
    if _is_na(s):
        return None
    if isinstance(s, bool):
        return float(s)
    if isinstance(s, int | float):
        return float(s)
    txt = str(s).strip(" \t\n\r\f\v")
    if not txt or txt == "NA":
        return None
    m = _NUMERIC_RX().fullmatch(txt)
    if m is None:
        return None
    body = txt.lstrip("+-")
    neg = txt.startswith("-")
    low = body.lower()
    if low in ("inf", "infinity"):
        return -math.inf if neg else math.inf
    if low == "nan":
        return math.nan
    if low.startswith("0x"):
        v = _hex_float(low[2:])
        if v is None:
            return None
        return -v if neg else v
    try:
        v = float(body)
    except ValueError:
        return None
    return -v if neg else v


@functools.cache
def _NUMERIC_RX() -> Any:
    return compile_r(
        r"[+-]?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[iI][nN][fF](?:[iI][nN][iI][tT][yY])?"
        r"|[nN][aA][nN]|0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?[0-9]+)?)",
        perl=True,
    )


def _hex_float(h: str) -> float | None:
    mant, _, exp = h.partition("p")
    whole, _, frac = mant.partition(".")
    if not whole and not frac:
        return None
    v = float(int(whole or "0", 16))
    for i, c in enumerate(frac, 1):
        v += int(c, 16) / 16**i
    if exp:
        v *= 2.0 ** int(exp)
    return v


def _r_as_character(x: Any) -> str | None:
    """R ``as.character()`` of one value, including dates and times."""
    from metacheck._r.base import as_character

    if _is_na(x):
        return None
    if isinstance(x, pd.Timestamp | dt.datetime):
        from metacheck.datacheck._files_rdata import posixct_as_character

        return posixct_as_character(x)
    if isinstance(x, dt.date):
        return x.isoformat()
    return as_character(x)


def _series_as_character(col: pd.Series) -> list[str | None]:
    """``as.character(col)`` for a data-frame column."""
    if isinstance(col.dtype, pd.CategoricalDtype):
        return [None if _is_na(v) else str(v) for v in col.tolist()]
    return [_r_as_character(v) for v in col.tolist()]


# -----------------------------------------------------------------------------
# Classification
# -----------------------------------------------------------------------------


def _file_category(file_name: list[str | None]) -> list[str | None]:
    from metacheck.fileinfo.category import file_category

    out = file_category(list(file_name))
    col = out["file_category"] if isinstance(out, pd.DataFrame) else out
    return [None if _is_na(v) else str(v) for v in _as_list(col)]


def _filetype(file_name: list[str | None]) -> list[str | None]:
    from metacheck.fileinfo.category import filetype

    out = filetype(list(file_name))
    if isinstance(out, dict):
        out = list(out.values())
    return [None if _is_na(v) else str(v) for v in _as_list(out)]


_KEYWORD_RULES = (
    ("documentation", "readme"),
    ("documentation", "code[ _.-]?book"),
    ("documentation", "pre[ _-]?reg(istration)?"),
    ("materials", "materials|stimuli|stimulus"),
    ("data", "data"),
    ("code", "code|script"),
    ("output", "output|results"),
)


def _tok(pattern: str) -> str:
    return f"(^|[/_. -])({pattern})($|[/_. -])"


def data_classify_files(
    file_name: Sequence[str | None] | str, file_path: Sequence[str | None] | str | None = None
) -> list[str]:
    """Classify repository files into data_check semantic types.

    Port of ``R/data_check_helpers.R::data_classify_files()``: format-locked
    extensions first, then category keywords anywhere in the (folder) path, then
    metacheck's coarse file-type crosswalk. Returns one of ``"data"``,
    ``"code"``, ``"documentation"``, ``"materials"``, ``"output"`` or
    ``"unknown"`` per file.
    """
    names = _as_list(file_name)
    n = len(names)
    if n == 0:
        return []
    cat_raw = _file_category(names)
    cat = ["documentation" if c in ("readme", "codebook") else c for c in cat_raw]
    ext = [None if f is None else _tolower(f) for f in file_ext(names)]
    fixed = [_FIXED_EXT_TYPE.get(e) if e else None for e in ext]
    locked = [f if f is not None else c for f, c in zip(fixed, cat, strict=True)]

    if file_path is not None:
        paths = _as_list(file_path)
        if len(paths) != n:  # R's ifelse() recycles the path vector
            paths = [paths[i % len(paths)] for i in range(n)] if paths else [None] * n
        path_for_kw = [
            nm if (p is None or p == "") else p for p, nm in zip(paths, names, strict=True)
        ]
    else:
        path_for_kw = list(names)
    tolower_checked(path_for_kw)
    path_lc = [None if p is None else _tolower(p) for p in path_for_kw]
    # R's regex engine refuses a string that is not valid UTF-8 (grepl() is FALSE);
    # only names glibc can still convert get this far (tolower() raised for the rest)
    r_invalid = [invalid_utf8(p) for p in path_lc]

    types = list(locked)
    claimed = [t is not None for t in locked]
    for rtype, pattern in _KEYWORD_RULES:
        hits = grepl(_tok(pattern), path_lc)
        for i, hit in enumerate(hits):
            if hit and not r_invalid[i] and fixed[i] is None and not claimed[i]:
                types[i] = rtype
                claimed[i] = True

    coarse = _filetype(names)
    for i, t in enumerate(types):
        if t is None:
            ci = coarse[i]
            first = None if ci is None else ci.split(";", 1)[0]
            types[i] = _FILE_TYPE_CROSSWALK.get(first) if first is not None else None
    for i, nm in enumerate(names):
        if nm is not None and _tolower(_r_basename(nm)) == "ro-crate-metadata.json":
            types[i] = "documentation"
    return [t if t is not None else "unknown" for t in types]


_PKG_SUBDIRS = ("R", "man", "tests", "vignettes", "data", "inst", "src")


def _is_r_package_file(file_path: Sequence[str | None] | str) -> list[bool]:
    """Files that belong to an R package's own source tree.

    Port of ``.is_r_package_file()``: a folder holding both ``DESCRIPTION`` and
    ``NAMESPACE`` is a package; its own files and standard subdirectories are
    flagged, except that a package at the repository root is only excluded when
    real data or code exists outside it.
    """
    fp = _as_list(file_path)
    n = len(fp)
    if n == 0:
        return []
    known = [p is not None and p != "" for p in fp]
    path = [(p if k else "<na>").replace("\\", "/") for p, k in zip(fp, known, strict=True)]
    dirs = [_r_dirname(p) for p in path]
    for i, p in enumerate(path):
        if p == _r_basename(p):
            dirs[i] = "."
    base_uc = [_toupper(_r_basename(p)) for p in path]
    desc = [d for d, k, b in zip(dirs, known, base_uc, strict=True) if k and b == "DESCRIPTION"]
    nsp = {d for d, k, b in zip(dirs, known, base_uc, strict=True) if k and b == "NAMESPACE"}
    pkg_dirs = list(dict.fromkeys(d for d in desc if d in nsp))
    if not pkg_dirs:
        return [False] * n

    def member_of(i: int) -> str | None:
        d = dirs[i]
        for pd_ in pkg_dirs:
            if d == pd_:
                if base_uc[i] in ("DESCRIPTION", "NAMESPACE"):
                    return pd_
                continue
            if pd_ == ".":
                rel: str | None = d
            else:
                prefix = pd_ + "/"
                rel = d[len(prefix) :] if d.startswith(prefix) else None
            if rel is not None:
                first = strsplit(rel, "/", fixed=True)
                if first and first[0] in _PKG_SUBDIRS:
                    return pd_
        return None

    owner = [member_of(i) for i in range(n)]
    is_member = [k and o is not None for k, o in zip(known, owner, strict=True)]
    for pd_ in pkg_dirs:
        if pd_ != ".":
            continue
        outside = [i for i in range(n) if not is_member[i] and known[i]]
        outside = [i for i in outside if not _tolower(_r_basename(path[i])).endswith(".rproj")]
        types = data_classify_files(
            [_r_basename(path[i]) for i in outside], [path[i] for i in outside]
        )
        if not any(t in ("data", "code") for t in types):
            for i in range(n):
                if owner[i] == pd_:
                    is_member[i] = False
    return is_member


def _data_doc_role(file_name: Sequence[str | None] | str) -> list[str | None]:
    """A documentation file's role: readme, license, codebook or supplemental.

    Port of ``.data_doc_role()``; ``None`` for files that are not documentation.
    """
    names = _as_list(file_name)
    if not names:
        return []
    is_doc = [t == "documentation" for t in data_classify_files(names)]
    cat_raw = _file_category(names)
    exts = file_ext(names)
    roles: list[str | None] = []
    for nm, doc, cat, e in zip(names, is_doc, cat_raw, exts, strict=True):
        base = "" if nm is None else _r_basename(nm)
        low = _tolower(base)
        ext = _tolower(e or "")
        if cat == "readme":
            role: str | None = "readme"
        elif low == "ro-crate-metadata.json" or grepl(r"^readme($|\.)", low):
            role = "readme"
        elif grepl(r"^licen[sc]e($|\.)", low):
            role = "license"
        elif cat == "codebook" or ext == "qsf":
            role = "codebook"
        elif doc:
            role = "supplemental"
        else:
            role = None
        roles.append(role)
    return roles


def data_format(ext: Sequence[str | None] | str) -> list[str]:
    """``"tabular"`` when metacheck can read the format as a table, else ``"raw"``.

    Port of ``R/data_check_helpers.R::data_format()``.
    """
    return [
        "tabular" if (e is not None and _tolower(e) in _READABLE_EXTENSIONS) else "raw"
        for e in _as_list(ext)
    ]


def data_is_manifest(
    df: pd.DataFrame | None,
    repo_files: Sequence[str | None] | str,
    threshold: float = 0.8,
    min_exts: int = 2,
) -> bool:
    """Detect a file manifest / table of contents masquerading as tabular data.

    Port of ``R/data_check_helpers.R::data_is_manifest()``: ``True`` when a
    column mostly names other repository files spanning at least *min_exts*
    extensions.
    """
    if df is None or len(df) == 0 or df.shape[1] == 0:
        return False
    files = [f for f in _as_list(repo_files) if f is not None and f != ""]
    if not files:
        return False
    repo_base = {_tolower(_r_basename(str(f).replace("\\", "/"))) for f in files}
    for j in range(df.shape[1]):
        vals = trimws(_series_as_character(df.iloc[:, j]))
        vals = [_tolower(v) for v in vals if v is not None and v != ""]
        if not vals:
            continue
        is_ref = [_r_basename(v.replace("\\", "/")) in repo_base for v in vals]
        if sum(is_ref) / len(is_ref) < threshold:
            continue
        exts = set(file_ext([v for v, r in zip(vals, is_ref, strict=True) if r])) - {""}
        if len(exts) >= min_exts:
            return True
    return False


# -----------------------------------------------------------------------------
# Manifests
# -----------------------------------------------------------------------------


class RNull:
    """R's ``NULL`` inside a list: jsonlite writes it as ``{}``."""

    _instance: RNull | None = None

    def __new__(cls) -> RNull:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "RNull()"


R_NULL = RNull()


class RVector(tuple):  # type: ignore[type-arg]
    """An R atomic vector of length != 1 (jsonlite writes it on one line)."""


def _json_num(x: float | np.floating[Any]) -> str:
    """A double as JSON: whole numbers without a fraction, others at full
    precision (shortest round-trip form). metacheck writes with jsonlite's
    ``digits = 4``, which turns ``0.000012345`` into ``0``."""
    x = float(x)  # a numpy float's repr() is "np.float64(...)"
    if not math.isfinite(x):
        return "null"
    if x == int(x) and abs(x) < 1e17:
        return str(int(x))
    return repr(x)


def _json_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _json_scalar(x: Any) -> str:
    if x is None or x is pd.NA:
        return "null"
    if isinstance(x, bool | np.bool_):
        return "true" if x else "false"
    if isinstance(x, int | np.integer):
        return str(int(x))
    if isinstance(x, float | np.floating):
        return _json_num(x)
    return _json_str(str(x))


def to_json_pretty(x: Any, indent: int = 0) -> str:
    """``jsonlite::toJSON(x, auto_unbox = TRUE, pretty = TRUE, na = "null")``.

    Python ``dict`` is a named list, ``list`` an unnamed list, :class:`RVector`
    an atomic vector, ``None`` an ``NA`` and :data:`R_NULL` a ``NULL``.
    """
    pad = "  " * (indent + 1)
    end = "  " * indent
    if isinstance(x, RNull):
        return "{}"
    if isinstance(x, dict):
        if not x:
            return "{}"
        items = [f"{pad}{_json_str(str(k))}: {to_json_pretty(v, indent + 1)}" for k, v in x.items()]
        return "{\n" + ",\n".join(items) + "\n" + end + "}"
    if isinstance(x, RVector):
        return "[" + ", ".join(_json_scalar(v) for v in x) + "]"
    if isinstance(x, list | tuple):
        if not x:
            return "[]"
        items = [pad + to_json_pretty(v, indent + 1) for v in x]
        return "[\n" + ",\n".join(items) + "\n" + end + "]"
    return _json_scalar(x)


def _from_json(value: Any) -> Any:
    """A parsed manifest as the writer's values: JSON null stays null (``None``).

    (metacheck reads it back with ``jsonlite::fromJSON(simplifyVector = FALSE)``,
    where null becomes ``NULL``, which it then writes as ``{}``: every re-merge
    turns a null into an empty object.)
    """
    if isinstance(value, dict):
        return {k: _from_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_json(v) for v in value]
    return value


def manifest_merge(path: str | os.PathLike[str], patch: dict[str, Any]) -> str:
    """Merge top-level keys into a metacheck manifest, preserving the others.

    Port of ``R/data_check_helpers.R::manifest_merge()``: reads any existing
    ``*.manifest.json``, replaces each key in *patch* wholesale (an
    :data:`R_NULL` value -- R's ``NULL`` -- removes the key; ``None`` is R's
    ``NA`` and is written as ``null``) and writes it back in jsonlite's
    layout. JSON ``null`` values already in the file stay ``null`` (metacheck
    rewrites them as ``{}``), and numbers keep their full precision.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: Any = {}
    if p.exists():
        try:
            existing = _from_json(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            existing = {}
        if not isinstance(existing, dict | list):
            existing = {}
    if isinstance(existing, list):  # a top-level JSON array is not a manifest
        existing = {}
    for name, value in patch.items():
        if isinstance(value, RNull):
            existing.pop(name, None)
        else:
            existing[name] = value
    p.write_text(to_json_pretty(existing) + "\n", encoding="utf-8")
    return str(p)


_DDI_MAPPING = {
    "provenance.software": "docDscr/citation/prodStmt/software (@version)",
    "provenance.prod_date": "docDscr/citation/prodStmt/prodDate",
    "files[].file_name": "fileDscr/fileTxt/fileName",
    "files[].file_url": "fileDscr/@URI",
    "files[].data_type": "fileDscr/fileTxt/fileCont",
    "files[].status": "fileDscr/fileTxt/ProcStat",
    "files[].skip_reason": "fileDscr/notes",
}


def _col(df: pd.DataFrame | None, name: str) -> list[Any] | None:
    if df is None or name not in df.columns:
        return None
    return [None if _is_na(v) else v for v in df[name].tolist()]


def _remote_size(url: str) -> float | None:
    try:
        from metacheck.archives.download import _remote_size as remote_size
    except ImportError:  # pragma: no cover - archives port not available
        return None
    v = remote_size(url)
    return None if _is_na(v) else float(v)


def _llm_use() -> bool:
    try:
        from metacheck.llm.core import llm_use
    except ImportError:  # pragma: no cover - llm port not available
        return False
    return bool(llm_use())


def _llm_model() -> str | None:
    try:
        from metacheck.llm.core import llm_model
    except ImportError:  # pragma: no cover - llm port not available
        return None
    return llm_model()


def _provenance(
    generated: str, model: str | None, concept_model: str | None = None
) -> dict[str, Any]:
    """The manifest's ``provenance`` block, describing the software that ran.

    metacheck records its own version and R's; pytacheck records itself, the
    metacheck release it ports and Python's version, and the local concept
    classifier when it ran (*concept_model*).
    """
    import platform

    from metacheck._version import UPSTREAM, __version__

    llm = (
        {"used": True, "model": model if model is not None else _llm_model()}
        if _llm_use()
        else {"used": False}
    )
    out: dict[str, Any] = {
        "software": {
            "name": "pytacheck",
            "version": __version__,
            "port_of": f"metacheck {UPSTREAM['version']} ({UPSTREAM['commit'][:12]})",
        },
        "python_version": f"Python {platform.python_version()}",
        "platform": f"{platform.machine()}-{sys.platform}",
        "prod_date": generated,
        "llm": llm,
    }
    if concept_model is not None:
        out["concept_classifier"] = {"used": True, "model": concept_model}
    out["ddi_mapping"] = dict(_DDI_MAPPING)
    return out


def _data_check_write_manifest(
    manifest: str | os.PathLike[str],
    files: pd.DataFrame,
    want: Sequence[Any] | Any,
    gated: pd.DataFrame | None,
    paper_id: Sequence[str | None] | str | None,
    download: str,
    max_file_size: float | None,
    max_download_size: float | None,
    skip_types: Sequence[str] | str | None = None,
    oversize: pd.DataFrame | None = None,
    failed: pd.DataFrame | None = None,
    zip_peek: Sequence[str | None] | None = None,
    model: str | None = None,
    concept_model: str | None = None,
) -> list[str]:
    """Write one ``*.manifest.json`` per paper recording every repository file.

    Port of ``.data_check_write_manifest()``: whether each file was downloaded,
    why not (intentional policy skips vs unintentional failures), sizes (a
    HEAD probe for wanted files the listing left unsized) and provenance.
    Returns the written paths.
    """
    n = len(files)
    want_l = _as_list(want)
    if not want_l:
        want_l = [False] * n
    elif len(want_l) != n:
        want_l = [want_l[i % len(want_l)] for i in range(n)]
    want_b = [_as_logical(w) is True for w in want_l]  # as.logical(); NA -> FALSE

    loc = _col(files, "file_location") or [None] * n
    downloaded = [bool(v) and os.path.exists(str(v)) for v in loc]
    gated_urls = set(_col(gated, "repo_url") or []) if gated is not None and len(gated) else set()

    sizes = _col(files, "file_size") or [None] * n
    file_size: list[float | None] = []
    for i in range(n):
        on_disk = os.path.getsize(str(loc[i])) if downloaded[i] else None
        v = sizes[i]
        file_size.append(
            float(on_disk) if on_disk is not None else _r_as_numeric(v)  # as.numeric()
        )
    urls = _col(files, "file_url") or [None] * n
    for i in range(n):
        if file_size[i] is None and want_b[i] and urls[i] and not downloaded[i]:
            file_size[i] = _remote_size(str(urls[i]))

    dtype = _col(files, "data_type") or [None] * n
    zp = list(zip_peek) if zip_peek is not None else []
    if len(zp) != n:
        zp = (zp + [None] * n)[:n]

    def paste_key(r: Any, f: Any) -> str:
        return f"{'NA' if _is_na(r) else r} {'NA' if _is_na(f) else f}"

    over_key = (
        {paste_key(r, f) for r, f in zip(oversize["repo_url"], oversize["file_name"], strict=True)}
        if oversize is not None and len(oversize)
        else set()
    )
    fail_err: dict[str, str] = {}
    if failed is not None and len(failed):
        for r, f, e in zip(failed["repo_url"], failed["file_name"], failed["error"], strict=True):
            fail_err.setdefault(paste_key(r, f), "NA" if _is_na(e) else str(e).split("\n", 1)[0])
    skip = _as_list(skip_types)
    has_repo = "repo_url" in files.columns
    repo_urls = _col(files, "repo_url") or [None] * n
    names = _col(files, "file_name") or [None] * n

    reason: list[str | None] = [None] * n
    intentional: list[bool | None] = [None] * n
    for i in range(n):
        if downloaded[i]:
            continue
        # paste(NULL, name) is just the name
        key = paste_key(repo_urls[i], names[i]) if has_repo else f"{names[i]}"
        url = urls[i]
        if download == "none":
            reason[i], intentional[i] = 'download = "none"', True
        elif skip and dtype[i] in skip:
            reason[i] = (
                f"excluded type '{'NA' if dtype[i] is None else dtype[i]}' (linked, not mirrored)"
            )
            intentional[i] = True
        elif zp[i]:
            reason[i], intentional[i] = zp[i], True
        elif not want_b[i]:
            reason[i] = 'not a data/codebook/README file (use download = "all")'
            intentional[i] = True
        elif not url:
            reason[i], intentional[i] = "no download URL in the listing", False
        elif key in over_key:
            from metacheck.report.blocks import _cap_num

            reason[i] = (
                f"exceeds max_file_size ({_cap_num(max_file_size)} MB): skipped by the per-file cap"
            )
            intentional[i] = True
        elif has_repo and repo_urls[i] in gated_urls:
            reason[i], intentional[i] = "repository refused by the size caps", True
        elif key in fail_err:
            reason[i] = "download failed after retries: " + fail_err[key]
            intentional[i] = False
        else:
            reason[i], intentional[i] = "download failed", False
    status = [
        "downloaded" if d else ("skipped" if it is True else "failed")
        for d, it in zip(downloaded, intentional, strict=True)
    ]

    generated = dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    provenance = _provenance(generated, model, concept_model)

    pid_arg = _as_list(paper_id)
    if "paper_id" in files.columns and n > 0:
        pids = _col(files, "paper_id") or []
        all_pids = list(dict.fromkeys(pids))
    else:
        first = pid_arg[0] if pid_arg else None
        pids = [first] * n
        all_pids = list(dict.fromkeys(pid_arg)) if n == 0 else [first]
        if not all_pids:
            all_pids = [None]

    file_path = _col(files, "file_path")
    provider = _col(files, "provider")
    data_format_col = _col(files, "data_format")
    paths: list[str] = []
    for pid in all_pids:
        idx = [i for i in range(n) if pids[i] == pid] if n > 0 else []
        if not str(manifest).lower().endswith(".json"):
            Path(manifest).mkdir(parents=True, exist_ok=True)
            label = pid if pid else "manifest"
            path = f"{manifest}/{label}.manifest.json"  # file.path() just pastes
        else:
            path = str(manifest)
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        entries = []
        for i in idx:
            entry: dict[str, Any] = {
                "file_name": names[i],
                "file_path": file_path[i] if file_path is not None else names[i],
            }
            if "repo_url" in files.columns:
                entry["repo_url"] = repo_urls[i]
            entry["file_url"] = urls[i]
            if provider is not None and provider[i] is not None:
                entry["provider"] = provider[i]
            if file_size[i] is not None:
                entry["file_size"] = file_size[i]
            entry["data_type"] = dtype[i]
            entry["data_format"] = data_format_col[i] if data_format_col is not None else None
            entry["downloaded"] = downloaded[i]
            entry["status"] = status[i]
            if not downloaded[i]:
                entry["skip_reason"] = reason[i]
                entry["skip_intentional"] = intentional[i]
            entries.append(entry)
        unint = [i for i in idx if not downloaded[i] and intentional[i] is False]
        intent = [i for i in idx if not downloaded[i] and intentional[i] is True]
        doc: dict[str, Any] = {
            "paper_id": pid,
            "generated": generated,
            "download": download,
        }
        if skip:
            doc["skip_types"] = list(skip)
        doc["caps"] = {
            # no cap / no repository URL: null (metacheck writes R's NULL as {})
            "max_file_size_mb": max_file_size,
            "max_download_size_mb": max_download_size,
        }
        doc["provenance"] = provenance
        doc["n_files"] = len(idx)
        doc["n_downloaded"] = sum(downloaded[i] for i in idx)
        doc["not_downloaded"] = {
            "intentional_n": len(intent),
            "unintentional_n": len(unint),
            "unintentional_files": [
                {
                    "file_name": names[i],
                    "repo_url": repo_urls[i] if has_repo else None,
                    "reason": reason[i],
                }
                for i in unint
            ],
            "rerun_recommended": len(unint) > 0,
        }
        doc["files"] = entries
        manifest_merge(path, doc)
        paths.append(path)
    return paths


# -----------------------------------------------------------------------------
# Study grouping
# -----------------------------------------------------------------------------


def _llm(*args: Any, **kwargs: Any) -> Any:
    """Call ``metacheck.llm.core.llm()`` (a hook tests can replace)."""
    from metacheck.llm.core import llm

    return llm(*args, **kwargs)


def _llm_schema(field: str, index_desc: str, value: str, value_desc: str) -> dict[str, Any]:
    """The object-wrapped array schema (ellmer ``type_object(<field> = type_array(...))``)."""
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": index_desc},
                        value: {"type": "string", "description": value_desc},
                    },
                    "required": ["index", value],
                },
            }
        },
        "required": [field],
    }


def _as_integer(x: Any) -> int | None:
    """R ``as.integer()`` of one value (truncates doubles; NA when not a number)."""
    if _is_na(x):
        return None
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    v = _r_as_numeric(x)
    if v is None or not math.isfinite(v) or abs(v) >= 2**31:
        return None
    return int(v)


def _llm_response_model(resp: Any) -> str | None:
    info = getattr(resp, "attrs", {}).get("llm") if resp is not None else None
    if isinstance(info, dict):
        model = info.get("model")
        return None if _is_na(model) else model
    return None


def _llm_classify_batched(
    item_texts: Sequence[str],
    system_prompt: str,
    value_desc: str,
    valid: Sequence[str] | None = None,
    batch_size: int = _DATA_CHECK_LLM_BATCH,
    model: str | None = None,
    params: dict[str, Any] | None = None,
    phase: str | None = None,
) -> pd.Series:
    """Classify items with an LLM in index-mapped batches.

    Port of ``.llm_classify_batched()``: each batch sends a numbered listing and
    maps the returned ``{index, value}`` pairs back by index, so dropped or
    reordered answers never misalign. Returns a string Series aligned with
    *item_texts* (``NA`` where no valid value came back) whose
    ``attrs["llm_model"]`` is the model the provider reported.
    """
    items = list(item_texts)
    n = len(items)
    out: list[str | None] = [None] * n
    model_used: str | None = None
    if n:
        type_spec = _llm_schema("results", "The item's number in the list", "value", value_desc)
        for start in range(0, n, batch_size):
            rows = list(range(start, min(start + batch_size, n)))
            listing = "\n".join(  # paste(): NA is "NA"
                f"{k}. {'NA' if _is_na(items[r]) else _r_as_character(items[r])}"
                for k, r in enumerate(rows, 1)
            )
            try:
                resp = _llm(
                    text=pd.DataFrame({"text": [listing]}),
                    text_col="text",
                    system_prompt=system_prompt,
                    type=type_spec,
                    model=model if model is not None else _llm_model(),
                    params=dict(params or {}),
                    phase=phase,
                )
            except Exception:
                resp = None
            resp = _strip_llm_wrapper(resp, "results")
            if (
                not isinstance(resp, pd.DataFrame)
                or len(resp) == 0
                or not {"index", "value"} <= set(resp.columns)
            ):
                continue
            if model_used is None:
                model_used = _llm_response_model(resp)
            idx = [_as_integer(v) for v in resp["index"].tolist()]
            vals = [
                None if _is_na(v) else _tolower(str(_r_as_character(v)).strip(" \t\r\n"))
                for v in resp["value"].tolist()
            ]
            for i, v in zip(idx, vals, strict=True):
                good = i is not None and 1 <= i <= len(rows) and (v is None or v != "")
                if valid is not None:
                    good = good and v in list(valid)
                if good and i is not None:
                    out[rows[i - 1]] = v
    s = pd.Series(out, dtype="string")
    s.attrs["llm_model"] = model_used
    return s


def data_group_llm(
    files: pd.DataFrame | None,
    model: str | None = None,
    params: dict[str, Any] | None = None,
    batch_size: int = _DATA_CHECK_LLM_BATCH,
    paper: Any = None,
) -> pd.DataFrame | None:
    """Assign a study group (``ex1``, ``ex2a``, ``pilot1``, ...) to each file.

    Port of ``R/data_check_helpers.R::data_group_llm()``: deterministic passes
    (source repository, study names in paths, data files referenced by
    scripts) first, the LLM only for what they leave unresolved, then fallbacks
    so every placeable file gets a real study, and relabelling from the
    manuscript's study roster. Returns a frame with ``group`` and
    ``referenced_by`` (one row per file) whose ``attrs`` hold ``model``,
    ``roster``, ``roster_check``, ``no_evidence`` and ``unresolved``; ``None``
    for empty input. *model* defaults to ``llm_model()``.
    """
    return _data_group_llm_impl(files, model, params, batch_size, paper)


def data_study_roster(paper: Any) -> list[str]:
    """The studies a paper names (``Experiment 1`` -> ``ex1``), as group codes.

    Port of ``R/data_check_helpers.R::data_study_roster()``.
    """
    from metacheck.papers.model import is_paper

    if not is_paper(paper):
        return []
    try:
        from metacheck.text.search import text_search

        hits = text_search(
            paper,
            r"\b(?:study|experiment|pilot)[ ._-]?[0-9]{1,2}[a-z]?\b",
            return_="match",
            ignore_case=True,
            perl=True,
        )
    except Exception:
        return []
    if hits is None or len(hits) == 0:
        return []
    m = [
        None if t is None else _tolower(gsub("[ ._-]", "", str(t))) for t in _as_list(hits["text"])
    ]
    codes = [c for c in _data_group_normalize(m) if c is not None]
    codes = list(dict.fromkeys(codes))
    if not codes:
        return []
    return _data_group_sort(codes)


def _data_group_sort(codes: Sequence[str]) -> list[str]:
    """Order study codes by number, then sub-study letter (``.data_group_sort()``)."""
    codes = list(codes)
    if not codes:
        return codes
    nums = [_as_integer(sub(r"^(ex|pilot)([0-9]{1,2})[a-z]?$", r"\2", c)) for c in codes]
    sufs = [sub(r"^(ex|pilot)[0-9]{1,2}([a-z]?)$", r"\2", c) for c in codes]
    order = sorted(
        range(len(codes)),
        key=lambda i: (nums[i] is None, nums[i] or 0, sufs[i]),
    )
    return [codes[i] for i in order]


def _data_group_check_roster(groups: Sequence[str | None], roster: Sequence[str]) -> dict[str, Any]:
    """Compare a file grouping to the manuscript roster (``.data_group_check_roster()``)."""
    found = list(dict.fromkeys(g for g in groups if g is not None))
    roster = list(roster)
    return {
        "roster": roster,
        "found": found,
        "missing": [r for r in dict.fromkeys(roster) if r not in found],
        "extra": [f for f in found if f not in roster],
        "agrees": len(roster) > 0 and set(roster) == set(found),
    }


def _read_text_lines(path: str | os.PathLike[str]) -> list[str]:
    """``readLines(path)``: lines split on LF / CRLF / CR, NUL-truncated."""
    raw = Path(path).read_bytes()
    lines = _split_lines(raw)
    return [ln.split(b"\0", 1)[0].decode("utf-8", "surrogateescape") for ln in lines]


def _split_lines(raw: bytes) -> list[bytes]:
    """Split bytes into lines like an R text connection (no trailing empty line)."""
    if not raw:
        return []
    text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lines = text.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def _has_invalid_utf8(s: str) -> bool:
    """Whether a ``surrogateescape``-decoded string held bytes that are not UTF-8."""
    if s.isascii():
        return False
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:  # the lone surrogates standing for invalid bytes
        return True
    return False


def _data_code_refs(path: str | None, max_bytes: float = 2e6) -> list[str]:
    """Basenames (lowercased) of the data files a script reads or writes.

    Port of ``.data_code_refs()``.
    """
    if path is None or path == "" or not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size > max_bytes:
        return []
    try:
        # lines that are not valid UTF-8 are read as Latin-1 (in R an 8-bit
        # script stops the whole study-grouping pass)
        txt = "\n".join(s or "" for s in _utf8_lines(_read_text_lines(path)))
    except OSError:
        return []
    if not txt:
        return []
    pat = "(?:" + _CODE_READ_FNS + ")\\s*\\([^)\"']*[\"']([^\"']+)[\"']"
    matches = [
        txt[s - 1 : s - 1 + n] for s, n in gregexpr_all(pat, txt, ignore_case=True, perl=True)
    ]
    if not matches:
        return []
    refs = sub("^.*?[\"']([^\"']+)[\"'].*$", r"\1", matches)
    refs = [r for r, ok in zip(refs, grepl(r"\.[A-Za-z0-9]{1,6}$", refs), strict=True) if ok]
    return list(dict.fromkeys(_tolower(_r_basename(r.replace("\\", "/"))) for r in refs))


_EX_PAT = "(?:experiment|study|(?<![a-z])expt?|(?<![a-z])ex)[ ._-]?([0-9]{1,2})([a-z](?![a-z]))?"
_PILOT_PAT = "(?<![a-z])pilot[ ._-]?([0-9]{1,2})?"


def _data_group_from_path(paths: Sequence[str | None]) -> list[str | None]:
    """A study group named by a file's own path, or ``None`` (``.data_group_from_path()``)."""
    out: list[str | None] = []
    for path in _as_list(paths):
        code: str | None = None
        if path is not None and path != "":
            for part in reversed(strsplit(_tolower(path), "/", fixed=True)):
                m = regexec(_EX_PAT, part, perl=True)
                if m:
                    code = "ex" + m[1] + m[2]
                    break
                m = regexec(_PILOT_PAT, part, perl=True)
                if m:
                    code = "pilot" + (m[1] if m[1] else "1")
                    break
        out.append(code)
    return out


def _data_group_normalize(x: Sequence[Any]) -> list[str | None]:
    """Normalize LLM-returned study codes; anything off-scheme is ``None``."""
    out: list[str | None] = []
    for v in _as_list(x):
        if v is None:
            out.append(None)
            continue
        s = _tolower(str(_r_as_character(v)).strip(" \t\r\n"))
        s = gsub("[ ._-]", "", s)
        s = sub("^(experiment|study|expt|exp)(?=[0-9])", "ex", s, perl=True)
        if s == "pilot":
            s = "pilot1"
        out.append(s if grepl("^(ex|pilot)[0-9]{1,2}[a-z]?$", s) else None)
    return out


def _strip_llm_wrapper(df: Any, wrapper: str) -> Any:
    """Drop the ``<wrapper>.`` prefix ellmer puts on object-wrapped array fields."""
    if df is None or not isinstance(df, pd.DataFrame):
        return df
    pref = wrapper + "."
    names = [str(c) for c in df.columns]
    if any(c.startswith(pref) for c in names):
        df = df.copy(deep=False)
        df.columns = [c.replace(pref, "", 1) if c.startswith(pref) else c for c in names]
    return df


def _data_group_from_repo(
    repo: Sequence[str | None], paths: Sequence[str | None], mirror_overlap: float = 0.9
) -> list[str | None]:
    """A base study per source repository (``.data_group_from_repo()``)."""
    paths = _as_list(paths)
    n = len(paths)
    if n == 0:
        return []
    repos = [None if (r is None or r == "") else str(r) for r in _as_list(repo)]
    distinct = list(dict.fromkeys(r for r in repos if r is not None))
    if len(distinct) < 2:
        return [None] * n
    base = [None if p is None else _tolower(_r_basename(str(p).replace("\\", "/"))) for p in paths]
    files_of = {
        r: list(dict.fromkeys(b for b, rr in zip(base, repos, strict=True) if rr == r))
        for r in distinct
    }
    slot_of: dict[str, int | None] = dict.fromkeys(distinct)
    next_slot = 0
    for r in distinct:
        merged: int | None = None
        for prev in distinct:
            if prev == r or slot_of[prev] is None:
                continue
            a, b = set(files_of[r]), set(files_of[prev])
            union = len(a | b)
            if union > 0 and len(a & b) / union >= mirror_overlap:
                merged = slot_of[prev]
                break
        if merged is not None:
            slot_of[r] = merged
        else:
            next_slot += 1
            slot_of[r] = next_slot
    if max(v for v in slot_of.values() if v is not None) < 2:
        return [None] * n
    return [None if r is None else f"ex{slot_of[r]}" for r in repos]


_GROUP_PROMPT = " ".join(
    (
        "You are grouping the files of a psychology research repository by study.",
        "Many repositories contain multiple studies (Experiment 1, Study 2a, a",
        "pilot, ...). Assign each numbered file to a study group using these codes:",
        "'ex1','ex2','ex2a',... for experiments/studies, 'pilot1','pilot2',... for",
        "pilots. Infer groups from folder names and filenames. EVERY file belongs",
        "to exactly one study -- there is no 'shared' option. If the whole",
        "repository is a single study, put every file in 'ex1'. If a file (e.g. a",
        "shared codebook or a materials file) genuinely serves multiple studies,",
        "assign it to whichever single study it is most closely associated with by",
        "folder or filename, or to the first study by number if there is no such",
        "association.",
        "Return one entry per input file, in the same order.",
    )
)

_PLACEABLE = ("data", "documentation", "materials", "code")


def _data_group_llm_impl(
    files: pd.DataFrame | None,
    model: str | None = None,
    params: dict[str, Any] | None = None,
    batch_size: int = 30,
    paper: Any = None,
) -> pd.DataFrame | None:
    """Port of ``.data_group_llm_impl()`` (see :func:`data_group_llm`)."""
    if files is None or len(files) == 0:
        return None
    params = dict(params or {})
    if params.get("seed") is None:
        params["seed"] = _DATA_GROUP_SEED
    names = _col(files, "file_name") or [None] * len(files)
    raw_paths = _col(files, "file_path") if "file_path" in files.columns else names
    paths = [
        (nm if (p is None or p == "") else p)
        for p, nm in zip(raw_paths or names, names, strict=True)
    ]
    paths = [None if p is None else str(p).replace("\\", "/") for p in paths]
    n = len(paths)
    if "repo_url" in files.columns:
        repo = _col(files, "repo_url") or [None] * n
    elif "repo_name" in files.columns:
        repo = _col(files, "repo_name") or [None] * n
    else:
        repo = [None] * n

    if "data_type" in files.columns:
        dtype = [None if v is None else _tolower(str(v)) for v in (_col(files, "data_type") or [])]
    else:
        dtype = [None] * n
    send = [True] * n if all(d is None for d in dtype) else [d in _PLACEABLE for d in dtype]

    repo_grp = _data_group_from_repo(repo, paths)
    multi_repo = any(g is not None for g in repo_grp)
    group: list[str | None] = list(repo_grp)

    pre = _data_group_from_path(paths)
    for i in range(n):
        if send[i] and pre[i] is not None:
            group[i] = pre[i]

    loc = _col(files, "file_location") if "file_location" in files.columns else [None] * n
    loc = loc or [None] * n
    is_code = [d in ("code", "materials") for d in dtype]
    script_i = [i for i in range(n) if is_code[i] and group[i] is not None and loc[i] is not None]
    referenced_by: list[list[str] | None] = [None] * n
    if script_i:
        base_of = [None if p is None else _tolower(_r_basename(p)) for p in paths]
        for si in script_i:
            refs = _data_code_refs(str(loc[si]))
            if not refs:
                continue
            hit = [b in refs for b in base_of]
            if not any(hit):
                continue
            gsi = group[si]
            for i in range(n):
                if hit[i] and group[i] is None:
                    group[i] = gsi
            for i in range(n):
                if hit[i] and group[i] is not None and group[i] != gsi:
                    cur = referenced_by[i] or []
                    referenced_by[i] = list(dict.fromkeys([*cur, gsi]))  # type: ignore[list-item]

    send = [
        send[i] and pre[i] is None and (not multi_repo or repo_grp[i] is None) and group[i] is None
        for i in range(n)
    ]
    type_spec = _llm_schema(
        "assignments", "The file's number in the list", "group", "Study group code: ex1/ex2a/pilot1"
    )
    used_model: str | None = None

    def ask_batch(rows: list[int]) -> list[int]:
        nonlocal used_model
        listing = "\n".join(
            f"{k}. {'NA' if paths[r] is None else paths[r]}" for k, r in enumerate(rows, 1)
        )
        try:
            resp = _llm(
                text=pd.DataFrame({"text": [listing]}),
                text_col="text",
                system_prompt=_GROUP_PROMPT,
                type=type_spec,
                model=model if model is not None else _llm_model(),
                params=params,
                phase="Assigning study groups",
            )
        except Exception:
            resp = None
        resp = _strip_llm_wrapper(resp, "assignments")
        if (
            not isinstance(resp, pd.DataFrame)
            or len(resp) == 0
            or not {"index", "group"} <= set(resp.columns)
        ):
            return rows
        idx = [_as_integer(v) for v in resp["index"].tolist()]
        grp = _data_group_normalize(resp["group"].tolist())
        ok_idx = []
        for i, g in zip(idx, grp, strict=True):
            if i is not None and 1 <= i <= len(rows) and g is not None:
                group[rows[i - 1]] = g
                ok_idx.append(i)
        if used_model is None:
            used_model = _llm_response_model(resp)
        answered = set(ok_idx)
        return [rows[k - 1] for k in range(1, len(rows) + 1) if k not in answered]

    send_rows = [i for i in range(n) if send[i]]
    unresolved: list[int] = []
    for start in range(0, len(send_rows), batch_size):
        rows = send_rows[start : start + batch_size]
        left = ask_batch(rows)
        if len(left) > 1:
            k = max(1, len(left) // 2)
            chunks = [left[j : j + k] for j in range(0, len(left), k)]
            left = [r for chunk in chunks for r in ask_batch(chunk)]
        unresolved.extend(left)

    is_placeable = [d in _PLACEABLE for d in dtype]
    study_codes = list(
        dict.fromkeys(g for g in group if g is not None and grepl("^(ex|pilot)[0-9]", g))
    )
    no_evidence = len(study_codes) == 0
    if any(p and g is None for p, g in zip(is_placeable, group, strict=True)):
        for i in range(n):
            if is_placeable[i] and group[i] is None and repo_grp[i] is not None:
                group[i] = repo_grp[i]
        fill: str | None = None
        if len(study_codes) == 1:
            fill = study_codes[0]
        elif len(study_codes) == 0:
            fill = "ex1"
        else:
            fill = _data_group_sort(study_codes)[0]
        for i in range(n):
            if is_placeable[i] and group[i] is None:
                group[i] = fill

    roster = data_study_roster(paper) if paper is not None else []
    if roster:
        found = list(dict.fromkeys(g for g in group if g is not None))
        already = [f for f in found if f in roster]
        to_name = [f for f in found if f not in roster]
        avail = [r for r in dict.fromkeys(roster) if r not in already]
        if to_name and len(to_name) == len(avail):
            mapping = dict(zip(to_name, avail, strict=True))
            group = [mapping.get(g, g) if g is not None else None for g in group]
            referenced_by = [
                None if r is None else [mapping.get(x, x) for x in r] for r in referenced_by
            ]

    out = pd.DataFrame({"group": pd.Series(group, dtype="string")})
    out["referenced_by"] = pd.Series(referenced_by, dtype=object)
    out.attrs["model"] = used_model
    out.attrs["roster"] = list(roster)
    out.attrs["roster_check"] = _data_group_check_roster(group, roster)
    out.attrs["no_evidence"] = no_evidence
    out.attrs["unresolved"] = [paths[i] for i in sorted(set(unresolved))]
    return out


# -----------------------------------------------------------------------------
# Text sniffing
# -----------------------------------------------------------------------------


def _split_text_lines(txt: str) -> list[str]:
    """``strsplit(txt, "\r\n|\n|\r")[[1]]``: no trailing empty piece."""
    parts = re.split(r"\r\n|\n|\r", txt)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _utf8_lines(x: Sequence[str | None]) -> list[str | None]:
    """Reinterpret invalid-UTF-8 lines as Latin-1 (``.utf8_lines()``).

    Strings carry invalid bytes as surrogate escapes (``surrogateescape``).
    """
    out: list[str | None] = []
    for s in x:
        if s is not None and _has_invalid_utf8(s):
            s = s.encode("utf-8", "surrogateescape").decode("latin-1")
        out.append(s)
    return out


def _latin1_fix(s: str) -> str:
    return s.encode("utf-8", "surrogateescape").decode("latin-1") if _has_invalid_utf8(s) else s


def _iconv_utf16(raw: bytes, enc: str) -> str | None:
    """glibc ``iconv(list(raw), from = enc, to = "UTF-8")``: ``None`` for R's ``NA``.

    A conversion error is ``NA``, and so is a result holding a NUL character
    (R cannot make a string with an embedded nul).
    """
    try:
        if enc == "UTF-16":
            if raw[:2] == b"\xff\xfe":
                out = raw[2:].decode("utf-16-le")
            elif raw[:2] == b"\xfe\xff":
                out = raw[2:].decode("utf-16-be")
            else:
                out = raw.decode("utf-16-be")
        else:
            out = raw.decode("utf-16-le" if enc == "UTF-16LE" else "utf-16-be")
    except UnicodeDecodeError:
        return None
    return None if "\0" in out else out


def text_peek(path: str | os.PathLike[str] | None, n: float = 20) -> list[str]:
    """The first *n* lines of a text file, tolerating BOMs and UTF-16.

    Port of ``R/data_check_helpers.R::text_peek()``: returns ``[]`` for a
    missing, empty or unreadable file. Lines that are not valid UTF-8 are
    read as Latin-1 (in R, splitting such text into lines fails before its
    Latin-1 repair is reached, so an 8-bit file cannot be peeked at all).
    """
    if path is None or isinstance(path, list | tuple):
        return []
    p = Path(path)
    if not p.exists() or p.is_dir():
        return []
    size = p.stat().st_size
    if size == 0:
        return []
    want = min(size, 65536) if math.isfinite(n) else size
    try:
        with p.open("rb") as fh:
            raw = fh.read(want)
    except OSError:
        return []
    if not raw:
        return []
    txt: str | None = None
    if b"\0" in raw[:1000]:
        for enc in ("UTF-16", "UTF-16LE", "UTF-16BE"):
            t = _iconv_utf16(raw, enc)
            if t:
                txt = t
                break
    if txt is None:
        # rawToChar() drops trailing nuls and fails (NA) on an embedded one
        raw = raw.rstrip(b"\0")
        if b"\0" in raw:
            return []
        txt = raw.decode("utf-8", "surrogateescape")
    txt = txt.removeprefix("﻿")
    lines = _utf8_lines(_split_text_lines(txt))
    return [s for s in lines if s is not None][: int(n) if math.isfinite(n) else None]


def txt_classify_content(path: str | os.PathLike[str]) -> str | None:
    """Classify a downloaded ``.txt`` file from its content.

    Port of ``R/data_check_helpers.R::txt_classify_content()``: ``"data"`` for
    an E-Prime export or a delimited table with a header row, else ``None``
    (keep the name-based classification).
    """
    head_lines = text_peek(path, n=30)
    if not head_lines:
        return None
    if any(grepl(r"^\*\*\*\s*Header Start", head_lines)) or (
        any(grepl("^(Experiment|Subject):", head_lines)) and any(grepl("^LevelName:", head_lines))
    ):
        return "data"
    try:
        sep: str | None = _sniff_delimiter(path)
    except Exception:
        sep = None
    if sep is not None:
        rows = [ln for ln in head_lines if trimws(ln) != ""]
        rows = [ln for ln in rows if not trimws(ln).startswith("#")]
        if len(rows) >= 2:
            counts = [len(strsplit(ln, sep, fixed=True)) for ln in rows[:5]]
            consistent = len(set(counts)) == 1 and counts[0] >= 2
            if consistent:
                try:
                    hdr = _detect_header(path, sep)
                except Exception:
                    hdr = False
                if hdr is True:
                    return "data"
    return None


class _LineReader:
    """``readLines(con, n = 1)`` on a text connection, one line at a time.

    Lines end at LF, CRLF or CR; a NUL truncates its line; invalid UTF-8 is
    reinterpreted as Latin-1 (``.utf8_lines()``). The file is read lazily in
    chunks, so sniffing a large file costs only its first lines.
    """

    _CHUNK = 65536

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.fh = Path(path).open("rb")  # noqa: SIM115 - closed at eof / on delete
        self.buf = b""
        self.eof = False

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        fh = getattr(self, "fh", None)
        if fh is not None and not fh.closed:
            fh.close()

    def _fill(self) -> None:
        chunk = self.fh.read(self._CHUNK)
        if chunk:
            self.buf += chunk
        else:
            self.eof = True
            self.close()

    def next(self, strip_bom: bool = True) -> str | None:
        """The next line (``None`` at the end).

        R's ``readLines()`` drops a UTF-8 byte-order mark from the start of the
        first line each call returns (in a UTF-8 locale): *strip_bom* is that
        first-line-of-the-call rule.
        """
        while True:
            k_n = self.buf.find(b"\n")
            k_r = self.buf.find(b"\r", 0, k_n if k_n != -1 else len(self.buf))
            k = k_r if k_r != -1 else k_n
            if k != -1 and not (k == len(self.buf) - 1 and self.buf[k] == 13 and not self.eof):
                line = self.buf[:k]
                skip = 2 if self.buf[k : k + 2] == b"\r\n" else 1
                self.buf = self.buf[k + skip :]
                break
            if self.eof:
                if not self.buf:
                    return None
                line, self.buf = self.buf, b""
                break
            self._fill()
        if strip_bom and line[:3] == b"\xef\xbb\xbf":
            line = line[3:]
        return _latin1_fix(line.split(b"\0", 1)[0].decode("utf-8", "surrogateescape"))


def _sniff_delimiter(path: str | os.PathLike[str]) -> str:
    """The field delimiter of the first non-blank, non-comment line (``.sniff_delimiter()``)."""
    reader = _LineReader(path)
    line: str | None = None
    for _ in range(10):
        line = reader.next()
        if line is None:
            break
        stripped = trimws(line)
        if stripped != "" and not stripped.startswith("#"):
            break
    if line is None:
        return ","
    candidates = (",", ";", "\t", "|")
    counts = [line.count(d) for d in candidates]
    if max(counts) == 0:
        return ","
    return candidates[counts.index(max(counts))]


_NUMLIKE = frozenset({"NA", "NAN", "NULL", "INF", "-INF", "+INF"})


def _detect_header(path: str | os.PathLike[str], sep: str) -> bool:
    """Whether a delimited file has a header row (``.detect_header()``).

    Headerless when its first two non-comment rows are both all-numeric.
    """
    reader = _LineReader(path)
    lines: list[str] = []
    while len(lines) < 2:
        line = reader.next()
        if line is None:
            break
        stripped = trimws(line)
        if stripped != "" and not stripped.startswith("#"):
            lines.append(line)
    if len(lines) < 2:
        return True

    def split_row(line: str) -> list[str]:
        return list(trimws(gsub('^"|"$', "", strsplit(line, sep, fixed=True))))

    def is_num(x: str) -> bool:
        if x == "":
            return True
        if _toupper(x) in _NUMLIKE:
            return True
        v = _r_as_numeric(x)
        return v is not None and not math.isnan(v)

    def all_num(toks: list[str]) -> bool:
        return len(toks) > 0 and all(is_num(t) for t in toks)

    return not (all_num(split_row(lines[0])) and all_num(split_row(lines[1])))


def _count_fields(line: str, sep: str) -> int:
    """Top-level fields in a line, outside double quotes (``.count_fields()``)."""
    if line == "":
        return 0
    in_quote = False
    fields = 1
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch == sep:
            fields += 1
    return fields


def _is_single_field_blob(path: str | os.PathLike[str], sep: str) -> bool:
    """A single huge value under a single header -- not a table (``.is_single_field_blob()``)."""
    try:
        reader = _LineReader(path)
        first2 = [ln for ln in (reader.next(), reader.next(strip_bom=False)) if ln is not None]
    except OSError:
        return False
    if len(first2) < 2:
        return False
    header, row1 = first2
    return (
        _count_fields(header, sep) <= 1
        and _count_fields(row1, sep) <= 1
        and len(row1.encode("utf-8", "surrogateescape")) >= _BLOB_ROW_MIN_BYTES
    )


# -----------------------------------------------------------------------------
# Reading data files
# -----------------------------------------------------------------------------


def _read_delim_fast(
    path: str | os.PathLike[str], sep: str, header: bool, n_rows: float = math.inf
) -> pd.DataFrame:
    """Read a delimited file as ``data.table::fread()`` does (``.read_delim_fast()``).

    Falls back to a ``utils::read.delim()`` emulation (with a Latin-1 retry for
    invalid UTF-8) when fread fails, as R does.
    """
    from metacheck.datacheck._files_fread import FreadError, fread
    from metacheck.datacheck._files_readers import read_delim

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return fread(Path(path), sep=sep, header=header, nrows=n_rows)
    except (FreadError, ValueError, OSError):  # tryCatch(fread(...), error = NULL)
        pass
    df = read_delim(path, sep=sep, header=header, nrows=n_rows)
    # re-read as Latin-1 only when a text cell is not valid UTF-8 (metacheck's
    # is.na(iconv(col, "UTF-8", "UTF-8")) is also TRUE for an NA cell, so a
    # valid UTF-8 file with an empty cell is re-read and "é" becomes "Ã©")
    has_invalid = any(
        isinstance(df.iloc[:, j].dtype, pd.StringDtype)
        and any(isinstance(v, str) and _has_invalid_utf8(v) for v in df.iloc[:, j].tolist())
        for j in range(df.shape[1])
    )
    if has_invalid:
        df = read_delim(path, sep=sep, header=header, nrows=n_rows, encoding="latin1")
    return df


def _flatten_cell(v: Any, limit: int) -> str:
    """``paste(utils::head(unlist(e), limit), collapse = "; ")`` (``None`` is NULL)."""
    if v is None:
        return ""
    items = _unlist(v)[:limit]
    return "; ".join("NA" if s is None else s for s in items)


def _unlist(v: Any) -> list[str | None]:
    if isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, np.ndarray):
        v = v.tolist()
    if isinstance(v, list | tuple):
        out: list[str | None] = []
        for e in v:
            out.extend(_unlist(e))
        return out
    return [_r_as_character(v)]


def _set_names(df: pd.DataFrame, names: list[str]) -> None:
    """``names(df) <- names``, carrying ``attrs["col_attrs"]`` over by position."""
    from metacheck.datacheck._colattrs import rename_col_attrs

    old = list(df.columns)
    df.columns = pd.Index(names, dtype=object)
    col_attrs = df.attrs.get("col_attrs")
    if col_attrs or getattr(col_attrs, "positional", None):  # a ColAttrs may key no name
        df.attrs["col_attrs"] = rename_col_attrs(cast(Mapping[str, Any], col_attrs), old, names)


def _utf8_repair_df(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Coerce a just-read data frame to valid UTF-8, names first, then values.

    Port of ``.utf8_repair_df()``: list columns are flattened to text; invalid
    UTF-8 names and values are reinterpreted as Latin-1, and the per-column
    repair counts are recorded in ``df.attrs["utf8_repaired"]``.
    """
    if df is None:
        return None
    if df.shape[1] > 0:
        for j in range(df.shape[1]):
            col = df.iloc[:, j]
            if col.dtype != object or not any(
                isinstance(v, list | tuple | dict | np.ndarray) for v in col.tolist()
            ):
                continue
            flat = [_flatten_cell(v, 20) for v in col.tolist()]
            df = df.copy(deep=False) if df is not None else df
            df.isetitem(j, pd.array(flat, dtype=RAW_STRING))
    names = [str(c) for c in df.columns]
    if any(_has_invalid_utf8(nm) for nm in names):
        fixed = []
        for j, nm in enumerate(names, 1):
            if _has_invalid_utf8(nm):
                nm = _latin1_fix(nm) or f"col_{j}"
            fixed.append(nm)
        df = df.copy(deep=False)
        _set_names(df, fixed)
    if df.shape[1] > 0:
        repaired: dict[str, int] = {}
        for j in range(df.shape[1]):
            col = df.iloc[:, j]
            name = str(df.columns[j])
            if isinstance(col.dtype, pd.CategoricalDtype):
                cats = [str(c) for c in col.cat.categories]
                bad = [_has_invalid_utf8(c) for c in cats]
                if any(bad):
                    df = df.copy(deep=False)
                    df.isetitem(j, col.cat.rename_categories([_latin1_fix(c) for c in cats]).array)
                    repaired[name] = sum(bad)
            elif isinstance(col.dtype, pd.StringDtype) or col.dtype == object:
                if isinstance(col.dtype, pd.StringDtype) and not _has_invalid_utf8(
                    "".join(col.dropna().tolist())  # a lone surrogate stays one when joined
                ):
                    continue
                vals = col.tolist()
                bad = [isinstance(v, str) and _has_invalid_utf8(v) for v in vals]
                if any(bad):
                    new = [_latin1_fix(v) if b else v for v, b in zip(vals, bad, strict=True)]
                    df = df.copy(deep=False)
                    df.isetitem(j, pd.Series(new, index=df.index, dtype=col.dtype).array)
                    repaired[name] = sum(bad)
        if repaired:
            df.attrs["utf8_repaired"] = repaired
    return _default_strings(df)


def _default_strings(df: pd.DataFrame) -> pd.DataFrame:
    """*df* with its repaired ``RAW_STRING`` columns in pandas' default string dtype."""
    default = pd.StringDtype()
    if default.storage == RAW_STRING.storage:  # no pyarrow: they are the same
        return df
    raw = [j for j in range(df.shape[1]) if df.dtypes.iloc[j] == RAW_STRING]
    if raw:
        df = df.copy(deep=False)
        for j in raw:
            df.isetitem(j, df.iloc[:, j].astype(default).array)
    return df


def _checks_fn(name: str) -> Callable[..., Any] | None:
    """A data_check helper from :mod:`metacheck.datacheck.checks` (if ported yet)."""
    try:
        from metacheck.datacheck import checks
    except ImportError:
        return None
    return getattr(checks, name, None)


def _is_qualtrics(df: pd.DataFrame) -> bool:
    fn = _checks_fn("data_check_is_qualtrics")
    return bool(fn(df)) if fn is not None else False


def _strip_qualtrics(df: pd.DataFrame) -> pd.DataFrame:
    fn = _checks_fn("data_strip_qualtrics_header")
    return fn(df) if fn is not None else df


def _promote_header(df: pd.DataFrame, raw_rows: list[list[str | None]]) -> dict[str, Any]:
    fn = _checks_fn("data_promote_header_row")
    if fn is None:
        return {"df": df, "promoted": 0}
    return dict(fn(df, raw_rows=raw_rows))


def _raw_rows(raw: pd.DataFrame) -> list[list[str | None]]:
    """``lapply(seq_len(nrow(raw)), function(i) as.character(raw[i, , drop = TRUE]))``.

    ``raw[i, , drop = TRUE]`` of a one-column frame is a vector, whose
    ``as.character()`` keeps every ``NA``; of a wider frame it is a list, whose
    ``as.character()`` keeps a character ``NA`` but turns any other ``NA``
    into ``"NA"`` (a date is its day number, a date-time its seconds).
    """
    from metacheck.datacheck._checks_rvec import chr as r_chr
    from metacheck.datacheck._checks_rvec import row_as_character, rvec

    if raw.shape[1] == 1:
        return [[v] for v in r_chr(raw.iloc[:, 0])]
    cols = [rvec(raw.iloc[:, j]) for j in range(raw.shape[1])]
    kinds = [c.kind for c in cols]
    levels = [c.levels for c in cols]
    return [row_as_character([c.values[i] for c in cols], kinds, levels) for i in range(len(raw))]


def data_read_head(
    path: str | os.PathLike[str], n_rows: float = 5, sheet: str | int | None = None
) -> pd.DataFrame | None:
    """Read the head of a data file regardless of format.

    Port of ``R/data_check_helpers.R::data_read_head()``: delimited text (``.csv``,
    ``.txt``, ``.tsv``, ``.dat``, ``.tab``, ``.table``; delimiter and header
    detected, Qualtrics header rows stripped, misplaced headers promoted), Excel, OpenDocument, SPSS/Stata/SAS, JASP/jamovi and R
    data files. ``n_rows = math.inf`` reads everything; *sheet* selects an
    Excel sheet (name or 1-based index). Returns ``None`` on failure or for an
    unsupported format (with a warning when reading failed).
    """
    path = str(path)
    ext = _tolower(_file_ext(path) or "")
    from metacheck.datacheck import _files_readers as readers

    try:
        df: pd.DataFrame | None
        if ext in ("csv", "txt", "tsv", "dat", "tab", "table"):
            sep = "\t" if ext == "tsv" else _sniff_delimiter(path)
            hdr = _detect_header(path, sep)
            if _is_single_field_blob(path, sep):
                return None
            df = _read_delim_fast(path, sep=sep, header=hdr, n_rows=n_rows)
            if not hdr and df is not None and df.shape[1] > 0:
                _set_names(df, [f"col_{j}" for j in range(1, df.shape[1] + 1)])
            df = _utf8_repair_df(df)
            if df is not None and _is_qualtrics(df):
                df = _strip_qualtrics(df)
            if df is not None and len(df) >= 1:
                try:
                    raw = _utf8_repair_df(_read_delim_fast(path, sep=sep, header=False, n_rows=6))
                except Exception:
                    raw = None
                if raw is not None and len(raw) >= 2:
                    df = _promote_header(df, _raw_rows(raw))["df"]
        elif ext in ("xlsx", "xls", "ods", "fods"):
            df = readers.read_sheet_head(
                path,
                ext,
                n_rows,
                sheet,
                is_qualtrics=_is_qualtrics,
                strip_qualtrics=_strip_qualtrics,
                promote=lambda d, raw: _promote_header(d, _raw_rows(_utf8_repair_df(raw))),  # type: ignore[arg-type]
            )
        elif ext in ("sav", "dta", "sas7bdat", "por"):
            df = readers.read_stat_file(path, ext, n_rows)
        elif ext in ("jasp", "omv"):
            df = readers.read_jasp_omv(path, ext, n_rows)
        elif ext == "rds":
            df = readers.read_rds_head(path, n_rows)
        elif ext in ("rda", "rdata"):
            df = _read_rdata_isolated(path, n_rows)
        else:
            df = None
        return _utf8_repair_df(df)
    except Exception as exc:
        msg = str(exc)
        if "time limit" in msg.lower():
            raise
        warnings.warn(f"Could not read {_r_basename(path)}: {msg}", stacklevel=2)
        return None


def _read_rdata_isolated(path: str | os.PathLike[str], n_rows: float = 5) -> pd.DataFrame | None:
    """The first data frame in an ``.RData``/``.rda`` workspace (head of *n_rows*).

    Port of ``.read_rdata_isolated()``. R restores the workspace in a separate
    process because loading model objects can crash it; pytacheck parses the
    serialization format itself (nothing is evaluated), so no subprocess is
    needed. ``None`` when the workspace holds no data frame or cannot be read.
    """
    from metacheck.datacheck._files_readers import read_rdata_first_df

    try:
        return read_rdata_first_df(path, n_rows)
    except Exception:
        return None


def rscript_path() -> str | None:
    """Path to an ``Rscript`` executable (``rscript_path()``).

    R returns the running installation's Rscript. pytacheck has no R of its
    own: this is ``$METACHECK_RSCRIPT``, ``$PYTACHECK_RSCRIPT`` or the first ``Rscript`` on ``PATH``
    (``None`` when there is none). pytacheck itself never needs it.
    """
    env = env_get("RSCRIPT")
    if env:
        return env
    return shutil.which("Rscript.exe" if sys.platform == "win32" else "Rscript")


# -----------------------------------------------------------------------------
# Likert scale detection
# -----------------------------------------------------------------------------


def _likert_values(x: Any) -> np.ndarray:
    """The finite numeric values of *x* (R: ``x[!is.na(x) & is.finite(x)]``) as doubles.

    Strings and missing values are dropped; numeric arrays and Series take a
    vectorised path.
    """
    arr: np.ndarray | None = None
    if isinstance(x, pd.Series):
        if pd.api.types.is_numeric_dtype(x.dtype) or pd.api.types.is_bool_dtype(x.dtype):
            arr = x.to_numpy(dtype=float, na_value=np.nan)
    elif isinstance(x, np.ndarray | list | tuple):
        try:
            a = np.asarray(x)
        except (TypeError, ValueError, OverflowError):
            a = None
        if a is not None and a.dtype.kind in "biuf":
            arr = a.astype(float, copy=False).ravel()
    if arr is None:
        arr = np.array(
            [float(v) for v in _as_list(x) if v is not None and not isinstance(v, str)],
            dtype=float,
        )
    finite: np.ndarray = arr[np.isfinite(arr)]
    return finite


def _detect_likert_scale(
    x: Any,
    max_levels: int = 23,
    min_core: int = 3,
    min_coverage: float = 0.90,
    common_frac: float = 0.01,
) -> dict[str, Any] | None:
    """Detect a Likert / rating scale in a numeric column and infer its range.

    Port of ``.detect_likert_scale()``: the dense consecutive core around the
    modal level, the floor anchored to 0/1, and everything outside the accepted
    range returned as ``suspects``. ``None`` when the column is not a scale.
    """
    vals = _likert_values(x)
    if vals.size < 20:
        return None
    if bool(np.any(vals != np.round(vals))):
        return None
    # as.integer(round(x)): beyond .Machine$integer.max the value becomes NA,
    # which still counts in n but is no level and never a suspect
    xs = vals[np.abs(vals) <= 2147483647].astype(np.int64)
    levels, counts_arr = np.unique(xs, return_counts=True)
    u = [int(v) for v in levels]
    if len(u) < 2 or len(u) > max_levels:
        return None
    counts = dict(zip(u, (int(c) for c in counts_arr), strict=True))
    lv = u
    cnt = [counts[v] for v in lv]
    n = int(vals.size)
    mode_i = cnt.index(max(cnt))
    common_floor = max(common_frac * n, 2)
    is_common = [c >= common_floor for c in cnt]
    lo = hi = lv[mode_i]

    def extend(direction: int) -> None:
        nonlocal lo, hi
        while True:
            cand = [p for p in lv if p > hi] if direction > 0 else [p for p in lv if p < lo]
            if not cand:
                break
            nxt = min(cand) if direction > 0 else max(cand)
            gap = abs(nxt - (hi if direction > 0 else lo))
            ni = lv.index(nxt)
            if gap == 1 or (is_common[ni] and -11 <= nxt <= 11):
                if direction > 0:
                    hi = nxt
                else:
                    lo = nxt
            else:
                break

    extend(+1)
    extend(-1)
    if hi - lo + 1 < min_core:
        return None
    floor_inferred: list[int] = []
    natural_floor = 0 if 0 in counts else 1
    if natural_floor < lo <= natural_floor + 2 and natural_floor >= -11:
        floor_inferred = [v for v in range(natural_floor, lo) if v not in counts]
        lo = natural_floor
    if lo < -11 or hi > 11:
        return None
    coverage = int(np.count_nonzero((xs >= lo) & (xs <= hi))) / n
    if coverage < min_coverage:
        return None
    suspects = [v for v in u if not lo <= v <= hi]
    levels_present = [v for v in range(lo, hi + 1) if v in counts]
    inf = ""
    if floor_inferred:
        inf = (
            f"; inferred the unobserved floor value{plural(len(floor_inferred))} "
            f"{', '.join(str(v) for v in floor_inferred)} to make it a {natural_floor}-based scale"
        )
    note = (
        f"Detected a {lo}-{hi} rating scale (levels observed: "
        f"{', '.join(str(v) for v in levels_present)}{inf})."
    )
    return {
        "lo": lo,
        "hi": hi,
        "levels_present": levels_present,
        "coverage": coverage,
        "suspects": suspects,
        "floor_inferred": floor_inferred,
        "note": note,
    }
