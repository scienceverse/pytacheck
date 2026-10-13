"""Small bounded fetch pools that inherit a run's cancellation and request memo."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import TypeVar

T = TypeVar("T")
U = TypeVar("U")


def map_ordered(func: Callable[[T], U], items: Sequence[T], workers: int = 4) -> list[U]:
    """Fetch concurrently, returning values (and raising errors) in input order."""
    if len(items) < 2:
        return [func(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = [pool.submit(copy_context().run, func, item) for item in items]
        return [future.result() for future in futures]
