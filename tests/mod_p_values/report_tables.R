# R side of the report-table parity cases (parity/cases/mod_p_values_review.yaml).
# tests/mod_p_values/report_tables.py is the Python twin.
#
# The report prose comparison masks the R code chunks that scroll_table()
# writes, so the *data* of a module's report tables is otherwise never
# compared. This recovers each chunk's table (deparsed R data) and its
# report_table() arguments from the module's report.

mp_report_tables <- function(paper, module) {
  mo <- module_run(paper, module)
  rep <- paste(mo$report, collapse = "\n\n")
  chunks <- regmatches(rep, gregexpr("(?s)```\\{r\\}.*?\n```", rep, perl = TRUE))[[1]]
  lapply(chunks, function(ch) {
    tbl <- sub("(?s).*# table data -+\ntable <- (.*?)\n\n# display table.*", "\\1", ch, perl = TRUE)
    call <- sub("(?s).*metacheck::report_table\\(table, (.*)\\)\n```$", "\\1", ch, perl = TRUE)
    args <- eval(parse(text = paste0("list(", call, ")")))
    table <- eval(parse(text = tbl))
    rownames(table) <- NULL
    list(
      table = as.data.frame(table, check.names = FALSE),
      colwidths = args[[1]],
      maxrows = args[[2]],
      escape = args[[3]]
    )
  })
}
