"""The bibr option: where the service is, which key to use, how a remembered key is kept.

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
from urllib.parse import urlsplit

import orjson

__all__ = [
    "KEY_ENV",
    "URL_ENV",
    "bibr_url",
    "convert_pdf",
    "forget_key",
    "key_path",
    "load_key",
    "resolve_key",
    "save_key",
    "show_key",
]

#: the environment variable R metacheck reads for the same key
KEY_ENV = "SCIVRS_API_KEY"
URL_ENV = "PYTACHECK_BIBR_URL"
#: seconds to wait for metacheck's public server list
LIST_TIMEOUT = 5.0

NOT_FOUND = "The bibr service could not be found. Use GROBID for now."
NO_KEY = "Enter your bibr key, or use GROBID."
REFUSED = "The bibr service did not accept this key."
UNAVAILABLE = "The bibr service is not available right now. Use GROBID for now."
BAD_FORMAT = "The bibr service sent a format this version cannot read yet. Use GROBID for now."
BAD_PDF = "The bibr service could not read this PDF. Try another copy of the paper."

_list_lock = threading.Lock()
_server_urls: list[str] | None = None


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


def _list_urls() -> list[str] | None:
    """The bibr addresses in metacheck's public server list; ``None`` if it cannot be read."""
    from pytacheck.io.convert import _server_list

    try:
        servers = _server_list()
    except Exception:
        return None
    return [
        str(s["url"]).rstrip("/")
        for s in servers
        if isinstance(s, dict) and s.get("service") == "bibr" and s.get("url")
    ]


def _safe(url: str) -> bool:
    """A key goes over https, or to this computer."""
    parts = urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    )


def bibr_url() -> str | None:
    """``PYTACHECK_BIBR_URL``, else the first bibr entry in the public list (read once)."""
    global _server_urls
    override = os.environ.get(URL_ENV, "").strip()
    if override:
        return override.rstrip("/")
    with _list_lock:
        cached = _server_urls
    if cached is None:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            cached = pool.submit(_list_urls).result(timeout=LIST_TIMEOUT)
        except FutureTimeout:
            cached = None
        finally:
            pool.shutdown(wait=False)
        if cached is None:
            return None  # not remembered: the next run asks again
        with _list_lock:
            _server_urls = cached
    return next((u for u in cached if _safe(u)), None)


def _status(exc: BaseException) -> int | None:
    match = re.match(r"HTTP (\d{3})", str(exc))
    return int(match.group(1)) if match else None


def convert_pdf(path: Path, workdir: Path, key: str) -> Path:
    """Turn a PDF into bibr JSON with the bibr service; the paper's file is in ``workdir``."""
    import httpx

    import pytacheck as pc
    from pytacheck.app.run import UserError

    if not key:
        raise UserError(NO_KEY)
    url = bibr_url()
    if url is None:
        raise UserError(NOT_FOUND)
    try:
        out = pc.convert(path, save_path=workdir, method="bibr", api_url=url, api_key=key)
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
    global _server_urls
    with _list_lock:
        _server_urls = None
