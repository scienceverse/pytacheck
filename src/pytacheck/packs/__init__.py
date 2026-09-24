"""Packs: shareable folders of modules and presets (module system v2).

Submodules are imported lazily, and ``import pytacheck`` never imports this
package: built-in modules resolve without it.

* :mod:`pytacheck.packs.manifest` -- ``pack.json`` validation and :class:`Pack`;
* :mod:`pytacheck.packs.tree` -- the ``pytacheck-tree-v1`` hash;
* :mod:`pytacheck.packs.registry` -- the active packs and module loading.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "FIELDS": "pytacheck.packs.manifest",
    "Pack": "pytacheck.packs.manifest",
    "PackError": "pytacheck.packs.manifest",
    "read_manifest": "pytacheck.packs.manifest",
    "validate_manifest": "pytacheck.packs.manifest",
    "validate_pack_name": "pytacheck.packs.manifest",
    "validate_preset": "pytacheck.packs.manifest",
    "validation_metrics": "pytacheck.packs.manifest",
    "file_sha256": "pytacheck.packs.tree",
    "tree_files": "pytacheck.packs.tree",
    "tree_sha256": "pytacheck.packs.tree",
    "Registry": "pytacheck.packs.registry",
    "active_packs": "pytacheck.packs.registry",
    "builtin_pack": "pytacheck.packs.registry",
    "find_module": "pytacheck.packs.registry",
    "get_pack": "pytacheck.packs.registry",
    "install_dir": "pytacheck.packs.registry",
    "integrity": "pytacheck.packs.registry",
    "load_module": "pytacheck.packs.registry",
    "pack_for_spec": "pytacheck.packs.registry",
    "pin_rev12": "pytacheck.packs.registry",
    "refresh": "pytacheck.packs.registry",
    "registry": "pytacheck.packs.registry",
}

__all__ = [
    "FIELDS",
    "Pack",
    "PackError",
    "Registry",
    "active_packs",
    "builtin_pack",
    "file_sha256",
    "find_module",
    "get_pack",
    "install_dir",
    "integrity",
    "load_module",
    "pack_for_spec",
    "pin_rev12",
    "read_manifest",
    "refresh",
    "registry",
    "tree_files",
    "tree_sha256",
    "validate_manifest",
    "validate_pack_name",
    "validate_preset",
    "validation_metrics",
]


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'pytacheck.packs' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover
    from pytacheck.packs.manifest import (
        FIELDS,
        Pack,
        PackError,
        read_manifest,
        validate_manifest,
        validate_pack_name,
        validate_preset,
        validation_metrics,
    )
    from pytacheck.packs.registry import (
        Registry,
        active_packs,
        builtin_pack,
        find_module,
        get_pack,
        install_dir,
        integrity,
        load_module,
        pack_for_spec,
        pin_rev12,
        refresh,
        registry,
    )
    from pytacheck.packs.tree import file_sha256, tree_files, tree_sha256
