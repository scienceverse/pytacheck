"""Empty input to the text and reference modules (``.paper_is_empty()``, ``.empty_input_result()``).

metacheck 0.4 answers a paper or paper list with no text and no references with one
result for all of these modules: traffic light "na", "There was no text to check." and a
summary table with one row per paper and no other column.
"""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import module_run
from metacheck.modules._empty import empty_input_result, paper_is_empty, per_paper_table

MODULES = [
    "causal_claims",
    "coi_check",
    "coi_check_oi",
    "funding_check",
    "funding_check_oi",
    "marginal",
    "open_practices",
    "ref_miscitation",
    "stat_p_nonsig",
]


def test_paper_is_empty() -> None:
    assert paper_is_empty(pc.test_paper([]))
    assert paper_is_empty(pc.paper())
    assert paper_is_empty(pc.PaperList([]))
    assert paper_is_empty(pc.PaperList([pc.test_paper([]), pc.test_paper([])]))
    assert not paper_is_empty(pc.test_paper(["Some text."]))
    assert not paper_is_empty(pc.PaperList([pc.test_paper([]), pc.test_paper(["Some text."])]))


def test_a_paper_with_references_but_no_text_is_not_empty(demo: pc.Paper) -> None:
    paper = demo.copy()
    paper.text = paper.text.iloc[0:0]
    assert len(paper.bib) > 0
    assert not paper_is_empty(paper)


@pytest.mark.parametrize("value", [None, "text", 1, pd.DataFrame({"text": []})])
def test_inputs_that_are_not_papers_are_never_empty(value: object) -> None:
    assert not paper_is_empty(value)


def test_per_paper_table() -> None:
    papers = pc.PaperList([pc.test_paper(["a"]), pc.test_paper(["b"])])
    out = per_paper_table(papers, found=True, n=0)
    assert out.columns.tolist() == ["paper_id", "found", "n"]
    assert out["paper_id"].tolist() == list(papers.names)
    assert out["found"].tolist() == [True, True]
    assert out["n"].tolist() == [0, 0]
    # no papers: no rows, but the columns
    empty = per_paper_table(pc.PaperList([]), found=True)
    assert empty.columns.tolist() == ["paper_id", "found"]
    assert len(empty) == 0


def test_empty_input_result() -> None:
    paper = pc.test_paper([])
    out = empty_input_result(paper)
    assert out["traffic_light"] == "na"
    assert out["summary_text"] == "There was no text to check."
    assert out["summary_table"]["paper_id"].tolist() == [paper.paper_id]
    assert out["summary_table"].columns.tolist() == ["paper_id"]


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("make", [pc.paper, lambda: pc.test_paper([]), lambda: pc.PaperList([])])
def test_modules_answer_an_empty_input_with_na(name: str, make) -> None:
    paper = make()
    out = module_run(paper, name)
    assert out.traffic_light == "na"
    assert out.summary_text == "There was no text to check."
    assert out.summary_table.columns.tolist() == ["paper_id"]
    assert len(out.summary_table) == (len(paper) if isinstance(paper, pc.PaperList) else 1)


@pytest.mark.parametrize(
    "name", [m for m in MODULES if m != "causal_claims"]
)  # that one asks the API
def test_a_paper_with_text_is_not_answered_as_empty(name: str) -> None:
    out = module_run(pc.test_paper(["Nothing relevant to any module here."]), name)
    assert out.summary_text != "There was no text to check."
