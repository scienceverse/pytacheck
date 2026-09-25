#!/usr/bin/env bash
# Create the exact R reference environment the parity goldens are generated
# with (R 4.5.3 + pinned conda-forge builds, linux-64), then install the
# pinned metacheck from the upstream/metacheck submodule and, for the accuracy
# report, metacheck's suggested careless (parity/r/install-suggests.R).
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
# non-ASCII names in metacheck's code only survive a UTF-8 install locale
export LANG=C.UTF-8 LC_ALL=C.UTF-8
micromamba create -y -p "$PREFIX" --file "$ROOT/parity/r/conda-linux-64.lock"
# httptest2 (for parity cases that replay recorded API responses) is not on
# conda-forge: install the pinned commit from GitHub.
tmp=$(mktemp -d)
git clone --quiet https://github.com/nealrichardson/httptest2.git "$tmp/httptest2"
git -C "$tmp/httptest2" checkout --quiet 37efe13ff4c51504570c5e6ac2d93fff71a416a2
"$PREFIX/bin/R" CMD INSTALL "$tmp/httptest2"
"$PREFIX/bin/R" CMD INSTALL --no-test-load "$ROOT/upstream/metacheck"
# metacheck's suggested careless (not on conda-forge), in its own library that
# only the accuracy report loads
"$PREFIX/bin/Rscript" "$ROOT/parity/r/install-suggests.R"
"$PREFIX/bin/Rscript" -e 'suppressPackageStartupMessages(library(metacheck)); cat("metacheck", as.character(packageVersion("metacheck")), "ready\n")'
echo "export PYTACHECK_RSCRIPT=$PREFIX/bin/Rscript"
