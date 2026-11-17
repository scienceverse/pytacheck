# R side of the key scanner's parity cases (parity/cases/datacheck_keys.yaml, see
# tests/datacheck_keys/gen_parity_cases.py). The Python twin is key_helpers.py.
#
# The keys are made here, when a case runs, in the form of each provider's keys, by
# a small generator that Python repeats exactly: no file in the repository holds one
# (GitHub refuses a push with a key it recognises), and no result does either: the
# helpers return where a key was found, which rule found it and whether it is the
# key made, never the key.

kc_spec <- local({
  s <- NULL
  function() {
    if (is.null(s)) s <<- jsonlite::fromJSON("tests/datacheck_keys/key_spec.json",
                                             simplifyVector = FALSE)
    s
  }
})

# Park-Miller random numbers: exact in doubles, the same in Python
kc_gen <- function(type) {
  spec <- kc_spec()
  i <- match(type, names(spec$types)) - 1
  x <- 7919 + 104729 * i
  out <- ""
  for (part in spec$types[[type]]) {
    if (part[[1]] == "lit") {
      out <- paste0(out, part[[2]])
    } else {
      chars <- strsplit(spec$alphabets[[part[[3]]]], "")[[1]]
      for (j in seq_len(part[[2]])) {
        x <- (x * 48271) %% 2147483647
        out <- paste0(out, chars[(x %/% 1024) %% length(chars) + 1])
      }
    }
  }
  out
}

# text with every @@KEY:<type>@@ replaced by that type's key
kc_fill <- function(text) {
  m <- gregexpr("@@KEY:[a-z0-9-]+@@", text)
  regmatches(text, m) <- lapply(regmatches(text, m), function(x)
    vapply(x, function(p) kc_gen(sub("@@$", "", sub("^@@KEY:", "", p))), ""))
  text
}

kc_dir <- function(...) {
  d <- file.path("parity", "_out", "dc_keys", ...)
  unlink(d, recursive = TRUE)
  dir.create(d, recursive = TRUE)
  d
}

kc_write <- function(dir, files) {
  for (rel in names(files)) {
    p <- file.path(dir, rel)
    dir.create(dirname(p), recursive = TRUE, showWarnings = FALSE)
    writeBin(charToRaw(kc_fill(files[[rel]])), p)
  }
  invisible(dir)
}

.kc <- function(name) get(name, envir = asNamespace("metacheck"))

# where, by which rule, and whether it is the key made (never the key)
kc_hits <- function(f, key) {
  data.frame(n = nrow(f), start = if (nrow(f)) f$start[[1]] - 1L else NA_integer_,
             end = if (nrow(f)) f$end[[1]] else NA_integer_,
             rules = paste(sort(unique(f$rule)), collapse = ","),
             same_key = nrow(f) > 0 && key %in% f$key, stringsAsFactors = FALSE)
}

kc_rules <- function() {
  x <- .kc(".key_rules")()
  list(n = nrow(x$patterns), name = x$patterns$name, confidence = x$patterns$confidence,
       group = x$patterns$group, path = x$patterns$path, entropy = x$patterns$entropy,
       entropy_strict = x$patterns$entropy_strict, n_keywords = lengths(x$patterns$keywords),
       n_drop_match = lengths(x$patterns$drop_match), n_need_match = lengths(x$patterns$need_match),
       n_drop_contains = lengths(x$patterns$drop_contains),
       ignore_patterns = unlist(x$ignore$patterns), ignore_texts = unlist(x$ignore$texts),
       n_keywords_all = length(x$keywords), licence = x$source$licence)
}

kc_find_types <- function() {
  find <- .kc(".key_find")
  out <- list()
  for (type in names(kc_spec()$types)) {
    key <- kc_gen(type)
    for (ctx in c("sentence", "r", "json_bearer", "json")) {
      s <- switch(ctx,
        sentence = paste("my key is", key, "for the API."),
        r = sprintf('api_key <- "%s"', key),
        json_bearer = sprintf('"Authorization": "Bearer %s"', key),
        json = sprintf('{"key":"%s","n":1}', key))
      out[[length(out) + 1]] <- cbind(data.frame(type = type, context = ctx, stringsAsFactors = FALSE),
                                     kc_hits(find(s), key))
    }
  }
  do.call(rbind, out)
}

kc_find_negatives <- function() {
  find <- .kc(".key_find")
  s <- c(paste0("sk-or-v1-", strrep("x", 64)), paste0("ghp_", strrep("a", 36)),
         "api_key = 'YOUR_API_KEY'", "token: ${API_KEY}", "key = {{ secrets.KEY }}",
         "AKIAIOSFODNN7EXAMPLE", "The participants were asked to rate the stimuli.",
         "KeyError: 'response_time' in file data_cleaning.py", "", NA_character_)
  data.frame(id = seq_along(s), n = vapply(s, function(z) nrow(find(z)), 1L, USE.NAMES = FALSE))
}

kc_find_long <- function() {
  key <- kc_gen("openrouter-api-key")
  s <- paste(strrep("participant,condition,1.234\n", 2000), "key =", key, strrep("x,y\n", 3000))
  kc_hits(.kc(".key_find")(s), key)
}

kc_find_encoded <- function() {
  key <- kc_gen("github-pat")
  b64 <- paste(rep(c(LETTERS, letters, 0:9, "+", "/"), length.out = 3000), collapse = "")
  b64 <- paste0(substr(b64, 1, 1000), "/BSA", paste(rep(c(LETTERS, letters, 0:9), length.out = 30), collapse = ""),
                "/", substr(b64, 1001, 3000))
  page <- paste0('<p>Results</p>\n<img src="data:image/png;base64,', b64, '">\n<p>token ', key, '</p>')
  out <- .kc(".key_remove")(list(report = page))
  list(found = kc_hits(.kc(".key_find")(page), key),
       image_kept = grepl(b64, out$report, fixed = TRUE), key_removed = !grepl(key, out$report, fixed = TRUE),
       n_removed = attr(out, "keys_removed"),
       report_length = nchar(out$report))
}

kc_find_path_rule <- function() {
  line <- 'password = "x7Kq2-Lm9Pz"'
  d <- kc_dir("path_rule")
  writeLines(line, file.path(d, "main.tf"))
  writeLines(line, file.path(d, "Test.java"))
  f <- .kc(".key_scan_files")(file.path(d, c("main.tf", "Test.java")), names = c("main.tf", "Test.java"))
  list(files = f$file, rules = f$rule, no_path = .kc(".key_find")(line)$rule)
}

kc_remove <- function() {
  k <- vapply(c("openrouter-api-key", "github-pat", "aws-access-token", "gcp-api-key", "groq-api-key"),
              kc_gen, "")
  res <- list(report = c("text", paste("found", k[[1]])),
              table = data.frame(a = c("x", k[[2]]), b = 1:2, stringsAsFactors = FALSE),
              nested = list(list(z = paste0("key=", k[[3]]))),
              note = "nothing here")
  names(res$table)[1] <- k[[5]]
  out <- .kc(".key_remove")(res)
  n <- attr(out, "keys_removed")
  attr(out, "keys_removed") <- NULL
  plain <- list(report = "nothing here", table = data.frame(a = 1:3))
  p <- .kc(".key_remove")(plain)
  list(out = out, n = n, plain_same = identical(p, plain),
       no_whole_key = !any(vapply(k, function(z) grepl(z, paste(unlist(out), collapse = " "), fixed = TRUE), TRUE)),
       replaced = .kc(".key_replace")("aaa SECRETKEY bbb",
         data.frame(start = c(5L, 8L), end = c(13L, 16L), rule = "r", key = "k", stringsAsFactors = FALSE)),
       shown = vapply(c("abcdefghij", strrep("x", 36), strrep("y", 200)), .kc(".key_shown"), "", USE.NAMES = FALSE))
}

kc_scan_files <- function() {
  k <- vapply(c("github-pat", "openrouter-api-key", "aws-access-token"), kc_gen, "")
  d <- kc_dir("scan_files")
  writeBin(charToRaw(paste0("x <- 1\r\n# caf\xe9\r\ntoken <- '", k[[1]], "'")), file.path(d, "a.R"))
  writeBin(as.raw(c(0, 1, 2, 0, 255)), file.path(d, "blob.bin"))
  writeLines(c("a", "b", paste("k1 =", k[[2]], "; k2 =", k[[3]])), file.path(d, "two.txt"))
  nm <- c("a.R", "blob.bin", "two.txt", "missing.txt")
  f <- .kc(".key_scan_files")(file.path(d, nm), names = nm)
  skipped <- attr(f, "not_searched")
  attr(f, "not_searched") <- NULL
  list(found = f, n_skipped = nrow(skipped),
       no_whole_key = !any(vapply(k, function(z) grepl(z, paste(unlist(f), collapse = " "), fixed = TRUE), TRUE)))
}

kc_scan_tables <- function() {
  key <- kc_gen("openrouter-api-key")
  responses <- data.frame(StartDate = sprintf("2026-01-0%d", 1:9), Q1 = "How\nworried?",
                          OpenRouterAPIKey = paste("Bearer", key), stringsAsFactors = FALSE)
  late <- data.frame(id = 1:500, answer = c(rep("ok", 498), paste("my key", key), "ok"),
                     stringsAsFactors = FALSE)
  hdr <- data.frame(x = 1); names(hdr) <- key
  seen <- new.env()
  f <- .kc(".key_scan_tables")(list(responses.csv = responses, late.csv = late), seen = seen)
  h <- .kc(".key_scan_tables")(list(h.csv = hdr))
  list(found = f, header_row = h$row,
       no_whole_key = !grepl(key, paste(unlist(f), collapse = " "), fixed = TRUE))
}

kc_scan_repo <- function(name) {
  spec <- kc_spec()
  d <- kc_write(kc_dir("scan_repo", name), spec$repos[[name]])
  rel <- list.files(d, recursive = TRUE)
  files <- data.frame(paper_id = "p1", repo_url = d, file_name = basename(rel), file_path = rel,
                      file_location = file.path(d, rel),
                      data_type = ifelse(grepl("\\.csv$", rel), "data", "code"),
                      stringsAsFactors = FALSE)
  f <- .kc(".key_scan_repo_files")(files, cache = FALSE)
  skipped <- attr(f, "not_searched")
  attr(f, "not_searched") <- NULL
  f$repo_url <- gsub(d, "<repo>", f$repo_url, fixed = TRUE)
  list(found = f, n_skipped = nrow(skipped))
}

# module_run(<test paper p1>, "data_check", local_path = <the repository>): the real pipeline
kc_run_repo <- function(name) {
  source("tests/mod_data_check/dc_helpers.R", local = TRUE)
  spec <- kc_spec()
  d <- kc_write(kc_dir("run", name), spec$repos[[name]])
  p <- test_paper("Some text.")
  p$paper_id <- "p1"
  .dc_careless_off()
  mo <- suppressWarnings(withr::with_options(list(metacheck.llm.use = FALSE),
    module_run(p, "data_check", local_path = d, local_only = TRUE)))
  .dc_norm_paths(mo)
}
