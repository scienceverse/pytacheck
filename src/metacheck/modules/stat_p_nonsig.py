"""Non-Significant P Value Check (port of ``inst/modules/stat_p_nonsig.R``).

References (roxygen ``@references`` of the R module):

Appelbaum, M., Cooper, H., Kline, R. B., Mayo-Wilson, E., Nezu, A. M., & Rao, S. M. (2018).
Journal article reporting standards for quantitative research in psychology: The APA
Publications and Communications Board task force report. American Psychologist, 73(1), 3–25.
https://doi.org/10.1037/amp0000191

Murphy, S. L., Merz, R., Reimann, L.-E., & Fernández, A. (2025). Nonsignificance misinterpreted
as an effect’s absence in psychology: Prevalence and temporal analyses. Royal Society Open
Science, 12(3), 242167. https://doi.org/10.1098/rsos.242167
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from metacheck.module import module
from metacheck.modules._empty import empty_input_result, paper_is_empty
from metacheck.report import collapse_section, format_ref, scroll_table

# R: format_ref(aczel2018) / format_ref(murphy2025), rendered by R's bibentry html style.
_ACZEL_2018 = (
    "Aczel B, Palfi B, Szollosi A, Kovacs M, Szaszi B, Szecsi P, Zrubka M, Gronau Q, "
    "van den Bergh D, Wagenmakers E (2018). &ldquo;Quantifying Support for the Null Hypothesis "
    "in Psychology: An Empirical Investigation.&rdquo; <em>Advances in Methods and Practices in "
    "Psychological Science</em>, <b>1</b>(3), 357&ndash;366. "
    '<a href="https://doi.org/10.1177/2515245918773742">doi:10.1177/2515245918773742</a>.'
)
_MURPHY_2025 = (
    "Murphy S, Merz R, Reimann L, Fernández A (2025). &ldquo;Nonsignificance misinterpreted as "
    "an effect’s absence in psychology: Prevalence and temporal analyses.&rdquo; <em>Royal "
    "Society Open Science</em>, <b>12</b>(3), 242167. "
    '<a href="https://doi.org/10.1098/rsos.242167">doi:10.1098/rsos.242167</a>.'
)

# "==" is a typo of "=" (metacheck counts "p == .03" as non-significant, U127)
# "p ≈ 0" or "p ~ .01" is significant too (metacheck 0.4.4.3, #642)
_SIG_COMPS = ["<", "=", "==", "≤", "<=", "=<", "≈", "~"]

_EXPLANATION = [
    "Meta-scientific research has shown nonsignificant p values are commonly misinterpreted. "
    "It is incorrect to infer that there is 'no effect', 'no difference', or that groups are "
    "'the same' after p > 0.05.",
    "It is possible that there is a true non-zero effect, but that the study did not detect it. "
    "Make sure your inference acknowledges that it is possible that there is a non-zero effect. "
    "It is correct to include the effect is 'not significantly' different, although this just "
    "restates that p > 0.05.",
    "Metacheck does not yet analyze automatically whether sentences which include "
    "non-significant p-values are correct, but we recommend manually checking the sentences "
    "below for possible misinterpreted non-significant p values.",
]

_GUIDANCE_INTRO = (
    "For metascientific articles demonstrating the rate of misinterpretations of non-significant "
    "results is high, see:"
)
_GUIDANCE_OUTRO = (
    "For educational material on preventing the misinterpretation of p values, see "
    "[Improving Your Statistical Inferences]"
    "(https://lakens.github.io/statistical_inferences/01-pvalue.html#sec-misconception1)."
)


@module(
    title="Non-Significant P Value Check",
    description=(
        "This module checks for imprecisely reported p values. If p > .05 is detected, it warns "
        "for misinterpretations."
    ),
    details="""
        The nonsignificant p-value check searches for regular expressions that match a predefined pattern. The module identifies all p-values in a manuscript and selects those that are not reported to be smaller than or equal to 0.05. It returns all sentences containing non-significant p-values.

        In the future, the Metacheck team aims to incorporate a machine learning classifier to only return sentences likely to contain misinterpretations. If you want to help to improve the module, reach out to the Metacheck development team.

        A p-value in a table note ("† p < .10, * p < .05") or a figure legend ("dashed lines indicate non-significant paths (p > .05)") is left out, as in stat_p_exact; the column `not_a_test` says why. A p-value next to words that state a threshold is kept, because such sentences often report a result against that level ("not significant at the conventional threshold (p = .054)").

        This module only checks p-values reported in the running text of the manuscript. It cannot (yet) process p-values reported only in tables.

        <validation>Validated on three hand-coded sets of papers (scienceverse/validation: 249 Psychological Science, 250 Economics and 250 MDPI papers). Of 1,783 non-significant p-values in the ground truth, version 0.4.4.3 found 1,719 (3.6% missed), and 94.8% of its 1,814 detections were in the ground truth. Some differences come from the ground truth: it lacks p-values with a correction name ("p Bonf = .061"), and counts partial eta squared written with its subscript after it ("η 2 p = .05"), "p = .05", and some table notes and figure legends ("† p < .10, *p < .05"), which this module leaves out, as non-significant p-values. These figures measure agreement with the hand-coding, which has known errors in both directions (scienceverse/validation#48).</validation>
    """,
    keywords=["results"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def stat_p_nonsig(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/stat_p_nonsig.R::stat_p_nonsig()``."""
    from metacheck.stats.helpers import _p_not_test
    from metacheck.text.expand import text_expand
    from metacheck.text.extract import extract_p_values

    if paper_is_empty(paper):
        return empty_input_result(paper)

    # detailed table of results ----
    table = extract_p_values(paper)

    # Specify conditions for a significant result
    value = table["p_value"].astype("float64").to_numpy()
    cond = (
        ~np.isnan(value)
        & (np.nan_to_num(value, nan=np.inf) <= 0.05)
        & table["p_comp"].isin(_SIG_COMPS).to_numpy(dtype=bool)
    )

    table = table.copy()
    table["significance"] = pd.Series(
        np.where(cond, "significant", "nonsignificant"), index=table.index, dtype="string"
    )
    table = table.loc[~cond].reset_index(drop=True)

    # Expand the sentences so the full sentence can be seen
    table = text_expand(table, paper, expand_to="sentence")

    # A p-value in a table note or a figure legend is not a result whose interpretation can be
    # checked ("† p < .10, * p < .05", "dashed lines indicate non-significant paths (p > .05)"):
    # left out, as stat_p_exact does (.p_not_test(); #604, #642). A "threshold" is kept: in 40
    # that .p_not_test() took for one, 14 were results reported against the level ("significant
    # when the categorical measure was used (p = .003) but not when the continuous measure was
    # used (p = .08)"); markers and legends were results in 3 and 2 of 40.
    table = table.copy()
    table["not_a_test"] = pd.Series(_p_not_test(table), index=table.index, dtype=object)
    table = table.loc[table["not_a_test"].isna() | table["not_a_test"].eq("threshold")].reset_index(
        drop=True
    )

    # summary_table ----
    # must have id column as the id of each paper, one row per paper
    # further columns to be added to a master summary table
    nonsig = table["significance"].eq("nonsignificant").fillna(False).astype(int)
    sums = nonsig.groupby(table["paper_id"], sort=False, dropna=False).sum()
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(list(sums.index), dtype="string"),
            "n_nonsignificant": pd.array(sums.to_numpy(), dtype="Int64"),
        }
    )

    # traffic light ----
    # possible values: na, info, red, yellow, green, fail
    # no nonsignificant p values: nothing to check, so "na", not green
    tl = "yellow" if int(summary_table["n_nonsignificant"].sum()) > 0 else "na"

    report: Any
    if tl == "na":
        report = "We detected no nonsignificant p values."
        summary_text = report
    else:
        n = len(table)
        summary_text = (
            f"We found {n:d} non-significant p value{'' if n == 1 else 's'} that should be "
            "checked for appropriate interpretation."
        )

        guidance = [
            _GUIDANCE_INTRO,
            format_ref(_ACZEL_2018),
            format_ref(_MURPHY_2025),
            _GUIDANCE_OUTRO,
        ]

        report_table = table.loc[:, ["text", "expanded"]]
        report_table.columns = ["Text", "Sentence"]

        # report text: R pastes these blocks together with blank lines
        report = [
            *_EXPLANATION,
            scroll_table(report_table, colwidths=[0.1, 0.9], maxrows=10),
            collapse_section(guidance),
        ]

    # return a list ----
    return {
        "summary_table": summary_table,
        "table": table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
