"""Performance benchmarks (``pytest benchmarks --benchmark-only``)."""

from __future__ import annotations

from pathlib import Path

import pytacheck as pc


def test_read_corpus(benchmark, corpus_dir: Path) -> None:
    papers = benchmark(pc.read, corpus_dir)
    assert len(papers) == 200


def test_paper_table_text(benchmark, corpus_dir: Path) -> None:
    papers = pc.read(corpus_dir)
    table = benchmark(pc.paper_table, papers, "text")
    assert len(table) > 0


def test_text_search_corpus(benchmark, corpus_dir: Path) -> None:
    papers = pc.read(corpus_dir)
    hits = benchmark(pc.text_search, papers, r"\bp\s*[<=>]\s*\.?\d")
    assert len(hits) > 0


def test_module_marginal_corpus(benchmark, corpus_dir: Path) -> None:
    papers = pc.read(corpus_dir)
    out = benchmark(pc.module_run, papers, "marginal")
    assert len(out.summary_table) == 200
