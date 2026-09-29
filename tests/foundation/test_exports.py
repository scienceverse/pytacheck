"""Every top-level export resolves to what it names, however imports happened."""

from __future__ import annotations

import importlib
import pkgutil
import types

import pytest

import metacheck


def _import_all_submodules() -> None:
    for info in pkgutil.walk_packages(metacheck.__path__, "metacheck."):
        if ".modules." in info.name or info.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(info.name)
        except ImportError:  # optional extras (bibr, api, data readers)
            continue


@pytest.mark.parametrize("name", sorted(metacheck._EXPORTS))
def test_export_resolves(name: str) -> None:
    _import_all_submodules()
    target = getattr(importlib.import_module(metacheck._EXPORTS[name]), name)
    obj = getattr(metacheck, name)
    if obj is not target:
        # a submodule shadowing its function must forward calls to it
        assert isinstance(obj, types.ModuleType)
        assert callable(obj) == callable(target)


def test_module_decorator_import_forms() -> None:
    _import_all_submodules()
    from metacheck import module

    @module(title="T")
    def f(paper):
        return {}

    assert f.__pytacheck_module__.title == "T"


def test_metacheck_exports_are_in_sync() -> None:
    """Every ported metacheck export is a top-level export (scripts/sync_exports.py)."""
    pytest.importorskip("ruff")
    import runpy
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "sync_exports.py"
    sync = runpy.run_path(str(script))
    source = sync["INIT"].read_text(encoding="utf-8")
    assert sync["generate"](source) == source, "run: python scripts/sync_exports.py"
