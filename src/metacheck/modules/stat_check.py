"""StatCheck (port of ``inst/modules/stat_check.R``)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from metacheck._r.regex import grepl
from metacheck.module import module
from metacheck.papers.model import PaperList
from metacheck.report import collapse_section, format_ref, scroll_table
from metacheck.stats.core import (
    _paper_says_one_sided,
    _stat_bound_or_variance_fits,
    _stat_one_sided_fits,
)

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
_EQUIVALENCE = (
    r"\bequivalence\s+(?:tests?|testing|bounds?|margins?|intervals?|ranges?)\b|\bTOST\b"
    r"|\btwo\s+one[- ]sided\s+tests?\b"
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

        Statcheck considers p = 0.000 an error, as you should report p < 0.001. Furthermore, p < 0.03 is an error if the p-value was 0.031, and one should simply report exact p-values (p = 0.031). When the sentence of a test says it is one-sided ("one-tailed", "one-sided" or "directional"), a p-value that fits a one-sided test counts as consistent (statcheck's `OneTailedTxt` setting; #695). When the paper says anywhere that its tests are one-sided, a p-value that fits a one-sided test in either direction counts as consistent too: half the two-sided p, or 1 minus that half for an effect against the predicted direction. Equivalence tests (TOST) are left out, as their p-values do not test the difference. A statistic written with "<" or ">" is read as a bound, and in a sentence about equal variances an F-test's two-sided p (twice the one-tailed p) counts as consistent. For more information, see [StatCheck](https://statcheck.io/).

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
    from metacheck._r import r_round
    from metacheck.stats.core import stats

    # detailed table of results ----
    # a test whose own sentence says it is one-sided is checked as one (metacheck 0.4.4.26,
    # #695)
    stat_table = stats(paper, OneTailedTxt=True)

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

    # An equivalence test (TOST) reports the p of a test against the equivalence
    # bounds, not of the difference, so statcheck's p cannot be compared with it
    # (metacheck #695, point 5)
    if "text" in table.columns:
        texts = ["" if pd.isna(t) else str(t) for t in table["text"]]
    else:
        texts = [""] * len(table)
    is_equivalence = grepl(_EQUIVALENCE, texts, ignore_case=True, perl=True)
    table = table.loc[~np.asarray(is_equivalence, dtype=bool)].reset_index(drop=True)
    # In a paper that says its tests are one-sided, anywhere in its text, a p that
    # fits a one-sided test in either direction is consistent (#695, point 5)
    if len(table):
        one_sided = _paper_says_one_sided(paper)
        in_paper = np.array(
            [
                (not pd.isna(pid)) and bool(one_sided.get(str(pid), False))
                for pid in table["paper_id"]
            ],
            dtype=bool,
        )
        fits = np.array([f is True for f in _stat_one_sided_fits(table)], dtype=bool)
        is_error = (table["error"] == True).to_numpy(dtype=bool)  # noqa: E712
        fit = is_error & in_paper & fits
        table["one_sided_fit"] = pd.array(fit, dtype="boolean")
        table.loc[fit, ["error", "decision_error"]] = False
        # a statistic written with "<" or ">" is a bound, and an F-test for equal
        # variances reports a two-sided p (#695, point 6)
        is_error = (table["error"] == True).to_numpy(dtype=bool)  # noqa: E712
        bfit = is_error & np.array(
            [f is True for f in _stat_bound_or_variance_fits(table)], dtype=bool
        )
        table["bound_or_variance_fit"] = pd.array(bfit, dtype="boolean")
        table.loc[bfit, ["error", "decision_error"]] = False

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
