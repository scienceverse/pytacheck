"""Fixtures for the report tests."""

from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULES = HERE / "modules"
HTML_FIXTURES = HERE / "fixtures" / "html"


@pytest.fixture
def test_module():
    """Path of a twin test module (tests/report/modules/<name>.py)."""

    def get(name: str) -> str:
        return str(MODULES / f"{name}.py")

    return get


@pytest.fixture
def html_fixture():
    def get(name: str) -> str:
        return str(HTML_FIXTURES / name)

    return get


@pytest.fixture
def quiet():
    """Progress bars and messages off."""
    from pytacheck.config import verbose

    old = verbose()
    verbose(False)
    yield
    verbose(old)
