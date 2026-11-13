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
import math
import os
import re
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

from metacheck._r.base import as_character, r_round, trimws
from metacheck._r.frames import bind_rows
from metacheck._r.regex import grepl, gsub
from metacheck._values import as_str
from metacheck.repro.core import (
    _NOT_STARTED,
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
    _repro_bioc_packages,
    _repro_classify_install_message,
    _repro_script_limit,
    _run_frame,
    _run_row,
    _script_lines,
    _setdiff,
)

__all__ = [
    "repro_docker_available",
    "repro_install_deps_docker",
    "repro_install_deps_py_docker",
    "repro_run_scripts_docker",
    "repro_run_scripts_py_docker",
]

#: The non-root uid:gid of R's backend (``.repro_docker_uid``). Here it is what a container runs
#: as unless :func:`_repro_docker_user` finds the host's own to be a better one.
_REPRO_DOCKER_UID = "1000:1000"

#: The pre-built metacheck R image (``.repro_docker_default_image``): the ~750 packages most
#: common in papers' R code, with JAGS, a JDK and the GDAL/GEOS/PROJ stack. The more the image
#: has, the less the install phase (the one with network) has to fetch. Built from
#: https://github.com/scienceverse/metacheck_docker_reproducibility, as the two below.
_REPRO_DOCKER_DEFAULT_IMAGE = "ghcr.io/scienceverse/metacheck_r_large:latest"

#: The pre-built metacheck Python image (``.repro_docker_default_image_py``): the ~45
#: packages most common in papers' Python code (numpy, pandas, scipy, matplotlib, seaborn,
#: scikit-learn, statsmodels, ...). ``metacheck_py_large`` adds torch, tensorflow and ~70
#: more, at ~8 GB.
_REPRO_DOCKER_DEFAULT_IMAGE_PY = "ghcr.io/scienceverse/metacheck_py_small:latest"


def _repro_docker_image_py(image: str | None = None) -> str:
    """The image the Python Docker runners use: *image*, else the option
    ``metacheck.docker_image_py`` (R's default argument), else the pre-built one."""
    if image is not None:
        return image
    from metacheck.utils import get_option

    return str(get_option("metacheck.docker_image_py", _REPRO_DOCKER_DEFAULT_IMAGE_PY))


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
    ``PATH``, ``docker info`` failing because the daemon is not running, or the daemon too
    busy to answer). A running Docker can be slow to answer while other containers are
    busy, so only a late answer earns another, longer try (15, 30, then 60 seconds; #560).
    """
    if not shutil.which("docker"):
        return {
            "ok": False,
            "msg": "the 'docker' command was not found on PATH. Install Docker Desktop "
            "(https://www.docker.com/products/docker-desktop/) and ensure it is running.",
        }
    res: dict[str, Any] | Exception = {}
    for wait in (15, 30, 60):
        res = _docker(["info"], timeout=wait)
        if isinstance(res, Exception) or not res.get("timeout"):
            break
    if isinstance(res, dict) and res.get("timeout"):
        return {
            "ok": False,
            "msg": "Docker did not answer within 60 seconds, after two shorter tries. It may "
            "be busy; try again when it has less to do.",
        }
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
    """Port of R/reproducibility_check_docker.R::.repro_docker_resource_args().

    ``--ulimit core=1`` always, so a crash of R in a container writes no core file (on
    Windows each crash wrote about 2.5 GB to the temp folder), then ``--cpus``/``--memory``
    from the ``metacheck.docker_resource_limits`` option.
    """
    from metacheck.utils import get_option

    no_crash_file = ["--ulimit", "core=1"]
    limits = get_option("metacheck.docker_resource_limits")
    if limits is None:
        return no_crash_file

    def get(key: str) -> list[str]:
        # as.character(limits$<key>): a missing element is character(0), NA is "NA"
        v = limits.get(key) if isinstance(limits, Mapping) else getattr(limits, key, None)
        if v is None:
            return []
        vals = list(v) if isinstance(v, list | tuple) else [v]
        return [as_str(x) or "NA" for x in vals]

    memory = get("memory_gb")
    # paste0(limits$memory_gb, "g"): NULL pastes as "g"
    return [
        *no_crash_file,
        "--cpus",
        *get("cpus"),
        "--memory",
        *([f"{m}g" for m in memory] or ["g"]),
    ]


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

    The pre-built metacheck image (the option ``metacheck.docker_image`` names another, to
    try one before it is published) unless *use_declared_version*; then
    ``rocker/r-ver:<the first cleanly-shaped declared R version>`` (or
    ``rocker/r-ver:latest``).
    """
    if not _r_is_true(use_declared_version):
        from metacheck.utils import get_option

        return str(get_option("metacheck.docker_image", _REPRO_DOCKER_DEFAULT_IMAGE))
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
    return _deparse1_c([lit(v) for v in vals])


def _deparse1_c(parts: Sequence[str]) -> str:
    """``c(...)`` of deparsed elements, as ``deparse1()`` writes it.

    ``deparse()`` starts a new line once a line is longer than ``width.cutoff`` (500
    bytes for ``deparse1()``), after the ``", "`` that ends an element, and ``deparse1()``
    joins the lines with a space: a long vector has ``",  "`` at each break.
    """
    lines: list[str] = []
    cur = "c("
    for i, p in enumerate(parts):
        if i > 0:
            cur += ", "
        if len(cur.encode("utf-8")) > 500:
            lines.append(cur)
            cur = ""
        cur += p
    lines.append(cur + ")")
    return " ".join(lines)


#: Where packages live that are on neither CRAN nor Bioconductor
#: (``.repro_known_package_places()``, #568 item 12): a GitHub owner/repository, or the
#: address of a repository of packages.
_REPRO_KNOWN_PACKAGE_PLACES: dict[str, str] = {
    "INLA": "https://inla.r-inla-download.org/R/stable",
    "NicheMapR": "mrke/NicheMapR",
    "glmmADMB": "bbolker/glmmadmb",
    "rethinking": "rmcelreath/rethinking",
    "cmdstanr": "stan-dev/cmdstanr",
    "TwGeos": "SLisovski/TwGeos",
    "GeoLocTools": "SLisovski/GeoLocTools",
    "SGAT": "SWotherspoon/SGAT",
    "microclima": "ilyamaclean/microclima",
    "V.PhyloMaker": "jinyizju/V.PhyloMaker",
    "metabaR": "metabaRfactory/metabaR",
    "calecopal": "an-bui/calecopal",
    "easyGgplot2": "kassambara/easyGgplot2",
    "ggthemr": "Mikata-Project/ggthemr",
    "BuenColors": "caleblareau/BuenColors",
    "coefplot2": "palday/coefplot2",
    "tess3r": "bcm-uga/TESS3_encho_sen",
    "PAMLr": "KiranLDA/PAMLr",
    "rcoalescence": "thompsonsed/rcoalescence",
    "datamodelr": "bergant/datamodelr",
    "biomonitoR": "alexology/biomonitoR",
    "scampr": "ElliotDovers/scampr",
    "mapppdr": "CCheCastaldo/mapppdr",
    "riojaPlot": "nsj3/riojaPlot",
    "PCAviz": "NovembreLab/PCAviz",
    "methClust": "kkdey/methClust",
    "remake": "richfitz/remake",
    "ascr": "b-steve/ascr",
    "dentist": "phylotastic/dentist",
    "Hmsc": "hmsc-r/HMSC",
    "HMSC": "hmsc-r/HMSC",
}

#: Lines for a script that runs without this package, to set where packages come from
#: (``.repro_repos_lines()``). The rocker/r-ver images of R before 4.0 name MRAN, which
#: closed in 2023, so nothing could be installed there; Posit keeps the same dated copies
#: of CRAN.
_REPOS_LINES = [
    'repos <- getOption("repos")',
    'if (is.null(repos) || !length(repos) || any(!nzchar(repos)) || any(repos == "@CRAN@")) {',
    '  options(repos = c(CRAN = "https://cloud.r-project.org"))',
    '} else if (any(grepl("mran.microsoft.com", repos, fixed = TRUE))) {',
    '  options(repos = c(CRAN = sub("^.*/snapshot/", "https://packagemanager.posit.co/cran/", repos[1])))',
    "}",
]


def _deparse1_named_chr(values: Mapping[str, str]) -> str:
    """``deparse1()`` of a named character vector whose names are syntactic."""
    return _deparse1_c([f"{k} = {_deparse1_chr([v])}" for k, v in values.items()])


_INSTALL_LOOP = [
    'lib <- "/rlib"',
    "dir.create(lib, recursive = TRUE, showWarnings = FALSE)",
    ".libPaths(c(lib, .libPaths()))",
    *_REPOS_LINES,
    "pkgs   <- .repro_docker_pkgs",
    "srcs   <- .repro_docker_srcs",
    "refs   <- .repro_docker_refs",
    "vers   <- .repro_docker_vers",
    # A bare rocker/r-ver image does not have remotes, and without it no
    # recorded version and no GitHub package can be installed.
    'gh_avail <- requireNamespace("remotes", quietly = TRUE)',
    'if (!gh_avail && (any(!is.na(vers)) || any(srcs == "github"))) {',
    '  try(install.packages("remotes", lib = lib, quiet = TRUE))',
    '  gh_avail <- requireNamespace("remotes", quietly = TRUE)',
    "}",
    # The same rules as .repro_installed_version() and .repro_same_version()
    # (R/reproducibility_check.R), inlined because this script runs without
    # this package.
    "have_version <- function(pkg) {",
    '  v <- suppressWarnings(tryCatch(utils::packageDescription(pkg, lib.loc = .libPaths(), fields = "Version"),',
    "                                 error = function(e) NA_character_))",
    "  if (length(v) != 1 || is.na(v)) NA_character_ else as.character(v)",
    "}",
    "same_version <- function(a, b) {",
    "  if (is.na(a) || is.na(b)) return(FALSE)",
    "  isTRUE(tryCatch(package_version(a) == package_version(b), error = function(e) FALSE))",
    "}",
    # The same rule as .repro_load_error() (R/reproducibility_check.R): a bare
    # image often lacks a system library, which only this message tells.
    "load_error <- function(pkg) {",
    '  msg <- tryCatch({ loadNamespace(pkg); "" }, error = function(e) conditionMessage(e))',
    '  substr(gsub("[[:space:]]+", " ", msg), 1, 300)',
    "}",
    "out <- list()",
    "for (i in seq_along(pkgs)) {",
    "  pkg <- pkgs[i]; src <- srcs[i]; ref <- refs[i]; ver <- vers[i]",
    # Read by the host while this runs, for the progress display.
    '  writeLines(as.character(i), "/sandbox/.install_status")',
    "  warns <- character(0)",
    '  note <- ""',
    "  version_done <- same_version(have_version(pkg), ver)",
    '  if (!version_done && !is.na(ver) && identical(src, "bioc")) {',
    '    note <- "Bioconductor versions follow the R version, so the current version was installed"',
    '  } else if (!version_done && !is.na(ver) && identical(src, "cran")) {',
    "    if (gh_avail) {",
    "      version_done <- tryCatch({",
    '        remotes::install_version(pkg, version = ver, lib = lib, upgrade = "never",',
    "                                 dependencies = NA, force = TRUE, quiet = FALSE)",
    "        same_version(have_version(pkg), ver)",
    "      }, error = function(e) { note <<- conditionMessage(e); FALSE })",
    '      if (!version_done && !nzchar(note)) note <- paste0("the recorded version could not be installed on R ", getRversion())',
    '    } else note <- "the remotes package is not available in this image"',
    "  }",
    "  res <- tryCatch({",
    "    withCallingHandlers({",
    "    if (version_done) {",
    '    } else if (identical(src, "github")) {',
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
    # Same rule as .repro_not_loadable_msg() (R/reproducibility_check.R),
    # inlined because this script runs without this package: a package
    # install.packages() could not find only produces a "not available"
    # warning, which is the real reason (issue #421).
    # A recorded version already in the image is not in lib. Being in lib is
    # checked as well, since the load check also passes for a namespace an
    # earlier package loaded, and a failed install_github() does not stop. A
    # package that is gone from CRAN may still be in the image.
    '    in_lib <- file.exists(file.path(lib, pkg, "DESCRIPTION"))',
    '    in_image <- !in_lib && identical(src, "cran") && is.na(ver) && !is.na(have_version(pkg))',
    "    if (!(version_done || in_lib || in_image) ||",
    "        !requireNamespace(pkg, quietly = TRUE, lib.loc = if (in_lib && !version_done) lib else .libPaths())) {",
    '      na <- unique(grep("is not available|non-zero exit status", warns, value = TRUE))',
    '      stop(if (length(na) > 0) paste(na, collapse = "; ")',
    '           else if (!(version_done || in_lib)) "the install did not put the package in the library"',
    '           else paste0("installed but package is not loadable: ", load_error(pkg)))',
    "    }",
    '    list(ok = TRUE, msg = "")',
    "  }, error = function(e) list(ok = FALSE, msg = conditionMessage(e)))",
    # An install from GitHub or a web address that failed, for a package that is on CRAN
    # or Bioconductor: installed from there, with a note (metacheck #598). A failed
    # download says so. The same rules as repro_install_deps().
    '  if (!res$ok && src %in% c("github", "url")) {',
    '    dl <- grep("cannot open URL|cannot open the connection|download|status was|Could not resolve host|HTTP error|HTTP status|404|403|Failed to connect", c(warns, res$msg), value = TRUE, ignore.case = TRUE)',
    '    first <- if (length(dl)) paste0("could not download ", ref, ": ", substr(gsub("[[:space:]]+", " ", dl[1]), 1, 200)) else res$msg',
    "    cran_has <- tryCatch(pkg %in% rownames(utils::available.packages()), error = function(e) FALSE)",
    '    alt <- if (cran_has) "CRAN" else if (isTRUE(.repro_docker_bioc[i])) "Bioconductor" else NA_character_',
    "    if (!is.na(alt)) {",
    '      try(if (alt == "CRAN") install.packages(pkg, lib = lib, quiet = FALSE) else {',
    '        if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager", lib = lib, quiet = FALSE)',
    "        BiocManager::install(pkg, lib = lib, update = FALSE, ask = FALSE) })",
    '      if (file.exists(file.path(lib, pkg, "DESCRIPTION")) && requireNamespace(pkg, quietly = TRUE, lib.loc = lib)) {',
    '        res <- list(ok = TRUE, msg = "")',
    '        note <- paste0("installed from ", alt, ": the install from ", ref, " failed (", substr(first, 1, 200), ")")',
    '      } else res$msg <- paste0(first, "; installing it from ", alt, " failed too")',
    "    } else res$msg <- first",
    "  }",
    "  v <- have_version(pkg)",
    "  if (res$ok && !is.na(ver) && !same_version(v, ver) && !nzchar(note))",
    '    note <- "the source gave another version than the recorded one"',
    # stringsAsFactors: the default is TRUE before R 4.0, and the image may be that old
    "  out[[i]] <- data.frame(package = pkg, source = src, installed = res$ok, message = res$msg,",
    "                         version_installed = v, version_note = note, via_archive = FALSE, stringsAsFactors = FALSE)",
    '  saveRDS(do.call(rbind, out), "/sandbox/.install_results.rds")',
    "}",
]

#: The part of the install script for packages that are not on CRAN
#: (``.repro_docker_elsewhere_lines()``): a second pass after every package has had its
#: turn, since one time limit covers the whole install. First Bioconductor, then the
#: latest version in the CRAN Archive (two rounds, because a package can need another
#: archived one), then a table of known places and R-universe (#568). Runs inside the
#: container, without this package.
_ELSEWHERE_SCRIPT = [
    # This R treats a warning old packages cause as an error, and has dropped
    # Calloc() and the like, so maptools did not compile. Only for this pass.
    'makevars <- file.path(tempdir(), "Makevars")',
    'writeLines(c("CFLAGS += -Wno-error=format-security -DCalloc=R_Calloc -DRealloc=R_Realloc -DFree=R_Free", paste0(c("CXXFLAGS", "CXX11FLAGS", "CXX14FLAGS", "CXX17FLAGS", "CXX20FLAGS"), " += -Wno-error=format-security")), makevars)',
    'Sys.setenv(R_MAKEVARS_USER = makevars, R_LIBS = paste(.libPaths(), collapse = ":"))',
    'retry_rows <- function() which(vapply(out, function(x) !x$installed && identical(x$source, "cran") && grepl("is not available|there is no package called", x$message), logical(1)))',
    # What an install here says, kept: an install that gives only warnings
    # returned as if it had worked, and INLA, from its own repository, failed
    # without a word in 10.1111/1365-2435.14173 (#568)
    "last_log <- character(0)",
    'logged <- function(expr) withCallingHandlers(expr, warning = function(w) { last_log <<- c(last_log, conditionMessage(w)); invokeRestart("muffleWarning") }, error = function(e) last_log <<- c(last_log, conditionMessage(e)))',
    "bioc_known <- NULL",
    "in_elsewhere <- function(pkg, depth = 1) {",
    "  if (is.null(bioc_known)) bioc_known <<- tryCatch({",
    '    if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager", lib = lib, quiet = TRUE)',
    "    rownames(utils::available.packages(repos = BiocManager::repositories()))",
    "  }, error = function(e) character(0))",
    # A package on CRAN can need one from Bioconductor, which install.packages()
    # does not find: speaq needs MassSpecWavelet.
    '  first_msg <- if (pkg %in% pkgs) paste(out[[match(pkg, pkgs)]]$message) else ""',
    '  bioc_needed <- regmatches(first_msg, regexpr("(?<=there is no package called .)[A-Za-z0-9.]+", first_msg, perl = TRUE))',
    "  if (length(bioc_needed) && bioc_needed %in% bioc_known) {",
    "    BiocManager::install(bioc_needed, lib = lib, update = FALSE, ask = FALSE)",
    '    return("bioc")',
    "  }",
    "  if (pkg %in% bioc_known) {",
    "    BiocManager::install(pkg, lib = lib, update = FALSE, ask = FALSE)",
    '    return("bioc")',
    "  }",
    '  listing <- suppressWarnings(tryCatch(readLines(paste0("https://cran.r-project.org/src/contrib/Archive/", pkg, "/"), warn = FALSE), error = function(e) character(0)))',
    # rgdal and rgeos compile against sp's header files, which sp dropped in
    # version 2.2 after they left CRAN; 2.1-4 is the last version that has them
    # (#568). The run then loads this sp, from the temporary library.
    '  if (pkg %in% c("rgdal", "rgeos") && !file.exists(file.path(lib, "sp", "include", "sp.h"))) {',
    '    sp_src <- file.path(tempdir(), "sp_2.1-4.tar.gz")',
    '    try(utils::download.file("https://cran.r-project.org/src/contrib/Archive/sp/sp_2.1-4.tar.gz", sp_src, quiet = TRUE), silent = TRUE)',
    '    if (file.exists(sp_src)) try(install.packages(sp_src, lib = lib, repos = NULL, type = "source", quiet = TRUE), silent = TRUE)',
    "  }",
    '  hits <- grep(paste0(pkg, "_[0-9][^\\"\']*\\\\.tar\\\\.gz"), listing, value = TRUE)',
    "  if (!length(hits)) return(NA_character_)",
    '  dates <- sub(".*(\\\\d{4}-\\\\d{2}-\\\\d{2} \\\\d{2}:\\\\d{2}).*", "\\\\1", hits)',
    "  latest <- hits[order(dates, decreasing = TRUE)][1]",
    '  tarball <- regmatches(latest, regexpr(paste0(pkg, "_[0-9][^\\"\']*\\\\.tar\\\\.gz"), latest))',
    '  if (!requireNamespace("remotes", quietly = TRUE)) install.packages("remotes", lib = lib, quiet = TRUE)',
    '  archive_url <- paste0("https://cran.r-project.org/src/contrib/Archive/", pkg, "/", tarball)',
    # A dependency that has left CRAN too is installed from the Archive first: the
    # install takes dependencies from CRAN only, so ggsn, which needs maptools,
    # stopped (10.1111/1365-2435.14195; #568). Read from the package's DESCRIPTION.
    "  if (depth < 3) tryCatch({",
    "    tb <- file.path(tempdir(), tarball)",
    "    utils::download.file(archive_url, tb, quiet = TRUE)",
    '    ex <- file.path(tempdir(), paste0("desc_", pkg))',
    '    utils::untar(tb, files = paste0(pkg, "/DESCRIPTION"), exdir = ex)',
    '    dcf <- read.dcf(file.path(ex, pkg, "DESCRIPTION"))',
    '    fields <- intersect(c("Depends", "Imports", "LinkingTo"), colnames(dcf))',
    '    needs <- trimws(sub("\\\\(.*", "", unlist(strsplit(paste(dcf[1, fields], collapse = ","), ","))))',
    '    needs <- setdiff(needs[nzchar(needs)], c("R", rownames(utils::installed.packages(lib.loc = .libPaths())), rownames(utils::available.packages())))',
    "    for (d in needs) tryCatch(in_elsewhere(d, depth + 1), error = function(e) NULL)",
    "  }, error = function(e) NULL)",
    '  logged(remotes::install_url(archive_url, lib = lib, upgrade = "never", dependencies = TRUE, quiet = TRUE))',
    '  "archive"',
    "}",
    # Packages only elsewhere (#568, item 12): a table of known places, then
    # R-universe's search for that exact name. A GitHub tarball needs no token.
    "known_places <- " + _deparse1_named_chr(_REPRO_KNOWN_PACKAGE_PLACES),
    "in_known_place <- function(pkg) {",
    "  place <- unname(known_places[pkg])",
    '  if (!is.na(place) && startsWith(place, "https://")) {',
    '    logged(install.packages(pkg, lib = lib, repos = c(place, getOption("repos")), quiet = TRUE))',
    "    return(place)",
    "  }",
    "  if (!is.na(place)) {",
    '    if (!requireNamespace("remotes", quietly = TRUE)) install.packages("remotes", lib = lib, quiet = TRUE)',
    '    logged(remotes::install_url(paste0("https://github.com/", place, "/archive/HEAD.tar.gz"), lib = lib, upgrade = "never", dependencies = TRUE, quiet = TRUE))',
    '    return(paste0("github.com/", place))',
    "  }",
    # a search that fails is not a package R-universe lacks (#389); a bare
    # rocker/r-ver image has no jsonlite, so the search always failed there
    '  if (!requireNamespace("jsonlite", quietly = TRUE)) install.packages("jsonlite", lib = lib, quiet = TRUE)',
    '  found <- tryCatch(jsonlite::fromJSON(paste0("https://r-universe.dev/api/search?q=", pkg, "&limit=20"))$results, error = function(e) e)',
    '  if (inherits(found, "error")) return(paste("the search of R-universe failed:", conditionMessage(found)))',
    "  if (is.null(found) || !NROW(found)) return(NA_character_)",
    '  who <- found[["_user"]][found$Package == pkg]',
    "  if (!length(who)) return(NA_character_)",
    '  repo <- paste0("https://", who[1], ".r-universe.dev")',
    '  logged(install.packages(pkg, lib = lib, repos = c(repo, getOption("repos")), quiet = TRUE))',
    "  repo",
    "}",
    "for (round in 1:2) for (i in retry_rows()) {",
    "  pkg <- pkgs[i]",
    '  writeLines(as.character(i), "/sandbox/.install_status")',
    "  last_log <- character(0)",
    "  from <- tryCatch(in_elsewhere(pkg), error = function(e) conditionMessage(e))",
    '  usable <- function() file.exists(file.path(lib, pkg, "DESCRIPTION")) && requireNamespace(pkg, quietly = TRUE, lib.loc = lib)',
    # what stopped it is kept, next to the first pass's "not available"
    "  tried <- from",
    "  if (!usable()) from <- tryCatch(in_known_place(pkg), error = function(e) conditionMessage(e))",
    '  stopped <- c(tried, from)[!is.na(c(tried, from)) & !c(tried, from) %in% c("bioc", "archive") & !grepl("^https://|^github", c(tried, from))]',
    '  if (!usable() && round == 2 && length(stopped)) out[[i]]$message <- paste0(out[[i]]$message, "; also tried elsewhere: ", substr(stopped[1], 1, 300))',
    '  if (!usable() && round == 2 && !length(stopped) && length(last_log)) out[[i]]$message <- paste0(out[[i]]$message, "; installing it from ", c(from, tried)[!is.na(c(from, tried))][1], " failed: ", substr(paste(unique(last_log), collapse = " | "), 1, 400))',
    # Found in none of the places: the code does not say where the package comes
    # from, which authors can act on (#389). See .repro_classify_install_message().
    '  if (!usable() && round == 2 && is.na(tried) && is.na(from)) out[[i]]$message <- paste0(out[[i]]$message, "; found nowhere else: not on Bioconductor, in the CRAN Archive, in our list of known places or on R-universe")',
    "  if (usable()) {",
    "    out[[i]]$installed <- TRUE",
    '    out[[i]]$message <- ""',
    "    out[[i]]$version_installed <- have_version(pkg)",
    '    out[[i]]$via_archive <- identical(from, "archive")',
    '    if (identical(from, "bioc")) out[[i]]$source <- "bioc"',
    '    if (!from %in% c("bioc", "archive")) { out[[i]]$source <- "elsewhere"; out[[i]]$version_note <- paste0("installed from ", from) }',
    "  }",
    '  saveRDS(do.call(rbind, out), "/sandbox/.install_results.rds")',
    "}",
]


def _install_script(install_deps: pd.DataFrame) -> list[str]:
    """The ``install.R`` a :func:`repro_install_deps_docker` container runs.

    Each package is installed in turn (a recorded version through
    ``remotes::install_version()``), its result written back after each one; then the
    packages that were not on CRAN are looked for elsewhere.
    """
    n = len(install_deps)
    bioc = set(_repro_bioc_packages())
    refs = _chr_list(install_deps["ref"]) if "ref" in install_deps.columns else None
    versions = (
        _chr_list(install_deps["version"]) if "version" in install_deps.columns else [None] * n
    )
    header = [
        ".repro_docker_pkgs <- " + _deparse1_chr(_chr_list(install_deps["package"])),
        ".repro_docker_srcs <- " + _deparse1_chr(_chr_list(install_deps["source"])),
        ".repro_docker_refs <- " + _deparse1_chr(refs),
        ".repro_docker_vers <- " + _deparse1_chr(versions),
        # whether each package is a Bioconductor one, for a failed GitHub or URL install
        ".repro_docker_bioc <- c("
        + ", ".join("TRUE" if p in bioc else "FALSE" for p in _chr_list(install_deps["package"]))
        + ")",
    ]
    return header + _INSTALL_LOOP + _ELSEWHERE_SCRIPT


def _repro_install_error_lines(txt: str | None, pkg: str | None) -> str:
    """Port of R/reproducibility_check_docker.R::.repro_install_error_lines().

    The lines of an install's output that say why *pkg* did not install: an ``ERROR:``
    line that names it ("ERROR: dependencies 'rgdal', 'rgeos' are not available for
    package 'ellipsenm'"), and the compiler's errors in its own part of the output, from
    "installing *source* package 'pkg'" to the next package. ``""`` when none (#568).
    """
    lines = (txt or "").split("\n")
    if txt is None or txt == "" or not pkg:
        return ""
    q = re.escape(pkg)
    named = [
        ln for ln in lines if re.search("^ERROR: .*package ['\u2018\"]" + q + "['\u2019\"]", ln)
    ]
    start = [
        i
        for i, ln in enumerate(lines)
        if re.search("installing \\*source\\* package ['\u2018\"]" + q + "['\u2019\"]", ln)
    ]
    own: list[str] = []
    if start:
        s0 = start[-1]
        nxt = [i for i, ln in enumerate(lines) if re.search(r"^\* (installing|DONE)", ln)]
        e = ([i for i in nxt if i > s0] + [len(lines)])[0]
        own = [
            ln
            for ln in lines[s0:e]
            if re.search(r"fatal error|error: ", ln) and not re.search(r"^ERROR: ", ln)
        ]
    out = _unique_strs([trimws(x) for x in named + own])
    if not out:
        return ""
    return " | ".join(out[:4])[:500]


def _unique_strs(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


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
    for col in ("version_installed", "version_note"):
        if col in df.columns:
            out[col] = pd.Series(_chr_list(df[col]), dtype="string")
    if "via_archive" in df.columns:
        out["via_archive"] = pd.Series(
            [as_str(v) is not None and bool(v) for v in df["via_archive"]], dtype="boolean"
        )
    return out


def _read_regular_head(path: str, n: int) -> bytes | None:
    """The first *n* bytes of *path* when it is a regular file, never read through a link
    (``None`` otherwise). Where ``os.open()`` has no ``O_NOFOLLOW`` (Windows), a link is
    refused by its ``lstat()``."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow and os.path.islink(path):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, n)
    finally:
        os.close(fd)


def _read_install_status(path: str) -> int | None:
    """The number the install container left in ``.install_status``: a small regular file,
    never read through a link."""
    head = _read_regular_head(path, 32)
    if head is None:
        return None
    first = head.decode("ascii", "replace").split("\n")[0].strip()
    return int(first) if first.isdigit() else None


def _repro_docker_run_install(
    args: Sequence[str],
    timeout: float,
    out_file: str,
    status_file: str,
    pkgs: Sequence[str | None],
    versions: Sequence[str | None],
) -> dict[str, Any] | Exception:
    """Run the install container and show what it is doing (``.repro_docker_run_install()``).

    The container writes the number of the package it has started to *status_file*; while
    :func:`_docker` runs the container, a thread reads that number every second and shows
    the package, the count and the time so far
    (:func:`~metacheck.repro.core._repro_install_what`; #568). The output, with the error
    output, goes to *out_file*. Returns :func:`_docker`'s result.
    """
    import threading

    from metacheck.repro.core import _repro_install_what
    from metacheck.utils import pb

    n = len(pkgs)
    bar = pb(None, "(:spin) :what")
    started = time.monotonic()
    done = threading.Event()

    def watch() -> None:
        current = 0
        while not done.wait(1.0):
            i = _read_install_status(status_file)
            if i is None or not 1 <= i <= n:
                continue
            what = _repro_install_what(i, n, pkgs[i - 1], versions[i - 1], started)
            if i != current:
                _message("[repro/docker]   ", what)
            bar.tick(max(0, i - current), {"what": what})
            current = i

    watcher = threading.Thread(target=watch, name="repro-install-status", daemon=True)
    watcher.start()
    try:
        return _docker(args, timeout=timeout, stdout=out_file, merge_stderr=True)
    finally:
        done.set()
        watcher.join(5)
        bar.terminate()


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
        n = len(install_deps)
        versions = (
            _chr_list(install_deps["version"]) if "version" in install_deps.columns else [None] * n
        )
        raw = _repro_docker_run_install(
            args,
            timeout,
            out_file,
            os.path.join(sandbox_dir, ".install_status"),
            _chr_list(install_deps["package"]),
            versions,
        )
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
            # A failed install whose message gives no reason, only "had non-zero exit
            # status", gets the reason from the install's own output (#568).
            if tbl is not None and len(tbl) and txt:
                vague_pat = "ERROR: |error: |not available|there is no package|cannot "
                msgs = _chr_list(tbl["message"])
                inst = _col(tbl, "installed")
                for i, (ok, m) in enumerate(zip(inst, msgs, strict=True)):
                    if ok or grepl(vague_pat, m or ""):
                        continue
                    why = _repro_install_error_lines(txt, as_str(tbl["package"].iloc[i]))
                    if why:
                        tbl.loc[tbl.index[i], "message"] = (m or "") + ("; " if m else "") + why
            if res.get("timeout"):
                msg = f"docker install timed out after {as_character(r_round(timeout))}s"
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
    vers = _chr_list(install_deps["version"]) if "version" in install_deps.columns else None
    ver_of: dict[str | None, str | None] = {}
    for p, v in zip(pkgs, vers or [None] * len(pkgs), strict=True):
        ver_of.setdefault(p, v)
    out["version_recorded"] = pd.Series(
        [ver_of.get(p) for p in _col(out, "package")], dtype="string"
    )
    if "version_installed" not in out.columns:
        out["version_installed"] = pd.Series([None] * len(out), dtype="string")
    notes = _col(out, "version_note") if "version_note" in out.columns else [None] * len(out)
    out["version_note"] = pd.Series([n or "" for n in notes], dtype="string")
    archived = _col(out, "via_archive") if "via_archive" in out.columns else [None] * len(out)
    out["via_archive"] = pd.Series([a is True for a in archived], dtype="boolean")
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


#: What the per-script runner sets up before the script runs (metacheck 0.4.3.7). The
#: container is read-only apart from ``/tmp``, its home folder too, so a package that keeps
#: files in the user's cache, configuration or data folder stopped: ``renv::restore()`` with
#: "failed to create directory at path '/home/rcheck/.cache/R/renv'" (#523). Those folders
#: are put in ``/tmp``. ``rmarkdown::render()`` looks for pandoc, which the image has only
#: inside Quarto, and ``RSTUDIO_PANDOC`` points it there (#523). Plots without a screen go to
#: R's pdf device, which stops on a font it does not know (``theme_ipsum()`` asks for "Arial
#: Narrow"); Cairo's device takes any font name (#568). The stand-ins (``View()``,
#: ``windowsFonts()``, the file choosers) are in an attached environment of their own, not the
#: workspace, so a script that starts with ``rm(list = ls())`` keeps them (#568).
_WRAPPER_ENV_SETUP = [
    '  Sys.setenv(XDG_CACHE_HOME = "/tmp/.cache", XDG_CONFIG_HOME = "/tmp/.config",',
    '             XDG_DATA_HOME = "/tmp/.local/share", R_USER_CACHE_DIR = "/tmp/.cache",',
    '             R_USER_CONFIG_DIR = "/tmp/.config", R_USER_DATA_DIR = "/tmp/.local/share")',
    '  quarto <- Sys.which("quarto")',
    '  if (nzchar(quarto) && !nzchar(Sys.getenv("RSTUDIO_PANDOC"))) {',
    '    tools <- file.path(dirname(normalizePath(quarto)), "tools")',
    '    pandoc <- c(file.path(tools, "pandoc"), Sys.glob(file.path(tools, "*", "pandoc")))',
    "    pandoc <- pandoc[file.exists(pandoc)]",
    "    if (length(pandoc)) Sys.setenv(RSTUDIO_PANDOC = dirname(pandoc[1]))",
    "  }",
    "  options(knitr.graphics.error = FALSE)",
    '  if (isTRUE(capabilities("cairo"))) options(device = function(...) '
    'grDevices::cairo_pdf("Rplots.pdf", onefile = TRUE))',
    "  stand_ins <- new.env()",
    '  assign("View", function(...) invisible(NULL), envir = stand_ins)',
    '  assign("windowsFonts", function(...) invisible(NULL), envir = stand_ins)',
    '  assign("windowsFont", function(...) "sans", envir = stand_ins)',
    '  for (ask in c("file.choose", "choose.files", "choose.dir", "tk_choose.files", '
    '"tk_choose.dir"))',
    '    assign(ask, function(...) stop("file choice cancelled", call. = FALSE), '
    "envir = stand_ins)",
    '  attach(stand_ins, name = "metacheck:stand_ins", warn.conflicts = FALSE)',
]


def _wrapper_lines(
    preamble: list[str], container_script: str, lib: bool, cap_file_container: str
) -> list[str]:
    """The per-script runner the container executes (``.repro_docker_run()``).

    With a library, ``/rlib`` goes first on ``.libPaths()`` and into ``R_LIBS``, so the R
    processes a script starts find the packages too: ``gbm()``'s cross-validation workers
    stopped with "there is no package called 'gbm'" (#568). The echo of each expression goes
    past a ``sink()`` the script opened, to the real stdout: a model written with
    ``sink("m.jags"); cat("model {...}"); sink()`` got the echo lines, and JAGS stopped on
    "syntax error on line 1 near '>'" (#568).
    """
    lib_setup = [
        '  .libPaths(c("/rlib", .libPaths()))',
        '  Sys.setenv(R_LIBS = paste(.libPaths(), collapse = ":"))',
    ]
    lines = [
        *preamble,
        ".repro_docker_run <- function() {",
        *_WRAPPER_ENV_SETUP,
        *(lib_setup if lib else []),
        "  captures <- list()",
        f'  on.exit(try(.mc_write_json(captures, "{cap_file_container}"), silent = TRUE), '
        "add = TRUE)",
        f'  exprs <- parse("{container_script}", keep.source = TRUE)',
        '  srcrefs <- attr(exprs, "srcref")',
        "  env <- globalenv()",
        "  echo_con <- NULL",
        "  for (i in seq_along(exprs)) {",
        "    e <- exprs[[i]]",
        "    sr <- if (!is.null(srcrefs) && length(srcrefs) >= i) srcrefs[[i]] else NULL",
        "    txt <- if (!is.null(sr)) as.character(sr) else deparse(e)",
        '    echo <- paste0(paste0("> ", txt, collapse = "\\n"), "\\n")',
        "    if (sink.number() > 0) {",
        "      if (is.null(echo_con)) "
        'echo_con <- suppressWarnings(file("/dev/stdout", "w", raw = TRUE))',
        "      cat(echo, file = echo_con); flush(echo_con)",
        "    } else cat(echo)",
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


def _repro_docker_workdir(run_dir: str | None, sandbox_root: str) -> str:
    """The container's working directory for a script's *run_dir* (``.repro_docker_workdir()``).

    ``/sandbox`` itself, also when *run_dir* is missing, or the folder below it.
    """
    if run_dir is None or run_dir == "":
        return "/sandbox"
    if _normalize_path(run_dir) == _normalize_path(sandbox_root):
        return "/sandbox"
    return _repro_docker_container_path(run_dir, sandbox_root)


def _repro_docker_network_args(net: Mapping[str, Any] | None) -> list[str]:
    """``docker run`` arguments for the network: off, unless the internet is on (*net*)."""
    return ["--network", "none"] if net is None else []


def _repro_docker_counted_cmd(cmd: Sequence[str], net: Mapping[str, Any] | None) -> list[str]:
    """The command a container runs (``.repro_docker_counted_cmd()``).

    With the internet on it first marks its start in ``/tmp``, so a file is known to be new,
    and at the end writes what it received to the sandbox, so a script that ends between two
    checks is still counted. The command's own exit status is kept.
    """
    if net is None:
        return list(cmd)
    return [
        "sh", "-c",
        'touch /tmp/.repro_t0; "$@"; s=$?; '
        "cat /sys/class/net/eth0/statistics/rx_bytes > /sandbox/.repro_received 2>/dev/null; "
        "exit $s",
        "sh", *cmd,
    ]  # fmt: skip


def _repro_docker_received(name: str, max_file: float = math.inf) -> dict[str, Any]:
    """What a running container has received over the network (``.repro_docker_received()``).

    ``{"bytes", "file", "file_bytes"}``: the bytes received and, once that is at least
    *max_file*, the first file of at least that size that appeared in the sandbox or ``/tmp``
    since the script started. ``None`` for what the container could not be asked.
    """
    find = (
        f'; [ "$r" -ge {max_file:.0f} ] && find /sandbox /tmp -type f -newer /tmp/.repro_t0 '
        f'-size +{math.floor(max_file / 1024):.0f}k -printf "%s %p\\n" 2>/dev/null | head -n 1'
        if math.isfinite(max_file)
        else ""
    )
    res = _docker(
        [
            "exec", name, "sh", "-c",
            "r=$(cat /sys/class/net/eth0/statistics/rx_bytes); echo $r" + find,
        ],
        timeout=20,
    )  # fmt: skip
    text = "" if isinstance(res, Exception) else str(res.get("stdout") or "")
    out = str(trimws(text)).split("\n") if trimws(text) != "" else []
    nbytes: float | None = None
    if out:
        with contextlib.suppress(ValueError):
            nbytes = float(out[0])
    big = re.match(r"^(\d+) (.+)$", out[1]) if len(out) > 1 else None
    return {
        "bytes": nbytes if nbytes is not None and not math.isnan(nbytes) else None,
        "file": big.group(2) if big else None,
        "file_bytes": float(big.group(1)) if big else None,
    }


def _docker_start(args: Sequence[str], *, stdout: str, stderr: str) -> Any:
    """``processx::process$new("docker", args, stdout = , stderr = )``: a started ``docker``.

    Returns the :class:`subprocess.Popen`, or the exception when ``docker`` could not be
    started. Its output goes to the files, which the caller reads when it has ended.
    """
    exe = shutil.which("docker") or "docker"
    try:
        with open(stdout, "wb") as out_f, open(stderr, "wb") as err_f:
            return subprocess.Popen(  # noqa: S603 - the docker CLI
                [exe, *args], stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL
            )
    except OSError as exc:
        return exc


def _wait(proc: Any, secs: float) -> bool:
    """Wait at most *secs* for *proc*; whether it has ended."""
    try:
        proc.wait(timeout=secs)
    except subprocess.TimeoutExpired:
        return False
    return True


def _read_received(path: str) -> float | None:
    """The count the container left in ``.repro_received``: a small regular file, never a link."""
    head = _read_regular_head(path, 64)
    if head is None:
        return None
    first = head.decode("ascii", "replace").split("\n")[0]
    try:
        value = float(first)
    except ValueError:
        return None
    return None if math.isnan(value) else value


def _repro_docker_run_watched(
    args: Sequence[str],
    name: str,
    timeout: float,
    stdout: str,
    stderr: str,
    sandbox_root: str,
    net: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a ``docker run`` and wait for it (``.repro_docker_run_watched()``).

    With the internet off (*net* ``None``) this is :func:`_docker`. With it on, the
    container is checked every two seconds, and stopped when the paper's scripts together
    have downloaded more than ``net["max_total"]`` bytes, or a file larger than
    ``net["max_file"]`` has appeared (#568), as the repository's own files are limited. A
    download that ends between two checks is still counted towards the total
    (``net["used"]["bytes"]``, changed in place), so a later script is stopped.
    ``arriving`` says whether data was still coming in when the time ran out, so a slow
    download is named as the reason. Returns ``{"res", "stopped", "received",
    "arriving"}``; ``res`` is :func:`_docker`'s result (or the exception), ``stopped`` the
    reason the watcher stopped the container, else ``None``.
    """
    from metacheck.repro.outcomes import _repro_mb

    if net is None:
        ran = _docker(args, timeout=timeout, stdout=stdout, stderr=stderr)
        return {"res": ran, "stopped": None, "received": 0.0, "arriving": False}

    received_file = os.path.join(sandbox_root, ".repro_received")
    with contextlib.suppress(OSError):
        os.unlink(received_file)
    proc = _docker_start(args, stdout=stdout, stderr=stderr)
    if isinstance(proc, Exception):
        return {"res": proc, "stopped": None, "received": 0.0, "arriving": False}
    used = net["used"]
    before = float(used.get("bytes") or 0)
    max_total = float(net.get("max_total", math.inf))
    max_file = float(net.get("max_file", math.inf))
    rx = rx_prev = 0.0
    stopped: str | None = None
    timed_out = False
    t0 = time.monotonic()
    while True:
        left = timeout - (time.monotonic() - t0)
        if left <= 0:
            timed_out = True
            break
        if _wait(proc, math.ceil(min(2.0, left) * 1000) / 1000):
            break
        seen = _repro_docker_received(name, max_file)
        if seen["bytes"] is None:
            continue
        rx_prev, rx = rx, float(seen["bytes"])
        if before + rx > max_total:
            stopped = (
                f"stopped: the paper's scripts downloaded {_repro_mb(before + rx)} MB, more "
                f"than the limit of {_repro_mb(max_total)} MB (max_download_size)"
            )
            break
        if seen["file"] is not None:
            shown = re.sub(r"^/sandbox/", "", str(seen["file"]))
            stopped = (
                f"stopped: it downloaded {shown}, a file of {_repro_mb(seen['file_bytes'])} MB, "
                f"more than the limit of {_repro_mb(max_file)} MB per file (max_file_size)"
            )
            break
    if timed_out or stopped is not None:
        _repro_docker_stop(name)
        if not _wait(proc, 10):
            with contextlib.suppress(OSError):
                proc.kill()
            _wait(proc, 10)
    final = _read_received(received_file)
    if final is not None:
        rx = max(rx, final)
    with contextlib.suppress(OSError):
        os.unlink(received_file)
    used["bytes"] = before + rx
    res: dict[str, Any] = (
        {"status": None, "timeout": True, "stdout": ""}
        if timed_out
        else {"status": proc.poll(), "timeout": False, "stdout": ""}
    )
    # more than 64 KB since the check before: data was still coming in
    return {
        "res": res,
        "stopped": stopped,
        "received": rx,
        "arriving": timed_out and rx - rx_prev > 64 * 1024,
    }


def _repro_docker_memory_gb() -> float | None:
    """The memory limit of each container, in GB (``metacheck.docker_resource_limits``)."""
    from metacheck.utils import get_option

    limits = get_option("metacheck.docker_resource_limits")
    if limits is None:
        return None
    gb = (
        limits.get("memory_gb")
        if isinstance(limits, Mapping)
        else getattr(limits, "memory_gb", None)
    )
    if isinstance(gb, list | tuple):
        gb = gb[0] if gb else None
    try:
        return None if gb is None else float(gb)
    except (TypeError, ValueError):
        return None


def _repro_docker_oom_note(status: Any) -> str:
    """:func:`~metacheck.repro.outcomes._repro_oom_text` for this run's limit, ``""`` for none."""
    from metacheck.repro.outcomes import _repro_oom_text

    return _repro_oom_text(status, memory_gb=_repro_docker_memory_gb()) or ""


#: The run-phase ``/tmp``: in memory and writable. ``exec``: Docker mounts it ``noexec`` by
#: default, so code compiled while the script runs (rstan's models, ``Rcpp::sourceCpp()``,
#: TMB) could not be loaded: "failed to map segment from shared object" (#568). The code can
#: already run anything R can; ``nosuid`` and ``nodev`` stay.
_RUN_TMPFS = "/tmp:exec,nosuid,nodev"  # noqa: S108 - inside the container

#: Java 17 and later keep other code out of their own classes, and rJava, which calls Java's
#: methods by reflection, was refused the iterator of an ArrayList (``tabulizer``; #578).
_JAVA_OPTIONS = "JAVA_TOOL_OPTIONS=--add-opens=java.base/java.util=ALL-UNNAMED"


def _run_dirs(run_tbl: pd.DataFrame) -> list[str | None]:
    return _chr_list(run_tbl["run_dir"]) if "run_dir" in run_tbl.columns else [None] * len(run_tbl)


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
    deadline: float | None = None,
    net: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Run the paper's scripts, in order, each in an isolated Docker container.

    Port of ``R/reproducibility_check_docker.R::repro_run_scripts_docker()`` (0.4.3.7):
    the same outcomes and columns as :func:`~metacheck.repro.core.repro_run_scripts`
    (*deadline* too), but each script runs via ``docker run --read-only --user <non-root>``
    with no capabilities, a process limit and the resource limits, with *sandbox_root*
    mounted at ``/sandbox``, the script's ``run_dir`` as its working directory (and
    ``PWD``), and *lib_dir* read-only at ``/rlib``. The network is off unless *net* (the
    limits of :func:`~metacheck.repro.runsupport._repro_download_limits`) is given: then
    the container is watched, and a script whose downloads go over the limits is stopped
    (outcome ``download_too_large``). A container stopped for using more memory than the
    limit (exit status 137) gets a note saying so in its error output.

    metacheck mounts the library writable while the scripts run, so a package can keep data
    in its own folder (``zipcodeR``; #575). Here it stays read-only: a script must not change
    the packages the next script loads.
    """
    if run_tbl is None or len(run_tbl) == 0:
        return _run_frame([])
    from metacheck.repro.core import _repro_duration
    from metacheck.repro.outcomes import _repro_timeout_text
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
    run_dirs = _run_dirs(run_tbl)
    preamble = _repro_docker_capture_preamble()
    cap_file_container = "/sandbox/.capture.json"
    cap_file_host = os.path.join(root, ".capture.json")

    user = _repro_docker_user()
    _repro_docker_hand_over(root)  # the scripts and data the container reads, and where it writes

    bar = pb(len(ordered), ":what [:bar] :current/:total")
    rows: list[dict[str, Any]] = []
    run_started = time.monotonic()
    try:
        bar.tick(0, {"what": ""})
        for fn in ordered:
            so_far = _repro_duration(time.monotonic() - run_started)
            bar.tick(1, {"what": f"Running {fn} ({so_far} so far)"})
            i = names.index(fn)
            pre = _pre_run_outcome(fn, skip, parses)
            if pre is not None:
                rows.append(pre)
                continue
            # Once the time for all scripts together has run out, a script is not
            # started; a script that is started stops when that time runs out
            limit = _repro_script_limit(timeout, deadline)
            if limit is None:
                rows.append(_run_row(fn, "skipped_time_limit", _NOT_STARTED))
                continue
            run_timeout, cut_by_deadline = limit
            exec_lines = _script_lines(paths[i])
            _message(
                "[repro/docker]   -> running '", fn, "' (timeout ", as_character(run_timeout),
                "s) ...",
            )  # fmt: skip
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
            # the script's run_dir (see repro_write_scripts()): the sandbox's top unless the
            # layout chose a folder below it. PWD as a shell would set it: a Hydra or
            # OmegaConf setting ${oc.env:PWD} stopped three scripts (#568)
            workdir = _repro_docker_workdir(run_dirs[i], root)
            container_name = _repro_docker_container_name()
            args = [
                "run", "--rm", "--name", container_name,
                *_repro_docker_network_args(net), "--read-only",
                "--tmpfs", _RUN_TMPFS,
                "--user", user,
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "512",
                *_repro_docker_resource_args(),
                "-w", workdir,
                "-e", f"PWD={workdir}",
                "-e", _JAVA_OPTIONS,
                "-v", f"{root}:/sandbox",
            ]  # fmt: skip
            if lib_norm is not None:
                args += ["-v", f"{lib_norm}:/rlib:ro"]
            args += [image, *_repro_docker_counted_cmd(["Rscript", container_wrapper], net)]

            with tempfile.TemporaryDirectory(prefix="pytacheck_docker_") as tmp:
                out_file = os.path.join(tmp, "out.txt")
                err_file = os.path.join(tmp, "err.txt")
                t0 = time.monotonic()
                watched = _repro_docker_run_watched(
                    args, container_name, run_timeout, out_file, err_file, root, net
                )
                res = watched["res"]
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

            # stopped for what it downloaded, with the internet on
            if watched["stopped"] is not None:
                rows.append(
                    _run_row(
                        fn,
                        "download_too_large",
                        watched["stopped"],
                        stdout=so,
                        stderr=se,
                        elapsed=elapsed,
                        script_lines=exec_lines,
                        captures=captures,
                    )
                )
                continue
            if isinstance(res, Exception):
                rows.append(
                    _run_row(
                        fn,
                        "timed_out" if is_timeout else "errored",
                        str(res),
                        # stopped by the time left for all scripts, not its own limit (#581)
                        ("deadline" if cut_by_deadline else "timeout") if is_timeout else "runtime",
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
                        _repro_timeout_text(run_timeout, watched),
                        "deadline" if cut_by_deadline else "timeout",
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
                # stopped for using more memory than the limit (#568)
                se = se + _repro_docker_oom_note(res.get("status"))
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


# ---------------------------------------------------------------------------
# Python (port of R/reproducibility_check_python_docker.R)
# ---------------------------------------------------------------------------

#: PyTorch's own index, next to PyPI: the large image has torch for processors only
#: ("+cpu"), and torchvision from PyPI, built for the other torch, stopped with "operator
#: torchvision::nms does not exist"; the index has "+cpu" builds, which pip prefers as the
#: higher version (#568).
_PY_EXTRA_INDEX = "https://download.pytorch.org/whl/cpu"


def _sh_quote(x: str) -> str:
    """R ``shQuote()`` (``type = "sh"``): in single quotes, a single quote as ``'\\''``."""
    return "'" + x.replace("'", "'\\''") + "'"


def _py_install_per_package(timeout: float) -> int:
    """The seconds one requirement may take: half of *timeout*, at least 2 and at most 15 min."""
    return max(120, min(900, math.ceil(timeout / 2)))


def _py_install_driver(reqs: Sequence[str], pkgs: Sequence[str], timeout: float) -> list[str]:
    """The ``sh`` driver a :func:`repro_install_deps_py_docker` container runs (metacheck 0.4.3.7).

    One ``pip install`` per requirement, since ``pip install a b c`` stops at the first that
    fails, each with a time limit of its own (:func:`_py_install_per_package`), so one slow
    package leaves time for the others: "requests" and "tqdm" failed only because the batch
    ran out of time (#568). A wheel is preferred, since a package that must be compiled can
    take the whole limit. pip installs with ``--prefix`` inside the container, so it uses what
    the image has (with ``--target`` it installed torch again for a package that needs it),
    and what it installed is copied to ``/pylib`` where not there yet: pip's own move copies
    file permissions, which a library on an exFAT drive refuses (#568). A pinned version that
    does not install is replaced by the current one (the bare name). When a package needs
    another version of one the image has, ``--prefix`` tries to change the image's copy, which
    it may not ("Permission denied"; #568): then it is installed apart, with ``--target``.

    After each requirement a line ``<i> ok|pinned|failed`` goes to ``.install_results``, so a
    run that times out still reports what finished, and pip's error output stays in
    ``.err_<i>.txt``. metacheck's driver prints JSON with ``printf``, which a backslash in
    pip's message, or a ``%`` in a requirement, breaks.
    """
    per_package = _py_install_per_package(timeout)
    lines = ["#!/bin/sh", "set -u", 'OUT="/sandbox/.install_results"', ': > "$OUT"']
    for i, (req, pkg) in enumerate(zip(reqs, pkgs, strict=True), start=1):
        tmp = f"/tmp/pyt_{i}"  # noqa: S108 - inside the container
        tmp_t = f"/tmp/pyt_t{i}"  # noqa: S108 - inside the container
        err = f"/sandbox/.err_{i}.txt"
        opts = "--quiet --prefer-binary --no-input"
        pip = (
            f"timeout {per_package} pip install {opts} --prefix {tmp} "
            f"--extra-index-url {_PY_EXTRA_INDEX}"
        )
        copy = (
            f"  for f in {tmp}/lib*/python*/site-packages/* {tmp}/lib*/python*/site-packages/.[!.]*;"
            ' do [ -e "$f" ] && [ ! -e "/pylib/${f##*/}" ] && cp -r "$f" /pylib/; done;'
            f" rm -rf {tmp}"
        )
        target_pip = (
            f"rm -rf {tmp_t}; timeout {per_package} pip install {opts} --target {tmp_t} "
            f"--extra-index-url {_PY_EXTRA_INDEX}"
        )
        target_copy = (
            f"  for f in {tmp_t}/* {tmp_t}/.[!.]*;"
            ' do [ -e "$f" ] && [ ! -e "/pylib/${f##*/}" ] && cp -r "$f" /pylib/; done;'
            f" rm -rf {tmp_t}"
        )
        lines += [f"if {pip} {_sh_quote(req)} 2> {err}; then", copy, f'  echo "{i} ok" >> "$OUT"']
        if req != pkg:
            lines += [
                f"elif {pip} {_sh_quote(pkg)} 2>> {err}; then",
                copy,
                f'  echo "{i} pinned" >> "$OUT"',
            ]
        lines += [
            f"elif {target_pip} {_sh_quote(req)} 2>> {err}; then",
            target_copy,
            f'  echo "{i} ok" >> "$OUT"',
            "else",
            f'  echo "{i} failed" >> "$OUT"',
            "fi",
        ]
    return lines


def _read_py_install_results(sandbox_dir: str, n: int) -> list[tuple[int, str, str]]:
    """``(i, status, message)`` of each requirement the driver finished, in order.

    The message is pip's error output with its line ends removed, as metacheck's driver has
    it (``tr -d '\\n'``), for a requirement that failed; ``""`` otherwise.
    """
    path = os.path.join(sandbox_dir, ".install_results")
    if not os.path.isfile(path):
        return []
    out: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for line in _read_lines(path):
        m = re.match(r"^(\d+) (ok|pinned|failed)$", line.strip())
        if not m or not 1 <= int(m.group(1)) <= n or int(m.group(1)) in seen:
            continue
        i, status = int(m.group(1)), m.group(2)
        seen.add(i)
        msg = ""
        if status == "failed":
            err = os.path.join(sandbox_dir, f".err_{i}.txt")
            if os.path.isfile(err):
                msg = Path(err).read_bytes().decode("utf-8", "replace").replace("\n", "")
        out.append((i, status, msg))
    return out


def repro_install_deps_py_docker(
    install_deps: pd.DataFrame | None,
    lib_dir: str | os.PathLike[str],
    image: str | None = None,
    timeout: float = 600,
) -> pd.DataFrame:
    """Install Python dependencies inside a throwaway Docker container.

    Port of ``R/reproducibility_check_python_docker.R::repro_install_deps_py_docker()``
    (0.4.3.7): one container (default network, since PyPI must be reachable; non-root, no
    capabilities, a process limit) runs the ``sh`` driver of :func:`_py_install_driver`,
    installing into *lib_dir* mounted at ``/pylib``. It sees its driver and the library,
    never the paper's data. A dependency without a result (the run timed out or failed) is
    recorded as not installed, with the run's message. A pinned version replaced by the
    current one says so in ``version_note``. Same columns as
    :func:`~metacheck.repro.python.repro_install_deps_py`.
    """
    from metacheck.repro.core import _install_frame
    from metacheck.repro.python import _requirements

    if install_deps is None or len(install_deps) == 0:
        return _install_frame([])
    image = _repro_docker_image_py(image)
    lib = os.fspath(lib_dir)
    os.makedirs(lib, exist_ok=True)
    pkgs = [p or "" for p in _chr_list(install_deps["package"])]
    srcs = _chr_list(install_deps["source"])
    reqs = _requirements(install_deps)

    sandbox_dir = tempfile.mkdtemp(prefix="repro_docker_install_py_")
    out_fd, out_file = tempfile.mkstemp(suffix=".out")
    os.close(out_fd)
    try:
        driver = os.path.join(sandbox_dir, "install.sh")
        Path(driver).write_bytes(
            ("\n".join(_py_install_driver(reqs, pkgs, timeout)) + "\n").encode("utf-8")
        )
        os.chmod(driver, 0o755)  # noqa: S103 - the container user runs it
        _repro_docker_hand_over(sandbox_dir)
        _repro_docker_hand_over(lib, tree=False)
        container_name = _repro_docker_container_name()
        args = [
            "run", "--rm", "--name", container_name,
            "--user", _repro_docker_user(),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "512",
            *_repro_docker_resource_args(),
            # pip's cache and settings go to /tmp: the container user has no home folder
            "-e", "HOME=/tmp",
            "-v", f"{sandbox_dir}:/sandbox",
            "-v", f"{_normalize_path(lib)}:/pylib",
            image, "sh", _repro_docker_container_path(driver, sandbox_dir),
        ]  # fmt: skip
        raw = _docker(args, timeout=timeout, stdout=out_file, merge_stderr=True)
        res = None if isinstance(raw, Exception) else raw
        if res is None or res.get("timeout"):
            _repro_docker_stop(container_name)

        _repro_docker_scrub(sandbox_dir)  # a link the install left must not be read through
        done = _read_py_install_results(sandbox_dir, len(pkgs)) if res is not None else []
        if res is None:
            msg = "docker run could not be started"
        elif res.get("timeout"):
            msg = f"docker install timed out after {as_character(r_round(timeout))}s"
        else:
            try:
                txt = "\n".join(_read_lines(out_file))
            except OSError:
                txt = ""
            status = as_character(res.get("status")) or "NA"
            msg = f"docker install failed (status {status}): {txt}"
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.unlink(out_file)

    rows: list[dict[str, Any]] = []
    for i, status, err in done:
        k = i - 1
        note = None
        if status == "pinned":
            pinned = re.sub(r"['\"\\%]", "", reqs[k])
            note = f"the pinned version {pinned} could not be installed, so the current one was"
        rows.append(
            {
                "package": pkgs[k],
                "source": srcs[k],
                "installed": status != "failed",
                "message": err if status == "failed" else "",
                "version_note": note,
            }
        )
    # the packages without a result, once each (R: setdiff())
    finished = {pkgs[i - 1] for i, _, _ in done}
    rows.extend(
        {"package": p, "source": srcs[pkgs.index(p)], "installed": False, "message": msg}
        for p in dict.fromkeys(pkgs)
        if p not in finished
    )
    return _install_frame([{**r, "via_archive": False, "category": None} for r in rows])


def repro_run_scripts_py_docker(
    run_tbl: pd.DataFrame | None,
    order: Any,
    sandbox_root: str | os.PathLike[str],
    lib_dir: str | os.PathLike[str] | None = None,
    image: str | None = None,
    timeout: float = 600,
    skip: Sequence[str] | str = (),
    parses: Mapping[str, Any] | pd.Series | None = None,
    failed_deps: Sequence[str | None] | str = (),
    deadline: float | None = None,
    net: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Run the paper's Python scripts, in order, each in an isolated Docker container.

    Port of ``R/reproducibility_check_python_docker.R::repro_run_scripts_py_docker()``
    (0.4.3.7): the same container as :func:`repro_run_scripts_docker` (no network unless
    *net*, then watched; read-only root; a writable in-memory ``/tmp`` that may hold
    programs; non-root, no capabilities, a process limit), with *sandbox_root* at
    ``/sandbox``, the script's ``run_dir`` as its working directory (and ``PWD``), *lib_dir*
    read-only at ``/pylib``, and ``python <script>``. ``PYTHONPATH`` holds the repository's
    own module folders (``module_folders``, counted from ``module_root``) first, then
    ``/pylib``. Caches go to ``/tmp`` (Numba stopped with "cannot cache function"; #568),
    and there is no screen for plots and windows. Once *deadline* has passed a script is not
    started; a started one stops then. What the code left in the sandbox that leads out of it
    is removed after each container. Same columns and outcomes as
    :func:`~metacheck.repro.python.repro_run_scripts_py`; ``captures`` is always ``None``.

    metacheck mounts the library writable while the scripts run (#575); here it stays
    read-only, as for R code.
    """
    from metacheck.repro.outcomes import _repro_timeout_text
    from metacheck.repro.python import _py_run_row
    from metacheck.utils import pb

    if run_tbl is None or len(run_tbl) == 0:
        return _run_frame([])
    image = _repro_docker_image_py(image)
    ordered = _ordered_names(run_tbl, order)
    root = _normalize_path(os.fspath(sandbox_root), must_work=True)
    lib_norm = (
        _normalize_path(os.fspath(lib_dir), must_work=True)
        if lib_dir is not None and os.path.isdir(lib_dir)
        else None
    )
    names = _chr_list(run_tbl["file_name"])
    paths = _chr_list(run_tbl["script_path"])
    run_dirs = _run_dirs(run_tbl)
    n = len(names)
    folders = _chr_list(run_tbl["module_folders"]) if "module_folders" in run_tbl else [None] * n
    roots = _chr_list(run_tbl["module_root"]) if "module_root" in run_tbl else [None] * n
    user = _repro_docker_user()
    _repro_docker_hand_over(root)

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
            # Once the time for all scripts together has run out, a script is not started; a
            # script that is started stops when that time runs out
            limit = _repro_script_limit(timeout, deadline)
            if limit is None:
                rows.append(_run_row(fn, "skipped_time_limit", _NOT_STARTED))
                continue
            run_timeout, cut_by_deadline = limit
            exec_lines = _script_lines(paths[i])
            _message(
                "[repro/py-docker]   -> running '", fn, "' (timeout ", as_character(run_timeout),
                "s) ...",
            )  # fmt: skip
            workdir = _repro_docker_workdir(run_dirs[i], root)
            container_name = _repro_docker_container_name()
            args = [
                "run", "--rm", "--name", container_name,
                *_repro_docker_network_args(net), "--read-only",
                "--tmpfs", _RUN_TMPFS,
                "--user", user,
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "512",
                *_repro_docker_resource_args(),
                "-w", workdir,
                "-e", f"PWD={workdir}",
                "-v", f"{root}:/sandbox",
                "-e", "HOME=/tmp",
                "-e", "NUMBA_CACHE_DIR=/tmp/numba",
                "-e", "MPLCONFIGDIR=/tmp/matplotlib",
                "-e", "XDG_CACHE_HOME=/tmp/.cache",
                "-e", "MPLBACKEND=Agg",
                "-e", "QT_QPA_PLATFORM=offscreen",
            ]  # fmt: skip
            if lib_norm is not None:
                args += ["-v", f"{lib_norm}:/pylib:ro"]
            # the repository's own modules first (see _repro_py_module_folders()), then what
            # was installed
            base = _repro_docker_workdir(roots[i], root) if roots[i] else "/sandbox"
            own = [f for f in (folders[i] or "").split("\n") if f != ""]
            py_path = [base if f == "." else f"{base}/{f}" for f in own]
            if lib_norm is not None:
                py_path.append("/pylib")
            if py_path:
                args += ["-e", "PYTHONPATH=" + ":".join(py_path)]
            container_script = _repro_docker_container_path(paths[i] or "", root)
            args += [image, *_repro_docker_counted_cmd(["python", container_script], net)]

            with tempfile.TemporaryDirectory(prefix="pytacheck_docker_") as tmp:
                out_file = os.path.join(tmp, "out.txt")
                err_file = os.path.join(tmp, "err.txt")
                t0 = time.monotonic()
                watched = _repro_docker_run_watched(
                    args, container_name, run_timeout, out_file, err_file, root, net
                )
                res = watched["res"]
                elapsed = time.monotonic() - t0
                is_timeout = (
                    isinstance(res, Exception)
                    and bool(grepl("timed? ?out", str(res), ignore_case=True))
                ) or (isinstance(res, dict) and bool(res.get("timeout")))
                if is_timeout:
                    _repro_docker_stop(container_name)
                removed = _repro_docker_scrub(root)
                if removed:
                    _message(
                        "[repro/py-docker]   removed from the sandbox after '", fn, "': ",
                        ", ".join(removed[:10]), " ..." if len(removed) > 10 else "",
                    )  # fmt: skip
                so, se = _read_cap(out_file), _read_cap(err_file)
            _message(
                "[repro/py-docker]   <- '", fn, "' done in ", as_character(round(elapsed, 1)), "s"
            )
            # stopped for what it downloaded, with the internet on
            if watched["stopped"] is not None:
                rows.append(
                    _run_row(
                        fn,
                        "download_too_large",
                        watched["stopped"],
                        stdout=so,
                        stderr=se,
                        elapsed=elapsed,
                        script_lines=exec_lines,
                    )
                )
                continue
            if isinstance(res, Exception):
                rows.append(
                    _run_row(
                        fn,
                        "timed_out" if is_timeout else "errored",
                        str(res),
                        # stopped by the time left for all scripts, not its own limit (#581)
                        ("deadline" if cut_by_deadline else "timeout") if is_timeout else "runtime",
                        None,
                        so,
                        se,
                        elapsed,
                        exec_lines,
                    )
                )
                continue
            if res.get("timeout"):
                rows.append(
                    _run_row(
                        fn,
                        "timed_out",
                        _repro_timeout_text(run_timeout, watched),
                        "deadline" if cut_by_deadline else "timeout",
                        None,
                        so,
                        se,
                        elapsed,
                        exec_lines,
                    )
                )
                continue
            status = res.get("status")
            if status != 0:
                # stopped for using more memory than the limit (#568)
                se = se + _repro_docker_oom_note(status)
            rows.append(
                _py_run_row(
                    fn, status, False, so, se, elapsed, exec_lines, run_timeout, failed_deps
                )
            )
    finally:
        bar.terminate()
    return _run_frame(rows)
