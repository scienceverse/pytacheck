# R halves of the parity cases in parity/cases/repo_download.yaml that need
# more than one call (mirrored by tests/repo_download/_parity_helpers.py).
# A case sources this file and calls one of the functions through do.call():
#
#   r: "do.call"
#   args:
#     what: {$expr: {r: "local({source(file.path(root, 'tests/repo_download/parity_helpers.R'), local = TRUE); rd_expand})", py: ...}}
#     args: {fn: ".expand_zip", fixture: "mixed.zip"}
#
# Fixtures are read from tests/repo_download/data; archives are expanded /
# files downloaded into a fresh temporary directory, and temporary paths are
# replaced by <TMP> / <SESSION> (and the fixture directory by <DATA>).

rd_dir <- file.path(root, "tests", "repo_download", "data")

rd_data <- function(rel) file.path(rd_dir, rel)

rd_row <- function(name) {
  data.frame(
    repo_url = "r", file_name = name, file_path = name, file_url = "u",
    file_location = "<unset>", file_size = 1, file_type = "archive",
    paper_id = "p.1", data_type = "unknown", doc_role = NA_character_,
    data_format = "tabular", group = NA_character_, stringsAsFactors = FALSE)
}

rd_expand <- function(fn, fixture, skip_types = "materials", minimal = FALSE) {
  d <- tempfile("pc_rd_")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  f <- file.path(d, fixture)
  file.copy(rd_data(fixture), f)
  row <- rd_row(fixture)
  if (isTRUE(minimal)) row <- row[, c("repo_url", "file_name")]
  expand <- getFromNamespace(sub("^_", ".", fn), "metacheck")
  out <- expand(f, row, skip_types = skip_types)
  if ("file_location" %in% names(out))
    out$file_location <- sub(d, "<TMP>", out$file_location, fixed = TRUE)
  out
}

rd_fetch_members <- function(url, names = NULL) {
  d <- tempfile("pc_rd_")
  on.exit(unlink(d, recursive = TRUE))
  out <- metacheck:::.zip_fetch_members(url, names = names, dest = d)
  if (!is.null(out) && "path" %in% names(out))
    out$path <- sub(d, "<TMP>", out$path, fixed = TRUE)
  out
}

rd_download <- function(files, file_url = NULL, twice = FALSE, ...) {
  if (!is.null(file_url)) {
    local <- !is.na(file_url) & startsWith(file_url, "data/")
    file_url[local] <- paste0("file://", rd_data(substring(file_url[local], 6)))
    files$file_url <- file_url
  }
  sess <- tempfile("pc_sess_")
  old <- options(metacheck.repo_cache.session_dir = sess,
                 metacheck.repo_cache.notified = TRUE)
  on.exit({
    options(old)
    unlink(sess, recursive = TRUE)
  })
  dl <- download_repo_files(files, ...)
  if (isTRUE(twice)) dl <- download_repo_files(files, ...)
  fix <- function(x) gsub(rd_dir, "<DATA>", x, fixed = TRUE)
  failed <- attr(dl, "failed")
  failed$file_url <- fix(failed$file_url)
  failed$error <- fix(failed$error)
  list(
    file_location = sub(sess, "<SESSION>", dl$file_location, fixed = TRUE),
    gated = attr(dl, "gated"),
    oversize_skipped = attr(dl, "oversize_skipped"),
    failed = failed
  )
}

rd_rate_limit_response <- function(status, headers, reset_offset = NULL) {
  headers <- as.list(headers)
  if (!is.null(reset_offset))
    headers[["ratelimit-reset"]] <- as.character(round(as.numeric(Sys.time()) + reset_offset))
  httr2::response(status_code = status, url = "https://x.org/a", headers = headers, body = raw(0))
}
