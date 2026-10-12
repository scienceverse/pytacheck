"""D84: the COI, funding and ethics checks fall back to the statements bibr extracts."""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.modules._statements import bibr_statements


def _paper(sentences: list[str], **statements: str) -> pc.Paper:
    paper = pc.test_paper(sentences)
    info = paper["info"].copy()
    for field, text in statements.items():
        info[field] = pd.array([text], dtype="string")
    paper["info"] = info
    return paper


@pytest.mark.parametrize(
    ("module", "field", "statement"),
    [
        ("coi_check", "coi_statement", "The authors declare no conflict of interest."),
        ("coi_check_oi", "coi_statement", "The authors declare no conflict of interest."),
        ("funding_check", "funding_statement", "Funded by the NSF under award 12345."),
        ("funding_check_oi", "funding_statement", "Funded by the NSF under award 12345."),
    ],
)
def test_statement_only_in_bibr_is_found(module: str, field: str, statement: str) -> None:
    out = pc.module_run(_paper(["We ran a study."], **{field: statement}), module)
    assert out.traffic_light == "green"
    assert out.table["text"].tolist() == [statement]


def test_the_expressions_come_first() -> None:
    paper = _paper(
        ["We have no known conflicts of interest.", "We report all studies."],
        coi_statement="We have no known conflicts of interest. We report all studies.",
    )
    out = pc.module_run(paper, "coi_check")
    assert out.table["text"].tolist() == ["We have no known conflicts of interest."]


def test_without_a_statement_nothing_changes() -> None:
    out = pc.module_run(pc.test_paper(["We ran a study."]), "funding_check")
    assert out.traffic_light == "red"


def test_ethics_statement_only_in_bibr() -> None:
    paper = _paper(
        ["We recruited 40 participants who gave informed consent."],
        ethics_statement="The study was approved by the local ethics board.",
    )
    out = pc.module_run(paper, "ethics_check")
    st = out.summary_table
    assert st["ethics_approved"].tolist() == [True]
    assert st["ethics_statements"].tolist() == [
        ["The study was approved by the local ethics board."]
    ]


def test_paper_list_keeps_paper_order() -> None:
    papers = pc.PaperList(
        [
            _paper(["We ran a study."], coi_statement="None declared."),
            pc.test_paper(["The authors declare no competing interests."]),
        ]
    )
    out = pc.module_run(papers, "coi_check")
    assert out.table["paper_id"].tolist() == [p.paper_id for p in papers]


def test_bibr_statements_collapses_whitespace() -> None:
    paper = _paper(["x"], funding_statement="Funded by\r\n the  NSF.")
    assert bibr_statements(paper, "funding_statement") == {paper.paper_id: "Funded by the NSF."}
    assert bibr_statements(paper, "coi_statement") == {}
    assert bibr_statements(pd.DataFrame({"text": ["x"]}), "coi_statement") == {}
