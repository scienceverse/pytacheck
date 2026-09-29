# syntax=docker/dockerfile:1.7
#
# pytacheck container images.
#
#   docker build -t pytacheck .                                   # core + data readers + API
#   docker build -t pytacheck:bibr --build-arg WITH_BIBR=1 .      # + bibr, reads PDF/DOCX directly
#
#   docker run --rm -v "$PWD:/work" pytacheck run paper.json -m all_p_values
#   docker run --rm -p 8000:8000 -e PYTACHECK_API_KEY pytacheck serve --host 0.0.0.0
#                                 (a key of 32+ characters; without one, serve refuses 0.0.0.0)
#   --build-arg BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim   # a Docker Hub mirror
ARG PYTHON_VERSION=3.12
ARG BASE_IMAGE=python:${PYTHON_VERSION}-slim

FROM ${BASE_IMAGE} AS build
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG UV_VERSION=0.9.8
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/pytacheck
ARG WITH_BIBR=0
# bibr needs a C compiler: numind's cdifflib has no wheel and builds from source
RUN if [ "$WITH_BIBR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && \
      rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    extras="--extra data --extra api"; \
    if [ "$WITH_BIBR" = "1" ]; then extras="$extras --extra bibr"; fi; \
    uv sync --locked --no-dev --no-install-project $extras
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    extras="--extra data --extra api"; \
    if [ "$WITH_BIBR" = "1" ]; then extras="$extras --extra bibr"; fi; \
    uv sync --locked --no-dev --no-editable $extras

FROM ${BASE_IMAGE} AS runtime
ARG WITH_BIBR=0
LABEL org.opencontainers.image.title="pytacheck" \
      org.opencontainers.image.description="Check research outputs for best practices (Python port of metacheck)" \
      org.opencontainers.image.source="https://github.com/scienceverse/pytacheck" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
RUN if [ "$WITH_BIBR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libmagic1 && \
      rm -rf /var/lib/apt/lists/*; \
    fi && \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin pytacheck && \
    mkdir -p /work /cache && chown pytacheck:pytacheck /work /cache
COPY --from=build /opt/pytacheck /opt/pytacheck
ENV PATH=/opt/pytacheck/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTACHECK_CACHE_DIR=/cache \
    HF_HOME=/cache/huggingface
USER pytacheck
WORKDIR /work
VOLUME ["/cache"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import pytacheck" || exit 1
ENTRYPOINT ["pytacheck"]
CMD ["--help"]
