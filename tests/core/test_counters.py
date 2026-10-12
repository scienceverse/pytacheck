"""The work counters of a run: one Doc per paper, and nothing compiled on a warm pass.

* A paper read from JSON or Grobid XML keeps its tables as JSON records; its Doc
  is built once (by the reader, or by the first search) and reused by every
  later search, run or not.
* A paper whose tables are DataFrames gets one Doc per run inside the trusted
  scope of ``run_modules()`` and ``report_module_run()``, and a new Doc for every
  search outside one (a user may have edited a table in place).
* A second run of the same modules compiles no pattern.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

import metacheck as pc
from metacheck._r import regex as rx
from metacheck.core.doc import _derived
from metacheck.core.scope import counting, trusted_scope
from metacheck.provenance import run_modules
from metacheck.text.extract import extract_eq, extract_urls
from metacheck.text.search import text_search
from parity.cases import metacheck_defaults

MODULES = [
    "all_p_values",
    "all_urls",
    "coi_check",
    "ethics_check",
    "funding_check",
    "marginal",
    "open_practices",
    "stat_check",
    "stat_effect_size",
]
PAPERS = [
    "debruine/debruine-fret.xml",
    "psychsci/0956797614527830.json",
    "bibr12/PMC4383902.json",
]
FORMS = ["raw", "materialised", "metacheck_defaults"]


@pytest.fixture(autouse=True)
def _quiet() -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _read(path: Path, form: str) -> pc.Paper:
    if form == "metacheck_defaults":
        if path.suffix != ".xml":
            pytest.skip("metacheck's TEI conversion reads XML")
        with metacheck_defaults():
            paper = pc.read(path)
    else:
        paper = pc.read(path)
        if form == "materialised":
            _ = paper.text, paper.section
    assert isinstance(paper, pc.Paper)
    assert (paper._raw_records("text") is not None) == (form == "raw")
    return paper


def _run(paper: pc.Paper) -> None:
    chain = run_modules(paper, MODULES)
    assert [o.traffic_light for o in chain].count("fail") == 0


def _searches(paper: pc.Paper) -> None:
    text_search(paper, "power")
    text_search(paper, ["data", "materials"], return_="paragraph")
    text_search(text_search(paper, "significant"), "p ?[<=]", return_="section")
    extract_eq(paper)
    extract_urls(paper)


@pytest.mark.parametrize("name", PAPERS)
def test_a_raw_paper_is_indexed_once(fixtures_dir: Path, name: str) -> None:
    with counting() as n:
        paper = _read(fixtures_dir / name, "raw")
        for _ in range(3):
            _searches(paper)
        _run(paper)
        _run(paper)
    assert n["doc_builds"] == 1


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("name", PAPERS)
def test_a_run_indexes_each_paper_once(fixtures_dir: Path, name: str, form: str) -> None:
    paper = _read(fixtures_dir / name, form)
    for _ in range(2):
        with counting() as n:
            _run(paper)
        assert n["doc_builds"] <= 1


@pytest.mark.parametrize("form", ["materialised", "metacheck_defaults"])
def test_a_table_paper_is_indexed_per_search_outside_a_run(fixtures_dir: Path, form: str) -> None:
    paper = _read(fixtures_dir / PAPERS[0], form)
    with counting() as n:
        for _ in range(3):
            text_search(paper, "power")
    assert n["doc_builds"] == 3
    with counting() as n, trusted_scope():
        for _ in range(3):
            text_search(paper, "power")
    assert n["doc_builds"] == 1


@pytest.mark.parametrize("form", ["materialised", "metacheck_defaults"])
def test_a_doc_that_cannot_be_served_is_not_kept(fixtures_dir: Path, form: str) -> None:
    paper = _read(fixtures_dir / PAPERS[0], form)
    for _ in range(2):
        text_search(paper, "power")
        assert "doc" not in _derived(paper)
    with trusted_scope():
        text_search(paper, "power")
        assert "doc" in _derived(paper)
    text_search(paper, "power")  # outside again: the scope's Doc is dropped, not kept
    assert "doc" not in _derived(paper)


def test_a_raw_paper_keeps_its_doc(fixtures_dir: Path) -> None:
    paper = _read(fixtures_dir / PAPERS[0], "raw")
    text_search(paper, "power")
    assert "doc" in _derived(paper)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("name", PAPERS)
def test_a_warm_run_compiles_nothing(fixtures_dir: Path, name: str, form: str) -> None:
    paper = _read(fixtures_dir / name, form)
    _run(paper)
    _searches(paper)
    before = rx._compile.cache_info().misses
    _run(paper)
    _searches(paper)
    assert rx._compile.cache_info().misses == before
