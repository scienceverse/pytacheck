g <- function() {
  x <- 1
  stop("multi\nline\n  indented")
}
local({ g() })
