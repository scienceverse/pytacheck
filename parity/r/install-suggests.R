# Install metacheck's suggested packages that change module results, and that
# the parity cases switch on and off themselves, into their own library,
# <R.home()>/suggests: the `careless` package, which data_check needs to screen
# survey data for careless responding (tests/mod_data_check/dc_helpers.R loads a
# vendored copy for the cases that want it). Only the accuracy report
# (`python -m parity accuracy --generate`) puts this library on R's path, so it
# runs metacheck as a user with its suggested packages installed would.
#
#   Rscript parity/r/install-suggests.R
#
# Dependencies (psych) come from the main library: parity/r/conda-linux-64.lock
# pins them (install-with-pak.R installs them with careless).
lib <- file.path(R.home(), "suggests")
dir.create(lib, showWarnings = FALSE, recursive = TRUE)
remotes::install_version(
  "careless", version = "1.2.2", lib = lib, dependencies = FALSE,
  upgrade = "never", repos = "https://cloud.r-project.org"
)
if (!requireNamespace("careless", lib.loc = lib, quietly = TRUE)) {
  stop("careless did not install into ", lib)
}
cat("careless", as.character(packageVersion("careless", lib.loc = lib)), "in", lib, "\n")
