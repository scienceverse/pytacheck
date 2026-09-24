"""metacheck's JSON error log (``logger()``, ``lastlog()``, ``logpath()``).

Entries are also emitted on the standard :mod:`logging` logger
``"pytacheck"``. The on-disk log is JSON Lines (newest last), capped at
1000 entries, in the user data directory.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson
import platformdirs

__all__ = ["lastlog", "logger", "logpath"]

_LOG = logging.getLogger("pytacheck")
_LOCK = threading.Lock()
_MAX_ENTRIES = 1000


def logpath() -> Path:
    """Path of the log file (created if missing). Override with ``PYTACHECK_LOG``."""
    override = os.environ.get("PYTACHECK_LOG")
    path = (
        Path(override)
        if override
        else Path(platformdirs.user_data_dir("pytacheck", "scienceverse"))
        / "log"
        / "pytacheck.log.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


def _jsonable(value: Any) -> Any:
    try:
        orjson.dumps(value)
        return value
    except TypeError:
        return repr(value)


def logger(label: str = "", contents: Any = None, path: str | Path | None = None) -> Path:
    """Record an event; returns the log path."""
    if contents is None:
        contents = {}
    if not isinstance(contents, dict):
        contents = {"error": contents}
    entry = {
        "label": label,
        "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **{k: _jsonable(v) for k, v in contents.items()},
    }
    _LOG.debug("%s: %s", label, contents)
    target = Path(path) if path else logpath()
    line = orjson.dumps(entry) + b"\n"
    with _LOCK:
        try:
            with target.open("ab") as fh:
                fh.write(line)
            if target.stat().st_size > 4_000_000:
                lines = target.read_bytes().splitlines(keepends=True)[-_MAX_ENTRIES:]
                target.write_bytes(b"".join(lines))
        except OSError:  # pragma: no cover - read-only home etc.
            pass
    return target


def lastlog(i: int | list[int] = 1, path: str | Path | None = None) -> Any:
    """The *i*-th most recent log entries (1 = newest), like ``lastlog()``."""
    target = Path(path) if path else logpath()
    try:
        lines = target.read_bytes().splitlines()
    except OSError:
        return None
    entries = [orjson.loads(line) for line in reversed(lines) if line.strip()]
    if not entries:
        return None
    idx = [i] if isinstance(i, int) else list(i)
    picked = [entries[k - 1] for k in idx if 1 <= k <= len(entries)]
    if len(picked) == 1:
        return picked[0]
    import pandas as pd

    return pd.DataFrame(picked)
