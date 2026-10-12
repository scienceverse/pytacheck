"""Packs: shareable folders of modules and presets (module system v2).

Submodules are imported lazily, and ``import metacheck`` never imports this
package: built-in modules resolve without it.

* :mod:`metacheck.packs.manifest` -- ``pack.json`` validation and :class:`Pack`;
* :mod:`metacheck.packs.tree` -- the ``pytacheck-tree-v1`` hash;
* :mod:`metacheck.packs.registry` -- the active packs and module loading;
* :mod:`metacheck.packs.stores` -- stores and their ``index.json``;
* :mod:`metacheck.packs.fetch` -- downloading and safely extracting sources;
* :mod:`metacheck.packs.auth` -- GitHub tokens for private stores and packs;
* :mod:`metacheck.packs.install` -- install, remove, update, list, show;
* :mod:`metacheck.packs.scan` -- static scans of pack code (never imported);
* :mod:`metacheck.packs.check` -- ``pack check`` (the module contract);
* :mod:`metacheck.packs.scaffold` -- ``pack_new()`` and ``module_template()``;
* :mod:`metacheck.packs.build` -- ``store build`` (a store's ``index.json``).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "FIELDS": "metacheck.packs.manifest",
    "Pack": "metacheck.packs.manifest",
    "PackError": "metacheck.packs.manifest",
    "read_manifest": "metacheck.packs.manifest",
    "validate_manifest": "metacheck.packs.manifest",
    "validate_pack_name": "metacheck.packs.manifest",
    "validate_preset": "metacheck.packs.manifest",
    "validation_metrics": "metacheck.packs.manifest",
    "file_sha256": "metacheck.packs.tree",
    "tree_files": "metacheck.packs.tree",
    "tree_sha256": "metacheck.packs.tree",
    "Registry": "metacheck.packs.registry",
    "active_packs": "metacheck.packs.registry",
    "builtin_pack": "metacheck.packs.registry",
    "find_module": "metacheck.packs.registry",
    "get_pack": "metacheck.packs.registry",
    "install_dir": "metacheck.packs.registry",
    "integrity": "metacheck.packs.registry",
    "load_module": "metacheck.packs.registry",
    "pack_for_spec": "metacheck.packs.registry",
    "pin_rev12": "metacheck.packs.registry",
    "refresh": "metacheck.packs.registry",
    "registry": "metacheck.packs.registry",
    "overlay": "metacheck.packs.registry",
    "StoreError": "metacheck.packs.stores",
    "store_add": "metacheck.packs.stores",
    "store_index": "metacheck.packs.stores",
    "store_list": "metacheck.packs.stores",
    "store_remove": "metacheck.packs.stores",
    "store_search": "metacheck.packs.stores",
    "store_update": "metacheck.packs.stores",
    "Cancelled": "metacheck.packs.install",
    "pack_install": "metacheck.packs.install",
    "pack_list": "metacheck.packs.install",
    "pack_remove": "metacheck.packs.install",
    "pack_show": "metacheck.packs.install",
    "pack_update": "metacheck.packs.install",
    "CheckIssue": "metacheck.packs.check",
    "pack_check": "metacheck.packs.check",
    "module_template": "metacheck.packs.scaffold",
    "pack_new": "metacheck.packs.scaffold",
    "store_build": "metacheck.packs.build",
}

__all__ = [
    "FIELDS",
    "Cancelled",
    "CheckIssue",
    "Pack",
    "PackError",
    "Registry",
    "StoreError",
    "active_packs",
    "builtin_pack",
    "file_sha256",
    "find_module",
    "get_pack",
    "install_dir",
    "integrity",
    "load_module",
    "module_template",
    "overlay",
    "pack_check",
    "pack_for_spec",
    "pack_install",
    "pack_list",
    "pack_new",
    "pack_remove",
    "pack_show",
    "pack_update",
    "pin_rev12",
    "read_manifest",
    "refresh",
    "registry",
    "store_add",
    "store_build",
    "store_index",
    "store_list",
    "store_remove",
    "store_search",
    "store_update",
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
        raise AttributeError(f"module 'metacheck.packs' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover
    from metacheck.packs.build import store_build
    from metacheck.packs.check import CheckIssue, pack_check
    from metacheck.packs.install import (
        Cancelled,
        pack_install,
        pack_list,
        pack_remove,
        pack_show,
        pack_update,
    )
    from metacheck.packs.manifest import (
        FIELDS,
        Pack,
        PackError,
        read_manifest,
        validate_manifest,
        validate_pack_name,
        validate_preset,
        validation_metrics,
    )
    from metacheck.packs.registry import (
        Registry,
        active_packs,
        builtin_pack,
        find_module,
        get_pack,
        install_dir,
        integrity,
        load_module,
        overlay,
        pack_for_spec,
        pin_rev12,
        refresh,
        registry,
    )
    from metacheck.packs.scaffold import module_template, pack_new
    from metacheck.packs.stores import (
        StoreError,
        store_add,
        store_index,
        store_list,
        store_remove,
        store_search,
        store_update,
    )
    from metacheck.packs.tree import file_sha256, tree_files, tree_sha256
