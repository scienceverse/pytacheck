"""The Python code path of the Reproducibility Check module.

Port of the Python block of ``inst/modules/reproducibility_check.R`` (metacheck 0.4.3.7,
``## Python code path``): the same static analysis as the R block (dependencies, path
rewrites, run order, missing inputs) and, with ``execute=True``, a run of each Python script
under the same sandbox as the R scripts: a Docker container by default, the host only with an
explicit ``sandbox="process"`` (D62). :func:`assess_py` returns the pieces the module
combines with the R block's into one result.

Deliberate differences from metacheck (D119): an import of one of the paper's own modules is
not a dependency to install, and a ``NameError`` does not trigger a second run in another
order, which cannot change the outcome (every script runs in its own process).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import bind_rows, grepl, plural, r_round, strsplit, trimws
from metacheck._values import as_str
from metacheck.modules import _reproducibility as h
from metacheck.modules import reproducibility_check as rc
from metacheck.report import collapse_section, scroll_table
from metacheck.repro.python import repro_dependencies_py

#: Outcomes that keep a script from running through without being a failure of its code.
_NOT_FAILURES = (
    "skipped_time_limit",
    "docker_failed",
    "needs_a_person",
    "needs_internet",
    "download_too_large",
    "not_downloaded",
    "out_of_memory",
    "not_read",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _unique(xs: Sequence[Any]) -> list[Any]:
    return list(dict.fromkeys(xs))


def _names(df: Any, col: str) -> list[str]:
    """A column of names as strings (``NA`` as ``"NA"``)."""
    return [rc._na_str(x) for x in h._chr_col(df, col)]


def _paste2(x: Any, y: Any) -> str:
    """``paste(x, y)`` of two elements (``NA`` is written ``NA``)."""
    return f"{'NA' if h._na(x) else x} {'NA' if h._na(y) else y}"


def _lgl(d: pd.DataFrame, col: str) -> list[bool]:
    """``isTRUE`` of each element of a logical column (all ``False`` without the column)."""
    if col not in d.columns:
        return [False] * len(d)
    return [h._is_true(v) for v in d[col].tolist()]


def _rbind(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """``rbind(a, b)`` of two frames that may be empty."""
    if len(b) == 0:
        return a
    if len(a) == 0:
        return b.reset_index(drop=True)
    return pd.concat([a, b], ignore_index=True)


def _file_ext_stripped(path: str) -> str:
    """``tools::file_path_sans_ext(basename(path))``."""
    base = path.rstrip("/").rpartition("/")[2]
    return re.sub(r"\.[A-Za-z0-9]+$", "", base)


def _dirname(path: str) -> str:
    """R ``dirname()`` of a ``/`` path."""
    p = path.rstrip("/")
    if "/" not in p:
        return "."
    d = p.rpartition("/")[0]
    return d if d else "/"


def _ordered_like(results: pd.DataFrame, order: Sequence[str | None]) -> pd.DataFrame:
    """``results[order(match(results$file_name, order)), ]`` (a name not in *order* last)."""
    pos: dict[str | None, int] = {}
    for i, n in enumerate(order):
        pos.setdefault(n, i)
    names = h._chr_col(results, "file_name")
    idx = sorted(range(len(results)), key=lambda i: (names[i] not in pos, pos.get(names[i], 0)))
    return results.iloc[idx].reset_index(drop=True)


def _replace_rows(
    results: pd.DataFrame, new: pd.DataFrame, order: Sequence[str | None]
) -> pd.DataFrame:
    """The rows of *new* in place of those of *results* with the same file name, in *order*."""
    kept = results[~results["file_name"].isin(new["file_name"].tolist())]
    return _ordered_like(bind_rows([kept, new]), order)


# ---------------------------------------------------------------------------
# reading the files
# ---------------------------------------------------------------------------


def _manifest_text(structure_df: Any) -> list[str] | None:
    """The repository's ``requirements.txt`` (preferred) or ``pyproject.toml``, if it has one."""
    names = h._chr_col(structure_df, "file_name")
    locs = h._chr_col(structure_df, "file_location") or [None] * len(names)
    for pat in (r"(^|/)requirements\.txt$", r"(^|/)pyproject\.toml$"):
        hits = [i for i, m in enumerate(grepl(pat, names, ignore_case=True)) if m]
        if hits:
            loc = locs[hits[0]]
            return rc._read_code(loc) if h._file_exists(loc) else None
    return None


def _extract(
    path: str | None, name: str
) -> tuple[list[str], list[str], list[str], str | None, bool]:
    """A file's raw text, its Python code, what was left out of a notebook to get it, why it
    could not be read (``None`` when it could), and whether it is not text.

    A notebook is JSON and a Quarto document is Markdown: their Python code is extracted first,
    as code_check does (and as the R path purls an .Rmd/.qmd), or ``python`` would be run on
    the JSON/Markdown itself. A file that could not be read keeps its error, as for R (#620).
    """
    if path is None:
        return [], [], [], "there is no copy of it and no link to it", False
    binary = False
    if h._file_exists(path):
        from metacheck.repro.core import _repro_binary_kind

        binary = _repro_binary_kind(path) is not None
    raw_read, err = rc._read_code_error(path)
    raw = raw_read or []
    if grepl(r"\.ipynb$", name, ignore_case=True):
        from metacheck.repro.layout import _code_notebook_parts

        try:
            parts = _code_notebook_parts(raw)
        except Exception as e:
            return raw, [], [], err or str(e) or type(e).__name__, binary
        return raw, list(parts["lines"]), list(parts["removed"]), err, binary
    if grepl(r"\.qmd$", name, ignore_case=True):
        from metacheck.codecheck.core import code_extract_qmd_py

        try:
            code = [x if x is not None else "NA" for x in code_extract_qmd_py(path)]
        except Exception as e:
            code = []
            err = err or str(e) or type(e).__name__
        return raw, code, [], err, binary
    return raw, raw, [], err, binary


def _parse_all(code_texts: Sequence[Sequence[str | None]]) -> tuple[list[bool], list[bool]]:
    """Whether each Python file parses, and which of those that do not are Python 2.

    Port of ``.repro_py_parses()`` (see :func:`metacheck.repro.python._repro_py_parses`).
    """
    from metacheck.repro.python import _repro_py_parses

    res = _repro_py_parses(list(code_texts))
    parses = [bool(x) for x in res.tolist()]
    python2 = res.attrs.get("python2")
    return parses, [bool(x) for x in python2] if python2 else [False] * len(parses)


# ---------------------------------------------------------------------------
# the block
# ---------------------------------------------------------------------------


def assess_py(
    paper: Any,
    cleanup: list[str],
    a: Mapping[str, Any],
    *,
    py_files: pd.DataFrame,
    code_tbl: pd.DataFrame,
    plan: Any,
    structure_df: Any,
    version_pin: Any = None,  # noqa: ARG001 - the Python block has no recorded versions
    stat_output: list[dict[str, Any]],
    sandbox_root: str | None,
    execute: bool,
    keep_sandbox: bool = False,
    r_names: Sequence[str] = (),
) -> dict[str, Any]:
    """Assess (and with *execute*, run) the paper's Python code (R's ``if (has_py) { ... }``).

    Returns the ``py_*`` results the module combines with the R block's: ``table``,
    ``summary_table``, ``tl``, ``report`` (the section, without its heading),
    ``summary_text``, ``run_results``, ``install_results``, ``modifications``,
    ``missing_inputs``, ``n_dup``, ``stat_output`` (the given output plus what the scripts
    printed), ``sandbox_root``, ``structure_df`` and ``plan`` (changed when files were
    fetched after a run), ``recorded`` and ``recorded_advice`` (``None``: the Python block has
    no recorded versions) and ``install_r``.
    """
    from metacheck.repro.core import (
        _repro_built_names,
        repro_file_io,
        repro_missing_inputs,
        repro_rewrite_paths,
        repro_run_order,
    )
    from metacheck.repro.layout import (
        _repro_inputs_after_run,
        _repro_inputs_to_report,
        _repro_keep_working_refs,
        _repro_listed_folders,
        _repro_name_corrections,
        _repro_name_report,
        _repro_names_corrected,
        _repro_path_fix_text,
        _repro_ref_checkable,
        _repro_run_folders,
        _repro_script_path,
        _repro_script_places,
        _repro_shown_location,
        _repro_static_light,
        _repro_unique_script_names,
    )
    from metacheck.repro.outcomes import _repro_absent_report, _repro_py_starts_server
    from metacheck.repro.python import _repro_docker_image_py2, _repro_py_get_ipython
    from metacheck.repro.runsupport import _repro_not_run_report
    from metacheck.repro.software import (
        _repro_is_py_test,
        _repro_paper_text,
        _repro_py_imported,
        _repro_software_kind,
        _repro_sw_chain,
        _repro_sw_link_text,
    )

    def pid(*dfs: Any) -> str | None:
        return h._pid(paper, *dfs)

    # -- Byte-identical duplicates across repo mirrors --------------------------------------
    # Resolved and hashed as the R block does, as a separate pass: a cross-language
    # byte-identical duplicate is not a real scenario worth sharing bookkeeping over.
    resolve = rc._resolver(py_files, structure_df)
    hashes: list[str | None] = []
    for i in range(len(py_files)):
        txt = rc._read_code(resolve(i))
        hashes.append(None if not txt else "\n".join(txt))
    seen_hash: set[str] = set()
    dup: list[bool] = []
    for hv in hashes:
        dup.append(hv is not None and hv in seen_hash)
        if hv is not None:
            seen_hash.add(hv)
    n_dup = sum(dup)
    dup_report: list[Any] | None = None
    if n_dup > 0:
        fnames0 = h._chr_col(py_files, "file_name")
        dup_idx = [i for i, d in enumerate(dup) if d]
        kept_idx = [hashes.index(hashes[i]) for i in dup_idx]
        # by its link or path in the repository, as for R
        dup_loc = _repro_shown_location(py_files, dup_idx)
        kept_loc = _repro_shown_location(py_files, kept_idx)
        descs = []
        for k, i in enumerate(dup_idx):
            at = f" at `{dup_loc[k]}`" if dup_loc[k] else ""
            kept_at = f" at `{kept_loc[k]}`" if kept_loc[k] else ""
            descs.append(
                f"`{rc._na_str(fnames0[i])}`{at} "
                f"(same as `{rc._na_str(fnames0[kept_idx[k]])}`{kept_at})"
            )
        dup_report = [
            "#### Duplicate files across repo mirrors",
            f"{n_dup:d} file{plural(n_dup)} {plural(n_dup, 'is', 'are')} byte-identical to "
            "another file already in the run (the paper links more than one "
            "repository/component that mirrors the same materials). "
            f"{plural(n_dup, 'It was', 'They were')} only run once, from its first occurrence, "
            "to avoid wasted duplicate execution: " + "; ".join(descs) + ".",
        ]
        py_files = py_files.iloc[[i for i, d in enumerate(dup) if not d]].reset_index(drop=True)
    # a name of its own for each file, as for R; also apart from R's names, since an R and a
    # Python notebook can share one
    py_files = py_files.copy()
    py_files["file_name"] = pd.array(
        _repro_unique_script_names(py_files, taken=list(r_names)), dtype="string"
    )
    resolve = rc._resolver(py_files, structure_df)
    n_code = len(py_files)
    names = [str(f) for f in h._chr_col(py_files, "file_name")]

    # -- Read the files, extracting a notebook's or a Quarto document's code -------------------
    extracted = [_extract(resolve(i), names[i]) for i in range(n_code)]
    raw_texts = [e[0] for e in extracted]
    commands_removed = [e[2] for e in extracted]
    read_errors = [e[3] for e in extracted]
    is_binary = [e[4] for e in extracted]
    code_texts: list[list[str]] = []
    uses_get_ipython: list[bool] = []
    for e in extracted:
        stubbed = [x if x is not None else "NA" for x in _repro_py_get_ipython(e[1])]
        uses_get_ipython.append(stubbed != e[1])
        code_texts.append(stubbed)
    code_text_list = h.RNamedList(names, code_texts)
    raw_text_list = h.RNamedList(names, raw_texts)

    # -- Dependencies (pooled across files) ----------------------------------------------------
    manifest_text = _manifest_text(structure_df)
    deps = repro_dependencies_py(code_text_list, manifest_text)
    install_deps = deps[[not h._is_true(b) for b in h._col(deps, "base")]].reset_index(drop=True)
    # The repository's own modules (`from utils import helpers`, with utils/helpers.py in the
    # repository) are not packages: nothing to install, and a failed import of one is an error
    # in the run, not a missing package
    file_paths = h._chr_col(py_files, "file_path")
    local_paths = [
        (p if p else names[i]).replace("\\", "/")
        for i, p in enumerate(file_paths or [None] * n_code)
    ]
    local_modules = {_file_ext_stripped(p) for p in local_paths} | {
        part for p in local_paths for part in _dirname(p).split("/")
    }
    install_deps = install_deps[
        [p not in local_modules for p in h._chr_col(install_deps, "package")]
    ].reset_index(drop=True)
    n_deps = len(install_deps)

    # -- Per-file path rewrite + I/O for ordering ----------------------------------------------
    io = repro_file_io(code_text_list, "Python")
    script_paths = _repro_script_path(py_files)
    rewrites = [
        repro_rewrite_paths(
            code_texts[i],
            names[i],
            plan,
            "Python",
            structure_df=structure_df,
            script_path=script_paths[i],
        )
        for i in range(n_code)
    ]
    order_tbl = repro_run_order(io)
    script_places = _repro_script_places(py_files)
    run_dirs = _repro_run_folders(script_places, code_text_list, plan, order_tbl, "Python")
    # a reference that works as written stays as it is
    rewrites = [
        _repro_keep_working_refs(rewrites[i], plan, script_paths[i], run_dirs[i])
        for i in range(n_code)
    ]
    # a folder the script lists, where the deposit holds it (#578, item 2)
    for i in range(n_code):
        d = rewrites[i]
        lf = _repro_listed_folders(code_texts[i], plan, run_dirs[i], "Python")
        if lf is not None and len(lf):
            have_refs = set(h._chr_col(d, "ref")) if len(d) else set()
            extra = lf[[ref not in have_refs for ref in h._chr_col(lf, "ref")]]
            if len(extra):
                rewrites[i] = bind_rows([d, extra]) if len(d.columns) else extra
    rewrite_list = h.RNamedList(names, rewrites)

    rewrites_n = [
        sum(
            m and not am and not aw
            for m, am, aw in zip(
                _lgl(d, "matched"), _lgl(d, "ambiguous"), _lgl(d, "as_written"), strict=True
            )
        )
        if len(d)
        else 0
        for d in rewrites
    ]
    ambiguous_n = [sum(_lgl(d, "ambiguous")) if len(d) else 0 for d in rewrites]
    built_names = [_repro_built_names(code_texts[i], "Python") for i in range(n_code)]
    checkable = [
        _repro_ref_checkable(d, built_names[i]) if len(d) else [] for i, d in enumerate(rewrites)
    ]
    unresolved_refs = [
        b
        for i, d in enumerate(rewrites)
        if len(d)
        for b, m, ok in zip(
            h._chr_col(d, "basename"), _lgl(d, "matched"), checkable[i], strict=True
        )
        if not m and ok
    ]

    # -- Run order ---------------------------------------------------------------------------
    cycle = [str(x) for x in h._as_list(order_tbl.attrs.get("cycle"))]
    ambiguous_order = h._is_true(order_tbl.attrs.get("ambiguous"))

    # -- Missing-input diagnosis ---------------------------------------------------------------
    io_names = h._chr_col(io, "file_name")
    io_writes = h._col(io, "writes")
    produced = _unique(
        [
            None if as_str(w) is None else str(as_str(w)).lower()
            for ws in io_writes
            for w in h._as_list(ws)
        ]
    )
    candidate = [
        x
        for x in _unique([None if x is None else x.lower() for x in unresolved_refs])
        if x not in produced
    ]
    missing_inputs = repro_missing_inputs(candidate, plan, structure_df)
    n_missing = len(missing_inputs)
    n_withheld = sum(s == "withheld_size" for s in h._chr_col(missing_inputs, "status"))

    # -- Parsing ----------------------------------------------------------------------------
    # code_check only checks R files for syntax errors, so Python files are checked here,
    # with Python's own parser: it reads the code without running it.
    parses, python2 = _parse_all(code_texts)
    # A package's tests and setup.py are not the analysis, nor are the files of software, as
    # for R.
    repo_names = h._chr_col(py_files, "repo_name") if "repo_name" in py_files.columns else None
    software = _repro_software_kind(
        script_paths,
        code_text_list,
        structure_df,
        repo_names,
        paper_text=_repro_paper_text(paper),
        raw_text_list=raw_text_list,
    )
    sw_kind = h._chr_col(software, "kind")
    sw_why = h._chr_col(software, "why")
    sw_links = h._chr_col(software, "links")
    chain = _repro_sw_chain(
        sw_kind,
        names,
        io,
        imported=_repro_py_imported(code_text_list, file_paths or None),
        repos=repo_names,
        links=sw_links,
    )
    for i, c in enumerate(chain):
        if c is not None:
            sw_kind[i] = None
            sw_links[i] = c
    not_analysis = [
        _repro_is_py_test(script_paths[i] or "", code_texts[i]) or sw_kind[i] is not None
        for i in range(n_code)
    ]
    n_noparse_static = sum(not p and not na for p, na in zip(parses, not_analysis, strict=True))
    # A server waits for a connection until the time limit, so it is not run.
    server = [_repro_py_starts_server(code_texts[i]) and not not_analysis[i] for i in range(n_code)]
    # a file that could not be read, and an empty file, are not run, as for R (#620)
    from metacheck.repro.core import _repro_read_failed_msg

    read_msg = _repro_read_failed_msg(read_errors)
    not_read = [read_errors[i] is not None and not not_analysis[i] for i in range(n_code)]
    empty = [
        read_errors[i] is None
        and not not_analysis[i]
        and not is_binary[i]
        and not any(re.search(r"\S", x or "") for x in raw_texts[i])
        for i in range(n_code)
    ]

    ot_names = h._chr_col(order_tbl, "file_name")
    m_ord = rc._match_first(names, ot_names)
    file_order = rc._pick(h._col(order_tbl, "order"), m_ord)
    file_order = [None if h._na(o) else int(o) for o in file_order]
    placeable = [o is not None for o in file_order]
    # a file the script writes itself counts too: open(path, "w") names its path like a read,
    # so the script that creates the file was "missing_input" (U235)
    writes_by_file: dict[str | None, list[Any]] = {}
    for n, ws in zip(io_names, io_writes, strict=True):
        writes_by_file.setdefault(n, h._as_list(ws))

    def low(w: Any) -> str | None:
        v = as_str(w)
        return None if v is None else str(v).lower()

    produced_before: list[list[str | None]] = []
    for i in range(n_code):
        ord_i = file_order[i]
        earlier = (
            []
            if ord_i is None
            else [
                names[j]
                for j in range(n_code)
                if file_order[j] is not None and file_order[j] < ord_i
            ]
        )
        vals = [low(w) for w in writes_by_file.get(names[i], [])]
        vals += [low(w) for e in earlier for w in writes_by_file.get(e, [])]
        produced_before.append(_unique(vals))
    unresolved_list: list[list[str | None]] = []
    for i, d in enumerate(rewrites):
        if not len(d):
            unresolved_list.append([])
            continue
        bases = [None if b is None else b.lower() for b in h._chr_col(d, "basename")]
        unresolved_list.append(
            _unique(
                [
                    b
                    for b, m, ok in zip(bases, _lgl(d, "matched"), checkable[i], strict=True)
                    if not m and ok and b not in produced_before[i]
                ]
            )
        )
    file_unresolved = [len(u) > 0 for u in unresolved_list]
    runnable = [
        p and pl and not fu and not na and not nr and not em
        for p, pl, fu, na, nr, em in zip(
            parses, placeable, file_unresolved, not_analysis, not_read, empty, strict=True
        )
    ]
    n_runnable = sum(runnable)
    not_runnable_reason: list[str | None] = []
    for i in range(n_code):
        if runnable[i]:
            not_runnable_reason.append(None)
        elif not_analysis[i]:
            not_runnable_reason.append("not_analysis")
        elif not_read[i]:
            not_runnable_reason.append("not_read")
        elif empty[i]:
            not_runnable_reason.append("empty")
        elif python2[i]:
            not_runnable_reason.append("python2")
        elif not parses[i]:
            not_runnable_reason.append("parse_error")
        elif not placeable[i]:
            not_runnable_reason.append("unplaceable")
        else:
            not_runnable_reason.append("missing_input")
    unresolved_inputs = [
        ", ".join("NA" if x is None else x for x in u) if u else None for u in unresolved_list
    ]

    # -- Traffic light (static part) -------------------------------------------------------
    # Same priority structure as the R block's own static light.
    tl_clean = n_noparse_static == 0 and not cycle and not ambiguous_order and not sum(ambiguous_n)
    tl = _repro_static_light(missing_inputs, tl_clean)
    if any(not_read) and tl == "green":
        tl = "yellow"  # #620

    # -- Execution (opt-in; runs downloaded code) ------------------------------------------
    ex: dict[str, Any] = {
        "sandbox_root": sandbox_root,
        "structure_df": structure_df,
        "plan": plan,
        "stat_output": stat_output,
        "run_results": None,
        "install_results": None,
        "run_tbl": None,
        "exec_error": None,
        "definer_lookup": {},
        "not_downloaded": pd.DataFrame(),
        "lfs_stopped": {},
        "written_later": {},
        "placed": None,
        "deposit_placed": None,
        "folders_made": None,
        "ran_py2": [],
        "imported": {},
        "stat_cut": {},
    }
    if execute:
        _execute_py(
            paper,
            cleanup,
            a,
            ex,
            names=names,
            files=py_files,
            code_text_list=code_text_list,
            rewrite_list=rewrite_list,
            script_places=script_places,
            run_dirs=run_dirs,
            order_tbl=order_tbl,
            install_deps=install_deps,
            local_modules=local_modules,
            manifest_text=manifest_text,
            parses=parses,
            python2=python2,
            not_analysis=not_analysis,
            not_read=not_read,
            empty=empty,
            server=server,
            code_tbl=code_tbl,
            keep_sandbox=keep_sandbox,
            has_r=len(r_names) > 0,
        )
        sandbox_root = ex["sandbox_root"]
        structure_df, plan = ex["structure_df"], ex["plan"]
        stat_output = ex["stat_output"]
    run_results: pd.DataFrame | None = ex["run_results"]
    install_results: pd.DataFrame | None = ex["install_results"]
    run_tbl: pd.DataFrame | None = ex["run_tbl"]
    exec_error: str | None = ex["exec_error"]
    if execute:
        tl = _repro_static_light(
            _repro_inputs_after_run(missing_inputs, unresolved_list, names, run_results),
            tl_clean,
        )
        outcomes_now = h._chr_col(run_results, "outcome") if run_results is not None else []
        if any(o in ("errored", "timed_out") for o in outcomes_now):
            tl = "red"
        elif any(o in _NOT_FAILURES for o in outcomes_now) and tl == "green":
            tl = "yellow"

    # -- Per-file table --------------------------------------------------------------------
    ord_of = rc._pick(h._col(order_tbl, "order"), m_ord)
    basis_of = rc._pick(h._chr_col(order_tbl, "order_basis"), m_ord)
    dep_of = rc._pick(h._chr_col(order_tbl, "depends_on"), m_ord)
    if run_results is not None:
        m_run = rc._match_first(names, h._chr_col(run_results, "file_name"))
        outcome_of = rc._pick(h._chr_col(run_results, "outcome"), m_run)
        errtype_of = rc._pick(h._chr_col(run_results, "error_type"), m_run)
        undefvar_of = rc._pick(h._chr_col(run_results, "undefined_var"), m_run)
        reason_of = (
            rc._pick(h._chr_col(run_results, "reason"), m_run)
            if "reason" in run_results.columns
            else [None] * n_code
        )
    else:
        outcome_of = errtype_of = undefvar_of = reason_of = [None] * n_code
    from metacheck.repro.outcomes import _repro_error_message

    # the message of the failure, short (metacheck 0.4.3.20, #615)
    if run_results is not None:
        error_message_of = rc._pick(
            _repro_error_message(
                h._chr_col(run_results, "outcome"),
                h._chr_col(run_results, "error") if "error" in run_results.columns else None,
                h._chr_col(run_results, "stderr") if "stderr" in run_results.columns else None,
                python=True,
            ),
            m_run,
        )
    else:
        error_message_of = [None] * n_code
    # a file that could not be read: why (#620)
    error_message_of = [read_msg[i] if (not_read[i]) else m for i, m in enumerate(error_message_of)]
    if run_tbl is not None and "setwd_removed" in run_tbl.columns:
        setwd_of = rc._pick(
            h._col(run_tbl, "setwd_removed"),
            rc._match_first(names, h._chr_col(run_tbl, "file_name")),
        )
    else:
        setwd_of = [None] * n_code
    m_io = rc._match_first(names, io_names)
    reads_col = h._col(io, "reads")
    reads_of = [[] if j is None or not reads_col else h._as_list(reads_col[j]) for j in m_io]
    writes_of = [[] if j is None or not io_writes else h._as_list(io_writes[j]) for j in m_io]
    name_corrections = _repro_name_corrections(rewrite_list)
    link_text = _repro_sw_link_text(sw_links)
    table = pd.DataFrame(
        {
            "paper_id": rc._str_col(
                h._col(py_files, "paper_id") if "paper_id" in py_files.columns else [pid()] * n_code
            ),
            "file_name": rc._str_col(names),
            "file_path": rc._str_col(list(script_paths)),
            "parses": rc._bool_col(parses),
            "run_order": rc._int_col(ord_of),
            "order_basis": rc._str_col(basis_of),
            "depends_on": rc._str_col(dep_of),
            "paths_rewritten": rc._int_col(rewrites_n),
            "paths_ambiguous": rc._int_col(ambiguous_n),
            "setwd_removed": rc._int_col(setwd_of),
            "runnable": rc._bool_col(runnable),
            "not_runnable_reason": rc._str_col(not_runnable_reason),
            "unresolved_inputs": rc._str_col(unresolved_inputs),
            "outcome": rc._str_col(outcome_of),
            "error_type": rc._str_col(errtype_of),
            "undefined_var": rc._str_col(undefvar_of),
            "reads": rc._obj_col(reads_of),
            "writes": rc._obj_col(writes_of),
            "names_corrected": rc._str_col(_repro_names_corrected(name_corrections, names)),
            "not_analysis_why": rc._str_col(
                [w if na else None for w, na in zip(sw_why, not_analysis, strict=True)]
            ),
            "paper_link": rc._str_col(
                [None if na else t for t, na in zip(link_text, not_analysis, strict=True)]
            ),
            "repo_name": rc._str_col(repo_names if repo_names is not None else [None] * n_code),
            "reason": rc._str_col(reason_of),
            "error_message": rc._str_col(error_message_of),
            "commands_removed": rc._str_col(
                [("; ".join(x) if x else None) for x in commands_removed]
            ),
        }
    )

    # -- Report ----------------------------------------------------------------------------
    if execute:
        intro = (
            "This module also assesses whether the paper's Python code could be run on its "
            "data, and — because `execute = TRUE` — **actually ran it**. "
        )
    else:
        intro = (
            "This module also assesses whether the paper's Python code could be run on its "
            "data. It performs static analysis only here: **no code is run**. "
        )
    report: list[Any] = [intro + f"We examined {n_code:d} Python code file{plural(n_code)}."]
    report_order, order_table = _order_text(order_tbl, cycle, ambiguous_order)

    total_ambiguous = sum(ambiguous_n)
    report_paths = _repro_path_fix_text(rewrite_list, execute)
    if total_ambiguous > 0:
        report_paths += (
            f" {total_ambiguous:d} reference{plural(total_ambiguous)} matched several files "
            "sharing a name and could not be resolved by study group or folder; "
            f"{plural(total_ambiguous, 'it was', 'they were')} left unrewritten and flagged."
        )
    reported_inputs = _repro_inputs_to_report(missing_inputs, execute)
    missing_table: pd.DataFrame | None = None
    report_missing: str | None
    if len(reported_inputs) == 0:
        report_missing = (
            None
            if execute
            else "Every data file the code reads is either produced by an earlier script or "
            "present in the repository."
        )
    elif execute:
        nr = len(reported_inputs)
        report_missing = (
            f"{nr:d} input file{plural(nr)} the code reads {plural(nr, 'is', 'are')} in the "
            "deposit but not usable as named: too large to download, inside an archive, or "
            "under a similar name."
        )
        missing_table = rc._missing_frame(reported_inputs)
    else:
        report_missing = (
            f"{n_missing:d} input file{plural(n_missing)} the code reads "
            f"{plural(n_missing, 'is', 'are')} not available. Files withheld only because of "
            "their size are distinguished from files absent from the repository — the former "
            "are a size-cap issue, not a reproducibility failure."
        )
        missing_table = rc._missing_frame(missing_inputs)

    report += ["#### Run order", report_order, scroll_table(order_table, maxrows=10)]
    report += [
        "#### File paths",
        report_paths,
        *(_repro_name_report(name_corrections, execute) or []),
    ]
    if report_missing is not None:
        report += ["#### Missing inputs", report_missing]
    if missing_table is not None:
        report.append(scroll_table(missing_table, maxrows=10))
    if execute and run_results is not None:
        report += (
            _repro_absent_report(
                _repro_inputs_after_run(missing_inputs, unresolved_list, names, run_results)
            )
            or []
        )
    # Same position as the R block's own "#### Parsing" subsection. (metacheck also says so
    # when the files could not be checked, for want of a Python interpreter; they always are
    # here.)
    ran_py2: list[str] = ex["ran_py2"]
    if n_noparse_static > 0:
        report.append("#### Parsing")
        syntax = [n for n, p, t in zip(names, parses, python2, strict=True) if not p and not t]
        py2_files = [n for n, t in zip(names, python2, strict=True) if t]
        if syntax:
            report.append(
                f"{len(syntax):d} Python file{plural(len(syntax))} did not parse and cannot be "
                f"run (Python's own parser found a syntax error): {', '.join(syntax)}."
            )
        n2 = len(py2_files)
        if n2 > 0 and ran_py2:
            report.append(
                f"{n2:d} Python file{plural(n2, ' is', 's are')} written for Python 2, which has "
                f"been unsupported since 2020. We ran {plural(len(ran_py2), 'it', 'them')} with "
                f"Python 2.7 (Docker image `{_repro_docker_image_py2() or ''}`), with the last "
                f"versions of {plural(len(ran_py2), 'its', 'their')} packages that support "
                "Python 2, which can differ from the versions the authors used. We did not "
                "convert the code. Python 2 gets no more fixes, so the authors could convert "
                f"{plural(n2, 'it', 'them')} to Python 3: {', '.join(py2_files)}."
            )
        elif n2 > 0:
            report.append(
                f"{n2:d} Python file{plural(n2, ' is', 's are')} written for Python 2, which has "
                "been unsupported since 2020. We run Python 3, so "
                f"{plural(n2, 'it was', 'they were')} not run, and we do not convert code, since "
                "that can change results: `/` between whole numbers divides differently. To be "
                f"run, {plural(n2, 'it needs', 'they need')} to be converted to Python 3 by the "
                f"authors: {', '.join(py2_files)}."
            )
    report += (
        _repro_not_run_report(
            names,
            [
                "not_analysis" if na else "not_read" if nr else "empty" if em else None
                for na, nr, em in zip(not_analysis, not_read, empty, strict=True)
            ],
            why=sw_why,
            read_msg=read_msg,
        )
        or []
    )
    n_removed = sum(len(x) for x in commands_removed)
    if n_removed > 0 or any(uses_get_ipython):
        report += [
            "#### Notebook commands",
            f"To run the Python code we left out {n_removed:d} notebook "
            f"command{plural(n_removed)}, which {plural(n_removed, 'is', 'are')} not Python"
            + (
                ", and added a stand-in for `get_ipython()` that does nothing"
                if any(uses_get_ipython)
                else ""
            )
            + ". A command that installs a package or prepares a file did not run, so a later "
            "error can follow from that. A script that runs outside a notebook should not rely "
            "on them.",
        ]
        if n_removed > 0:
            report.append(
                scroll_table(
                    pd.DataFrame(
                        {
                            "File": [
                                n for n, x in zip(names, commands_removed, strict=True) for _ in x
                            ],
                            "Command": [c for x in commands_removed for c in x],
                        }
                    ),
                    maxrows=10,
                )
            )

    if run_results is not None and len(run_results) > 0:
        report += _execution_report_py(a, ex, internet=h._is_true(a.get("internet")))
    elif exec_error is not None:
        report += [
            "#### Execution",
            "**The Python code was not run.** This is a limitation of the computer running this "
            f"check, not of the paper's code: {exec_error}",
        ]
    else:
        report.append(
            "*Executing the code is a separate, opt-in phase (run with `execute = TRUE`). This "
            "report describes what a run would involve; it did not run anything.*"
        )

    # -- Summary ---------------------------------------------------------------------------
    outcomes_all = h._chr_col(run_results, "outcome") if run_results is not None else []
    n_ran_ok = outcomes_all.count("ran_ok") if run_results is not None else None
    lines: list[str] = []
    if execute and exec_error is not None:
        lines.append(
            f"We assessed {n_code:d} Python code file{plural(n_code)} for reproducibility, but "
            f"could not run {plural(n_code, 'it', 'them')}: {exec_error}"
        )
    elif execute:
        lines.append(
            f"We assessed AND ran {n_code:d} Python code file{plural(n_code)} for reproducibility."
        )
    else:
        lines.append(
            f"We assessed {n_code:d} Python code file{plural(n_code)} for reproducibility "
            "(static analysis; no code was run)."
        )
    if not execute:
        lines.append(
            f"{n_runnable:d} Python file{plural(n_runnable)} "
            f"appear{'s' if n_runnable == 1 else ''} runnable so far (parses, inputs resolve, "
            "placeable in the run order)."
        )
    if n_dup > 0:
        lines.append(
            f"{n_dup:d} Python file{plural(n_dup)} skipped as byte-identical "
            f"duplicate{plural(n_dup)} of another file already in the run."
        )
    # Files other files import are not counted, as R's sourced scripts are not.
    if execute and run_results is not None:
        n_imp = outcomes_all.count("run_by_import")
        # nor are files of software (a package's tests, __init__.py)
        n_own = len(run_results) - n_imp - outcomes_all.count("not_analysis")
        more = (
            f" ({n_imp:d} more {plural(n_imp, 'file is', 'files are')} imported by other files, "
            f"not run on {plural(n_imp, 'its', 'their')} own)"
            if n_imp > 0
            else ""
        )
        lines.append(
            f"{n_ran_ok:d} of {n_own:d} Python script{plural(n_own)} ran without error when "
            f"executed{more}."
        )
    if len(reported_inputs) > 0:
        nr = len(reported_inputs)
        lines.append(
            f"{nr:d} referenced Python input{plural(nr)} unavailable ({n_withheld:d} withheld "
            "due to size)."
        )
    lines.append(
        f"{n_deps:d} installable Python dependenc{'y' if n_deps == 1 else 'ies'} detected."
    )
    summary_text = h._paste_bullets(lines)

    summary_table = rc._block_summary(
        pid(structure_df, code_tbl),
        {
            "repro_code_n": n_code,
            "repro_runnable": n_runnable,
            "repro_missing_inputs": n_missing,
            "repro_deps": n_deps,
            "repro_ran_ok": n_ran_ok,
        },
    )
    if dup_report is not None:
        report += dup_report

    modifications = _modifications_py(
        rewrite_list,
        name_corrections,
        names=names,
        commands_removed=commands_removed,
        uses_get_ipython=uses_get_ipython,
        ex=ex if execute else None,
    )
    return {
        "table": table,
        "summary_table": summary_table,
        "tl": tl,
        "report": report,
        "summary_text": summary_text,
        "run_results": run_results,
        "install_results": install_results,
        "modifications": modifications,
        "missing_inputs": missing_inputs,
        "n_dup": n_dup,
        "stat_output": stat_output,
        "sandbox_root": sandbox_root,
        "structure_df": structure_df,
        "plan": plan,
        # the Python block has no recorded versions
        "recorded": None,
        "recorded_advice": None,
        "install_r": h._is_true(a["install_missing"]),
    }


def _order_text(
    order_tbl: pd.DataFrame, cycle: list[str], ambiguous_order: bool
) -> tuple[str, pd.DataFrame]:
    """The "Run order" text and table."""
    ordered = sorted(
        (
            (o, f, d, b)
            for o, f, d, b in zip(
                h._col(order_tbl, "order"),
                h._chr_col(order_tbl, "file_name"),
                h._chr_col(order_tbl, "depends_on"),
                h._chr_col(order_tbl, "order_basis"),
                strict=True,
            )
            if not h._na(o)
        ),
        key=lambda r: r[0],
    )
    order_table = pd.DataFrame(
        {
            "Order": rc._int_col([r[0] for r in ordered]),
            "File": rc._str_col([r[1] for r in ordered]),
            "Runs after": rc._str_col([r[2] if h._nzchar(r[2]) else "—" for r in ordered]),
            "Basis": rc._str_col([r[3] for r in ordered]),
        }
    )
    if ambiguous_order:
        text = (
            "No ordering signal (no data dependency, `source()`-equivalent, or numeric filename "
            "prefix) distinguishes the run order of these files. They may need to run in a "
            "specific order that could not be determined automatically — check this before "
            "running."
        )
    elif cycle:
        text = (
            f"A dependency **cycle** was detected among {len(cycle):d} file{plural(len(cycle))} "
            f"({', '.join(cycle)}): each reads a file another writes, so no run order satisfies "
            "all of them. This must be resolved before the code can run."
        )
    else:
        text = (
            "The scripts were ordered by their data dependencies (a script writing a file "
            "another reads runs first) and numeric filename prefixes. Python has no "
            "`source()`-equivalent import-based ordering signal (an `import` loads a module, "
            "not the paper's own analysis flow), so that basis never applies here."
        )
    return text, order_table


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def _execute_py(
    paper: Any,
    cleanup: list[str],
    a: Mapping[str, Any],
    ex: dict[str, Any],
    *,
    names: list[str],
    files: pd.DataFrame,
    code_text_list: h.RNamedList,
    rewrite_list: h.RNamedList,
    script_places: Sequence[str | None],
    run_dirs: Sequence[str],
    order_tbl: pd.DataFrame,
    install_deps: pd.DataFrame,
    local_modules: set[str],
    manifest_text: Any,
    parses: list[bool],
    python2: list[bool],
    not_analysis: list[bool],
    not_read: list[bool],
    empty: list[bool],
    server: list[bool],
    code_tbl: Any,
    keep_sandbox: bool,
    has_r: bool,
) -> None:
    """The execution phase: write the scripts, install, run, run again, label the outcomes.

    Fills *ex* (the sandbox, ``run_results``, ``install_results``, ``run_tbl``, the state the
    report and the modifications read, and the statistical output of the scripts).
    """
    from metacheck.repro.core import (
        _message,
        _repro_clock_deadline,
        _repro_deadline,
        _repro_install_limit,
        _repro_no_time_to_install,
        _repro_no_time_to_prepare,
        _repro_short_of_time,
        repro_materialize_layout,
        repro_write_scripts,
    )
    from metacheck.repro.layout import (
        _folders_frame,
        _list_files,
        _repro_fetch_files,
        _repro_fetchable,
        _repro_lfs_stopped,
        _repro_make_failed_write_folders,
        _repro_not_downloaded,
        _repro_package_plan,
        _repro_place_from_deposit,
        _repro_place_written,
        _repro_sandbox_names,
        _repro_written_later,
    )
    from metacheck.repro.outcomes import (
        _repro_docker_failed,
        _repro_failure_reason,
        _repro_needs_arguments,
        _repro_needs_internet_error,
        _repro_needs_person_error,
        _repro_out_of_memory,
        _repro_unopened_files,
    )
    from metacheck.repro.python import (
        _repro_docker_image_py2,
        _repro_find_python,
        _repro_py_defined_vars,
        _repro_py_module_folders,
        _repro_python_missing_reason,
    )
    from metacheck.repro.runsupport import (
        _repro_later_pass,
        _repro_output_head,
        _repro_py_import_links,
        _repro_py_local_closure,
    )
    from metacheck.repro.software import _repro_py_import_names, _repro_py_imported
    from metacheck.statout.stat_output import stat_output_json, stat_output_write, stat_results_long
    from metacheck.statout.stat_tables import _ipynb_is_noise, _ipynb_stat_line, _ipynb_stat_table

    sandbox = a["sandbox"]
    if sandbox not in rc._SANDBOXES:
        raise ValueError(f"unknown sandbox {sandbox!r}: expected one of {', '.join(rc._SANDBOXES)}")
    docker = sandbox == "docker"
    timeout = a["timeout"]
    total_timeout = a.get("total_timeout")
    install_missing = a["install_missing"]
    install_is_true = h._is_true(install_missing)
    state = a.get("run_state")
    if state is None:
        state = {"script_deadline": None}
    net = a.get("net")
    n_code = len(names)
    _message(
        f"[repro/py] execute = TRUE (sandbox = {sandbox}). install_missing = {install_missing}, "
        f"timeout = {timeout}s, keep_sandbox = {keep_sandbox}"
    )

    structure_df, plan = ex["structure_df"], ex["plan"]
    sandbox_root = ex["sandbox_root"]
    # Reuse the SAME sandbox_root the R block (and/or the JASP step) already materialised, if
    # any: one shared tree, not two.
    py_no_time = _repro_no_time_to_prepare(total_timeout, state)
    py_copy_deadline = time.time() if py_no_time else _repro_clock_deadline(total_timeout)
    if sandbox_root is None:
        sandbox_root = rc._new_sandbox(h._is_true(keep_sandbox), cleanup)
        repro_materialize_layout(plan, structure_df, sandbox_root, deadline=py_copy_deadline)
    elif not has_r:
        # the R block's own materialise call never ran, so a Python-only paper still needs the
        # layout built into whatever sandbox_root the JASP/jamovi step above created (that
        # step only writes statistical_output/, never the data/ tree)
        repro_materialize_layout(plan, structure_df, sandbox_root, deadline=py_copy_deadline)
    ex["sandbox_root"] = sandbox_root
    lib_dir = os.path.join(sandbox_root, "_pylib")

    _message(f"[repro/py] writing {n_code} rewritten script(s) into the layout ...")
    run_tbl = repro_write_scripts(
        code_text_list,
        rewrite_list,
        plan,
        sandbox_root,
        lang="Python",
        script_paths=list(script_places),
        writes_in_place=True,
        run_dirs=list(run_dirs),
    )
    _message(f"[repro/py]   wrote {len(run_tbl)} script(s).")
    # where each script finds the repository's own modules
    sandbox_files = [
        f
        for f in (x.replace("\\", "/") for x in _list_files(sandbox_root))
        if not re.match(r"^(_pylib|_rlib|output|statistical_output)/", f)
    ]
    file_paths = h._chr_col(files, "file_path") or None
    import_names = dict(
        zip(names, _repro_py_import_names([code_text_list[n] for n in names]), strict=True)
    )
    import_links = _repro_py_import_links(import_names, file_paths)
    root_real = os.path.realpath(sandbox_root).replace("\\", "/")
    skip_modules = {n for n, na, sv in zip(names, not_analysis, server, strict=True) if na or sv}
    folders: list[str] = []
    for fn, script_path in zip(
        _names(run_tbl, "file_name"), h._chr_col(run_tbl, "script_path"), strict=True
    ):
        # the script's imports and those of the repository's files it imports (#568); only for
        # files that run, which keeps this short in a copy of a tool
        if fn in skip_modules:
            folders.append("")
            continue
        place = (
            os.path.realpath(script_path or "").replace("\\", "/").replace(root_real + "/", "", 1)
        )
        own_mods = _repro_py_local_closure(import_names, file_paths, fn, import_links)
        code = [x for n in [fn, *own_mods] if n in code_text_list for x in code_text_list[n]]
        folders.append("\n".join(_repro_py_module_folders(code, place, sandbox_files)))
    run_tbl = run_tbl.copy()
    run_tbl["module_root"] = pd.array([sandbox_root] * len(run_tbl), dtype="string")
    run_tbl["module_folders"] = pd.array(folders, dtype="string")
    ex["run_tbl"] = run_tbl

    # The process sandbox needs a Python interpreter on this computer; sandbox = "docker" uses
    # the image's own. Without one, or when installing or running stops with an error, the
    # Python files are recorded as not run and the R results are still returned, instead of the
    # whole module stopping.
    parse_bin = "" if docker else _repro_find_python()
    if not docker and not parse_bin:
        ex["exec_error"] = _repro_python_missing_reason() + "."

    failed_deps: list[str | None] = []
    # Only the packages of files that run, and of the files they import, are installed, as for
    # R (#568): not those of a package's code, its tests, a copy of a tool or a server, which
    # are not run.
    run_set = [
        n
        for n, p, na, sv in zip(names, parses, not_analysis, server, strict=True)
        if p and not na and not sv
    ]
    imports_of = _repro_py_imported(code_text_list, file_paths)
    while True:
        more = [
            m
            for m, importers in imports_of.items()
            if any(x in run_set for x in importers) and m not in run_set
        ]
        if not more:
            break
        run_set += more
    needed = set(
        h._chr_col(
            repro_dependencies_py(
                h.RNamedList(run_set, [code_text_list[n] for n in run_set]), manifest_text
            ),
            "package",
        )
    )
    install_pkgs = h._chr_col(install_deps, "package")
    not_needed = [p for p in install_pkgs if p not in needed]
    if not_needed:
        _message(
            f"[repro/py] not installing {len(not_needed)} package(s) only files that are not "
            f"run use: {', '.join(str(p) for p in not_needed)}"
        )
    to_install = install_deps[[p in needed for p in install_pkgs]].reset_index(drop=True)
    install_results: pd.DataFrame | None = None
    if (
        ex["exec_error"] is None
        and install_is_true
        and len(to_install) > 0
        and _repro_no_time_to_install(total_timeout, state)
    ):
        # as for R: a script that fails on one of them could not run here
        _message(
            f"[repro/py] no time left to install {len(to_install)} dependenc(y/ies); "
            "skipping installs."
        )
        failed_deps = [str(p) for p in h._chr_col(to_install, "package")]
    elif ex["exec_error"] is None and install_is_true and len(to_install) > 0:
        _message(
            f"[repro/py] installing {len(to_install)} dependenc(y/ies): "
            f"{', '.join(str(p) for p in h._chr_col(to_install, 'package'))}"
        )
        limit = _repro_install_limit(a.get("install_timeout"), timeout, total_timeout)
        try:
            if docker:
                from metacheck.repro import docker as rdocker

                install_results = rdocker.repro_install_deps_py_docker(
                    to_install, lib_dir, timeout=limit
                )
            else:
                from metacheck.repro import python as rpython

                install_results = rpython.repro_install_deps_py(
                    to_install, lib_dir, python_bin=parse_bin, timeout=limit
                )
        except Exception as exc:
            ex["exec_error"] = str(exc)
            install_results = None
        if install_results is not None:
            ok = [h._is_true(v) for v in h._col(install_results, "installed")]
            _message(f"[repro/py]   install done: {sum(ok)} ok, {len(ok) - sum(ok)} failed.")
            failed_deps = [
                p for p, o in zip(h._chr_col(install_results, "package"), ok, strict=True) if not o
            ]
    else:
        _message("[repro/py] install_missing FALSE or no deps; skipping installs.")
        # as for R: nothing installed, so a missing package means the script could not run here
        if not install_is_true:
            failed_deps = list(install_pkgs)
    ex["install_results"] = install_results

    parses_named = pd.Series(
        [
            p and not na and not sv and not nr and not em
            for p, na, sv, nr, em in zip(parses, not_analysis, server, not_read, empty, strict=True)
        ],
        index=names,
        dtype=bool,
    )
    run_order_names = rc._order_names(order_tbl)
    # A module other files import is not run on its own (see .repro_py_imported()), like an R
    # script another script source()s.
    imported = dict(imports_of)
    # a file that does not parse keeps that outcome: it cannot run when imported either
    ok_parse = {n for n, p, nr in zip(names, parses, not_read, strict=True) if p and not nr}
    imported = {m: v for m, v in imported.items() if m in ok_parse}
    # a test is not run, so a module only a test imports is not run by it
    na_names = {n for n, na in zip(names, not_analysis, strict=True) if na}
    imported = {m: [x for x in v if x not in na_names] for m, v in imported.items()}
    imported = {m: v for m, v in imported.items() if v}
    ex["imported"] = imported
    if imported:
        _message("[repro/py] imported by other files, not run on their own: " + ", ".join(imported))
    run_order_names = [n for n in run_order_names if n not in imported]

    run_results: pd.DataFrame | None = None
    names_before: list[str] | None = None
    written_later: dict[str, list[str]] = {}

    placed = _repro_place_written({}, None, None, ".", [], [])
    deposit_placed = placed
    ran_py2: list[str] = []
    py2_tried: list[str] = []

    def run_py(tbl: pd.DataFrame, order: Sequence[str]) -> pd.DataFrame | None:
        try:
            if docker:
                from metacheck.repro import docker as rdocker

                return rdocker.repro_run_scripts_py_docker(
                    tbl,
                    order,
                    sandbox_root=sandbox_root,
                    lib_dir=lib_dir if os.path.isdir(lib_dir) else None,
                    timeout=timeout,
                    parses=parses_named,
                    failed_deps=failed_deps,
                    deadline=state["script_deadline"],
                    net=net,
                )
            from metacheck.repro import python as rpython

            res: pd.DataFrame = rpython.repro_run_scripts_py(
                tbl,
                order,
                lib_dir=lib_dir if os.path.isdir(lib_dir) else None,
                python_bin=parse_bin,
                timeout=timeout,
                parses=parses_named,
                failed_deps=failed_deps,
                deadline=state["script_deadline"],
            )
            return res
        except Exception as exc:
            ex["exec_error"] = str(exc)
            return None

    def order_for(redo: Sequence[str]) -> list[str]:
        return [n for n in run_order_names if n in redo] + [
            n for n in redo if n not in run_order_names
        ]

    if ex["exec_error"] is None:
        if state["script_deadline"] is None:
            state["script_deadline"] = _repro_deadline(total_timeout)
        _message(
            f"[repro/py] running {len(run_order_names)} script(s) in order: "
            + " -> ".join(run_order_names)
        )
        names_before = _repro_sandbox_names(sandbox_root)
        run_results = run_py(run_tbl, run_order_names)
        # A file a script could not open, which the repository lists but which was not
        # downloaded, is fetched now, and those scripts run once more (#568; as for R)
        if (
            run_results is not None
            and rc._download_on(a["download"])
            and any(_repro_fetchable(structure_df))
        ):
            outcome0 = h._chr_col(run_results, "outcome")
            err0 = h._chr_col(run_results, "error")
            se0 = h._chr_col(run_results, "stderr")
            stopped = [o == "errored" for o in outcome0]
            unopened = _repro_unopened_files(
                [_paste2(err0[i], se0[i]) for i in range(len(run_results)) if stopped[i]]
            )
            lower_unopened = {u.lower() for u in unopened}
            fetchable = _repro_fetchable(structure_df)
            sd_names = h._chr_col(structure_df, "file_name")
            rows = [
                f
                and n is not None
                and str(h._basename(n.replace("\\", "/"))).lower() in lower_unopened
                for f, n in zip(fetchable, sd_names, strict=True)
            ]
            # (not when the time is up: the scripts would not be run again)
            if any(rows) and not _repro_short_of_time(total_timeout, 60):
                _message(f"[repro/py] fetching {sum(rows)} file(s) scripts could not open")
                structure_df = _repro_fetch_files(
                    structure_df,
                    rows,
                    a["max_file_size"],
                    a["max_download_size"],
                    a["cache"],
                    a["skip_on_api_limit"],
                )
                locs = h._chr_col(structure_df, "file_location")
                got = [r and loc is not None for r, loc in zip(rows, locs, strict=True)]
                if any(got):
                    plan = _repro_package_plan(structure_df)
                    got_locs = {loc for g, loc in zip(got, locs, strict=True) if g}
                    sources = h._chr_col(plan, "source")
                    repro_materialize_layout(
                        plan[[s is not None and s in got_locs for s in sources]],
                        structure_df,
                        sandbox_root,
                        deadline=_repro_clock_deadline(total_timeout),
                    )
                    sd_names = h._chr_col(structure_df, "file_name")
                    got_names = [str(n).lower() for g, n in zip(got, sd_names, strict=True) if g]
                    fnames = _names(run_results, "file_name")
                    redo = [
                        fnames[i]
                        for i in range(len(run_results))
                        if stopped[i]
                        and any(gn in _paste2(err0[i], se0[i]).lower() for gn in got_names)
                    ]
                    if redo:
                        res3 = _repro_later_pass(
                            run_py(run_tbl[run_tbl["file_name"].isin(redo)], order_for(redo))
                        )
                        if res3 is not None:
                            # a script with its file fetched but no time is skipped_time_limit,
                            # not the missing-file failure the fetch was meant to fix (#581)
                            run_results = _replace_rows(run_results, res3, run_order_names)
        ex["structure_df"], ex["plan"] = structure_df, plan

    # A script that stopped on a file another script wrote later runs once more, as for R
    # (#568): in 10.1111/2041-210X.13814 "analyse_results.py" ran before "create_inputs.py",
    # which writes f"input{i}.txt". A file the deposit holds in another folder is copied to
    # where a script looked, and a script runs again each time it stops on a new file (#575,
    # items 2 and 4; see the R loop).
    folders_made = _folders_frame([])
    if run_results is not None and names_before is not None:
        seen: dict[str, list[str]] = {}
        # a folder a write stopped on is made (metacheck 0.4.3.12, #591; see the R loop)
        for _pass in range(5):
            names_after = _repro_sandbox_names(sandbox_root)
            later = _repro_written_later(run_results, names_before, names_after)
            later = {
                s: files_
                for s, files_ in later.items()
                if {f.lower() for f in files_} - {f.lower() for f in seen.get(s, [])}
            }
            # a file written in another folder is copied to where the script looks
            placed = _rbind(
                placed,
                _repro_place_written(
                    later, run_results, run_tbl, sandbox_root, names_before, names_after
                ),
            )
            from_deposit = _repro_place_from_deposit(
                run_results, run_tbl, sandbox_root, names_before
            )
            deposit_placed = _rbind(deposit_placed, from_deposit)
            made_by: dict[str, list[str]] = {}
            for s_, f_ in zip(
                _names(folders_made, "script"), _names(folders_made, "folder"), strict=True
            ):
                made_by.setdefault(s_, []).append(f_)
            made = _repro_make_failed_write_folders(run_results, run_tbl, sandbox_root, made_by)
            folders_made = _rbind(folders_made, made)
            redo = _unique([*later, *_names(from_deposit, "script"), *_names(made, "script")])
            if not redo:
                break
            _message(
                f"[repro/py] re-running {len(redo)} script(s) that stopped on a file another "
                "script wrote, the deposit holds elsewhere, or a folder a write needed: "
                + ", ".join(redo)
            )
            res4 = _repro_later_pass(
                run_py(run_tbl[run_tbl["file_name"].isin(redo)], order_for(redo))
            )
            if res4 is None:
                break
            ran4 = [
                n
                for n, o in zip(_names(res4, "file_name"), h._chr_col(res4, "outcome"), strict=True)
                if o != "skipped_time_limit"
            ]
            for s in [s for s in later if s in ran4]:
                written_later[s] = _unique([*written_later.get(s, []), *later[s]])
                seen[s] = _unique([*seen.get(s, []), *later[s]])
            # a script this pass was to run but had no time for is skipped_time_limit, not the
            # failure the pass was meant to fix (#581)
            run_results = _replace_rows(run_results, res4, h._chr_col(run_results, "file_name"))
            if not ran4:
                break

    # Code written for Python 2 runs again with Python 2.7, with its own packages in _pylib2:
    # the last versions that support Python 2 (#568). The code is not converted, since that can
    # change results (`/` between whole numbers). A script that stopped on a Python 2 module it
    # imports is run this way too.
    py2_image = _repro_docker_image_py2() if docker else None
    if run_results is not None and py2_image is not None:
        py2_set = {n for n, t in zip(names, python2, strict=True) if t}
        skip_run = {n for n in names if n in skip_modules}
        py2_flag = _py2_flags(run_results, py2_set)
        py2_names = [
            n
            for n, f in zip(_names(run_results, "file_name"), py2_flag, strict=True)
            if f and n not in skip_run
        ]
        if py2_names:
            _message(f"[repro/py] running {len(py2_names)} Python 2 script(s) with {py2_image}")
            lib2 = os.path.join(sandbox_root, "_pylib2")
            py2_failed: list[str | None] = []
            if install_is_true:
                py2_deps = repro_dependencies_py(
                    h.RNamedList(py2_names, [code_text_list[n] for n in py2_names]), manifest_text
                )
                py2_deps = py2_deps[
                    [
                        not h._is_true(b) and p not in local_modules
                        for p, b in zip(
                            h._chr_col(py2_deps, "package"), h._col(py2_deps, "base"), strict=True
                        )
                    ]
                ].reset_index(drop=True)
                _message(
                    f"[repro/py] installing {len(py2_deps)} Python 2 dependenc(y/ies): "
                    + ", ".join(str(p) for p in h._chr_col(py2_deps, "package"))
                )
                if len(py2_deps):
                    from metacheck.repro import docker as rdocker

                    try:
                        py2_install: pd.DataFrame | None = rdocker.repro_install_deps_py_docker(
                            py2_deps,
                            lib2,
                            image=py2_image,
                            timeout=_repro_install_limit(
                                a.get("install_timeout"), timeout, total_timeout
                            ),
                        )
                    except Exception as exc:
                        _message(f"[repro/py]   Python 2 install stopped: {exc}")
                        py2_install = None
                    if py2_install is None:
                        py2_failed = list(h._chr_col(py2_deps, "package"))
                    else:
                        py2_failed = [
                            p
                            for p, o in zip(
                                h._chr_col(py2_install, "package"),
                                h._col(py2_install, "installed"),
                                strict=True,
                            )
                            if not h._is_true(o)
                        ]
                    _message(
                        "[repro/py]   Python 2 install done; not installed: "
                        + (", ".join(str(p) for p in py2_failed) if py2_failed else "none")
                    )
            res2: pd.DataFrame | None
            try:
                from metacheck.repro import docker as rdocker

                res2 = rdocker.repro_run_scripts_py_docker(
                    run_tbl[run_tbl["file_name"].isin(py2_names)],
                    [n for n in run_order_names if n in py2_names],
                    sandbox_root=sandbox_root,
                    lib_dir=lib2 if os.path.isdir(lib2) else None,
                    image=py2_image,
                    timeout=timeout,
                    failed_deps=py2_failed,
                    deadline=state["script_deadline"],
                    net=net,
                )
            except Exception:
                res2 = None
            res2 = _repro_later_pass(res2)  # see .repro_later_pass() (#581)
            if res2 is not None and len(res2):
                # a script not started for lack of time says so, not "python2" (#568)
                ran_py2 = [
                    n
                    for n, o in zip(
                        _names(res2, "file_name"), h._chr_col(res2, "outcome"), strict=True
                    )
                    if o != "skipped_time_limit"
                ]
                py2_tried = _names(res2, "file_name")
                run_results = _replace_rows(run_results, res2, h._chr_col(run_results, "file_name"))

    if run_results is not None and imported:
        run_results = run_results[~run_results["file_name"].isin(list(imported))]
        run_results = bind_rows(
            [
                run_results,
                pd.DataFrame(
                    {
                        "file_name": pd.array(list(imported), dtype="string"),
                        "outcome": pd.array(["run_by_import"] * len(imported), dtype="string"),
                        "error": pd.array([""] * len(imported), dtype="string"),
                        "stdout": pd.array([""] * len(imported), dtype="string"),
                        "stderr": pd.array([""] * len(imported), dtype="string"),
                    }
                ),
            ]
        )

    # Python 2 code gets an outcome of its own: the parser's verdict, or, with no Python on
    # this computer or for a script importing such a file, the error.
    not_downloaded = pd.DataFrame({"script": pd.array([], dtype="string")})
    lfs_stopped: dict[str, list[str]] = {}
    if run_results is not None:
        run_results = run_results.copy()
        fn_col = _names(run_results, "file_name")
        outcome = h._chr_col(run_results, "outcome")
        error = h._chr_col(run_results, "error")
        stderr = h._chr_col(run_results, "stderr")
        stdout = h._chr_col(run_results, "stdout")
        py2_set = {n for n, t in zip(names, python2, strict=True) if t}
        na_set = {n for n, na in zip(names, not_analysis, strict=True) if na}
        server_set = {n for n, sv in zip(names, server, strict=True) if sv}
        not_read_set = {n for n, nr in zip(names, not_read, strict=True) if nr}
        empty_set = {n for n, em in zip(names, empty, strict=True) if em}
        flag = _py2_flags(run_results, py2_set)
        for i in range(len(run_results)):
            if flag[i] and fn_col[i] not in py2_tried:
                outcome[i] = "python2"
        for i in range(len(run_results)):
            if fn_col[i] in na_set:
                outcome[i] = "not_analysis"
        for i in range(len(run_results)):
            if fn_col[i] in not_read_set:
                outcome[i] = "not_read"
        for i in range(len(run_results)):
            if fn_col[i] in empty_set:
                outcome[i] = "empty"
        for i in range(len(run_results)):
            if fn_col[i] in server_set:
                outcome[i] = "needs_a_person"
        msgs = [_paste2(error[i], stderr[i]) for i in range(len(run_results))]
        errored = [o == "errored" for o in outcome]
        if docker:
            failed_flag = _repro_docker_failed(msgs)
            for i in range(len(outcome)):
                if errored[i] and failed_flag[i]:
                    outcome[i] = "docker_failed"
            errored = [o == "errored" for o in outcome]
        person = _repro_needs_person_error(msgs)
        # a program that needs its arguments from the person running it (#568)
        args_flag = _repro_needs_arguments(
            [code_text_list.get(n, []) for n in fn_col],
            msgs,
            "Python",
            out=stdout,
        )
        for i in range(len(outcome)):
            if errored[i] and (person[i] or args_flag[i]):
                outcome[i] = "needs_a_person"
        errored = [o == "errored" for o in outcome]
        if docker:
            net_flag = _repro_needs_internet_error(msgs)
            for i in range(len(outcome)):
                if errored[i] and net_flag[i]:
                    outcome[i] = "needs_internet"
            errored = [o == "errored" for o in outcome]
        # ran out of memory (#578, item 8; as for R)
        oom = _repro_out_of_memory(msgs)
        for i in range(len(outcome)):
            if errored[i] and oom[i]:
                outcome[i] = "out_of_memory"
        run_results["outcome"] = pd.array(outcome, dtype="string")
        # stopped on a file we did not download (#568; as for R)
        not_downloaded = _repro_not_downloaded(run_results, structure_df)
        nd_scripts = set(h._chr_col(not_downloaded, "script"))
        for i in range(len(outcome)):
            if fn_col[i] in nd_scripts:
                outcome[i] = "not_downloaded"
        no_message = [
            o == "errored" and not re.search(r"\S", (e or "") + (s or ""))
            for o, e, s in zip(outcome, error, stderr, strict=True)
        ]
        for i in range(len(outcome)):
            if no_message[i]:
                error[i] = "the script stopped without a message"
        run_results["outcome"] = pd.array(outcome, dtype="string")
        run_results["error"] = pd.array(error, dtype="string")
        reason = _repro_failure_reason(
            outcome, [_paste2(error[i], stderr[i]) for i in range(len(outcome))]
        )
        lfs_stopped = _repro_lfs_stopped(run_results, sandbox_root)
        reason = ["file" if fn_col[i] in lfs_stopped else r for i, r in enumerate(reason)]
        run_results["reason"] = pd.array(reason, dtype="string")
        _message(
            "[repro/py] execution finished. outcomes: "
            + "; ".join(f"{f}={o}" for f, o in zip(fn_col, outcome, strict=True))
        )
    else:
        _message(f"[repro/py] Python code not run: {ex['exec_error']}")
    ex["run_results"] = run_results
    ex["not_downloaded"] = not_downloaded
    ex["lfs_stopped"] = lfs_stopped
    ex["written_later"] = written_later
    ex["placed"] = placed
    ex["deposit_placed"] = deposit_placed
    ex["folders_made"] = folders_made
    ex["ran_py2"] = ran_py2

    # A NameError usually means the script expects a name another of the paper's scripts
    # defines. Unlike the R path, there is no re-run in a corrected order: every Python script
    # runs in its own `python` process, so a name another script defines is never available to
    # it, whatever the order. The report names the script that defines the name instead.
    definer_lookup: dict[str, str] = {}
    if run_results is not None:
        undef = [
            (u, v)
            for u, t, v in zip(
                h._chr_col(run_results, "file_name"),
                h._chr_col(run_results, "error_type"),
                h._chr_col(run_results, "undefined_var"),
                strict=True,
            )
            if t == "undefined_variable"
        ]
        if undef:
            defs = _repro_py_defined_vars(code_text_list)
            def_sets = list(
                zip(
                    h._chr_col(defs, "file_name"),
                    (h._as_list(d) for d in h._col(defs, "defines")),
                    strict=True,
                )
            )
            for user, v in undef:
                definers = _unique([n for n, d in def_sets if v in d and n != user])
                if definers:
                    definer_lookup[f"{rc._na_str(user)}||{rc._na_str(v)}"] = ", ".join(
                        rc._na_str(d) for d in definers
                    )
    ex["definer_lookup"] = definer_lookup

    # Statistical output extraction from Python stdout. Mirrors the R block's own
    # r_stat_output step, but TEXT-PARSED ONLY: Python has no object-capture analogue
    # (`captures` is always NULL per row from both Python run-script backends), so just
    # .ipynb_stat_line()/.ipynb_stat_table() against each script's captured stdout.
    # Deliberately NO raw-text fallback when neither parses (consistent with read_r_output()'s
    # own behaviour on the R side: it returns list() for unparseable console output).
    stat_cut: dict[str, Any] = {}
    py_stat_output: list[dict[str, Any]] = []
    pid = h._pid(paper, structure_df, code_tbl)
    if run_results is not None:
        for fn, so in zip(
            _names(run_results, "file_name"), h._chr_col(run_results, "stdout"), strict=True
        ):
            if not so:
                continue
            lines = list(strsplit(so, "\n", fixed=True))
            _message(f"[repro/py] parsing output of '{fn}' ({len(lines)} lines) ...")
            lines = _repro_output_head(lines)
            n_lines = getattr(lines, "n_lines", None)
            if n_lines is not None:
                stat_cut[str(fn)] = n_lines
            lines = [x for x in lines if trimws(x) != ""]
            if not lines or _ipynb_is_noise(lines):
                continue
            try:
                tabs = _ipynb_stat_line(lines) or _ipynb_stat_table(lines)
            except Exception:
                tabs = None
            if not tabs:
                continue
            py_stat_output.append(
                {
                    "file": fn,
                    "n_tables": len(tabs),
                    "source": "python_output",
                    "json": stat_output_json(tabs, paper_id=pid, source_file=fn),
                    "long": stat_results_long(tabs, paper_id=pid, source_file=fn),
                }
            )
    ex["stat_cut"] = stat_cut
    if py_stat_output:
        _message(
            f"[repro/py] extracted statistical output from {len(py_stat_output)} script(s)' "
            "console output."
        )
        ex["stat_output"] = [*(ex["stat_output"] or []), *py_stat_output]
        stat_output_write(ex["stat_output"], sandbox_root)


def _py2_flags(run_results: pd.DataFrame, py2_names: set[str]) -> list[bool]:
    """Which result rows are Python 2 code: the parser's verdict, or the error it ended in."""
    from metacheck.repro.python import _repro_py2_error

    fn = _names(run_results, "file_name")
    outcome = h._chr_col(run_results, "outcome")
    stderr = h._chr_col(run_results, "stderr")
    error = h._chr_col(run_results, "error")
    by_error = _repro_py2_error([_paste2(stderr[i], error[i]) for i in range(len(run_results))])
    return [
        (fn[i] in py2_names or (outcome[i] == "errored" and bool(by_error[i])))
        for i in range(len(run_results))
    ]


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def _execution_report_py(
    a: Mapping[str, Any],
    ex: Mapping[str, Any],
    *,
    internet: bool,
) -> list[Any]:
    """The Python section's "Execution" subsection (R's ``py_report`` after a run)."""
    from metacheck.repro.core import _repro_deadline_text, _repro_no_prep_text
    from metacheck.repro.layout import (
        _repro_folders_made_text,
        _repro_lfs_stopped_text,
        _repro_not_downloaded_text,
        _repro_placed_text,
        _repro_slash_report,
        _repro_written_later_text,
    )
    from metacheck.repro.outcomes import (
        _repro_download_stop_text,
        _repro_internet_text,
        _repro_needs_internet_text,
    )
    from metacheck.repro.runsupport import _repro_output_cut_note

    run_results: pd.DataFrame = ex["run_results"]
    install_results: pd.DataFrame | None = ex["install_results"]
    run_tbl: pd.DataFrame | None = ex["run_tbl"]
    sandbox = a["sandbox"]
    oc = h._chr_col(run_results, "outcome")
    rnames = _names(run_results, "file_name")
    n_run = len(run_results)

    def count(*kinds: str) -> int:
        return sum(o in kinds for o in oc)

    n_ok, n_err, n_timeout = count("ran_ok"), count("errored"), count("timed_out")
    n_noparse, n_py2, n_nodep = (
        count("not_parsed"),
        count("python2"),
        count("dependency_unavailable"),
    )
    n_import, n_late = count("run_by_import"), count("skipped_time_limit")
    n_scripts = sum(o not in ("not_analysis", "not_downloaded", "empty") for o in oc)
    imported: Mapping[str, Sequence[str]] = ex["imported"]
    import_text = (
        f" {n_import:d} {plural(n_import, 'file was', 'files were')} not run on "
        f"{plural(n_import, 'its', 'their')} own, because other files import "
        f"{plural(n_import, 'it', 'them')}: "
        + "; ".join(f"{m} (by {', '.join(v)})" for m, v in imported.items())
        + "."
        if n_import
        else ""
    )
    report_exec = (
        "**The Python code was run.** Each script ran in an isolated "
        f"{'Docker container' if sandbox == 'docker' else 'subprocess'} against a throwaway "
        f"copy of the repository, in the run order above, with a "
        f"{rc._sprintf_d(a['timeout'])}-second per-script timeout. Of {n_scripts:d} "
        f"script{plural(n_scripts)}: {n_ok:d} ran without error, {n_err:d} errored, "
        f"{n_timeout:d} timed out, {n_nodep:d} could not run because one of its own "
        f"dependencies is unavailable, {n_noparse:d} did not parse, and {n_py2:d} "
        f"{plural(n_py2, 'is', 'are')} written for Python 2 and not run.{import_text} Running "
        "against current package versions: a break can reflect version drift, which argues for "
        "pinning versions (e.g. in a `requirements.txt`)."
    )
    # scripts not started because the time for all scripts together ran out
    if n_late > 0:
        report_exec += (
            f" {n_late:d} {plural(n_late, 'script was', 'scripts were')} not started, or stopped "
            f"early, because {_repro_deadline_text(a.get('total_timeout'))} had run out."
            f"{_repro_no_prep_text((a.get('run_state') or {}).get('prep_skipped_left'))}"
        )
    written_later: Mapping[str, Sequence[str]] = ex["written_later"]
    placed, deposit_placed = ex["placed"], ex["deposit_placed"]
    if written_later:
        report_exec += _repro_written_later_text(
            written_later, [n for n, o in zip(rnames, oc, strict=True) if o == "ran_ok"], placed
        )
    if deposit_placed is not None and len(deposit_placed):
        report_exec += _repro_placed_text(deposit_placed)
    folders_made = ex["folders_made"]
    if folders_made is not None and len(folders_made):
        report_exec += _repro_folders_made_text(folders_made)
    if ex["lfs_stopped"]:
        report_exec += _repro_lfs_stopped_text(ex["lfs_stopped"])
    ran_py2_here = [n for n in rnames if n in ex["ran_py2"]]
    if ran_py2_here:
        k = len(ran_py2_here)
        report_exec += (
            f" {k:d} {plural(k, 'script is', 'scripts are')} written for Python 2, so "
            f"{plural(k, 'it was', 'they were')} run with Python 2.7 instead of Python 3: "
            f"{', '.join(ran_py2_here)}."
        )
    n_docker = count("docker_failed")
    if n_docker > 0:
        report_exec += (
            f" **Docker failed for {n_docker:d} script{plural(n_docker)}, so "
            f"{plural(n_docker, 'it was', 'they were')} not run.** This says nothing about the "
            "code; run the check again when Docker works."
        )
    if internet:
        report_exec += " " + _repro_internet_text(a["net"])
    needs_net = [n for n, o in zip(rnames, oc, strict=True) if o == "needs_internet"]
    if needs_net:
        report_exec += _repro_needs_internet_text(needs_net, internet)
    too_large = [n for n, o in zip(rnames, oc, strict=True) if o == "download_too_large"]
    if too_large:
        report_exec += _repro_download_stop_text(too_large)
    not_dl_names = {n for n, o in zip(rnames, oc, strict=True) if o == "not_downloaded"}
    if not_dl_names:
        nd = ex["not_downloaded"]
        sub_nd = nd[[s in not_dl_names for s in h._chr_col(nd, "script")]]
        report_exec += _repro_not_downloaded_text(sub_nd, a["max_file_size"]) or ""

    report: list[Any] = ["#### Execution", report_exec]
    elapsed = h._col(run_results, "elapsed")
    err_types = h._chr_col(run_results, "error_type")
    report.append(
        scroll_table(
            pd.DataFrame(
                {
                    "File": rnames,
                    "Outcome": oc,
                    "Detail": ["" if t is None else t for t in err_types],
                    "Time (s)": [None if h._na(e) else r_round(float(e), 1) for e in elapsed],
                }
            ),
            maxrows=15,
        )
    )

    undef = [i for i, t in enumerate(err_types) if t == "undefined_variable"]
    if undef:
        nu = len(undef)
        undef_vars = h._chr_col(run_results, "undefined_var")
        lookup: Mapping[str, str] = ex["definer_lookup"]
        defined_in = [
            lookup.get(f"{rc._na_str(rnames[i])}||{rc._na_str(undef_vars[i])}", "") for i in undef
        ]
        report += [
            "**Undefined-name errors**",
            f"**{nu:d} script{plural(nu)} failed on an undefined name** (`NameError`). This "
            "typically means the script expects a variable, function, or class that another "
            "script defines — i.e. it is meant to be run alongside a larger set of scripts, not "
            "on its own — or the name is simply never created. When another of the paper's own "
            "script files defines the same name, this is called out below as a likely "
            "ordering/copy-paste gap. Each is counted as an error (red).",
            scroll_table(
                pd.DataFrame(
                    {
                        "File": [rnames[i] for i in undef],
                        "Missing name": [undef_vars[i] for i in undef],
                        "Defined in": [
                            d if d != "" else "(not defined anywhere in the paper's code)"
                            for d in defined_in
                        ],
                    }
                ),
                maxrows=10,
            ),
        ]
    nodep = [i for i, t in enumerate(err_types) if t == "dependency_unavailable"]
    if nodep:
        nn = len(nodep)
        report += [
            "**Dependency-unavailable errors**",
            f"**{nn:d} script{plural(nn)} could not run because {plural(nn, 'its', 'their')} own "
            "dependency is unavailable** — the package failed to `pip install`. This is an "
            "infrastructure limitation, not a defect in the paper's code, so it does **not** "
            "force the traffic light red.",
            scroll_table(pd.DataFrame({"File": [rnames[i] for i in nodep]}), maxrows=10),
        ]
    if run_tbl is not None and "setwd_removed" in run_tbl.columns:
        hit = [not h._na(v) and v > 0 for v in h._col(run_tbl, "setwd_removed")]
        files = [f for f, x in zip(h._chr_col(run_tbl, "file_name"), hit, strict=True) if x]
        if files:
            ns = len(files)
            ok_names = {n for n, o in zip(rnames, oc, strict=True) if o == "ran_ok"}
            ok_after = [f for f in _unique(files) if f in ok_names]
            report += [
                "**`os.chdir()` in the code**",
                f"**{ns:d} script{plural(ns)} call{plural(ns, 's', '')} `os.chdir()`.** This is "
                "bad practice: it hardcodes where the code must run (often an absolute path on "
                "the author's own machine) and breaks the moment the code is run anywhere else. "
                "To run the code we replaced these calls with `pass`. Any such script that then "
                "ran is **reproducible only after ignoring an `os.chdir()` that should not have "
                "been in the code and needs to be fixed**.",
            ]
            if ok_after:
                report.append(
                    "Reproducible after removing `os.chdir()`: "
                    + ", ".join(rc._na_str(f) for f in ok_after)
                    + "."
                )
            paths = [p for p, x in zip(h._chr_col(run_tbl, "setwd_paths"), hit, strict=True) if x]
            counts = [v for v, x in zip(h._col(run_tbl, "setwd_removed"), hit, strict=True) if x]
            report.append(
                scroll_table(
                    pd.DataFrame(
                        {
                            "File": files,
                            "os.chdir() removed": rc._int_col(counts).tolist(),
                            "Path(s) it set": [
                                p if h._nzchar(p) and p != "" else "(non-literal)" for p in paths
                            ],
                        }
                    ),
                    maxrows=10,
                )
            )
    if run_tbl is not None:
        report += _repro_slash_report(run_tbl) or []

    state = {"truncated": False}
    errors = h._chr_col(run_results, "error")
    stdouts = h._col(run_results, "stdout")
    stderrs = h._col(run_results, "stderr")
    blocks: list[Any] = []
    for i in range(n_run):
        so = rc._tail_cap(stdouts[i], state)
        se = rc._tail_cap(stderrs[i], state)
        if not so and not se and not h._nzchar(errors[i]):
            continue
        err_text = rc._na_str(errors[i])
        fence = rc._fence(so, se, [err_text])
        body: list[str] = []
        if so:
            body += ["**Output (stdout):**", fence, *so, fence]
        if se:
            body += ["**Messages / errors (stderr):**", fence, *se, fence]
        if not body:
            body = [fence, err_text, fence]
        blocks.append(
            collapse_section(
                "\n".join(body), title=f"Output — {rc._na_str(rnames[i])} ({rc._na_str(oc[i])})"
            )
        )
    if blocks:
        report.append("*Expand a row below to see the output each script produced.*")
        report += blocks
    if state["truncated"]:
        report.append(
            f"*Output shown here is capped at the last {rc._MAX_LINES:d} lines per stream to keep "
            "the report readable. The complete, untruncated output of every script is stored in "
            "the module result: `reproducibility_check_output$run_results$stdout` (and "
            "`$stderr`), one row per script.*"
        )
    cut_note = _repro_output_cut_note(ex["stat_cut"])
    if cut_note is not None:
        report.append(cut_note)

    if install_results is not None and len(install_results) > 0:
        installed = [h._is_true(v) for v in h._col(install_results, "installed")]
        pkgs = h._chr_col(install_results, "package")
        n_ok_i = sum(installed)
        report.append(
            "Dependencies were installed into a throwaway library before running (`pip "
            f"install`): {n_ok_i:d} succeeded, {len(installed) - n_ok_i:d} failed. "
            + ", ".join(rc._na_str(p) for p in pkgs)
            + " were installed."
        )
        failed = [i for i, ok in enumerate(installed) if not ok]
        if failed:
            msgs = h._chr_col(install_results, "message")
            report.append(
                "**Failed:** "
                + "; ".join(
                    rc._na_str(pkgs[i]) + (f" ({msgs[i]})" if h._nzchar(msgs[i]) else "")
                    for i in failed
                )
            )
    return report


# ---------------------------------------------------------------------------
# modifications
# ---------------------------------------------------------------------------


def _modifications_py(
    rewrite_list: h.RNamedList,
    name_corrections: pd.DataFrame,
    *,
    names: Sequence[str],
    commands_removed: Sequence[Sequence[str]],
    uses_get_ipython: Sequence[bool],
    ex: Mapping[str, Any] | None,
) -> pd.DataFrame:
    """The Python block's ``modifications``: one row per change made to the scripts.

    Path rewrites, name corrections and what was left out of a notebook come from the static
    analysis; what writing and running the scripts changed only exists when they ran.
    """
    from metacheck.repro.layout import _repro_slash_rows
    from metacheck.repro.python import _repro_docker_image_py2

    rows: list[tuple[Any, str, Any]] = []
    for fn in rewrite_list:
        d = rewrite_list[fn]
        if d is None or not len(d):
            continue
        for ref, tgt, m, am in zip(
            h._chr_col(d, "ref"),
            h._chr_col(d, "target"),
            h._col(d, "matched"),
            h._col(d, "ambiguous"),
            strict=True,
        ):
            if h._is_true(m) and not h._is_true(am) and tgt:
                rows.append((fn, "path_rewrite", f'"{rc._na_str(ref)}" -> "{tgt}"'))
    for cfn, in_code, in_dep in zip(
        h._chr_col(name_corrections, "file_name"),
        h._chr_col(name_corrections, "name_in_code"),
        h._chr_col(name_corrections, "name_in_deposit"),
        strict=True,
    ):
        rows.append((cfn, "name_corrected", f'"{rc._na_str(in_code)}" -> "{rc._na_str(in_dep)}"'))
    # notebook commands left out, and the stand-in for get_ipython(), for the authors
    for fn, removed in zip(names, commands_removed, strict=True):
        rows += [(fn, "notebook_command_removed", c) for c in removed]
    rows += [
        (fn, "get_ipython_added", "a stand-in for get_ipython() that does nothing")
        for fn, u in zip(names, uses_get_ipython, strict=True)
        if u
    ]

    extra: list[pd.DataFrame] = []
    if ex is not None:
        later_rows: list[tuple[Any, str, Any]] = []
        setwd_rows: list[tuple[Any, str, Any]] = []
        run_tbl = ex.get("run_tbl")
        # setwd_removed is also the change_type for os.chdir(): the same concept (the
        # working-directory override stripped before running), and what reads this column
        # keys on its change_type strings
        if run_tbl is not None and "setwd_paths" in run_tbl.columns:
            for sfn, n, p in zip(
                h._chr_col(run_tbl, "file_name"),
                h._col(run_tbl, "setwd_removed"),
                h._chr_col(run_tbl, "setwd_paths"),
                strict=True,
            ):
                if not h._na(n) and n > 0 and p:
                    setwd_rows += [(sfn, "setwd_removed", x) for x in strsplit(p, ", ", fixed=True)]
        slash = _repro_slash_rows(run_tbl) if run_tbl is not None else None
        py2_rows = [
            (
                fn,
                "python2",
                f"run with Python 2.7 ({_repro_docker_image_py2() or ''})",
            )
            for fn in ex.get("ran_py2") or []
        ]
        for fn, files in (ex.get("written_later") or {}).items():
            later_rows += [
                (fn, "run_again", f"run once more after another script wrote {x}") for x in files
            ]
        placed = ex.get("placed")
        if placed is not None and len(placed):
            later_rows += [
                (s, "output_copied", f"copied {f}, written by another script, to {t}")
                for s, f, t in zip(
                    h._chr_col(placed, "script"),
                    h._chr_col(placed, "from"),
                    h._chr_col(placed, "to"),
                    strict=True,
                )
            ]
        dep_placed = ex.get("deposit_placed")
        if dep_placed is not None and len(dep_placed):
            later_rows += [
                (
                    s,
                    "file_placed",
                    f"copied {f}, of the deposit, to {t}, where the script looked, and ran it "
                    "once more",
                )
                for s, f, t in zip(
                    h._chr_col(dep_placed, "script"),
                    h._chr_col(dep_placed, "from"),
                    h._chr_col(dep_placed, "to"),
                    strict=True,
                )
            ]
        made_rows = ex.get("folders_made")
        if made_rows is not None and len(made_rows):
            later_rows += [
                (
                    s,
                    "folder_made",
                    f"made the folder {f}, where the script writes, and ran it once more",
                )
                for s, f in zip(
                    h._chr_col(made_rows, "script"), h._chr_col(made_rows, "folder"), strict=True
                )
            ]
        # bind_rows(py_rw_rows, setwd, slash, python2, later)
        extra = [
            part[["file_name", "change_type", "detail"]]
            for part in (
                rc._mod_frame(setwd_rows),
                slash,
                rc._mod_frame(py2_rows),
                rc._mod_frame(later_rows),
            )
            if part is not None and len(part)
        ]
    frames = [f for f in (rc._mod_frame(rows), *extra) if f is not None and len(f)]
    if not frames:
        return rc._empty_modifications()
    out = pd.concat(frames, ignore_index=True)
    return out.astype({"file_name": "string", "change_type": "string", "detail": "string"})
