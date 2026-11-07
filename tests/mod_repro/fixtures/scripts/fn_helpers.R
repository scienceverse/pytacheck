# helpers used by fn_main.R
summarise_cols <- function(d) {
  colMeans(d[sapply(d, is.numeric)])
}
