# Helpers for the mod_ref_db parity cases (parity/cases/mod_ref_db.yaml).
#
# The Python twin is tests/mod_ref_db/helpers.py; both build the same
# synthetic papers so the reference-database modules can be tested on DOIs
# that hit RetractionWatch, FLoRA and the miscite database.
#
#   source(file.path(root, "tests/mod_ref_db/helpers.R"))
#   ref_paper(c("10.1177/0956797614520714", NA), cites = list(0L, c(0L, 1L)))

# A paper whose references have the given DOIs (bib_id 0, 1, ...). Each
# element of `cites` is a body sentence citing those bib_ids (an xref each).
ref_paper <- function(dois, id = "synthetic", cites = list()) {
  n <- length(dois)
  body <- vapply(seq_along(cites), function(i) {
    sprintf("Body sentence %d cites %s.", i, paste(cites[[i]], collapse = " and "))
  }, character(1))
  refs <- sprintf("Reference %d. https://doi.org/%s", seq_len(n) - 1L, dois)
  p <- test_paper(c(body, refs))
  p$paper_id <- id
  p$bib <- data.frame(
    bib_id = seq_len(n) - 1L,
    text_id = length(body) + seq_len(n),
    doi = as.character(dois)
  )
  p$xref <- data.frame(
    xref_id = as.integer(unlist(cites)),
    xref_type = rep("bibr", length(unlist(cites))),
    contents = rep("(ref)", length(unlist(cites))),
    text_id = rep(seq_along(cites), lengths(cites))
  )
  p
}

# The data of the first report table of a module output, with the arguments
# scroll_table() passed to metacheck::report_table().
report_tbl <- function(o) {
  chunk <- Filter(function(x) grepl("# table data", x, fixed = TRUE), o$report)[[1]]
  code <- gsub("```[^\n]*", "", chunk)
  code <- sub("metacheck::report_table(", "report_table(", code, fixed = TRUE)
  e <- new.env()
  e$report_table <- function(table, colwidths, maxrows, escape) {
    list(table = as.data.frame(table), colwidths = colwidths, maxrows = maxrows, escape = escape)
  }
  eval(parse(text = code), envir = e)
}

# The demo paper without references (testthat: "no references").
demo_no_refs <- function() {
  paper <- demopaper()
  paper$bib <- paper$bib[c(), ]
  paper$bib_match <- NULL
  paper
}

# The demo paper keeping only references without a DOI (testthat: "no DOIs").
demo_no_dois <- function() {
  paper <- demopaper()
  paper$bib$doi[[1]] <- NA
  paper$bib <- paper$bib[is.na(paper$bib$doi), ]
  paper$bib_match <- NULL
  paper
}
