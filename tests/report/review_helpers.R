# R side of the report review parity cases (parity/cases/report_review.yaml).
# Python twin: tests/report/review_helpers.py. Builds on parity_helpers.R.

source(file.path("tests", "report", "parity_helpers.R"))

# a condition message (or the value, when there is no error)
rv_error <- function(expr) {
  tryCatch(expr, error = function(e) list(error = conditionMessage(e)))
}

# report() on a paper list, written into a temporary folder
rv_report_list <- function(paper, modules, files, output_format = "qmd", args = list()) {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  d <- tempfile()
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  res <- suppressWarnings(report(paper, mods, file.path(d, files), output_format, args))
  sp <- attr(res, "save_path")
  list(
    names = names(res),
    failed = unname(vapply(res, is.null, logical(1))),
    save_path = unname(lapply(sp, function(p) if (is.null(p)) NA_character_ else sub(paste0(d, "/"), "", p, fixed = TRUE))),
    files = sort(list.files(d)),
    traffic_lights = unname(lapply(res, function(r) {
      if (is.null(r)) NA_character_ else unname(vapply(r, `[[`, character(1), "traffic_light"))
    })),
    text = unname(lapply(sort(list.files(d)), function(f) {
      rp_mask(gsub("tests/report/modules/([a-z_]+)\\.[Rr]", "\\1", paste(readLines(file.path(d, f)), collapse = "\n")))
    }))
  )
}

# the class-free structure of a report_module_run() output
rv_module_run_list <- function(paper, modules, args = list()) {
  rv_error(rp_report_module_run(paper, modules, args))
}
