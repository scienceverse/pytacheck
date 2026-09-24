# Convert metacheck's data_check dictionaries (data/scales.rda, data/tasks.rda)
# into the dependency-free files pytacheck ships
# (src/pytacheck/resources/data/{scales,tasks}.json.gz).
#
# Usage (from the repository root, with the R reference installation):
#
#   Rscript scripts/convert_datacheck_data.R [<metacheck dir>] [<output dir>]
#
# Defaults: upstream/metacheck and src/pytacheck/resources/data.
#
# Each data frame becomes <name>.json.gz: gzip-compressed UTF-8 JSON in the
# format scripts/convert_databases.R uses for the reference databases,
#
#   {"format": 1, "name": "...", "date": null, "nrow": n,
#    "columns": {"col": "string" | "integer" | "number" | "boolean", ...},
#    "data": {"col": [value | null, ...], ...}}
#
# i.e. column-major values with explicit column types. Missing values are
# JSON null. The output is deterministic (gzip header without a timestamp).
#
# pytacheck.datacheck.scales.scales() and pytacheck.datacheck.tasks.tasks()
# read these files.

args <- commandArgs(trailingOnly = TRUE)
src <- if (length(args) >= 1) args[[1]] else "upstream/metacheck"
out <- if (length(args) >= 2) args[[2]] else "src/pytacheck/resources/data"

data_dir <- file.path(src, "data")
if (!dir.exists(data_dir)) stop("No data directory at ", data_dir)
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

convert <- function(name) {
  path <- file.path(data_dir, paste0(name, ".rda"))
  env <- new.env()
  load(path, envir = env)
  df <- get(name, envir = env)
  if (!is.data.frame(df)) stop(path, " does not hold a data frame called ", name)
  df <- as.data.frame(df, stringsAsFactors = FALSE)

  obj <- list(
    format = 1L,
    name = name,
    date = NULL,
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

for (nm in c("scales", "tasks")) convert(nm)
