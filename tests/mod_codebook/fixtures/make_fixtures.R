# Fixtures for the codebook_check module tests and parity cases.
#
# Run from the repository root:
#   LANG=C.UTF-8 Rscript tests/mod_codebook/fixtures/make_fixtures.R
#
# For each scenario this writes a small local repository under
# tests/mod_codebook/fixtures/repos/<scenario>/ (data files + codebooks, the
# same shapes as upstream tests/testthat/test-module-codebook_check.R) and runs
# metacheck's `data_check` module over it. data_check's output (the `table`,
# `structure` and `previews` elements codebook_check consumes) is stored as
# canonical parity JSON in tests/mod_codebook/fixtures/dc/<scenario>.json, with
# `file_location` made relative to the repository root. Both the R and the
# Python parity/test helpers (cbc_helpers.R / helpers.py) rebuild the same
# data_check output from that file, so codebook_check can be parity-tested
# independently of the (separately ported) data_check module.

suppressPackageStartupMessages({
  library(metacheck)
  library(jsonlite)
})
source("parity/r/canonical.R")
llm_use(FALSE)
options(metacheck.osf.cache = FALSE)

fx     <- "tests/mod_codebook/fixtures"
repos  <- file.path(fx, "repos")
dcdir  <- file.path(fx, "dc")
unlink(repos, recursive = TRUE)
unlink(dcdir, recursive = TRUE)
dir.create(repos, recursive = TRUE)
dir.create(dcdir, recursive = TRUE)

wcsv <- function(df, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(df, path, row.names = FALSE)
}
wlines <- function(x, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  writeLines(x, path)
}
cp <- function(from, to) {
  dir.create(dirname(to), recursive = TRUE, showWarnings = FALSE)
  file.copy(from, to, overwrite = TRUE)
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

# ── Coverage vs. label quality ───────────────────────────────────────────────
scenario("conflict", function(d) {
  wcsv(data.frame(id = 1:5, mood = c(1, 2, 3, 4, 5)), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "mood,Positive affect"), file.path(d, "codebook.csv"))
  wlines(c("varname,description", "mood,Sleep quality rating"), file.path(d, "codebook_b.csv"))
})

scenario("merge", function(d) {
  wcsv(data.frame(id = 1:5, mood = 1:5), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "mood,Positive affect"), file.path(d, "codebook.csv"))
  wlines(c("varname,description", "mood,positive affect"), file.path(d, "codebook_b.csv"))
})

scenario("unused", function(d) {
  wcsv(data.frame(id = 1:5, age = 20:24), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "age,Age in years", "income,Annual income",
           "region,Region of residence"), file.path(d, "codebook.csv"))
})

scenario("misalign", function(d) {
  wcsv(data.frame(id = 1:5, neoImagination = 1:5, neoAnxiety = 5:1,
                  neoWarmth = c(2, 3, 4, 3, 2)), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", paste0("neo", 1:8, ",NEO item ", 1:8)),
         file.path(d, "codebook.csv"))
})

scenario("partial", function(d) {
  wcsv(data.frame(id = 1:5, a = 1:5, b = 1:5, c = 1:5, dd = 1:5, ee = 1:5),
       file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "a,Item a", "b,Item b", "c,Item c", "dd,Item d",
           "ee,Item e"), file.path(d, "codebook.csv"))
})

# ── Values outside the documented range ──────────────────────────────────────
scenario("range", function(d) {
  wcsv(data.frame(id = 1:6, q1 = c(1, 2, 3, -99, 55, 4)), file.path(d, "data", "s.csv"))
  wlines(c("varname,description,values",
           "q1,Agreement,1=Strongly disagree; 2=Disagree; 3=Neutral; 4=Agree; 5=Strongly agree"),
         file.path(d, "codebook.csv"))
})

scenario("norange", function(d) {
  wcsv(data.frame(id = 1:6, q1 = c(1, 2, 3, -99, 55, 4)), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "q1,Agreement"), file.path(d, "codebook.csv"))
})

scenario("missing_codes", function(d) {
  wcsv(data.frame(id = 1:8, q1 = c(1, 2, 3, 9, 2, 12, 5, -99),
                  q2 = c(1, 2, 3, 4, 5, 1, 2, 77)),
       file.path(d, "data", "s.csv"))
  wlines(c("varname,description,values,missing",
           "q1,Agreement,1=Low; 2=Mid; 3=High; 9=Refused,9=Refused",
           "q2,Satisfaction,1=Very low; 2=Low; 3=Mid; 4=High; 5=Very high,"),
         file.path(d, "codebook.csv"))
})

# ── Traffic light ────────────────────────────────────────────────────────────
scenario("green", function(d) {
  wcsv(data.frame(age = 20:24, sex = c(1, 2, 1, 2, 1)), file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "age,Age in years", "sex,Sex of respondent"),
         file.path(d, "codebook.csv"))
})

scenario("downgrade", function(d) {
  wcsv(data.frame(q1 = c(1, 2, 3, 9)), file.path(d, "data", "s.csv"))
  wlines(c("varname,description,values", "q1,Agreement,1=Low; 2=Mid; 3=High"),
         file.path(d, "codebook.csv"))
})

scenario("nocb", function(d) {
  wcsv(data.frame(a = 1:5, b = 1:5), file.path(d, "data", "s.csv"))
})

scenario("empty", function(d) {
  invisible(NULL)   # data/ exists but holds no data file
})

scenario("scope", function(d) {
  wcsv(data.frame(id = 1:5, age = 20:24, sex = c(1, 2, 1, 2, 1)),
       file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "age,Age in years", "sex,Sex"),
         file.path(d, "codebook.csv"))
})

# ── Duplicated column names ──────────────────────────────────────────────────
scenario("dup", function(d) {
  wlines(c("id,POWER,POWER,POWER", "1,3,4,5", "2,2,1,4", "3,5,5,2"),
         file.path(d, "data", "loop.csv"))
})

# ── Behavioural tasks ────────────────────────────────────────────────────────
scenario("task", function(d) {
  set.seed(21)
  wcsv(data.frame(id = rep(1:10, each = 4), trial = rep(1:4, 10),
                  stroop_rt = round(runif(40, 400, 900)),
                  stroop_correct = sample(0:1, 40, TRUE)),
       file.path(d, "data", "trials.csv"))
}, text = c("Participants completed a colour-word Stroop task.",
            "Reaction time and accuracy were recorded on every trial."))

scenario("taskonly", function(d) {
  wcsv(data.frame(id = 1:5, age = 20:24), file.path(d, "data", "demo.csv"))
  wlines(c("varname,description", "age,Age in years", "id,Participant id"),
         file.path(d, "codebook.csv"))
}, text = "Participants completed a Stroop task before the survey.")

scenario("task_medium", function(d) {
  set.seed(22)
  wcsv(data.frame(id = rep(1:10, each = 4), trial = rep(1:4, 10),
                  iat_rt = round(runif(40, 300, 1200)),
                  flanker_rt = round(runif(40, 300, 900))),
       file.path(d, "data", "iat.csv"))
})

# ── Scales ───────────────────────────────────────────────────────────────────
scenario("orphan", function(d) {
  set.seed(31)
  wcsv(data.frame(id = 1:20,
                  IERQ_pos   = round(runif(20, 5, 35), 1),
                  IERQ_persp = round(runif(20, 5, 35), 1),
                  IERQ_sooth = round(runif(20, 5, 35), 1),
                  IERQ_model = round(runif(20, 5, 35), 1)),
       file.path(d, "data", "totals.csv"))
})

scenario("rulesonly", function(d) {
  set.seed(41)
  items <- as.data.frame(matrix(sample(1:5, 40 * 10, TRUE), nrow = 40))
  names(items) <- paste0("panas_", 1:10)
  wcsv(cbind(id = 1:40, items), file.path(d, "data", "s.csv"))
})

scenario("panas_high", function(d) {
  set.seed(42)
  items <- as.data.frame(matrix(sample(1:5, 30 * 10, TRUE), nrow = 30))
  names(items) <- paste0("PANAS", 1:10)
  items$PANAS_total <- rowSums(items)
  wcsv(cbind(id = 1:30, items), file.path(d, "data", "affect.csv"))
  wlines(c("varname,description,values",
           paste0("PANAS", 1:10, ",Affect item ", 1:10,
                  ",1=Very slightly; 2=A little; 3=Moderately; 4=Quite a bit; 5=Extremely"),
           "PANAS_total,Total affect score,"),
         file.path(d, "codebook.csv"))
}, text = c("Affect was measured with the Positive and Negative Affect Schedule (PANAS).",
            "The PANAS contains 10 items."))

scenario("scales_mixed", function(d) {
  set.seed(43)
  mk <- function(prefix, n, k = 5, nrow = 25) {
    x <- as.data.frame(matrix(sample(1:k, nrow * n, TRUE), nrow = nrow))
    names(x) <- paste0(prefix, seq_len(n))
    x
  }
  aq  <- mk("AQ", 12, 4)
  names(aq) <- sprintf("AQ%02d", 1:12)
  aq$AQ_SUM <- rowSums(aq)
  swls <- mk("swls_", 5, 7)
  pss  <- mk("pss_", 10, 5)
  bfi  <- mk("bfi_", 6, 5)
  wcsv(cbind(id = 1:25, aq, swls, pss, bfi), file.path(d, "data", "survey.csv"))
}, text = c("We administered the Autism Spectrum Quotient and the Aggression Questionnaire.",
            "Stress was measured with the Perceived Stress Scale."))

scenario("loop", function(d) {
  set.seed(44)
  cols <- list(id = 1:15)
  for (s in 1:3) for (i in 1:4)
    cols[[sprintf("POWER.PP%d_%d", s, i)]] <- sample(1:7, 15, TRUE)
  for (i in 1:5) cols[[sprintf("slider_%d", i)]] <- sample(0:100, 15, TRUE)
  cols$prob_a <- round(runif(15), 2)
  cols$prob_b <- round(runif(15), 2)
  cols$prob_c <- round(runif(15), 2)
  wcsv(as.data.frame(cols, check.names = FALSE), file.path(d, "data", "loop.csv"))
})

# ── Qualtrics export machinery / paradata ────────────────────────────────────
scenario("qualtrics", function(d) {
  set.seed(51)
  n <- 12
  df <- data.frame(
    StartDate = rep("2024-01-01 10:00:00", n),
    EndDate = rep("2024-01-01 10:20:00", n),
    Status = rep(0, n),
    Progress = rep(100, n),
    `Duration (in seconds)` = sample(300:900, n),
    Finished = rep(1, n),
    RecordedDate = rep("2024-01-01 10:20:00", n),
    ResponseId = sprintf("R_%03d", 1:n),
    check.names = FALSE)
  for (i in 1:5) df[[sprintf("TIPI_%d", i)]] <- sample(1:7, n, TRUE)
  df[["Q1_DO_order"]] <- sample(1:5, n, TRUE)
  df[["Q2_TEXT"]] <- rep("free text", n)
  df[["Q8timing_First.Click"]] <- round(runif(n, 1, 5), 2)
  df[["Q8timing_Last.Click"]] <- round(runif(n, 5, 9), 2)
  df[["Q8timing_Page.Submit"]] <- round(runif(n, 9, 12), 2)
  df[["Q8timing_Click.Count"]] <- sample(1:4, n, TRUE)
  df[["age"]] <- sample(18:60, n, TRUE)
  wcsv(df, file.path(d, "data", "survey.csv"))
  wlines(c("varname,description", "age,Age in years", "Q2_TEXT,Other please specify",
           "TIPI_1,Extraverted enthusiastic"),
         file.path(d, "codebook.csv"))
})

# ── Embedded labels (haven / JASP / jamovi) ─────────────────────────────────
scenario("haven", function(d) {
  cp("tests/datacheck_columns/fixtures/labelled.sav", file.path(d, "data", "labelled.sav"))
  cp("tests/datacheck_columns/fixtures/labelled.dta", file.path(d, "data", "labelled.dta"))
})

scenario("jasp_omv", function(d) {
  cp("upstream/metacheck/tests/testthat/fixtures/formats/sample.jasp",
     file.path(d, "data", "sample.jasp"))
  cp("upstream/metacheck/tests/testthat/fixtures/formats/sample.omv",
     file.path(d, "data", "sample.omv"))
})

# ── Unstructured README (rules ignore it; the LLM tier parses it) ────────────
scenario("readme_text", function(d) {
  wcsv(data.frame(id = 1:5, mood = 1:5, sleep_hours = c(6, 7, 8, 5, 6),
                  grp = c(1, 2, 1, 2, 1)),
       file.path(d, "data", "s.csv"))
  wlines(c("# Study data",
           "",
           "This repository holds the data for our mood study. The variables",
           "are described below in prose rather than as a table.",
           "",
           "mood - Positive affect rated from 1 to 5",
           "sleep_hours - Hours slept the previous night",
           "grp - Experimental condition"),
         file.path(d, "README.md"))
})

# ── Several data files (one tab each) + an unmatched codebook ────────────────
scenario("multifile", function(d) {
  wcsv(data.frame(pid = 1:4, age = c(20, 30, 40, 50), sex = c(1, 2, 1, 2)),
       file.path(d, "data", "demographics.csv"))
  wcsv(data.frame(pid = 1:4, score = c(10, 12, 9, 14), rt_mean = c(512.5, 498.1, 530.2, 505.9)),
       file.path(d, "data", "results.csv"))
  wlines(c("varname,description", "pid,Participant number", "age,Age in years",
           "sex,Sex", "score,Total score", "education,Years of education"),
         file.path(d, "codebook.csv"))
})

# ── Paper-list pieces (combined by the helpers into one data_check output) ───
scenario("pl_a", function(d) {
  wcsv(data.frame(id = 1:5, age = 20:24), file.path(d, "data", "a.csv"))
  wlines(c("varname,description", "age,Age in years", "id,Participant id",
           "income,Annual income"), file.path(d, "codebook_a.csv"))
})

scenario("pl_b", function(d) {
  wcsv(data.frame(id = 1:5, mood = 1:5, anx = 5:1), file.path(d, "data", "b.csv"))
  wlines(c("varname,description", "mood,Positive affect", "id,Participant id"),
         file.path(d, "codebook_b.csv"))
})

# ── LLM tiers: fuzzy column matching, long unstructured codebooks ─────────────
scenario("fuzzy", function(d) {
  wcsv(data.frame(id = 1:5, age_years = 20:24, gender_code = c(1, 2, 1, 2, 1),
                  income_k = c(20, 30, 40, 50, 60), mood = 1:5),
       file.path(d, "data", "s.csv"))
  wlines(c("varname,description", "participant_id,Participant number", "age,Age in years",
           "gender,Gender of the participant", "income_thousands,Income in thousands",
           "mood,Positive affect"),
         file.path(d, "codebook.csv"))
})

scenario("readme_long", function(d) {
  wcsv(data.frame(id = 1:5, v1 = 1:5, v2 = 5:1, extra = 1:5), file.path(d, "data", "s.csv"))
  wlines(c("# Variables", "",
           paste0("Background line ", 1:60, " describing the study in prose."),
           "v1 - First rating", "v2 - Second rating",
           paste0("More notes ", 1:60, " about the procedure.")),
         file.path(d, "README.txt"))
})
