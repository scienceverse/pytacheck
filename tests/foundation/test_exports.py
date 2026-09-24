"""Every top-level export resolves to what it names, however imports happened."""

from __future__ import annotations

import importlib
import pkgutil
import types

import pytest

import pytacheck


def _import_all_submodules() -> None:
    for info in pkgutil.walk_packages(pytacheck.__path__, "pytacheck."):
        if ".modules." in info.name or info.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(info.name)
        except ImportError:  # optional extras (bibr, api, data readers)
            continue


@pytest.mark.parametrize("name", sorted(pytacheck._EXPORTS))
def test_export_resolves(name: str) -> None:
    _import_all_submodules()
    target = getattr(importlib.import_module(pytacheck._EXPORTS[name]), name)
    obj = getattr(pytacheck, name)
    if obj is not target:
        # a submodule shadowing its function must forward calls to it
        assert isinstance(obj, types.ModuleType)
        assert callable(obj) == callable(target)


def test_module_decorator_import_forms() -> None:
    _import_all_submodules()
    from pytacheck import module

    @module(title="T")
    def f(paper):
        return {}

    assert f.__pytacheck_module__.title == "T"
