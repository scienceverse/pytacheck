"""File information: naming checks and file category/type classification.

Ports of ``R/file-naming.R`` (:mod:`metacheck.fileinfo.naming`),
``R/file_category.R`` (:mod:`metacheck.fileinfo.category`) and the
``metacheck::file_types`` data (:mod:`metacheck.fileinfo.types`).
"""

from __future__ import annotations

from typing import Any

__all__ = ["check_file_naming", "file_category", "file_types", "filetype"]

_LAZY = {
    "check_file_naming": "metacheck.fileinfo.naming",
    "file_category": "metacheck.fileinfo.category",
    "filetype": "metacheck.fileinfo.category",
    "file_types": "metacheck.fileinfo.types",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
