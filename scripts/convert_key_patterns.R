# Convert metacheck's key rules (inst/databases/key_patterns.Rds: the rules of
# the secret scanner betterleaks, MIT licence) into the dependency-free file
# pytacheck ships (src/metacheck/resources/databases/key_patterns.json.gz), and
# copy the licence text beside it (key_patterns.COPYRIGHT).
#
# Usage (from the repository root, with the R reference installation):
#
#   Rscript scripts/convert_key_patterns.R [<metacheck dir>] [<output dir>]
#
# Defaults: upstream/metacheck and src/metacheck/resources/databases. Run it
# again after each sync that changes inst/databases/key_patterns.Rds.
#
# The JSON is
#
#   {"format": 1, "source": {url, file, commit, licence, read},
#    "ignore": {"patterns": [...], "texts": [...]},
#    "patterns": [{"name", "description", "confidence", "regex", "group" (int
#      or null), "path" (regex or null), "entropy" (number or null),
#      "entropy_strict", "keywords": [...], "drop_match": [...],
#      "need_match": [...], "drop_contains": [...]}, ...]}
#
# The regexes are Go (RE2) patterns that R reads with perl = TRUE; pytacheck
# reads them with the `regex` module (metacheck.keys). The output is
# deterministic (gzip header without a timestamp).

args <- commandArgs(trailingOnly = TRUE)
src <- if (length(args) >= 1) args[[1]] else "upstream/metacheck"
out <- if (length(args) >= 2) args[[2]] else "src/metacheck/resources/databases"

rds <- file.path(src, "inst/databases/key_patterns.Rds")
if (!file.exists(rds)) stop("No key patterns at ", rds)
x <- readRDS(rds)
dir.create(out, showWarnings = FALSE, recursive = TRUE)

p <- x$patterns
strs <- function(v) I(enc2utf8(as.character(unlist(v))))
rows <- lapply(seq_len(nrow(p)), function(i) {
  list(
    name = enc2utf8(p$name[[i]]),
    description = if (is.na(p$description[[i]])) NULL else enc2utf8(p$description[[i]]),
    confidence = p$confidence[[i]],
    regex = enc2utf8(p$regex[[i]]),
    group = if (is.na(p$group[[i]])) NULL else p$group[[i]],
    path = if (is.na(p$path[[i]])) NULL else enc2utf8(p$path[[i]]),
    entropy = if (is.na(p$entropy[[i]])) NULL else p$entropy[[i]],
    entropy_strict = if (is.na(p$entropy_strict[[i]])) NULL else p$entropy_strict[[i]],
    keywords = strs(p$keywords[[i]]),
    drop_match = strs(p$drop_match[[i]]),
    need_match = strs(p$need_match[[i]]),
    drop_contains = strs(p$drop_contains[[i]])
  )
})
obj <- list(
  format = 1L,
  source = lapply(x$source, enc2utf8),
  ignore = list(patterns = strs(x$ignore$patterns), texts = strs(x$ignore$texts)),
  patterns = rows
)
json <- jsonlite::toJSON(obj, auto_unbox = TRUE, null = "null", digits = NA, pretty = FALSE)
target <- file.path(out, "key_patterns.json.gz")
con <- gzfile(target, "wb", compression = 9)
writeBin(charToRaw(enc2utf8(as.character(json))), con)
close(con)
# R's gzfile writes no timestamp, but zero the MTIME field to be sure
b <- readBin(target, "raw", file.size(target)); b[5:8] <- as.raw(0); writeBin(b, target)

# the licence text: the part of inst/COPYRIGHTS about the rules, as shipped
copy <- file.path(src, "inst/COPYRIGHTS")
if (file.exists(copy)) file.copy(copy, file.path(out, "key_patterns.COPYRIGHT"), overwrite = TRUE)
message(sprintf("%d rules, ignore: %d patterns, %d texts -> %s", nrow(p), length(x$ignore$patterns),
                length(x$ignore$texts), target))
