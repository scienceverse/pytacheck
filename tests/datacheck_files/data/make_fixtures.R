# Regenerate the R-written fixtures used by the datacheck_files parity cases
# and tests (run from this directory with the metacheck reference R):
#   Rscript make_fixtures.R
# make_fixtures.py writes the byte-level text files, workbooks and ODS files.
suppressPackageStartupMessages({library(haven); library(readODS)})
set.seed(42)

# -- delimited text written by R -----------------------------------------------
utils::write.csv(data.frame(id = 1:5, score = c(1.5, 2, NA, 4.25, 5),
                            group = c("a", "b", "a", NA, "c"),
                            flag = c(TRUE, FALSE, NA, TRUE, TRUE)),
                 "rwrite.csv", row.names = FALSE)
utils::write.csv(data.frame(a = 1:100, b = round(rnorm(100), 3)), "hundred.csv",
                 row.names = FALSE)

# -- SPSS / Stata --------------------------------------------------------------
df <- data.frame(id = 1:6)
df$gender <- labelled(c(1, 2, 1, 9, NA, 2), c(Male = 1, Female = 2, Refused = 9),
                      label = "Respondent gender")
df$score <- c(1.5, 2.25, NA, 4, 5, 6)
attr(df$score, "label") <- "Total score"
df$likert <- labelled_spss(c(1, 5, 3, 99, 2, 4),
                           c("Strongly disagree" = 1, "Strongly agree" = 5, "No answer" = 99),
                           na_values = 99, label = "Agreement")
df$name <- c("Ann", "", "Carl", NA, "Eve", "Fé")
df$when <- as.Date("2021-03-01") + 0:5
df$stamp <- as.POSIXct("2021-03-01 12:30:00", tz = "UTC") + 3600 * 0:5
write_sav(df, "labelled.sav")
df2 <- df[c("id", "gender", "score", "name", "when")]
write_dta(df2, "labelled.dta", version = 14)

# -- R data --------------------------------------------------------------------
study_data <- data.frame(id = 1:5, score = c(1.5, 2, 3, 4, 5),
                         f = factor(c("a", "b", "a", "c", "b")),
                         s = c("x", NA, "z", "w", "v"),
                         d = as.Date("2020-01-01") + 0:4,
                         l = c(TRUE, NA, FALSE, TRUE, TRUE), stringsAsFactors = FALSE)
saveRDS(study_data, "study.rds")
saveRDS(tibble::tibble(a = 1:3, b = c("x", "y", "z")), "tibble.rds")
saveRDS(list(a = 1, b = "x"), "list.rds")
a_model <- lm(score ~ id, study_data)
other_df <- data.frame(q = 1:3)
save(study_data, a_model, other_df, file = "workspace.RData")
nums <- 1:10
save(a_model, nums, file = "noframe.RData")
zeta <- data.frame(z = c(3, 2, 1))
alpha <- data.frame(a = c("p", "q"))
save(zeta, alpha, file = "twoframes.rda")
odd <- data.frame(id = 1:3)
odd$when <- as.POSIXct(c("2021-01-01 10:00:00", NA, "2021-06-01 23:30:15"), tz = "UTC")
odd$big <- bit64::as.integer64(c("12345678901", NA, "-5"))
odd$ord <- factor(c("lo", "hi", "lo"), levels = c("lo", "hi"), ordered = TRUE)
odd$lst <- list(1:2, "a", NULL)
odd$nested <- data.frame(x = c(1, 2, 3), y = c("u", "v", "w"))
odd$lab <- haven::labelled(c(1, 2, 1), c(No = 1, Yes = 2), label = "Agreed?")
saveRDS(odd, "odd.rds")
bad <- data.frame(1:3, 4:6)
names(bad) <- c(paste0(rawToChar(as.raw(0xef)), "PeronalData_fullname"), "ok")
saveRDS(bad, "badname.rds")
lat <- data.frame(txt = c("caf\xe9", "plain"), stringsAsFactors = FALSE)
Encoding(lat$txt) <- "latin1"
saveRDS(lat, "latin1.rds")
saveRDS(study_data, "study_xz.rds", compress = "xz")
saveRDS(study_data, "study_bz2.rds", compress = "bzip2")
saveRDS(study_data, "study_ascii.rds", ascii = TRUE)
# attributes through head(): labels on unclassed/factor columns are dropped,
# vctrs (haven) columns keep theirs; the RData child has no vctrs methods
labs <- data.frame(id = 1:3, when = as.POSIXct(c("2021-01-01 10:00:00", NA, "2021-06-01"),
                                               tz = "Europe/Amsterdam"),
                   day = as.Date("2020-01-01") + 0:2)
labs$lab <- labelled(c(1, 2, 1), c(No = 1, Yes = 2), label = "Agreed?")
labs$plain <- c(1.5, 2, 3); attr(labs$plain, "label") <- "Plain label"
labs$f <- factor(c("a", "b", "a")); attr(labs$f, "label") <- "F label"
labs_model <- lm(plain ~ id, labs)
save(labs_model, labs, file = "labs_ws.RData")
saveRDS(labs, "labs.rds")

# -- OpenDocument --------------------------------------------------------------
readODS::write_ods(data.frame(id = 1:4, grp = c("a", "b", "a", "b"),
                              val = c(0.5, NA, 2.25, 3)), "written.ods")
readODS::write_fods(data.frame(id = 1:3, name = c("x", "y", "z")), "written.fods")
