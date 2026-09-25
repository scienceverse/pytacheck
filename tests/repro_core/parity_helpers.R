# R side of the repro_core parity helpers (twins of parity_support.py).
# Sourced by the cases' `$expr` code; the parity runner's working directory
# is the repository root.

rc_repro_fixtures <- "upstream/metacheck/tests/testthat/fixtures/repro"
rc_fixtures <- "tests/repro_core/fixtures"

rc_files_df <- function(file_name, reads = NULL, writes = NULL, sources = NULL) {
  df <- data.frame(file_name = file_name)
  if (!is.null(reads)) df$reads <- reads
  if (!is.null(writes)) df$writes <- writes
  if (!is.null(sources)) df$sources <- sources
  df
}

rc_run_order_full <- function(files, extra_edges = NULL) {
  out <- repro_run_order(files, extra_edges)
  list(out, attr(out, "cycle"), attr(out, "ambiguous"), attr(out, "fuzzy_sources"))
}

rc_listing <- function(root) {
  sort(list.files(root, recursive = TRUE, all.files = TRUE, include.dirs = TRUE))
}

rc_materialize <- function(plan, structure_df) {
  root <- file.path(tempfile(), "root")
  out <- repro_materialize_layout(plan, structure_df, root)
  list(files = rc_listing(root),
       materialised = attr(out, "materialised"),
       is_root = identical(as.vector(out), root))
}

rc_unroot <- function(x, root) gsub(root, "<root>", x, fixed = TRUE)

rc_write_scripts <- function(code_text_list, rewrite_list, plan, inject_libs = NULL) {
  root <- file.path(tempfile(), "root")
  dir.create(root, recursive = TRUE)
  out <- repro_write_scripts(code_text_list, rewrite_list, plan, root,
                             inject_libs = inject_libs)
  written <- lapply(out$script_path, function(p) rc_unroot(readLines(p), root))
  out$script_path <- rc_unroot(out$script_path, root)
  out$run_dir <- rc_unroot(out$run_dir, root)
  list(table = out, written = written, files = rc_listing(root))
}

rc_run_scripts <- function(scripts, order = NULL, extra = NULL, lib = FALSE, ...) {
  root <- file.path(tempfile(), "root")
  dir.create(root, recursive = TRUE)
  file.copy(file.path(rc_repro_fixtures, "data.csv"), file.path(root, "data.csv"))
  names <- character(0)
  for (s in scripts) {
    src <- file.path(rc_repro_fixtures, s)
    if (!file.exists(src)) src <- file.path(rc_fixtures, "scripts", s)
    file.copy(src, file.path(root, s))
    names <- c(names, s)
  }
  for (nm in names(extra)) {
    writeLines(extra[[nm]], file.path(root, nm), sep = "")
    names <- c(names, nm)
  }
  run_tbl <- data.frame(file_name = names, script_path = file.path(root, names),
                        run_dir = root)
  lib_dir <- NULL
  if (isTRUE(lib)) {
    lib_dir <- file.path(root, "lib_x")
    dir.create(lib_dir)
  }
  out <- repro_run_scripts(run_tbl, order = order %||% names, lib_dir = lib_dir, ...)
  out$elapsed <- NULL
  for (col in c("error", "stdout", "stderr")) out[[col]] <- rc_unroot(out[[col]], root)
  out$script_lines <- lapply(out$script_lines, rc_unroot, root = root)
  out
}
