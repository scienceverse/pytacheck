# R side of the reproducibility_check review parity cases
# (parity/cases/mod_repro_review.yaml). The regular cases compare the report as
# prose, which skips every scroll_table() chunk; rv_tables() pulls the data
# frame out of each of the report's table chunks so they can be compared too.
# The Python twin is tests/mod_repro/rv_helpers.py.

source("tests/mod_repro/rc_helpers.R", local = TRUE)

rv_chunk_table <- function(x) {
  code <- sub("(?s).*# table data -+\n(.*?)\n+# display table.*", "\\1", x, perl = TRUE)
  env <- new.env()
  eval(parse(text = code), envir = env)
  tb <- env$table
  if ("Time (s)" %in% names(tb)) tb[["Time (s)"]] <- rep(0, nrow(tb))
  tb
}

rv_tables <- function(mo) {
  out <- list()
  for (x in unlist(mo$report, use.names = FALSE)) {
    if (!is.character(x) || !grepl("# table data", x, fixed = TRUE)) next
    out[[length(out) + 1]] <- rv_chunk_table(x)
  }
  out
}

# The module output with its report tables as an extra element.
rv_with_tables <- function(mo) {
  mo <- unclass(mo)
  mo$report_tables <- rv_tables(mo)
  mo
}

rv_run <- function(name, ...) rv_tables(rc_run(name, ...))
rv_run_mocked <- function(name, ...) rv_tables(rc_run_mocked(name, ...))

# The module on a scenario's paper, with its upstream outputs read from a
# committed metacheck capture_module_tables() file (tests/mod_repro/fixtures/
# review_tables/p1.rds, the "pipeline" scenario's chain) via tables_dir.
rv_run_saved <- function(name, ...) {
  paper <- rc_paper(rc_specs()$scenarios[[name]])
  rc_scrub(module_run(paper, "reproducibility_check",
                      tables_dir = "tests/mod_repro/fixtures/review_tables", ...))
}

# A real piped chain on the local fixture project: data_check |> psychds_check
# |> code_check |> reproducibility_check (the module reads the three
# upstream outputs from the chain instead of running them itself).
rv_chain <- function(...) {
  proj <- "tests/mod_repro/fixtures/project"
  p <- test_paper("x")
  p$paper_id <- "p1"
  x <- module_run(p, "data_check", local_path = proj, local_only = TRUE)
  x <- module_run(x, "psychds_check", local_path = proj, local_only = TRUE)
  x <- module_run(x, "code_check", local_path = proj, local_only = TRUE)
  rc_scrub(module_run(x, "reproducibility_check", ...))
}
