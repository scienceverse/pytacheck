"""The execution phase of the Reproducibility Check (``execute = TRUE``), for R code.

Part of the port of metacheck 0.4.3.7's ``inst/modules/reproducibility_check.R``: section
7b of its R block (copy the repository, install, run each script, run again where that can
help, read the output) in :func:`_execute`, and the report's "Execution" part in
:func:`_execution_report`. :func:`~metacheck.modules.reproducibility_check._assess_r` calls
both; the traffic light and ``modifications`` are worked out there from what
:func:`_execute` returns.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import time
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import bind_rows, grepl, plural, r_round
from metacheck._values import as_str
from metacheck.modules import _reproducibility as h
from metacheck.modules.reproducibility_check import (
    _FN_MISSING_PAT,
    _MAX_LINES,
    _SANDBOXES,
    _download_on,
    _fence,
    _int_col,
    _is_several,
    _na_str,
    _new_sandbox,
    _order_names,
    _sprintf_d,
    _tail_cap,
)
from metacheck.report import collapse_section, scroll_table

#: R's ``layout_name``: what the scripts run against
_LAYOUT_NAME = "the repository"

#: Outcomes that are not scripts the report counts (``n_scripts``)
_NOT_SCRIPTS = (
    "jags_model",
    "not_r_content",
    "defines_functions",
    "not_analysis",
    "not_downloaded",
    "empty",
)

#: ``list.files(d, pattern = "\\.Rproj$|^\\.here$|^DESCRIPTION$", all.files = TRUE)``
_HERE_MARK = re.compile(r"\.Rproj$|^\.here$|^DESCRIPTION$")


# -- small R idioms ---------------------------------------------------------------------------


def _names(df: pd.DataFrame | None) -> list[str | None]:
    """``df$file_name`` (empty when *df* has no such column)."""
    return h._chr_col(df, "file_name")


def _given(**kw: Any) -> dict[str, Any]:
    """The arguments R passes that are not ``NULL``: a ``NULL`` one is the callee's default."""
    return {k: v for k, v in kw.items() if v is not None}


def _msgs(df: pd.DataFrame) -> list[str]:
    """``paste(df$error, df$stderr)`` (``NA`` pastes as ``"NA"``)."""
    n = len(df)
    err = h._chr_col(df, "error") or [None] * n
    se = h._chr_col(df, "stderr") or [None] * n
    return [f"{_na_str(e)} {_na_str(s)}" for e, s in zip(err, se, strict=True)]


def _rows_in(df: pd.DataFrame, names: Sequence[Any]) -> pd.DataFrame:
    """``df[df$file_name %in% names, , drop = FALSE]``."""
    if "file_name" not in df.columns:
        return df.iloc[0:0]
    keep = set(names)
    return df[[n in keep for n in _names(df)]].reset_index(drop=True)


def _rows_not_in(df: pd.DataFrame, names: Sequence[Any]) -> pd.DataFrame:
    """``df[!df$file_name %in% names, , drop = FALSE]``."""
    if "file_name" not in df.columns:
        return df
    drop = set(names)
    return df[[n not in drop for n in _names(df)]].reset_index(drop=True)


def _order_by(df: pd.DataFrame, order: Sequence[Any]) -> pd.DataFrame:
    """``df[order(match(df$file_name, order)), , drop = FALSE]`` (no match last, stable)."""
    first: dict[Any, int] = {}
    for i, n in enumerate(order):
        first.setdefault(n, i)
    keys = [first.get(n) for n in _names(df)]
    idx = sorted(range(len(df)), key=lambda i: (keys[i] is None, keys[i] or 0))
    return df.iloc[idx].reset_index(drop=True)


def _replace_runs(
    old: pd.DataFrame, new: pd.DataFrame | None, order: Sequence[Any]
) -> pd.DataFrame:
    """A later pass's results in place of the earlier ones, in *order*.

    ``bind_rows(old[!old$file_name %in% new$file_name, ], new)``, ordered by *order*.
    """
    if new is None:
        return old
    return _order_by(bind_rows([_rows_not_in(old, _names(new)), new]), order)


def _intersect(x: Sequence[Any], y: Sequence[Any]) -> list[Any]:
    """R ``intersect()``: the unique elements of *x* that are in *y*."""
    ys = set(y)
    return list(dict.fromkeys(v for v in x if v in ys))


def _setdiff(x: Sequence[Any], y: Sequence[Any]) -> list[Any]:
    """R ``setdiff()``: the unique elements of *x* not in *y*."""
    ys = set(y)
    return list(dict.fromkeys(v for v in x if v not in ys))


def _folders_by_script(made: pd.DataFrame) -> dict[str, list[str]]:
    """``split(made$folder, made$script)``: the folders made for each script so far."""
    out: dict[str, list[str]] = {}
    for script, folder in zip(h._chr_col(made, "script"), h._chr_col(made, "folder"), strict=True):
        out.setdefault(str(script), []).append(str(folder))
    return out


def _pass_order(order: Sequence[Any], redo: Sequence[Any]) -> list[Any]:
    """``c(intersect(order, redo), setdiff(redo, order))``."""
    return [*_intersect(order, redo), *_setdiff(redo, order)]


def _set_col(df: pd.DataFrame, name: str, values: Sequence[Any]) -> None:
    """``df$name <- values`` for a character column."""
    df[name] = pd.Series(list(values), index=df.index, dtype="string")


def _pin_versions(version_pin: Any) -> list[Any]:
    """``version_pin$r_versions %||% character(0)``.

    A list from code_check; a string or an array when read back from a saved build.
    """
    pin = version_pin if isinstance(version_pin, Mapping) else {}
    raw = pin.get("r_versions")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _sub_list(lst: h.RNamedList, keep: Sequence[bool]) -> h.RNamedList:
    """``lst[keep]`` of an R named list (a logical index)."""
    items = [(k, v) for (k, v), ok in zip(lst.items(), keep, strict=True) if ok]
    return h.RNamedList([k for k, _ in items], [v for _, v in items])


# -- the execution step ------------------------------------------------------------------------


def _execute(
    paper: Any, cleanup: list[str], a: Mapping[str, Any], ctx: Mapping[str, Any]
) -> dict[str, Any]:
    """Section 7b of the R block: lay out, install, run, run again, read the output.

    Port of the ``if (isTRUE(execute)) { ... }`` part of the R block of
    ``inst/modules/reproducibility_check.R`` (metacheck 0.4.3.7), without the traffic
    light, which the caller works out from ``run_results``. *a* holds the module's
    arguments (with ``net`` and ``run_state``), *ctx* the R block's locals by their R
    names; neither is changed, except ``a["run_state"]["script_deadline"]``, the deadline
    of all scripts together, which the Python block shares.
    """
    from metacheck.codecheck.core import code_packages
    from metacheck.repro import core, docker, layout, outcomes, runsupport
    from metacheck.repro import recorded as rec
    from metacheck.statout.r_capture import _r_captures_to_tables, _r_merge_captures
    from metacheck.statout.r_output import read_r_output
    from metacheck.statout.stat_output import (
        stat_output_json,
        stat_output_write,
        stat_results_long,
    )

    sandbox = a["sandbox"]
    if sandbox not in _SANDBOXES:
        # run_r_scripts() and the install step below treat anything but "docker" as the host
        # process, so a value that skipped _match_sandbox() must stop here, not get that backend
        raise ValueError(f"unknown sandbox {sandbox!r}: expected one of {', '.join(_SANDBOXES)}")
    timeout = a["timeout"]
    total_timeout = a.get("total_timeout")
    install_missing = a.get("install_missing")
    keep_sandbox = h._is_true(ctx.get("keep_sandbox", a.get("keep_sandbox")))
    use_declared = h._is_true(a.get("docker_use_declared_version"))

    file_names = [str(f) for f in ctx["file_names"]]
    r_files: pd.DataFrame = ctx["r_files"]
    rewrite_list = ctx["rewrite_list"]
    plan = ctx["plan"]
    structure_df = ctx["structure_df"]
    code_tbl = ctx["code_tbl"]
    version_pin = ctx["version_pin"]
    deps: pd.DataFrame = ctx["deps"]
    install_deps: pd.DataFrame = ctx["install_deps"]
    io: pd.DataFrame = ctx["io"]
    order_tbl: pd.DataFrame = ctx["order_tbl"]
    runs_as_r = [h._is_true(x) for x in ctx["runs_as_r"]]
    sniff_type: list[str | None] = list(ctx["sniff_type"])
    r_script_paths: list[str | None] = list(ctx["r_script_paths"])
    r_script_places = list(ctx["r_script_places"])
    r_run_dirs = list(ctx["r_run_dirs"])
    here_roots: list[str | None] = list(ctx["here_roots"])
    stat_output: list[dict[str, Any]] = list(ctx.get("stat_output") or [])
    # copied: the Stan update below changes the code the scripts are written from
    src: h.RNamedList = ctx["code_text_list"]
    code_text_list = h.RNamedList(list(src.keys()), list(src.values()))

    def dbg(*parts: Any) -> None:
        core._message("[repro] ", *parts)

    dbg(
        f"execute = TRUE (sandbox = {sandbox}). install_missing = "
        f"{'NULL' if install_missing is None else 'TRUE' if install_missing else 'FALSE'}, "
        f"timeout = {timeout}s, keep_sandbox = {'TRUE' if keep_sandbox else 'FALSE'}"
    )

    # Reuse the root the JASP/jamovi step already materialised (if any stat output existed
    # above), so data/ and statistical_output/ end up in the SAME tree the executed scripts
    # also run against, rather than two roots.
    sandbox_root: str | None = ctx.get("sandbox_root")
    if sandbox_root is None:
        sandbox_root = _new_sandbox(keep_sandbox, cleanup)
    # In Docker the library is a host folder mounted into the container (metacheck: a
    # volume), so it goes with the sandbox in both (#568).
    lib_dir = os.path.join(sandbox_root, "_lib")
    dbg("sandbox root: ", sandbox_root)

    # With recorded_versions = TRUE, when the repository records package versions, the
    # packages the scripts load are installed at those versions (see
    # _repro_recorded_plan()), also when install_missing was not set. Several papers run as
    # one share one library, so one paper's list must not decide another's versions.
    recorded: dict[str, Any] | None = None
    if h._is_true(a.get("recorded_versions")) and install_missing is not False:
        if _is_several(paper):
            pin = version_pin if isinstance(version_pin, Mapping) else {}
            if h._is_true(pin.get("pinned")):
                warnings.warn(
                    "reproducibility_check(): recorded package versions are not used when "
                    "several papers run as one. Run the papers one at a time, or with "
                    'workers > 1 and sandbox = "docker", to use them.',
                    stacklevel=2,
                )
        else:
            recorded = rec._repro_recorded_plan(deps, version_pin)
    if recorded is not None:
        warnings.warn(
            rec._repro_recorded_warning(recorded, version_pin, sandbox, use_declared), stacklevel=2
        )
    install_r = h._is_true(install_missing) or (install_missing is None and recorded is not None)
    to_install = (
        rec._repro_install_order(recorded["deps"]) if recorded is not None else install_deps
    )
    # Only the packages of files that run are installed, not those of a package's code, its
    # tests or a copy of a tool, which are not run (#568): in 10.1111/2041-210X.13787 one
    # script ran, for 2 seconds, after 35 minutes of installing what 766 files of a web
    # application import.
    if recorded is None and "not_analysis" in sniff_type:
        run_rows = [s != "not_analysis" for s in sniff_type]
        try:
            pkgs = h._col(r_files, "packages")
            cc_pkgs = code_packages([p for p, ok in zip(pkgs, run_rows, strict=False) if ok])
        except Exception:
            cc_pkgs = []
        needed = {
            *h._chr_col(
                core.repro_dependencies(_sub_list(code_text_list, run_rows), "R"), "package"
            ),
            *cc_pkgs,
        }
        pkgs_all = h._chr_col(to_install, "package")
        unused = [p for p in pkgs_all if p not in needed]
        if unused:
            dbg(
                f"not installing {len(unused)} package(s) only files that are not run use: ",
                ", ".join(_na_str(p) for p in unused),
            )
        to_install = to_install[[p in needed for p in pkgs_all]].reset_index(drop=True)

    # Docker base image: by default the pre-built metacheck_r_large image (fast -- most
    # dependencies already installed), regardless of what R version the paper declared.
    # docker_use_declared_version = TRUE opts into matching the paper's own declared R
    # version instead (rocker/r-ver:<version>, a bare image with nothing pre-installed --
    # slower, since every dependency installs from scratch). When it is FALSE (the default)
    # and the paper DID declare a version, warn once so this is never a silent substitution
    # of one R version for another (the warning about recorded versions above says so too).
    docker_image: str | None = None
    if sandbox == "docker":
        declared = _pin_versions(version_pin)
        if not use_declared and declared and recorded is None:
            warnings.warn(
                'reproducibility_check(sandbox = "docker"): this paper declared R version '
                f"{declared[0]}, but docker_use_declared_version = FALSE (the default), so the "
                "run uses the pre-built metacheck_r_large image's own R version instead, for "
                "speed. Set docker_use_declared_version = TRUE to match the paper's declared "
                "version exactly -- slower, since every dependency then installs from scratch "
                "rather than using the pre-built image.",
                stacklevel=2,
            )
        docker_image = docker._repro_docker_image_for(declared, use_declared_version=use_declared)
        dbg("docker image: ", docker_image)

    # 1. Build the data tree the plan describes, and write the scripts into it.
    # Not when too little time is left, and the copying stops at the clock deadline (a
    # server's job limit), as running does
    no_time = core._repro_no_time_to_prepare(total_timeout, a["run_state"])
    dbg(f"copying the repository ({len(plan) if plan is not None else 0} files) ...")
    ml = core.repro_materialize_layout(
        plan,
        structure_df,
        sandbox_root,
        deadline=time.time() if no_time else core._repro_clock_deadline(total_timeout),
    )
    not_copied = getattr(ml, "attrs", {}).get("not_copied_time") or 0
    if not_copied > 0:
        dbg(f"  {not_copied} not copied: the time had run out.")
    # Stan files in the syntax Stan removed in 2.33 get the newer form (#568)
    stan_parts: list[pd.DataFrame | None] = [rec._repro_upgrade_stan_files(sandbox_root)]
    # and Stan programs held in the R code as a quoted text (#568; see
    # _repro_upgrade_stan_in_r())
    stan_in_r: list[tuple[str, str]] = []
    texts = list(code_text_list.values())
    for k, (fn, txt) in enumerate(code_text_list.items()):
        up = rec._repro_upgrade_stan_in_r(txt)
        if not up["changes"]:
            continue
        texts[k] = list(up["txt"])
        stan_in_r.append(
            (fn, "Stan in the R code, from before 2.33: " + ", ".join(map(str, up["changes"])))
        )
    code_text_list = h.RNamedList(list(code_text_list.keys()), texts)
    if stan_in_r:
        stan_parts.append(_mod_rows([(fn, "stan_syntax", d) for fn, d in stan_in_r]))
    # Git LFS pointers replaced by the deposit's copy of the file (#568)
    lfs = getattr(ml, "attrs", {}).get("lfs")
    if lfs is not None and len(lfs):
        stan_parts.append(
            _mod_rows(
                [
                    (
                        h._basename(p),
                        "lfs_pointer",
                        "a Git LFS pointer, not the file; the deposit's copy of the stated size "
                        "was used",
                    )
                    for p in h._chr_col(lfs, "pointer")
                ]
            )
        )
    stan_found = [p for p in stan_parts if p is not None and len(p)]
    stan_rows = bind_rows(stan_found) if stan_found else None

    # groundhog, checkpoint and renv calls would need the network, which is off in a Docker
    # run, and recorded versions are installed before the run.
    replace_env_calls = recorded is not None or sandbox == "docker"

    def write(
        inject_libs: Mapping[str, Any] | None = None,
        prepend: Mapping[str, Sequence[str]] | None = None,
    ) -> pd.DataFrame:
        return core.repro_write_scripts(
            code_text_list,
            rewrite_list,
            plan,
            sandbox_root,
            inject_libs=inject_libs,
            script_paths=r_script_places,
            writes_in_place=True,
            replace_env_calls=replace_env_calls,
            run_dirs=r_run_dirs,
            prepend=prepend,
        )

    dbg(f"writing {len(code_text_list)} rewritten script(s) into the layout ...")
    run_tbl = write()
    rprofile_replaced = layout._repro_neutralise_rprofile(sandbox_root) if replace_env_calls else 0
    # here::here() needs a mark for the top of the project, which is often not deposited:
    # it stopped with "Could not find a root" (#568, item 20). The mark goes where the
    # script's own here() paths point, when they show it, not in its run folder:
    # here("scripts", "functions.R") from scripts/ became scripts/scripts/functions.R
    # (10.1111/1365-2664.14317; #568). The top always has a mark, so here() finds a root
    # from every folder; a mark in each run folder came before the authors' own .Rproj at
    # the top.
    root_dirs = [
        sandbox_root if r == "." else os.path.join(sandbox_root, core._portable_path(r) or "")
        for r in here_roots
        if r is not None
    ]
    for d in dict.fromkeys([sandbox_root, *root_dirs]):
        try:
            marked = any(_HERE_MARK.search(f) for f in os.listdir(d))
        except OSError:
            marked = False
        if not marked:
            try:
                open(os.path.join(d, ".here"), "a").close()
            except OSError:
                pass  # file.create() of a folder that is not there: a warning in R
    dbg(f"  wrote {len(run_tbl)} script(s).")

    # 2. Optionally install dependencies. sandbox = "process": CRAN sources into your main
    #    library when cran_install_main = TRUE, else (like GitHub/URL always) into the
    #    throwaway library. sandbox = "docker": always the throwaway library -- a container
    #    has no access to (and must not be given access to) the host's real R library, so
    #    cran_install_main has no meaning there and is silently ignored.
    install_results: pd.DataFrame | None = None
    failed_deps: list[str] = []
    if (
        install_r
        and len(to_install) > 0
        and core._repro_no_time_to_install(total_timeout, a["run_state"])
    ):
        # a script started in the last minute that fails on one of them could not run here
        # (dependency_unavailable), as when nothing is installed
        dbg(f"no time left to install {len(to_install)} dependenc(y/ies); skipping installs.")
        failed_deps = [str(p) for p in h._chr_col(to_install, "package") if p is not None]
    elif install_r and len(to_install) > 0:
        dbg(
            f"installing {len(to_install)} dependenc(y/ies)",
            f" (cran_install_main = {'TRUE' if h._is_true(a.get('cran_install_main')) else 'FALSE'})"
            if sandbox == "process"
            else "",
            ": ",
            ", ".join(_na_str(p) for p in h._chr_col(to_install, "package")),
        )
        install_limit = core._repro_install_limit(
            a.get("install_timeout"), timeout, total_timeout, recorded=recorded is not None
        )
        if sandbox == "docker":
            install_results = docker.repro_install_deps_docker(
                to_install, lib_dir, image=docker_image or "", timeout=install_limit
            )
        else:
            # as before, no limit unless one was asked for or versions are recorded
            limited = (
                recorded is not None
                or a.get("install_timeout") is not None
                or isinstance(total_timeout, _dt.datetime)
            )
            install_results = core.repro_install_deps(
                to_install,
                lib_dir,
                cran_to_main_lib=h._is_true(a.get("cran_install_main")),
                **_given(deadline=time.time() + install_limit if limited else None),
            )
        installed = [h._is_true(v) for v in h._col(install_results, "installed")]
        dbg(f"  install done: {sum(installed)} ok, {len(installed) - sum(installed)} failed.")
        # An old version next to current ones can keep a package from loading, or crash R:
        # see _repro_load_check_lines().
        if recorded is not None:
            install_results = rec._repro_load_check(
                install_results,
                lib_dir,
                image=docker_image,
                timeout=core._repro_install_limit(1800, timeout, total_timeout),
            )
        # A dependency this call could not install (live CRAN AND the Archive retry both
        # failed, or a GitHub/URL source errored) -- a script that then fails on it is
        # classified dependency_unavailable, not errored.
        failed_deps = [
            str(p)
            for p, ok in zip(
                h._chr_col(install_results, "package"),
                h._col(install_results, "installed"),
                strict=True,
            )
            if p is not None and not h._is_true(ok)
        ]
    else:
        dbg("install_missing FALSE or no deps; skipping installs.")
        # Nothing was installed, so a script that then fails because one of its packages
        # is not there could not run here: dependency_unavailable, not an error in the
        # script.
        if not install_r:
            failed_deps = [str(p) for p in h._chr_col(install_deps, "package") if p is not None]

    # 3. Run in order. A file that will not parse is not run. Gated on runs_as_r (not
    #    `parses`): a sniffed file (#394/#396) "parses" for the runnable/traffic-light
    #    bookkeeping, but it is still not R and must not be handed to the runner. A file
    #    predicted to be missing does not stop a script from being run: the prediction is
    #    often wrong (a read inside if (file.exists()), a name built at run time), and
    #    running the script gives its real outcome and error.
    parses_named = pd.Series(
        [r and s is None for r, s in zip(runs_as_r, sniff_type, strict=True)],
        index=file_names,
        dtype=bool,
    )
    run_order_names = _order_names(order_tbl)

    # A script another script runs with source() (main.R loads tidyverse, then
    # source("a.R")) only works inside that script: each script runs in its own fresh R
    # process, so on its own it lacks the packages and objects the sourcing script set up
    # first, and fails (most often on `%>%`). It is therefore not run on its own; it runs
    # inside the script that sources it, and is recorded as `run_by_source`. See
    # _repro_run_by_source() for when a sourced script still runs on its own. A test is not
    # run, so what it sources is not run by it either.
    can_run = pd.Series(
        [r and s != "not_analysis" for r, s in zip(runs_as_r, sniff_type, strict=True)],
        index=file_names,
        dtype=bool,
    )
    run_by_source = runsupport._repro_run_by_source(order_tbl, can_run)
    if run_by_source:
        dbg(
            "run only through source(): ",
            "; ".join(f"{s} (by {', '.join(by)})" for s, by in run_by_source.items()),
        )

    state = a["run_state"]

    def run_r_scripts(tbl: pd.DataFrame, ord_: Sequence[Any]) -> pd.DataFrame:
        own = _rows_not_in(tbl, list(run_by_source))
        lib = lib_dir if os.path.isdir(lib_dir) else None
        deadline = state.get("script_deadline")
        if sandbox == "docker":
            res = docker.repro_run_scripts_docker(
                own,
                list(ord_),
                sandbox_root=sandbox_root,
                lib_dir=lib,
                image=docker_image or "",
                timeout=timeout,
                parses=parses_named,
                failed_deps=failed_deps,
                deadline=deadline,
                net=a.get("net"),
            )
        else:
            res = core.repro_run_scripts(
                own,
                list(ord_),
                lib_dir=lib,
                timeout=timeout,
                parses=parses_named,
                failed_deps=failed_deps,
                **_given(deadline=deadline),
            )
        if not run_by_source:
            return res
        by_src = core._run_frame([core._run_row(fn, "run_by_source") for fn in run_by_source])
        return _order_by(bind_rows([res, by_src]), ord_)

    dbg(f"running {len(run_order_names)} script(s) in order: ", " -> ".join(run_order_names))
    if state.get("script_deadline") is None:
        state["script_deadline"] = core._repro_deadline(total_timeout)
    # the files there before the run, to see which a script wrote (see the end)
    r_names_before = layout._repro_sandbox_names(sandbox_root)
    run_results = run_r_scripts(run_tbl, run_order_names)
    dbg("execution finished (pass 1). outcomes: ", _outcomes_text(run_results))

    # 4. Corrective re-run (ONE extra pass, no more). Two sibling correctable causes, both
    #    surfaced as error_type == "undefined_variable" by the runner's classification (it
    #    deliberately does not split them): an "object 'X' not found" error means the
    #    script expected a VARIABLE another script defines but that ran later (or not at
    #    all); a "could not find function \"X\"" error more often means the script simply
    #    never library()'d the package that exports it. The raw message text (still in
    #    `error`) tells the two apart. Find the file that defines each missing variable,
    #    AND the package that provides each missing function name (#390), add "definer
    #    precedes user" edges and/or inject the library() calls, recompute the order, and
    #    re-run once. A second undefined-var error after this pass is accepted as the final
    #    outcome (no iterating to convergence).
    reran = False
    reran_for_reorder = False
    # Where each pass-1 undefined symbol is defined, keyed on "<user_file>||<symbol>": the
    # report's undefined-variable table says "defined in <file>" from it, also for a symbol
    # that got no reorder edge (0 or >1 definers) and after the re-run (metacheck#402).
    definer_lookup: dict[str, str] = {}
    # library() calls this pass decided on, file_name -> package(s), kept for the report
    # and `modifications` (#391); a script can get several (see _repro_missing_fn_pkgs()).
    library_injections: dict[str, list[str]] = {}
    # file_name -> the scripts to load first in the re-run
    run_after: dict[str, list[str]] = {}
    err_types = h._chr_col(run_results, "error_type")
    undef_idx = [i for i, t in enumerate(err_types) if t == "undefined_variable"]
    if undef_idx:
        # the pattern the runner matched; a row that matches neither (it should not happen)
        # is treated as the object-not-found branch
        errors = h._chr_col(run_results, "error")
        users = _names(run_results)
        vars_ = h._chr_col(run_results, "undefined_var")
        is_fn_missing = grepl(_FN_MISSING_PAT, [errors[i] for i in undef_idx], perl=True)
        defs = core.repro_defined_vars(code_text_list)
        def_names = _names(defs)
        def_sets = [h._as_list(d) for d in h._col(defs, "defines")]

        def def_of(v: str | None) -> list[str | None]:
            # the file(s) that define variable v at top level
            return [n for n, d in zip(def_names, def_sets, strict=True) if v in d]

        def script_dir(f: str | None) -> str | None:
            i = file_names.index(f) if f in file_names else None
            return None if i is None else core._r_dirname(r_script_paths[i])

        # Scoped candidate packages for the missing-library() lookup: the paper's OWN
        # declared dependencies plus a small curated list of very common packages -- NOT a
        # blind scan of every installed package (see _repro_find_export_pkg()).
        candidate_pkgs = list(
            dict.fromkeys([*h._chr_col(deps, "package"), *core._repro_common_pkgs()])
        )
        extra_edges: list[list[str]] = []
        for k, i in enumerate(undef_idx):
            user, v = users[i], vars_[i]
            definers = _setdiff(def_of(v), [user])
            if definers:
                definer_lookup[f"{_na_str(user)}||{_na_str(v)}"] = ", ".join(
                    _na_str(d) for d in definers
                )
            # Of several, the one in the user's own folder, when that is one file.
            if len(definers) > 1:
                here = script_dir(user)
                near = [d for d in definers if here is not None and script_dir(d) == here]
                if len(near) == 1:
                    definers = near
            # Only act when exactly one file defines it (unambiguous); 0 = genuinely
            # undefined (a real bug, leave it), >1 = ambiguous (do not guess).
            if len(definers) == 1:
                definer = str(definers[0])
                extra_edges.append([definer, str(user)])
                # Authors often run scripts one after another in one session. Each script
                # here has a session of its own, so the order alone cannot give it the
                # object: the re-run loads the defining script first (#568, item 16). Not
                # when the defining script already loads this one: that never ends.
                if user not in run_after.get(definer, []) and definer not in run_after.get(
                    str(user), []
                ):
                    run_after.setdefault(str(user), []).append(definer)
                dbg(f"undefined-var edge: '{definer}' defines '{v}' needed by '{user}'")
            elif not definers and is_fn_missing[k] and user not in library_injections:
                # Zero same-repo definers for a missing FUNCTION (not object): the
                # library() calls the project's other scripts make when one of them provides
                # it, else the one package that exports it, else magrittr for `%>%` (see
                # _repro_missing_fn_pkgs()). One injection per file per pass -- a script
                # failing on a second, different missing function is left for a later,
                # separate report rather than stacking guesses.
                pkgs = runsupport._repro_missing_fn_pkgs(
                    str(v), str(user), code_text_list, candidate_pkgs
                )
                if pkgs:
                    library_injections[str(user)] = list(pkgs)
                    dbg(
                        f"missing-library injection: '{v}' -> library({', '.join(pkgs)}) "
                        f"for '{user}'"
                    )
        if extra_edges or library_injections:
            order_tbl2 = (
                core.repro_run_order(io, extra_edges=extra_edges) if extra_edges else order_tbl
            )
            run_order2 = _order_names(order_tbl2)
            # The scripts written again with the library() calls (#390) and the scripts to
            # load first; a file with neither is written exactly as before.
            run_tbl2 = (
                write(
                    inject_libs=library_injections,
                    prepend=rec._repro_run_after_lines(run_after, run_tbl, sandbox_root),
                )
                if library_injections or run_after
                else run_tbl
            )
            # Only what this pass changes runs again (#581): the scripts that got library()
            # lines or another script to load first, the scripts the new order moves (the
            # users of the new edges), and the scripts that read what those write. Running
            # every script again let an unchanged script that had timed out use the
            # paper's remaining time a second time, and the changed script then got none.
            # An unchanged script keeps its first result; so does a reader of a changed
            # script's files that timed out in the first pass, which would only time out
            # again.
            redo2: list[str | None] = list(
                dict.fromkeys([*library_injections, *run_after, *(e[1] for e in extra_edges)])
            )
            io_names = _names(io)
            reads_of = dict(
                zip(io_names, [h._as_list(r) for r in h._col(io, "reads")], strict=True)
            )
            writes_of = dict(
                zip(io_names, [h._as_list(w) for w in h._col(io, "writes")], strict=True)
            )
            first_outcome: dict[str | None, str | None] = {}
            for f, o in zip(_names(run_results), h._chr_col(run_results, "outcome"), strict=True):
                first_outcome.setdefault(f, o)  # match(): the first row of a name
            while True:
                written = {
                    _lower(w) for f in _intersect(redo2, list(writes_of)) for w in writes_of[f]
                }
                readers = [f for f, r in reads_of.items() if any(_lower(x) in written for x in r)]
                readers = [
                    f for f in _setdiff(readers, redo2) if first_outcome.get(f) != "timed_out"
                ]
                if not readers:
                    break
                redo2 += readers
            # A script this pass changed but had no time left for is skipped_time_limit, not
            # its first result: that was the failure the change was meant to fix, and it was
            # never run in the form the check meant to test (#581).
            run_results2 = runsupport._repro_later_pass(
                run_r_scripts(_rows_in(run_tbl2, redo2), _intersect(run_order2, redo2))
            )
            run_results = _replace_runs(run_results, run_results2, run_order2)
            order_tbl = order_tbl2  # report the corrected order
            run_tbl = run_tbl2  # so the setwd/font/injection detail (#391) reflects pass 2
            reran = True
            reran_for_reorder = len(extra_edges) > 0
            dbg("execution finished (pass 2). outcomes: ", _outcomes_text(run_results))

    # A script that stopped on a default of R that has changed since the code was written
    # (text read as factors before R 4.0, data files in Latin-1) runs once more with the
    # old default set at its top, and so do the scripts that stopped on a file it writes,
    # which it may now make (#568).
    old_defaults_used = rec._repro_old_defaults(run_results)
    if old_defaults_used:
        io_names = _names(io)
        makers_write = [
            w
            for f, ws in zip(io_names, h._col(io, "writes"), strict=True)
            if f in old_defaults_used
            for w in h._as_list(ws)
        ]
        outcomes_now = h._chr_col(run_results, "outcome")
        stopped_on = [
            o == "errored"
            and any(_lower(u) in makers_write for u in outcomes._repro_unopened_files(m))
            for o, m in zip(outcomes_now, _msgs(run_results), strict=True)
        ]
        redo = list(
            dict.fromkeys(
                [
                    *old_defaults_used,
                    *(f for f, s in zip(_names(run_results), stopped_on, strict=True) if s),
                ]
            )
        )
        # a script waiting for such a script's file was written for the same R, and in
        # 10.1111/1365-2435.13388 then stopped on relevel(): it gets the same defaults
        for f in _setdiff(redo, list(old_defaults_used)):
            old_defaults_used[str(f)] = list(
                dict.fromkeys(k for v in old_defaults_used.values() for k in v)
            )
        prepend_lines = dict(rec._repro_run_after_lines(run_after, run_tbl, sandbox_root) or {})
        for f, kinds in old_defaults_used.items():
            prepend_lines[f] = [*rec._repro_old_default_lines(kinds), *prepend_lines.get(f, [])]
        dbg(f"re-running {len(redo)} script(s) with R's old defaults: ", ", ".join(map(str, redo)))
        run_tbl = write(inject_libs=library_injections, prepend=prepend_lines)
        run_now = _order_names(order_tbl)
        # a script given the old defaults but no time is skipped_time_limit, not the
        # failure the old defaults were meant to fix (#581)
        res_old = runsupport._repro_later_pass(
            run_r_scripts(_rows_in(run_tbl, redo), _pass_order(run_now, redo))
        )
        run_results = _replace_runs(run_results, res_old, run_now)

    # A file a script could not open, which the repository lists but which was not
    # downloaded, is fetched now, and the scripts that stopped on it run once more (#568).
    # The first download leaves out some kinds of files, and a name built while the script
    # runs cannot be seen before.
    plan_now = plan
    # (not when the time is up: the scripts would not be run again)
    if (
        _download_on(a.get("download"))
        and any(layout._repro_fetchable(structure_df))
        and not core._repro_short_of_time(total_timeout, 60)
    ):
        stopped = [o == "errored" for o in h._chr_col(run_results, "outcome")]
        msgs = _msgs(run_results)
        unopened = {
            u.lower()
            for u in outcomes._repro_unopened_files(
                [m for m, s in zip(msgs, stopped, strict=True) if s]
            )
        }
        sd_names = _names(structure_df)
        rows = [
            ok and (core._norm_base(n) or "") in unopened
            for ok, n in zip(layout._repro_fetchable(structure_df), sd_names, strict=True)
        ]
        if any(rows):
            dbg(
                f"fetching {sum(rows)} file(s) scripts could not open: ",
                ", ".join(_na_str(n) for n, r in zip(sd_names, rows, strict=True) if r),
            )
            structure_df = layout._repro_fetch_files(
                structure_df,
                rows,
                a.get("max_file_size", 100),
                a.get("max_download_size", 500),
                a.get("cache", False),
                a.get("skip_on_api_limit", False),
            )
            locs = h._chr_col(structure_df, "file_location")
            got = [r and loc is not None for r, loc in zip(rows, locs, strict=True)]
            if any(got):
                plan_now = layout._repro_package_plan(structure_df)
                got_locs = {loc for loc, g in zip(locs, got, strict=True) if g}
                new_plan = plan_now[
                    [s is not None and s in got_locs for s in h._chr_col(plan_now, "source")]
                ].reset_index(drop=True)
                core.repro_materialize_layout(
                    new_plan,
                    structure_df,
                    sandbox_root,
                    deadline=core._repro_clock_deadline(total_timeout),
                )
                fetched_after = [
                    (n or "").lower() for n, g in zip(_names(structure_df), got, strict=True) if g
                ]
                redo = [
                    f
                    for f, m, s in zip(_names(run_results), msgs, stopped, strict=True)
                    if s and any(x in m.lower() for x in fetched_after)
                ]
                if redo:
                    dbg(
                        f"re-running {len(redo)} script(s) with the fetched file(s): ",
                        ", ".join(map(str, redo)),
                    )
                    # a script with its file fetched but no time is skipped_time_limit, not
                    # the missing-file failure the fetch was meant to fix (#581)
                    res3 = runsupport._repro_later_pass(
                        run_r_scripts(_rows_in(run_tbl, redo), _pass_order(run_order_names, redo))
                    )
                    run_results = _replace_runs(run_results, res3, run_order_names)

    # A script that stopped on a file another script wrote later runs once more: a name
    # built while a script runs cannot be seen before, so the order missed it (#568; see
    # _repro_written_later()). So does a script that stopped on a file the deposit holds in
    # another folder, once that file is copied to where it looked (see
    # _repro_place_from_deposit()). A script runs again each time it stops on a file it did
    # not stop on before: a figure script that loads the files of four analysis scripts
    # stops on each in turn (#575, item 4). Five passes at most, for a chain.
    r_written_later: dict[str, list[str]] = {}
    r_placed = layout._none_placed()
    r_deposit_placed = r_placed
    r_seen: dict[str, list[str]] = {}  # script -> the files it stopped on when run again
    # A script whose write stopped on a folder the deposit does not have runs once more
    # after the folder is made (metacheck 0.4.3.12, #591; see _repro_make_failed_write_folders())
    r_folders_made = layout._folders_frame([])
    for _ in range(5):
        names_after = layout._repro_sandbox_names(sandbox_root)
        later = layout._repro_written_later(run_results, r_names_before, names_after)
        later = {
            s: fs
            for s, fs in later.items()
            if _setdiff([_lower(x) for x in fs], [_lower(x) for x in r_seen.get(s, [])])
        }
        r_placed = bind_rows(
            [
                r_placed,
                layout._repro_place_written(
                    later, run_results, run_tbl, sandbox_root, r_names_before, names_after
                ),
            ]
        )
        from_deposit = layout._repro_place_from_deposit(
            run_results, run_tbl, sandbox_root, r_names_before
        )
        r_deposit_placed = bind_rows([r_deposit_placed, from_deposit])
        made = layout._repro_make_failed_write_folders(
            run_results, run_tbl, sandbox_root, _folders_by_script(r_folders_made)
        )
        r_folders_made = bind_rows([r_folders_made, made])
        redo = list(
            dict.fromkeys(
                [*later, *h._chr_col(from_deposit, "script"), *h._chr_col(made, "script")]
            )
        )
        if not redo:
            break
        dbg(
            f"re-running {len(redo)} script(s) that stopped on a file another script wrote, "
            "the deposit holds elsewhere, or a folder a write needed: ",
            ", ".join(map(str, redo)),
        )
        # a script this pass was to run but had no time for is skipped_time_limit, not the
        # failure the pass was meant to fix (#581)
        res4 = runsupport._repro_later_pass(
            run_r_scripts(_rows_in(run_tbl, redo), _pass_order(run_order_names, redo))
        )
        ran4 = (
            [
                f
                for f, o in zip(_names(res4), h._chr_col(res4, "outcome"), strict=True)
                if o != "skipped_time_limit"
            ]
            if res4 is not None
            else []
        )
        for s in _intersect(list(later), ran4):
            r_written_later[str(s)] = list(
                dict.fromkeys([*r_written_later.get(str(s), []), *later[str(s)]])
            )
            r_seen[str(s)] = list(dict.fromkeys([*r_seen.get(str(s), []), *later[str(s)]]))
        run_results = _replace_runs(run_results, res4, run_order_names)
        if not ran4:
            break

    # The relabels below change the outcome and error columns of a copy.
    run_results = run_results.copy()
    outcome = h._chr_col(run_results, "outcome")
    error_type = h._chr_col(run_results, "error_type")
    rr_names = _names(run_results)

    def relabel(rows: Sequence[bool], label: str) -> None:
        for i, hit in enumerate(rows):
            if hit:
                outcome[i] = label
                error_type[i] = None

    def errored(flags: Sequence[bool]) -> list[bool]:
        return [o == "errored" and bool(f) for o, f in zip(outcome, flags, strict=True)]

    # A model file or other non-R content (see the sniff step) was never a script to run:
    # it gets an outcome of its own, not `not_parsed`, which counted it as a failure in the
    # results (#568). Not for a file a script sources: its functions were used.
    for i, rr_fn in enumerate(rr_names):
        j = file_names.index(rr_fn) if rr_fn in file_names else None
        if j is not None and sniff_type[j] is not None and outcome[i] != "run_by_source":
            outcome[i] = sniff_type[j]
    msgs = _msgs(run_results)
    # A script whose container never started says nothing about the script.
    if sandbox == "docker":
        relabel(errored(outcomes._repro_docker_failed(msgs)), "docker_failed")
    # A script that stopped because nobody could answer it says nothing about the code;
    # nor does a program that needs its arguments from the person running it (#568).
    # code_text_list[run_results$file_name]: no code for a name that is not there
    code_of = [
        list(code_text_list[f]) if f is not None and f in code_text_list else [] for f in rr_names
    ]
    asks = [
        p or q
        for p, q in zip(
            outcomes._repro_needs_person_error(msgs),
            outcomes._repro_needs_arguments(
                code_of, msgs, "R", out=h._chr_col(run_results, "stdout") or None
            ),
            strict=True,
        )
    ]
    relabel(errored(asks), "needs_a_person")
    # Stopped because the network is off, which says nothing about the code either.
    if sandbox == "docker":
        relabel(errored(outcomes._repro_needs_internet_error(msgs)), "needs_internet")
    # Ran out of the memory each script may use, which says nothing about the code either
    # (#578, item 8; see _repro_out_of_memory())
    relabel(errored(outcomes._repro_out_of_memory(msgs)), "out_of_memory")
    _set_col(run_results, "outcome", outcome)
    _set_col(run_results, "error_type", error_type)
    # Stopped on a file the repository lists but that we did not download, such as one
    # over the size limit: the script could not be tried (#568).
    r_not_downloaded = layout._repro_not_downloaded(run_results, structure_df)
    nd = set(h._chr_col(r_not_downloaded, "script"))
    relabel([f in nd for f in rr_names], "not_downloaded")
    _set_col(run_results, "outcome", outcome)
    _set_col(run_results, "error_type", error_type)
    # Always a message, and a reason for each script that did not run through.
    err = h._chr_col(run_results, "error")
    stderr = h._chr_col(run_results, "stderr")
    no_message = errored(
        [not grepl(r"\S", _na_str(e) + _na_str(s)) for e, s in zip(err, stderr, strict=True)]
    )
    err = [
        "the script stopped without a message" if nm else e
        for e, nm in zip(err, no_message, strict=True)
    ]
    _set_col(run_results, "error", err)
    reason = outcomes._repro_failure_reason(outcome, _msgs(run_results))
    # stopped on a Git LFS pointer the deposit has no file for: a missing file (#568)
    r_lfs_stopped = layout._repro_lfs_stopped(run_results, sandbox_root)
    reason = ["file" if f in r_lfs_stopped else r for f, r in zip(rr_names, reason, strict=True)]
    _set_col(run_results, "reason", reason)
    run_results.attrs["reran_for_order"] = reran
    run_results.attrs["reran_for_reorder"] = reran_for_reorder

    # Turn each script's run into statistical results, the same way JASP/jamovi files are
    # handled -- so RUN R code contributes to statistical_output too. TWO sources, merged
    # by _r_merge_captures():
    #   * CAPTURED OBJECTS (R/r-capture.R) -- the htest/summary.lm/anova objects
    #     themselves, recorded by a task callback in the run subprocess. Exact values, and
    #     the object knows which test produced a statistic (the same "W" is Shapiro-Wilk's
    #     after shapiro.test but the rank sum after wilcox.test), which the printed form
    #     cannot express;
    #   * CONSOLE OUTPUT -- read_r_output() parsing stdout, which still catches results a
    #     script only ever printed (inside a loop, via print()) and so never returned at
    #     top level.
    # Captures win where both describe the same source line; the text path fills the rest.
    stat_cut: dict[str, int] = {}  # scripts whose output was searched only in part
    r_stat_output: list[dict[str, Any]] = []
    has_lines = "script_lines" in run_results.columns
    has_caps = "captures" in run_results.columns
    for i, out_fn in enumerate(rr_names):
        fn = _na_str(out_fn)
        so: Any = as_str(run_results["stdout"].iloc[i]) if "stdout" in run_results.columns else None
        # One line per script, with the amount of output: parsing long output used to take
        # hours with nothing printed (#483).
        dbg(f"parsing output of '{fn}' ({0 if not so else len(so.split(chr(10)))} lines) ...")
        so = runsupport._repro_output_head(so)
        n_lines = getattr(so, "n_lines", None)
        if n_lines is not None:
            stat_cut[fn] = int(n_lines)
        exec_lines = h._as_list(run_results["script_lines"].iloc[i]) if has_lines else []
        caps = run_results["captures"].iloc[i] if has_caps else None
        code_lines = exec_lines if exec_lines else None
        try:
            cap_tabs = _r_captures_to_tables(caps, source_label=fn, code_lines=code_lines)
        except Exception:
            cap_tabs = []
        txt_tabs: list[dict[str, Any]] = []
        if so is not None and so != "":
            try:
                txt_tabs = read_r_output(str(so), source_label=fn, code_lines=code_lines)
            except Exception:
                txt_tabs = []
        tabs = _r_merge_captures(cap_tabs, txt_tabs)
        if not tabs:
            continue
        pid = ctx["pid"] if "pid" in ctx else h._pid(paper, structure_df, code_tbl)
        r_stat_output.append(
            {
                "file": fn,
                "n_tables": len(tabs),
                "source": "r_output",
                "n_captured": len(cap_tabs),
                "json": stat_output_json(tabs, paper_id=pid, source_file=fn),
                "long": stat_results_long(tabs, paper_id=pid, source_file=fn),
            }
        )
    if r_stat_output:
        stat_output = [*stat_output, *r_stat_output]
        dbg(f"extracted statistical output from {len(r_stat_output)} script(s)' console output.")
    # Re-write statistical_output/ now that the scripts' results have joined stat_output:
    # the earlier write only had JASP/jamovi results.
    if stat_output:
        stat_output_write(stat_output, sandbox_root)

    return {
        "sandbox_root": sandbox_root,
        "run_results": run_results,
        "install_results": install_results,
        "run_tbl": run_tbl,
        "order_tbl": order_tbl,
        "stat_output": stat_output,
        "structure_df": structure_df,
        "plan": plan_now,
        "recorded": recorded,
        "install_r": install_r,
        "library_injections": library_injections,
        "run_after": run_after,
        "old_defaults_used": old_defaults_used,
        "stan_rows": stan_rows,
        "r_written_later": r_written_later,
        "r_placed": r_placed,
        "r_deposit_placed": r_deposit_placed,
        "r_folders_made": r_folders_made,
        # for the report
        "definer_lookup": definer_lookup,
        "run_by_source": run_by_source,
        "rprofile_replaced": rprofile_replaced,
        "r_not_downloaded": r_not_downloaded,
        "r_lfs_stopped": r_lfs_stopped,
        "stat_cut": stat_cut,
        "docker_image": docker_image,
    }


def _lower(x: Any) -> str | None:
    s = as_str(x)
    return None if s is None else s.lower()


def _outcomes_text(run_results: pd.DataFrame) -> str:
    """``paste(sprintf("%s=%s", file_name, outcome), collapse = "; ")``."""
    return "; ".join(
        f"{_na_str(f)}={_na_str(o)}"
        for f, o in zip(_names(run_results), h._chr_col(run_results, "outcome"), strict=True)
    )


def _mod_rows(rows: Sequence[tuple[str | None, str, str]]) -> pd.DataFrame:
    """Rows of ``modifications``: ``file_name``, ``change_type``, ``detail``."""
    return pd.DataFrame(
        {
            "file_name": pd.array([r[0] for r in rows], dtype="string"),
            "change_type": pd.array([r[1] for r in rows], dtype="string"),
            "detail": pd.array([r[2] for r in rows], dtype="string"),
        }
    )


# -- the report --------------------------------------------------------------------------------


def _execution_report(
    ex: Mapping[str, Any], a: Mapping[str, Any], ctx: Mapping[str, Any]
) -> list[Any]:
    """The report's "Execution" part, from what :func:`_execute` returned (*ex*).

    Port of the ``if (!is.null(run_results) && nrow(run_results) > 0) { ... }`` part of
    "## Execution" in the R block, with the install report and the recorded versions'
    section. Returns the report items in R's order: strings, ``scroll_table()`` and
    ``collapse_section()`` objects.
    """
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
    from metacheck.repro.recorded import _repro_old_defaults_text, _repro_recorded_report
    from metacheck.repro.runsupport import _repro_output_cut_note

    sandbox = a["sandbox"]
    run_results: pd.DataFrame = ex["run_results"]
    run_tbl: pd.DataFrame | None = ex.get("run_tbl")
    install_results: pd.DataFrame | None = ex.get("install_results")
    recorded = ex.get("recorded")
    library_injections: Mapping[str, Sequence[str]] = ex.get("library_injections") or {}
    run_after: Mapping[str, Sequence[str]] = ex.get("run_after") or {}
    run_by_source: Mapping[str, Sequence[str]] = ex.get("run_by_source") or {}
    definer_lookup: Mapping[str, str] = ex.get("definer_lookup") or {}

    oc = h._chr_col(run_results, "outcome")
    names = _names(run_results)

    def count(o: str) -> int:
        return sum(x == o for x in oc)

    def with_outcome(o: str) -> list[str | None]:
        return [f for f, x in zip(names, oc, strict=True) if x == o]

    n_ran_ok = count("ran_ok")
    n_by_src = count("run_by_source")
    n_late = count("skipped_time_limit")
    n_scripts = sum(x not in _NOT_SCRIPTS for x in oc)
    by_src_text = (
        f", and {n_by_src:d} {plural(n_by_src, 'was', 'were')} run by another script with "
        "`source()`"
        if n_by_src > 0
        else ""
    )
    report_exec = (
        "**The code was run.** Each script ran in an isolated "
        f"{'Docker container' if sandbox == 'docker' else 'subprocess'} against a throwaway "
        f"copy of {_LAYOUT_NAME}, in the run order above, with a "
        f"{_sprintf_d(a['timeout'])}-second per-script timeout. Of {n_scripts:d} "
        f"script{plural(n_scripts)}: {n_ran_ok:d} ran without error, {count('errored'):d} "
        f"errored, {count('timed_out'):d} timed out, {count('dependency_unavailable'):d} could "
        "not run because one of its own dependencies is unavailable, "
        f"{count('not_parsed'):d} did not parse{by_src_text}."
        # recorded versions have their own part of the report (see below)
        + (
            " Running against current package versions: a break can reflect version drift, "
            "which argues for pinning versions."
            if recorded is None
            else ""
        )
    )
    # scripts not started because the time for all scripts together ran out
    if n_late > 0:
        report_exec += (
            f" {n_late:d} {plural(n_late, 'script was', 'scripts were')} not started, or "
            f"stopped early, because {_repro_deadline_text(a.get('total_timeout'))} had run out."
            f"{_repro_no_prep_text(a['run_state'].get('prep_skipped_left'))}"
        )
    n = count("docker_failed")
    if n > 0:
        report_exec += (
            f" **Docker failed for {n:d} script{plural(n)}, so {plural(n, 'it was', 'they were')} "
            "not run.** This says nothing about the code; run the check again when Docker works."
        )
    n = count("needs_a_person")
    if n > 0:
        report_exec += (
            f" **{n:d} script{plural(n)} need{plural(n, 's', '')} a person** to choose a file, "
            "answer a question or use an app, which cannot be reproduced without one: "
            + ", ".join(_na_str(f) for f in with_outcome("needs_a_person"))
            + ". Naming the file in the script solves this."
        )
    if h._is_true(a.get("internet")):
        report_exec += " " + _repro_internet_text(a["net"])
    if count("needs_internet") > 0:
        report_exec += _repro_needs_internet_text(
            [str(f) for f in with_outcome("needs_internet")], h._is_true(a.get("internet"))
        )
    if count("download_too_large") > 0:
        report_exec += _repro_download_stop_text(
            [str(f) for f in with_outcome("download_too_large")]
        )
    nd: pd.DataFrame | None = ex.get("r_not_downloaded")
    if count("not_downloaded") > 0 and nd is not None:
        shown = set(with_outcome("not_downloaded"))
        report_exec += (
            _repro_not_downloaded_text(
                nd[[s in shown for s in h._chr_col(nd, "script")]].reset_index(drop=True),
                a.get("max_file_size", 100),
            )
            or ""
        )
    old_defaults_used = ex.get("old_defaults_used") or {}
    if old_defaults_used:
        report_exec += _repro_old_defaults_text(old_defaults_used, with_outcome("ran_ok")) or ""
    if run_after:
        k = len(run_after)
        pairs = "; ".join(f"`{u}` after `{'`, `'.join(d)}`" for u, d in run_after.items())
        report_exec += (
            f" **{k:d} script{plural(k)} use{plural(k, 's', '')} an object or function that only "
            "another script defines**, so in the second run that script was loaded first, in "
            f"the same session: {pairs}. Such a script should `source()` the other itself, "
            "since it does not run on its own."
        )
    r_written_later = ex.get("r_written_later") or {}
    if r_written_later:
        report_exec += _repro_written_later_text(
            r_written_later, with_outcome("ran_ok"), ex.get("r_placed")
        )
    r_deposit_placed = ex.get("r_deposit_placed")
    if r_deposit_placed is not None and len(r_deposit_placed):
        report_exec += _repro_placed_text(r_deposit_placed)
    r_folders_made = ex.get("r_folders_made")
    if r_folders_made is not None and len(r_folders_made):
        report_exec += _repro_folders_made_text(r_folders_made)
    r_lfs_stopped = ex.get("r_lfs_stopped") or {}
    if r_lfs_stopped:
        report_exec += _repro_lfs_stopped_text(r_lfs_stopped)
    n_filled = sum(len(v) for v in (ctx.get("r_choices_filled") or {}).values())
    if n_filled > 0:
        report_exec += (
            f" **{n_filled:d} `file.choose()` call{plural(n_filled, ' was', 's were')} replaced "
            "by the file the code names**, so the script could run; the script should name the "
            "file itself."
        )

    # Scripts run only inside the script that sources them: say which, and why they were
    # not run on their own.
    report_by_source: list[Any] | None = None
    if n_by_src > 0:
        report_by_source = [
            f"{n_by_src:d} script{plural(n_by_src)} {plural(n_by_src, 'is', 'are')} run by "
            f"another script with `source()`, so {plural(n_by_src, 'it was', 'they were')} not "
            f"run on {plural(n_by_src, 'its', 'their')} own: each script runs in a fresh R "
            f"process, and on {plural(n_by_src, 'its', 'their')} own a sourced script lacks the "
            "packages and objects the sourcing script sets up first. "
            f"{plural(n_by_src, 'Its', 'Their')} outcome is part of the sourcing script's "
            "outcome.",
            scroll_table(
                pd.DataFrame(
                    {
                        "File": list(run_by_source),
                        "Run by": [", ".join(map(str, v)) for v in run_by_source.values()],
                    }
                ),
                maxrows=10,
            ),
        ]

    # library() calls added for a `could not find function` error, reported like the
    # setwd() repair below: what was added, and which scripts ran once it was. A script
    # that only runs with a library() call it does not make itself is reproducible only
    # after that repair.
    report_libs: list[Any] | None = None
    if library_injections:
        k = len(library_injections)
        ok = set(with_outcome("ran_ok"))
        ok_after_libs = [f for f in library_injections if f in ok]
        report_libs = [
            f"**{k:d} script{plural(k)} use{plural(k, 's', '')} functions from packages "
            f"{plural(k, 'it does', 'they do')} not load {plural(k, 'itself', 'themselves')}.** "
            "The packages are usually loaded in another script of the project, which only helps "
            "when the scripts run in one R session in the right order. To run "
            f"{plural(k, 'it', 'them')} we added the `library()` calls below at the top. Any "
            "such script that then ran is **reproducible only after adding `library()` calls it "
            "should make itself**."
        ]
        if ok_after_libs:
            report_libs.append(
                "Reproducible after adding `library()` calls: " + ", ".join(ok_after_libs) + "."
            )
        report_libs.append(
            scroll_table(
                pd.DataFrame(
                    {
                        "File": list(library_injections),
                        "library() calls added": [
                            ", ".join(f"library({p})" for p in pkgs)
                            for pkgs in library_injections.values()
                        ],
                    }
                ),
                maxrows=10,
            )
        )

    # Note the corrective re-run when it happened, so the outcomes are read as "after
    # re-ordering"/"after injecting library()", not the first attempt.
    if h._is_true(run_results.attrs.get("reran_for_order")):
        bits: list[str] = []
        if h._is_true(run_results.attrs.get("reran_for_reorder")):
            bits.append(
                "a script hit an `object '...' not found` error, indicating it expected a "
                "variable another script defines. We inferred which script supplies it and "
                "re-ordered so that script runs first"
            )
        if library_injections:
            bits.append(
                'a script hit a `could not find function "..."` error; we added the '
                "`library()` calls it needs (see below)"
            )
        report_exec += (
            f" \n\n*{'; '.join(bits)}, and re-ran once. The outcomes above are from that "
            "corrected run.*"
        )

    # setwd() warning. A script that only runs after we strip a setwd() is "reproducible
    # after ignoring a setwd() that should not be in the code".
    report_setwd: list[Any] | None = None
    if run_tbl is not None and "setwd_removed" in run_tbl.columns:
        n_removed = h._col(run_tbl, "setwd_removed")
        hit = [not h._na(v) and v > 0 for v in n_removed]
        if any(hit):
            setwd_files = [f for f, x in zip(_names(run_tbl), hit, strict=True) if x]
            setwd_n = [v for v, x in zip(n_removed, hit, strict=True) if x]
            setwd_paths = (
                [p for p, x in zip(h._chr_col(run_tbl, "setwd_paths"), hit, strict=True) if x]
                if "setwd_paths" in run_tbl.columns
                else [None] * len(setwd_files)
            )
            ok = set(with_outcome("ran_ok"))
            ok_after = list(dict.fromkeys(f for f in setwd_files if f in ok))
            ns = len(setwd_files)
            report_setwd = [
                f"**{ns:d} script{plural(ns)} call{plural(ns, 's', '')} `setwd()`.** This is bad "
                "practice: it hardcodes where the code must run (often an absolute path on the "
                "author's own machine) and breaks the moment the code is run anywhere else. To "
                "run the code we replaced these calls with a statement that does nothing. Any "
                "such script that then ran is **reproducible only after ignoring a `setwd()` "
                "that should not have been in the code and needs to be fixed**."
            ]
            if ok_after:
                report_setwd.append(
                    "Reproducible after removing `setwd()`: "
                    + ", ".join(_na_str(f) for f in ok_after)
                    + "."
                )
            report_setwd.append(
                scroll_table(
                    pd.DataFrame(
                        {
                            "File": setwd_files,
                            "setwd() removed": _int_col(setwd_n).tolist(),
                            "Path(s) it set": [
                                p if h._nzchar(p) else "(non-literal)" for p in setwd_paths
                            ],
                        }
                    ),
                    maxrows=10,
                )
            )

    # Undefined-variable errors, called out explicitly: an "object 'X' not found" usually
    # means the script is a snippet expecting a variable another script defines, rather than
    # a standalone program. "Defined in <file>" comes from the corrective re-run's lookup,
    # also for a symbol left unresolved there (0 or >1 definers); blank when the symbol is
    # not defined anywhere in the paper's code (a genuine bug, not a sourcing gap).
    err_types = h._chr_col(run_results, "error_type")
    undef_vars = h._chr_col(run_results, "undefined_var")
    undef = [i for i, t in enumerate(err_types) if t == "undefined_variable"]
    report_undef: list[Any] | None = None
    if undef:
        nu = len(undef)
        defined_in = [
            definer_lookup.get(f"{_na_str(names[i])}||{_na_str(undef_vars[i])}", "") for i in undef
        ]
        report_undef = [
            f"**{nu:d} script{plural(nu)} failed on an undefined variable or function** "
            "(`object '...' not found` / `could not find function \"...\"`). This typically "
            "means the script expects a variable or function that another script defines — "
            "i.e. it is meant to be sourced into a larger session, not run on its own — or "
            "the symbol is simply never created. When another of the paper's own script files "
            "defines the same name, this is called out below as a likely sourcing/copy-paste "
            "gap. Each is counted as an error (red).",
            scroll_table(
                pd.DataFrame(
                    {
                        "File": [names[i] for i in undef],
                        "Missing variable": [undef_vars[i] for i in undef],
                        "Defined in": [
                            d if d != "" else "(not defined anywhere in the paper's code)"
                            for d in defined_in
                        ],
                    }
                ),
                maxrows=10,
            ),
        ]

    # Dependency-unavailable errors, called out explicitly: a script that library()/
    # require()s a package neither live CRAN nor the CRAN Archive could provide never got
    # to run its own analysis. Reported separately from "errored" and NOT counted toward
    # the traffic light, since this is an availability problem, not a bug in the code.
    nodep = [i for i, t in enumerate(err_types) if t == "dependency_unavailable"]
    report_nodep: list[Any] | None = None
    if nodep:
        nn = len(nodep)
        report_nodep = [
            f"**{nn:d} script{plural(nn)} could not run because {plural(nn, 'its', 'their')} own "
            "dependency is unavailable** — the package failed to install from both live CRAN "
            "and the CRAN Archive (or a GitHub/URL source could not be resolved). This is an "
            "infrastructure limitation, not a defect in the paper's code, so it does **not** "
            "force the traffic light red.",
            scroll_table(pd.DataFrame({"File": [names[i] for i in nodep]}), maxrows=10),
        ]

    elapsed = h._col(run_results, "elapsed")
    exec_table = pd.DataFrame(
        {
            "File": names,
            "Outcome": oc,
            "Detail": ["" if t is None else t for t in err_types],
            "Time (s)": [None if h._na(e) else r_round(float(e), 1) for e in elapsed],
        }
    )

    # For EVERY script (not just failures) a collapsed dropdown holding the output the run
    # generated -- stdout (the analysis results the script printed) followed by stderr
    # (warnings / the error that stopped it): the reproduction transcript. Each stream is
    # tail-capped so a very chatty script cannot bloat the HTML; the FULL output is always
    # kept in the returned result (see the note below). The fence is longer than any run of
    # backticks in the output, so the output cannot close it (see _fence()).
    state = {"truncated": False}
    errors = h._chr_col(run_results, "error")
    stdouts = h._col(run_results, "stdout")
    stderrs = h._col(run_results, "stderr")
    output_blocks: list[Any] = []
    for i in range(len(run_results)):
        so = _tail_cap(stdouts[i] if stdouts else "", state)
        se = _tail_cap(stderrs[i] if stderrs else "", state)
        err = errors[i] if errors else ""
        # A file that never ran (skipped / not parsed) has no transcript to show.
        if not so and not se and not h._nzchar(err):
            continue
        body: list[str] = []
        err_text = _na_str(err)
        fence = _fence(so, se, [err_text])
        if so:
            body += ["**Output (stdout):**", fence, *so, fence]
        if se:
            body += ["**Messages / errors (stderr):**", fence, *se, fence]
        if not body and h._nzchar(err):
            body = [fence, err_text, fence]
        output_blocks.append(
            collapse_section(
                "\n".join(body), title=f"Output — {_na_str(names[i])} ({_na_str(oc[i])})"
            )
        )
    truncation_note = (
        f"*Output shown here is capped at the last {_MAX_LINES:d} lines per stream to keep the "
        "report readable. The complete, untruncated output of every script is stored in the "
        "module result: `reproducibility_check_output$run_results$stdout` (and `$stderr`), one "
        "row per script.*"
        if state["truncated"]
        else None
    )

    report: list[Any] = ["#### Execution", report_exec, scroll_table(exec_table, maxrows=15)]
    if report_undef is not None:
        report += ["**Undefined-variable errors**", *report_undef]
    if report_nodep is not None:
        report += ["**Dependency-unavailable errors**", *report_nodep]
    if report_setwd is not None:
        report += ["**`setwd()` in the code**", *report_setwd]
    if run_tbl is not None:
        report += _repro_slash_report(run_tbl) or []
    # what else we changed to run the code, so the authors can fix it themselves
    stan_rows: pd.DataFrame | None = ex.get("stan_rows")
    if stan_rows is not None and len(stan_rows):
        report += [
            "**Other changes we made to run the code**",
            "Stan files in the syntax Stan removed in version 2.33 were given the newer syntax, "
            "and Git LFS pointers (placeholders a GitHub archive holds instead of the file) were "
            "replaced by the deposit's copy of the file. Updating the Stan files, and depositing "
            "the files themselves, solves this.",
            scroll_table(
                pd.DataFrame(
                    {
                        "File": h._chr_col(stan_rows, "file_name"),
                        "Change": h._chr_col(stan_rows, "detail"),
                    }
                ),
                maxrows=10,
            ),
        ]
    # The calls that install packages themselves cannot run here.
    n_env = int(ex.get("rprofile_replaced") or 0)
    if run_tbl is not None and "env_calls_replaced" in run_tbl.columns:
        n_env += sum(int(v) for v in h._col(run_tbl, "env_calls_replaced") if not h._na(v))
    if n_env > 0:
        report += [
            "**Calls that install packages in the code**",
            f"{n_env:d} call{plural(n_env)} to `install.packages()`, `groundhog`, `checkpoint`, "
            f"`renv`, `pacman` and the like {plural(n_env, 'was', 'were')} replaced, since they "
            "install packages themselves: `groundhog.library()` and `p_load()` by `library()`, "
            "the others by a statement that does nothing. The scripts ran with the packages "
            "installed before the run.",
        ]
    if report_libs is not None:
        report += ["**`library()` calls added**", *report_libs]
    if report_by_source is not None:
        report += ["**Run by another script**", *report_by_source]
    if output_blocks:
        report.append("*Expand a row below to see the output each script produced.*")
        report += output_blocks
    if truncation_note is not None:
        report.append(truncation_note)
    cut_note = _repro_output_cut_note(ex.get("stat_cut") or {})
    if cut_note is not None:
        report.append(cut_note)

    if install_results is not None and len(install_results) > 0:
        report += _r_install_report(install_results)
    report += (
        _repro_recorded_report(
            recorded,
            install_results,
            ctx.get("version_pin"),
            sandbox,
            h._is_true(a.get("docker_use_declared_version")),
        )
        or []
    )
    return report


#: The labels of the failure categories of an install (``.repro_classify_install_message()``)
_INSTALL_CATEGORY_LABELS = {
    "source_unknown": (
        "not on CRAN or Bioconductor, and the code does not say where it comes from"
    ),
    "cran_unavailable": "not available on CRAN for this R version",
    "compile_failure": "compilation/configure failure",
    "network": "network issue",
    "transitive_dependency_missing": "a dependency of this package is unavailable",
    "other": "uncategorised",
}


def _r_install_report(install_results: pd.DataFrame) -> list[str]:
    """The dependency-installation lines of the "Execution" part (metacheck 0.4.3.7)."""
    from metacheck.repro.outcomes import _repro_source_unknown, _repro_source_unknown_text

    installed = [h._is_true(v) for v in h._col(install_results, "installed")]
    pkgs = h._chr_col(install_results, "package")
    n_ok = sum(installed)
    out = [
        "Dependencies were installed into a throwaway library before running: "
        f"{n_ok:d} succeeded, {len(installed) - n_ok:d} failed. "
        + ", ".join(_na_str(p) for p in pkgs)
        + " were installed."
    ]
    # A package installed via the CRAN Archive retry (live CRAN no longer serves it)
    # reproduced against a STALE version, not necessarily the one the authors used.
    if "via_archive" in install_results.columns:
        via_arch = [
            p
            for p, v in zip(pkgs, h._col(install_results, "via_archive"), strict=True)
            if h._is_true(v)
        ]
        if via_arch:
            out.append(
                f"**{len(via_arch):d} of these were no longer on live CRAN** and were instead "
                "installed from the **CRAN Archive**'s most recent published version — a real "
                "version the package once had, but not necessarily the one the paper's authors "
                "used: " + ", ".join(_na_str(p) for p in via_arch) + "."
            )
    # A failure category separates "genuinely gone from CRAN" (permanent) from "transient
    # network hiccup" (worth a retry) from "needs a compiler toolchain" (environment setup,
    # not a paper problem), as a label beside the raw message, which stays the
    # authoritative text. No category (older saved results, or NA): no label.
    failed = [i for i, ok in enumerate(installed) if not ok]
    if failed:
        msgs = h._chr_col(install_results, "message")
        has_cat = "category" in install_results.columns
        cats = h._chr_col(install_results, "category") if has_cat else [None] * len(pkgs)
        parts = []
        for i in failed:
            # an unknown category has the label NA, which sprintf() prints
            label = "" if cats[i] is None else _INSTALL_CATEGORY_LABELS.get(str(cats[i]), "NA")
            msg = msgs[i] if msgs else ""
            parts.append(
                _na_str(pkgs[i])
                + (f" [{label}]" if label != "" else "")
                + (f" ({_na_str(msg)})" if h._nzchar(msg) else "")
            )
        out.append("**Failed:** " + "; ".join(parts))
    unknown = _repro_source_unknown_text(_repro_source_unknown(install_results))
    if unknown is not None:
        out.append(unknown)
    return out
