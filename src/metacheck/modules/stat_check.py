"""StatCheck (port of ``inst/modules/stat_check.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck.module import module
from metacheck.papers.model import PaperList
from metacheck.report import collapse_section, format_ref, scroll_table

# R: format_ref(Nuijten2017) / format_ref(Nuijten2023), rendered by R's bibentry html style.
_NUIJTEN_2017 = (
    "Nuijten M, van Assen M, Hartgerink C, Epskamp S, Wicherts J (2017). "
    "&ldquo;The validity of the tool &quot;statcheck&quot; in discovering statistical "
    "reporting inconsistencies.&rdquo; "
    '<a href="https://doi.org/10.31234/osf.io/tcxaja">doi:10.31234/osf.io/tcxaja</a>, Preprint.'
)
_NUIJTEN_2023 = (
    "Nuijten M, Wicherts J (2023). "
    "&ldquo;The effectiveness of implementing statcheck in the peer review process to avoid "
    "statistical reporting errors.&rdquo; "
    '<a href="https://doi.org/10.31234/osf.io/bxau9">doi:10.31234/osf.io/bxau9</a>, Preprint.'
)

_NO_STATS = (
    "No detectable t- or F-tests. StatCheck currently only detects statistics written in APA "
    "format."
)
_NO_VALIDATED_REPORT = (
    "No t-tests or F-tests detected.\n\n"
    "The accuracy of StatCheck has only been validated for *t*-tests and *F*-tests."
)
_RED_REPORT = (
    "We detected possible errors in test statistics. Note that as the accuracy of statcheck has "
    "only been validated for *t*-tests and *F*-tests. As Metacheck only uses validated modules, "
    "we only provide statcheck results for *t* tests and *F*-tests."
)
_LABELS = {
    "raw": "Text",
    "computed_p": "Recomputed p",
    "section": "Section",
    "text": "Sentence",
}


def _paper_ids(paper: Any) -> pd.DataFrame:
    """``data.frame(paper_id = paper$paper_id)``: the paper IDs, one row each.

    A paper list has no ``paper_id`` element, so metacheck builds a data frame
    with no columns (U124; ``module_run()`` adds the IDs back); here it has the
    IDs of the list's papers.
    """
    if isinstance(paper, PaperList):
        ids = list(dict.fromkeys(p.paper_id for p in paper))
        return pd.DataFrame({"paper_id": pd.Series(ids, dtype="string")})
    pid = getattr(paper, "paper_id", None)
    if pid is None:
        return pd.DataFrame()
    return pd.DataFrame({"paper_id": pd.Series([pid], dtype="string")})


@module(
    title="StatCheck",
    description="Check consistency of p-values and test statistics",
    details="""
        The Statcheck module runs Statcheck. Statcheck searches for regular expressions that match a predefined pattern, and identifies APA reported statistical tests. More information on the package can be found at <https://github.com/cran/statcheck>. The module only returns Statcheck results for t-tests and F-tests, as these are the only tests which have been validated, see <https://osf.io/preprints/psyarxiv/tcxaj_v1/>.

        Statcheck was developed by Michèle Nuijten and Sascha Epskamp.

        Statcheck considers p = 0.000 an error, as you should report p < 0.001. Furthermore, p < 0.03 is an error if the p-value was 0.031, and one should simply report exact p-values (p = 0.031). Statcheck might miss one-sided tests, and falsely assume the p-value is incorrect. For more information, see [StatCheck](https://statcheck.io/).

        This module only checks statistical results reported in the running text of the manuscript. It cannot (yet) process statistics reported only in tables.

        <validation>In a sample of 685 tests with 34 instances of inconsistent reporting, Statcheck correctly detected 34 of them, and incorrectly identified 26. Therefore 0% of true instances were missed, and 43% of detections were false positives. See <https://osf.io/preprints/psyarxiv/tcxaj_v1/> for more details of the validation.</validation>
    """,
    keywords=["results"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={"paper": "a paper object or paperlist object"},
)
def stat_check(paper: Any) -> dict[str, Any]:
    from metacheck.stats._rmath import r_round
    from metacheck.stats.core import stats

    # detailed table of results ----
    stat_table = stats(paper)

    # handle no stats
    if len(stat_table) == 0:
        return {
            "summary_table": _paper_ids(paper),
            "table": pd.DataFrame(),
            "traffic_light": "na",
            "report": _NO_STATS,
            "summary_text": _NO_STATS,
        }

    # We only select t-tests and F-tests for now,
    # as statcheck is only validated for these tests.
    keep = stat_table["test_type"].isin(["t", "F"]).fillna(False).to_numpy(dtype=bool)
    table = stat_table.loc[keep].reset_index(drop=True)

    # no validated stats
    if len(table) == 0:
        return {
            "summary_table": _paper_ids(paper),
            "table": pd.DataFrame(),
            "traffic_light": "na",
            "report": _NO_VALIDATED_REPORT,
            "summary_text": "No t-tests or F-tests detected",
        }

    # summary output for paperlists ----
    groups = table.groupby("paper_id", sort=False, dropna=False)
    sizes = groups.size()
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(list(sizes.index), dtype="string"),
            "statcheck_found": pd.array(sizes.to_numpy(), dtype="Int64"),
            "statcheck_errors": pd.array(groups["error"].sum().to_numpy(), dtype="Int64"),
            "statcheck_decision_errors": pd.array(
                groups["decision_error"].sum().to_numpy(), dtype="Int64"
            ),
        }
    )

    # determine the traffic light ----
    errors = table["error"].fillna(False).to_numpy(dtype=bool)
    tl = "red" if errors.any() else "green"

    if tl == "green":
        report: Any = "We detected no errors in t-tests or F-tests."
        summary_text = report
    else:
        n_errors = int(errors.sum())
        summary_text = (
            f"{n_errors:d} possible error{'' if n_errors == 1 else 's'} in t-tests or F-tests"
        )

        # report_table ----
        # Only show these columns in the HTML view (if they exist)
        cols = [c for c in ("raw", "computed_p", "section", "text") if c in table.columns]
        report_table = table.loc[errors, cols].reset_index(drop=True)
        report_table["computed_p"] = [r_round(float(v), 5) for v in report_table["computed_p"]]
        report_table.columns = [_LABELS[c] for c in cols]

        guidance = [
            "For metascientific research on the validity of statcheck, and it's usefulness to "
            "prevent statistical reporting errors, see:",
            str(format_ref(_NUIJTEN_2017)),
            str(format_ref(_NUIJTEN_2023)),
        ]
        report = [
            _RED_REPORT,
            scroll_table(report_table, colwidths=["10em", None, None, None]),
            collapse_section(guidance),
        ]

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
