"""V7 (a paragraph repeated word for word counts once) and F6 (repeated paper ids).

Both change results on purpose, as the maintainer decided on 2026-09-27 (F6, decision 6)
and 2026-09-28 (V7); docs/UPSTREAM_ISSUES.md D60 and U208 record them. The papers here are
small and built by hand, so each test names what it pins:

* a row repeated with every cell equal is one row, in every search mode;
* rows that differ in any cell (text_id, whitespace, punctuation, case) are kept;
* a match repeated inside one sentence is not a repeated row;
* tables and strings given to ``text_search()`` are searched as they are;
* papers of a list that share an id get ``id``, ``id~2``, ``id~3``, ... once, at every
  boundary, and the caller's papers keep their ids.
"""

from __future__ import annotations

import random
import warnings
from collections.abc import Sequence
from typing import Any

import pandas as pd
import pytest

from metacheck.core.doc import Docs
from metacheck.core.errors import PytacheckWarning
from metacheck.core.patterns import Pat
from metacheck.module import module_run
from metacheck.papers import Paper, PaperList, paper_id, paper_table
from metacheck.papers.ids import resolve, resolve_ids
from metacheck.text.extract import extract_p_values
from metacheck.text.search import text_search
from tests._legacy.search import text_search as legacy_text_search
from tests.core.chains import PATTERNS, make_paper

TEXT_COLUMNS = ["text", "text_id", "section_id", "paragraph_id"]


def make(pid: str, rows: Sequence[tuple[Any, ...]], materialise: bool = False) -> Paper:
    """A paper with one results section; *rows* are ``(text, text_id, paragraph_id)``."""
    texts = [
        {"text": t, "text_id": tid, "section_id": 1, "paragraph_id": pg} for t, tid, pg in rows
    ]
    p = Paper(paper_id=pid)
    p._set_raw("text", texts, TEXT_COLUMNS)
    p._set_raw(
        "section",
        [{"section_id": 1, "header": "Results", "section_type": "results"}],
        ["section_id", "header", "section_type"],
    )
    info = [{"title": f"Paper {pid}"}]
    p._set_raw("info", info, ["title"])
    if materialise:
        p.get("text")
        p.get("section")
    return p


PARAGRAPH = [("The effect was small, p = 0.04.", 1, 1), ("A second test gave p < .001.", 2, 1)]


def with_repeat(
    rows: Sequence[tuple[Any, ...]] = PARAGRAPH, **change: Any
) -> list[tuple[Any, ...]]:
    """*rows*, then the same rows once more; ``text_id``/``paragraph_id`` may be moved on."""
    again = []
    for t, tid, pg in rows:
        again.append(
            (
                change.get("text", lambda s: s)(t),
                tid + change.get("text_id", 0),
                pg + change.get("paragraph_id", 0),
            )
        )
    return [*rows, *again]


# -- V7: a row repeated word for word counts once ---------------------------------------


@pytest.mark.parametrize("materialise", [False, True])
def test_a_paragraph_repeated_word_for_word_gives_the_matches_once(materialise: bool) -> None:
    plain = make("a", PARAGRAPH, materialise)
    doubled = make("a", with_repeat(), materialise)
    assert len(doubled.get("text")) == 2 * len(plain.get("text"))
    for ret in ("sentence", "match"):
        pd.testing.assert_frame_equal(
            text_search(doubled, r"p\s*[=<]", ret, perl=True),
            text_search(plain, r"p\s*[=<]", ret, perl=True),
        )
    # the wider modes return the same rows; their text is joined from the table's
    # sentences, as in R, so it carries the repeat (only the row count is the point here)
    for ret in ("paragraph", "section", "header", "paper_id"):
        assert len(text_search(doubled, r"p\s*[=<]", ret, perl=True)) == len(
            text_search(plain, r"p\s*[=<]", ret, perl=True)
        )
    pd.testing.assert_frame_equal(extract_p_values(doubled), extract_p_values(plain))
    assert extract_p_values(doubled)["text"].tolist() == ["p = 0.04", "p < .001"]


def test_the_repeat_counts_once_in_a_module() -> None:
    plain = module_run(make("a", PARAGRAPH), "all_p_values")
    doubled = module_run(make("a", with_repeat()), "all_p_values")
    assert len(plain.table) == len(doubled.table) == 2
    assert doubled.summary_text == plain.summary_text == "We found 2 p-values"
    assert doubled.summary_table["p_values"].tolist() == [2]


def test_a_repeat_anywhere_in_the_paper_counts_once() -> None:
    # not next to the first: a later row equal in every cell
    rows = [*PARAGRAPH, ("Another sentence, p = 0.5.", 3, 2), *PARAGRAPH]
    got = extract_p_values(make("a", rows))
    assert got["text"].tolist() == ["p = 0.04", "p < .001", "p = 0.5"]


def test_the_first_row_is_the_one_kept() -> None:
    rows = [("First, p = 0.01.", 1, 1), ("Other, p = 0.02.", 2, 1), ("First, p = 0.01.", 1, 1)]
    got = extract_p_values(make("a", rows))
    assert got["text"].tolist() == ["p = 0.01", "p = 0.02"]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"text_id": 10}, id="another_text_id"),
        pytest.param({"paragraph_id": 5}, id="another_paragraph"),
        pytest.param({"text": lambda s: s.replace(" ", "  ")}, id="whitespace"),
        pytest.param({"text": lambda s: s.rstrip(".")}, id="punctuation"),
        pytest.param({"text": lambda s: s.upper()}, id="case"),
    ],
)
def test_rows_that_differ_in_any_cell_are_kept(change: dict[str, Any]) -> None:
    # (one tweak at a time; in this paper the same p-values appear in the new rows too)
    rows = with_repeat(**change)
    got = extract_p_values(make("a", rows))
    others = extract_p_values(make("a", PARAGRAPH))
    # R's own behaviour: every distinct row is searched, nothing is merged
    assert len(got) >= 2 * len(others) - 2
    assert len(got) > len(others)


def test_near_duplicates_are_not_merged_in_match_mode() -> None:
    # equal in every cell except the raw text: whitespace differs, so they are two rows
    rows = [("The test gave p = 0.04.", 1, 1), ("The test  gave p = 0.04.", 1, 1)]
    got = text_search(make("a", rows), r"p = \d\.\d+", "match", perl=True)
    assert got["text"].tolist() == ["p = 0.04", "p = 0.04"]


def test_a_match_repeated_inside_one_sentence_counts_each_time() -> None:
    rows = [("It was p = 0.04, and again p = 0.04.", 1, 1)]
    assert extract_p_values(make("a", rows))["text"].tolist() == ["p = 0.04", "p = 0.04"]
    # and when that sentence is repeated, each match still counts, once per sentence
    got = extract_p_values(make("a", [*rows, *rows]))
    assert got["text"].tolist() == ["p = 0.04", "p = 0.04"]


def test_equal_text_with_another_text_id_is_another_row() -> None:
    # the same sentence in two places of a paper (two text ids) is metacheck's two rows
    rows = [("We found p = 0.04.", 1, 1), ("We found p = 0.04.", 2, 2)]
    assert len(extract_p_values(make("a", rows))) == 2
    assert len(text_search(make("a", rows), "found")) == 2


def test_a_table_or_strings_given_to_text_search_are_searched_as_they_are() -> None:
    frame = pd.DataFrame({"text": ["p = 0.04", "p = 0.04"], "text_id": [1, 1]})
    got = text_search(frame, r"p = \d\.\d+", "match", perl=True)
    assert got["text"].tolist() == ["p = 0.04", "p = 0.04"]
    assert text_search(["p = 0.04", "p = 0.04"], r"p = \d\.\d+", "match", perl=True) == [
        "p = 0.04",
        "p = 0.04",
    ]


def test_a_paper_list_drops_the_repeat_within_each_paper_only() -> None:
    a, b = make("a", with_repeat()), make("b", PARAGRAPH)
    got = extract_p_values(PaperList([a, b]))
    assert got["paper_id"].tolist() == ["a", "a", "b", "b"]
    # the same sentence in two papers is two rows
    assert got["text"].tolist() == ["p = 0.04", "p < .001"] * 2


def test_match_mode_equals_the_frozen_search_on_the_paper_without_its_repeats() -> None:
    """The V7 oracle for match mode: the frozen text_search() (which keeps repeats) on the
    paper whose repeated rows were removed."""
    rng = random.Random(11)
    n_repeated = 0
    for n in range(60):
        paper = make_paper(rng, f"p{n}", n % 3 == 0)
        text = paper.get("text")
        deduped = text.drop_duplicates().reset_index(drop=True)
        n_repeated += len(deduped) < len(text)
        clean = paper.copy()
        clean["text"] = deduped
        for pattern in rng.sample(PATTERNS, 4):
            for ref in (False, True):
                got = text_search(paper, pattern, "match", include_refs=ref)
                want = legacy_text_search(clean, pattern, "match", include_refs=ref)
                pd.testing.assert_frame_equal(got, want)
    assert n_repeated  # the generator does make papers with repeated rows


# -- V7: Hits.ordered() and statements() ------------------------------------------------


def hits_of(*papers: Paper, pattern: str = "p") -> Any:
    return Docs.of(PaperList(papers)).hits(Pat(pattern))


def test_ordered_sorts_by_paper_then_text_id_with_missing_ids_last() -> None:
    a = make("a", [("p one", 3, 1), ("p two", None, 1), ("p three", 1, 1)])
    b = make("b", [("p four", 2, 1)])
    rows = hits_of(b, a).ordered(["a", "b"])  # papers in the order b, a; levels a, b
    assert [(d.paper_id, label) for d, _, label in rows] == [("a", "a")] * 3 + [("b", "b")]
    assert rows.texts() == ["p three", "p one", "p two", "p four"]


def test_ordered_levels_default_to_the_papers_in_list_order() -> None:
    a, b = make("a", [("p one", 1, 1)]), make("b", [("p two", 1, 1)])
    assert hits_of(b, a).ordered().texts() == ["p two", "p one"]
    assert hits_of(b, a).ordered(["a", "b"]).texts() == ["p one", "p two"]


def test_ordered_labels_a_paper_that_is_not_a_level_with_none() -> None:
    a, b = make("a", [("p one", 1, 1)]), make("b", [("p two", 1, 1)])
    rows = hits_of(a, b).ordered(["a"])
    assert [label for _, _, label in rows] == ["a", None]
    assert rows.statements() == {"a": ["p one"]}


def test_statements_list_each_text_once_per_paper() -> None:
    a = make("a", [("p one", 1, 1), ("p two", 2, 2), ("p one", 3, 3)])
    b = make("b", [("p one", 1, 1)])
    assert hits_of(a, b).statements() == {"a": ["p one", "p two"], "b": ["p one"]}


def test_a_row_repeated_word_for_word_is_one_statement_row() -> None:
    a = make("a", with_repeat())
    h = hits_of(a, pattern="p")
    assert len(h) == 2
    assert len(h.ordered()) == 2
    # the repeat is dropped by the call, not only by statements()
    assert h.statements() == {"a": [t for t, _, _ in PARAGRAPH]}


def test_ordered_follows_the_pattern_major_order_of_a_list_within_a_text_id() -> None:
    a = make("a", [("alpha beta", 1, 1)])
    from metacheck.core.patterns import patterns

    h = Docs.of(a).hits(patterns("beta", "alpha"))
    assert h.ordered().texts() == ["alpha beta"]  # one row: found by both, listed once


# -- F6: repeated ids -------------------------------------------------------------------


def test_resolve_ids_renames_later_repeats_and_keeps_the_first() -> None:
    assert resolve_ids(["a", "b", "c"]) == ["a", "b", "c"]
    assert resolve_ids(["a", "a", "b", "a"]) == ["a", "a~2", "b", "a~3"]
    assert resolve_ids(["a", "b", "b", "a", "b"]) == ["a", "b", "b~2", "a~2", "b~3"]


def test_resolve_ids_skips_a_name_another_paper_already_has() -> None:
    assert resolve_ids(["X", "X", "X~2"]) == ["X", "X~3", "X~2"]
    assert resolve_ids(["X~2", "X", "X"]) == ["X~2", "X", "X~3"]
    out = resolve_ids(["X", "X~2", "X", "X~2", "X"])
    assert len(set(out)) == len(out)
    assert out[:2] == ["X", "X~2"]


def test_resolve_ids_leaves_ids_that_are_not_text() -> None:
    assert resolve_ids([None, None, "a", "a"]) == [None, None, "a", "a~2"]


def test_resolve_returns_the_very_list_when_no_id_repeats() -> None:
    papers = PaperList([make("a", PARAGRAPH), make("b", PARAGRAPH)])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert resolve(papers) is papers


def test_resolve_renames_a_copy_and_warns_once() -> None:
    first, second, third = (make("X", PARAGRAPH) for _ in range(3))
    papers = PaperList([first, second, third])
    with pytest.warns(PytacheckWarning, match=r"'X' as 'X~2', 'X~3'") as caught:
        out = resolve(papers)
    assert len(caught) == 1
    assert out.names == ["X", "X~2", "X~3"]
    assert out[0] is first  # the first keeps its id: the very paper
    assert out[1] is not second
    assert papers.names == ["X", "X", "X"]  # the caller's papers are not renamed
    assert second.paper_id == third.paper_id == "X"


def test_resolve_of_a_python_list_gives_a_list() -> None:
    with pytest.warns(PytacheckWarning):
        out = resolve([make("X", PARAGRAPH), make("X", PARAGRAPH)])
    assert isinstance(out, list)
    assert [p.paper_id for p in out] == ["X", "X~2"]


def test_a_renamed_paper_shares_the_records_of_the_original() -> None:
    a, b = make("X", PARAGRAPH), make("X", PARAGRAPH)
    with pytest.warns(PytacheckWarning):
        out = resolve([a, b])
    assert out[1]._raw_records("text") is not None  # still unbuilt records, not a frame
    assert out[1]._raw_records("text")[0] is b._raw_records("text")[0]
    assert b._raw_records("text") is not None  # and the original did not build a table


def test_docs_of_a_list_with_a_repeated_id_resolves_it() -> None:
    a, b = make("X", PARAGRAPH), make("X", [("Other p = 0.9.", 1, 1)])
    with pytest.warns(PytacheckWarning):
        docs = Docs.of(PaperList([a, b]))
    assert [d.paper_id for d in docs] == ["X", "X~2"]
    assert docs.paper_ids() == ["X", "X~2"]


def test_paper_tables_of_a_list_use_the_resolved_ids() -> None:
    a, b = make("X", PARAGRAPH), make("X", [("Other p = 0.9.", 1, 1)])
    with pytest.warns(PytacheckWarning):
        text = paper_table(PaperList([a, b]), "text")
    assert text["paper_id"].tolist() == ["X", "X", "X~2"]
    with pytest.warns(PytacheckWarning):
        assert paper_id(PaperList([a, b])) == ["X", "X~2"]


def test_the_same_paper_twice_is_two_papers() -> None:
    a = make("X", PARAGRAPH)
    with pytest.warns(PytacheckWarning):
        got = extract_p_values(PaperList([a, a]))
    assert got["paper_id"].tolist() == ["X", "X", "X~2", "X~2"]


def test_text_search_of_a_list_with_a_repeated_id_equals_the_resolved_list() -> None:
    a, b = make("X", PARAGRAPH), make("X", [("Other p = 0.9.", 1, 1)])
    with pytest.warns(PytacheckWarning):
        got = text_search(PaperList([a, b]), "p")
    want = text_search(
        PaperList([make("X", PARAGRAPH), make("X~2", [("Other p = 0.9.", 1, 1)])]), "p"
    )
    pd.testing.assert_frame_equal(got, want)


@pytest.mark.parametrize("module", ["all_p_values", "ethics_check", "ref_consistency"])
def test_a_module_on_a_list_with_a_repeated_id_equals_the_resolved_list(module: str) -> None:
    def papers(ids: tuple[str, str]) -> PaperList:
        return PaperList(
            [make(ids[0], PARAGRAPH), make(ids[1], [("Ethical approval was given.", 1, 1)])]
        )

    with pytest.warns(PytacheckWarning):
        got = module_run(papers(("X", "X")), module)
    want = module_run(papers(("X", "X~2")), module)
    assert got.summary_table["paper_id"].tolist() == ["X", "X~2"]
    pd.testing.assert_frame_equal(got.summary_table, want.summary_table)
    assert got.summary_text == want.summary_text
    assert got.traffic_light == want.traffic_light
    if want.table is not None:
        pd.testing.assert_frame_equal(got.table, want.table)


def test_the_summary_has_one_row_per_paper_of_a_list_with_repeated_ids() -> None:
    papers = PaperList([make("X", PARAGRAPH) for _ in range(3)])
    with pytest.warns(PytacheckWarning):
        out = module_run(papers, "all_p_values")
    assert out.summary_table["paper_id"].tolist() == ["X", "X~2", "X~3"]
    assert out.summary_table["p_values"].tolist() == [2, 2, 2]
    assert out.paper.names == ["X", "X~2", "X~3"]  # a chained module sees the resolved list
    # a later module of the chain does not resolve (or warn) again
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        again = module_run(out, "ethics_check")
    assert again.summary_table["paper_id"].tolist() == ["X", "X~2", "X~3"]


def test_report_of_a_list_with_a_repeated_id_writes_one_file_per_paper(tmp_path: Any) -> None:
    from metacheck.report import report

    papers = PaperList([make("X", PARAGRAPH), make("X", [("Other p = 0.9.", 1, 1)])])
    with pytest.warns(PytacheckWarning):
        out = report(papers, ["all_p_values"], tmp_path / "_report.md", "md")
    assert sorted(out) == ["X", "X~2"]
    assert sorted(f.name for f in tmp_path.iterdir()) == ["X_report.md", "X~2_report.md"]
