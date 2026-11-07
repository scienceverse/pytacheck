"""The metadata FAIR_repo() writes beside the reorganised files.

Port of ``R/fair-repo-metadata.R``: a Psych-DS ``dataset_description.json`` per
study, an RO-Crate ``ro-crate-metadata.json`` at the top, a ``requirements.txt``,
``logs/``, and the README and CHANGES files. The paper's full text is not written
(R keeps ``.FAIR_repo_write_paper_text()`` commented out, so it is not ported).

Every R function ``.FAIR_repo_x`` is ``_FAIR_repo_x`` here. The functions call each
other through the module's globals, so a test can replace one of them.

Data shapes (so ``metacheck.fair.repo`` fits this file):

* a table is a pandas DataFrame with R's column names; a missing value is ``None`` or
  ``pd.NA``; a list column is an ``object`` column of Python lists;
* ``plan`` is the file plan; row positions used in code are 0-based, but the stored
  ``plan["recovered_row"]`` values are R's 1-based row numbers;
* the RO-Crate and ``dataset_description.json`` objects are plain dicts and lists built
  in R's key order. A key with value ``None`` is dropped, as R's
  ``Filter(Negate(is.null), ...)`` does. A Python list is a JSON array (R's list), a
  Python scalar is a JSON scalar (R's length-one vector).
"""

from __future__ import annotations

import dataclasses
import json
import os
import posixpath
import re
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r import grepl, gsub, strsplit, sub
from metacheck._r.base import as_character, plural, r_sort_key, trimws
from metacheck._values import as_int, is_missing, is_true
from metacheck.datacheck._files_registry import EXT_REGISTRY
from metacheck.text.causal import _json_number

__all__: list[str] = []

# The text written into any field metacheck cannot extract, so a researcher can search
# the file for it and knows what to fill in by hand.
_FAIR_repo_unknown = "unknown"

# Representation that receives a numeric statistics block in variableMeasured.
_FAIR_repo_numeric_reps = ["numeric"]
# Measurement levels that receive a valuePattern (their distinct values matter).
_FAIR_repo_categorical_levels = ["nominal", "ordinal"]

# The study's two life-cycle events metacheck cannot find out (see lifecycle_entities).
_FAIR_repo_lifecycle_ids = ["collection", "processing"]

# The 14 CRediT (ANSI/NISO Z39.104-2022) contributor roles.
_FAIR_repo_credit_roles = [
    "conceptualization",
    "data-curation",
    "formal-analysis",
    "funding-acquisition",
    "investigation",
    "methodology",
    "project-administration",
    "resources",
    "software",
    "supervision",
    "validation",
    "visualization",
    "writing-original-draft",
    "writing-review-editing",
]

# The software a file format needs, for formats that need more than a text editor or
# spreadsheet program.
_FAIR_repo_format_software: dict[str, str] = {
    "sav": "IBM SPSS Statistics",
    "zsav": "IBM SPSS Statistics",
    "por": "IBM SPSS Statistics",
    "sps": "IBM SPSS Statistics",
    "spv": "IBM SPSS Statistics",
    "dta": "Stata",
    "do": "Stata",
    "smcl": "Stata",
    "sas7bdat": "SAS",
    "xpt": "SAS",
    "sas": "SAS",
    "xlsx": "Microsoft Excel or LibreOffice Calc",
    "xls": "Microsoft Excel or LibreOffice Calc",
    "ods": "LibreOffice Calc",
    "jasp": "JASP",
    "omv": "jamovi",
    "rds": "R",
    "rdata": "R",
    "rda": "R",
    "mat": "MATLAB",
    "m": "MATLAB",
    "inp": "Mplus",
    "psyexp": "PsychoPy",
    "iqx": "Inquisit",
    "ebs2": "E-Prime",
    "es3": "E-Prime",
    "osexp": "OpenSesame",
}

# ── small helpers (R base functions and the data shapes) ─────────────────────


def _py(v: Any) -> Any:
    """A cell as a plain Python value: numpy scalars unwrapped, a missing value ``None``."""
    if isinstance(v, np.generic):
        v = v.item()
    return None if is_missing(v) else v


def _is_listlike(v: Any) -> bool:
    return isinstance(v, list | tuple | np.ndarray | pd.Series)


def _flatten(x: Any) -> list[Any]:
    """R's ``unlist(x, use.names = FALSE)``: every leaf of nested lists, in order."""
    if x is None:
        return []
    if isinstance(x, Mapping):
        return [leaf for v in x.values() for leaf in _flatten(v)]
    if _is_listlike(x):
        return [leaf for v in list(x) for leaf in _flatten(v)]
    return [x]


def _chr(v: Any) -> str | None:
    """R's ``as.character()`` of one value (``None`` for a missing one)."""
    v = _py(v)
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if _is_listlike(v):
        items = _flatten(v)
        return _chr(items[0]) if len(items) == 1 else None
    return as_character(v)


def _has(x: Any) -> bool:
    """R's ``length(x) == 1 && !is.na(x) && nzchar(as.character(x))`` for one cell."""
    x = _py(x)
    if x is None or _is_listlike(x) or isinstance(x, Mapping):
        return False
    return x != "" if isinstance(x, str) else True


def _basename(p: str) -> str:
    return posixpath.basename(p.rstrip("/"))


def _dirname(p: str) -> str:
    """R's ``dirname()``: ``"."`` for a bare file name."""
    d = posixpath.dirname(p.rstrip("/"))
    return d if d else "."


_EXT_RE = re.compile(r"\.([^\W_]+)\Z")


def _file_ext(p: str) -> str:
    """``tools::file_ext()``: the letters and digits after the last dot, else ``""``."""
    m = _EXT_RE.search(p)
    return m.group(1) if m else ""


def _today() -> str:
    """``format(Sys.Date(), "%Y-%m-%d")``."""
    return time.strftime("%Y-%m-%d")


def _compact(d: Mapping[str, Any]) -> dict[str, Any]:
    """R's ``Filter(Negate(is.null), list(...))``: the entries whose value is not ``None``."""
    return {k: v for k, v in d.items() if v is not None}


def _unique(x: Sequence[Any]) -> list[Any]:
    """R's ``unique()``: the first appearance of each value."""
    return list(dict.fromkeys(x))


def _col(df: Any, name: str) -> list[Any]:
    """The cells of column *name* as plain values (all ``None`` when the column is absent)."""
    if isinstance(df, pd.DataFrame) and name in df.columns:
        return [v if _is_listlike(v) else _py(v) for v in df[name].tolist()]
    return [None] * (len(df) if isinstance(df, pd.DataFrame) else 0)


def _nrow(df: Any) -> int:
    return len(df) if isinstance(df, pd.DataFrame) else 0


def _ref(ids: Sequence[Any]) -> list[dict[str, Any]]:
    """RO-Crate references: one ``{"@id": id}`` per id, as a list (a JSON array)."""
    return [{"@id": x} for x in ids]


def _as_list(x: Any) -> list[Any]:
    """A value that R holds as a character vector, as a list (``None`` is empty)."""
    if x is None:
        return []
    return _flatten(x)


# ── how the module outputs (``ops``) are read ────────────────────────────────


def _mo(ops: Any, name: str) -> Any:
    """The module output called *name* in ``ops`` (``None`` when absent)."""
    if ops is None or not hasattr(ops, "get"):
        return None
    return ops.get(name)


def _mo_table(ops: Any, name: str) -> Any:
    """``ops[[name]]$table``: the table of one module, from a ModuleOutput, a dict, or a fake."""
    mo = _mo(ops, name)
    if mo is None:
        return None
    if isinstance(mo, Mapping):
        return mo.get("table")
    tb = getattr(mo, "table", None)
    if tb is not None:
        return tb
    getter = getattr(mo, "get", None)
    return getter("table") if callable(getter) else None


def _mo_get(ops: Any, name: str, key: str) -> Any:
    """``ops[[name]][[key]]``: an element of one module output (``None`` when absent)."""
    if key == "table":
        return _mo_table(ops, name)
    mo = _mo(ops, name)
    if mo is None:
        return None
    if isinstance(mo, Mapping):
        return mo.get(key)
    getter = getattr(mo, "get", None)
    if callable(getter):
        return getter(key)
    return getattr(mo, key, None)


# One study of the copy: `dir` is its folder ("" when the copy holds one study, else
# "study-<group>"), `grp` its group code (None for one study).
def _FAIR_repo_roots(plan: pd.DataFrame) -> list[dict[str, Any]]:
    """The studies of the copy (port of ``.FAIR_repo_roots()``)."""
    written = [w for w in _col(plan, "written") if isinstance(w, str) and w.startswith("study-")]
    dirs = _unique([re.sub(r"^(study-[^/]+)/.*$", r"\1", w, flags=re.DOTALL) for w in written])
    if not dirs:
        return [{"dir": "", "grp": None}]
    return [{"dir": d, "grp": d[len("study-") :]} for d in sorted(dirs, key=r_sort_key)]


def _FAIR_repo_id(grp: str | None) -> str:
    """``"-ex1"`` for a group's ids, ``""`` for a copy with one study."""
    return "" if grp is None else f"-{grp}"


def _FAIR_repo_label(grp: str | None) -> str:
    return "" if grp is None else f"Study {grp.upper()}"


# ── dataset_description.json ─────────────────────────────────────────────────


def _merge_labels(cols: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """``merge(cols, labels, by = c(source_file, column_name), all.x, sort = FALSE)``.

    R lists the matched rows first (a key in the order it first appears in ``cols``),
    then the rows without a label; a label column that clashes with a column of
    ``cols`` is suffixed ``.lbl``.
    """
    by = ["source_file", "column_name"]
    extra = [c for c in labels.columns if c not in by]
    lab = labels.reset_index(drop=True)
    pos: dict[tuple[str, str], int] = {}
    for i, key in enumerate(zip(_col(lab, "source_file"), _col(lab, "column_name"), strict=True)):
        pos.setdefault((str(key[0]), str(key[1])), i)
    keys = [
        (str(a), str(b))
        for a, b in zip(_col(cols, "source_file"), _col(cols, "column_name"), strict=True)
    ]
    first: dict[tuple[str, str], int] = {}
    for i, k in enumerate(keys):
        if k in pos:
            first.setdefault(k, i)
    matched = sorted((i for i, k in enumerate(keys) if k in pos), key=lambda i: (first[keys[i]], i))
    alone = [i for i, k in enumerate(keys) if k not in pos]
    order = matched + alone
    left = cols.iloc[order].reset_index(drop=True)
    rows = [pos.get(keys[i]) for i in order]
    right = lab[extra].reindex([-1 if r is None else r for r in rows]).reset_index(drop=True)
    right = right.astype(object).where(pd.Series([r is not None for r in rows]), None)
    right.columns = [c + ".lbl" if c in cols.columns else c for c in extra]
    return pd.concat([left, right], axis=1)


def _FAIR_repo_variable_measured(
    cols: pd.DataFrame | None,
    labels: pd.DataFrame | None = None,
    new_path: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The ``variableMeasured`` list for one set of columns (``.FAIR_repo_variable_measured()``).

    *cols* is a subset of data_check's columns table; *labels* is codebook_check's
    labels table (or ``None``). Every column is described, because Psych-DS requires
    each CSV column to appear in ``variableMeasured``; an empty (all-NA) column gets a
    short entry saying so, without statistics. *new_path* maps data_check's
    ``source_file`` to the path written in the copy.
    """
    from metacheck.datacheck._columns_labels import _decode_value_labels

    if cols is None or not len(cols):
        return []
    if (
        isinstance(labels, pd.DataFrame)
        and len(labels)
        and {"source_file", "column_name"} <= set(labels.columns)
        and {"source_file", "column_name"} <= set(cols.columns)
    ):
        keep = [
            "source_file",
            "column_name",
            "label",
            "label_status",
            "label_source",
            "codebook_variable",
            "scale",
            "scale_confidence",
            "scale_source",
            "value_labels",
            "missing_values",
            "question",
        ]
        labels = labels[[c for c in keep if c in labels.columns]]
        labels = labels[~labels.duplicated(subset=["source_file", "column_name"])]
        cols = _merge_labels(cols, labels)
    paths = new_path or {}

    def src_of(s: Any) -> Any:
        return paths.get(s, s)

    out: list[dict[str, Any]] = []
    for record in cols.to_dict("records"):
        row: dict[str, Any] = {str(k): v for k, v in record.items()}

        def field(f: str, row: dict[str, Any] = row) -> Any:
            return _py(row[f]) if f in row and not _is_listlike(row[f]) else None

        quality = field("quality")
        if isinstance(quality, str) and quality.lower() == "empty":
            out.append(
                _compact(
                    {
                        "@type": "PropertyValue",
                        "name": field("column_name"),
                        "description": "Empty column (no observed values in the shared data).",
                        "metacheck:quality": "empty",
                        "metacheck:sourceFile": src_of(field("source_file"))
                        if _has(field("source_file"))
                        else None,
                    }
                )
            )
            continue

        rep = field("representation")
        lvl = field("measurement_level")
        pv: dict[str, Any] = {"@type": "PropertyValue", "name": field("column_name")}

        # Variable description from the codebook, when documented.
        if field("label_status") in ("labelled", "llm") and _has(field("label")):
            pv["description"] = field("label")
            if _has(field("label_source")):
                pv["metacheck:labelSource"] = field("label_source")
            if _has(field("codebook_variable")):
                pv["metacheck:codebookVariable"] = field("codebook_variable")

        # The instrument a variable was measured with goes in schema.org's own
        # measurementTechnique; how it was identified in a metacheck extension.
        if _has(field("scale")):
            pv["measurementTechnique"] = field("scale")
            pv["metacheck:scale"] = _compact(
                {
                    "name": field("scale"),
                    "source": field("scale_source") if _has(field("scale_source")) else None,
                    "confidence": field("scale_confidence")
                    if _has(field("scale_confidence"))
                    else None,
                }
            )

        # Measurement level and concept have no schema.org property (DDI
        # classificationLevel and Concept); unit is schema.org unitText.
        if _has(lvl):
            pv["metacheck:measurementLevel"] = lvl
        if _has(field("concept")):
            pv["metacheck:concept"] = field("concept")
        if _has(field("unit")):
            pv["unitText"] = field("unit")
        if _has(field("role")):
            pv["metacheck:role"] = field("role")
        if _has(field("question")):
            pv["metacheck:question"] = field("question")

        # Value labels (DDI CodeList), each code with its label.
        vl_json = field("value_labels")
        vl = _decode_value_labels(vl_json if _has(vl_json) else None)
        codes: list[str | None]
        names: list[Any]
        if isinstance(vl, pd.Series):
            codes, names = [str(k) for k in vl.index], [_py(v) for v in vl.tolist()]
        elif isinstance(vl, Mapping):
            codes, names = [str(k) for k in vl], [_py(v) for v in vl.values()]
        elif vl:
            codes, names = [None] * len(vl), [_py(v) for v in vl]
        else:
            codes, names = [], []
        if codes:
            pv["metacheck:valueLabels"] = vl_json
            pv["metacheck:codeList"] = [
                {"@type": "PropertyValue", "value": c, "name": nm}
                for c, nm in zip(codes, names, strict=True)
            ]
        # Missing-value codes (DDI MissingValues).
        if _has(field("missing_values")):
            pv["metacheck:missingValues"] = field("missing_values")

        if rep == "numeric":
            if _has(field("min")):
                pv["minValue"] = field("min")
            if _has(field("max")):
                pv["maxValue"] = field("max")
            stat_fields = [
                "n",
                "n_missing",
                "mean",
                "sd",
                "se",
                "median",
                "p25",
                "p75",
                "iqr",
                "skewness",
                "kurtosis",
            ]
            stats = _compact({f: field(f) if _has(field(f)) else None for f in stat_fields})
            if stats:
                pv["metacheck:statistics"] = stats

        # The distinct values of a categorical text column.
        if (
            _has(lvl)
            and lvl in _FAIR_repo_categorical_levels
            and rep != "numeric"
            and _has(field("sample_values"))
        ):
            parts = strsplit(str(field("sample_values")), r"\|")
            vals = _unique([trimws(p) for p in parts if p is not None])
            vals = [v for v in vals if v != ""]
            if vals:
                pv["valuePattern"] = "|".join(vals)

        if _has(rep):
            pv["metacheck:representation"] = rep
        if _has(field("source_file")):
            pv["metacheck:sourceFile"] = src_of(field("source_file"))
        out.append(_compact(pv))
    return out


def _FAIR_repo_keywords(kw: Any) -> list[str] | None:
    """A keywords value as a flat list of strings (``.FAIR_repo_keywords()``).

    On a paper, ``paper.info["keywords"]`` can be a cell holding a list that holds the
    keywords, which would otherwise be written as a nested array. ``None`` when there
    are none.
    """
    if kw is None:
        return None
    if _is_listlike(kw) and len(kw) == 0:
        return None
    items = [_chr(v) for v in _flatten(kw)]
    out = [v for v in items if v is not None and v != ""]
    return out or None


def _FAIR_repo_is_web(x: Any) -> Any:
    """Whether a value is a web address (``.FAIR_repo_is_web()``): one value, or a list of them."""
    if isinstance(x, pd.Series) or _is_listlike(x):
        return [_FAIR_repo_is_web(v) for v in list(x)]
    v = _py(x)
    return isinstance(v, str) and bool(grepl(r"^https?://", v))


def _drop_local_text(x: str, folder: str) -> str:
    """The full path of *folder* in a text becomes the folder's own name."""
    base = _basename(folder)
    for f in _unique([folder, folder.replace("/", "\\")]):
        if f:
            x = x.replace(f, base)
    return x


def _drop_local_frame(x: pd.DataFrame, folder: str | None) -> pd.DataFrame:
    df = x.copy()
    if "file_location" in df.columns:
        df = df.drop(columns="file_location")
    for col in ("repo_url", "file_url"):
        if col in df.columns:
            cells = [
                v if (is_missing(v) or not isinstance(v, str) or _FAIR_repo_is_web(v)) else None
                for v in df[col].tolist()
            ]
            df[col] = pd.Series(cells, index=df.index, dtype=x[col].dtype)
    for col in list(df.columns):
        dtype = df[col].dtype
        if str(dtype) == "object":
            df[col] = pd.Series(
                [_FAIR_repo_drop_local(v, folder) for v in df[col].tolist()],
                index=df.index,
                dtype=object,
            )
        elif folder and isinstance(dtype, pd.StringDtype):
            df[col] = pd.Series(
                [v if is_missing(v) else _drop_local_text(v, folder) for v in df[col].tolist()],
                index=df.index,
                dtype=dtype,
            )
    return df


def _drop_local_dict(x: Mapping[str, Any], folder: str | None) -> dict[str, Any]:
    out = {k: v for k, v in x.items() if k != "file_location"}
    if isinstance(out.get("sandbox"), str):
        del out["sandbox"]
    for k in ("repo_url", "file_url"):
        v = out.get(k)
        if isinstance(v, str) and not _FAIR_repo_is_web(v):
            out[k] = None
    return {k: _FAIR_repo_drop_local(v, folder) for k, v in out.items()}


def _FAIR_repo_drop_local(x: Any, folder: str | None = None) -> Any:
    """A module output without the places on this computer it holds, at any depth.

    Port of ``.FAIR_repo_drop_local()``: every ``file_location`` (where a file is; also
    inside code_check's ``version_pin``), a ``repo_url`` or ``file_url`` that is a local
    path, reproducibility_check's ``sandbox`` folder, and the full path of *folder* in
    any text (repo_check's report names it), which becomes the folder's own name. Used
    for what is written into ``logs/``. The input is not changed. A ModuleOutput's
    ``paper`` and ``prev_outputs`` are left as they are: the log leaves them out.
    """
    from metacheck.module import ModuleOutput

    if isinstance(x, str):
        return _drop_local_text(x, folder) if folder else x
    if isinstance(x, pd.DataFrame):
        return _drop_local_frame(x, folder)
    if isinstance(x, ModuleOutput):
        extras = _drop_local_dict(x.extras, folder)
        return dataclasses.replace(
            x,
            table=_FAIR_repo_drop_local(x.table, folder),
            report=_FAIR_repo_drop_local(x.report, folder),
            summary_text=_FAIR_repo_drop_local(x.summary_text, folder),
            summary_table=_FAIR_repo_drop_local(x.summary_table, folder),
            extras=extras,
        )
    if isinstance(x, Mapping):
        return _drop_local_dict(x, folder)
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        # a report block (a table, a text) holds the texts and tables of the report
        changes = {
            f.name: _FAIR_repo_drop_local(getattr(x, f.name), folder)
            for f in dataclasses.fields(x)
            if f.init
        }
        try:
            return dataclasses.replace(x, **changes)
        except (TypeError, ValueError):
            return x
    if isinstance(x, list):
        return [_FAIR_repo_drop_local(v, folder) for v in x]
    if isinstance(x, tuple):
        return tuple(_FAIR_repo_drop_local(v, folder) for v in x)
    return x


_MIME_BY_EXT: dict[str, str | None] = {}
for _row in EXT_REGISTRY:
    _MIME_BY_EXT.setdefault(_row[0], _row[4])


def _FAIR_repo_encoding_format(file_name: str) -> str | None:
    """MIME type for a file's extension, from the package's one extension table.

    ``None`` when none is known, so the field is left out (``.FAIR_repo_encoding_format()``).
    """
    return _MIME_BY_EXT.get(_file_ext(file_name).lower())


def _FAIR_repo_info(paper: Any, field: str) -> Any:
    """A field of ``paper.info``, or ``None`` when absent or empty (``.FAIR_repo_info()``)."""
    info = getattr(paper, "info", None) if paper is not None else None
    v: Any = None
    if isinstance(info, pd.DataFrame):
        if field in info.columns and len(info):
            v = info[field].iloc[0]
    elif isinstance(info, Mapping | pd.Series):
        v = info.get(field)
    if isinstance(v, np.generic):
        v = v.item()
    if _is_listlike(v):
        n = len(v)
        if n == 0:
            return None
        if n == 1:
            only = next(iter(v))
            atomic = only is not None and not _is_listlike(only) and not isinstance(only, Mapping)
            if atomic and (is_missing(only) or only == ""):
                return None
        return v
    if is_missing(v) or v == "":
        return None
    return v


def _FAIR_repo_doi_url(paper: Any) -> str | None:
    doi = _FAIR_repo_info(paper, "doi")
    if doi is None:
        return None
    return "https://doi.org/" + str(sub(r"^https?://(dx\.)?doi.org/", "", str(_chr(doi))))


def _FAIR_repo_dataset_description(
    paper: Any,
    title: str,
    study_label: str,
    variables: list[dict[str, Any]],
    reused: Sequence[str] = (),
) -> dict[str, Any]:
    """The ``dataset_description.json`` of one study (``.FAIR_repo_dataset_description()``)."""
    suffix = f" — {study_label}" if study_label else ""
    desc: dict[str, Any] = {
        "@context": "https://schema.org/",
        "@type": "Dataset",
        "name": f"{title}{suffix}",
        "description": "Psych-DS dataset generated by metacheck"
        + (f" for {study_label}" if study_label else "")
        + ".",
        "variableMeasured": variables,
        "schemaVersion": "Psych-DS 1.5.1",
    }
    au = _FAIR_repo_authors(paper)
    if len(au):
        desc["author"] = [{"@type": "Person", "name": x} for x in au["name"].tolist()]
    # the paper that uses the data; the dataset has no DOI of its own yet
    desc["citation"] = _FAIR_repo_doi_url(paper)
    desc["keywords"] = _FAIR_repo_keywords(_FAIR_repo_info(paper, "keywords"))

    # Files this study reuses from another study's folder, which hold them; a plain
    # key, because this file's @context is schema.org alone.
    if len(reused):
        desc["reusedMaterials"] = [
            {"contentUrl": p, "description": "Reused from another study in this collection."}
            for p in reused
        ]
    desc["metacheck:generated"] = _today()
    return _compact(desc)


def _FAIR_repo_reused_by_group(plan: pd.DataFrame) -> dict[str, list[str]]:
    """Per study group, the written paths of files it reuses from another study.

    ``plan["referenced_by"]`` names the OTHER studies whose scripts read a file (see
    data_group_llm()); the file is copied into its own study only.
    """
    out: dict[str, list[str]] = {}
    if "referenced_by" not in plan.columns:
        return out
    refs_col = plan["referenced_by"].tolist()
    written = _col(plan, "written")
    for refs, p in zip(refs_col, written, strict=True):
        refs_list = [_py(g) for g in _flatten(refs)] if refs is not None else []
        refs_list = [g for g in refs_list if g is not None]
        if not refs_list or p is None:
            continue
        for g in refs_list:
            out[g] = _unique([*out.get(g, []), p])
    return out


# ── ro-crate-metadata.json ───────────────────────────────────────────────────


def _FAIR_repo_abstract_text(paper: Any, max_chars: int = 1500) -> str:
    """The paper's abstract as one string, for the RO-Crate description.

    The sections typed "abstract" in the paper's text table; ``""`` when there is none
    (``.FAIR_repo_abstract_text()``). A missing text is left out (R would write "NA").
    """
    from metacheck.papers.model import is_paper
    from metacheck.papers.tables import paper_table

    if not is_paper(paper):
        return ""
    tx: Any
    se: Any
    try:
        tx = paper_table(paper, "text")
    except Exception:
        tx = None
    try:
        se = paper_table(paper, "section")
    except Exception:
        se = None
    if (
        not isinstance(tx, pd.DataFrame)
        or not len(tx)
        or not {"text", "section_id"} <= set(tx.columns)
        or not isinstance(se, pd.DataFrame)
        or not {"section_id", "section_type"} <= set(se.columns)
    ):
        return ""
    ids = se.loc[se["section_type"] == "abstract", "section_id"].tolist()
    at = tx[tx["section_id"].isin(ids)]
    if not len(at):
        return ""
    if "text_id" in at.columns:
        at = at.sort_values("text_id", kind="stable", na_position="last")
    texts = [t for t in _col(at, "text") if isinstance(t, str) and trimws(t) != ""]
    txt = str(trimws(" ".join(texts)))
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "…"
    return txt


def _FAIR_repo_ddi_array(x: Any) -> list[str]:
    """A one-element array, the shape of every DDI field in the RO-Crate.

    It holds "unknown" when the value is missing (``.FAIR_repo_ddi_array()``).
    """
    items = _as_list(x)
    v = _chr(items[0]) if items else None
    if v is None or trimws(v) == "":
        v = _FAIR_repo_unknown
    return [v]


def _FAIR_repo_prereg_field(ops: Any, pid: str, field: str) -> str | None:
    """One field of the paper's preregistration, from prereg_check's table.

    ``None`` when prereg_check was not run, the paper links no registration, or the
    field is empty. Also ``None`` when the paper links more than one: papers often
    register each study separately, with different numbers, and nothing yet says which
    registration covers which study, so taking the first would put one study's sample
    size on every study (``.FAIR_repo_prereg_field()``).
    """
    pr = _mo_table(ops, "prereg_check")
    if (
        not isinstance(pr, pd.DataFrame)
        or not len(pr)
        or not {"paper_id", field} <= set(pr.columns)
    ):
        return None
    ids = _col(pr, "paper_id")
    hit = [i for i, p in enumerate(ids) if p is not None and p == pid]
    if len(hit) != 1:
        return None
    v = _chr(pr[field].iloc[hit[0]])
    if v is None or trimws(v) == "":
        return None
    return v


def _FAIR_repo_study_design_entity(ops: Any, pid: str, grp: str | None = None) -> dict[str, Any]:
    """How a study was run, with DDI-Lifecycle 3.3 terms (``.FAIR_repo_study_design_entity()``).

    dataCollectionMethodology (ddi:DataCollectionMethodology), purpose (ddi:Purpose),
    populationOfConcern (ddi:PopulationOfConcern) and targetSampleSize
    (ddi:OverallTargetSampleSize), filled from a single linked preregistration when
    prereg_check was run. Only how the data were collected is kept as "unknown" when it
    is not found; the others are left out, so the authors are not given empty fields
    nobody asked for.
    """

    def get_f(field: str) -> str | None:
        return _FAIR_repo_prereg_field(ops, pid, field)

    def if_found(field: str) -> list[str] | None:
        v = get_f(field)
        return None if v is None else [v]

    label = _FAIR_repo_label(grp)
    return _compact(
        {
            "@id": "#study-design" + _FAIR_repo_id(grp),
            "@type": "ResearchProject",
            "name": f"Study design ({label})" if label else "Study design",
            "purpose": if_found("research_questions"),
            "dataCollectionMethodology": _FAIR_repo_ddi_array(get_f("study_design_overview")),
            "populationOfConcern": if_found("data_exclusion_criteria"),
            "targetSampleSize": if_found("sample_size"),
        }
    )


def _FAIR_repo_lifecycle_entities(grp: str | None = None) -> list[dict[str, Any]]:
    """The study's two life-cycle events metacheck cannot find out.

    DDI LifecycleEvents for the authors to fill in: when the data were collected
    (ddi:DataCollectionDate) and how they were processed into the shared data. The
    scripts that were run are separate events (see ``provenance_entities``).
    """
    label = _FAIR_repo_label(grp)
    of = f": {label}" if label else ""
    base = "#lifecycle" + _FAIR_repo_id(grp)
    return [
        {
            "@id": f"{base}-collection",
            "@type": "CreateAction",
            "name": f"Data collection{of}",
            "eventType": "data collection",
            "obtainedDate": _FAIR_repo_ddi_array(_FAIR_repo_unknown),
        },
        {
            "@id": f"{base}-processing",
            "@type": "CreateAction",
            "name": f"Data processing{of}",
            "eventType": "data processing",
            "description": _FAIR_repo_unknown,
        },
    ]


def _credit_slug_one(x: Any) -> str | None:
    v = _chr(x)
    if v is None:
        return None
    s = re.sub(r"(^-|-$)", "", re.sub(r"[^a-z]+", "-", v.lower()))
    return s if s in _FAIR_repo_credit_roles else None


def _FAIR_repo_credit_slug(x: Any) -> Any:
    """A role as the paper states it as its CRediT name, or ``None`` when it is not one.

    ``"Writing - review & editing"`` is ``"writing-review-editing"``. One value gives
    one value, a sequence a list (``.FAIR_repo_credit_slug()``).
    """
    if _is_listlike(x):
        return [_credit_slug_one(v) for v in list(x)]
    return _credit_slug_one(x)


def _FAIR_repo_credit_entities(authors: pd.DataFrame) -> list[dict[str, Any]]:
    """One CRediT Role per author (``.FAIR_repo_credit_entities()``).

    With the roles the paper states for them (by their NISO addresses), or "unknown"
    for the authors to fill in.
    """
    out: list[dict[str, Any]] = []
    names = _col(authors, "name")
    ids = _col(authors, "id")
    credits = authors["credit"].tolist() if "credit" in authors.columns else [None] * len(authors)
    for i in range(len(authors)):
        roles = [_chr(r) for r in _as_list(credits[i])]
        slugs = [_FAIR_repo_credit_slug(r) for r in roles]
        role_name: Any
        if roles:
            role_name = [
                r if s is None else {"@id": f"https://credit.niso.org/contributor-roles/{s}/"}
                for r, s in zip(roles, slugs, strict=True)
            ]
        else:
            role_name = _FAIR_repo_unknown
        out.append(
            {
                "@id": f"#credit-{i + 1}",
                "@type": "Role",
                "name": f"CRediT roles of {names[i]}",
                "agent": {"@id": ids[i]},
                "roleName": role_name,
            }
        )
    return out


# ── What is known about the paper and the repository ─────────────────────────


def _FAIR_repo_authors(paper: Any) -> pd.DataFrame:
    """The paper's authors (``.FAIR_repo_authors()``).

    Columns: ``name``, ``orcid``, ``email``, ``corresponding``, ``affiliation``, ``id``
    (ORCID address, else ``"#author-<n>"``) and ``credit`` (a list of the roles the
    paper states for each). Papers read with bibr carry email, CRediT roles and an
    affiliation table; others may have only names. An empty table has R's column order
    for an empty one (``id`` before ``orcid``).
    """
    au = getattr(paper, "author", None) if paper is not None else None
    if not isinstance(au, pd.DataFrame) or not len(au):
        return pd.DataFrame(
            {
                "name": pd.Series([], dtype="string"),
                "id": pd.Series([], dtype="string"),
                "orcid": pd.Series([], dtype="string"),
                "email": pd.Series([], dtype="string"),
                "corresponding": pd.Series([], dtype=bool),
                "affiliation": pd.Series([], dtype="string"),
                "credit": pd.Series([], dtype=object),
            }
        )
    n = len(au)

    def chr_(col: str) -> list[str | None]:
        if col in au.columns and not any(_is_listlike(v) for v in au[col].tolist()):
            v = [_chr(x) for x in au[col].tolist()]
        else:
            v = [None] * n
        return [None if x is not None and trimws(x) == "" else x for x in v]

    given = chr_("given")
    family = chr_("family")
    name = [trimws(f"{g or ''} {f or ''}") for g, f in zip(given, family, strict=True)]
    orcid = [
        o if o is None or grepl(r"^https?://", o) else f"https://orcid.org/{o}"
        for o in chr_("orcid")
    ]
    aff = chr_("affiliation")
    # a paper read with bibr has an affiliation table naming its authors
    at = getattr(paper, "affiliation", None)
    if (
        isinstance(at, pd.DataFrame)
        and len(at)
        and "author_ids" in at.columns
        and "author_id" in au.columns
    ):
        if "institution" in at.columns:
            label = _col(at, "institution")
        elif "text" in at.columns:
            label = _col(at, "text")
        else:
            label = [None] * len(at)
        if "text" in at.columns:
            txt = _col(at, "text")
            label = [
                t if (lab is None or lab == "") else lab for lab, t in zip(label, txt, strict=True)
            ]
        author_ids = at["author_ids"].tolist()
        aid = _col(au, "author_id")
        for i in range(n):
            if aff[i] is not None:
                continue
            hit = [
                k
                for k, ids in enumerate(author_ids)
                if aid[i] is not None and any(aid[i] == _py(v) for v in _flatten(ids))
            ]
            if hit:
                aff[i] = "; ".join(_unique([label[k] for k in hit if label[k] is not None]))
    credit: list[list[str]]
    if "credit_roles" in au.columns and au["credit_roles"].dtype == object:
        credit = [
            [c for c in (_chr(v) for v in _flatten(x)) if c is not None]
            for x in au["credit_roles"].tolist()
        ]
    else:
        credit = [[] for _ in range(n)]
    corresponding = (
        [is_true(_py(v)) for v in au["corresponding"].tolist()]
        if "corresponding" in au.columns
        else [False] * n
    )
    ident = [f"#author-{k + 1}" if o is None else o for k, o in enumerate(orcid)]
    out = pd.DataFrame(
        {
            "name": pd.Series(name, dtype="string"),
            "orcid": pd.Series(orcid, dtype="string"),
            "email": pd.Series(chr_("email"), dtype="string"),
            "corresponding": pd.Series(corresponding, dtype=bool),
            "affiliation": pd.Series(aff, dtype="string"),
            "id": pd.Series(ident, dtype="string"),
            "credit": pd.Series(credit, dtype=object),
        }
    )
    return out[out["name"].str.len() > 0].reset_index(drop=True)


def _FAIR_repo_funding(paper: Any, ops: Any) -> dict[str, Any] | None:
    """The paper's funding (``.FAIR_repo_funding()``).

    The statement (from the paper as read by bibr, else the sentences funding_check
    found), and the funders and award numbers named, when the paper was read with
    bibr. ``None`` when nothing was found. Missing texts are left out (R would join them
    as "NA").
    """
    statement = _FAIR_repo_info(paper, "funding_statement")
    if statement is not None:
        statement = _chr(statement)
    ft = _mo_table(ops, "funding_check")
    if statement is None and isinstance(ft, pd.DataFrame) and len(ft) and "text" in ft.columns:
        texts = _unique([_chr(t) for t in _col(ft, "text") if _chr(t) is not None])
        if texts:
            statement = " ".join(texts)
    fu = getattr(paper, "funding", None) if paper is not None else None
    funders: list[str] = []
    awards: list[str] = []
    if isinstance(fu, pd.DataFrame) and "funder" in fu.columns:
        funders = _unique([c for c in (_chr(v) for v in fu["funder"].tolist()) if c is not None])
    if isinstance(fu, pd.DataFrame) and "award_ids" in fu.columns:
        awards = _unique(
            [c for c in (_chr(v) for v in _flatten(fu["award_ids"].tolist())) if c is not None]
        )
    if statement is None and not funders:
        return None
    return {"statement": statement, "funders": funders, "awards": awards}


def _FAIR_repo_formats(plan: pd.DataFrame, output_dir: str) -> pd.DataFrame:  # noqa: ARG001
    """The formats of the repository's files in the copy (``.FAIR_repo_formats()``).

    Not the files metacheck wrote. Columns: ``ext``, ``n`` (number of files), the
    ``software`` a format needs, and ``csv_copy`` (whether a CSV copy was written of its
    data files).
    """
    written = _col(plan, "written")
    original = _col(plan, "original_target")
    convert = [is_true(v) for v in _col(plan, "convert")]
    sheets = _col(plan, "sheet_files")
    ok = [w is not None for w in written]
    files: list[str] = [w for w in written if w is not None]
    files += [o for o, k, c in zip(original, ok, convert, strict=True) if k and c and o is not None]
    for s, k in zip(sheets, ok, strict=True):
        if k:
            files += [f for f in (_chr(v) for v in _as_list(s)) if f is not None]
    ext = [e for e in (_file_ext(f).lower() for f in files) if e]
    if not ext:
        return pd.DataFrame(
            {
                "ext": pd.Series([], dtype="string"),
                "n": pd.Series([], dtype="int64"),
                "software": pd.Series([], dtype="string"),
                "csv_copy": pd.Series([], dtype=bool),
            }
        )
    names = sorted(set(ext), key=r_sort_key)
    converted = {
        _file_ext(o).lower()
        for o, k, c in zip(original, ok, convert, strict=True)
        if c and k and o is not None
    }
    return pd.DataFrame(
        {
            "ext": pd.Series(names, dtype="string"),
            "n": pd.Series([ext.count(e) for e in names], dtype="int64"),
            "software": pd.Series(
                [_FAIR_repo_format_software.get(e) for e in names], dtype="string"
            ),
            "csv_copy": pd.Series([e in converted for e in names], dtype=bool),
        }
    )


def _FAIR_repo_software(ops: Any) -> dict[str, pd.DataFrame]:
    """The languages and packages the code uses (``.FAIR_repo_software()``).

    ``languages`` has ``name``, ``n`` (files) and ``version`` (from a renv.lock or
    sessionInfo for R); ``packages`` has ``package``, ``language`` and ``version`` (from
    a renv.lock, else missing). Packages that come with the language are left out (base
    R's stats or utils, Python's os or json): they cannot be installed, and listing them
    made ``pip install -r requirements.txt`` fail. A Python package is named as pip
    installs it (``import sklearn`` is scikit-learn).
    """
    from metacheck.codecheck.core import code_packages
    from metacheck.repro.core import _repro_base_packages
    from metacheck.repro.python import _PY_STDLIB, _repro_py_pip_name

    tb = _mo_table(ops, "code_check")
    has_lang = isinstance(tb, pd.DataFrame) and "language" in tb.columns
    lang = [v for v in _col(tb, "language") if v is not None] if has_lang else []
    names = sorted(set(lang), key=r_sort_key)
    vp = _mo_get(ops, "code_check", "version_pin")
    vp_get = vp.get if isinstance(vp, Mapping) else (lambda _k, _d=None: None)
    r_ver = _unique([c for c in (_chr(v) for v in _as_list(vp_get("r_versions"))) if c is not None])
    languages = pd.DataFrame(
        {
            "name": pd.Series(names, dtype="string"),
            "n": pd.Series([lang.count(nm) for nm in names], dtype="int64"),
            "version": pd.Series(
                [", ".join(r_ver) if (nm == "R" and r_ver) else None for nm in names],
                dtype="string",
            ),
        }
    )

    def pkgs_of(language: str) -> list[str]:
        if not has_lang:
            return []
        rows = tb[tb["language"].isin([language])]
        if not len(rows):
            return []
        try:
            return list(code_packages(rows))
        except Exception:
            return []

    base = set(_repro_base_packages())
    r_pkgs = [p for p in _unique(pkgs_of("R")) if p not in base]
    py = [p for p in pkgs_of("Python") if re.sub(r"\..*$", "", p) not in _PY_STDLIB]
    py_pkgs = _unique(list(_repro_py_pip_name(py)))
    renv = vp_get("renv_packages")
    r_version: list[str | None]
    if isinstance(renv, pd.DataFrame) and len(renv):
        first: dict[str, Any] = {}
        for p, v in zip(_col(renv, "package"), _col(renv, "version"), strict=True):
            first.setdefault(p, v)
        r_version = [_chr(first.get(p)) for p in r_pkgs]
    else:
        r_version = [None] * len(r_pkgs)
    packages = pd.DataFrame(
        {
            "package": pd.Series([*r_pkgs, *py_pkgs], dtype="string"),
            "language": pd.Series(["R"] * len(r_pkgs) + ["Python"] * len(py_pkgs), dtype="string"),
            "version": pd.Series([*r_version, *([None] * len(py_pkgs))], dtype="string"),
        }
    )
    return {"languages": languages, "packages": packages}


def _FAIR_repo_not_included(plan: pd.DataFrame, structure_df: pd.DataFrame | None) -> pd.DataFrame:
    """The folder's files that are not in the copy, because they could not be read or written.

    Their path and type. A file left out because an identical one is in the copy is not
    one of them (``.FAIR_repo_not_included()``). Row ``i`` of *structure_df* is plan row ``i``.
    """
    written = _col(plan, "written")
    target = _col(plan, "target_path")
    same = _col(plan, "same_as")
    rows = [
        i
        for i in range(len(plan))
        if written[i] is None and target[i] is not None and same[i] is None
    ]
    if not rows:
        return pd.DataFrame(
            {
                "path": pd.Series([], dtype="string"),
                "type": pd.Series([], dtype="string"),
                "reason": pd.Series([], dtype="string"),
            }
        )
    names = _col(plan, "file_name")
    types = _col(plan, "data_type")
    if isinstance(structure_df, pd.DataFrame) and "file_path" in structure_df.columns:
        fp = _col(structure_df, "file_path")
        path = [fp[i] if fp[i] is not None else names[i] for i in rows]
    else:
        path = [names[i] for i in rows]
    return pd.DataFrame(
        {
            "path": pd.Series(path, dtype="string"),
            "type": pd.Series([types[i] for i in rows], dtype="string"),
            "reason": pd.Series(["could not be read or written"] * len(rows), dtype="string"),
        }
    )


_README_NAME_RE = r"^readme(\.(md|txt|rmd|qmd|markdown))?$"


def _read_lines(path: str, n: int | None = None) -> list[str]:
    """R's ``readLines()``: the lines of a file (LF, CRLF or CR ends a line).

    A line that is not valid UTF-8 is read as Latin-1 (an older Mac or Windows file),
    as the R code does with ``iconv(lines[bad], "latin1", "UTF-8")``.
    """
    with open(path, "rb") as f:
        raw = f.read()
    lines = raw.splitlines()
    if n is not None:
        lines = lines[:n]
    out: list[str] = []
    for ln in lines:
        try:
            out.append(ln.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(ln.decode("latin-1"))
    return out


def _FAIR_repo_readme_title(written: Sequence[str] | str, output_dir: str) -> str | None:
    """The title in the folder's own README at the top (``.FAIR_repo_readme_title()``).

    Its first "# " heading, a line underlined with "===", or the ``title:`` line of an R
    Markdown or Quarto README. ``None`` when there is no README there, or it marks no
    title (a plain first line is not taken: the TU/e template starts "This readme.text
    file was generated on ...").
    """
    paths = [written] if isinstance(written, str) else list(written)
    top = [w for w in paths if _dirname(w) == "." and grepl(_README_NAME_RE, w, ignore_case=True)]
    for f in top:
        try:
            lines = _read_lines(os.path.join(output_dir, f), 40)
        except OSError:
            lines = []
        lines = [trimws(ln) for ln in lines]
        yaml = [ln for ln in lines if grepl(r"^title:", ln)]
        head = [ln for ln in lines if grepl(r"^#\s+\S", ln)]
        under = [i for i, ln in enumerate(lines) if i > 0 and grepl(r"^=+$", ln)]
        t: str | None
        if yaml:
            t = str(gsub("^title:\\s*|[\"']", "", yaml[0]))
        elif head:
            t = str(sub(r"^#\s+", "", head[0]))
        elif under and lines[under[0] - 1] != "":
            t = lines[under[0] - 1]
        else:
            t = None
        if t is not None and t != "":
            return t
    return None


def _FAIR_repo_collect(
    paper: Any,
    pid: str,
    ops: Any,
    plan: pd.DataFrame,
    structure_df: pd.DataFrame | None,
    output_dir: str,
) -> dict[str, Any]:
    """Everything the RO-Crate and the README are written from (``.FAIR_repo_collect()``).

    So the two always say the same. The keys are R's: ``pid``, ``title``,
    ``title_known``, ``description``, ``abstract``, ``keywords``, ``doi_url``,
    ``authors``, ``funding``, ``licence``, ``roots``, ``software``, ``formats``,
    ``not_included``, ``provenance`` and ``design``.
    """
    from metacheck.codecheck.core import code_lang

    abstract = _FAIR_repo_abstract_text(paper)
    written = [w for w in _col(plan, "written") if w is not None]
    # every language of the copied scripts, also one code_check did not check
    software = _FAIR_repo_software(ops)
    dtype = _col(plan, "data_type")
    code_paths = [
        w
        for w, d in zip(_col(plan, "written"), dtype, strict=True)
        if w is not None and d == "code"
    ]
    file_langs = (
        [v for v in code_lang([os.path.join(output_dir, p) for p in code_paths]) if v is not None]
        if code_paths
        else []
    )
    have = set(software["languages"]["name"].dropna().tolist())
    new_langs = [v for v in _unique(file_langs) if v not in have]
    if new_langs:
        software["languages"] = pd.concat(
            [
                software["languages"],
                pd.DataFrame(
                    {
                        "name": pd.Series(new_langs, dtype="string"),
                        "n": pd.Series([file_langs.count(v) for v in new_langs], dtype="int64"),
                        "version": pd.Series([None] * len(new_langs), dtype="string"),
                    }
                ),
            ],
            ignore_index=True,
        )
    licence = [w for w in written if grepl(r"^LICEN[CS]E", _basename(w), ignore_case=True)]
    title = _FAIR_repo_info(paper, "title")
    if title is None:
        title = _FAIR_repo_readme_title(written, output_dir)
    roots = _FAIR_repo_roots(plan)
    return {
        "pid": pid,
        "title": pid if title is None else title,
        "title_known": title is not None,
        "description": abstract
        if abstract
        else "A copy of these research files, reorganised by metacheck.",
        "abstract": abstract,
        "keywords": _FAIR_repo_keywords(_FAIR_repo_info(paper, "keywords")),
        "doi_url": _FAIR_repo_doi_url(paper),
        "authors": _FAIR_repo_authors(paper),
        "funding": _FAIR_repo_funding(paper, ops),
        "licence": licence[0] if licence else None,
        "roots": roots,
        "software": software,
        "formats": _FAIR_repo_formats(plan, output_dir),
        "not_included": _FAIR_repo_not_included(plan, structure_df),
        "provenance": _FAIR_repo_provenance_entities(ops, plan),
        "design": [
            _FAIR_repo_study_design_entity(ops, pid, root["grp"]) for root in _FAIR_repo_roots(plan)
        ],
    }


def _FAIR_repo_decode_missing(s: Any) -> list[tuple[str, str | None]] | None:
    """Missing-value codes with their reasons, from a ``missing_values`` JSON string.

    The string is either an array of codes or a code -> reason object
    (``.FAIR_repo_decode_missing()``). R returns a named vector; this is a list of
    ``(code, reason)`` pairs in order (``reason`` is ``None`` when none was given), or
    ``None`` when there are no codes.
    """
    s = _py(s)
    if not isinstance(s, str) or s == "":
        return None
    try:
        out = json.loads(s)
    except ValueError:
        return None
    if out is None or (isinstance(out, list | dict) and not out):
        return None
    pairs: list[tuple[str | None, str | None]] = []
    if isinstance(out, dict):
        # unlist() of a named list: a null value is dropped, a nested one gets the key
        for k, v in out.items():
            pairs += [(k, _chr(leaf)) for leaf in _flatten(v) if leaf is not None]
    else:
        pairs = [(None, _chr(v)) for v in _flatten(out)]
    if not pairs:
        return None
    if all(k is None or k == "" for k, _ in pairs):
        pairs = [(v, None) for _, v in pairs]
    return [(k, v) for k, v in pairs if k is not None and k != ""]


def _FAIR_repo_missing_scheme_entity(labels_df: Any) -> dict[str, Any] | None:
    """One set of every distinct missing-value code (with its reason) used in the paper's data.

    ``None`` when no variable declares one (``.FAIR_repo_missing_scheme_entity()``).
    """
    if not isinstance(labels_df, pd.DataFrame) or "missing_values" not in labels_df.columns:
        return None
    pooled: list[tuple[str, str | None]] = []
    for s in labels_df["missing_values"].tolist():
        pooled += _FAIR_repo_decode_missing(s) or []
    if not pooled:
        return None
    seen: set[str] = set()
    kept: list[tuple[str, str | None]] = []
    for code, reason in pooled:
        key = f"{code}\r{reason or ''}"
        if key not in seen:
            seen.add(key)
            kept.append((code, reason))
    terms = [
        _compact(
            {
                "@type": "DefinedTerm",
                "termCode": code,
                "name": reason,
            }
        )
        for code, reason in kept
    ]
    return {
        "@id": "#missingvalues",
        "@type": "DefinedTermSet",
        "name": "Missing-value scheme",
        "description": (
            "Codes used across this paper's data to denote a missing response, "
            "pooled from every variable's declared missing-value codes."
        ),
        "hasDefinedTerm": terms,
    }


def _FAIR_repo_resolve_ref(ref: str, script_group: str | None, plan: pd.DataFrame) -> str | None:
    """The written path of a file a script names, or ``None`` (``.FAIR_repo_resolve_ref()``).

    Matched by base name among the written files; several matches are narrowed to the
    script's own study, and stay ``None`` when that leaves more than one.
    """
    written = _col(plan, "written")
    names = _col(plan, "file_name")
    group = _col(plan, "group")
    want = _basename(ref).lower()
    cand = [
        i
        for i in range(len(plan))
        if written[i] is not None and names[i] is not None and _basename(names[i]).lower() == want
    ]
    if len(cand) > 1 and script_group is not None:
        cand = [i for i in cand if group[i] == script_group]
    paths = _unique([written[i] for i in cand])
    return paths[0] if len(paths) == 1 else None


def _FAIR_repo_provenance_entities(ops: Any, plan: pd.DataFrame) -> list[dict[str, Any]]:
    """One RO-Crate CreateAction per script reproducibility_check analysed.

    The script (instrument), the files it reads (object) and writes (result), and its
    place in the run order (schema.org position; scripts without an order between them
    share one). reproducibility_check's ``reads``/``writes`` hold the file names the
    code names, found by reading it, not by running it (``.FAIR_repo_provenance_entities()``).
    """
    repro = _mo_table(ops, "reproducibility_check")
    if (
        not isinstance(repro, pd.DataFrame)
        or not len(repro)
        or not {"file_name", "reads", "writes"} <= set(repro.columns)
    ):
        return []

    def slug(fn: str) -> str:
        return re.sub(r"(^-|-$)", "", re.sub(r"[^A-Za-z0-9]+", "-", _basename(fn)).lower())

    written = _col(plan, "written")
    groups = _col(plan, "group")
    entities: list[dict[str, Any]] = []
    used: list[str] = []
    fnames = _col(repro, "file_name")
    reads_col = repro["reads"].tolist()
    writes_col = repro["writes"].tolist()
    run_order = _col(repro, "run_order") if "run_order" in repro.columns else None
    for i, fname in enumerate(fnames):
        if fname is None:
            continue
        code_path = _FAIR_repo_resolve_ref(fname, None, plan)
        if code_path is None:
            continue
        grp = groups[written.index(code_path)]

        def resolve(refs: Any, grp: str | None = grp) -> list[str]:
            items = [r for r in (_chr(v) for v in _flatten(refs)) if r]
            p = [_FAIR_repo_resolve_ref(r, grp, plan) for r in items]
            return _unique([x for x in p if x is not None])

        reads = resolve(reads_col[i])
        writes = resolve(writes_col[i])
        if not reads and not writes:
            continue

        base = slug(fname)
        if not base:
            base = str(i + 1)
        ident = base
        n = 1
        while ident in used:
            n += 1
            ident = f"{base}-{n}"
        used.append(ident)
        pos = as_int(run_order[i]) if run_order is not None else None
        entities.append(
            _compact(
                {
                    "@id": f"#lifecycle-{ident}",
                    "@type": "CreateAction",
                    "name": f"Script run: {_basename(fname)}",
                    "eventType": "script run",
                    "position": pos,
                    "instrument": {"@id": code_path},
                    "object": _ref(reads) if reads else None,
                    "result": _ref(writes) if writes else None,
                }
            )
        )
    return entities


def _file_size(path: str) -> float | None:
    """``file.size()``: the size in bytes, ``None`` when the file is not there."""
    try:
        return float(os.path.getsize(path))
    except OSError:
        return None


def _size_text(size: float | None) -> str | None:
    """``as.character(size)`` of ``file.size()``, which is a double (100000 is "1e+05")."""
    return None if size is None else as_character(size)


def _FAIR_repo_file_entities(plan: pd.DataFrame, output_dir: str) -> list[dict[str, Any]]:
    """One RO-Crate File entity per written file (``.FAIR_repo_file_entities()``).

    Its type and size, and for a CSV written from another data file, the original kept
    next to it; for a script recovered from an output file, that file.
    """
    from metacheck.codecheck.core import code_lang

    written = _col(plan, "written")
    original = _col(plan, "original_target")
    convert = [is_true(v) for v in _col(plan, "convert")]
    dtype = _col(plan, "data_type")
    rec_src = _col(plan, "recovered_source")
    sheets = _col(plan, "sheet_files")
    rows = [i for i, w in enumerate(written) if w is not None]
    out: list[dict[str, Any]] = []
    for i in rows:
        p = written[i]
        orig = original[i]
        converted = convert[i] and orig is not None and orig != ""
        size = _file_size(os.path.join(output_dir, p))
        # the copy, so a notebook's or Quarto document's language is read from it
        lang = code_lang(os.path.join(output_dir, p)) if dtype[i] == "code" else None
        based_on: Any = None
        if converted:
            based_on = {"@id": orig}
        elif rec_src[i] is not None:
            based_on = {"@id": rec_src[i]}
        out.append(
            _compact(
                {
                    "@id": p,
                    "@type": ["File", "SoftwareSourceCode"] if lang is not None else "File",
                    "name": _basename(p),
                    "encodingFormat": _FAIR_repo_encoding_format(p),
                    "contentSize": _size_text(size),
                    "programmingLanguage": {"@id": _FAIR_repo_lang_id(lang)}
                    if lang is not None
                    else None,
                    "metacheck:software": _FAIR_repo_software_for(p),
                    "isBasedOn": based_on,
                }
            )
        )
    # the CSVs of an Excel file's other sheets
    for i in rows:
        for q in (_chr(v) for v in _as_list(sheets[i])):
            if q is None:
                continue
            out.append(
                _compact(
                    {
                        "@id": q,
                        "@type": "File",
                        "name": _basename(q),
                        "encodingFormat": _FAIR_repo_encoding_format(q),
                        "contentSize": _size_text(_file_size(os.path.join(output_dir, q))),
                        "isBasedOn": {"@id": original[i]} if original[i] is not None else None,
                    }
                )
            )
    # the originals kept next to their CSV files
    for i in rows:
        if not (convert[i] and original[i] is not None):
            continue
        p = original[i]
        full = os.path.join(output_dir, p)
        if not os.path.exists(full):
            continue
        out.append(
            _compact(
                {
                    "@id": p,
                    "@type": "File",
                    "name": _basename(p),
                    "encodingFormat": _FAIR_repo_encoding_format(p),
                    "contentSize": _size_text(_file_size(full)),
                    "metacheck:software": _FAIR_repo_software_for(p),
                }
            )
        )
    return out


def _FAIR_repo_software_for(path: str) -> str | None:
    """The software a file's format needs, or ``None`` (see ``_FAIR_repo_format_software``)."""
    return _FAIR_repo_format_software.get(_file_ext(path).lower())


def _FAIR_repo_lang_id(lang: str) -> str:
    return "#language-" + re.sub(r"[^A-Za-z0-9]+", "-", lang).lower()


def _FAIR_repo_rocrate_json(
    info: Mapping[str, Any],
    output_dir: str,
    plan: pd.DataFrame,
    labels_df: Any = None,
    extra_files: Sequence[str] = (),
) -> str:
    """Build and write ``ro-crate-metadata.json`` (RO-Crate 1.3) at the top of the copy.

    Port of ``.FAIR_repo_rocrate_json()``. Built from *info* (``_FAIR_repo_collect()``).
    Its ``@context`` adds the DDI-Lifecycle terms used for how a study was run and
    metacheck's own prefix. The root Dataset ("./") holds every file; with several
    studies, each study is a Dataset part holding its own files. *extra_files* are the
    files metacheck wrote (logs, requirements.txt, README, CHANGES), relative to the top.

    "unknown" is written only where the authors are asked for something: when and how the
    data were collected, how they were processed, each author's CRediT roles the paper
    does not state, and the licence. Returns the path written.
    """
    roots = list(info["roots"])
    multi = len(roots) > 1
    files = _FAIR_repo_file_entities(plan, output_dir)
    # a repository's own ro-crate-metadata.json is replaced by this one
    files = [f for f in files if f["@id"] != "ro-crate-metadata.json"]
    file_ids = [f["@id"] for f in files]

    def in_dir(ids: Sequence[str], d: str) -> list[str]:
        if d:
            return [x for x in ids if x.startswith(d + "/")]
        return [x for x in ids if not grepl(r"^study-", x)]

    # a file metacheck added to (an author's CHANGES.md) is listed once
    extra_ids = [p for p in _unique([p for p in extra_files if p]) if p not in set(file_ids)]
    extra = [
        _compact(
            {
                "@id": p,
                "@type": "File",
                "name": _basename(p),
                "encodingFormat": _FAIR_repo_encoding_format(p),
            }
        )
        for p in extra_ids
    ]

    reused_by_group = _FAIR_repo_reused_by_group(plan)

    def about_of(root: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            *_ref(["#study-design" + _FAIR_repo_id(root["grp"])]),
            *_ref(
                [
                    "#lifecycle" + _FAIR_repo_id(root["grp"]) + "-" + x
                    for x in _FAIR_repo_lifecycle_ids
                ]
            ),
        ]

    study_parts: list[dict[str, Any]] = []
    if multi:
        written = _col(plan, "written")
        group = _col(plan, "group")
        dtype = _col(plan, "data_type")
        for root in roots:
            dt = [
                d
                for w, g, d in zip(written, group, dtype, strict=True)
                if w is not None and g == root["grp"]
            ]
            reused = reused_by_group.get(root["grp"], [])
            study_parts.append(
                _compact(
                    {
                        "@id": root["dir"] + "/",
                        "@type": "Dataset",
                        "name": _FAIR_repo_label(root["grp"]),
                        "hasPart": [
                            *_ref(in_dir(file_ids, root["dir"])),
                            *_ref(in_dir(extra_ids, root["dir"])),
                        ],
                        "metacheck:hasSharedData": any(d == "data" for d in dt),
                        "metacheck:hasSharedCode": any(d == "code" for d in dt),
                        "metacheck:hasSharedMaterials": any(d == "materials" for d in dt),
                        "metacheck:reusesFile": _ref(reused) if reused else None,
                        "about": about_of(root),
                    }
                )
            )

    # Authors, with their email; a corresponding author is the contact.
    au = info["authors"]
    a_id = _col(au, "id")
    a_name = _col(au, "name")
    a_aff = _col(au, "affiliation")
    a_email = _col(au, "email")
    a_corr = [is_true(v) for v in _col(au, "corresponding")]
    author_entities = [
        _compact(
            {
                "@id": a_id[i],
                "@type": "Person",
                "name": a_name[i],
                "affiliation": a_aff[i],
                "email": a_email[i],
                "contactPoint": {"@id": f"mailto:{a_email[i]}"}
                if a_corr[i] and a_email[i] is not None
                else None,
            }
        )
        for i in range(len(au))
    ]
    contacts = [
        {
            "@id": f"mailto:{a_email[i]}",
            "@type": "ContactPoint",
            "contactType": "corresponding author",
            "email": a_email[i],
            "name": a_name[i],
        }
        for i in range(len(au))
        if a_corr[i] and a_email[i] is not None
    ]
    credit = _FAIR_repo_credit_entities(au)

    doi_url = info.get("doi_url")
    paper_entity: list[dict[str, Any]] = (
        [{"@id": doi_url, "@type": "ScholarlyArticle", "name": info["title"]}]
        if doi_url is not None
        else []
    )

    fu = info.get("funding")
    funder_entities: list[dict[str, Any]] = []
    grant: list[dict[str, Any]] = []
    if fu is not None:
        funders = _as_list(fu.get("funders"))
        awards = _as_list(fu.get("awards"))
        funder_entities = [
            {"@id": f"#funder-{k + 1}", "@type": "Organization", "name": f}
            for k, f in enumerate(funders)
        ]
        grant = [
            _compact(
                {
                    "@id": "#funding",
                    "@type": "Grant",
                    "name": "Funding",
                    "description": fu.get("statement"),
                    "identifier": awards if awards else None,
                    "funder": _ref([f"#funder-{k + 1}" for k in range(len(funders))])
                    if funder_entities
                    else None,
                }
            )
        ]

    sw = info["software"]
    langs = sw["languages"]
    l_name, l_ver = _col(langs, "name"), _col(langs, "version")
    lang_entities = [
        _compact(
            {
                "@id": _FAIR_repo_lang_id(l_name[i]),
                "@type": "ComputerLanguage",
                "name": l_name[i],
                "version": l_ver[i],
            }
        )
        for i in range(len(langs))
    ]
    pk = sw["packages"]
    p_name, p_lang, p_ver = _col(pk, "package"), _col(pk, "language"), _col(pk, "version")
    package_entities = [
        _compact(
            {
                "@id": f"#package-{p_lang[i].lower()}-{p_name[i]}",
                "@type": "SoftwareApplication",
                "name": p_name[i],
                "programmingLanguage": {"@id": _FAIR_repo_lang_id(p_lang[i])},
                "version": p_ver[i],
            }
        )
        for i in range(len(pk))
    ]

    ni = info["not_included"]
    n_path, n_reason = _col(ni, "path"), _col(ni, "reason")
    not_included = [
        {
            "@id": f"#not-included-{i + 1}",
            "@type": "CreativeWork",
            "name": n_path[i],
            "description": f"Not in this copy: {n_reason[i]}",
        }
        for i in range(len(ni))
    ]

    missing_entity = _FAIR_repo_missing_scheme_entity(labels_df)
    provenance = list(info["provenance"])

    licence = _py(info.get("licence"))
    root_entity: dict[str, Any] = _compact(
        {
            "@id": "./",
            "@type": "Dataset",
            "name": info["title"],
            "description": info["description"],
            "keywords": info.get("keywords"),
            "citation": {"@id": doi_url} if doi_url is not None else None,
            # "unknown" when no paper named them, for the authors to fill in
            "author": _ref(a_id) if len(au) else _FAIR_repo_unknown,
            "license": {"@id": licence} if licence is not None else _FAIR_repo_unknown,
            "funding": {"@id": "#funding"} if grant else None,
            "dateCreated": _today(),
            "hasPart": [
                *(_ref([r["dir"] + "/" for r in roots]) if multi else []),
                *_ref(in_dir(file_ids, "")),
                *_ref(in_dir(extra_ids, "")),
            ],
            "about": [
                *(about_of(roots[0]) if not multi else []),
                *(_ref(["#missingvalues"]) if missing_entity is not None else []),
                *_ref([e["@id"] for e in provenance]),
                *_ref([e["@id"] for e in credit]),
            ],
            "metacheck:softwareRequirements": [
                *_ref([e["@id"] for e in lang_entities]),
                *_ref([e["@id"] for e in package_entities]),
            ],
            "metacheck:notIncluded": _ref([e["@id"] for e in not_included])
            if not_included
            else None,
        }
    )
    if not root_entity["metacheck:softwareRequirements"]:
        del root_entity["metacheck:softwareRequirements"]

    lifecycle = [e for root in roots for e in _FAIR_repo_lifecycle_entities(root["grp"])]

    descriptor = {
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.3"},
        "about": {"@id": "./"},
    }

    crate = {
        "@context": [
            "https://w3id.org/ro/crate/1.3/context",
            {
                "metacheck": "https://scienceverse.org/schema/metacheck/terms/",
                "ddi": "https://ddialliance.org/Specification/DDI-Lifecycle/3.3/",
                "populationOfConcern": "ddi:PopulationOfConcern",
                "targetSampleSize": "ddi:OverallTargetSampleSize",
                "obtainedDate": "ddi:DataCollectionDate",
                "eventType": "ddi:EventType",
                "dataCollectionMethodology": "ddi:DataCollectionMethodology",
                "purpose": "ddi:Purpose",
            },
        ],
        "@graph": [
            descriptor,
            root_entity,
            *study_parts,
            *files,
            *extra,
            *paper_entity,
            *grant,
            *funder_entities,
            *list(info.get("design") or []),
            *([missing_entity] if missing_entity is not None else []),
            *lifecycle,
            *provenance,
            *credit,
            *author_entities,
            *contacts,
            *lang_entities,
            *package_entities,
            *not_included,
        ],
    }

    path = os.path.join(output_dir, "ro-crate-metadata.json")
    _FAIR_repo_write_json(crate, path)
    return path


_SURROGATES = re.compile("[\ud800-\udfff]")


def _utf8_text(s: str) -> str:
    """A text as valid UTF-8; see ``_FAIR_repo_utf8``."""
    if not _SURROGATES.search(s):
        return s
    try:
        # bytes that were not UTF-8 reached Python as lone surrogates (surrogateescape)
        return s.encode("utf-8", "surrogateescape").decode("latin-1")
    except UnicodeEncodeError:
        return s.encode("utf-8", "replace").decode("utf-8")


def _FAIR_repo_utf8(x: Any) -> Any:
    """Every text in *x* (at any depth) as valid UTF-8 (``.FAIR_repo_utf8()``).

    R repairs a string that is not valid UTF-8 by reading it as Latin-1 (a column
    ``"PPFD (\\xb5mol m-2 s-1)"`` from an older Western European encoding). A Python
    ``str`` is already text, so it stays as it is, except one that carries undecodable
    bytes as lone surrogates (what ``errors="surrogateescape"`` makes): that one is read
    as Latin-1 the same way, and any other lone surrogate becomes ``?``. ``bytes`` are
    decoded as UTF-8, else as Latin-1. The input is not changed.
    """
    if isinstance(x, str):
        return _utf8_text(x)
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8")
        except UnicodeDecodeError:
            return x.decode("latin-1")
    if isinstance(x, Mapping):
        return {k: _FAIR_repo_utf8(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_FAIR_repo_utf8(v) for v in x]
    if isinstance(x, tuple):
        return tuple(_FAIR_repo_utf8(v) for v in x)
    if isinstance(x, pd.DataFrame):
        df = x.copy()
        for c in df.columns:
            if df[c].dtype == object or isinstance(df[c].dtype, pd.StringDtype):
                df[c] = pd.Series(
                    [_FAIR_repo_utf8(v) for v in df[c].tolist()], index=df.index, dtype=df[c].dtype
                )
        return df
    return x


def _json_text(x: Any, level: int = 0) -> str:
    """A value as pretty JSON, the way ``jsonlite::toJSON(auto_unbox, pretty, digits = 4)`` shows it.

    Two spaces per level; an array of plain values on one line, any other array one element per
    line (as jsonlite shows a vector and a list); a number as jsonlite writes it; a
    missing value ``null``.
    """
    if isinstance(x, np.generic):
        x = x.item()
    if x is None or x is pd.NA:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return "null" if (x != x or x in (float("inf"), float("-inf"))) else _json_number(x)
    if isinstance(x, str):
        return json.dumps(x, ensure_ascii=False)
    pad = "  " * (level + 1)
    end = "  " * level
    if isinstance(x, Mapping):
        if not x:
            return "{}"
        body = ",\n".join(
            f"{pad}{json.dumps(str(k), ensure_ascii=False)}: {_json_text(v, level + 1)}"
            for k, v in x.items()
        )
        return "{\n" + body + "\n" + end + "}"
    if isinstance(x, pd.DataFrame):
        return _json_text(x.to_dict("records"), level)
    if isinstance(x, list | tuple | np.ndarray | pd.Series):
        items = list(x)
        if not items:
            return "[]"
        if all(v is None or isinstance(v, str | int | float | bool | np.generic) for v in items):
            # an R vector of values: jsonlite keeps it on one line
            return "[" + ", ".join(_json_text(v, level + 1) for v in items) + "]"
        body = ",\n".join(pad + _json_text(v, level + 1) for v in items)
        return "[\n" + body + "\n" + end + "]"
    return json.dumps(str(x), ensure_ascii=False)


def _FAIR_repo_write_json(obj: Any, path: str) -> str:
    """Write *obj* as pretty JSON, UTF-8 without a byte order mark (``.FAIR_repo_write_json()``)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = _json_text(_FAIR_repo_utf8(obj)) + "\n"
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", errors="replace"))
    return path


# ── Other files ──────────────────────────────────────────────────────────────


def _FAIR_repo_file_list(structure_df: pd.DataFrame, pid: str, path: str) -> str:
    """Write the list of the original folder's files (``.FAIR_repo_file_list()``).

    Each file's path in the folder, size and type, and which metacheck version (and
    language model, if any) made the copy. ``r_version`` has no meaning here, so the
    Python version is written as ``python_version`` instead.
    """
    from metacheck import __version__
    from metacheck.llm.core import llm_model, llm_use

    n = len(structure_df)
    loc = _col(structure_df, "file_location")
    file_path = _col(structure_df, "file_path")
    data_type = _col(structure_df, "data_type")
    files: list[dict[str, Any]] = []
    for i in range(n):
        size = _file_size(loc[i]) if loc[i] is not None and os.path.exists(loc[i]) else None
        files.append(
            _compact(
                {
                    "file_path": file_path[i],
                    "file_size": int(size) if size is not None else None,
                    "data_type": data_type[i],
                }
            )
        )
    doc = {
        "folder": pid,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provenance": {
            "software": {"name": "metacheck", "version": __version__},
            "python_version": sys.version.split()[0],
            "llm": {"used": True, "model": llm_model()} if llm_use() is True else {"used": False},
        },
        "n_files": n,
        "files": files,
    }
    return _FAIR_repo_write_json(doc, path)


def _FAIR_repo_write_logs(
    ops: Any,
    structure_df: pd.DataFrame | None,
    pid: str,
    output_dir: str,
    folder: str | None = None,
) -> dict[str, str]:
    """The ``logs/`` folder (``.FAIR_repo_write_logs()``).

    The list of the original folder's files, and every check's results as a JSON file
    (``capture_module_tables()``; R writes an R data file, readRDS(), see D172), without
    the places on this computer the files are in. Returns the written paths, each with a
    one-line description, in the order they were written.
    """
    from metacheck.repro.tables import capture_module_tables

    logs_dir = os.path.join(output_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    written: dict[str, str] = {}

    if isinstance(structure_df, pd.DataFrame) and len(structure_df):
        file_list = os.path.join(logs_dir, f"{pid}.files.json")
        _FAIR_repo_file_list(structure_df, pid, file_list)
        written[file_list] = "Every file of the original folder, with its path, size and type."

    saved: str | None
    try:
        chain = {k: _FAIR_repo_drop_local(v, folder) for k, v in dict(ops or {}).items()}
        saved = capture_module_tables(chain, logs_dir, paper_id=pid)
    except Exception:
        saved = None
    if saved and os.path.exists(saved):
        # D172: pytacheck saves the results as a JSON file; metacheck's is an .rds file
        written[saved] = "The results of every metacheck check that was run, as a JSON file."

    # what each file holds, in a log of its own in the folder, so the copy keeps one
    # README per folder
    if written:
        log = os.path.join(logs_dir, "log.md")
        lines = [
            "# Log",
            "",
            "The records of the metacheck checks behind this copy of the files:",
            "",
            *[f"- `{_basename(p)}`: {d}" for p, d in written.items()],
        ]
        _write_text_lines(lines, log)
        written[log] = "What each file in this folder holds."
    return written


def _write_text_lines(lines: Sequence[str], path: str) -> None:
    """R's ``writeLines()``: each line followed by a newline, as UTF-8."""
    with open(path, "wb") as f:
        f.write("".join(f"{ln}\n" for ln in lines).encode("utf-8", errors="surrogateescape"))


def _FAIR_repo_write_requirements(
    software: Mapping[str, Any], plan: pd.DataFrame, output_dir: str
) -> str | None:
    """``requirements.txt`` at the top (``.FAIR_repo_write_requirements()``).

    It lists the Python packages the code imports, for ``pip install -r
    requirements.txt`` (names only: the code does not say which versions were used). R
    packages are not listed here, since pip would try to install them; they are in the
    README and the RO-Crate. Not written when the folder has its own requirements.txt,
    environment.yml or pyproject.toml. Returns the path written, or ``None``.
    """
    pk = software["packages"]
    pkgs = [
        p
        for p, lang in zip(_col(pk, "package"), _col(pk, "language"), strict=True)
        if lang == "Python"
    ]
    if not pkgs:
        return None
    own = any(
        grepl(
            r"^(requirements.*\.txt|environment\.ya?ml|pyproject\.toml)$",
            _basename(w),
            ignore_case=True,
        )
        for w in _col(plan, "written")
        if w is not None
    )
    path = os.path.join(output_dir, "requirements.txt")
    if own or os.path.exists(path):
        return None
    _write_text_lines(
        [
            "# Written by metacheck from the Python packages the code imports.",
            "# These are package names only, not versions: the code shows which",
            "# packages are imported, not which versions were used.",
            *pkgs,
        ],
        path,
    )
    return path


def _FAIR_repo_change_lines(plan: pd.DataFrame, copied: Mapping[str, Any]) -> list[str]:
    """The changes metacheck made, as lines for CHANGES.md (``.FAIR_repo_change_lines()``).

    ``copied["not_tables"]`` holds 0-based plan positions; ``plan["recovered_row"]`` holds
    R's 1-based row numbers.
    """

    def na(v: Any) -> str:
        return "NA" if v is None else str(v)

    n = len(plan)
    written = _col(plan, "written")
    original = _col(plan, "original_target")
    convert = [is_true(v) for v in _col(plan, "convert")]
    sheets = _col(plan, "sheet_files")
    lines = [
        _today() + " - Files reorganised by "
        "[metacheck](https://www.scienceverse.org/metacheck/): every "
        "file was moved to its place in the Psych-DS layout."
    ]
    conv = [i for i in range(n) if convert[i] and written[i] is not None]
    if conv:
        lines += ["", "Data files written as CSV files (the original is kept next to each):"]
        for i in conv:
            csvs = [
                written[i],
                *[c for c in (_chr(v) for v in _as_list(sheets[i])) if c is not None],
            ]
            lines.append(
                f"- `{'`, `'.join(csvs)}` from `{na(original[i])}`"
                + (" (one file per sheet)" if len(csvs) > 1 else "")
            )
    bag = [
        i
        for i in range(n)
        if written[i] is not None and written[i].startswith("logs/original-package/")
    ]
    if bag:
        lines += [
            "",
            "The files of the original archive package (BagIt) are in "
            "`logs/original-package/`. They list the files as they were before "
            "this reorganisation, so their paths and checksums do not match this copy:",
            *[f"- `{_basename(written[i])}`" for i in bag],
        ]
    not_tables = list(copied.get("not_tables") or [])
    if not_tables:
        lines += [
            "",
            "Data files kept as they are, because they hold no table:",
            *[f"- `{na(written[i])}`" for i in not_tables],
        ]
    same_as = _col(plan, "same_as")
    same = [i for i in range(n) if same_as[i] is not None]
    if same:
        current = (
            _col(plan, "current_path")
            if "current_path" in plan.columns
            else _col(plan, "file_name")
        )
        lines += [
            "",
            "Files left out, because an identical file is in this copy:",
            *[f"- `{na(current[i])}` (identical to `{same_as[i]}`)" for i in same],
        ]
    renamed = copied.get("renamed")
    if isinstance(renamed, pd.DataFrame) and len(renamed):
        lines += [
            "",
            "Files given a numbered name, because another file had the same place:",
            *[
                f"- `{na(w)}` is `{na(f)}`"
                for w, f in zip(_col(renamed, "written"), _col(renamed, "file_name"), strict=True)
            ],
        ]
    sc = copied.get("scripts")
    if not isinstance(sc, pd.DataFrame):
        sc = pd.DataFrame()
    s_target = _col(sc, "target_path")
    s_paths = [as_int(v) or 0 for v in _col(sc, "paths_changed")]
    s_setwd = [as_int(v) or 0 for v in _col(sc, "setwd_removed")]
    s_setwd_paths = [v or "" for v in _col(sc, "setwd_paths")]
    s_libs = [v or "" for v in _col(sc, "libraries_added")]
    if len(sc) and any(v > 0 for v in s_paths):
        lines += [
            "",
            "Scripts whose file paths were changed to the new layout:",
            *[
                f"- `{s_target[i]}` ({s_paths[i]} path{plural(s_paths[i])})"
                for i in range(len(sc))
                if s_paths[i] > 0
            ],
        ]
    if len(sc) and any(v > 0 for v in s_setwd):
        lines += [
            "",
            "Scripts whose `setwd()` lines were replaced by a statement that does "
            "nothing, because they set a folder written out in the script or the "
            "script's own folder, and the file paths now start at the top folder "
            "(the original line is kept as a comment). Run the scripts from the top "
            "folder of this repository:",
            *[
                f"- `{s_target[i]}`" + (f" (was `{s_setwd_paths[i]}`)" if s_setwd_paths[i] else "")
                for i in range(len(sc))
                if s_setwd[i] > 0
            ],
        ]
    if len(sc) and any(s_libs):
        lines += [
            "",
            "Scripts given `library()` calls at the top, because running them showed "
            "they use a function of a package they do not load:",
            *[f"- `{s_target[i]}` ({s_libs[i]})" for i in range(len(sc)) if s_libs[i]],
        ]
    recovered_row = _col(plan, "recovered_row")
    rec = [i for i in range(n) if written[i] is not None and recovered_row[i] is not None]
    if rec:
        rec_src = _col(plan, "recovered_source")
        current_rec = _col(plan, "current_path") if "current_path" in plan.columns else None
        lines += [
            "",
            "Scripts recovered from output files, which hold the code that made "
            "them (the output file is kept too):",
        ]
        for i in rec:
            if rec_src[i] is not None:
                source = rec_src[i]
            elif current_rec is None:
                source = "an output file"
            else:
                # recovered_row is R's 1-based row number of the output file
                source = na(current_rec[int(recovered_row[i]) - 1])
            lines.append(f"- `{written[i]}` from `{source}`")
    links = list(copied.get("readme_links") or [])
    if links:
        lines += [
            "",
            "READMEs whose links were changed to the files' new places (the text is unchanged):",
            *[f"- `{x}`" for x in links],
        ]
    return lines


def _list_files(root: str) -> list[str]:
    """``list.files(root, recursive = TRUE)``: the files below *root*, hidden ones skipped."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = os.path.relpath(dirpath, root)
        prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        out += [prefix + f for f in sorted(filenames) if not f.startswith(".")]
    return out


def _FAIR_repo_write_readme_changes(
    plan: pd.DataFrame,
    copied: Mapping[str, Any],
    info: Mapping[str, Any],
    output_dir: str,
    columns_df: Any = None,
    labels_df: Any = None,
) -> list[str]:
    """Write CHANGES.md and the READMEs (``.FAIR_repo_write_readme_changes()``).

    Add the changes to the top CHANGES.md (after any the repository has); write a
    README.md in each folder that has none (the top, and each study folder), as a readable
    version of ro-crate-metadata.json; and a CHANGES.md in each study folder that has none,
    as Psych-DS expects. A README or CHANGES file of the repository itself is left as it
    is. Returns the paths written.
    """
    all_written = [w for w in _col(plan, "written") if w is not None]

    def has_file(d: str, pattern: str) -> bool:
        w = [x for x in all_written if (_dirname(x) == d if d else _dirname(x) == ".")]
        return any(grepl(pattern, _basename(x), ignore_case=True) for x in w)

    written: list[str] = []

    changes = os.path.join(output_dir, "CHANGES.md")
    old: list[str] = []
    if os.path.exists(changes):
        with open(changes, "rb") as f:
            old = [ln.decode("utf-8", "surrogateescape") for ln in f.read().splitlines()]
    _write_text_lines(
        [*old, *([""] if old else []), *_FAIR_repo_change_lines(plan, copied)], changes
    )
    written.append(changes)

    roots = list(info["roots"])
    multi = len(roots) > 1
    if multi:
        for root in roots:
            if not has_file(root["dir"], "^CHANGES"):
                path = os.path.join(output_dir, root["dir"], "CHANGES.md")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _write_text_lines(
                    [
                        f"{_today()} - Study folder made by metacheck. See CHANGES.md in the top folder."
                    ],
                    path,
                )
                written.append(path)

    # the files the copy will hold, including the ones written after this
    def will_have(dir_files: Sequence[str]) -> list[str]:
        return _unique([*dir_files, "README.md"])

    study_readmes = (
        [f"{r['dir']}/README.md" for r in roots if not has_file(r["dir"], "^README")]
        if multi
        else []
    )
    all_files = [*_list_files(output_dir), "ro-crate-metadata.json", *study_readmes]

    if not has_file("", "^README"):
        path = os.path.join(output_dir, "README.md")
        _FAIR_repo_write_lines(
            _FAIR_repo_readme_top(info, plan, will_have(all_files), columns_df, labels_df), path
        )
        written.append(path)
    if multi:
        for root in roots:
            if has_file(root["dir"], "^README"):
                continue
            in_dir = [f for f in all_files if f.startswith(root["dir"] + "/")]
            path = os.path.join(output_dir, root["dir"], "README.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _FAIR_repo_write_lines(
                _FAIR_repo_readme_study(
                    info,
                    root,
                    plan,
                    will_have([re.sub(r"^[^/]+/", "", f) for f in in_dir]),
                    columns_df,
                    labels_df,
                ),
                path,
            )
            written.append(path)
    return written


def _FAIR_repo_write_lines(lines: Sequence[Any], path: str) -> None:
    """Write *lines* as a UTF-8 text file, one per line (``.FAIR_repo_write_lines()``)."""
    fixed = _FAIR_repo_utf8(["NA" if ln is None else str(ln) for ln in lines])
    with open(path, "wb") as f:
        f.write("".join(f"{ln}\n" for ln in fixed).encode("utf-8", errors="replace"))


def _FAIR_repo_tree(paths: Sequence[str], max: int = 150) -> list[str]:
    """A folder tree of *paths* (relative, with "/"), for a code block (``.FAIR_repo_tree()``).

    At most *max* files are shown.
    """
    ordered = sorted(_unique(list(paths)), key=r_sort_key)
    more = len(ordered) - max
    if more > 0:
        ordered = ordered[:max]
    lines: list[str] = []
    prev: list[str] = []
    for p in ordered:
        parts = [x for x in strsplit([p], "/", fixed=True)[0] if x is not None]
        dirs = parts[:-1]
        k = 0
        while k < min(len(prev), len(dirs)) and prev[k] == dirs[k]:
            k += 1
        lines.extend("  " * j + dirs[j] + "/" for j in range(k, len(dirs)))
        lines.append("  " * len(dirs) + (parts[-1] if parts else ""))
        prev = dirs
    if more > 0:
        lines.append(f"... and {more} more files")
    return ["```", *lines, "```"]


def _FAIR_repo_variable_tables(
    plan: pd.DataFrame,
    columns_df: Any,
    labels_df: Any,
    rows: Sequence[int],
    strip: str = "",
    max: int = 30,
) -> list[str]:
    """Per written data CSV file, a table of its variables (``.FAIR_repo_variable_tables()``).

    The variables, their labels and missing-value codes, at most *max* rows. *rows* are
    0-based plan positions.
    """
    from metacheck.fair import repo as _repo

    if not isinstance(columns_df, pd.DataFrame) or not len(columns_df):
        return []
    out: list[str] = []
    names = _col(plan, "file_name")
    written = _col(plan, "written")
    group = _col(plan, "group")
    sheet_map = plan["sheet_map"].tolist() if "sheet_map" in plan.columns else [None] * len(plan)
    # one table per CSV: a spreadsheet's sheets each have their own
    entries: list[tuple[int, str | None, str | None]] = []
    for i in rows:
        smap = sheet_map[i] if isinstance(sheet_map[i], Mapping) else {}
        if not smap:
            entries.append((i, names[i], written[i]))
        else:
            for s, csv in smap.items():
                key = _repo._FAIR_repo_sheet_key(names[i], s, columns_df)
                entries.append((i, _py(key), csv))

    def esc(x: Any) -> str:
        v = _chr(x)
        return "" if v is None else v.replace("|", "\\|")

    src = _col(columns_df, "source_file")
    grp_col = _col(columns_df, "group") if "group" in columns_df.columns else None
    lab_ok = (
        isinstance(labels_df, pd.DataFrame)
        and len(labels_df)
        and {"source_file", "column_name"} <= set(labels_df.columns)
    )
    for i, key, entry_written in entries:
        keep = [s is not None and s == key for s in src]
        if grp_col is not None and group[i] is not None:
            keep = [k and g == group[i] for k, g in zip(keep, grp_col, strict=True)]
        cols = columns_df[keep]
        if not len(cols):
            continue
        lab = labels_df[labels_df["source_file"].isin([key])] if lab_ok else None
        first: dict[Any, int] = {}
        if lab is not None:
            for k, v in enumerate(_col(lab, "column_name")):
                first.setdefault(v, k)
        m = [first.get(c) if lab is not None else None for c in _col(cols, "column_name")]
        label: list[Any] = [None] * len(cols)
        if lab is not None and "label" in lab.columns:
            lab_label = _col(lab, "label")
            label = [None if k is None else lab_label[k] for k in m]
        mv = [""] * len(cols)
        if lab is not None and "missing_values" in lab.columns:
            lab_mv = _col(lab, "missing_values")
            mv = []
            for pos_k in m:
                v = _FAIR_repo_decode_missing(None if pos_k is None else lab_mv[pos_k])
                mv.append(", ".join(c for c, _ in v) if v else "")
        # a table of names only says nothing dataset_description.json does not
        if not any(esc(x) for x in label) and not any(mv):
            continue
        n = len(cols)
        show = range(min(n, max))
        cname = _col(cols, "column_name")
        shown = str(sub(strip, "", entry_written)) if strip else entry_written
        out += [
            "",
            f"### `{shown}`",
            "",
            "| Variable | Label | Missing-value codes |",
            "|---|---|---|",
            *[f"| {esc(cname[k])} | {esc(label[k])} | {esc(mv[k])} |" for k in show],
        ]
        if n > max:
            out += ["", f"... and {n - max} more variables; see `dataset_description.json`."]
    return out


def _FAIR_repo_still_unknown(info: Mapping[str, Any]) -> list[str]:
    """What the authors are still asked for, as README lines (``.FAIR_repo_still_unknown()``)."""
    au = info["authors"]
    names = _col(au, "name")
    emails = _col(au, "email")
    corr = [is_true(v) for v in _col(au, "corresponding")]
    credit = au["credit"].tolist() if "credit" in au.columns else [None] * len(au)
    no_roles = [names[i] for i in range(len(au)) if not _as_list(credit[i])]
    out: list[str] = []
    if info.get("title_known") is not True:
        out.append("- The title")
    if not len(au):
        out.append("- The authors")
    if info.get("doi_url") is None:
        out.append("- The paper that uses these files")
    if _py(info.get("licence")) is None:
        out.append("- The licence under which the data may be reused")
    if not any(c and e is not None for c, e in zip(corr, emails, strict=True)):
        out.append("- A contact person and email address")
    if no_roles:
        out.append("- The CRediT roles of " + ", ".join(no_roles))
    out.append("- When the data were collected")
    if any(d.get("dataCollectionMethodology") == [_FAIR_repo_unknown] for d in info["design"]):
        out.append("- How the data were collected")
    out.append("- How the data were processed into the shared data")
    return out


def _FAIR_repo_readme_top(
    info: Mapping[str, Any],
    plan: pd.DataFrame,
    files: Sequence[str],
    columns_df: Any,
    labels_df: Any,
) -> list[str]:
    """The README of the top folder: everything in ro-crate-metadata.json, for people."""
    au = info["authors"]
    a_name, a_orcid, a_aff = _col(au, "name"), _col(au, "orcid"), _col(au, "affiliation")
    a_email = _col(au, "email")
    a_corr = [is_true(v) for v in _col(au, "corresponding")]
    a_credit = au["credit"].tolist() if "credit" in au.columns else [None] * len(au)
    au_lines: list[str] = []
    for i in range(len(au)):
        roles = [r for r in (_chr(v) for v in _as_list(a_credit[i])) if r is not None]
        au_lines.append(
            f"- {a_name[i]}"
            + (f" ({a_orcid[i]})" if a_orcid[i] is not None else "")
            + (f", {a_aff[i]}" if a_aff[i] is not None else "")
            + (". CRediT: " + ", ".join(roles) if roles else "")
        )
    if not len(au):
        au_lines = ["Not found."]
    contacts = [i for i in range(len(au)) if a_corr[i] and a_email[i] is not None]
    contact = "; ".join(f"{a_name[i]} <{a_email[i]}>" for i in contacts) if contacts else "unknown"

    multi = len(info["roots"]) > 1
    fmt = info["formats"]
    if len(fmt):
        f_ext, f_n = _col(fmt, "ext"), _col(fmt, "n")
        f_sw, f_csv = _col(fmt, "software"), _col(fmt, "csv_copy")
        fmt_lines = [
            "| Format | Files | Software needed | CSV copy included |",
            "|---|---|---|---|",
            *[
                f"| .{f_ext[i]} | {int(f_n[i])} | {f_sw[i] or ''} | {'yes' if f_csv[i] else ''} |"
                for i in range(len(fmt))
            ],
        ]
    else:
        fmt_lines = ["No files."]

    sw = info["software"]
    langs, pk = sw["languages"], sw["packages"]
    sw_lines: list[str] = []
    l_name, l_ver, l_n = _col(langs, "name"), _col(langs, "version"), _col(langs, "n")
    for i in range(len(langs)):
        ver = f" {l_ver[i]}" if l_ver[i] is not None else ""
        sw_lines.append(f"- {l_name[i]}{ver} ({int(l_n[i])} file{plural(int(l_n[i]))})")
    p_name, p_lang, p_ver = _col(pk, "package"), _col(pk, "language"), _col(pk, "version")
    for lang in _unique(p_lang):
        items = [
            p_name[i] + (f" ({p_ver[i]})" if p_ver[i] is not None else "")
            for i in range(len(pk))
            if p_lang[i] == lang
        ]
        sw_lines.append(f"- {lang} packages: " + ", ".join(items))
    if any(lang == "Python" for lang in p_lang):
        sw_lines.append("- Install the Python packages with `pip install -r requirements.txt`.")

    prov = list(info["provenance"])

    def ids(x: Any) -> str:
        return ", ".join(f"`{e['@id']}`" for e in (x or []))

    order = sorted(
        range(len(prov)),
        key=lambda k: (
            float(prov[k]["position"]) if prov[k].get("position") is not None else float("inf")
        ),
    )
    script_lines: list[str] = []
    for k in order:
        e = prov[k]
        script_lines.append(
            f"1. `{e['instrument']['@id']}`"
            + (f" reads {ids(e.get('object'))}" if e.get("object") else "")
            + (";" if e.get("object") and e.get("result") else "")
            + (f" writes {ids(e.get('result'))}" if e.get("result") else "")
        )

    written = _col(plan, "written")
    dtype = _col(plan, "data_type")
    data_rows = [
        i
        for i in range(len(plan))
        if written[i] is not None
        and grepl(r"^(study-[^/]+/)?data/.+\.csv$", written[i])
        and dtype[i] == "data"
    ]
    var_lines = _FAIR_repo_variable_tables(plan, columns_df, labels_df, data_rows)
    ni = info["not_included"]
    fu = info.get("funding")
    licence = _py(info.get("licence"))

    lines: list[str] = [f"# {info['title']}", ""]
    if info["abstract"]:
        lines += [info["abstract"], ""]
    lines.append(
        "This folder is a copy of the research files, reorganised by "
        "[metacheck](https://www.scienceverse.org/metacheck/) on "
        + _today()
        + ". `ro-crate-metadata.json` holds "
        "the same information in machine-readable form, and `CHANGES.md` "
        "lists what metacheck changed."
    )
    lines += ["", "## Authors", "", *au_lines, "", f"Contact: {contact}"]
    if info.get("doi_url") is not None:
        lines += ["", "## Paper", "", info["doi_url"]]
    lines += ["", "## Licence", "", "unknown" if licence is None else f"See `{licence}`."]
    if fu is not None:
        lines += ["", "## Funding", "", fu.get("statement") or ""]
        funders, awards = _as_list(fu.get("funders")), _as_list(fu.get("awards"))
        if funders:
            lines.append("Funders: " + "; ".join(funders))
        if awards:
            lines.append("Grants: " + "; ".join(awards))
    lines += [
        "",
        "## Files",
        "",
        "The files follow the [Psych-DS](https://psych-ds.github.io/) layout: "
        "data in `data/`, named `study-<name>_data.csv`, analysis code in "
        "`analysis/`, materials in `materials/`, documentation in "
        "`documentation/`"
        + (", with one `study-<group>/` folder per study" if multi else "")
        + ". Each data folder's `dataset_description.json` describes its variables.",
        "",
        *_FAIR_repo_tree(files),
        "",
        "## File formats",
        "",
        *fmt_lines,
    ]
    if sw_lines:
        lines += ["", "## Software", "", *sw_lines]
    if script_lines:
        lines += ["", "## Scripts", "", "In the order they can be run:", "", *script_lines]
    if var_lines:
        lines += ["", "## Variables", *var_lines]
    elif data_rows:
        lines += [
            "",
            "## Variables",
            "",
            "No codebook describes the variables. Their names and values are "
            "in `dataset_description.json`.",
        ]
    if len(ni):
        lines += [
            "",
            "## Not included",
            "",
            *[
                f"- `{p}` ({t}): {r}"
                for p, t, r in zip(
                    _col(ni, "path"), _col(ni, "type"), _col(ni, "reason"), strict=True
                )
            ],
        ]
    lines += [
        "",
        "## Still to be filled in",
        "",
        "metacheck could not find the following:",
        "",
        *_FAIR_repo_still_unknown(info),
    ]
    return lines


def _FAIR_repo_readme_study(
    info: Mapping[str, Any],
    root: Mapping[str, Any],
    plan: pd.DataFrame,
    files: Sequence[str],
    columns_df: Any,
    labels_df: Any,
) -> list[str]:
    """The README of a study folder: its files and variables (``.FAIR_repo_readme_study()``)."""
    written = _col(plan, "written")
    dtype = _col(plan, "data_type")
    rows = [
        i
        for i in range(len(plan))
        if written[i] is not None
        and written[i].startswith(root["dir"] + "/data/")
        and grepl(r"\.csv$", written[i])
        and dtype[i] == "data"
    ]
    lines = [
        f"# {info['title']} — {_FAIR_repo_label(root['grp'])}",
        "",
        "One study of the research files, reorganised by "
        "[metacheck](https://www.scienceverse.org/metacheck/). The "
        "authors, licence, software and scripts are described in "
        "`ro-crate-metadata.json` in the top folder.",
        "",
        "## Files",
        "",
        *_FAIR_repo_tree(files),
    ]
    if rows:
        lines += [
            "",
            "## Variables",
            *_FAIR_repo_variable_tables(
                plan, columns_df, labels_df, rows, strip="^" + root["dir"] + "/"
            ),
        ]
    return lines
