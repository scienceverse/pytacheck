# R side of the code_check parity cases (parity/cases/mod_code.yaml).
#
# code_check reads the repository file listing of an earlier repo_check (or
# data_check) run through get_prev_outputs(). tests/mod_code/scenarios.json
# describes such listings; cc_prev() builds the repo_check output (a
# metacheck_module_output whose paper is a test paper / paperlist with fixed
# ids) so module_run() chains code_check onto it, as metacheck's own tests do.
# The Python twin (and the scenario keys) is tests/mod_code/helpers.py.
# Paths are relative to the repository root (the parity runner's working
# directory).

cc_scenarios <- jsonlite::fromJSON("tests/mod_code/scenarios.json", simplifyVector = FALSE)

cc_base_cols <- c("paper_id", "repo_name", "repo_url", "file_name", "file_path",
                  "file_url", "file_location", "file_size")

.cc_repo_rows <- function(repo, default_pid) {
  lapply(repo$files, function(rel) {
    rel <- unlist(rel)
    path <- file.path(repo$dir, rel)
    local <- repo$local %||% TRUE
    list(
      paper_id = repo$paper_id %||% default_pid,
      repo_name = repo$repo_name %||% basename(repo$dir),
      repo_url = repo$repo_url %||% NA_character_,
      file_name = basename(rel),
      file_path = rel,
      file_url = if (is.null(repo$file_url)) NA_character_
        else if (identical(repo$file_url, "local")) paste0("file://", normalizePath(path))
        else paste0(repo$file_url, rel),
      file_location = if (isTRUE(local)) path else NA_character_,
      file_size = as.double(file.size(path))
    )
  })
}

.cc_frame <- function(rows, drop = character(0)) {
  cols <- cc_base_cols
  for (r in rows) cols <- c(cols, setdiff(names(r), cols))
  out <- lapply(cols, function(cl) {
    vals <- lapply(rows, function(r) r[[cl]])
    nonnull <- Filter(Negate(is.null), vals)
    numeric <- cl == "file_size" ||
      (length(nonnull) > 0 && all(vapply(nonnull, is.numeric, logical(1))))
    if (numeric) {
      vapply(vals, function(v) if (is.null(v)) NA_real_ else as.double(v), double(1))
    } else {
      vapply(vals, function(v) if (is.null(v)) NA_character_ else as.character(v), character(1))
    }
  })
  names(out) <- cols
  df <- as.data.frame(out, stringsAsFactors = FALSE, check.names = FALSE)
  if (length(rows) == 0) df <- df[0, , drop = FALSE]
  df[, setdiff(names(df), unlist(drop)), drop = FALSE]
}

cc_table <- function(name) {
  sc <- cc_scenarios[[name]]
  pids <- unlist(sc$papers %||% list("p1"))
  rows <- list()
  for (repo in sc$repos) rows <- c(rows, .cc_repo_rows(repo, pids[[1]]))
  rows <- c(rows, sc$rows)
  .cc_frame(rows, sc$drop %||% character(0))
}

cc_paper <- function(name, text = "Some text.") {
  sc <- cc_scenarios[[name]]
  if (isTRUE(sc$demo)) return(demopaper())
  if (!is.null(sc$read)) return(read(unlist(sc$read)))
  pids <- unlist(sc$papers %||% list("p1"))
  papers <- lapply(pids, function(pid) {
    p <- test_paper(text)
    p$paper_id <- pid
    p
  })
  if (length(papers) == 1) papers[[1]] else do.call(paperlist, papers)
}

.cc_output <- function(module, paper, pids, prev, ...) {
  structure(c(list(
    module = module,
    title = c(repo_check = "Repository Check", data_check = "Data Check")[[module]],
    section = "general",
    report = "",
    traffic_light = "info",
    summary_text = NULL,
    summary_table = data.frame(paper_id = pids),
    paper = paper,
    prev_outputs = prev
  ), list(...)), class = "metacheck_module_output")
}

cc_prev <- function(name, paper = NULL) {
  sc <- cc_scenarios[[name]]
  if (is.null(paper)) paper <- cc_paper(name)
  pids <- unlist(sc$papers %||% list("p1"))
  table <- cc_table(name)
  repo <- .cc_output("repo_check", paper, pids, list(), table = table)
  if (is.null(sc$structure)) return(repo)
  structure_tbl <- dplyr::bind_rows(table, .cc_frame(sc$structure, sc$drop %||% character(0)))
  stripped <- repo
  stripped$paper <- NULL
  stripped$summary_table <- NULL
  stripped$prev_outputs <- NULL
  .cc_output("data_check", paper, pids, list(repo_check = stripped),
             table = NULL, structure = structure_tbl)
}

cc_run <- function(name, ...) module_run(cc_prev(name), "code_check", ...)

# The data of the report's table blocks (each an R chunk holding
# `table <- <deparsed data frame>`), in order.
cc_report_tables <- function(name, ...) {
  mo <- cc_run(name, ...)
  chunks <- Filter(function(b) grepl("```{r}", b, fixed = TRUE), mo$report)
  lapply(chunks, function(b) {
    code <- sub("(?s).*\ntable <- (.*?)\n\n# display table.*", "\\1", b, perl = TRUE)
    tb <- eval(parse(text = code))
    rownames(tb) <- NULL
    tb
  })
}

# Machine-independent paths in a code_check output: the repository root becomes
# <ROOT> and the per-session download directory <DL> (download cases).
cc_norm <- function(mo) {
  root <- normalizePath(".")
  norm <- function(x) {
    x <- gsub(root, "<ROOT>", x, fixed = TRUE)
    sub("^.*/metacheck-repo-files/", "<DL>/", x)
  }
  for (cl in intersect(c("file_url", "file_location", "error"), names(mo$table))) {
    mo$table[[cl]] <- norm(mo$table[[cl]])
  }
  fl <- mo$version_pin$file_location
  if (length(fl) > 0) mo$version_pin$file_location <- stats::setNames(norm(unname(fl)), names(fl))
  mo$report <- norm(mo$report)
  mo
}

# The table's read errors with machine-independent paths and without the
# " in current working directory ('<cwd>')" suffix readr adds for a relative
# path: pytacheck's code_read() (pytacheck.codecheck.core) does not add it
# yet, and these cases check which path code_check() hands to the reader.
cc_errors <- function(name, ...) {
  mo <- cc_norm(cc_run(name, ...))
  err <- mo$table$error
  if (is.null(err)) return(NULL)
  sub(" in current working directory \\('<ROOT>'\\)\\.$", ".", err)
}

# The manifests code_check(manifest = ) writes for scenario `name`: a named
# list (file name -> parsed JSON) of every file in a fresh manifest directory,
# or of the single `one.manifest.json` when `file = TRUE`.
cc_manifest <- function(name, file = FALSE, ...) {
  d <- tempfile("manifest")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE), add = TRUE)
  target <- if (isTRUE(file)) file.path(d, "one.manifest.json") else d
  cc_run(name, manifest = target, ...)
  fs <- sort(list.files(d))
  stats::setNames(lapply(file.path(d, fs), jsonlite::fromJSON, simplifyVector = FALSE), fs)
}

# code_check with `local_path` (a fresh repo_check of that directory), on a
# test paper; paths made machine-independent.
cc_local <- function(path, ...) {
  p <- test_paper("Some text.")
  p$paper_id <- "p1"
  cc_norm(module_run(p, "code_check", local_path = path, ...))
}

# The data_check output of scenario `name` with a zero-row `structure`: R's
# `%||%` keeps it (it is not NULL), so repo_check's table is not used.
cc_prev_empty_structure <- function(name) {
  prev <- cc_prev(name)
  prev$structure <- prev$structure[0, , drop = FALSE]
  prev
}
