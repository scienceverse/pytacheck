# R side of the psychds_check parity cases (parity/cases/mod_psychds.yaml).
#
# psychds_check reads data_check's `structure`, `table` and `group_no_evidence`
# (and codebook_check's `table`) from the module chain. These builders make a
# metacheck_module_output that looks like a finished data_check run, from a
# named spec in tests/mod_psychds/fixtures/chains.json, so the module can be
# run as `module_run(psychds_fake_chain("<name>"), "psychds_check")` without
# running data_check itself. The Python twin is
# tests/mod_psychds/parity_support.py::fake_chain().
#
# Sourced by the case expressions with `source(rpath(...), local = TRUE)`
# (`rpath()` is parity/r/run_cases.R's repository-path helper).

# `file` names a spec file in tests/mod_psychds/fixtures/ (chains.json for
# parity/cases/mod_psychds.yaml, review_chains.json for mod_psychds_review.yaml).
psychds_specs <- function(file = "chains.json") {
  jsonlite::fromJSON(rpath(file.path("tests/mod_psychds/fixtures", file)),
                     simplifyVector = FALSE)
}

psychds_chr <- function(vals) {
  vapply(vals, function(v) if (is.null(v)) NA_character_ else as.character(v),
         character(1))
}

psychds_paper <- function(which) {
  # a list of repository-relative paths: read() them (one paper or a list)
  if (is.list(which)) return(metacheck::read(vapply(unlist(which), rpath, character(1))))
  switch(which %||% "demo",
    demo = demopaper(),
    psychsci = metacheck::read(vapply(c(
      "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
      "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
      "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json",
      "upstream/metacheck/inst/demos/to_err_is_human.json"
    ), rpath, character(1))),
    test = {
      p <- test_paper()
      p$paper_id <- "p1"  # test_paper() ids come from the clock
      p
    },
    none = NULL,
    stop("unknown paper ", which)
  )
}

psychds_structure <- function(spec) {
  st <- spec$structure
  if (is.null(st)) return(NULL)
  cols <- list()
  for (nm in setdiff(names(st), "referenced_by")) cols[[nm]] <- psychds_chr(st[[nm]])
  s <- as.data.frame(cols, stringsAsFactors = FALSE, check.names = FALSE)
  if (isTRUE(spec$classify)) {
    s$data_type <- metacheck:::data_classify_files(s$file_name, s$file_path)
    s$doc_role <- metacheck:::.data_doc_role(s$file_name)
  }
  if ("referenced_by" %in% names(st)) {
    s$referenced_by <- lapply(st$referenced_by, function(v)
      if (length(v)) as.character(unlist(v)) else NULL)
  }
  s
}

psychds_fake_chain <- function(name, file = "chains.json") {
  spec <- psychds_specs(file)$chains[[name]]
  if (is.null(spec)) stop("unknown chain ", name)
  paper <- psychds_paper(spec$paper)
  ids <- if (is.null(paper)) character(0) else paper_id(paper)
  first_id <- if (length(ids)) ids[[1]] else NA_character_

  columns <- NULL
  if (!is.null(spec$columns)) {
    n <- as.integer(spec$columns)
    columns <- data.frame(
      paper_id = rep(first_id, n),
      source_file = rep("data.csv", n),
      column_name = sprintf("v%d", seq_len(n)),
      stringsAsFactors = FALSE
    )
  }
  prev <- list()
  if (!is.null(spec$labels)) {
    labels <- psychds_chr(spec$labels)
    lab <- data.frame(
      source_file = rep("data.csv", length(labels)),
      column_name = sprintf("v%d", seq_along(labels)),
      stringsAsFactors = FALSE
    )
    if (!isTRUE(spec$labels_no_status)) lab$label_status <- labels
    prev$codebook_check <- list(table = lab)
  }
  gne <- spec$group_no_evidence
  if (identical(gne, "NA")) gne <- NA

  out <- list(
    module = "data_check",
    title = "Data Check",
    section = "results",
    table = columns,
    report = "",
    traffic_light = "info",
    summary_text = "",
    summary_table = data.frame(paper_id = ids, stringsAsFactors = FALSE),
    paper = paper,
    prev_outputs = prev,
    structure = psychds_structure(spec),
    group_no_evidence = gne
  )
  class(out) <- "metacheck_module_output"
  out
}

# psychds_check on a fake chain with llm_use() set to `use` (restored after).
psychds_run_llm <- function(name, use = TRUE, file = "chains.json") {
  old <- getOption("metacheck.llm.use")
  on.exit(options(metacheck.llm.use = old), add = TRUE)
  llm_use(use)
  module_run(psychds_fake_chain(name, file), "psychds_check")
}

# The module-local psychds_tree_html() helper (sourced from the installed
# module) on the named node table of chains.json's "trees" (NULL stays NULL).
psychds_tree <- function(name, file = "chains.json") {
  spec <- psychds_specs(file)$trees[[name]]
  nodes <- if (is.null(spec$nodes)) NULL else as.data.frame(
    lapply(spec$nodes, psychds_chr), stringsAsFactors = FALSE, check.names = FALSE)
  env <- new.env()
  sys.source(system.file("modules", "psychds_check.R", package = "metacheck"),
             envir = env)
  env$psychds_tree_html(nodes)
}
