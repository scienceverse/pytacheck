"""LLM access (port of metacheck's ``R/llm.R``, ``R/llm-cache.R``, ``R/cap-prompt.R``).

* :func:`llm` -- query a model for each text (free text or structured data);
* settings: :func:`llm_use`, :func:`llm_model`, :func:`llm_max_calls`,
  :func:`llm_max_tokens`, :func:`llm_timeout`, :func:`llm_reasoning`;
* :func:`llm_cache` / :func:`llm_cache_clear` -- the on-disk response cache (JSON
  files; metacheck's ``.rds`` entries are not used);
* structured-output types: :func:`type_object`, :func:`type_array`,
  :func:`type_string`, :func:`type_integer`, :func:`type_number`,
  :func:`type_boolean`, :func:`type_enum`, :func:`type_from_schema`;
* :func:`cap_report` -- report a skipped unit of work.

Submodules are imported lazily, so ``import metacheck.llm`` is cheap; the
provider SDKs (``pip install "metacheck[llm]"``) are imported only when a model
is first called.
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
    "llm_cache": "cache",
    "llm_cache_clear": "cache",
    "cap_report": "cap_prompt",
    "LLMError": "_backend",
    "Schema": "types",
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
        raise AttributeError(f"module 'metacheck.llm' has no attribute {name!r}")
    value = getattr(importlib.import_module(f"metacheck.llm.{mod}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


from metacheck._callable import callable_module  # noqa: E402

# ``pc.llm(...)`` must keep working after ``import metacheck.llm`` shadows it.
callable_module(__name__, "llm")
