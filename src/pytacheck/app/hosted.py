"""Hosted mode: settings from the environment, run limits, clean-up and the extra texts.

Nothing here imports gradio; ``ui`` and ``server`` use it.
"""

from __future__ import annotations

import base64
import concurrent.futures
import contextvars
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

__all__ = [
    "DEFAULT_PORT",
    "DELETE_CACHE",
    "HOSTED_NOTE",
    "MAX_QUEUE",
    "MAX_RUNS",
    "TOO_SLOW",
    "HostedConfig",
    "HostedError",
    "JobRunner",
    "data_link",
    "footer",
]

T = TypeVar("T")

TOKENS_ENV = "METACHECK_APP_TOKENS"
HOSTS_ENV = "METACHECK_APP_HOSTS"
SPACE_HOST_ENV = "SPACE_HOST"  # set by Hugging Face Spaces
TIMEOUT_ENV = "METACHECK_APP_JOB_TIMEOUT"
COMMIT_ENV = "METACHECK_APP_COMMIT"
PORT_ENV = "PORT"
DEFAULT_PORT = 7860
DEFAULT_TIMEOUT = 900.0
MIN_TOKEN_LENGTH = 32
#: runs at the same time, and paper checks waiting for a free place
MAX_RUNS = 2
MAX_QUEUE = 10
#: [seconds between sweeps, age in seconds] for files Gradio made
DELETE_CACHE = (60, 300)

REPO_URL = "https://github.com/scienceverse/pytacheck"
HOSTED_NOTE = "Your paper is processed on this server and deleted when the report is ready."
TOO_SLOW = (
    "Checking this paper took too long, so it was stopped. "
    "Try a shorter paper, or try again in a few minutes."
)


class HostedError(Exception):
    """The hosted settings are missing or wrong; the message says what to set."""


def _items(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


@dataclass(frozen=True)
class HostedConfig:
    tokens: tuple[str, ...]
    hosts: tuple[str, ...]
    job_timeout: float = DEFAULT_TIMEOUT
    commit: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HostedConfig:
        env = os.environ if env is None else env
        tokens = _items(env.get(TOKENS_ENV))
        if not tokens:
            raise HostedError(
                f"Hosted mode needs access tokens. Set {TOKENS_ENV} to one or more tokens "
                f"of at least {MIN_TOKEN_LENGTH} characters, separated by commas."
            )
        for number, token in enumerate(tokens, 1):
            if len(token) < MIN_TOKEN_LENGTH:
                raise HostedError(
                    f"Token {number} in {TOKENS_ENV} is shorter than {MIN_TOKEN_LENGTH} "
                    "characters. Make longer ones, for example with: openssl rand -hex 24"
                )
        hosts = [h.lower() for h in _items(env.get(HOSTS_ENV)) or _items(env.get(SPACE_HOST_ENV))]
        if not hosts:
            raise HostedError(
                f"Hosted mode needs the host names it is served under. Set {HOSTS_ENV} "
                "to one or more names, separated by commas (for example www.example.org)."
            )
        for host in hosts:
            if "/" in host or " " in host:
                raise HostedError(f"{HOSTS_ENV} takes host names only, not addresses: {host}")
        raw = (env.get(TIMEOUT_ENV) or "").strip()
        try:
            timeout = float(raw) if raw else DEFAULT_TIMEOUT
        except ValueError:
            timeout = 0.0
        if timeout <= 0:
            raise HostedError(f"{TIMEOUT_ENV} must be a number of seconds above 0.")
        commit = (env.get(COMMIT_ENV) or "").strip() or None
        return cls(tuple(tokens), tuple(hosts), timeout, commit)


def default_port(env: Mapping[str, str] | None = None) -> int:
    """The ``PORT`` variable when it holds a port number, else 7860."""
    raw = (os.environ if env is None else env).get(PORT_ENV, "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_PORT


def source_url(commit: str | None) -> str:
    """The exact source of the running version when the commit is known."""
    if commit and all(c in "0123456789abcdefABCDEF" for c in commit):
        return f"{REPO_URL}/tree/{commit}"
    return REPO_URL


def footer(version: str, commit: str | None) -> str:
    """Version and source link: the AGPL asks a network service to offer its source."""
    url = source_url(commit)
    return f"metacheck (Python) {version}. Source code: [{url}]({url})"


def data_link(name: str, page: str) -> str:
    """A download link that holds the report itself, so no file stays on the server."""
    from html import escape

    body = base64.b64encode(page.encode("utf-8")).decode("ascii")
    return (
        f'<a href="data:text/html;charset=utf-8;base64,{body}" download="{escape(name)}">'
        "Download the report</a>"
    )


class JobRunner:
    """Runs one paper check with a time limit and deletes the uploaded file afterwards.

    A thread cannot be stopped. After the limit the person gets the message at once, and
    the run's thread finishes in the background and then cleans up after itself.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def run(
        self,
        job: Callable[..., T],
        *args: Any,
        upload: str | os.PathLike[str] | None = None,
        on_timeout: Callable[[], Exception],
    ) -> T:
        """``job(*args)``, then delete ``upload``."""

        def work() -> T:
            try:
                return job(*args)
            finally:
                if upload:
                    Path(upload).unlink(missing_ok=True)

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(contextvars.copy_context().run, work)
        pool.shutdown(wait=False)
        try:
            return future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            raise on_timeout() from None
