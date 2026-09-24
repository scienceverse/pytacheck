#' Validate Demo
#'
#' @description
#' Flags sentences for validate() tests.
#'
#' @keywords results
rp_validate_mod <- function(paper) {
  t <- text_search(paper, ".")
  table <- data.frame(
    paper_id = c(t$paper_id, "zzz"),
    text = c(t$text, "an extra row"),
    flag = c(grepl("significan", t$text), NA),
    n = c(nchar(t$text), 0L)
  )
  list(table = table, traffic_light = "info", summary_text = "done")
}
