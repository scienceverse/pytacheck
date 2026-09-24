"""Replication Check (port of ``inst/modules/ref_replication.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck._r.base import plural, trimws
from pytacheck._r.regex import grepl, gsub
from pytacheck.module import module
from pytacheck.report import link, scroll_table

_NO_REFS = {"traffic_light": "na", "summary_text": "We found no references with DOIs"}

# FLoRA column -> the name the module gives it (R: dplyr::select(doi = doi_o, ...))
_FLORA_COLS = {
    "doi_o": "doi",
    "apa_ref_r": "replication_ref",
    "doi_r": "replication_doi",
    "url_r": "replication_url",
    "outcome": "replication_outcome",
    "type": "replication_type",
}


def _is_na(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and x != x)


def _chr(x: Any) -> str:
    """``sprintf("%s", x)`` for a character value (``NA`` prints as ``"NA"``)."""
    return "NA" if _is_na(x) else str(x)


def _refs_with_doi(paper: Any) -> pd.DataFrame:
    """``ref_table(paper) |> dplyr::filter(!is.na(doi), doi != "")``."""
    from pytacheck.papers.tables import ref_table

    bib = ref_table(paper)
    doi = bib["doi"]
    keep = (doi.notna() & (doi != "")).fillna(False).to_numpy(dtype=bool)
    return bib.loc[keep].reset_index(drop=True)


# -- tools::toTitleCase() -----------------------------------------------------------

_TC_ALONE = frozenset(
    [
        "2D", "3D", "AIC", "BayesX", "GoF", "HTML", "LaTeX", "MonetDB", "OpenBUGS", "TeX",
        "U.S.", "U.S.A.", "WinBUGS", "aka", "et", "al.", "ggplot2", "i.e.", "jar", "jars",
        "ncdf", "netCDF", "rgl", "rpart", "xls", "xlsx",
        # "either" words
        "all", "above", "after", "along", "also", "among", "any", "both", "can", "few", "it",
        "less", "log", "many", "may", "more", "over", "some", "their", "then", "this", "under",
        "until", "using", "von", "when", "where", "which", "will", "without", "yet", "you",
        "your",
    ]
)  # fmt: skip
_TC_LPAT = (
    r"^(a|an|and|are|as|at|be|but|by|en|for|if|in|is|nor|not|of|on|or|per|so|the|to|v[.]?"
    r"|via|vs[.]?|from|into|than|that|with)$"
)
_TC_SPLIT = frozenset(' -/"()\n\t')


def _split_string(x: str) -> list[str]:
    """``tools:::C_splitString``: runs of text, each delimiter its own token."""
    out: list[str] = []
    buf: list[str] = []
    for ch in x:
        if ch in _TC_SPLIT:
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(ch)
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _title_case1(x: Any) -> Any:
    if _is_na(x):
        return None
    xx = _split_string(str(x))
    n = len(xx)
    if n == 0:
        return ""
    alone = [t in _TC_ALONE for t in xx]
    quoted = grepl(r"^'.*'$", xx)
    alone = [a or bool(q) for a, q in zip(alone, quoted, strict=True)]
    havecaps = grepl(r"^[[:alpha:]].*[[:upper:]]+", xx)
    low = [bool(v) for v in grepl(_TC_LPAT, xx, ignore_case=True)]
    low[0] = False
    ends = [i for i, v in enumerate(grepl(r"[-:]$", xx)) if v]
    ends = [i for i in ends if i + 2 <= n - 1]
    ends = [i for i in ends if xx[i + 1] == " " and grepl(r"^['[:alnum:]]", xx[i + 2])]
    ends = [i for i in ends if not (xx[i] == "-" and grepl(_TC_LPAT, xx[i + 2]))]
    for i in ends:
        low[i + 2] = False
    for i in [i for i, t in enumerate(xx) if t == '"' and i + 1 <= n - 1]:
        low[i + 1] = False
    xx = [t.lower() if lo else t for t, lo in zip(xx, low, strict=True)]
    out = []
    for t, hc, lo, al in zip(xx, havecaps, low, alone, strict=True):
        if hc or lo or len(t) == 1 or al:
            out.append(t)
        elif len(t) >= 3 and t[0] in ("'", '"'):
            out.append(t[0] + t[1].upper() + t[2:].lower())
        else:
            out.append(t[:1].upper() + t[1:].lower())
    return "".join(out)


def _to_title_case(text: list[Any]) -> list[Any]:
    """``tools::toTitleCase()`` (vectorised; ``NA`` stays ``NA``)."""
    return [_title_case1(t) for t in text]


def _summarise_n(table: pd.DataFrame, name: str) -> pd.DataFrame:
    """``dplyr::summarise(table, .by = "paper_id", <name> = dplyr::n())``."""
    sizes = table.groupby("paper_id", sort=False, dropna=False).size()
    return pd.DataFrame(
        {
            "paper_id": pd.array(list(sizes.index), dtype="string"),
            name: pd.array([int(v) for v in sizes.tolist()], dtype="Int64"),
        }
    )


@module(
    title="Replication Check",
    description=(
        "This module checks references and warns for citations of original studies for which "
        "replication or reproduction studies exist in the FLoRA database."
    ),
    details="""
        The Replication Check module compares the reference list against studies in the FLoRA (FORRT Library of Replication Attempts) database based on the DOI. If a study in the database is found, a reminder is provided that a replication or reproduction of the original study exists, and should be cited (currently, a warning is provided regardless of whether the replication/reproduction study is already cited).

        The module requires that the reference has a DOI. If you run the ref_doi_check module in a pipeline before this, it will use the enhanced DOI list from that module, otherwise it will only run on references with existing DOIs.

        It is possible the original study was cited for other reasons than the empirical claim tested, or that the replication/reproduction in the FLoRA database is for only one of the studies in the paper, and not the study the authors discuss.

        The database can be manually updated with the `FLoRA_update()` function. For more information, see <https://forrt.org/FLoRA/>.
    """,
    keywords=["reference"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
        "Lukas Wallrich <lukas.wallrich@gmail.com>",
    ],
    params={
        "paper": "a paper object or paperlist object",
        "show_outcomes": (
            "logical. If TRUE, include replication outcome\n"
            "and type in the report table. Default is FALSE."
        ),
    },
)
def ref_replication(paper: Any, show_outcomes: bool = False) -> dict[str, Any]:
    """Port of ``inst/modules/ref_replication.R::ref_replication()``.

    Joins the paper's references (with their ``bib_match``-fixed DOIs, via
    ``ref_table()``) to the FLoRA database of replications on the original
    study's DOI, dropping replications whose DOI is already cited.
    """
    from pytacheck.db.replications import FLoRA

    # create table ----
    bib = _refs_with_doi(paper)

    # If there are no rows, return immediately
    if len(bib) == 0:
        return dict(_NO_REFS)

    ## join to flora table
    flora = FLoRA()
    flora = flora.loc[flora["doi_o"].isin(bib["doi"]).to_numpy(dtype=bool), list(_FLORA_COLS)]
    flora = flora.rename(columns=_FLORA_COLS)
    table = bib.merge(flora, on="doi", how="inner", sort=False)

    ## remove rows that are already cited (by DOI); R compares with the DOIs of
    ## every paper's references (bib$doi), also for a paper list
    rep_doi = table["replication_doi"]
    has_rep_doi = (rep_doi.notna() & (rep_doi != "")).fillna(False)
    already_cited = has_rep_doi & rep_doi.isin(bib["doi"]).fillna(False)
    table = table.loc[~already_cited.to_numpy(dtype=bool)].reset_index(drop=True)

    # Remove trailing URLs from reference text to avoid duplication with link
    refs = trimws(gsub(r"https?://[^[:space:]]+$", "", table["replication_ref"], ignore_case=True))
    table["replication_ref"] = pd.array([None if _is_na(v) else v for v in refs], dtype="string")

    # traffic_light ----
    tl = "info" if len(table) else "na"

    # summary_table ----
    summary_table = _summarise_n(table, "replications")

    # summary_text & report ----
    n_doi = int(bib["doi"].notna().sum())
    report: Any
    if len(table) == 0:
        summary_text = "No citations to articles in the FLoRA database were found."
        report = f"We checked {n_doi:d} references with DOIs. {summary_text}"
    else:
        ## summary_text ----
        types = table["replication_type"]
        n_replications = int((types == "replication").fillna(False).sum())
        n_reproductions = int((types == "reproduction").fillna(False).sum())
        n_originals = int(table["doi"].nunique(dropna=False))

        if n_reproductions == 0:
            summary_text = (
                f"We found {n_replications:d} replication{plural(n_replications)} for "
                f"{n_originals:d} original{plural(n_originals)} you cited."
            )
        elif n_replications == 0:
            summary_text = (
                f"We found {n_reproductions:d} reproduction{plural(n_reproductions)} for "
                f"{n_originals:d} original{plural(n_originals)} you cited."
            )
        else:
            summary_text = (
                f"We found {n_replications:d} replication{plural(n_replications)} and "
                f"{n_reproductions:d} reproduction{plural(n_reproductions)} for "
                f"{n_originals:d} original{plural(n_originals)} you cited."
            )

        ## report_text ----
        has_both_types = n_replications > 0 and n_reproductions > 0

        if n_reproductions == 0:
            study_type_text = "replication studies"
            col_header = "Replication"
        elif n_replications == 0:
            study_type_text = "reproduction studies"
            col_header = "Reproduction"
        else:
            study_type_text = "replication/reproduction studies"
            col_header = "Replication/Reproduction"

        report_text = (
            f"We checked {n_doi:d} reference{plural(n_doi)} with DOIs. {summary_text}\n\n"
            f"Check if you are aware of the {study_type_text}, and cite them where appropriate."
        )

        ## report_table ----
        # Create links using DOI if available, otherwise use URL
        rep_doi = table["replication_doi"]
        has_doi = (rep_doi.notna() & (rep_doi != "")).fillna(False).tolist()
        # Format DOI links with doi: prefix to match format_ref() output
        bare_doi = [_chr(v) for v in gsub(r"^https?://doi.org/", "", rep_doi)]
        doi_links = link(
            url=[f"https://doi.org/{d}" for d in bare_doi],
            text=[f"doi:{d}" for d in bare_doi],
        )
        url_links = link(table["replication_url"].tolist())
        replication_links = [
            d if h else u for h, d, u in zip(has_doi, doi_links, url_links, strict=True)
        ]
        ref_text = table["replication_ref"].tolist()

        # Only label entries with type if both replications and reproductions are present
        if has_both_types:
            type_label = _to_title_case(table["replication_type"].tolist())
            rep_col = [
                f"<b>[{_chr(t)}]</b> {_chr(r)} {_chr(lk)}"
                for t, r, lk in zip(type_label, ref_text, replication_links, strict=True)
            ]
        else:
            rep_col = [
                f"{_chr(r)} {_chr(lk)}" for r, lk in zip(ref_text, replication_links, strict=True)
            ]
        report_table = pd.DataFrame(
            {
                "Reference": table["text"].to_numpy(),
                col_header: pd.array(rep_col, dtype="string"),
            }
        )

        if show_outcomes:
            report_table["Outcome"] = table["replication_outcome"].to_numpy()
            report_table["Type"] = table["replication_type"].to_numpy()

        ## report ----
        colwidths = [0.3, 0.4, 0.15, 0.15] if show_outcomes else [0.5, 0.5]
        report = [report_text, scroll_table(report_table, colwidths=colwidths)]

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
