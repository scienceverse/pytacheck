# R side of the mod_ethics parity helpers (see tests/mod_ethics/parity_support.py).

# A test paper with a fixed paper_id; with `section_type`, one section per sentence.
ec_paper <- function(text, id = "p1", section_type = NULL) {
  p <- test_paper(text)
  p$paper_id <- id
  if (!is.null(section_type)) {
    n <- length(text)
    p$text$section_id <- seq_len(n) - 1
    sec <- p$section[rep(1, n), ]
    sec$section_id <- seq_len(n) - 1
    sec$header <- paste("Section", seq_len(n))
    sec$section_type <- section_type
    p$section <- sec
  }
  p
}

# A paper list of test papers with the given ids (a character vector) and
# sentences (a list of character vectors).
ec_papers <- function(ids, texts) {
  do.call(paperlist, unname(Map(function(id, txt) ec_paper(txt, id), ids, texts)))
}

# The module function itself (not module_run()), sourced from the installed
# package like module_run() does, for cases that check its raw return value.
ec_direct <- function(paper) {
  suppressPackageStartupMessages(require("dplyr", quietly = TRUE, character.only = TRUE))
  env <- new.env(parent = asNamespace("metacheck"))
  sys.source(system.file("modules", "ethics_check.R", package = "metacheck"), envir = env)
  env$ethics_check(paper)
}

# Keep only `cols` (in that order) of one of the paper's tables.
ec_select <- function(p, table, cols) {
  p[[table]] <- p[[table]][, cols, drop = FALSE]
  p
}

# Keep the rows `rows` (1-based, may repeat) of one of the paper's tables.
ec_rows <- function(p, table, rows) {
  p[[table]] <- p[[table]][rows, , drop = FALSE]
  rownames(p[[table]]) <- NULL
  p
}

# Set one column of one of the paper's tables.
ec_set <- function(p, table, col, value) {
  p[[table]][[col]] <- value
  p
}

# A small paper with a title, an abstract and one more part (as the rule of .data_source()
# weighs where a sentence stands, #695 point 18), with a fixed paper_id.
ds_paper <- function(abstract = character(0), body = character(0),
                     header = "Method", title = "Test Paper", id = "p1") {
  p <- test_paper(c(abstract, body))
  p$paper_id <- id
  p$text$section_id <- c(rep(0, length(abstract)), rep(1, length(body)))
  p$section <- dplyr::tibble(
    section_id = c(0, 1), header = c("Abstract", header),
    parent_section_id = NA_integer_, section_type = c("abstract", "method"),
    classification_score = 0
  )
  p$info$title <- title
  p
}
