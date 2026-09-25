# Regenerate tests/statout_core/data/review_capture_expected/<name>.json: what
# metacheck's own .r_capture_runner() (run with callr::r(), as
# repro_run_scripts() does) printed and captured, plus .r_captures_to_tables()
# and .r_merge_captures() of it, in the parity harness' canonical encoding.
#
#   LANG=C.UTF-8 TZ=UTC $PYTACHECK_RSCRIPT tests/statout_core/make_capture_expected.R review_shadow
#
# (run from the repository root)
suppressPackageStartupMessages(library(metacheck))
source(file.path("parity", "r", "canonical.R"))
for (name in commandArgs(TRUE)) {
  script <- normalizePath(file.path("tests", "statout_core", "data", paste0(name, ".R")))
  out_file <- tempfile()
  err_file <- tempfile()
  cap_file <- tempfile(fileext = ".rds")
  res <- tryCatch(
    callr::r(metacheck:::.r_capture_runner(),
             args = list(script = script, wd = dirname(script), capture_file = cap_file,
                         helpers = metacheck:::.r_capture_helpers()),
             error = "error", stdout = out_file, stderr = err_file),
    error = function(e) e)
  stdout <- readChar(out_file, file.size(out_file), useBytes = TRUE)
  Encoding(stdout) <- "UTF-8"
  captures <- if (file.exists(cap_file)) readRDS(cap_file) else NULL
  code <- readLines(script, encoding = "UTF-8")
  tabs <- metacheck:::.r_captures_to_tables(captures, code_lines = code)
  merged <- metacheck:::.r_merge_captures(
    tabs, read_r_output(sub("\n$", "", stdout), code_lines = code))
  out <- list(
    stdout = stdout,
    error = if (inherits(res, "error")) conditionMessage(res) else NULL,
    tables = pc_canonical(list(tabs = tabs, merged = merged)))
  writeLines(jsonlite::toJSON(out, auto_unbox = TRUE, null = "null", digits = NA, pretty = TRUE),
             file.path("tests", "statout_core", "data", "review_capture_expected",
                       paste0(name, ".json")), useBytes = TRUE)
}
