#' Validation Demo
#'
#' @description
#' Demo description
#'
#' @details
#' Demo details...
#'
#' A second paragraph of details.
#'
#' <validation>
#' Here is my demo validation information.
#' </validation>
#'
#' @keywords method
#'
#' @author Lisa DeBruine (\email{debruine@gmail.com})
#' @author Daniel Lakens
#' @author Jakub Werner
#'
#' @param paper a paper object or paperlist object
#' @param pattern the pattern to search for
#'
#' @returns a list
rp_validation <- function(paper, pattern = "significan") {
  table <- text_search(paper, pattern)
  small <- data.frame(n = nrow(table), label = "matches")
  summary_table <- dplyr::count(table, paper_id, name = "hits")
  tl <- if (nrow(table)) "yellow" else "green"
  report <- c(
    paste("This module searched every sentence for the pattern and listed the",
          "sentences where it was found. Check each of them: a mention is not",
          "necessarily a problem, but it is worth a second look before you submit",
          "the manuscript, because reviewers will often ask about it."),
    scroll_table(table[, c("text", "header")], colwidths = c(.8, .2), maxrows = 3),
    collapse_section(scroll_table(small), "Full table"),
    collapse_section(c("Guidance paragraph one.", "Guidance *two*."))
  )
  list(
    table = table,
    summary_table = summary_table,
    na_replace = 0,
    traffic_light = tl,
    summary_text = sprintf("Found %d sentence%s.", nrow(table), plural(nrow(table))),
    report = report
  )
}
