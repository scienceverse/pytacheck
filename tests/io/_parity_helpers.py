"""Small Python counterparts of base R helpers used to normalise io_review parity results."""

from __future__ import annotations

import os
from typing import Any


def basename(path: Any) -> Any:
    """R's ``basename()`` (vectorised; ``NA`` stays ``NA``)."""
    if path is None:
        return None
    if isinstance(path, str | os.PathLike):
        return os.path.basename(os.fspath(path))
    return [basename(p) for p in path]


def unname(obj: Any, force: bool = False) -> Any:
    """R's ``unname()``: the values of a named vector (a dict) as a plain list."""
    del force
    if obj is None:
        return None
    return list(obj.values()) if isinstance(obj, dict) else list(obj)


def names(x: Any) -> Any:
    """R's ``names()`` of a named vector (a dict)."""
    return None if x is None else [str(k) for k in x]
