"""On-disk cache of LLM responses (port of ``R/llm-cache.R``).

:func:`pytacheck.llm.llm` runs at temperature 0, so a call with the same
model, system prompt, text, type spec and parameters is replayed from disk
instead of re-issued (and re-billed). Errors are never cached.

The cache is **shared with metacheck**: entries are ``.rds`` files named by
the same key R computes (the MD5 of ``serialize(list(text, system_prompt,
model, type, params), ascii = TRUE)``, reproduced by
:mod:`pytacheck.llm._rds`, with the type spec rendered as R prints it), and
hold the same ``list(df, raw, thinking, created, version)``. Point R's
``METACHECK_LLM_CACHE_DIR`` and pytacheck at one directory and each reuses
the other's responses. Keys embed the R version in the serialization header;
pytacheck writes R 4.5.3's (set ``PYTACHECK_R_SERIALIZE_VERSION`` to match
another R release).

Location: ``PYTACHECK_LLM_CACHE_DIR`` or ``METACHECK_LLM_CACHE_DIR`` if set,
else ``.metacheck_llm_cache`` under the metacheck cache root (the
``metacheck.cache.dir`` option, ``PYTACHECK_CACHE_DIR``, or the working
directory).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["llm_cache", "llm_cache_clear"]


def llm_cache(enabled: bool | None = None) -> bool:
    """Port of ``llm_cache()``: enable, disable or query the LLM response cache."""
    from pytacheck.utils import get_option, options

    if enabled is None:
        return get_option("metacheck.llm.cache", True) is True
    if not isinstance(enabled, bool):
        raise ValueError("Set llm_cache with TRUE or FALSE")
    options({"metacheck.llm.cache": enabled})
    return enabled


def llm_cache_clear() -> int:
    """Port of ``llm_cache_clear()``: delete all cached responses; return how many."""
    d = Path(_llm_cache_dir())
    files = [f for f in d.iterdir() if f.is_file() and f.name.endswith(".rds")]
    for f in files:
        f.unlink(missing_ok=True)
    return len(files)


def _llm_cache_dir() -> str:
    """Port of ``.llm_cache_dir()``: the LLM cache folder (created if needed)."""
    override = os.environ.get("PYTACHECK_LLM_CACHE_DIR", "") or os.environ.get(
        "METACHECK_LLM_CACHE_DIR", ""
    )
    try:
        from pytacheck.archives.cache import _metacheck_cache_subdir
    except ImportError:  # pragma: no cover - archives port not available
        from pytacheck.config import cache_dir

        return str(cache_dir(".metacheck_llm_cache", override=override or None))
    return _metacheck_cache_subdir(".metacheck_llm_cache", override=override or None)


def _param_robj(v: Any) -> Any:
    """A parameter value as R holds it (``ellmer::params()`` output)."""
    from pytacheck.llm._rds import RInt, RList, RVec

    if isinstance(v, Mapping):
        return RList(
            [_param_robj(x) for x in v.values()], {"names": RVec("chr", [str(k) for k in v])}
        )
    if isinstance(v, list | tuple):
        if v and all(isinstance(x, str) for x in v):
            return RVec("chr", list(v))
        if v and all(isinstance(x, bool) for x in v):
            return RVec("lgl", list(v))
        if v and all(isinstance(x, RInt) for x in v):
            return RVec("int", [int(x) for x in v])
        if v and all(isinstance(x, int | float) and not isinstance(x, bool) for x in v):
            return RVec("dbl", [float(x) for x in v])
        return RList([_param_robj(x) for x in v])
    return v


def _chr(x: Any) -> Any:
    from pytacheck.llm._rds import RVec

    if x is None:
        return RVec("chr", [None])
    if isinstance(x, list | tuple):
        return RVec("chr", [None if v is None else str(v) for v in x])
    return RVec("chr", [str(x)])


def _llm_cache_key(
    text: str | None,
    system_prompt: Any,
    type: Any,
    model: str,
    params: Mapping[str, Any] | None,
) -> str:
    """Port of ``.llm_cache_key()``: the MD5 key R computes for these inputs.

    Parameters are sorted by name; the type spec is reduced to its printed
    form (``capture.output(print(type))``) so any schema change misses.
    """
    from pytacheck.llm._rds import RList, RVec, serialize
    from pytacheck.llm.types import as_type, type_print_lines

    p: Any
    if params:
        names = sorted(params)
        p = RList([_param_robj(params[k]) for k in names], {"names": RVec("chr", names)})
    else:
        p = RList([])
    payload = {
        "text": _chr(text),
        "system_prompt": _chr(system_prompt),
        "model": _chr(model),
        "type": None if type is None else RVec("chr", type_print_lines(as_type(type))),
        "params": p,
    }
    raw = serialize(payload, ascii=True)
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def _llm_cache_path(key: str) -> str:
    """Port of ``.llm_cache_path()``: the ``.rds`` file for a key."""
    return os.path.join(_llm_cache_dir(), f"{key}.rds")


def _llm_cache_get(key: str) -> dict[str, Any] | None:
    """Port of ``.llm_cache_get()``: the cached entry, or ``None`` on a miss/unreadable file.

    ``df`` is a data frame (structured calls) or ``{"answer": ...}``.
    """
    from pytacheck.llm._rds import read_rds, to_python

    path = _llm_cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        entry = to_python(read_rds(path))
    except Exception:
        return None
    return entry if isinstance(entry, dict) else None


def _llm_cache_put(key: str, df: Any, raw: Any = None, thinking: Any = None) -> dict[str, Any]:
    """Port of ``.llm_cache_put()``: write an entry (errors writing are ignored)."""
    from pytacheck.llm._rds import RInt, RVec, write_rds

    stored_df = df
    if isinstance(df, dict) and set(df) == {"answer"} and isinstance(df["answer"], str):
        # chat$chat() returns an "ellmer_output"; trimws() keeps the class
        stored_df = {
            "answer": RVec("chr", [df["answer"]], {"class": RVec("chr", ["ellmer_output"])})
        }
    entry = {
        "df": stored_df,
        "raw": raw,
        "thinking": thinking,
        "created": dt.datetime.now(dt.UTC),
        "version": RInt(1),
    }
    try:
        write_rds(entry, _llm_cache_path(key))
    except Exception:
        pass
    return {**entry, "df": df}
