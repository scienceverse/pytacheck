"""The case generators run and give the committed case files.

Each generator writes to its module's ``OUT``; the test points that at a
temporary file and compares it with the committed one. No network, R or Docker.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name",
    ["make_cases", "make_review_cases"],
)
def test_generator_reproduces_committed_cases(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = importlib.import_module(f"tests.mod_repo_check.{name}")
    committed = mod.OUT
    out = tmp_path / committed.name
    monkeypatch.setattr(mod, "OUT", out)
    mod.main()
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")
