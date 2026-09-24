"""Miscitation (port of ``inst/modules/ref_miscitation.R``)."""

from __future__ import annotations

import warnings
from typing import Any

import pandas as pd

from pytacheck._r.base import plural
from pytacheck._r.frames import bind_rows, count
from pytacheck.module import module

_COLS = ["paper_id", "bib_id", "doi", "citation", "reftext", "warning"]


class _NA:
    """Hashable stand-in for a missing value (R ``unique()`` treats NAs as equal)."""

    def __repr__(self) -> str:
        return "NA"


_NA_KEY = _NA()


def _is_na(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and x != x)


def _key(x: Any) -> Any:
    return _NA_KEY if _is_na(x) else x


def _chr(x: Any) -> str:
    """``sprintf("%s", x)`` / ``paste()`` of a value (``NA`` prints as ``"NA"``)."""
    from pytacheck._r.base import as_character

    if _is_na(x):
        return "NA"
    return x if isinstance(x, str) else str(as_character(x))


def _right_join(x: pd.DataFrame, y: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """``dplyr::right_join(x, y, by)``: matched rows in ``x`` order, then unmatched ``y`` rows.

    ``NA`` keys match each other (dplyr's default ``na_matches = "na"``).
    """
    inner = x.merge(y, on=by, how="inner", sort=False, suffixes=(".x", ".y"))
    keys = x.loc[:, by].drop_duplicates()
    hit = y.loc[:, by].merge(keys, on=by, how="left", sort=False, indicator=True)["_merge"]
    unmatched = y.loc[(hit != "both").to_numpy(dtype=bool)]
    return bind_rows([inner, unmatched]).loc[:, list(inner.columns)].reset_index(drop=True)


def _pivot_wider(counts: pd.DataFrame) -> pd.DataFrame:
    """``tidyr::pivot_wider(names_from = doi, values_from = bib_id, names_prefix = "miscite_")``.

    One row per ``paper_id`` and one column per DOI, both in order of first
    appearance. When a paper has several references with the same DOI the
    values are not uniquely identified: like tidyr, every value column then
    becomes a list column (missing cells ``None``) and a warning is given.
    """
    pids = counts["paper_id"].tolist()
    dois = counts["doi"].tolist()
    bib_ids = counts["bib_id"].tolist()

    id_order: dict[Any, Any] = {}
    doi_order: dict[Any, Any] = {}
    cells: dict[tuple[Any, Any], list[Any]] = {}
    for pid, doi, bid in zip(pids, dois, bib_ids, strict=True):
        pk, dk = _key(pid), _key(doi)
        id_order.setdefault(pk, pid)
        doi_order.setdefault(dk, doi)
        cells.setdefault((pk, dk), []).append(None if _is_na(bid) else bid)

    out = pd.DataFrame(
        {"paper_id": pd.array([None if _is_na(v) else v for v in id_order.values()], "string")}
    )
    listcols = any(len(v) > 1 for v in cells.values())
    if listcols:
        warnings.warn(
            "Values from `bib_id` are not uniquely identified; output will contain list-cols.",
            stacklevel=3,
        )
    bid_dtype = counts["bib_id"].dtype
    for dk, doi in doi_order.items():
        name = "miscite_" + _chr(doi)
        vals = [cells.get((pk, dk)) for pk in id_order]
        if listcols:
            out[name] = pd.Series(vals, dtype=object)
        else:
            col = [None if v is None else v[0] for v in vals]
            try:
                out[name] = pd.array(col, dtype=bid_dtype)
            except (TypeError, ValueError):
                out[name] = pd.Series(col, dtype=object)
    return out


def _miscite_db() -> pd.DataFrame:
    from pytacheck.db.databases import miscite

    return miscite()


@module(
    title="Miscitation",
    description=(
        "Check for frequently miscited papers. This module is just a proof of concept -- the "
        "miscite database is not yet populated with real examples."
    ),
    details="""
        If you want to use your own database, create a data frame with the columns "doi", "reftext", and "warning", which will be used to match the DOI in papers (use short DOIs like xxx/1234, not https://doi.org/xxx/1234) and produce the report text.
    """,
    keywords=["reference"],
    author=["Lisa DeBruine <lisa.debruine@glasgow.ac.uk>"],
    params={
        "paper": "a paper object or paperlist object",
        "db": "the miscitation database (data frame with doi, reftext, and warning columns)",
    },
)
def ref_miscitation(paper: Any, db: pd.DataFrame | None = None) -> dict[str, Any]:
    """Port of ``inst/modules/ref_miscitation.R::ref_miscitation()``.

    ``db`` defaults to the bundled miscite database
    (``readRDS(system.file("databases/miscite.Rds", package = "metacheck"))``,
    :func:`pytacheck.db.miscite`).
    """
    from pytacheck.papers.tables import paper_table

    if db is None:
        db = _miscite_db()
    elif not isinstance(db, pd.DataFrame):
        db = pd.DataFrame(db)

    # consolidate bib tables and filter to relevant DOI
    bibs = paper_table(paper, "bib", ["paper_id", "bib_id", "doi"])
    bibs = (
        bibs.merge(db, on="doi", how="inner", sort=False, suffixes=(".x", ".y"))
        .drop_duplicates()
        .reset_index(drop=True)
    )

    # consolidate xrefs, filter, and expand
    text = paper_table(paper, "text")
    xref = paper_table(paper, "xref")
    xref = xref.loc[xref["xref_id"].notna().to_numpy(dtype=bool)]
    joined = xref.merge(
        text, on=["paper_id", "text_id"], how="left", sort=False, suffixes=(".x", ".y")
    )
    cited = joined.loc[:, ["paper_id", "xref_id", "text"]].set_axis(
        ["paper_id", "bib_id", "citation"], axis=1
    )
    xrefs = _right_join(cited, bibs, ["paper_id", "bib_id"]).drop_duplicates()
    xrefs = xrefs.reset_index(drop=True)

    # detailed table of results ----
    table = xrefs.loc[:, _COLS].drop_duplicates().reset_index(drop=True)

    # summary output for paperlists ----
    counted = count(table, ["paper_id", "bib_id", "doi"]).drop(columns="n")
    summary_table = _pivot_wider(counted)

    # determine the traffic light ----
    tl = "yellow" if len(table) > 0 else "green"

    # report text for each possible traffic light ----
    report: Any
    if len(table) == 0:
        summary_text = "We detected no miscited papers"
        report = summary_text
    else:
        to_warn = xrefs.loc[:, ["doi", "warning", "reftext"]].drop_duplicates()

        n = len(table)
        summary_text = f"We found {n:d} citation{plural(n)} to papers that are commonly miscited."

        all_dois = xrefs["doi"].tolist()
        citations = xrefs["citation"].tolist()
        report = []
        for warn_doi, warning, reftext in to_warn.itertuples(index=False, name=None):
            # R: xrefs$citation[xrefs$doi == warn_doi] (an NA comparison selects NA)
            if _is_na(warn_doi):
                all_instances: list[Any] = [None] * len(all_dois)
            else:
                all_instances = [
                    None if _is_na(d) else c
                    for d, c in zip(all_dois, citations, strict=True)
                    if _is_na(d) or d == warn_doi
                ]
            instances = all_instances[:5]

            n_head = len(instances)
            n_all = len(all_instances)
            instance_n = f"{n_head:d} of {n_all:d}" if n_head < n_all else f"{n_head:d}"
            quotes = "\n\n".join(f"> {_chr(c)}" for c in instances) if instances else "> "

            report.append(
                f"**{_chr(warn_doi)}**\n\n{_chr(reftext)}\n\n{_chr(warning)}\n\n"
                f"*{instance_n} Instance{plural(n_all)}:*\n\n{quotes}"
            )

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
