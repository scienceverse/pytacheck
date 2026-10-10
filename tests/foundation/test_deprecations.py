"""Deprecated top-level names warn once, keep working, and nothing else warns."""

from __future__ import annotations

import ast
import importlib
import warnings
from pathlib import Path

import pytest

import metacheck

DEPRECATED = sorted(metacheck._DEPRECATED)
KEPT = sorted(set(metacheck._EXPORTS) - set(metacheck._DEPRECATED))
SRC = Path(metacheck.__file__).resolve().parent


def _ours(record: list[warnings.WarningMessage]) -> list[warnings.WarningMessage]:
    return [
        w
        for w in record
        if issubclass(w.category, DeprecationWarning) and str(w.message).startswith("metacheck.")
    ]


def _forget(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Undo the cache of an earlier read, so that this test reads the name afresh."""
    if name in vars(metacheck):
        monkeypatch.delitem(vars(metacheck), name)


def test_deprecated_names_are_exports() -> None:
    assert DEPRECATED
    assert set(DEPRECATED) <= set(metacheck._EXPORTS)
    assert all(not alt.startswith("metacheck.") for alt in metacheck._DEPRECATED.values())


@pytest.mark.parametrize("name", DEPRECATED)
def test_deprecated_name_warns_once_and_works(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    target = getattr(importlib.import_module(metacheck._EXPORTS[name]), name)
    _forget(monkeypatch, name)
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        first = getattr(metacheck, name)
        second = getattr(metacheck, name)
        exec(f"from metacheck import {name}", {})  # the import form, cached by now
    assert first is target
    assert second is target
    assert callable(first)
    ours = _ours(record)
    assert len(ours) == 1, [str(w.message) for w in ours]
    message = str(ours[0].message)
    assert f"metacheck.{name} is deprecated" in message
    assert "the next minor release" in message
    if metacheck._DEPRECATED[name]:
        assert f"use {metacheck._DEPRECATED[name]} instead" in message
    assert ours[0].filename == __file__  # attributed to the reader, not to metacheck


def test_from_import_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    _forget(monkeypatch, "zenodo_upload")
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        from metacheck import zenodo_upload  # noqa: F401
    assert len(_ours(record)) == 1


def test_old_import_name_warns_at_the_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck

    _forget(monkeypatch, "rw")
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        value = pytacheck.rw
    ours = _ours(record)
    assert value is metacheck.retractionwatch
    assert len(ours) == 1
    assert ours[0].filename == __file__
    assert "use retractionwatch() instead" in str(ours[0].message)


@pytest.mark.parametrize("name", DEPRECATED)
def test_submodule_import_is_silent(name: str) -> None:
    module = importlib.import_module(metacheck._EXPORTS[name])
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        getattr(module, name)
    assert _ours(record) == []


@pytest.mark.parametrize("name", KEPT)
def test_kept_name_does_not_warn(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        importlib.import_module(metacheck._EXPORTS[name])  # its own import may warn
    except ImportError:  # optional extras
        pytest.skip("optional dependency")
    _forget(monkeypatch, name)
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        getattr(metacheck, name)
    assert _ours(record) == []


def test_nothing_in_the_package_reads_a_deprecated_name() -> None:
    """A deprecated name that the package starts to use is no longer unused."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.ImportFrom) and node.module in ("metacheck", "pytacheck"):
                names = [a.name for a in node.names]
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in ("metacheck", "pytacheck", "pc", "mc")
            ):
                names = [node.attr]
            else:
                continue
            found += [f"{path.relative_to(SRC)}: {n}" for n in names if n in metacheck._DEPRECATED]
    assert found == []
