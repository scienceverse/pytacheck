"""PubPeer Comments (port of ``inst/modules/ref_pubpeer.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck._r.base import plural
from pytacheck.module import module

__all__ = ["ref_pubpeer"]

_NO_REFS = {"traffic_light": "na", "summary_text": "We found no references with DOIs"}


def _refs_with_doi(paper: Any) -> pd.DataFrame:
    """``ref_table(paper) |> dplyr::filter(!is.na(doi), doi != "")``."""
    from pytacheck.papers.tables import ref_table

    bib = ref_table(paper)
    doi = bib["doi"]
    keep = (doi.notna() & (doi != "")).fillna(False).to_numpy(dtype=bool)
    return bib.loc[keep].reset_index(drop=True)


def _commented(pp: pd.DataFrame | None) -> pd.DataFrame:
    """``pp[pp$total_comments > 0 & pp$users != "Statcheck", ]``.

    Rows where the condition is ``NA`` become all-``NA`` rows in R; their
    ``NA`` DOI can never join a reference with a DOI, so they are dropped.
    """
    if pp is None:
        # R: NULL[...] is NULL, and inner_join(bib, NULL) fails
        raise TypeError("`y` must be a data frame, not NULL.")
    tc = pd.to_numeric(pp["total_comments"], errors="coerce")
    keep = (tc > 0) & (pp["users"].astype("string") != "Statcheck")
    return pp.loc[keep.fillna(False).to_numpy(dtype=bool)].reset_index(drop=True)


def _summarise_comments(table: pd.DataFrame) -> pd.DataFrame:
    """``dplyr::summarise(table, .by = "paper_id", pubpeer_comments = sum(total_comments))``."""
    tc = pd.to_numeric(table["total_comments"], errors="coerce").astype("float64")
    sums = tc.groupby(table["paper_id"], sort=False, dropna=False).sum(min_count=0)
    return pd.DataFrame(
        {
            "paper_id": pd.array(list(sums.index), dtype="string"),
            "pubpeer_comments": pd.array(sums.tolist(), dtype="float64"),
        }
    )


@module(
    title="PubPeer Comments",
    description=(
        "This module checks references and warns for citations that have comments on pubpeer "
        "(excluding Statcheck comments)."
    ),
    details="""
        The PubPeer module uses the PubPeer API to check for each reference that has a DOI whether there are comments on the post-publication peer review platform. If comments are found, a link to the comments is provided. Comments by ‘Statcheck’ on PubPeer are ignored, see https://retractionwatch.com/2016/09/02/heres-why-more-than-50000-psychology-studies-are-about-to-have-pubpeer-entries/.

        The module requires that the reference has a DOI. If you run the doi_check module in a pipeline before this, it will use the enhanced DOI list from that module, otherwise it will only run on references with existing DOIs.

        For more information, see [PubPeer](https://www.pubpeer.com/static/about).
    """,
    keywords=["reference"],
    requires=["network"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={"paper": "a paper object or paperlist object"},
)
def ref_pubpeer(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/ref_pubpeer.R::ref_pubpeer()``.

    Looks up every reference with a DOI (``bib_match``-fixed, via
    ``ref_table()``) on PubPeer with ``pubpeer_comments()`` and keeps those
    with comments by someone other than Statcheck.
    """
    from pytacheck.db.pubpeer import pubpeer_comments
    from pytacheck.modules.ref_summary import _join
    from pytacheck.report import link, scroll_table

    # create table ----
    bib = _refs_with_doi(paper)

    # If there are no rows, return immediately
    if len(bib) == 0:
        return dict(_NO_REFS)

    ## join to  pubpeer ----
    pp = _commented(pubpeer_comments(bib["doi"].tolist()))
    table = _join(bib, pp, ["doi"], "inner")

    # traffic_light ----
    tl = "info" if len(table) else "na"

    # summary_table ----
    summary_table = _summarise_comments(table)

    # summary_text & report ----
    n_doi = int(bib["doi"].notna().sum())
    report: Any
    if len(table) == 0:
        summary_text = "No references with comments in PubPeer were found."
        report = f"We checked {n_doi:d} references with DOIs. {summary_text}"
    else:
        ## summary_text ----
        tc = pd.to_numeric(table["total_comments"], errors="coerce")
        n = int((tc > 0).fillna(False).sum())
        summary_text = f"You cited {n:d} reference{plural(n)} with comments in PubPeer."

        ## report_text ----
        report_text = (
            f"We checked {n_doi:d} reference{plural(n_doi)} with DOIs. {summary_text}\n\n"
            "Pubpeer is a platform for post-publication peer review. We have filtered out "
            "Pubpeer comments by 'Statcheck'. You can check out the comments by visiting the "
            "URLs below:"
        )

        ## report_table ----
        rows = table["url"].notna().to_numpy(dtype=bool)
        report_table = table.loc[rows, ["text", "total_comments", "url"]].reset_index(drop=True)
        urls = report_table["url"].tolist()
        report_table["url"] = pd.array(link(urls, "link") if urls else [], dtype="string")
        report_table.columns = ["Reference", "Comments", "PubPeer Link"]

        ## report ----
        report = [report_text, scroll_table(report_table, maxrows=10)]

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
