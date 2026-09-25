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
      file_url = if (is.null(repo$file_url)) NA_character_ else paste0(repo$file_url, rel),
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
