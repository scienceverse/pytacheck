"""Miscitation (port of ``inst/modules/ref_miscitation.R``)."""

from __future__ import annotations

import heapq
import warnings
from itertools import islice
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
    becomes a list column and a warning is given. Its missing cells are
    tidyr's ``NULL`` cells, stored as ``NaN`` (the module system's ``NULL``
    list cell, which a later ``na_replace`` leaves alone, as R's ``is.na()``
    does) rather than ``None`` (``NA``).
    """
    pids = counts["paper_id"].tolist()
    dois = counts["doi"].tolist()
    bib_ids = counts["bib_id"].tolist()

    # row / column positions in order of first appearance, and the cell values
    id_pos: dict[Any, int] = {}
    id_vals: list[Any] = []
    doi_pos: dict[Any, int] = {}
    doi_vals: list[Any] = []
    cells: dict[tuple[int, int], list[Any]] = {}
    for pid, doi, bid in zip(pids, dois, bib_ids, strict=True):
        pk, dk = _key(pid), _key(doi)
        i = id_pos.get(pk)
        if i is None:
            i = id_pos[pk] = len(id_vals)
            id_vals.append(None if _is_na(pid) else pid)
        j = doi_pos.get(dk)
        if j is None:
            j = doi_pos[dk] = len(doi_vals)
            doi_vals.append(doi)
        cells.setdefault((i, j), []).append(None if _is_na(bid) else bid)

    listcols = any(len(v) > 1 for v in cells.values())
    if listcols:
        warnings.warn(
            "Values from `bib_id` are not uniquely identified; output will contain list-cols.",
            stacklevel=3,
        )
    n = len(id_vals)
    missing: Any = float("nan") if listcols else None
    values: list[list[Any]] = [[missing] * n for _ in doi_vals]
    for (i, j), v in cells.items():
        values[j][i] = v if listcols else v[0]

    # build all columns at once (one DataFrame construction, not one insert per DOI)
    columns: dict[str, Any] = {"paper_id": pd.array(id_vals, dtype="string")}
    bid_dtype = counts["bib_id"].dtype
    for doi, col in zip(doi_vals, values, strict=True):
        name = "miscite_" + _chr(doi)
        if listcols:
            columns[name] = pd.Series(col, dtype=object)
        else:
            try:
                columns[name] = pd.array(col, dtype=bid_dtype)
            except (TypeError, ValueError):
                columns[name] = pd.Series(col, dtype=object)
    return pd.DataFrame(columns)


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

    # consolidate bib tables and filter to relevant DOI
    bibs = paper_table(paper, "bib", ["paper_id", "bib_id", "doi"])
    if db is None:
        db = _miscite_db()
    elif not isinstance(db, pd.DataFrame):
        db = pd.DataFrame(db)
    # dplyr::inner_join(bibs, db, by = "doi"): both tables need the key, which
    # must have compatible types
    for side, df in (("x", bibs), ("y", db)):
        if "doi" not in df.columns:  # e.g. a CERMINE paper's bib table has no doi
            raise ValueError(
                f"Join columns in `{side}` must be present in the data.\n✖ Problem with `doi`."
            )
    doi = db["doi"]
    character = (
        pd.api.types.is_string_dtype(doi)
        or doi.dtype == object
        or isinstance(doi.dtype, pd.CategoricalDtype)
    )
    if not character:
        # dplyr joins a character key only to a character (or factor) key, or
        # to a non-empty all-NA logical column (vctrs "unspecified"), which is
        # what pandas infers as an all-NaN float64 column
        if len(doi) == 0 or not bool(doi.isna().all()):
            raise TypeError("Can't join `x$doi` with `y$doi` due to incompatible types.")
        db = db.assign(doi=doi.astype("string"))
    bibs = (
        bibs.merge(db, on="doi", how="inner", sort=False, suffixes=(".x", ".y"))
        .drop_duplicates()
        .reset_index(drop=True)
    )

    # consolidate xrefs, filter, and expand
    text = paper_table(paper, "text")
    xref = paper_table(paper, "xref")
    # bibr 12.x papers cite a reference with a "bib" xref whose target_id is
    # the bib_id (their xref_id is the row's own key)
    if "paper_id" in xref.columns:
        from pytacheck.io.bibr12 import _bibr12_paper_ids

        v12 = xref["paper_id"].isin(_bibr12_paper_ids(paper)).to_numpy(dtype=bool)
        if v12.any():
            is_bib = xref["xref_type"].isin(["bib"]).to_numpy(dtype=bool)
            xref = xref.copy()
            xref.loc[v12, "xref_id"] = xref["target_id"].where(is_bib, pd.NA)[v12]
    if "xref_id" not in xref.columns:
        # R: dplyr::filter(!is.na(xref_id)) on an xref table without xref_id
        raise ValueError("In argument: `!is.na(xref_id)`.")
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

        # R: all_instances <- xrefs$citation[xrefs$doi == warn_doi]; a comparison
        # with NA selects NA, so rows with an NA DOI give an NA instance for every
        # DOI (and an NA warn_doi selects NA for every row). The rows of each DOI
        # are indexed once instead of scanning xrefs for every DOI.
        all_dois = xrefs["doi"].tolist()
        citations = xrefs["citation"].tolist()
        doi_rows: dict[Any, list[int]] = {}
        na_rows: list[int] = []
        for i, d in enumerate(all_dois):
            if _is_na(d):
                na_rows.append(i)
            else:
                doi_rows.setdefault(d, []).append(i)

        report = []
        for warn_doi, warning, reftext in to_warn.itertuples(index=False, name=None):
            if _is_na(warn_doi):
                n_all = len(all_dois)
                instances: list[Any] = [None] * min(5, n_all)
            else:
                rows = doi_rows.get(warn_doi, [])
                n_all = len(rows) + len(na_rows)
                head = list(islice(heapq.merge(rows, na_rows), 5)) if na_rows else rows[:5]
                instances = [None if _is_na(all_dois[i]) else citations[i] for i in head]

            n_head = len(instances)
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
