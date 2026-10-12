"""Helpers of the ``code_check`` module (port of ``inst/modules/code_check.R``).

:mod:`metacheck.modules.code_check` holds the module function; this file holds
the per-file analysis loop, the per-paper summary table, the version-pinning
report and the manifest merge that ``code_check()`` runs inline in R. The
text scanners themselves (comments, absolute paths, ``setwd()``, libraries,
file references, version pins) are :mod:`metacheck.codecheck.core`.
"""

from __future__ import annotations

import contextlib
import math
import os
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any, cast

import pandas as pd

from metacheck._r.base import as_character, paste, plural, slashed
from metacheck._r.regex import grepl
from metacheck._values import is_missing

# R: listed_langs / checked_langs
LISTED_LANGS = ("R", "Python", "SAS", "SPSS", "Stata", "Mplus", "MATLAB", "JASP")
CHECKED_LANGS = tuple(lang for lang in LISTED_LANGS if lang != "JASP")

# R: analysis_cols, the per-file columns seeded as NA when nothing was analysed
ANALYSIS_COLS = (
    "checked",
    "parse_error",
    "parse_error_msg",
    "code_abs_path",
    "absolute_paths",
    "code_setwd",
    "setwd_calls",
    "code_install_packages",
    "install_packages_calls",
    "library_lines",
    "library_max_between",
    "packages_n",
    "packages",
    "comment_lines",
    "code_lines",
    "percentage_comment",
    "has_docstring",
    "loaded_files_missing",
    "loaded_files_missing_names",
    "error",
)

# the R type of each per-file column (one-row data frames bound by bind_rows())
_DTYPES = {
    "checked": "boolean",
    "parse_error": "boolean",
    "parse_error_msg": "string",
    "code_abs_path": "Int64",
    "absolute_paths": "string",
    "code_setwd": "Int64",
    "setwd_calls": "string",
    "code_install_packages": "Int64",
    "install_packages_calls": "string",
    "library_lines": "Int64",
    "library_max_between": "Int64",
    "packages_n": "Int64",
    "packages": "string",
    "comment_lines": "Int64",
    "code_lines": "Int64",
    "percentage_comment": "float64",
    "has_docstring": "boolean",
    "loaded_files_missing": "Int64",
    "loaded_files_missing_names": "string",
    "error": "string",
}


# ---------------------------------------------------------------------------
# small R idioms
# ---------------------------------------------------------------------------


def col(df: pd.DataFrame, name: str, default: Any = None) -> list[Any]:
    """``df$name`` as a list (*default* recycled when the column is absent)."""
    if name in df.columns:
        return [None if is_missing(v) else v for v in df[name].tolist()]
    return [default] * len(df)


def _r_tolower(x: str) -> str:
    """R ``tolower()``: a per-character (``towlower``) mapping.

    ``towlower`` is Unicode's simple lowercase mapping; Python's full mapping
    differs only where it yields several characters (``"İ".lower()`` is
    ``"i̇"``), whose first one is the simple mapping (``"i"``).
    """
    return "".join(ch.lower()[0] for ch in x)


def _r_basename(x: str) -> str:
    """R ``basename()`` of a ``/``-separated path (trailing slashes ignored)."""
    return slashed(x).rstrip("/").rsplit("/", 1)[-1]


def _base_names(x: Sequence[Any]) -> list[str | None]:
    """``gsub("\\\\", "/", x) |> basename()`` (``NA`` stays ``NA``)."""
    return [None if is_missing(v) else _r_basename(str(v).replace("\\", "/")) for v in x]


def location_series(values: list[Any], index: Any, like: pd.Series | None) -> pd.Series:
    """A ``file_location`` column of *values*: ``string`` when *like* is, else ``object``."""
    dtype: Any = object
    if like is not None and isinstance(like.dtype, pd.StringDtype):
        dtype = like.dtype
    out: pd.Series = pd.Series(values, index=index, dtype=dtype)
    return out


def _msg(exc: BaseException) -> str:
    """``conditionMessage(e)`` of an error raised by a code reader/scanner."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


# ---------------------------------------------------------------------------
# the per-file analysis loop
# ---------------------------------------------------------------------------


def _read_lines(path: Any, file_name: Any, language: Any) -> list[str | None]:
    """The code of one file, extracted the way ``code_check()`` picks it."""
    from metacheck.codecheck.core import (
        _as_chr,
        code_extract_py,
        code_extract_qmd_py,
        code_extract_r,
        code_read,
    )

    name = None if is_missing(file_name) else str(file_name)
    is_rmd = name is not None and grepl(r"\.rmd$", name, ignore_case=True)
    is_qmd = name is not None and grepl(r"\.qmd$", name, ignore_case=True)
    is_qmd_py = is_qmd and language == "Python"
    is_ipynb = name is not None and grepl(r"\.ipynb$", name, ignore_case=True)
    if is_rmd or (is_qmd and not is_qmd_py):
        lines = code_extract_r(path)
    elif is_qmd_py:
        lines = code_extract_qmd_py(path)
    elif is_ipynb:
        lines = code_extract_py(path)
    else:
        lines = code_read(path)
    out = _as_chr(lines)
    return [] if out is None else out


def _parse_text(lines: list[str | None]) -> list[str]:
    """What ``code_parse_r(text = lines)`` hands to ``parse()``.

    A text whose first line is a YAML ``---`` fence is purled first. An
    empty file parses (nothing to parse); R's ``code_parse_r(text =
    character(0))`` failed with "subscript out of bounds" before PR #426, which
    ``code_check()`` recorded as the file's error (UPSTREAM_ISSUES U87).
    """
    from metacheck.codecheck.core import code_extract_r

    if not lines:
        return []
    if lines[0] is not None and grepl(r"^---\s*$", lines[0]):
        lines = list(code_extract_r(text=cast(Any, lines)) or [])
    return ["NA" if v is None else v for v in lines]


def _repo_basenames(all_files: pd.DataFrame) -> dict[Any, set[str]]:
    """Lower-cased base names of the files listed in each repository."""
    out: dict[Any, set[str]] = {}
    if "repo_url" not in all_files.columns:
        return out
    repos = col(all_files, "repo_url")
    names = _base_names(col(all_files, "file_name"))
    for repo, name in zip(repos, names, strict=True):
        if repo is None:
            continue
        bucket = out.setdefault(repo, set())
        if name is not None:
            bucket.add(_r_tolower(name))
    return out


def analyse_files(
    checked_files: pd.DataFrame, all_files: pd.DataFrame
) -> tuple[list[dict[str, Any]], dict[Any, dict[Any, list[str | None]]]]:
    """Run every code check on every file of *checked_files*.

    The loop of ``code_check()``: one dict of per-file results per row (in
    the order R sets them, so a file whose check failed half-way keeps the
    columns set before the error, plus ``error``), and the R code text of
    each file keyed by ``paper_id`` then row. R's ``parse()`` of
    every R file runs as one batch (one ``Rscript`` process with the R
    parser engine) rather than one per file.
    """
    from metacheck.codecheck._rparse import parse_errors
    from metacheck.codecheck.core import (
        code_abs_path,
        code_file_refs,
        code_install_packages,
        code_library_lines,
        code_library_names,
        code_line_stats,
        code_remove_comments,
        code_setwd,
    )
    from metacheck.utils import pb

    # R: lapply(seq_along(checked_files$file_location), ...) -- no rows at all
    # without a file_location column
    if "file_location" not in checked_files.columns:
        return [], {}
    n = len(checked_files)
    names = col(checked_files, "file_name")
    langs = col(checked_files, "language")
    locs = col(checked_files, "file_location")
    # R reads `the_file$file_url`: NULL without the column, else a string or
    # NA -- which R's readers take as the path "NA" (e.g. "'NA' does not exist")
    urls = (
        ["NA" if u is None else u for u in col(checked_files, "file_url")]
        if "file_url" in checked_files.columns
        else [None] * n
    )
    repos = col(checked_files, "repo_url")
    has_pid = "paper_id" in checked_files.columns
    pids = [None if v is None else str(as_character(v)) for v in col(checked_files, "paper_id")]
    repo_names = _repo_basenames(all_files)

    bar = pb(n, ":what [:bar] :current/:total") if n else None
    rows: list[dict[str, Any]] = []
    lines_of: list[list[str | None] | None] = []
    r_text_by_paper: dict[Any, dict[Any, list[str | None]]] = {}
    to_parse: list[int] = []
    parse_texts: list[list[str]] = []

    # 1. read each file (and prepare R's parse() input)
    for i in range(n):
        row: dict[str, Any] = {"checked": True}
        rows.append(row)
        lines_of.append(None)
        if bar is not None:
            bar.tick(1, {"what": names[i]})
        path = locs[i] if locs[i] is not None else urls[i]
        try:
            lines = _read_lines(path, names[i], langs[i])
        except Exception as exc:
            row["error"] = _msg(exc)
            continue
        if langs[i] == "R":
            # keyed by row, not by file name: R keeps only the last of two
            # same-named files, and of the files of an NA paper id only the
            # last one (UPSTREAM_ISSUES U89)
            pid = pids[i] if has_pid else "_all"
            r_text_by_paper.setdefault(pid, {})[i] = lines
        row["parse_error"] = None
        row["parse_error_msg"] = None
        if langs[i] == "R":
            try:
                parse_texts.append(_parse_text(lines))
            except Exception as exc:
                row["error"] = _msg(exc)
                continue
            to_parse.append(i)
        lines_of[i] = lines
    if bar is not None:
        bar.terminate()

    # 2. parse every R file at once
    for i, m in zip(to_parse, parse_errors(parse_texts), strict=True):
        rows[i]["parse_error"] = m is not None
        rows[i]["parse_error_msg"] = None if m is None else m.replace("<text>", "line", 1)

    # 3. the text checks
    for i in range(n):
        read = lines_of[i]
        if read is None:
            continue
        # code lines, NA (None) included, as the scanners take R character vectors
        code: Any = read
        row = rows[i]
        lang = langs[i]
        try:
            file_nc: Any = code_remove_comments(code, lang)

            absolute_paths = code_abs_path(file_nc)
            row["code_abs_path"] = len(absolute_paths)
            row["absolute_paths"] = paste(absolute_paths["abs_path"].tolist(), collapse=" | ")

            if lang == "R":
                setwd_calls = code_setwd(file_nc)["setwd_call"].tolist()
            else:
                setwd_calls = []
            row["code_setwd"] = len(setwd_calls)
            row["setwd_calls"] = paste(setwd_calls, collapse=" | ")

            if lang == "R":
                install_calls = code_install_packages(file_nc)["install_packages_call"].tolist()
            else:
                install_calls = []
            row["code_install_packages"] = len(install_calls)
            row["install_packages_calls"] = paste(install_calls, collapse=" | ")

            library_lines = [int(v) for v in code_library_lines(file_nc, lang)["line"].tolist()]
            row["library_lines"] = len(library_lines)
            if len(library_lines) > 1:
                row["library_max_between"] = max(b - a for a, b in pairwise(library_lines))
            else:
                row["library_max_between"] = None

            from metacheck._r.base import r_sorted

            found = code_library_names(file_nc, lang)["package"].tolist()
            pkgs = r_sorted([p for p in dict.fromkeys(found) if not is_missing(p)])
            row["packages_n"] = len(pkgs)
            row["packages"] = ", ".join(pkgs)

            stats = code_line_stats(code, lang)
            row["comment_lines"] = stats["comment_lines"]
            row["code_lines"] = stats["code_lines"]
            row["percentage_comment"] = stats["percent_comments"]
            row["has_docstring"] = stats["has_docstring"]

            refs = _base_names(code_file_refs(file_nc, lang))
            in_repo = repo_names.get(repos[i], set()) if repos[i] is not None else set()
            missing = [r for r in refs if r is not None and _r_tolower(r) not in in_repo]
            row["loaded_files_missing"] = len(missing)
            row["loaded_files_missing_names"] = ", ".join(missing)
        except Exception as exc:
            row["error"] = _msg(exc)
    return rows, r_text_by_paper


def collected_frame(checked_files: pd.DataFrame, rows: list[dict[str, Any]]) -> pd.DataFrame:
    """``dplyr::bind_rows(collected)``: each checked file with its results.

    Columns are the file's own followed by the result columns in the order
    they first appear across the rows (so an early error row puts ``error``
    before the columns a later, successful row adds).
    """
    order: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                order.append(k)
    out = checked_files.reset_index(drop=True).copy()
    for k in order:
        if k in out.columns:
            base = out[k].tolist()
            values = [row.get(k, base[j]) for j, row in enumerate(rows)]
            out[k] = pd.Series(values, dtype=out[k].dtype if k not in _DTYPES else _DTYPES[k])
            continue
        values = [row.get(k) for row in rows]
        if k == "percentage_comment":
            values = [math.nan if v is None else float(v) for v in values]
        out[k] = pd.Series(values, dtype=_DTYPES.get(k, object))
    return out


def seed_analysis_cols(code_files: pd.DataFrame) -> pd.DataFrame:
    """R: every per-file analysis column absent from *code_files* becomes ``NA``."""
    out = code_files.copy()
    for k in ANALYSIS_COLS:
        if k not in out.columns:
            out[k] = pd.Series([None] * len(out), index=out.index, dtype="boolean")
    return out


# ---------------------------------------------------------------------------
# summary table
# ---------------------------------------------------------------------------


def _num(values: list[Any]) -> list[float | None]:
    return [None if is_missing(v) else float(v) for v in values]


def summary_table(
    code_files: pd.DataFrame,
    all_files: pd.DataFrame,
    r_text_by_paper: Mapping[Any, Mapping[Any, Any]],
    version_pin: Mapping[str, Any],
    skip_on_api_limit: bool,
    max_files_per_repo: float,
    max_file_size: float = 100,
    max_download_size: float = 500,
    cache: bool = False,
) -> pd.DataFrame:
    """The per-paper ``summary_table`` of ``code_check()``.

    ``summarise(.by = paper_id)`` of the file table, the distinct packages
    per paper and whether each paper's repository pins its R environment
    (``.code_version_pin_check()`` per paper, when ``all_files`` has a
    ``paper_id`` column).
    """
    from metacheck.codecheck.core import _code_version_pin_check, code_packages

    if "paper_id" not in code_files.columns:
        raise KeyError("Can't select columns that don't exist.\n✖ Column `paper_id` doesn't exist.")
    pid_values = code_files["paper_id"].tolist()
    groups: dict[Any, list[int]] = {}
    keys: list[Any] = []
    for i, v in enumerate(pid_values):
        key = None if is_missing(v) else v
        if key not in groups:
            groups[key] = []
            keys.append(key)
        groups[key].append(i)

    def has(c: str) -> bool:
        return c in code_files.columns

    checked = col(code_files, "checked")
    num_cols = {
        c: _num(col(code_files, c))
        for c in (
            "code_abs_path",
            "code_setwd",
            "code_install_packages",
            "loaded_files_missing",
            "percentage_comment",
            "parse_error",
        )
        if has(c)
    }

    def total(c: str, rows: list[int]) -> int:
        if c not in num_cols:
            return 0
        return int(sum(v for v in (num_cols[c][i] for i in rows) if v is not None))

    packages = col(code_files, "packages")
    recs = []
    for key in keys:
        rows = groups[key]
        pcs = [
            v
            for v in (num_cols.get("percentage_comment", [None] * len(code_files))[i] for i in rows)
            if v is not None and not math.isnan(v)
        ]
        recs.append(
            {
                "paper_id": key,
                "code_n": len(rows),
                "code_checked": sum(1 for i in rows if checked[i] is True),
                "code_abs_path": total("code_abs_path", rows),
                "code_setwd": total("code_setwd", rows),
                "code_install_packages": total("code_install_packages", rows),
                "code_missing_files": total("loaded_files_missing", rows),
                "code_min_comments": min(pcs) if pcs else math.nan,
                "code_parse_errors": total("parse_error", rows),
                # files of an NA paper id (paper = None) count as one paper;
                # R's split() drops them and reports 0 (UPSTREAM_ISSUES U78)
                "code_packages_n": len(code_packages([packages[i] for i in rows])),
            }
        )
    out = pd.DataFrame(recs)
    out["paper_id"] = out["paper_id"].astype("string")
    for c in out.columns[1:]:
        if c != "code_min_comments":
            out[c] = out[c].astype("int64")
    out["code_min_comments"] = out["code_min_comments"].astype("float64")

    if "paper_id" in all_files.columns:
        # per paper, the files of an NA paper id (paper = None) as one paper
        # (R: split() drops them, so they are never pinned; UPSTREAM_ISSUES U78)
        af = all_files.reset_index(drop=True)
        by_paper: dict[str | None, list[int]] = {}
        for i, v in enumerate(af["paper_id"].tolist()):
            by_paper.setdefault(None if is_missing(v) else str(as_character(v)), []).append(i)
        pinned: dict[str | None, bool] = {}
        for pid, rows in by_paper.items():
            # the module's download caps and cache apply here too (R's
            # per-paper check falls back to the defaults, UPSTREAM_ISSUES U88)
            res = _code_version_pin_check(
                af.iloc[rows].reset_index(drop=True),
                code_text_list=dict(r_text_by_paper.get(pid) or {}),
                max_file_size=max_file_size,
                max_download_size=max_download_size,
                cache=cache,
                skip_on_api_limit=skip_on_api_limit,
                max_files_per_repo=max_files_per_repo,
            )
            pinned[pid] = bool(res.get("pinned") is True)
        out["code_version_pinned"] = [
            pinned.get(None if is_missing(p) else str(p), False) for p in out["paper_id"].tolist()
        ]
    else:
        out["code_version_pinned"] = bool(version_pin.get("pinned") is True)
    out["code_version_pinned"] = out["code_version_pinned"].astype(bool)
    return out


# ---------------------------------------------------------------------------
# version pinning report
# ---------------------------------------------------------------------------

_MECH_LABELS = {
    "renv.lock": "an `renv.lock` file",
    "sessionInfo": "a `sessionInfo()` record",
    "groundhog": "a `groundhog` date-pin",
    "checkpoint": "a `checkpoint` date-pin",
}

REPORT_NO_PIN = (
    "No `renv.lock` file, `sessionInfo()`/`session_info()` record, or "
    "`groundhog`/`checkpoint` date-pin call was found anywhere in the repository. Without "
    "one of these, the exact R and package versions used for the analysis are not "
    "recoverable, and package versions may drift between when this analysis was run and any "
    "later reproduction attempt. Consider depositing an `renv.lock` (via the `renv` package), "
    "a `sessionInfo()` text dump, or (for R) a `groundhog`/`checkpoint` date-pin alongside the "
    "code — or, for Python code, a `requirements.txt` (not currently checked for by this "
    "module, but still good practice to include)."
)


def version_pin_report(version_pin: Mapping[str, Any]) -> tuple[str, str, pd.DataFrame | None]:
    """The report text, summary line and table of ``code_check()``'s pinning section."""
    if not version_pin.get("pinned"):
        return (
            REPORT_NO_PIN,
            "No pinned R/package environment (renv.lock, sessionInfo(), or "
            "groundhog/checkpoint) was found.",
            None,
        )
    mechanisms = list(version_pin.get("mechanisms") or [])
    found_txt = ", ".join(_MECH_LABELS.get(m, "NA") for m in mechanisms)
    r_versions = [str(v) for v in version_pin.get("r_versions") or []]
    rv_txt = (
        f" Declared R version{plural(len(r_versions))}: {', '.join(r_versions)}."
        if r_versions
        else ""
    )
    renv_packages = version_pin.get("renv_packages")
    n_pkgs = 0 if renv_packages is None else len(renv_packages)
    renv_files = list(version_pin.get("renv_files") or [])
    renv_txt = (
        f" The `renv.lock` file{plural(len(renv_files))} lock"
        f"{'s' if len(renv_files) == 1 else ''} {n_pkgs} package version{plural(n_pkgs)}."
        if n_pkgs > 0
        else ""
    )
    report = f"The repository pins its analysis environment via {found_txt}.{rv_txt}{renv_txt}"
    summary = f"A pinned R/package environment was found ({', '.join(mechanisms)})."
    table = None
    if n_pkgs > 0:
        assert renv_packages is not None
        table = renv_packages.copy()
        table.columns = ["Lockfile", "Package", "Version", "Source"]
    return report, summary, table


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def _failed_rows(failed: pd.DataFrame | None) -> pd.DataFrame | None:
    """The failed downloads, one row per ``(repo_url, file_name)``."""
    if failed is None or len(failed) == 0:
        return failed
    key = list(
        zip(
            col(failed, "repo_url"),
            col(failed, "file_name"),
            strict=True,
        )
    )
    seen: set[Any] = set()
    keep = []
    for k in key:
        keep.append(k not in seen)
        seen.add(k)
    return failed.loc[keep].reset_index(drop=True)


def merge_manifests(
    manifest: str | os.PathLike[str],
    code_files: pd.DataFrame,
    paper: Any,
    all_packages: list[str],
    failed: pd.DataFrame | None,
) -> None:
    """Merge each paper's loaded packages and failed code downloads into its manifest.

    The ``manifest`` block of ``code_check()``: one ``<paper_id>.manifest.json``
    per paper (or the given ``*.json`` file), with ``code$packages``,
    ``code$files_failed`` and ``code$ddi_mapping`` (see ``manifest_merge()``).
    Errors writing a manifest are ignored, as in R.
    """
    from metacheck.codecheck.core import code_packages
    from metacheck.datacheck.files import manifest_merge

    failed = _failed_rows(failed)
    has_pid = "paper_id" in code_files.columns and len(code_files) > 0
    if has_pid:
        pids = list(dict.fromkeys(None if is_missing(v) else v for v in code_files["paper_id"]))
    else:
        from metacheck.papers.tables import paper_id

        ids: list[Any] = [] if paper is None else list(paper_id(paper))
        pids = ids or [getattr(paper, "paper_id", None)]
    packages = col(code_files, "packages")
    code_pids = col(code_files, "paper_id")
    has_failed = failed is not None and len(failed) > 0
    for pid in pids:
        if has_pid:
            rows = [i for i, v in enumerate(code_pids) if pid is not None and v == pid]
            pkgs = code_packages([packages[i] for i in rows])
        else:
            pkgs = list(all_packages)
        failed_i: pd.DataFrame | None
        if has_failed and failed is not None and "paper_id" in failed.columns:
            fp = col(failed, "paper_id")
            failed_i = failed.loc[[v == pid for v in fp]].reset_index(drop=True)
        elif failed is not None and len(pids) <= 1:
            failed_i = failed
        else:
            failed_i = None
        if not pkgs and (failed_i is None or len(failed_i) == 0):
            continue

        path = os.fspath(manifest)
        if not grepl(r"\.json$", path, ignore_case=True):
            label = pid if pid is not None and str(pid) != "" else "manifest"
            path = os.path.join(path, f"{label}.manifest.json")
        code: dict[str, Any] = {}
        if pkgs:
            code["packages"] = list(pkgs)
        if failed_i is not None and len(failed_i) > 0:
            code["files_failed"] = [
                {
                    k: _failed_cell(failed_i, k, i)
                    for k in ("file_name", "repo_url", "file_url", "error")
                }
                for i in range(len(failed_i))
            ]
        code["ddi_mapping"] = {
            "code.packages": "otherMat/software (loaded packages)",
            "code.files_failed": (
                "fileDscr/notes (code files whose download failed after retries)"
            ),
        }
        # R: tryCatch(manifest_merge(...), error = function(e) NULL)
        with contextlib.suppress(Exception):
            manifest_merge(path, {"code": code})


def _failed_cell(df: pd.DataFrame, name: str, i: int) -> Any:
    """``failed_i$<name>[i]``: ``NULL`` (R_NULL) for an absent column, ``NA`` as ``None``."""
    if name not in df.columns:
        from metacheck.datacheck.files import R_NULL

        return R_NULL
    v = df[name].iloc[i]
    return None if is_missing(v) else v
