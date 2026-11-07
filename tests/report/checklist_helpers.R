# R side of the checklist report and file tree parity cases
# (parity/cases/report.yaml, ids "checklist.*" and "file_tree.*"). Python twin:
# tests/report/checklist_helpers.py. Builds on parity_helpers.R.

source(file.path("tests", "report", "parity_helpers.R"))

# A module output, as module_run() returns it.
rc_mo <- function(module, title, traffic_light = "info", ...) {
  structure(list(module = module, title = title, traffic_light = traffic_light, ...),
            class = "metacheck_module_output")
}

# Mask what legitimately differs: images and the font as data: URIs (the
# files are the same), the date, the versions, and the setup chunk (D75).
rc_mask <- function(x) {
  x <- gsub("data:[A-Za-z/+.-]+;base64,[A-Za-z0-9+/=]+", "data:<masked>", x, perl = TRUE)
  x <- gsub("[0-9]{4}-[0-9]{2}-[0-9]{2}", "<date>", x, perl = TRUE)
  x <- gsub("Metacheck [0-9][0-9.]*( \\(pytacheck [^)]*\\))?", "Metacheck <version>", x, perl = TRUE)
  rp_mask(x)
}

# The module outputs of a named scenario.
rc_scenario <- function(name) {
  logo <- file.path("src", "metacheck", "report", "assets", "logo.png")
  switch(name,
    checks = list(my_check = rc_mo("my_check", "My Check", "red", checks = data.frame(
      status = c("fix", "fine", "improve"),
      title = c("A", "B", "C"),
      found = c("Found A.", "Found B.", NA),
      todo = c("Do A.", "", "Do C.")
    ))),
    checks_unknown = list(my_check = rc_mo("my_check", "My Check", "red", checks = data.frame(
      status = c("fix", "great", "improve"),
      title = c("A", "B", "C"),
      found = c("Found A.", "Found B.", "Found C.")
    ))),
    single = list(
      a = rc_mo("a", "Red", "red", summary_text = "Something to fix.", report = "The details."),
      b = rc_mo("b", "Yellow", "yellow", summary_text = "Something.", report = "Something."),
      c = rc_mo("c", "Green", "green", summary_text = "Fine."),
      d = rc_mo("d", "Not applicable", "na", summary_text = "Nothing to check."),
      e = rc_mo("e", "Failed", "fail", summary_text = "This module failed to run", report = "an error"),
      f = rc_mo("f", "Blue", "blue", summary_text = c("Part one.", "Part two."),
                report = c("Part one.", "Part two."))
    ),
    empty = list(),
    all_statuses = list(my_check = rc_mo("my_check", "All <results> & more", "red", checks = data.frame(
      status = c("fix", "improve", "decide", "info", "fine", "na", "fix", "fine"),
      title = c("No junk", "Size", "Pick one", "Note", "Names", "ORCIDs", "Bare", "Plain"),
      section = c("2 Files", "", "1.1", "", "", "", "", ""),
      found = c("2 junk files.", "1 MB.", "Two options.", "FYI.", "All fine.", "No ORCID.", "", "Ok."),
      todo = c("Remove them.", "Shrink it.", "Choose.", "", "", "", "", ""),
      why = c("Junk confuses.", "", "", "", "", "", "", ""),
      note = c("", "A note.", "", "", "", "Not needed.", "", ""),
      details = c("The junk table.", "", "Options.", "FYI details.", "The size table.", "", "", ""),
      details_title = c("Show the files", "", "", "", "", "", "", ""),
      stringsAsFactors = FALSE
    ))),
    full = list(my_check = rc_mo("my_check", "My \"Check\"", "red",
      intro = "This report checks a package.",
      logo = logo,
      checks = data.frame(
        status = c("fix", "fine", "na"),
        title = c("No junk", "Size", "ORCIDs"),
        section = c("2 Files", "1.1", ""),
        found = c("2 junk files.", "1 MB.", "No ORCID."),
        todo = c("Remove them.", "", ""),
        details = c("The junk table.", "The size table.", ""),
        details_title = c("Show the files", "", "")
      ))),
    multi = list(
      my_check = rc_mo("my_check", "My Check", "red", checks = data.frame(
        status = "fix", title = "No junk", found = "2 junk files.", todo = "Remove them.")),
      other = rc_mo("other", "Other", "green", summary_text = "Fine.")
    ),
    tree = list(
      data_check = rc_mo("data_check", "Data Check", "yellow",
        summary_text = "Some data files.",
        report = c("The data files.", "#### Data Tree", "Where the files sit.", "TREE-LINES")),
      reproducibility_check = rc_mo("reproducibility_check", "Reproducibility Check", "green",
        summary_text = "The code ran.", report = "Ran.", checklist_section = "The scripts."),
      other = rc_mo("other", "Other", "green", summary_text = "Fine.", report = "Other details.")
    ),
    no_tree = list(
      reproducibility_check = rc_mo("reproducibility_check", "Reproducibility Check", "green",
        summary_text = "The code ran.", report = "Ran.", checklist_section = "The scripts."),
      other = rc_mo("other", "Other", "green", summary_text = "Fine.", report = "Other details.")
    ),
    brief_keep = list(
      kept = rc_mo("kept", "Kept", "green", summary_text = "The code ran.", report = "Ran fine.",
                   brief = TRUE),
      green = rc_mo("green", "Plain green", "green", summary_text = "Nothing to see."),
      red = rc_mo("red", "Red", "red", summary_text = "Fix this.", report = "Details of the problem.")
    ),
    section_note = list(sec = rc_mo("sec", "Sectioned", "red",
      summary_text = "Look below.", report = c("Long", "report"),
      checklist_section = c("Paragraph one.", "Paragraph two."))),
    fold = {
      prev <- report_type()
      report_type("checklist")
      on.exit(report_type(prev))
      list(fold = rc_mo("fold", "Folding", "yellow", summary_text = "Short.",
        report = c("Details first.", collapse_section(c("One.", "Two."), "Why this", callout = "note"))))
    },
    stop("no scenario ", name)
  )
}

# The paper of a checklist report: none, a stand-in, one with a title, or with
# a title and a DOI.
rc_paper <- function(kind) {
  if (kind == "none") return(list())
  p <- test_paper()
  if (kind %in% c("titled", "doi")) p$info$title <- "my_package"
  if (kind == "doi") p$info$doi <- "10.1234/abc"
  p
}

# .checklist_checks() on a scenario, as columns, with the warnings it gave.
rc_checks <- function(name) {
  warns <- character(0)
  checks <- withCallingHandlers(
    metacheck:::.checklist_checks(rc_scenario(name)),
    warning = function(w) {
      warns <<- c(warns, conditionMessage(w))
      invokeRestart("muffleWarning")
    }
  )
  c(as.list(checks), list(warnings = warns))
}

# .report_checklist_qmd() on a scenario.
rc_qmd <- function(name, paper = "plain") {
  prev <- report_type()
  report_type("checklist")
  on.exit(report_type(prev))
  rc_mask(metacheck:::.report_checklist_qmd(rc_scenario(name), rc_paper(paper)))
}

# report_qmd() on module outputs, with a report type
rc_report_qmd <- function(name, paper = "titled", type = "checklist") {
  prev <- report_type()
  report_type(type)
  on.exit(report_type(prev))
  rc_mask(report_qmd(rc_scenario(name), rc_paper(paper)))
}

# report() to a .qmd, with a report type
rc_report <- function(paper, modules, type = "checklist", args = list()) {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  names(args) <- vapply(names(args), rp_mod, character(1), USE.NAMES = FALSE)
  f <- tempfile(fileext = ".qmd")
  on.exit(unlink(f))
  res <- suppressWarnings(report(paper, mods, f, "qmd", report_type = type, args = args))
  txt <- paste(readLines(f), collapse = "\n")
  list(output = rp_norm_output(res),
       save_path_ok = identical(attr(res, "save_path"), f),
       type_after = report_type(),
       file = rc_mask(gsub("tests/report/modules/([a-z_]+)\\.[Rr]", "\\1", txt)))
}

# report_repository() on an empty folder, with a report type
rc_report_repository <- function(folder, modules, args = list(), type = "checklist") {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  names(args) <- vapply(names(args), rp_mod, character(1), USE.NAMES = FALSE)
  d <- tempfile()
  dir.create(file.path(d, folder), recursive = TRUE)
  f <- tempfile(fileext = ".qmd")
  on.exit(unlink(c(d, f), recursive = TRUE))
  res <- suppressWarnings(report_repository(file.path(d, folder), output_file = f,
                                            output_format = "qmd", modules = mods, args = args,
                                            report_type = type))
  txt <- paste(readLines(f), collapse = "\n")
  out <- rp_norm_output(res)
  for (i in seq_along(out)) {
    if (!is.null(out[[i]]$summary_table)) out[[i]]$summary_table$paper_id <- "test_paper"
  }
  list(output = out,
       file = rc_mask(gsub("tests/report/modules/([a-z_]+)\\.[Rr]", "\\1", txt)))
}

# ---- file tree -----------------------------------------------------------

rc_files <- function(kind) {
  switch(kind,
    study = list(
      files = data.frame(
        repo_url = "my_study",
        file_name = c("raw.csv", "README.md", "analysis.R"),
        file_path = c("data/raw.csv", "README.md", "code/analysis.R"),
        data_type = c("data", "documentation", "code"),
        doc_role = c(NA, "readme", NA)
      ),
      naming = data.frame(file_name = "raw.csv", detail = "no date"),
      useless = NULL
    ),
    link = list(
      files = data.frame(repo_url = "https://osf.io/abcde", file_name = "raw.csv",
                         file_path = "data/raw.csv", data_type = "data"),
      naming = NULL,
      useless = data.frame(
        repo_url = "https://osf.io/abcde",
        file_path = c(".DS_Store", "__MACOSX/._raw.csv", "__MACOSX/._x.csv", ".git/config"),
        kind = c("operating system", "operating system", "operating system", "version control"),
        severity = c("problem", "problem", "problem", "suggestion")
      )
    ),
    server = list(
      files = data.frame(
        repo_url = "/data/storage/jobs/1a2b3c/repo/my_study",
        file_name = "raw.csv", file_path = "data/raw.csv", data_type = "data", doc_role = NA),
      naming = NULL, useless = NULL
    ),
    link_slash = list(
      files = data.frame(repo_url = "https://osf.io/abcde/", file_name = "raw.csv",
                         file_path = "data/raw.csv", data_type = "data", doc_role = NA),
      naming = NULL, useless = NULL
    ),
    no_files = list(files = NULL, naming = NULL, useless = NULL),
    zero_rows = list(
      files = data.frame(repo_url = character(0), file_name = character(0),
                         file_path = character(0), data_type = character(0)),
      naming = NULL, useless = NULL
    ),
    no_paths = list(files = data.frame(file_name = "a.csv"), naming = NULL, useless = NULL),
    stop("no files ", kind)
  )
}

rc_ft_rows <- function(paths, is_dir = NULL) {
  rows <- metacheck:::.file_tree_rows(paths, is_dir)
  list(text = rows$text, leaf_idx = rows$leaf_idx)
}

rc_ft_block <- function(kind) {
  x <- rc_files(kind)
  block <- metacheck:::.file_tree_block(x$files, x$naming, x$useless)
  if (is.null(block)) return(NULL)
  as.list(block)
}

# A local folder drawn in full; the folder's own (random) name is masked.
rc_ft_local <- function() {
  d <- tempfile("ft")
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  for (x in c("data/raw", "code/__pycache__", ".git/objects/ab", "docs/empty")) {
    dir.create(file.path(d, x), recursive = TRUE)
  }
  writeLines("a,b\n1,2", file.path(d, "data", "raw", "raw data.csv"))
  writeLines("x <- 1", file.path(d, "code", "analysis.R"))
  writeLines("b", file.path(d, "code", "__pycache__", "u.pyc"))
  for (i in 1:3) writeLines("o", file.path(d, ".git", "objects", "ab", paste0("obj", i)))
  writeLines("junk", file.path(d, ".DS_Store"))
  writeLines("*.html", file.path(d, ".gitignore"))
  writeLines("# Study", file.path(d, "README.md"))
  files <- data.frame(repo_url = d,
                      file_name = c("raw data.csv", "analysis.R", "README.md"),
                      file_path = c("data/raw/raw data.csv", "code/analysis.R", "README.md"),
                      data_type = c("data", "code", "documentation"),
                      doc_role = c(NA, NA, "readme"))
  block <- metacheck:::.file_tree_block(files)
  lines <- strsplit(block[[4]], "\n")[[1]]
  list(head = block[[1]], legend = block[[2]],
       label = sub(basename(d), "<dir>", block[[3]], fixed = TRUE),
       lines = lines)
}

rc_ft_report <- function(kind) {
  report <- c("Intro", "#### Data Tree", "legend", "tree", "#### Data Files", "x")
  switch(kind,
    from = metacheck:::.file_tree_from_report(report),
    from_none = list(metacheck:::.file_tree_from_report(c("Intro", "#### Data Files")),
                     metacheck:::.file_tree_from_report(NULL)),
    drop = metacheck:::.file_tree_drop_from_report(report),
    drop_end = list(metacheck:::.file_tree_drop_from_report(c("Intro", "#### Data Tree", "tree")),
                    metacheck:::.file_tree_drop_from_report(c("Intro", "#### Data Tree"))),
    drop_none = list(metacheck:::.file_tree_drop_from_report(c("Intro", "#### Data Files")),
                     metacheck:::.file_tree_drop_from_report(NULL)),
    stop("no report ", kind)
  )
}

rc_ft_pick <- function(kind) {
  report <- c("Intro", "#### Data Tree", "legend", "tree", "#### Data Files", "x")
  files <- rc_files("study")$files
  useless <- data.frame(repo_url = "my_study", file_path = ".DS_Store",
                        kind = "operating system", severity = "problem")
  switch(kind,
    data_check = metacheck:::.file_tree(list(data_check = list(report = report))),
    repo_check = as.list(metacheck:::.file_tree(list(
      data_check = list(report = "nothing"),
      repo_check = list(table = files[1, ], useless_files = useless)))),
    none = list(metacheck:::.file_tree(list())),
    stop("no pick ", kind)
  )
}
