"""What the setup wizard remembers for the local app: the contact email and whether bibr
runs on this computer.

The file sits next to the saved bibr key and only this user can read it. It does not import
gradio. A hosted server never reads it.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson

from metacheck._env import env_lookup, env_names

__all__ = [
    "SELFHOST_URL",
    "apply",
    "load",
    "settings_path",
    "start_selfhost",
    "valid_email",
    "write",
]

#: where ``bibr serve`` listens when it runs on this computer
SELFHOST_URL = "http://127.0.0.1:8000"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def settings_path() -> Path:
    from metacheck.app.bibr import key_path

    return key_path().with_name("app-settings.json")


def valid_email(address: str) -> bool:
    return bool(_EMAIL.match(address))


def load() -> dict[str, Any]:
    """The saved settings (``{}`` when there are none or the file cannot be read)."""
    try:
        data = orjson.loads(settings_path().read_bytes())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(**values: Any) -> Path:
    """Merge *values* into the saved settings; a value of ``None`` removes the setting."""
    data = load()
    for name, value in values.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(orjson.dumps(data))
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def apply() -> dict[str, Any]:
    """Put the saved settings to work for this process; settings made in the environment win.

    Returns the saved settings.
    """
    from metacheck import config

    data = load()
    address = data.get("email")
    if isinstance(address, str) and valid_email(address) and config.email() is None:
        config.email(address)
    if data.get("bibr_selfhost") is True and env_lookup("BIBR_URL") is None:
        os.environ[env_names("BIBR_URL")[0]] = SELFHOST_URL
    return data


def _bibr_command() -> Path | None:
    """The ``bibr`` command installed beside this Python, or ``None``."""
    exe = Path(sys.executable).with_name("bibr.exe" if os.name == "nt" else "bibr")
    return exe if exe.is_file() else None


def start_selfhost() -> subprocess.Popen[bytes] | None:
    """Start ``bibr serve`` for this app when the setup chose it and nothing answers yet.

    Returns the process (the caller stops it) or ``None``. It never raises: without bibr the
    app says so when a PDF is read.
    """
    if load().get("bibr_selfhost") is not True:
        return None
    import httpx

    with contextlib.suppress(httpx.HTTPError):
        # trust_env=False: a proxy setting must not intercept a request to this computer
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            client.get(SELFHOST_URL + "/")
        return None  # something already answers there
    exe = _bibr_command()
    if exe is None:
        return None
    try:
        return subprocess.Popen(  # noqa: S603 - our own tool environment's command
            [str(exe), "serve", "--host", "127.0.0.1", "--port", "8000"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
