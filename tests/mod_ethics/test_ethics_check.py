"""Tests for the ethics_check module.

Ports tests/testthat/test-module-ethics_check.R, plus checks of R's quirks
(errors, report on a one-paper list, duplicated statements in the report)
measured with R 4.5.3 / metacheck, and of the literal prefilter.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck._r import grepl
from metacheck.core.errors import PytacheckWarning
from metacheck.modules.ethics_check import _ETHICS_WORDS
from tests.mod_ethics.make_parity_cases import SWEEP
from tests.mod_ethics.parity_support import ds_paper, ec_paper, ec_papers

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


def test_own_data_with_and_without_a_statement() -> None:
    own = "Participants were recruited online and gave informed consent."
    mo = run(ds_paper(body=[own], header="Participants"))
    st = mo.summary_table
    assert st["data_source"].tolist() == ["own"]
    assert st["needs_ethics"].tolist() == [True]
    assert st["ethics_approved"].tolist() == [False]
    assert mo.traffic_light == "yellow"  # yellow, not red (#695, point 2)
    assert "consent; recruitment" in st["data_source_signs"].iloc[0]
    assert st["data_source_statements"].iloc[0] == [own]
    assert "collected from people or animals, but we found no ethics statement" in mo.report
    assert "describe someone else's study in the same words" in mo.report
    assert own in mo.report

    mo = run(
        ds_paper(
            body=[own, "This study was approved by the institutional review board."],
            header="Participants",
        )
    )
    assert mo.summary_table["ethics_approved"].tolist() == [True]
    assert mo.traffic_light == "green"
    assert "and it has an ethics statement" in mo.report
    assert "institutional review board" in mo.report
    # "this study was approved" is not listed as a reason for the class "own"
    assert "records" not in mo.summary_table["data_source_signs"].iloc[0]


def test_one_sign_of_data_collection_needs_a_method_part_or_a_second_sign() -> None:
    s = "Participants were recruited online."
    mo = run(ds_paper(body=[s], header="Participants"))
    assert mo.summary_table["data_source"].tolist() == ["own"]
    mo = run(ds_paper(body=[s], header="Discussion"))
    assert mo.summary_table["data_source"].tolist() == ["none"]
    assert mo.traffic_light == "na"
    assert "at most one sign that the authors collected data" in mo.report
    # a negation near a participant phrase is no statement that there are no data
    mo = run(ds_paper(body=[s, "No participants were excluded."], header="Participants"))
    assert mo.summary_table["data_source"].tolist() == ["own"]


def test_sentences_about_other_peoples_studies_are_not_the_authors_own() -> None:
    mo = run(
        ds_paper(
            body=[
                "For instance, in a study by Mazar and Zhong (2010, study 3) participants were "
                "recruited online and gave informed consent."
            ],
            header="Introduction",
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["none"]
    # a year is not a citation
    mo = run(
        ds_paper(
            body=["In 2021, participants were recruited online and gave informed consent."],
            header="Introduction",
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["own"]


def test_papers_without_data_about_people_need_no_statement() -> None:
    mo = run(
        ds_paper(
            abstract=["We conducted a systematic review and meta-analysis of 35 studies."],
            body=[
                "Databases were searched in May 2024.",
                "The 21 included studies enrolled a total of 7,035 participants who gave "
                "informed consent.",
            ],
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["none"]
    assert mo.summary_table["needs_ethics"].tolist() == [False]
    assert "a review or meta-analysis named in the title or abstract" in mo.report

    mo = run(
        ds_paper(
            abstract=["In this commentary, we argue that preregistration is useful."],
            body=["Participants were recruited online in the original study (Smith, 2019)."],
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["none"]

    mo = run(
        ds_paper(
            body=[
                "No human subjects were involved in this study.",
                "We analysed country-level mortality data.",
                "Aggregate data were taken from Eurostat.",
            ]
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["none"]
    assert "figures about groups" in mo.summary_table["data_source_signs"].iloc[0]

    mo = run(pc.test_paper("This paper presents a theoretical model of decision-making."))
    assert mo.summary_table["data_source"].tolist() == ["none"]
    assert mo.summary_table["needs_ethics"].tolist() == [False]
    assert pd.isna(mo.summary_table["ethics_statements"].iloc[0])
    assert mo.traffic_light == "na"


@pytest.mark.parametrize("src", ["UK Biobank", "British Cohort Study"])
def test_open_resources_need_no_statement(src: str) -> None:
    mo = run(
        ds_paper(
            abstract=[f"We analysed data from the {src} ."],
            body=[
                f"Data were obtained from the {src} resource.",
                "Participants had a mean age of 56 years.",
            ],
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["open_resource"]
    assert mo.summary_table["needs_ethics"].tolist() == [False]
    assert "open resource, which needs no ethics statement" in mo.report


def test_obtained_records_need_a_statement() -> None:
    mo = run(
        ds_paper(
            abstract=["This retrospective cohort study used electronic health records."],
            body=[
                "We retrospectively reviewed the records of 412 patients treated at the "
                "university hospital.",
                "Consent was waived because the data were anonymised.",
            ],
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["existing_records"]
    assert mo.summary_table["needs_ethics"].tolist() == [True]
    assert mo.traffic_light == "yellow"
    assert "records about individual people that the authors obtained for this study" in mo.report

    # existing data from a source the module cannot identify: the report says so
    mo = run(
        ds_paper(
            abstract=["This is a secondary analysis of existing data."],
            body=[
                "We used data from the Healthy Minds Study.",
                "The analytic sample comprised 2,004 students.",
            ],
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["existing_unclear"]
    assert mo.summary_table["needs_ethics"].tolist() == [True]
    assert "we could not tell where the data came from" in mo.report
    assert "UK Biobank or NHANES" in mo.report


def test_protocols_uncertain_papers_and_papers_without_text() -> None:
    mo = run(
        ds_paper(
            body=[
                "Participants will be recruited online and will be asked to give informed consent."
            ]
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["own"]
    assert "future tense" in mo.summary_table["data_source_signs"].iloc[0]

    mo = run(ds_paper(body=["We tested the model on new data."]))
    assert mo.summary_table["data_source"].tolist() == ["uncertain"]
    assert pd.isna(mo.summary_table["needs_ethics"].iloc[0])
    assert mo.traffic_light == "yellow"
    assert "We could not tell whether this paper reports data about people or animals" in mo.report
    assert "we could not tell whether it reports data" in mo.summary_text

    mo = run(pc.paper("empty"))
    assert mo.summary_table["data_source"].tolist() == ["no_text"]
    assert mo.traffic_light == "na"


def test_ethics_check_reads_the_results_of_modules_that_ran_before_it() -> None:
    p = ds_paper(
        body=[
            "Participants were recruited online.",
            "The groups differed, t(28) = 2.40, p = .023.",
        ],
        header="Participants",
    )
    out = pc.report_module_run(p, ["stat_check", "ethics_check"])
    signs = out["ethics_check"].summary_table["data_source_signs"].iloc[0]
    assert "results of other modules" in signs


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
            ds_paper(
                body=[
                    "Participants were recruited and gave informed consent.",
                    "This study was approved by the ethics committee.",
                ],
                header="Participants",
            ),
            pc.test_paper("This paper presents a theoretical model of decision-making."),
        ]
    )
    mo = run(paper)
    assert len(mo.summary_table) == 2
    assert mo.summary_table["ethics_approved"].tolist() == [True, False]
    assert mo.summary_table["needs_ethics"].tolist() == [True, False]
    assert mo.traffic_light == "green"


def test_error_argument_is_of_length_zero(psychsci: pc.PaperList) -> None:
    # R uses psychsci$`0956797617714811` (not in the fixtures); this fixture
    # paper has no ethics approval statement either (R 4.5.3 / metacheck:
    # ethics_approved FALSE, needs_ethics TRUE, traffic light red)
    mo = run(psychsci["0956797614522816"])
    assert mo.summary_table["ethics_approved"].tolist() == [False]
    assert mo.summary_table["needs_ethics"].tolist() == [True]
    assert mo.traffic_light == "yellow"


# -- R behaviour measured with metacheck ---------------------------------------


_OWN = "Participants were recruited online and gave informed consent."


def own_paper(extra: list[str] | None = None, id: str = "p1") -> Any:
    """A paper whose data are the authors' own, with the statements *extra* after the method."""
    return ds_paper(body=[_OWN, *(extra or [])], header="Participants", id=id)


def test_output_elements_and_types() -> None:
    mo = run(own_paper(["The IRB approved it."]))
    assert mo.keys()[-1] == "na_replace"
    assert mo.na_replace == {"ethics_approved": False}
    assert mo.section == "general"
    assert list(mo.summary_table.columns) == [
        "paper_id",
        "ethics_approved",
        "ethics_statements",
        "data_source",
        "needs_ethics",
        "data_source_signs",
        "data_source_statements",
    ]
    assert list(mo.table.columns)[-3:] == ["ethics", "sign", "live_data"]
    # a statement row, then the sentence the data source rests on
    assert mo.table["ethics"].tolist() == [True, pd.NA]
    assert mo.table["sign"].tolist() == [pd.NA, "own"]
    assert mo.table["live_data"].tolist() == [pd.NA, True]
    assert str(mo.table["ethics"].dtype) == "boolean"


def test_summary_texts() -> None:
    mo = run(own_paper())
    assert mo.summary_text == (
        "1 of 1 paper appeared to report data about people or animals that need an ethics "
        "statement, and lacked one."
    )
    mo = run(
        pc.PaperList(
            [
                own_paper(id="p1"),
                ds_paper(body=["We tested it.", "IRB approved."], id="p2"),
            ]
        )
    )
    assert mo.traffic_light == "yellow"
    assert mo.summary_text.startswith("1 of 2 papers appeared")
    # a paper list gets no report: module_run() falls back to the summary text
    assert mo.report == mo.summary_text
    # nothing needs a statement
    mo = run(pc.test_paper("Participants were recruited."))
    assert mo.traffic_light == "na"
    assert mo.summary_text == (
        "No papers appeared to report data about people or animals that need an ethics statement."
    )
    mo = run(own_paper(["The IRB approved it."]))
    assert mo.summary_text == (
        "All papers that appeared to report data about people or animals that need an ethics "
        "statement contained one."
    )


def test_report_quotes_each_ethics_statement_once() -> None:
    # U102: metacheck quoted table$text[table$ethics], every row (duplicates kept)
    mo = run(
        ds_paper(
            body=[_OWN, "The IRB approved it.", _OWN, "The IRB approved it."], header="Participants"
        )
    )
    assert mo.summary_table["ethics_statements"].iloc[0] == ["The IRB approved it."]
    assert mo.report == (
        "The paper appears to report data that the authors collected from people or animals, "
        "and it has an ethics statement.\n\n"
        f"We based this on:\n\n> {_OWN}\n\n"
        "Ethics statement:\n\n> The IRB approved it."
    )


def test_one_paper_list_gets_a_report() -> None:
    mo = run(ec_papers(["solo"], [["The IRB approved it."]]))
    assert mo.traffic_light == "na"
    assert mo.summary_table["data_source"].tolist() == ["none"]
    assert mo.report.endswith("Ethics statement:\n\n> The IRB approved it.")
    assert mo.report.startswith("The paper does not appear to report data about individual")


def test_rows_follow_paper_order() -> None:
    mo = run(
        pc.PaperList(
            [
                own_paper(["The IRB approved it."], id="z9"),
                ds_paper(body=["IRB approved."], id="a1"),
            ]
        )
    )
    # statement rows first, then the sentences the data source rests on
    assert mo.table["paper_id"].tolist() == ["z9", "a1", "z9"]
    assert mo.summary_table["paper_id"].tolist() == ["z9", "a1"]


def test_references_are_not_searched() -> None:
    paper = ec_paper(
        ["Participants were recruited online.", "Approved by the ethics committee."],
        section_type=["method", "references"],
    )
    mo = run(paper)
    assert mo.summary_table["ethics_approved"].tolist() == [False]
    # the one sign of data collection is in a method part
    assert mo.summary_table["data_source"].tolist() == ["none"]


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


def test_duplicated_paper_ids_are_two_papers() -> None:
    # U101: metacheck stops with "factor level [2] is duplicated"; U208: the second paper
    # is renamed `d1~2`, so each is summarised on its own
    papers = pc.PaperList([ds_paper(body=["The IRB approved it."], id="d1"), own_paper(id="d1")])
    with pytest.warns(PytacheckWarning, match="'d1' as 'd1~2'"):
        out = direct(papers)
    assert out["summary_table"]["paper_id"].tolist() == ["d1", "d1~2"]
    assert out["summary_table"]["ethics_approved"].tolist() == [True, False]
    assert out["summary_table"]["data_source"].tolist() == ["uncertain", "own"]
    assert out["summary_table"]["needs_ethics"].isna().tolist() == [True, False]
    with pytest.warns(PytacheckWarning):
        assert run(papers).traffic_light == "yellow"


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
    from metacheck.modules.ethics_check import ethics_check

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
    papers = [ds_paper(body=["The IRB approved it."], id="a"), own_paper(id="b")]
    out = direct(container(papers))
    ref = direct(pc.PaperList(papers))
    pd.testing.assert_frame_equal(out["table"], ref["table"])
    pd.testing.assert_frame_equal(out["summary_table"], ref["summary_table"])
    assert out["summary_table"]["paper_id"].tolist() == ["a", "b"]
    assert (out["traffic_light"], out["report"]) == ("yellow", None)
    assert out["summary_text"] == (
        "1 of 2 papers appeared to report data about people or animals that need an ethics "
        "statement, and lacked one."
    )


def test_plain_list_of_one_paper_gets_a_report() -> None:
    out = direct([own_paper(["The IRB approved it."], id="a")])
    assert out["traffic_light"] == "green"
    assert out["report"] == (
        "The paper appears to report data that the authors collected from people or animals, "
        "and it has an ethics statement.\n\n"
        f"We based this on:\n\n> {_OWN}\n\n"
        "Ethics statement:\n\n> The IRB approved it."
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
        "sign",
        "live_data",
    ]
    assert len(out["table"]) == 0
    assert out["traffic_light"] == "na"
    assert out["summary_table"]["ethics_approved"].tolist() == [False]
    # U102: no sentences, so nothing to read a data source from
    assert out["summary_table"]["data_source"].tolist() == ["no_text"]
    assert out["summary_table"]["needs_ethics"].isna().tolist() == [True]


def test_sentences_of_a_paper_without_info_row() -> None:
    # U102: a paper without an info row keeps its own ID (metacheck: its
    # sentences get an NA paper_id, it is left out of the summary, and the list
    # gets a one-paper report quoting the other paper's and its sentences)
    p1 = _no_info(ds_paper(body=["The IRB approved it.", "Participants were recruited."], id="p1"))
    p2 = ec_paper(["The ethics committee approved it."], "p2")
    out = direct(pc.PaperList([p1, p2]))
    table = out["table"]
    assert table["paper_id"].tolist() == ["p1", "p2", "p1"]
    assert table["sign"].tolist() == [pd.NA, pd.NA, "own"]
    assert table["text"].tolist() == [
        "The IRB approved it.",
        "The ethics committee approved it.",
        "Participants were recruited.",
    ]
    assert out["summary_table"]["paper_id"].tolist() == ["p1", "p2"]
    assert out["summary_table"]["data_source"].tolist() == ["own", "none"]
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


# -- the literal prefilter never changes the result ---------------------------------


def test_sweep_sentences_match_their_patterns() -> None:
    # every pattern is the one that finds at least one sentence of the sweep (the last six
    # are near misses)
    sentences = SWEEP[:-6]
    for pattern in _ETHICS_WORDS:
        assert any(grepl(pattern, sentences, ignore_case=True)), pattern


def _ethics_rows(table: pd.DataFrame) -> pd.DataFrame:
    rows = table.loc[table["ethics"].fillna(False).astype(bool)]
    rows = rows.drop(columns=["ethics", "sign", "live_data"], errors="ignore")
    return rows.sort_values(["paper_id", "text_id"], kind="stable").reset_index(drop=True)


def test_module_search_equals_plain_search() -> None:
    paper = pc.test_paper(SWEEP)
    mo = run(paper)
    ethics = mo.table.loc[mo.table["ethics"].fillna(False).astype(bool)]
    ethics = ethics.drop(columns=["ethics", "sign", "live_data"]).reset_index(drop=True)
    plain = pc.text_search(paper, list(_ETHICS_WORDS))
    plain = plain.sort_values("text_id", kind="stable").reset_index(drop=True)
    pd.testing.assert_frame_equal(ethics, plain, check_dtype=False)


def test_module_search_equals_the_search_before_the_core(fixtures_dir: Path) -> None:
    # the frozen text_search() of tests/_legacy/ has no literal prefilter at all
    from tests._legacy import search as legacy

    papers = pc.read(
        [
            *sorted((fixtures_dir / "psychsci").glob("*.json")),
            *sorted((fixtures_dir / "debruine").glob("*.xml")),
            fixtures_dir / "problems" / "0956797615569889.xml",
        ]
    )
    for source in (papers, pc.test_paper(SWEEP)):
        expected = legacy.text_search(source, list(_ETHICS_WORDS))
        got = _ethics_rows(run(source).table)
        expected = expected.sort_values(["paper_id", "text_id"], kind="stable")
        assert len(got) > 0
        pd.testing.assert_frame_equal(got, expected.reset_index(drop=True), check_dtype=False)


# -- 0.4.4.37: .detect_no_participants() is gone, .data_source() decides ----------


def test_a_paper_that_says_its_subjects_were_not_recruited_needs_no_ethics() -> None:
    # dq96z_v2 (0.4.3.28): the participant phrase "subjects recruited" is in the sentence
    # that says the professionals were not subjects; one sign is not enough for "own"
    mo = run(
        pc.test_paper(
            "The professionals described their own work as collaborators in the projects, "
            "not as subjects recruited for a study."
        )
    )
    assert mo.summary_table["data_source"].tolist() == ["none"]
    assert mo.summary_table["needs_ethics"].tolist() == [False]
    assert mo.traffic_light == "na"
    assert "not as subjects recruited" not in mo.report
    assert "states_no_participants" not in mo.summary_table.columns


def test_a_statement_of_no_participants_does_not_hide_a_participants_part() -> None:
    # R 0.4.4.40 measured: a Participants heading and a recruitment sentence make the data
    # the authors' own, whatever else the paper says about human subjects
    for s in [
        "No human subjects were involved in this study.",
        "As this study did not involve human subjects, institutional review board approval "
        "was not required.",
        "No participants were excluded.",
        "No participants were minors.",
        "Only children without G6PD deficiency were enrolled in the study.",
        "This study would not have been possible without our participants.",
        "No participant was included more than once across training samples.",
    ]:
        mo = run(ds_paper(body=["Participants were recruited online.", s], header="Participants"))
        assert mo.summary_table["data_source"].tolist() == ["own"], s
        assert mo.summary_table["needs_ethics"].tolist() == [True], s
        approved = mo.summary_table["ethics_approved"].iloc[0]
        assert mo.traffic_light == ("green" if approved else "yellow"), s
