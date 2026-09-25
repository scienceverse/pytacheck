# Writes tests/repro_core/fixtures/tables_review/: metacheck-shaped saved module
# tables (what capture_module_tables() writes) for the repro_core_review parity
# cases of collect_module_tables() / .load_module_tables(). Run from the
# repository root:  Rscript tests/repro_core/make_review_fixtures.R
out <- "tests/repro_core/fixtures/tables_review"
unlink(out, recursive = TRUE)
dir.create(out, recursive = TRUE)
save_one <- function(name, x) saveRDS(x, file.path(out, name))

# a.rds: integer/factor/logical/character columns (with NA), no paper_id column,
# a second module and a combined summary table
save_one("a.rds", list(
  paper_id = "a", generated = "2026-01-01T00:00:00+0000",
  summary_table = data.frame(paper_id = "a", n = 2L),
  modules = list(
    mymod = list(table = data.frame(x = 1:2, f = factor(c("u", "v")),
                                    l = c(TRUE, NA), s = c("p", NA)),
                 traffic_light = "green", summary_text = "two rows",
                 report = c("line one", "line two")),
    other = list(table = data.frame(y = 3.5), traffic_light = "info")
  )))
# B.rds: double/character columns and its own paper_id column (kept as is)
save_one("B.rds", list(
  paper_id = "B", generated = "2026-01-01T00:00:00+0000",
  summary_table = NULL,
  modules = list(mymod = list(table = data.frame(x = c(1.5, NA), f = c("w", "z"),
                                                 paper_id = c("pp1", "pp2"))))))
# .hidden.rds: a dot-file, which list.files() does not list
save_one(".hidden.rds", list(paper_id = "hidden", modules = list(
  mymod = list(table = data.frame(x = 99L)))))
# empty.rds: no modules at all
save_one("empty.rds", list(paper_id = "empty", generated = "x", summary_table = NULL,
                           modules = list()))
# z0.rds: the module is present but its table is empty
save_one("z0.rds", list(paper_id = "z0", modules = list(
  mymod = list(table = data.frame(x = integer(0))))))
# corrupt.rds: not an RDS file at all
writeLines("not an rds file", file.path(out, "corrupt.rds"))
# d.rds/: a directory matching the pattern
dir.create(file.path(out, "d.rds"))
writeLines("keep", file.path(out, "d.rds", "keep.txt"))
# c.json: a JSON file that is not a pytacheck results file (R never looks at it)
writeLines("{}", file.path(out, "c.json"))

# tables_types/: one module whose table's column types differ between papers,
# for dplyr::bind_rows()'s common types (logical < integer < double; an all-NA
# logical column takes the other papers' type)
types <- "tests/repro_core/fixtures/tables_types"
unlink(types, recursive = TRUE)
dir.create(types, recursive = TRUE)
tbl <- list(
  p1 = data.frame(x = c(TRUE, NA), y = NA, z = c(FALSE, TRUE), w = 1:2),
  p2 = data.frame(x = 3:4, y = c("a", NA), z = 5:6, w = c(0.5, NA)),
  p3 = data.frame(x = c(1.5, 2), y = NA_character_, z = c(TRUE, NA), w = NA)
)
for (nm in names(tbl)) saveRDS(list(paper_id = nm, modules = list(m = list(table = tbl[[nm]]))),
                               file.path(types, paste0(nm, ".rds")))
