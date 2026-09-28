#' Chained Module
#'
#' @description
#' Uses the marginal module's table.
#'
#' @details
#' Counts the rows of the marginal table.
#'
#' @keywords reference
#'
#' @author Lisa DeBruine
rp_chained <- function(paper, extra = "") {
  p <- get_prev_outputs("marginal", "table")
  n <- if (is.null(p)) -1L else nrow(p)
  list(
    table = data.frame(n = n),
    traffic_light = if (n > 0) "red" else "green",
    summary_text = paste0("The marginal table had ", n, " rows.", extra),
    report = c(paste0("Chained report ", n, "."), extra)
  )
}
