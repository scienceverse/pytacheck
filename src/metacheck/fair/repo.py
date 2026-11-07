"""``FAIR_repo()``: write a reorganised, FAIR copy of a research folder.

Port of ``R/fair-repo.R``. The folder is read, never changed: it is first copied to a
temporary folder, the checks of :func:`~metacheck.report.report.report_repository` (repo, code,
data, codebook and reproducibility checks, with no code run unless asked) run on that copy, and
every file is then written to its place in the Psych-DS layout of ``output_dir``, with
``dataset_description.json``, ``ro-crate-metadata.json``, a README, ``CHANGES.md``,
``requirements.txt`` and ``logs/`` beside it (see :mod:`metacheck.fair.metadata`).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import grepl, gsub, sub
from metacheck._r.base import as_character, plural
from metacheck._values import is_missing
from metacheck._values import is_true as _is_true
from metacheck.fair.metadata import (
    _FAIR_repo_collect,
    _FAIR_repo_dataset_description,
    _FAIR_repo_label,
    _FAIR_repo_reused_by_group,
    _FAIR_repo_rocrate_json,
    _FAIR_repo_roots,
    _FAIR_repo_variable_measured,
    _FAIR_repo_write_json,
    _FAIR_repo_write_logs,
    _FAIR_repo_write_readme_changes,
    _FAIR_repo_write_requirements,
)

__all__ = ["FAIR_repo"]

# ── small helpers ────────────────────────────────────────────────────────────


def _vals(df: Any, col: str, n: int | None = None) -> list[Any]:
    """A column as a list, ``None`` for a missing value (``None`` for each row when absent)."""
    if not isinstance(df, pd.DataFrame):
        return [None] * (n or 0)
    if col not in df.columns:
        return [None] * len(df)
    return [None if is_missing(v) else v for v in df[col].tolist()]


def _has(df: Any, col: str) -> bool:
    return isinstance(df, pd.DataFrame) and col in df.columns


def _slash(p: str) -> str:
    return p.replace("\\", "/")


def _basename(p: str) -> str:
    return _slash(p).rsplit("/", 1)[-1]


def _dirname(p: str) -> str:
    q = _slash(p)
    if "/" not in q:
        return "."
    d = q.rsplit("/", 1)[0]
    return d or "/"


def _file_ext(p: str) -> str:
    """``tools::file_ext()``."""
    m = re.search(r"\.([A-Za-z0-9]+)$", p)
    return m.group(1) if m else ""


def _sans_ext(p: str) -> str:
    """``tools::file_path_sans_ext()``."""
    return re.sub(r"([^.]+)\.[A-Za-z0-9]+$", r"\1", p)


def _md5(path: str) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(p: str) -> str:
    """``normalizePath(p, winslash = "/", mustWork = FALSE)``."""
    return _slash(os.path.realpath(p))


def _write_lines(path: str, lines: Sequence[str]) -> None:
    """``writeLines(lines, con, useBytes = TRUE)`` into a binary UTF-8 connection."""
    with open(path, "wb") as fh:
        fh.write("".join(f"{ln}\n" for ln in lines).encode("utf-8", errors="replace"))


def _message(*parts: Any) -> None:
    from metacheck.utils import message

    message(*parts)


def _set_col(df: pd.DataFrame, name: str, values: Sequence[Any], dtype: str | None = None) -> None:
    """Assign a whole column (also a column of lists or dicts) without pandas guessing."""
    series = pd.Series(list(values), index=df.index, dtype=object if dtype is None else dtype)
    df[name] = series


# ── FAIR_repo() ──────────────────────────────────────────────────────────────


def FAIR_repo(
    path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    paper: Any = None,
    overwrite: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Convert a research folder into a clean, FAIR copy (port of ``FAIR_repo()``).

    Writes a reorganised copy of a folder of research files (data, code, materials,
    documentation) on your own computer. Nothing is downloaded and no link is followed: only
    the folder you name is read, and it is not changed. The folder is first copied to a
    temporary folder and the checks run on that copy, so nothing they write (such as code
    recovered from an output file) can reach your folder; the copy is removed at the end.
    The checks run the way :func:`~metacheck.report.report.report_repository` runs them:
    ``repo_check`` takes an inventory of the folder, and ``code_check``, ``data_check``,
    ``codebook_check`` and ``reproducibility_check`` (with ``execute=False``, so no code is
    run, unless you set *execute*) build on it. Whether a language model is used is decided by
    :func:`~metacheck.llm.core.llm_use`.

    Every file is placed where the Psych-DS layout puts it (data under ``data/``, code under
    ``analysis/``, materials under ``materials/``, documentation under ``documentation/``, one
    ``study-<group>/`` folder per study when there are several; see
    :func:`~metacheck.datacheck.psychds.psychds_file_plan`), keeping its own subfolders. A file
    a script writes and no script reads, and an R data file holding a fitted model, is a result
    and goes to ``outputs/``. A data file in a format that needs particular software
    (``.xlsx``, ``.sav``, ``.dta``, ...) is also written as a real CSV file, with the original
    kept next to it; an Excel file with several sheets gets one CSV per sheet, named after the
    sheet; an R data file only when it holds a table and no CSV of the same name is next to it.
    A table in a text file (``.txt``, ``.tsv``) is kept as it is. A data CSV file is copied
    without a byte order mark. The files of an archive package (BagIt) go to
    ``logs/original-package/``. A file from an archive in the folder that is identical to a
    file next to it, and a README identical to another, are left out.

    R and Python scripts (``.R``, ``.Rmd``, ``.qmd``, ``.py``, ``.ipynb``) are changed so they
    still find their files in the new layout, run from the top folder: each file path that
    :func:`~metacheck.repro.core.repro_rewrite_paths` can match to one file of the folder is
    replaced by that file's new path, as is the path a script writes a file to that is in the
    folder or goes to ``outputs/``; and each ``setwd()``/``os.chdir()`` line that sets a folder
    written out in the script, no folder, or the script's own folder is replaced by a statement
    that does nothing, with the original line kept in a comment. Every change is reported, and
    listed in ``CHANGES.md``. Scripts in other languages are copied unchanged.

    Code that ``code_check`` recovers from an output file (a rendered R Markdown or Quarto page,
    an SPSS ``.spv``, a Stata ``.smcl`` or an Mplus ``.out`` file) is kept as a script in
    ``analysis/``, unless the folder has the script it was made from. In a README that the folder
    has, a link to a file of the folder (such as an image) is changed to that file's new place.

    Metadata is written next to the files: ``dataset_description.json`` in each study folder
    with data (Psych-DS), ``ro-crate-metadata.json`` at the top (RO-Crate 1.3), ``README.md`` (a
    readable version of the RO-Crate) in the top folder and in each study folder unless the
    folder has its own, ``requirements.txt`` (the Python packages the code imports) unless the
    folder has its own, ``logs/`` (the list of files and the results of the checks, without the
    places on this computer the files are in) and ``CHANGES.md``.

    Parameters
    ----------
    path
        The folder to convert.
    output_dir
        The folder to write into; created if needed. There is no default.
    paper
        Optionally the paper that uses these files (see :func:`~metacheck.papers.io.read`), for
        its title, authors, DOI, CRediT roles and funding. Its links are not followed.
    overwrite
        Whether to replace an existing *output_dir*. When ``False`` (the default) and
        *output_dir* exists, nothing is written and the result has ``existed = True``.
    execute
        Whether to run the R and Python scripts, as ``reproducibility_check(execute=True)``
        does: in a copy, with each script in its own process. Default ``False``: no code is run.

    Returns
    -------
    dict
        ``output_dir``, ``n_files_copied``, ``n_studies``, ``plan`` (the file plan, with a
        ``written`` column: the path each file was written to, ``None`` when it was not),
        ``scripts`` (one row per changed script: ``target_path``, ``paths_changed``,
        ``setwd_removed``, ``setwd_paths``, ``libraries_added``), ``skipped`` (files that could
        not be read), ``copy_failed`` and ``renamed`` (a file whose target another file already
        took), ``identical``, ``empty_plan`` (``True`` when the folder had no files), and the
        paths of the metadata written: ``descriptions``, ``rocrate``, ``requirements``, ``logs``
        and ``readme_changes``. When an existing *output_dir* was kept, the result has only
        ``output_dir``, the counts and ``existed = True``.
    """
    from metacheck.datacheck.psychds import psychds_file_plan
    from metacheck.papers.io import test_paper
    from metacheck.papers.model import is_paper

    if not isinstance(path, str | os.PathLike) or isinstance(path, bytes):
        raise ValueError("`path` must be a single path to a folder")
    path = os.fspath(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"No folder found at {path}")
    if paper is not None and not is_paper(paper):
        raise TypeError("`paper` must be a single paper object (see read())")
    full_path = _norm(path)
    output_dir = os.fspath(output_dir)
    out_full = _norm(output_dir)
    if out_full == full_path or out_full.startswith(full_path.rstrip("/") + "/"):
        raise ValueError("`output_dir` must be outside the folder being converted")
    if os.path.isdir(output_dir) and not overwrite:
        _message(
            "The folder already exists, so nothing was written: ",
            output_dir,
            ". Set overwrite = TRUE to replace it.",
        )
        return {"output_dir": output_dir, "n_files_copied": 0, "n_studies": 0, "existed": True}

    # The checks run on a copy of the folder in the temporary folder, so nothing they write
    # (code recovered from an output file, an unpacked archive) can reach the folder being
    # converted: it is only read, to make the copy. The copy has the folder's name, so the file
    # paths and the paper id are the same. Only the files listed before the checks run are
    # converted.
    work = _FAIR_repo_work_copy(full_path)
    try:
        listed = [_norm(os.path.join(root, f)) for root, _d, files in os.walk(work) for f in files]

        ops = _FAIR_repo_run_checks(work, paper, execute)
        # Without a paper there is no title and there are no authors; the title is then taken
        # from the folder's own README, else its name (see _FAIR_repo_collect()).
        if paper is None:
            paper = test_paper()
            paper.info = paper.info.assign(title=pd.Series([None], dtype="string"))
            paper.author = paper.author.iloc[0:0]
        pid = os.path.basename(full_path)

        dc = ops["data_check"]
        structure_df = _FAIR_repo_only_listed(dc.get("structure"), work, listed)
        # psychds_file_plan() gives one row per file of data_check's file table, in the same
        # order, so row i of the plan is file i of structure_df.
        plan = psychds_file_plan(structure_df, dc.get("group_no_evidence"))["table"]

        if os.path.isdir(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
        os.makedirs(output_dir, exist_ok=True)

        # With no files, the logs and the metadata are still written.
        empty_plan = len(plan) == 0
        if empty_plan:
            _message(
                "No files were found to convert, so only the logs and the metadata were written."
            )
            plan = pd.DataFrame(
                {
                    "file_name": pd.Series([], dtype="string"),
                    "data_type": pd.Series([], dtype="string"),
                    "group": pd.Series([], dtype="string"),
                    "target_path": pd.Series([], dtype="string"),
                    "convert": pd.Series([], dtype="boolean"),
                    "original_target": pd.Series([], dtype="string"),
                }
            )
        plan = plan.reset_index(drop=True)
        if not isinstance(structure_df, pd.DataFrame):
            structure_df = pd.DataFrame()

        plan = _FAIR_repo_adjust_plan(plan, structure_df, ops)
        plan = _FAIR_repo_identical(plan, structure_df)
        plan, structure_df = _FAIR_repo_add_recovered(plan, structure_df, ops)
        copied = _FAIR_repo_copy(plan, structure_df, output_dir, _FAIR_repo_added_libraries(ops))
        written = copied["written"]
        _set_col(plan, "written", written, "string")
        _set_col(plan, "same_as", copied["same_as"], "string")
        # what was actually written: a file that was no table is not converted, and an original
        # may have a numbered name
        _set_col(plan, "convert", copied["converted"], "boolean")
        _set_col(plan, "original_target", copied["original_written"], "string")
        _set_col(plan, "sheet_files", copied["sheet_files"])
        _set_col(plan, "sheet_map", copied["sheet_map"])
        # where the output file a recovered script came from was written
        rec_rows = _vals(plan, "recovered_row")
        _set_col(
            plan,
            "recovered_source",
            [None if r is None else written[int(r) - 1] for r in rec_rows],
            "string",
        )
        copied["readme_links"] = _FAIR_repo_fix_readme_links(plan, structure_df, output_dir)
        meta = _FAIR_repo_write_metadata(
            paper, pid, ops, plan, structure_df, copied, output_dir, folder=work
        )
        if not empty_plan:
            _FAIR_repo_messages(plan, copied)
    finally:
        shutil.rmtree(os.path.dirname(work), ignore_errors=True)

    written_paths = [w for w in plan["written"].tolist() if not is_missing(w)]
    study_dirs = list(
        dict.fromkeys(
            sub(r"^(study-[^/]+)/.*$", r"\1", w) for w in written_paths if w.startswith("study-")
        )
    )
    names = plan["file_name"].tolist()
    return {
        "output_dir": output_dir,
        "n_files_copied": len(written_paths),
        "n_studies": 0 if not written_paths else max(1, len(study_dirs)),
        "plan": plan,
        "scripts": copied["scripts"],
        "skipped": [names[i] for i in copied["skipped"]],
        "copy_failed": [names[i] for i in copied["failed"]],
        "renamed": copied["renamed"],
        "identical": _FAIR_repo_identical_table(plan),
        "empty_plan": empty_plan,
        **meta,
    }


def _FAIR_repo_run_checks(path: str, paper: Any = None, execute: bool = False) -> Any:
    """Run the checks FAIR_repo() uses on a local folder, as ``report_repository()`` does.

    ``repo_check`` reads the folder, and the others build on its result. ``local_only`` is given
    to every check that has it, so none of them looks for a repository online, also when the
    paper links one. ``reproducibility_check`` reads the code without running it unless
    *execute*. ``funding_check`` runs only on a paper. A failed check stops the conversion with
    its own error.
    """
    from metacheck.papers.io import test_paper
    from metacheck.report.report import report_module_run

    modules = [
        "repo_check",
        "code_check",
        "data_check",
        "codebook_check",
        "reproducibility_check",
        *(["funding_check"] if paper is not None else []),
    ]
    args: dict[str, Mapping[str, Any]] = {
        "repo_check": {"local_path": path, "local_only": True},
        "code_check": {"local_only": True},
        "data_check": {"local_only": True},
        "codebook_check": {"local_only": True},
        "reproducibility_check": {"local_only": True, "execute": execute is True},
    }
    ops = report_module_run(paper if paper is not None else test_paper(), modules, args=args)
    for m in modules:
        mo = ops.get(m)
        if mo is not None and mo.traffic_light == "fail":
            report = mo.report
            if report is None or (not isinstance(report, str) and len(report) == 0):
                report = "unknown error"
            text = "\n  ".join([report] if isinstance(report, str) else [str(r) for r in report])
            raise RuntimeError(
                f"The '{m}' check failed, so the folder cannot be converted:\n  {text}"
            )
    return ops


def _FAIR_repo_work_copy(path: str) -> str:
    """Copy the folder at *path* (every file, hidden ones too, with their dates) to the temp folder.

    The checks run on the copy, so the folder itself is only read. Stops when not every file was
    copied: converting part of a folder would leave files out without saying so. Returns the
    copy's path.
    """
    parent = tempfile.mkdtemp(prefix="metacheck-fair-")
    work = os.path.join(parent, os.path.basename(path))

    def count(d: str) -> int:
        return sum(len(files) for _r, _d, files in os.walk(d))

    ok = True
    try:
        shutil.copytree(path, work, copy_function=shutil.copy2)
    except (OSError, shutil.Error):
        ok = False
    n_in = count(path)
    n_out = count(work) if os.path.isdir(work) else 0
    if not ok or n_out < n_in:
        shutil.rmtree(parent, ignore_errors=True)
        raise OSError(
            f"The folder could not be copied to the temporary folder ({n_out} of {n_in} files "
            f"were copied), so it was not converted: {path}"
        )
    return _norm(work)


def _FAIR_repo_only_listed(structure_df: Any, work: str, listed: Sequence[str]) -> Any:
    """data_check's file table without the files that appeared in the copy during the checks.

    Files in the copy that were not there before the checks ran (``listed``) were made by a
    check, not by the authors. Files outside the copy, such as the files of an archive unpacked
    in the temporary folder, are kept.
    """
    if not isinstance(structure_df, pd.DataFrame) or len(structure_df) == 0:
        return structure_df
    if "file_location" not in structure_df.columns:
        return structure_df
    prefix = (work + "/").lower()
    known = {p.lower() for p in listed}
    keep = []
    for loc in structure_df["file_location"].tolist():
        if is_missing(loc):
            keep.append(True)
            continue
        norm = _norm(str(loc)).lower()
        inside = norm.startswith(prefix)
        keep.append(not (inside and norm not in known))
    return structure_df[keep].reset_index(drop=True)


# ── the file plan ────────────────────────────────────────────────────────────


def _FAIR_repo_add_recovered(
    plan: pd.DataFrame, structure_df: pd.DataFrame, ops: Any
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep the code ``code_check`` recovered from output files as scripts of the plan.

    Code recovered from a rendered R Markdown page, an SPSS ``.spv``, a Stata ``.smcl`` or an
    Mplus ``.out`` file is kept as a script: a row is added to the plan and to the file table
    for each, placed in ``analysis/`` with the subfolders of its output file
    (``documentation/sub/x.html`` gives ``analysis/sub/x.R``), so it is copied, and its paths
    changed, like the authors' own scripts. ``recovered_row`` is the (1-based) plan row of the
    output file. Not when the folder has a script of that name (the ``.Rmd`` the page was made
    from): the recovered code would be a second copy of it.
    """
    n = len(plan)
    _set_col(plan, "recovered_row", [None] * n, "Int64")
    cc = _op_table(ops, "code_check")
    if (
        not isinstance(cc, pd.DataFrame)
        or len(cc) == 0
        or "recovered_from" not in cc.columns
        or n == 0
    ):
        return plan, structure_df
    loc = _vals(cc, "file_location")
    rec_from = _vals(cc, "recovered_from")
    keep = [
        i
        for i in range(len(cc))
        if rec_from[i] is not None and loc[i] is not None and os.path.exists(loc[i])
    ]
    seen_loc: set[str] = set()
    rows: list[int] = []
    for i in keep:
        if loc[i] not in seen_loc:
            seen_loc.add(loc[i])
            rows.append(i)
    if not rows:
        return plan, structure_df

    paths = (
        _vals(structure_df, "file_path")
        if _has(structure_df, "file_path")
        else _vals(structure_df, "file_name")
    )

    def stem(p: Any) -> str:
        return _sans_ext(_basename(str(p))).lower()

    types = _vals(plan, "data_type")
    own = [stem(p) for p, t in zip(paths, types, strict=False) if t == "code" and p is not None]
    cc_names = _vals(cc, "file_name")
    cc_paths = _vals(cc, "file_path")
    for k in rows:
        name = cc_names[k]
        if name is not None and stem(name) in own:
            continue
        src = paths.index(rec_from[k]) if rec_from[k] in paths else None
        targets = _vals(plan, "target_path")
        src_target = None if src is None else targets[src]
        if src_target is None or not src_target:
            folder = "analysis"
        else:
            folder = re.sub(r"^((study-[^/]+/)?)[^/]+(/|$)", r"\1analysis\3", _dirname(src_target))
        srow = _row_copy(structure_df, src)
        srow["file_name"] = name
        if "file_path" in srow.columns:
            srow["file_path"] = cc_paths[k]
        # its own name, not its output file's, in what identifies a file
        if "source_file" in srow.columns:
            srow["source_file"] = cc_paths[k]
        srow["file_location"] = loc[k]
        srow["data_type"] = "code"
        prow = _row_copy(plan, src)
        prow["file_name"] = name
        prow["data_type"] = "code"
        prow["target_path"] = f"{folder}/{name}"
        prow["convert"] = False
        prow["original_target"] = None
        if "same_as" in prow.columns:
            prow["same_as"] = None
        if "script_writes" in prow.columns:
            prow["script_writes"] = False
        prow["recovered_row"] = pd.array([None if src is None else src + 1], dtype="Int64")
        structure_df = pd.concat([structure_df, srow], ignore_index=True)
        plan = pd.concat([plan, prow], ignore_index=True)
        own.append(stem(name))
    return plan.reset_index(drop=True), structure_df.reset_index(drop=True)


def _row_copy(df: pd.DataFrame, i: int | None) -> pd.DataFrame:
    """One row of *df* as a one-row frame (all missing when *i* is ``None``), dtypes kept."""
    if i is None:
        row = df.iloc[0:1].copy()
        for c in row.columns:
            row[c] = pd.Series([None], index=row.index, dtype=row[c].dtype)
        return row
    return df.iloc[i : i + 1].copy().reset_index(drop=True)


def _op_table(ops: Any, name: str) -> Any:
    """``ops[[name]]$table`` of a module output (also of a plain dict, as tests pass)."""
    mo = ops.get(name) if hasattr(ops, "get") else None
    if mo is None:
        return None
    if isinstance(mo, Mapping):
        return mo.get("table")
    return getattr(mo, "table", None)


def _op_get(ops: Any, name: str, key: str) -> Any:
    """``ops[[name]][[key]]`` for an element of a module output (``None`` when absent)."""
    mo = ops.get(name) if hasattr(ops, "get") else None
    if mo is None:
        return None
    if isinstance(mo, Mapping):
        return mo.get(key)
    getter = getattr(mo, "get", None)
    return getter(key) if getter is not None else getattr(mo, key, None)


def _FAIR_repo_write_metadata(
    paper: Any,
    pid: str,
    ops: Any,
    plan: pd.DataFrame,
    structure_df: pd.DataFrame,
    copied: dict[str, Any],
    output_dir: str,
    folder: str | None = None,
) -> dict[str, Any]:
    """Write the metadata files (see :mod:`metacheck.fair.metadata`) and return their paths.

    ``descriptions`` (``dataset_description.json`` per study), ``rocrate``, ``requirements``,
    ``logs``, and ``readme_changes`` (the README and CHANGES files metacheck wrote).
    """
    columns_df = _op_table(ops, "data_check")
    labels_df = _op_table(ops, "codebook_check")
    roots = _FAIR_repo_roots(plan)
    reused = _FAIR_repo_reused_by_group(plan)
    written = ["" if w is None else w for w in _vals(plan, "written")]
    # what the README, the RO-Crate and the dataset descriptions are written from
    info = _FAIR_repo_collect(paper, pid, ops, plan, structure_df, output_dir)
    # each data file's path in the copy, by data_check's name for it (an identical file left
    # out: the copy that was kept)
    sf = (
        _vals(structure_df, "source_file")
        if _has(structure_df, "source_file")
        else _vals(structure_df, "file_name")
    )
    same_as = _vals(plan, "same_as")
    plan_written = _vals(plan, "written")
    new_path: dict[str, str] = {}
    seen: set[Any] = set()
    for i, s in enumerate(sf):
        dup = s in seen
        seen.add(s)
        w = plan_written[i] if i < len(plan_written) else None
        p = w if w is not None else (same_as[i] if i < len(same_as) else None)
        if p is not None and s is not None and not dup:
            new_path[s] = p
    # the columns of a sheet are in the CSV of that sheet
    sheet_maps = _vals(plan, "sheet_map") if _has(plan, "sheet_map") else [{}] * len(plan)
    for i, smap in enumerate(sheet_maps):
        if not smap or i >= len(sf) or sf[i] is None:
            continue
        for s, csv_path in smap.items():
            key = _FAIR_repo_sheet_key(sf[i], s, columns_df)
            if key is not None:
                new_path[key] = csv_path

    # dataset_description.json for each study with a data/ folder (a Psych-DS dataset has data;
    # a folder of code only is not one); a repository's own dataset_description.json is kept.
    descriptions: list[str] = []
    sheet_files = _vals(plan, "sheet_files") if _has(plan, "sheet_files") else []
    for root in roots:
        prefix = f"{root['dir']}/" if root["dir"] else ""
        # with every sheet's CSV: the first sheet can be a README in documentation/
        all_written = written + [p for lst in sheet_files if lst for p in lst]
        if not any(w.startswith(prefix + "data/") for w in all_written):
            continue
        dest = os.path.join(output_dir, root["dir"], "dataset_description.json")
        if os.path.exists(dest):
            continue
        grp = root["grp"]
        if grp is None or not isinstance(columns_df, pd.DataFrame):
            cols = columns_df
        else:
            cols = columns_df[columns_df["group"].isin([grp])]
        variables = _FAIR_repo_variable_measured(cols, labels_df, new_path)
        # every column of every CSV in data/ is described, also those data_check did not read
        # (a sheet of notes it left out): Psych-DS requires it
        csvs = [
            w for w in all_written if w.startswith(prefix + "data/") and re.search(r"\.csv$", w)
        ]
        variables = variables + _FAIR_repo_missing_columns(
            [os.path.join(output_dir, c) for c in csvs], csvs, [v["name"] for v in variables]
        )
        desc = _FAIR_repo_dataset_description(
            paper,
            info["title"],
            _FAIR_repo_label(grp),
            variables,
            [] if grp is None else reused.get(grp, []),
        )
        _FAIR_repo_write_json(desc, dest)
        descriptions.append(dest)

    # The paper's full text is not copied into the folder for now (R keeps the call commented
    # out too).
    logs = _FAIR_repo_write_logs(ops, structure_df, pid, output_dir, folder)
    requirements = _FAIR_repo_write_requirements(info["software"], plan, output_dir)
    readme_changes = _FAIR_repo_write_readme_changes(
        plan, copied, info, output_dir, columns_df, labels_df
    )

    top = os.path.realpath(output_dir)

    def rel(p: str) -> str:
        return _slash(os.path.relpath(os.path.realpath(p), top))

    rocrate = _FAIR_repo_rocrate_json(
        info,
        output_dir,
        plan,
        labels_df,
        extra_files=[
            rel(p)
            for p in [
                *descriptions,
                *list(logs),
                *([requirements] if requirements else []),
                *readme_changes,
            ]
        ],
    )
    return {
        "descriptions": descriptions,
        "rocrate": rocrate,
        "requirements": [requirements] if requirements else [],
        "logs": list(logs),
        "readme_changes": readme_changes,
    }


def _FAIR_repo_missing_columns(
    paths: Sequence[str], rel: Sequence[str], known: Sequence[str]
) -> list[dict[str, Any]]:
    """A PropertyValue for each CSV column whose name is not in *known*, from the header row."""
    import csv

    out: list[dict[str, Any]] = []
    have = list(known)
    for path, rel_path in zip(paths, rel, strict=False):
        try:
            with open(path, "rb") as fh:
                raw = fh.read(1 << 16)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            text = text.lstrip("﻿")
            header = next(csv.reader(text.splitlines()), [])
        except (OSError, csv.Error, StopIteration):
            header = []
        for cl in header:
            if cl not in have:
                have.append(cl)
                out.append({"@type": "PropertyValue", "name": cl, "metacheck:sourceFile": rel_path})
    return out


# ── where results, R data files, text tables and an archive package's own files go ──

_BAGIT = re.compile(r"^(bagit\.txt|bag-info\.txt|(tag)?manifest-[a-z0-9]+\.txt)$")


def _FAIR_repo_adjust_plan(
    plan: pd.DataFrame, structure_df: pd.DataFrame, ops: Any
) -> pd.DataFrame:
    """Decide where results, R data files, text tables and an archive package's files go.

    Decided before anything is copied, so the scripts' paths are changed to their final places.
    A file a script writes and no script reads is a result: it goes to ``outputs/``, under its
    own name (a file one script writes and another reads, such as cleaned data, stays data).
    An R data file (``.rds``, ``.RData``, ``.rda``) holding a fitted model is a result too; one
    holding no table, or with a CSV of the same name next to it, is kept as it is instead of
    written again as CSV. A table in a text file (``.txt``, ``.tsv``, ``.dat``, ``.tab``) is
    already open and readable, so it is kept as it is, without a CSV copy. The files of a BagIt
    package list the files as they were: after the reorganisation their paths and checksums no
    longer match, so they go to ``logs/original-package/`` instead of ``documentation/``.
    """
    n = len(plan)
    if not n:
        return plan
    loc = _vals(structure_df, "file_location") if len(structure_df) else [None] * n
    loc = (loc + [None] * n)[:n]
    file_names = _vals(plan, "file_name")
    name = [_basename(str(f)) for f in file_names]
    target = _vals(plan, "target_path")
    convert = [_is_true(v) for v in _vals(plan, "convert")]
    original = _vals(plan, "original_target")
    dtype = _vals(plan, "data_type")
    current = _vals(plan, "current_path") if _has(plan, "current_path") else None

    def to_outputs(i: int) -> None:
        folder = _dirname(target[i])
        # "study-ex1/data/scen_a" becomes "study-ex1/outputs/scen_a"
        folder = re.sub(r"^((study-[^/]+/)?)[^/]+", r"\1outputs", folder)
        target[i] = f"{folder}/{name[i]}"
        convert[i] = False
        original[i] = None

    def keep_as_is(i: int) -> None:
        if original[i] is not None:
            target[i] = original[i]
        convert[i] = False
        original[i] = None

    repro = _op_table(ops, "reproducibility_check")
    script_writes = [False] * n
    if isinstance(repro, pd.DataFrame) and {"reads", "writes"} <= set(repro.columns):
        reads = {_basename(str(x)).lower() for x in _flatten(repro["reads"].tolist())}
        writes = {_basename(str(x)).lower() for x in _flatten(repro["writes"].tolist())}
        result = writes - reads
        # a file a script writes: its new path goes into that script too (see
        # _FAIR_repo_point_to_new_paths())
        for i in range(n):
            script_writes[i] = name[i].lower() in writes and dtype[i] != "code"
        for i in range(n):
            if name[i].lower() in result and target[i] is not None and dtype[i] != "code":
                to_outputs(i)

    ext = [_file_ext(x).lower() for x in name]
    for i in range(n):
        if ext[i] in ("txt", "tsv", "dat", "tab") and convert[i]:
            keep_as_is(i)
    for i in range(n):
        if _BAGIT.match(name[i].lower()) and target[i] is not None:
            target[i] = f"logs/original-package/{name[i]}"
            convert[i] = False
            original[i] = None
    r_data = [
        i
        for i in range(n)
        if ext[i] in ("rds", "rdata", "rda")
        and target[i] is not None
        and not sub(r"^study-[^/]+/", "", target[i]).startswith("outputs/")
    ]

    def stem_of(i: int) -> str:
        where = (current[i] if current is not None else None) or name[i]
        if current is not None and current[i] is None:
            where = "NA"
        return f"{_dirname(where)}/{_sans_ext(name[i])}".lower()

    csv_here = {stem_of(i) for i in range(n) if ext[i] == "csv"}
    for i in r_data:
        li = loc[i]
        if li is None or not os.path.exists(li):
            continue
        kind = _FAIR_repo_r_data_kind(li)
        stem = stem_of(i)
        if kind == "model":
            to_outputs(i)
        elif kind != "table" or stem in csv_here:
            keep_as_is(i)
    _set_col(plan, "script_writes", script_writes, "boolean")
    _set_col(plan, "target_path", target, "string")
    _set_col(plan, "convert", convert, "boolean")
    _set_col(plan, "original_target", original, "string")
    return plan


def _flatten(x: Any) -> list[Any]:
    """``unlist()`` of a list column: every element of every cell, missing values dropped."""
    out: list[Any] = []
    for v in x:
        if v is None or (not isinstance(v, list | tuple) and is_missing(v)):
            continue
        if isinstance(v, list | tuple):
            out.extend(e for e in v if not is_missing(e))
        else:
            out.append(v)
    return out


_MODEL_CLASSES = frozenset(
    [
        "lm",
        "glm",
        "merMod",
        "lmerMod",
        "glmerMod",
        "brmsfit",
        "stanfit",
        "stanreg",
        "gam",
        "lavaan",
        "rfsrc",
        "randomForest",
        "train",
        "nls",
        "coxph",
        "survreg",
        "MCMCglmm",
        "glmmTMB",
        "betareg",
        "polr",
        "clm",
        "htest",
    ]
)


def _FAIR_repo_r_data_kind(path: str) -> str:
    """What an R data file holds: ``"table"``, ``"model"`` (a fitted model) or ``"other"``.

    A table is what ``data_read_head()`` reads as a data frame; a model is an object with a
    model call, or of a common model class. Nothing in the file is evaluated.
    """
    from metacheck.datacheck import _files_rdata as rd

    try:
        if re.search(r"\.rds$", path, flags=re.IGNORECASE):
            objs = [rd.read_rds(path)]
        else:
            objs = [obj for _name, obj in rd.read_rdata(path)]
    except Exception:
        return "other"
    if not objs:
        return "other"

    def classes(o: Any) -> list[str]:
        cls = getattr(o, "attrs", {}).get("class")
        return [c for c in (getattr(cls, "value", None) or []) if c is not None]

    def element_named(o: Any, key: str) -> Any:
        names = getattr(getattr(o, "attrs", {}).get("names"), "value", None) or []
        vals = getattr(o, "value", None)
        if key in names and isinstance(vals, list):
            return vals[names.index(key)]
        return None

    def is_model(o: Any) -> bool:
        if _MODEL_CLASSES & set(classes(o)):
            return True
        if getattr(o, "type", None) == rd.VECSXP and not rd.is_data_frame(o):
            call = element_named(o, "call")
            if call is not None and getattr(call, "type", None) == rd.LANGSXP:
                return True
        # an S4 object with a `call` slot
        return getattr(o, "type", None) == rd.OBJSXP and "call" in getattr(o, "attrs", {})

    if any(is_model(o) for o in objs):
        return "model"

    def is_table(o: Any) -> bool:
        if rd.is_data_frame(o):
            return True
        dim = getattr(o, "attrs", {}).get("dim")
        return dim is not None and len(getattr(dim, "value", [])) == 2

    if any(is_table(o) for o in objs):
        return "table"
    return "other"


def _FAIR_repo_identical(plan: pd.DataFrame, structure_df: pd.DataFrame) -> pd.DataFrame:
    """Mark the files the copy does not need twice, in a column ``same_as``.

    ``same_as`` holds the place of the identical file that is kept, else ``None``: a file from
    an archive in the folder that is identical to a file outside it (data_check unpacks a
    ``.zip``, so a folder holding both ``data.zip`` and its unpacked files had every file
    twice), and a README identical to another README (the one highest in the folder is kept).
    A left-out file's ``target_path`` becomes the kept file's, so a script that reads it is
    pointed at the kept copy.
    """
    n = len(plan)
    same_as: list[str | None] = [None] * n
    if not n:
        _set_col(plan, "same_as", same_as, "string")
        return plan
    loc = (_vals(structure_df, "file_location") + [None] * n)[:n]
    file_names = _vals(plan, "file_name")
    raw_path = (
        _vals(structure_df, "file_path") if _has(structure_df, "file_path") else list(file_names)
    )
    raw_path = (raw_path + [None] * n)[:n]
    path = [_slash(str(p if p is not None else file_names[i])) for i, p in enumerate(raw_path)]
    target = _vals(plan, "target_path")
    on_disk = [
        loc[i] is not None and os.path.exists(loc[i]) and target[i] is not None for i in range(n)
    ]
    # a file listed from inside an archive ("data.zip/x.csv") or from where it was unpacked
    # ("data.zip.contents/x.csv")
    in_zip = [
        grepl(r"(^|/)[^/]*\.(zip|tar|tgz|gz|7z|rar)(\.contents)?/", p, ignore_case=True)
        for p in path
    ]
    doc_role = (_vals(structure_df, "doc_role") + [None] * n)[:n]
    readme = [r == "readme" for r in doc_role]
    base = [_basename(p).lower() for p in path]
    zip_bases = {base[i] for i in range(n) if in_zip[i]}
    readme_bases = {base[i] for i in range(n) if readme[i]}
    # only files that could have a twin are read for their checksum
    h: list[str | None] = [None] * n
    for i in range(n):
        if on_disk[i] and (
            in_zip[i] or readme[i] or base[i] in zip_bases or base[i] in readme_bases
        ):
            try:
                h[i] = _md5(loc[i])
            except OSError:
                h[i] = None
    for i in range(n):
        if in_zip[i] and h[i] is not None:
            twin = next((j for j in range(n) if not in_zip[j] and h[j] == h[i]), None)
            if twin is not None:
                same_as[i] = target[twin]
    rows = [i for i in range(n) if readme[i] and h[i] is not None and same_as[i] is None]
    depth = {i: len(path[i].split("/")) for i in rows}
    seen: dict[str, str | None] = {}
    for i in sorted(rows, key=lambda r: depth[r]):
        if h[i] in seen:
            same_as[i] = seen[h[i]]  # type: ignore[index]
        else:
            seen[h[i]] = target[i]  # type: ignore[index]
    for i in range(n):
        if same_as[i] is not None:
            target[i] = same_as[i]
    _set_col(plan, "same_as", same_as, "string")
    _set_col(plan, "target_path", target, "string")
    return plan


def _FAIR_repo_identical_table(plan: pd.DataFrame) -> pd.DataFrame:
    """The files left out because an identical one is in the copy: their path, and the kept file."""
    same = _vals(plan, "same_as")
    rows = [i for i, s in enumerate(same) if s is not None]
    names = _vals(plan, "file_name")
    current = _vals(plan, "current_path") if _has(plan, "current_path") else list(names)
    path = [current[i] if current[i] is not None else names[i] for i in rows]
    return pd.DataFrame(
        {
            "path": pd.Series(path, dtype="string"),
            "same_as": pd.Series([same[i] for i in rows], dtype="string"),
        }
    )


# ── copying ──────────────────────────────────────────────────────────────────


def _FAIR_repo_copy(
    plan: pd.DataFrame,
    structure_df: pd.DataFrame,
    output_dir: str,
    libraries: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Copy every planned file to its place in *output_dir*.

    Each plan row's file is *structure_df*'s row of the same number (see :func:`FAIR_repo`),
    never looked up by name: names repeat across a paper's repositories. A data file planned for
    conversion is written as a real CSV, and its original is copied next to it. A data CSV is
    copied without a byte order mark. An R or Python script gets its file paths and ``setwd()``
    lines changed (:func:`_FAIR_repo_write_script`). When a second file is planned for a path
    already written, an identical copy is left out and a different file is written under a
    numbered name (``x_2.csv``). Rows marked by :func:`_FAIR_repo_identical` are not copied.

    Returns a dict: ``written`` (the path written per plan row, or ``None``), ``scripts``
    (changes per script), ``skipped`` / ``failed`` (0-based plan rows not on disk / not copied),
    ``renamed`` (a frame of from/to paths), ``same_as`` (per plan row, the place of the identical
    file that was kept instead), ``converted``, ``original_written``, ``sheet_files``,
    ``sheet_map`` and ``not_tables`` (0-based plan rows).
    """
    libraries = libraries or {}
    n = len(plan)
    written: list[str | None] = [None] * n
    skipped: list[int] = []
    failed: list[int] = []
    renamed: list[dict[str, str]] = []
    scripts: dict[str, dict[str, Any]] = {}
    src_all = (_vals(structure_df, "file_location") + [None] * n)[:n]
    convert = [_is_true(v) for v in _vals(plan, "convert")]
    converted = list(convert)  # False where the file was no table after all
    original_written: list[str | None] = [None] * n
    sheet_files: list[list[str]] = [[] for _ in range(n)]  # the CSVs of a file's other sheets
    sheet_map: list[dict[str, str]] = [{} for _ in range(n)]  # sheet name -> its CSV
    hashes: dict[str, str] = {}  # target path -> md5 of the file written there
    same_as = list(_vals(plan, "same_as")) if _has(plan, "same_as") else [None] * n
    target = _vals(plan, "target_path")
    file_names = _vals(plan, "file_name")
    dtype = _vals(plan, "data_type")
    original = _vals(plan, "original_target")

    def place(tgt: str, src: str, h: str | None = None) -> str | None:
        """The path to write to: the planned one, unless another file took it.

        ``None`` when an identical file is already there.
        """
        if h is None:
            h = _md5(src)
        if tgt not in hashes:
            hashes[tgt] = h
            return tgt
        if hashes[tgt] == h:
            return None
        stem = _sans_ext(tgt)
        ext_ = _file_ext(tgt)
        k = 2
        while True:
            alt = f"{stem}_{k}" + (f".{ext_}" if ext_ else "")
            if alt not in hashes:
                hashes[alt] = h
                return alt
            if hashes[alt] == h:
                return None
            k += 1

    for i in range(n):
        tgt = target[i]
        if tgt is None or not tgt or same_as[i] is not None:
            continue
        src = src_all[i]
        if src is None or not src or not os.path.exists(src):
            skipped.append(i)
            continue
        to = place(tgt, src)
        if to is None:  # an identical copy is already written
            same_as[i] = tgt
            continue
        if to != tgt:
            renamed.append({"file_name": file_names[i], "planned": tgt, "written": to})
        dest = os.path.join(output_dir, to)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        is_data_csv = bool(
            re.search(r"\.csv$", to, flags=re.IGNORECASE)
            and re.search(r"^(study-[^/]+/)?data/", to)
        )
        script = None
        if dtype[i] == "code":
            script = _FAIR_repo_write_script(
                src,
                file_names[i],
                dest,
                to,
                plan,
                structure_df,
                _FAIR_repo_libraries_for(libraries, plan, i),
            )
        if convert[i]:
            # The original is kept next to the CSV, placed like any other file, so two different
            # originals with one name do not overwrite each other.
            orig = original[i]
            orig_to = place(orig, src) if orig else None
            orig_ok = False
            if orig_to is not None:
                odest = os.path.join(output_dir, orig_to)
                os.makedirs(os.path.dirname(odest), exist_ok=True)
                orig_ok = _copy_file(src, odest)
            if orig_ok:
                original_written[i] = orig_to
            # A spreadsheet with several sheets gets one CSV per sheet holding a table, named
            # after the sheet: only the first sheet was written, so the CSV lacked the other
            # sheets, or held a README sheet instead of the data.
            sheets = _data_file_sheets(src)
            if sheets and len(sheets) > 1:
                made: dict[str, str] = {}
                src_md5 = _md5(src)
                for k, sheet in enumerate(sheets, start=1):
                    # each sheet is its own content: the file's checksum and the sheet
                    sheet_to = place(_FAIR_repo_sheet_target(to, sheet, k), src, h=f"{src_md5} {k}")
                    if sheet_to is None:
                        continue
                    if _FAIR_repo_write_data_csv(src, os.path.join(output_dir, sheet_to), sheet=k):
                        made[sheet] = sheet_to
                if made:
                    paths_made = list(made.values())
                    written[i] = paths_made[0]
                    sheet_files[i] = paths_made[1:]
                    sheet_map[i] = made
                    continue
            if _FAIR_repo_write_data_csv(src, dest):
                written[i] = to
            else:
                # Not a table (an .rds holding a fitted model or a list): the original is the
                # file itself.
                if os.path.exists(dest):
                    os.unlink(dest)
                converted[i] = False
                original_written[i] = None
                if orig_ok:
                    written[i] = orig_to
                else:
                    failed.append(i)
            continue
        if script is not None:
            scripts[to] = script
            ok = True
        elif is_data_csv:
            ok = _FAIR_repo_copy_no_bom(src, dest)
        else:
            ok = _copy_file(src, dest)
        if ok:
            written[i] = to
        else:
            failed.append(i)

    scripts_df = pd.DataFrame(
        {
            "target_path": pd.Series([s["target_path"] for s in scripts.values()], dtype="string"),
            "paths_changed": pd.Series(
                [s["paths_changed"] for s in scripts.values()], dtype="Int64"
            ),
            "setwd_removed": pd.Series(
                [s["setwd_removed"] for s in scripts.values()], dtype="Int64"
            ),
            "setwd_paths": pd.Series([s["setwd_paths"] for s in scripts.values()], dtype="string"),
            "libraries_added": pd.Series(
                [s["libraries_added"] for s in scripts.values()], dtype="string"
            ),
        }
    )
    renamed_df = pd.DataFrame(
        {
            "file_name": pd.Series([r["file_name"] for r in renamed], dtype="string"),
            "planned": pd.Series([r["planned"] for r in renamed], dtype="string"),
            "written": pd.Series([r["written"] for r in renamed], dtype="string"),
        }
    )
    return {
        "written": written,
        "scripts": scripts_df,
        "skipped": skipped,
        "failed": list(dict.fromkeys(failed)),
        "renamed": renamed_df,
        "same_as": same_as,
        "converted": converted,
        "original_written": original_written,
        "sheet_files": sheet_files,
        "sheet_map": sheet_map,
        "not_tables": [
            i for i in range(n) if convert[i] and not converted[i] and written[i] is not None
        ],
    }


def _data_file_sheets(path: str) -> list[str] | None:
    from metacheck.datacheck.files import _data_file_sheets as sheets

    return sheets(path)


def _copy_file(src: str, dest: str) -> bool:
    try:
        shutil.copy2(src, dest)
    except OSError:
        return False
    return True


def _FAIR_repo_write_script(
    src: str,
    file_name: str,
    dest: str,
    target: str,
    plan: pd.DataFrame,
    structure_df: pd.DataFrame | None,
    libraries: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Write an R or Python script with its file paths changed to the new layout.

    The paths are found in the code (for an ``.Rmd``/``.qmd`` its R chunks, for a notebook its
    code cells) and changed in the whole file, each only where it stands in quotes; the
    ``setwd()``/``os.chdir()`` lines are replaced. Returns ``None`` for a file that is not an R
    or Python script (the caller copies it unchanged), else what was changed.
    """
    from metacheck.codecheck.core import code_extract_py, code_extract_r, code_lang, code_read
    from metacheck.repro.core import _repro_apply_rewrites, repro_rewrite_paths

    ext = _file_ext(file_name).lower()
    if ext not in ("r", "rmd", "qmd", "py", "ipynb"):
        return None
    # a notebook or Quarto document declares its language inside the file
    lang = code_lang(src if ext in ("ipynb", "qmd") else file_name)
    if lang not in ("R", "Python"):
        return None
    # Python chunks of a Quarto document are not extracted
    if ext == "qmd" and lang != "R":
        return None
    try:
        raw: list[Any] = list(code_read(src))
    except Exception:
        return None
    try:
        if ext in ("rmd", "qmd"):
            code = code_extract_r(src)
        elif ext == "ipynb":
            code = code_extract_py(src)
        else:
            code = raw
    except Exception:
        code = []

    rw = repro_rewrite_paths(code, target, plan, lang, structure_df=structure_df)
    txt = _repro_apply_rewrites(list(raw), rw)
    txt, n_pointed = _FAIR_repo_point_to_new_paths(txt, plan)
    # A notebook's lines are JSON strings, which the setwd() pattern does not match, so a
    # notebook keeps its setwd()/os.chdir() calls.
    txt, n_setwd, setwd_paths = _FAIR_repo_neutralise_setwd(txt, lang, parse=ext == "r")
    # the library() calls reproducibility_check added when it ran the script (execute = TRUE),
    # for a function of a package the script did not load
    libs = [p for p in libraries if p] if ext == "r" else []
    if libs:
        added: list[str | None] = [
            f"library({p})  # [metacheck FAIR_repo added: the script uses a function of this "
            "package without loading it]"
            for p in libs
        ]
        txt = added + list(txt)

    # A script with nothing to change is copied as it is, so its line endings and encoding stay
    # the authors'.
    if list(txt) == raw:
        if not _copy_file(src, dest):
            return None
    else:
        try:
            _write_lines(dest, ["" if t is None else t for t in txt])
        except OSError:
            return None

    changed = 0
    if rw is not None and len(rw):
        matched = _vals(rw, "matched")
        ambiguous = _vals(rw, "ambiguous")
        rtarget = _vals(rw, "target")
        refs = _vals(rw, "ref")
        as_written = _vals(rw, "as_written") if "as_written" in rw.columns else [False] * len(rw)
        changed = sum(
            1
            for k in range(len(rw))
            if _is_true(matched[k])
            and not ambiguous[k]
            and rtarget[k]
            and not as_written[k]
            and rtarget[k] != refs[k]
        )
    return {
        "target_path": target,
        "paths_changed": changed + n_pointed,
        "setwd_removed": n_setwd,
        "setwd_paths": ", ".join(setwd_paths),
        "libraries_added": ", ".join(libs),
    }


def _FAIR_repo_libraries_for(
    libraries: Mapping[str, Sequence[str]], plan: pd.DataFrame, i: int
) -> list[str]:
    """The packages ``reproducibility_check`` added ``library()`` calls for to plan row *i*'s script.

    Found by its file name; none when two scripts share that name, since which one was run is
    unknown.
    """
    names = _vals(plan, "file_name")
    types = _vals(plan, "data_type") if _has(plan, "data_type") else [None] * len(plan)
    fn = _basename(str(names[i]))
    code_names = [_basename(str(nm)) for nm, t in zip(names, types, strict=False) if t == "code"]
    if not libraries or sum(1 for c in code_names if c == fn) != 1:
        return []
    return list(libraries.get(fn, []))


def _FAIR_repo_added_libraries(ops: Any) -> dict[str, list[str]]:
    """The ``library()`` calls ``reproducibility_check`` added to a script it ran.

    Added (with ``execute=True``) because the script used a function of a package it did not
    load: script file name -> packages. Empty when no code was run.
    """
    m = _op_get(ops, "reproducibility_check", "modifications")
    if not isinstance(m, pd.DataFrame) or len(m) == 0 or "change_type" not in m.columns:
        return {}
    out: dict[str, list[str]] = {}
    for kind, detail, fname in zip(
        _vals(m, "change_type"), _vals(m, "detail"), _vals(m, "file_name"), strict=False
    ):
        if kind == "library_injected" and detail is not None and fname is not None:
            out.setdefault(_basename(str(fname)), []).append(
                re.sub(r"^library\((.*)\)$", r"\1", str(detail))
            )
    return dict(sorted(out.items()))


# ── setwd(), written paths, README links ─────────────────────────────────────


def _lines_start_in_string(lines: Sequence[str]) -> list[bool]:
    """Whether each line of R code starts inside a string or a backtick name.

    A small lexer for R's strings (single and double quotes with backslash escapes, raw strings
    ``r"(...)"``), backticks and comments, enough to tell a real ``setwd()`` call from text that
    holds one in a multi-line string.
    """
    out: list[bool] = []
    state: str | None = None  # the closing quote, or the closing sequence of a raw string
    raw = False
    for line in lines:
        out.append(state is not None)
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if state is None:
                if c == "#":
                    break
                m = re.match(r"""[rR](["'])(-*)([\[({])""", line[i:])
                if m and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] in "._")):
                    close = {"[": "]", "(": ")", "{": "}"}[m.group(3)]
                    state = close + m.group(2) + m.group(1)
                    raw = True
                    i += m.end()
                    continue
                if c in "\"'`":
                    state = c
                    raw = False
            elif raw:
                if line.startswith(state, i):
                    i += len(state)
                    state = None
                    raw = False
                    continue
            elif c == "\\":
                i += 1
            elif c == state:
                state = None
            i += 1
    return out


def _FAIR_repo_neutralise_setwd(
    txt: Sequence[str | None], lang: str, parse: bool = False
) -> tuple[list[str | None], int, list[str]]:
    """Replace a script's ``setwd()``/``os.chdir()`` calls that would send its paths astray.

    Replaced are the calls that set a folder written out in the script (an absolute path on the
    authors' computer, or a relative folder, which no longer holds what it did), set no folder,
    or set the script's own folder (rstudioapi's document path, ``sys.frame(1)$ofile``,
    ``__file__``) with a statement that does nothing (see ``_repro_neutralise_setwd()``). The
    scripts' file paths now start at the top folder, so any of these sends them to the wrong
    place. A folder computed when the code runs (``setwd(tempdir())``, or ``setwd(old)`` to go
    back to where it was) is left. With *parse*, an R script is parsed so only real calls count,
    not the text ``setwd(`` inside a string. Returns ``(lines, n, paths)``.
    """
    from metacheck.repro.core import _repro_neutralise_setwd

    lines = list(txt)
    call_pat = (
        r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?chdir\s*\((.*)$"
        if lang == "Python"
        else r"^\s*(?:base::)?setwd\s*\((.*)$"
    )
    rx = re.compile(call_pat)
    matches = [None if t is None else rx.match(t) for t in lines]
    hit = [m is not None for m in matches]
    if parse and any(hit):
        from metacheck.codecheck._rparse import parse_error

        parsed_ok = parse_error([t or "" for t in lines], engine="python") is None
        if parsed_ok:
            in_string = _lines_start_in_string([t or "" for t in lines])
            hit = [h and not s for h, s in zip(hit, in_string, strict=True)]
    own = re.compile(
        r"getActiveDocumentContext|getSourceEditorContext|sys\.frame|this\.path|thisfile|__file__"
    )
    replace = []
    for k, m in enumerate(matches):
        arg = m.group(1) if m is not None else ""
        replace.append(
            hit[k]
            and (
                re.match(r"^\s*\)", arg) is not None  # no folder
                or re.match(r"""^\s*(['"])[^'"]*\1\s*\)""", arg) is not None  # a written-out folder
                or own.search(arg) is not None  # the script's own folder
            )
        )
    if not any(replace):
        return lines, 0, []
    idx = [k for k, r in enumerate(replace) if r]
    new, n, paths = _repro_neutralise_setwd([lines[k] for k in idx], lang, by="metacheck FAIR_repo")
    for k, t in zip(idx, new, strict=True):
        lines[k] = t
    return lines, n, list(paths)


def _FAIR_repo_point_to_new_paths(
    txt: Sequence[str | None], plan: pd.DataFrame
) -> tuple[list[str | None], int]:
    """Point a script's quoted paths to the new place of the files scripts write.

    A script that writes ``write.csv(x, "results/table.csv")`` writes ``"outputs/table.csv"`` in
    the copy, where the shared table is, and one that writes data another script reads writes it
    under its new name, which the reading script now uses. ``repro_rewrite_paths()`` changes the
    files a script reads, not the ones it writes. A quoted string counts when its last part is
    the original name of such a file. Returns the lines and the number of strings changed.
    """
    lines = list(txt)
    n_plan = len(plan)
    written_by_script = (
        [_is_true(v) for v in _vals(plan, "script_writes")]
        if _has(plan, "script_writes")
        else [False] * n_plan
    )
    convert = [_is_true(v) for v in _vals(plan, "convert")]
    original = _vals(plan, "original_target")
    target = _vals(plan, "target_path")
    # a file written as CSV with its original kept: a script writes the original
    place = [
        original[i] if convert[i] and original[i] is not None else target[i] for i in range(n_plan)
    ]
    out = [
        i
        for i in range(n_plan)
        if place[i] is not None
        and (re.match(r"^(study-[^/]+/)?outputs/", place[i]) or written_by_script[i])
    ]
    if not out:
        return lines, 0
    name = [_basename(str(f)).lower() for f in _vals(plan, "file_name")]
    target_of: dict[str, str] = {}
    for i in out:
        target_of.setdefault(name[i], place[i])
    # one name for two different files: which one a script means is unknown (an identical copy
    # that is left out does not count)
    same_as = _vals(plan, "same_as") if _has(plan, "same_as") else [None] * n_plan
    kept = [name[i] for i in range(n_plan) if target[i] is not None and same_as[i] is None]
    twice = {k for k in kept if kept.count(k) > 1}
    target_of = {k: v for k, v in target_of.items() if k not in twice}
    n = 0
    pat = re.compile(r"""(["'])([^"'\n]+)\1""")
    for k, line in enumerate(lines):
        if line is None:
            continue
        found = list(dict.fromkeys(m.group(0) for m in pat.finditer(line)))
        for s in found:
            inner = s[1:-1]
            key = _basename(inner).lower()
            if key not in target_of or inner == target_of[key]:
                continue
            q = s[0]
            lines[k] = (lines[k] or "").replace(s, f"{q}{target_of[key]}{q}")
            n += 1
    return lines, n


_README_LINK = re.compile(r"""(\]\(\s*<?|(?:src|href)\s*=\s*["'])([^)"'<>\s#?]+)""")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:|^/")


def _FAIR_repo_fix_readme_links(
    plan: pd.DataFrame, structure_df: pd.DataFrame, output_dir: str
) -> list[str]:
    """Change the links in the copied READMEs that point to a file of the folder to its new place.

    Markdown, R Markdown, Quarto and HTML READMEs: the README kept its place but the file (such
    as an image in ``man/figures/``) moved, so the images no longer showed. Only the link
    changes, in the copy; a link to a web page, to a part of the page, or to a file that is not
    in the folder is left as it is. A link is read from the README's own folder, as in the folder
    as it was. Returns the READMEs changed.
    """
    from metacheck.utils import _url_decode

    if not len(plan) or not _has(plan, "written"):
        return []
    raw = (
        _vals(structure_df, "file_path")
        if _has(structure_df, "file_path")
        else _vals(structure_df, "file_name")
    )
    names = _vals(plan, "file_name")
    raw = (raw + [None] * len(plan))[: len(plan)]
    orig = [
        re.sub(r"^\./", "", _slash(str(p if p is not None else names[i])))
        for i, p in enumerate(raw)
    ]
    written = _vals(plan, "written")
    same_as = _vals(plan, "same_as") if _has(plan, "same_as") else [None] * len(plan)
    readmes = [
        i
        for i in range(len(plan))
        if written[i] is not None
        and re.match(r"^readme.*\.(md|markdown|rmd|qmd|html?)$", _basename(orig[i]), flags=re.I)
    ]
    # the written place of each file, by its path in the folder as it was
    place_of: dict[str, str] = {}
    for i in range(len(plan)):
        p = written[i] if written[i] is not None else same_as[i]
        if p is not None:
            place_of.setdefault(orig[i].lower(), p)
    changed: list[str] = []
    for i in readmes:
        dest = os.path.join(output_dir, written[i])
        try:
            with open(dest, "rb") as fh:
                text = fh.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        txt = re.split(r"\r\n|\r|\n", text)
        if txt and txt[-1] == "":
            txt.pop()
        from_dir = _dirname(orig[i])
        new_dir = _dirname(written[i])
        up = "" if new_dir == "." else "../" * len(new_dir.split("/"))
        new_txt = list(txt)
        for k, line in enumerate(txt):
            for s in dict.fromkeys(m.group(0) for m in _README_LINK.finditer(line)):
                m2 = _README_LINK.match(s)
                if m2 is None:
                    continue
                lead, link = m2.group(1), m2.group(2)
                if _URL_SCHEME.match(link):  # web, mail, absolute
                    continue
                plain = _url_decode([link])[0] or link
                rel = _FAIR_repo_join_path(from_dir, plain)
                to = None if rel is None else place_of.get(rel.lower())
                if to is None:
                    continue
                new_link = up + to
                if "%20" in link:
                    new_link = new_link.replace(" ", "%20")
                if new_link == link:
                    continue
                new_txt[k] = new_txt[k].replace(s, lead + new_link)
        if new_txt == txt:
            continue
        try:
            _write_lines(dest, new_txt)
        except OSError:
            continue
        changed.append(written[i])
    return changed


def _FAIR_repo_join_path(dir: str, link: str) -> str | None:
    """*link* read from the folder *dir* (``"."`` for the top), as a path from the top folder.

    ``"./a/../b.png"`` from ``"docs"`` is ``"docs/b.png"``; ``None`` when it leaves the top
    folder.
    """
    parts = ([] if dir == "." else dir.split("/")) + link.split("/")
    out: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if not out:
                return None
            out.pop()
        else:
            out.append(p)
    return "/".join(out) if out else None


def _FAIR_repo_copy_no_bom(src: str, dest: str) -> bool:
    """Copy a file, dropping a leading UTF-8 byte order mark (EF BB BF) if present.

    With one, the first column name of a CSV is read as ``"\\ufeffid"``. Byte-level, so it works
    for any encoding. Returns ``True`` on success.
    """
    try:
        with open(src, "rb") as fh:
            data = fh.read()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        with open(dest, "wb") as out:
            out.write(data)
    except OSError:
        return False
    return True


def _FAIR_repo_sheet_key(file: str, sheet: str, columns_df: Any) -> str | None:
    """data_check's name for the table on sheet *sheet* of the file it calls *file*.

    ``"file [sheet]"`` when it read several tables from the file, the file's own name when this
    was the only one, ``None`` when it read no table from this sheet.
    """
    if not isinstance(columns_df, pd.DataFrame) or "sheet" not in columns_df.columns:
        return None
    source = _vals(columns_df, "source_file")
    sheets = _vals(columns_df, "sheet")
    key = f"{file} [{sheet}]"
    if key in source:
        return key
    if any(s == file and sh == sheet for s, sh in zip(source, sheets, strict=False)):
        return file
    return None


def _FAIR_repo_sheet_target(target: str, sheet: str, k: int) -> str:
    """The CSV of one sheet: the file's CSV name with the sheet as its Psych-DS ``description``.

    ``data/study-harvest_data.csv`` and sheet ``data sheet`` give
    ``data/study-harvest_description-datasheet_data.csv``. A sheet of notes or a codebook rather
    than a table goes to ``documentation/``: in ``data/`` its cells were listed as variables
    without names.
    """
    from metacheck.datacheck.files import _sheet_is_notes

    value = gsub(r"[^A-Za-z0-9]+", "", sheet)
    if not value:
        value = f"sheet{k}"
    out = sub(r"_data\.csv$", f"_description-{value}_data.csv", target)
    if _sheet_is_notes([sheet])[0]:
        out = sub(r"^((study-[^/]+/)?)data/", r"\1documentation/", out)
    return str(out)


def _csv_cell(x: Any, quote: bool) -> str:
    if is_missing(x):
        return ""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, pd.Timestamp):
        # as.character() of a Date, or of a date-time
        midnight = x == x.normalize()
        return x.strftime("%Y-%m-%d" if midnight else "%Y-%m-%d %H:%M:%S")
    if not quote:
        return as_character(x) or ""
    return '"' + str(x).replace('"', '""') + '"'


def _FAIR_repo_write_data_csv(src: str, dest: str, sheet: int | str | None = None) -> bool:
    """Write a real CSV file from a data file that is not one (xlsx, sav, dta, ...).

    The file is read in full with ``data_read_head()``, the reader data_check uses, so the
    columns match the ones data_check found. UTF-8, no byte order mark, no row names (R's
    ``write.csv()``: text quoted, numbers as ``as.character()`` gives them, missing values
    empty). Returns ``False`` when the file cannot be read as a table.
    """
    from metacheck.datacheck.files import data_read_head

    try:
        df = data_read_head(src, n_rows=float("inf"), sheet=sheet)
        if df is None or not isinstance(df, pd.DataFrame) or df.shape[1] == 0 or len(df) == 0:
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        quoted = [
            not (
                pd.api.types.is_bool_dtype(df.dtypes.iloc[c])
                or pd.api.types.is_numeric_dtype(df.dtypes.iloc[c])
                or pd.api.types.is_datetime64_any_dtype(df.dtypes.iloc[c])
            )
            for c in range(df.shape[1])
        ]
        lines = [",".join('"' + str(c).replace('"', '""') + '"' for c in df.columns)]
        cols = [df.iloc[:, c].tolist() for c in range(df.shape[1])]
        lines.extend(
            ",".join(_csv_cell(cols[c][r], quoted[c]) for c in range(len(cols)))
            for r in range(len(df))
        )
        with open(dest, "wb") as fh:
            fh.write(("\n".join(lines) + "\n").encode("utf-8", errors="replace"))
    except Exception:
        return False
    return True


def _FAIR_repo_messages(plan: pd.DataFrame, copied: dict[str, Any]) -> None:
    """Tell the user what was not copied, renamed, or changed in a script."""
    written = _vals(plan, "written")
    n_written = sum(w is not None for w in written)
    msg = [f"Wrote {n_written} file{plural(n_written)} to the new layout."]
    dtype = _vals(plan, "data_type")
    names = _vals(plan, "file_name")
    skipped = copied["skipped"]
    if skipped:
        k = len(skipped)
        counts: dict[str, int] = {}
        for i in skipped:
            t = dtype[i] if dtype[i] is not None else "unknown"
            counts[t] = counts.get(t, 0) + 1
        by_type = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        msg.append(
            f"{k} file{plural(k)} {plural(k, 'was', 'were')} not copied, because "
            f"{plural(k, 'it', 'they')} could not be read (by type: "
            + ", ".join(f"{c} {t}" for t, c in by_type)
            + ")."
        )
    failed = copied["failed"]
    if failed:
        k = len(failed)
        msg.append(
            f"{k} file{plural(k)} could not be written: "
            + ", ".join(names[i] for i in failed)
            + "."
        )
    not_tables = copied["not_tables"]
    if not_tables:
        k = len(not_tables)
        msg.append(
            f"{k} data file{plural(k)} {plural(k, 'is', 'are')} no table (for example an R file "
            f"holding a fitted model), so {plural(k, 'it was', 'they were')} kept as "
            f"{plural(k, 'it is', 'they are')}, without a CSV copy."
        )
    n_same = sum(s is not None for s in _vals(plan, "same_as"))
    if n_same:
        msg.append(
            f"{n_same} file{plural(n_same)} {plural(n_same, 'was', 'were')} left out, because an "
            "identical file is in the copy (for example a file both in a .zip and next to it); "
            "CHANGES.md lists them."
        )
    renamed = copied["renamed"]
    if len(renamed):
        pairs = "; ".join(
            f"{a} -> {b}" for a, b in zip(renamed["file_name"], renamed["written"], strict=True)
        )
        msg.append(
            "Two different files had the same place in the new layout, so the second was given "
            f"a numbered name: {pairs}. A script that reads one of them may need its path checked."
        )
    sc = copied["scripts"]
    if len(sc) and (sc["paths_changed"] > 0).any():
        k = int((sc["paths_changed"] > 0).sum())
        msg.append(
            f"File paths were changed in {k} script{plural(k)}, so {plural(k, 'it finds', 'they find')} "
            "the files in the new layout."
        )
    if len(sc) and (sc["setwd_removed"] > 0).any():
        hit = sc[sc["setwd_removed"] > 0]
        k = len(hit)
        msg.append(
            f"setwd() was removed from {k} script{plural(k)} ({', '.join(hit['target_path'])}), "
            "because it set a folder written out in the script or the script's own folder, and "
            "the file paths now start at the top folder; each line is kept as a comment. Run "
            "the scripts from the top folder of the copy."
        )
    libs = sc["libraries_added"].fillna("") if len(sc) else pd.Series([], dtype="string")
    if len(sc) and (libs != "").any():
        k = int((libs != "").sum())
        msg.append(
            f"library() calls were added at the top of {k} script{plural(k)}, because running "
            f"{plural(k, 'it', 'them')} showed {plural(k, 'it uses', 'they use')} a function of a "
            f"package {plural(k, 'it does', 'they do')} not load."
        )
    recovered = _vals(plan, "recovered_row")
    n_rec = sum(
        1 for w, r in zip(written, recovered, strict=False) if w is not None and r is not None
    )
    if n_rec:
        msg.append(
            f"{n_rec} script{plural(n_rec)} {plural(n_rec, 'was', 'were')} recovered from output "
            "files that hold the code that made them; CHANGES.md lists them."
        )
    _message("\n".join(msg))
