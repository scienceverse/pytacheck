# syntax=docker/dockerfile:1.7
#
# pytacheck container images.
#
#   docker build -t pytacheck .                                   # core + data readers + API
#   docker build -t pytacheck:bibr --build-arg WITH_BIBR=1 .      # + bibr, reads PDF/DOCX directly
#
#   docker run --rm -v "$PWD:/work" pytacheck run paper.json -m all_p_values
#   docker run --rm -p 8000:8000 pytacheck serve --host 0.0.0.0
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:0.9.8 AS uv

FROM python:${PYTHON_VERSION}-slim AS build
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/pytacheck
ARG WITH_BIBR=0
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

FROM python:${PYTHON_VERSION}-slim AS runtime
ARG WITH_BIBR=0
LABEL org.opencontainers.image.title="pytacheck" \
      org.opencontainers.image.description="Check research outputs for best practices (Python port of metacheck)" \
      org.opencontainers.image.source="https://github.com/thesanogoeffect/pytacheck" \
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
