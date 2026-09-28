# Binary fixtures for parity/cases/datacheck_columns_review.yaml. Run from the
# repository root:
#   Rscript tests/datacheck_columns/fixtures/review/make_review_fixtures.R
# (text fixtures are written by make_review_fixtures.py)
suppressPackageStartupMessages({
  library(openxlsx)
  library(readODS)
  library(haven)
})
d <- file.path("tests", "datacheck_columns", "fixtures", "review")

# -- SPSS: duplicate value-label texts, unsorted codes, user-defined missings
df <- data.frame(id = 1:4)
df$q1 <- labelled(c(1, 2, -99, -98),
                  labels = c(Yes = 1, No = 2, Missing = -99, Missing = -98),
                  label = "  Question one  ")
df$q2 <- labelled_spss(c(3, 1, 2, -9), labels = c(High = 3, Low = 1, Mid = 2, Refused = -9),
                       na_values = c(-9), label = "Question two")
df$q3 <- labelled(c(5, 6, 7, 8), labels = c("A long free text answer that goes on and on" = 5,
  "Another long free text answer that goes on and on" = 6, "x" = 7, "y" = 8, "z" = 9))
df$plain <- c(1.5, 2.5, 3.5, 4.5)
write_sav(df, file.path(d, "dup_labels.sav"))

# -- Excel: duplicate headers; numeric variable names; title rows above a
#    header whose cells include numbers; a headerless positional block
wb <- createWorkbook()
addWorksheet(wb, "dups")
writeData(wb, "dups", data.frame(Variable = c("a", "b", "c"), Label = c("A", "B", "C"),
                                 Label2 = c("x", "y", "z"), stringsAsFactors = FALSE))
writeData(wb, "dups", data.frame(x = "Label"), startCol = 3, startRow = 1, colNames = FALSE)
addWorksheet(wb, "numvars")
writeData(wb, "numvars", data.frame(Item = c(1, 2, 3, 10.5), Description = c("One", "Two", "Three", "Ten and a half"),
                                    Values = c("1=a; 2=b", "", "1 (low) to 7 (high)", "x = y, z = w")))
addWorksheet(wb, "titled")
writeData(wb, "titled", data.frame(
  X1 = c("Study codebook", "2024", "Field", "age", "sex"),
  X2 = c(NA, "v1", "Meaning", "Age", "Sex"),
  X3 = c(NA, NA, "Coding", "", "1 = m, 2 = f"),
  stringsAsFactors = FALSE), colNames = FALSE)
addWorksheet(wb, "positional")
writeData(wb, "positional", data.frame(
  a = c("Big five items", "bf1", "bf2", "bf3", "bf4", ""),
  b = c("", "I am the life of the party", "I feel little concern for others",
        "I am always prepared", "I get stressed out easily", "orphan wording here"),
  c = c("", "1) Disagree", "1) Disagree", "1) Disagree", "", "1) Disagree"),
  d = c("", "5) Agree", "5) Agree", "5) Agree", "5) Agree", "5) Agree"),
  stringsAsFactors = FALSE), colNames = FALSE)
saveWorkbook(wb, file.path(d, "tricky.xlsx"), overwrite = TRUE)

# -- OpenDocument: numeric columns, blank cells, a sheet without a header
write_ods(data.frame(
  variable = c("x1", "x2", "x3"),
  label = c("First", "", "Third"),
  code = c(1, 2, 3),
  stringsAsFactors = FALSE), file.path(d, "numeric.ods"), sheet = "vars")
write_ods(data.frame(
  a = c("nothing", "to", "see"),
  b = c(1, 2, 3),
  stringsAsFactors = FALSE), file.path(d, "numeric.ods"), sheet = "other", append = TRUE)
