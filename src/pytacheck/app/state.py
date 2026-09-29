"""The state file that lets a second ``metacheck-app`` reuse a running app."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

import orjson

__all__ = ["State", "find_running", "read_state", "remove_state", "state_path", "write_state"]

State = dict[str, Any]

#: seconds to wait for a running app to answer
PROBE_TIMEOUT = 2.0


def state_path() -> Path:
    import platformdirs

    return Path(platformdirs.user_state_dir("pytacheck")) / "app.json"


def write_state(port: int, token: str, pid: int | None = None) -> Path:
    """Write ``{"port", "token", "pid"}`` readable by this user only."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = orjson.dumps({"port": port, "token": token, "pid": os.getpid() if pid is None else pid})
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return path


def read_state() -> State | None:
    try:
        data = orjson.loads(state_path().read_bytes())
    except (OSError, ValueError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("port"), int)
        and isinstance(data.get("token"), str)
        and isinstance(data.get("pid"), int)
    ):
        return data
    return None


def remove_state(port: int) -> None:
    """Delete the state file if it still describes this process's app."""
    state = read_state()
    if state and state["pid"] == os.getpid() and state["port"] == port:
        with contextlib.suppress(OSError):
            state_path().unlink()


def find_running() -> State | None:
    """The saved state when an app answers there with the saved token, else ``None``."""
    import httpx

    state = read_state()
    if state is None:
        return None
    try:
        # trust_env=False: a proxy setting must not intercept a request to this computer
        with httpx.Client(trust_env=False, timeout=PROBE_TIMEOUT) as client:
            resp = client.get(
                f"http://127.0.0.1:{state['port']}/", params={"token": state["token"]}
            )
    except httpx.HTTPError:
        return None
    return state if resp.status_code == 303 and resp.cookies else None
