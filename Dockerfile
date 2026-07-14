FROM ghcr.io/astral-sh/uv:0.9.8 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE.md ./
COPY pytacheck ./pytacheck
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN groupadd --system --gid 10001 pytacheck \
    && useradd --system --uid 10001 --gid pytacheck --home-dir /app --no-create-home pytacheck
WORKDIR /app
COPY --from=builder --chown=pytacheck:pytacheck /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER pytacheck
EXPOSE 2005
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2005/ready', timeout=2).read()"]
# Runtime command: pytacheck serve --host 0.0.0.0 --port 2005
CMD ["pytacheck", "serve", "--host", "0.0.0.0", "--port", "2005"]
