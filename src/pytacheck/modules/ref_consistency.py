"""Reference Consistency (port of ``inst/modules/ref_consistency.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck.module import module

__all__ = ["ref_consistency"]

_KEYS = ["paper_id", "bib_id"]

_REPORT = {
    "red": (
        "There are cross-references that are not in the bibliography and/or bibliography "
        "entries not cross-referenced in the text"
    ),
    "green": (
        "All cross-references were in the bibliography and bibliography entries were "
        "cross-referenced in the text"
    ),
    "na": "No bibliography entries were detected",
}

_CAVEAT = (
    "This module relies on Grobid correctly parsing the references. There are likley to be "
    "some false positives."
)


def _align_key(x: pd.DataFrame, y: pd.DataFrame, key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give *key* one dtype on both sides (R joins integer and double keys)."""
    dx, dy = x[key].dtype, y[key].dtype
    if dx == dy:
        return x, y
    num = pd.api.types.is_numeric_dtype
    if num(dx) and num(dy):
        target = "Float64" if "float" in str(dx).lower() or "float" in str(dy).lower() else "Int64"
    else:
        target = "string"
    x = x.assign(**{key: x[key].astype(target)})
    y = y.assign(**{key: y[key].astype(target)})
    return x, y


def _full_join(x: pd.DataFrame, y: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """``dplyr::full_join()``: x's rows (with matches) then y's unmatched rows, in order."""
    from pytacheck._r.frames import bind_rows

    for k in by:
        x, y = _align_key(x, y, k)
    left = x.merge(y, on=by, how="left", sort=False)
    matched = y.merge(x.loc[:, by].drop_duplicates(), on=by, how="left", indicator=True)
    y_only = matched.loc[matched["_merge"] == "left_only"].drop(columns="_merge")
    out = bind_rows([left, y_only])
    return out.loc[:, list(left.columns)].reset_index(drop=True)


def _flag_counts(table: pd.DataFrame, flag: pd.Series, papers: pd.Series) -> pd.Series | None:
    """``count(table, paper_id, x = flag) |> pivot_wider(...)``, keeping only ``x_TRUE``.

    ``None`` when no row has the flag (pivot_wider makes no ``_TRUE`` column);
    otherwise per paper: the number of flagged rows, 0 for a paper whose rows
    are all unflagged (``values_fill = 0``) and NA for a paper with no rows.
    """
    if not bool(flag.any()):
        return None
    counts = flag.astype(int).groupby(table["paper_id"], sort=False, dropna=False).sum()
    values = [int(counts[p]) if p in counts.index else pd.NA for p in papers.tolist()]
    return pd.Series(values, index=papers.index, dtype="Int64")


@module(
    title="Reference Consistency",
    description="Check if all references are cited and all citations are referenced",
    details="""
        This module is currently under development and should not be relied on until we have increased the accuracy of the reference labeling while importing papers. It has a high false-positive rate because grobid (the PDF-importing tool) tends to miss some references and falsely identify some text as citations.
    """,
    keywords=["reference"],
    author=["Lisa DeBruine <lisa.debruine@glasgow.ac.uk>"],
    params={"paper": "a paper object or paperlist object"},
)
def ref_consistency(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/ref_consistency.R::ref_consistency()``."""
    from pytacheck._r.frames import count
    from pytacheck.papers.tables import paper_id, paper_table, ref_table
    from pytacheck.report import scroll_table

    # detailed table of results ----
    refs = ref_table(paper)
    bibs = refs.loc[:, ["paper_id", "bib_id", "text"]].rename(columns={"text": "reference"})
    xref_all = paper_table(paper, "xref")
    if len(xref_all.columns) == 0:
        # a paper list with no xref tables at all: dplyr::filter() would fail on it
        raise ValueError("object 'xref_type' not found")
    is_bibr = (xref_all["xref_type"] == "bibr").fillna(False).astype(bool)
    xrefs = xref_all.loc[is_bibr, ["paper_id", "xref_id", "contents", "text_id"]].rename(
        columns={"xref_id": "bib_id"}
    )
    xrefs = xrefs.reset_index(drop=True)
    text = paper_table(paper, "text").loc[:, ["paper_id", "text_id", "text"]]

    joined = _full_join(bibs, xrefs, _KEYS)
    keep = joined["contents"].isna() | joined["bib_id"].isna()
    table = joined.loc[keep.to_numpy()].reset_index(drop=True)
    table, text = _align_key(table, text, "text_id")
    table = table.merge(text, on=["paper_id", "text_id"], how="left", sort=False)
    table = table.drop(columns="text_id")

    # summary_table ----
    nbibs = count(bibs, "paper_id", name="n_bib")
    nxrefs = count(xrefs, "paper_id", name="n_xrefs")
    summary_table = pd.DataFrame({"paper_id": pd.Series(paper_id(paper), dtype="string")})
    for part in (nbibs, nxrefs):
        part = part.assign(paper_id=part["paper_id"].astype("string"))
        summary_table = summary_table.merge(part, on="paper_id", how="left", sort=False)
    for name, flag in (
        ("n_missing", table["bib_id"].isna()),
        ("n_extra", table["contents"].isna()),
    ):
        col = _flag_counts(table, flag, summary_table["paper_id"])
        if col is not None:
            summary_table[name] = col

    # traffic light ----
    if len(bibs) == 0:
        tl = "na"
    elif len(table) > 0:
        tl = "red"
    else:
        tl = "green"

    # report ----
    contents_na = table["contents"].isna()
    report_table = pd.DataFrame(
        {
            "bib_id": table["bib_id"],
            "type": pd.Series(
                ["extra" if v else "missing" for v in contents_na.tolist()],
                index=table.index,
                dtype="string",
            ),
            "contents": table["contents"],
            "reference": table["reference"].where(table["reference"].notna(), table["text"]),
        }
    )
    report_text = [_CAVEAT, scroll_table(report_table)]

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report_text,
        "summary_text": _REPORT[tl],
    }
