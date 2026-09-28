"""Shared state for the API concurrency test (imported by a test pack's module)."""

from __future__ import annotations

import threading
import time

lock = threading.Lock()
state = {"running": 0, "max": 0, "calls": 0}


def reset() -> None:
    with lock:
        state.update(running=0, max=0, calls=0)


def slow(seconds: float = 0.2) -> None:
    """Hold a check open for *seconds*, recording how many run at once."""
    with lock:
        state["running"] += 1
        state["calls"] += 1
        state["max"] = max(state["max"], state["running"])
    try:
        time.sleep(seconds)
    finally:
        with lock:
            state["running"] -= 1
