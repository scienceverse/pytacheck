# Convert metacheck's small lookup data sets (data/<name>.rda) into the
# dependency-free files pytacheck ships under src/pytacheck/resources/data/.
#
# Usage (from the repository root, with the R reference installation):
#
#   Rscript scripts/convert_data.R [<metacheck dir>] [<output dir>] [<name> ...]
#
# Defaults: upstream/metacheck, src/pytacheck/resources/data and the data sets
# listed in `default_names` below (currently file_types, read by
# pytacheck.fileinfo.types). scripts/convert_datacheck_data.R converts the
# data_check dictionaries (scales, tasks) the same way.
#
# Each data frame becomes <name>.json.gz: gzip-compressed UTF-8 JSON in the
# format scripts/convert_databases.R uses for the reference databases,
#
#   {"format": 1, "name": "...", "date": null, "nrow": n,
#    "columns": {"col": "string" | "integer" | "number" | "boolean", ...},
#    "data": {"col": [value | null, ...], ...}}
#
# i.e. column-major values with explicit column types, rows in R's order.
# Missing values are JSON null. The output is deterministic (gzip header
# without a timestamp), so re-running at an unchanged commit changes nothing.

default_names <- c("file_types")

args <- commandArgs(trailingOnly = TRUE)
src <- if (length(args) >= 1) args[[1]] else "upstream/metacheck"
out <- if (length(args) >= 2) args[[2]] else "src/pytacheck/resources/data"
names_to_convert <- if (length(args) >= 3) args[-(1:2)] else default_names

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

for (nm in names_to_convert) convert(nm)
