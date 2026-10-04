"""Python side of the llm parity cases (``parity/cases/llm.yaml``).

LLM settings live in global options and API keys in environment variables,
so every case runs its call inside :func:`scoped` (R: ``withr::with_options()``
/ ``withr::with_envvar()``) and leaves no state behind for other areas.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import metacheck.llm as L
from metacheck.llm import cache as C
from metacheck.llm import core as K
from metacheck.llm import types as T
from metacheck.llm._backend import LLMError, _body_detail

__all__ = [
    "LLM_ON",
    "C",
    "K",
    "L",
    "LLMError",
    "T",
    "identity",
    "scoped",
]

#: llm_use(TRUE) with the response cache off.
LLM_ON = {"metacheck.llm.use": True, "metacheck.llm.cache": False}


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x


@contextlib.contextmanager
def _envvars(env: Mapping[str, str | None]) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def scoped(
    fn: Callable[[], Any],
    options: Mapping[str, Any] | None = None,
    env: Mapping[str, str | None] | None = None,
) -> Any:
    """Run ``fn()`` with R options and environment variables temporarily set.

    A ``None`` option value unsets the option (R ``options(x = NULL)``).
    """
    from metacheck.utils import local_options

    opts = dict(options or {})
    with local_options(opts), _envvars(env or {}):
        for k, v in opts.items():
            if v is None:
                from metacheck.utils import options as set_options

                set_options({k: None})
        return fn()


def with_cache_dir(fn: Callable[[str], Any]) -> Any:
    """Run ``fn(dir)`` with the LLM cache in a fresh temporary directory."""
    with (
        tempfile.TemporaryDirectory() as d,
        _envvars({"METACHECK_LLM_CACHE_DIR": d, "PYTACHECK_LLM_CACHE_DIR": None}),
    ):
        return fn(d)


def cache_files(d: str) -> list[str]:
    """``sort(list.files(d))``."""
    from metacheck._r import r_sorted

    return r_sorted(os.listdir(d))  # type: ignore[no-any-return]


def cond(
    message: str,
    status: int | None = None,
    json: Any = None,
    text: str | None = None,
    wrap: str | None = None,
    timeout: bool = False,
) -> LLMError:
    """An error as the SDK layer reports it: message, status, the provider's reason, timeout.

    The reason is read from the body *json* (or *text*) as a failed request's body is read.
    *wrap* is the message of an error that wraps the failed request's.
    """
    import json as _json

    detail = None
    if status is not None:
        detail = _body_detail(
            _json.dumps(json, separators=(",", ":")) if json is not None else (text or "")
        )[0]
    return LLMError(
        message if wrap is None else wrap,
        status=status,
        detail=detail,
        timeout=timeout,
        transport=timeout,
    )
