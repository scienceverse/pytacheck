"""Packs say ``pytacheck``; the package is installed as the distribution ``metacheck``."""

from __future__ import annotations

import importlib.metadata as md

import pytest

from pytacheck import __version__
from pytacheck.packs.install import _requires_ok
from pytacheck.presets import _dependency_ok, _installed_version


def _only(monkeypatch: pytest.MonkeyPatch, installed: dict[str, str]) -> None:
    def fake(name: str) -> str:
        try:
            return installed[name]
        except KeyError:
            raise md.PackageNotFoundError(name) from None

    monkeypatch.setattr(md, "version", fake)


def test_pytacheck_requirement_finds_metacheck(monkeypatch) -> None:
    _only(monkeypatch, {"metacheck": "0.4.0a1"})
    assert _installed_version("pytacheck") == "0.4.0a1"
    assert _installed_version("PyTaCheck") == "0.4.0a1"
    assert _dependency_ok("pytacheck>=0.3")
    assert _requires_ok({"pytacheck": ">=0.3"}) == []
    assert _requires_ok({"pytacheck": ">=0.5"}) == ["pytacheck>=0.5"]


def test_old_install_still_counts(monkeypatch) -> None:
    _only(monkeypatch, {"pytacheck": "0.3.1"})
    assert _dependency_ok("pytacheck>=0.3")


def test_no_install_is_missing(monkeypatch) -> None:
    _only(monkeypatch, {})
    assert not _dependency_ok("pytacheck>=0.3")
    with pytest.raises(md.PackageNotFoundError):
        _installed_version("pytacheck")


def test_other_names_are_untouched(monkeypatch) -> None:
    _only(monkeypatch, {"metacheck": "0.4.0a1", "rich": "13.7"})
    assert _dependency_ok("rich>=13")
    assert not _dependency_ok("bibr")


def test_version_is_the_installed_distribution() -> None:
    assert __version__ == md.version("metacheck")
