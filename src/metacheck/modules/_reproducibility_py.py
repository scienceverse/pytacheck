"""The Python code path of the Reproducibility Check module.

Port of the Python half of ``inst/modules/reproducibility_check.R`` (metacheck d3b8594):
the same static analysis as the R path (dependencies, path rewrites, run order, missing
inputs) and, with ``execute=True``, a run of each Python script under the same sandbox
as the R scripts: a Docker container by default, the host only with an explicit
``sandbox="process"`` (D62). :func:`assess_py` returns the pieces the module combines
with the R path's into one result.

Deliberate differences from metacheck (D119): a Python file that does not parse is
found statically, as an R file is; a notebook's code cells are what runs (metacheck runs
its JSON); an import of one of the paper's own modules is not a dependency to install;
an import is installed under its PyPI name and matched to a manifest pin by it; and a
``NameError`` does not trigger metacheck's second run in another order, which cannot
change the outcome (every script runs in its own process).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import grepl, plural, r_round, strsplit, trimws
from metacheck._values import as_str
from metacheck.modules import _reproducibility as h
from metacheck.modules import reproducibility_check as rc
from metacheck.report import collapse_section, scroll_table


def _read_py(path: str | None, is_notebook: bool) -> tuple[list[str] | None, list[str]]:
    """A Python file's raw text and the code to analyse and run (a notebook's code cells)."""
    raw = rc._read_code(path)
    if not is_notebook or raw is None:
        return raw, raw or []
    from metacheck.codecheck.core import code_extract_py

    try:
        return raw, [x if x is not None else "NA" for x in code_extract_py(text=raw)]
    except Exception:
        return raw, []


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


def _local_modules(py_names: Sequence[str], structure_df: Any) -> set[str]:
    """The module names the paper's own ``.py`` files (and packages) can be imported as."""
    out: set[str] = set()
    for fn in [*py_names, *h._chr_col(structure_df, "file_name")]:
        if fn is None or not grepl(r"\.py$", fn, ignore_case=True):
            continue
        parts = fn.replace("\\", "/").split("/")
        stem = parts[-1][:-3]
        if stem == "__init__":
            if len(parts) > 1:
                out.add(parts[-2])
        else:
            out.add(stem)
    return out


def _dedup(
    py_files: pd.DataFrame, resolve: Any, read: list[tuple[list[str] | None, list[str]]]
) -> tuple[list[int], list[str] | None]:
    """The rows to keep (byte-identical files run once) and the duplicates' report section."""
    fnames = h._chr_col(py_files, "file_name")
    hashes = [None if not raw else "\n".join(raw) for raw, _ in read]
    seen: set[str] = set()
    dup = []
    for hv in hashes:
        dup.append(hv is not None and hv in seen)
        if hv is not None:
            seen.add(hv)
    keep = [i for i, d in enumerate(dup) if not d]
    n_dup = sum(dup)
    if n_dup == 0:
        return keep, None
    descs = []
    for i in (i for i, d in enumerate(dup) if d):
        kept = hashes.index(hashes[i])
        loc, kept_loc = resolve(i), resolve(kept)
        at = f" at `{loc}`" if h._nzchar(loc) else ""
        kept_at = f" at `{kept_loc}`" if h._nzchar(kept_loc) else ""
        descs.append(
            f"`{rc._na_str(fnames[i])}`{at} (same as `{rc._na_str(fnames[kept])}`{kept_at})"
        )
    return keep, [
        "#### Duplicate files across repo mirrors",
        f"{n_dup:d} file{plural(n_dup)} {plural(n_dup, 'is', 'are')} byte-identical to another "
        "file already in the run (the paper links more than one repository/component that "
        f"mirrors the same materials). {plural(n_dup, 'It was', 'They were')} only run once, "
        "from its first occurrence, to avoid wasted duplicate execution: " + "; ".join(descs) + ".",
    ]


def _lgl(d: pd.DataFrame, col: str) -> list[bool]:
    return [h._is_true(v) for v in h._col(d, col)] if len(d) else []


def assess_py(
    paper: Any,
    cleanup: list[str],
    a: Mapping[str, Any],
    *,
    py_files: pd.DataFrame,
    code_tbl: pd.DataFrame,
    plan: Any,
    structure_df: Any,
    stat_output: list[dict[str, Any]],
    sandbox_root: str | None,
    execute: bool,
) -> dict[str, Any]:
    """Assess (and with *execute*, run) the paper's Python code.

    Returns ``table`` (the R path's columns), ``counts`` (the summary-table counts),
    ``tl``, ``report`` (the section, without its heading), ``summary_lines``,
    ``run_results``, ``install_results``, ``modifications``, ``stat_output`` (the given
    output plus what the scripts printed) and ``sandbox_root``.
    """
    from metacheck.repro.core import (
        repro_file_io,
        repro_missing_inputs,
        repro_rewrite_paths,
        repro_run_order,
    )
    from metacheck.repro.python import _repro_py_parses, repro_dependencies_py

    def pid(*dfs: Any) -> str | None:
        return h._pid(paper, *dfs)

    resolve = rc._resolver(py_files, structure_df)
    is_nb = grepl(r"\.ipynb$", h._chr_col(py_files, "file_name"), ignore_case=True)
    read = [_read_py(resolve(i), bool(is_nb[i])) for i in range(len(py_files))]
    keep, dup_report = _dedup(py_files, resolve, read)
    n_dup = len(py_files) - len(keep)
    if n_dup:
        py_files = py_files.iloc[keep].reset_index(drop=True)
        read = [read[i] for i in keep]
    n_code = len(py_files)
    names = [str(f) for f in h._chr_col(py_files, "file_name")]
    code_texts = [code for _, code in read]
    code_text_list = h.RNamedList(names, code_texts)

    # Dependencies (pooled across files; the paper's own modules are not dependencies)
    deps = repro_dependencies_py(code_text_list, manifest_text=_manifest_text(structure_df))
    local = _local_modules(names, structure_df)
    is_dep = [
        p not in local and not h._is_true(b)
        for p, b in zip(h._chr_col(deps, "package"), h._col(deps, "base"), strict=True)
    ]
    install_deps = deps[is_dep].reset_index(drop=True)
    n_deps = len(install_deps)

    # Path rewrites, I/O, run order, missing inputs
    io = repro_file_io(code_text_list, "Python")
    rewrites = [
        repro_rewrite_paths(code_texts[i], names[i], plan, "Python", structure_df=structure_df)
        for i in range(n_code)
    ]
    rewrite_list = h.RNamedList(names, rewrites)
    rewrites_n = [
        sum(m and not am for m, am in zip(_lgl(d, "matched"), _lgl(d, "ambiguous"), strict=True))
        for d in rewrites
    ]
    ambiguous_n = [sum(_lgl(d, "ambiguous")) for d in rewrites]
    order_tbl = repro_run_order(io)
    cycle = [str(x) for x in h._as_list(order_tbl.attrs.get("cycle"))]
    ambiguous_order = h._is_true(order_tbl.attrs.get("ambiguous"))

    io_names = h._chr_col(io, "file_name")
    io_writes = h._col(io, "writes")
    writes_of = {
        n: [as_str(w) for w in h._as_list(ws)] for n, ws in zip(io_names, io_writes, strict=True)
    }
    produced = {w.lower() for ws in writes_of.values() for w in ws if w is not None}
    unresolved = list(
        dict.fromkeys(
            b.lower()
            for d in rewrites
            for b, m in zip(h._chr_col(d, "basename"), _lgl(d, "matched"), strict=True)
            if not m and b is not None
        )
    )
    missing_inputs = repro_missing_inputs(
        [b for b in unresolved if b not in produced], plan, structure_df
    )
    n_missing = len(missing_inputs)
    n_withheld = sum(s == "withheld_size" for s in h._chr_col(missing_inputs, "status"))

    parses = [_repro_py_parses(t) for t in code_texts]
    parse_errs = parses.count(False)
    file_order = rc._pick(
        h._col(order_tbl, "order"), rc._match_first(names, h._chr_col(order_tbl, "file_name"))
    )
    file_order = [None if h._na(o) else int(o) for o in file_order]
    unresolved_list: list[list[str]] = []
    for i, d in enumerate(rewrites):
        # a file an earlier script writes, or this one (`open(path, "w")` names its path as
        # a reference too; metacheck took it for a missing input: U235)
        before = {
            w.lower()
            for j, n in enumerate(names)
            if j == i
            or (
                file_order[i] is not None
                and file_order[j] is not None
                and file_order[j] < file_order[i]
            )
            for w in writes_of.get(n, [])
            if w is not None
        }
        unresolved_list.append(
            list(
                dict.fromkeys(
                    b.lower()
                    for b, m in zip(h._chr_col(d, "basename"), _lgl(d, "matched"), strict=True)
                    if not m and b is not None and b.lower() not in before
                )
            )
        )
    runnable = [
        p and o is not None and not u
        for p, o, u in zip(parses, file_order, unresolved_list, strict=True)
    ]
    reason = [
        None if r else "parse_error" if not p else "unplaceable" if o is None else "missing_input"
        for r, p, o in zip(runnable, parses, file_order, strict=True)
    ]

    # Traffic light (static part), as the R path's
    if n_missing > 0 and n_withheld == n_missing:
        tl = "red"
    elif (
        parse_errs == 0
        and n_missing == 0
        and not cycle
        and not ambiguous_order
        and not sum(ambiguous_n)
    ):
        tl = "green"
    else:
        tl = "yellow"

    ex: dict[str, Any] = {}
    if execute:
        ex = _execute_py(
            paper,
            cleanup,
            a,
            names=names,
            code_text_list=code_text_list,
            rewrite_list=rewrite_list,
            plan=plan,
            structure_df=structure_df,
            code_tbl=code_tbl,
            install_deps=install_deps,
            order_tbl=order_tbl,
            parses=parses,
            skip=[n for n, u in zip(names, unresolved_list, strict=True) if u],
            sandbox_root=sandbox_root,
            stat_output=stat_output,
        )
        sandbox_root = ex["sandbox_root"]
        stat_output = ex["stat_output"]
        if any(o in ("errored", "timed_out") for o in h._chr_col(ex["run_results"], "outcome")):
            tl = "red"
    run_results: pd.DataFrame | None = ex.get("run_results")
    install_results: pd.DataFrame | None = ex.get("install_results")
    run_tbl: pd.DataFrame | None = ex.get("run_tbl")

    # Per-file table (the R path's columns)
    m_ord = rc._match_first(names, h._chr_col(order_tbl, "file_name"))
    if run_results is not None:
        m_run = rc._match_first(names, h._chr_col(run_results, "file_name"))
        outcome_of = rc._pick(h._chr_col(run_results, "outcome"), m_run)
        errtype_of = rc._pick(h._chr_col(run_results, "error_type"), m_run)
        undefvar_of = rc._pick(h._chr_col(run_results, "undefined_var"), m_run)
    else:
        outcome_of = errtype_of = undefvar_of = [None] * n_code
    setwd_of = (
        rc._pick(
            h._col(run_tbl, "setwd_removed"),
            rc._match_first(names, h._chr_col(run_tbl, "file_name")),
        )
        if run_tbl is not None and "setwd_removed" in run_tbl.columns
        else [None] * n_code
    )
    m_io = rc._match_first(names, io_names)
    reads_col = h._col(io, "reads")
    table = pd.DataFrame(
        {
            "paper_id": rc._str_col(
                h._col(py_files, "paper_id") if "paper_id" in py_files.columns else [pid()] * n_code
            ),
            "file_name": rc._str_col(names),
            "parses": rc._bool_col(parses),
            "run_order": rc._int_col(rc._pick(h._col(order_tbl, "order"), m_ord)),
            "order_basis": rc._str_col(rc._pick(h._chr_col(order_tbl, "order_basis"), m_ord)),
            "depends_on": rc._str_col(rc._pick(h._chr_col(order_tbl, "depends_on"), m_ord)),
            "paths_rewritten": rc._int_col(rewrites_n),
            "paths_ambiguous": rc._int_col(ambiguous_n),
            "setwd_removed": rc._int_col(setwd_of),
            "runnable": rc._bool_col(runnable),
            "not_runnable_reason": rc._str_col(reason),
            "unresolved_inputs": rc._str_col(
                [", ".join(u) if u else None for u in unresolved_list]
            ),
            "outcome": rc._str_col(outcome_of),
            "error_type": rc._str_col(errtype_of),
            "undefined_var": rc._str_col(undefvar_of),
            "reads": rc._obj_col([[] if j is None else h._as_list(reads_col[j]) for j in m_io]),
            "writes": rc._obj_col([[] if j is None else h._as_list(io_writes[j]) for j in m_io]),
        }
    )

    # Report
    report: list[Any] = [
        "This module also assesses whether the paper's Python code could be run on its data, "
        + (
            "and — because `execute = TRUE` — **actually ran it**. "
            if execute
            else "It performs static analysis only here: **no code is run**. "
        )
        + f"We examined {n_code:d} Python code file{plural(n_code)}."
    ]
    report += _static_report(
        order_tbl, cycle, ambiguous_order, rewrites_n, ambiguous_n, missing_inputs
    )
    if parse_errs > 0:
        report += [
            "#### Parsing",
            f"{parse_errs:d} Python file{plural(parse_errs)} did not parse and cannot be run "
            "(a syntax error, or Python 2 code).",
        ]
    if run_results is not None and len(run_results) > 0:
        report += _execution_report_py(
            run_results, install_results, run_tbl, ex["definer_lookup"], a["sandbox"], a["timeout"]
        )
    else:
        report.append(
            "*Executing the code is a separate, opt-in phase (run with `execute = TRUE`). This "
            "report describes what a run would involve; it did not run anything.*"
        )
    report += dup_report or []

    # Summary
    outcomes = h._chr_col(run_results, "outcome") if run_results is not None else []
    n_ran_ok = outcomes.count("ran_ok") if run_results is not None else None
    n_runnable = sum(runnable)
    lines = [
        f"We assessed AND ran {n_code:d} Python code file{plural(n_code)} for reproducibility."
        if execute
        else f"We assessed {n_code:d} Python code file{plural(n_code)} for reproducibility "
        "(static analysis; no code was run).",
        f"{n_runnable:d} Python file{plural(n_runnable)} appear{'s' if n_runnable == 1 else ''} "
        "runnable so far (parses, inputs resolve, placeable in the run order).",
    ]
    if n_dup > 0:
        lines.append(
            f"{n_dup:d} Python file{plural(n_dup)} skipped as byte-identical "
            f"duplicate{plural(n_dup)} of another file already in the run."
        )
    if execute and run_results is not None:
        nr = len(run_results)
        lines.append(
            f"{n_ran_ok:d} of {nr:d} Python script{plural(nr)} ran without error when executed."
        )
    if n_missing > 0:
        lines.append(
            f"{n_missing:d} referenced Python input{plural(n_missing)} unavailable "
            f"({n_withheld:d} withheld due to size)."
        )
    lines.append(
        f"{n_deps:d} installable Python dependenc{'y' if n_deps == 1 else 'ies'} detected."
    )

    return {
        "table": table,
        "counts": {
            "repro_code_n": n_code,
            "repro_runnable": n_runnable,
            "repro_missing_inputs": n_missing,
            "repro_deps": n_deps,
            "repro_ran_ok": n_ran_ok,
        },
        "tl": tl,
        "report": report,
        "summary_lines": lines,
        "run_results": run_results,
        "install_results": install_results,
        "modifications": rc._modifications(rewrite_list, run_tbl, {}, execute),
        "stat_output": stat_output,
        "sandbox_root": sandbox_root,
    }


def _static_report(
    order_tbl: pd.DataFrame,
    cycle: list[str],
    ambiguous_order: bool,
    rewrites_n: list[int],
    ambiguous_n: list[int],
    missing_inputs: pd.DataFrame,
) -> list[Any]:
    """The "Run order", "File paths" and "Missing inputs" subsections."""
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
        report_order = (
            "No ordering signal (no data dependency, `source()`-equivalent, or numeric filename "
            "prefix) distinguishes the run order of these files. They may need to run in a specific order that could "
            "not be determined automatically — check this before running."
        )
    elif cycle:
        report_order = (
            f"A dependency **cycle** was detected among {len(cycle):d} file{plural(len(cycle))} "
            f"({', '.join(cycle)}): each reads a file another writes, so no run order satisfies "
            "all of them. This must be resolved before the code can run."
        )
    else:
        report_order = (
            "The scripts were ordered by their data dependencies (a script writing a file "
            "another reads runs first) and numeric filename prefixes. Python has no "
            "`source()`-equivalent import-based ordering signal (an `import` loads a module, "
            "not the paper's own analysis flow), so that basis never applies here."
        )
    total, amb = sum(rewrites_n), sum(ambiguous_n)
    report_paths = (
        f"To run against the Psych-DS layout, {total:d} referenced data path{plural(total)} "
        f"would be rewritten to {plural(total, 'its', 'their')} new location{plural(total)}."
    )
    if amb > 0:
        report_paths += (
            f" {amb:d} reference{plural(amb)} matched several files sharing a name and could "
            f"not be resolved by study group; {plural(amb, 'it was', 'they were')} left "
            "unrewritten and flagged."
        )
    n_missing = len(missing_inputs)
    out: list[Any] = [
        "#### Run order",
        report_order,
        scroll_table(order_table, maxrows=10),
        "#### File paths",
        report_paths,
        "#### Missing inputs",
    ]
    if n_missing == 0:
        out.append(
            "Every data file the code reads is either produced by an earlier script or present "
            "in the repository."
        )
    else:
        out += [
            f"{n_missing:d} input file{plural(n_missing)} the code reads "
            f"{plural(n_missing, 'is', 'are')} not available. Files withheld only because of "
            "their size are distinguished from files absent from the repository — the former "
            "are a size-cap issue, not a reproducibility failure.",
            scroll_table(
                pd.DataFrame(
                    {
                        "File": missing_inputs["basename"].tolist(),
                        "Status": missing_inputs["status"].tolist(),
                        "Reason": missing_inputs["detail"].tolist(),
                    }
                ),
                maxrows=10,
            ),
        ]
    return out


def _execute_py(
    paper: Any,
    cleanup: list[str],
    a: Mapping[str, Any],
    *,
    names: list[str],
    code_text_list: h.RNamedList,
    rewrite_list: h.RNamedList,
    plan: Any,
    structure_df: Any,
    code_tbl: Any,
    install_deps: pd.DataFrame,
    order_tbl: pd.DataFrame,
    parses: list[bool],
    skip: list[str],
    sandbox_root: str | None,
    stat_output: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write the scripts into the layout, install, run each in the sandbox, read its output."""
    from metacheck.repro.core import _message, repro_materialize_layout, repro_write_scripts
    from metacheck.repro.python import _repro_py_defined_vars
    from metacheck.statout.stat_output import stat_output_json, stat_output_write, stat_results_long
    from metacheck.statout.stat_tables import _ipynb_is_noise, _ipynb_stat_line, _ipynb_stat_table

    sandbox = a["sandbox"]
    if sandbox not in rc._SANDBOXES:
        raise ValueError(f"unknown sandbox {sandbox!r}: expected one of {', '.join(rc._SANDBOXES)}")
    timeout = a["timeout"]
    docker = sandbox == "docker"
    _message(f"[repro/py] execute = TRUE (sandbox = {sandbox}), timeout = {timeout}s")
    if sandbox_root is None:
        sandbox_root = rc._new_sandbox(h._is_true(a["keep_sandbox"]), cleanup)
        repro_materialize_layout(plan, structure_df, sandbox_root)
    lib_dir = os.path.join(sandbox_root, "_pylib")
    run_tbl = repro_write_scripts(
        code_text_list,
        rewrite_list,
        plan,
        sandbox_root,
        lang="Python",
        # the container sees the sandbox at /sandbox, not at its path on this machine
        # (metacheck writes the host's path: U239)
        output_dir="/sandbox/output/" if docker else None,
    )

    install_results: pd.DataFrame | None = None
    failed_deps: list[str] = []
    if h._is_true(a["install_missing"]) and len(install_deps) > 0:
        if docker:
            from metacheck.repro.docker import repro_install_deps_py_docker

            install_results = repro_install_deps_py_docker(install_deps, lib_dir, timeout=timeout)
        else:
            from metacheck.repro.python import repro_install_deps_py

            install_results = repro_install_deps_py(install_deps, lib_dir, timeout=timeout)
        failed_deps = [
            str(p)
            for p, ok in zip(
                h._chr_col(install_results, "package"),
                h._col(install_results, "installed"),
                strict=True,
            )
            if not h._is_true(ok)
        ]

    lib = lib_dir if os.path.isdir(lib_dir) else None
    parses_named = pd.Series(parses, index=names, dtype=bool)
    order = rc._order_names(order_tbl)
    if docker:
        from metacheck.repro.docker import repro_run_scripts_py_docker

        run_results = repro_run_scripts_py_docker(
            run_tbl, order, sandbox_root=sandbox_root, lib_dir=lib, timeout=timeout,
            skip=skip, parses=parses_named, failed_deps=failed_deps,
        )  # fmt: skip
    else:
        from metacheck.repro.python import repro_run_scripts_py

        run_results = repro_run_scripts_py(
            run_tbl, order, lib_dir=lib, timeout=timeout, skip=skip, parses=parses_named,
            failed_deps=failed_deps,
        )  # fmt: skip

    # Which other script defines the name a NameError misses (for the report). metacheck
    # re-runs in an order that puts it first, which cannot help: each script is its own
    # process and sees nothing another one defined.
    definer_lookup: dict[str, str] = {}
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
            definers = list(dict.fromkeys(n for n, d in def_sets if v in d and n != user))
            if definers:
                definer_lookup[f"{rc._na_str(user)}||{rc._na_str(v)}"] = ", ".join(
                    rc._na_str(d) for d in definers
                )

    # The statistics each script printed (text only: there is no captured object)
    pid = h._pid(paper, structure_df, code_tbl)
    py_stat_output: list[dict[str, Any]] = []
    for fn, so in zip(
        h._chr_col(run_results, "file_name"), h._chr_col(run_results, "stdout"), strict=True
    ):
        if not h._nzchar(so):
            continue
        lines = [x for x in strsplit(so, "\n", fixed=True) if trimws(x) != ""]
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
    if py_stat_output:
        stat_output = [*stat_output, *py_stat_output]
        stat_output_write(stat_output, sandbox_root)
    return {
        "sandbox_root": sandbox_root,
        "run_results": run_results,
        "install_results": install_results,
        "run_tbl": run_tbl,
        "definer_lookup": definer_lookup,
        "stat_output": stat_output,
    }


def _execution_report_py(
    run_results: pd.DataFrame,
    install_results: pd.DataFrame | None,
    run_tbl: pd.DataFrame | None,
    definer_lookup: Mapping[str, str],
    sandbox: str,
    timeout: Any,
) -> list[Any]:
    """The Python section's "Execution" subsection."""
    oc = h._chr_col(run_results, "outcome")
    names = h._chr_col(run_results, "file_name")
    n_run = len(run_results)
    n_skipped = oc.count("skipped_missing_inputs")
    report_exec = (
        "**The Python code was run.** Each script ran in an isolated "
        f"{'Docker container' if sandbox == 'docker' else 'subprocess'} against a throwaway "
        "copy of the Psych-DS layout, in the run order above, with a "
        f"{rc._sprintf_d(timeout)}-second per-script timeout. Of {n_run:d} script{plural(n_run)}: "
        f"{oc.count('ran_ok'):d} ran without error, {oc.count('errored'):d} errored, "
        f"{oc.count('timed_out'):d} timed out, {n_skipped:d} {plural(n_skipped, 'was', 'were')} "
        f"skipped (inputs unavailable), {oc.count('dependency_unavailable'):d} could not run "
        "because one of its own dependencies is unavailable, and "
        f"{oc.count('not_parsed'):d} did not parse. Running against current package versions: "
        "a break can reflect version drift, which argues for pinning versions (e.g. in a "
        "`requirements.txt`)."
    )
    report: list[Any] = ["#### Execution", report_exec]
    elapsed = h._col(run_results, "elapsed")
    err_types = h._chr_col(run_results, "error_type")
    report.append(
        scroll_table(
            pd.DataFrame(
                {
                    "File": names,
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
        defined_in = [
            definer_lookup.get(f"{rc._na_str(names[i])}||{rc._na_str(undef_vars[i])}", "")
            for i in undef
        ]
        report += [
            "**Undefined-name errors**",
            f"**{nu:d} script{plural(nu)} failed on an undefined name** (`NameError`). This "
            "typically means the script expects a variable, function, or class that another "
            "script defines — i.e. it is meant to be run alongside a larger set of scripts, not "
            "on its own — or the name is simply never created. Each script runs in its own "
            "process, so a name another script defines is never seen: when another of the "
            "paper's own script files defines the same name, this is called out below as a "
            "likely copy-paste or import gap. Each is counted as an error (red).",
            scroll_table(
                pd.DataFrame(
                    {
                        "File": [names[i] for i in undef],
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
            scroll_table(pd.DataFrame({"File": [names[i] for i in nodep]}), maxrows=10),
        ]
    if run_tbl is not None and "setwd_removed" in run_tbl.columns:
        hit = [not h._na(v) and v > 0 for v in h._col(run_tbl, "setwd_removed")]
        files = [f for f, x in zip(h._chr_col(run_tbl, "file_name"), hit, strict=True) if x]
        if files:
            ns = len(files)
            ok = {n for n, o in zip(names, oc, strict=True) if o == "ran_ok"}
            ok_after = [f for f in dict.fromkeys(files) if f in ok]
            report += [
                "**`os.chdir()` in the code**",
                f"**{ns:d} script{plural(ns)} call{plural(ns, 's', '')} `os.chdir()`.** This is "
                "bad practice: it hardcodes where the code must run (often an absolute path on "
                "the author's own machine) and breaks the moment the code is run anywhere else. "
                "To run the code we commented these calls out. Any such script that then ran is "
                "**reproducible only after ignoring an `os.chdir()` that should not have been in "
                "the code and needs to be fixed**.",
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
                                p if h._nzchar(p) else "(non-literal)" for p in paths
                            ],
                        }
                    ),
                    maxrows=10,
                )
            )

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
                "\n".join(body), title=f"Output — {rc._na_str(names[i])} ({rc._na_str(oc[i])})"
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

    if install_results is not None and len(install_results) > 0:
        installed = [h._is_true(v) for v in h._col(install_results, "installed")]
        pkgs = h._chr_col(install_results, "package")
        n_ok = sum(installed)
        report.append(
            "Dependencies were installed into a throwaway library before running (`pip "
            f"install`): {n_ok:d} succeeded, {len(installed) - n_ok:d} failed. "
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
