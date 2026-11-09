# Convert metacheck's key rules (inst/databases/key_patterns.Rds, made by data-raw/key_patterns.R
# from the rules of the secret scanner betterleaks, MIT licence) into the dependency-free file
# pytacheck ships (src/metacheck/resources/databases/key_patterns.json.gz), which
# metacheck.keys reads.
#
# Usage (from the repository root, with the R reference installation and jsonlite):
#
#   Rscript scripts/convert_key_patterns.R [<metacheck dir>] [<output dir>]
#
# Defaults: upstream/metacheck and src/metacheck/resources/databases.
#
# The file is gzip-compressed UTF-8 JSON: {"source": {...}, "ignore": {"patterns": [...],
# "texts": [...]}, "patterns": [{"name", "description", "confidence", "regex", "group", "path",
# "entropy", "entropy_strict", "keywords", "drop_match", "need_match", "drop_contains"}, ...]}.
# A missing value is null; the lists are plain arrays.

args <- commandArgs(trailingOnly = TRUE)
src <- if (length(args) >= 1) args[[1]] else "upstream/metacheck"
out <- if (length(args) >= 2) args[[2]] else "src/metacheck/resources/databases"

x <- readRDS(file.path(src, "inst", "databases", "key_patterns.Rds"))
p <- x$patterns
na_null <- function(v) if (is.na(v)) NULL else v
recs <- lapply(seq_len(nrow(p)), function(i) {
  list(
    name = p$name[i], description = p$description[i], confidence = p$confidence[i],
    regex = p$regex[i], group = na_null(p$group[i]), path = na_null(p$path[i]),
    entropy = na_null(p$entropy[i]), entropy_strict = isTRUE(p$entropy_strict[i]),
    keywords = as.list(p$keywords[[i]]), drop_match = as.list(p$drop_match[[i]]),
    need_match = as.list(p$need_match[[i]]), drop_contains = as.list(p$drop_contains[[i]])
  )
})
obj <- list(source = x$source, ignore = x$ignore, patterns = recs)
json <- jsonlite::toJSON(obj, auto_unbox = TRUE, digits = NA, null = "null")
dir.create(out, showWarnings = FALSE, recursive = TRUE)
con <- gzfile(file.path(out, "key_patterns.json.gz"), "w")
writeLines(json, con)
close(con)
