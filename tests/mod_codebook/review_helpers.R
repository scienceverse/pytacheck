# R side of the codebook_check review parity cases
# (parity/cases/mod_codebook_review.yaml, written by
# tests/mod_codebook/make_review_cases.py). The Python twin is
# tests/mod_codebook/review_helpers.py: every rv_*() input below is built
# identically there.

source("tests/mod_codebook/cbc_helpers.R", local = TRUE)

# The module's own environment (its internal helpers).
rv_env <- function() {
  e <- new.env(parent = asNamespace("metacheck"))
  sys.source(metacheck:::module_find("codebook_check"), envir = e)
  e
}

# The attributes R keeps on each OSD object (not part of the canonical value).
rv_osd_attrs <- function(osds) {
  lapply(osds, function(o) list(
    write = attr(o, "write"), code = attr(o, "code"),
    orphan_total = attr(o, "orphan_total"), scale = attr(o, "scale"),
    dedup_key = attr(o, "dedup_key")))
}

# A data frame whose column names may repeat (or be empty).
rv_named_frame <- function(nms, values = c(1, 2, 3, 4, 5)) {
  df <- as.data.frame(matrix(values, nrow = length(values), ncol = length(nms)))
  names(df) <- nms
  df
}

rv_propagate_df <- function() {
  data.frame(
    source_file = c("s.csv", "s.csv", "s.csv", "s.csv", "s.csv", "s.csv", "t.csv", "t.csv"),
    column_name = c("1", "2", "_3", "q_1", "q_2", "__", "q_1", "q_9"),
    scale = c("S", NA, "", "Q", NA, NA, NA, "T"),
    scale_confidence = c("high", "low", "x", "medium", NA, "z", NA, "low"),
    scale_source = c("manuscript", NA, "k", "matched", NA, NA, NA, "self_generated"),
    stringsAsFactors = FALSE)
}

rv_dup_names <- function() {
  c("b", "a", "b", "a", "B", "A", "B", "A", "a", "_x", "_x", "Z", "Z",
    "é", "é", "e", "e", "solo")
}

rv_text_scales <- function(cols = c("scale_name", "acronym", "n_items", "administered",
                                    "confidence")) {
  ts <- data.frame(
    scale_name = c("Grit Scale", NA, "  ", "Big Five Inventory", "Life Orientation Test",
                   "Short Scale", "grit scale"),
    acronym = c(NA, "X", "Y", "BFI", "", "LOT", " GS "),
    n_items = c("12", NA, "", " 5 ", "", NA, "8"),
    administered = c("unclear", "yes", NA, "Unclear ", "no", "UNCLEAR", "unclear"),
    confidence = c("high", "low", "low", "medium", "high", NA, "low"),
    stringsAsFactors = FALSE)
  ts[, cols, drop = FALSE]
}

rv_split_df <- function() {
  data.frame(
    x_1 = c("1", "2", "NA", "abc", "3"),
    x_2 = c(1, Inf, 3, 4, 2),
    x_3 = c(NA, NA, NA, NA, NA),
    x_4 = c(1.5, 2, 3, 4, 2),
    x_total = c(10, 20, 30, 40, 25),
    stringsAsFactors = FALSE)
}

rv_prefix_names <- function() {
  c("StartDate", "bfi_1", "bfi_2", "Q1_DO_x", "bfi_3", "bfi_1", "",
    "émo_1", "émo_2", "émo_3", "ab_1", "ab_2", "ab_3", "",
    "zz_1", "zz_2", "zz_3", "Q9_1", "Q9_2", "Q9_3", "Q9_TEXT")
}

rv_likert_labels <- function() {
  data.frame(
    source_file = "s.csv",
    column_name = c("a1", "a2", "b1", "b2", "c1"),
    value_labels = c(
      "[\"x\",\"y\",\"z\"]",
      "{\"1.5\":\"one\",\"2\":\"two\",\" 3\":\"three\",\"1e1\":\"ten\",\"-99\":\"Refused\",\"x\":\"bad\"}",
      "{\"1\":\"only\",\"-9\":\"Missing\"}",
      "{\"1\":\"one\",\"2\":\"two\"}",
      NA),
    missing_values = c(NA, "{\"-99\":\"Refused\"}", "{\"-9\":\"Missing\"}", NA, NA),
    stringsAsFactors = FALSE)
}

rv_likert_columns <- function() {
  data.frame(
    source_file = "s.csv",
    column_name = c("c1", "c1", "c2", "c3", "d1", "d2"),
    min = c(1, 2, NA, 1, 0.5, 1),
    max = c(7, 7, 7, NaN, 3, 3),
    stringsAsFactors = FALSE)
}

rv_synonyms <- function() {
  data.frame(
    source_file = c("a.csv", "a.csv", "a.csv", "a.csv", "b.csv", "b.csv", "b.csv", "c.csv"),
    column_name = paste0("v", 1:8),
    scale = c("trust in science", "science trust ratings", "trust", "Other thing",
              "alpha beta", "beta alpha", "the scale", "solo label"),
    confidence = "medium", scale_source = "self_generated",
    stringsAsFactors = FALSE)
}

rv_osd_groups <- function() {
  sg <- data.frame(
    source_file = c("s.csv", "s.csv", "s.csv", "s.csv", "t.csv", "s.csv", "t.csv"),
    prefix = c("AQ", "AQT", "ZZ", "YY", "AQ", "OT", "ZZ"),
    scale = c("Autism Quotient", "Autism Quotient", NA, "Made Up", "Autism Quotient",
              "Orphan Total", NA),
    confidence = c("high", "medium", NA, "low", "high", "medium", NA),
    scale_source = c("manuscript", "manuscript", NA, "self_generated", "manuscript",
                     "matched", NA),
    n_columns = c(3L, 3L, 3L, 2L, 3L, 3L, 3L),
    max_item = c(3L, NA, 3L, 2L, 3L, NA, 3L),
    totals_only = c(FALSE, TRUE, FALSE, FALSE, FALSE, TRUE, FALSE),
    stringsAsFactors = FALSE)
  sg$columns <- list(c("AQ1", "AQ2", "AQ3"), c("AQT_a", "AQT_b", "AQT_c"),
                     c("ZZ1", "ZZ2", "ZZ3"), c("YY1", "YY2"), c("AQ3", "AQ1", "AQ2"),
                     c("OT_a", "OT_b", "OT_c"), c("ZZ1", "ZZ2", "ZZ3"))
  sg
}

rv_osd_columns <- function() {
  cols <- c("AQ1", "AQ2", "AQ3", "ZZ1", "ZZ2", "ZZ3", "YY1", "YY2")
  data.frame(
    source_file = c(rep("s.csv", 8), "t.csv", "t.csv", "t.csv"),
    column_name = c(cols, "ZZ1", "ZZ2", "ZZ3"),
    min = c(1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1),
    max = c(5, 5, 5, 7, 7, 7, 100, 100, 7, 7, 7),
    stringsAsFactors = FALSE)
}

rv_osd_labels <- function() {
  data.frame(
    source_file = c("s.csv", "s.csv", "s.csv", "s.csv", "t.csv"),
    column_name = c("AQ1", "AQ2", "ZZ1", "YY1", "AQ1"),
    label = c("I like crowds", NA, "", "Slider one", "I like crowds"),
    question = c(NA, "Do you notice patterns?", "Zed one?", NA, NA),
    value_labels = c("{\"1\":\"No\",\"2\":\"Yes\"}", NA, NA, NA, NA),
    stringsAsFactors = FALSE)
}

# The osd objects and their attributes, for .scales_to_osd() of rv_osd_groups().
rv_scales_to_osd <- function() {
  osds <- rv_env()$.scales_to_osd(rv_osd_groups(), rv_osd_columns(), rv_osd_labels())
  list(osd = osds, attrs = rv_osd_attrs(osds))
}

# A module run's scales_osd attributes.
rv_run_osd_attrs <- function(mo) rv_osd_attrs(mo$scales_osd)

# The module's embedded-label harvest of one labelled data file (read labels-only).
rv_haven_labels <- function(f) {
  ext <- tolower(tools::file_ext(f))
  df <- switch(ext,
    sav      = as.data.frame(haven::read_sav(f, n_max = 0L)),
    dta      = as.data.frame(haven::read_dta(f, n_max = 0L)),
    sas7bdat = as.data.frame(haven::read_sas(f, n_max = 0L)))
  metacheck:::.extract_haven_labels(df, basename(f), group = "g1")
}

rv_text_paper <- function() {
  p <- test_paper(c(
    "We used the BFI-2 questionnaire to measure personality.",
    "Participants felt enthusiastic and determined during the task.",
    "The q.x variable was coded by two raters.",
    "An unrelated sentence about the weather.",
    "ITEM responses were recorded on a scale from 1 to 5.",
    "The Perceived Stress Scale (PSS) contains 10 items.",
    "Enthusiastic participants were more determined."))
  p$paper_id <- "tp"
  p
}

# ── LLM tiers under fixed, adversarial answers (fixtures/llm_review.json) ─────
# Each phase answers with the same data frame whatever it is asked (JSON null
# is NA; a column of JSON booleans is logical). "Matching codebook columns"
# is split into its merge ("Column: ...") and match requests.
rv_llm_spec <- function(name) {
  jsonlite::fromJSON("tests/mod_codebook/fixtures/llm_review.json",
                     simplifyVector = FALSE)[[name]]
}

.rv_col <- function(x) {
  vals <- Filter(Negate(is.null), x)
  if (length(vals) && all(vapply(vals, is.logical, logical(1))))
    return(vapply(x, function(e) if (is.null(e)) NA else e, logical(1)))
  vapply(x, function(e) if (is.null(e)) NA_character_ else as.character(e), character(1))
}

rv_fixed_llm <- function(spec) {
  function(text, system_prompt = NULL, type = NULL, text_col = "text", model = NULL,
           params = list(), phase = NULL, ...) {
    key <- phase
    if (identical(phase, "Matching codebook columns")) {
      txt <- as.character(text[[text_col]][1])
      key <- paste0(phase, if (startsWith(txt, "Column: ")) "/merge" else "/match")
    }
    cols <- spec[[key]]
    res <- if (is.null(cols)) data.frame() else {
      df <- as.data.frame(lapply(cols, .rv_col), stringsAsFactors = FALSE,
                          check.names = FALSE)
      names(df) <- names(cols)
      df
    }
    attr(res, "llm") <- list(model = "mock/rv")
    res
  }
}

# module_run(<data_check output>, "codebook_check") with llm() answering from `spec`.
rv_llm_run <- function(scenario, spec, ..., prev = NULL) {
  prev <- prev %||% cbc_prev(scenario)
  testthat::with_mocked_bindings(
    withr::with_options(list(metacheck.llm.use = TRUE),
                        module_run(prev, "codebook_check", ...)),
    llm = rv_fixed_llm(rv_llm_spec(spec)), .package = "metacheck")
}

# data_check's output for `scenario` with no paper (paper = NULL: local files only).
rv_prev_null <- function(scenario) .cbc_output(NULL, cbc_pieces(scenario), "p1")
