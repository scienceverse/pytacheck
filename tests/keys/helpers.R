# R side of the keys parity helpers (twins of tests/keys/parity_support.py).
# Sourced by the cases' `$expr` code. Nothing here returns a key itself: the
# tables hold only the type of key and the beginning of it.

kp_dir <- function(files) {
  d <- tempfile("kp")
  dir.create(d)
  for (n in names(files)) {
    f <- file.path(d, n)
    dir.create(dirname(f), recursive = TRUE, showWarnings = FALSE)
    writeLines(files[[n]], f)
  }
  d
}

kp_scan <- function(files) {
  d <- kp_dir(files)
  on.exit(unlink(d, recursive = TRUE))
  f <- metacheck:::.key_scan_files(file.path(d, names(files)), names = names(files))
  ns <- attr(f, "not_searched")
  attr(f, "not_searched") <- NULL
  list(keys = f, not_searched = ns)
}

kp_find <- function(text, path = NULL) {
  f <- metacheck:::.key_find(text, path = path)
  f$key <- NULL
  f
}

kp_remove <- function(x) {
  out <- metacheck:::.key_remove(x)
  n <- attr(out, "keys_removed")
  attr(out, "keys_removed") <- NULL
  list(out = out, n = n %||% 0L)
}

kp_tables <- function(tables) {
  metacheck:::.key_scan_tables(tables)
}

kp_data_check <- function(files, data = NULL) {
  d <- kp_dir(files)
  on.exit(unlink(d, recursive = TRUE))
  if (!is.null(data)) for (n in names(data)) utils::write.csv(data[[n]], file.path(d, n), row.names = FALSE)
  mo <- module_run(test_paper("x"), "data_check", local_path = d, local_only = TRUE)
  rep <- module_report(mo)
  list(keys = mo$keys, traffic_light = mo$traffic_light, key_n = mo$summary_table$key_n,
       has_block = grepl("Keys in the repository", rep, fixed = TRUE),
       summary_found = regmatches(mo$summary_text, regexpr("We found [0-9]+ keys?", mo$summary_text)))
}

kp_rules <- function() {
  x <- metacheck:::.key_rules()
  list(n = nrow(x$patterns), names = x$patterns$name, confidence = x$patterns$confidence)
}
