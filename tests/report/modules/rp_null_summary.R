#' Null Summary
#'
#' @description
#' A module without a summary text.
#'
#' @details
#' Only details.
#'
#' @keywords intro
#'
#' @author Daniel Lakens
rp_null_summary <- function(paper) {
  list(
    traffic_light = "info",
    report = c("Some report text.", "More report text.")
  )
}
