#!/usr/bin/env bash
# Build the R reference image from the Dockerfile beside this file, with
# metacheck at the commit the upstream/metacheck submodule has checked out.
#
#   parity/r/docker/build.sh [IMAGE]
#
# IMAGE defaults to $PYTACHECK_R_IMAGE, else pytacheck-r-reference:<the first 8
# hex digits of that commit>, the image the Rscript wrapper beside this file runs.
#
# The build context is a temporary folder with only what the Dockerfile copies:
# parity/r/conda-linux-64.lock, parity/r/install-suggests.R and a clean export of
# that metacheck commit (changes not committed in the submodule are left out).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
if [ ! -e "$root/upstream/metacheck/.git" ]; then
  echo "upstream/metacheck is not checked out: run git submodule update --init upstream/metacheck" >&2
  exit 1
fi
rev="$(git -C "$root/upstream/metacheck" rev-parse HEAD)"
image="${1:-${PYTACHECK_R_IMAGE:-pytacheck-r-reference:${rev:0:8}}}"
context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
cp "$root/parity/r/conda-linux-64.lock" "$root/parity/r/install-suggests.R" "$context/"
mkdir "$context/metacheck"
git -C "$root/upstream/metacheck" archive "$rev" | tar -x -C "$context/metacheck"
docker build -f "$here/Dockerfile" --build-arg "METACHECK_REV=$rev" -t "$image" "$context"
echo "built $image: export PYTACHECK_RSCRIPT=$here/Rscript"
