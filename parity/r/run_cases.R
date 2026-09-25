# Run parity cases against the installed metacheck and write golden JSON.
#
# Usage:
#   Rscript parity/r/run_cases.R <repo_root> <cases.yaml> [<cases.yaml> ...]
#           [--only id1,id2] [--out DIR]
#
# Writes parity/golden/<area>/<case id>.json (with --out, DIR/<area>/<case
# id>.json) for each case. Run through `python -m parity generate` or `python -m
# parity accuracy --generate`, which set the locale (C.UTF-8) and paths.

suppressPackageStartupMessages({
  library(metacheck)
  library(jsonlite)
  library(yaml)
})

# R turns non-ASCII argument names (c("\u03b1" = "alpha")) into "<U+03B1>" when a
# package is installed under a non-UTF-8 locale, silently changing its behaviour.
local({
  ns <- asNamespace("metacheck")
  mangled <- Filter(function(n) {
    f <- get(n, envir = ns)
    is.function(f) && any(grepl("<U\\+[0-9A-Fa-f]{4,}>", deparse(f), perl = TRUE))
  }, ls(ns, all.names = TRUE))
  if (length(mangled)) {
    stop(
      "metacheck was installed under a non-UTF-8 locale (mangled names in ",
      paste(head(mangled, 3), collapse = ", "), "): reinstall it with ",
      "LANG=C.UTF-8 LC_ALL=C.UTF-8 R CMD INSTALL --no-test-load upstream/metacheck",
      call. = FALSE
    )
  }
})

argv <- commandArgs(trailingOnly = TRUE)
root <- normalizePath(argv[[1]])
only <- NULL
out <- file.path(root, "parity", "golden")
files <- character(0)
i <- 2
while (i <= length(argv)) {
  if (argv[[i]] == "--only") {
    only <- strsplit(argv[[i + 1]], ",")[[1]]
    i <- i + 2
  } else if (argv[[i]] == "--out") {
    out <- argv[[i + 1]]
    i <- i + 2
  } else {
    files <- c(files, argv[[i]])
    i <- i + 1
  }
}

source(file.path(root, "parity", "r", "canonical.R"))
source(file.path(root, "parity", "r", "helpers.R"))
options(warn = 1, scienceverse.verbose = FALSE)
Sys.setenv(TZ = "UTC")
# Same isolation as metacheck's tests/testthat/setup.R: throwaway caches, no
# session OSF listing cache.
.pc_cache <- file.path(tempdir(), "pytacheck-parity-cache")
dir.create(.pc_cache, showWarnings = FALSE, recursive = TRUE)
options(metacheck.cache.dir = .pc_cache, metacheck.osf.cache = FALSE)
Sys.setenv(METACHECK_LLM_CACHE_DIR = file.path(.pc_cache, "llm"))
mock_root <- file.path(root, "upstream", "metacheck", "tests", "testthat")
mock_path <- function(d) {
  if (grepl("^/", d)) d
  else if (startsWith(d, "tests/") || startsWith(d, "parity/")) file.path(root, d)
  else file.path(mock_root, d)
}

upstream <- tryCatch({
  as.character(utils::packageDescription("metacheck")$Version)
}, error = function(e) NA_character_)

# Deterministic paper ids ----------------------------------------------------
# paper() names a paper without an id by the md5 of Sys.time(), so every
# test_paper() got a new id on every run and the goldens could never be
# regenerated identically (docs/UPSTREAM_ISSUES.md U11). Here paper() numbers
# them instead: the n-th id-less paper of a case is the first 14 hex digits of
# md5("pytacheck-parity-<n>"). parity/cases.py numbers pytacheck's id-less
# papers the same way, so the ids agree whenever both sides create their papers
# in the same order (as test papers are).
.pc_ids <- new.env()
.pc_ids$n <- 0L
.pc_next_id <- function() {
  .pc_ids$n <- .pc_ids$n + 1L
  substr(unname(tools::md5sum(bytes = charToRaw(sprintf("pytacheck-parity-%d", .pc_ids$n)))), 1, 14)
}
local({
  ns <- asNamespace("metacheck")
  orig <- get("paper", envir = ns)
  wrapped <- function(id = NULL, ...) orig(if (is.null(id)) .pc_next_id() else id, ...)
  for (env in list(ns, as.environment("package:metacheck"))) {
    unlockBinding("paper", env)
    assign("paper", wrapped, envir = env)
    lockBinding("paper", env)
  }
})

rpath <- function(p) {
  if (grepl("^/", p)) p else file.path(root, p)
}

# Papers read from files are read once per session: R values are immutable,
# and the accuracy report runs every module on the same corpus papers. The
# read's warnings are signalled again on every later use, so each case's golden
# still lists them. A read that numbered an id-less paper (.pc_next_id) is not
# kept, since that id depends on the case.
.pc_papers <- new.env()
read_once <- function(key, read) {
  hit <- .pc_papers[[key]]
  if (!is.null(hit)) {
    for (w in hit$warnings) warning(w)
    return(hit$value)
  }
  n <- .pc_ids$n
  caught <- list()
  value <- withCallingHandlers(read(), warning = function(w) caught[[length(caught) + 1]] <<- w)
  if (.pc_ids$n == n) .pc_papers[[key]] <- list(value = value, warnings = caught)
  value
}

# Decode a YAML argument spec into an R value -------------------------------
# A YAML list as an atomic vector, with YAML nulls as NA (unlist() drops them).
na_vec <- function(val) {
  if (!is.list(val)) return(val)
  if (length(val) == 0) return(NULL)
  unlist(lapply(val, function(e) if (is.null(e)) NA else e))
}

decode <- function(x) {
  if (is.list(x) && length(x) == 1 && !is.null(names(x)) &&
      startsWith(names(x)[[1]], "$")) {
    key <- names(x)[[1]]
    val <- x[[1]]
    return(switch(key,
      "$paper" = read_once(paste0("paper:", val), function() {
        if (identical(val, "demo")) demopaper() else metacheck::read(rpath(val))
      }),
      "$read" = {
        paths <- vapply(unlist(val), rpath, character(1))
        read_once(paste(c("read:", paths), collapse = "\n"), function() metacheck::read(paths))
      },
      "$test_paper" = {
        txt <- unlist(val$text %||% LETTERS)
        url <- unlist(val$url %||% character(0))
        test_paper(as.character(txt), as.character(url))
      },
      "$df" = {
        cols <- lapply(val, function(col) if (is.list(col)) na_vec(col) else col)
        as.data.frame(cols, stringsAsFactors = FALSE, check.names = FALSE)
      },
      "$chr" = as.character(na_vec(val) %||% character(0)),
      "$int" = as.integer(na_vec(val) %||% integer(0)),
      "$dbl" = as.double(na_vec(val) %||% double(0)),
      "$lgl" = as.logical(na_vec(val) %||% logical(0)),
      "$list" = lapply(val, decode),
      "$null" = NULL,
      "$NA" = NA,
      "$file" = rpath(val),
      "$expr" = eval(parse(text = val$r), envir = new.env(parent = globalenv())),
      "$call" = {
        fn <- get_fn(val$r)
        do.call(fn, decode_args(val$args %||% list()))
      },
      "$catch" = pc_catch(decode(val)),
      stop("unknown argument constructor ", key)
    ))
  }
  if (is.list(x) && !is.null(names(x))) return(lapply(x, decode))
  x
}

decode_args <- function(args) {
  out <- lapply(args, decode)
  # keep explicit NULL arguments
  out
}

get_fn <- function(name) {
  if (grepl(":::", name, fixed = TRUE)) {
    parts <- strsplit(name, ":::", fixed = TRUE)[[1]]
    return(getFromNamespace(parts[[2]], parts[[1]]))
  }
  if (grepl("::", name, fixed = TRUE)) {
    parts <- strsplit(name, "::", fixed = TRUE)[[1]]
    return(getExportedValue(parts[[1]], parts[[2]]))
  }
  if (exists(name, envir = asNamespace("metacheck"), inherits = FALSE)) {
    return(get(name, envir = asNamespace("metacheck")))
  }
  get(name, mode = "function")
}

run_case <- function(case) {
  set.seed(8675309)
  .pc_ids$n <- 0L
  warnings <- character(0)
  evaluate <- function(expr_fn) {
    if (!is.null(case$mock_dir)) {
      if (!requireNamespace("httptest2", quietly = TRUE)) stop("httptest2 is needed for mock_dir cases")
      httptest2::with_mock_dir(mock_path(case$mock_dir), expr_fn())
    } else {
      expr_fn()
    }
  }
  value <- withCallingHandlers(
    tryCatch(evaluate(function() {
      if (!is.null(case$module)) {
        args <- decode_args(case$args %||% list())
        paper <- args$paper
        args$paper <- NULL
        do.call(module_run, c(list(paper = paper, module = case$module), args))
      } else {
        fn <- get_fn(case$r)
        do.call(fn, decode_args(case$args %||% list()))
      }
    }), error = function(e) structure(list(message = conditionMessage(e)), class = "pc_error")),
    warning = function(w) {
      warnings <<- c(warnings, conditionMessage(w))
      invokeRestart("muffleWarning")
    },
    message = function(m) invokeRestart("muffleMessage")
  )
  ok <- !inherits(value, "pc_error")
  list(
    id = case$id,
    ok = ok,
    error = if (ok) NULL else value$message,
    warnings = as.list(warnings),
    value = if (ok) pc_canonical(value) else NULL,
    metacheck_version = upstream,
    r_version = R.version.string
  )
}

total <- 0
failed <- 0
# Goldens must not change from run to run or machine to machine. In every
# string of a golden:
# * the checkout directory is "<repo>" (parity/canonical.py writes the Python
#   results the same way) and R's session temporary directory "<tempdir>";
# * the random names tempfile() gives ("file1a2b3c4d5e") are "<tempfile>",
#   wherever they appear (a paper converted from a temporary file is named
#   after it);
# * a date-time stamp from while the case ran (a conversion's completed_at, a
#   listing's retrieved) is "<now>".
.pc_tempfile_name <- "^[A-Za-z_.-]*[0-9a-f]{8,}$"
.pc_stamp <- "[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?"

portable_json <- function(json, started, finished) {
  subs <- c(
    "<repo>" = root,
    "<tempdir>" = tempdir(),
    "<tempdir>" = normalizePath(tempdir(), mustWork = FALSE)
  )
  for (i in seq_along(subs)) {
    json <- gsub(subs[[i]], names(subs)[[i]], json, fixed = TRUE)
  }
  entries <- regmatches(json, gregexpr("<tempdir>/[^/\"'\\\\[:space:]]+", json))[[1]]
  entries <- unique(sub("^<tempdir>/", "", entries))
  for (name in entries) {
    stem <- tools::file_path_sans_ext(name)
    if (!grepl(.pc_tempfile_name, stem)) next
    json <- gsub(stem, "<tempfile>", json, fixed = TRUE)
  }
  stamps <- unique(regmatches(json, gregexpr(.pc_stamp, json))[[1]])
  for (stamp in stamps) {
    utc <- grepl("Z$", stamp)
    zone <- regmatches(stamp, regexpr("[+-][0-9]{2}:?[0-9]{2}$", stamp))
    text <- substr(sub("T", " ", stamp), 1, 19)
    t <- tryCatch(
      as.numeric(as.POSIXct(text, tz = if (utc || length(zone)) "UTC" else "",
                            format = "%Y-%m-%d %H:%M:%S")),
      error = function(e) NA_real_
    )
    if (length(zone)) {
      z <- as.integer(gsub(":", "", zone))
      t <- t - sign(z) * ((abs(z) %/% 100) * 3600 + (abs(z) %% 100) * 60)
    }
    if (!is.na(t) && t >= floor(as.numeric(started)) - 1 && t <= as.numeric(finished) + 1) {
      json <- gsub(stamp, "<now>", json, fixed = TRUE)
    }
  }
  json
}

# yaml reads a quoted, explicitly tagged float (!!float ".inf") as NA with a
# warning; read it as the float it names, as Python's YAML reader does.
yaml_float <- function(x) {
  switch(tolower(x),
    ".inf" = , "+.inf" = Inf,
    "-.inf" = -Inf,
    ".nan" = NaN,
    as.numeric(gsub("_", "", x, fixed = TRUE))
  )
}

for (f in files) {
  spec <- yaml::read_yaml(f, handlers = list(float = yaml_float))
  area <- spec$area
  outdir <- file.path(out, area)
  dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
  for (case in spec$cases) {
    if (!is.null(only) && !(case$id %in% only)) next
    if (isTRUE(case$skip_r)) next
    total <- total + 1
    started <- Sys.time()
    res <- run_case(case)
    finished <- Sys.time()
    if (!isTRUE(res$ok)) failed <- failed + 1
    json <- portable_json(as.character(pc_to_json(res, pretty = TRUE)), started, finished)
    writeLines(enc2utf8(json), file.path(outdir, paste0(case$id, ".json")),
               useBytes = TRUE)
    cat(sprintf("[%s] %s/%s%s\n", if (res$ok) "ok" else "ERR", area, case$id,
                if (res$ok) "" else paste0(": ", res$error)))
  }
}
cat(sprintf("%d cases, %d raised errors in R\n", total, failed))
