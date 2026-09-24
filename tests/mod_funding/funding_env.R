# Helpers for the mod_funding parity cases: evaluate the internal functions of
# metacheck's inst/modules/funding_check.R (which are not exported).

fc_env <- function(capture = FALSE) {
  e <- new.env(parent = globalenv())
  sys.source(file.path(root, "upstream", "metacheck", "inst", "modules", "funding_check.R"),
             envir = e)
  if (capture) {
    e$.captured <- character(0)
    wrap <- function(fn) {
      force(fn)
      function(pattern, x, ...) {
        e$.captured <- c(e$.captured, pattern)
        fn(pattern, x, ...)
      }
    }
    e$grep <- wrap(base::grep)
    e$grepl <- wrap(base::grepl)
    e$gsub <- function(pattern, replacement, x, ...) {
      e$.captured <- c(e$.captured, pattern)
      base::gsub(pattern, replacement, x, ...)
    }
  }
  e
}

# the regex patterns each function passes to grep()/grepl()/gsub(), in call order
fc_patterns <- function(calls) {
  out <- list()
  for (nm in names(calls)) {
    e <- fc_env(capture = TRUE)
    e[[nm]](calls[[nm]])
    out[[nm]] <- e$.captured
  }
  out
}

# call functions on the same character vector; integer results stay 1-based
fc_apply <- function(fns, article) {
  e <- fc_env()
  out <- list()
  for (fn in fns) {
    out[[fn]] <- e[[fn]](article)
  }
  out
}

# rtransparent_funding() on several character vectors
fc_funding <- function(articles) {
  e <- fc_env()
  vapply(articles, function(a) e$rtransparent_funding(a), character(1), USE.NAMES = FALSE)
}
