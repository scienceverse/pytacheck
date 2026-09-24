# Run parity cases against the installed metacheck and write golden JSON.
#
# Usage:
#   Rscript parity/r/run_cases.R <repo_root> <cases.yaml> [<cases.yaml> ...] [--only id1,id2]
#
# Writes parity/golden/<area>/<case id>.json for each case. Run through
# `python -m parity generate`, which sets the locale (C.UTF-8) and paths.

suppressPackageStartupMessages({
  library(metacheck)
  library(jsonlite)
  library(yaml)
})

argv <- commandArgs(trailingOnly = TRUE)
root <- normalizePath(argv[[1]])
only <- NULL
files <- character(0)
i <- 2
while (i <= length(argv)) {
  if (argv[[i]] == "--only") {
    only <- strsplit(argv[[i + 1]], ",")[[1]]
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

rpath <- function(p) {
  if (grepl("^/", p)) p else file.path(root, p)
}

# Decode a YAML argument spec into an R value -------------------------------
decode <- function(x) {
  if (is.list(x) && length(x) == 1 && !is.null(names(x)) &&
      startsWith(names(x)[[1]], "$")) {
    key <- names(x)[[1]]
    val <- x[[1]]
    return(switch(key,
      "$paper" = if (identical(val, "demo")) demopaper() else metacheck::read(rpath(val)),
      "$read" = metacheck::read(vapply(unlist(val), rpath, character(1))),
      "$test_paper" = {
        txt <- unlist(val$text %||% LETTERS)
        url <- unlist(val$url %||% character(0))
        test_paper(as.character(txt), as.character(url))
      },
      "$df" = {
        cols <- lapply(val, function(col) {
          if (is.list(col)) {
            vapply(col, function(e) if (is.null(e)) NA else e, FUN.VALUE = col[[1]] %||% NA)
          } else col
        })
        as.data.frame(cols, stringsAsFactors = FALSE, check.names = FALSE)
      },
      "$chr" = as.character(unlist(val) %||% character(0)),
      "$int" = as.integer(unlist(val) %||% integer(0)),
      "$dbl" = as.double(unlist(val) %||% double(0)),
      "$lgl" = as.logical(unlist(val) %||% logical(0)),
      "$list" = lapply(val, decode),
      "$null" = NULL,
      "$NA" = NA,
      "$file" = rpath(val),
      "$expr" = eval(parse(text = val$r), envir = new.env(parent = globalenv())),
      "$call" = {
        fn <- get_fn(val$r)
        do.call(fn, decode_args(val$args %||% list()))
      },
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
for (f in files) {
  spec <- yaml::read_yaml(f)
  area <- spec$area
  outdir <- file.path(root, "parity", "golden", area)
  dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
  for (case in spec$cases) {
    if (!is.null(only) && !(case$id %in% only)) next
    if (isTRUE(case$skip_r)) next
    total <- total + 1
    res <- run_case(case)
    if (!isTRUE(res$ok)) failed <- failed + 1
    json <- pc_to_json(res, pretty = TRUE)
    writeLines(enc2utf8(as.character(json)), file.path(outdir, paste0(case$id, ".json")),
               useBytes = TRUE)
    cat(sprintf("[%s] %s/%s%s\n", if (res$ok) "ok" else "ERR", area, case$id,
                if (res$ok) "" else paste0(": ", res$error)))
  }
}
cat(sprintf("%d cases, %d raised errors in R\n", total, failed))
