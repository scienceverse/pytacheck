"""Where each repository file would go in a Psych-DS layout (port of ``R/psychds-file-plan.R``).

:func:`psychds_file_plan` is the file/folder plan that ``psychds_check``
reports and ``reproducibility_check`` uses to rewrite paths. It is a pure
function of ``data_check``'s ``structure`` table: it runs no module, checks no
Psych-DS compliance and writes nothing to disk.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r import gsub, regextract, slashed, sub
from metacheck._values import as_str

__all__ = ["psychds_file_plan"]

# R: type_to_subdir -- file type -> Psych-DS subdirectory (data and readme are
# handled separately, by data_type and doc_role). "unknown" gets its own visible
# folder rather than being folded into documentation/.
_TYPE_TO_SUBDIR = {
    "code": "analysis",
    "materials": "materials",
    "output": "outputs",
    "documentation": "documentation",
    "unknown": "unknown",
}

# Psych-DS's own validator rule for a data file name (schema_model "Datafile"):
# keyword-value pairs, then "_data.csv" / "_data.tsv"
_PSYCHDS_DATAFILE = re.compile(r"^[a-z]+-[a-zA-Z0-9]+(_[a-z]+-[a-zA-Z0-9]+)*_data\.(csv|tsv)$")
# documentation types whose readme/licence/CHANGES files go to the root (a bare
# LICENSE or CHANGES is "unknown" in data_check); dataset_description.json and
# ro-crate-metadata.json go there by name whatever their type ("code" for .json)
_ROOT_DOC_TYPES = ("documentation", "unknown")


# -- small R helpers ---------------------------------------------------------------


def _paste(x: str | None) -> str:
    """How ``paste0()`` prints one value (``NA`` becomes ``"NA"``)."""
    return "NA" if x is None else x


def _col(df: pd.DataFrame, name: str) -> list[str | None] | None:
    """``df$name`` as a list of strings (``None`` when the column is absent)."""
    if name not in df.columns:
        return None
    return [as_str(v) for v in df[name].tolist()]


def _is_true(x: Any) -> bool:
    """R ``isTRUE()``: a single, non-missing ``TRUE``."""
    if isinstance(x, pd.Series | list | tuple | np.ndarray):
        vals = list(np.asarray(x, dtype=object).reshape(-1))
        return len(vals) == 1 and _is_true(vals[0])
    if isinstance(x, np.bool_):
        return bool(x)
    return x is True


def _backslash_to_slash(x: str | None) -> str | None:
    """R ``gsub("\\\\", "/", x)``."""
    return None if x is None else x.replace("\\", "/")


def _basename(path: str | None) -> str | None:
    """R ``basename()`` on Unix: tilde expansion, trailing slashes dropped."""
    if path is None:
        return None
    if path.startswith("~"):
        path = os.path.expanduser(path)
    return slashed(path).rstrip("/").rpartition("/")[2]


def _dirname(path: str) -> str:
    """R ``dirname()`` on Unix."""
    if path.startswith("~"):
        path = os.path.expanduser(path)
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


def _file_ext(x: list[str | None]) -> list[str | None]:
    """``tools::file_ext()`` of a vector: the trailing alphanumeric extension, or ``""``."""
    return [
        None if v is None else ("" if m is None else m[1:])
        for v, m in zip(x, regextract(r"\.([[:alnum:]]+)$", x), strict=True)
    ]


def _file_path_sans_ext(x: list[str | None]) -> list[str | None]:
    """``tools::file_path_sans_ext()`` of a vector."""
    out: list[str | None] = sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", x)
    return out


def _keyword_slug(x: list[str | None]) -> list[str | None]:
    """R ``keyword_slug()``: drop what a Psych-DS keyword *value* may not contain."""
    out: list[str | None] = gsub("[^a-zA-Z0-9]+", "", x)
    return out


def _tolower(x: str | None) -> str | None:
    """R ``tolower()`` (``towlower()`` per character)."""
    if x is None:
        return None
    if x.isascii():
        return x.lower()
    # towlower() maps U+0130 to "i"; other one-to-many lowerings stay unchanged
    return "".join("i" if c == "İ" else low if len(low := c.lower()) == 1 else c for c in x)


# -- the plan ----------------------------------------------------------------------


@dataclass
class FilePlan:
    """The plan as per-file lists (one entry per row of ``structure``)."""

    names: list[str | None]  # each file's basename
    file_name: list[str | None]
    data_type: list[str | None]
    groups: list[str | None]
    doc_role: list[str | None]
    study_groups: list[str]
    current_path: list[str | None]
    target_path: list[str | None]
    is_data: list[bool]
    is_excluded: list[bool]
    misplaced: list[bool]
    needs_convert: list[bool]
    original_target: list[str | None]

    @property
    def multi_study(self) -> bool:
        return len(self.study_groups) > 1


def file_plan(structure_df: pd.DataFrame) -> FilePlan:
    """Each file's current and target path (the lists behind :func:`psychds_file_plan`)."""
    from metacheck.datacheck.files import data_format

    n_files = len(structure_df)
    raw_name = _col(structure_df, "file_name") or [None] * n_files
    raw_path = _col(structure_df, "file_path") or [None] * n_files
    data_type = _col(structure_df, "data_type") or [None] * n_files
    groups = _col(structure_df, "group") or [None] * n_files
    doc_role = _col(structure_df, "doc_role") or [None] * n_files
    study_groups = list(dict.fromkeys(g for g in groups if g is not None))
    multi_study = len(study_groups) > 1

    # each file's name and current location: the name falls back to the path's
    # basename; a file with neither cannot be placed (R fails on a missing name
    # or data type)
    current_path = [
        _backslash_to_slash(p) if p is not None else _backslash_to_slash(f)
        for p, f in zip(raw_path, raw_name, strict=True)
    ]
    names = [
        _basename(_backslash_to_slash(f)) if f is not None else _basename(c)
        for f, c in zip(raw_name, current_path, strict=True)
    ]
    file_name = [f if f is not None else nm for f, nm in zip(raw_name, names, strict=True)]
    dtypes = [dt if dt is not None else "unknown" for dt in data_type]
    stems = _keyword_slug(_file_path_sans_ext(names))
    name_ext = _file_ext(names)

    # each file's Psych-DS target path. Only a repository with two or more
    # study groups uses the study-<group>/ layout; files without a group (the
    # collection-level README and ro-crate metadata) stay at the root.
    def target_of(i: int) -> str | None:
        name = names[i]
        if name is None:
            return None
        dt, role, grp = dtypes[i], doc_role[i], groups[i]
        prefix = f"study-{grp}/" if multi_study and grp is not None else ""
        low = _tolower(name) or ""
        ext = name_ext[i] or ""
        if low == "dataset_description.json":
            return f"{prefix}dataset_description.json"
        if low == "ro-crate-metadata.json":
            return name  # collection-level metadata: the root, under its own name
        if dt == "data":
            if _PSYCHDS_DATAFILE.match(name):
                return f"{prefix}data/{name}"  # already a valid Psych-DS data file name
            stem = stems[i] or f"file{i + 1}"
            return f"{prefix}data/study-{stem}_data.csv"
        if dt in _ROOT_DOC_TYPES:
            if role == "readme" or role == "license":
                base = "README" if role == "readme" else "LICENSE"
                return prefix + (f"{base}.{ext}" if ext else base)
            if re.match(r"^changes(\.|$)", low):
                return prefix + name  # a CHANGES log sits at the root, under its own name
        return f"{prefix}{_TYPE_TO_SUBDIR.get(dt, 'documentation')}/{name}"

    target_path: list[str | None] = [target_of(i) for i in range(n_files)]
    is_data = [dt == "data" for dt in dtypes]

    # tabular data not already in CSV is converted (original kept alongside);
    # raw (non-tabular) data keeps its own name and extension; a file already
    # named as a Psych-DS data file (.csv or .tsv) stays as it is
    src_ext = [_tolower(e) or "" for e in name_ext]
    src_format = data_format(src_ext)
    compliant = [n is not None and bool(_PSYCHDS_DATAFILE.match(n)) for n in names]
    needs_convert = [
        d and e != "csv" and fmt == "tabular" and not ok
        for d, e, fmt, ok in zip(is_data, src_ext, src_format, compliant, strict=True)
    ]
    is_raw_data = [
        d and e != "" and e != "csv" and not nc and not ok
        for d, e, nc, ok in zip(is_data, src_ext, needs_convert, compliant, strict=True)
    ]

    def same_dir_real_name(i: int) -> str:
        # the same directory as the _data.csv target, but the file's own basename
        return f"{_dirname(_paste(target_path[i]))}/{_paste(names[i])}"

    original_target = [same_dir_real_name(i) if needs_convert[i] else None for i in range(n_files)]
    for i in range(n_files):
        if is_raw_data[i]:
            target_path[i] = same_dir_real_name(i)
    # misplaced against the final target (R compares before a raw file's target
    # is set, so raw data already in place was "moved" onto itself)
    is_excluded = [t is None for t in target_path]
    misplaced = [
        not ex and c != t for ex, c, t in zip(is_excluded, current_path, target_path, strict=True)
    ]
    return FilePlan(
        names=names,
        file_name=file_name,
        data_type=data_type,
        groups=groups,
        doc_role=doc_role,
        study_groups=study_groups,
        current_path=current_path,
        target_path=target_path,
        is_data=is_data,
        is_excluded=is_excluded,
        misplaced=misplaced,
        needs_convert=needs_convert,
        original_target=original_target,
    )


def plan_table(structure_df: pd.DataFrame, plan: FilePlan) -> pd.DataFrame:
    """The plan of *structure_df* as a table (``psychds_file_plan()$table``)."""
    status = [
        "excluded" if ex else ("move" if m else "present")
        for ex, m in zip(plan.is_excluded, plan.misplaced, strict=True)
    ]
    if "referenced_by" in structure_df.columns:
        # copies, so the plan never shares a list with the input structure
        referenced_by = [
            list(v) if isinstance(v, list) else v for v in structure_df["referenced_by"].tolist()
        ]
    else:
        referenced_by = [None] * len(structure_df)
    return pd.DataFrame(
        {
            "file_name": pd.array(plan.file_name, dtype="string"),
            "data_type": pd.array(plan.data_type, dtype="string"),
            "group": pd.array(plan.groups, dtype="string"),
            "current_path": pd.array(plan.current_path, dtype="string"),
            "target_path": pd.array(plan.target_path, dtype="string"),
            "status": pd.array(status, dtype="string"),
            "convert": pd.array(plan.needs_convert, dtype="boolean"),
            "original_target": pd.array(plan.original_target, dtype="string"),
            "referenced_by": pd.Series(referenced_by, dtype=object),
        }
    )


def psychds_file_plan(structure_df: Any, group_no_evidence: Any = False) -> dict[str, Any]:
    """Where each repository file would go in a Psych-DS layout (port of ``psychds_file_plan()``).

    Maps every file of *structure_df* (``data_check``'s ``structure`` table:
    ``file_name``, ``file_path``, ``data_type``, and ``group``, ``doc_role``
    and ``referenced_by`` when present) to its place in a Psych-DS dataset:
    data under ``data/`` as ``study-<name>_data.csv`` (a file already named
    like ``study-1_data.csv`` keeps its name), code under ``analysis/``,
    materials under ``materials/``, outputs under ``outputs/``, documentation
    under ``documentation/``, unclassified files under ``unknown/``, and the
    readme, licence, ``CHANGES``, ``dataset_description.json`` and
    ``ro-crate-metadata.json`` at the root. With two or more study groups,
    each study is its own ``study-<group>/`` dataset.

    A tabular data file that is not a CSV (``.xlsx``, ``.sav``, ...) is
    planned to be converted, its original kept beside it (``convert``,
    ``original_target``); raw data (``.npy``, ``.h5``, ...) keeps its own
    name. Nothing is converted, moved or written.

    Returns ``{"table": ..., "group_no_evidence": ...}``: one row per file
    (``file_name``, ``data_type``, ``group``, ``current_path``,
    ``target_path``, ``status`` -- ``"present"``, ``"move"`` or
    ``"excluded"`` for a file with no name --, ``convert``,
    ``original_target``, ``referenced_by``), an empty table when
    *structure_df* is ``None`` or empty; *group_no_evidence* (whether no
    evidence named a study, from ``data_check``) is passed through as a
    ``bool``.
    """
    no_evidence = _is_true(group_no_evidence)
    if not isinstance(structure_df, pd.DataFrame) or len(structure_df) == 0:
        return {"table": pd.DataFrame(), "group_no_evidence": no_evidence}
    return {
        "table": plan_table(structure_df, file_plan(structure_df)),
        "group_no_evidence": no_evidence,
    }
