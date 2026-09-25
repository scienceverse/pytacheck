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

import pytacheck.llm as L
from pytacheck.llm import cache as C
from pytacheck.llm import core as K
from pytacheck.llm import providers as P
from pytacheck.llm import types as T
from pytacheck.llm._rds import RInt
from pytacheck.llm.providers import LLMError

__all__ = [
    "LLM_ON",
    "C",
    "K",
    "L",
    "LLMError",
    "P",
    "RInt",
    "T",
    "catch",
    "identity",
    "ollama_no_content",
    "ollama_reply",
    "resp",
    "scoped",
]

#: llm_use(TRUE) with the response cache off.
LLM_ON = {"metacheck.llm.use": True, "metacheck.llm.cache": False}


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x


def catch(fn: Callable[[], Any]) -> dict[str, Any]:
    """``fn()`` with its error and warning texts, for comparing them with R.

    The R side of such a case is ``tryCatch(withCallingHandlers(...))``
    returning ``list(value = <value or list(error = msg)>, warnings = msgs)``.
    Only ``UserWarning``\\ s (R ``warning()``) are recorded.
    """
    import warnings

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        try:
            value = fn()
        except Exception as e:
            value = {"error": str(e.args[0]) if len(e.args) == 1 else str(e)}
    return {
        "value": value,
        "warnings": [str(w.message) for w in ws if issubclass(w.category, UserWarning)],
    }


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
    from pytacheck.utils import local_options

    opts = dict(options or {})
    with local_options(opts), _envvars(env or {}):
        for k, v in opts.items():
            if v is None:
                from pytacheck.utils import options as set_options

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
    from pytacheck._r import r_sorted

    return r_sorted(os.listdir(d))  # type: ignore[no-any-return]


def resp(status: int, json: Any = None, text: str | None = None) -> Any:
    """An httr2-like response for error-message cases."""
    import httpx

    if json is not None:
        return httpx.Response(status, json=json)
    return httpx.Response(
        status, content=(text or "").encode(), headers={"content-type": "text/plain"}
    )


def cond(
    message: str,
    status: int | None = None,
    json: Any = None,
    text: str | None = None,
    wrap: str | None = None,
    timeout: bool = False,
) -> LLMError:
    """An R condition as metacheck sees it: message, ``$resp``, ``$parent``, timeout class."""
    r = resp(status, json, text) if status is not None else None
    e = LLMError(message, resp=r, timeout=timeout)
    if wrap is not None:
        return LLMError(wrap, parent=e)
    return e


def ollama_reply(body: Any, fn: Callable[[], Any]) -> Any:
    """``fn()`` with every Ollama ``/api/chat`` request answered by *body*.

    R: ``httr2::with_mocked_responses(function(req) httr2::response_json(body = body), ...)``.
    An R value such as ``character(0)`` comes back in its Python form (``[]``).
    """
    import httpx
    import respx

    from pytacheck.llm._rds import RVec, to_python

    with respx.mock(assert_all_called=False) as router:
        router.post(url__regex=r"/api/chat$").mock(return_value=httpx.Response(200, json=body))
        value = fn()
    return to_python(value) if isinstance(value, RVec) else value


def ollama_no_content(fn: Callable[[], Any], empty: tuple[str, ...] = ("B", "C")) -> Any:
    """``fn()`` with ``.llm_ollama_native()`` replying without ``message.content``
    for the texts in *empty* and ``"ok"`` otherwise.

    R: ``testthat::with_mocked_bindings(..., .llm_ollama_native = function(text, ...)
    if (text %in% c('B', 'C')) trimws(NULL) else 'ok', .package = 'metacheck')``.
    metacheck's function returns ``character(0)`` for such a reply; pytacheck's
    raises (U19), which ``llm()`` records as that text's error.
    """
    from unittest import mock

    def stub(text: str | None, *args: Any, **kwargs: Any) -> Any:
        if text in empty:
            raise RuntimeError("The Ollama reply has no message content.")
        return "ok"

    with mock.patch.object(K, "_llm_ollama_native", stub):
        return fn()
