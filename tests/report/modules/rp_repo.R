#' Repository Stand-in
#'
#' @description
#' Reports the arguments report_repository() passes.
#'
#' @keywords general
rp_repo <- function(paper, local_path = NULL, local_only = FALSE, note = "") {
  list(
    table = data.frame(folder = basename(local_path %||% "none"), local_only = local_only),
    traffic_light = "info",
    summary_text = paste0("Folder ", basename(local_path %||% "none"),
                          "; local only: ", local_only, note)
  )
}
