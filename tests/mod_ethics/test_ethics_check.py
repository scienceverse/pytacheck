"""Tests for the ethics_check module.

Ports tests/testthat/test-module-ethics_check.R, plus checks of R's quirks
(errors, report on a one-paper list, duplicated statements in the report)
measured with R 4.5.3 / metacheck, and of the Python-only sentence prefilter.
"""

from __future__ import annotations

import re
import re._parser as sre_parse  # type: ignore[import-not-found]
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck._r import grepl
from pytacheck.modules.ethics_check import _ETHICS_ANY, _ETHICS_WORDS
from tests.mod_ethics.make_parity_cases import SWEEP
from tests.mod_ethics.parity_support import ec_paper, ec_papers

MODULE = "ethics_check"


def run(paper: Any) -> Any:
    return pc.module_run(paper, MODULE)


def plist(*texts: str | list[str]) -> Any:
    """``paperlist(test_paper(...), ...)`` with fixed IDs p1, p2, ..."""
    return ec_papers(
        [f"p{i + 1}" for i in range(len(texts))],
        [[t] if isinstance(t, str) else t for t in texts],
    )


# -- test-module-ethics_check.R ---------------------------------------------


def test_ethics_check_listed() -> None:
    assert MODULE in pc.module_list()["name"].tolist()


def test_approved_and_needs_ethics() -> None:
    paper = pc.test_paper(
        [
            "Participants were recruited from a local community sample and gave informed consent.",
            "This study was approved by the institutional review board.",
        ]
    )
    mo = run(paper)
    assert mo.summary_table["ethics_approved"].tolist() == [True]
    assert mo.summary_table["needs_ethics"].tolist() == [True]
    assert mo.traffic_light == "green"

    live_statements = mo.summary_table["live_data_statements"].iloc[0]
    assert all(grepl("Participants were recruited", live_statements))
    assert "we would expect an ethics approval statement, and it was present" in mo.report
    assert "Participants were recruited" in mo.report
    assert "institutional review board" in mo.report


def test_not_approved_but_needs_ethics() -> None:
    paper = pc.test_paper(
        "Participants were recruited from a local community sample and gave informed consent."
    )
    mo = run(paper)
    assert mo.summary_table["ethics_approved"].tolist() == [False]
    assert mo.summary_table["needs_ethics"].tolist() == [True]
    assert mo.traffic_light == "red"

    live_statements = mo.summary_table["live_data_statements"].iloc[0]
    assert all(grepl("Participants were recruited", live_statements))
    assert "we would expect an ethics approval statement, but it was not present" in mo.report
    assert "Participants were recruited" in mo.report


def test_not_approved_and_does_not_need_ethics() -> None:
    paper = pc.test_paper("This paper presents a theoretical model of decision-making.")
    mo = run(paper)
    assert mo.summary_table["ethics_approved"].tolist() == [False]
    assert mo.summary_table["needs_ethics"].tolist() == [False]
    assert pd.isna(mo.summary_table["live_data_statements"].iloc[0])
    assert pd.isna(mo.summary_table["ethics_statements"].iloc[0])
    assert mo.traffic_light == "na"


def test_approved_but_does_not_need_ethics() -> None:
    paper = pc.test_paper(
        "No ethical approval was required for the completion of the study as there were no "
        "human or animal subjects used for the conduct of the research."
    )
    mo = run(paper)
    assert mo.summary_table["needs_ethics"].tolist() == [False]


def test_waiver_and_exemption_phrasing() -> None:
    mo = run(pc.test_paper("The study was deemed exempt by the institutional review board."))
    assert mo.table["ethics"].any()


def test_declaration_of_helsinki() -> None:
    mo = run(
        pc.test_paper(
            "The experiment was conducted in accordance with the Declaration of Helsinki."
        )
    )
    assert mo.table["ethics"].any()


def test_animal_research_committee() -> None:
    mo = run(pc.test_paper("All procedures were approved by the Animal Ethics Committee."))
    assert mo.table["ethics"].any()


def test_rec_reb_dec_abbreviations_match_only_near_ethics_context() -> None:
    # genuine approvals using short abbreviations should still match
    approved = plist(
        "REB approval for this study was obtained from Carleton University (#118953).",
        "The protocol was approved by the Research Ethics Board (REB) of the university.",
        "Approval was obtained from the local research ethics committee (REC).",
        "Ethical approval was granted by the Dierexperimentencommissie (DEC) of Utrecht "
        "University.",
    )
    mo = run(approved)
    assert mo.summary_table["ethics_approved"].tolist() == [True, True, True, True]

    # unrelated jargon that happens to contain REC/DEC/REB should NOT match
    not_approved = plist(
        "We used the recognition heuristic (REC/basic) to forecast the rank order of the "
        "election outcome.",
        "The number of the decision task (DEC) was logged for each trial.",
        "Regret priming has been shown to reduce decisional errors (Connolly & Reb, 2012).",
    )
    mo2 = run(not_approved)
    assert mo2.summary_table["ethics_approved"].tolist() == [False, False, False]


def test_approval_phrasing_without_leading_ethics_word() -> None:
    approved = plist(
        "All studies were approved by the Scientific Council of Research and Creation at the "
        "West University of Timisoara regarding compliance with ethical aspects in scientific "
        "research.",
        "The research received approval from the Basque Center on Cognition, Brain and "
        "Language (BCBL)'s Ethics and Scientific Committee (ref.: 030522SM).",
        "All experiments were approved by the University of Western Australia's Human "
        "Research Ethics Office.",
    )
    mo = run(approved)
    assert mo.summary_table["ethics_approved"].tolist() == [True, True, True]

    # near-miss phrasing that should NOT match
    not_approved = plist(
        "Approved the submitted version for publication: FGH, JDH, MP, AR, and FAS.",
        "The current research was performed in compliance with ethical guidelines at the "
        "Faculty of Psychology, University of Bergen.",
        "CloudResearch approved participants for high-quality survey responses.",
    )
    mo2 = run(not_approved)
    assert mo2.summary_table["ethics_approved"].tolist() == [False, False, False]


def test_ethics_check_paperlist() -> None:
    paper = pc.PaperList(
        [
            pc.test_paper(
                [
                    "Participants were recruited and gave informed consent.",
                    "This study was approved by the ethics committee.",
                ]
            ),
            pc.test_paper("This paper presents a theoretical model of decision-making."),
        ]
    )
    mo = run(paper)
    assert len(mo.summary_table) == 2
    assert mo.summary_table["ethics_approved"].tolist() == [True, False]
    assert mo.summary_table["needs_ethics"].tolist() == [True, False]


def test_error_argument_is_of_length_zero(psychsci: pc.PaperList) -> None:
    # R uses psychsci$`0956797617714811` (not in the fixtures); this fixture
    # paper has no ethics approval statement either (R 4.5.3 / metacheck:
    # ethics_approved FALSE, needs_ethics TRUE, traffic light red)
    mo = run(psychsci["0956797614522816"])
    assert mo.summary_table["ethics_approved"].tolist() == [False]
    assert mo.summary_table["needs_ethics"].tolist() == [True]
    assert mo.traffic_light == "red"


# -- R behaviour measured with metacheck ---------------------------------------


def test_output_elements_and_types() -> None:
    mo = run(pc.test_paper(["Participants were recruited.", "The IRB approved it."]))
    assert mo.keys()[-1] == "na_replace"
    assert mo.na_replace == {"ethics_approved": False}
    assert mo.section == "general"
    assert list(mo.summary_table.columns) == [
        "paper_id",
        "ethics_approved",
        "ethics_statements",
        "needs_ethics",
        "live_data_statements",
    ]
    assert list(mo.table.columns)[-2:] == ["ethics", "live_data"]
    assert mo.table["ethics"].tolist() == [True, pd.NA]
    assert mo.table["live_data"].tolist() == [pd.NA, True]
    assert str(mo.table["ethics"].dtype) == "boolean"


def test_summary_texts() -> None:
    mo = run(pc.test_paper("Participants were recruited."))
    assert mo.summary_text == (
        "1 of 1 paper appeared to involve live data collection and lacked an ethics approval "
        "statement."
    )
    mo = run(plist("Participants were recruited.", ["We were recruited.", "IRB approved."]))
    assert mo.traffic_light == "red"
    assert mo.summary_text.startswith("1 of 2 papers appeared")
    # a paper list gets no report: module_run() falls back to the summary text
    assert mo.report == mo.summary_text


def test_report_quotes_each_ethics_statement_once() -> None:
    # U102: metacheck quotes table$text[table$ethics], every row (duplicates kept)
    mo = run(
        pc.test_paper(
            [
                "Participants were recruited.",
                "The IRB approved it.",
                "Participants were recruited.",
                "The IRB approved it.",
            ]
        )
    )
    assert mo.summary_table["ethics_statements"].iloc[0] == ["The IRB approved it."]
    assert mo.report == (
        "Based on the following text, we would expect an ethics approval statement, and it "
        "was present:\n\n> Participants were recruited.\n\nEthics approval statement:\n\n"
        "> The IRB approved it."
    )


def test_one_paper_list_gets_a_report() -> None:
    mo = run(ec_papers(["solo"], [["The IRB approved it."]]))
    assert mo.traffic_light == "na"
    assert mo.report == (
        "An ethics approval statement was detected, based on the following text:\n\n"
        "> The IRB approved it."
    )


def test_rows_follow_paper_order() -> None:
    mo = run(
        ec_papers(
            ["z9", "a1"],
            [["The IRB approved it.", "Participants were recruited."], ["IRB approved."]],
        )
    )
    assert mo.table["paper_id"].tolist() == ["z9", "a1", "z9"]
    assert mo.summary_table["paper_id"].tolist() == ["z9", "a1"]


def test_references_are_not_searched() -> None:
    paper = ec_paper(
        ["Participants were recruited online.", "Approved by the ethics committee."],
        section_type=["method", "references"],
    )
    mo = run(paper)
    assert mo.traffic_light == "red"


@pytest.mark.parametrize("paper", [lambda: pc.PaperList([]), lambda: []])
def test_empty_paperlist_has_an_empty_result(paper: Any) -> None:
    # U79: metacheck stops ("Join columns in `x` must be present in the data.")
    out = direct(paper())
    assert out["traffic_light"] == "na"
    assert out["summary_table"].columns.tolist()[:2] == ["paper_id", "ethics_approved"]
    assert len(out["summary_table"]) == 0
    assert len(out["table"]) == 0
    assert out["report"] is None
    mo = run(pc.PaperList([]))
    assert mo.traffic_light == "na"
    assert len(mo.summary_table) == 0


def test_duplicated_paper_ids_are_summarised_together() -> None:
    # U101: metacheck stops with "factor level [2] is duplicated"
    papers = ec_papers(["d1", "d1"], [["The IRB approved it."], ["Participants were recruited."]])
    out = direct(papers)
    assert out["summary_table"]["paper_id"].tolist() == ["d1"]
    assert out["summary_table"]["ethics_approved"].tolist() == [True]
    assert out["summary_table"]["needs_ethics"].tolist() == [True]
    assert run(papers).traffic_light == "green"


def test_paper_without_info_row_is_summarised() -> None:
    # U102: metacheck stops on paper() ("invalid subscript type 'list'")
    mo = run(pc.paper())
    assert mo.traffic_light == "na"
    assert len(mo.summary_table) == 1


def test_does_not_mutate_input() -> None:
    paper = pc.test_paper(["Participants were recruited.", "The IRB approved it."])
    text = paper.text.copy()
    run(paper)
    pd.testing.assert_frame_equal(paper.text, text)


# -- review: inputs R handles in particular ways (measured with metacheck) ------------

_NOT_A_PAPER = (
    "The paper argument doesn't seem to be a scivrs_paper object or a list of paper objects"
)


def direct(paper: Any) -> dict[str, Any]:
    """The module function's own return value (R: the sourced ``ethics_check(paper)``)."""
    from pytacheck.modules.ethics_check import ethics_check

    return ethics_check(paper)


def _select_text(paper: Any, cols: list[str]) -> Any:
    paper.text = paper.text.loc[:, cols]
    return paper


def _no_info(paper: Any) -> Any:
    paper.info = paper.info.iloc[0:0]
    return paper


_FORMATTED_FIRST = ["formatted", "text_id", "section_id", "paragraph_id"]


@pytest.mark.parametrize(
    ("paper", "exc", "message"),
    [
        # text_search() runs first: a character vector per pattern, which bind_rows()
        # refuses; anything else that is not a paper, a paper list or a table
        (lambda: "The IRB approved it.", ValueError, "Argument 1 must be a data frame or"),
        (lambda: None, TypeError, _NOT_A_PAPER),
        (lambda: 5, TypeError, _NOT_A_PAPER),
        (lambda: [1, 2], TypeError, _NOT_A_PAPER),
        # a table passes text_search() but not paper_id()
        (
            lambda: ec_paper(["The IRB approved it."]).text,
            TypeError,
            "paper must be a paper or paperlist object.",
        ),
    ],
)
def test_direct_r_errors(paper: Any, exc: type[Exception], message: str) -> None:
    with pytest.raises(exc, match=re.escape(message)):
        direct(paper())


@pytest.mark.parametrize(
    "texts",
    [
        ["The IRB approved it.", "Participants were recruited."],
        ["Nothing.", "Participants were recruited."],
    ],
)
def test_text_table_without_text_column_has_no_sentences(texts: list[str]) -> None:
    # U102: metacheck searches the first column instead and stops with opaque
    # errors ("Assigned data `character(0)` ...", "`text` is graphics::text()")
    out = direct(_select_text(ec_paper(texts), _FORMATTED_FIRST))
    assert len(out["table"]) == 0
    assert out["traffic_light"] == "na"
    assert out["summary_table"]["ethics_approved"].tolist() == [False]


def test_paper_without_info_row_uses_its_own_id() -> None:
    # U102: metacheck stops ("invalid subscript type 'list'")
    out = direct(_no_info(ec_paper(["The IRB approved it."], "solo")))
    assert out["summary_table"]["paper_id"].tolist() == ["solo"]
    assert out["summary_table"]["ethics_approved"].tolist() == [True]


@pytest.mark.parametrize("container", [list, tuple, lambda ps: {"x": ps[0], "y": ps[1]}])
def test_plain_lists_of_papers(container: Any) -> None:
    # R's .is_paper_list(): any list made only of papers (list(p1, p2), list(x = p1, ...))
    papers = [
        ec_paper(["The IRB approved it."], "a"),
        ec_paper(["Participants were recruited."], "b"),
    ]
    out = direct(container(papers))
    ref = direct(pc.PaperList(papers))
    pd.testing.assert_frame_equal(out["table"], ref["table"])
    pd.testing.assert_frame_equal(out["summary_table"], ref["summary_table"])
    assert out["summary_table"]["paper_id"].tolist() == ["a", "b"]
    assert (out["traffic_light"], out["report"]) == ("red", None)
    assert out["summary_text"] == (
        "1 of 2 papers appeared to involve live data collection and lacked an ethics "
        "approval statement."
    )


def test_plain_list_of_one_paper_gets_a_report() -> None:
    out = direct([ec_paper(["Participants were recruited.", "The IRB approved it."], "a")])
    assert out["traffic_light"] == "green"
    assert out["report"] == (
        "Based on the following text, we would expect an ethics approval statement, and it "
        "was present:\n\n> Participants were recruited.\n\nEthics approval statement:\n\n"
        "> The IRB approved it."
    )


def test_text_table_without_text_column() -> None:
    # text_search() searches the first column (text_id) and drops `text`; the module
    # adds an empty `text` column after `ethics`
    paper = _select_text(
        ec_paper(["The IRB approved it.", "Participants were recruited."]),
        ["text_id", "section_id", "paragraph_id", "formatted"],
    )
    out = direct(paper)
    assert out["table"].columns.tolist() == [
        "text_id",
        "section_id",
        "paragraph_id",
        "formatted",
        "paper_id",
        "header",
        "section_type",
        "ethics",
        "text",
        "live_data",
    ]
    assert len(out["table"]) == 0
    assert out["traffic_light"] == "na"
    assert out["summary_table"]["ethics_approved"].tolist() == [False]
    assert out["summary_table"]["needs_ethics"].tolist() == [False]


def test_sentences_of_a_paper_without_info_row() -> None:
    # U102: a paper without an info row keeps its own ID (metacheck: its
    # sentences get an NA paper_id, it is left out of the summary, and the list
    # gets a one-paper report quoting the other paper's and its sentences)
    p1 = _no_info(ec_paper(["The IRB approved it.", "Participants were recruited."], "p1"))
    p2 = ec_paper(["The ethics committee approved it."], "p2")
    out = direct(pc.PaperList([p1, p2]))
    table = out["table"]
    assert table["paper_id"].tolist() == ["p1", "p2", "p1"]
    assert table["text"].tolist() == [
        "The IRB approved it.",
        "The ethics committee approved it.",
        "Participants were recruited.",
    ]
    assert out["summary_table"]["paper_id"].tolist() == ["p1", "p2"]
    assert out["summary_table"]["needs_ethics"].tolist() == [True, False]
    assert out["traffic_light"] == "green"
    assert out["report"] is None


def test_rows_sorted_by_text_id_with_missing_last() -> None:
    paper = ec_paper(
        ["The IRB approved it.", "Participants were recruited.", "Ethics approval was obtained."]
    )
    text = paper.text.copy()
    text["text_id"] = pd.array([None, 3, 1], dtype="Int64")
    paper.text = text
    out = direct(paper)
    ethics = out["table"].loc[out["table"]["ethics"].fillna(False).astype(bool)]
    assert ethics["text"].tolist() == ["Ethics approval was obtained.", "The IRB approved it."]


# -- the Python-only prefilter ---------------------------------------------------

_LITERALS = _ETHICS_ANY.split("|")


def _covered(items: Any, literals: list[str] = _LITERALS) -> bool:
    """Does every match of the parsed sequence *items* contain a prefilter literal?

    True when a run of literal characters contains one of the literals, or a
    group, alternation (all branches) or repeat (at least once) is covered.
    """
    run_chars: list[str] = []

    def flush() -> bool:
        s = "".join(run_chars).lower()
        run_chars.clear()
        return any(lit in s for lit in literals)

    for op, av in items:
        name = str(op)
        if name == "LITERAL":
            run_chars.append(chr(av))
            continue
        if flush():
            return True
        if name == "SUBPATTERN" and _covered(av[-1], literals):
            return True
        if name == "BRANCH" and all(_covered(b, literals) for b in av[1]):
            return True
        if name in ("MAX_REPEAT", "MIN_REPEAT") and av[0] >= 1 and _covered(av[2], literals):
            return True
    return flush()


@pytest.mark.parametrize("pattern", _ETHICS_WORDS)
def test_prefilter_covers_every_pattern(pattern: str) -> None:
    # the patterns are also valid Python regexes with the same structure
    assert _covered(sre_parse.parse(pattern))


def test_sweep_sentences_match_their_patterns() -> None:
    for pattern, sentence in zip(_ETHICS_WORDS, SWEEP, strict=False):
        assert grepl(pattern, [sentence], ignore_case=True) == [True], pattern


def test_prefilter_keeps_every_matching_sentence(fixtures_dir: Path) -> None:
    papers = pc.read(
        [
            *sorted((fixtures_dir / "psychsci").glob("*.json")),
            *sorted((fixtures_dir / "debruine").glob("*.xml")),
            fixtures_dir / "problems" / "0956797615569889.xml",
        ]
    )
    texts = [t for p in papers for t in p.text["text"].tolist()] + SWEEP
    keep = grepl(_ETHICS_ANY, texts, ignore_case=True)
    for pattern in _ETHICS_WORDS:
        hits = grepl(pattern, texts, ignore_case=True)
        assert all(k for h, k in zip(hits, keep, strict=True) if h), pattern


def test_prefiltered_search_equals_plain_search() -> None:
    paper = pc.test_paper(SWEEP)
    mo = run(paper)
    ethics = mo.table.loc[mo.table["ethics"].fillna(False).astype(bool)]
    ethics = ethics.drop(columns=["ethics", "live_data"]).reset_index(drop=True)
    plain = pc.text_search(paper, list(_ETHICS_WORDS))
    plain = plain.sort_values("text_id", kind="stable").reset_index(drop=True)
    pd.testing.assert_frame_equal(ethics, plain, check_dtype=False)
