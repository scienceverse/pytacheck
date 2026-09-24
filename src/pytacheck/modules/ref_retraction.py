"""RetractionWatch (port of ``inst/modules/ref_retraction.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck._r.base import plural
from pytacheck.module import module
from pytacheck.report import scroll_table

_NO_REFS = {"traffic_light": "na", "summary_text": "We found no references with DOIs"}


def _refs_with_doi(paper: Any) -> pd.DataFrame:
    """``ref_table(paper) |> dplyr::filter(!is.na(doi), doi != "")``."""
    from pytacheck.papers.tables import ref_table

    bib = ref_table(paper)
    doi = bib["doi"]
    keep = (doi.notna() & (doi != "")).fillna(False).to_numpy(dtype=bool)
    return bib.loc[keep].reset_index(drop=True)


def _summarise_by_paper(table: pd.DataFrame, values: pd.Series, name: str) -> pd.DataFrame:
    """``dplyr::summarise(table, .by = "paper_id", <name> = sum(values))``.

    Groups keep their order of first appearance; the result is an integer column.
    """
    sums = values.groupby(table["paper_id"], sort=False, dropna=False).sum()
    return pd.DataFrame(
        {
            "paper_id": pd.array(list(sums.index), dtype="string"),
            name: pd.array([int(v) for v in sums.tolist()], dtype="Int64"),
        }
    )


@module(
    title="RetractionWatch",
    description=(
        "This module checks references and warns for citations in the RetractionWatch Database."
    ),
    details="""
        The RetractionWatch Check module compares the reference list against studies in the RetractionWatch database based on the DOI. If a study in the database is found, a reminder is provided that the study was retracted, has an expression of concern, or a correction.

        The module requires that the reference has a DOI. If you run the ref_doi_check module in a pipeline before this, it will use the enhanced DOI list from that module, otherwise it will only run on references with existing DOIs.

        It is possible the authors are already aware that a study was retracted, but the module can't evaluate this.

        The database can be manually updated with the rw_update function. For more information, see https://gitlab.com/crossref/retraction-watch-data.
    """,
    keywords=["reference"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={"paper": "a paper object or paperlist object"},
)
def ref_retraction(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/ref_retraction.R::ref_retraction()``.

    Joins the paper's references (with their ``bib_match``-fixed DOIs, via
    ``ref_table()``) to the RetractionWatch database on the DOI.
    """
    from pytacheck.db.retractionwatch import retractionwatch

    # table ----
    bib = _refs_with_doi(paper)

    # If there are no rows, return immediately
    if len(bib) == 0:
        return dict(_NO_REFS)

    ## join to rw table (dplyr::inner_join keeps bib's order)
    rw = retractionwatch()
    rw = rw.loc[rw["doi"].isin(bib["doi"]).to_numpy(dtype=bool)]
    table = bib.merge(rw, on="doi", how="inner", sort=False).reset_index(drop=True)

    # traffic_light ----
    tl = "info" if len(table) else "na"

    # summary_table ----
    summary_table = _summarise_by_paper(table, table["retractionwatch"].notna(), "retractionwatch")

    # summary_text & report ----
    n_doi = int(bib["doi"].notna().sum())
    report: Any
    if len(table) == 0:
        summary_text = "No citations to articles in the RetractionWatch database were found."
        report = f"We checked {n_doi:d} references with DOIs. {summary_text}"
    else:
        n = len(table)
        summary_text = f"You cited {n:d} article{plural(n)} in the RetractionWatch database."
        report_text = (
            f"We checked {n_doi:d} reference{plural(n_doi)} with DOIs. {summary_text}\n\n"
            "Check if you are aware of the retraction, correction, or expression of concern "
            "for each reference, and consider whether it still supports your claims."
        )
        report_table = table.loc[:, ["text", "retractionwatch"]].set_axis(
            ["Reference", "RW Type"], axis=1
        )
        report = [report_text, scroll_table(report_table)]

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
