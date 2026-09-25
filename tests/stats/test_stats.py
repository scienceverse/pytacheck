"""Port of metacheck's tests/testthat/test-stats.R and the stat_check tests of test-module-stat.R."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import module_run
from pytacheck.stats import stats
from pytacheck.stats.statcheck import VAR_RAW

STATCHECK_COLUMNS = [
    "test_type",
    "df1",
    "df2",
    "test_comp",
    "test_value",
    "p_comp",
    "reported_p",
    "computed_p",
    "raw",
    "error",
    "decision_error",
    "one_tailed_in_txt",
    "apa_factor",
]


def test_stats_is_callable() -> None:
    assert callable(stats)
    assert "statcheck" in (stats.__doc__ or "")


def test_stats_bad_argument() -> None:
    # R: expect_error(stats(bad_arg))
    with pytest.raises(TypeError):
        stats(object())


def test_defaults() -> None:
    paper = pc.test_paper("Test (M=4.5, t(97.2) = -1.96, p = 0.152).")
    stat_table = stats(paper)
    assert isinstance(stat_table, pd.DataFrame)
    assert len(stat_table) == 1
    assert list(stat_table.columns[:13]) == STATCHECK_COLUMNS
    row = stat_table.iloc[0]
    assert row["test_type"] == "t"
    assert row["df2"] == 97.2
    assert row["test_comp"] == "="
    assert row["test_value"] == -1.96
    assert row["p_comp"] == "="
    assert row["reported_p"] == 0.152
    assert bool(row["error"]) is True
    # the sentence's own columns follow statcheck's
    assert row["text"] == "Test (M=4.5, t(97.2) = -1.96, p = 0.152)."
    assert row["paper_id"] == paper.paper_id

    # no matches
    stat_table = stats(pc.test_paper("No stats here"))
    assert stat_table.shape == (0, 0)


TEST_TEXT = pd.DataFrame(
    {
        "text": [
            "t(20) = 4.23, p = .002",
            "t(20) = 4.23, p = 0.0004",
            "(z = 1.4, p < .05)",
            "z = 1.4, p < .05",  # doesn't parse as Z; weird!
            "H = 2.2, p = .000",
        ]
    }
)


def test_statcheck_options() -> None:
    z_table = stats(TEST_TEXT, stat="Z")
    assert len(z_table) == 1

    t_table = stats(TEST_TEXT, stat="t")
    assert len(t_table) == 2
    assert t_table["error"].tolist() == [True, False]

    all_table = stats(TEST_TEXT, AllPValues=True)
    assert len(all_table) == len(TEST_TEXT)
    assert list(all_table.columns[:3]) == ["p_comp", "reported_p", "p_decimals"]


def test_invalid_statcheck_arguments_raise() -> None:
    # U4: R's tryCatch() turned statcheck()'s "unused argument" error into data.frame()
    with pytest.raises(TypeError, match="bogus"):
        stats(TEST_TEXT, bogus=1)
    with pytest.raises(TypeError, match="messages"):
        stats(TEST_TEXT, messages=True)


def test_character_vector_is_checked() -> None:
    # U4: R fails ("argument is of length zero")
    table = stats(["t(20) = 4.23, p < .001", "no numbers", "t(20) = 4.23, p = .02"])
    assert table[VAR_RAW].tolist() == ["t(20) = 4.23, p < .001", "t(20) = 4.23, p = .02"]
    assert table["error"].tolist() == [False, True]
    assert table.columns[-1] == "text"
    assert stats("t(20) = 4.23, p = .002")["reported_p"].tolist() == [0.002]
    assert stats(["no numbers"]).shape == (0, 0)


def test_sentences_keep_the_results_that_can_be_checked() -> None:
    # U4: R drops every result of a sentence on the first warning or error
    paper = pc.test_paper(
        [
            "Zero df t(0) = 2.1, p = .03.",  # cannot be checked
            "Perfect r(20) = 1.00, p < .001.",  # R: sqrt() of a negative warns
            "Range t(28) = 2.20, p = .036, with p = .05-.10 elsewhere.",  # coercion warning
            "Bracket t(20) = (2.1, p = .03.",  # R: if () on two values errors
            "Both t(0) = 2.1, p = .03 and t(28) = 2.20, p = .036.",
            "Fine t(28) = 2.20, p = .036.",
        ]
    )
    table = stats(paper)
    assert table[VAR_RAW].tolist() == [
        "r(20) = 1.00, p < .001",
        "t(28) = 2.20, p = .036",
        "t(20) = (2.1, p = .03",
        "t(28) = 2.20, p = .036",
        "t(28) = 2.20, p = .036",
    ]
    assert table["error"].tolist() == [False, False, True, False, False]


def test_error_psychsci(psychsci: pc.PaperList) -> None:
    # R: stats(psychsci[100]) used to error ("missing value where TRUE/FALSE needed")
    table = stats(psychsci)
    assert len(table) == 45
    assert set(table["test_type"]) == {"t", "F"}
    assert table["paper_id"].isin(psychsci.names).all()


def test_stat_check_module() -> None:
    paper = pc.test_paper(
        ["This is right: t(17.4), p = 0.137", "This is wrong: t(97.2) = -1.96, p = 0.152"]
    )
    mod_output = module_run(paper, "stat_check")
    assert mod_output.traffic_light == "red"
    assert len(mod_output.table) == 1
    assert mod_output.table["raw"].iloc[0] == "t(97.2) = -1.96, p = 0.152"
    assert mod_output.module == "stat_check"
    assert mod_output.summary_text == "1 possible error in t-tests or F-tests"

    # iteration
    paper = pc.PaperList(
        [
            pc.test_paper("This is right: t(17.4), p = 0.137"),
            pc.test_paper("This is wrong: t(97.2) = -1.96, p = 0.152"),
        ]
    )
    mod_output = module_run(paper, "stat_check")
    assert len(mod_output.table) == 1
    assert mod_output.table["paper_id"].iloc[0] == paper.names[1]
    summary = mod_output.summary_table
    assert summary["paper_id"].tolist() == paper.names
    assert summary["statcheck_found"].tolist() == [0, 1]
    assert summary["statcheck_errors"].tolist() == [0, 1]


def test_stat_check_module_lights() -> None:
    none = module_run(pc.test_paper("No statistics at all."), "stat_check")
    assert none.traffic_light == "na"
    assert none.table.shape == (0, 0)

    not_validated = module_run(pc.test_paper("Correlated r(28) = .22, p = .24."), "stat_check")
    assert not_validated.traffic_light == "na"
    assert not_validated.summary_text == "No t-tests or F-tests detected"

    green = module_run(pc.test_paper("Fine: t(28) = 2.20, p = .036."), "stat_check")
    assert green.traffic_light == "green"
    assert green.summary_text == "We detected no errors in t-tests or F-tests."


def test_stat_check_does_not_mutate(demo: pc.Paper) -> None:
    before = pc.paper_table(demo, "text").copy()
    module_run(demo, "stat_check")
    pd.testing.assert_frame_equal(pc.paper_table(demo, "text"), before)
