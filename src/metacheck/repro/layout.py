"""Where the scripts and the files are: the static layout of a check.

Ports of the helpers of metacheck's ``R/reproducibility_check.R`` (and the few of
``R/code_check.R`` they need) that decide, before and after a run, where each script
and file lies in the repository as the authors organised it: the package layout's plan
(:func:`_repro_package_plan`), the references that work as written and those that are
pointed to the file (:func:`_repro_keep_working_refs`), the folder each script runs
from and where ``here()`` starts, the folders held in variables or listed by the code,
the names the scripts spell otherwise than the deposit, the files not downloaded, the
files copied to where a script looked for them, and the report sentences on each.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pandas as pd

from metacheck._r import (
    as_character,
    bind_rows,
    compile_r,
    grepl,
    gsub,
    plural,
    r_sorted,
    regextract_all,
    strsplit,
    sub,
    trimws,
)
from metacheck._values import field, is_missing
from metacheck.codecheck._rcoerce import r_unlist_chr
from metacheck.codecheck.core import _json_load
from metacheck.datacheck.files import _utf8_lines
from metacheck.report.blocks import scroll_table
from metacheck.repro.core import (
    _LFS_HEAD,
    _basename,
    _bs2fs,
    _chr_list,
    _code_remove_comments,
    _col,
    _copy_file,
    _dir_create,
    _file_path_sans_ext,
    _frame,
    _is_true,
    _na_str,
    _paste_lines,
    _portable_path,
    _r_dirname,
    _read_lines,
    _repro_md5,
    _repro_neutralise_env_calls,
    _repro_norm_rel,
    _repro_simple_string_vars,
    _setdiff,
    _split_path,
    _tolower,
    _unique,
    _write_lines,
)
from metacheck.repro.outcomes import _repro_mb, _repro_unopened_files

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

#: a path that starts at a drive, the root or the home folder: not in the package
_ABSOLUTE = re.compile(r"^([A-Za-z]:/|/|~)")
#: a path that goes up a folder
_GOES_UP = re.compile(r"(^|/)\.\.(/|$)")
#: the files that mark the top of a project for ``here()`` (lower case)
_PROJECT_MARK = r"(^|/)(\.here|[^/]*\.rproj|description|\.git)$"


def _chr_or_none(df: pd.DataFrame, name: str) -> list[str | None]:
    """A character column, or ``NA`` for every row when the table lacks it."""
    return _chr_list(df[name]) if name in df.columns else [None] * len(df)


def _is_true_col(df: pd.DataFrame, name: str) -> list[bool]:
    """``df[[name]] %in% TRUE``; all ``FALSE`` when the table lacks the column."""
    if name not in df.columns:
        return [False] * len(df)
    return [_is_true(v) for v in _col(df, name)]


def _nzchar(x: str | None) -> bool:
    """``!is.na(x) & nzchar(x)``."""
    return x is not None and x != ""


def _parent_folders(files: Sequence[str | None]) -> list[str]:
    """Every folder above the *files*, nearest first (each once, ``"."`` left out).

    R: ``d <- dirname(files); while (length(d <- setdiff(d[d != "."], folders))) {
    folders <- c(folders, d); d <- dirname(d) }``.
    """
    folders: list[str] = []
    d = [_r_dirname(f) for f in files if f is not None]
    while True:
        d = _setdiff([x for x in d if x != "."], folders)
        if not d:
            return folders
        folders.extend(cast(list[str], d))
        d = [_r_dirname(x) for x in d]


def _strip_comments(lines: Sequence[str | None], lang: str) -> list[str | None]:
    """``tryCatch(code_remove_comments(lines, lang), error = function(e) lines)``."""
    try:
        return _code_remove_comments(lines, lang)
    except Exception:
        return list(lines)


def _plan_targets(plan: pd.DataFrame | None) -> list[str]:
    """The plan's ``target_path`` without ``NA``, with ``/`` between folders."""
    if plan is None or len(plan) == 0 or "target_path" not in plan.columns:
        return []
    return [cast(str, _bs2fs(t)) for t in _chr_list(plan["target_path"]) if t is not None]


def _rewrite_columns(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Rows of a ``repro_rewrite_paths()`` result that also have ``as_written``."""
    return _frame(
        {
            "ref": ("string", [r["ref"] for r in rows]),
            "basename": ("string", [r["basename"] for r in rows]),
            "matched": ("boolean", [r["matched"] for r in rows]),
            "target": ("string", [r["target"] for r in rows]),
            "ambiguous": ("boolean", [r["ambiguous"] for r in rows]),
            "n_candidates": ("Int64", [r["n_candidates"] for r in rows]),
            "is_call": ("boolean", [r["is_call"] for r in rows]),
            "name_in_code": ("string", [r["name_in_code"] for r in rows]),
            "name_in_deposit": ("string", [r["name_in_deposit"] for r in rows]),
            "as_written": ("boolean", [r["as_written"] for r in rows]),
        }
    )


def _folder_row(ref: str, target: str) -> dict[str, Any]:
    """A rewrite row for a folder: found, not ambiguous, not yet written."""
    return {
        "ref": ref,
        "basename": _basename(cast(str, _bs2fs(ref))),
        "matched": True,
        "target": target,
        "ambiguous": False,
        "n_candidates": 1,
        "is_call": False,
        "name_in_code": None,
        "name_in_deposit": None,
        "as_written": False,
    }


# ---------------------------------------------------------------------------
# R/code_check.R: renv's own files, and a notebook's code
# ---------------------------------------------------------------------------


def _in_folder(files: pd.DataFrame, folder: str) -> list[bool]:
    """Port of ``R/code_check.R::.in_folder()``.

    Whether each file is in a folder of this name, at any depth. The folder is in
    ``file_path``; ``file_name`` is used only for a row without a path.
    """
    n = len(files)
    if n == 0:
        return []
    path = _chr_or_none(files, "file_path")
    name = _chr_or_none(files, "file_name")
    p = [name[i] if not _nzchar(path[i]) else path[i] for i in range(n)]
    return cast(list[bool], grepl(f"(^|/){folder}/", [_bs2fs(x) for x in p], ignore_case=True))


def _in_renv_folder(files: pd.DataFrame) -> list[bool]:
    """Port of ``R/code_check.R::.in_renv_folder()``.

    Files inside a ``renv/`` folder were written by the renv package, not by the
    authors: ``renv/activate.R`` (run by the project's ``.Rprofile`` to set up its
    package library) and, when the folder is shared in full, ``renv/library/`` with the
    R files of every installed package. code_check and reproducibility_check leave them
    out, so they are not counted, checked or run as the paper's code (#548).
    ``renv.lock`` sits next to the ``renv/`` folder, not in it, so it is still found as
    a version pin.

    The folder is in ``file_path`` (also for a local folder and a ``.zip`` member:
    ``project.zip/renv/activate.R``); ``file_name`` holds the base name only, so it is
    used just when a row has no ``file_path``. ``True`` per row of *files* (a
    code_check/repo_check table).
    """
    return _in_folder(files, "renv")


def _code_notebook_cell(lines: Sequence[str | None]) -> dict[str, list[str]]:
    """Port of ``R/code_check.R::.code_notebook_cell()``.

    One notebook cell of a non-R kernel without its commands. A cell that starts with
    ``%%bash`` and the like is not Python at all, so the whole cell goes; ``%%time`` and
    the like only wrap Python. An indented command becomes ``pass``, since removing the
    only line of an ``if`` block would leave code that does not parse. Also removed: the
    commands IPython accepts without a ``%``: ``cd folder``, and ``name?`` for help.
    Returns ``{"lines": ..., "removed": ...}``.
    """
    ls = list(lines)
    nonblank = [x for x in ls if _nzchar(trimws(x))]
    first = nonblank[0] if nonblank else None
    if (
        first is not None
        and grepl(r"^\s*%%", first)
        and not grepl(r"^\s*%%(time|timeit|capture|prun)\b", first)
    ):
        return {"lines": [], "removed": [trimws(first)]}
    magic = grepl(r"^\s*[%!?]", ls)
    cd = grepl(r"^\s*cd(\s+[^=(\s].*)?$", ls, perl=True)
    help_ = grepl(r"^\s*[A-Za-z_][A-Za-z0-9_.]*\?{1,2}\s*$", ls)
    is_cmd = [bool(a or b or c) for a, b, c in zip(magic, cd, help_, strict=True)]
    indented = [c and bool(grepl(r"^\s", x)) for c, x in zip(is_cmd, ls, strict=True)]
    removed = [trimws(x) for x, c in zip(ls, is_cmd, strict=True) if c]
    kept: list[str] = []
    for x, c, ind in zip(ls, is_cmd, indented, strict=True):
        if ind:
            kept.append(cast(str, sub(r"^(\s*).*$", r"\1pass", x)))
        elif not c:
            kept.append(cast(str, x))
    return {"lines": kept, "removed": removed}


def _code_notebook_parts(text: str | Sequence[str | None]) -> dict[str, list[str]]:
    """Port of ``R/code_check.R::.code_notebook_parts()``.

    A notebook's code, and the commands left out of it, from its JSON text. Commands
    are IPython magics (``%cd ..``) and shell escapes (``!pip install x``), which are
    not Python and would otherwise be scanned as though they were. An R-kernel
    notebook has no such commands, and ``  %>% filter()`` and ``!x`` are code there.
    Returns ``{"lines": ..., "removed": ...}``; each code cell is followed by a blank
    line that marks the cell boundary.
    """
    raw = [text] if isinstance(text, str) else list(text)
    nb = _json_load(_paste_lines(raw))
    cells = field(nb, "cells")
    if isinstance(cells, dict):
        cells = list(cells.values())
    if not isinstance(cells, list) or not cells:
        return {"lines": [], "removed": []}
    kernel = field(nb, "metadata", "kernelspec")
    language = field(kernel, "language")
    kname = field(kernel, "name")
    language = language.lower() if isinstance(language, str) else ""
    kname = kname.lower() if isinstance(kname, str) else ""
    is_r = language == "r" or kname in ("ir", "r")

    lines: list[str] = []
    removed: list[str] = []
    for cl in cells:
        if field(cl, "cell_type") != "code":
            continue
        # `source` is normally an array of lines (each keeping its trailing "\n"),
        # but the schema also permits a single string -- handle both.
        s = field(cl, "source")
        if s is None or (isinstance(s, list | dict) and not s):
            continue
        cell_lines = [
            x for x in strsplit("".join(r_unlist_chr(s)), "\n", fixed=True) if x is not None
        ]
        part = {"lines": cell_lines, "removed": []} if is_r else _code_notebook_cell(cell_lines)
        lines.extend(part["lines"])
        if part["lines"]:
            lines.append("")  # blank line marks the cell boundary
        removed.extend(part["removed"])
    return {"lines": lines, "removed": removed}


# ---------------------------------------------------------------------------
# names the scripts spell otherwise than the deposit
# ---------------------------------------------------------------------------


def _repro_name_corrections(rewrite_list: Mapping[str, pd.DataFrame | None]) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_name_corrections()``.

    The names a script reads under another spelling than the deposit's, one row per
    name, from :func:`~metacheck.repro.core.repro_rewrite_paths` results (a mapping, by
    script). Columns ``file_name``, ``name_in_code`` and ``name_in_deposit``.
    """
    scripts: list[str | None] = []
    in_code: list[str | None] = []
    in_deposit: list[str | None] = []
    for fn, d in rewrite_list.items():
        if d is None or len(d) == 0 or "name_in_deposit" not in d.columns:
            continue
        seen: set[tuple[str | None, str | None]] = set()
        for a, b in zip(
            _chr_or_none(d, "name_in_code"), _chr_list(d["name_in_deposit"]), strict=True
        ):
            if b is None or (a, b) in seen:
                continue
            seen.add((a, b))
            scripts.append(fn)
            in_code.append(a)
            in_deposit.append(b)
    return _frame(
        {
            "file_name": ("string", scripts),
            "name_in_code": ("string", in_code),
            "name_in_deposit": ("string", in_deposit),
        }
    )


def _repro_names_corrected(
    corrections: pd.DataFrame, file_names: Sequence[str | None]
) -> list[str | None]:
    """Port of ``R/reproducibility_check.R::.repro_names_corrected()``.

    For ``table``: a script's corrected names as text, ``None`` when there are none.
    """
    scripts = _chr_list(corrections["file_name"])
    a = _chr_list(corrections["name_in_code"])
    b = _chr_list(corrections["name_in_deposit"])
    out: list[str | None] = []
    for fn in file_names:
        hit = [i for i, s in enumerate(scripts) if s == fn]
        out.append("; ".join(f"{_na_str(a[i])} -> {_na_str(b[i])}" for i in hit) if hit else None)
    return out


def _repro_name_report(corrections: pd.DataFrame, execute: bool = False) -> list[Any] | None:
    """Port of ``R/reproducibility_check.R::.repro_name_report()``.

    The report's note on these names, so authors can change their code: a sentence and
    a table; ``None`` if there are none.
    """
    n = len(corrections)
    if n == 0:
        return None
    text = (
        f"**{n} file name{plural(n)} in the code {plural(n, 'is', 'are')} not the same as in "
        "the deposit**, in case, spaces or punctuation, and exactly one file in the deposit "
        f"fits. {'To run the code we corrected' if _is_true(execute) else 'To run the code we would correct'} "
        "this in a copy of the script so that it finds the file. The script itself "
        "should be changed, because as written it does not find the file on every "
        "system."
    )
    table = _frame(
        {
            "File": ("string", _chr_list(corrections["file_name"])),
            "Name in the code": ("string", _chr_list(corrections["name_in_code"])),
            "Name in the deposit": ("string", _chr_list(corrections["name_in_deposit"])),
        }
    )
    return [text, scroll_table(table, maxrows=10)]


def _repro_path_fix_text(
    rewrite_list: Mapping[str, pd.DataFrame | None], execute: bool = False
) -> str:
    """Port of ``R/reproducibility_check.R::.repro_path_fix_text()``.

    The report's sentence on the paths that do not work in the repository as deposited
    and were pointed to the file (#568), so authors can change their code: an absolute
    path on their computer, a path from another folder, or a file that is elsewhere in
    the repository (see :func:`_repro_keep_working_refs`). One string, since the module
    adds sentences to it. *rewrite_list* holds a ``repro_rewrite_paths()`` result per
    script, named by script.
    """
    fixes: list[tuple[str | None, str | None, str | None]] = []
    for f, d in rewrite_list.items():
        if d is None or len(d) == 0:
            continue
        # a table without `as_written` has no path that was pointed anywhere (R: the
        # missing column makes the whole condition empty)
        if "as_written" not in d.columns:
            continue
        matched = _is_true_col(d, "matched")
        ambiguous = _is_true_col(d, "ambiguous")
        written = _is_true_col(d, "as_written")
        ref = _chr_or_none(d, "ref")
        target = _chr_or_none(d, "target")
        fixes.extend(
            (f, ref[i], target[i])
            for i in range(len(d))
            if matched[i]
            and not ambiguous[i]
            and not written[i]
            and target[i] is not None
            and ref[i] is not None
            and target[i] != ref[i]
        )
    n = len(fixes)
    if n == 0:
        return "Every data path in the code works in the repository as deposited."
    shown = fixes[:10]
    listed = "; ".join(f"`{r}` to `{t}` (in {f})" for f, r, t in shown)
    return (
        f"**{n} data path{plural(n)} in the code {plural(n, 'does', 'do')} not work in the "
        "repository as deposited.** "
        f"{'We pointed' if _is_true(execute) else 'We would point'} {plural(n, 'it', 'them')} "
        f"to where the file is, so the code could run: {listed}"
        f"{f', and {n - 10} more' if n > 10 else ''}. "
        f"The code should be changed, because as written it does not find "
        f"{plural(n, 'the file', 'these files')}."
    )


# ---------------------------------------------------------------------------
# files the repository lists but that were not downloaded
# ---------------------------------------------------------------------------


def _repro_fetchable(structure_df: pd.DataFrame | None) -> list[bool]:
    """Port of ``R/reproducibility_check.R::.repro_fetchable()``.

    The rows of a repository listing (data_check's ``structure``) that are not on disk
    but can be fetched: a file address, or an archive and the member in it.
    """
    if structure_df is None or len(structure_df) == 0:
        return []
    loc = _chr_or_none(structure_df, "file_location")
    url = _chr_or_none(structure_df, "file_url")
    arc = _chr_or_none(structure_df, "archive_url")
    mem = _chr_or_none(structure_df, "archive_member")
    return [
        (not _nzchar(loc[i])) and (_nzchar(url[i]) or (_nzchar(arc[i]) and mem[i] is not None))
        for i in range(len(structure_df))
    ]


def _repro_named_files(
    structure_df: pd.DataFrame | None, code_text: str | Sequence[str | None] | None
) -> list[bool]:
    """Port of ``R/reproducibility_check.R::.repro_named_files()``.

    Before a run: the files the repository lists, but that were not downloaded, which
    the code names, by file name, or by extension in a pattern such as
    ``list.files(pattern = "\\.wav$")`` or ``glob("*.tif")``. The first download leaves
    out images, sound, video and documents from hosts that send files one by one; a
    script that reads one gets it here (#568).
    """
    want = _repro_fetchable(structure_df)
    code = _chr_list(code_text)
    if not any(want) or not code or structure_df is None:
        return want
    # bytes that are not UTF-8 stopped tolower() for the whole paper (#568)
    txt = _paste_lines(_utf8_lines(code)).lower()
    base = [
        _tolower(_basename(_bs2fs(x))) for x in _utf8_lines(_chr_list(structure_df["file_name"]))
    ]
    ext = [sub(r".*\.", "", b) if b is not None and grepl(r"\.", b) else "" for b in base]
    # Only the files still to fetch are looked for, each name once, among the words
    # of the code: searching all of the code for every listed file took more than 4
    # minutes in a software repository (#568). A name with other characters than these
    # is still searched for in the text.
    cand = _unique(b for b, w in zip(base, want, strict=True) if w)
    words = set(strsplit(txt, "[^a-z0-9._+-]+"))
    found = {
        b
        for b in cand
        if b is not None
        and (b in words if grepl(r"^[a-z0-9._+-]+$", b) else b in txt)  # fixed=TRUE
    }
    exts = _unique(e for e, w in zip(ext, want, strict=True) if w and e)
    ext_named = {
        e
        for e in exts
        if grepl(r"""(\\{1,2}\.|\*\.|['"]\.)""" + cast(str, e) + r"\b", txt, perl=True)
    }
    return [w and (b in found or e in ext_named) for b, e, w in zip(base, ext, want, strict=True)]


def _repro_fetch_files(
    structure_df: pd.DataFrame,
    rows: Sequence[bool],
    max_file_size: float = 100,
    max_download_size: float = 500,
    cache: bool = False,
    skip_on_api_limit: bool = False,
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_fetch_files()``.

    Fetch rows *rows* of a repository listing with
    :func:`metacheck.archives.download.download_repo_files`, within the module's own
    size limits; returns the listing with the locations filled in. A download that
    fails leaves the listing as it was.
    """
    if not any(rows):
        return structure_df
    from metacheck.archives import download

    mask = [bool(r) for r in rows]
    counts: dict[str, float] = {}
    if "repo_url" in structure_df.columns:
        for u in _chr_list(structure_df["repo_url"]):
            if u is not None:
                counts[u] = counts.get(u, 0) + 1
    try:
        dl = download.download_repo_files(
            structure_df.loc[mask],
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            repo_file_counts=counts,
            cache=cache,
            skip_on_api_limit=skip_on_api_limit,
        )
    except Exception:
        dl = None
    if dl is None:
        return structure_df
    out = structure_df.copy()
    loc = _chr_or_none(out, "file_location")
    got = _chr_list(dl["file_location"])
    pos = [i for i, m in enumerate(mask) if m]
    for k, i in enumerate(pos):
        loc[i] = got[k]
    out["file_location"] = pd.Series(loc, index=out.index, dtype="string")
    return out


def _repro_not_downloaded(
    run_results: pd.DataFrame | None, structure_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_not_downloaded()``.

    The scripts that stopped on a file the repository lists but that was not
    downloaded, such as one over the size limit: one row per script and file (#568).
    Such a script was not tried, so it says nothing about the code. A file is matched by
    its name and the folder above it, as far as the error gives them; when a downloaded
    file matches as well, the script may have looked in the wrong place, and it stays a
    failure. Columns ``script``, ``file`` and ``size``.
    """

    def result(
        scripts: Sequence[str | None], files: Sequence[str | None], sizes: Sequence[float | None]
    ) -> pd.DataFrame:
        return _frame(
            {
                "script": ("string", scripts),
                "file": ("string", files),
                "size": ("Float64", sizes),
            }
        )

    none = result([], [], [])
    if (
        run_results is None
        or len(run_results) == 0
        or structure_df is None
        or len(structure_df) == 0
    ):
        return none
    n = len(structure_df)
    loc = _chr_or_none(structure_df, "file_location")
    absent = [not _nzchar(x) for x in loc]
    if not any(absent):
        return none
    names = _utf8_lines(_chr_or_none(structure_df, "file_name"))
    listed = _utf8_lines(
        _chr_list(structure_df["file_path"]) if "file_path" in structure_df.columns else names
    )
    listed = [names[i] if listed[i] is None else listed[i] for i in range(n)]

    def parts(p: str | None) -> list[str]:
        if p is None:
            return []
        return [x for x in p.lower().split("/") if x and x not in (".", "..")]

    listed_parts = [parts(_bs2fs(p)) for p in listed]
    sizes: list[float | None] = []
    for v in _col(structure_df, "file_size") if "file_size" in structure_df.columns else [None] * n:
        try:
            sizes.append(None if v is None else float(v))
        except (TypeError, ValueError):
            sizes.append(None)

    outcome = _chr_or_none(run_results, "outcome")
    script = _chr_or_none(run_results, "file_name")
    error = _chr_or_none(run_results, "error")
    stderr = _chr_or_none(run_results, "stderr")
    out_script: list[str | None] = []
    out_file: list[str | None] = []
    out_size: list[float | None] = []
    for i in (k for k, o in enumerate(outcome) if o == "errored"):
        unopened = _repro_unopened_files(f"{_na_str(error[i])} {_na_str(stderr[i])}", full=True)
        hits: list[int] = []
        for u in unopened:
            up = parts(u)[-2:]
            if not up:
                continue
            m = [len(lp) >= len(up) and lp[-len(up) :] == up for lp in listed_parts]
            if any(a and not ab for a, ab in zip(m, absent, strict=True)):
                continue
            hits.extend(j for j in range(n) if m[j] and absent[j])
        for j in _unique(hits):
            out_script.append(script[i])
            out_file.append(listed[j])
            out_size.append(sizes[j])
    return result(out_script, out_file, out_size)


def _repro_not_downloaded_text(nd: pd.DataFrame | None, max_file_size: float = 100) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_not_downloaded_text()``.

    The report's sentence on scripts that could not be tried, naming each file and, when
    it was over the limit, its size. ``None`` when there are none.
    """
    if nd is None or len(nd) == 0:
        return None
    scripts = _chr_list(nd["script"])
    n = len(_unique(scripts))
    all_files = _chr_list(nd["file"])
    all_sizes = _col(nd, "size")
    seen: set[str | None] = set()
    files: list[str | None] = []
    sizes: list[Any] = []
    for f, s in zip(all_files, all_sizes, strict=True):
        if f in seen:
            continue
        seen.add(f)
        files.append(f)
        sizes.append(s)
    too_large = [s is not None and s > max_file_size * 1024**2 for s in sizes]
    desc = [
        f"{f} ({_repro_mb(s)} MB)" if big else _na_str(f)
        for f, s, big in zip(files, sizes, too_large, strict=True)
    ]
    limit = f"; files over {as_character(max_file_size)} MB are not downloaded"
    return (
        f" **{n} script{plural(n)} could not be tried, because a file {plural(n, 'it', 'they')} "
        f"need{plural(n, 's', '')} was not downloaded**: "
        f"{', '.join(_na_str(s) for s in _unique(scripts))}. "
        f"{plural(n, 'It is', 'They are')} not counted as {plural(n, 'a script', 'scripts')} that "
        f"did not reproduce. The file{plural(len(files))}: {', '.join(desc)}"
        f"{limit if any(too_large) else ''}."
    )


# ---------------------------------------------------------------------------
# files written during a run, copied to where a script looked
# ---------------------------------------------------------------------------


def _list_files(top: str) -> list[str]:
    """``list.files(top, recursive = TRUE)``: the files below *top*, hidden ones left out,
    with ``/`` between folders as in R (also on Windows)."""
    found: list[str] = []
    for d, subdirs, names in os.walk(top, followlinks=True):
        subdirs[:] = [s for s in subdirs if not s.startswith(".")]
        rel = os.path.relpath(d, top).replace(os.sep, "/")
        found.extend(nm if rel == "." else f"{rel}/{nm}" for nm in names if not nm.startswith("."))
    return cast(list[str], r_sorted(found))


def _repro_sandbox_names(root: str | os.PathLike[str]) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_sandbox_names()``.

    The paths of the files in the sandbox, to see later which files the scripts wrote.
    Installed packages are not counted. Hidden files and folders are not listed
    (``list.files()`` default).
    """
    # _lib is the R library (see the module), in the sandbox outside Docker
    return [f for f in _list_files(os.fspath(root)) if not grepl(r"^(_r?lib|_pylib2?)/", f)]


def _none_placed() -> pd.DataFrame:
    """The copies of nothing: ``script``, ``file``, ``from`` and ``to``."""
    return _placed_frame([])


def _placed_frame(rows: Sequence[tuple[str, str, str, str]]) -> pd.DataFrame:
    return _frame(
        {
            "script": ("string", [r[0] for r in rows]),
            "file": ("string", [r[1] for r in rows]),
            "from": ("string", [r[2] for r in rows]),
            "to": ("string", [r[3] for r in rows]),
        }
    )


def _repro_place_files(
    results: pd.DataFrame | None,
    run_tbl: pd.DataFrame | None,
    root: str | os.PathLike[str],
    pool: Sequence[str],
    wanted: Mapping[str, Sequence[str | None]] | None = None,
    same_content: bool = False,
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_place_files()``.

    The copies of :func:`_repro_place_written` and :func:`_repro_place_from_deposit`:
    for each script that stopped on a file (only the files in ``wanted[script]`` when
    that is given), the one file of *pool* (paths from the top of *root*) with its name
    is copied to the path the error gives, counted from the folder the script runs in.
    Several files of that name count as one when *same_content* and they are identical.
    A path on the authors' computer, or one outside the sandbox, gets no copy. Returns
    ``script``, ``file``, ``from`` and ``to`` (from the top).
    """
    if results is None or len(results) == 0 or len(pool) == 0 or run_tbl is None:
        return _none_placed()
    root_s = os.fspath(root)
    root_n = cast(str, _bs2fs(os.path.realpath(root_s)))

    def rel_dir(path: str | None) -> str | None:
        """An existing folder, from the top of the sandbox (``"."`` for the top)."""
        if path is None:
            return None
        p = cast(str, _bs2fs(os.path.realpath(path)))
        if p.lower() == root_n.lower():
            return "."
        if not p.lower().startswith(root_n.lower() + "/"):
            return None
        return p[len(root_n) + 1 :]

    pool_base = [(_basename(p) or "").lower() for p in pool]
    r_name = _chr_or_none(results, "file_name")
    r_outcome = _chr_or_none(results, "outcome")
    r_error = _chr_or_none(results, "error")
    r_stderr = _chr_or_none(results, "stderr")
    t_name = _chr_or_none(run_tbl, "file_name")
    t_dir = _chr_or_none(run_tbl, "run_dir")
    scripts: list[str | None] = (
        [s for s, o in zip(r_name, r_outcome, strict=True) if o == "errored"]
        if wanted is None
        else list(wanted)
    )
    rows: list[tuple[str, str, str, str]] = []
    for s in scripts:
        if s is None or s not in r_name or s not in t_name:
            continue
        i = r_name.index(s)
        k = t_name.index(s)
        paths = _repro_unopened_files(f"{_na_str(r_error[i])} {_na_str(r_stderr[i])}", full=True)
        if wanted is not None:
            names = {(x or "").lower() for x in wanted[s]}
            paths = [p for p in paths if (_basename(p) or "").lower() in names]
        run_dir = rel_dir(t_dir[k])
        for p in _unique(paths):
            # the path from the top of the sandbox; None for one on the authors' own
            # computer, or one that leaves the sandbox
            to: str | None
            if p.startswith("/sandbox/"):
                to = _repro_norm_rel(re.sub("^/sandbox/", "", p))
            elif re.match(r"^(/|~|[A-Za-z]:)", p) or run_dir is None:
                to = None
            else:
                to = _repro_norm_rel(p if run_dir == "." else f"{run_dir}/{p}")
            if not to:
                continue
            dest = f"{root_s}/{to}"
            if os.path.exists(dest):
                continue
            base = (_basename(p) or "").lower()
            cand = [
                q
                for q, qb in zip(pool, pool_base, strict=True)
                if qb == base and os.path.exists(f"{root_s}/{q}")
            ]
            if (
                len(cand) > 1
                and same_content
                and len(set(_repro_md5([f"{root_s}/{q}" for q in cand]))) == 1
            ):
                cand = cand[:1]
            if len(cand) != 1:
                continue
            _dir_create(os.path.dirname(dest))
            if _copy_file(f"{root_s}/{cand[0]}", dest):
                rows.append((s, cast(str, _basename(p)), cand[0], to))
    return _placed_frame(rows)


def _repro_place_written(
    later: Mapping[str, Sequence[str | None]],
    results: pd.DataFrame | None,
    run_tbl: pd.DataFrame | None,
    root: str | os.PathLike[str],
    before: Sequence[str],
    after: Sequence[str],
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_place_written()``.

    Before a script runs once more (see :func:`_repro_written_later`): a file it looked
    for in vain, which another script wrote during the run in another folder, is copied
    to where it looked. Authors often run every script from one folder, so a file saved
    by one is where the next looks; here each script runs from its own folder (#568).
    Only when exactly one file of that name was written during the run. Returns the
    copies: ``script``, ``file``, ``from``, ``to`` (from the top).
    """
    if not later:
        return _none_placed()
    return _repro_place_files(results, run_tbl, root, _setdiff(after, before), later)


def _repro_place_from_deposit(
    results: pd.DataFrame | None,
    run_tbl: pd.DataFrame | None,
    root: str | os.PathLike[str],
    before: Sequence[str],
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_place_from_deposit()``.

    Before a script runs once more: a file it looked for in vain, which the deposit
    holds in another folder, is copied to where it looked. A path built while the
    script runs cannot be rewritten: ``file.path(local.directory, "data")`` gave
    ``./data/HMSC_YXdata.csv``, while Dryad, which keeps no folders, has the file at
    the top (#575, item 2). Only when one file of the deposit has that name, or all that
    do have the same content. *before* holds the sandbox's files before the run
    (:func:`_repro_sandbox_names`). Returns the copies: ``script``, ``file``, ``from``,
    ``to`` (from the top).
    """
    if results is None or len(results) == 0 or len(before) == 0:
        return _none_placed()
    return _repro_place_files(results, run_tbl, root, before, None, same_content=True)


_QUOTE = "['\"`\u2018\u2019]"
_NOT_QUOTE = "[^'\"`\u2018\u2019\n]+"
_WRITES_R = (
    "[\"'](?:w|a|wb|ab|w\\+|a\\+)[\"']|"
    "\\b(?:pdf|png|jpeg|jpg|tiff|bmp|svg|svglite|cairo_pdf|cairo_ps|postscript|sink|save|"
    "saveRDS|ggsave|writeLines|writeBin|dev\\.copy|dev\\.print)\\s*\\(|"
    "\\bwrite[A-Za-z_.]*\\s*\\(|\\bsaveWorkbook\\s*\\("
)
_WRITES_PY = (
    "\\bopen\\s*\\([^)]*,\\s*(?:mode\\s*=\\s*)?[\"'][^\"']*[wax]|"
    "\\.(?:savefig|to_csv|to_excel|to_parquet|to_pickle|to_json|to_feather|to_html|"
    "to_latex|to_netcdf|write|save)\\s*\\(|\\bnp\\.(?:save|savez|savetxt)\\s*\\(|"
    "\\b(?:savefig|imsave|imwrite)\\s*\\("
)


def _repro_write_failed_folders(msg: str | None) -> list[str]:
    """Port of R ``.repro_write_failed_folders()`` (#591).

    The folders a script's write stopped on, from its error. A script writes into a
    folder that is not in the deposit (authors leave empty output folders out), and the
    check could make it before the run only when it could see the path (a quoted path, or
    a folder in a variable of the same script), not when the path was built elsewhere: in
    a function of a sourced file (``cannot create file './outputs/...xlsx'``) or inside
    ``here()`` (ggsave's ``Cannot find directory '/sandbox/.../5_figs/final'``). Only a
    message of a write counts: "cannot create file", ggsave's "Cannot find directory",
    "cannot open (compressed) file" when the call that failed writes (a mode ``"w"``,
    ``pdf()``, ``saveRDS()``, ``write.csv()``, ...), and Python's ``FileNotFoundError``
    when the line it names writes (``open(..., "w")``, ``savefig()``, ``to_csv()``, ...).
    A file a script reads but does not find gives a message too, and is not a folder to
    make. Returns the folders as the message gives them.
    """
    if is_missing(msg) or not msg:
        return []
    text = cast(str, msg)

    def grab(prefix: str) -> list[str]:
        return [m.group(1) for m in re.finditer(prefix + _QUOTE + f"({_NOT_QUOTE})" + _QUOTE, text)]

    out = [_r_dirname(x) or "" for x in grab("cannot create file ")]
    out += grab("Cannot find directory ")
    calls = re.findall(r"Error in [^\n]*", text)
    if any(re.search(_WRITES_R, c) for c in calls):
        out += [_r_dirname(x) or "" for x in grab("cannot open (?:compressed )?file ")]
    if "FileNotFoundError" in text:
        lines = [ln.strip() for ln in text.split("\n")]
        code = [
            ln
            for ln in lines
            if ln and not re.match(r"^(File |Traceback|FileNotFoundError|\^|~)", ln)
        ]
        if code and re.search(_WRITES_PY, code[-1]):
            out += [_r_dirname(x) or "" for x in grab("No such file or directory: ")]
    out = [x.replace("\\", "/") for x in out]
    return _unique(x for x in out if x and x not in (".", "/"))


def _repro_make_failed_write_folders(
    results: pd.DataFrame | None,
    run_tbl: pd.DataFrame | None,
    root: str | os.PathLike[str],
    seen: Mapping[str, Sequence[str]] | None = None,
) -> pd.DataFrame:
    """Port of R ``.repro_make_failed_write_folders()`` (#591).

    Before a script runs once more: the folders its writes stopped on are made, inside
    the sandbox only (see :func:`_repro_write_failed_folders`), counted from the folder
    the script runs in; a folder on the authors' computer is left. *seen* holds the
    folders made for each script before, which are not made again. Returns the folders
    made: ``script`` and ``folder`` (from the top of the sandbox).
    """
    none = _frame({"script": ("string", []), "folder": ("string", [])})
    if results is None or len(results) == 0 or run_tbl is None:
        return none
    seen = seen or {}
    root_s = os.fspath(root)
    root_n = cast(str, _bs2fs(os.path.realpath(root_s)))

    def rel_dir(path: str | None) -> str | None:
        if path is None:
            return None
        p = cast(str, _bs2fs(os.path.realpath(path)))
        if p.lower() == root_n.lower():
            return "."
        if not p.lower().startswith(root_n.lower() + "/"):
            return None
        return p[len(root_n) + 1 :]

    r_name = _chr_or_none(results, "file_name")
    r_outcome = _chr_or_none(results, "outcome")
    r_error = _chr_or_none(results, "error")
    r_stderr = _chr_or_none(results, "stderr")
    t_name = _chr_or_none(run_tbl, "file_name")
    t_dir = _chr_or_none(run_tbl, "run_dir")
    rows: list[tuple[str, str]] = []
    for s in [n for n, o in zip(r_name, r_outcome, strict=True) if o == "errored"]:
        if s is None or s not in r_name or s not in t_name:
            continue
        i = r_name.index(s)
        k = t_name.index(s)
        run_dir = rel_dir(t_dir[k])
        for d in _repro_write_failed_folders(f"{_na_str(r_error[i])} {_na_str(r_stderr[i])}"):
            to: str | None
            if d.startswith("/sandbox/"):
                to = _repro_norm_rel(re.sub("^/sandbox/", "", d))
            elif d.lower().startswith(root_n.lower() + "/"):
                to = rel_dir(d)
            elif re.match(r"^(/|~|[A-Za-z]:)", d) or run_dir is None:
                to = None
            else:
                to = _repro_norm_rel(d if run_dir == "." else f"{run_dir}/{d}")
            if not to or to == "." or to in list(seen.get(s, [])):
                continue
            dest = f"{root_s}/{_portable_path(to)}"
            if os.path.isdir(dest):
                continue
            try:
                os.makedirs(dest, exist_ok=True)
            except OSError:
                continue
            rows.append((s, to))
    return _frame(
        {
            "script": ("string", [r[0] for r in rows]),
            "folder": ("string", [r[1] for r in rows]),
        }
    )


def _repro_folders_made_text(made: pd.DataFrame | None) -> str:
    """Port of R ``.repro_folders_made_text()`` (#591).

    The report's sentence on the folders made for scripts whose writes stopped on them
    (see :func:`_repro_make_failed_write_folders`); ``""`` when there are none.
    """
    if made is None or len(made) == 0:
        return ""
    scripts = _chr_list(made["script"])
    n_s = len(_unique(scripts))
    n = len(made)
    listed = "; ".join(
        f"`{f}` for `{s}`" for f, s in zip(_chr_list(made["folder"]), scripts, strict=True)
    )
    return (
        f" **{plural(n_s, 'A script', f'{n_s} scripts')} wrote into "
        f"{plural(n, 'a folder', f'{n} folders')} that the deposit does not have**, so we made "
        f"{plural(n, 'it', 'them')} and ran {plural(n_s, 'it', 'them')} once more: {listed}. "
        "Keeping the (empty) output folders in the deposit, or making them in the code with "
        "`dir.create()`, solves this."
    )


def _repro_placed_text(placed: pd.DataFrame | None) -> str:
    """Port of ``R/reproducibility_check.R::.repro_placed_text()``.

    The report's sentence on the files of the deposit copied to where a script looked
    for them (see :func:`_repro_place_from_deposit`); ``""`` when there are none.
    """
    if placed is None or len(placed) == 0:
        return ""
    n = len(placed)
    ns = len(_unique(_chr_list(placed["script"])))
    copies = "; ".join(
        f"`{f}` to `{t}` for `{s}`"
        for f, t, s in zip(
            _chr_list(placed["from"]),
            _chr_list(placed["to"]),
            _chr_list(placed["script"]),
            strict=True,
        )
    )
    return (
        f" **{plural(ns, 'A script', f'{ns} scripts')} looked for {plural(n, 'a file', f'{n} files')} "
        f"in another folder than the deposit has {plural(n, 'it', 'them')}**, so before running "
        f"{plural(ns, 'it', 'them')} once more we copied {plural(n, 'it', 'them')} there: {copies}. "
        "Building paths from the top of the project (as with `here::here()`), or keeping the "
        "folders the code expects, solves this."
    )


def _repro_written_later(
    results: pd.DataFrame | None, before: Sequence[str], after: Sequence[str]
) -> dict[str, list[str]]:
    """Port of ``R/reproducibility_check.R::.repro_written_later()``.

    After a run: the scripts that stopped on a file of a name a script wrote at a path
    that was not in the sandbox before the run (*before* and *after*, from
    :func:`_repro_sandbox_names`). A name built while a script runs, such as
    ``f"input{i}.txt"``, cannot be seen before, so the run order missed it (#568).
    Paths, not names, are compared, since the deposit can hold a file of the same name
    elsewhere. Returns script -> the files it can now open.
    """
    new = {(_basename(p) or "").lower() for p in _setdiff(after, before)}
    if not new or results is None or len(results) == 0:
        return {}
    r_name = _chr_or_none(results, "file_name")
    r_outcome = _chr_or_none(results, "outcome")
    r_error = _chr_or_none(results, "error")
    r_stderr = _chr_or_none(results, "stderr")
    out: dict[str, list[str]] = {}
    for i, o in enumerate(r_outcome):
        if o != "errored":
            continue
        u = _repro_unopened_files(f"{_na_str(r_error[i])} {_na_str(r_stderr[i])}")
        hit = _unique(x for x in u if x.lower() in new)  # the name as the error gives it
        if hit and r_name[i] is not None:
            out[cast(str, r_name[i])] = hit
    return out


def _repro_written_later_text(
    later: Mapping[str, Sequence[str]],
    ran_ok: Sequence[str | None] = (),
    placed: pd.DataFrame | None = None,
) -> str:
    """Port of ``R/reproducibility_check.R::.repro_written_later_text()``.

    The report's sentence on the scripts run once more after a later script wrote the
    file they stopped on (see :func:`_repro_written_later`); *ran_ok* names the scripts
    that then ran to the end.
    """
    if not later:
        return ""
    n = len(later)
    ok = [s for s in later if s in set(ran_ok)]
    copied = ""
    if placed is not None and len(placed):
        k = len(placed)
        copies = "; ".join(
            f"`{f}` to `{t}` for `{s}`"
            for f, t, s in zip(
                _chr_list(placed["from"]),
                _chr_list(placed["to"]),
                _chr_list(placed["script"]),
                strict=True,
            )
        )
        copied = (
            f" {plural(k, 'One file was written', f'{k} files were written')} in another folder "
            f"than where the script looked, so before that run we copied {plural(k, 'it', 'them')} "
            f"there: {copies}. The scripts assume they all run from one folder; saying which, or "
            "building paths from the top of the project (as with `here::here()`), solves this."
        )
    listed = "; ".join(f"`{s}` ({', '.join(files)})" for s, files in later.items())
    then = (
        f" Then {plural(len(ok), 'this one', 'these')} ran to the end: "
        + ", ".join(f"`{s}`" for s in ok)
        + "."
        if ok
        else ""
    )
    return (
        f" **{n} {plural(n, 'script stopped', 'scripts stopped')} on a file another script writes**, "
        f"so {plural(n, 'it was', 'they were')} run once more after the other scripts: "
        f"{listed}.{copied}{then} Which script must run first can "
        "be stated in a README, or shown by numbering the file names (01_, 02_)."
    )


def _repro_lfs_stopped(
    results: pd.DataFrame | None, root: str | os.PathLike[str]
) -> dict[str, list[str]]:
    """Port of ``R/reproducibility_check.R::.repro_lfs_stopped()``.

    After a run: the scripts that stopped while reading a Git LFS pointer for which the
    deposit has no copy of the file itself (a GitHub archive and a Zenodo copy made from
    it hold only the pointer): five scripts stopped on ``readRDS()`` with "unknown input
    format" (#568). The file was never deposited, so the script stopped on a missing
    file, not on its code. Returns script -> the pointer files it named.
    """
    if results is None or len(results) == 0:
        return {}
    top = os.fspath(root)
    pointers: list[str] = []
    for rel in _list_files(top):
        if grepl(r"/(_rlib|_pylib2?)/", _bs2fs(f"{top}/{rel}")):
            continue
        path = f"{top}/{rel}"
        try:
            if os.path.getsize(path) >= 1024:
                continue
            first = _read_lines(path)[:1]
        except OSError:
            continue
        if first and first[0].startswith(_LFS_HEAD):
            pointers.append(cast(str, _basename(path)))
    ptr = _unique(pointers)
    if not ptr:
        return {}
    r_name = _chr_or_none(results, "file_name")
    r_outcome = _chr_or_none(results, "outcome")
    r_error = _chr_or_none(results, "error")
    r_stderr = _chr_or_none(results, "stderr")
    out: dict[str, list[str]] = {}
    for i, o in enumerate(r_outcome):
        if o != "errored":
            continue
        msg = f"{_na_str(r_error[i])} {_na_str(r_stderr[i])}"
        hit = [p for p in ptr if p in msg]
        if hit and r_name[i] is not None:
            out[cast(str, r_name[i])] = hit
    return out


def _repro_lfs_stopped_text(lfs_stopped: Mapping[str, Sequence[str]]) -> str:
    """Port of ``R/reproducibility_check.R::.repro_lfs_stopped_text()``.

    The report's sentence on scripts that stopped on a Git LFS pointer (see
    :func:`_repro_lfs_stopped`); ``""`` when there are none.
    """
    if not lfs_stopped:
        return ""
    n = len(lfs_stopped)
    files = _unique(f for fs in lfs_stopped.values() for f in fs)
    return (
        f" **{n} {plural(n, 'script stopped', 'scripts stopped')} while reading a file the "
        "deposit holds only as a Git LFS pointer**, a small text file that names the file: "
        f"{', '.join(f'`{f}`' for f in files)}. The file itself was never deposited "
        "(a GitHub archive does not include such files), so this is a missing file, not an "
        f"error in the code: {', '.join(f'`{s}`' for s in lfs_stopped)}. "
        "Depositing the files themselves solves this."
    )


# ---------------------------------------------------------------------------
# what is checked before the code runs
# ---------------------------------------------------------------------------


def _repro_ref_checkable(d: pd.DataFrame, built: Sequence[str | None] = ()) -> list[bool]:
    """Port of ``R/reproducibility_check.R::.repro_ref_checkable()``.

    Which rows of a ``repro_rewrite_paths()`` table name a file that can be checked
    before the code runs. A name built at run time cannot: the row of a ``paste()`` or
    ``sprintf()`` call, a name from ``_repro_built_names()`` (*built*), a name with a
    placeholder (``{name}.csv``, ``%s.pdf``), or a piece of one (``_field.csv``,
    ``.png``). Such a name is only left out of the prediction, and the script is run in
    any case, so a real file called ``_data.csv`` does no harm here. A backslash is no
    such sign, because it separates folders on Windows.
    """
    is_call = _is_true_col(d, "is_call")
    base = _chr_or_none(d, "basename")
    ref = _chr_or_none(d, "ref")
    built_set = set(built)
    out: list[bool] = []
    for i in range(len(d)):
        b = base[i]
        piece = bool(grepl(r"^[._]", b)) or "." not in (b or "")
        placeholder = bool(grepl(r"[%{}$*]|: ", ref[i]))
        out.append(
            not is_call[i] and not piece and not placeholder and _tolower(b) not in built_set
        )
    return out


def _repro_inputs_to_report(missing_inputs: pd.DataFrame, execute: bool = False) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_inputs_to_report()``.

    The predicted missing files worth reporting. Without a run, all of them. After a
    run, only files the deposit does hold but the code could not use: code_check already
    lists files that are not in the repository, and the run shows what happened to each
    script.
    """
    if not _is_true(execute) or len(missing_inputs) == 0:
        return missing_inputs
    status = _chr_or_none(missing_inputs, "status")
    similar = _chr_or_none(missing_inputs, "similar_to")
    keep = [
        (s is not None and s != "absent") or sim is not None
        for s, sim in zip(status, similar, strict=True)
    ]
    return missing_inputs.loc[keep].reset_index(drop=True)


def _repro_inputs_after_run(
    missing_inputs: pd.DataFrame,
    unresolved: Sequence[Sequence[str | None] | None],
    file_names: Sequence[str | None],
    run_results: pd.DataFrame | None,
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_inputs_after_run()``.

    The predicted missing files that still count after a run: those a script names that
    did not run through. A script that ran without an error did not need its file, as
    when it reads the file only inside ``if (file.exists())``. *unresolved* holds the
    names each script could not resolve, in the order of *file_names*.
    """
    if run_results is None or len(missing_inputs) == 0:
        return missing_inputs
    ran_names = {
        f
        for f, o in zip(
            _chr_or_none(run_results, "file_name"),
            _chr_or_none(run_results, "outcome"),
            strict=True,
        )
        if o in ("ran_ok", "run_by_source", "run_by_import")
    }
    needed = {
        x
        for fn, names in zip(file_names, unresolved, strict=False)
        if fn not in ran_names
        for x in (names or [])
    }
    keep = [b in needed for b in _chr_or_none(missing_inputs, "basename")]
    return missing_inputs.loc[keep].reset_index(drop=True)


def _repro_static_light(missing_inputs: pd.DataFrame, clean: bool) -> str:
    """Port of ``R/reproducibility_check.R::.repro_static_light()``.

    The traffic light before any error in a run is counted. Red is the "code cannot run
    only because size-gated inputs were withheld" story: some inputs missing, and all of
    them size-gated. *clean* says that nothing else in the static check found a problem.
    """
    n = len(missing_inputs)
    withheld = sum(1 for s in _chr_or_none(missing_inputs, "status") if s == "withheld_size")
    if n > 0 and withheld == n:
        return "red"
    if clean and n == 0:
        return "green"
    return "yellow"


# ---------------------------------------------------------------------------
# the package layout
# ---------------------------------------------------------------------------


def _repro_package_plan(structure_df: pd.DataFrame | None) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_package_plan()``.

    The package layout's plan: every file in its own place.

    The Psych-DS layout gives the code only the data files it recognised, renamed and
    moved into Psych-DS folders, with the code's paths rewritten to match. That suits a
    study whose scripts read a few tables, but code that reads folders of images or scan
    files, or imports its own helper modules from a subfolder, then fails for files that
    are in the package (``raw_data/scan1/img.tif`` became ``data/img.tif``;
    ``code/utils/helpers.py`` became ``analysis/helpers.py``, so ``from utils import
    helpers`` failed). The package layout copies every file the run has (every file of a
    local folder) to its own path in the package, so a path that works in the package
    works in the sandbox; ``repro_rewrite_paths()`` still rewrites a reference that does
    not (an absolute path on the author's computer, a file in another folder) to where
    that file is.

    *structure_df* is the ``data_check`` structure table (``file_name``, ``file_path``,
    ``file_location``). Returns a plan with ``file_name``, ``current_path``,
    ``target_path`` (the file's own path in the package) and ``source`` (its file on
    disk), one row per path. It is accepted as the ``plan`` of ``repro_rewrite_paths()``,
    ``repro_materialize_layout()`` and ``repro_write_scripts()``.
    """

    def result(rows: Sequence[tuple[str | None, str, str | None]]) -> pd.DataFrame:
        return _frame(
            {
                "file_name": ("string", [r[0] for r in rows]),
                "current_path": ("string", [r[1] for r in rows]),
                "target_path": ("string", [r[1] for r in rows]),
                "source": ("string", [r[2] for r in rows]),
            }
        )

    if structure_df is None or len(structure_df) == 0 or "file_name" not in structure_df.columns:
        return result([])
    name = _chr_list(structure_df["file_name"])
    path = _chr_or_none(structure_df, "file_path") if "file_path" in structure_df.columns else name
    path = [name[i] if not _nzchar(p) else p for i, p in enumerate(path)]
    source = _chr_or_none(structure_df, "file_location")
    rows: list[tuple[str | None, str, str | None]] = []
    seen: set[str] = set()
    for fn, p, src in zip(name, path, source, strict=True):
        if p is None:
            continue
        p = re.sub(r"^(\./|/)+", "", p.replace("\\", "/"))
        if p == "" or _GOES_UP.search(p) or p in seen:
            continue
        seen.add(p)
        rows.append((fn, p, src))
    return result(rows)


def _repro_keep_working_refs(
    rw: pd.DataFrame,
    plan: pd.DataFrame | None,
    script_path: str | None = None,
    run_dir: str = ".",
    here_dir: str | None = ".",
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_keep_working_refs()``.

    Rewrite only the references that cannot work as written.

    Every script runs in the repository as the authors organised it, with every file at
    its own path, from the folder :func:`_repro_run_folders` chooses. A reference is
    then:

    * left as written when it names a file's path from the top
      (``"raw_data/scan1/log.csv"``), also when other files share the name (20 scan
      folders each with a ``log.csv``), and when it is a relative path that names no
      file and no file with its name was found, or the file found has a path that ends
      in the path as written: the code probably joins it with a folder of its own
      (``os.path.join(base, "fig7/x.csv")``), and rewriting it to the full path gave
      ``processed_data/fig7/processed_data/fig7/x.csv``;
    * rewritten to the one file with its name elsewhere in the repository when it names
      no file as written (``"./data/x.csv"`` with the file in ``exploratory/``): the
      authors moved or left out a folder when they deposited;
    * rewritten to the file's path from the top when it names a file relative to the
      script's own folder (``source("helpers.R")``, ``"../raw_data/scores.csv"``), since
      the script does not run from there;
    * rewritten as ``repro_rewrite_paths()`` found, by the file's name, when it is an
      absolute path (``Z:/Strontium/...`` on the author's computer) or goes up out of
      the package.

    A reference left as written is marked ``as_written``; it is not counted as
    rewritten, and not as a missing input, unless it names no file and no file with its
    name is in the repository. Another exception is a file the package has only inside
    an archive, read at the path unpacking it would give: as the package is, that path
    does not exist, so the reference is not found, and the file becomes a missing input
    (``in_archive`` in ``repro_missing_inputs()``).

    *rw* is a ``repro_rewrite_paths()`` result for one script, *plan* the package
    layout's plan, *script_path* the script's own path in the package, *run_dir* the
    folder the script runs from, from the top of the package (a reference works as
    written when it names a file counted from there, and a rewritten path is counted
    from there too) and *here_dir* the folder ``here()`` starts in (a path that is the
    one argument of ``here()``, ``in_here``, counts from there, not from *run_dir*).
    Returns *rw* with a logical ``as_written`` column.
    """
    n = len(rw)
    out = rw.copy()
    out["as_written"] = pd.Series([False] * n, index=out.index, dtype="boolean")
    if n == 0 or plan is None or len(plan) == 0:
        return out
    plan_targets = set(_plan_targets_raw(plan))
    ref_orig = _chr_or_none(rw, "ref")
    raw_ref = [r if r is not None else "" for r in ref_orig]
    is_call = _is_true_col(rw, "is_call")
    ref = [re.sub(r"^(\./)+", "", r.replace("\\", "/")) for r in raw_ref]
    absolute = [bool(_ABSOLUTE.match(r)) for r in ref]
    # here("data/x.csv") counts from where here() starts: rewritten from the folder the
    # script runs from, here("JohnstoneStrait/dateModPars.csv") became
    # here("../data/dateModPars.csv"), above the top (10.1111/1365-2656.12852; #575, item 6)
    if "in_here" in rw.columns:
        in_here = [h and not a for h, a in zip(_is_true_col(rw, "in_here"), absolute, strict=True)]
    else:
        in_here = [False] * n
    start = here_dir if here_dir is not None and not is_missing(here_dir) else "."
    top = run_dir if run_dir is not None and not is_missing(run_dir) else "."
    dir_of = [start if h else top for h in in_here]
    # the path from the top, when the reference names a file relative to the script's
    # own folder
    script_dir = (
        cast(str, _r_dirname(script_path.replace("\\", "/")))
        if script_path is not None and not is_missing(script_path) and script_path != ""
        else "."
    )
    from_script: list[str | None] = []
    from_run: list[str | None] = []
    for i, r in enumerate(ref):
        if absolute[i]:
            from_script.append(None)
            from_run.append(None)
            continue
        p = _repro_norm_rel(r if script_dir in (".", "") else f"{script_dir}/{r}")
        from_script.append(p if p is not None and p in plan_targets else None)
        from_run.append(_repro_norm_rel(r if dir_of[i] in (".", "") else f"{dir_of[i]}/{r}"))
    in_place = [
        not is_call[i] and from_run[i] is not None and from_run[i] in plan_targets for i in range(n)
    ]
    relocate = [not in_place[i] and not is_call[i] and from_script[i] is not None for i in range(n)]
    up = [bool(_GOES_UP.search(r)) for r in ref]
    # a path built by sprintf()/file.path() is kept unless it holds an absolute path
    absolute_call = [
        is_call[i] and bool(re.search(r"""["']([A-Za-z]:[/\\]|/|~)""", raw_ref[i]))
        for i in range(n)
    ]
    keep = [
        in_place[i]
        or (
            not relocate[i]
            and (
                (is_call[i] and not absolute_call[i])
                or (not is_call[i] and not absolute[i] and not up[i])
            )
        )
        for i in range(n)
    ]
    # a name spelled otherwise than the file's does not work as written
    if "name_in_deposit" in rw.columns:
        keep = [
            k and v is None for k, v in zip(keep, _chr_list(rw["name_in_deposit"]), strict=True)
        ]
    matched = _is_true_col(rw, "matched")
    ambiguous = _is_true_col(rw, "ambiguous")
    matched_v = _col(rw, "matched") if "matched" in rw.columns else [None] * n
    ambiguous_v = _col(rw, "ambiguous") if "ambiguous" in rw.columns else [None] * n
    tgt_raw = _chr_or_none(rw, "target")
    tgt = [None if t is None else re.sub(r"^(\./)+", "", t.replace("\\", "/")) for t in tgt_raw]
    # A path that names no file, while repro_rewrite_paths() found the one file with
    # that name elsewhere in the repository: the authors moved or left out a folder when
    # they deposited (`./data/x.csv` with the file in `exploratory/`), and the path is
    # pointed there (#568). Not when the file's path ends in the path as written: the
    # code then probably joins it to a folder of its own (`os.path.join(base, "fig7/x.csv")`).
    found = [_nzchar(tgt[i]) and matched[i] and not ambiguous[i] for i in range(n)]
    # A file at the top named as it is there is no joined path: the authors ran the
    # script from the top, while here it runs from its own folder. In 10.1002/pan3.10310
    # the data, a deposit of its own, lies at the top, and
    # "./kilimanjaro_..._questionnaire.csv" was kept as written and not found (#568).
    joined = [
        found[i] and cast(str, tgt[i]).lower().endswith("/" + ref[i].lower()) for i in range(n)
    ]
    # a bare name counts as joined only when it stands in a joining call
    if "in_join" in rw.columns:
        in_join = _is_true_col(rw, "in_join")
        joined = [j and ("/" in ref[i] or in_join[i]) for i, j in enumerate(joined)]
    # the one argument of here() is joined to nothing but where here() starts
    joined = [j and not in_here[i] for i, j in enumerate(joined)]
    keep = [
        k
        and not (
            not in_place[i]
            and not is_call[i]
            and not absolute[i]
            and not up[i]
            and found[i]
            and not joined[i]
        )
        for i, k in enumerate(keep)
    ]
    # A name several files of the deposit have, none of which the path as written names,
    # is not one that works as written: it was kept, and its mark as ambiguous was taken
    # away, so paths_ambiguous did not count it (#575, item 3).
    keep = [k and not (not in_place[i] and ambiguous[i]) for i, k in enumerate(keep)]
    # a path that names no file, with no file of its name in the repository, stays as
    # written and is a missing input
    absent = [keep[i] and not in_place[i] and not is_call[i] and not matched[i] for i in range(n)]
    # A file found by name only inside an archive ("data.zip/data/x.csv"), read at the
    # path unpacking the archive would give it ("data/x.csv"): the code runs on the package
    # as it is, where that path does not exist, so the reference is not found, and the file
    # is a missing input (#520, item 2).
    arch_re = r"(^|/)[^/]+\.(zip|tar|tgz|tar\.gz)/"
    in_archive: list[bool] = []
    for i in range(n):
        t = tgt[i]
        if not keep[i] or in_place[i] or t is None or not grepl(arch_re, t):
            in_archive.append(False)
            continue
        unpacked = cast(str, sub(arch_re, r"\1", t))
        in_archive.append(unpacked == ref[i] or unpacked.endswith("/" + ref[i]))
    keep = [k and not in_archive[i] for i, k in enumerate(keep)]

    new_matched = list(matched_v)
    new_ambiguous = list(ambiguous_v)
    new_target: list[str | None] = list(tgt_raw)
    for i in range(n):
        if relocate[i]:
            new_matched[i] = True
            new_ambiguous[i] = False
            new_target[i] = from_script[i]
    for i in range(n):
        if keep[i]:
            new_matched[i] = True
            new_ambiguous[i] = False
            new_target[i] = ref_orig[i]
    for i in range(n):
        if in_archive[i]:
            new_matched[i] = False
            new_target[i] = None
        if absent[i]:
            new_matched[i] = False
    # a rewritten path counts from where the script runs, or where here() starts
    for i in range(n):
        t = new_target[i]
        if not keep[i] and _nzchar(t):
            new_target[i] = _repro_rel_from(cast(str, t), dir_of[i])
    out["matched"] = pd.Series(new_matched, index=out.index, dtype="boolean")
    out["ambiguous"] = pd.Series(new_ambiguous, index=out.index, dtype="boolean")
    out["target"] = pd.Series(new_target, index=out.index, dtype="string")
    out["as_written"] = pd.Series(keep, index=out.index, dtype="boolean")
    return out


def _plan_targets_raw(plan: pd.DataFrame) -> list[str]:
    """The plan's ``target_path`` as it is (``plan$target_path``), without ``NA``."""
    if "target_path" not in plan.columns:
        return []
    return [t for t in _chr_list(plan["target_path"]) if t is not None]


def _repro_rel_from(path: str, dir: str = ".") -> str:
    """Port of ``R/reproducibility_check.R::.repro_rel_from()``.

    A path from the top of the package, counted from folder *dir* of the package.
    """
    if dir in (".", ""):
        return path
    a = _split_path(dir.replace("\\", "/"))
    b = _split_path(path.replace("\\", "/"))
    n = min(len(a), len(b) - 1)
    same = [i for i in range(n) if a[i] != b[i]]
    common = same[0] if same else n
    return "/".join([*([".."] * (len(a) - common)), *b[common:]])


# ---------------------------------------------------------------------------
# the folder each script runs from, and where here() starts
# ---------------------------------------------------------------------------


def _repro_here_start(run_dir: str | None, here_root: str | None, plan: pd.DataFrame | None) -> str:
    """Port of ``R/reproducibility_check.R::.repro_here_start()``.

    The folder ``here()`` starts in for a script that runs from *run_dir* (both from the
    top of the package): the folder :func:`_repro_here_roots` chose (*here_root*), else
    the nearest folder at or above *run_dir* with a project's mark (``.here``,
    ``.Rproj``, ``DESCRIPTION``, ``.git``), else the top, which always gets a ``.here``
    mark.
    """
    if here_root is not None and not is_missing(here_root):
        return here_root
    files = _plan_targets(plan)
    marked = {
        cast(str, _r_dirname(f)).lower() for f in files if grepl(_PROJECT_MARK, f, ignore_case=True)
    }
    d = (
        cast(str, _bs2fs(run_dir))
        if run_dir is not None and not is_missing(run_dir) and run_dir != ""
        else "."
    )
    while d not in (".", ""):
        if d.lower() in marked:
            return d
        parent = cast(str, _r_dirname(d))
        if parent == d:  # "/" is its own parent
            break
        d = parent
    return "."


def _repro_run_folders(
    places: Sequence[str | None],
    code_text_list: Mapping[str, Any],
    plan: pd.DataFrame | None,
    order_tbl: pd.DataFrame | None,
    lang: str = "R",
) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_run_folders()``.

    The folder each script runs from, in the package layout. A path in code counts from
    the folder the authors ran it in, which is not recorded: their own folder, or the top
    of their project. Of 271 failures on a file in the listing, 96 would find it from the
    script's own folder and 59 from a folder above, also when the name is only known
    while the script runs, which no rewriting can follow (#568, item 4). So each script
    runs from the folder at or above its own from which the most of the files and folders
    it names exist. On a tie: the nearest folder with an ``.Rproj`` file, which is where
    RStudio runs a project, else the script's own folder. A script run with ``source()``
    takes the folder of the script that sources it, since ``source()`` does not change
    the working folder.

    *places* are the scripts' paths from the top (:func:`_repro_script_places`);
    *code_text_list* holds each script's code, in the same order. Returns one folder per
    script from the top, ``"."`` being the top.
    """
    out = ["."] * len(places)
    if plan is None or len(plan) == 0:
        return out
    codes = list(code_text_list.values())
    files = [t.lower() for t in _plan_targets(plan)]
    folders = _parent_folders(files)
    rproj = {cast(str, _r_dirname(f)) for f in files if grepl(r"\.rproj$", f)}
    known = set(files) | set(folders)
    folder_set = set(folders)
    for i, place in enumerate(places):
        own = cast(str, _r_dirname((_bs2fs(place) or "").lower()))
        chain = [own]
        while chain[-1] not in (".", ""):
            chain.append(cast(str, _r_dirname(chain[-1])))
        if len(chain) == 1:
            continue
        # every quoted text counts: only what exists in the package adds to the score,
        # and a name also appears outside read calls: os.listdir("raw/scan1")
        raw_code = [_iconv_drop(x) for x in _chr_list(codes[i] if i < len(codes) else None)]
        code = _strip_comments(raw_code, lang)
        quoted = [
            q
            for line in code
            for q in cast(list[str], regextract_all(r"""["'][^"'\n]+["']""", line))
        ]
        stripped = _unique(cast(str, gsub(r"""^["']|["']$""", "", q)) for q in quoted)
        refs = [x.replace("\\", "/").lower() for x in stripped]
        refs = [r for r in refs if not grepl(r"^([a-z]:/|/|~)|://|[%{}$*]", r)]
        score: list[int] = []
        for d in chain:
            total = 0
            for r in refs:
                frm = _repro_norm_rel(r if d == "." else f"{d}/{r}")
                if frm is None:
                    continue
                if frm in known or ("/" in r and _r_dirname(frm) in folder_set):
                    total += 1
            score.append(total)
        top = max(score)
        best = [c for c, s in zip(chain, score, strict=True) if s == top]
        marked = [b for b in best if b in rproj]
        pick = marked[0] if marked else best[0]
        # the original spelling of the folder, as case can matter on the system that runs it
        depth = len(chain) - (chain.index(pick) + 1)
        orig = cast(str, _r_dirname(_bs2fs(place) or ""))
        pieces = _split_path(orig)
        out[i] = "." if pick == "." or depth <= 0 else "/".join(pieces[:depth])
    edges = order_tbl.attrs.get("source_edges") if order_tbl is not None else None
    if edges is not None and len(edges):
        fn = [str(k) for k in code_text_list]
        e_from = _chr_list(edges["from"])
        e_to = _chr_list(edges["to"])
        for _round in range(5):
            for k in range(len(edges)):
                a, b = e_from[k], e_to[k]
                src = fn.index(a) if a is not None and a in fn else None
                dst = fn.index(b) if b is not None and b in fn else None
                if src is not None and dst is not None and b not in e_from:
                    out[src] = out[dst]
    return out


def _iconv_drop(x: str | None) -> str:
    """``iconv(x, "", "UTF-8", sub = "")``: bytes that are not UTF-8 are dropped."""
    if x is None:
        return "NA"
    return x.encode("utf-8", "ignore").decode("utf-8")


def _repro_here_roots(
    code_text_list: Mapping[str, Any],
    run_dirs: Sequence[str | None],
    plan: pd.DataFrame | None,
) -> dict[str, str | None]:
    """Port of ``R/reproducibility_check.R::.repro_here_roots()``.

    The folder ``here()`` should take as the project's top, for scripts that use it
    (#568). With no ``.Rproj``, ``.here`` or the like in the deposit, ``here()`` starts
    in the folder the script runs from, so ``here("scripts", "functions.R")`` became
    ``/sandbox/scripts/scripts/functions.R``. For each script, the folder at or above
    the one it runs from from which the most of its ``here("a", "b")`` paths exist; a
    ``.here`` file there makes ``here()`` start there. Not when a project marker already
    lies between. *code_text_list* and *run_dirs* (folders from the top) are per script;
    returns per script the folder, from the top in the deposit's spelling, or ``None``
    (no ``here()`` paths, or none found).
    """
    out: dict[str, str | None] = {str(k): None for k in code_text_list}
    if plan is None or len(plan) == 0 or not code_text_list:
        return out
    tp = _plan_targets(plan)
    files = [t.lower() for t in tp]
    known = set(files) | set(_parent_folders(files))
    markers = {cast(str, _r_dirname(f)) for f in files if grepl(_PROJECT_MARK, f)}
    here_pat = r"""\bhere(?:::here)?\s*\(\s*(["'][^"'\n]*["']\s*,?\s*)+\)"""
    for i, (name, code) in enumerate(code_text_list.items()):
        txt = _paste_lines(_utf8_lines(_chr_list(code)))
        calls = cast(list[str], regextract_all(here_pat, txt, perl=True))
        if not calls:
            continue
        paths: list[str] = []
        for cl in calls:
            args = cast(list[str], regextract_all(r"""["'][^"'\n]*["']""", cl))
            paths.append("/".join(cast(str, gsub(r"""^["']|["']$""", "", a)) for a in args))
        paths = [cast(str, sub("/+$", "", gsub("/+", "/", p))).lower() for p in paths]
        rd = run_dirs[i] if i < len(run_dirs) else None
        chain = [(rd if rd is not None and not is_missing(rd) else ".").lower()]
        while chain[-1] not in (".", ""):
            chain.append(cast(str, _r_dirname(chain[-1])))
        # "../data/x.csv" counts from the folder too: here("../data/dateModPars.csv")
        # from "scripts/" names "data/dateModPars.csv" (10.1111/1365-2656.12852; #575, item 6)
        score: list[int] = []
        for f in chain:
            normed = [_repro_norm_rel(p if f == "." else f"{f}/{p}") for p in paths]
            score.append(sum(1 for p in normed if p is not None and p in known))
        if max(score) < 1:
            continue
        pick = chain[score.index(max(score))]
        # a marker at or below the pick, on the way from the run folder, wins already
        between = chain[: chain.index(pick) + 1]
        if any(b in markers for b in between):
            continue
        under = [t for t in tp if t.lower().startswith(pick + "/")]
        out[str(name)] = "." if pick == "." else (under[0][: len(pick)] if under else None)
    return out


# ---------------------------------------------------------------------------
# folders the code holds in a variable, or lists
# ---------------------------------------------------------------------------

_SIMPLE_CALL_PAT = (
    r"^([.a-zA-Z][.a-zA-Z0-9_]*\s*(?:<<-|<-|=)\s*)"
    r"(?:base::)?(?:paste0|paste|file\.path|path\.expand|normalizePath)"
    r"""\s*\(\s*((['"])(?:[^'"\\]|\\.)*\3)\s*\)\s*$"""
)


def _string_var_of_line(line: str | None) -> tuple[str, str] | None:
    """``.repro_simple_string_vars(line, calls = TRUE)`` for one line of code.

    The assignment of a quoted text, also as the one argument of ``paste0()``,
    ``paste()``, ``file.path()``, ``path.expand()`` or ``normalizePath()``, which give
    that text: a folder on the authors' computer was held as
    ``main_address = paste0("~/Library/...")`` in 10.1111/1365-2435.14620 (#578, item 4).
    ``(name, value)``, or ``None`` for any other line. (The ``calls`` option of the R
    function; :func:`~metacheck.repro.core._repro_simple_string_vars` has none.)
    """
    if line is None:
        return None
    line = cast(str, sub(_SIMPLE_CALL_PAT, r"\1\2", line, perl=True))
    found = _repro_simple_string_vars([line])
    for name, value in found.items():
        return name, value
    return None


def _repro_folder_vars(
    code_text: str | Sequence[str | None],
    plan: pd.DataFrame | None,
    run_dir: str = ".",
    rw: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_folder_vars()``.

    Folders on the authors' computer held in a variable (#568): ``root <-
    "~/Data_to_publish/"`` with ``paste0(root, "2_data/")`` and
    ``source(paste0(root, "/1_scripts/0_FUNCTIONS.R"))`` (10.1111/1365-2656.14164), or
    ``dataDir <- "~/MGDrivE/Main/SoftwarePaper2"`` with ``file.path(dataDir,
    "Data/k.csv")`` (10.1111/2041-210X.13318). The variable gets the folder of the
    deposit under which the most of the paths pasted to it exist, counted from *run_dir*,
    the folder the script runs from; the pasted pieces are then not rewritten on their
    own (they were, so ``"/1_scripts/0_FUNCTIONS.R"`` became ``"0_FUNCTIONS.R"`` after
    the author's folder). Only an absolute folder (``C:/...``, ``/...``, ``~/...``) is
    changed, and only when at least one pasted path exists in the deposit, or a pasted
    file is in the deposit by its name (see below): a piece the script's rewrites *rw*
    point to a file, or without them, a name one file has. Returns ``value`` (the folder
    as written), ``target`` (its new value), ``keep_pieces`` (whether the pasted pieces
    keep their own rewrites) and a list-column ``pieces`` (the texts pasted to it, as
    written).
    """

    def build(
        rows: Sequence[tuple[str, str, bool, list[str]]],
    ) -> pd.DataFrame:
        return _frame(
            {
                "value": ("string", [r[0] for r in rows]),
                "target": ("string", [r[1] for r in rows]),
                "keep_pieces": ("boolean", [r[2] for r in rows]),
                "pieces": ("object", [r[3] for r in rows]),
            }
        )

    empty = build([])
    code = _chr_list(code_text)
    if plan is None or len(plan) == 0 or not code:
        return empty
    nc = _strip_comments(_utf8_lines(code), "R")

    def absolute_folder(x: str) -> bool:
        return bool(grepl(r"^([A-Za-z]:[/\\]|/|~)", x)) and not grepl(r"\.[A-Za-z0-9]{1,8}$", x)

    # Every value a variable is given, not only the last: in 10.1111/1365-2435.14620
    # main_address is set four times, to the folder with and without a final "/", and only
    # the last value was rewritten (#578, item 4)
    assigned = [p for p in (_string_var_of_line(x) for x in nc) if p is not None]
    assigned = [p for p in assigned if absolute_folder(p[1])]
    # `!duplicated(names, fromLast = TRUE)`: each name once, at its last position
    last_at = {name: i for i, (name, _) in enumerate(assigned)}
    var_names = [assigned[i][0] for i in sorted(last_at.values())]
    if not var_names:
        return empty
    pieces_of: dict[str, list[str]] = {}

    def rows_for(v: str, to: str, keep: bool) -> list[tuple[str, str, bool, list[str]]]:
        """The rows of one variable: each value it is given, with the new folder, and a
        final "/" where the value has one."""
        vals = _unique(value for name, value in assigned if name == v)
        base = re.sub("/+$", "", to)
        if base == "":
            base = "."
        return [
            (val, base + "/" if re.search(r"[/\\]$", val) else base, keep, pieces_of[v])
            for val in vals
        ]

    joined = _paste_lines(nc)
    files = [t.lower() for t in _plan_targets(plan)]
    folders = _parent_folders(files)
    known = set(files) | set(folders)
    out_rows: list[tuple[str, str, bool, list[str]]] = []
    for v in var_names:
        vq = re.sub(r"([.\\])", r"\\\1", v)
        pat = r"\b(?:paste0|file\.path|paste)\s*\(\s*" + vq + r"""\s*,\s*(['"])([^'"\n]*)\1"""
        hits = cast(list[str], regextract_all(pat, joined, perl=True))
        if not hits:
            continue
        pieces = _unique(cast(str, sub(pat, r"\2", h, perl=True)) for h in hits)
        pieces_of[v] = pieces
        rel = [re.sub("/+$", "", re.sub(r"^[/\\]+", "", p.replace("\\", "/"))) for p in pieces]
        rel = [r for r in rel if r]
        if not rel:
            continue
        # "../2_data/x.csv" counts from the folder too (10.1111/1365-2656.14164)
        candidates = [".", *folders]
        score: list[int] = []
        for f in candidates:
            normed = [_repro_norm_rel(r if f == "." else f"{f}/{r}") for r in rel]
            score.append(sum(1 for p in normed if p is not None and p.lower() in known))
        if max(score) < 1:
            # No pasted path exists as written, but a pasted file is in the deposit by
            # its name, and the path rewrite points the piece there, counted from the run
            # folder: the folder then becomes the run folder, and the piece keeps its
            # rewrite. In 10.1111/1365-2435.14620 "obsSurvival_Temperature_dfs/
            # observed_surv_df.xlsx" is in DATA.zip/DATA/..., and main_address stayed the
            # authors' folder (#578, item 4).
            if rw is not None and len(rw):
                by_name = _pointed_pieces(rw, pieces)
            else:
                # without the rewrites, a name that one file of the deposit has
                named = [
                    (_basename(r) or "").lower()
                    for r in rel
                    if re.search(r"\.[A-Za-z0-9]{1,8}$", r)
                ]
                file_bases = [_basename(f) for f in files]
                by_name = [b for b in named if file_bases.count(b) == 1]
            if not by_name:
                continue
            # "./" with every value, so paste0() and file.path() both still join
            out_rows.extend((val, "./", True, pcs) for val, _t, _k, pcs in rows_for(v, ".", True))
            continue
        best = [c for c, s in zip(candidates, score, strict=True) if s == max(score)]
        # several equally good: the one nearest the script's own folder, else none
        rd = f"{run_dir.lower()}/"
        near = [b for b in best if rd.startswith(f"{b}/") or b == "."]
        pick: str | None
        if len(best) == 1:
            pick = best[0]
        elif near:
            pick = max(near, key=len)  # which.max(): the first of the longest
        else:
            pick = None
        if pick is None:
            continue
        # the original spelling of the folder, as case can matter
        if pick != ".":
            tp = _plan_targets(plan)
            under = [t for t in tp if t.lower().startswith(f"{pick}/")]
            if not under:
                continue
            pick = under[0][: len(pick)]
        to = cast(str, _r_dirname(_repro_rel_from("./x" if pick == "." else f"{pick}/x", run_dir)))
        out_rows.extend(rows_for(v, to, False))
    return build(out_rows) if out_rows else empty


def _pointed_pieces(rw: pd.DataFrame, pieces: Sequence[str]) -> list[str]:
    """The pasted *pieces* that the rewrites *rw* point to a file of the deposit."""
    if "as_written" not in rw.columns:
        return []  # R: the missing column leaves the condition empty
    matched = _is_true_col(rw, "matched")
    ambiguous = _is_true_col(rw, "ambiguous")
    written = _is_true_col(rw, "as_written")
    target = _chr_or_none(rw, "target")
    ref = _chr_or_none(rw, "ref")
    pointed = {
        ref[i]
        for i in range(len(rw))
        if matched[i] and not ambiguous[i] and target[i] is not None and not written[i]
    }
    return [p for p in pieces if p in pointed]


def _repro_apply_folder_vars(rw: pd.DataFrame, fv: pd.DataFrame | None) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_apply_folder_vars()``.

    Apply :func:`_repro_folder_vars` to one script's rewrites: a row for each folder's
    new value, and the pasted pieces no longer rewritten on their own.
    """
    if fv is None or len(fv) == 0:
        return rw
    # pieces of a folder that became the run folder keep their rewrites (#578, item 4)
    dropped = {
        p
        for pcs, keep in zip(_col(fv, "pieces"), _col(fv, "keep_pieces"), strict=True)
        if not _is_true(keep)
        for p in (pcs or [])
    }
    if len(rw):
        refs = _chr_or_none(rw, "ref")
        rw = rw.loc[[r not in dropped for r in refs]]
    values = _chr_list(fv["value"])
    targets = _chr_list(fv["target"])
    add = _rewrite_columns(
        [_folder_row(v or "", t or "") for v, t in zip(values, targets, strict=True)]
    )
    return bind_rows([rw, add])


def _repro_listed_folders(
    code_text: str | Sequence[str | None],
    plan: pd.DataFrame | None,
    run_dir: str = ".",
    lang: str = "R",
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_listed_folders()``.

    Folders a script lists, with ``list.files()``, ``dir()``, ``list.dirs()``,
    ``Sys.glob()`` or ``fs::dir_ls()`` in R and ``os.listdir()``, ``os.scandir()``,
    ``glob.glob()`` or ``Path(...).glob()`` in Python, rewritten to where the deposit
    holds them: in 10.1111/2041-210X.14241 ``list.files("../data/ir_data/control_forage_data/")``
    found nothing, while the folder is in ``heatwave_data_07_25_22.zip/heatwave_data/``,
    and the data frame had no columns (#578, item 2). ``code_file_refs()`` returns only
    file names, so the path rewrite never saw the folder. A folder that works as written
    from *run_dir* stays. Otherwise its last parts are compared with the folders of the
    plan, as many as match, and it is rewritten only when that leaves one folder, counted
    from *run_dir*, with its final ``/`` kept. For a glob pattern (``data/*.csv``) the
    folder before the pattern counts. Returns rows in the form of
    ``repro_rewrite_paths()``, for the rewrites of the script.
    """
    empty = _rewrite_columns([])
    code = _chr_list(code_text)
    if plan is None or len(plan) == 0 or not code:
        return empty
    txt = _paste_lines(_strip_comments(_utf8_lines(code), lang))
    python = lang == "Python"
    pat = (
        r"""\b(?:os\.listdir|os\.scandir|glob\.i?glob|Path)\s*\(\s*(["'])([^"'\n]+)\1"""
        if python
        else r"""\b(?:list\.files|list\.dirs|dir|Sys\.glob|(?:fs::)?dir_ls)\s*\(\s*(?:path\s*=\s*)?(["'])([^"'\n]+)\1"""
    )
    hits = cast(list[str], regextract_all(pat, txt, perl=True))
    if python:
        # Path("x") only when it is listed: Path("x").glob() or .iterdir()
        def listed(h: str) -> bool:
            at = 0
            while (at := txt.find(h, at)) >= 0:
                after = txt[at + len(h) : at + len(h) + 41]
                if grepl(r"^\s*\)\s*\.(?:r?glob|iterdir)\b", after, perl=True):
                    return True
                at += len(h)
            return False

        hits = [h for h in hits if not h.startswith("Path") or listed(h)]
    refs = _unique(cast(str, sub("(?s)" + pat, r"\2", h, perl=True)) for h in hits)
    if not refs:
        return empty
    files = _plan_targets(plan)
    folders = _parent_folders(files)
    if not folders:
        return empty
    folder_parts = [f.lower().split("/") for f in folders]
    folders_low = {f.lower() for f in folders}
    rows: list[dict[str, Any]] = []
    for ref in refs:
        r = re.sub(r"\\+", "/", ref)
        glob = bool(grepl(r"[*?]", _basename(r)))
        dir_part = cast(str, _r_dirname(r)) if glob else re.sub("/+$", "", r)
        rest = _basename(r) if glob else ""
        # works as written from where the script runs
        if _ABSOLUTE.match(dir_part):
            from_run = None
        else:
            from_run = _repro_norm_rel(
                dir_part if run_dir in (".", "") else f"{run_dir}/{dir_part}"
            )
        if from_run is not None and from_run.lower() in folders_low:
            continue
        parts = [
            p
            for p in dir_part.lower().split("/")
            if p and p not in (".", "..", "~") and not re.match(r"^[a-z]:$", p)
        ]
        if not parts:
            continue
        shared: list[int] = []
        for fp in folder_parts:
            n = min(len(fp), len(parts))
            a = fp[::-1][:n]
            b = parts[::-1][:n]
            same = [x == y for x, y in zip(a, b, strict=True)]
            shared.append(n if all(same) else same.index(False))
        top = max(shared)
        best = [i for i, s in enumerate(shared) if s == top]
        if top < 1 or len(best) != 1:
            continue
        to = _repro_rel_from(folders[best[0]], run_dir)
        if glob:
            to = f"{to}/{rest}"
        else:
            to = to + ("/" if re.search(r"[/\\]$", ref) else "")
        row = _folder_row(ref, to)
        row["basename"] = _basename(dir_part)
        rows.append(row)
    return _rewrite_columns(rows) if rows else empty


# ---------------------------------------------------------------------------
# a file chosen by a person, the .Rprofile, Windows paths
# ---------------------------------------------------------------------------

#: What asks a person to choose a file, which nobody can during a check.
_repro_choose_pat = (
    r"(?<![\w.])(?:utils::|tcltk::|rstudioapi::)?"
    r"(?:file\.choose|choose\.files|tk_choose\.files|selectFile)"
    r"\s*\((?:[^()]|\([^()]*\))*\)"
)


def _repro_fill_file_choice(
    code_text: str | Sequence[str | None], data_files: Sequence[str]
) -> dict[str, Any]:
    """Port of ``R/reproducibility_check.R::.repro_fill_file_choice()``.

    Fill in the file where a script asks a person to choose one, when the code says
    which: a comment on that line or just above it names a data file of the deposit, or
    the deposit holds only one data file (#568, item 18). Otherwise the call stays, and
    the script stops there with the outcome ``needs_a_person``. Returns ``{"txt": ...,
    "filled": ...}``: the text and what was filled in.
    """
    code = _chr_list(code_text)
    data = [d for d in _chr_list(data_files) if d is not None]
    out: dict[str, Any] = {"txt": code, "filled": []}
    if not code or not data:
        return out
    txt = list(code)
    stems = [_file_path_sans_ext(f) or "" for f in data]
    rx = compile_r(_repro_choose_pat, perl=True)
    filled: list[str] = []
    for i, line in enumerate(txt):
        if line is None or rx.search(line) is None or re.match(r"^\s*#", line):
            continue
        above = txt[max(0, i - 2) : i]
        ctx = [x for x in above if x is not None and re.match(r"^\s*#", x)]
        if "#" in line:
            ctx.append(re.sub(r"^.*?#", "", line, count=1))
        named = [
            f for f, s in zip(data, stems, strict=True) if len(s) >= 4 and any(s in c for c in ctx)
        ]
        if len(set(named)) == 1:
            pick = named[0]
        elif not named and len(data) == 1:
            pick = data[0]
        else:
            continue
        new = '"' + pick.replace('"', '\\"') + '"'
        spans = [m.span() for m in rx.finditer(line)]
        count = len(spans)
        out_line, at = "", 0
        for a, b in spans:
            out_line += line[at:a] + new
            at = b
        txt[i] = out_line + line[at:]
        filled.extend([pick] * count)
    out["txt"] = txt
    out["filled"] = filled
    return out


def _repro_neutralise_rprofile(root: str | os.PathLike[str]) -> int:
    """Port of ``R/reproducibility_check.R::.repro_neutralise_rprofile()``.

    The same for a project's ``.Rprofile``, which R runs when it starts: it usually
    holds ``source("renv/activate.R")``, which downloads renv and changes the library the
    run uses. The rest of the file stays, since it can set options the analysis relies
    on. Returns the number of calls replaced.
    """
    paths: list[str] = []
    # in every folder, since a script can run from its project's folder
    for d, _subdirs, names in os.walk(os.fspath(root), followlinks=True):
        if ".Rprofile" in names:
            paths.append(os.path.join(d, ".Rprofile"))
    n = 0
    for path in sorted(paths):
        try:
            txt: list[str | None] = list(_read_lines(path))
        except OSError:
            continue
        new, k = _repro_neutralise_env_calls(txt)
        if k > 0:
            try:
                _write_lines(new, path)
            except OSError:
                pass
        n += k
    return n


#: ``gsub("\\\\\\\\", "/", p)``: the doubled backslash of a Windows path in R source
_TWO_BACKSLASHES = "\\" * 2


def _repro_slash_rows(run_tbl: pd.DataFrame | None) -> pd.DataFrame | None:
    """Port of ``R/reproducibility_check.R::.repro_slash_rows()``.

    The modifications rows for the Windows paths ``repro_write_scripts()`` changed, one
    per path, and for the paths it made relative to the working folder because they lay
    under the folder of a removed ``setwd()`` (``path_rewrite``, #578, item 3); ``None``
    when there were none. Columns ``file_name``, ``change_type`` and ``detail``.
    """
    if run_tbl is None or len(run_tbl) == 0:
        return None
    names = _chr_or_none(run_tbl, "file_name")
    scripts: list[str | None] = []
    types: list[str] = []
    details: list[str] = []

    def rows_of(col: str, change: str, detail: Any) -> None:
        if col not in run_tbl.columns:
            return
        for i, v in enumerate(_chr_list(run_tbl[col])):
            if not _nzchar(v):
                continue
            for p in cast(list[str], strsplit(v, "; ", fixed=True)):
                scripts.append(names[i])
                types.append(change)
                details.append(detail(p))

    rows_of(
        "setwd_folder_removed",
        "path_rewrite",
        lambda p: f"{p} (under the folder of a removed setwd())",
    )
    rows_of("slashes_fixed", "windows_path", lambda p: f"{p} -> {p.replace(_TWO_BACKSLASHES, '/')}")
    if not scripts:
        return None
    return _frame(
        {
            "file_name": ("string", scripts),
            "change_type": ("string", types),
            "detail": ("string", details),
        }
    )


def _repro_slash_report(run_tbl: pd.DataFrame | None) -> list[Any] | None:
    """Port of ``R/reproducibility_check.R::.repro_slash_report()``.

    The report's part on Windows paths: a sentence and a table; ``None`` when none.
    """
    rows = _repro_slash_rows(run_tbl)
    if rows is None:
        return None
    change = _chr_list(rows["change_type"])
    keep = [c == "windows_path" for c in change]
    if not any(keep):
        return None
    rows = rows.loc[keep]
    n = len(_unique(_chr_list(rows["file_name"])))
    text = (
        f"**{n} script{plural(n)} use{plural(n, 's', '')} Windows paths**, with `\\` between "
        "folders, which works only on Windows. To run the code we put `/` instead, which "
        "works on every system, Windows included."
    )
    table = _frame(
        {
            "File": ("string", _chr_list(rows["file_name"])),
            "Path in the code": (
                "string",
                [sub(" -> .*", "", d) for d in _chr_list(rows["detail"])],
            ),
        }
    )
    return [text, scroll_table(table, maxrows=10)]


# ---------------------------------------------------------------------------
# a script's name and place
# ---------------------------------------------------------------------------


def _repro_script_path(files: pd.DataFrame) -> list[str | None]:
    """Port of ``R/reproducibility_check.R::.repro_script_path()``.

    A script's path in its repository (forward slashes, nothing in front), or its file
    name when it has no path.
    """
    name = _chr_or_none(files, "file_name")
    path = _chr_or_none(files, "file_path")
    out: list[str | None] = []
    for nm, p in zip(name, path, strict=True):
        p = nm if not _nzchar(p) else p
        out.append(None if p is None else re.sub(r"^(\./|/)+", "", p.replace("\\", "/")))
    return out


def _repro_unique_script_names(
    files: pd.DataFrame, taken: Sequence[str | None] = ()
) -> list[str | None]:
    """Port of ``R/reproducibility_check.R::.repro_unique_script_names()``.

    A name of its own for each script of a paper. Every step of the check knows a script
    by ``file_name``, which holds no folder, so scripts that share a name (not
    byte-identical copies, which are removed first) shared one result, and only the first
    ran (#568). A name that occurs once stays as it is, so results stay comparable. A
    repeated name becomes the script's path, which still ends in the file name; with the
    repository in front when the path repeats too (the same path in a GitHub and a
    Zenodo copy), and as a last resort a number. *taken* holds names already used by the
    other language.
    """
    out = _chr_or_none(files, "file_name")
    key = [_tolower(x) for x in out]
    taken_low = [_tolower(x) for x in taken]
    seen: set[str | None] = set()
    dup: set[str | None] = set()
    for k in key:
        (dup if k in seen else seen).add(k)
    repeated = [k in dup or k in taken_low for k in key]
    if not any(repeated):
        return out
    paths = _repro_script_path(files)
    out = [paths[i] if repeated[i] else out[i] for i in range(len(out))]

    def clashes() -> list[bool]:
        k = [_tolower(x) for x in out]
        seen_k: set[str | None] = set()
        dup_k: set[str | None] = set()
        for x in k:
            (dup_k if x in seen_k else seen_k).add(x)
        return [repeated[i] and (k[i] in dup_k or k[i] in taken_low) for i in range(len(k))]

    if "repo_name" in files.columns:
        repo = _chr_list(files["repo_name"])
        add = clashes()
        out = [
            f"{repo[i]}/{out[i]}" if add[i] and _nzchar(repo[i]) else out[i]
            for i in range(len(out))
        ]
    left = clashes()
    return [f"{i + 1}/{out[i]}" if left[i] else out[i] for i in range(len(out))]


def _repro_order_path(files: pd.DataFrame) -> list[str | None]:
    """Port of ``R/reproducibility_check.R::.repro_order_path()``.

    The path to tell a script's folder from, for ``repro_run_order()``: its unique name
    when that holds a folder (it may have the repository in front), else its path in the
    repository.
    """
    name = _chr_or_none(files, "file_name")
    path = _repro_script_path(files)
    return [n if n is not None and "/" in n else p for n, p in zip(name, path, strict=True)]


def _repro_script_places(files: pd.DataFrame) -> list[str | None]:
    """Port of ``R/reproducibility_check.R::.repro_script_places()``.

    Where ``repro_write_scripts()`` puts each script (``script_paths``): its own path in
    the repository. Two scripts with one place (the same path in two repositories) go by
    their unique names, and no script is put above the sandbox.
    """
    fname = _chr_or_none(files, "file_name")
    out = _repro_script_path(files)
    k = [_tolower(x) for x in out]
    seen: set[str] = set()
    dup: set[str] = set()
    for x in k:
        if x is None:
            continue
        (dup if x in seen else seen).add(x)
    out = [fname[i] if k[i] is not None and k[i] in dup else out[i] for i in range(len(out))]
    return cast(list[str | None], gsub(r"(^|/)\.\.(?=/|$)", r"\1_", out, perl=True))


def _repro_shown_location(files: pd.DataFrame, idx: int | Sequence[int]) -> list[str | None]:
    """Port of ``R/reproducibility_check.R::.repro_shown_location()``.

    Where code file *idx* of *files* (code_check's table) came from, for the report: its
    link, else its path in the repository, else ``None``. Never its place on the computer
    that runs the check, which on a server is the server's job folder. *idx* are 0-based
    row positions (R's ``which()`` is 1-based).
    """
    rows = [idx] if isinstance(idx, int) else list(idx)
    url_all = _chr_or_none(files, "file_url")
    path_all = _chr_or_none(files, "file_path")
    out: list[str | None] = []
    for i in rows:
        url, path = url_all[i], path_all[i]
        if url is not None and grepl(r"^https?://", url):
            out.append(url)
        elif _nzchar(path):
            out.append(path)
        else:
            out.append(None)
    return out
