"""Reproducibility Check (port of ``inst/modules/reproducibility_check.R``).

The module assesses whether a paper's code can be run on its data: a static
phase (dependencies, Psych-DS path rewriting, run order, missing inputs)
that always runs, and an opt-in execution phase (``execute=True``) that runs
the R scripts in Docker containers (the default, ``sandbox="docker"``) or, only
when asked for with ``sandbox="process"``, in ``Rscript`` subprocesses on this
machine with no filesystem or network isolation. This is a deliberate
difference from metacheck, whose default is ``"process"`` (D62 in
docs/UPSTREAM_ISSUES.md). The building blocks are :mod:`metacheck.repro`; the
module-local pieces are in :mod:`metacheck.modules._reproducibility`.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import bind_rows, grepl, plural, r_round, strsplit
from metacheck.module import module
from metacheck.modules import _reproducibility as h
from metacheck.report import collapse_section, scroll_table

_NA_REPLACE_EMPTY = {
    "repro_code_n": 0.0,
    "repro_runnable": 0.0,
    "repro_missing_inputs": 0.0,
    "repro_deps": 0.0,
}
_NA_REPLACE = {**_NA_REPLACE_EMPTY, "repro_ran_ok": 0.0}

_FN_MISSING_PAT = "could not find function ['\"]([^'\"]+)['\"]"
_MAX_LINES = 5000


def _int_col(values: Sequence[Any]) -> pd.Series:
    return pd.Series(pd.array([None if h._na(v) else int(v) for v in values], dtype="Int64"))


def _str_col(values: Sequence[Any]) -> pd.Series:
    return pd.Series(pd.array([h._chr(v) for v in values], dtype="string"))


def _bool_col(values: Sequence[Any]) -> pd.Series:
    return pd.Series(pd.array([None if h._na(v) else bool(v) for v in values], dtype="boolean"))


def _obj_col(values: Sequence[Any]) -> pd.Series:
    return pd.Series(list(values), dtype=object)


def _sprintf_d(x: Any) -> str:
    """``sprintf("%d", x)`` of a number (R refuses a non-integer double)."""
    if isinstance(x, float) and not x.is_integer():
        raise ValueError("invalid format '%d'; use format %f, %e, %g or %a for numeric objects")
    return f"{int(x):d}"


def _summary_empty(pid: str | None, em: Mapping[str, Any] | None) -> pd.DataFrame:
    ms = em["summary"] if em is not None else None
    return pd.DataFrame(
        {
            "paper_id": pd.array([pid], dtype="string"),
            "repro_code_n": pd.array([0], dtype="Int64"),
            "repro_runnable": pd.array([0], dtype="Int64"),
            "repro_missing_inputs": pd.array([0], dtype="Int64"),
            "repro_deps": pd.array([0], dtype="Int64"),
            "repro_tests_reported": pd.array(
                [None if ms is None else int(ms["n_tests"])], dtype="Int64"
            ),
            "repro_tests_matched": pd.array(
                [None if ms is None else int(ms["n_found"])], dtype="Int64"
            ),
        }
    )


def _tail_cap(txt: Any, state: dict[str, bool]) -> list[str]:
    """The last :data:`_MAX_LINES` lines of one output stream (R ``tail_cap()``)."""
    if h._na(txt):
        # nzchar(NA) is TRUE: strsplit(NA) gives one NA line
        return ["NA"]
    if txt == "":
        return []
    lines = list(strsplit(txt, "\n", fixed=True))
    if len(lines) > _MAX_LINES:
        state["truncated"] = True
        lines = [
            f"... [{len(lines) - _MAX_LINES:d} earlier lines omitted — see full output in "
            "the result object] ...",
            *lines[-_MAX_LINES:],
        ]
    return lines


def _fence(*streams: Sequence[Any]) -> str:
    """A code fence longer than any run of backticks in *streams*.

    metacheck's four backticks can be closed by the code's own output, and what follows is then
    part of the report: a ``{r}`` chunk there is run by Quarto when the report is rendered. An
    output without a long run of backticks (every ordinary one) still gets four.
    """
    longest = max(
        (len(run) for s in streams for line in s for run in re.findall(r"`+", str(line))),
        default=0,
    )
    return "`" * max(4, longest + 1)


def _order_names(order_tbl: pd.DataFrame) -> list[str]:
    """``order_tbl$file_name[order(order_tbl$order)]`` (``NA`` orders last)."""
    names = h._chr_col(order_tbl, "file_name")
    orders = h._col(order_tbl, "order")
    idx = sorted(
        range(len(names)),
        key=lambda i: (h._na(orders[i]), 0 if h._na(orders[i]) else orders[i]),
    )
    return [str(names[i]) for i in idx]


def _na_str(x: Any) -> str:
    """``sprintf("%s", x)`` of one string (``"NA"`` for a missing value)."""
    v = h._chr(x)
    return "NA" if v is None else v


def _match_first(x: Sequence[Any], table: Sequence[Any]) -> list[int | None]:
    """R ``match(x, table)`` (0-based, ``None`` for no match)."""
    first: dict[Any, int] = {}
    for i, v in enumerate(table):
        first.setdefault(v, i)
    return [first.get(v) for v in x]


def _pick(values: Sequence[Any], idx: Sequence[int | None]) -> list[Any]:
    return [None if i is None else values[i] for i in idx]


#: The sandboxes ``execute = TRUE`` can run the code in, the safe one first: ``match.arg()``
#: takes the first choice as the default, so this order IS the default (metacheck's is the
#: reverse, ``c("process", "docker")``: D62).
_SANDBOXES = ("docker", "process")

_OLD_DEFAULT = ("process", "docker")


def _match_sandbox(sandbox: Any) -> str:
    """``match.arg(sandbox)``: exact or unique partial match (``"d"`` is ``"docker"``).

    ``None`` and the whole choice vector give ``"docker"``. The choice vector that is
    metacheck's default, ``("process", "docker")``, is refused: a caller that passes
    metacheck's default on would otherwise get a Docker run where it expected a run on
    this machine, or the reverse, and must say which one it wants.
    """
    from metacheck.utils import match_arg

    if isinstance(sandbox, list | tuple):
        if list(sandbox) == list(_SANDBOXES):
            return _SANDBOXES[0]
        if list(sandbox) == list(_OLD_DEFAULT):
            raise ValueError(
                '\'arg\' must be of length 1: c("process", "docker") is metacheck\'s default, '
                'which runs the code on this machine; the default here is "docker". Name the '
                'sandbox you want: "docker", or "process" to run WITHOUT isolation.'
            )
        if len(sandbox) != 1:
            raise ValueError("'arg' must be of length 1")
        sandbox = sandbox[0]
    if sandbox is not None and not isinstance(sandbox, str):
        raise ValueError("'arg' must be NULL or a character vector")
    return match_arg(sandbox, list(_SANDBOXES))


@module(
    title="Reproducibility Check",
    description="""
        This module assesses whether a paper's code can be run on its data. It works
        in two phases. The **static** phase (always run) collects the package
        dependencies the code declares, rewrites each script's data-file paths to the
        Psych-DS layout the release uses, works out the order the scripts must run in,
        and diagnoses why any referenced input file is unavailable — reporting what a
        reproduction attempt would involve and where it is likely to break. The
        **execution** phase (opt-in, `execute = TRUE`) then actually runs the code:
        it materialises the Psych-DS layout into a throwaway directory, optionally
        installs the declared dependencies into a throwaway library, runs each script
        in order in its own Docker container (the default) with a per-script timeout,
        and captures the outcome and full output of each run.
    """,
    details="""
        By default (`execute = FALSE`) the module does **not run any code** — it
        performs only static analysis: reading dependencies with the same extractors
        as `code_check`, mapping referenced files to their new locations with the
        plan from `psychds_check`, and building a run-order dependency graph. It
        reports what *would* be installed and run rather than doing it.

        When `execute = TRUE`, the module additionally **runs the downloaded code** —
        a deliberate, opt-in action gated behind that argument. By default
        (`sandbox = "docker"`) every script runs in its own locked-down Docker
        container, so Docker must be installed and running; if it is not, the module
        stops with an error and runs nothing. In the run phase the container has no
        network (`--network none`), a read-only root filesystem (with a writable,
        in-memory `/tmp`), only the throwaway sandbox directory and the package
        library (read-only) mounted — never your home folder or any other file of
        yours — a non-root user, no Linux capabilities, no privilege escalation and a
        limit of 512 processes, and it is stopped at the timeout. The CPU and memory
        of a container are limited only in a batch run (`workers > 1`).

        What the container does **not** isolate: the optional install phase (only
        with `install_missing = TRUE`) has network access by design, because packages
        must be fetched. It runs the packages' install scripts, as a non-root user
        with all capabilities dropped, and sees only its install script and the
        package library, never the paper's data. The image is the pre-built
        `ghcr.io/scienceverse/metacheck_r:latest` (or `rocker/r-ver:<version>` with
        `docker_use_declared_version = TRUE`), which Docker pulls under its `:latest`
        tag, so it can change between runs — a known limit. A container shares the
        host's kernel: it is a strong boundary for ordinary code, not a promise
        against a kernel exploit.

        `sandbox = "process"` is the explicit, unsafe alternative (it needs R, an
        `Rscript`): it runs the code **on your machine, as you, with no filesystem or
        network isolation**. A subprocess isolates a crash and nothing else, so the
        code can read, change or delete your files and use the network. Never use it
        for code you do not trust; the module warns each time it is used. Installs
        (when `install_missing = TRUE`) and the throwaway run library never touch
        your main R library, unless `cran_install_main = TRUE`. (metacheck's default
        is `"process"`; pytacheck's is `"docker"`, a deliberate difference, D62 in
        docs/UPSTREAM_ISSUES.md.) Each script's outcome
        is one of `ran_ok`, `errored`, `timed_out`, `skipped_missing_inputs`,
        `dependency_unavailable`, or `not_parsed`; a timeout means "still running at
        the cutoff", not a failure, so raise `timeout` for legitimately long-running
        (e.g. Bayesian) scripts. `dependency_unavailable` means the script's own
        package could not be installed from live CRAN, the CRAN Archive, or a named
        GitHub/URL source — an infrastructure limitation, not a defect in the
        paper's code, so unlike `errored`/`timed_out` it does **not** force the
        traffic light red. The full stdout/stderr of every run is kept in the
        result's `run_results` (the report shows it in per-script dropdowns, capped
        for readability). The traffic light reflects the STATIC readiness signals
        by default, but an execution error (`errored`/`timed_out`) does recolour it
        to red — a script that crashes on its own analysis is a genuine
        reproduction failure.

        Dependencies are reported name-only, with the source (CRAN, GitHub, or a base
        package already shipped with R) — static analysis cannot know which *version*
        was used, and running against current package versions is itself informative:
        a break argues the authors should have pinned versions.

        Run order is derived from read-after-write data dependencies (a script writing
        a file another reads must run first), `source()` edges, and numeric filename
        prefixes as a weak tie-breaker. Ambiguous orderings, dependency cycles, and
        inputs a script reads but that no earlier script produces (and that are not in
        the repository) are surfaced as warnings, since these are the situations most
        likely to make an otherwise-correct reproduction fail.
    """,
    keywords=["results"],
    requires=["network"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={
        "paper": "a paper object or paperlist object, or NULL to check local files\n"
        "only (see [test_paper()])",
        "local_path": "optional path to a local directory, passed through to\n"
        "`data_check` / `repo_check` when their output is not already available",
        "local_only": "if TRUE, skip online repository lookups (see `repo_check`)",
        "model": "the LLM model name used only when `llm_use(TRUE)` (passed to the\n"
        "upstream modules whose study grouping the path rewrite relies on)",
        "params": "a named list passed to `llm()`, used only when `llm_use(TRUE)`",
        "execute": "if TRUE, actually RUN the paper's code (against a throwaway\n"
        "copy of the Psych-DS layout). This runs downloaded code and is off by\n"
        "default; it is a deliberate, opt-in action. By default it runs in a Docker\n"
        "container (see `sandbox`).",
        "sandbox": 'how `execute = TRUE` runs each script. `"docker"` (the default)\n'
        "runs it inside a locked-down Docker container. In the run phase: no\n"
        "network, a read-only root filesystem (a writable in-memory `/tmp`), only\n"
        "the throwaway sandbox directory and the package library (read-only)\n"
        "mounted, a non-root user, all capabilities dropped, a process limit. It\n"
        "needs Docker installed and running (see [repro_docker_available()]);\n"
        "without it the module stops with an error and runs nothing. The optional\n"
        "install phase (`install_missing = TRUE`) is NOT isolated from the\n"
        "network, by design, because packages must be fetched: it runs their\n"
        "install scripts non-root with capabilities dropped and sees only its\n"
        "install script and the package library, never the paper's data. The image\n"
        "is pulled and tagged `:latest`, so it can change between runs (a known\n"
        'limit). `"process"` runs it in a subprocess on this machine with NO\n'
        "filesystem or network isolation: the code can read, change and delete\n"
        "anything you can, and use the network. Pass it only for code you trust;\n"
        'the module warns each time. (metacheck\'s default is `"process"`; this is\n'
        "a deliberate difference, D62.)",
        "docker_use_declared_version": 'if TRUE (and `sandbox = "docker"`), use\n'
        "the R version `code_check`'s version-pinning detection found declared in\n"
        "the repository instead of the pre-built `metacheck_r` image.",
        "install_missing": "if TRUE (and `execute = TRUE`), install the code's\n"
        "declared dependencies into a throwaway temp library before running.",
        "cran_install_main": "if TRUE (and `install_missing` and `execute` are\n"
        'both TRUE, and `sandbox = "process"`), CRAN-source dependencies are\n'
        "installed into your DEFAULT R library instead of the throwaway one.",
        "timeout": "per-script timeout in seconds for the execute phase\n(default 600).",
        "keep_sandbox": "if TRUE, do not delete the temp materialised layout\n"
        "after the run, and return its path as element `sandbox` of the result.",
        "cache": "if `TRUE`, forwarded to the internal `data_check`/`code_check`\n"
        "runs: files they download are kept in the persistent on-disk cache.",
        "download,skip_types,peek_zips,max_file_size,max_download_size": "forwarded\n"
        "to the internal `data_check` run, with the same defaults `data_check`\n"
        "itself uses.",
        "tables_dir": "optional directory of per-paper saved module outputs\n"
        "(`capture_module_tables()`), read before running `data_check`/\n"
        "`code_check`/`psychds_check`.",
        "workers": "when `paper` is a paperlist AND `execute = TRUE` AND\n"
        '`sandbox = "docker"`, the number of papers to run CONCURRENTLY.',
        "results_dir": "when `paper` is a paperlist and `workers > 1`, an\n"
        "optional directory to write each paper's full result to as soon as it\n"
        "finishes.",
    },
)
def reproducibility_check(
    paper: Any,
    local_path: str | os.PathLike[str] | None = None,
    local_only: bool = False,
    model: str | None = None,
    params: Mapping[str, Any] | None = None,
    execute: bool = False,
    sandbox: str | Sequence[str] = "docker",
    docker_use_declared_version: bool = False,
    install_missing: bool = False,
    cran_install_main: bool = False,
    timeout: float = 600,
    keep_sandbox: bool = False,
    cache: Any = False,
    download: Any = "data",
    skip_types: Sequence[str] | str | None = None,
    peek_zips: bool = False,
    max_file_size: float = 100,
    max_download_size: float = 500,
    skip_on_api_limit: bool = False,
    tables_dir: str | os.PathLike[str] | None = None,
    workers: int = 1,
    results_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Port of inst/modules/reproducibility_check.R::reproducibility_check().

    Reads ``code_check``'s ``table``/``version_pin``, ``psychds_check``'s
    ``table`` (the path-rewrite plan) and ``data_check``'s ``structure`` from
    the chain, else from *tables_dir*, else runs the missing modules (LLM
    off). ``model=None`` means ``llm_model()``.

    With ``execute=True`` the paper's R scripts are RUN. By default
    (``sandbox="docker"``; metacheck's default is ``"process"``: D62) each one
    runs in its own locked-down Docker container: in the run phase no network, a
    read-only root filesystem, only the throwaway sandbox directory (and the
    package library, read-only) mounted, a non-root user, all capabilities dropped
    and a process limit. Without a running Docker the call raises ``RuntimeError``
    and nothing runs; it never falls back to the host. What the container does not
    isolate: the optional install phase (``install_missing=True``), which has
    network access by design (packages must be fetched), runs the packages' install
    scripts non-root with capabilities dropped and sees only its install script and
    the package library, never the paper's data; and the image is pulled and tagged
    ``:latest``, so it can change between runs (a known limit).

    ``sandbox="process"`` runs the scripts in ``Rscript`` subprocesses on this
    machine, as you, with NO filesystem or network isolation: not for code you do
    not trust. It must be asked for by name, and each run with it emits one
    :class:`~metacheck.core.errors.PytacheckWarning`. A paper list with
    ``workers > 1`` under Docker runs its papers concurrently. The static phase
    (``execute=False``, the default) needs no Docker and runs nothing.
    """
    sandbox = _match_sandbox(sandbox)

    from metacheck.papers.model import is_paper_list

    if h._is_true(execute):
        # Refuse before anything is downloaded, installed or run, and before a batch starts
        # its workers. A missing Docker is never answered by a process run.
        _require_sandbox(sandbox)

    if (
        is_paper_list(paper)
        and len(paper) > 1
        and workers > 1
        and h._is_true(execute)
        and sandbox == "docker"
    ):
        return h._reproducibility_check_batch(
            paper,
            workers=workers,
            results_dir=results_dir,
            local_path=local_path,
            local_only=local_only,
            model=model,
            params=params,
            execute=execute,
            sandbox=sandbox,
            docker_use_declared_version=docker_use_declared_version,
            install_missing=install_missing,
            cran_install_main=cran_install_main,
            timeout=timeout,
            keep_sandbox=keep_sandbox,
            cache=cache,
            download=download,
            skip_types=skip_types,
            peek_zips=peek_zips,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            skip_on_api_limit=skip_on_api_limit,
            tables_dir=tables_dir,
        )

    from metacheck.http import skip_on_api_limit as _skip_limit
    from metacheck.utils import local_options

    cleanup: list[str] = []
    try:
        with (
            local_options({"metacheck.skip_on_api_limit": skip_on_api_limit}),
            _skip_limit(bool(skip_on_api_limit)),
        ):
            return _check(
                paper,
                cleanup,
                local_path=local_path,
                local_only=local_only,
                model=model,
                params=params,
                execute=execute,
                sandbox=sandbox,
                docker_use_declared_version=docker_use_declared_version,
                install_missing=install_missing,
                cran_install_main=cran_install_main,
                timeout=timeout,
                keep_sandbox=keep_sandbox,
                cache=cache,
                download=download,
                skip_types=skip_types,
                peek_zips=peek_zips,
                max_file_size=max_file_size,
                max_download_size=max_download_size,
                skip_on_api_limit=skip_on_api_limit,
                tables_dir=tables_dir,
            )
    finally:
        for d in cleanup:
            shutil.rmtree(d, ignore_errors=True)


_PROCESS_WARNING = (
    'reproducibility_check(execute = TRUE, sandbox = "process") runs the paper\'s code on this '
    "machine, as you, with no filesystem or network isolation: it can read, change or delete "
    'your files and use the network. Use it only for code you trust. sandbox = "docker" (the '
    "default) runs it in a container with no network and no access to your files."
)


def _require_sandbox(sandbox: str) -> None:
    """Check that the sandbox ``execute = TRUE`` asked for is available, or refuse.

    ``"docker"`` needs a running Docker, else :class:`RuntimeError` says why and how to go on
    (install or start Docker, or ask for ``sandbox = "process"`` by name). ``"process"`` needs
    an ``Rscript`` and warns once that nothing isolates the code. There is no fallback from
    one to the other.
    """
    if sandbox == "docker":
        from metacheck.repro.docker import repro_docker_available

        docker_ok = repro_docker_available()
        if not docker_ok.get("ok"):
            raise RuntimeError(
                f'execute = TRUE with sandbox = "docker": {docker_ok.get("msg")} The paper\'s '
                "code is run in a Docker container, with no network and no access to your files, "
                'so nothing was run. Install or start Docker; or pass sandbox = "process" to run '
                "the code on this machine WITHOUT any isolation (not for code you do not trust)."
            )
        return
    from metacheck.core.errors import PytacheckWarning
    from metacheck.repro.core import _rscript

    if _rscript() is None:
        raise RuntimeError(
            'execute = TRUE with sandbox = "process" needs R (an `Rscript` on PATH or '
            "PYTACHECK_RSCRIPT): it runs each script in a subprocess. "
            'Install R, or use sandbox = "docker".'
        )
    warnings.warn(_PROCESS_WARNING, PytacheckWarning, stacklevel=3)


def _new_sandbox(keep_sandbox: bool, cleanup: list[str]) -> str:
    """``tempfile("repro_sandbox_")``, removed on exit unless *keep_sandbox*."""
    root = tempfile.mkdtemp(prefix="repro_sandbox_")
    if not h._is_true(keep_sandbox):
        cleanup.append(root)
    return root


def _check(paper: Any, cleanup: list[str], **a: Any) -> dict[str, Any]:
    """The single-paper check (everything after the argument checks)."""
    from metacheck.module import get_prev_outputs

    execute = h._is_true(a["execute"])
    keep_sandbox = h._is_true(a["keep_sandbox"])

    def pid(*dfs: Any) -> str | None:
        return h._pid(paper, *dfs)

    saved = h.SavedTables(a["tables_dir"], pid)

    def upstream(mod: str, item: str) -> Any:
        x = get_prev_outputs(mod, item)
        return x if x is not None else saved.get(mod, item)

    # 1. Inputs from the upstream modules --------------------------------------
    code_tbl = upstream("code_check", "table")
    plan = upstream("psychds_check", "table")
    structure_df = upstream("data_check", "structure")
    version_pin = upstream("code_check", "version_pin")

    def run_missing(mod: str) -> Any:
        return h.run_missing(
            paper,
            mod,
            local_path=a["local_path"],
            local_only=a["local_only"],
            model=a["model"],
            params=a["params"],
            cache=a["cache"],
            skip_on_api_limit=a["skip_on_api_limit"],
            download=a["download"],
            skip_types=a["skip_types"],
            peek_zips=a["peek_zips"],
            max_file_size=a["max_file_size"],
            max_download_size=a["max_download_size"],
        )

    if structure_df is None:
        structure_df = run_missing("data_check").get("structure")
    if plan is None:
        plan = run_missing("psychds_check").table
    if code_tbl is None:
        cc = run_missing("code_check")
        code_tbl = cc.table
        version_pin = cc.get("version_pin")

    # SPSS / Stata data without syntax ------------------------------------------
    fnames_all = h._chr_col(structure_df, "file_name")
    code_fnames = h._chr_col(code_tbl, "file_name")
    ss = h.spss_stata(fnames_all, code_fnames)
    spss_red, stata_red = ss["spss_red"], ss["stata_red"]
    spss_report, stata_report = ss["spss_report"], ss["stata_report"]

    # Self-reproducible output (JASP/jamovi/SPSS Viewer/notebooks, .smcl, .out) ----
    stat_output, self_repro_report = h.self_repro(structure_df, lambda: pid(structure_df, code_tbl))

    # Materialised root for statistical_output/ ---------------------------------
    sandbox_root: str | None = None
    if stat_output:
        from metacheck.repro.core import repro_materialize_layout
        from metacheck.statout.stat_output import stat_output_write

        sandbox_root = _new_sandbox(keep_sandbox, cleanup)
        repro_materialize_layout(plan, structure_df, sandbox_root)
        stat_output_write(stat_output, sandbox_root)

    # Non-R code with no way to check its results ------------------------------
    nonr_lang, nonr_report = h.nonr_code(
        code_tbl, fnames_all, ss["has_sav"], ss["has_dta"], len(stat_output) > 0
    )

    def empty(text: str, tl: str, extra_report: Sequence[str]) -> dict[str, Any]:
        em = h.match_output(paper, stat_output)
        n_output_stats = h._n_stats(stat_output)
        has_self_repro_here = len(stat_output) > 0
        if has_self_repro_here:
            n = len(stat_output)
            headline = (
                f"We assessed {n:d} self-reproducible output file{plural(n)} "
                "(JASP/jamovi/SPSS-Viewer/Mplus) instead of R code."
            )
        elif nonr_report is not None:
            headline = (
                "We found no R code to run, but did find other code with no reproducible "
                "output to check it against — see below."
            )
        else:
            headline = text
        lines: list[str] = [headline]
        if has_self_repro_here:
            lines.append(
                f"{n_output_stats:d} statistic{plural(n_output_stats)} stored from the "
                "extracted output."
            )
        if em is not None:
            lines.append(h.match_summary_line(em["summary"]))
        if has_self_repro_here:
            lines.append(text)
        report = [*extra_report, *(self_repro_report or []), *(em["report"] if em else [])]
        resp: dict[str, Any] = {
            "table": pd.DataFrame(),
            "summary_table": _summary_empty(pid(structure_df, code_tbl), em),
            "na_replace": dict(_NA_REPLACE_EMPTY),
            "traffic_light": tl,
            "summary_text": h._paste_bullets(lines),
            "report": report or None,
            "stat_output": stat_output or None,
            "match_table": em["table_raw"] if em is not None else None,
        }
        if keep_sandbox and sandbox_root is not None:
            resp["sandbox"] = sandbox_root
        return resp

    note = h.non_r_note(structure_df, nonr_lang)
    if spss_red or stata_red:
        empty_tl = "red"
    elif ss["has_sav"] or ss["has_dta"] or stat_output:
        empty_tl = "yellow"
    elif nonr_report is not None:
        empty_tl = "info"
    else:
        empty_tl = "na"
    extra = [*(spss_report or []), *(stata_report or []), *(nonr_report or [])]

    # Only R is handled in this phase.
    if code_tbl is None or len(code_tbl) == 0 or "language" not in code_tbl.columns:
        return empty(
            "We found no R code files to assess for reproducibility." + note, empty_tl, extra
        )
    langs = h._chr_col(code_tbl, "language")
    if "file_name" in code_tbl.columns:
        renv = grepl(r"(^|/)renv/activate\.R$", h._chr_col(code_tbl, "file_name"), ignore_case=True)
        keep = [i for i, lang in enumerate(langs) if lang == "R" and not renv[i]]
    else:
        # r_files[!grepl(pattern, NULL), ] is r_files[logical(0), ]: no rows
        keep = []
    r_files = code_tbl.iloc[keep].reset_index(drop=True)
    if len(r_files) == 0:
        return empty(
            "We found no R code files to assess for reproducibility (this phase runs R code only)."
            + note,
            empty_tl,
            extra,
        )
    return _assess_r(
        paper,
        cleanup,
        a,
        r_files=r_files,
        code_tbl=code_tbl,
        plan=plan,
        structure_df=structure_df,
        version_pin=version_pin,
        stat_output=stat_output,
        sandbox_root=sandbox_root,
        spss_red=spss_red,
        stata_red=stata_red,
        spss_report=spss_report,
        stata_report=stata_report,
        self_repro_report=self_repro_report,
        nonr_report=nonr_report,
        execute=execute,
        keep_sandbox=keep_sandbox,
    )


def _resolver(r_files: pd.DataFrame, structure_df: Any) -> Any:
    """R ``resolve_row_path(i)``: a code row's local path, else its URL, else ``None``."""
    own = h._chr_col(r_files, "file_location") or [None] * len(r_files)
    urls = h._chr_col(r_files, "file_url") or [None] * len(r_files)
    fnames = h._chr_col(r_files, "file_name")
    lookup: list[tuple[str | None, str | None]] = []
    if h._has_cols(structure_df, "file_name", "file_location"):
        lookup = list(
            zip(
                h._chr_col(structure_df, "file_name"),
                h._chr_col(structure_df, "file_location"),
                strict=True,
            )
        )
    first_loc: dict[str | None, str | None] = {}
    by_base: dict[str, list[str | None]] = {}  # basename -> locations, in row order
    for n, loc in lookup:
        first_loc.setdefault(n, loc)
        if n is not None:
            by_base.setdefault(str(h._basename(n)), []).append(loc)

    def resolve(i: int) -> str | None:
        if h._file_exists(own[i]):
            return own[i]
        fn = fnames[i]
        if fn in first_loc and h._file_exists(first_loc[fn]):
            return first_loc[fn]
        # basename fallback: the first existing location among the rows whose
        # file_name has the same basename (basename(NA) matches nothing)
        b = h._basename(fn)
        if b is not None:
            for loc in by_base.get(b, ()):
                if h._file_exists(loc):
                    return loc
        url = urls[i]
        if url is not None and url != "":
            return url
        return None

    return resolve


def _read_code(path: str | None) -> list[str] | None:
    """``code_read(path)`` (``None`` on an error)."""
    if path is None:
        return None
    from metacheck.codecheck.core import code_read

    try:
        return [x if x is not None else "NA" for x in code_read(path)]
    except Exception:
        return None


def _assess_r(
    paper: Any,
    cleanup: list[str],
    a: Mapping[str, Any],
    *,
    r_files: pd.DataFrame,
    code_tbl: pd.DataFrame,
    plan: Any,
    structure_df: Any,
    version_pin: Any,
    stat_output: list[dict[str, Any]],
    sandbox_root: str | None,
    spss_red: bool,
    stata_red: bool,
    spss_report: list[str] | None,
    stata_report: list[str] | None,
    self_repro_report: list[str] | None,
    nonr_report: list[str] | None,
    execute: bool,
    keep_sandbox: bool,
) -> dict[str, Any]:
    """The R-code analysis (sections 2-10 of the R module)."""
    from metacheck.codecheck.core import code_extract_r, code_packages
    from metacheck.repro.core import (
        _repro_base_packages,
        _repro_content_sniff,
        _repro_is_jags_model,
        repro_dependencies,
        repro_file_io,
        repro_missing_inputs,
        repro_rewrite_paths,
        repro_run_order,
    )

    def pid(*dfs: Any) -> str | None:
        return h._pid(paper, *dfs)

    resolve = _resolver(r_files, structure_df)

    # 1b. Byte-identical duplicates across repo mirrors --------------------------
    # R reads each file once to hash it and again for raw_text_list; a row's
    # path does not depend on the other rows, so the first read is kept and
    # reused (a URL-resolved file is downloaded once instead of twice).
    read_texts: list[list[str] | None] = [_read_code(resolve(i)) for i in range(len(r_files))]
    hashes: list[str | None] = [None if not txt else "\n".join(txt) for txt in read_texts]
    seen: set[str] = set()
    dup: list[bool] = []
    for hv in hashes:
        dup.append(hv is not None and hv in seen)
        if hv is not None:
            seen.add(hv)
    n_dup = sum(dup)
    dup_report: list[str] | None = None
    fnames = h._chr_col(r_files, "file_name")
    if n_dup > 0:
        descs = []
        for i, is_dup in enumerate(dup):
            if not is_dup:
                continue
            kept = hashes.index(hashes[i])
            dup_loc, kept_loc = resolve(i), resolve(kept)
            at = f" at `{dup_loc}`" if dup_loc is not None and dup_loc != "" else ""
            kept_at = f" at `{kept_loc}`" if kept_loc is not None and kept_loc != "" else ""
            fn_i = "NA" if fnames[i] is None else fnames[i]
            fn_k = "NA" if fnames[kept] is None else fnames[kept]
            descs.append(f"`{fn_i}`{at} (same as `{fn_k}`{kept_at})")
        dup_report = [
            "#### Duplicate files across repo mirrors",
            f"{n_dup:d} file{plural(n_dup)} {plural(n_dup, 'is', 'are')} byte-identical to "
            "another file already in the run (the paper links more than one "
            "repository/component that mirrors the same materials). "
            f"{plural(n_dup, 'It was', 'They were')} only run once, from its first occurrence, "
            "to avoid wasted duplicate execution: " + "; ".join(descs) + ".",
        ]
        keep_rows = [i for i, is_dup in enumerate(dup) if not is_dup]
        r_files = r_files.iloc[keep_rows].reset_index(drop=True)
        resolve = _resolver(r_files, structure_df)
        fnames = h._chr_col(r_files, "file_name")
        read_texts = [read_texts[i] for i in keep_rows]
    n_code = len(r_files)
    names = [str(f) for f in fnames]

    # 2. Read each file (raw, and purled for R Markdown/Quarto) --------------------
    raw_texts: list[list[str]] = []
    code_texts: list[list[str]] = []
    is_rmd = grepl(r"\.(rmd|qmd)$", fnames, ignore_case=True)
    for i in range(n_code):
        path = resolve(i)
        raw = read_texts[i] or []
        raw_texts.append(raw)
        if path is None:
            code_texts.append([])
        elif is_rmd[i]:
            try:
                ext = code_extract_r(path)
                code_texts.append([] if ext is None else list(ext))
            except Exception:
                code_texts.append([])
        else:
            code_texts.append(raw)
    code_text_list = h.RNamedList(names, code_texts)

    # 3. Dependencies (pooled across files) -------------------------------------
    deps = repro_dependencies(code_text_list, "R")
    try:
        cc_pkgs = code_packages(h._col(r_files, "packages"))
    except Exception:
        cc_pkgs = []
    have = set(h._chr_col(deps, "package"))
    missing_pkgs = list(dict.fromkeys(p for p in cc_pkgs if p not in have))
    if missing_pkgs:
        base_pkgs = set(_repro_base_packages())
        is_base = [p in base_pkgs for p in missing_pkgs]
        deps = bind_rows(
            [
                deps,
                pd.DataFrame(
                    {
                        "package": pd.array(missing_pkgs, dtype="string"),
                        "source": pd.array(
                            ["base" if b else "cran" for b in is_base], dtype="string"
                        ),
                        "ref": pd.array([None] * len(missing_pkgs), dtype="string"),
                        "base": pd.array(is_base, dtype="boolean"),
                    }
                ),
            ]
        )
    install_deps = deps[~deps["base"].fillna(False).astype(bool)].reset_index(drop=True)
    n_deps = len(install_deps)

    # 4. Per-file path rewrite + I/O for ordering ---------------------------------
    io = repro_file_io(code_text_list)
    rewrites = [
        repro_rewrite_paths(code_texts[i], names[i], plan, "R", structure_df=structure_df)
        for i in range(n_code)
    ]
    rewrite_list = h.RNamedList(names, rewrites)

    def n_true(d: pd.DataFrame, mask: Sequence[bool]) -> int:
        return sum(bool(m) for m in mask) if len(d) else 0

    def lgl(d: pd.DataFrame, col: str) -> list[bool]:
        return [h._is_true(v) for v in h._col(d, col)]

    rewrites_n = [
        n_true(
            d, [m and not am for m, am in zip(lgl(d, "matched"), lgl(d, "ambiguous"), strict=True)]
        )
        for d in rewrites
    ]
    ambiguous_n = [n_true(d, lgl(d, "ambiguous")) for d in rewrites]
    unresolved_refs: list[str | None] = []
    for d in rewrites:
        if len(d):
            unresolved_refs.extend(
                b
                for b, m in zip(h._chr_col(d, "basename"), lgl(d, "matched"), strict=True)
                if not m
            )
    n_call_paths = 0
    n_call_unresolved = 0
    for d in rewrites:
        if len(d) and "is_call" in d.columns:
            for c, m in zip(lgl(d, "is_call"), lgl(d, "matched"), strict=True):
                if c:
                    n_call_paths += 1
                    n_call_unresolved += not m

    # 5. Run order ----------------------------------------------------------------
    order_tbl = repro_run_order(io)
    cycle = [str(x) for x in h._as_list(order_tbl.attrs.get("cycle"))]
    ambiguous_order = h._is_true(order_tbl.attrs.get("ambiguous"))
    fuzzy_sources = order_tbl.attrs.get("fuzzy_sources")
    if not isinstance(fuzzy_sources, pd.DataFrame):
        fuzzy_sources = pd.DataFrame({"from": [], "to": []})

    # 6. Missing-input diagnosis ----------------------------------------------------
    io_writes = h._col(io, "writes")
    produced = list(
        dict.fromkeys(
            h._chr(w).lower() if h._chr(w) is not None else None  # type: ignore[union-attr]
            for ws in io_writes
            for w in h._as_list(ws)
        )
    )
    unres_lower = list(dict.fromkeys(None if r is None else r.lower() for r in unresolved_refs))
    candidate_missing = [r for r in unres_lower if r not in produced]
    missing_inputs = repro_missing_inputs(candidate_missing, plan, structure_df)
    n_missing_inputs = len(missing_inputs)
    n_withheld = sum(s == "withheld_size" for s in h._chr_col(missing_inputs, "status"))

    parse_error_raw = (
        [h._is_true(v) for v in h._col(r_files, "parse_error")]
        if "parse_error" in r_files.columns
        else [False] * n_code
    )

    # Sniff files that failed to parse: JSON/HTML dumps and JAGS/BUGS models.
    sniff_type: list[str | None] = [None] * n_code
    sniff_detail: list[str | None] = [None] * n_code
    for i in [i for i, p in enumerate(parse_error_raw) if p]:
        others = [raw_texts[j] for j in range(n_code) if j != i]
        if _repro_is_jags_model(raw_texts[i], names[i], other_code_text=others):
            sniff_type[i] = "jags_model"
            sniff_detail[i] = "JAGS/BUGS model definition (not standalone R)"
            continue
        detected = _repro_content_sniff(raw_texts[i])
        if detected is not None:
            sniff_type[i] = "not_r_content"
            sniff_detail[i] = f"file content does not appear to be R (detected: {detected})"
    n_not_r_content = sum(s == "not_r_content" for s in sniff_type)
    n_jags_model = sum(s == "jags_model" for s in sniff_type)

    parse_error_genuine = [
        p and s is None for p, s in zip(parse_error_raw, sniff_type, strict=True)
    ]
    parse_errs = sum(parse_error_genuine)
    parses = [not p for p in parse_error_genuine]
    runs_as_r = [not p for p in parse_error_raw]
    ot_names = h._chr_col(order_tbl, "file_name")
    ot_order = h._col(order_tbl, "order")
    file_order = _pick(ot_order, _match_first(names, ot_names))
    file_order = [None if h._na(o) else int(o) for o in file_order]
    placeable = [o is not None for o in file_order]

    io_names = h._chr_col(io, "file_name")
    first_writes: dict[str | None, list[Any]] = {}
    for n, ws in zip(io_names, io_writes, strict=True):
        first_writes.setdefault(n, h._as_list(ws))
    produced_before: list[list[str | None]] = []
    for i in range(n_code):
        ord_i = file_order[i]
        if ord_i is None:
            produced_before.append([])
            continue
        earlier = [
            names[j] for j in range(n_code) if file_order[j] is not None and file_order[j] < ord_i
        ]
        vals = [
            None if h._chr(w) is None else h._chr(w).lower()  # type: ignore[union-attr]
            for e in earlier
            for w in first_writes.get(e, [])
        ]
        produced_before.append(list(dict.fromkeys(vals)))

    unresolved_list: list[list[str | None]] = []
    for i, d in enumerate(rewrites):
        if not len(d):
            unresolved_list.append([])
            continue
        low = [None if b is None else b.lower() for b in h._chr_col(d, "basename")]
        unresolved_list.append(
            list(
                dict.fromkeys(
                    b
                    for b, m in zip(low, lgl(d, "matched"), strict=True)
                    if not m and b not in produced_before[i]
                )
            )
        )
    file_unresolved = [len(u) > 0 for u in unresolved_list]
    runnable = [
        p and pl and not fu and s is None
        for p, pl, fu, s in zip(parses, placeable, file_unresolved, sniff_type, strict=True)
    ]
    n_runnable = sum(runnable)

    not_runnable_reason: list[str | None] = []
    for i in range(n_code):
        if sniff_type[i] is not None:
            not_runnable_reason.append(sniff_type[i])
        elif runnable[i]:
            not_runnable_reason.append(None)
        elif not parses[i]:
            not_runnable_reason.append("parse_error")
        elif not placeable[i]:
            not_runnable_reason.append("unplaceable")
        else:
            not_runnable_reason.append("missing_input")
    unresolved_inputs = [
        ", ".join("NA" if x is None else x for x in u) if u else None for u in unresolved_list
    ]

    # 7. Traffic light (static part) ----------------------------------------------
    if n_missing_inputs > 0 and n_withheld == n_missing_inputs:
        tl = "red"
    elif (
        parse_errs == 0
        and n_missing_inputs == 0
        and len(cycle) == 0
        and not ambiguous_order
        and sum(ambiguous_n) == 0
    ):
        tl = "green"
    else:
        tl = "yellow"
    if spss_red or stata_red:
        tl = "red"

    # 7b. Execution (opt-in; runs downloaded code) ------------------------------------
    run_results: pd.DataFrame | None = None
    install_results: pd.DataFrame | None = None
    run_tbl: pd.DataFrame | None = None
    library_injections: dict[str, str] = {}
    definer_lookup: dict[str, str] = {}
    if execute:
        ex = _execute(
            paper,
            cleanup,
            a,
            names=names,
            code_text_list=code_text_list,
            rewrite_list=rewrite_list,
            plan=plan,
            structure_df=structure_df,
            code_tbl=code_tbl,
            version_pin=version_pin,
            deps=deps,
            install_deps=install_deps,
            io=io,
            order_tbl=order_tbl,
            runs_as_r=runs_as_r,
            file_unresolved=file_unresolved,
            sandbox_root=sandbox_root,
            keep_sandbox=keep_sandbox,
            stat_output=stat_output,
        )
        sandbox_root = ex["sandbox_root"]
        run_results = ex["run_results"]
        install_results = ex["install_results"]
        run_tbl = ex["run_tbl"]
        order_tbl = ex["order_tbl"]
        library_injections = ex["library_injections"]
        definer_lookup = ex["definer_lookup"]
        stat_output = ex["stat_output"]
        if any(o in ("errored", "timed_out") for o in h._chr_col(run_results, "outcome")):
            tl = "red"

    # 8. Per-file table --------------------------------------------------------------
    ot_names = h._chr_col(order_tbl, "file_name")
    m_ord = _match_first(names, ot_names)
    ord_of = _pick(h._col(order_tbl, "order"), m_ord)
    basis_of = _pick(h._chr_col(order_tbl, "order_basis"), m_ord)
    dep_of = _pick(h._chr_col(order_tbl, "depends_on"), m_ord)

    if run_results is not None:
        m_run = _match_first(names, h._chr_col(run_results, "file_name"))
        outcome_of = _pick(h._chr_col(run_results, "outcome"), m_run)
        errtype_of = _pick(h._chr_col(run_results, "error_type"), m_run)
        undefvar_of = _pick(h._chr_col(run_results, "undefined_var"), m_run)
    else:
        outcome_of = errtype_of = undefvar_of = [None] * n_code
    if run_tbl is not None and "setwd_removed" in run_tbl.columns:
        setwd_of = _pick(
            h._col(run_tbl, "setwd_removed"), _match_first(names, h._chr_col(run_tbl, "file_name"))
        )
    else:
        setwd_of = [None] * n_code

    m_io = _match_first(names, io_names)
    reads_col = h._col(io, "reads")
    reads_of = [[] if j is None or not reads_col else h._as_list(reads_col[j]) for j in m_io]
    writes_of = [[] if j is None or not io_writes else h._as_list(io_writes[j]) for j in m_io]

    table = pd.DataFrame(
        {
            "paper_id": _str_col(
                h._col(r_files, "paper_id") if "paper_id" in r_files.columns else [pid()] * n_code
            ),
            "file_name": _str_col(names),
            "parses": _bool_col(parses),
            "run_order": _int_col(ord_of),
            "order_basis": _str_col(basis_of),
            "depends_on": _str_col(dep_of),
            "paths_rewritten": _int_col(rewrites_n),
            "paths_ambiguous": _int_col(ambiguous_n),
            "setwd_removed": _int_col(setwd_of),
            "runnable": _bool_col(runnable),
            "not_runnable_reason": _str_col(not_runnable_reason),
            "unresolved_inputs": _str_col(unresolved_inputs),
            "outcome": _str_col(outcome_of),
            "error_type": _str_col(errtype_of),
            "undefined_var": _str_col(undefvar_of),
            "reads": _obj_col(reads_of),
            "writes": _obj_col(writes_of),
        }
    )

    # 9. Report ----------------------------------------------------------------------
    if execute:
        intro = (
            "This module assesses whether the paper's code could be run on its data, and — "
            "because `execute = TRUE` — **actually ran it**. "
        )
    else:
        intro = (
            "This module assesses whether the paper's code could be run on its data. It "
            "performs static analysis only here: **no code is run**. "
        )
    report: list[Any] = [intro + f"We examined {n_code:d} R code file{plural(n_code)}."]

    ordered = [
        (o, f, dp, b)
        for o, f, dp, b in zip(
            h._col(order_tbl, "order"),
            h._chr_col(order_tbl, "file_name"),
            h._chr_col(order_tbl, "depends_on"),
            h._chr_col(order_tbl, "order_basis"),
            strict=True,
        )
        if not h._na(o)
    ]
    ordered.sort(key=lambda r: r[0])
    order_table = pd.DataFrame(
        {
            "Order": _int_col([r[0] for r in ordered]),
            "File": _str_col([r[1] for r in ordered]),
            "Runs after": _str_col([r[2] if h._nzchar(r[2]) else "—" for r in ordered]),
            "Basis": _str_col([r[3] for r in ordered]),
        }
    )
    if ambiguous_order:
        report_order = (
            "No ordering signal (no data dependency, `source()` call, or numeric filename "
            "prefix) distinguishes the run order of these files. They may need to run in a "
            "specific order that could not be determined automatically — check this before "
            "running."
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
            "another reads runs first), `source()` calls, and numeric filename prefixes."
        )
    n_fuzzy = len(fuzzy_sources)
    if n_fuzzy > 0:
        pairs = "; ".join(
            f"`{_na_str(t)}` -> `{_na_str(f)}`"
            for t, f in zip(
                fuzzy_sources["to"].tolist(), fuzzy_sources["from"].tolist(), strict=True
            )
        )
        report_order += (
            f" **{n_fuzzy:d} `source()` reference{plural(n_fuzzy)}** only matched a file after "
            "normalising both names (lowercase, extension stripped, a leading numbering prefix "
            "stripped, separators collapsed) — a likely renumbered/reworded filename rather than "
            f"an exact match: {pairs}. Treat these as lower-confidence than an exact `source()` "
            "resolution."
        )

    total_rewrites = sum(rewrites_n)
    total_ambiguous = sum(ambiguous_n)
    report_paths = (
        f"To run against the Psych-DS layout, {total_rewrites:d} referenced data "
        f"path{plural(total_rewrites)} would be rewritten to "
        f"{plural(total_rewrites, 'its', 'their')} new location{plural(total_rewrites)}."
    )
    if total_ambiguous > 0:
        report_paths += (
            f" {total_ambiguous:d} reference{plural(total_ambiguous)} matched several files "
            "sharing a name and could not be resolved by study group; "
            f"{plural(total_ambiguous, 'it was', 'they were')} left unrewritten and flagged."
        )
    if n_call_paths > 0:
        resolved = (
            "all of these"
            if n_call_unresolved == 0
            else f"{n_call_paths - n_call_unresolved:d} of {n_call_paths:d}"
        )
        tail = (
            f"; {n_call_unresolved:d} could not be resolved and "
            f"{plural(n_call_unresolved, 'was', 'were')} left unrewritten (the script runs "
            "against the original, un-rewritten call and will likely fail"
            if n_call_unresolved > 0
            else ""
        )
        report_paths += (
            f" **{n_call_paths:d} data path{plural(n_call_paths)} "
            f"{plural(n_call_paths, 'was', 'were')} built at runtime** with "
            "`sprintf()`/`paste()`/`file.path()` instead of written as a literal path — this "
            "is fragile authoring practice (it silently breaks if the code is run from a "
            "different working directory than the author's own). "
            f"We resolved {resolved} on a best-effort basis{tail}."
        )

    missing_table: pd.DataFrame | None = None
    if n_missing_inputs == 0:
        report_missing = (
            "Every data file the code reads is either produced by an earlier script or present "
            "in the repository."
        )
    else:
        report_missing = (
            f"{n_missing_inputs:d} input file{plural(n_missing_inputs)} the code reads "
            f"{plural(n_missing_inputs, 'is', 'are')} not available. Files withheld only because "
            "of their size are distinguished from files absent from the repository — the former "
            "are a size-cap issue, not a reproducibility failure."
        )
        missing_table = pd.DataFrame(
            {
                "File": missing_inputs["basename"].tolist(),
                "Status": missing_inputs["status"].tolist(),
                "Reason": missing_inputs["detail"].tolist(),
            }
        )

    report += [
        "#### Run order",
        report_order,
        scroll_table(order_table, maxrows=10),
        "#### File paths",
        report_paths,
        "#### Missing inputs",
        report_missing,
    ]
    if missing_table is not None:
        report.append(scroll_table(missing_table, maxrows=10))
    if parse_errs > 0:
        report += [
            "#### Parsing",
            f"{parse_errs:d} R file{plural(parse_errs)} did not parse and cannot be run (see "
            "code_check for the errors).",
        ]
    if n_not_r_content > 0 or n_jags_model > 0:
        report.append("#### Non-R content detected")
        if n_not_r_content > 0:
            report.append(
                f"{n_not_r_content:d} file{plural(n_not_r_content)} that failed to parse "
                f"{plural(n_not_r_content, 'is', 'are')} not actually R content (a JSON/HTML/XML "
                "dump saved with an R-type extension) -- this is informational, not a "
                "reproducibility defect."
            )
        if n_jags_model > 0:
            report.append(
                f"{n_jags_model:d} file{plural(n_jags_model)} {plural(n_jags_model, 'is', 'are')} "
                "a JAGS/BUGS model definition (`model { ... }`), not standalone R -- these are "
                "meant to be passed to `rjags::jags.model()`/`R2jags::jags()`/"
                "`runjags::run.jags()`, not run directly, and are not a reproducibility defect."
            )
        rows = [i for i, s in enumerate(sniff_type) if s is not None]
        report.append(
            scroll_table(
                pd.DataFrame(
                    {
                        "File": [names[i] for i in rows],
                        "Detected": [sniff_type[i] for i in rows],
                        "Detail": [sniff_detail[i] for i in rows],
                    }
                ),
                maxrows=10,
            )
        )

    if run_results is not None and len(run_results) > 0:
        report += _execution_report(
            run_results,
            install_results,
            run_tbl,
            library_injections,
            definer_lookup,
            a["timeout"],
        )
    else:
        report.append(
            "*Executing the code is a separate, opt-in phase (run with `execute = TRUE`). This "
            "report describes what a run would involve; it did not run anything.*"
        )

    # Reported vs. reproduced ------------------------------------------------------
    n_output_stats = h._n_stats(stat_output)
    match_summary: Mapping[str, Any] | None = None
    match_table_raw: pd.DataFrame | None = None
    matched = h.match_output(paper, stat_output)
    if matched is not None:
        match_table_raw = matched["table_raw"]
        match_summary = matched["summary"]
        report += matched["report"]

    # 10. Summary ----------------------------------------------------------------------
    run_outcomes = h._chr_col(run_results, "outcome") if run_results is not None else []
    n_ran_ok = sum(o == "ran_ok" for o in run_outcomes) if run_results is not None else None
    n_nodep_sum = sum(o == "dependency_unavailable" for o in run_outcomes)
    lines: list[str] = []
    if execute:
        lines.append(
            f"We assessed AND ran {n_code:d} R code file{plural(n_code)} for reproducibility "
            "(each in an isolated subprocess)."
        )
    else:
        lines.append(
            f"We assessed {n_code:d} R code file{plural(n_code)} for reproducibility (static "
            "analysis; no code was run)."
        )
    lines.append(
        f"{n_runnable:d} file{plural(n_runnable)} appear{'s' if n_runnable == 1 else ''} runnable "
        "so far (parses, inputs resolve, placeable in the run order)."
    )
    if n_dup > 0:
        lines.append(
            f"{n_dup:d} file{plural(n_dup)} skipped as byte-identical duplicate{plural(n_dup)} of "
            "another file already in the run (the paper links more than one "
            "repository/component)."
        )
    if execute and run_results is not None:
        nr = len(run_results)
        lines.append(f"{n_ran_ok:d} of {nr:d} script{plural(nr)} ran without error when executed.")
    if n_nodep_sum > 0:
        lines.append(
            f"{n_nodep_sum:d} script{plural(n_nodep_sum)} could not run because "
            f"{plural(n_nodep_sum, 'its', 'their')} own dependency is unavailable (not counted "
            "against the traffic light)."
        )
    if n_missing_inputs > 0:
        lines.append(
            f"{n_missing_inputs:d} referenced input{plural(n_missing_inputs)} unavailable "
            f"({n_withheld:d} withheld due to size)."
        )
    lines.append(f"{n_deps:d} installable dependenc{'y' if n_deps == 1 else 'ies'} detected.")
    if match_summary is not None:
        lines.append(
            f"{n_output_stats:d} statistic{plural(n_output_stats)} stored from the extracted "
            "output; " + h.match_summary_line(match_summary)
        )
    summary_text = h._paste_bullets(lines)

    summary_table = pd.DataFrame(
        {
            "paper_id": pd.array([pid(structure_df, code_tbl)], dtype="string"),
            "repro_code_n": pd.array([n_code], dtype="Int64"),
            "repro_runnable": pd.array([n_runnable], dtype="Int64"),
            "repro_missing_inputs": pd.array([n_missing_inputs], dtype="Int64"),
            "repro_deps": pd.array([n_deps], dtype="Int64"),
            "repro_ran_ok": pd.array([n_ran_ok], dtype="Int64"),
            "repro_tests_reported": pd.array(
                [None if match_summary is None else int(match_summary["n_tests"])], dtype="Int64"
            ),
            "repro_tests_matched": pd.array(
                [None if match_summary is None else int(match_summary["n_found"])], dtype="Int64"
            ),
        }
    )

    report += [*(spss_report or []), *(stata_report or []), *(self_repro_report or [])]
    report += [*(nonr_report or []), *(dup_report or [])]

    modifications = _modifications(rewrite_list, run_tbl, library_injections, execute)

    out: dict[str, Any] = {
        "table": table,
        "summary_table": summary_table,
        "na_replace": dict(_NA_REPLACE),
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
        "run_results": run_results,
        "install_results": install_results,
        "stat_output": stat_output or None,
        "match_table": match_table_raw,
        "modifications": modifications,
    }
    if keep_sandbox and sandbox_root is not None:
        out["sandbox"] = sandbox_root
    return out


def _execute(
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
    version_pin: Any,
    deps: pd.DataFrame,
    install_deps: pd.DataFrame,
    io: pd.DataFrame,
    order_tbl: pd.DataFrame,
    runs_as_r: list[bool],
    file_unresolved: list[bool],
    sandbox_root: str | None,
    keep_sandbox: bool,
    stat_output: list[dict[str, Any]],
) -> dict[str, Any]:
    """Section 7b: materialise the layout, install, run, re-run once, extract output."""
    from metacheck.repro.core import (
        _message,
        _repro_common_pkgs,
        _repro_find_export_pkg,
        repro_defined_vars,
        repro_install_deps,
        repro_materialize_layout,
        repro_run_order,
        repro_run_scripts,
        repro_write_scripts,
    )
    from metacheck.statout.r_capture import _r_captures_to_tables, _r_merge_captures
    from metacheck.statout.r_output import read_r_output
    from metacheck.statout.stat_output import (
        stat_output_json,
        stat_output_write,
        stat_results_long,
    )

    sandbox = a["sandbox"]
    if sandbox not in _SANDBOXES:
        # run() and the install step below treat anything but "docker" as the host process,
        # so a value that skipped _match_sandbox() must stop here, not get that backend
        raise ValueError(f"unknown sandbox {sandbox!r}: expected one of {', '.join(_SANDBOXES)}")
    timeout = a["timeout"]
    install_missing = h._is_true(a["install_missing"])

    def dbg(*parts: Any) -> None:
        _message("[repro] ", *parts)

    dbg(
        f"execute = TRUE (sandbox = {sandbox}). install_missing = "
        f"{'TRUE' if install_missing else 'FALSE'}, timeout = {timeout}s, keep_sandbox = "
        f"{'TRUE' if keep_sandbox else 'FALSE'}"
    )
    if sandbox_root is None:
        sandbox_root = _new_sandbox(keep_sandbox, cleanup)
    lib_dir = os.path.join(sandbox_root, "_lib")
    dbg("sandbox root: ", sandbox_root)

    docker_image: str | None = None
    if sandbox == "docker":
        from metacheck.repro.docker import _repro_docker_image_for

        # version_pin$r_versions %||% character(0) (a list from code_check; a
        # string or an array when read back from a saved build)
        pin = version_pin if isinstance(version_pin, Mapping) else {}
        raw_declared = pin.get("r_versions")
        if raw_declared is None:
            declared: list[Any] = []
        elif isinstance(raw_declared, str):
            declared = [raw_declared]
        else:
            declared = list(raw_declared)
        use_declared = h._is_true(a["docker_use_declared_version"])
        if not use_declared and len(declared) > 0:
            warnings.warn(
                'reproducibility_check(sandbox = "docker"): this paper declared R version '
                f"{declared[0]}, but docker_use_declared_version = FALSE (the default), so the "
                "run uses the pre-built metacheck_r image's own R version instead, for speed. "
                "Set docker_use_declared_version = TRUE to match the paper's declared version "
                "exactly -- slower, since every dependency then installs from scratch rather "
                "than using the pre-built image.",
                stacklevel=2,
            )
        docker_image = _repro_docker_image_for(declared, use_declared_version=use_declared)
        dbg("docker image: ", docker_image)

    # 1. Build the data tree the plan describes, and write the scripts into it.
    repro_materialize_layout(plan, structure_df, sandbox_root)
    run_tbl = repro_write_scripts(code_text_list, rewrite_list, plan, sandbox_root)

    # 2. Optionally install dependencies.
    install_results: pd.DataFrame | None = None
    failed_deps: list[str] = []
    n_deps = len(install_deps)
    if install_missing and n_deps > 0:
        if sandbox == "docker":
            from metacheck.repro.docker import repro_install_deps_docker

            install_results = repro_install_deps_docker(
                install_deps, lib_dir, image=docker_image or "", timeout=timeout
            )
        else:
            install_results = repro_install_deps(
                install_deps, lib_dir, cran_to_main_lib=h._is_true(a["cran_install_main"])
            )
        failed_deps = [
            str(p)
            for p, ok in zip(
                h._chr_col(install_results, "package"),
                h._col(install_results, "installed"),
                strict=True,
            )
            if not h._is_true(ok)
        ]

    # 3. Run in order.
    parses_named = pd.Series(runs_as_r, index=names, dtype=bool)
    skip_files = [n for n, u in zip(names, file_unresolved, strict=True) if u]

    def run(tbl: pd.DataFrame, order: list[str]) -> pd.DataFrame:
        lib = lib_dir if os.path.isdir(lib_dir) else None
        if sandbox == "docker":
            from metacheck.repro.docker import repro_run_scripts_docker

            return repro_run_scripts_docker(
                tbl,
                order,
                sandbox_root=sandbox_root,
                lib_dir=lib,
                image=docker_image or "",
                timeout=timeout,
                skip=skip_files,
                parses=parses_named,
                failed_deps=failed_deps,
            )
        return repro_run_scripts(
            tbl,
            order,
            lib_dir=lib,
            timeout=timeout,
            skip=skip_files,
            parses=parses_named,
            failed_deps=failed_deps,
        )

    run_results = run(run_tbl, _order_names(order_tbl))

    # 4. Corrective re-run (one extra pass) for undefined variables/functions.
    err_types = h._chr_col(run_results, "error_type")
    undef_idx = [i for i, t in enumerate(err_types) if t == "undefined_variable"]
    reran = False
    reran_for_reorder = False
    definer_lookup: dict[str, str] = {}
    library_injections: dict[str, str] = {}
    if undef_idx:
        errors = h._chr_col(run_results, "error")
        users = h._chr_col(run_results, "file_name")
        vars_ = h._chr_col(run_results, "undefined_var")
        is_fn_missing = grepl(_FN_MISSING_PAT, [errors[i] for i in undef_idx], perl=True)
        defs = repro_defined_vars(code_text_list)
        def_names = h._chr_col(defs, "file_name")
        def_sets = [h._as_list(d) for d in h._col(defs, "defines")]

        def def_of(v: str | None) -> list[str | None]:
            return [n for n, d in zip(def_names, def_sets, strict=True) if v in d]

        candidate_pkgs = list(dict.fromkeys([*h._chr_col(deps, "package"), *_repro_common_pkgs()]))
        extra_edges: list[list[str]] = []
        for k, i in enumerate(undef_idx):
            user, v = users[i], vars_[i]
            definers = list(dict.fromkeys(d for d in def_of(v) if d != user))
            if definers:
                key = f"{'NA' if user is None else user}||{'NA' if v is None else v}"
                definer_lookup[key] = ", ".join("NA" if d is None else d for d in definers)
            if len(definers) == 1:
                extra_edges.append([str(definers[0]), str(user)])
                dbg(f"undefined-var edge: '{definers[0]}' defines '{v}' needed by '{user}'")
            elif len(definers) == 0 and is_fn_missing[k] and str(user) not in library_injections:
                pkg = _repro_find_export_pkg(v, candidate_pkgs)
                if pkg is not None:
                    library_injections[str(user)] = pkg
                    dbg(
                        f"missing-library injection: '{v}' resolved to package '{pkg}' for '{user}'"
                    )
        if extra_edges or library_injections:
            order_tbl2 = repro_run_order(io, extra_edges=extra_edges) if extra_edges else order_tbl
            run_tbl2 = (
                repro_write_scripts(
                    code_text_list,
                    rewrite_list,
                    plan,
                    sandbox_root,
                    inject_libs=library_injections,
                )
                if library_injections
                else run_tbl
            )
            run_results = run(run_tbl2, _order_names(order_tbl2))
            order_tbl = order_tbl2
            run_tbl = run_tbl2
            reran = True
            reran_for_reorder = len(extra_edges) > 0
    run_results.attrs["reran_for_order"] = reran
    run_results.attrs["reran_for_reorder"] = reran_for_reorder

    # Statistical results from each script's run: captured objects + console output.
    pid = h._pid(paper, structure_df, code_tbl) if len(run_results) else None
    r_stat_output: list[dict[str, Any]] = []
    for i in range(len(run_results)):
        so = h._chr(run_results["stdout"].iloc[i])
        fn = h._chr(run_results["file_name"].iloc[i])
        exec_lines = (
            h._as_list(run_results["script_lines"].iloc[i])
            if "script_lines" in run_results.columns
            else []
        )
        caps = run_results["captures"].iloc[i] if "captures" in run_results.columns else None
        code_lines = exec_lines if exec_lines else None
        try:
            cap_tabs = _r_captures_to_tables(caps, source_label=fn, code_lines=code_lines)
        except Exception:
            cap_tabs = []
        txt_tabs: list[dict[str, Any]] = []
        if so is not None and so != "":
            try:
                txt_tabs = read_r_output(so, source_label=fn, code_lines=code_lines)
            except Exception:
                txt_tabs = []
        tabs = _r_merge_captures(cap_tabs, txt_tabs)
        if not tabs:
            continue
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
    if stat_output:
        stat_output_write(stat_output, sandbox_root)

    return {
        "sandbox_root": sandbox_root,
        "run_results": run_results,
        "install_results": install_results,
        "run_tbl": run_tbl,
        "order_tbl": order_tbl,
        "library_injections": library_injections,
        "definer_lookup": definer_lookup,
        "stat_output": stat_output,
    }


def _execution_report(
    run_results: pd.DataFrame,
    install_results: pd.DataFrame | None,
    run_tbl: pd.DataFrame | None,
    library_injections: Mapping[str, str],
    definer_lookup: Mapping[str, str],
    timeout: Any,
) -> list[Any]:
    """The report's "Execution" section."""
    oc = h._chr_col(run_results, "outcome")
    n_run = len(run_results)
    n_ran_ok = sum(o == "ran_ok" for o in oc)
    n_errored = sum(o == "errored" for o in oc)
    n_timeout = sum(o == "timed_out" for o in oc)
    n_skipped = sum(o == "skipped_missing_inputs" for o in oc)
    n_noparse = sum(o == "not_parsed" for o in oc)
    n_nodep = sum(o == "dependency_unavailable" for o in oc)
    report_exec = (
        "**The code was run.** Each script ran in an isolated subprocess against a throwaway "
        "copy of the Psych-DS layout, in the run order above, with a "
        f"{_sprintf_d(timeout)}-second per-script timeout. Of {n_run:d} script{plural(n_run)}: "
        f"{n_ran_ok:d} ran without error, {n_errored:d} errored, {n_timeout:d} timed out, "
        f"{n_skipped:d} {plural(n_skipped, 'was', 'were')} skipped (inputs unavailable), "
        f"{n_nodep:d} could not run because one of its own dependencies is unavailable, and "
        f"{n_noparse:d} did not parse. Running against current package versions: a break can "
        "reflect version drift, which argues for pinning versions."
    )
    if h._is_true(run_results.attrs.get("reran_for_order")):
        bits: list[str] = []
        if h._is_true(run_results.attrs.get("reran_for_reorder")):
            bits.append(
                "a script hit an `object '...' not found` error, indicating it expected a "
                "variable another script defines. We inferred which script supplies it and "
                "re-ordered so that script runs first"
            )
        if library_injections:
            libs = ", ".join(f"`library({p})`" for p in library_injections.values())
            bits.append(
                'a script hit a `could not find function "..."` error that resolved to exactly '
                f"one package ({libs}); we injected the corresponding `library()` call"
            )
        report_exec += (
            f" \n\n*{'; '.join(bits)}, and re-ran once. The outcomes above are from that "
            "corrected run.*"
        )

    names = h._chr_col(run_results, "file_name")
    report_setwd: list[Any] | None = None
    if run_tbl is not None and "setwd_removed" in run_tbl.columns:
        hit = [
            h._is_true(v > 0) if not h._na(v) else False for v in h._col(run_tbl, "setwd_removed")
        ]
        setwd_files = [f for f, x in zip(h._chr_col(run_tbl, "file_name"), hit, strict=True) if x]
        setwd_n = [v for v, x in zip(h._col(run_tbl, "setwd_removed"), hit, strict=True) if x]
        setwd_paths = [p for p, x in zip(h._chr_col(run_tbl, "setwd_paths"), hit, strict=True) if x]
        if setwd_files:
            ok = {n for n, o in zip(names, oc, strict=True) if o == "ran_ok"}
            ok_after = list(dict.fromkeys(f for f in setwd_files if f in ok))
            ns = len(setwd_files)
            report_setwd = [
                f"**{ns:d} script{plural(ns)} call{plural(ns, 's', '')} `setwd()`.** This is bad "
                "practice: it hardcodes where the code must run (often an absolute path on the "
                "author's own machine) and breaks the moment the code is run anywhere else. To "
                "run the code we commented these calls out. Any such script that then ran is "
                "**reproducible only after ignoring a `setwd()` that should not have been in "
                "the code and needs to be fixed**."
            ]
            if ok_after:
                report_setwd.append(
                    "Reproducible after removing `setwd()`: "
                    + ", ".join("NA" if f is None else f for f in ok_after)
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

    err_types = h._chr_col(run_results, "error_type")
    undef_vars = h._chr_col(run_results, "undefined_var")
    undef = [i for i, t in enumerate(err_types) if t == "undefined_variable"]
    report_undef: list[Any] | None = None
    if undef:
        nu = len(undef)
        defined_in = [
            definer_lookup.get(
                f"{'NA' if names[i] is None else names[i]}||"
                f"{'NA' if undef_vars[i] is None else undef_vars[i]}",
                "",
            )
            for i in undef
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

    state = {"truncated": False}
    errors = h._chr_col(run_results, "error")
    stdouts = h._col(run_results, "stdout")
    stderrs = h._col(run_results, "stderr")
    output_blocks: list[Any] = []
    for i in range(n_run):
        so = _tail_cap(stdouts[i], state)
        se = _tail_cap(stderrs[i], state)
        err = errors[i]
        if not so and not se and not h._nzchar(err):
            continue
        body: list[str] = []
        err_text = "NA" if err is None else err
        fence = _fence(so, se, [err_text])
        if so:
            body += ["**Output (stdout):**", fence, *so, fence]
        if se:
            body += ["**Messages / errors (stderr):**", fence, *se, fence]
        if not body and h._nzchar(err):
            body = [fence, err_text, fence]
        output_blocks.append(
            collapse_section(
                "\n".join(body),
                title=f"Output — {_na_str(names[i])} ({_na_str(oc[i])})",
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
    if output_blocks:
        report.append("*Expand a row below to see the output each script produced.*")
        report += output_blocks
    if truncation_note is not None:
        report.append(truncation_note)

    if install_results is not None and len(install_results) > 0:
        report += _install_report(install_results)
    return report


_CATEGORY_LABELS = {
    "cran_unavailable": "not available on CRAN for this R version",
    "compile_failure": "compilation/configure failure",
    "network": "network issue",
    "transitive_dependency_missing": "a dependency of this package is unavailable",
    "other": "uncategorised",
}


def _install_report(install_results: pd.DataFrame) -> list[str]:
    """The dependency-installation lines of the "Execution" section."""
    installed = [h._is_true(v) for v in h._col(install_results, "installed")]
    pkgs = h._chr_col(install_results, "package")
    n_ok = sum(installed)
    n_fail = len(installed) - n_ok
    out = [
        "Dependencies were installed into a throwaway library before running: "
        f"{n_ok:d} succeeded, {n_fail:d} failed. "
        + ", ".join("NA" if p is None else p for p in pkgs)
        + " were installed."
    ]
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
                "used: " + ", ".join("NA" if p is None else p for p in via_arch) + "."
            )
    failed = [i for i, ok in enumerate(installed) if not ok]
    if failed:
        msgs = h._chr_col(install_results, "message")
        cats = (
            h._chr_col(install_results, "category")
            if "category" in install_results.columns
            else [None] * len(pkgs)
        )
        parts = []
        for i in failed:
            if "category" in install_results.columns:
                c = cats[i]
                # cat_label() of an unknown category is NA, which sprintf() prints
                label = "" if c is None else _CATEGORY_LABELS.get(c, "NA")
            else:
                label = ""
            msg = msgs[i]
            parts.append(
                f"{'NA' if pkgs[i] is None else pkgs[i]}"
                + (f" [{label}]" if label != "" else "")
                + (f" ({'NA' if msg is None else msg})" if h._nzchar(msg) else "")
            )
        out.append("**Failed:** " + "; ".join(parts))
    return out


def _modifications(
    rewrite_list: h.RNamedList,
    run_tbl: pd.DataFrame | None,
    library_injections: Mapping[str, str],
    execute: bool,
) -> pd.DataFrame:
    """One row per individual change made to the scripts (``modifications``)."""
    file_name: list[str] = []
    change_type: list[str] = []
    detail: list[str] = []

    def add(fn: str, kind: str, text: str) -> None:
        file_name.append(fn)
        change_type.append(kind)
        detail.append(text)

    for fn in rewrite_list:
        d = rewrite_list[fn]  # rewrite_list[[fn]]: the first file of that name
        if d is None or not len(d):
            continue
        for ref, tgt, m, am in zip(
            h._chr_col(d, "ref"),
            h._chr_col(d, "target"),
            h._col(d, "matched"),
            h._col(d, "ambiguous"),
            strict=True,
        ):
            if h._is_true(m) and not h._is_true(am) and tgt is not None and tgt != "":
                add(fn, "path_rewrite", f'"{"NA" if ref is None else ref}" -> "{tgt}"')

    if execute and run_tbl is not None and len(run_tbl) > 0:
        rt_names = h._chr_col(run_tbl, "file_name")
        if "setwd_paths" in run_tbl.columns:
            for rt_fn, n, p in zip(
                rt_names,
                h._col(run_tbl, "setwd_removed"),
                h._chr_col(run_tbl, "setwd_paths"),
                strict=True,
            ):
                if not h._na(n) and n > 0 and h._nzchar(p):
                    for x in strsplit("NA" if p is None else p, ", ", fixed=True):
                        add(str(rt_fn), "setwd_removed", x)
        if "family_detail" in run_tbl.columns:
            for rt_fn, n, p in zip(
                rt_names,
                h._col(run_tbl, "family_replaced"),
                h._chr_col(run_tbl, "family_detail"),
                strict=True,
            ):
                if not h._na(n) and n > 0 and h._nzchar(p):
                    for x in strsplit("NA" if p is None else p, "; ", fixed=True):
                        add(str(rt_fn), "font_replaced", x)
    if execute:
        for fn, pkg in library_injections.items():
            add(fn, "library_injected", f"library({pkg})")
    return pd.DataFrame(
        {
            "file_name": pd.array(file_name, dtype="string"),
            "change_type": pd.array(change_type, dtype="string"),
            "detail": pd.array(detail, dtype="string"),
        }
    )
