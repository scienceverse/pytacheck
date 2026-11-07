# R side of the FAIR_repo parity cases (parity/cases/fair.yaml, fair_review.yaml). Each case
# sources this file in an `$expr`; tests/fair/parity_helpers.py is the Python twin. Both sides
# return the same names and shapes. Fixture folders: tests/fair/fixtures/<name> (written by
# tests/fair/make_fixtures.py), or an absolute path such as demofile("repo").
#
# ff_run() copies a fixture to <tmp>/<name> (the folder's name is the paper id and the copy's
# title, so it is the same on both sides), runs FAIR_repo() on the copy with the language model
# off, and returns a summary of the folder it wrote. What is masked, and why:
# * today's date -> "<date>" (CHANGES.md, the README, metacheck:generated, dateCreated);
# * any temporary folder -> "<tmp>";
# * the file the checks' results are saved in: metacheck writes an R data file
#   (logs/<folder>.rds), pytacheck a JSON file (logs/<folder>.json, D138). Both are written
#   "<folder>.<results>" wherever the name occurs (the file list, the RO-Crate, the README's
#   tree), and the RO-Crate's encodingFormat of that one file is "<results>".
# Nothing else is changed.

ff_root <- function() {
  if (exists("root", envir = globalenv(), inherits = FALSE)) get("root", envir = globalenv()) else getwd()
}

ff_fixture_dir <- function(fixture) {
  if (grepl("^(/|[A-Za-z]:)", fixture)) return(fixture)
  file.path(ff_root(), "tests", "fair", "fixtures", fixture)
}

# a new empty folder in the temporary folder
ff_tmp <- function() {
  base <- tempfile("ff-")
  dir.create(base, recursive = TRUE)
  base
}

# Copy a fixture into `base` as <base>/<name> and return that path. "empty" is an empty folder
# (git cannot hold one).
ff_copy <- function(fixture, base) {
  if (identical(fixture, "empty")) {
    work <- file.path(base, "empty")
    dir.create(work)
    return(work)
  }
  src <- ff_fixture_dir(fixture)
  ok <- file.copy(src, base, recursive = TRUE)
  work <- file.path(base, basename(src))
  if (!isTRUE(ok) || !dir.exists(work)) stop("could not copy the fixture ", fixture)
  work
}

# readLines() as the cases read text: UTF-8, any of LF, CRLF and CR ends a line
ff_lines <- function(path) readLines(path, warn = FALSE, encoding = "UTF-8")

# a JSON file as nested lists, or NULL when it does not exist
ff_json <- function(path) {
  if (!file.exists(path)) return(NULL)
  jsonlite::fromJSON(paste(ff_lines(path), collapse = "\n"), simplifyVector = FALSE)
}

# The substitutions of every string: temporary folders (longest first), today's date, and the
# name of the file the results are saved in (see the top of this file).
ff_subs <- function(base, pid) {
  roots <- unique(c(normalizePath(base, winslash = "/", mustWork = FALSE), base,
                    normalizePath(tempdir(), winslash = "/", mustWork = FALSE), tempdir()))
  roots <- roots[nzchar(roots)]
  roots <- roots[order(-nchar(roots))]
  list(from = c(roots, format(Sys.Date(), "%Y-%m-%d"), paste0(pid, ".rds")),
       to = c(rep("<tmp>", length(roots)), "<date>", paste0(pid, ".<results>")))
}

# every string at any depth with the substitutions made (names and NULL elements are kept)
ff_clean <- function(x, subs) {
  if (is.character(x)) {
    for (i in seq_along(subs$from)) x <- gsub(subs$from[i], subs$to[i], x, fixed = TRUE)
    return(x)
  }
  if (is.list(x)) {
    out <- lapply(x, ff_clean, subs = subs)
    names(out) <- names(x)
    return(out)
  }
  x
}

# the RO-Crate entity of the results file has the format of that file in R (application/x-r-rds)
# and in pytacheck (application/json): both "<results>"
ff_mask_results <- function(crate, pid) {
  g <- crate[["@graph"]]
  if (is.null(g)) return(crate)
  id <- paste0("logs/", pid, ".<results>")
  for (i in seq_along(g)) {
    e <- g[[i]]
    if (identical(e[["@id"]], id) && !is.null(e[["encodingFormat"]])) {
      e[["encodingFormat"]] <- "<results>"
      g[[i]] <- e
    }
  }
  crate[["@graph"]] <- g
  crate
}

# What FAIR_repo() wrote into `out`, as plain values.
ff_summary <- function(res, out, pid, subs) {
  actual <- list.files(out, recursive = TRUE, all.files = TRUE, no.. = TRUE)
  shown <- ff_clean(actual, subs)
  ord <- order(shown, method = "radix")
  actual <- actual[ord]
  shown <- shown[ord]

  # the files whose text is compared as text or JSON below, and the logs, are not in md5
  skip <- grepl("^logs/", actual) |
    actual %in% c("README.md", "CHANGES.md", "ro-crate-metadata.json",
                  "dataset_description.json", "requirements.txt") |
    grepl("^study-[^/]+/(README\\.md|CHANGES\\.md|dataset_description\\.json)$", actual)
  md5 <- list()
  if (any(!skip)) {
    sums <- unname(tools::md5sum(file.path(out, actual[!skip])))
    md5 <- as.list(stats::setNames(sums, actual[!skip]))
  }

  study_paths <- actual[grepl("^study-[^/]+/dataset_description\\.json$", actual)]
  study_descriptions <- lapply(stats::setNames(study_paths, study_paths), function(p)
    ff_clean(ff_json(file.path(out, p)), subs))
  text_paths <- actual[actual %in% c("CHANGES.md", "README.md", "requirements.txt") |
                         grepl("^study-[^/]+/(README|CHANGES)\\.md$", actual)]
  texts <- lapply(stats::setNames(text_paths, text_paths), function(p)
    ff_clean(ff_lines(file.path(out, p)), subs))
  script_paths <- actual[grepl("\\.(r|rmd|qmd|py|ipynb)$", actual, ignore.case = TRUE)]
  scripts <- lapply(stats::setNames(script_paths, script_paths), function(p)
    ff_lines(file.path(out, p)))

  rocrate <- ff_json(file.path(out, "ro-crate-metadata.json"))
  if (!is.null(rocrate)) rocrate <- ff_mask_results(ff_clean(rocrate, subs), pid)

  plan <- res[["plan"]]
  plan_out <- NULL
  if (is.data.frame(plan) && nrow(plan)) {
    cols <- intersect(c("file_name", "data_type", "group", "target_path", "written", "convert",
                        "original_target", "same_as", "script_writes", "recovered_row",
                        "recovered_source"), names(plan))
    plan_out <- plan[, cols, drop = FALSE]
    rownames(plan_out) <- NULL
  }
  result <- list(
    n_files_copied = res[["n_files_copied"]],
    n_studies = res[["n_studies"]],
    empty_plan = isTRUE(res[["empty_plan"]]),
    skipped = as.character(res[["skipped"]]),
    copy_failed = as.character(res[["copy_failed"]]),
    renamed = res[["renamed"]],
    identical = res[["identical"]],
    scripts = res[["scripts"]],
    plan = plan_out,
    existed = isTRUE(res[["existed"]]))

  fl <- ff_json(file.path(out, "logs", paste0(pid, ".files.json")))
  filelog <- NULL
  if (!is.null(fl)) {
    filelog <- list(
      folder = fl[["folder"]],
      n_files = fl[["n_files"]],
      files = lapply(fl[["files"]], function(f) Filter(Negate(is.null), list(
        file_path = f[["file_path"]], file_size = f[["file_size"]], data_type = f[["data_type"]]))),
      llm = fl[["provenance"]][["llm"]])
  }

  list(files = shown, md5 = md5,
       description = ff_clean(ff_json(file.path(out, "dataset_description.json")), subs),
       study_descriptions = study_descriptions, rocrate = rocrate, texts = texts,
       scripts = scripts, result = result, filelog = filelog)
}

# FAIR_repo() on a copy of a fixture, the language model off, as a summary (see the top).
# drop: paths (in the copy) to delete first; existing: make the output folder first, holding a
# file mine.txt.
ff_run <- function(fixture, paper = NULL, overwrite = FALSE, execute = FALSE,
                   drop = character(0), existing = FALSE) {
  old <- options(metacheck.llm.use = FALSE)
  base <- ff_tmp()
  on.exit({
    options(old)
    unlink(base, recursive = TRUE)
  }, add = TRUE)
  work <- ff_copy(fixture, base)
  for (f in drop) unlink(file.path(work, f), recursive = TRUE)
  out <- file.path(base, "out")
  if (existing) {
    dir.create(out)
    writeLines("keep", file.path(out, "mine.txt"))
  }
  res <- suppressMessages(suppressWarnings(
    FAIR_repo(work, output_dir = out, paper = paper, overwrite = overwrite, execute = execute)))
  ff_summary(res, out, basename(work), ff_subs(base, basename(work)))
}

# A test paper with some fields of paper$info set: `info` is a named list; a list value is a
# list cell (keywords = list(c("a", "b"))), anything else a single value.
ff_test_paper <- function(info = list()) {
  p <- test_paper("x")
  for (nm in names(info)) p$info[[nm]] <- info[[nm]]
  p
}

# The paper of fair_paper() in test-fair-repo.R: a title, a DOI and two authors with an email, an
# ORCID, an affiliation and CRediT roles. links: the text names a repository on OSF and on
# GitHub; funding: a funding statement, a funder and an award.
ff_paper <- function(links = FALSE, funding = FALSE) {
  p <- test_paper("x")
  p$info$title <- "A test paper"
  p$info$doi <- "10.1234/abc"
  p$author <- data.frame(author_id = 1:2, given = c("Ann", "Bob"),
                         family = c("Smith", "Jones"),
                         email = c("ann@x.org", NA), corresponding = c(TRUE, FALSE),
                         orcid = c("0000-0001-2345-6789", NA),
                         affiliation = c("TU/e", NA))
  p$author$credit_roles <- list(c("Conceptualization", "Writing - original draft"),
                                character(0))
  if (links)
    p$text$text <- "Data and code are at https://osf.io/abcde and https://github.com/a/b."
  if (funding) {
    p$info$funding_statement <- "Supported by ERC."
    p$funding <- data.frame(funding_id = 1, funder = "ERC")
    p$funding$award_ids <- list("A1")
  }
  p
}

# Whether FAIR_repo() fails for a wrong argument, on a copy of the fixture "basic": a list(error =)
# of TRUE (it failed) or FALSE (it did not). The messages are never compared.
ff_error <- function(kind) {
  old <- options(metacheck.llm.use = FALSE)
  base <- ff_tmp()
  on.exit({
    options(old)
    unlink(base, recursive = TRUE)
  }, add = TRUE)
  d <- ff_copy("basic", base)
  out <- file.path(base, "out")
  failed <- tryCatch({
    suppressMessages(suppressWarnings(switch(kind,
      no_output_dir = FAIR_repo(d),
      not_a_folder = FAIR_repo(file.path(base, "no_such_folder"), output_dir = out),
      two_paths = FAIR_repo(c("a", "b"), output_dir = out),
      a_file = FAIR_repo(file.path(d, "README.txt"), output_dir = out),
      inside = FAIR_repo(d, output_dir = file.path(d, "copy")),
      same_folder = FAIR_repo(d, output_dir = d),
      bad_paper = FAIR_repo(d, output_dir = out, paper = list(1)),
      stop("unknown kind"))))
    FALSE
  }, error = function(e) TRUE)
  list(error = failed)
}
