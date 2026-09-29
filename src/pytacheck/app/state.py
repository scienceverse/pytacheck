"""The state file that lets a second ``metacheck-app`` reuse a running app."""

from __future__ import annotations

import contextlib
import os
import sys
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


def _alive(pid: int) -> bool:
    """Whether a process with this id runs."""
    if sys.platform == "win32":
        # os.kill(pid, 0) ends the process on Windows, so ask the kernel instead
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it runs, as another user
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # exists, but belongs to someone else
        return True
    return True


def _drop() -> None:
    with contextlib.suppress(OSError):
        state_path().unlink()


def find_running() -> State | None:
    """The saved state when our app runs there with the saved token, else ``None``.

    The saved process must be alive, and the answer must set exactly this app's cookie
    with the saved token: another program on the port must not receive the browser.
    A file that fails either test is removed.
    """
    import httpx

    from pytacheck.app.security import cookie_name

    state = read_state()
    if state is None:
        return None
    if not _alive(state["pid"]):
        _drop()
        return None
    try:
        # trust_env=False: a proxy setting must not intercept a request to this computer
        with httpx.Client(trust_env=False, timeout=PROBE_TIMEOUT) as client:
            resp = client.get(
                f"http://127.0.0.1:{state['port']}/", params={"token": state["token"]}
            )
    except httpx.HTTPError:
        return None
    if resp.status_code == 303 and resp.cookies.get(cookie_name(state["port"])) == state["token"]:
        return state
    _drop()
    return None
