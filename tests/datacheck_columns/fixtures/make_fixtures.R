# Regenerate the codebook fixtures used by parity/cases/datacheck_columns.yaml
# and tests/datacheck_columns/. Run from the repository root:
#   Rscript tests/datacheck_columns/fixtures/make_fixtures.R
# (text fixtures -- csv/json/md/qsf/rtf -- are written by make_fixtures.py)
suppressPackageStartupMessages({
  library(openxlsx)
  library(readODS)
  library(haven)
  library(officer)
})
d <- file.path("tests", "datacheck_columns", "fixtures")

# -- Excel: three sheets (named header, title rows above the header, positional)
wb <- createWorkbook()
addWorksheet(wb, "Codebook")
writeData(wb, "Codebook", data.frame(
  Variable = c("age", "sex", "cond", "score"),
  Label = c("Age in years", "Participant sex", "Condition", "Total score"),
  Values = c("", "1 = Male; 2 = Female; -99 = Refused", "0 = control, 1 = treatment", ""),
  Question = c("How old are you?", "What is your sex?", "", ""),
  stringsAsFactors = FALSE))
addWorksheet(wb, "Notes")
writeData(wb, "Notes", data.frame(
  X1 = c("Codebook for Studies 1-4", "", "Item", "rt", "acc"),
  X2 = c(NA, NA, "Description", "Reaction time (ms)", "Accuracy"),
  stringsAsFactors = FALSE), colNames = FALSE)
addWorksheet(wb, "IPIP")
writeData(wb, "IPIP", data.frame(
  a = c("IPIP-NEO items", "neo1", "neo2", "neo3", "neo4"),
  b = c("", "Worry about things", "Make friends easily", "Have a vivid imagination", "Trust others"),
  c = c("", "1 - Very inaccurate", "1 - Very inaccurate", "1 - Very inaccurate", "1 - Very inaccurate"),
  d = c("", "5 - Very accurate", "5 - Very accurate", "5 - Very accurate", "5 - Very accurate"),
  stringsAsFactors = FALSE), colNames = FALSE)
saveWorkbook(wb, file.path(d, "codebook.xlsx"), overwrite = TRUE)

# -- OpenDocument: two sheets
write_ods(data.frame(
  name = c("id", "rt", "rt"),
  description = c("Participant ID", "Reaction time", ""),
  stringsAsFactors = FALSE), file.path(d, "codebook.ods"), sheet = "vars")
write_ods(data.frame(
  x = c("neo1", "neo2", "neo3"),
  y = c("Worry about things", "Make friends easily", "Trust other people"),
  stringsAsFactors = FALSE), file.path(d, "codebook.ods"), sheet = "items", append = TRUE)

# -- SPSS / Stata with embedded labels
df <- data.frame(id = 1:6)
df$sex <- labelled(c(1, 2, -99, 1, 2, -99),
                   labels = c(Male = 1, Female = 2, Refused = -99), label = "Sex")
df$age <- structure(c(23, 45, 31, 29, 55, 19), label = "Age in years")
df$q1 <- labelled(c(1, 2, 3, 4, 5, 1),
                  labels = c("Strongly disagree" = 1, "Strongly agree" = 5, "Not applicable" = 9))
write_sav(df, file.path(d, "labelled.sav"))
write_dta(df, file.path(d, "labelled.dta"))

# -- Word document (paragraphs, a tab and a break, a table)
doc <- read_docx()
doc <- body_add_par(doc, "Codebook for study 1", style = "heading 1")
doc <- body_add_par(doc, "age: participant age in years")
doc <- body_add_par(doc, "")
doc <- body_add_fpar(doc, fpar(ftext("sex"), run_tab(), ftext("1 = male; 2 = female"),
                               run_linebreak(), ftext("second line")))
doc <- body_add_table(doc, data.frame(variable = c("id", "score"),
                                      label = c("Participant ID", "Total score")))
doc <- body_add_par(doc, "cond = condition & group <x>")
print(doc, target = file.path(d, "codebook.docx"))

# -- PDF with definition lines (and one without)
pdf(file.path(d, "codebook.pdf"), width = 8.5, height = 11)
plot.new()
par(mar = c(0, 0, 0, 0))
defs <- c("Codebook", "age:  Age of the participant in years",
          "sex:  Participant sex (1 = male, 2 = female)",
          "cond:  Experimental condition", "rt_mean:  Mean reaction time in ms",
          "acc:  Proportion of correct responses", "block:  Block number of the trial")
for (i in seq_along(defs)) text(0.05, 0.95 - i * 0.05, defs[i], adj = 0, family = "mono")
dev.off()
pdf(file.path(d, "prose.pdf"), width = 8.5, height = 11)
plot.new()
text(0.5, 0.5, "This PDF is a short narrative with no variable definitions.")
dev.off()
