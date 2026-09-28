# Build the fixture repositories of the data_check parity cases and tests
# (tests/mod_data_check/fixtures/repos/<scenario>/). Run once from the
# repository root; the files are committed, so neither the parity harness nor
# pytest needs R to rebuild them:
#
#   LANG=C.UTF-8 Rscript tests/mod_data_check/fixtures/make_fixtures.R
suppressPackageStartupMessages({
  library(metacheck)
})
root <- "tests/mod_data_check/fixtures/repos"
unlink(root, recursive = TRUE)
dir.create(root, recursive = TRUE)
mk <- function(...) {
  p <- file.path(root, ...)
  dir.create(dirname(p), recursive = TRUE, showWarnings = FALSE)
  p
}
wcsv <- function(df, ...) utils::write.csv(df, mk(...), row.names = FALSE)
# an absolute path (zip::zip() and tar() change the working directory)
absp <- function(p) file.path(normalizePath(dirname(p)), basename(p))
set.seed(20260925)

# -- basic: clean data, a readme, code and a stimulus ------------------------------
wcsv(data.frame(
  id = 1:20,
  age = c(23, 35, 41, 29, 30, 22, 19, 55, 61, 44, 38, 27, 33, 25, 48, 52, 36, 40, 21, 26),
  gender = rep(c("Male", "Female"), 10),
  score = round(seq(0.5, 10, length.out = 20), 2)), "basic", "data", "study.csv")
writeLines(c("# Study materials", "", "Data and code for the study."), mk("basic", "README.md"))
writeLines(c("d <- read.csv('data/study.csv')", "summary(lm(score ~ age, d))"),
           mk("basic", "analysis.R"))
writeBin(as.raw(c(0x89, 0x50, 0x4e, 0x47)), mk("basic", "materials", "stimulus.png"))

# -- flags: most columns raise a data-quality finding (red) --------------------------
flags <- data.frame(
  id = 1:12,
  `Gender` = c("Male ", "male", "Female", "female", "Male", "Female",
               "Male", "female", "Female", "Male", "male", "Female"),
  score_txt = c("12", "13", "n/a", "15", "16", "17", "18", "19", "20", "21", "22", "23"),
  empty_col = rep(NA, 12),
  `filter_$` = c(1, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1),
  email = sprintf("person%02d@example.org", 1:12),
  latitude = c(52.1, 52.2, 51.9, 52.0, 52.3, 51.8, 52.4, 52.1, 52.0, 51.7, 52.2, 52.3),
  longitude = c(4.3, 4.4, 4.2, 4.5, 4.1, 4.6, 4.3, 4.4, 4.2, 4.5, 4.3, 4.4),
  comments = c(
    "I found the task quite hard because the letters moved too quickly for me",
    "The second block was easier than the first one and I enjoyed it a lot more",
    "My neighbour Jan helped me set up the laptop before I started the session",
    "I was a bit tired today since I had to work late at the hospital yesterday",
    "Everything went fine, although the room was rather noisy during block three",
    "I think I pressed the wrong key a few times near the end of the experiment",
    "The instructions were clear and the break between the blocks was welcome",
    "I did not understand what to do in the practice trials at the very start",
    "My daughter was playing in the next room so I was distracted at times today",
    "Nothing special to report, it all worked as expected on my own computer",
    "I would have liked more practice trials before the real task started here",
    "The sounds were too loud at first but I turned the volume down afterwards"),
  `a b` = rnorm(12), a_b = rnorm(12),
  rt = round(rlnorm(12, 6, 0.3)),
  check.names = FALSE)
wcsv(flags, "flags", "data", "messy.csv")

# -- yellow: one flagged column in ten ------------------------------------------------
yl <- as.data.frame(lapply(1:9, function(i) round(rnorm(25, 50, 10), 1)))
names(yl) <- paste0("m", 1:9)
yl$condition <- rep(c("control", "control ", "treatment", "treatment", "control"), 5)
wcsv(yl, "yellow", "clean.csv")
# a codebook is documentation (doc_role "codebook"), never column-extracted
wcsv(data.frame(variable = c(paste0("m", 1:9), "condition"),
                label = c(paste("Measure", 1:9), "Experimental condition")),
     "yellow", "codebook.csv")

# -- nodata: no tabular data at all ---------------------------------------------------
writeLines("# Read me", mk("nodata", "README.md"))
writeLines("x <- 1", mk("nodata", "code", "analysis.R"))
writeLines("%PDF-1.4 fake", mk("nodata", "paper.pdf"))

# -- qualtrics: a Qualtrics export (3 header rows) --------------------------------------
q <- function(...) paste0('"', c(...), '"', collapse = ",")
meta <- c("StartDate", "EndDate", "Status", "IPAddress", "Progress",
          "Duration (in seconds)", "Finished", "RecordedDate", "ResponseId",
          "LocationLatitude", "LocationLongitude", "Q1_DO_Q2")
items <- paste0("q_", 1:6)
hdr2 <- c("Start Date", "End Date", "Response Type", "IP Address", "Progress",
          "Duration (in seconds)", "Finished", "Recorded Date", "Response ID",
          "Location Latitude", "Location Longitude", "Q1 - Display Order",
          paste("How much do you agree with statement", 1:6))
hdr3 <- sprintf('{""ImportId"":""%s""}', c("startDate", "endDate", "status", "ipAddress",
          "progress", "duration", "finished", "recordedDate", "_recordId",
          "locationLatitude", "locationLongitude", "QID1_DO", paste0("QID2_", 1:6)))
body <- vapply(1:40, function(i) {
  status <- if (i %in% c(3, 17)) 1 else 0
  fin <- if (i %in% c(8, 25, 33)) 0 else 1
  prog <- if (fin == 0) 60 else 100
  dur <- c(35, 48, 300:337)[i]
  start <- sprintf("2021-05-%02d 10:%02d:00", (i %% 28) + 1, i %% 60)
  q(start, start, status, sprintf("10.0.0.%d", i), prog, dur, fin, start,
    sprintf("R_%010d", i * 7919), 52 + i / 100, 4 + i / 100, "Q2|Q1",
    sample(1:5, 6, TRUE))
}, character(1))
writeLines(c(q(meta, items), q(hdr2), paste0('"', hdr3, '"', collapse = ","), body),
           mk("qualtrics", "data", "survey.csv"))

# -- careless: a straightliner and an alternating responder -------------------------------
make_careless_survey <- function(prefix = "panas", n_items = 10, n_ok = 50,
                                 levels = 2:4, seed = 1) {
  set.seed(seed)
  items <- as.data.frame(matrix(sample(levels, n_ok * n_items, replace = TRUE),
                                nrow = n_ok))
  names(items) <- paste0(prefix, "_", seq_len(n_items))
  straight <- as.data.frame(matrix(rep(median(levels), n_items), nrow = 1))
  alternating <- as.data.frame(matrix(rep(range(levels), length.out = n_items),
                                      nrow = 1))
  names(straight) <- names(alternating) <- names(items)
  items <- rbind(items, straight, alternating)
  cbind(participant_id = seq_len(n_ok + 2), items)
}
wcsv(make_careless_survey(), "careless", "data", "survey.csv")

# nobody straightlines
set.seed(7)
items <- as.data.frame(matrix(sample(1:5, 40 * 10, replace = TRUE), nrow = 40))
names(items) <- paste0("panas_", 1:10)
items$panas_1 <- rep(c(1L, 5L), length.out = 40)
items$panas_5 <- rep(c(2L, 4L), length.out = 40)
wcsv(cbind(participant_id = 1:40, items), "careless_clean", "survey.csv")

# two scales; the second respondent straightlines only the short (6-item) one
s1 <- make_careless_survey("panas", n_items = 8, n_ok = 40, levels = 1:5, seed = 2)
s2 <- make_careless_survey("rse", n_items = 6, n_ok = 40, levels = 1:5, seed = 3)
wide <- cbind(s1, s2[, -1, drop = FALSE])
wide[5, paste0("rse_", 1:6)] <- 4L
wcsv(wide, "careless_two", "data", "survey.csv")
# a 3-item block is never screened
wcsv(data.frame(participant_id = 1:40, q_1 = rep(3L, 40), q_2 = rep(3L, 40),
                q_3 = rep(3L, 40)), "careless_short", "survey.csv")

# -- spreadsheets ----------------------------------------------------------------------
wb <- openxlsx::createWorkbook()
openxlsx::addWorksheet(wb, "Data")
openxlsx::writeData(wb, "Data", data.frame(id = 1:4, grp = c("a", "b", "a", "b"),
                                           empty = rep(NA, 4), val = c(10, 20, 30, 40)))
openxlsx::addStyle(wb, "Data", openxlsx::createStyle(fgFill = "#FFCC00"), rows = 2, cols = 4)
openxlsx::addStyle(wb, "Data", openxlsx::createStyle(fgFill = "#00CCFF"), rows = 3, cols = 4)
openxlsx::mergeCells(wb, "Data", cols = 1:2, rows = 7)
openxlsx::saveWorkbook(wb, mk("spreadsheets", "data", "messy.xlsx"), overwrite = TRUE)
openxlsx::write.xlsx(data.frame(id = 1:3, score = c(1.1, 2.2, 3.3)),
                     mk("spreadsheets", "data", "clean.xlsx"))
# a banner row above the header
wb <- openxlsx::createWorkbook()
openxlsx::addWorksheet(wb, "Sheet1")
openxlsx::writeData(wb, "Sheet1", "Experiment 1 export", startRow = 1, startCol = 1)
openxlsx::writeData(wb, "Sheet1", data.frame(subject = 1:8, rt = seq(400, 750, 50),
                                             acc = c(1, 0, 1, 1, 0, 1, 1, 1)),
                    startRow = 2)
openxlsx::saveWorkbook(wb, mk("spreadsheets", "data", "offset.xlsx"), overwrite = TRUE)
# a README sheet in front of the data sheet (issue #379)
wb <- openxlsx::createWorkbook()
openxlsx::addWorksheet(wb, "README")
openxlsx::writeData(wb, "README", data.frame(
  notes = c("This workbook holds the data of the reported study, collected online in spring.",
            "Each participant completed the questionnaire once, in about ten minutes in total.",
            "Scores are the mean of the five items, higher scores mean more agreement overall.",
            "Missing answers are left blank rather than being coded with a special value here.",
            "Contact the corresponding author for questions about any part of these data sets.")))
openxlsx::addWorksheet(wb, "data")
openxlsx::writeData(wb, "data", data.frame(pid = 1:6, score = c(3.2, 4.1, 2.8, 3.9, 4.4, 3.0),
                                           cond = rep(c("a", "b"), 3)))
openxlsx::saveWorkbook(wb, mk("spreadsheets", "data", "twosheets.xlsx"), overwrite = TRUE)
# a coding worksheet: mostly free text, mostly empty
wb <- openxlsx::createWorkbook()
openxlsx::addWorksheet(wb, "coding")
openxlsx::writeData(wb, "coding", data.frame(
  segment = c("Participant describes the morning routine in detail and mentions coffee",
              NA, "Talks about the commute and the traffic jams on the ring road", NA, NA,
              "Explains why the weekend felt far too short this time around"),
  code = c("routine", NA, NA, NA, NA, "leisure"),
  memo = c(NA, NA, "Check with second coder whether this is a routine or a complaint",
           NA, NA, NA)))
openxlsx::saveWorkbook(wb, mk("spreadsheets", "data", "coding.xlsx"), overwrite = TRUE)
file.copy(readxl::readxl_example("datasets.xls"), mk("spreadsheets", "data", "legacy.xls"))
writeLines("this is not a workbook", mk("spreadsheets", "data", "broken.xlsx"))

# OpenDocument: a flat .fods and the same content zipped as .ods, with a coloured
# cell style, a merged range, an empty column and a repeated empty padding run
ods_body <- '<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" office:version="1.2">
<office:automatic-styles>
<style:style style:name="ce1" style:family="table-cell"><style:table-cell-properties fo:background-color="#ffcc00"/></style:style>
<style:style style:name="ce2" style:family="table-cell"><style:table-cell-properties fo:background-color="transparent"/></style:style>
</office:automatic-styles>
<office:body><office:spreadsheet>
<table:table table:name="Results">
<table:table-row><table:table-cell office:value-type="string"><text:p>id</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>group</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>note</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>score</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell office:value-type="float" office:value="1"><text:p>1</text:p></table:table-cell><table:table-cell table:style-name="ce1" office:value-type="string"><text:p>a</text:p></table:table-cell><table:table-cell/><table:table-cell office:value-type="float" office:value="10"><text:p>10</text:p></table:table-cell></table:table-row>
<table:table-row><table:table-cell office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell><table:table-cell table:style-name="ce1" office:value-type="string"><text:p>b</text:p></table:table-cell><table:table-cell table:style-name="ce2"/><table:table-cell office:value-type="float" office:value="20"><text:p>20</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="2"><table:table-cell table:number-columns-repeated="4"/></table:table-row>
<table:table-row><table:table-cell table:number-columns-spanned="2" office:value-type="string"><text:p>total</text:p></table:table-cell><table:covered-table-cell/><table:table-cell/><table:table-cell office:value-type="float" office:value="30"><text:p>30</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="1048570"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
</table:table>
</office:spreadsheet></office:body></office:document-content>'
fods <- sub('<office:document-content ', '<office:document ', ods_body, fixed = TRUE)
fods <- sub('</office:document-content>', '</office:document>', fods, fixed = TRUE)
writeLines(fods, mk("spreadsheets", "data", "messy.fods"))
tmp <- tempfile("ods_"); dir.create(file.path(tmp, "META-INF"), recursive = TRUE)
writeLines("application/vnd.oasis.opendocument.spreadsheet", file.path(tmp, "mimetype"), sep = "")
writeLines(ods_body, file.path(tmp, "content.xml"))
writeLines('<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>', file.path(tmp, "META-INF", "manifest.xml"))
ods_path <- absp(mk("spreadsheets", "data", "messy.ods"))
zip::zip(ods_path, c("mimetype", "content.xml", "META-INF/manifest.xml"), root = tmp)
readODS::write_ods(data.frame(id = 1:3, value = c(0.5, 1.5, 2.5)),
                   mk("spreadsheets", "data", "clean.ods"))

# -- archives: a zip with data + stimuli, a zip of only stimuli, a tarball, a .csv.gz ------
tmp <- tempfile("zip_"); dir.create(file.path(tmp, "inner"), recursive = TRUE)
dir.create(file.path(tmp, "stimuli"))
utils::write.csv(data.frame(subject = 1:6, rt = c(512, 498, 610, 577, 530, 489)),
                 file.path(tmp, "inner", "trials.csv"), row.names = FALSE)
writeLines("Codebook: subject = participant number; rt = reaction time in ms",
           file.path(tmp, "inner", "codebook.txt"))
writeBin(as.raw(c(0x89, 0x50, 0x4e, 0x47)), file.path(tmp, "stimuli", "face01.png"))
zip::zip(absp(mk("archives", "bundle.zip")),
         c("inner/trials.csv", "inner/codebook.txt", "stimuli/face01.png"), root = tmp)
zip::zip(absp(mk("archives", "stimuli.zip")),
         "stimuli/face01.png", root = tmp)
tmp2 <- tempfile("tar_"); dir.create(tmp2)
utils::write.csv(data.frame(block = 1:4, acc = c(0.9, 0.85, 0.95, 0.8)),
                 file.path(tmp2, "summary.csv"), row.names = FALSE)
tar_path <- absp(mk("archives", "results.tar.gz"))
old <- setwd(tmp2)
utils::tar(tar_path, "summary.csv", compression = "gzip", tar = "internal")
setwd(old)
con <- gzfile(mk("archives", "extra.csv.gz"), "wb")
writeLines(c("item,value", "a,1", "b,2", "c,3"), con)
close(con)
writeLines("# Archives", mk("archives", "README.md"))

# -- rdata: a model-only workspace and a workspace holding a data frame ----------------------
a_model <- lm(mpg ~ cyl, mtcars)
save(a_model, file = mk("rdata", "data", "analysis_workspace.RData"))
study_data <- data.frame(id = 1:5, score = c(1.5, 2.5, 3.5, 4.5, 5.5))
save(study_data, file = mk("rdata", "data", "clean_data.RData"))
wcsv(data.frame(id = 1:5, x = c(0.1, 0.4, 0.2, 0.8, 0.5)), "rdata", "data", "study.csv")

# -- trial-level: jsPsych exports (one file per participant) ------------------------------
for (s in 1:2) {
  wcsv(data.frame(rt = c(812, 640, 702), stimulus = c("<p>A</p>", "<p>B</p>", "<p>C</p>"),
                  response = c("f", "j", "f"), trial_type = "html-keyboard-response",
                  trial_index = 0:2, time_elapsed = c(900, 1600, 2400),
                  internal_node_id = c("0.0-0.0", "0.0-1.0", "0.0-2.0")),
       "trial", "data", sprintf("sub-%02d.csv", s))
}
wcsv(data.frame(id = 1:4, age = c(21, 34, 28, 45)), "trial", "data", "demographics.csv")

# -- manifest: a file listing masquerading as data ------------------------------------------
wcsv(data.frame(file = c("data.csv", "analysis.R", "paper.pdf"),
                description = c("the data", "the analysis", "the preprint")),
     "manifest", "files.csv")
wcsv(data.frame(id = 1:3, y = c(2.5, 3.5, 1.5)), "manifest", "data.csv")
writeLines("y <- 1", mk("manifest", "analysis.R"))
writeLines("%PDF-1.4 fake", mk("manifest", "paper.pdf"))

# -- txt: a .txt of data (reclassified from content), a readme .txt and a fake .out ----------
writeLines(c("subject\tblock\trt\tacc", sprintf("%d\t%d\t%d\t%d", rep(1:5, each = 2),
             rep(1:2, 5), 400 + (1:10) * 13, rep(c(1, 0), 5))), mk("txt", "session1.txt"))
writeLines(c("README", "", "The data are in session1.txt."), mk("txt", "README.txt"))
writeLines(c("some tool log", "nothing here"), mk("txt", "model.out"))

# -- groups: two studies, a collection-level readme and licence -------------------------------
wcsv(data.frame(id = 1:5, y = c(1.2, 2.3, 3.1, 4.8, 5.5)), "groups", "study1", "data.csv")
wcsv(data.frame(id = 1:5, y = c(2.2, 1.3, 4.1, 3.8, 6.5)), "groups", "study2", "data.csv")
writeLines("d <- read.csv('data.csv')", mk("groups", "study1", "analysis.R"))
writeLines("# Two studies", mk("groups", "README.md"))
writeLines("MIT License", mk("groups", "LICENSE"))

# -- encoding: latin-1 bytes in a csv ------------------------------------------------------------
con <- file(mk("encoding", "latin1.csv"), "wb")
writeBin(charToRaw("id,city,score\n1,Z\xfcrich,3\n2,Malm\xf6,4\n3,Paris,5\n4,K\xf6ln,2\n"), con)
close(con)

# -- nontabular: a coding worksheet saved as csv ----------------------------------------------------
wcsv(data.frame(
  excerpt = c("The participant says she mostly works from home and prefers quiet mornings",
              "He reports that the new schedule gives him more time with his children",
              "They mention feeling isolated since the office closed during the pandemic",
              "She describes the commute as the most stressful part of her working day"),
  theme = c("home working", "family time", "isolation", "commute stress"),
  coder_note = c("clear example", "check with second coder", "borderline", "clear example")),
  "nontabular", "coding.csv")
wcsv(data.frame(id = 1:4, y = c(1, 2, 3, 4)), "nontabular", "scores.csv")
# unreadable data files: a text file saved as .sav and an empty .csv
writeLines("this is not an SPSS file", mk("nontabular", "broken.sav"))
file.create(mk("nontabular", "empty.csv"))

# -- llm: an unknown file type and ambiguous columns -------------------------------------------
wcsv(data.frame(pp = 1:12, q3 = c(512, 498, 610, 577, 530, 489, 505, 620, 480, 555, 590, 470),
                v7 = rep(c(1, 2, 3), 4), label = rep(c("x1", "y2", "z3", "w4"), 3)),
     "llm", "data", "trials.csv")
writeLines("binary-ish", mk("llm", "notes.xyz"))
writeLines("# Readme", mk("llm", "README.md"))

# -- schema: same-header files (one LLM question per header signature) ----------------------
for (s in 1:3) {
  wcsv(data.frame(trial = 1:6, q3 = c(512, 498, 610, 577, 530, 489) + s,
                  v7 = c(1, 2, 3, 1, 2, 3)), "schema", sprintf("pp%d.csv", s))
}
wcsv(data.frame(pid = 1:3, q3 = c(20, 30, 40), note = c("a", "b", "c")), "schema", "summary.csv")

cat("fixtures written to", root, "\n")
