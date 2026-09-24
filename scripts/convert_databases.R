# Convert metacheck's bundled databases (inst/databases/*.Rds) into the
# dependency-free files pytacheck ships (src/pytacheck/resources/databases/).
#
# Usage (from the repository root, with the R reference installation):
#
#   Rscript scripts/convert_databases.R [<metacheck dir>] [<output dir>]
#
# Defaults: upstream/metacheck and src/pytacheck/resources/databases.
#
# Each <name>.Rds data frame becomes <name>.json.gz: gzip-compressed UTF-8
# JSON of the form
#
#   {"format": 1, "name": "...", "date": "YYYY-MM-DD" | null, "nrow": n,
#    "columns": {"col": "string" | "integer" | "number" | "boolean", ...},
#    "data": {"col": [value | null, ...], ...}}
#
# i.e. column-major values with explicit column types (the bibr schema type
# names) and the "date" attribute metacheck uses to decide whether a user's
# refreshed copy (rw_update(), FLoRA_update()) is newer. Missing values are
# JSON null. The output is deterministic (gzip header without a timestamp),
# so re-running the script on unchanged inputs gives byte-identical files.
#
# pytacheck.db.databases reads these files; rw_update() and FLoRA_update()
# write refreshed copies in the same format.

args <- commandArgs(trailingOnly = TRUE)
src <- if (length(args) >= 1) args[[1]] else "upstream/metacheck"
out <- if (length(args) >= 2) args[[2]] else "src/pytacheck/resources/databases"

db_dir <- file.path(src, "inst", "databases")
if (!dir.exists(db_dir)) stop("No databases directory at ", db_dir)
dir.create(out, showWarnings = FALSE, recursive = TRUE)

col_type <- function(x) {
  if (is.factor(x)) return("string")
  switch(typeof(x),
    character = "string",
    integer = "integer",
    double = "number",
    logical = "boolean",
    stop("unsupported column type: ", typeof(x))
  )
}

col_values <- function(x) {
  if (is.factor(x)) x <- as.character(x)
  if (is.character(x)) x <- enc2utf8(x)
  # I() stops auto_unbox from turning a one-row column into a scalar
  I(x)
}

convert <- function(path) {
  name <- sub("\\.Rds$", "", basename(path))
  df <- readRDS(path)
  if (!is.data.frame(df)) stop(path, " is not a data frame")
  date <- attr(df, "date")
  df <- as.data.frame(df, stringsAsFactors = FALSE)

  obj <- list(
    format = 1L,
    name = name,
    date = if (is.null(date)) NULL else format(as.Date(date), "%Y-%m-%d"),
    nrow = nrow(df),
    columns = lapply(df, col_type),
    data = lapply(df, col_values)
  )

  json <- jsonlite::toJSON(
    obj,
    auto_unbox = TRUE, null = "null", na = "null",
    digits = NA, pretty = FALSE
  )

  dest <- file.path(out, paste0(name, ".json.gz"))
  con <- gzfile(dest, open = "wb", compression = 9)
  writeLines(enc2utf8(as.character(json)), con, useBytes = TRUE, sep = "")
  close(con)
  cat(sprintf("%s: %d rows x %d columns -> %s (%s bytes)\n",
              name, nrow(df), ncol(df), dest, format(file.size(dest))))
}

files <- sort(list.files(db_dir, "\\.Rds$", full.names = TRUE))
for (f in files) convert(f)
