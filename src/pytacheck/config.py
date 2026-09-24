"""Package-wide settings: contact email, verbosity and cache locations.

Settings come from (highest priority first) values set at runtime with the
functions below, then environment variables, then defaults. Environment
variable names follow metacheck where one exists so the same shell setup
works for both packages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import platformdirs

__all__ = ["cache_dir", "email", "verbose"]

_state: dict[str, object] = {}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email(address: str | None = None) -> str | None:
    """Get or set the contact email sent to polite APIs (Crossref, OpenAlex).

    Reads ``PYTACHECK_EMAIL`` or ``METACHECK_EMAIL`` when unset.
    """
    if address is not None:
        if not _EMAIL_RE.match(address):
            raise ValueError(f"{address!r} does not look like an email address")
        _state["email"] = address
        return address
    value = (
        _state.get("email")
        or os.environ.get("PYTACHECK_EMAIL")
        or os.environ.get("METACHECK_EMAIL")
    )
    return str(value) if value else None


def verbose(value: bool | None = None) -> bool:
    """Get or set whether progress messages are printed (default ``True``)."""
    if value is not None:
        _state["verbose"] = bool(value)
    if "verbose" in _state:
        return bool(_state["verbose"])
    env = os.environ.get("PYTACHECK_VERBOSE")
    return env.lower() not in ("0", "false", "no") if env else True


def cache_dir(subdir: str = "", override: str | os.PathLike[str] | None = None) -> Path:
    """A cache directory (created on demand).

    Root: ``PYTACHECK_CACHE_DIR`` or the platform user cache directory.
    """
    if override is not None:
        path = Path(override)
    else:
        root = os.environ.get("PYTACHECK_CACHE_DIR") or platformdirs.user_cache_dir(
            "pytacheck", "scienceverse"
        )
        path = Path(root) / subdir if subdir else Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path
