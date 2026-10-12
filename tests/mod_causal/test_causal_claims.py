"""Tests for the causal_claims module (port of ``inst/modules/causal_claims.R``).

metacheck has no testthat tests for this module. These cover its branches
offline: the randomization patterns (expected values computed with R), the
causal-claim branches and traffic lights with a fake ``causal_relations()``,
and the real :func:`metacheck.text.causal.causal_relations` against a mocked
Gradio Space (respx). The parity cases (``parity/cases/mod_causal.yaml``)
compare the same paths with R.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import httpx
import pandas as pd
import pytest
import respx

import metacheck as pc
from metacheck.module import ModuleError
from metacheck.modules.causal_claims import _EXCLUDE_RE, _INCLUDE_RE
from metacheck.report.blocks import ReportTable
from parity.cases import ROOT
from tests.mod_causal.parity_support import fake_causal_relations, mk, run_fake_causal, untitled

BASE = "https://lakens-causal-sentences.hf.space/gradio_api/call/predict"

NO_RANDOM_REPORT = [
    "Metacheck's text matching algorithm did not identify sentences describing randomization. "
    "If random assignment was present, please clearly report this (e.g., participants were "
    "randomly assigned to...). If this was a non-randomized study, the journal article "
    "reporting standards (JARS) ask that you describe the following:",
    "- Procedures employed to help minimize potential bias due to nonrandomization "
    "(e.g., matching, propensity score matching).",
]


def _run(paper: Any) -> Any:
    return run_fake_causal(lambda: pc.module_run(paper, "causal_claims"))


def _tables(report: list[Any]) -> list[pd.DataFrame]:
    return [b.data for b in report if isinstance(b, ReportTable)]


def test_module_info() -> None:
    info = pc.module_info("causal_claims")
    assert info.title == "Randomization and Causal Claims"
    assert list(info.keywords) == ["method"]
    assert list(info.requires) == ["network"]
    assert list(info.author) == ["Daniel Lakens (\\email{D.Lakens@tue.nl})"]


# grepl(include_re / exclude_re, x, perl = TRUE) in R 4.5.3, except where marked
# U85 (British spellings and negations, which pytacheck handles and metacheck misses)
PATTERN_CASES = [
    ("Participants were randomly assigned to conditions.", True, False),
    ("Participants were Randomly Assigned to one of two groups.", True, False),
    ("Subjects were assigned at random to groups.", True, False),
    ("They were assigned randomly to the treatment.", True, False),
    ("Randomization was done by computer.", True, False),
    ("We randomise participants.", True, False),  # U85
    ("The sample was randomly divided into two halves.", True, False),
    ("Stratified random assignment was used.", True, False),
    ("We fit a model with random effects of participant.", False, True),
    ("Participants were randomly assigned and random intercepts were included.", True, True),
    ("Random slopes were included.", False, True),
    ("Trials were presented in random order and participants were randomized.", True, True),
    ("Pseudo-randomized sequences were randomized.", True, True),
    ("A random digit-dialing sample was randomized.", True, True),
    ("Successful random assignment was checked.", True, True),
    ("Participants were randomly\nassigned.", True, False),
    ("random assignment with non-breaking space", False, False),
    ("randomisation (British spelling) was used.", True, False),  # U85
    ("Participants were not randomized.", True, True),  # U85
    ("This was a non-randomised study.", True, True),  # U85
    ("Participants were not randomly assigned to groups.", True, True),  # U85
    ("There was no random assignment to conditions.", True, True),  # U85
    ("Stimuli were shown in randomised order.", True, True),  # U85
    ("We RANDOMIZED everything.", True, False),
    ("Participants (N = 40) were randomly-assigned to groups.", False, False),
    ("randomizedness is a word", False, False),
    ("Pixels were random-pixel masks and participants were randomized.", True, True),
]


@pytest.mark.parametrize(("text", "inc", "exc"), PATTERN_CASES)
def test_patterns(text: str, inc: bool, exc: bool) -> None:
    assert pc._r.grepl(_INCLUDE_RE, [text], perl=True) == [inc]
    assert pc._r.grepl(_EXCLUDE_RE, [text], perl=True) == [exc]


def test_no_randomization_no_causal_claims() -> None:
    paper = untitled(["We included random effects.", "Nothing else."], id="p")
    res = pc.module_run(paper, "causal_claims")  # no sentences: no network
    assert res["traffic_light"] == "green"
    assert res["summary_text"] == (
        "\n-  We identified no sentences describing randomization."
        "\n-  No causal claims were observed in the title."
        "\n-  No causal claims were observed in the abstract."
    )
    report = res["report"]
    assert report[1:4] == ["#### Randomization", *NO_RANDOM_REPORT]
    assert report[4:8] == [
        "#### Causal Claims",
        "No causal claims were observed in the title.",
        "No causal claims were observed in the abstract.",
        "",
    ]
    assert report[8] == ""  # empty scroll_table()
    assert report[9].startswith('::: {.callout-tip title="Learn More" collapse="true"}')
    assert "Antonakis J, Bendahan S, Jacquart P, Lalive R (2010)" in report[9]
    assert list(res["table"].columns) == ["sentence", "causal", "cause", "effect"]
    assert len(res["table"]) == 0
    assert res["summary_table"]["paper_id"].tolist() == ["p"]
    assert res["summary_table"]["causal"].tolist() == [0]


def test_randomization_sentences() -> None:
    texts = [
        "Participants were randomly assigned to one of three conditions.",
        "Randomization was performed with a computer script.",
        "A random sample of 200 adults was drawn.",
        # U85: "random order" is not assignment, "participants were randomized" is
        # (metacheck drops the whole sentence)
        "Trials were presented in random order after participants were randomized.",
    ]
    res = pc.module_run(untitled(texts), "causal_claims")
    assert res["summary_text"].startswith(
        "\n-  We identified 3 sentences describing randomization."
    )
    report = res["report"]
    assert report[2] == "We identified 3 sentences describing randomization."
    (table,) = _tables(report)
    assert table.iloc[:, 0].tolist() == [texts[0], texts[1], texts[3]]
    assert report[4].startswith("If this was a study that contained random assignment")
    assert report[5].startswith("1. Random assignment method")
    assert res["traffic_light"] == "green"

    one = pc.module_run(untitled(texts[:1], title="  "), "causal_claims")
    assert one["report"][2] == "We identified 1 sentence describing randomization."


def test_empty_paper() -> None:
    res = pc.module_run(pc.paper(), "causal_claims")
    assert res["traffic_light"] == "green"
    assert len(res["table"]) == 0


def test_na_title_is_skipped() -> None:
    # U84: metacheck stops on causal_relations(NA) ("missing value where TRUE/FALSE needed")
    res = pc.module_run(untitled(["Some text."], title=None), "causal_claims")
    assert res["traffic_light"] == "green"
    assert "No causal claims were observed in the title." in res["report"]


def test_describes_randomization() -> None:
    # U85: an exclusion removes only its own words; negations do not count
    from metacheck.modules.causal_claims import _describes_randomization

    texts = pd.Series(
        [
            "Participants were randomly assigned to conditions, and we modelled random intercepts.",
            "Participants were randomised to conditions.",
            "Participants were not randomized.",
            "This was a non-randomized study.",
            "Without random assignment, causal claims are limited.",
            "Participants had not been randomly allocated.",
            "Stimuli were presented in randomised order.",
            "We fit a model with random effects of participant.",
            "Participants were randomly assigned; those not randomized were excluded.",
        ]
    )
    assert _describes_randomization(texts).tolist() == [
        True, True, False, False, False, False, False, False, True,
    ]  # fmt: skip


def test_causal_abstract_without_randomization_is_yellow() -> None:
    paper = mk(["Stress causes poor sleep.", "We surveyed adults."], ["Method text."], id="a")
    res = _run(paper)
    assert res["traffic_light"] == "yellow"
    assert res["table"].to_dict("list") == {
        "sentence": ["Stress causes poor sleep."],
        "causal": [True],
        "cause": ["Stress"],
        "effect": ["sleep."],
    }
    assert "Causal claims were detected in the abstract." in res["report"]
    assert res["report"][-2].startswith("As no random assignment to conditions was detected")
    assert res["summary_table"]["causal"].tolist() == [1]


def test_causal_abstract_with_randomization_is_green() -> None:
    paper = mk(
        ["Exercise causes better mood and higher energy."],
        ["Participants were randomly assigned to conditions."],
        id="b",
    )
    res = _run(paper)
    assert res["traffic_light"] == "green"
    assert res["table"]["cause"].tolist() == ["Exercise", "causes"]
    assert res["report"][-2] == (
        "Random assignment was detected, so these causal claims might be warranted, "
        "but it is always prudent to double-check."
    )
    assert res["summary_table"]["causal"].tolist() == [2]


def test_causal_title() -> None:
    paper = mk(["We measured errors."], [], title="Sleep loss causes errors and slips", id="c")
    res = _run(paper)
    assert res["traffic_light"] == "yellow"
    assert "\n-  Causal claims were detected in the title." in res["summary_text"]
    i = res["report"].index("Causal claims were detected in the title.")
    title_table = res["report"][i + 1]
    assert isinstance(title_table, ReportTable)
    assert title_table.colwidths == 1
    assert title_table.data.to_dict("list") == {
        "sentence": ["Sleep loss causes errors and slips"] * 2,
        "cause": ["Sleep", "loss"],
        "effect": ["slips", "and"],
    }
    # no causal abstract sentence: no abstract table, empty follow-up text
    assert res["report"][i + 2 : i + 5] == [
        "No causal claims were observed in the abstract.",
        "",
        "",
    ]
    assert len(res["table"]) == 0
    assert res["summary_table"]["causal"].tolist() == [0]  # sum(FALSE)


def test_paper_list_titles_and_counts_per_paper() -> None:
    papers = pc.PaperList(
        [
            mk(["X causes Y.", "Same sentence here."], [], title="Heat causes thirst", id="p1"),
            mk(["X causes Y."], ["Groups were randomly assigned."], id="p2"),
            untitled(["No abstract here."], id="p3"),
        ]
    )
    calls: list[Any] = []

    def recording(sentence: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
        calls.append(list(sentence))
        return fake_causal_relations(sentence)

    with mock.patch("metacheck.text.causal.causal_relations", recording):
        res = pc.module_run(papers, "causal_claims")
    # U84: every paper's title is classified (metacheck reads paper$info$title,
    # NULL for a paper list), blank and missing titles are skipped
    assert calls == [["Heat causes thirst"], ["X causes Y.", "Same sentence here.", "X causes Y."]]
    assert res["traffic_light"] == "green"  # p2 describes random assignment
    assert "Causal claims were detected in the title." in res["report"]
    # U84: each paper counts its own sentences (metacheck's join by text counted
    # the repeated sentence twice for both papers: 2, 2, 0)
    assert res["summary_table"].to_dict("list") == {
        "paper_id": ["p1", "p2", "p3"],
        "causal": [1, 1, 0],
    }


def test_repeated_sentence_counts_once_per_occurrence() -> None:
    # U84: metacheck counts a sentence repeated k times k * k times (4 here)
    paper = mk(["X causes Y.", "X causes Y.", "Nothing."], [], id="dup")
    res = _run(paper)
    assert res["summary_table"].to_dict("list") == {"paper_id": ["dup"], "causal": [2]}


def test_does_not_mutate_paper() -> None:
    paper = pc.demopaper()
    before = paper.copy()
    _run(paper)
    assert paper == before


def _sse(payload: str) -> str:
    return f"event: generating\ndata: null\n\nevent: complete\ndata: {payload}\n\n"


def test_with_mocked_space() -> None:
    """The real causal_relations() against a recorded-style Gradio exchange."""
    results = {
        "Smoking causes cancer and heart disease.": [
            {
                "causal": True,
                "relations": [
                    {"cause": "Smoking", "effect": "heart disease"},
                    {"cause": "Smoking", "effect": "cancer"},
                ],
            }
        ],
        "We surveyed 300 adults.": [{"causal": False, "relations": []}],
        "Tobacco use kills": [{"causal": True, "relations": [{"cause": "Tobacco use"}]}],
    }
    events: dict[str, str] = {}

    def post(request: httpx.Request) -> httpx.Response:
        sentence = json.loads(request.content)["data"][0]
        eid = f"ev{len(events) + 1}"
        events[eid] = json.dumps([json.dumps(results[sentence])])
        return httpx.Response(200, json={"event_id": eid})

    paper = mk(
        ["Smoking causes cancer and heart disease.", "We surveyed 300 adults."],
        ["Participants were not randomized."],
        title="Tobacco use kills",
        id="smk",
    )
    with respx.mock() as router:
        router.post(BASE).mock(side_effect=post)
        router.get(url__regex=rf"{BASE}/ev\d+").mock(
            side_effect=lambda request: httpx.Response(
                200,
                text=_sse(events[request.url.path.rsplit("/", 1)[-1]]),
                headers={"content-type": "text/event-stream"},
            )
        )
        res = pc.module_run(paper, "causal_claims")
    # U85: "not randomized" is not random assignment (metacheck: green)
    assert res["traffic_light"] == "yellow"
    assert res["table"]["effect"].tolist() == ["cancer", "heart disease"]
    i = res["report"].index("Causal claims were detected in the title.")
    title_table = res["report"][i + 1].data
    assert title_table["cause"].tolist() == ["Tobacco use"]
    assert title_table["effect"].isna().tolist() == [True]
    assert res["summary_table"].to_dict("list") == {"paper_id": ["smk"], "causal": [2]}


# -- review: branches the first cases did not reach -----------------------------


def test_any_follows_r_if_any() -> None:
    from metacheck.modules.causal_claims import _any

    assert _any(pd.Series([], dtype="boolean")) is False
    assert _any(pd.Series([False, False], dtype="boolean")) is False
    assert _any(pd.Series([pd.NA, True], dtype="boolean")) is True
    # R: if (!any(c(NA, FALSE))) -> "missing value where TRUE/FALSE needed"
    with pytest.raises(ValueError, match="missing value where TRUE/FALSE needed"):
        _any(pd.Series([pd.NA, False], dtype="boolean"))


def test_na_causal_flag_keeps_r_semantics() -> None:
    from tests.mod_causal.parity_support import run_fake_causal_na

    paper = mk(["X causes Y.", "Maybe noise causes Z.", "Nothing."], [], title="", id="na1")
    res = run_fake_causal_na(lambda: pc.module_run(paper, "causal_claims"))
    # dplyr::filter() drops the NA row; sum() gives NA, which na_replace turns into 0
    assert res["table"]["sentence"].tolist() == ["X causes Y."]
    assert res["summary_table"].to_dict("list") == {"paper_id": ["na1"], "causal": [0]}
    assert res["traffic_light"] == "yellow"


def test_no_text_column_is_a_join_error() -> None:
    # R: text_search() returns no `text` column, so the left_join() fails
    paper = untitled(["Participants were randomly assigned."], id="nt")
    paper.text = paper.text.drop(columns=["text"])
    with pytest.raises(ModuleError, match=r"Join columns in `y` must be present in the data\."):
        pc.module_run(paper, "causal_claims")


def test_empty_paper_list() -> None:
    # U79: metacheck's text_search() of an empty list has no `text` column, so the
    # module failed ("Join columns in `y` must be present"); an empty list has
    # nothing to report
    res = pc.module_run(pc.PaperList([]), "causal_claims")
    assert res.traffic_light == "green"
    assert len(res.table) == 0
    assert len(res.summary_table) == 0
    assert "We identified no sentences describing randomization." in res.summary_text


def test_references_are_not_searched() -> None:
    from tests.mod_causal.parity_support import with_section_types

    paper = with_section_types(
        mk(["X causes Y."], ["Participants were randomly assigned."], title="", id="refs"),
        ["abstract", "references"],
    )
    res = _run(paper)
    assert res["summary_text"].startswith("\n-  We identified no sentences describing")
    assert res["traffic_light"] == "yellow"


@pytest.mark.parametrize("which", ["demo", "psychsci"])
def test_one_search_equals_rs_two_searches(which: str) -> None:
    """R searches twice (text_search(paper, "random") and text_search(paper));
    the port filters one search: the randomization sentences must be the same."""
    from metacheck._r import grepl
    from metacheck.modules.causal_claims import _EXCLUDE_RE, _INCLUDE_RE
    from tests.mod_causal.gen_parity_cases import PSYCHSCI

    paper = (
        pc.demopaper() if which == "demo" else pc.PaperList(pc.read([ROOT / p for p in PSYCHSCI]))
    )
    direct = pc.text_search(paper, "random")["text"]
    keep = [
        i and not e
        for i, e in zip(
            grepl(_INCLUDE_RE, direct, perl=True),
            grepl(_EXCLUDE_RE, direct, perl=True),
            strict=True,
        )
    ]
    expected = direct[keep].tolist()
    res = _run(paper)
    n = len(expected)
    if n == 0:
        assert NO_RANDOM_REPORT[0] in res["report"]
    else:
        i = res["report"].index(
            f"We identified {n} sentence{'' if n == 1 else 's'} describing randomization."
        )
        assert res["report"][i + 1].data.iloc[:, 0].tolist() == expected


def test_report_tables_render_like_r() -> None:
    """Report tables render to raw HTML blocks in the .qmd (D75; R writes R chunks)."""
    from tests.mod_causal.parity_support import report_qmd

    paper = mk(["X causes Y."], ["Participants were randomly assigned."], title="A causes B")
    report = report_qmd(_run(paper))
    chunks = [s for s in report if "```{=html}" in s]
    assert len(chunks) == 3
    assert '<colgroup><col style="width:100%"><col><col></colgroup>' in chunks[1]
    assert "<colgroup>" not in chunks[0]
    assert "<td>Participants were randomly assigned.</td>" in chunks[0]
