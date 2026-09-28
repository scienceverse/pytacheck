"""Benchmark fixtures: a synthetic corpus built from the psychsci fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import orjson
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PSYCHSCI = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "psychsci"


def make_corpus(target: Path, n: int) -> Path:
    """Write *n* bibr JSON papers (copies of the fixtures with unique ids)."""
    sources = sorted(PSYCHSCI.glob("*.json"))
    target.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        data = orjson.loads(sources[i % len(sources)].read_bytes())
        data["paper_id"] = f"{data['paper_id']}_{i:04d}"
        (target / f"{data['paper_id']}.json").write_bytes(orjson.dumps(data))
    return target


@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_corpus(tmp_path_factory.mktemp("corpus"), 200)
