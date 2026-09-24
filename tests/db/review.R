# Helpers for the adversarial-review parity cases (parity/cases/db_review.yaml).
#
# Bulk comparisons of metacheck's Crossref parsing on every recorded fixture
# (upstream tests/testthat/apis). The Python halves live in
# tests/db/review_helpers.py.
#
#   source(file.path(root, "tests/db/review.R"))

.review_apis <- file.path(root, "upstream", "metacheck", "tests", "testthat", "apis")

# recorded api.labs.crossref.org/works/<doi> bodies that hold a work
review_work_files <- function() {
  dir <- file.path(.review_apis, "api.labs.crossref.org", "works")
  files <- list.files(dir, "\\.json$")
  files <- files[order(files, method = "radix")]
  keep <- vapply(files, \(f) {
    j <- jsonlite::read_json(file.path(dir, f))
    is.list(j$message) && !is.null(names(j$message))
  }, logical(1))
  file.path(dir, files[keep])
}

review_work <- function(f) jsonlite::read_json(f)$message

# .crossref_parse_item() on every recorded work, one select at a time;
# errors become their message
review_parse_each <- function(selects) {
  files <- review_work_files()
  lapply(selects, \(sel) {
    lapply(files, \(f) {
      tryCatch(
        metacheck:::.crossref_parse_item(review_work(f), sel),
        error = \(e) paste("ERROR:", conditionMessage(e))
      )
    })
  })
}

# .crossref_query_parse() of all recorded works as one item list
review_query_parse_all <- function(select, min_score = 0) {
  items <- lapply(review_work_files(), review_work)
  tryCatch(
    metacheck:::.crossref_query_parse(items, min_score, select),
    error = \(e) paste("ERROR:", conditionMessage(e))
  )
}

# every recorded search response (api.crossref.org/works-*.json)
review_query_files <- function() {
  dir <- file.path(.review_apis, "api.crossref.org")
  files <- list.files(dir, "^works-.*\\.json$")
  file.path(dir, files[order(files, method = "radix")])
}

review_query_parse_each <- function(select, min_score = 50) {
  lapply(review_query_files(), \(f) {
    j <- jsonlite::read_json(f)
    tryCatch(
      metacheck:::.crossref_query_parse(j$message$items, min_score, select),
      error = \(e) paste("ERROR:", conditionMessage(e))
    )
  })
}

# R's error message (or the value) of an expression
review_try <- function(expr) {
  tryCatch(expr, error = \(e) paste("ERROR:", conditionMessage(e)))
}
