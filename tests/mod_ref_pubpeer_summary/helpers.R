# Helpers for the mod_ref_pubpeer_summary parity cases
# (parity/cases/mod_ref_pubpeer_summary.yaml).
#
# The Python twin is tests/mod_ref_pubpeer_summary/helpers.py; both build the
# same synthetic papers and module chains. PubPeer responses for the synthetic
# DOIs are recorded in tests/mod_ref_pubpeer_summary/mock (see make_mocks.py).
#
#   source(file.path(root, "tests/mod_ref_pubpeer_summary/helpers.R"))
#   chain(pp_list(), c("ref_pubpeer", "ref_summary"))

# A paper whose references have the given DOIs (bib_id 0, 1, ...).
pp_paper <- function(dois, id = "synthetic") {
  n <- length(dois)
  refs <- sprintf("Reference %d. https://doi.org/%s", seq_len(n) - 1L, dois)
  p <- test_paper(c("Body text.", refs))
  p$paper_id <- id
  p$bib <- data.frame(
    bib_id = seq_len(n) - 1L,
    text_id = 1L + seq_len(n),
    doi = as.character(dois)
  )
  p
}

# Synthetic DOI sets (their PubPeer responses are in the mock directory).
pp_dois <- list(
  single = "10.9999/pp.one",
  statcheck = c("10.9999/pp.stat", "10.9999/pp.none"),
  fail = "10.9999/pp.fail",
  p1 = c("10.9999/PP.Upper", "10.9999/pp.zero", NA, "", "10.9999/pp.dup", "10.9999/pp.dup"),
  p2 = c("10.9999/pp.nourl", "10.9999/pp.stat", "10.9999/pp.many"),
  P0 = c("10.9999/pp.dup", "10.9999/pp.nohit"),
  mixed = c("10.1002/bdm.586", "10.1371/journal.ppat.1000066", "10.9999/pp.one",
            NA, "10.9999/pp.stat", "10.1177/0956797614520714")
)

pp_single <- function() pp_paper(pp_dois$single, "single")
pp_statcheck <- function() pp_paper(pp_dois$statcheck, "statcheck")
pp_fail <- function() pp_paper(pp_dois$fail, "fail")
pp_mixed <- function() pp_paper(pp_dois$mixed, "mixed")

# A paper list: duplicated DOIs (within and across papers), an upper-case DOI,
# NA and empty DOIs, zero comments, Statcheck-only comments and a missing URL.
pp_list <- function() {
  paperlist(
    pp_paper(pp_dois$p1, "p1"),
    pp_paper(pp_dois$p2, "p2"),
    pp_paper(pp_dois$P0, "P0")
  )
}

# The demo paper without references (testthat: "no references").
demo_no_refs <- function() {
  paper <- demopaper()
  paper$bib <- paper$bib[c(), ]
  paper$bib_match <- paper$bib_match[c(), ]
  paper
}

# The demo paper keeping only references without a DOI (testthat: "no DOIs").
demo_no_dois <- function() {
  paper <- demopaper()
  paper$bib <- paper$bib[is.na(paper$bib$doi), ]
  paper$bib_match <- NULL
  paper
}

# The demo paper without bib_match (testthat: "no bib_match").
demo_no_match <- function() {
  paper <- demopaper()
  paper$bib_match <- NULL
  paper
}

# The psychsci fixture papers.
psychsci3 <- function() {
  read(file.path(root, "upstream/metacheck/tests/testthat/fixtures/psychsci",
                 c("0956797613520608.json", "0956797614522816.json",
                   "0956797614527830.json")))
}

# Run modules one after another (module_run(module_run(paper, m1), m2) ...).
chain <- function(paper, modules, ...) {
  out <- paper
  for (m in modules) out <- module_run(out, m)
  out
}

# Replace one column of a module output's table (to feed ref_summary edge cases).
set_table_col <- function(out, col, values) {
  out$table[[col]] <- values
  out
}

# Drop columns from a module output's table.
drop_table_cols <- function(out, cols) {
  out$table <- out$table[, setdiff(names(out$table), cols)]
  out
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

# Set every *_mismatch column of a module output's table to `value`.
set_mismatch <- function(out, value) {
  for (col in grep("_mismatch$", names(out$table), value = TRUE)) {
    out$table[[col]] <- rep(value, nrow(out$table))
  }
  out
}
