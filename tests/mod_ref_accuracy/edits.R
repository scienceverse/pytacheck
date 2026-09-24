# Helpers for the parity cases in parity/cases/mod_ref_accuracy.yaml: the demo
# paper with edited reference tables. The Python halves live in
# tests/mod_ref_accuracy/edits.py.
#
#   source(file.path(root, "tests/mod_ref_accuracy/edits.R"))

# set `paper[[table]][[col]]` in the row(s) with `bib_id` (a list column cell
# takes any R value, e.g. a data frame of authors)
ra_set <- function(p, table, bib_id, col, value) {
  i <- which(p[[table]]$bib_id == bib_id)
  if (is.list(p[[table]][[col]])) {
    p[[table]][[col]][i] <- list(value)
  } else {
    p[[table]][[col]][i] <- value
  }
  p
}

# the demo paper with edits (each list(table, bib_id, col, value)), the
# bib_match rows of some bib_ids appended again (duplicate matches), some
# tables emptied or dropped, and optionally a new paper_id
ra_demo <- function(edits = list(), drop = character(0), empty = character(0),
                    paper_id = NULL, dup_match = integer(0)) {
  p <- demopaper()
  for (e in edits) p <- ra_set(p, e[[1]], e[[2]], e[[3]], e[[4]])
  for (id in dup_match) {
    p$bib_match <- rbind(p$bib_match, p$bib_match[p$bib_match$bib_id == id, ])
  }
  for (t in empty) p[[t]] <- p[[t]][c(), ]
  for (t in drop) p[[t]] <- NULL
  if (!is.null(paper_id)) p$paper_id <- paper_id
  p
}

# the demo paper with extra cross-references appended to its xref table
ra_xrefs <- function(xref_id, contents, text_id = 4L, xref_type = "bibr",
                     paper = demopaper()) {
  paper$xref <- rbind(paper$xref, data.frame(
    xref_id = as.integer(xref_id), xref_type = xref_type,
    contents = contents, text_id = as.integer(text_id)
  ))
  paper
}

# the data frames that a module's report tables display (scroll_table()
# deparses each into an R chunk: evaluate its `table <- ...` part)
ra_report_tables <- function(o) {
  chunks <- Filter(\(x) grepl("```{r}", x, fixed = TRUE), o$report)
  lapply(chunks, \(ch) {
    code <- sub("(?s).*# table data -+\n", "", ch, perl = TRUE)
    code <- sub("(?s)\n\n# display table.*", "", code, perl = TRUE)
    eval(parse(text = code))
  })
}

# test_paper() with a fixed paper_id (its default id is time-based, which
# would make the goldens change on every regeneration)
ra_test_paper <- function(text, paper_id = "test_paper") {
  p <- test_paper(text)
  p$paper_id <- paper_id
  p
}

# ---- helpers for parity/cases/mod_ref_accuracy_review.yaml ----

# replace a whole column of `paper[[table]]` (NULL drops it)
ra_setcol <- function(p, table, col, value) {
  p[[table]][[col]] <- value
  p
}

# set the text of the text-table row(s) with `text_id`
ra_text <- function(p, text_id, value) {
  p$text$text[p$text$text_id == text_id] <- value
  p
}

# append a copy of the bib_match row for `from` with bib_id `to` (may be NA)
ra_match_copy <- function(p, from, to) {
  row <- p$bib_match[p$bib_match$bib_id == from, ]
  row$bib_id <- to
  p$bib_match <- rbind(p$bib_match, row)
  p
}

# keep only the rows of `paper[[table]]` with these bib_ids
ra_keep <- function(p, table, bib_id) {
  p[[table]] <- p[[table]][p[[table]]$bib_id %in% bib_id, ]
  p
}
