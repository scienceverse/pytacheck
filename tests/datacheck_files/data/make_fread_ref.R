# data.table::fread() reference for tests/datacheck_files/test_delim.py. The
# inputs (<battery>_cases.json: base64 file bytes, sep, header, nrows) are small
# randomly generated delimited files; run from the repository root with the
# metacheck reference R in a UTF-8 locale:
#   LC_ALL=C.UTF-8 TZ=UTC Rscript tests/datacheck_files/data/make_fread_ref.R [battery]
# where battery is "fread" (the default: fread_cases.json -> fread_ref.json) or
# "fread_quoted" (written by make_fread_quoted_cases.py).
suppressPackageStartupMessages({library(data.table); library(jsonlite)})
source("parity/r/canonical.R")
dir <- "tests/datacheck_files/data"
battery <- commandArgs(trailingOnly = TRUE)[1]
if (is.na(battery)) battery <- "fread"
cases <- fromJSON(file.path(dir, paste0(battery, "_cases.json")), simplifyVector = FALSE)
tmp <- tempfile(fileext = ".csv")
out <- lapply(cases, function(cs) {
  writeBin(base64_dec(cs$b64), tmp)
  nr <- if (is.null(cs$nrows)) -1L else cs$nrows
  df <- tryCatch(suppressWarnings(as.data.frame(fread(tmp, sep = cs$sep, header = cs$header,
        nrows = nr, showProgress = FALSE, data.table = FALSE, check.names = FALSE,
        encoding = "UTF-8"), check.names = FALSE)), error = function(e) paste("ERR", conditionMessage(e)))
  if (is.character(df)) return(list(name = cs$name, error = df))
  cls <- unname(vapply(df, function(x) class(x)[1], ""))
  for (j in seq_along(df)) if (inherits(df[[j]], "integer64")) df[[j]] <- as.character(df[[j]])
  list(name = cs$name, value = pc_canonical(df), classes = I(cls))
})
writeLines(pc_to_json(out), file.path(dir, paste0(battery, "_ref.json")))
