# Review script: user definitions that shadow base functions. metacheck's
# capture helpers are closures over its namespace, so they still see base's.
d <- data.frame(y = c(2.1, 3.4, 1.9, 4.2, 3.3, 2.8), g = rep(c("a", "b"), each = 3))
sapply <- function(X, f, ...) base::sapply(X, f, ...)
sapply(split(d$y, d$g), mean)
trimws <- function(x, ...) paste0("<", x, ">")
aggregate(y ~ g, data = d, FUN = mean)
vapply <- function(...) stop("shadowed vapply")
aggregate(y ~ g, data = d, FUN = median)
