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
