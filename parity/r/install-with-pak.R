# Portable (non-conda) way to build the R reference: installs the package
# versions in parity/r/packages.lock with pak, then metacheck from the
# upstream/metacheck submodule, then metacheck's suggested package careless
# into its own library for the accuracy report (see install-suggests.R). Run
# from the repository root.
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
# careless's one dependency, psych, is pinned in packages.lock and installed
# above, into the main library: pak would otherwise put the latest psych here
suggests <- file.path(R.home(), "suggests")
dir.create(suggests, showWarnings = FALSE, recursive = TRUE)
pak::pkg_install(
  "careless@1.2.2", lib = suggests, upgrade = FALSE, ask = FALSE, dependencies = FALSE
)
