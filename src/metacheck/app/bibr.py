"""The bibr option: where the service is and how to talk to it, which key to use, how a
remembered key is kept.

It does not import gradio. Problems the person can fix are raised as ``UserError``.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import orjson

__all__ = [
    "BACKENDS",
    "BACKEND_ENV",
    "KEY_ENV",
    "URL_ENV",
    "bibr_service",
    "convert_pdf",
    "forget_key",
    "key_path",
    "load_key",
    "resolve_key",
    "save_key",
    "settings_problem",
    "show_key",
]

#: the environment variable R metacheck reads for the same key
KEY_ENV = "SCIVRS_API_KEY"
URL_ENV = "PYTACHECK_BIBR_URL"
#: how to talk to the service in ``URL_ENV``: one of ``BACKENDS``, ``bibr`` when it is not set
BACKEND_ENV = "PYTACHECK_BIBR_BACKEND"
#: ``bibr`` is the job API of bibr serve and of the hosted bibr service in front of it
#: (``/papers/jobs``); ``scivrs`` is the job queue of the Scienceverse platform (``/jobs``)
BACKENDS = ("bibr", "scivrs")
#: seconds to wait for metacheck's public server list
LIST_TIMEOUT = 5.0

NOT_FOUND = "The bibr service could not be found. Use GROBID for now."
NO_KEY = "Enter your bibr key, or use GROBID."
REFUSED = "The bibr service did not accept this key."
UNAVAILABLE = "The bibr service is not available right now. Use GROBID for now."
BAD_FORMAT = "The bibr service sent a format this version cannot read yet. Use GROBID for now."
BAD_PDF = "The bibr service could not read this PDF. Try another copy of the paper."
#: wrong settings: the person running the app can fix them (a hosted server does not start)
BAD_BACKEND = (
    f"{BACKEND_ENV} is either bibr (the default: bibr serve, or the hosted bibr service in "
    "front of it) or scivrs (the Scienceverse platform)."
)
BAD_URL = f"{URL_ENV} must be a full address, such as https://bibr.example.org."
PLAIN_HTTP = (
    f"{URL_ENV} must start with https://, so that the bibr key is not sent unencrypted. "
    "Plain http:// works for this computer only (localhost, 127.0.0.1 or ::1)."
)

_list_lock = threading.Lock()
#: the bibr entries of the public list, as (address, backend)
_servers: list[tuple[str, str]] | None = None


def key_path() -> Path:
    import platformdirs

    return Path(platformdirs.user_config_dir("pytacheck")) / "app-keys.json"


def load_key() -> str:
    """The remembered key, or an empty string."""
    try:
        data = orjson.loads(key_path().read_bytes())
    except (OSError, ValueError):
        return ""
    key = data.get("bibr") if isinstance(data, dict) else None
    return key if isinstance(key, str) else ""


def save_key(key: str) -> Path:
    """Remember the key in a file only this user can read."""
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(orjson.dumps({"bibr": key}))
    # Windows keeps the file in the user profile folder, which is already private
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def forget_key() -> None:
    with contextlib.suppress(OSError):
        key_path().unlink()


def show_key(key: str) -> str:
    """A key as it may be shown back: only its last four characters."""
    return f"…{key[-4:]}" if len(key) > 8 else "…"


def resolve_key(typed: str | None, *, remembered: bool = True) -> str:
    """The key to use: the environment's, else the typed one, else the remembered one.

    ``remembered=False`` never reads the saved key: a server shared by several people has none.
    """
    return (
        os.environ.get(KEY_ENV, "").strip()
        or (typed or "").strip()
        or (load_key() if remembered else "")
    )


def _entry_backend(entry: dict[str, Any]) -> str | None:
    """How to talk to a bibr entry of the public list: its ``protocol`` when it has one; else
    ``scivrs`` when its key is ``SCIVRS_API_KEY`` (the platform's), else ``bibr``. ``None``
    for a protocol this version does not know."""
    protocol = entry.get("protocol")
    if protocol:
        backend = str(protocol).strip().lower()
        return backend if backend in BACKENDS else None
    return "scivrs" if entry.get("api_key") == KEY_ENV else "bibr"


def _list_services() -> list[tuple[str, str]] | None:
    """The bibr entries of metacheck's public server list, as (address, backend); ``None``
    if it cannot be read. An entry this version cannot talk to is left out."""
    from metacheck.io.convert import _server_list

    try:
        servers = _server_list()
    except Exception:
        return None
    found = []
    for s in servers:
        if isinstance(s, dict) and s.get("service") == "bibr" and s.get("url"):
            backend = _entry_backend(s)
            if backend is not None:
                found.append((str(s["url"]).rstrip("/"), backend))
    return found


def _safe(url: str) -> bool:
    """A key goes over https, or to this computer."""
    parts = urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    )


def _configured() -> tuple[str, str] | None:
    """``PYTACHECK_BIBR_URL`` and the backend from ``PYTACHECK_BIBR_BACKEND``, or ``None``
    when no address is set. A wrong setting raises ``ValueError`` that says what to set."""
    backend = os.environ.get(BACKEND_ENV, "").strip().lower() or "bibr"
    if backend not in BACKENDS:
        raise ValueError(BAD_BACKEND)
    url = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not url:
        return None
    try:
        parts = urlsplit(url)
        usable = parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        usable = False
    if not usable:
        raise ValueError(BAD_URL)
    # the bibr backend refuses to send a key over plain http to another computer
    if backend == "bibr" and not _safe(url):
        raise ValueError(PLAIN_HTTP)
    return url, backend


def settings_problem() -> str | None:
    """What is wrong with ``PYTACHECK_BIBR_URL`` or ``PYTACHECK_BIBR_BACKEND``, or ``None``
    (a hosted server checks this before it starts)."""
    try:
        _configured()
    except ValueError as exc:
        return str(exc)
    return None


def bibr_service() -> tuple[str, str] | None:
    """Where the service is and how to talk to it, as (address, backend):
    ``PYTACHECK_BIBR_URL`` with ``PYTACHECK_BIBR_BACKEND``, else the first bibr entry in the
    public list that a key may go to (read once). A wrong setting raises ``ValueError``."""
    global _servers
    configured = _configured()
    if configured is not None:
        return configured
    with _list_lock:
        cached = _servers
    if cached is None:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            cached = pool.submit(_list_services).result(timeout=LIST_TIMEOUT)
        except FutureTimeout:
            cached = None
        finally:
            pool.shutdown(wait=False)
        if cached is None:
            return None  # not remembered: the next run asks again
        with _list_lock:
            _servers = cached
    return next((s for s in cached if _safe(s[0])), None)


def _status(exc: BaseException) -> int | None:
    match = re.match(r"HTTP (\d{3})", str(exc))
    return int(match.group(1)) if match else None


def convert_pdf(path: Path, workdir: Path, key: str) -> Path:
    """Turn a PDF into bibr JSON with the bibr service; the paper's file is in ``workdir``."""
    import httpx

    import metacheck as pc
    from metacheck.app.run import UserError

    if not key:
        raise UserError(NO_KEY)
    try:
        service = bibr_service()
    except ValueError as exc:
        raise UserError(str(exc)) from None
    if service is None:
        raise UserError(NOT_FOUND)
    url, backend = service
    try:
        out = pc.convert(
            path, save_path=workdir, method="bibr", api_url=url, api_key=key, backend=backend
        )
    except RuntimeError as exc:
        status = _status(exc)
        if status in (401, 403):
            raise UserError(REFUSED) from None
        if status == 404:
            raise UserError(NOT_FOUND) from None  # a wrong address
        if status is None or status == 429 or status >= 500:
            if status is None and str(exc).startswith("Job "):
                raise UserError(BAD_PDF) from None
            raise UserError(UNAVAILABLE) from None
        raise UserError(BAD_PDF) from None
    except (ConnectionError, TimeoutError, OSError, httpx.HTTPError):
        raise UserError(UNAVAILABLE) from None
    except Exception:
        raise UserError(BAD_PDF) from None
    if not out:
        raise UserError(BAD_PDF)
    return Path(str(out[0] if isinstance(out, list | tuple) else out))


def _forget_list() -> None:
    """For tests: read the server list again."""
    global _servers
    with _list_lock:
        _servers = None
