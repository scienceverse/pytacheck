"""stat_check does not depend on what ran before it on the same paper (H0-7).

``report()`` and the accuracy harness run every module on one paper object.
stat_check once seemed to give other results there than on a paper of its
own; that came from a benchmark that patched ``copy.deepcopy`` and so broke
scipy's docstring parser (docs/design/PERF_REPORT.md), and nothing needed a
fix. This keeps it that way: stat_check run last, after the other offline
paper modules of the accuracy matrix, equals stat_check run alone.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import pytacheck as pc
from parity.accuracy import MATRIX_FILE
from parity.canonical import canonical
from pytacheck.module import module_run, run_session
from tests.httpmock import no_network

#: the accuracy matrix's offline paper modules but stat_check, in the matrix's order
OTHERS = [
    m
    for m in tomllib.loads(MATRIX_FILE.read_text(encoding="utf-8"))["papers"]["modules"]
    if m != "stat_check"
]

#: matrix inputs on which stat_check finds statistics (red and green)
INPUTS = [
    "formats/published.pdf.tei.xml",
    "formats/preprint.pdf.tei.xml",
    "problems/0956797617737129.xml",
    "psychsci/0956797614522816.json",
]


@pytest.mark.parametrize("name", INPUTS)
def test_stat_check_after_the_other_modules_equals_stat_check_alone(
    name: str, fixtures_dir: Path
) -> None:
    path = fixtures_dir / name
    with no_network():
        alone = module_run(pc.read(path), "stat_check")
        shared = pc.read(path)
        with run_session():
            for module in OTHERS:
                module_run(shared, module)
            last = module_run(shared, "stat_check")
    assert len(alone.table) > 0
    assert canonical(last) == canonical(alone)
