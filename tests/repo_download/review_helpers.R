# R halves of the review parity cases in parity/cases/repo_download_review.yaml
# (mirrored by tests/repo_download/_review_helpers.py). A case sources this file
# and calls one of the functions through do.call(), like parity_helpers.R.
#
# rv_serve() answers every httr2 request from a table of routes -- an in-process
# HTTP server that honours Range requests -- so zip_peek(), the member fetches
# and download_repo_files() run against real bytes on both sides:
#
#   routes: a list of list(url, fixture | body, status = 200, headers = list(),
#           range = TRUE, length = TRUE, method = NULL, suffix = NULL)
#
# `fixture` is a file under tests/repo_download/data; `range` = FALSE ignores a
# Range header (the whole body, with `status`); `length` = FALSE sends no
# Content-Length on a HEAD. `suffix` answers a suffix range ("bytes=-n"):
# "206" honours it (the last n bytes; S3), "416" refuses one longer than the
# file with "bytes */<size>" (GitHub), "ignore" sends the whole body with 200;
# without it a suffix range is an error, as before. Unknown URLs get a 404.

rv_dir <- file.path(root, "tests", "repo_download", "data")

# Invalid UTF-8 bytes as "<xx>", so the result can be written as JSON (Python
# shows the same bytes the same way).
rv_bytes <- function(x) {
  if (!is.character(x)) return(x)
  bad <- !is.na(x) & !validUTF8(x)
  x[bad] <- iconv(x[bad], "UTF-8", "UTF-8", sub = "byte")
  x
}

rv_df_bytes <- function(df) {
  if (is.null(df)) return(df)
  for (nm in names(df)) if (is.character(df[[nm]])) df[[nm]] <- rv_bytes(df[[nm]])
  df
}

rv_route_body <- function(r) {
  if (!is.null(r$fixture)) {
    p <- file.path(rv_dir, r$fixture)
    return(readBin(p, "raw", file.size(p)))
  }
  if (!is.null(r$body)) return(charToRaw(enc2utf8(r$body)))
  raw(0)
}

rv_mock <- function(routes) {
  function(req) {
    method <- req$method %||% (if (is.null(req$body)) "GET" else "POST")
    hit <- NULL
    for (r in routes) {
      if (identical(r$url, req$url) && (is.null(r$method) || identical(r$method, method))) {
        hit <- r
        break
      }
    }
    if (is.null(hit)) {
      return(httr2::response(status_code = 404L, url = req$url, method = method,
                             body = charToRaw("not found")))
    }
    body <- rv_route_body(hit)
    status <- as.integer(hit$status %||% 200L)
    headers <- as.list(hit$headers %||% list())
    if (identical(method, "HEAD")) {
      if (!isFALSE(hit$length)) headers[["Content-Length"]] <- as.character(length(body))
      return(httr2::response(status_code = status, url = req$url, method = method,
                             headers = headers, body = raw(0)))
    }
    rng <- req$headers$Range
    if (!is.null(rng) && !isFALSE(hit$range) && status == 200L) {
      total <- length(body)
      if (!is.null(hit$suffix) && grepl("^bytes=-[0-9]+$", rng)) {
        n <- as.numeric(sub("^bytes=-", "", rng))
        if (identical(hit$suffix, "ignore")) {
          return(httr2::response(status_code = 200L, url = req$url, method = method,
                                 headers = headers, body = body))
        }
        if (identical(hit$suffix, "416") && n > total) {
          headers[["Content-Range"]] <- sprintf("bytes */%d", total)
          return(httr2::response(status_code = 416L, url = req$url, method = method,
                                 headers = headers, body = raw(0)))
        }
        from <- max(0, total - n)
        headers[["Content-Range"]] <- sprintf("bytes %.0f-%.0f/%d", from, total - 1, total)
        return(httr2::response(status_code = 206L, url = req$url, method = method,
                               headers = headers, body = body[(from + 1):total]))
      }
      m <- regmatches(rng, regexec("^bytes=([0-9]+)-([0-9]+)$", rng))[[1]]
      from <- as.numeric(m[2])
      to <- min(as.numeric(m[3]), length(body) - 1)
      if (from > length(body) - 1) {
        return(httr2::response(status_code = 416L, url = req$url, method = method,
                               headers = headers, body = raw(0)))
      }
      headers[["Content-Range"]] <- sprintf("bytes %.0f-%.0f/%d", from, to, length(body))
      return(httr2::response(status_code = 206L, url = req$url, method = method,
                             headers = headers, body = body[(from + 1):(to + 1)]))
    }
    httr2::response(status_code = status, url = req$url, method = method,
                    headers = headers, body = body)
  }
}

# Run fn(...) with every request answered by rv_mock(routes), a fresh
# zip_peek() session cache and TESTTHAT set (so .download_many_parallel()
# performs its requests one after the other, as it does under test mocks).
rv_serve <- function(routes, fn, ...) {
  cache <- metacheck:::.zip_peek_cache
  rm(list = ls(cache, all.names = TRUE), envir = cache)
  old <- Sys.getenv("TESTTHAT", unset = NA)
  Sys.setenv(TESTTHAT = "true")
  on.exit({
    if (is.na(old)) Sys.unsetenv("TESTTHAT") else Sys.setenv(TESTTHAT = old)
    rm(list = ls(cache, all.names = TRUE), envir = cache)
  })
  httr2::with_mocked_responses(rv_mock(routes), fn(...))
}

rv_peek <- function(routes, url, ...) {
  rv_df_bytes(rv_serve(routes, zip_peek, url, ...))
}

rv_size <- function(routes, url) {
  rv_serve(routes, metacheck:::.remote_size, url)
}

# zip_peek(cache = TRUE) twice, the in-memory cache cleared in between (a new
# session): `first` answers the first peek, `second` the second, and the disk
# cache sits in a fresh temporary folder. `rate_limited` records a long rate
# limit for the host before the first peek (made with skip_on_api_limit =
# TRUE) and forgets it after.
rv_peek_cached <- function(first, second, url, rate_limited = FALSE, ...) {
  d <- tempfile("rv_zpc_")
  dir.create(d)
  old <- options(metacheck.zip_peek_cache.dir = d)
  host <- httr2::url_parse(url)$hostname
  forget <- function() {
    suppressWarnings(rm(list = host, envir = metacheck:::.host_rate_limit_cache))
  }
  on.exit({
    forget()
    options(old)
    unlink(d, recursive = TRUE)
  })
  if (isTRUE(rate_limited)) metacheck:::.host_rate_limit_record(host, 999)
  one <- rv_serve(first, zip_peek, url, cache = TRUE,
                  skip_on_api_limit = isTRUE(rate_limited), ...)
  forget()
  stored <- metacheck:::.zip_peek_cache_has(url)
  two <- rv_serve(second, zip_peek, url, cache = TRUE, ...)
  list(first = rv_df_bytes(one), stored = stored, second = rv_df_bytes(two),
       entries = length(list.files(d)))
}

rv_decision <- function(routes, url, ...) {
  out <- rv_serve(routes, zip_decision, url, ...)
  out$contents <- rv_df_bytes(out$contents)
  if (!is.null(out$types)) {
    out$types <- stats::setNames(as.integer(out$types), rv_bytes(names(out$types)))
  }
  out
}

rv_fetch <- function(routes, url, names = NULL, verify = TRUE) {
  d <- tempfile("rv_")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  out <- rv_serve(routes, metacheck:::.zip_fetch_members, url, names = names,
                  dest = d, verify = verify)
  if (!is.null(out) && "path" %in% names(out)) {
    out$path <- sub(d, "<TMP>", out$path, fixed = TRUE)
  }
  files <- sort(list.files(d, recursive = TRUE, all.files = TRUE))
  list(result = rv_df_bytes(out),
       files = rv_bytes(files),
       sizes = unname(file.size(file.path(d, files))))
}

rv_download <- function(routes, files, twice = FALSE, disk = TRUE, ...) {
  sess <- tempfile("rv_sess_")
  old <- options(metacheck.repo_cache.session_dir = sess,
                 metacheck.repo_cache.notified = TRUE)
  on.exit({
    options(old)
    unlink(sess, recursive = TRUE)
  })
  run <- function() {
    dl <- download_repo_files(files, ...)
    if (isTRUE(twice)) dl <- download_repo_files(files, ...)
    dl
  }
  dl <- rv_serve(routes, run)
  on_disk <- sort(list.files(sess, recursive = TRUE, all.files = TRUE))
  out <- list(
    file_location = sub(sess, "<SESSION>", dl$file_location, fixed = TRUE),
    gated = attr(dl, "gated"),
    oversize_skipped = attr(dl, "oversize_skipped"),
    failed = attr(dl, "failed")
  )
  # httr2's mocked responses are not streamed to `path`, so an empty body
  # leaves no file behind in R: `disk = FALSE` leaves the listing out
  if (isTRUE(disk)) {
    out$on_disk <- on_disk
    out$sizes <- unname(file.size(file.path(sess, on_disk)))
  }
  out
}

# download_repo_files() with its two transports replaced by recorders: which
# archive is requested (and with what byte limit), and which files are then
# fetched one by one. httr2's mocked responses are not streamed to a file, so
# the real transports cannot finish in R; this keeps the routing comparable.
rv_download_spy <- function(routes, files, ...) {
  ns <- asNamespace("metacheck")
  zips <- list()
  ones <- character(0)
  real_zip <- get(".download_zip_to_cache", envir = ns)
  real_one <- get(".download_one", envir = ns)
  spy_zip <- function(files, row_idx, zip_url, strip_dir, req_func, timeout_s,
                      max_bytes = Inf, skip_on_api_limit = FALSE,
                      expected_bytes = NA_real_) {
    zips[[length(zips) + 1]] <<- list(zip_url = zip_url, rows = length(row_idx),
                                      strip_dir = strip_dir, max_bytes = max_bytes,
                                      expected_bytes = expected_bytes)
    files
  }
  spy_one <- function(url, dest, skip_on_api_limit = FALSE, expected_bytes = NA_real_) {
    ones <<- c(ones, url)
    dir.create(dirname(dest), showWarnings = FALSE, recursive = TRUE)
    writeBin(as.raw(1), dest)
    NA_character_
  }
  utils::assignInNamespace(".download_zip_to_cache", spy_zip, "metacheck")
  utils::assignInNamespace(".download_one", spy_one, "metacheck")
  on.exit({
    utils::assignInNamespace(".download_zip_to_cache", real_zip, "metacheck")
    utils::assignInNamespace(".download_one", real_one, "metacheck")
  })
  out <- rv_download(routes, files, disk = FALSE, ...)
  c(list(zips = zips, downloaded = ones), out)
}

rv_expand <- function(fn, fixture, skip_types = "materials") {
  d <- tempfile("rv_")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  f <- file.path(d, basename(fixture))
  file.copy(file.path(rv_dir, fixture), f)
  row <- data.frame(repo_url = "r", file_name = basename(fixture), file_path = basename(fixture),
                    file_url = "u", file_location = "<unset>", file_size = 1,
                    data_type = "unknown", doc_role = NA_character_, data_format = "x",
                    stringsAsFactors = FALSE)
  expand <- getFromNamespace(fn, "metacheck")
  out <- expand(f, row, skip_types = skip_types)
  out$file_location <- sub(d, "<TMP>", out$file_location, fixed = TRUE)
  rv_df_bytes(out)
}

rv_category <- function(x) rv_df_bytes(file_category(x))

rv_filetype <- function(x) {
  out <- filetype(x)
  names(out) <- rv_bytes(names(out))
  out
}
