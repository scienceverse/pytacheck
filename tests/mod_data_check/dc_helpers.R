# R side of the data_check parity cases (parity/cases/mod_data_check.yaml).
#
# data_check reads repo_check's file listing through get_prev_outputs(). The
# scenarios in tests/mod_data_check/scenarios.json describe such listings (the
# fixture repositories under tests/mod_data_check/fixtures/repos, plus explicit
# rows); dc_prev() builds the repo_check output (a metacheck_module_output
# whose paper is a test paper or paper list with fixed ids), so module_run()
# chains data_check onto it exactly as in a report pipeline. The Python twin
# is tests/mod_data_check/helpers.py.
#
# careless: the R reference environment cannot install the suggested
# `careless` package, so dc_run(careless = TRUE) loads the vendored copy of its
# longstring()/irv() (tests/mod_data_check/fixtures/careless) for the run and
# unloads it afterwards; careless = FALSE runs without it (metacheck's
# "not installed" path).

dc_root <- "tests/mod_data_check"
dc_fixtures <- file.path(dc_root, "fixtures")

dc_scenario <- function(name) {
  jsonlite::fromJSON(file.path(dc_root, "scenarios.json"),
                     simplifyVector = FALSE)[[name]]
}

.dc_base_cols <- c("paper_id", "repo_name", "repo_url", "file_name", "file_path",
                   "file_url", "file_location", "file_size")

.dc_chr <- function(x) if (is.null(x)) NA_character_ else as.character(x)

# Copy a fixture directory to a fresh temporary directory (scenarios whose
# archives are extracted beside themselves must not write into the repository).
.dc_copy <- function(dir) {
  tmp <- tempfile("dcrepo_")
  dir.create(tmp)
  file.copy(dir, tmp, recursive = TRUE)
  file.path(tmp, basename(dir))
}

.dc_repo_rows <- function(repo, default_pid, copy) {
  src <- file.path(dc_fixtures, "repos", repo$dir)
  base <- if (isTRUE(copy)) .dc_copy(src) else src
  files <- unlist(repo$files)
  local <- is.null(repo$local) || isTRUE(repo$local)
  lapply(files, function(rel) {
    list(
      paper_id = .dc_chr(repo$paper_id %||% default_pid),
      repo_name = .dc_chr(repo$repo_name %||% basename(repo$dir)),
      repo_url = .dc_chr(repo$repo_url),
      file_name = basename(rel),
      file_path = rel,
      file_url = if (is.null(repo$file_url)) NA_character_
                 else if (identical(repo$file_url, "local"))
                   paste0("file://", normalizePath(file.path(src, rel)))
                 else paste0(repo$file_url, rel),
      file_location = if (local) file.path(base, rel) else NA_character_,
      file_size = as.numeric(file.size(file.path(src, rel)))
    )
  })
}

.dc_frame <- function(rows, drop = character(0)) {
  cols <- .dc_base_cols
  for (r in rows) cols <- c(cols, setdiff(names(r), cols))
  out <- lapply(cols, function(cn) {
    vals <- lapply(rows, function(r) r[[cn]])
    num <- cn == "file_size" ||
      (any(!vapply(vals, is.null, TRUE)) &&
         all(vapply(vals, function(v) is.null(v) || is.numeric(v), TRUE)))
    if (num) {
      vapply(vals, function(v) if (is.null(v)) NA_real_ else as.numeric(v), 0)
    } else {
      vapply(vals, function(v) if (is.null(v)) NA_character_ else as.character(v), "")
    }
  })
  names(out) <- cols
  df <- as.data.frame(out, stringsAsFactors = FALSE, check.names = FALSE)
  if (length(rows) == 0) {
    df <- data.frame(lapply(stats::setNames(cols, cols), function(cn)
      if (cn == "file_size") numeric(0) else character(0)), check.names = FALSE)
  }
  df[, setdiff(names(df), unlist(drop)), drop = FALSE]
}

# repo_check's file table of scenario `name`.
dc_table <- function(name) {
  sc <- dc_scenario(name)
  pids <- unlist(sc$papers %||% list("p1"))
  rows <- list()
  for (repo in sc$repos) rows <- c(rows, .dc_repo_rows(repo, pids[[1]], sc$copy))
  rows <- c(rows, sc$rows)
  .dc_frame(rows, sc$drop %||% character(0))
}

# The scenario's paper (a test paper, a paper list, or papers read from files).
dc_paper <- function(name) {
  sc <- dc_scenario(name)
  if (!is.null(sc$read)) {
    p <- read(unlist(sc$read))
    return(p)
  }
  pids <- unlist(sc$papers %||% list("p1"))
  text <- unlist(sc$text %||% list("Some text."))
  papers <- lapply(pids, function(pid) {
    p <- test_paper(text)
    p$paper_id <- pid
    p
  })
  if (length(papers) == 1) papers[[1]] else do.call(paperlist, papers)
}

.dc_df <- function(x) {
  if (is.null(x)) return(NULL)
  as.data.frame(lapply(x, function(col) vapply(col, function(v)
    if (is.null(v)) NA_character_ else as.character(v), "")),
    stringsAsFactors = FALSE)
}

# The repo_check output data_check chains onto for scenario `name` (with a
# `codebook` key: a codebook_check output holding that table, chained onto it).
dc_prev <- function(name) {
  sc <- dc_scenario(name)
  paper <- dc_paper(name)
  pids <- if (inherits(paper, "scivrs_paperlist")) names(paper) else paper$paper_id
  rc <- structure(list(
    module = "repo_check",
    title = "Repository Check",
    section = "general",
    table = dc_table(name),
    report = "",
    traffic_light = "info",
    summary_text = NULL,
    summary_table = data.frame(paper_id = pids),
    paper = paper,
    prev_outputs = list(),
    gated_repos = .dc_df(sc$gated_repos),
    naming_issues = .dc_df(sc$naming_issues)
  ), class = "metacheck_module_output")
  if (is.null(sc$codebook)) return(rc)
  stripped <- rc
  stripped$paper <- NULL
  stripped$summary_table <- NULL
  stripped$prev_outputs <- NULL
  structure(list(
    module = "codebook_check",
    title = "Codebook Check",
    section = "results",
    table = .dc_df(sc$codebook),
    report = "",
    traffic_light = "info",
    summary_text = NULL,
    summary_table = data.frame(paper_id = pids),
    paper = paper,
    prev_outputs = list(repo_check = stripped)
  ), class = "metacheck_module_output")
}

# careless: load / unload the vendored longstring()/irv()
.dc_careless_on <- function() {
  if (!"careless" %in% loadedNamespaces())
    pkgload::load_all(file.path(dc_fixtures, "careless"), quiet = TRUE,
                      export_all = FALSE, helpers = FALSE, attach = FALSE)
}
.dc_careless_off <- function() {
  if ("careless" %in% loadedNamespaces()) pkgload::unload("careless")
}

# ── LLM: a deterministic mock of llm() (twin of helpers.py::mock_llm) ─────────
dc_mock_spec <- function(name) {
  jsonlite::fromJSON(file.path(dc_fixtures, "llm_mock.json"),
                     simplifyVector = FALSE)[[name]]
}

dc_mock_llm <- function(spec) {
  function(text, system_prompt = NULL, type = NULL, text_col = "text", model = NULL,
           params = list(), phase = NULL, ...) {
    if (!is.null(phase) && phase %in% unlist(spec$error_phases)) stop("mock LLM error")
    txt <- as.character(text[[text_col]][1])
    lines <- strsplit(txt, "\n", fixed = TRUE)[[1]]
    items <- grep("^[0-9]+\\. ", lines, value = TRUE)
    idx <- as.integer(sub("^([0-9]+)\\. .*$", "\\1", items))
    body <- sub("^[0-9]+\\. ", "", items)
    pick <- function(map, keys, default) vapply(keys, function(k)
      if (!is.null(map[[k]])) as.character(map[[k]]) else default, "", USE.NAMES = FALSE)
    res <- switch(phase,
      "Classifying file types" = {
        fname <- sub("^file_name: ", "", body)
        ext <- ifelse(grepl(".", fname, fixed = TRUE), sub("^.*\\.", "", fname), "")
        data.frame(index = idx, value = pick(spec$file_types, ext, "unknown"))
      },
      "Classifying column concepts" = {
        nm <- sub("^column_name: ", "", body)
        data.frame(index = idx, value = pick(spec$concepts, nm, "other"))
      },
      "Assigning study groups" = data.frame(index = idx, group = rep(
        as.character(spec$group %||% "ex1"), length(idx))),
      stop("unexpected phase ", phase))
    attr(res, "llm") <- list(model = "mock/dc")
    res
  }
}

# A temporary copy's location as "<copy>/<dir>/..." (goldens must not hold
# per-run temporary paths).
.dc_norm_paths <- function(mo) {
  st <- mo$structure
  root <- paste0(normalizePath("."), "/")
  if (!is.null(st) && "file_location" %in% names(st)) {
    loc <- sub("^.*/dcrepo_[^/]*/", "<copy>/", st$file_location)
    loc <- sub("^.*/metacheck-repo-files/", "<cache>/", loc)
    loc <- ifelse(startsWith(loc, root), substring(loc, nchar(root) + 1L), loc)
    mo$structure$file_location <- loc
  }
  if (!is.null(st) && "file_url" %in% names(st)) {
    url <- st$file_url
    mo$structure$file_url <- ifelse(startsWith(url, paste0("file://", root)),
      paste0("file://<root>/", substring(url, nchar(root) + 8L)), url)
  }
  mo
}

# module_run(<test paper p1>, "data_check", local_path = <fixture dir>): the
# real pipeline, with repo_check listing the directory.
dc_run_local <- function(dir, ..., copy = FALSE) {
  p <- test_paper("Some text.")
  p$paper_id <- "p1"
  .dc_careless_off()
  src <- file.path(dc_fixtures, "repos", dir)
  # archives are unpacked beside themselves: work on a copy at a fixed relative
  # path, so that the repository URL (and the report) is the same on every run
  if (isTRUE(copy)) {
    work <- file.path("parity", "_out", "dc_local_copy")
    unlink(work, recursive = TRUE)
    dir.create(work, recursive = TRUE)
    file.copy(src, work, recursive = TRUE)
    src <- file.path(work, basename(src))
  }
  mo <- suppressWarnings(withr::with_options(list(metacheck.llm.use = FALSE),
    module_run(p, "data_check", local_path = src, local_only = TRUE, ...)))
  .dc_norm_paths(mo)
}

# module_run(<repo_check output of `name`>, "data_check", ...).
dc_run <- function(name, ..., careless = FALSE, llm = NULL, prev = NULL) {
  prev <- prev %||% dc_prev(name)
  if (isTRUE(careless)) .dc_careless_on() else .dc_careless_off()
  on.exit(.dc_careless_off(), add = TRUE)
  # warnings (e.g. readxl's "Could not read ...") name absolute paths, which
  # must not end up in the goldens; they are not compared anyway
  mo <- suppressWarnings(if (is.null(llm)) {
    withr::with_options(list(metacheck.llm.use = FALSE),
                        module_run(prev, "data_check", ...))
  } else {
    testthat::with_mocked_bindings(
      withr::with_options(list(metacheck.llm.use = TRUE),
                          module_run(prev, "data_check", ...)),
      llm = dc_mock_llm(dc_mock_spec(llm)), .package = "metacheck")
  })
  .dc_norm_paths(mo)
}

# The data of every scroll_table() in a module report: the `table <- ...` line
# of each R chunk (also those pasted inside the tabset strings), evaluated.
dc_report_tables <- function(mo) {
  txt <- paste(mo$report, collapse = "\n")
  chunks <- regmatches(txt, gregexpr("(?s)```\\{r\\}.*?\n```", txt, perl = TRUE))[[1]]
  lapply(chunks, function(s) {
    code <- sub("^```\\{r\\}", "", sub("```$", "", s))
    e <- new.env()
    eval(parse(text = code)[[1]], e)
    df <- as.data.frame(e$table, check.names = FALSE)
    rownames(df) <- NULL
    df
  })
}

dc_run_tables <- function(name, ...) dc_report_tables(dc_run(name, ...))

# The dv_report of the empty() return (a character vector: prose blocks plus
# one scroll_table() chunk): its prose and the data of its tables.
dc_dv_report <- function(name, ...) {
  rep <- dc_run(name, ...)$dv_report
  list(text = rep[!grepl("```{r}", rep, fixed = TRUE)],
       tables = dc_report_tables(list(report = rep)))
}

# The data_check module file sourced into an environment (its internal
# helpers), plus the closures defined inside data_check() itself.
dc_env <- function() {
  e <- new.env(parent = asNamespace("metacheck"))
  sys.source(metacheck:::module_find("data_check"), envir = e)
  inner <- c(".tree_type_icon", ".tree_type_label", "repo_tree_rows",
             "repo_tree_block", "file_tabset", "desc_file_tabset",
             "data_file_tabset")
  for (ex in as.list(body(e$data_check))[-1]) {
    if (is.call(ex) && identical(ex[[1]], as.name("<-")) && is.name(ex[[2]]) &&
        as.character(ex[[2]]) %in% inner) {
      eval(ex, e)
    }
  }
  e
}

# Run a module-internal helper with the vendored careless loaded.
dc_with_careless <- function(expr) {
  .dc_careless_on()
  on.exit(.dc_careless_off(), add = TRUE)
  force(expr)
}
