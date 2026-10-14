# Regenerate the R objects that test the readers of R's serialization format
# (tests/datacheck_files/test_rdata_reader.py). Base R only; run from this
# directory:  Rscript make_robjects.R
set.seed(1)
lat <- "caf\xe9"; Encoding(lat) <- "latin1"
byt <- "\xff\xfeab"; Encoding(byt) <- "bytes"
setClass("Person", representation(name = "character", age = "numeric"))
e <- new.env()
e$self <- e
e$f <- function(x) x + 1
delayedAssign("lazy", stop("never"), assign.env = e)
named <- 1:10
names(named) <- letters[1:10]
locked <- local({a <- 1; environment()})
lockEnvironment(locked)
objects <- list(
  strings = c(a = lat, b = "plain", c = NA, d = enc2utf8("naïve"), e = byt),
  latin1_frame = data.frame(x = 1:3, y = c(lat, "b", NA), stringsAsFactors = FALSE),
  compact_int = 1:1000, compact_real = as.numeric(1:10), real_seq = seq(1, 10, by = 0.5),
  wrapped = named, deferred = as.character(1:50), deferred_real = as.character(c(1.5, 2, NA, 1e10)),
  complex = complex(real = c(1, -0, Inf, NA, NaN, 0), imaginary = c(Inf, 1, -Inf, 2, NaN, -0)),
  raw = as.raw(0:255), logical = list(TRUE, NA, c(FALSE, TRUE)),
  reals = c(NA_real_, NaN, Inf, -Inf, -0, 1e-310),
  s4 = new("Person", name = "A", age = 1), env_cycle = e,
  bytecode = compiler::cmpfun(function(a, b = 2, ...) {
    z <- a + b
    for (i in 1:3) z <- z * i
    if (z > 10) "big" else list(...)
  }),
  language = list(quote(a + b * c), y ~ x + (1 | g), expression(1 + 2, sin(x))),
  factor = factor(c("lo", "hi", NA, "lo"), levels = c("lo", "hi")),
  attrs = structure(1:3, class = c("myclass", "integer"), foo = list(1, "a")),
  posixct = as.POSIXct("2020-01-01 10:00:00", tz = "UTC") + 0:2,
  posixlt = as.POSIXlt("2020-01-01 10:00:00", tz = "UTC"),
  matrix = matrix(1:6, 2, dimnames = list(c("a", "b"), NULL)), array = array(1:24, c(2, 3, 4)),
  null = NULL, empty_list = list(), empty_chr = character(0), empty_df = data.frame(),
  asis = I(list(1, "a")), df_matrix_col = data.frame(id = 1:2, m = I(matrix(1:4, 2))),
  dup_names = list(a = 1, a = 2, 3), pairlist = pairlist(a = 1, b = "x", 3),
  primitives = list(sum = .Primitive("sum"), `if` = .Primitive("if")),
  env_unhashed = new.env(hash = FALSE), env_locked = locked,
  env_list = as.environment(list(x = 1, y = "a")))
saveRDS(objects, "objects.rds")
saveRDS(objects, "objects_v2.rds", version = 2)
con <- file("objects_native.rds", "wb")
serialize(objects, con, xdr = FALSE)
close(con)
# environments R writes by reference: the base namespace, a namespace, a package
saveRDS(list(mean = mean, base = .BaseNamespaceEnv, ns = asNamespace("stats"),
             pkg = as.environment("package:stats"), global = globalenv(),
             empty = emptyenv(), baseenv = baseenv()), "envs.rds")
# an environment written as a persistent name (as lazy-load databases do)
saveRDS(list(x = 1, env = e), "persist.rds", refhook = function(env) "env::1")
# a formula of 400 terms: each `+` nests one level deeper
saveRDS(as.formula(paste("y ~", paste0("x", 1:400, collapse = " + "))), "formula400.rds")
# a data frame of the column types data_read_head() converts to pandas
i64 <- readBin(writeBin(c(5L, 0L, 0L, NA_integer_, -3L, -1L), raw()), "double", 3)
saveRDS(data.frame(
  int = c(1L, NA, -5L), lgl = c(TRUE, NA, FALSE), num = c(1.5, NA, NaN),
  fct = factor(c("b", NA, "a"), levels = c("a", "b")),
  ord = factor(c("lo", "hi", "lo"), levels = c("lo", "hi"), ordered = TRUE),
  date = structure(c(-0.5, NA, 18262), class = "Date"),
  idate = structure(c(1L, NA, -1L), class = c("IDate", "Date")),
  i64 = structure(i64, class = "integer64"), chr = c(lat, NA, "x"),
  time = as.POSIXct(c("2020-01-01 10:00:00", NA, "1960-06-01 00:00:00"), tz = "Europe/Amsterdam"),
  stringsAsFactors = FALSE), "columns.rds")
