# R side of the reproducibility_check parity cases (parity/cases/mod_repro.yaml).
#
# reproducibility_check reads code_check's `table` (+ `version_pin`),
# psychds_check's `table` and data_check's `structure` from the module chain.
# rc_input() builds that chain from a named scenario in
# tests/mod_repro/fixtures/scenarios.json (the way
# test-module-reproducibility_check.R's fake_module_output() does), and
# rc_run() runs the module on it. Execute cases keep the sandbox, then replace
# its (random) path by "<root>" and delete it. The Python twin is
# tests/mod_repro/helpers.py. Sourced with `source(..., local = TRUE)`; the
# parity runner's working directory is the repository root.

rc_specs <- function() {
  jsonlite::fromJSON("tests/mod_repro/fixtures/scenarios.json", simplifyVector = FALSE)
}

rc_psychsci <- c(
  "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
  "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
  "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json",
  "upstream/metacheck/inst/demos/to_err_is_human.json"
)

rc_df <- function(cols) {
  if (is.null(cols)) return(NULL)
  out <- list()
  for (nm in names(cols)) {
    v <- cols[[nm]]
    out[[nm]] <- if (nm == "parse_error") {
      vapply(v, function(x) if (is.null(x)) NA else as.logical(x), logical(1))
    } else {
      vapply(v, function(x) if (is.null(x)) NA_character_ else as.character(x), character(1))
    }
  }
  as.data.frame(out, stringsAsFactors = FALSE, check.names = FALSE)
}

rc_with_pid <- function(df, pid) {
  if (is.null(df) || "paper_id" %in% names(df)) return(df)
  cbind(data.frame(paper_id = rep(pid, nrow(df)), stringsAsFactors = FALSE), df)
}

rc_paper <- function(spec) {
  switch(spec$paper %||% "test",
    test = {
      p <- if (is.null(spec$text)) test_paper() else test_paper(unlist(spec$text))
      p$paper_id <- "p1"  # test_paper() ids come from the clock
      p
    },
    demo = demopaper(),
    psychsci = metacheck::read(rc_psychsci),
    bibr12 = metacheck::read("tests/fixtures/bibr_v12_full.json"),
    stop("unknown paper ", spec$paper)
  )
}

# Repository-relative file locations made absolute (for `module:` cases,
# which the Python runner does not run from the repository root).
rc_absolute <- function(df) {
  if (is.null(df)) return(df)
  for (col in intersect(c("file_location", "file_url"), names(df))) {
    v <- df[[col]]
    ok <- !is.na(v)
    v[ok] <- normalizePath(v[ok], mustWork = FALSE)
    df[[col]] <- v
  }
  df
}

rc_input <- function(name, absolute = FALSE) {
  spec <- rc_specs()$scenarios[[name]]
  if (is.null(spec)) stop("unknown scenario ", name)
  paper <- rc_paper(spec)
  if (isTRUE(spec$real)) return(paper)
  ids <- paper_id(paper)
  code <- rc_with_pid(rc_df(spec$code), ids[[1]])
  structure_df <- rc_with_pid(rc_df(spec$structure), ids[[1]])
  if (absolute) {
    code <- rc_absolute(code)
    structure_df <- rc_absolute(structure_df)
  }
  prev <- list()
  if (!is.null(spec$plan)) prev$psychds_check <- list(table = rc_df(spec$plan))
  if (!is.null(code)) {
    if (!is.null(structure_df)) prev$data_check <- list(structure = structure_df)
    head <- list(module = "code_check", table = code, paper = paper,
                 summary_table = data.frame(paper_id = ids))
    if (!is.null(spec$version_pin)) {
      head$version_pin <- list(r_versions = unlist(spec$version_pin))
    }
  } else {
    head <- list(module = "data_check", table = data.frame(), paper = paper,
                 summary_table = data.frame(paper_id = ids), structure = structure_df)
  }
  head$prev_outputs <- prev
  class(head) <- "metacheck_module_output"
  head
}

# The scenario's upstream outputs saved as a prior build's <paper_id>.rds
# (tables_dir); `tables = "none"` points tables_dir at an empty directory.
rc_tables_dir <- function(name, td, tables) {
  x <- rc_input(name)
  if (identical(tables, "none")) return(x$paper)
  pid <- paper_id(x$paper)[[1]]
  saveRDS(list(paper_id = pid, modules = list(
    code_check = list(table = x$table),
    data_check = list(structure = x$prev_outputs$data_check$structure),
    psychds_check = list(table = x$prev_outputs$psychds_check$table)
  )), file.path(td, paste0(pid, ".rds")))
  x$paper
}

# Run times vary between runs: zero them (run_results$elapsed and the
# execution table's "Time (s)", deparsed into the report's R chunk) so the
# goldens are reproducible.
rc_untime <- function(mo) {
  if (!is.null(mo$run_results) && nrow(mo$run_results)) {
    mo$run_results$elapsed <- 0
    mo$report <- gsub('"Time \\(s\\)" = (c\\([^)]*\\)|[0-9.e+-]+)', '"Time (s)" = 0',
                      mo$report)
  }
  mo
}

rc_scrub <- function(mo) {
  mo <- rc_untime(mo)
  root <- mo$sandbox
  if (is.null(root)) return(mo)
  un <- function(x) if (is.character(x)) gsub(root, "<root>", x, fixed = TRUE) else x
  mo$report <- un(mo$report)
  if (!is.null(mo$run_results)) {
    for (col in c("error", "stdout", "stderr")) mo$run_results[[col]] <- un(mo$run_results[[col]])
    mo$run_results$script_lines <- lapply(mo$run_results$script_lines, un)
  }
  if (!is.null(mo$modifications) && nrow(mo$modifications)) {
    mo$modifications$detail <- un(mo$modifications$detail)
  }
  mo$sandbox <- "<root>"
  unlink(root, recursive = TRUE)
  mo
}

rc_run <- function(name, ..., tables = NULL) {
  if (!is.null(tables)) {
    td <- tempfile("rc_tables_")
    dir.create(td)
    on.exit(unlink(td, recursive = TRUE), add = TRUE)
    paper <- rc_tables_dir(name, td, tables)
    return(rc_scrub(module_run(paper, "reproducibility_check", tables_dir = td, ...)))
  }
  rc_scrub(module_run(rc_input(name), "reproducibility_check", ...))
}

# -- mocked external steps ------------------------------------------------------
# Installing packages needs the network and the Docker backend needs Docker:
# rc_run_mocked() swaps those steps (only) for stand-ins inside metacheck's
# namespace -- a fixed install result, and Docker runs delegated to the
# process runner -- so the module's own logic and report around them can be
# compared. The Python twin patches the same functions.

rc_fake_install <- function(install_deps, ...) {
  data.frame(
    package = c(install_deps$package, "oldpkg", "oddpkg", "fine"),
    source = "cran",
    installed = c(rep(FALSE, nrow(install_deps)), TRUE, FALSE, TRUE),
    message = c(rep("package is not available for this version of R", nrow(install_deps)),
                "", NA, ""),
    via_archive = c(rep(FALSE, nrow(install_deps)), TRUE, FALSE, FALSE),
    category = c(rep("cran_unavailable", nrow(install_deps)), NA, "mystery", NA),
    stringsAsFactors = FALSE
  )
}

rc_run_mocked <- function(name, ...) {
  ns <- "metacheck"
  fakes <- list(
    repro_install_deps = function(install_deps, lib_dir, cran_to_main_lib = FALSE)
      rc_fake_install(install_deps),
    repro_install_deps_docker = function(install_deps, lib_dir, image = "", timeout = 600)
      rc_fake_install(install_deps),
    repro_docker_available = function() list(ok = TRUE, msg = ""),
    repro_run_scripts_docker = function(run_tbl, order, sandbox_root, lib_dir = NULL,
                                        image = "", timeout = 600, skip = character(0),
                                        parses = NULL, failed_deps = character(0))
      metacheck:::repro_run_scripts(run_tbl, order, lib_dir = lib_dir, timeout = timeout,
                                    skip = skip, parses = parses, failed_deps = failed_deps)
  )
  old <- lapply(names(fakes), function(n) get(n, envir = asNamespace(ns)))
  names(old) <- names(fakes)
  for (n in names(fakes)) utils::assignInNamespace(n, fakes[[n]], ns)
  on.exit(for (n in names(old)) utils::assignInNamespace(n, old[[n]], ns), add = TRUE)
  suppressWarnings(rc_run(name, ...))
}
