# R-generated fixtures for the datacheck_files_review parity cases (haven and
# readRDS inputs). Run from this directory with the metacheck reference R:
#   LANG=C.UTF-8 TZ=UTC Rscript make_review_fixtures.R
suppressPackageStartupMessages(library(haven))
dir.create("review", showWarnings = FALSE)

# haven decides a column's type from the file's variable type and format,
# never from the values: integers with missing values stay double, all-NA
# date/time columns keep their class, zero-row files keep their types.
stata <- data.frame(
  i = c(1L, NA, 3L),
  s = c("a", NA, "c"),
  d = as.Date(c("2020-01-01", NA, NA)),
  t = as.POSIXct(c(NA, NA, NA), tz = "UTC") + 0,
  l = labelled(c(1L, NA, 2L), c(No = 1L, Yes = 2L), label = "Agree?"),
  stringsAsFactors = FALSE)
write_dta(stata, "review/stata_int_na.dta")

zero <- data.frame(num = numeric(0), chr = character(0), day = as.Date(character(0)),
                   when = as.POSIXct(character(0), tz = "UTC"), stringsAsFactors = FALSE)
write_sav(zero, "review/zero_rows.sav")
write_dta(zero, "review/zero_rows.dta")

spss <- data.frame(
  when = as.POSIXct(c(NA, NA), tz = "UTC") + 0,
  day = as.Date(c(NA, NA)),
  dur = hms::hms(c(3600, NA)),
  lab = labelled(c(1, 9), c(Low = 1, Missing = 9)),
  stringsAsFactors = FALSE)
write_sav(spss, "review/all_na_dates.sav")

# .utf8_repair_df(): a list cell holding a POSIXct or complex vector is pasted
# through as.character() (unlist() keeps an atomic vector's class)
cells <- data.frame(id = 1:3)
cells$l <- I(list(as.POSIXct(c("2020-01-01 10:00:00", "2020-01-01 00:00:00"), tz = "UTC"),
                  as.POSIXct("2020-01-01 10:00:00.5", tz = "UTC"), c(1i, 2 - 0.5i)))
saveRDS(cells, "review/list_cells.rds")
