"""Built-in modules carry metacheck's roxygen metadata verbatim.

module_list(), module_help() and module_report() print these fields, so the
title, keywords and authors must be exactly the R module's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pytacheck.module import _builtin_names, module_find

R_MODULES = Path(__file__).resolve().parents[2] / "upstream" / "metacheck" / "inst" / "modules"


def _roxygen(path: Path) -> dict[str, list[str]]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#'"):
            if lines:
                break
            continue
        lines.append(line[2:].strip())
    tags: dict[str, list[str]] = {"title": []}
    current = "title"
    for line in lines:
        m = re.match(r"@(\w+)\s*(.*)$", line)
        if m:
            current = m.group(1)
            tags.setdefault(current, [])
            if m.group(2):
                tags[current].append(m.group(2))
        elif line and current == "title" and not tags["title"]:
            tags["title"].append(line)
    return tags


PORTED = [n for n in _builtin_names() if (R_MODULES / f"{n}.R").is_file()]


@pytest.mark.parametrize("name", PORTED)
def test_metadata_matches_r(name: str) -> None:
    tags = _roxygen(R_MODULES / f"{name}.R")
    spec = module_find(name)
    assert spec.title == tags["title"][0]
    assert list(spec.keywords) == " ".join(tags.get("keywords", [])).split()
    assert list(spec.author) == tags.get("author", [])
