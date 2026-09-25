# Review fixtures for the codebook_check module (adversarial-review scenarios).
#
# Run from the repository root:
#   LANG=C.UTF-8 Rscript tests/mod_codebook/fixtures/make_review_fixtures.R
#
# Same mechanics as make_fixtures.R (a small local repository per scenario,
# metacheck's `data_check` run over it, its output stored as canonical parity
# JSON), but written to fixtures/repos_review/ and fixtures/dc_review/ so this
# script and make_fixtures.R never delete each other's output. cbc_helpers.R /
# helpers.py read dc_review/<scenario>.json for the `rv_*` scenarios.

suppressPackageStartupMessages({
  library(metacheck)
  library(jsonlite)
})
source("parity/r/canonical.R")
llm_use(FALSE)
options(metacheck.osf.cache = FALSE)

fx     <- "tests/mod_codebook/fixtures"
repos  <- file.path(fx, "repos_review")
dcdir  <- file.path(fx, "dc_review")
unlink(repos, recursive = TRUE)
unlink(dcdir, recursive = TRUE)
dir.create(repos, recursive = TRUE)
dir.create(dcdir, recursive = TRUE)

wcsv <- function(df, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(df, path, row.names = FALSE, fileEncoding = "UTF-8")
}
wlines <- function(x, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  con <- file(path, open = "w", encoding = "UTF-8")
  on.exit(close(con))
  writeLines(x, con)
}

scenario <- function(name, build, text = "x") {
  d <- file.path(repos, name)
  dir.create(file.path(d, "data"), recursive = TRUE, showWarnings = FALSE)
  build(d)
  p <- test_paper(text)
  p$paper_id <- "p1"
  dc <- module_run(p, "data_check", local_path = d, local_only = TRUE)
  st <- dc$structure
  if (!is.null(st) && nrow(st) > 0 && "file_location" %in% names(st)) {
    st$file_location <- ifelse(is.na(st$file_location), NA_character_,
                               file.path(d, st$file_path))
  }
  if (!is.null(st) && "referenced_by" %in% names(st)) st$referenced_by <- NULL
  out <- list(
    scenario = name,
    text = as.list(text),
    table = pc_canonical(dc$table),
    structure = pc_canonical(st),
    previews = pc_canonical(dc$previews)
  )
  writeLines(pc_to_json(out, pretty = FALSE), file.path(dcdir, paste0(name, ".json")))
  cat("wrote", name, "\n")
}

# ── Values outside the documented range: %g / signif() formatting, head(8),
#    arrange(desc(n_values)) ties, non-numeric codes, a 0/1 code list ─────────
scenario("rv_violations", function(d) {
  wcsv(data.frame(
    id = 1:15,
    q1 = c(1, 2, 3, 4, 5, 100000, 0.123456, 6, -99, 7, 1234567, 2, 3, 4, 5),
    q2 = c(1:5, 6, 7, 8, 9, 10, 1, 2, 3, 4, 5),
    q3 = c(1:5, 11:20),
    q4 = c(1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 3),
    q5 = c(0, 1, 0, 1, 2, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1),
    q6 = c(1:5, 6, 7, 8, 9, 10, 1, 2, 3, 4, 5)),
    file.path(d, "data", "s.csv"))
  wlines(c("varname,description,values,missing",
           "q1,Agreement,1=Strongly disagree; 2=Disagree; 3=Neutral; 4=Agree; 5=Strongly agree,-99=Refused",
           "q2,Rating,1=Low; 2=2; 3=3; 4=4; 5=High,",
           "q3,Score,1=a; 2=b; 3=c; 4=d; 5=e,",
           "q4,Fruit,a=Apple; b=Banana,",
           "q5,Consent,0=No; 1=Yes,",
           "q6,Other rating,1=Low; 2=2; 3=3; 4=4; 5=High,"),
         file.path(d, "codebook.csv"))
})

# ── C-locale arrange() of the documentation table, tab order, conflicts ──────
scenario("rv_sort", function(d) {
  wlines(c("Zeta,alpha,Beta,_id,émotion,delta",
           "1,2,3,4,5,6", "2,3,4,5,6,7", "3,4,5,6,7,8"),
         file.path(d, "data", "a.csv"))
  wlines(c("Alpha,beta,gamma", "1,2,3", "2,3,4", "3,4,5"),
         file.path(d, "data", "b.csv"))
  wlines(c("varname,description", "alpha,First letter", "Beta,Second letter",
           "émotion,Emotion rating", "gamma,Third letter"),
         file.path(d, "codebook.csv"))
  wlines(c("varname,description", "gamma,A different third letter"),
         file.path(d, "codebook_b.csv"))
})

# ── round(): 12.5% -> 12 and 62.5% -> 62 (half to even), misalignment ────────
scenario("rv_round_low", function(d) {
  wcsv(data.frame(id = 1:5, c1 = 1:5, c2 = 1:5, c3 = 1:5, c4 = 1:5, c5 = 1:5,
                  c6 = 1:5, c7 = 1:5), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "c1,Column one", "other1,Other 1", "other2,Other 2",
           "other3,Other 3", "other4,Other 4", "other5,Other 5"),
         file.path(d, "codebook.csv"))
})

scenario("rv_round_mid", function(d) {
  wcsv(data.frame(id = 1:5, c1 = 1:5, c2 = 1:5, c3 = 1:5, c4 = 1:5, c5 = 1:5,
                  c6 = 1:5, c7 = 1:5), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "c1,Column one", "c2,Column two", "c3,Column three",
           "c4,Column four", "c5,Column five"),
         file.path(d, "codebook.csv"))
})

# ── A Likert block with a duplicated column name, documented by a codebook ───
scenario("rv_dupscale", function(d) {
  set.seed(61)
  m <- matrix(sample(1:5, 20 * 5, TRUE), nrow = 20)
  wlines(c("id,bfi_1,bfi_2,bfi_2,bfi_3,bfi_4",
           apply(cbind(1:20, m), 1, paste, collapse = ",")),
         file.path(d, "data", "s.csv"))
  wlines(c("varname,description,values",
           paste0("bfi_", 1:4, ",Personality item ", 1:4,
                  ",1=Disagree strongly; 2=Disagree; 3=Neutral; 4=Agree; 5=Agree strongly")),
         file.path(d, "codebook.csv"))
})

# ── Item numbers too large for as.integer() (max_item -Inf), a question
#    column (label falls back to the question), a lone short prefix ──────────
scenario("rv_maxitem", function(d) {
  set.seed(62)
  mk <- function(nms) {
    x <- as.data.frame(matrix(sample(1:5, 20 * length(nms), TRUE), nrow = 20))
    names(x) <- nms
    x
  }
  big <- mk(c("abc_99999999997", "abc_99999999998", "abc_99999999999"))
  sat <- mk(paste0("sat_", 1:4))
  wcsv(cbind(id = 1:20, big, sat), file.path(d, "data", "s.csv"))
  wlines(c("varname,description,question",
           "sat_1,,How satisfied are you with your job?",
           "sat_2,,How satisfied are you with your pay?",
           "sat_3,Satisfaction with colleagues,How satisfied are you with your colleagues?",
           "sat_4,,"),
         file.path(d, "codebook.csv"))
})

# ── Documentation but no data file: empty_summary() with structure rows ──────
scenario("rv_onlycb", function(d) {
  wlines(c("varname,description", "age,Age in years", "sex,Sex"),
         file.path(d, "codebook.csv"))
  wlines(c("# Study", "", "No data yet."), file.path(d, "README.md"))
})
