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

# ---- review scenarios (parity/cases/mod_ref_pubpeer_summary_review.yaml) ----------

# More synthetic DOI sets (their PubPeer responses are in the mock directory).
pp_dois$odd <- c("10.9999/pp.emptyusers", "10.9999/pp.nulltc", "10.9999/pp.statlist",
                 "10.9999/pp.emptyurl", "10.9999/pp.statcase")
pp_dois$nourl_only <- "10.9999/pp.nourl"
pp_dois$nourl_some <- c("10.9999/pp.nourl", "10.9999/pp.zero")

pp_odd <- function() pp_paper(pp_dois$odd, "odd")
pp_nourl_only <- function() pp_paper(pp_dois$nourl_only, "nourl_only")
pp_nourl_some <- function() pp_paper(pp_dois$nourl_some, "nourl_some")

# A paper list whose second paper has no references at all.
pp_list_empty <- function() {
  paperlist(pp_paper(pp_dois$odd, "odd"), pp_paper(character(0), "empty"))
}

# A paper list with mixed-case paper ids and a bib_match table, so ref_table()
# sorts the references with dplyr::arrange(paper_id, bib_id) (C locale).
pp_case_list <- function() {
  b <- pp_paper(c("10.9999/pp.one", "10.9999/pp.dup"), "b")
  A <- pp_paper(c("10.9999/pp.none", "10.9999/pp.stat"), "A")
  A$bib_match <- data.frame(bib_id = 1L, doi = "10.9999/pp.many")
  a <- pp_paper("10.9999/pp.zero", "a")
  paperlist(b, A, a)
}

# Append copies of rows `idx` (1-based) of a module output's table, with the
# columns in `values` replaced.
add_rows <- function(out, idx, values) {
  new <- out$table[idx, ]
  for (col in names(values)) new[[col]] <- values[[col]]
  out$table <- dplyr::bind_rows(out$table, new)
  out
}

# Replace several columns of a module output's table.
set_table_cols <- function(out, values) {
  for (col in names(values)) out$table[[col]] <- values[[col]]
  out
}

# ref_retraction's table gains columns that collide with ref_summary's own
# (dplyr adds .x/.y suffixes, repeating them until the names are unique).
rv_ret_collide <- function() {
  chain(demopaper(), c("ref_accuracy", "ref_pubpeer", "ref_retraction")) |>
    set_table_cols(list(pubpeer = "P", pubpeer.x = "PX", accuracy_mismatch = "AM"))
}

# ref_retraction's table keeps only the join keys (and the dropped text/doi).
rv_ret_keys <- function() {
  chain(demopaper(), "ref_retraction") |>
    drop_table_cols("retractionwatch")
}

# duplicated ref_accuracy rows: one in the same group (names repeat), one in a
# new no_match group (the reference gets two rows, in group order).
rv_acc_dup <- function() {
  chain(demopaper(), "ref_accuracy") |>
    add_rows(4L, list(title_mismatch = TRUE)) |>
    add_rows(1L, list(no_match = FALSE)) |>
    add_rows(2L, list(no_match = NA, year_mismatch = TRUE))
}

# character *_mismatch / no_match columns (`%in%` coerces FALSE to "FALSE").
rv_acc_chr <- function() {
  chain(demopaper(), "ref_accuracy") |>
    set_table_cols(list(
      doi_mismatch = c("TRUE", "FALSE", "false", NA, "0"),
      year_mismatch = c("FALSE", "FALSE", "yes", "FALSE", "FALSE"),
      container_mismatch = rep("FALSE", 5),
      title_mismatch = c("FALSE", "TRUE", "FALSE", "FALSE", NA),
      author_mismatch = rep("FALSE", 5),
      no_match = c("TRUE", "FALSE", "FALSE", NA, "1")
    ))
}

# integer / double *_mismatch columns mixed with logical ones (combined to integer).
rv_acc_num <- function() {
  chain(demopaper(), "ref_accuracy") |>
    set_table_cols(list(
      doi_mismatch = c(1L, 0L, NA, 2L, 0L),
      year_mismatch = c(0, 0.5, 0, 0, 0),
      title_mismatch = c(FALSE, FALSE, NA, FALSE, TRUE),
      no_match = c(1, 0, NA, 0, 2)
    ))
}

# logical and character *_mismatch columns cannot be combined by pivot_longer().
rv_acc_mixed <- function() {
  chain(demopaper(), "ref_accuracy") |>
    set_table_cols(list(doi_mismatch = c("TRUE", "FALSE", "FALSE", "FALSE", "FALSE")))
}

# an all-NA logical column combines with a character one.
rv_acc_na_chr <- function() {
  chain(demopaper(), "ref_accuracy") |>
    set_table_cols(list(
      doi_mismatch = rep(NA, 5),
      year_mismatch = c("TRUE", "FALSE", "FALSE", "x", "FALSE"),
      container_mismatch = rep("FALSE", 5),
      title_mismatch = rep("FALSE", 5),
      author_mismatch = rep("FALSE", 5)
    ))
}

# extra columns matching grep("no_match|_mismatch") but not ends_with("_mismatch"),
# and one only ends_with() matches ignoring case.
rv_acc_extra <- function() {
  chain(demopaper(), "ref_accuracy") |>
    set_table_cols(list(
      title_mismatch_score = c(0.1, 0.2, 0.3, 0.4, 0.5),
      no_match_reason = c("a", "b", "c", "d", "e"),
      doi_mismatch_MISMATCH = c(FALSE, TRUE, FALSE, FALSE, NA)
    ))
}

# ref_accuracy without any *_mismatch column (pivot_longer() selects nothing).
rv_acc_no_mismatch <- function() {
  out <- chain(demopaper(), "ref_accuracy")
  drop_table_cols(out, grep("_mismatch$", names(out$table), value = TRUE))
}

# ref_pubpeer's table with missing URLs.
rv_pp_url_na <- function() {
  chain(demopaper(), "ref_pubpeer") |>
    set_table_cols(list(url = NA_character_))
}

# ref_replication's table without replication_type, with a duplicated row.
rv_rep_no_type <- function() {
  chain(demopaper(), "ref_replication") |>
    add_rows(1L, list()) |>
    drop_table_cols("replication_type")
}

# test_paper() with a fixed paper_id (its own id comes from the clock).
test_paper_id <- function(text, id) {
  p <- test_paper(text)
  p$paper_id <- id
  p
}

# SICI and non-ASCII DOIs (lower-cased for the request, joined back in their own case).
pp_dois$sici <- c("10.1002/(SICI)1099-0720(199908)13:4<333::AID-ACP588>3.0.CO;2-Z",
                  "10.9999/PP.Ä", "10.9999/ẞ.x")
pp_sici <- function() pp_paper(pp_dois$sici, "sici")

# A bibr export schema 12.0 fixture paper.
bibr12 <- function(name) {
  read(file.path(root, "upstream/metacheck/tests/testthat/fixtures/bibr12",
                 paste0(name, ".json")))
}
