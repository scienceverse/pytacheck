# Vectors for tests/datacheck_files/test_likert.py, drawn in the same order as
# upstream test-data-checks.R (".detect_likert_scale infers scale ranges
# robustly"), which needs R's RNG. Run with the metacheck reference R:
#   Rscript make_likert_ref.R
set.seed(1)
v <- list(
  s1_5 = sample(1:5, 200, TRUE),
  s1_7 = sample(1:7, 200, TRUE),
  s0_10 = sample(0:10, 300, TRUE),
  bip5 = sample(-5:5, 300, TRUE),
  bip11 = sample(-11:11, 400, TRUE),
  s2_5 = sample(2:5, 200, TRUE),
  s3_5 = sample(3:5, 200, TRUE),
  zero = sample(c(0, 2, 3, 4, 5), 200, TRUE),
  gap = sample(c(1, 2, 5, 6, 7), 200, TRUE),
  sus99 = c(sample(1:5, 200, TRUE), 99),
  sus33 = c(sample(1:7, 200, TRUE), 33),
  sus8 = c(sample(1:6, 200, TRUE), 8),
  neg99 = c(sample(1:5, 200, TRUE), -99, -99),
  adj6 = c(sample(1:5, 200, TRUE), 6),
  age = sample(18:65, 200, TRUE),
  rt = round(rlnorm(200, log(650), .35)),
  coded = sample(c(10, 20, 30), 200, TRUE),
  count = rpois(200, 8),
  nonint = rnorm(200),
  few = c(1, 2, 3)
)
jsonlite::write_json(v, "likert_ref.json", digits = NA, auto_unbox = FALSE)
