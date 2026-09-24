#!/usr/bin/env bash
# Create the exact R reference environment the parity goldens are generated
# with (R 4.5.3 + pinned conda-forge builds, linux-64), then install the
# pinned metacheck from the upstream/metacheck submodule.
#
#   parity/r/setup-reference.sh [PREFIX]          # default PREFIX: .r-reference
#   export PYTACHECK_RSCRIPT="$PREFIX/bin/Rscript"
#
# On other platforms, install R >= 4.5 and the versions in packages.lock
# (e.g. with pak: Rscript parity/r/install-with-pak.R), results may then
# differ in the last digits of some floating-point values.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${1:-$ROOT/.r-reference}"

if ! command -v micromamba >/dev/null 2>&1; then
  echo "micromamba not found: see https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html" >&2
  exit 1
fi
micromamba create -y -p "$PREFIX" --file "$ROOT/parity/r/conda-linux-64.lock"
"$PREFIX/bin/R" CMD INSTALL --no-test-load "$ROOT/upstream/metacheck"
"$PREFIX/bin/Rscript" -e 'suppressPackageStartupMessages(library(metacheck)); cat("metacheck", as.character(packageVersion("metacheck")), "ready\n")'
echo "export PYTACHECK_RSCRIPT=$PREFIX/bin/Rscript"
