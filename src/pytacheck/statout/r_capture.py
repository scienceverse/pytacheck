"""Capture statistical result OBJECTS from an executed R script.

Port of ``R/r-capture.R``. metacheck runs each script in a ``callr``
subprocess whose runner evaluates the top-level statements one at a time,
echoes them as ``source(echo = TRUE)`` would, and reduces every recognised
result object (``htest``, ``summary.lm``, ``anova``, aggregates named by
their ``FUN`` ...) to a plain record of named numbers, written to a sidecar
file. The parent converts those records to the same table shape
:func:`~pytacheck.statout.r_output.read_r_output` returns and merges them
with the tables parsed from stdout.

The object inspection has to happen *inside R*, so the child-side reducers
(``.r_cap_htest()``, ``.r_cap_coef_matrix()``, ``.r_cap_anova()``,
``.r_call_fun_arg()``, ``.r_cap_by_fun()``, ``.r_capture_value()``) are kept
as R source (:data:`_CHILD_R`) that is shipped to an ``Rscript`` subprocess \u2014
the Python analogue of ``callr::r()``. The only change is the sidecar format:
the child writes JSON (with a small base-R encoder, so the child needs no
packages) instead of an RDS file. The parent-side functions
(:func:`_r_captures_to_tables`, :func:`_r_merge_captures`,
:func:`_r_method_to_fn`) are plain Python.

Running R is optional: :func:`_rscript` finds ``Rscript`` from
``PYTACHECK_RSCRIPT`` or ``PATH`` and returns ``None`` when R is absent.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytacheck._r.regex import grepl

__all__: list[str] = []

# The classes worth capturing (``.R_CAPTURE_CLASSES``).
_R_CAPTURE_CLASSES = (
    "htest",
    "summary.lm",
    "anova",
    "summary.aov",
    "summary.glm",
    "lm",
    "glm",
)

# ---------------------------------------------------------------------------
# Child-side R code (verbatim ports of the R reducers)
# ---------------------------------------------------------------------------

_CHILD_FUNCTIONS: dict[str, str] = {
    "%||%": "function(a, b) if (is.null(a)) b else a",
    ".r_cap_htest": r"""function(x) {
  st <- list()
  add <- function(nm, v) {
    if (is.null(v) || !length(v)) return()
    v <- v[!is.na(v)]
    if (!length(v)) return()
    nms <- names(v)
    for (i in seq_along(v)) {
      key <- if (!is.null(nms) && nzchar(nms[i])) nms[i] else nm
      st[[key]] <<- unname(v[[i]])
    }
  }
  add("statistic", x$statistic)
  add("parameter", x$parameter)
  add("p", x$p.value)
  add("estimate", x$estimate)
  ci <- x$conf.int
  if (!is.null(ci) && length(ci) == 2) {
    st[["conf.low"]]  <- ci[[1]]
    st[["conf.high"]] <- ci[[2]]
  }
  if (!length(st)) return(NULL)
  list(analysis = as.character(x$method %||% "htest")[1],
       method   = as.character(x$method %||% "")[1],
       rows     = list(list(label = as.character(x$data.name %||% "")[1],
                            stats = st)))
}""",
    ".r_cap_coef_matrix": r"""function(cf, analysis) {
  if (is.null(cf) || !is.matrix(cf) || !nrow(cf)) return(NULL)
  cn <- colnames(cf); rn <- rownames(cf)
  if (is.null(cn)) return(NULL)
  rows <- lapply(seq_len(nrow(cf)), function(i) {
    st <- list()
    for (j in seq_along(cn)) {
      v <- cf[i, j]
      if (!is.na(v)) st[[cn[j]]] <- unname(v)
    }
    if (!length(st)) return(NULL)
    list(label = if (!is.null(rn)) rn[i] else as.character(i), stats = st)
  })
  rows <- Filter(Negate(is.null), rows)
  if (!length(rows)) return(NULL)
  list(analysis = analysis, method = analysis, rows = rows)
}""",
    ".r_cap_anova": r"""function(x, analysis) {
  df <- as.data.frame(x, stringsAsFactors = FALSE)
  if (!nrow(df) || !ncol(df)) return(NULL)
  cn <- names(df); rn <- rownames(df)
  rows <- lapply(seq_len(nrow(df)), function(i) {
    st <- list()
    for (j in seq_along(cn)) {
      v <- suppressWarnings(as.numeric(df[i, j]))
      if (length(v) == 1 && !is.na(v)) st[[cn[j]]] <- v
    }
    if (!length(st)) return(NULL)
    list(label = if (!is.null(rn)) trimws(rn[i]) else as.character(i),
         stats = st)
  })
  rows <- Filter(Negate(is.null), rows)
  if (!length(rows)) return(NULL)
  list(analysis = analysis, method = analysis, rows = rows)
}""",
    ".r_call_fun_arg": r"""function(call_text) {
  if (is.null(call_text) || !nzchar(call_text)) return(NA_character_)
  cl <- tryCatch(str2lang(call_text), error = function(e) NULL)
  if (is.null(cl) || !is.call(cl)) return(NA_character_)
  fname <- tryCatch(as.character(cl[[1]])[1], error = function(e) NA_character_)
  if (is.na(fname) || !nzchar(fname)) return(NA_character_)
  f <- tryCatch(get(fname, mode = "function"), error = function(e) NULL)
  if (is.null(f)) return(NA_character_)

  pick <- function(m) {
    if (is.null(m)) return(NULL)
    fun <- m[["FUN"]]
    if (is.null(fun)) return(NULL)
    if (is.name(fun)) return(as.character(fun))
    if (is.character(fun) && length(fun)) return(fun[[1]])
    NULL
  }
  hit <- pick(tryCatch(match.call(f, cl), error = function(e) NULL))
  if (!is.null(hit)) return(hit)

  meths <- tryCatch(as.character(utils::methods(fname)),
                    error = function(e) character(0))
  for (mn in meths) {
    short <- sub(paste0("^", fname, "\\."), "", mn)
    mf <- tryCatch(utils::getS3method(fname, short), error = function(e) NULL)
    if (is.null(mf) || !("FUN" %in% names(formals(mf)))) next
    hit <- pick(tryCatch(match.call(mf, cl), error = function(e) NULL))
    if (!is.null(hit)) return(hit)
  }
  NA_character_
}""",
    ".r_cap_by_fun": r"""function(v, stat_name, max_rows = 40L) {
  df <- tryCatch(as.data.frame(v, stringsAsFactors = FALSE),
                 error = function(e) NULL)
  if (is.null(df) || !nrow(df) || ncol(df) < 1) return(NULL)
  nms <- names(df)
  if (is.null(nms) || !length(nms)) return(NULL)
  has_label_col <- any(!vapply(df, function(c) is.numeric(c) || is.integer(c),
                               logical(1)))
  rn <- rownames(df)
  named_rows <- !is.null(rn) &&
    !identical(as.character(rn), as.character(seq_len(nrow(df))))
  if (!has_label_col && !named_rows) return(NULL)
  if (nrow(df) > max_rows) return(NULL)
  is_num <- vapply(df, function(c) is.numeric(c) || is.integer(c), logical(1))
  if (!any(is_num)) return(NULL)
  lab_cols <- which(!is_num); val_cols <- which(is_num)

  rows <- lapply(seq_len(nrow(df)), function(i) {
    lab <- if (length(lab_cols))
      paste(trimws(as.character(unlist(df[i, lab_cols, drop = TRUE]))),
            collapse = " ") else rownames(df)[i] %||% as.character(i)
    st <- list()
    for (j in val_cols) {
      x <- suppressWarnings(as.numeric(df[i, j]))
      if (length(x) == 1 && !is.na(x)) {
        key <- if (length(val_cols) > 1)
          paste0(stat_name, " (", nms[j], ")") else stat_name
        st[[key]] <- x
      }
    }
    if (!length(st)) return(NULL)
    list(label = lab, stats = st)
  })
  rows <- Filter(Negate(is.null), rows)
  if (!length(rows)) return(NULL)
  list(analysis = paste0(stat_name, " by group"), method = "", rows = rows)
}""",
    ".r_capture_value": r"""function(v, call_text = NULL) {
  if (is.null(v)) return(NULL)

  if (inherits(v, "htest")) return(.r_cap_htest(v))

  if (inherits(v, "summary.lm") || inherits(v, "summary.glm")) {
    an <- if (inherits(v, "summary.glm")) "glm" else "lm"
    return(.r_cap_coef_matrix(v$coefficients, an))
  }

  if (inherits(v, "anova") || inherits(v, "summary.aov"))
    return(.r_cap_anova(if (is.list(v) && !is.data.frame(v) && length(v))
                          v[[1]] else v, "anova"))

  fun <- .r_call_fun_arg(call_text)
  if (!is.na(fun) && nzchar(fun)) {
    if (nzchar(stato_type_column(fun)$termSource))
      return(.r_cap_by_fun(v, fun))
  }

  NULL
}""",
}

# ``child_type()`` of ``.r_capture_helpers()``: the vocabulary check the
# call-based fallback needs, against the key vectors shipped with it.
_CHILD_TYPE = r"""function(header, call_fn = NULL) {
  key <- tolower(trimws(header %||% ""))
  if (!nzchar(key)) return(list(termSource = ""))
  keys <- unique(c(key, sub("\\[[^]]*\\]$", "", key)))
  for (k in keys) {
    if (k %in% .mc_stato_tables$map) return(list(termSource = "STATO"))
    if (k %in% .mc_stato_tables$mc_map) return(list(termSource = "metacheck"))
  }
  list(termSource = "")
}"""

# A dependency-free JSON encoder, so the child needs no packages to write
# its sidecar (metacheck writes RDS, which Python cannot read).
_JSON_R = r"""
.mc_json_str <- function(s) {
  s <- enc2utf8(as.character(s))
  s <- gsub("\\", "\\\\", s, fixed = TRUE)
  s <- gsub("\"", "\\\"", s, fixed = TRUE)
  s <- gsub("\n", "\\n", s, fixed = TRUE)
  s <- gsub("\r", "\\r", s, fixed = TRUE)
  s <- gsub("\t", "\\t", s, fixed = TRUE)
  s <- vapply(s, function(x) {
    cp <- utf8ToInt(x)
    if (length(cp) && any(is.na(cp))) return(x)
    ctl <- cp < 32L
    if (!any(ctl)) return(x)
    paste(ifelse(ctl, sprintf("\\u%04x", cp), vapply(cp, intToUtf8, "")), collapse = "")
  }, "", USE.NAMES = FALSE)
  paste0("\"", s, "\"")
}
.mc_json <- function(x) {
  if (is.null(x)) return("null")
  if (is.list(x)) {
    nms <- names(x)
    items <- vapply(x, .mc_json, "", USE.NAMES = FALSE)
    if (!is.null(nms))
      return(paste0("{", paste0(.mc_json_str(nms), ":", items, collapse = ","), "}"))
    return(paste0("[", paste(items, collapse = ","), "]"))
  }
  if (is.factor(x)) x <- as.character(x)
  if (length(x) != 1L) return(.mc_json(as.list(unname(x))))
  if (is.character(x)) return(if (is.na(x)) "null" else .mc_json_str(x))
  if (is.logical(x)) return(if (is.na(x)) "null" else if (x) "true" else "false")
  if (is.integer(x)) return(if (is.na(x)) "null" else as.character(x))
  if (is.double(x)) {
    if (is.nan(x)) return("\"NaN\"")
    if (is.na(x)) return("null")
    if (is.infinite(x)) return(if (x > 0) "\"Inf\"" else "\"-Inf\"")
    return(sprintf("%.17g", x))
  }
  .mc_json_str(paste(format(x), collapse = " "))
}
.mc_write_json <- function(x, file) writeLines(.mc_json(x), file, useBytes = TRUE)
"""

# The runner (``.r_capture_runner()``'s function), evaluating the script's
# top-level expressions one at a time with a "> " echo and autoprint.
_RUNNER_R = r"""function(script, wd, capture_file, helpers) {
  setwd(wd)
  env <- globalenv()
  for (nm in names(helpers)) assign(nm, helpers[[nm]], envir = env)
  captures <- list()
  on.exit(try(.mc_write_json(captures, capture_file), silent = TRUE), add = TRUE)

  exprs <- parse(script, keep.source = TRUE)
  srcrefs <- attr(exprs, "srcref")

  for (i in seq_along(exprs)) {
    e <- exprs[[i]]
    sr <- if (!is.null(srcrefs) && length(srcrefs) >= i) srcrefs[[i]] else NULL
    txt <- if (!is.null(sr)) as.character(sr) else deparse(e)
    cat(paste0("> ", txt, collapse = "\n"), "\n", sep = "")

    res <- withVisible(eval(e, envir = env))
    if (res$visible) {
      print(res$value)
    }
    call_txt <- paste(txt, collapse = " ")
    rec <- tryCatch(.r_capture_value(res$value, call_txt),
                    error = function(e) NULL)
    if (!is.null(rec)) {
      rec$line <- if (!is.null(sr)) as.integer(sr[1L]) else NA_integer_
      rec$call_text <- call_txt
      captures[[length(captures) + 1L]] <- rec
    }
  }
  invisible(NULL)
}"""


def _r_string(x: str) -> str:
    """An R string literal for *x*."""
    out = ['"']
    for ch in x:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _r_chr_vector(values: Sequence[str]) -> str:
    return "c(" + ", ".join(_r_string(v) for v in values) + ")" if values else "character(0)"


def _stato_vocab_keys() -> tuple[list[str], list[str]]:
    """The keys of ``.STATO_MAP`` and ``.MC_STAT_MAP`` (``R/stato-map.R``)."""
    from pytacheck.statout import stato_map

    found: list[list[str]] = []
    for names in (("_STATO_MAP", "STATO_MAP"), ("_MC_STAT_MAP", "MC_STAT_MAP")):
        for nm in names:
            val = getattr(stato_map, nm, None)
            if val is not None:
                found.append(list(val.keys()) if isinstance(val, Mapping) else list(val))
                break
        else:
            raise ImportError(f"pytacheck.statout.stato_map has no {names[0]}")
    return found[0], found[1]


def _r_capture_helpers() -> str:
    """R source evaluating to the named list of child-side reducers.

    Port of ``R/r-capture.R::.r_capture_helpers()``. metacheck returns the
    functions themselves for ``callr`` to serialise; here the list is built
    from source in the child. The STATO vocabulary keys the call-based
    fallback needs travel with it (as ``.mc_stato_tables``).

    metacheck's closures unserialise with its namespace as their environment,
    so they (and the runner, which shares their environment) find base R
    before the global environment the script runs in: a script's own
    ``sapply`` or ``trimws`` does not shadow base's for them. The helpers are
    therefore defined in an environment whose parent is base's namespace.
    """
    stato_keys, mc_keys = _stato_vocab_keys()
    parts = [
        "local(envir = new.env(parent = .BaseNamespaceEnv), {",
        f"  .mc_stato_tables <- list(map = {_r_chr_vector(stato_keys)}, "
        f"mc_map = {_r_chr_vector(mc_keys)})",
    ]
    for name, src in _CHILD_FUNCTIONS.items():
        parts.append(f"  `{name}` <- {src}")
    parts.append(f"  stato_type_column <- {_CHILD_TYPE}")
    names = [*_CHILD_FUNCTIONS, "stato_type_column"]
    parts.append("  list(" + ", ".join(f"`{n}` = `{n}`" for n in names) + ")")
    parts.append("})")
    return "\n".join(parts)


def _runner_source(
    script: str | os.PathLike[str],
    wd: str | os.PathLike[str],
    capture_file: str | os.PathLike[str],
    helpers: str,
    error_file: str | os.PathLike[str],
    libpath: Sequence[str] | None = None,
) -> str:
    """A standalone R script running :data:`_RUNNER_R` on *script*."""
    lib = f".libPaths({_r_chr_vector([str(p) for p in libpath])})\n" if libpath else ""
    return f"""{lib}local({{
{_JSON_R}
  helpers <- {helpers}
  henv <- environment(helpers[[1L]])
  for (nm in c(".mc_json", ".mc_json_str", ".mc_write_json")) {{
    f <- get(nm)
    environment(f) <- henv  # base before the script's globals, as for the helpers
    assign(nm, f, envir = henv)
  }}
  runner <- {_RUNNER_R}
  environment(runner) <- henv
  res <- tryCatch(
    runner({_r_string(str(script))}, {_r_string(str(wd))}, {_r_string(str(capture_file))},
           helpers),
    error = function(e) e)
  if (inherits(res, "error")) {{
    call <- conditionCall(res)
    writeLines(enc2utf8(c(conditionMessage(res),
                          if (is.null(call)) "" else paste(deparse(call), collapse = " "))),
               {_r_string(str(error_file))}, useBytes = TRUE)
    quit(save = "no", status = 1L)
  }}
}})
"""


# ---------------------------------------------------------------------------
# Running R (the callr::r() analogue)
# ---------------------------------------------------------------------------


class RSubprocessError(RuntimeError):
    """An error in the R subprocess (``callr_status_error``)."""


class RSubprocessTimeout(RSubprocessError):
    """The R subprocess timed out (``callr_timeout_error``)."""


def _rscript(rscript: str | None = None) -> str | None:
    """Path of ``Rscript`` (``PYTACHECK_RSCRIPT``, then ``PATH``) or ``None``."""
    return rscript or os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")


def _open_sink(path: str | os.PathLike[str] | None) -> Any:
    return subprocess.DEVNULL if path is None else open(path, "wb")


def _run_rscript(
    code: str,
    *,
    timeout: float | None = None,
    stdout: str | os.PathLike[str] | None = None,
    stderr: str | os.PathLike[str] | None = None,
    rscript: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run R *code* with ``Rscript``; return the exit status.

    Raises :class:`RSubprocessTimeout` on timeout and :class:`FileNotFoundError`
    when no ``Rscript`` is available.
    """
    exe = _rscript(rscript)
    if exe is None:
        raise FileNotFoundError("Rscript not found: install R or set PYTACHECK_RSCRIPT")
    with tempfile.TemporaryDirectory(prefix="pytacheck_r_") as tmp:
        driver = Path(tmp) / "driver.R"
        driver.write_text(code, encoding="utf-8")
        out = _open_sink(stdout)
        err = _open_sink(stderr)
        try:
            proc = subprocess.run(  # noqa: S603 - runs the configured Rscript
                [exe, "--no-save", "--no-restore", str(driver)],
                stdout=out,
                stderr=err,
                timeout=timeout,
                env={**os.environ, **(env or {})},
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RSubprocessTimeout("! callr timed out") from exc
        finally:
            for f in (out, err):
                if f is not subprocess.DEVNULL:
                    f.close()
        return proc.returncode


def _r_capture_runner() -> Callable[..., None]:
    """The runner that executes a script in a fresh R process.

    Port of ``R/r-capture.R::.r_capture_runner()`` combined with the
    ``callr::r()`` call of ``repro_run_scripts()``: the returned function
    ``runner(script, wd, capture_file, helpers, *, libpath=None,
    timeout=None, stdout=None, stderr=None, rscript=None)`` parses *script*,
    evaluates its top-level expressions one at a time in the child's global
    environment (echoing ``> <statement>`` and autoprinting visible values,
    so stdout matches ``source(echo = TRUE)``), and writes every captured
    result record to *capture_file* (JSON; read it back with
    :func:`_read_captures`). *helpers* is :func:`_r_capture_helpers`'s R
    source. *stdout*/*stderr* are file paths (``None`` discards the stream).

    Raises :class:`RSubprocessError` when the script errors (message in the
    style of ``callr``: ``"! in callr subprocess.\\nCaused by error ..."``),
    :class:`RSubprocessTimeout` on timeout.
    """

    def runner(
        script: str | os.PathLike[str],
        wd: str | os.PathLike[str],
        capture_file: str | os.PathLike[str],
        helpers: str,
        *,
        libpath: Sequence[str] | None = None,
        timeout: float | None = None,
        stdout: str | os.PathLike[str] | None = None,
        stderr: str | os.PathLike[str] | None = None,
        rscript: str | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="pytacheck_rerr_") as tmp:
            error_file = Path(tmp) / "error.txt"
            code = _runner_source(
                os.path.abspath(script),
                os.path.abspath(wd),
                os.path.abspath(capture_file),
                helpers,
                error_file,
                libpath,
            )
            status = _run_rscript(
                code, timeout=timeout, stdout=stdout, stderr=stderr, rscript=rscript
            )
            if status != 0:
                msg, call = "", ""
                if error_file.exists():
                    parts = error_file.read_text(encoding="utf-8", errors="replace").split("\n")
                    if parts and parts[-1] == "":
                        parts.pop()
                    if parts:
                        call = parts[-1]
                        msg = "\n".join(parts[:-1])
                where = f" in `{call}`" if call else ""
                raise RSubprocessError(
                    f"! in callr subprocess.\nCaused by error{where}:\n! {msg}".rstrip()
                )

    return runner


def _decode_capture_value(v: Any) -> Any:
    if isinstance(v, str) and v in ("Inf", "-Inf", "NaN"):
        return float(v.replace("Inf", "inf").replace("NaN", "nan"))
    return v


def _read_captures(capture_file: str | os.PathLike[str]) -> list[dict[str, Any]] | None:
    """Read the capture sidecar written by the runner (``readRDS()``).

    Returns ``None`` when the file is missing or unreadable, like the
    ``tryCatch(readRDS(cap_file), error = function(e) NULL)`` of the caller.
    """
    try:
        raw = Path(capture_file).read_text(encoding="utf-8")
        caps = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(caps, list):
        return [] if caps == {} else None
    for rec in caps:
        if not isinstance(rec, dict):
            continue
        for row in rec.get("rows") or []:
            if isinstance(row, dict) and isinstance(row.get("stats"), dict):
                row["stats"] = {k: _decode_capture_value(x) for k, x in row["stats"].items()}
    return caps


@dataclass
class RCaptureResult:
    """What one captured run produced (stdout, stderr, captures, error)."""

    stdout: str = ""
    stderr: str = ""
    captures: list[dict[str, Any]] | None = None
    error: RSubprocessError | None = None
    elapsed: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _r_capture_run(
    script: str | os.PathLike[str],
    wd: str | os.PathLike[str] | None = None,
    *,
    timeout: float | None = None,
    libpath: Sequence[str] | None = None,
    rscript: str | None = None,
) -> RCaptureResult:
    """Run *script* through :func:`_r_capture_runner` and collect everything.

    Convenience wrapper mirroring what ``repro_run_scripts()`` does around
    ``callr::r()``: stdout/stderr go to temporary files that are read back,
    and the capture sidecar is read with :func:`_read_captures`.
    """
    script = os.path.abspath(script)
    wd = os.path.dirname(script) if wd is None else wd
    with tempfile.TemporaryDirectory(prefix="pytacheck_cap_") as tmp:
        out_file = Path(tmp) / "out.txt"
        err_file = Path(tmp) / "err.txt"
        cap_file = Path(tmp) / "captures.json"
        t0 = time.monotonic()
        error: RSubprocessError | None = None
        try:
            _r_capture_runner()(
                script,
                wd,
                cap_file,
                _r_capture_helpers(),
                libpath=libpath,
                timeout=timeout,
                stdout=out_file,
                stderr=err_file,
                rscript=rscript,
            )
        except RSubprocessError as exc:
            error = exc
        elapsed = time.monotonic() - t0
        captures = _read_captures(cap_file) if cap_file.exists() else None

        def read(f: Path) -> str:
            if not f.exists():
                return ""
            text = f.read_text(encoding="utf-8", errors="replace")
            lines = text.split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            return "\n".join(lines)

        return RCaptureResult(
            stdout=read(out_file),
            stderr=read(err_file),
            captures=captures,
            error=error,
            elapsed=elapsed,
        )


def _r_eval_child(call: str, rscript: str | None = None) -> Any:
    """Evaluate the R expression *call* with the child helpers in scope.

    The result is JSON-encoded in R and decoded here. Used by the Python
    entry points of the child-side reducers (:func:`_r_cap_htest`, ...).
    """
    with tempfile.TemporaryDirectory(prefix="pytacheck_eval_") as tmp:
        out = Path(tmp) / "value.json"
        code = f"""local({{
{_JSON_R}
  helpers <- {_r_capture_helpers()}
  for (nm in names(helpers)) assign(nm, helpers[[nm]], envir = globalenv())
  henv <- environment(helpers[[1L]])
  value <- eval(quote({call}), envir = henv)
  .mc_write_json(value, {_r_string(str(out))})
}})
"""
        status = _run_rscript(code, rscript=rscript, stderr=Path(tmp) / "err.txt")
        if status != 0:
            err = (Path(tmp) / "err.txt").read_text(encoding="utf-8", errors="replace")
            raise RSubprocessError(err.strip())
        value = json.loads(out.read_text(encoding="utf-8"))
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        for row in value["rows"]:
            if isinstance(row, dict) and isinstance(row.get("stats"), dict):
                row["stats"] = {k: _decode_capture_value(x) for k, x in row["stats"].items()}
    return value


def _r_cap_htest(x: str, rscript: str | None = None) -> dict[str, Any] | None:
    """Reduce an ``htest`` to a capture record.

    Port of ``R/r-capture.R::.r_cap_htest()``. The reducer runs in R: *x* is
    R source evaluating to the object (e.g. ``"t.test(extra ~ group, data =
    sleep)"``).
    """
    return _r_eval_child(f".r_cap_htest({x})", rscript)  # type: ignore[no-any-return]


def _r_cap_coef_matrix(cf: str, analysis: str, rscript: str | None = None) -> dict[str, Any] | None:
    """Reduce a coefficients matrix (R source *cf*) to a capture record.

    Port of ``R/r-capture.R::.r_cap_coef_matrix()`` (runs in R).
    """
    return _r_eval_child(f".r_cap_coef_matrix({cf}, {_r_string(analysis)})", rscript)  # type: ignore[no-any-return]


def _r_cap_anova(x: str, analysis: str, rscript: str | None = None) -> dict[str, Any] | None:
    """Reduce an anova table (R source *x*) to a capture record.

    Port of ``R/r-capture.R::.r_cap_anova()`` (runs in R).
    """
    return _r_eval_child(f".r_cap_anova({x}, {_r_string(analysis)})", rscript)  # type: ignore[no-any-return]


def _r_call_fun_arg(call_text: str | None, rscript: str | None = None) -> str | None:
    """The ``FUN`` a call names (``"mean"`` for ``aggregate(y ~ g, d, mean)``).

    Port of ``R/r-capture.R::.r_call_fun_arg()`` (runs in R: it needs
    ``match.call()`` against the real function's formals).
    """
    if call_text is None or call_text == "":
        return None
    return _r_eval_child(f".r_call_fun_arg({_r_string(call_text)})", rscript)  # type: ignore[no-any-return]


def _r_cap_by_fun(
    v: str, stat_name: str, max_rows: int = 40, rscript: str | None = None
) -> dict[str, Any] | None:
    """Reduce a classless aggregate (R source *v*) keyed by its ``FUN``.

    Port of ``R/r-capture.R::.r_cap_by_fun()`` (runs in R).
    """
    return _r_eval_child(  # type: ignore[no-any-return]
        f".r_cap_by_fun({v}, {_r_string(stat_name)}, max_rows = {int(max_rows)}L)", rscript
    )


def _r_capture_value(
    v: str, call_text: str | None = None, rscript: str | None = None
) -> dict[str, Any] | None:
    """Reduce one top-level value (R source *v*) to a capture record or ``None``.

    Port of ``R/r-capture.R::.r_capture_value()`` (runs in R).
    """
    ct = "NULL" if call_text is None else _r_string(call_text)
    return _r_eval_child(f".r_capture_value({v}, {ct})", rscript)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------


def _is_na_scalar(x: Any) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _as_int(x: Any) -> int | None:
    if _is_na_scalar(x):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _r_captures_to_tables(
    caps: Sequence[Mapping[str, Any]] | None,
    source_label: str | None = None,  # noqa: ARG001 - R signature; unused in R too
    code_lines: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Turn capture records into :func:`read_r_output`-shaped tables.

    Port of ``R/r-capture.R::.r_captures_to_tables()``.
    """
    from pytacheck.statout.r_output import (
        _chr_frame,
        _r_call_object_ref,
        _r_dollar,
        _r_dollar_found,
        _r_root_ref_map,
    )
    from pytacheck.statout.stat_tables import _stat_num_to_chr

    if not caps:
        return []
    root_map = _r_root_ref_map(code_lines) if code_lines is not None else None

    def resolve_ref(ref: str | None) -> str | None:
        if ref is None:
            return ref
        if root_map is not None and ref in root_map:
            return root_map[ref]
        return ref

    out: list[dict[str, Any]] = []
    for rec in caps:
        rows = _r_dollar(rec, "rows")
        if not rows:
            continue
        keys: list[str] = []
        for r in rows:
            for k in _r_dollar(r, "stats") or {}:
                if k not in keys:
                    keys.append(k)
        if not keys:
            continue
        names = ["label"]
        cols: list[list[Any]] = [[_label_chr(*_r_dollar_found(r, "label")) for r in rows]]
        for k in keys:
            col = []
            for r in rows:
                v = (_r_dollar(r, "stats") or {}).get(k)
                col.append("" if v is None else _stat_num_to_chr(_as_numeric_value(v)))
            if k in names:
                cols[names.index(k)] = col
            else:
                names.append(k)
                cols.append(col)
        df = _chr_frame(names, cols)
        analysis = _r_dollar(rec, "analysis")
        line = _r_dollar(rec, "line")
        out.append(
            {
                "analysis": analysis,
                "title": analysis,
                "data": df,
                "line": _as_int(line),
                "line_seq": 1,
                "call_fn": _r_method_to_fn(_r_dollar(rec, "method")),
                "model_ref": resolve_ref(_r_call_object_ref(_r_dollar(rec, "call_text") or "")),
                "captured": True,
            }
        )
    lines = [_as_int(x["line"]) for x in out]
    for i, x in enumerate(out):
        same = [j for j, ln in enumerate(lines) if ln is not None and ln == lines[i]]
        x["line_seq"] = same.index(i) + 1 if i in same else None
    return out


def _label_chr(found: bool, x: Any) -> str | None:
    """``as.character(r$label %||% "")`` (a present ``None`` is ``NA``)."""
    from pytacheck._r.base import as_character

    if not found:
        return ""
    if isinstance(x, list):
        x = x[0] if x else None
    return as_character(x)


def _as_numeric_value(v: Any) -> float | None:
    """R ``as.numeric()`` of one captured value."""
    from pytacheck.statout.r_output import _r_as_numeric

    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        return _r_as_numeric(v)
    if isinstance(v, list) and len(v) == 1:
        return _as_numeric_value(v[0])
    return None


def _r_merge_captures(
    cap_tabs: Sequence[dict[str, Any]] | None, txt_tabs: Sequence[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Merge captured-object tables with text-parsed tables for one script.

    Port of ``R/r-capture.R::.r_merge_captures()``: a capture wins for every
    source line it covers; text tables without a line are dropped while
    captures exist. Results sharing a line are renumbered.
    """
    cap = list(cap_tabs or [])
    txt = list(txt_tabs or [])
    if not cap:
        return txt
    if not txt:
        return cap
    from pytacheck.statout.r_output import _r_dollar

    cap_lines = {ln for ln in (_as_int(_r_dollar(x, "line")) for x in cap) if ln is not None}
    keep = []
    for x in txt:
        ln = _as_int(_r_dollar(x, "line"))
        keep.append(ln is not None and ln not in cap_lines)
    out = [dict(x) for x in cap] + [dict(x) for x, k in zip(txt, keep, strict=True) if k]
    lines = [_as_int(_r_dollar(x, "line")) for x in out]
    for i, x in enumerate(out):
        same = [j for j, ln in enumerate(lines) if ln is not None and ln == lines[i]]
        x["line_seq"] = same.index(i) + 1 if same else 1
    return out


_METHOD_FNS = (
    ("shapiro", "shapiro.test"),
    ("wilcoxon|mann-whitney", "wilcox.test"),
    ("kruskal", "kruskal.test"),
    ("bartlett", "bartlett.test"),
    ("fisher", "fisher.test"),
    ("mcnemar", "mcnemar.test"),
    ("chi-squared|chi-square", "chisq.test"),
    ("proportion", "prop.test"),
    ("kolmogorov", "ks.test"),
    ("friedman", "friedman.test"),
    ("f test", "var.test"),
    ("correlation", "cor.test"),
    ("t-test", "t.test"),
)


def _r_method_to_fn(method: str | None) -> str:
    """Map an ``htest`` ``method`` string onto the function that produced it.

    Port of ``R/r-capture.R::.r_method_to_fn()``.
    """
    if isinstance(method, list):
        method = method[0] if method else None
    if method is None:
        return ""
    m = str(method).lower()
    if m == "":
        return ""
    for pat, fn in _METHOD_FNS:
        if grepl(pat, m):
            return fn
    return ""
