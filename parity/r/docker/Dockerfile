# The R side of pytacheck's parity harness, as .github/workflows/parity.yml
# builds it: Ubuntu 24.04 (the goldens' glibc 2.39), the exact conda-forge R
# builds of parity/r/conda-linux-64.lock, httptest2 and metacheck at their
# pinned commits, and careless in the suggests library.
#
# build.sh beside this file prepares the build context (the lock file,
# install-suggests.R and the upstream/metacheck submodule's commit) and builds
# it; see README.md.
FROM ubuntu:noble-20260911@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
RUN apt-get update -q \
 && apt-get install -q -y --no-install-recommends build-essential ca-certificates git tzdata unzip curl \
 && rm -rf /var/lib/apt/lists/*
# a fixed micromamba release, checked against its published checksum
ARG MICROMAMBA_VERSION=2.9.0-0
ARG MICROMAMBA_SHA256=366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3
RUN curl -fsSL -o /usr/local/bin/micromamba \
      "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-64" \
 && echo "${MICROMAMBA_SHA256}  /usr/local/bin/micromamba" | sha256sum -c --quiet - \
 && chmod +x /usr/local/bin/micromamba
COPY conda-linux-64.lock /tmp/r.lock
RUN micromamba create -y -q -p /opt/r -f /tmp/r.lock && micromamba clean -a -y -q
ENV PATH=/opt/r/bin:$PATH
RUN git clone --quiet https://github.com/nealrichardson/httptest2.git /tmp/httptest2 \
 && git -C /tmp/httptest2 checkout --quiet 37efe13ff4c51504570c5e6ac2d93fff71a416a2 \
 && R CMD INSTALL /tmp/httptest2 && rm -rf /tmp/httptest2
COPY metacheck /tmp/metacheck
RUN R CMD INSTALL --no-test-load /tmp/metacheck && rm -rf /tmp/metacheck
ARG METACHECK_REV
LABEL pytacheck.metacheck-rev=$METACHECK_REV
COPY install-suggests.R /tmp/install-suggests.R
RUN Rscript /tmp/install-suggests.R
ENV HOME=/tmp
