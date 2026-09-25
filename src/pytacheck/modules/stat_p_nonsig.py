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

from pytacheck.module import module
from pytacheck.report import collapse_section, format_ref, scroll_table

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
_SIG_COMPS = ["<", "=", "==", "≤", "<=", "=<"]

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

        This module only checks p-values reported in the running text of the manuscript. It cannot (yet) process p-values reported only in tables.

        <validation>In a sample of 194 papers with 1602 instances of non-significant p-values, this module correctly detected 1486 of them, and incorrectly identified 153. Additionally, 91% of detections were true instances (positive predictive value). That is, when this module flags non-significant p-values in a paper, it correctly identifies an issue 91% of the time.</validation>
    """,
    keywords=["results"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def stat_p_nonsig(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/stat_p_nonsig.R::stat_p_nonsig()``."""
    from pytacheck.text.expand import text_expand
    from pytacheck.text.extract import extract_p_values

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
    tl = "yellow" if int(summary_table["n_nonsignificant"].sum()) > 0 else "green"

    report: Any
    if tl == "green":
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
