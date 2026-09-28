"""The pytacheck.db package surface."""

from __future__ import annotations

import importlib

import pytest

import pytacheck.db as db


@pytest.mark.parametrize("name", sorted(db._EXPORTS))
def test_lazy_exports_resolve(name: str) -> None:
    assert callable(getattr(db, name))


def test_retractionwatch_module_is_not_shadowed() -> None:
    mod = importlib.import_module("pytacheck.db.retractionwatch")
    assert callable(mod.retractionwatch)
    assert mod.rw is mod.retractionwatch
    assert db.rw is mod.retractionwatch
