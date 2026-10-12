"""D85: checks skip the papers bibr classifies as a type they do not apply to."""

from __future__ import annotations

import pandas as pd

import metacheck as pc
from metacheck.modules._paper_type import NO_RESEARCH, paper_type, skipped_type

LIVE = "We recruited 40 participants who gave informed consent."


def _paper(sentences: list[str], kind: str | None, confidence: float | None = None) -> pc.Paper:
    paper = pc.test_paper(sentences)
    info = paper["info"].copy()
    info["paper_type"] = pd.array([kind], dtype="string")
    info["paper_type_confidence"] = pd.array([confidence], dtype="Float64")
    paper["info"] = info
    return paper


def test_paper_type_and_confidence() -> None:
    assert paper_type(_paper(["x"], "review", 0.9)) == ("review", 0.9)
    assert paper_type(_paper(["x"], None)) == (None, None)
    assert skipped_type(_paper(["x"], "commentary"), NO_RESEARCH) == "commentary"
    assert skipped_type(_paper(["x"], "commentary", 0.5), NO_RESEARCH) is None
    assert skipped_type(_paper(["x"], "review"), NO_RESEARCH) is None


def test_ethics_skips_a_review() -> None:
    out = pc.module_run(_paper([LIVE], "review", 0.95), "ethics_check")
    assert out.traffic_light == "na"
    assert out.summary_table["needs_ethics"].tolist() == [False]
    assert out.summary_text == (
        "bibr classifies the paper as a review, which collects no data from participants, "
        "so it needs no ethics approval statement."
    )


def test_ethics_checks_an_empirical_paper_and_an_unsure_label() -> None:
    for paper in (_paper([LIVE], "empirical"), _paper([LIVE], "review", 0.4)):
        assert pc.module_run(paper, "ethics_check").traffic_light == "red"


def test_open_practices_skips_a_commentary() -> None:
    out = pc.module_run(_paper(["We comment on a paper."], "commentary"), "open_practices")
    assert out.traffic_light == "na"
    assert out.summary_text == (
        "bibr classifies the paper as a commentary, which reports no research of its own, "
        "so no shared data or code are expected."
    )
    assert out.report == out.summary_text


def test_open_practices_still_reports_sharing_in_a_commentary() -> None:
    paper = _paper(["Data are available at https://osf.io/abc."], "commentary")
    assert pc.module_run(paper, "open_practices").traffic_light == "yellow"


def test_open_practices_paper_list_counts_them() -> None:
    papers = pc.PaperList([_paper(["x"], "erratum"), _paper(["y"], "empirical")])
    out = pc.module_run(papers, "open_practices")
    assert out.summary_text == (
        "0 papers shared both data and code, 0 only data, 0 only code, and 2 neither; "
        "1 paper is not research (a commentary, correction or retraction)."
    )
