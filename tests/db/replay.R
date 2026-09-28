# Replay metacheck's recorded httptest2 API fixtures without httptest2.
#
# Used by parity cases in parity/cases/db.yaml so that metacheck's network
# functions (crossref_doi(), datacite_doi(), pubpeer_comments(), ...) can be
# compared with pytacheck on the recorded responses, offline. Mirrors
# tests/httpmock.py (the Python replayer): the mock file name follows
# httptest2::build_mock_url() and an unrecorded request gets a 404.
#
#   source(file.path(root, "tests/db/replay.R"))
#   db_replay(crossref_doi("10.1177/0956797614520714"))

.db_mock_path <- function(req) {
  url <- sub("^.*?://", "", req$url)
  parts <- strsplit(url, "?", fixed = TRUE)[[1]]
  f <- gsub(":", "-", sub("/$", "", parts[[1]]))
  hash <- function(x) substr(digest::digest(x), 1, 6)
  if (length(parts) > 1) f <- paste0(f, "-", hash(parts[[2]]))
  data <- req$body$data
  if (!is.null(data)) {
    if (is.raw(data)) data <- rawToChar(data)
    if (identical(req$body$type, "json")) {
      data <- do.call(jsonlite::toJSON, c(list(data), req$body$params))
    }
    f <- paste0(f, "-", hash(as.character(unclass(data))))
  }
  method <- req$method %||% if (is.null(data)) "GET" else "POST"
  if (method != "GET") f <- paste0(f, "-", method)
  f
}

.db_mock_response <- function(req, dir) {
  path <- file.path(dir, .db_mock_path(req))
  types <- c(json = "application/json", html = "text/html; charset=utf-8",
             xml = "application/xml", txt = "text/plain; charset=utf-8")
  r_file <- paste0(path, ".R")
  if (file.exists(r_file)) {
    find_mock_file <- function(x) file.path(dir, x)
    resp <- eval(parse(r_file, encoding = "UTF-8"))
    if (inherits(resp$body, "httr2_path")) {
      body_file <- unclass(resp$body)
      resp$body <- if (file.exists(body_file)) readBin(body_file, "raw", file.size(body_file)) else raw(0)
    }
    resp$url <- req$url
    return(resp)
  }
  for (ext in names(types)) {
    f <- paste0(path, ".", ext)
    if (file.exists(f)) {
      return(httr2::response(
        status_code = 200, url = req$url, method = req$method %||% "GET",
        headers = list(`content-type` = types[[ext]]),
        body = readBin(f, "raw", file.size(f))
      ))
    }
  }
  httr2::response(
    status_code = 404, url = req$url,
    headers = list(`content-type` = "application/json"),
    body = charToRaw(sprintf('{"error": "no recorded fixture for %s"}', .db_mock_path(req)))
  )
}

db_replay <- function(code, dir = "apis") {
  if (!grepl("^/", dir)) {
    dir <- file.path(root, "upstream", "metacheck", "tests", "testthat", dir)
  }
  old <- options(metacheck.email = "metacheck@scienceverse.org")
  on.exit(options(old), add = TRUE)
  testthat::with_mocked_bindings(
    online = function(...) TRUE,
    .package = "metacheck",
    code = httr2::with_mocked_responses(
      function(req) .db_mock_response(req, dir),
      code
    )
  )
}
