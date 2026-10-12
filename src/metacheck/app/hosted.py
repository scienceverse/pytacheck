"""Hosted mode: settings from the environment, run limits, clean-up and the extra texts.

Nothing here imports gradio; ``ui`` and ``server`` use it.
"""

from __future__ import annotations

import base64
import contextlib
import multiprocessing
import os
import pickle
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from metacheck._env import env_name

__all__ = [
    "DEFAULT_PORT",
    "HOSTED_NOTE",
    "MAX_QUEUE",
    "MAX_RUNS",
    "TOO_SLOW",
    "HostedConfig",
    "HostedError",
    "JobRunner",
    "data_link",
    "footer",
    "sweep",
]

T = TypeVar("T")

TOKENS_ENV = env_name("APP_TOKENS")
AUTH_ENV = env_name("APP_AUTH")
USER_HEADER_ENV = env_name("APP_USER_HEADER")
HOSTS_ENV = env_name("APP_HOSTS")
SPACE_HOST_ENV = "SPACE_HOST"  # set by Hugging Face Spaces
TIMEOUT_ENV = env_name("APP_JOB_TIMEOUT")
COMMIT_ENV = env_name("APP_COMMIT")
PORT_ENV = "PORT"
DEFAULT_PORT = 7860
DEFAULT_TIMEOUT = 900.0
MIN_TOKEN_LENGTH = 32
#: runs at the same time, and paper checks waiting for a free place
MAX_RUNS = 2
MAX_QUEUE = 10
#: how a run's process is started; tests use "fork" to carry their patches over
START_METHOD = "spawn"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,}")
HEADER_PATTERN = re.compile(r"[A-Za-z0-9-]+")

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
    #: a sign-in proxy in front of the app lets people in, so there are no tokens
    proxy_auth: bool = False
    #: in proxy mode, a header the proxy sets on every request it lets through
    user_header: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HostedConfig:
        env = os.environ if env is None else env
        auth = (env.get(AUTH_ENV) or "").strip().lower()
        if auth not in ("", "tokens", "proxy"):
            raise HostedError(f"{AUTH_ENV} is either tokens (the default) or proxy.")
        proxy_auth = auth == "proxy"
        tokens = _items(env.get(TOKENS_ENV))
        if proxy_auth and tokens:
            raise HostedError(
                f"{TOKENS_ENV} is not used when {AUTH_ENV} is proxy. Remove one of the two."
            )
        if not tokens and not proxy_auth:
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
            if not TOKEN_PATTERN.fullmatch(token):
                raise HostedError(
                    f"Token {number} in {TOKENS_ENV} has characters that do not survive in a "
                    "link. Use only letters, digits, - and _, for example: openssl rand -hex 24"
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
        user_header = (env.get(USER_HEADER_ENV) or "").strip() or None
        if user_header and not proxy_auth:
            raise HostedError(f"{USER_HEADER_ENV} only works when {AUTH_ENV} is proxy.")
        if user_header and not HEADER_PATTERN.fullmatch(user_header):
            raise HostedError(
                f"{USER_HEADER_ENV} takes one header name, for example X-Forwarded-User."
            )
        return cls(tuple(tokens), tuple(hosts), timeout, commit, proxy_auth, user_header)


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


def sweep(job_timeout: float) -> tuple[int, int]:
    """Gradio's ``delete_cache``: [seconds between sweeps, age in seconds].

    An upload can wait in the queue behind every place before it, so its age is well above
    the longest wait plus the run. The runner deletes each upload after its run anyway.
    """
    return (60, int(job_timeout * (MAX_QUEUE // MAX_RUNS + 1)) + 60)


def remove_upload(path: str | os.PathLike[str]) -> None:
    """Delete an upload, and its folder (named after the file's hash) when that is empty."""
    upload = Path(path)
    upload.unlink(missing_ok=True)
    root = os.environ.get("GRADIO_TEMP_DIR")
    folder = upload.parent.resolve()
    if root and folder != Path(root).resolve() and folder.is_relative_to(Path(root).resolve()):
        with contextlib.suppress(OSError):
            folder.rmdir()


def _child(conn: Any, job: Callable[..., Any], args: tuple[Any, ...], progress: bool) -> None:
    """The process of one run: send progress, then the result or the error."""

    def report(fraction: float, text: str) -> None:
        conn.send(("progress", fraction, text))

    try:
        try:
            answer: tuple[Any, ...] = ("done", job(*args, report) if progress else job(*args))
            conn.send(answer)
        except Exception as exc:
            try:
                pickle.loads(pickle.dumps(exc))  # noqa: S301 - our own error, to see it comes out again
                conn.send(("error", exc))
            except Exception:  # the error cannot be sent as it is
                conn.send(("error", RuntimeError(str(exc))))
    finally:
        conn.close()


class JobRunner:
    """Runs one paper check in its own process, with a time limit.

    At the limit the process is killed, so a check that hangs frees its place at once and
    cannot hold up the next runs. The uploaded file is deleted when the run ends.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def run(
        self,
        job: Callable[..., T],
        *args: Any,
        upload: str | os.PathLike[str] | None = None,
        on_timeout: Callable[[], Exception],
        on_progress: Callable[[float, str], None] | None = None,
    ) -> T:
        """``job(*args)`` (plus a progress function as last argument if ``on_progress`` is
        given), then delete ``upload``. ``job`` and its arguments must be picklable."""
        context: Any = multiprocessing.get_context(START_METHOD)
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_child, args=(send, job, args, on_progress is not None), daemon=True
        )
        try:
            process.start()
            send.close()  # so that the end of the child shows as end of file
            deadline = time.monotonic() + self.timeout
            while True:
                if not receive.poll(max(deadline - time.monotonic(), 0)):
                    raise on_timeout() from None
                try:
                    message = receive.recv()
                except EOFError:
                    raise RuntimeError("The check ended without an answer") from None
                if message[0] == "progress":
                    if on_progress:
                        on_progress(message[1], message[2])
                elif message[0] == "error":
                    raise message[1]
                else:
                    return cast(T, message[1])
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                    if process.is_alive():
                        process.kill()
                process.join()
            receive.close()
            send.close()
            if upload:
                remove_upload(upload)
