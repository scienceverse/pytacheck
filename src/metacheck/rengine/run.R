# The R engine's runner: R metacheck's own checks, run on one paper, written as canonical JSON.
#
#   Rscript run.R <request.json> <result.json>
#
# The request (written by metacheck.rengine) names the paper file (a bibr JSON or a GROBID
# XML file, read with metacheck::read()), the modules, their arguments and how to run them:
#
#   chain = TRUE    report_module_run(): in order, each module seeing the earlier outputs, a
#                   failed module a "fail" output, sorted by section (what report() runs)
#   chain = FALSE   module_run() for each module on its own, a failed module the "fail"
#                   output of metacheck's plumber POST /check
#   info = TRUE     no paper: only the versions and the modules this installation has
#
# The result is canonical.R's encoding (next to this file) of a list: metacheck_version,
# r_version, modules (info) or outputs (the module outputs, by name), tables (below), and
# error (a message, when the paper could not be read or the run failed as a whole).
#
# scroll_table() is replaced while the modules run: instead of an R chunk holding the
# deparsed table (which only Quarto with R could render), it keeps the table and returns
# the marker "<!-- pytacheck-r-table N -->"; metacheck.rengine puts the N-th table of
# `tables` back in its place as a table block.
local({
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 2) stop("usage: Rscript run.R <request.json> <result.json>")
  request <- jsonlite::fromJSON(args[[1]], simplifyVector = TRUE,
                                simplifyDataFrame = FALSE, simplifyMatrix = FALSE)
  here <- dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE)[[1]])))
  source(file.path(here, "canonical.R"), local = TRUE)

  result <- list()
  write_result <- function() {
    json <- as.character(pc_to_json(pc_canonical(result)))
    con <- file(args[[2]], open = "wb")
    on.exit(close(con))
    writeBin(charToRaw(enc2utf8(json)), con)
  }

  tables <- list()
  capture_table <- function(table, colwidths = "auto", maxrows = 2, escape = FALSE,
                            column = "body") {
    # as metacheck's scroll_table(): a vector is a one-column table, an empty table is ""
    if (is.atomic(table)) {
      table <- data.frame(table)
      colnames(table) <- ""
    }
    if (is.null(table) || nrow(table) == 0 || ncol(table) == 0) return("")
    tables[[length(tables) + 1]] <<- list(
      table = table, colwidths = colwidths, maxrows = maxrows, escape = escape,
      column = column
    )
    sprintf("\n<!-- pytacheck-r-table %d -->\n", length(tables))
  }

  `%||%` <- function(x, y) if (is.null(x)) y else x
  failed <- function(module, e) {
    # metacheck's plumber POST /check: a module that fails is a "fail" output
    structure(
      list(
        module = module,
        title = module,
        table = NULL,
        summary_table = NULL,
        summary_text = "Error running module",
        report = paste0("Error running module '", module, "': ", conditionMessage(e)),
        traffic_light = "fail",
        section = "general"
      ),
      class = "metacheck_module_output"
    )
  }

  outcome <- tryCatch({
    suppressPackageStartupMessages(library(metacheck))
    result$metacheck_version <- as.character(utils::packageVersion("metacheck"))
    result$r_version <- paste(R.version$major, R.version$minor, sep = ".")
    if (isTRUE(request$info)) {
      result$modules <- tools::file_path_sans_ext(
        list.files(system.file("modules", package = "metacheck"), pattern = "\\.R$")
      )
    } else {
      utils::assignInNamespace("scroll_table", capture_table, ns = "metacheck")

      paper <- metacheck::read(request$paper)
      modules <- as.character(unlist(request$modules))
      margs <- request$args
      if (!is.list(margs)) margs <- list()
      outputs <- if (isTRUE(request$chain)) {
        metacheck::report_module_run(paper, modules, args = margs)
      } else {
        out <- lapply(modules, function(module) {
          tryCatch(
            do.call(metacheck::module_run, c(list(paper = paper, module = module),
                                             margs[[module]] %||% list())),
            error = function(e) failed(module, e)
          )
        })
        names(out) <- modules
        out
      }
      result$outputs <- unclass(outputs)[names(outputs)]
      result$tables <- tables
    }
    NULL
  }, error = function(e) conditionMessage(e))
  if (!is.null(outcome)) result$error <- outcome
  write_result()
})
