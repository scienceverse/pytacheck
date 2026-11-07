"""On-disk cache of LLM replies.

:func:`metacheck.llm.llm` runs at temperature 0, so a call with the same
model, system prompt, text, schema and parameters is replayed from disk
instead of re-issued (and re-billed). Errors are never cached.

An entry is a JSON file named by the SHA-256 of the canonical JSON of what was
asked, in the folder ``json`` of the cache directory (R metacheck's ``.rds``
files in the directory above are not read, written or deleted). It holds the
model's reply as parsed (the text, or the JSON of a structured reply), so a
change in how replies become columns needs no new calls.

By default (the option ``metacheck.llm.cache`` unset) the replies last only for the
session, in a folder in the temporary directory that is removed when the process ends.
``llm_cache(True)`` keeps them between sessions; ``llm_cache(False)`` turns the cache off.

Lasting location: ``PYTACHECK_LLM_CACHE_DIR`` or ``METACHECK_LLM_CACHE_DIR`` if set,
else ``.metacheck_llm_cache`` under the metacheck cache root (the
``metacheck.cache.dir`` option, ``PYTACHECK_CACHE_DIR``, or the working
directory).
"""

from __future__ import annotations

import atexit
import contextlib
import datetime as dt
import hashlib
import json
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from metacheck._env import env_get

__all__ = ["llm_cache", "llm_cache_clear"]

_FOLDER = "json"
_VERSION = 1


def llm_cache(enabled: bool | None = None) -> bool:
    """Port of ``llm_cache()``: keep replies between sessions, store none, or query.

    ``True`` keeps the replies between sessions, ``False`` stores none; with ``None`` it
    returns whether they are kept between sessions (``False`` by default, when they last only
    for the session). ``options({"metacheck.llm.cache": None})`` returns to the default.
    """
    from metacheck.utils import get_option, options

    if enabled is None:
        return get_option("metacheck.llm.cache", False) is True
    if not isinstance(enabled, bool):
        raise ValueError("Set llm_cache with TRUE or FALSE")
    options({"metacheck.llm.cache": enabled})
    return enabled


def llm_cache_clear() -> int:
    """Port of ``llm_cache_clear()``: delete all cached replies; return how many."""
    files = [f for f in _entries_dir().glob("*.json") if f.is_file()]
    for f in files:
        f.unlink(missing_ok=True)
    return len(files)


def _llm_cache_enabled() -> bool:
    """``!isFALSE(getOption("metacheck.llm.cache"))``: replies are stored and replayed."""
    from metacheck.utils import get_option

    return get_option("metacheck.llm.cache", None) is not False


_session_dir: str | None = None


def _session_cache_dir() -> str:
    """The folder of this session's replies, in the temporary directory (removed at exit)."""
    global _session_dir
    if _session_dir is None or not Path(_session_dir).is_dir():
        _session_dir = tempfile.mkdtemp(prefix="metacheck-llm-cache-")
        atexit.register(shutil.rmtree, _session_dir, True)
    return _session_dir


def _llm_cache_dir() -> str:
    """Port of ``.llm_cache_dir()``: the LLM cache folder.

    A folder in the temporary directory unless ``llm_cache(True)`` keeps the replies between
    sessions; the lasting folder is created if needed.
    """
    if not llm_cache():
        return _session_cache_dir()
    override = env_get("LLM_CACHE_DIR") or ""
    try:
        from metacheck.archives.cache import _metacheck_cache_subdir
    except ImportError:  # pragma: no cover - archives port not available
        from metacheck.config import cache_dir

        return str(cache_dir(".metacheck_llm_cache", override=override or None))
    return _metacheck_cache_subdir(".metacheck_llm_cache", override=override or None)


def _entries_dir() -> Path:
    return Path(_llm_cache_dir()) / _FOLDER


def _llm_cache_key(
    text: str | None,
    system_prompt: Any,
    type: Any,
    model: str,
    params: Mapping[str, Any] | None,
    api_args: Mapping[str, Any] | None = None,
) -> str:
    """The key of one request: the SHA-256 of what it asks, as canonical JSON."""
    from metacheck.llm.types import as_type

    schema = None if type is None else as_type(type)
    payload = {
        "version": _VERSION,
        "text": text,
        "system_prompt": system_prompt,
        "model": model,
        "type": None if schema is None else {"schema": dict(schema), "raw": schema.raw},
        "params": dict(params or {}),
        "api_args": dict(api_args or {}),
    }
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _llm_cache_path(key: str) -> Path:
    """The file of a key."""
    return _entries_dir() / f"{key}.json"


def _llm_cache_get(key: str) -> dict[str, Any] | None:
    """The cached entry, or ``None`` on a miss or an unreadable file."""
    try:
        entry = json.loads(_llm_cache_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and "reply" in entry else None


def _llm_cache_put(key: str, reply: Any) -> None:
    """Write an entry (an error writing is ignored)."""
    entry = {"version": _VERSION, "created": dt.datetime.now(dt.UTC).isoformat(), "reply": reply}
    path = _llm_cache_path(key)
    with contextlib.suppress(OSError, TypeError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
