# R halves of the zip-mode budget cases in parity/cases/archives_osf_review.yaml
# (osf_file_download.zip.*), mirrored by tests/archives_osf/_zip_budget_helpers.py.
# A case sources this file and calls ob_zip_download() through do.call().
#
# The listing is a table built from the arguments (one component, "child1", of
# project "abcde"); the archive request is answered with a fixture zip under
# tests/archives_osf/data/zip_budget, refused as curl does when it is larger
# than the byte limit set on the request; the files fetched one by one are
# written with their listed size. The result records which archive was
# requested with what limit, and which files were fetched.

ob_dir <- file.path(root, "tests", "archives_osf", "data", "zip_budget")

ob_zip_download <- function(name, size, provider, budget_bytes = NULL,
                            archive = NULL, unzip = TRUE) {
  n <- length(name)
  contents <- data.frame(
    osf_type = c(rep("files", n), "nodes"),
    osf_id = c(paste0("file", seq_len(n)), "child1"),
    name = c(name, "Data"),
    provider = c(provider, NA),
    path = c(paste0("/", name), NA),
    kind = c(rep("file", n), "folder"),
    size = c(as.numeric(size), NA),
    download_url = c(paste0("https://example.test/", name), NA),
    parent = c(rep("child1", n), "abcde"),
    project = c(rep("child1", n), "abcde"),
    filetype = c(rep("csv", n), NA),
    downloads = c(rep(1, n), NA),
    stringsAsFactors = FALSE
  )
  zip_raw <- NULL
  if (!is.null(archive)) {
    p <- file.path(ob_dir, archive)
    zip_raw <- readBin(p, "raw", file.size(p))
  }
  mds <- if (is.null(budget_bytes)) Inf else budget_bytes / 1024^2
  archives <- list()
  fetched <- character(0)
  d <- tempfile("ob_")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  osf_cache_clear()
  dl <- testthat::with_mocked_bindings(
    testthat::with_mocked_bindings(
      osf_file_download("abcde", d, mode = "zip", unzip = unzip,
                        max_download_size = mds, metadata = FALSE),
      req_perform = function(req, path = NULL, ...) {
        lim <- req$options$maxfilesize_large
        archives[[length(archives) + 1]] <<- list(url = req$url,
                                                  max_bytes = lim %||% NA_real_)
        if (is.null(zip_raw)) return(httr2::response(status_code = 404L, url = req$url))
        if (!is.null(lim) && length(zip_raw) > lim) stop("Maximum file size exceeded")
        if (!is.null(path)) writeBin(zip_raw, path)
        httr2::response(status_code = 200L, url = req$url)
      },
      .package = "httr2"
    ),
    osf_info = function(...) contents,
    osf_type = function(...) "nodes",
    .download_many_parallel = function(urls, dests, expected_size = NA_real_,
                                       skip_on_api_limit = FALSE) {
      fetched <<- c(fetched, urls)
      for (k in seq_along(dests)) {
        dir.create(dirname(dests[k]), showWarnings = FALSE, recursive = TRUE)
        writeBin(raw(if (is.na(expected_size[k])) 1 else expected_size[k]), dests[k])
      }
      rep(NA_character_, length(urls))
    },
    .package = "metacheck"
  )
  dl$download_path <- basename(dl$download_path)
  list(value = dl, archives = archives, fetched = fetched,
       files = sort(list.files(d, recursive = TRUE)))
}
