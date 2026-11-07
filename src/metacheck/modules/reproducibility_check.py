"""Reproducibility Check (port of ``inst/modules/reproducibility_check.R``).

The module assesses whether a paper's R and Python code can be run on its data: a
static phase (dependencies, rewriting the paths that do not work in the repository
as deposited, run order, missing inputs) that always runs, and an opt-in execution
phase (``execute=True``) that runs the scripts in Docker containers (the default,
``sandbox="docker"``) or, only when asked for with ``sandbox="process"``, in
``Rscript``/``python3`` subprocesses on this machine with no filesystem or network
isolation. This is a deliberate difference from metacheck, whose default is
``"process"`` (D62 in docs/UPSTREAM_ISSUES.md). The building blocks are
:mod:`metacheck.repro`; the module-local pieces are in
:mod:`metacheck.modules._reproducibility` (shared), ``_reproducibility_exec`` (the
R block's execution step) and ``_reproducibility_py`` (the Python block).
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

from metacheck._r import bind_rows, grepl, plural, strsplit
from metacheck._values import as_str
from metacheck.module import module
from metacheck.modules import _reproducibility as h
from metacheck.report import scroll_table

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
    return pd.Series(pd.array([as_str(v) for v in values], dtype="string"))


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
    v = as_str(x)
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
        This module assesses whether a paper's R and Python code can be run on its
        data. It works in two phases. The **static** phase (always run) collects the
        package dependencies the code declares, rewrites the data-file paths that do not
        work in the repository as the authors organised it, works out the order the
        scripts must run in, and diagnoses why any referenced input file is unavailable
        — reporting what a reproduction attempt would involve and where it is likely to
        break. The **execution** phase (opt-in, `execute = TRUE`) then actually runs the
        code: it copies the repository into a throwaway directory, optionally installs
        the declared dependencies into a throwaway library, runs each script in order in
        its own Docker container (the default) with a per-script timeout, and captures
        the outcome and full output of each run.
    """,
    details="""
        By default (`execute = FALSE`) the module does **not run any code** — it
        performs only static analysis: reading dependencies with the same extractors
        as `code_check`, finding the files the code references in the repository,
        and building a run-order dependency graph.
        It reports what *would* be installed and run rather than doing it.

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
        `ghcr.io/scienceverse/metacheck_r_large:latest` (or `rocker/r-ver:<version>`
        with `docker_use_declared_version = TRUE`) for R code and
        `ghcr.io/scienceverse/metacheck_py_small:latest` for Python code, which Docker
        pulls under their `:latest` tag, so they can change between runs — a known
        limit. A container shares the
        host's kernel: it is a strong boundary for ordinary code, not a promise
        against a kernel exploit.

        `sandbox = "process"` is the explicit, unsafe alternative (it needs R, an
        `Rscript`, for R code and a `python3` for Python code): it runs the code **on your machine, as you, with no filesystem or
        network isolation**. A subprocess isolates a crash and nothing else, so the
        code can read, change or delete your files and use the network. Never use it
        for code you do not trust; the module warns each time it is used. Installs
        (when `install_missing = TRUE`) and the throwaway run library never touch
        your main R library, unless `cran_install_main = TRUE`. (metacheck's default
        is `"process"`; pytacheck's is `"docker"`, a deliberate difference, D62 in
        docs/UPSTREAM_ISSUES.md.) Each script's outcome is one of `ran_ok`, `errored`,
        `timed_out`, `dependency_unavailable`, `not_parsed`, `run_by_source` (an R script
        another script runs with `source()`, which is run only inside that script, since
        on its own it lacks the packages and objects the sourcing script sets up first),
        `run_by_import` (a Python file other files import, a module rather than a
        script, unless it has an `if __name__ == "__main__":` block), `jags_model` (a
        JAGS or BUGS model saved with an R extension, which another script reads) or
        `not_r_content` (a file that is not R at all, such as a JSON or HTML dump);
        these last two are not run and are not failures. `python2` marks Python 2 code
        that was not run; with `sandbox = "docker"` such code runs with Python 2.7
        (option `metacheck.docker_image_py2`; FALSE turns this off), which
        `modifications` records; it is never converted, since that can change results.
        `defines_functions` is a file that only defines functions, which is used, not
        run (`table$used_by` names the scripts that use it), and `not_analysis` is a
        file of software rather than of the analysis: a package's code, tests,
        documentation or `setup.py`, a copy of a tool, a web application, a script a
        program wrote (`table$not_analysis_why` says which); neither is run or counted
        as a script. `docker_failed` means the container never started, which says
        nothing about the script, so it keeps the light from being green but does not
        make it red. `needs_a_person` is a script that stopped because it asks a person
        to choose a file or answer a question, or that starts an app, and
        `needs_internet` one that stopped because the network is off in a Docker run,
        or, with `internet = TRUE`, could not reach what it asked for;
        `download_too_large` is a script stopped for downloading more than the limits
        allow (see `internet`), and `out_of_memory` one that, or a program it started
        (such as a compiler), ran out of the memory each script may use. All four are
        treated the same way. `not_downloaded` is a script that stopped on a file the
        repository lists but that was not downloaded, such as one over
        `max_file_size`: it could not be tried, so it is not counted as a script that
        did not reproduce (`summary_table$repro_not_downloaded_n` counts them apart).
        `skipped_time_limit` is a script not started, or stopped, because
        `total_timeout` had run out. A timeout means "still running at
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
        reproduction failure. A script that fails with `could not find function`
        is run once more with the `library()` calls the project's other scripts
        make added at its top (or the one package that exports the function, or
        magrittr for `%>%`), and a script that then runs is reported as
        reproducible only after that repair.

        Dependencies are reported name-only, with the source (CRAN, GitHub, or a base
        package already shipped with R) — static analysis of the code cannot know which
        *version* was used, and running against current package versions is itself
        informative: a break argues the authors should have pinned versions. When the
        repository does record versions, a run with `recorded_versions = TRUE` installs
        the packages the scripts load at those versions, but it does not change the R
        version, which remains a limitation of the check.

        Python code (`.py` files and Jupyter notebooks' code cells) is assessed the same
        way, in a section of its own: its imports are its dependencies (installed with
        `pip`, pinned by the repository's `requirements.txt` or `pyproject.toml` when it
        has one), each script runs whole with `python` under the same sandbox as R code,
        and the statistics a script prints are matched against the paper like R
        output.

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
        "execute": "if TRUE, actually RUN the paper's code, in a throwaway copy\n"
        "of the repository as the authors organised it: every file the run has\n"
        "(every file of a local folder; of a repository, every file that was\n"
        "downloaded, see `download`) at its own path. Only a path that does not\n"
        "work there is rewritten: an absolute path on the author's computer, a\n"
        "path from another folder, or a path to a file that is elsewhere in the\n"
        "repository, found by its name; the report notes each. This runs\n"
        "downloaded code and is off by default; it is a deliberate, opt-in action.\n"
        "By default it runs in a Docker container (see `sandbox`).",
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
        "the repository instead of the pre-built `metacheck_r_large` image.",
        "install_missing": "if TRUE (and `execute = TRUE`), install the code's\n"
        "declared dependencies into a throwaway temp library before running (CRAN\n"
        "via `install.packages`, GitHub/URL via `remotes`; Python packages via\n"
        "`pip install --target`). If FALSE, nothing is installed: a script needing\n"
        "an absent package simply errors, recorded as its outcome. If `None` (the\n"
        "default), this is FALSE, except that the R packages the scripts load are\n"
        "installed when `recorded_versions = TRUE` and the repository records\n"
        "versions for them.",
        "recorded_versions": "if TRUE (and `execute = TRUE`), and the repository\n"
        "records package versions in an `renv.lock` file, a `sessionInfo()` or\n"
        "`session_info()` record, or a date in a `groundhog.library()` or\n"
        "`checkpoint()` call, the R packages the scripts load are installed at those\n"
        "versions, which takes much longer. Only loaded packages are installed; a\n"
        "loaded package that is not listed gets its current version. The R version\n"
        "is not changed, so an old package may not install or may behave\n"
        "differently, and packages they depend on and packages that come with R are\n"
        "not at their recorded versions; a warning and the report say so. A recorded\n"
        "version that cannot be installed, or that keeps a package from loading, is\n"
        "replaced by the current one. FALSE (the default) does not use them; when a\n"
        "script then fails and the repository records versions, the report\n"
        "recommends a run with TRUE. Not used when `install_missing = FALSE`, nor\n"
        "when several papers run as one (one at a time, or `workers > 1` with\n"
        '`sandbox = "docker"`, does use them).',
        "install_timeout": "the time in seconds for installing a paper's R and\n"
        "Python packages. `None` (the default) uses `timeout`, or an hour when\n"
        "recorded versions are installed. With a clock time as `total_timeout`,\n"
        "installing takes at most half of the time that is left. With\n"
        '`sandbox = "process"` a package that is being installed is not stopped; no\n'
        "further package starts.",
        "cran_install_main": "if TRUE (and `install_missing` and `execute` are\n"
        'both TRUE, and `sandbox = "process"`), CRAN-source dependencies are\n'
        "installed into your DEFAULT R library instead of the throwaway one.",
        "timeout": "per-script timeout in seconds for the execute phase\n(default 600).",
        "total_timeout": "the time in seconds for running all scripts together,\n"
        "R and Python, counted from when the first script starts (default 3600, one\n"
        "hour; `None` or `inf` for no limit), or a clock time (a `datetime`) by\n"
        "which all scripts must be done, such as a server's job limit minus the time\n"
        "its report needs. Once it has run out, no further script is started\n"
        "(outcome `skipped_time_limit`), and a running script is stopped, so a\n"
        "package with many slow scripts still gets a report.",
        "internet": 'if TRUE (and `sandbox = "docker"`), scripts can use the\n'
        "internet while they run, so a script that downloads its data can run. What\n"
        "they download follows the limits for the repository's own files: a script\n"
        "is stopped (outcome `download_too_large`) when the paper's scripts together\n"
        "have downloaded more than `max_download_size`, or a file larger than\n"
        "`max_file_size` appears. `None` (the default) reads the option\n"
        "`metacheck.repro_internet` (FALSE unless set), which keeps the network off,\n"
        "since the code has not been checked by anyone; installing packages always\n"
        'has the internet. With `sandbox = "process"` scripts always have the\n'
        "internet, without these limits.",
        "keep_sandbox": "if TRUE, do not delete the temp materialised layout\n"
        "after the run, and return its path as element `sandbox` of the result.",
        "cache": "if `TRUE`, forwarded to the internal `data_check`/`code_check`\n"
        "runs: files they download are kept in the persistent on-disk cache.",
        "download,skip_types,peek_zips,max_file_size,max_download_size": "forwarded\n"
        "to the internal `data_check` run, with the same defaults `data_check`\n"
        "itself uses.",
        "tables_dir": "optional directory of per-paper saved module outputs\n"
        "(`capture_module_tables()`), read before running `data_check`/\n"
        "`code_check`.",
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
    install_missing: bool | None = None,
    recorded_versions: bool = False,
    install_timeout: float | None = None,
    cran_install_main: bool = False,
    timeout: float = 600,
    total_timeout: Any = 3600,
    internet: bool | None = None,
    keep_sandbox: bool = False,
    cache: Any = False,
    download: Any = "data",
    skip_types: Sequence[str] | str | None = None,
    peek_zips: bool = True,
    max_file_size: float = 100,
    max_download_size: float = 500,
    skip_on_api_limit: bool = False,
    tables_dir: str | os.PathLike[str] | None = None,
    workers: int = 1,
    results_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Port of inst/modules/reproducibility_check.R::reproducibility_check().

    Reads ``code_check``'s ``table``/``version_pin`` and ``data_check``'s
    ``structure`` from the chain, else from *tables_dir*, else runs the missing
    modules (LLM off). The plan the path rewrite uses is the repository as
    deposited, every file at its own path (``.repro_package_plan()``, metacheck
    #568). ``model=None`` means ``llm_model()``.

    With ``execute=True`` the paper's R and Python scripts are RUN. By default
    (``sandbox="docker"``; metacheck's default is ``"process"``: D62) each one
    runs in its own locked-down Docker container: in the run phase no network
    (unless ``internet=True``, and then within the download limits), a read-only
    root filesystem, only the throwaway sandbox directory (and the package library,
    read-only: D155) mounted, a non-root user, all capabilities dropped and a
    process limit. Without a running Docker the call raises ``RuntimeError``
    and nothing runs; it never falls back to the host. What the container does not
    isolate: the optional install phase (``install_missing=True``), which has
    network access by design (packages must be fetched), runs the packages' install
    scripts non-root with capabilities dropped and sees only its install script and
    the package library, never the paper's data; and the image is pulled and tagged
    ``:latest``, so it can change between runs (a known limit).

    ``sandbox="process"`` runs the scripts in ``Rscript`` (``python3``) subprocesses on this
    machine, as you, with NO filesystem or network isolation: not for code you do
    not trust. It must be asked for by name, and each run with it emits one
    :class:`~metacheck.core.errors.PytacheckWarning`. A paper list with
    ``workers > 1`` under Docker runs its papers concurrently. The static phase
    (``execute=False``, the default) needs no Docker and runs nothing.
    """
    sandbox = _match_sandbox(sandbox)
    if internet is None:
        from metacheck.utils import get_option

        internet = bool(get_option("metacheck.repro_internet", False))
    # the network is on only when asked for, and only in a container (metacheck #568, item 21)
    internet = h._is_true(internet) and sandbox == "docker"

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
            recorded_versions=recorded_versions,
            install_timeout=install_timeout,
            cran_install_main=cran_install_main,
            timeout=timeout,
            total_timeout=total_timeout,
            internet=internet,
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
        opts: dict[str, Any] = {"metacheck.skip_on_api_limit": skip_on_api_limit}
        if h._is_true(execute):
            # the data_check and code_check this check runs fetch what a run needs in their
            # first download (repo-download's .download_for_run())
            opts["metacheck.download_for_run"] = True
        with (
            local_options(opts),
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
                recorded_versions=recorded_versions,
                install_timeout=install_timeout,
                cran_install_main=cran_install_main,
                timeout=timeout,
                total_timeout=total_timeout,
                internet=internet,
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
    (install or start Docker, or ask for ``sandbox = "process"`` by name). ``"process"`` is
    checked by :func:`_require_process` once the paper's code files are known. There is no
    fallback from one to the other.
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


def _require_process(*, has_r: bool, has_py: bool) -> None:
    """Check the interpreters ``sandbox = "process"`` runs the paper's code with, then warn.

    Called once the paper's code files are known and before any of them runs: R code needs
    an ``Rscript``, Python code a ``python3``. The warning that nothing isolates the code
    is given once per run.
    """
    from metacheck.core.errors import PytacheckWarning
    from metacheck.repro import python as rpy
    from metacheck.repro.core import _rscript

    if has_r and _rscript() is None:
        raise RuntimeError(
            'execute = TRUE with sandbox = "process" needs R (an `Rscript` on PATH or '
            "PYTACHECK_RSCRIPT): it runs each script in a subprocess. "
            'Install R, or use sandbox = "docker".'
        )
    if has_py and rpy._python() is None:
        raise RuntimeError(
            'execute = TRUE with sandbox = "process" needs Python (a `python3` on PATH) to '
            'run the paper\'s Python scripts in a subprocess. Install it, or use sandbox = "docker".'
        )
    warnings.warn(_PROCESS_WARNING, PytacheckWarning, stacklevel=4)


def _new_sandbox(keep_sandbox: bool, cleanup: list[str]) -> str:
    """``tempfile("repro_sandbox_")``, removed on exit unless *keep_sandbox*."""
    root = tempfile.mkdtemp(prefix="repro_sandbox_")
    if not h._is_true(keep_sandbox):
        cleanup.append(root)
    return root


def _check(paper: Any, cleanup: list[str], **a: Any) -> dict[str, Any]:
    """The single-paper check (everything after the argument checks)."""
    from metacheck.module import get_prev_outputs
    from metacheck.repro.runsupport import _repro_download_limits

    execute = h._is_true(a["execute"])
    keep_sandbox = h._is_true(a["keep_sandbox"])
    # the limits on what scripts download, shared by all of the paper's scripts; None
    # keeps the network off (#568, item 21)
    a["net"] = _repro_download_limits(a["internet"], a["max_file_size"], a["max_download_size"])
    # set when the first script starts, R or Python (see total_timeout)
    a["run_state"] = {"script_deadline": None}

    def pid(*dfs: Any) -> str | None:
        return h._pid(paper, *dfs)

    saved = h.SavedTables(a["tables_dir"], pid)

    def upstream(mod: str, item: str) -> Any:
        x = get_prev_outputs(mod, item)
        return x if x is not None else saved.get(mod, item)

    # 1. Inputs from the upstream modules --------------------------------------
    # code_check gives the code files; data_check the files of the repository, from
    # which the plan the path rewrite needs is built: every file at its own path (the
    # code runs in the repository as the authors organised it; only paths that do not
    # work there are rewritten, metacheck #568).
    from metacheck.repro.layout import (
        _in_renv_folder,
        _repro_fetch_files,
        _repro_fetchable,
        _repro_named_files,
        _repro_package_plan,
    )

    code_tbl = upstream("code_check", "table")
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
    plan = _repro_package_plan(structure_df)
    if code_tbl is None:
        cc = run_missing("code_check")
        code_tbl = cc.table
        version_pin = cc.get("version_pin")
    # Before a run, the files the code names that the repository lists but the first
    # download left out (an image, a sound file) are fetched, in one call (#568)
    if (
        execute
        and _download_on(a["download"])
        and code_tbl is not None
        and len(code_tbl) > 0
        and any(_repro_fetchable(structure_df))
    ):
        code_txt: list[str] = []
        for loc in h._chr_col(code_tbl, "file_location"):
            if h._file_exists(loc):
                code_txt.extend(_read_text(loc)[0])
        named = _repro_named_files(structure_df, code_txt)
        if any(named):
            from metacheck.repro.core import _message

            _message(
                f"[repro] fetching {sum(named)} file(s) the code names that were not downloaded"
            )
            structure_df = _repro_fetch_files(
                structure_df,
                named,
                a["max_file_size"],
                a["max_download_size"],
                a["cache"],
                a["skip_on_api_limit"],
            )
            plan = _repro_package_plan(structure_df)

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

    # R and Python are handled in this phase.
    no_code = "We found no R or Python code files to assess for reproducibility."
    if code_tbl is None or len(code_tbl) == 0 or "language" not in code_tbl.columns:
        return empty(no_code + note, empty_tl, extra)
    langs = h._chr_col(code_tbl, "language")
    if "file_name" in code_tbl.columns:
        # renv's own files (renv/activate.R, renv/library/) are not the paper's analysis
        # code, so they are not run (metacheck #548: the old pattern never matched)
        in_renv = _in_renv_folder(code_tbl)
        keep = [i for i, lang in enumerate(langs) if lang == "R" and not in_renv[i]]
        keep_py = [i for i, lang in enumerate(langs) if lang == "Python" and not in_renv[i]]
    else:
        # r_files[!grepl(pattern, NULL), ] is r_files[logical(0), ]: no rows
        keep = keep_py = []
    r_files = code_tbl.iloc[keep].reset_index(drop=True)
    py_files = code_tbl.iloc[keep_py].reset_index(drop=True)
    has_r, has_py = len(r_files) > 0, len(py_files) > 0
    if not has_r and not has_py:
        return empty(no_code + note, empty_tl, extra)
    if execute and a["sandbox"] == "process":
        _require_process(has_r=has_r, has_py=has_py)

    # The R block, then the Python block; the comparison with the paper, the
    # SPSS/Stata, self-reproducible-output and other-language sections and the
    # SPSS/Stata red light come after both, so they see the output of both languages.
    r = (
        _assess_r(
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
            execute=execute,
            keep_sandbox=keep_sandbox,
        )
        if has_r
        else _no_block(pid(structure_df, code_tbl), stat_output, sandbox_root)
    )
    stat_output, sandbox_root = r["stat_output"], r["sandbox_root"]
    # a file fetched after the R run is in the Python block's structure and plan too
    structure_df, plan = r.get("structure_df", structure_df), r.get("plan", plan)
    if has_py:
        from metacheck.modules._reproducibility_py import assess_py

        py = assess_py(
            paper,
            cleanup,
            a,
            py_files=py_files,
            code_tbl=code_tbl,
            plan=plan,
            structure_df=structure_df,
            version_pin=version_pin,
            stat_output=stat_output,
            sandbox_root=sandbox_root,
            execute=execute,
            keep_sandbox=keep_sandbox,
            # the R files' names (after duplicates and name clashes), which the Python
            # files' own names must differ from
            r_names=[str(x) for x in h._chr_col(r["table"], "file_name")] if has_r else [],
        )
    else:
        py = _no_block(pid(structure_df, code_tbl), stat_output, sandbox_root)
    stat_output, sandbox_root = py["stat_output"], py["sandbox_root"]
    return _finish(
        paper,
        a,
        r=r,
        py=py,
        has_py=has_py,
        pid=pid(structure_df, code_tbl),
        stat_output=stat_output,
        sandbox_root=sandbox_root,
        spss_red=spss_red,
        stata_red=stata_red,
        shared_reports=[spss_report, stata_report, self_repro_report, nonr_report],
        execute=execute,
        keep_sandbox=keep_sandbox,
    )


def _download_on(download: Any) -> bool:
    """``!identical(download, "none") && !isFALSE(download)``."""
    return not (download is False or (isinstance(download, str) and download == "none"))


def _read_text(path: str | None) -> tuple[list[str], str | None]:
    """``code_read(path)`` and, for a local file that is not text, what it is.

    A file that is not text (R data saved as ``.R``) gives no lines, as metacheck's
    ``code_read()`` does (#568), instead of being decoded and scanned as code.
    """
    if path is None:
        return [], None
    if h._file_exists(path):
        from metacheck.repro.core import _repro_binary_kind

        kind = _repro_binary_kind(path)
        if kind is not None:
            return [], kind
    txt = _read_code(path)
    return (txt or []), None


#: The count columns both language blocks give and the result adds up.
_COUNTS = ("repro_code_n", "repro_runnable", "repro_missing_inputs", "repro_deps", "repro_ran_ok")


def _block_summary(pid: str | None, counts: Mapping[str, int | None]) -> pd.DataFrame:
    """A language block's one-row summary (the tests columns are filled in after both)."""
    return pd.DataFrame(
        {
            "paper_id": pd.array([pid], dtype="string"),
            **{k: pd.array([counts.get(k)], dtype="Int64") for k in _COUNTS},
            "repro_tests_reported": pd.array([None], dtype="Int64"),
            "repro_tests_matched": pd.array([None], dtype="Int64"),
        }
    )


def _no_block(
    pid: str | None, stat_output: list[dict[str, Any]], sandbox_root: str | None
) -> dict[str, Any]:
    """The placeholder of a language the paper has no files in (R ``if (!has_r)``)."""
    return {
        "table": pd.DataFrame(),
        "summary_table": _block_summary(
            pid,
            {
                "repro_code_n": 0,
                "repro_runnable": 0,
                "repro_missing_inputs": 0,
                "repro_deps": 0,
                "repro_ran_ok": None,
            },
        ),
        "tl": "na",
        "report": None,
        "summary_text": None,
        "run_results": None,
        "install_results": None,
        "modifications": _empty_modifications(),
        "missing_inputs": None,
        "n_dup": 0,
        "stat_output": stat_output,
        "sandbox_root": sandbox_root,
        "recorded": None,
        "recorded_advice": None,
        "install_r": False,
    }


def _empty_modifications() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "file_name": pd.array([], dtype="string"),
            "change_type": pd.array([], dtype="string"),
            "detail": pd.array([], dtype="string"),
        }
    )


_TL_RANK = {"red": 4, "error": 4, "yellow": 2, "info": 2, "green": 1, "na": 0}


def _combine_tl(a: str, b: str) -> str:
    """The more severe of two traffic lights (red/error > yellow/info > green > na)."""
    return a if _TL_RANK.get(a, 0) >= _TL_RANK.get(b, 0) else b


def _bind(a: pd.DataFrame | None, b: pd.DataFrame | None) -> pd.DataFrame | None:
    """``dplyr::bind_rows(a, b)``, ``None`` when neither side has a frame."""
    frames = [x for x in (a, b) if x is not None and not (len(x) == 0 and len(x.columns) == 0)]
    if not frames:
        return None if a is None and b is None else pd.DataFrame()
    if len(frames) == 1:
        return frames[0]
    return bind_rows(frames)


def _na_sum(a: Any, b: Any) -> int | None:
    """Sum two counts, ``NA`` as 0 unless both are ``NA``."""
    if h._na(a) and h._na(b):
        return None
    return (0 if h._na(a) else int(a)) + (0 if h._na(b) else int(b))


def _finish(
    paper: Any,
    a: Mapping[str, Any],
    *,
    r: Mapping[str, Any],
    py: Mapping[str, Any],
    has_py: bool,
    pid: str | None,
    stat_output: list[dict[str, Any]],
    sandbox_root: str | None,
    spss_red: bool,
    stata_red: bool,
    shared_reports: Sequence[list[Any] | None],
    execute: bool,
    keep_sandbox: bool,
) -> dict[str, Any]:
    """Combine the R and Python blocks, with the sections shared across languages."""
    from metacheck.repro.outcomes import (
        _repro_by_repository,
        _repro_run_counts,
        _repro_source_unknown,
    )

    table = _bind(r["table"], py["table"])
    if table is None:
        table = pd.DataFrame()
    # The comparison with the paper runs once, after both language blocks: stat_output
    # holds the self-reproducible output plus what the R and Python runs added.
    shared_match = h.match_output(paper, stat_output)
    match_table_raw = shared_match["table_raw"] if shared_match is not None else None
    ms = shared_match["summary"] if shared_match is not None else None
    n_output_stats = h._n_stats(stat_output)
    shared_report: list[Any] = [
        *(_repro_by_repository(table) or []),
        *(shared_match["report"] if shared_match is not None else []),
    ]
    for part in shared_reports:
        shared_report += part or []
    shared_summary_text = (
        "\n- "
        + f"{n_output_stats:d} statistic{plural(n_output_stats)} stored from the extracted "
        + "output; "
        + h.match_summary_line(ms)
        if ms is not None
        else None
    )

    rs, ps = r["summary_table"], py["summary_table"]
    r_pid = h._chr_col(rs, "paper_id")
    first_pid = r_pid[0] if r_pid and r_pid[0] is not None else pid
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.array([first_pid], dtype="string"),
            **{
                k: pd.array([_na_sum(h._col(rs, k)[0], h._col(ps, k)[0])], dtype="Int64")
                for k in _COUNTS
            },
            "repro_tests_reported": pd.array(
                [None if ms is None else int(ms["n_tests"])], dtype="Int64"
            ),
            "repro_tests_matched": pd.array(
                [None if ms is None else int(ms["n_found"])], dtype="Int64"
            ),
        }
    )

    tl = "na" if r["tl"] == "na" and py["tl"] == "na" else _combine_tl(r["tl"], py["tl"])
    # SPSS/Stata data with no syntax means part of the analysis is unreproducible:
    # red, whatever languages the paper's code is in
    if spss_red or stata_red:
        tl = "red"

    report: list[Any] = [
        *(r["report"] or []),
        *(["### Python code", *(py["report"] or [])] if has_py else []),
        *(["### All code and output"] if has_py and shared_report else []),
        *shared_report,
    ]
    summary_text = "".join(
        x for x in (r["summary_text"], py["summary_text"], shared_summary_text) if x
    )
    run_results = _bind(r["run_results"], py["run_results"])
    if run_results is None or len(run_results) == 0:
        run_results = r["run_results"] if r["run_results"] is not None else py["run_results"]
    install_results = _bind(r["install_results"], py["install_results"])
    if install_results is None or len(install_results) == 0:
        install_results = (
            r["install_results"] if r["install_results"] is not None else py["install_results"]
        )
    modifications = _bind(r["modifications"], py["modifications"])
    # the scripts, the software not run, and why scripts did not run through (#568, item 24)
    run_counts = _repro_run_counts(run_results)
    summary_table = pd.concat([summary_table, run_counts.reset_index(drop=True)], axis=1)

    outcomes = h._chr_col(run_results, "outcome") if run_results is not None else []
    out: dict[str, Any] = {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {**_NA_REPLACE, **dict.fromkeys(run_counts.columns, 0.0)},
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
        # Kept in the brief report whatever the light when code was run (#504): a green
        # light there means "we ran your code and it worked".
        "brief": execute
        and run_results is not None
        and "outcome" in run_results.columns
        and any(o in ("ran_ok", "errored", "timed_out") for o in outcomes),
        "run_results": run_results,
        "install_results": install_results,
        "recorded_versions": r["recorded"],
        "stat_output": stat_output or None,
        "match_table": match_table_raw,
        "modifications": modifications,
        # The checklist report's own section on this check (None when no code ran).
        # Returned as a module-specific output until ModuleOutput has the field.
        "checklist_section": _checklist(
            a,
            table=table,
            run_results=run_results,
            install_results=install_results,
            missing_inputs=_bind(r["missing_inputs"], py["missing_inputs"]),
            n_dup=int(r["n_dup"]) + int(py["n_dup"]),
            ms=ms,
            r=r,
            source_unknown=_repro_source_unknown(install_results),
        )
        if execute
        else None,
    }
    if keep_sandbox and sandbox_root is not None:
        out["sandbox"] = sandbox_root
    return out


def _checklist(
    a: Mapping[str, Any],
    *,
    table: pd.DataFrame,
    run_results: pd.DataFrame | None,
    install_results: pd.DataFrame | None,
    missing_inputs: pd.DataFrame | None,
    n_dup: int,
    ms: Mapping[str, Any] | None,
    r: Mapping[str, Any],
    source_unknown: Sequence[str],
) -> str | None:
    """``.repro_checklist_section()`` with the module's arguments."""
    from metacheck.repro.recorded import _repro_recorded_summary
    from metacheck.repro.section import _repro_checklist_section

    # R pastes both after the introduction; at most one is there (versions were used or
    # they were not)
    note = [
        str(x)
        for x in (
            _repro_recorded_summary(r["recorded"], install_results),
            r["recorded_advice"],
        )
        if x is not None
    ]
    return _repro_checklist_section(
        table,
        run_results,
        missing_inputs=missing_inputs,
        n_dup=n_dup,
        n_tests=None if ms is None else int(ms["n_tests"]),
        n_found=None if ms is None else int(ms["n_found"]),
        sandbox=a["sandbox"],
        timeout=a["timeout"],
        install_missing=bool(r["install_r"]),
        recorded_note=" ".join(note) or None,
        net=a["net"],
        source_unknown=source_unknown,
        install_results=install_results,
        versions_recorded=r["recorded_advice"] is not None,
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
    execute: bool,
    keep_sandbox: bool,
) -> dict[str, Any]:
    """The R block of the R module (``if (has_r) { ... }``): the ``r_*`` results."""
    from metacheck.codecheck.core import code_extract_py, code_extract_r, code_packages
    from metacheck.repro.core import (
        _repro_base_packages,
        _repro_built_names,
        _repro_content_sniff,
        _repro_is_jags_model,
        repro_dependencies,
        repro_file_io,
        repro_missing_inputs,
        repro_rewrite_paths,
        repro_run_order,
    )
    from metacheck.repro.layout import (
        _repro_apply_folder_vars,
        _repro_fill_file_choice,
        _repro_folder_vars,
        _repro_here_roots,
        _repro_here_start,
        _repro_inputs_after_run,
        _repro_inputs_to_report,
        _repro_keep_working_refs,
        _repro_listed_folders,
        _repro_name_corrections,
        _repro_name_report,
        _repro_names_corrected,
        _repro_order_path,
        _repro_path_fix_text,
        _repro_ref_checkable,
        _repro_run_folders,
        _repro_script_path,
        _repro_script_places,
        _repro_shown_location,
        _repro_static_light,
        _repro_unique_script_names,
    )
    from metacheck.repro.outcomes import _repro_absent_report, _repro_starts_app
    from metacheck.repro.recorded import (
        _repro_recorded_advice,
        _repro_recorded_preview,
        _repro_recorded_summary,
    )
    from metacheck.repro.runsupport import (
        _repro_function_names,
        _repro_function_users,
        _repro_not_run_report,
    )
    from metacheck.repro.software import (
        _repro_is_r_test,
        _repro_paper_text,
        _repro_software_kind,
        _repro_sw_chain,
        _repro_sw_link_text,
    )

    sandbox = a["sandbox"]

    def pid(*dfs: Any) -> str | None:
        return h._pid(paper, *dfs)

    resolve = _resolver(r_files, structure_df)

    # 2. Byte-identical duplicates across repo mirrors -----------------------------
    # R reads each file once to hash it and again for raw_text_list; a row's path does
    # not depend on the other rows, so the first read is kept and reused.
    reads = [_read_text(resolve(i)) for i in range(len(r_files))]
    hashes: list[str | None] = [None if not txt else "\n".join(txt) for txt, _ in reads]
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
        dup_idx = [i for i, d in enumerate(dup) if d]
        kept_idx = [hashes.index(hashes[i]) for i in dup_idx]
        # each copy by its link, else its path in the repository; never its place on the
        # computer that runs the check
        dup_loc = _repro_shown_location(r_files, dup_idx)
        kept_loc = _repro_shown_location(r_files, kept_idx)
        descs = []
        for k, i in enumerate(dup_idx):
            at = f" at `{dup_loc[k]}`" if dup_loc[k] else ""
            kept_at = f" at `{kept_loc[k]}`" if kept_loc[k] else ""
            descs.append(
                f"`{_na_str(fnames[i])}`{at} (same as `{_na_str(fnames[kept_idx[k]])}`{kept_at})"
            )
        dup_report = [
            "#### Duplicate files across repo mirrors",
            f"{n_dup:d} file{plural(n_dup)} {plural(n_dup, 'is', 'are')} byte-identical to "
            "another file already in the run (the paper links more than one "
            "repository/component that mirrors the same materials). "
            f"{plural(n_dup, 'It was', 'They were')} only run once, from its first occurrence, "
            "to avoid wasted duplicate execution: " + "; ".join(descs) + ".",
        ]
        keep_rows = [i for i, d in enumerate(dup) if not d]
        r_files = r_files.iloc[keep_rows].reset_index(drop=True)
        reads = [reads[i] for i in keep_rows]
    # Scripts that still share a name differ: each gets a name of its own, which every
    # step below knows it by (#568).
    r_files = r_files.copy()
    r_files["file_name"] = pd.array(_repro_unique_script_names(r_files), dtype="string")
    resolve = _resolver(r_files, structure_df)
    n_code = len(r_files)
    names = [str(f) for f in h._chr_col(r_files, "file_name")]

    # Read each file (raw, and the code of R Markdown/Quarto and R-kernel notebooks) ---
    raw_texts: list[list[str]] = [txt for txt, _ in reads]
    is_binary = [kind is not None for _, kind in reads]
    code_texts: list[list[str]] = []
    is_rmd = grepl(r"\.(rmd|qmd)$", names, ignore_case=True)
    is_ipynb = grepl(r"\.ipynb$", names, ignore_case=True)
    for i in range(n_code):
        path = resolve(i)
        if path is None:
            code_texts.append([])
        elif is_rmd[i] or is_ipynb[i]:
            # an R-kernel notebook is JSON: its code cells are extracted, as code_check
            # does, or the JSON itself would be run as R
            try:
                ext = code_extract_r(path) if is_rmd[i] else code_extract_py(path)
                code_texts.append([] if ext is None else list(ext))
            except Exception:
                code_texts.append([])
        else:
            code_texts.append(raw_texts[i])
    raw_text_list = h.RNamedList(names, raw_texts)

    # Fill in the file where a script asks a person to choose one and the code says
    # which (see .repro_fill_file_choice()).
    sd_names = h._chr_col(structure_df, "file_name") if structure_df is not None else []
    data_pat = r"\.(csv|tsv|txt|dat|tab|xls|xlsx|ods|rds|rda|rdata|sav|dta)$"
    deposit_data = list(
        dict.fromkeys(
            str(h._basename(n.replace("\\", "/")))
            for n, hit in zip(sd_names, grepl(data_pat, sd_names, ignore_case=True), strict=True)
            if hit and n is not None
        )
    )
    choices = [_repro_fill_file_choice(txt, deposit_data) for txt in code_texts]
    code_texts = [list(c["txt"]) for c in choices]
    r_choices_filled = {names[i]: list(c["filled"]) for i, c in enumerate(choices)}
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
    io["file_path"] = pd.array(_repro_order_path(r_files), dtype="string")
    r_script_paths = _repro_script_path(r_files)
    rewrites = [
        repro_rewrite_paths(
            code_texts[i],
            names[i],
            plan,
            "R",
            structure_df=structure_df,
            script_path=r_script_paths[i],
        )
        for i in range(n_code)
    ]
    order_tbl = repro_run_order(io)
    r_script_places = _repro_script_places(r_files)
    # the folder each script runs from
    r_run_dirs = _repro_run_folders(r_script_places, code_text_list, plan, order_tbl, "R")
    # where here() starts for each script: a path given to here() counts from there
    # (a named vector in R: by position here)
    here_roots = list(_repro_here_roots(code_text_list, r_run_dirs, plan).values())
    r_here_dirs = [_repro_here_start(r_run_dirs[i], here_roots[i], plan) for i in range(n_code)]
    # a reference that works as written stays as it is
    rewrites = [
        _repro_keep_working_refs(
            rewrites[i], plan, r_script_paths[i], r_run_dirs[i], here_dir=r_here_dirs[i]
        )
        for i in range(n_code)
    ]
    # a folder on the authors' computer held in a variable gets the deposit's folder
    rewrites = [
        _repro_apply_folder_vars(
            rewrites[i],
            _repro_folder_vars(code_texts[i], plan, r_run_dirs[i], rw=rewrites[i]),
        )
        for i in range(n_code)
    ]
    # a folder the script lists, where the deposit holds it (#578, item 2)
    for i in range(n_code):
        d = rewrites[i]
        lf = _repro_listed_folders(code_texts[i], plan, r_run_dirs[i], "R")
        if lf is not None and len(lf):
            have_refs = set(h._chr_col(d, "ref")) if len(d) else set()
            extra = lf[[ref not in have_refs for ref in h._chr_col(lf, "ref")]]
            if len(extra):
                rewrites[i] = bind_rows([d, extra]) if len(d.columns) else extra
    rewrite_list = h.RNamedList(names, rewrites)

    def lgl(d: pd.DataFrame, col: str) -> list[bool]:
        return [h._is_true(v) for v in h._col(d, col)] if col in d.columns else [False] * len(d)

    rewrites_n = [
        sum(
            m and not am and not aw
            for m, am, aw in zip(
                lgl(d, "matched"), lgl(d, "ambiguous"), lgl(d, "as_written"), strict=True
            )
        )
        if len(d)
        else 0
        for d in rewrites
    ]
    ambiguous_n = [sum(lgl(d, "ambiguous")) if len(d) else 0 for d in rewrites]
    built_names = [_repro_built_names(code_texts[i], "R") for i in range(n_code)]
    checkable = [
        _repro_ref_checkable(d, built_names[i]) if len(d) else [] for i, d in enumerate(rewrites)
    ]
    unresolved_refs: list[str | None] = [
        b
        for i, d in enumerate(rewrites)
        if len(d)
        for b, m, ok in zip(h._chr_col(d, "basename"), lgl(d, "matched"), checkable[i], strict=True)
        if not m and ok
    ]
    # Paths assembled at runtime (sprintf()/paste()-family)
    n_call_paths = 0
    n_call_unresolved = 0
    for d in rewrites:
        if len(d) and "is_call" in d.columns:
            for c, m in zip(lgl(d, "is_call"), lgl(d, "matched"), strict=True):
                if c:
                    n_call_paths += 1
                    n_call_unresolved += not m

    # 5. Run order ----------------------------------------------------------------
    cycle = [str(x) for x in h._as_list(order_tbl.attrs.get("cycle"))]
    ambiguous_order = h._is_true(order_tbl.attrs.get("ambiguous"))
    fuzzy_sources = order_tbl.attrs.get("fuzzy_sources")
    if not isinstance(fuzzy_sources, pd.DataFrame):
        fuzzy_sources = pd.DataFrame({"from": [], "to": []})

    # 6. Missing-input diagnosis ----------------------------------------------------
    io_writes = h._col(io, "writes")
    produced = list(
        dict.fromkeys(
            None if as_str(w) is None else str(as_str(w)).lower()
            for ws in io_writes
            for w in h._as_list(ws)
        )
    )
    unres_lower = list(dict.fromkeys(None if x is None else x.lower() for x in unresolved_refs))
    candidate_missing = [x for x in unres_lower if x not in produced]
    missing_inputs = repro_missing_inputs(candidate_missing, plan, structure_df)
    n_missing_inputs = len(missing_inputs)
    n_withheld = sum(s == "withheld_size" for s in h._chr_col(missing_inputs, "status"))

    parse_error_raw = (
        [h._is_true(v) for v in h._col(r_files, "parse_error")]
        if "parse_error" in r_files.columns
        else [False] * n_code
    )

    # Files that failed to parse, and files that are not text (R data saved as .R, #568):
    # JSON/HTML dumps, binary data, JAGS/BUGS models.
    sniff_type: list[str | None] = [None] * n_code
    sniff_detail: list[str | None] = [None] * n_code
    for i in [i for i in range(n_code) if parse_error_raw[i] or is_binary[i]]:
        others = [raw_texts[j] for j in range(n_code) if j != i]
        if _repro_is_jags_model(raw_texts[i], names[i], other_code_text=others):
            sniff_type[i] = "jags_model"
            sniff_detail[i] = "JAGS/BUGS model definition (not standalone R)"
            continue
        path_i = resolve(i)
        detected = reads[i][1] if path_i is not None and h._file_exists(path_i) else None
        if detected is None:
            detected = _repro_content_sniff(raw_texts[i])
        if detected is not None:
            sniff_type[i] = "not_r_content"
            sniff_detail[i] = f"file content does not appear to be R (detected: {detected})"
    n_not_r_content = sum(s == "not_r_content" for s in sniff_type)
    n_jags_model = sum(s == "jags_model" for s in sniff_type)
    # A package's tests are not the analysis, and a file that only defines functions is
    # used, not run (#568). Nor are the files of software: a package's code, a tool, a web
    # application, and examples and vignettes nothing links to the paper's results.
    r_function_names = [_repro_function_names(txt) for txt in code_texts]
    repo_names = h._chr_col(r_files, "repo_name") if "repo_name" in r_files.columns else None
    r_software = _repro_software_kind(
        r_script_paths,
        code_text_list,
        structure_df,
        repo_names,
        paper_text=_repro_paper_text(paper),
        raw_text_list=raw_text_list,
    )
    sw_kind = h._chr_col(r_software, "kind")
    sw_why = h._chr_col(r_software, "why")
    sw_links = h._chr_col(r_software, "links")
    sw_kind = [
        None if k == "unlinked" and len(r_function_names[i]) > 0 else k
        for i, k in enumerate(sw_kind)
    ]
    # a script the analysis depends on (it writes what a running script reads, or a
    # running script sources it) is linked through that script
    r_chain = _repro_sw_chain(sw_kind, names, io, repos=repo_names, links=sw_links)
    for i, linked in enumerate(r_chain):
        if linked is not None:
            sw_kind[i] = None
            sw_links[i] = linked
    is_test = _repro_is_r_test(list(r_script_paths))
    for i in range(n_code):
        if sniff_type[i] is None and (is_test[i] or sw_kind[i] is not None):
            sniff_type[i] = "not_analysis"
    sw_why = [w if sniff_type[i] == "not_analysis" else None for i, w in enumerate(sw_why)]
    is_r_ext = grepl(r"\.[Rr]$", names)
    only_functions = [
        sniff_type[i] is None and is_r_ext[i] and len(r_function_names[i]) > 0
        for i in range(n_code)
    ]
    for i in range(n_code):
        if only_functions[i]:
            sniff_type[i] = "defines_functions"
    # A script that starts an app waits for a person until the time limit.
    for i in range(n_code):
        if sniff_type[i] is None and _repro_starts_app(code_texts[i]):
            sniff_type[i] = "needs_a_person"
    io_sources = {
        str(k): v for k, v in zip(h._chr_col(io, "file_name"), h._col(io, "sources"), strict=True)
    }
    r_used_by: list[str | None] = []
    for i in range(n_code):
        if not only_functions[i]:
            r_used_by.append(None)
            continue
        users = _repro_function_users(names[i], r_function_names[i], code_text_list, io_sources)
        r_used_by.append(", ".join(users) if users else None)
    # Such a file has no place in the run order, so one script and a model file are not
    # two scripts whose order is unknown.
    if sum(s is None for s in sniff_type) <= 1:
        ambiguous_order = False

    parse_error_genuine = [
        p and s is None for p, s in zip(parse_error_raw, sniff_type, strict=True)
    ]
    parse_errs = sum(parse_error_genuine)
    parses = [not p for p in parse_error_genuine]
    runs_as_r = [not p and not b for p, b in zip(parse_error_raw, is_binary, strict=True)]
    ot_names = h._chr_col(order_tbl, "file_name")
    file_order = _pick(h._col(order_tbl, "order"), _match_first(names, ot_names))
    file_order = [None if h._na(o) else int(o) for o in file_order]
    placeable = [o is not None for o in file_order]

    # Inputs produced earlier: a file a script writes itself is not one it is missing
    # either (U235).
    io_names = h._chr_col(io, "file_name")
    first_writes: dict[str | None, list[Any]] = {}
    for n, ws in zip(io_names, io_writes, strict=True):
        first_writes.setdefault(n, h._as_list(ws))

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
        vals = [low(w) for w in first_writes.get(names[i], [])]
        vals += [low(w) for e in earlier for w in first_writes.get(e, [])]
        produced_before.append(list(dict.fromkeys(vals)))

    unresolved_list: list[list[str | None]] = []
    for i, d in enumerate(rewrites):
        if not len(d):
            unresolved_list.append([])
            continue
        bases = [None if b is None else b.lower() for b in h._chr_col(d, "basename")]
        unresolved_list.append(
            list(
                dict.fromkeys(
                    b
                    for b, m, ok in zip(bases, lgl(d, "matched"), checkable[i], strict=True)
                    if not m and ok and b not in produced_before[i]
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
    # When the code is run, the light is worked out again from what still counts as
    # missing, and an execution error overrides it to red.
    tl_clean = parse_errs == 0 and len(cycle) == 0 and not ambiguous_order and sum(ambiguous_n) == 0
    tl = _repro_static_light(missing_inputs, tl_clean)

    # 7b. Execution (opt-in; runs downloaded code) ------------------------------------
    ex: dict[str, Any] = {}
    run_results: pd.DataFrame | None = None
    install_results: pd.DataFrame | None = None
    run_tbl: pd.DataFrame | None = None
    recorded: Any = None
    install_r = h._is_true(a["install_missing"])
    # what the execution step reads, by the names of the R module's locals
    ctx: dict[str, Any] = {
        "file_names": names,
        "r_files": r_files,
        "code_text_list": code_text_list,
        "raw_text_list": raw_text_list,
        "rewrite_list": rewrite_list,
        "plan": plan,
        "structure_df": structure_df,
        "code_tbl": code_tbl,
        "version_pin": version_pin,
        "deps": deps,
        "install_deps": install_deps,
        "io": io,
        "order_tbl": order_tbl,
        "runs_as_r": runs_as_r,
        "sniff_type": sniff_type,
        "r_script_paths": r_script_paths,
        "r_script_places": r_script_places,
        "r_run_dirs": r_run_dirs,
        "here_roots": here_roots,
        "missing_inputs": missing_inputs,
        "unresolved_list": unresolved_list,
        "sandbox_root": sandbox_root,
        "stat_output": stat_output,
        "r_choices_filled": r_choices_filled,
        "pid": pid(structure_df, code_tbl),
        "keep_sandbox": keep_sandbox,
    }
    if execute:
        from metacheck.modules._reproducibility_exec import _execute

        ex = _execute(paper, cleanup, a, ctx)
        sandbox_root = ex["sandbox_root"]
        run_results = ex["run_results"]
        install_results = ex["install_results"]
        run_tbl = ex["run_tbl"]
        order_tbl = ex["order_tbl"]
        stat_output = ex["stat_output"]
        structure_df = ex["structure_df"]
        plan = ex["plan"]
        recorded = ex["recorded"]
        install_r = ex["install_r"]
        # A predicted missing file no longer counts for a script that ran through.
        tl = _repro_static_light(
            _repro_inputs_after_run(missing_inputs, unresolved_list, names, run_results), tl_clean
        )
        outcomes = h._chr_col(run_results, "outcome")
        not_failures = (
            "skipped_time_limit",
            "docker_failed",
            "needs_a_person",
            "needs_internet",
            "download_too_large",
            "not_downloaded",
            "out_of_memory",
        )
        if any(o in ("errored", "timed_out") for o in outcomes):
            tl = "red"
        elif any(o in not_failures for o in outcomes) and tl == "green":
            tl = "yellow"

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
        reason_of = (
            _pick(h._chr_col(run_results, "reason"), m_run)
            if "reason" in run_results.columns
            else [None] * n_code
        )
    else:
        outcome_of = errtype_of = undefvar_of = reason_of = [None] * n_code
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
    name_corrections = _repro_name_corrections(rewrite_list)
    table = pd.DataFrame(
        {
            "paper_id": _str_col(
                h._col(r_files, "paper_id") if "paper_id" in r_files.columns else [pid()] * n_code
            ),
            "file_name": _str_col(names),
            "file_path": _str_col(list(r_script_paths)),
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
            "names_corrected": _str_col(_repro_names_corrected(name_corrections, names)),
            "used_by": _str_col(r_used_by),
            "not_analysis_why": _str_col(sw_why),
            # for a file of software that runs: what links it to the paper's results
            "paper_link": _str_col(
                [
                    t if k is None else None
                    for k, t in zip(sw_kind, _repro_sw_link_text(sw_links), strict=True)
                ]
            ),
            "repo_name": _str_col(repo_names if repo_names is not None else [None] * n_code),
            "reason": _str_col(reason_of),
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
    report_order, order_table = _order_report(order_tbl, cycle, ambiguous_order, fuzzy_sources)

    total_ambiguous = sum(ambiguous_n)
    report_paths = _repro_path_fix_text(rewrite_list, execute)
    if total_ambiguous > 0:
        report_paths += (
            f" {total_ambiguous:d} reference{plural(total_ambiguous)} matched several files "
            "sharing a name and could not be resolved by study group or folder; "
            f"{plural(total_ambiguous, 'it was', 'they were')} left unrewritten and flagged."
        )
    report_paths = _call_paths_text(report_paths, n_call_paths, n_call_unresolved)
    report_names = _repro_name_report(name_corrections, execute)

    # When the code was run, its outcomes show what is missing, and code_check lists
    # files that are not in the repository, so only what those two cannot say remains.
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
        missing_table = _missing_frame(reported_inputs)
    else:
        report_missing = (
            f"{n_missing_inputs:d} input file{plural(n_missing_inputs)} the code reads "
            f"{plural(n_missing_inputs, 'is', 'are')} not available. Files withheld only because "
            "of their size are distinguished from files absent from the repository — the former "
            "are a size-cap issue, not a reproducibility failure."
        )
        missing_table = _missing_frame(missing_inputs)
    report += ["#### Run order", report_order, scroll_table(order_table, maxrows=10)]
    report += ["#### File paths", report_paths, *(report_names or [])]
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
    if parse_errs > 0:
        report += [
            "#### Parsing",
            f"{parse_errs:d} R file{plural(parse_errs)} did not parse and cannot be run (see "
            "code_check for the errors).",
        ]
    if n_not_r_content > 0 or n_jags_model > 0:
        report += _sniff_report(names, sniff_type, sniff_detail, n_not_r_content, n_jags_model)
    report += _repro_not_run_report(names, sniff_type, r_used_by, sw_why) or []

    if run_results is not None and len(run_results) > 0:
        from metacheck.modules._reproducibility_exec import _execution_report

        report += _execution_report(ex, a, ctx)
    else:
        report.append(
            "*Executing the code is a separate, opt-in phase (run with `execute = TRUE`). This "
            "report describes what a run would involve; it did not run anything.*"
        )
        if h._is_true(a["recorded_versions"]) and a["install_missing"] is not False:
            report += _as_lines(_repro_recorded_preview(deps, version_pin))
    recorded_advice: Any = None
    if (
        execute
        and not h._is_true(a["recorded_versions"])
        and a["install_missing"] is not False
        and not _is_several(paper)
    ):
        recorded_advice = _repro_recorded_advice(deps, version_pin, run_results)
        report += _as_lines(recorded_advice)

    # 10. Summary ----------------------------------------------------------------------
    outcomes_all = h._chr_col(run_results, "outcome") if run_results is not None else []
    n_ran_ok = sum(o == "ran_ok" for o in outcomes_all) if run_results is not None else None
    n_nodep_sum = sum(o == "dependency_unavailable" for o in outcomes_all)
    n_by_src_sum = sum(o == "run_by_source" for o in outcomes_all)
    n_run_own = len(run_results) - n_by_src_sum if run_results is not None else 0
    lines: list[str | None] = []
    if execute:
        where = "Docker container" if sandbox == "docker" else "subprocess"
        lines.append(
            f"We assessed AND ran {n_code:d} R code file{plural(n_code)} for reproducibility "
            f"(each in an isolated {where})."
        )
    else:
        lines.append(
            f"We assessed {n_code:d} R code file{plural(n_code)} for reproducibility (static "
            "analysis; no code was run)."
        )
        lines.append(
            f"{n_runnable:d} file{plural(n_runnable)} appear{'s' if n_runnable == 1 else ''} "
            "runnable so far (parses, inputs resolve, placeable in the run order)."
        )
    if n_dup > 0:
        lines.append(
            f"{n_dup:d} file{plural(n_dup)} skipped as byte-identical duplicate{plural(n_dup)} of "
            "another file already in the run (the paper links more than one "
            "repository/component)."
        )
    if execute and run_results is not None:
        more = (
            f" ({n_by_src_sum:d} more {plural(n_by_src_sum, 'is', 'are')} run by another script "
            "with `source()`)"
            if n_by_src_sum > 0
            else ""
        )
        lines.append(
            f"{n_ran_ok:d} of {n_run_own:d} script{plural(n_run_own)} ran without error when "
            f"executed{more}."
        )
    if n_nodep_sum > 0:
        lines.append(
            f"{n_nodep_sum:d} script{plural(n_nodep_sum)} could not run because "
            f"{plural(n_nodep_sum, 'its', 'their')} own dependency is unavailable (not counted "
            "against the traffic light)."
        )
    if len(reported_inputs) > 0:
        nr = len(reported_inputs)
        lines.append(
            f"{nr:d} referenced input{plural(nr)} unavailable ({n_withheld:d} withheld due to "
            "size)."
        )
    lines.append(f"{n_deps:d} installable dependenc{'y' if n_deps == 1 else 'ies'} detected.")
    lines += _as_lines(_repro_recorded_summary(recorded, install_results))
    lines += _as_lines(recorded_advice)
    summary_text = h._paste_bullets(lines)

    summary_table = _block_summary(
        pid(structure_df, code_tbl),
        {
            "repro_code_n": n_code,
            "repro_runnable": n_runnable,
            "repro_missing_inputs": n_missing_inputs,
            "repro_deps": n_deps,
            "repro_ran_ok": n_ran_ok,
        },
    )
    if dup_report is not None:
        report += dup_report

    # `modifications`: one row per change (path rewrites always; what writing and
    # running the scripts changed only when they ran)
    modifications = _r_modifications(
        rewrite_list, name_corrections, r_choices_filled, ex if execute else None
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
        "recorded": recorded,
        "recorded_advice": recorded_advice,
        "install_r": install_r,
    }


_OLD_DEFAULT_DETAIL = {
    "factors": "text read as factors, as before R 4.0",
    "latin1": "data files read as Latin-1",
}


def _r_modifications(
    rewrite_list: h.RNamedList,
    name_corrections: pd.DataFrame,
    choices_filled: Mapping[str, Sequence[str]],
    ex: Mapping[str, Any] | None,
) -> pd.DataFrame:
    """The R block's ``modifications``: one row per change made to the scripts.

    Path rewrites, name corrections and filled-in file choices come from the static
    analysis; the rest (*ex*, the execution step's state) only exists when the scripts
    were written and run.
    """
    from metacheck.repro.layout import _repro_slash_rows

    rows: list[tuple[Any, str, Any]] = []
    fn: str | None
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
                rows.append((fn, "path_rewrite", f'"{_na_str(ref)}" -> "{tgt}"'))
    # a name in the code spelled otherwise than in the deposit, for the authors
    for fn, in_code, in_dep in zip(
        h._chr_col(name_corrections, "file_name"),
        h._chr_col(name_corrections, "name_in_code"),
        h._chr_col(name_corrections, "name_in_deposit"),
        strict=True,
    ):
        rows.append((fn, "name_corrected", f'"{_na_str(in_code)}" -> "{_na_str(in_dep)}"'))
    for fn, filled in choices_filled.items():
        rows += [(fn, "file_choice_filled", x) for x in filled]

    extra: list[pd.DataFrame] = []
    if ex is not None:
        run_tbl = ex.get("run_tbl")
        if run_tbl is not None and len(run_tbl):
            rt_names = h._chr_col(run_tbl, "file_name")
            for col_n, col_d, kind, sep in (
                ("setwd_removed", "setwd_paths", "setwd_removed", ", "),
                ("family_replaced", "family_detail", "font_replaced", "; "),
            ):
                if col_d not in run_tbl.columns:
                    continue
                for fn, n, p in zip(
                    rt_names, h._col(run_tbl, col_n), h._chr_col(run_tbl, col_d), strict=True
                ):
                    if not h._na(n) and n > 0 and h._nzchar(p):
                        rows += [(fn, kind, x) for x in strsplit(p, sep, fixed=True)]
        # one row per library() call added (a script can get several)
        for fn, pkgs in (ex.get("library_injections") or {}).items():
            rows += [(fn, "library_injected", f"library({p})") for p in h._as_list(pkgs)]
        if run_tbl is not None and "env_calls_replaced" in run_tbl.columns:
            for fn, n in zip(
                h._chr_col(run_tbl, "file_name"), h._col(run_tbl, "env_calls_replaced"), strict=True
            ):
                if not h._na(n) and n > 0:
                    rows.append(
                        (
                            fn,
                            "env_call_replaced",
                            f"{int(n):d} groundhog, checkpoint or renv call{plural(int(n))}",
                        )
                    )
        tail: list[tuple[Any, str, Any]] = []
        for fn, kinds in (ex.get("old_defaults_used") or {}).items():
            tail += [(fn, "old_default", _OLD_DEFAULT_DETAIL.get(k)) for k in kinds]
        slash = _repro_slash_rows(run_tbl) if run_tbl is not None else None
        stan = ex.get("stan_rows")
        later: list[tuple[Any, str, Any]] = []
        for fn, deps_on in (ex.get("run_after") or {}).items():
            later += [(fn, "loaded_first", x) for x in deps_on]
        for fn, files in (ex.get("r_written_later") or {}).items():
            later += [
                (fn, "run_again", f"run once more after another script wrote {x}") for x in files
            ]
        placed = ex.get("r_placed")
        if placed is not None and len(placed):
            later += [
                (s, "output_copied", f"copied {f}, written by another script, to {t}")
                for s, f, t in zip(
                    h._chr_col(placed, "script"),
                    h._chr_col(placed, "from"),
                    h._chr_col(placed, "to"),
                    strict=True,
                )
            ]
        dep_placed = ex.get("r_deposit_placed")
        if dep_placed is not None and len(dep_placed):
            later += [
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
        # bind_rows(rw_rows, setwd, font, library, env, slash, old_default, stan,
        # run_after, later)
        extra = [
            part[["file_name", "change_type", "detail"]]
            for part in (slash, _mod_frame(tail), stan, _mod_frame(later))
            if part is not None and len(part)
        ]
    frames = [f for f in (_mod_frame(rows), *extra) if f is not None and len(f)]
    if not frames:
        return _empty_modifications()
    out = pd.concat(frames, ignore_index=True)
    return out.astype({"file_name": "string", "change_type": "string", "detail": "string"})


def _mod_frame(rows: Sequence[tuple[Any, str, Any]]) -> pd.DataFrame | None:
    if not rows:
        return None
    return pd.DataFrame(
        {
            "file_name": pd.array([r[0] for r in rows], dtype="string"),
            "change_type": pd.array([r[1] for r in rows], dtype="string"),
            "detail": pd.array([r[2] for r in rows], dtype="string"),
        }
    )


def _is_several(paper: Any) -> bool:
    """``.is_paper_list(paper) && length(paper) > 1``."""
    from metacheck.papers.model import is_paper_list

    return bool(is_paper_list(paper) and len(paper) > 1)


def _as_lines(x: Any) -> list[str]:
    """An R character vector (``NULL``, a string or several) as a list of lines."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return [str(v) for v in x if v is not None]


def _missing_frame(mi: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "File": mi["basename"].tolist(),
            "Status": mi["status"].tolist(),
            "Reason": mi["detail"].tolist(),
        }
    )


def _order_report(
    order_tbl: pd.DataFrame, cycle: list[str], ambiguous_order: bool, fuzzy_sources: pd.DataFrame
) -> tuple[str, pd.DataFrame]:
    """The "Run order" text and table."""
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
        text = (
            "No ordering signal (no data dependency, `source()` call, or numeric filename "
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
        text += (
            f" **{n_fuzzy:d} `source()` reference{plural(n_fuzzy)}** only matched a file after "
            "normalising both names (lowercase, extension stripped, a leading numbering prefix "
            "stripped, separators collapsed) — a likely renumbered/reworded filename rather than "
            f"an exact match: {pairs}. Treat these as lower-confidence than an exact `source()` "
            "resolution."
        )
    return text, order_table


def _call_paths_text(report_paths: str, n_call_paths: int, n_call_unresolved: int) -> str:
    """The note on data paths built at runtime, added to the "File paths" text."""
    if n_call_paths == 0:
        return report_paths
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
    return report_paths + (
        f" **{n_call_paths:d} data path{plural(n_call_paths)} "
        f"{plural(n_call_paths, 'was', 'were')} built at runtime** with "
        "`sprintf()`/`paste()`/`file.path()` instead of written as a literal path — this "
        "is fragile authoring practice (it silently breaks if the code is run from a "
        "different working directory than the author's own). "
        f"We resolved {resolved} on a best-effort basis{tail}."
    )


def _sniff_report(
    names: Sequence[str],
    sniff_type: Sequence[str | None],
    sniff_detail: Sequence[str | None],
    n_not_r_content: int,
    n_jags_model: int,
) -> list[Any]:
    """The "Non-R content detected" section."""
    out: list[Any] = ["#### Non-R content detected"]
    if n_not_r_content > 0:
        out.append(
            f"{n_not_r_content:d} file{plural(n_not_r_content)} that failed to parse "
            f"{plural(n_not_r_content, 'is', 'are')} not actually R content (a JSON/HTML/XML "
            "dump saved with an R-type extension) -- this is informational, not a "
            "reproducibility defect."
        )
    if n_jags_model > 0:
        out.append(
            f"{n_jags_model:d} file{plural(n_jags_model)} {plural(n_jags_model, 'is', 'are')} "
            "a JAGS/BUGS model definition (`model { ... }`), not standalone R -- these are "
            "meant to be passed to `rjags::jags.model()`/`R2jags::jags()`/"
            "`runjags::run.jags()`, not run directly, and are not a reproducibility defect."
        )
    rows = [i for i, s in enumerate(sniff_type) if s in ("not_r_content", "jags_model")]
    out.append(
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
    return out
