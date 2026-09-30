"""Ports of metacheck's testthat tests for the stat_effect_size module.

Source: tests/testthat/test-module-stat_effect_size.R, plus unit tests of the
module's internal helpers (defined inside ``stat_effect_size()`` in R).
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import ModuleError, module_list, module_run
from metacheck.modules import stat_effect_size as ses

MODULE = "stat_effect_size"


def _run(text: str | list[str]):
    return module_run(pc.test_paper(text), MODULE)


# ------------------------------------------------ test-module-stat_effect_size.R


def test_stat_effect_size() -> None:
    mods = module_list()
    assert MODULE in mods["name"].tolist()

    # no relevant text
    mod_output = _run("There are no stats.")
    assert mod_output.traffic_light == "na"
    assert len(mod_output.table) == 0

    # relevant text - red
    mod_output = _run(
        [
            "A was bigger than B, t(124) = 1.23, p 0.013.",
            "We also ran an ANOVA, F(1, 13) = 2.34, p = .23.",
        ]
    )
    assert mod_output.traffic_light == "red"
    assert len(mod_output.table) == 2
    s = mod_output.summary_table
    assert s["ttests_with_es"].tolist() == [0]
    assert s["ttests_without_es"].tolist() == [1]
    assert s["Ftests_with_es"].tolist() == [0]
    assert s["Ftests_without_es"].tolist() == [1]

    # relevant text - yellow
    mod_output = _run(
        [
            "A was bigger than B, t(124) = 1.23, p 0.013, d = 0.34.",
            "We also ran an ANOVA, F(1, 13) = 2.34, p = .23.",
        ]
    )
    assert mod_output.traffic_light == "yellow"
    assert len(mod_output.table) == 2
    s = mod_output.summary_table
    assert s["ttests_with_es"].tolist() == [1]
    assert s["ttests_without_es"].tolist() == [0]
    assert s["Ftests_with_es"].tolist() == [0]
    assert s["Ftests_without_es"].tolist() == [1]

    # relevant text - green
    # ES values are coherent with the test statistics: d = 0.34 matches
    # t(124) = 1.23 under the independent unequal-n range, and ηp² = 0.15
    # matches F(1, 13) = 2.34 (implied partial eta-squared = 0.1525)
    mod_output = _run(
        [
            "A was bigger than B, t(124) = 1.23, p 0.013, d = 0.34.",
            "We also ran an ANOVA, F(1, 13) = 2.34, p = .23, ηp² = 0.15.",
        ]
    )
    assert mod_output.traffic_light == "green"
    assert len(mod_output.table) == 2
    s = mod_output.summary_table
    assert s["ttests_with_es"].tolist() == [1]
    assert s["ttests_without_es"].tolist() == [0]
    assert s["Ftests_with_es"].tolist() == [1]
    assert s["Ftests_without_es"].tolist() == [0]

    # iterate
    paper = pc.PaperList(
        [
            pc.test_paper("A was bigger than B, t(124) = 1.23, p 0.013, d = 0.34."),
            pc.test_paper("We also ran an ANOVA, F(1, 13) = 2.34, p = .23."),
        ]
    )
    mod_output = module_run(paper, MODULE)
    t = mod_output.table
    s = mod_output.summary_table
    assert t["test_text"].tolist() == ["t(124) = 1.23", "F(1, 13) = 2.34"]
    assert t["test"].tolist() == ["t-test", "F-test"]
    assert s["ttests_with_es"].tolist() == [1, 0]
    assert s["ttests_without_es"].tolist() == [0, 0]
    assert s["Ftests_without_es"].tolist() == [0, 1]
    assert s["Ftests_with_es"].tolist() == [0, 0]

    # coherence checks for d in t-tests
    mod_output = _run("A was bigger than B, t(124) = 1.23, p 0.013, d = 0.34.")
    assert mod_output.table["d_coherence"].iloc[0] == "match_under_assumptions"
    assert mod_output.table["d_coherence_assumption"].iloc[0] in (
        "paired_dz",
        "independent_equal_n",
        "independent_unequal_n_range",
    )

    mod_output = _run("A was bigger than B, t(20) = 1.00, p 0.32, d = 3.00.")
    assert mod_output.table["d_coherence"].iloc[0] == "no_match"
    assert mod_output.table["d_coherence_assumption"].iloc[0] == "none"

    # multiple t-tests and d values in one sentence should split into paired rows
    mod_output = _run(
        "A was bigger than B, t(23) = 2.73; t(23) = 2.98; t(23) = 4.74, "
        "d = 0.56; d = 0.61; d = 0.97."
    )
    t = mod_output.table
    assert len(t) == 3
    assert t["test_text"].tolist() == ["t(23) = 2.73", "t(23) = 2.98", "t(23) = 4.74"]
    assert t["es"].tolist() == ["d = 0.56", "d = 0.61", "d = 0.97"]
    assert t["d_coherence"].tolist() == ["match_under_assumptions"] * 3
    assert t["d_coherence_assumption"].tolist() == ["paired_dz"] * 3

    # F-tests with multiple values and eta-squared values in one sentence should
    # split into paired rows
    mod_output = _run(
        "The model was significant, F(5, 120) = 91.32; F(5, 120) = 74.45; "
        "F(5, 120) = 388.16, η² = .79; η² = .67; η² = .94."
    )
    t = mod_output.table
    assert len(t) == 3
    assert t["test_text"].tolist() == [
        "F(5, 120) = 91.32",
        "F(5, 120) = 74.45",
        "F(5, 120) = 388.16",
    ]
    assert t["es"].tolist() == ["η² = .79", "η² = .67", "η² = .94"]
    assert t["eta_coherence"].tolist() == ["indeterminate"] * 3
    assert t["eta_coherence_assumption"].tolist() == ["eta_squared"] * 3

    mod_output = _run("The model was significant, F(1, 13) = 2.34, p = .23, ηp² = 0.15.")
    assert mod_output.table["eta_coherence"].iloc[0] == "match_under_assumptions"
    assert mod_output.table["eta_coherence_assumption"].iloc[0] == "partial_eta_squared"

    # Cohen's f: not checked regardless of whether other ES are present
    mod_output = _run("The model was significant, F(1, 48) = 5.23, p = .026, f = 0.37.")
    assert mod_output.table["eta_coherence"].iloc[0] == "indeterminate"
    assert mod_output.table["eta_coherence_note"].iloc[0] == (
        "Cohen's f not checked: cannot determine whether based on eta squared or partial eta "
        "squared."
    )

    # Cohen's f alongside partial eta: partial eta is still checked, f is ignored
    mod_output = _run("The model was significant, F(1, 13) = 2.34, p = .23, ηp² = 0.15, f = 0.42.")
    assert mod_output.table["eta_coherence"].iloc[0] == "match_under_assumptions"
    assert mod_output.table["eta_coherence_assumption"].iloc[0] == "partial_eta_squared"

    # ωp² cannot be verified from F and dfs alone — should be indeterminate, not no_match
    mod_output = _run("The model was significant, F(2, 151) = 1.00, p = .37, ωp² = 0.00.")
    assert mod_output.table["eta_coherence"].iloc[0] == "indeterminate"


def test_stat_effect_size_no_warnings() -> None:
    """stat_effect_size emits no warnings for a paper with no stats (#308)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        mod_output = _run("There are no stats.")
    assert mod_output.traffic_light == "na"
    assert len(mod_output.table) == 0


def test_stat_effect_size_implied_n() -> None:
    """stat_effect_size reports the sample sizes behind a d coherence match."""
    # equal-n: t(48) = 2.00 implies d = 0.566 for n1 = n2 = 25 (N = 50)
    t = _run("t(48) = 2.00, d = 0.57.").table
    assert t["d_coherence_assumption"].iloc[0] == "independent_equal_n"
    assert t["d_implied_n"].iloc[0] == "n1 = n2 = 25, N = 50"
    assert "n1 = n2 = 25, N = 50" in t["d_coherence_note"].iloc[0]

    # unequal-n: closest split is reported as n1, n2 and N
    t = _run("t(38) = 2.10, d = 0.68.").table
    assert t["d_coherence_assumption"].iloc[0] == "independent_unequal_n_range"
    assert t["d_implied_n"].iloc[0] == "n1 = 16, n2 = 24, N = 40"
    assert "n1 = 16, n2 = 24, N = 40" in t["d_coherence_note"].iloc[0]

    # paired dz: n recorded (n = df + 1) but not added to the note
    t = _run("t(23) = 2.73, d = 0.56.").table
    assert t["d_coherence_assumption"].iloc[0] == "paired_dz"
    assert t["d_implied_n"].iloc[0] == "n = 24"
    assert t["d_coherence_note"].iloc[0] == "Match under paired-samples dz assumption."


# ------------------------------------------------------------ module outputs


def test_output_structure(demo) -> None:
    out = module_run(demo, MODULE)
    assert out.traffic_light == "yellow"
    t = out.table
    assert list(t.columns[:8]) == [
        "paper_id",
        "text_id",
        "test",
        "test_text",
        "es",
        "section_id",
        "paragraph_id",
        "text",
    ]
    assert list(t.columns[8:21]) == list(ses._D_COLUMNS)
    assert list(t.columns[21:]) == list(ses._F_COLUMNS)
    assert t["test_text"].tolist() == ["t(97.7) = 2.9", "t(97.2) = -1.96"]
    assert t["d_coherence_note"].tolist() == [
        "Non-integer df indicates Welch's t-test (unequal variances); sample sizes cannot be "
        "determined.",
        "No parseable d effect size found.",
    ]
    assert out.extras["na_replace"] == 0
    # U125: with the full stop metacheck leaves out
    assert out.summary_text == (
        "We found 1 t-test and/or F-test where effect sizes are not reported. Check these tests "
        "in the table below, and consider adding effect sizes."
    )


def test_all_reported_but_inconsistent() -> None:
    out = _run(["t(20) = 1.00, d = 3.00.", "F(1, 13) = 2.34, ηp² = 0.35."])
    assert out.traffic_light == "red"
    assert out.summary_text.startswith("All effect sizes were reported, but some appear")
    prose = [b for b in out.report if isinstance(b, str)]
    assert prose[0] == (
        "All tests had effect sizes, but some effect sizes do not match the reported test "
        "statistic."
    )
    assert prose[1] == "For t-tests with a reported d, coherence checks yielded 1 no match."
    assert prose[2] == (
        "For F-tests with a reported eta-squared effect size, coherence checks yielded 1 no match."
    )


def test_green_report() -> None:
    out = _run(["t(23) = 2.73, d = 0.56.", "t(48) = 2.00, d = 0.57.", "t(97.7) = 2.9, d = 0.59."])
    assert out.traffic_light == "green"
    assert out.report[0] == (
        "All detected t-tests and F-tests had an effect size reported in the same sentence."
    )
    assert out.report[1] == (
        "For t-tests with a reported d, coherence checks yielded 2 matches under assumptions, "
        "1 indeterminate case."
    )


def test_effect_sizes_without_tests() -> None:
    out = _run(["The effect was large, d = 0.80.", "r = .30"])
    assert out.traffic_light == "na"
    assert out.summary_text == "No t-tests or F-tests were detected."
    assert out.table.shape == (0, 0)


def test_paperlist_without_tests() -> None:
    papers = pc.PaperList([pc.test_paper("No stats."), pc.test_paper("d = 0.5")])
    out = module_run(papers, MODULE)
    assert out.traffic_light == "na"
    assert out.summary_table.columns.tolist() == ["paper_id"]
    assert len(out.summary_table) == 2


def test_paper_order_follows_paper_list(fixtures_dir) -> None:
    files = [
        fixtures_dir / "psychsci" / "0956797614522816.json",
        fixtures_dir / "psychsci" / "0956797613520608.json",
    ]
    papers = pc.read(files)
    out = module_run(papers, MODULE)
    ids = list(dict.fromkeys(out.table["paper_id"].tolist()))
    assert ids == ["0956797614522816", "0956797613520608"]
    assert out.summary_table["paper_id"].tolist() == ids


def test_nan_eta_is_indeterminate() -> None:
    # U126: 0/0 implied eta; metacheck's `if (any(NA))` stops the module
    out = _run("F(0, 0) = 1.00, ηp² = 0.50.")
    assert out.table["eta_coherence"].tolist() == ["indeterminate"]
    assert out.table["eta_coherence_note"].tolist() == [
        "The implied effect size is undefined for these degrees of freedom."
    ]


@pytest.mark.parametrize(
    ("text", "coherence", "note"),
    [
        # U126: "partial η2" is a partial eta-squared (metacheck: "Eta-squared reported")
        (
            "F(1, 20) = 5.00, p = .04, partial η2 = .20.",
            "match_under_assumptions",
            "Match under partial eta-squared formula from F and dfs.",
        ),
        # U126: d, r, beta next to an F-test are not eta-squared
        (
            "F(1, 20) = 5.00, p = .04, d = 0.9.",
            "indeterminate",
            "Effect size reported but not verifiable from F and degrees of freedom alone.",
        ),
        (
            "F(1, 20) = 5.00, p = .04, Cohen's d = 0.9.",
            "indeterminate",
            "Effect size reported but not verifiable from F and degrees of freedom alone.",
        ),
        # "beta" (and "theta", "zeta") contain "eta" but are not an eta-squared
        (
            "F(1, 20) = 5.00, p = .04, beta = .30.",
            "indeterminate",
            "Effect size reported but not verifiable from F and degrees of freedom alone.",
        ),
        (
            "F(1, 20) = 5.00, p = .04, partial eta-squared = .20.",
            "match_under_assumptions",
            "Match under partial eta-squared formula from F and dfs.",
        ),
        # U126: a bound is checked as a bound (implied .0002 < .001)
        (
            "F(1, 491) = 0.10, ηp² < .001.",
            "match_under_assumptions",
            "Match under partial eta-squared formula from F and dfs.",
        ),
        # U125: the note on both eta-squared and partial eta-squared is kept
        (
            "F(1, 20) = 5.00, η2 = .15; ηp2 = .20.",
            "match_under_assumptions",
            "Match under partial eta-squared formula from F and dfs. Both eta-squared and "
            "partial eta-squared reported; coherence evaluated only for partial eta-squared.",
        ),
    ],
)
def test_eta_classification(text: str, coherence: str, note: str) -> None:
    out = _run(text)
    assert out.table["eta_coherence"].tolist() == [coherence]
    assert out.table["eta_coherence_note"].tolist() == [note]


def test_partial_applies_only_to_its_own_value() -> None:
    # U126: a plain "η2 = .05" of another test in a sentence that also has a
    # "partial η2 = .20" stays an eta-squared (not checked, not a no-match)
    out = _run(
        "The main effect was significant, F(1, 20) = 5.00, p = .036, partial η2 = .20, "
        "and so was the interaction, F(2, 40) = 3.00, p = .061, η2 = .05."
    )
    assert out.table["eta_coherence"].tolist() == ["match_under_assumptions", "indeterminate"]
    assert out.table["eta_coherence_assumption"].tolist() == [
        "partial_eta_squared",
        "eta_squared",
    ]


@pytest.mark.parametrize(
    ("text", "coherence"),
    [
        # U126: metacheck checks "d > 0.4" as d = 0.4 (no match); the implied dz is .45
        ("t(40) = 2.9, p = .006, d > 0.4.", "match_under_assumptions"),
        ("t(40) = 2.9, p = .006, d < 0.2.", "no_match"),
        ("t(40) = 2.9, p = .006, d < -0.4.", "match_under_assumptions"),
    ],
)
def test_d_bounds(text: str, coherence: str) -> None:
    assert _run(text).table["d_coherence"].tolist() == [coherence]


def test_stat_check_paper_list_summary_has_ids() -> None:
    # U124: metacheck's data.frame(paper_id = paperlist$paper_id) has no columns
    from metacheck.modules.stat_check import _paper_ids

    papers = pc.PaperList([pc.test_paper(["No stats."]), pc.test_paper(["None either."])])
    assert _paper_ids(papers)["paper_id"].tolist() == [p.paper_id for p in papers]


def test_does_not_mutate_input() -> None:
    paper = pc.test_paper(["t(23) = 2.73, d = 0.56.", "F(1, 13) = 2.34."])
    before = paper.text.copy()
    module_run(paper, MODULE)
    pd.testing.assert_frame_equal(paper.text, before)


# ---------------------------------------------------------------- helpers


def test_label_lhs() -> None:
    lhs = ["t", "F", "F", "F", "d", "Cohen's d", "dz", "d_rm", "g", "Hedges' g", "f2", "ηp²"]
    lhs += ["η2", "ω²", "omega", "ξ", "β", "b", "r", "p", "M", "T", "R2", "BF10"]
    df = [None, "(1, 20)", "(1.5, 20)", None] + [None] * (len(lhs) - 4)
    assert ses._label_lhs(lhs, df) == [
        "t-test",
        "F-test",
        None,
        None,
        *["es"] * 15,
        None,
        None,
        None,
        None,
        None,
    ]


def test_parsers() -> None:
    assert ses._parse_t_stats(None) == []
    assert ses._parse_t_stats("") == []
    assert ses._parse_t_stats("t(23) = 2.73") == [("t(23) = 2.73", 2.73, 23.0)]
    assert ses._parse_t_stats("t(23) < 2.73") == []
    assert ses._parse_f_stats("F(1, 20) = -4.1") == [("F(1, 20) = -4.1", -4.1, 1.0, 20.0)]
    assert ses._parse_d_stats("Cohen’s  d = .5; dz = 1e-1; g = 2; d > 0.4") == [
        ("cohen’s d", 0.5, "Cohen’s  d = .5", "="),
        ("dz", 0.1, "dz = 1e-1", "="),
        ("d", 0.4, "d > 0.4", ">"),
    ]
    assert ses._parse_eta_stats("ηp² = .17; η2 = .1; f = .3; ω² = 0; BF10 = 3; xyz; d = 1") == [
        ("partial_eta_squared", 0.17, "ηp² = .17", "="),
        ("eta_squared", 0.1, "η2 = .1", "="),
        ("cohens_f", 0.3, "f = .3", "="),
        ("non_checkable", 0.0, "ω² = 0", "="),
        ("non_checkable", 3.0, "BF10 = 3", "="),
        ("non_checkable", 1.0, "d = 1", "="),  # U126: metacheck says eta_squared
    ]
    # U126: "partial η2" in the sentence
    assert ses._parse_eta_stats("η2 = .2", "F(1, 20) = 5, partial η2 = .2.") == [
        ("partial_eta_squared", 0.2, "η2 = .2", "=")
    ]
    # ... only for the value that follows "partial"
    assert ses._parse_eta_stats("η2 = .05", "partial η2 = .20, and η2 = .05.") == [
        ("eta_squared", 0.05, "η2 = .05", "=")
    ]
    assert ses._parse_eta_stats("η2 = .2", "partial η2 = .25.") == [
        ("eta_squared", 0.2, "η2 = .2", "=")
    ]
    assert [e.label for e in ses._parse_eta_stats("beta = .3; theta = .1; zeta = .2")] == [
        "non_checkable"
    ] * 3


def test_count_coh_and_coherence_text() -> None:
    assert ses._count_coh(["no_match", None, "a; no_match", "match"], "no_match") == 2
    assert ses._format_coherence_text(0, 0, 0, "t-tests", "d") is None
    assert ses._format_coherence_text(1, 2, 1, "t-tests", "d") == (
        "For t-tests with a reported d, coherence checks yielded 1 match under assumptions, "
        "2 no matches, 1 indeterminate case."
    )


@pytest.mark.parametrize(
    ("test", "test_text", "es", "coherence", "assumption"),
    [
        ("t-test", "t(23) = 2.73", "d = 0.56", "match_under_assumptions", "paired_dz"),
        ("t-test", "t(48) = 2.00", "d = 0.57", "match_under_assumptions", "independent_equal_n"),
        (
            "t-test",
            "t(38) = 2.10",
            "d = 0.68",
            "match_under_assumptions",
            "independent_unequal_n_range",
        ),
        ("t-test", "t(20) = 1.00", "d = 3.00", "no_match", "none"),
        ("t-test", "t(97.7) = 2.9", "d = 0.59", "indeterminate", "none"),
        ("t-test", "t = 2.9", "d = 0.59", "indeterminate", "none"),
        ("t-test", "t(20) = 2.9", None, "indeterminate", "none"),
        ("F-test", "F(1, 20) = 2.9", "d = 0.59", None, None),
    ],
)
def test_classify_d_coherence(test, test_text, es, coherence, assumption) -> None:
    out = ses._classify_d_coherence(test, test_text, es)
    assert list(out) == list(ses._D_COLUMNS)
    assert out["d_coherence"] == coherence
    assert out["d_coherence_assumption"] == assumption


def test_classify_d_coherence_values() -> None:
    out = ses._classify_d_coherence("t-test", "t(23) = 2.73", "d = 0.56")
    assert out["t_value"] == "2.73"
    assert out["df"] == "23"
    assert out["d_reported"] == "0.56"
    assert out["d_implied_paired_dz"] == "0.557258916483173"
    no = ses._classify_d_coherence("t-test", "t(20) = 1.00", "d = 3.00")
    assert no["d_coherence_note"] == (
        "No match under tested assumptions (paired dz, independent equal-n, independent "
        "unequal-n range). Tolerance = 0.01. A no-match can occur when fewer than 2 decimal "
        "places are reported."
    )


@pytest.mark.parametrize(
    ("test_text", "es", "coherence", "assumption"),
    [
        ("F(1, 13) = 2.34", "ηp² = 0.15", "match_under_assumptions", "partial_eta_squared"),
        ("F(1, 13) = 2.34", "ηp² = 0.35", "no_match", "partial_eta_squared"),
        ("F(1, 13) = 2.34", "η² = 0.15", "indeterminate", "eta_squared"),
        ("F(1, 13) = 2.34", "f = 0.42", "indeterminate", "none"),
        ("F(1, 13) = 2.34", "ωp² = 0.12", "indeterminate", "none"),
        ("F(1, 13) = 2.34", None, "indeterminate", "none"),
        ("F(1, 13) < 2.34", "ηp² = 0.15", "indeterminate", "none"),
    ],
)
def test_classify_f_coherence(test_text, es, coherence, assumption) -> None:
    out = ses._classify_f_coherence("F-test", test_text, es)
    assert list(out) == list(ses._F_COLUMNS)
    assert out["eta_coherence"] == coherence
    assert out["eta_coherence_assumption"] == assumption


def test_classify_f_coherence_values() -> None:
    out = ses._classify_f_coherence("F-test", "F(1, 13) = 2.34", "ηp² = 0.15")
    assert out["f_reported"] == "2.34"
    assert out["f_reported_text"] == "F(1, 13) = 2.34"
    assert out["df1"] == "1"
    assert out["df2"] == "13"
    assert out["eta_implied_partial"] == "0.152542372881356"
    assert ses._classify_f_coherence("t-test", "t(1) = 2", None) == dict.fromkeys(ses._F_COLUMNS)


# ---------------------------------------------------------------- review: edge cases


def test_df_beyond_integer_range_errors_like_r() -> None:
    # R: as.integer(round(df + 2)) is NA (with a warning), then `2:(NA - 2)` errors;
    # Python must not try to build a multi-GB grid of group sizes first
    with (
        pytest.warns(UserWarning, match="NAs introduced by coercion to integer range"),
        pytest.raises(ModuleError, match="NA/NaN argument"),
    ):
        _run("Huge, t(2147483646) = 2.0, d = 0.5.")
    with pytest.raises(ValueError, match="NA/NaN argument"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ses._classify_d_coherence("t-test", "t(9999999999) = 2.0", "d = 0.5")
    # without a d the check stops before building the grid, as in R
    out = _run("Huge, t(9999999999) = 2.0, p = .04.")
    assert out.table["d_coherence_note"].tolist() == ["No parseable d effect size found."]
    # R leaves t_value and df NA when no d parses (the early return comes first)
    assert out.table["df"].isna().tolist() == [True]


def test_unequal_n_grid_matches_direct_formula() -> None:
    import numpy as np

    for df in range(2, 80):
        for t in (0.5, 1.7, 2.9, 6.25):
            n_total = df + 2
            n1 = np.arange(2, n_total - 1)
            d_vals = t * np.sqrt(1 / n1 + 1 / (n_total - n1))
            out = ses._classify_d_coherence("t-test", f"t({df}) = {t}", "d = 0.5")
            assert out["d_implied_indep_unequal_min"] == ses._chr(float(d_vals.min()))
            assert out["d_implied_indep_unequal_max"] == ses._chr(float(d_vals.max()))
            # the reported d is the midpoint of the range: an unequal-n match
            mid = (d_vals.min() + d_vals.max()) / 2
            out = ses._classify_d_coherence("t-test", f"t({df}) = {t}", f"d = {mid!r}")
            if out["d_coherence_assumption"] == "independent_unequal_n_range":
                best = int(np.argmin(np.abs(d_vals - mid)))
                assert out["d_implied_n"] == (
                    f"n1 = {n1[best]}, n2 = {n_total - n1[best]}, N = {n_total}"
                )


def test_text_table_input() -> None:
    # module_run() also accepts a text table; R's early return then builds
    # data.frame(paper_id = paper$paper_id) from the table's column
    tt = pc.text_search(pc.demopaper(), "Introduction|design")
    out = ses.stat_effect_size(tt)
    assert out["traffic_light"] == "na"
    assert out["summary_table"]["paper_id"].tolist() == tt["paper_id"].tolist()
    assert ses._paper_id_frame(tt.drop(columns="paper_id"), pc.Paper).shape == (0, 0)
    assert ses._paper_id_frame(pc.PaperList([]), pc.Paper).shape == (0, 0)

    tt = pc.text_search(pc.demopaper(), "significant")
    out = ses.stat_effect_size(tt)
    assert out["traffic_light"] == "red"
    assert out["table"]["test"].tolist() == ["t-test"]


def test_text_table_summary_table_has_one_row_per_table_row() -> None:
    # R module_run(): summary_table <- data.frame(paper_id = paper$paper_id)
    p = pc.test_paper(["A, t(40) = 2.9, d = 0.45.", "B, t(30) = 2.0.", "No stats."])
    tt = pc.text_search(p, "[0-9]")
    out = module_run(tt, MODULE)
    s = out.summary_table
    assert s["paper_id"].tolist() == [p.paper_id] * 2
    assert s["ttests_with_es"].tolist() == [1, 1]
    assert s["ttests_without_es"].tolist() == [1, 1]


def test_as_numeric_is_r_strtod_in_long_double() -> None:
    # R: sprintf("%a", as.numeric(x)); float() is correctly rounded, R is not
    assert ses._num("0.8050473192") == float.fromhex("0x1.9c2f298764982p-1")
    assert float("0.8050473192") == float.fromhex("0x1.9c2f298764981p-1")
    assert ses._num(".7246706653776675") == float.fromhex("0x1.7308089055d52p-1")
    assert ses._num("281412004015158280215") == float.fromhex("0x1.e82c08d558c9ep+67")
    for s in ("2.9", "0.45", ".17", "-0", "+2.9", "1e5", "4.5E-2", "5.", "1e-400"):
        assert ses._num(s) == float(s)
    assert ses._num("9" * 400) == float("inf")
    assert ses._num("-" + "9" * 400) == float("-inf")


def test_as_character_uses_long_double_digits() -> None:
    # R: as.character() of doubles on near-ties (format.c scientific())
    assert ses._chr(float.fromhex("0x1.9714938037f2cp-1")) == "0.79507885875862"
    assert ses._chr(float.fromhex("0x1.8807e0e187f07p-2")) == "0.3828425538686"
    assert ses._chr(float.fromhex("0x1.53b4226af62abp-35")) == "3.8619833750799e-11"
    cases = {
        1 / 3: "0.333333333333333",
        1e5: "1e+05",
        123456.0: "123456",
        100000.5: "100000.5",
        0.0001: "1e-04",
        0.00015: "0.00015",
        99999.99999999999: "1e+05",
        0.1 + 0.2: "0.3",
        -2.5: "-2.5",
        -0.0: "0",
        float("nan"): "NaN",
        float("inf"): "Inf",
        5e-324: "4.94065645841247e-324",
    }
    for x, want in cases.items():
        assert ses._chr(x) == want, x
    # format(): 7 significant digits, no dropping of zeros
    assert ses._format(20.5) == "20.5"
    assert ses._format(1234567.5) == "1234568"
    assert ses._format(5e5) == "5e+05"
    assert ses._format(1000000.0) == "1e+06"


def test_papers_without_digit_sentences_sort_by_icu_paper_id() -> None:
    def mk(text: str | list[str], pid: str) -> pc.Paper:
        p = pc.test_paper(text)
        p.paper_id = pid
        return p

    pl = pc.PaperList(
        [mk("No digits, t = n.s.", "b"), mk("Sig, t(20) = 2.1, d = 0.9.", "mm")]
        + [mk("B, t = n.s., d = n.s.", pid) for pid in ("Ab", "B", "_x", "aa")]
    )
    out = module_run(pl, MODULE)
    assert out.table["paper_id"].tolist() == ["mm", "_x", "aa", "Ab", "b", "B"]


def test_unequal_n_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    # the group-size grid is evaluated in blocks: results must not depend on the block size
    texts = [
        ("t(58) = 2.5", "d = 1.2"),
        ("t(10) = 3.0", "d = 2.33"),
        ("t(10) = 3.0", "d = 1.90"),
        ("t(97) = 2.1", "d = 0.6"),
        ("t(2) = 2.0", "d = 2.0"),
    ]
    want = [ses._classify_d_coherence("t-test", t, e) for t, e in texts]
    for block in (1, 2, 3, 7):
        monkeypatch.setattr(ses, "_UNEQUAL_BLOCK", block)
        assert [ses._classify_d_coherence("t-test", t, e) for t, e in texts] == want
    # R (parity golden stat_effect_size.review.d_variants)
    assert want[0]["d_implied_n"] == "n1 = 5, n2 = 55, N = 60"
    assert want[1]["d_implied_n"] == "n1 = 2, n2 = 10, N = 12"
    assert want[2]["d_implied_n"] == "n1 = 4, n2 = 8, N = 12"


def test_help_text_without_metacheck_typos() -> None:
    # U83: metacheck's details say "..., you the module provides a warning"
    details = pc.module_info(MODULE).details
    assert "might be incorrect, the module provides a warning." in details
    assert "you the module" not in details
