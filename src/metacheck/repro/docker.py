"""The Docker execution backend of the reproducibility check.

Port of ``R/reproducibility_check_docker.R``: the same install-then-run
steps as :func:`~metacheck.repro.core.repro_install_deps` and
:func:`~metacheck.repro.core.repro_run_scripts`, but inside containers
(through the ``docker`` CLI) instead of an ``Rscript`` subprocess on the
host, so the code cannot touch the host filesystem or network.

1. INSTALL phase: ``docker run`` with default networking (CRAN/GitHub must
   be reachable), non-root and with dropped capabilities, writing into a
   library on a mounted host directory (``/rlib``) so it survives into
   phase 2.
2. RUN phase: ``docker run --network none --read-only --user <non-root>``
   per script, with the materialised sandbox (``/sandbox``) and the
   populated library (``/rlib``, read-only) mounted.

The only change from metacheck is the capture sidecar of the run phase: the
container writes JSON (``/sandbox/.capture.json``) instead of an RDS file,
as the ``Rscript`` backend does (see :mod:`metacheck.statout.r_capture`).
The install phase writes R's own ``.install_results.rds``, read back with
pytacheck's R unserializer.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from metacheck._r.base import as_character, trimws
from metacheck._r.frames import bind_rows
from metacheck._r.regex import grepl, gsub
from metacheck._values import as_str
from metacheck.repro.core import (
    _chr_list,
    _classify_error,
    _col,
    _frame,
    _message,
    _ordered_names,
    _pre_run_outcome,
    _r_is_true,
    _read_cap,
    _read_lines,
    _repro_classify_install_message,
    _run_frame,
    _run_row,
    _script_lines,
    _setdiff,
)

__all__ = [
    "repro_docker_available",
    "repro_install_deps_docker",
    "repro_run_scripts_docker",
]

#: The non-root uid:gid of R's backend (``.repro_docker_uid``). Here it is what a container runs
#: as unless :func:`_repro_docker_user` finds the host's own to be a better one.
_REPRO_DOCKER_UID = "1000:1000"

#: The pre-built metacheck image with ~750 common packages (``.repro_docker_default_image``).
_REPRO_DOCKER_DEFAULT_IMAGE = "ghcr.io/scienceverse/metacheck_r:latest"


def _docker(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    merge_stderr: bool = False,
) -> dict[str, Any] | Exception:
    """``processx::run("docker", args, error_on_status = FALSE, timeout = ...)``.

    Returns ``{"status", "timeout", "stdout"}``, or the exception when
    ``docker`` could not be started (R's condition).
    """
    exe = shutil.which("docker") or "docker"
    out_f = open(stdout, "wb") if stdout else None  # noqa: SIM115 - closed below
    err_f = None
    if stderr and not merge_stderr:
        err_f = open(stderr, "wb")  # noqa: SIM115 - closed below
    try:
        proc = subprocess.run(  # noqa: S603 - the docker CLI
            [exe, *args],
            stdout=out_f if out_f is not None else subprocess.PIPE,
            stderr=(
                subprocess.STDOUT
                if merge_stderr
                else err_f
                if err_f is not None
                else subprocess.PIPE
            ),
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": None, "timeout": True, "stdout": ""}
    except OSError as exc:
        return exc
    finally:
        for f in (out_f, err_f):
            if f is not None:
                f.close()
    text = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else ""
    return {"status": proc.returncode, "timeout": False, "stdout": text}


def repro_docker_available() -> dict[str, Any]:
    """Is Docker available and running?

    Port of ``R/reproducibility_check_docker.R::repro_docker_available()``:
    ``{"ok": bool, "msg": str}`` -- ``msg`` says why not (``docker`` not on
    ``PATH``, or ``docker info`` failing because the daemon is not running).
    """
    if not shutil.which("docker"):
        return {
            "ok": False,
            "msg": "the 'docker' command was not found on PATH. Install Docker Desktop "
            "(https://www.docker.com/products/docker-desktop/) and ensure it is running.",
        }
    res = _docker(["info"], timeout=15)
    if isinstance(res, Exception) or res.get("status") != 0:
        return {
            "ok": False,
            "msg": "Docker does not appear to be running. Start Docker Desktop and try again.",
        }
    return {"ok": True, "msg": ""}


def _repro_docker_scrub(root: str | os.PathLike[str]) -> list[str]:
    """Delete from *root* what a container left there that the host must not follow or open.

    The sandbox directory is mounted read-write, so the paper's code can leave symbolic links in
    it that point at host paths: the container only sees a dangling link, but the host follows
    it when it writes the next runner script, reads the next script, the capture file or the
    install results. The code can also leave a named pipe or a socket, which a host read waits
    on for ever. Links that stay inside *root* are harmless and kept; every other link, and every
    file that is not a regular file or a directory, is removed. Returns what was removed, as
    paths relative to *root*.
    """
    top = os.fspath(root)
    real_top = os.path.realpath(top)
    removed: list[str] = []

    def inside(path: str) -> bool:
        try:
            return os.path.commonpath([real_top, os.path.realpath(path)]) == real_top
        except ValueError:  # another drive
            return False

    for here, dirs, files in os.walk(top, topdown=True, followlinks=False):
        for name in [*dirs, *files]:
            path = os.path.join(here, name)
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                if inside(path):
                    continue
            elif stat.S_ISREG(mode) or stat.S_ISDIR(mode):
                continue
            with contextlib.suppress(OSError):
                os.unlink(path)
                removed.append(os.path.relpath(path, top))
    return removed


def _repro_docker_user() -> str:
    """The ``--user`` of every container in this backend: ``uid:gid``, never root.

    On Linux a bind mount keeps the host's owner and mode, and the sandbox directory is private
    (``tempfile.mkdtemp``: mode 0700). A container user who is not the host user cannot even
    enter it, so with a fixed uid every script failed with "Permission denied" on a host whose
    uid is not 1000. The container therefore runs as the host user's own uid and gid. What it
    writes then belongs to the host user, which also keeps the host's cleanup and
    :func:`_repro_docker_scrub` working in directories the code created. Where that does not
    apply, the fixed :data:`_REPRO_DOCKER_UID` is used: a host user who is root (never run the
    code as root; see :func:`_repro_docker_hand_over`), and Docker Desktop on macOS and Windows,
    which does not enforce the host's file ownership.
    """
    if sys.platform.startswith("linux") and hasattr(os, "getuid") and os.getuid() != 0:
        return f"{os.getuid()}:{os.getgid()}"
    return _REPRO_DOCKER_UID


def _repro_docker_hand_over(path: str | os.PathLike[str], *, tree: bool = True) -> None:
    """Give the container user what it is to read and write, when the host user is root.

    A root host runs the container as :data:`_REPRO_DOCKER_UID`, who is not the owner of the
    private directories made here. *path*, and with *tree* everything below it, is handed to
    that user, links as links (``lchown``: a link is never followed). A no-op unless the host
    is Linux and root, since nothing else needs it (see :func:`_repro_docker_user`).
    """
    if not (sys.platform.startswith("linux") and hasattr(os, "getuid") and os.getuid() == 0):
        return
    uid, gid = (int(x) for x in _REPRO_DOCKER_UID.split(":"))
    top = os.fspath(path)
    with contextlib.suppress(OSError):
        os.lchown(top, uid, gid)
    if not tree:
        return
    for here, dirs, files in os.walk(top, followlinks=False):
        for name in [*dirs, *files]:
            with contextlib.suppress(OSError):
                os.lchown(os.path.join(here, name), uid, gid)


def _repro_docker_container_name() -> str:
    """A unique container name for one ``docker run`` (``.repro_docker_container_name()``)."""
    raw = f"repro_{secrets.token_hex(6)}"  # basename(tempfile("repro_"))
    return "repro_" + str(gsub("[^a-zA-Z0-9_.-]", "", raw))


def _repro_docker_stop(name: str, timeout: float = 30) -> None:
    """Best-effort ``docker stop`` of a container by name (``.repro_docker_stop()``)."""
    try:
        _docker(["stop", "--time", "5", name], timeout=timeout)
    except Exception:  # noqa: S110 - best effort, never masks the run's result
        pass
    return None


def _repro_docker_resource_args() -> list[str]:
    """``--cpus``/``--memory`` from the ``metacheck.docker_resource_limits`` option."""
    from metacheck.utils import get_option

    limits = get_option("metacheck.docker_resource_limits")
    if limits is None:
        return []

    def get(key: str) -> list[str]:
        # as.character(limits$<key>): a missing element is character(0), NA is "NA"
        v = limits.get(key) if isinstance(limits, Mapping) else getattr(limits, key, None)
        if v is None:
            return []
        vals = list(v) if isinstance(v, list | tuple) else [v]
        return [as_str(x) or "NA" for x in vals]

    memory = get("memory_gb")
    # paste0(limits$memory_gb, "g"): NULL pastes as "g"
    return ["--cpus", *get("cpus"), "--memory", *([f"{m}g" for m in memory] or ["g"])]


def _normalize_path(path: str, must_work: bool = False) -> str:
    """R ``normalizePath()`` on Unix (a path that does not exist is returned as given)."""
    p = os.path.expanduser(path)
    if os.path.exists(p):
        return os.path.realpath(p)
    if must_work:
        raise FileNotFoundError(f'path[1]="{path}": No such file or directory')
    return p


def _repro_docker_container_path(host_path: str, sandbox_root: str) -> str:
    """A host path under *sandbox_root* as its path inside the container (``/sandbox/...``)."""
    host_norm = _normalize_path(os.fspath(host_path)).replace("\\", "/")
    root_norm = _normalize_path(os.fspath(sandbox_root)).replace("\\", "/")
    root_prefix = root_norm + "/"
    if host_norm.startswith(root_prefix):
        rel = host_norm[len(root_prefix) :]
    elif host_norm == root_norm:
        rel = ""
    else:
        rel = host_norm.rstrip("/").rpartition("/")[2]
    return "/sandbox/" + rel


def _repro_docker_image_for(
    r_versions: Sequence[str | None] | str | None = (), use_declared_version: bool = False
) -> str:
    """The Docker image to run in (``.repro_docker_image_for()``).

    The pre-built metacheck image unless *use_declared_version*; then
    ``rocker/r-ver:<the first cleanly-shaped declared R version>`` (or
    ``rocker/r-ver:latest``).
    """
    if not _r_is_true(use_declared_version):
        return _REPRO_DOCKER_DEFAULT_IMAGE
    versions = _chr_list(r_versions)
    clean = [
        trimws(v)
        for v in versions
        if v is not None and grepl(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$", trimws(v))
    ]
    tag = clean[0] if clean else "latest"
    return f"rocker/r-ver:{tag}"


def _deparse1_chr(values: Sequence[str | None] | None) -> str:
    """``deparse1()`` of a character vector (``NULL`` for ``None``)."""
    if values is None:
        return "NULL"
    vals = list(values)
    if not vals:
        return "character(0)"
    all_na = all(v is None for v in vals)

    def lit(v: str | None) -> str:
        if v is None:
            return "NA_character_" if all_na else "NA"
        out = ['"']
        esc = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
        esc.update({"\a": "\\a", "\b": "\\b", "\f": "\\f", "\v": "\\v"})
        for ch in v:
            if ch in esc:
                out.append(esc[ch])
            elif ord(ch) < 32 or ord(ch) == 127:
                out.append(f"\\{ord(ch):03o}")
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)

    if len(vals) == 1:
        return lit(vals[0])
    return "c(" + ", ".join(lit(v) for v in vals) + ")"


_INSTALL_SCRIPT = [
    'lib <- "/rlib"',
    "dir.create(lib, recursive = TRUE, showWarnings = FALSE)",
    ".libPaths(c(lib, .libPaths()))",
    'repos <- getOption("repos")',
    'if (is.null(repos) || !length(repos) || any(!nzchar(repos)) || any(repos == "@CRAN@"))',
    '  options(repos = c(CRAN = "https://cloud.r-project.org"))',
    'gh_avail <- requireNamespace("remotes", quietly = TRUE)',
    "pkgs   <- .repro_docker_pkgs",
    "srcs   <- .repro_docker_srcs",
    "refs   <- .repro_docker_refs",
    "out <- list()",
    "for (i in seq_along(pkgs)) {",
    "  pkg <- pkgs[i]; src <- srcs[i]; ref <- refs[i]",
    "  warns <- character(0)",
    "  res <- tryCatch({",
    "    withCallingHandlers({",
    '    if (identical(src, "github")) {',
    '      if (!gh_avail) stop("the remotes package is not available in this image")',
    '      remotes::install_github(ref, lib = lib, upgrade = "never", quiet = FALSE)',
    '    } else if (identical(src, "url")) {',
    "      install.packages(ref, lib = lib, repos = NULL, quiet = FALSE)",
    '    } else if (identical(src, "bioc")) {',
    '      if (!requireNamespace("BiocManager", quietly = TRUE))',
    '        install.packages("BiocManager", lib = lib, quiet = FALSE)',
    "      BiocManager::install(pkg, lib = lib, update = FALSE, ask = FALSE)",
    "    } else {",
    "      install.packages(pkg, lib = lib, quiet = FALSE)",
    "    }",
    "    }, warning = function(w) warns <<- c(warns, conditionMessage(w)))",
    # Same rule as _repro_not_loadable_msg() (metacheck.repro.core), inlined
    # because this script runs without this package: a package
    # install.packages() could not find only produces a "not available"
    # warning, which is the real reason (issue #421).
    "    if (!requireNamespace(pkg, quietly = TRUE, lib.loc = lib)) {",
    '      na <- unique(grep("is not available", warns, value = TRUE, fixed = TRUE))',
    '      stop(if (length(na) > 0) paste(na, collapse = "; ")',
    '           else "installed but package is not loadable")',
    "    }",
    '    list(ok = TRUE, msg = "")',
    "  }, error = function(e) list(ok = FALSE, msg = conditionMessage(e)))",
    "  out[[i]] <- data.frame(package = pkg, source = src, installed = res$ok, message = res$msg)",
    '  saveRDS(do.call(rbind, out), "/sandbox/.install_results.rds")',
    "}",
]


def _install_script(install_deps: pd.DataFrame) -> list[str]:
    """The ``install.R`` a :func:`repro_install_deps_docker` container runs."""
    refs = _chr_list(install_deps["ref"]) if "ref" in install_deps.columns else None
    header = [
        ".repro_docker_pkgs <- " + _deparse1_chr(_chr_list(install_deps["package"])),
        ".repro_docker_srcs <- " + _deparse1_chr(_chr_list(install_deps["source"])),
        ".repro_docker_refs <- " + _deparse1_chr(refs),
    ]
    return header + _INSTALL_SCRIPT


def _read_install_results(path: str) -> pd.DataFrame | None:
    """``readRDS()`` of the container's ``.install_results.rds`` (``None`` if unreadable)."""
    from metacheck.datacheck._files_rdata import r_frame_to_pandas, read_rds

    try:
        obj = read_rds(path)
        df = r_frame_to_pandas(obj, subset=False)
    except Exception:
        return None
    out = pd.DataFrame(
        {
            "package": pd.Series(_chr_list(df.get("package", [])), dtype="string"),
            "source": pd.Series(_chr_list(df.get("source", [])), dtype="string"),
            "installed": pd.Series(
                [None if as_str(v) is None else bool(v) for v in df.get("installed", [])],
                dtype="boolean",
            ),
            "message": pd.Series(_chr_list(df.get("message", [])), dtype="string"),
        }
    )
    return out


def repro_install_deps_docker(
    install_deps: pd.DataFrame | None,
    lib_dir: str | os.PathLike[str],
    image: str = "rocker/r-ver:latest",
    timeout: float = 600,
) -> pd.DataFrame:
    """Install R dependencies inside a throwaway Docker container.

    Port of ``R/reproducibility_check_docker.R::repro_install_deps_docker()``:
    one container installs every package into *lib_dir* (mounted at
    ``/rlib``), writing its results incrementally so a timeout still reports
    the packages that finished; packages without a result are recorded as
    failed with the run's message. Same columns as
    :func:`~metacheck.repro.core.repro_install_deps` (``via_archive`` is
    always false).
    """
    from metacheck.repro.core import _install_frame

    if install_deps is None or len(install_deps) == 0:
        return _install_frame([])
    lib = os.fspath(lib_dir)
    os.makedirs(lib, exist_ok=True)

    sandbox_dir = tempfile.mkdtemp(prefix="repro_docker_install_")
    out_fd, out_file = tempfile.mkstemp(suffix=".out")
    os.close(out_fd)
    try:
        script_path = os.path.join(sandbox_dir, "install.R")
        Path(script_path).write_bytes(
            ("\n".join(_install_script(install_deps)) + "\n").encode("utf-8")
        )
        _repro_docker_hand_over(sandbox_dir)
        _repro_docker_hand_over(lib, tree=False)  # what is in it was written by the container
        container_name = _repro_docker_container_name()
        args = [
            "run", "--rm", "--name", container_name,
            "--user", _repro_docker_user(),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "512",
            *_repro_docker_resource_args(),
            "-v", f"{sandbox_dir}:/sandbox",
            "-v", f"{_normalize_path(lib)}:/rlib",
            image, "Rscript", "/sandbox/install.R",
        ]  # fmt: skip
        raw = _docker(args, timeout=timeout, stdout=out_file, merge_stderr=True)
        res = None if isinstance(raw, Exception) else raw
        if res is None or res.get("timeout"):
            _repro_docker_stop(container_name)

        _repro_docker_scrub(sandbox_dir)  # a link the install left must not be read through
        results_path = os.path.join(sandbox_dir, ".install_results.rds")
        tbl = (
            _read_install_results(results_path)
            if res is not None and os.path.exists(results_path)
            else None
        )
        if res is not None:
            try:
                txt = "\n".join(_read_lines(out_file))
            except OSError:
                txt = ""
            if res.get("timeout"):
                msg = f"docker install timed out after {as_character(timeout)}s"
            else:
                status = as_character(res.get("status")) or "NA"
                msg = f"docker install failed (status {status}): {txt}"
        else:
            msg = "docker run could not be started"
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.unlink(out_file)

    pkgs = _chr_list(install_deps["package"])
    srcs = _chr_list(install_deps["source"])
    done = _col(tbl, "package") if tbl is not None else []
    missing = _setdiff(pkgs, done)
    padding = None
    if missing:
        src_of: dict[str | None, str | None] = {}
        for p, s in zip(pkgs, srcs, strict=True):
            src_of.setdefault(p, s)
        padding = _frame(
            {
                "package": ("string", missing),
                "source": ("string", [src_of.get(p) for p in missing]),
                "installed": ("boolean", [False] * len(missing)),
                "message": ("string", [msg] * len(missing)),
            }
        )
    out = bind_rows([tbl, padding])
    out["via_archive"] = pd.Series([False] * len(out), dtype="boolean")
    installed = _col(out, "installed")
    messages = _col(out, "message")
    out["category"] = pd.Series(
        [
            None if inst is None else (None if inst else _repro_classify_install_message(m))
            for inst, m in zip(installed, messages, strict=True)
        ],
        dtype="string",
    )
    return out


def _repro_docker_capture_preamble() -> list[str]:
    """The capture helpers as standalone script lines (``.repro_docker_capture_preamble()``).

    metacheck deparses ``.r_capture_helpers()`` to top-level definitions; here
    the ported helpers (:mod:`metacheck.statout.r_capture`) are built from
    source and assigned into the global environment, together with the
    dependency-free JSON writer the container uses for its capture sidecar.
    """
    from metacheck.statout.r_capture import _JSON_R, _r_capture_helpers

    src = [
        *_JSON_R.strip("\n").split("\n"),
        ".repro_capture_helpers <- " + _r_capture_helpers(),
        "for (.nm in names(.repro_capture_helpers))",
        "  assign(.nm, .repro_capture_helpers[[.nm]], envir = globalenv())",
        "rm(.nm)",
    ]
    return "\n".join(src).split("\n")


def _make_names(x: str | None) -> str:
    """R ``make.names()`` of one name."""
    if x is None:
        return "NA."
    s = "".join(ch if (ch.isalnum() or ch in "._") else "." for ch in x)
    if s == "" or not (s[0].isalpha() or (s[0] == "." and not (len(s) > 1 and s[1].isdigit()))):
        s = "X" + s
    reserved = {
        "if", "else", "repeat", "while", "function", "for", "next", "break", "TRUE", "FALSE",
        "NULL", "Inf", "NaN", "NA", "NA_integer_", "NA_real_", "NA_character_",
        "NA_complex_", "in",
    }  # fmt: skip
    return s + "." if s in reserved else s


def _wrapper_lines(
    preamble: list[str], container_script: str, lib: bool, cap_file_container: str
) -> list[str]:
    """The per-script runner the container executes (``.repro_docker_run()``)."""
    lines = [
        *preamble,
        ".repro_docker_run <- function() {",
        *(['  .libPaths(c("/rlib", .libPaths()))'] if lib else []),
        "  captures <- list()",
        f'  on.exit(try(.mc_write_json(captures, "{cap_file_container}"), silent = TRUE), '
        "add = TRUE)",
        f'  exprs <- parse("{container_script}", keep.source = TRUE)',
        '  srcrefs <- attr(exprs, "srcref")',
        "  env <- globalenv()",
        "  for (i in seq_along(exprs)) {",
        "    e <- exprs[[i]]",
        "    sr <- if (!is.null(srcrefs) && length(srcrefs) >= i) srcrefs[[i]] else NULL",
        "    txt <- if (!is.null(sr)) as.character(sr) else deparse(e)",
        '    cat(paste0("> ", txt, collapse = "\\n"), "\\n", sep = "")',
        "    res <- withVisible(eval(e, envir = env))",
        "    if (res$visible) print(res$value)",
        '    call_txt <- paste(txt, collapse = " ")',
        "    rec <- tryCatch(.r_capture_value(res$value, call_txt), error = function(e) NULL)",
        "    if (!is.null(rec)) {",
        "      rec$line <- if (!is.null(sr)) as.integer(sr[1L]) else NA_integer_",
        "      rec$call_text <- call_txt",
        "      captures[[length(captures) + 1L]] <- rec",
        "    }",
        "  }",
        "  invisible(NULL)",
        "}",
        ".repro_docker_run()",
    ]
    return lines


def repro_run_scripts_docker(
    run_tbl: pd.DataFrame | None,
    order: Any,
    sandbox_root: str | os.PathLike[str],
    lib_dir: str | os.PathLike[str] | None = None,
    image: str = "rocker/r-ver:latest",
    timeout: float = 600,
    skip: Sequence[str] | str = (),
    parses: Mapping[str, Any] | pd.Series | None = None,
    failed_deps: Sequence[str] | str = (),
) -> pd.DataFrame:
    """Run the paper's scripts, in order, each in an isolated Docker container.

    Port of ``R/reproducibility_check_docker.R::repro_run_scripts_docker()``:
    the same outcomes and columns as
    :func:`~metacheck.repro.core.repro_run_scripts`, but each script runs via
    ``docker run --network none --read-only --user <non-root>`` with
    *sandbox_root* mounted at ``/sandbox`` (the working directory) and
    *lib_dir* read-only at ``/rlib``.
    """
    if run_tbl is None or len(run_tbl) == 0:
        return _run_frame([])
    from metacheck.statout.r_capture import _read_captures
    from metacheck.utils import pb

    ordered = _ordered_names(run_tbl, order)
    root = _normalize_path(os.fspath(sandbox_root), must_work=True)
    lib_norm = (
        _normalize_path(os.fspath(lib_dir), must_work=True)
        if lib_dir is not None and os.path.isdir(lib_dir)
        else None
    )
    names = _chr_list(run_tbl["file_name"])
    paths = _chr_list(run_tbl["script_path"])
    preamble = _repro_docker_capture_preamble()
    cap_file_container = "/sandbox/.capture.json"
    cap_file_host = os.path.join(root, ".capture.json")

    user = _repro_docker_user()
    _repro_docker_hand_over(root)  # the scripts and data the container reads, and where it writes

    bar = pb(len(ordered), ":what [:bar] :current/:total")
    rows: list[dict[str, Any]] = []
    try:
        bar.tick(0, {"what": ""})
        for fn in ordered:
            bar.tick(1, {"what": fn})
            i = names.index(fn)
            pre = _pre_run_outcome(fn, skip, parses)
            if pre is not None:
                rows.append(pre)
                continue
            exec_lines = _script_lines(paths[i])
            _message(
                "[repro/docker]   -> running '", fn, "' (timeout ", as_character(timeout), "s) ..."
            )
            container_script = _repro_docker_container_path(paths[i] or "", root)
            wrapper_path = os.path.join(root, f".runner_{_make_names(fn)}.R")
            Path(wrapper_path).write_bytes(
                (
                    "\n".join(
                        _wrapper_lines(
                            preamble, container_script, lib_norm is not None, cap_file_container
                        )
                    )
                    + "\n"
                ).encode("utf-8")
            )
            _repro_docker_hand_over(wrapper_path, tree=False)
            container_wrapper = _repro_docker_container_path(wrapper_path, root)
            container_name = _repro_docker_container_name()
            args = [
                "run", "--rm", "--name", container_name,
                "--network", "none", "--read-only",
                "--tmpfs", "/tmp",  # noqa: S108 - inside the container
                "--user", user,
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "512",
                *_repro_docker_resource_args(),
                "-w", "/sandbox",
                "-v", f"{root}:/sandbox",
            ]  # fmt: skip
            if lib_norm is not None:
                args += ["-v", f"{lib_norm}:/rlib:ro"]
            args += [image, "Rscript", container_wrapper]

            with tempfile.TemporaryDirectory(prefix="pytacheck_docker_") as tmp:
                out_file = os.path.join(tmp, "out.txt")
                err_file = os.path.join(tmp, "err.txt")
                t0 = time.monotonic()
                res = _docker(args, timeout=timeout, stdout=out_file, stderr=err_file)
                elapsed = time.monotonic() - t0
                is_timeout = (
                    isinstance(res, Exception)
                    and bool(grepl("timed? ?out", str(res), ignore_case=True))
                ) or (isinstance(res, dict) and bool(res.get("timeout")))
                if is_timeout:
                    _repro_docker_stop(container_name)
                # the code may have left links to host paths (or pipes) in its sandbox: nothing
                # the host does next, reading the capture file or a script, writing the next
                # runner, may follow or open them
                removed = _repro_docker_scrub(root)
                if removed:
                    _message(
                        "[repro/docker]   removed from the sandbox after '", fn, "': ",
                        ", ".join(removed[:10]), " ..." if len(removed) > 10 else "",
                    )  # fmt: skip
                captures = _read_captures(cap_file_host) if os.path.exists(cap_file_host) else None
                with contextlib.suppress(OSError):
                    os.unlink(cap_file_host)
                so, se = _read_cap(out_file), _read_cap(err_file)
            with contextlib.suppress(OSError):
                os.unlink(wrapper_path)
            _message(
                "[repro/docker]   <- '", fn, "' done in ", as_character(round(elapsed, 1)), "s"
            )

            if isinstance(res, Exception):
                msg = str(res)
                rows.append(
                    _run_row(
                        fn,
                        "timed_out" if is_timeout else "errored",
                        msg,
                        "timeout" if is_timeout else "runtime",
                        None,
                        so,
                        se,
                        elapsed,
                        exec_lines,
                        captures,
                    )
                )
                continue
            if res.get("timeout"):
                rows.append(
                    _run_row(
                        fn,
                        "timed_out",
                        f"timed out after {as_character(timeout)}s",
                        "timeout",
                        None,
                        so,
                        se,
                        elapsed,
                        exec_lines,
                        captures,
                    )
                )
                continue
            if res.get("status") != 0:
                undef_var, dep_unavailable = _classify_error(failed_deps, [se])
                etype = (
                    "dependency_unavailable"
                    if dep_unavailable
                    else "undefined_variable"
                    if undef_var is not None
                    else "runtime"
                )
                outc = "dependency_unavailable" if dep_unavailable else "errored"
                rows.append(
                    _run_row(fn, outc, se, etype, undef_var, so, se, elapsed, exec_lines, captures)
                )
                continue
            rows.append(
                _run_row(fn, "ran_ok", "", None, None, so, se, elapsed, exec_lines, captures)
            )
    finally:
        bar.terminate()
    return _run_frame(rows)
