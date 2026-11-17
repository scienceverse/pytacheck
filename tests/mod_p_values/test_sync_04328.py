"""stat_p_exact synchronised with metacheck 0.4.3.28 (tests/testthat/test-module-stat.R)."""

from __future__ import annotations

import pandas as pd

import metacheck as pc
from metacheck.module import module_run
from metacheck.stats.helpers import _p_not_test

# each sentence from a real paper (PsyArXiv reports and the open-access collections);
# the p-value in the second column, why it is not a test in the third (None: a result)
CASES: list[tuple[str, str, str | None]] = [
    # notes with markers
    ("Note. ** = p < .01, * = p < .05.", "p < .05", "marker"),
    (
        "Data are presented as mean ± SD. ns, p > 0.05; **p < 0.01; ***p < 0.001.",
        "p > 0.05",
        "marker",
    ),
    ("Error bars indicate standard error. + p < 0.10, * p <0.05.", "p < 0.10", "marker"),
    (
        "Model fit: χ2 = 129.59, df = 89, p < .01, CFI = 0.95, SRMR = .06, N = 135, *p <.05.",
        "p <.05",
        "marker",
    ),
    ("Error bars represent ±1 SEM.* p < 0.05 (two-tailed).", "p < 0.05", "marker"),
    # thresholds
    ("A p-value < 0.05 was considered statistically significant.", "p-value < 0.05", "threshold"),
    ("The significance level was set at p <.05.", "p <.05", "threshold"),
    (
        "All the reported clusters survived a cFWE-corrected threshold of P < 0.05.",
        "P < 0.05",
        "threshold",
    ),
    (
        "Contrasts, with a Bonferroni corrected alpha of p<.02 for 4 comparisons, showed an interaction.",
        "p<.02",
        "threshold",
    ),
    # legends
    (
        "Asterisks indicate significant differences between conditions (p < .05).",
        "p < .05",
        "legend",
    ),
    (
        "Note: Correlations significant at the p < .05 level are highlighted in bold.",
        "p < .05",
        "legend",
    ),
    (
        "Black and white markers indicate reported effects that did (black) and did not (white) "
        "differ significantly (p < 0.05) from the corresponding expected effect.",
        "p < 0.05",
        "legend",
    ),
    # results, which stay
    (
        "More importantly, there was a significant interaction [F(1,18) ¼ 6.05, P < 0.05, g2 ¼ 0.25].",
        "P < 0.05",
        None,
    ),
    (
        "The indirect effect through hopelessness was significant (β = 0.115*, p < 0.05).",
        "p < 0.05",
        None,
    ),
    (
        "Trials were more variable (paired t-test, n = 95, t52 = 6.92, ***p<<0.001, and for monkey D).",
        "p<<0.001",
        None,
    ),
    (
        "Silencing (overground n = 135/600 vs treadmill n = 22/151, *p<0.05 [z = 2.38]; hindlimbs).",
        "p<0.05",
        None,
    ),
    ("Tor1a +/+ p<0.05; Tor1a +/Dgag p<0.01.", "p<0.05", None),
    (
        "No other main effects or interactions reached conventional significance levels (all ps > 0.09).",
        "ps > 0.09",
        None,
    ),
    (
        "This effect remained significant when the maps were thresholded at varying levels (all Ps < 0.002).",
        "Ps < 0.002",
        None,
    ),
    ("Participants showed a higher exit threshold (P < 0.05).", "P < 0.05", None),
    (
        "The Kansas and Florida lines differed in DT but not PA (t-test, P>0.05, data not shown).",
        "P>0.05",
        None,
    ),
    (
        "Alpha power was higher in the online group than in the offline group (p < .05).",
        "p < .05",
        None,
    ),
    ("Participants who met the inclusion criteria scored higher (p < .05).", "p < .05", None),
]


def test_p_not_test_cases() -> None:
    p = pd.DataFrame(
        {
            "paper_id": "x",
            "text_id": range(1, len(CASES) + 1),
            "expanded": [c[0] for c in CASES],
            "text": [c[1] for c in CASES],
        }
    )
    assert _p_not_test(p) == [c[2] for c in CASES]


def test_p_not_test_empty() -> None:
    p = pd.DataFrame({"paper_id": [], "text_id": [], "expanded": [], "text": []})
    assert _p_not_test(p) == []


def test_note_under_two_tables_is_left_out_twice() -> None:
    # the same note under two tables: both copies are left out, not one (b92ku_v1)
    note = (
        "The table reports standard errors and asymptotic p-values. "
        "* p < .05. * * p < .01. * * * p < .001."
    )
    out = module_run(pc.test_paper([note, "Some results.", note]), "stat_p_exact")
    assert out.traffic_light == "green"
    assert out.summary_table["n_imprecise"].sum() == 0


def test_legend_joined_to_table_cells() -> None:
    # a legend joined to a table's cells (z2hpt_v1)
    text = CASES[11][0] + " .064 (.006; .122)* .008 (-.017; .033)"
    out = module_run(pc.test_paper(text), "stat_p_exact")
    assert out.traffic_light == "green"


def test_result_next_to_a_note_is_still_found() -> None:
    note = (
        "The table reports standard errors and asymptotic p-values. "
        "* p < .05. * * p < .01. * * * p < .001."
    )
    out = module_run(
        pc.test_paper(["Group A differed, F(1, 30) = 5.20, p < .05.", note]), "stat_p_exact"
    )
    assert out.summary_table["n_imprecise"].tolist() == [1]
    assert out.table["not_a_test"].notna().sum() == 3
    # the report says where the threshold words can be wrong
    assert any(
        isinstance(b, str)
        and "sometimes flags false positives when sentences discuss how p-value thresholds were set"
        in b
        for b in out.report
    )
