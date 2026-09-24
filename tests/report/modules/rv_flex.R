#' Flexible  Module
#'
#' @description
#' Returns what it is given (report review tests).
#'
#' @details
#' First details. <validation>One.</validation> Middle text.
#'
#' <validation>
#' Two.
#' </validation>
#'
#' End text.
#'
#' @keywords intro
#'
#' @author A One (\email{a@b.c})
#' @author B Two
#' @author C Three
#' @author D Four
rv_flex <- function(paper, summary_text = NULL, report = NULL,
                    traffic_light = "info") {
  list(
    traffic_light = traffic_light,
    summary_text = summary_text,
    report = report
  )
}
