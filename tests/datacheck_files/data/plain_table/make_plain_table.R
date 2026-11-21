# Regenerate the tables of other packages' classes that test how an .rds or .RData file is
# read as a plain data frame (tests/datacheck_files/test_sync_04418.py; metacheck 0.4.4.15,
# R test "data_read_head gives a table from an .rds or .RData file as a plain data frame").
# Needs tibble and haven; run from this directory:  Rscript make_plain_table.R
model <- asS4(structure(1, class = structure("Fit", package = "notinstalledpkg")))

# loo's model comparison: a column marked "matrix" that is a plain row of numbers
d <- data.frame(id = 1:2)
d$elpd_diff <- structure(c(0, -1.1), class = c("compare.loo", "matrix", "array"))
saveRDS(d, "loo.rds")

# a data.table date column stored as decimal numbers in a tibble
saveRDS(tibble::tibble(id = 1:3, day = structure(c(19000, 19001, 19002),
                                                 class = c("IDate", "Date"))), "idate.rds")

# a marginaleffects table carrying its fitted model
d <- structure(list(x = 1:3, y = c(.1, .2, .3)), class = c("predictions", "data.frame"),
               row.names = 1:3, model = model)
saveRDS(d, "predictions.rds")
save(d, file = "predictions.RData")

# a factor, a time and a number of other classes
d <- data.frame(id = 1:2)
d$f <- structure(1:2, levels = c("a", "b"), class = c("myfactor", "factor"))
d$t <- structure(c(0, 3600), class = c("mytime", "POSIXct", "POSIXt"), tzone = "UTC")
d$u <- structure(c(5, 6), class = "units", label = "Length")
saveRDS(d, "mixed.rds")

# SPSS's codes for missing values of a column in a tibble
saveRDS(tibble::tibble(q = haven::labelled_spss(c(1, 2, 99),
                                                labels = c(yes = 1, no = 2, missing = 99),
                                                na_values = 99, label = "Question")), "spss.rds")
