"""The size limits of a data package, which the environment can change.

The limits on how much of a package is read (the bytes an archive may unpack to, its files,
its compression ratio, its folder depth) and on how much of a repository's download is
fetched are built into the code as defaults. Each one can be changed with a ``METACHECK_PACKAGE_*``
variable (the names are in :data:`metacheck._env.ENV_VARS`, and ``docs/ENVIRONMENT.md``
lists them). The variable is read when the limit is used, so a process started with it set
has it everywhere: the local app, the command line and the hosted app alike.

A value that is not a positive number is not used: a warning names the variable, and the
default stays. Sizes may be given in bytes or with a unit, ``512MB`` or ``20 GiB`` (units are
powers of 1024, as the limits themselves are).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Literal

from metacheck._env import env_lookup
from metacheck._logging import get_logger

__all__ = ["Kind", "setting"]

Kind = Literal["size", "int", "float"]

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?", re.IGNORECASE)
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def _parse(text: str, kind: Kind) -> int | float | None:
    """The value of *text*, or ``None`` when it is not a positive number of the *kind*."""
    cleaned = text.strip()
    if kind == "size":
        match = _SIZE.fullmatch(cleaned)
        if match is None:
            return None
        value = float(match.group(1)) * _UNITS[match.group(2).lower()]
        return int(value) if value >= 1 else None
    try:
        number: float = float(cleaned) if kind == "float" else int(cleaned)
    except ValueError:
        return None
    return number if number > 0 else None


def setting(
    key: str, default: Any, kind: Kind = "int", environ: Mapping[str, str] | None = None
) -> Any:
    """The limit *key* (a name of :data:`metacheck._env.ENV_VARS`), from the environment or *default*.

    *kind* says how the value is read: ``"size"`` (bytes, or a number with a unit), ``"int"``
    or ``"float"``. *environ* defaults to ``os.environ``.
    """
    env = os.environ if environ is None else environ
    hit = env_lookup(key, env)
    if hit is None:
        return default
    name, text = hit
    value = _parse(text, kind)
    if value is None:
        get_logger().warning("%s is not a positive %s, so the default is used", name, kind)
        return default
    return value
