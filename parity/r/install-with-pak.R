# Portable (non-conda) way to build the R reference: installs the package
# versions in parity/r/packages.lock with pak, then metacheck from the
# upstream/metacheck submodule. Run from the repository root.
lock <- readLines("parity/r/packages.lock")
lock <- lock[nzchar(lock)]
if (!requireNamespace("pak", quietly = TRUE)) {
  install.packages("pak", repos = sprintf(
    "https://r-lib.github.io/p/pak/stable/%s/%s/%s",
    .Platform$pkgType, R.Version()$os, R.Version()$arch
  ))
}
pak::pkg_install(sub("==", "@", lock, fixed = TRUE), upgrade = FALSE, ask = FALSE)
pak::local_install("upstream/metacheck", upgrade = FALSE, ask = FALSE)
