# Canonical, type-preserving JSON encoding of R values for parity checks.
#
# Every value becomes {"t": <type>, ...}. The Python side
# (parity/pyharness/canonical.py) produces the same shape from pandas /
# Python objects so the two can be compared structurally.
#
#   NULL                      {"t": "null"}
#   atomic vector             {"t": "chr"|"dbl"|"int"|"lgl", "v": [...], "names": [...]?}
#                             NA -> null; NaN -> "NaN"; Inf -> "Inf"/"-Inf"
#   factor / Date / POSIXct   encoded as chr
#   data.frame                {"t": "df", "nrow": n, "names": [...], "v": [<column>, ...]}
#   list                      {"t": "list", "names": [...]|null, "v": [...]}
#   module output             {"t": "module_output", "names": [...], "v": [...]}
#                             (the paper and prev_outputs elements are dropped)
#   paper / paperlist         {"t": "paper"|"paperlist", "names": [...], "v": [...]}
#   anything else             {"t": "other", "class": [...], "repr": "..."}

.pc_atomic <- function(x) {
  if (is.factor(x)) x <- as.character(x)
  if (inherits(x, "Date")) x <- format(x, "%Y-%m-%d")
  if (inherits(x, "POSIXt")) x <- format(x, "%Y-%m-%dT%H:%M:%S")
  if (inherits(x, "difftime")) x <- as.numeric(x)
  type <- switch(typeof(x),
    character = "chr", double = "dbl", integer = "int", logical = "lgl",
    complex = "cplx", raw = "raw", "other")
  vals <- if (type == "dbl") {
    lapply(unclass(x), function(e) {
      if (is.nan(e)) "NaN"
      else if (is.na(e)) NULL
      else if (is.infinite(e)) if (e > 0) "Inf" else "-Inf"
      else e
    })
  } else if (type %in% c("cplx", "raw")) {
    as.list(as.character(x))
  } else {
    lapply(unclass(x), function(e) if (is.na(e)) NULL else e)
  }
  out <- list(t = type, v = unname(vals))
  if (!is.null(names(x))) out$names <- as.list(names(x))
  out
}

.pc_list <- function(t, x) {
  nms <- names(x)
  list(
    t = t,
    names = if (is.null(nms)) NULL else as.list(nms),
    v = unname(lapply(unclass(x), pc_canonical))
  )
}

pc_canonical <- function(x) {
  if (is.null(x)) return(list(t = "null"))
  if (inherits(x, "metacheck_module_output")) {
    keep <- setdiff(names(x), c("paper", "prev_outputs"))
    y <- unclass(x)[keep]
    return(.pc_list("module_output", y))
  }
  if (inherits(x, "scivrs_paper")) return(.pc_list("paper", x))
  if (inherits(x, "scivrs_paperlist")) return(.pc_list("paperlist", x))
  if (is.data.frame(x)) {
    return(list(
      t = "df",
      nrow = nrow(x),
      names = as.list(names(x)),
      v = unname(lapply(as.list(x), pc_canonical))
    ))
  }
  if (is.atomic(x) && !is.matrix(x)) return(.pc_atomic(x))
  if (is.matrix(x)) {
    return(list(t = "matrix", dim = as.list(dim(x)), v = .pc_atomic(as.vector(x))))
  }
  if (inherits(x, "bibentry")) {
    return(list(t = "other", class = as.list(class(x)),
                repr = paste(format(x, style = "text"), collapse = "\n")))
  }
  if (is.list(x)) return(.pc_list("list", x))
  if (is.function(x)) return(list(t = "function"))
  list(t = "other", class = as.list(class(x)),
       repr = paste(utils::capture.output(print(x)), collapse = "\n"))
}

pc_to_json <- function(x, pretty = FALSE) {
  jsonlite::toJSON(x, auto_unbox = TRUE, null = "null", na = "null",
                   digits = I(15), pretty = pretty, force = TRUE)
}
