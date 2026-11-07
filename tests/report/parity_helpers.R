# R side of the report parity cases (parity/cases/report.yaml). Each case
# sources this file in an `$expr` (the R runner's working directory is the
# repository root); tests/report/parity_helpers.py is the Python twin.

rp_mod <- function(m) {
  f <- file.path("tests", "report", "modules", paste0(m, ".R"))
  if (file.exists(f)) f else m
}

rp_stem <- function(x) sub("\\.(R|py)$", "", basename(x))

# a validation table: kable pads its cells and styles them, pytacheck writes
# them bare; the cells are the same text
rp_static_table_cells <- function(x) {
  m <- gregexpr("(?s)<table class=\"dt-static\">.*?</table>", x, perl = TRUE)
  regmatches(x, m) <- lapply(regmatches(x, m), function(tabs) {
    vapply(tabs, function(tab) {
      rows <- regmatches(tab, gregexpr("(?s)<tr.*?</tr>", tab, perl = TRUE))[[1]]
      cells <- vapply(rows, function(r) {
        cs <- regmatches(r, gregexpr("(?s)<t[hd][^>]*>.*?</t[hd]>", r, perl = TRUE))[[1]]
        paste(trimws(gsub("(?s)^<t[hd][^>]*>|</t[hd]>$", "", cs, perl = TRUE)), collapse = " | ")
      }, character(1), USE.NAMES = FALSE)
      paste0("<TABLE ", paste(cells, collapse = " / "), ">")
    }, character(1), USE.NAMES = FALSE)
  })
  x
}

# mask what legitimately differs: R code chunks (tables as deparsed R data),
# the report date and the version
rp_mask <- function(x) {
  # report_qmd() sets the report type for the R chunks in a setup chunk
  # (metacheck 0.4); pytacheck writes no R chunks (D75)
  x <- gsub("\n\n```\\{r, include=FALSE\\}\n[^`]*```\n\n", "\n\n", x, perl = TRUE)
  x <- gsub("(?s)\n```\\{r\\}.*?\n```\n", "\n<R-CHUNK>\n", x, perl = TRUE)
  x <- rp_static_table_cells(x)
  # a failed module's summary row links the module's file name, lower-cased and bare
  x <- gsub("#[a-z0-9_-]*testsreportmodules([a-z_]+)(r|py)\\b", "#\\1", x, perl = TRUE)
  gsub("(?m)^(\\s*(?:version|report-created): ).*$", "\\1<masked>", x, perl = TRUE)
}

rp_norm_output <- function(mo) {
  names(mo) <- rp_stem(names(mo))
  for (i in seq_along(mo)) {
    mo[[i]]$module <- rp_stem(mo[[i]]$module)
    if (identical(mo[[i]]$traffic_light, "fail")) mo[[i]]$title <- rp_stem(mo[[i]]$title)
  }
  attributes(mo) <- list(names = names(mo))
  mo
}

rp_module_report <- function(paper, module, header = 3, ...) {
  op <- module_run(paper, rp_mod(module), ...)
  rp_mask(module_report(op, header = header))
}

rp_report_module_run <- function(paper, modules, args = list()) {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  names(args) <- vapply(names(args), rp_mod, character(1), USE.NAMES = FALSE)
  rp_norm_output(suppressWarnings(report_module_run(paper, mods, args)))
}

rp_report_qmd <- function(paper, modules, args = list(), qmd_paper = paper) {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  names(args) <- vapply(names(args), rp_mod, character(1), USE.NAMES = FALSE)
  mo <- suppressWarnings(report_module_run(paper, mods, args))
  txt <- if (is.null(qmd_paper)) report_qmd(mo) else report_qmd(mo, qmd_paper)
  rp_mask(gsub("tests/report/modules/([a-z_]+)\\.[Rr]", "\\1", txt))
}

rp_report <- function(paper, modules, output_format = "qmd", args = list()) {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  names(args) <- vapply(names(args), rp_mod, character(1), USE.NAMES = FALSE)
  f <- tempfile(fileext = paste0(".", output_format))
  on.exit(unlink(f))
  res <- suppressWarnings(report(paper, mods, f, output_format, args = args))
  txt <- paste(readLines(f), collapse = "\n")
  list(
    output = rp_norm_output(res),
    save_path_ok = identical(attr(res, "save_path"), f),
    file = rp_mask(gsub("tests/report/modules/([a-z_]+)\\.[Rr]", "\\1", txt))
  )
}

rp_html_export <- function(fixture) {
  d <- tempfile()
  dir.create(d)
  on.exit(unlink(d, recursive = TRUE))
  file.copy(file.path("tests", "report", "fixtures", "html", fixture), d)
  p <- metacheck:::.html_export_r_source(file.path(d, fixture))
  if (is.na(p)) return(NA)
  # the code goes to <tempdir>/metacheck-recovered-code/<md5>/<code dir>/<file>
  list(file = sub("^.*/metacheck-recovered-code/", "", p), lines = readLines(p))
}

rp_report_repository <- function(folder, modules, args = list()) {
  mods <- vapply(modules, rp_mod, character(1), USE.NAMES = FALSE)
  names(args) <- vapply(names(args), rp_mod, character(1), USE.NAMES = FALSE)
  d <- tempfile()
  dir.create(file.path(d, folder), recursive = TRUE)
  f <- tempfile(fileext = ".qmd")
  on.exit(unlink(c(d, f), recursive = TRUE))
  res <- suppressWarnings(report_repository(file.path(d, folder), output_file = f,
                                            output_format = "qmd", modules = mods, args = args))
  txt <- paste(readLines(f), collapse = "\n")
  out <- rp_norm_output(res)
  for (i in seq_along(out)) {
    if (!is.null(out[[i]]$summary_table)) out[[i]]$summary_table$paper_id <- "test_paper"
  }
  list(output = out,
       file = rp_mask(gsub("tests/report/modules/([a-z_]+)\\.[Rr]", "\\1", txt)))
}

# report_repository() on a repository folder of the checkout (repo-relative
# path), with its default modules unless `modules` is given.
rp_report_repository_dir <- function(path, modules = NULL, args = list()) {
  f <- tempfile(fileext = ".qmd")
  on.exit(unlink(f))
  res <- if (is.null(modules)) {
    suppressWarnings(report_repository(path, output_file = f, output_format = "qmd", args = args))
  } else {
    suppressWarnings(report_repository(path, output_file = f, output_format = "qmd",
                                       modules = modules, args = args))
  }
  txt <- paste(readLines(f), collapse = "\n")
  out <- rp_norm_output(res)
  for (i in seq_along(out)) {
    if (!is.null(out[[i]]$summary_table)) out[[i]]$summary_table$paper_id <- "test_paper"
  }
  list(output = out, file = rp_mask(txt))
}

rp_validate <- function(gt, module) {
  metacheck::validate(gt, rp_mod(module))
}
