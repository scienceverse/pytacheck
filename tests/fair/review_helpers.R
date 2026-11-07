# R side of the FAIR_repo review parity cases (parity/cases/fair_review.yaml): the internal
# helpers of FAIR_repo(), called one by one. Python twin: tests/fair/review_helpers.py. Both sides
# return the same names and shapes; an input that is a table or a paper is built here from a
# `kind`, in the same way on both sides. Builds on parity_helpers.R.

source(file.path("tests", "fair", "parity_helpers.R"))

# the internal function .FAIR_repo_<name> of metacheck
fr_fn <- function(name) {
  get(paste0(".FAIR_repo_", name), envir = asNamespace("metacheck"), inherits = FALSE)
}

# a character vector as a list, an NA as NA (one element per input)
fr_list <- function(x) unname(as.list(x))

# ── repo.R ───────────────────────────────────────────────────────────────────

# .FAIR_repo_join_path() for each pair (dir[i], link[i])
fr_join_path <- function(dir, link) {
  f <- fr_fn("join_path")
  list(value = unname(mapply(function(d, l) f(d, l), dir, link,
                             SIMPLIFY = FALSE, USE.NAMES = FALSE)))
}

# .FAIR_repo_sheet_target() for each triple (target[i], sheet[i], k[i])
fr_sheet_target <- function(target, sheet, k) {
  f <- fr_fn("sheet_target")
  list(value = unname(mapply(function(t, s, i) f(t, s, i), target, sheet, k,
                             SIMPLIFY = FALSE, USE.NAMES = FALSE)))
}

# .FAIR_repo_sheet_key() on a columns table of three files
fr_sheet_key <- function() {
  f <- fr_fn("sheet_key")
  cols <- data.frame(source_file = c("a.xlsx [S1]", "a.xlsx [S2]", "b.xlsx", "c.xlsx"),
                     sheet = c("S1", "S2", "Only", "Z"), stringsAsFactors = FALSE)
  no_sheet <- data.frame(source_file = "a.xlsx", stringsAsFactors = FALSE)
  list(value = list(
    several = f("a.xlsx", "S2", cols),
    single = f("b.xlsx", "Only", cols),
    no_such_sheet = f("b.xlsx", "Other", cols),
    wrong_sheet_of_a_single = f("c.xlsx", "S1", cols),
    no_sheet_column = f("a.xlsx", "S1", no_sheet),
    no_table = f("a.xlsx", "S1", NULL)))
}

# .FAIR_repo_neutralise_setwd(): the lines, the number changed and the folders named
fr_setwd <- function(txt, lang = "R", parse = FALSE) {
  r <- fr_fn("neutralise_setwd")(txt, lang, parse = parse)
  list(txt = as.character(r$txt), n = as.integer(r$n), paths = as.character(r$paths))
}

# A plan of the files scripts write (columns file_name, target_path, convert, original_target,
# same_as, script_writes), for .FAIR_repo_point_to_new_paths(), by kind.
fr_plan_paths <- function(kind) {
  if (identical(kind, "basic")) {
    return(data.frame(
      file_name = c("data/raw.csv", "results/table.csv", "analysis.R"),
      target_path = c("data/study-a_data.csv", "outputs/table.csv", "analysis/analysis.R"),
      convert = FALSE, original_target = NA_character_, same_as = NA_character_,
      script_writes = c(FALSE, TRUE, FALSE), stringsAsFactors = FALSE))
  }
  if (identical(kind, "convert")) {
    return(data.frame(
      file_name = c("data/trial.xlsx", "analysis.R"),
      target_path = c("data/study-a_data.csv", "analysis/analysis.R"),
      convert = c(TRUE, FALSE),
      original_target = c("documentation/original/trial.xlsx", NA),
      same_as = NA_character_, script_writes = c(TRUE, FALSE), stringsAsFactors = FALSE))
  }
  if (identical(kind, "ambiguous")) {
    return(data.frame(
      file_name = c("a/table.csv", "b/table.csv", "figure.png", "copy/figure.png"),
      target_path = c("outputs/a/table.csv", "outputs/b/table.csv", "outputs/figure.png",
                      "outputs/figure.png"),
      convert = FALSE, original_target = NA_character_,
      same_as = c(NA, NA, NA, "outputs/figure.png"),
      script_writes = TRUE, stringsAsFactors = FALSE))
  }
  if (identical(kind, "backslash")) {
    return(data.frame(
      file_name = c("results\\table.csv", "analysis.R"),
      target_path = c("study-a/outputs/table.csv", "study-a/analysis/analysis.R"),
      convert = FALSE, original_target = NA_character_, same_as = NA_character_,
      script_writes = c(TRUE, FALSE), stringsAsFactors = FALSE))
  }
  stop("unknown kind")
}

# .FAIR_repo_point_to_new_paths(): the lines and the number of strings changed
fr_point_paths <- function(txt, kind) {
  r <- fr_fn("point_to_new_paths")(txt, fr_plan_paths(kind))
  list(txt = as.character(r$txt), n = as.integer(r$n))
}

# .FAIR_repo_r_data_kind() of a file of the fixtures (a path from tests/fair/fixtures)
fr_r_data_kind <- function(rel) {
  list(value = fr_fn("r_data_kind")(file.path(ff_root(), "tests", "fair", "fixtures", rel)))
}

# .FAIR_repo_copy_no_bom() of a file of the bytes of `kind`: whether it worked and the bytes
# of the copy in hex
fr_no_bom <- function(kind) {
  bom <- as.raw(c(0xEF, 0xBB, 0xBF))
  bytes <- switch(kind,
    bom_text = c(bom, charToRaw("id,x\n1,2\n")),
    plain = charToRaw("id,x\n1,2\n"),
    empty = raw(0),
    bom_only = bom,
    partial_bom = as.raw(c(0xEF, 0xBB)),
    bom_in_the_middle = c(charToRaw("a"), bom, charToRaw("b")),
    latin1 = as.raw(c(0x6e, 0xe9, 0x6d, 0x0a)),
    two_boms = c(bom, bom, charToRaw("x")),
    stop("unknown kind"))
  d <- tempfile("nb-")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE), add = TRUE)
  src <- file.path(d, "in.csv")
  dest <- file.path(d, "out.csv")
  writeBin(bytes, src)
  ok <- fr_fn("copy_no_bom")(src, dest)
  hex <- NA_character_
  if (file.exists(dest))
    hex <- paste(as.character(readBin(dest, "raw", file.size(dest))), collapse = "")
  list(ok = isTRUE(ok), hex = hex)
}

# ── metadata: small helpers ──────────────────────────────────────────────────

# .FAIR_repo_keywords() of the value of `kind`
fr_keywords <- function(kind) {
  kw <- switch(kind,
    list_cell = list(c("a", "b")),
    vector = c("a", "b"),
    na_and_blank = c("a", NA, "", "b"),
    nested = list("a", list("b", "c")),
    single_string = "solo",
    empty_chr = character(0),
    null = NULL,
    all_blank = c("", NA),
    numbers = c(1, 2),
    two_cells = list("a", "b"),
    stop("unknown kind"))
  list(value = fr_fn("keywords")(kw))
}

# .FAIR_repo_is_web() of a vector
fr_is_web <- function(x) {
  list(value = fr_list(fr_fn("is_web")(x)))
}

# .FAIR_repo_encoding_format() for each name; NA where none is known
fr_encoding_format <- function(x) {
  f <- fr_fn("encoding_format")
  list(value = lapply(x, function(n) {
    r <- f(n)
    if (is.null(r)) NA_character_ else r
  }))
}

# .FAIR_repo_software_for() for each path; NA where none is needed
fr_software_for <- function(x) {
  f <- fr_fn("software_for")
  list(value = lapply(x, function(p) {
    r <- f(p)
    if (is.null(r)) NA_character_ else r
  }))
}

# .FAIR_repo_credit_slug() of a vector
fr_credit_slug <- function(x) {
  list(value = fr_list(fr_fn("credit_slug")(x)))
}

# .FAIR_repo_ddi_array() of eight values
fr_ddi_array <- function() {
  f <- fr_fn("ddi_array")
  list(value = list(
    null = f(NULL), empty = f(character(0)), na = f(NA_character_), blank = f(""),
    spaces = f("  "), one = f("x"), two = f(c("a", "b")), first_na = f(c(NA, "b"))))
}

# .FAIR_repo_lang_id() of a vector
fr_lang_id <- function(x) {
  list(value = fr_list(fr_fn("lang_id")(x)))
}

# .FAIR_repo_id() and .FAIR_repo_label() of a group (NULL for one study)
fr_id_label <- function(grp) {
  list(id = fr_fn("id")(grp), label = fr_fn("label")(grp))
}

# .FAIR_repo_roots() of the plan of `kind`
fr_roots <- function(kind) {
  written <- switch(kind,
    none = c("data/x.csv", NA),
    one = c("study-a/data/x.csv", "study-a/analysis/z.R"),
    two = c("study-b/data/x.csv", "study-a/data/y.csv", "study-a/analysis/z.R", NA, "README.md"),
    study_word = c("study-guide/notes.txt", "studying/x.csv"),
    stop("unknown kind"))
  list(value = fr_fn("roots")(data.frame(written = written, stringsAsFactors = FALSE)))
}

# .FAIR_repo_tree()
fr_tree <- function(paths, max = 150L) {
  list(value = fr_fn("tree")(paths, max = max))
}

# .FAIR_repo_decode_missing() of each string: a list of {code, reason}, or NULL
fr_decode_missing <- function(s) {
  f <- fr_fn("decode_missing")
  list(value = lapply(s, function(x) {
    v <- f(x)
    if (is.null(v)) return(NULL)
    unname(Map(function(code, reason) list(code = code, reason = reason),
               names(v), unname(v)))
  }))
}

# .FAIR_repo_missing_scheme_entity() of the labels table of `kind`
fr_missing_scheme <- function(kind) {
  labels <- switch(kind,
    pooled = data.frame(missing_values = c('["-99","-98"]', '{"-99":"refused"}', NA, '["-99"]'),
                        stringsAsFactors = FALSE),
    none_declared = data.frame(missing_values = c(NA, ""), stringsAsFactors = FALSE),
    no_column = data.frame(label = "x", stringsAsFactors = FALSE),
    not_a_table = NULL,
    stop("unknown kind"))
  list(value = fr_fn("missing_scheme_entity")(labels))
}

# .FAIR_repo_resolve_ref() for each reference, with the group of its script (NA: none)
fr_resolve_ref <- function(ref, group) {
  plan <- data.frame(
    file_name = c("data/a.csv", "data/b.csv", "x/DATA.csv", "y/data.csv", "z/other.R",
                  "w/gone.csv"),
    written = c("study-a/data/a.csv", "study-a/data/b.csv", "study-a/data/DATA.csv",
                "study-b/data/data.csv", "study-a/analysis/other.R", NA),
    group = c("a", "a", "a", "b", "a", "a"), stringsAsFactors = FALSE)
  f <- fr_fn("resolve_ref")
  list(value = unname(mapply(function(r, g) f(r, if (is.na(g)) NULL else g, plan),
                             ref, group, SIMPLIFY = FALSE, USE.NAMES = FALSE)))
}

# .FAIR_repo_reused_by_group() of the plan of `kind`
fr_reused_by_group <- function(kind) {
  plan <- switch(kind,
    two_groups = {
      p <- data.frame(written = c("study-a/data/x.csv", "study-a/data/y.csv", NA),
                      stringsAsFactors = FALSE)
      p$referenced_by <- list(c("b", "c"), "b", "c")
      p
    },
    none_referenced = {
      p <- data.frame(written = c("data/x.csv", "data/y.csv"), stringsAsFactors = FALSE)
      p$referenced_by <- list(character(0), NULL)
      p
    },
    no_column = data.frame(written = "data/x.csv", stringsAsFactors = FALSE),
    stop("unknown kind"))
  list(value = fr_fn("reused_by_group")(plan))
}

# ── metadata: papers ─────────────────────────────────────────────────────────

# a test paper with the authors of `kind`
fr_paper <- function(kind) {
  if (identical(kind, "ff")) return(ff_paper())
  p <- test_paper("x")
  if (identical(kind, "none")) return(p)
  if (identical(kind, "blank")) {
    p$author <- data.frame(
      given = c("", "Ann", NA), family = c(NA, "", "Lee"),
      orcid = c("0000-0002-1825-0097", "https://orcid.org/0000-0002-1825-0098", ""),
      email = c(" ", "e@x.org", NA), stringsAsFactors = FALSE)
    return(p)
  }
  if (identical(kind, "corresponding")) {
    p$author <- data.frame(
      given = c("Ann", "Bob"), family = c("Smith", "Jones"),
      corresponding = c(NA, TRUE), stringsAsFactors = FALSE)
    return(p)
  }
  stop("unknown kind")
}

# .FAIR_repo_authors() of the paper of `kind`
fr_authors <- function(kind) {
  au <- fr_fn("authors")(fr_paper(kind))
  rownames(au) <- NULL
  list(value = au)
}

# .FAIR_repo_info() and .FAIR_repo_doi_url() of a paper whose info holds `value` as doi
fr_info <- function(kind) {
  doi <- switch(kind,
    plain = "10.1234/abc",
    https = "https://doi.org/10.1234/abc",
    dx = "http://dx.doi.org/10.1234/abc",
    blank = "",
    na = NA_character_,
    absent = NULL,
    stop("unknown kind"))
  p <- test_paper("x")
  if (!is.null(doi)) p$info$doi <- doi
  list(doi = fr_fn("info")(p, "doi"), doi_url = fr_fn("doi_url")(p),
       missing_field = fr_fn("info")(p, "no_such_field"))
}

# .FAIR_repo_info() of a paper whose info field "keywords" is a list cell, and of one that holds
# a vector of one blank
fr_info_list <- function() {
  p <- ff_test_paper(list(keywords = list(c("a", "b")), title = "T"))
  # the list cell of R holds the vector pytacheck keeps in the cell: both sides give the vector
  list(value = list(keywords = unlist(fr_fn("info")(p, "keywords")), title = fr_fn("info")(p, "title"),
                    keywords_kept = fr_fn("keywords")(fr_fn("info")(p, "keywords"))))
}

# .FAIR_repo_dataset_description() with the date masked
fr_dataset_description <- function(kind) {
  subs <- list(from = format(Sys.Date(), "%Y-%m-%d"), to = "<date>")
  f <- fr_fn("dataset_description")
  d <- switch(kind,
    plain = f(test_paper("x"), "A title", "", list()),
    study = f(ff_paper(), "A title", "Study EX1",
              list(list(`@type` = "PropertyValue", name = "x")),
              reused = c("study-a/data/x.csv", "study-a/data/y.csv")),
    keywords = f(ff_test_paper(list(title = "K", doi = "https://doi.org/10.1/abc",
                                    keywords = list(c("a", "b")))),
                 "K", "", list()),
    stop("unknown kind"))
  list(value = ff_clean(d, subs))
}

# .FAIR_repo_abstract_text() of a paper
fr_abstract_text <- function(paper, max_chars = 1500L) {
  list(value = fr_fn("abstract_text")(paper, max_chars = max_chars))
}

# .FAIR_repo_still_unknown() of the info of `kind`
fr_still_unknown <- function(kind) {
  one_author <- function(name, corresponding, email, roles) {
    au <- data.frame(name = name, corresponding = corresponding, email = email,
                     stringsAsFactors = FALSE)
    au$credit <- roles
    au
  }
  info <- switch(kind,
    complete = list(
      authors = one_author("Ann Smith", TRUE, "a@x.org", list("Conceptualization")),
      title_known = TRUE, doi_url = "https://doi.org/10.1/x", licence = "LICENSE",
      design = list(list(dataCollectionMethodology = list("online")))),
    nothing = list(
      authors = one_author(character(0), logical(0), character(0), list()),
      title_known = FALSE, doi_url = NULL, licence = NA_character_,
      design = list(list(dataCollectionMethodology = list("unknown")))),
    roles_missing = list(
      authors = one_author(c("Ann Smith", "Bob Jones"), c(TRUE, FALSE), c(NA, "b@x.org"),
                           list(character(0), "Software")),
      title_known = TRUE, doi_url = "https://doi.org/10.1/x", licence = NA_character_,
      design = list(list(dataCollectionMethodology = list("online")),
                    list(dataCollectionMethodology = list("unknown")))),
    stop("unknown kind"))
  list(value = fr_fn("still_unknown")(info))
}

# .FAIR_repo_drop_local() of the value of `kind`
fr_drop_local <- function(kind) {
  folder <- "/home/u/proj"
  f <- fr_fn("drop_local")
  x <- switch(kind,
    list = f(list(
      file_location = "/home/u/proj/a.R", repo_url = "/home/u/proj",
      file_url = "https://osf.io/x", sandbox = "/tmp/sbx",
      note = "see /home/u/proj/data/a.csv", n = 3L,
      nested = list(file_location = "/x", version_pin = list(file_location = "/y", pkg = "z")),
      report = c("ran /home/u/proj/a.R", "ok")), folder = folder),
    frame = f(data.frame(
      file_location = c("/a", "/b"), repo_url = c("/local", "https://github.com/a"),
      text = c("x /home/u/proj/y", "z"), n = 1:2, stringsAsFactors = FALSE), folder = folder),
    windows = f(c("C:\\Users\\u\\proj\\a.R and C:/Users/u/proj/b.R", "none"),
                folder = "C:/Users/u/proj"),
    text = f(c("a /home/u/proj/x /home/u/proj/y", NA), folder = folder),
    no_folder = f(list(file_location = "/a", note = "/home/u/proj/x"), folder = NULL),
    web_urls = f(list(repo_url = "http://a.org/x", file_url = c("https://b.org", "/c")),
                 folder = folder),
    stop("unknown kind"))
  list(value = x)
}

# .FAIR_repo_variable_measured() of the columns (and labels) of `kind`
fr_variable_measured <- function(kind) {
  f <- fr_fn("variable_measured")
  if (identical(kind, "numeric_and_empty")) {
    cols <- data.frame(
      source_file = "data/a.csv", column_name = c("age", "empty_col", "grp"),
      quality = c(NA, "empty", NA), representation = c("numeric", NA, "text"),
      measurement_level = c("ratio", NA, "nominal"),
      min = c(18, NA, NA), max = c(65, NA, NA), n = c(100, NA, 100),
      n_missing = c(0, NA, 0), mean = c(35.5, NA, NA), sd = c(10.25, NA, NA),
      median = c(34, NA, NA), sample_values = c(NA, NA, "a | b |  | a"),
      stringsAsFactors = FALSE)
    return(list(value = f(cols, NULL, c("data/a.csv" = "study-a/data/a.csv"))))
  }
  if (identical(kind, "categorical")) {
    cols <- data.frame(
      source_file = "data/a.csv", column_name = c("cond", "wave", "code"),
      representation = c("text", "text", "numeric"),
      measurement_level = c("nominal", "ordinal", "nominal"),
      sample_values = c("ctrl|treat|ctrl", " low | high |", "1|2"),
      stringsAsFactors = FALSE)
    return(list(value = f(cols)))
  }
  if (identical(kind, "labelled")) {
    cols <- data.frame(
      source_file = "data/a.csv", column_name = c("q0", "q1", "q2", "q3"),
      representation = c("text", "numeric", "numeric", "text"),
      measurement_level = c("nominal", "ordinal", "ordinal", NA),
      min = c(NA, 1, 1, NA), max = c(NA, 5, 5, NA), mean = c(NA, 3, 2.5, NA),
      stringsAsFactors = FALSE)
    labels <- data.frame(
      source_file = "data/a.csv", column_name = c("q2", "q1", "q3", "q1"),
      label = c("Second", "First", "Third", "Duplicate"),
      label_status = c("llm", "labelled", "none", "labelled"),
      label_source = c("llm", "codebook", NA, "codebook"),
      codebook_variable = c("Q2", "Q1", NA, "Q1"),
      scale = c("Scale B", "Scale A", NA, NA),
      scale_source = c("codebook", "codebook", NA, NA),
      scale_confidence = c("high", "medium", NA, NA),
      value_labels = c('{"1":"disagree","5":"agree"}', '{"1":"never","5":"always"}', NA, NA),
      missing_values = c('["-99"]', '{"-99":"refused"}', NA, NA),
      question = c("How often?", NA, NA, NA), stringsAsFactors = FALSE)
    return(list(value = f(cols, labels)))
  }
  stop("unknown kind")
}

# ── metadata: files ──────────────────────────────────────────────────────────

# .FAIR_repo_readme_title() of the top-level READMEs among `written`, in a fixture folder
fr_readme_title <- function(written, fixture = "review_files") {
  list(value = fr_fn("readme_title")(written, ff_fixture_dir(fixture)))
}

# .FAIR_repo_write_json() of the value of `kind`: the lines written and the size of the file
fr_write_json <- function(kind) {
  obj <- switch(kind,
    scalars = list(`@id` = "x", n = 3L, ratio = 1.5, ok = TRUE, nothing = NA, name = "\u00e9"),
    numbers = list(a = 1 / 3, b = 2 / 3, c = 1e-5, d = 1e5, e = 123456789, f = -0.5,
                   g = 1234567.891, h = 100000L, i = 0, j = 1e15, k = 0.1 + 0.2, l = 22 / 7 * 1000),
    nested = list(a = list(b = list(c = 1L, d = "x"), e = list(1L, 2L, list(3L))),
                  f = list(list(g = 1L), list(g = 2L))),
    empty_containers = list(a = list(), b = structure(list(), names = character(0)),
                            c = character(0), d = list(list()), e = ""),
    vectors = list(a = c(1L, 2L, 3L), b = c("x", "y"), c = "single", d = list("x"),
                   e = c(TRUE, FALSE)),
    strings = list(tab = "a\tb", quote = "say \"hi\"", back = "a\\b", newline = "a\nb",
                   unicode = "\u00e9 \u00fc \u65e5\u672c", slash = "a/b", ctrl = "a\001b",
                   emoji = "\U0001F600", empty = ""),
    na_values = list(a = NA, b = NA_character_, c = NA_real_, d = c(1, NA), e = c("x", NA),
                     f = list(NA)),
    stop("unknown kind"))
  d <- tempfile("wj-")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE), add = TRUE)
  p <- file.path(d, "sub", "x.json")
  fr_fn("write_json")(obj, p)
  list(lines = readLines(p, warn = FALSE, encoding = "UTF-8"), size = as.integer(file.size(p)))
}
