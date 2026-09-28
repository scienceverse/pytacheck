"""LLM access (port of metacheck's ``R/llm.R``, ``R/llm-cache.R``, ``R/cap-prompt.R``).

* :func:`llm` -- query a model for each text (free text or structured data);
* settings: :func:`llm_use`, :func:`llm_model`, :func:`llm_max_calls`,
  :func:`llm_max_tokens`, :func:`llm_timeout`, :func:`llm_reasoning`;
* :func:`llm_model_list` -- models available on each provider;
* :func:`llm_cache` / :func:`llm_cache_clear` -- the on-disk response cache
  (shared with metacheck);
* structured-output types: :func:`type_object`, :func:`type_array`,
  :func:`type_string`, :func:`type_integer`, :func:`type_number`,
  :func:`type_boolean`, :func:`type_enum`, :func:`type_from_schema`;
* :func:`cap_report` -- report a skipped unit of work.

Submodules are imported lazily, so ``import pytacheck.llm`` is cheap.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "llm": "core",
    "llm_use": "core",
    "llm_model": "core",
    "llm_max_calls": "core",
    "llm_max_tokens": "core",
    "llm_timeout": "core",
    "llm_reasoning": "core",
    "llm_model_list": "core",
    "llm_cache": "cache",
    "llm_cache_clear": "cache",
    "cap_report": "cap_prompt",
    "chat": "providers",
    "params": "providers",
    "LLMError": "providers",
    "Type": "types",
    "type_object": "types",
    "type_array": "types",
    "type_string": "types",
    "type_integer": "types",
    "type_number": "types",
    "type_boolean": "types",
    "type_enum": "types",
    "type_from_schema": "types",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module 'pytacheck.llm' has no attribute {name!r}")
    value = getattr(importlib.import_module(f"pytacheck.llm.{mod}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


from pytacheck._callable import callable_module  # noqa: E402

# ``pc.llm(...)`` must keep working after ``import pytacheck.llm`` shadows it.
callable_module(__name__, "llm")
