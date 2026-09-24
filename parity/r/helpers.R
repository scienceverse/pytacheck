# R-side wrappers giving base-R idioms a single callable name, so parity
# cases can target them. Each has a Python counterpart in pytacheck._r.

pc_grepl <- function(pattern, x, ignore.case = FALSE, perl = FALSE, fixed = FALSE) {
  grepl(pattern, x, ignore.case = ignore.case, perl = perl, fixed = fixed)
}
pc_gsub <- function(pattern, replacement, x, ignore.case = FALSE, perl = FALSE, fixed = FALSE) {
  gsub(pattern, replacement, x, ignore.case = ignore.case, perl = perl, fixed = fixed)
}
pc_sub <- function(pattern, replacement, x, ignore.case = FALSE, perl = FALSE, fixed = FALSE) {
  sub(pattern, replacement, x, ignore.case = ignore.case, perl = perl, fixed = fixed)
}
pc_regextract_all <- function(pattern, x, ignore.case = FALSE, perl = FALSE, fixed = FALSE) {
  regmatches(x, gregexpr(pattern, x, ignore.case = ignore.case, perl = perl, fixed = fixed))
}
pc_regextract <- function(pattern, x, ignore.case = FALSE, perl = FALSE, fixed = FALSE) {
  m <- regexpr(pattern, x, ignore.case = ignore.case, perl = perl, fixed = fixed)
  out <- rep(NA_character_, length(x))
  out[m > 0] <- regmatches(x, m)
  out
}
pc_regexec <- function(pattern, x, ignore.case = FALSE, perl = FALSE, fixed = FALSE) {
  regmatches(x, regexec(pattern, x, ignore.case = ignore.case, perl = perl, fixed = fixed))
}
pc_strsplit <- function(x, split, perl = FALSE, fixed = FALSE) {
  strsplit(x, split, perl = perl, fixed = fixed)
}
pc_as_character <- function(x) as.character(x)
pc_format_num <- function(x, digits = 7) vapply(x, function(v) format(v, digits = digits), character(1))
pc_trimws <- function(x, which = "both") trimws(x, which = which)
pc_sort <- function(x) sort(x)
