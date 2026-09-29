# The metacheck app for a Hugging Face Docker Space, or any host that runs a container.
#
#   docker build --build-arg REF=<commit> -t metacheck-app deploy/space
#
# REF is the commit of pytacheck to install. It is required, and a full commit hash keeps
# the build the same every time. The footer of the page links to this commit.
FROM python:3.12-slim

ARG REF=set-REF-to-a-commit-hash
ARG UV_VERSION=0.9.8

RUN test "$REF" != "set-REF-to-a-commit-hash" || \
    { echo "Set the commit to install: --build-arg REF=<commit>" >&2; exit 1; }

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

# the pinned versions of the app's dependencies, from the same commit
ADD https://raw.githubusercontent.com/scienceverse/pytacheck/${REF}/install/constraints-app.txt /tmp/constraints-app.txt

ENV VIRTUAL_ENV=/opt/metacheck
ENV PATH=/opt/metacheck/bin:$PATH
RUN uv venv "$VIRTUAL_ENV" && \
    uv pip install --no-cache -c /tmp/constraints-app.txt \
      "pytacheck[app] @ https://github.com/scienceverse/pytacheck/archive/${REF}.tar.gz" && \
    rm /tmp/constraints-app.txt

# Spaces run the app as user 1000
RUN useradd --create-home --uid 1000 user
USER user
WORKDIR /home/user

ENV METACHECK_APP_COMMIT=${REF}
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:7860/healthz', timeout=4)"

# METACHECK_APP_TOKENS (and on a Space, SPACE_HOST) come from the environment
CMD ["metacheck-app", "--hosted", "--host", "0.0.0.0", "--port", "7860"]
