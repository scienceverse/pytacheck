#' List Summary
#'
#' @description
#' A summary that is a markdown list.
#'
#' @details
#' <validation>Only validation.</validation>
#'
#' @keywords discussion
#'
#' @author A One
#' @author B Two
rp_list_summary <- function(paper, na = FALSE) {
  list(
    traffic_light = if (na) "na" else "info",
    summary_text = "\n* first item\n* second item",
    report = "Short report."
  )
}
