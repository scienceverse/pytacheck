test_that("summarise_cols returns the column means", {
  expect_equal(summarise_cols(data.frame(a = 1:3)), c(a = 2))
})
