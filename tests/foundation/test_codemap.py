"""docs/CODEMAP.md is up to date with the code (scripts/codemap.py)."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_codemap_is_up_to_date() -> None:
    if not (ROOT / "upstream" / "metacheck" / "NAMESPACE").is_file():
        pytest.skip("needs the upstream/metacheck submodule")
    codemap = runpy.run_path(str(ROOT / "scripts" / "codemap.py"))
    source = codemap["DOC"].read_text(encoding="utf-8")
    new = codemap["generate"](source)
    assert new == source, "docs/CODEMAP.md is out of date: run python scripts/codemap.py"
    assert codemap["problems"](new) == []
