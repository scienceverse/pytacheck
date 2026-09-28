# The R reference in Docker

Goldens are R's output, and their bytes depend on the C library R runs on (see
the header of `.github/workflows/parity.yml`). This folder builds the Parity
workflow's R environment as a Docker image. With it, any Linux machine with
Docker regenerates goldens byte for byte as CI does. An R on a host with another
glibc may not: `deparse()` keeps or escapes characters by glibc's Unicode tables.

The image holds:

- Ubuntu 24.04 (`ubuntu:noble-20260911`, pinned by digest): glibc 2.39, as in CI.
- The conda-forge R builds of `parity/r/conda-linux-64.lock`, installed with
  micromamba 2.9.0-0, checked against its published checksum.
- httptest2 at commit `37efe13f`.
- metacheck at the commit that `upstream/metacheck` has checked out.
- careless 1.2.2 in `<R.home()>/suggests`, the library that only the accuracy
  report loads (`parity/r/install-suggests.R`).

## Build

```bash
git submodule update --init upstream/metacheck
parity/r/docker/build.sh     # pytacheck-r-reference:<metacheck commit, 8 hex digits>
```

`build.sh` puts the lock file, `install-suggests.R` and a clean export of the
metacheck commit in a temporary build context, and builds the image from it. It
needs the network (Ubuntu, GitHub, conda-forge and CRAN), a few minutes and
about 3.7 GB of disk. `build.sh IMAGE`, or `PYTACHECK_R_IMAGE`, builds it under
another name.

Rebuild when `upstream/metacheck` moves: the wrapper then asks for the new
commit's image and says it is missing. Also rebuild after a change to the lock
file, `install-suggests.R` or the Dockerfile. The tag stays the same then, and
`build.sh` replaces the image under it. Keep the Dockerfile in step with
`parity.yml`: the same base image digest and httptest2 commit.

To keep a copy of a working image outside Docker:
`docker save pytacheck-r-reference:<tag> | gzip > r-reference.tar.gz`, and
`docker load < r-reference.tar.gz` to bring it back.

## Use

```bash
export PYTACHECK_RSCRIPT=$PWD/parity/r/docker/Rscript
uv run python -m parity generate --area core
uv run python -m parity accuracy --generate -m marginal
git status     # unchanged goldens are rewritten with the same bytes
```

The `Rscript` wrapper runs `Rscript` in the image with `docker run`:

- as your user and group, on the host's network, with an empty `HOME` of its
  own (a tmpfs): nothing one run leaves in `~`, such as metacheck's data
  folder, reaches the next;
- with `/tmp`, the checkout you call it from and the wrapper's own checkout
  mounted at the same paths, each with its git common dir (worktrees and
  submodules point into it). R sees no other host paths, except the working
  directory when it is outside a checkout;
- with your environment, minus host-only variables (`PATH`, `HOME`, `XDG_*`,
  `R_LIBS_USER`, `R_LIBS_SITE` and the like). `R_LIBS` is kept: the accuracy
  report puts the suggests library on it;
- with the image `$PYTACHECK_R_IMAGE`, else
  `pytacheck-r-reference:<first 8 hex digits of the metacheck commit>` of the
  checkout you call it from (or of the wrapper's own checkout).

It works on Linux only (`--network host`, `--user`). The Parity workflow stays
the final check: it installs the same environment and fails when a regenerated
golden differs from the committed one.
