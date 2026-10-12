"""PubPeer Comments (port of ``inst/modules/ref_pubpeer.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck._r.base import plural
from metacheck.module import module

__all__ = ["ref_pubpeer"]

_NO_REFS = {"traffic_light": "na", "summary_text": "We found no references with DOIs"}
_FAILED = (
    "PubPeer could not be reached (the request failed), so the references were not checked "
    "for comments."
)


def _refs_with_doi(paper: Any) -> pd.DataFrame:
    """``ref_table(paper) |> dplyr::filter(!is.na(doi), doi != "")``."""
    from metacheck.papers.tables import ref_table

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
        # R: NULL[...] is NULL, and inner_join(bib, NULL) fails in dplyr's
        # auto_copy() (U23); the module reports the failed request instead
        raise TypeError("`x` and `y` must share the same src.")
    if "total_comments" not in pp.columns or "users" not in pp.columns:
        return pp.iloc[0:0]  # R: `NULL > 0` is logical(0), which selects no rows
    tc = pd.to_numeric(pp["total_comments"], errors="coerce")
    keep = ((tc > 0) & (pp["users"].astype("string") != "Statcheck")).fillna(False)
    out = pp.loc[keep.to_numpy(dtype=bool)]
    # pubpeer_comments() returns a row per requested DOI, so a DOI cited k times
    # joined k * k rows (U118): one row per DOI
    out = out.drop_duplicates(subset="doi").reset_index(drop=True)
    if pd.api.types.is_numeric_dtype(out["total_comments"]):
        # R: pubpeer_comments()' `total_comments[is.na(...)] <- 0` makes it a double
        out["total_comments"] = out["total_comments"].astype("float64")
    return out


def _summarise_comments(table: pd.DataFrame) -> pd.DataFrame:
    """``dplyr::summarise(table, .by = "paper_id", pubpeer_comments = sum(total_comments))``.

    Each DOI counts once per paper (a DOI in two reference entries is one work).
    """
    table = table.drop_duplicates(subset=["paper_id", "doi"])
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
    from metacheck.db.pubpeer import pubpeer_comments
    from metacheck.modules.ref_summary import _join
    from metacheck.report import link, scroll_table

    # create table ----
    bib = _refs_with_doi(paper)

    # If there are no rows, return immediately
    if len(bib) == 0:
        return dict(_NO_REFS)

    ## join to  pubpeer ----
    found = pubpeer_comments(bib["doi"].tolist())
    if found is None:
        # a failed request (non-200) is reported, not a crash (U23)
        return {"traffic_light": "fail", "summary_text": _FAILED, "report": _FAILED}
    pp = _commented(found)
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
        # plural() as in the other branch (metacheck: "1 references", U82)
        report = f"We checked {n_doi:d} reference{plural(n_doi)} with DOIs. {summary_text}"
    else:
        ## summary_text ----
        # each commented work once per paper (U118)
        works = table.drop_duplicates(subset=["paper_id", "doi"])
        tc = pd.to_numeric(works["total_comments"], errors="coerce")
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
        # every commented reference, with a link where PubPeer gives one
        # (metacheck leaves out references without a URL, U118)
        if "url" not in table.columns:
            table = table.assign(url=pd.Series([None] * len(table), dtype="string"))
        report_table = table.loc[:, ["text", "total_comments", "url"]].reset_index(drop=True)
        urls = report_table["url"].tolist()
        links = [x if isinstance(x, str) else "" for x in (link(urls, "link") if urls else [])]
        report_table["url"] = pd.array(links, dtype="string")
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
