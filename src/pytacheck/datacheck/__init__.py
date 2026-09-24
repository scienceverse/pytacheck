"""data_check helpers (port of ``R/data_check_helpers.R`` and friends).

* :mod:`pytacheck.datacheck.files` -- file classification, formats, manifests,
  study grouping, text sniffing and reading data files (``data_read_head()``);
* :mod:`pytacheck.datacheck.columns` -- column typing, codebooks and labels;
* :mod:`pytacheck.datacheck.checks` -- data-quality checks.

Public names are resolved lazily so importing the package stays cheap.
"""

from __future__ import annotations

import importlib
from typing import Any

# name -> submodule that defines it
_EXPORTS: dict[str, str] = {
    "data_classify_files": "pytacheck.datacheck.files",
    "data_format": "pytacheck.datacheck.files",
    "data_group_llm": "pytacheck.datacheck.files",
    "data_is_manifest": "pytacheck.datacheck.files",
    "data_read_head": "pytacheck.datacheck.files",
    "data_study_roster": "pytacheck.datacheck.files",
    "manifest_merge": "pytacheck.datacheck.files",
    "rscript_path": "pytacheck.datacheck.files",
    "text_peek": "pytacheck.datacheck.files",
    "txt_classify_content": "pytacheck.datacheck.files",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'pytacheck.datacheck' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
