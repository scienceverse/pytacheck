"""Reference Consistency (port of ``inst/modules/ref_consistency.R``)."""

from __future__ import annotations

import numbers
from typing import Any

import numpy as np
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

# metacheck's typo "likley" is fixed (U83)
_CAVEAT = (
    "This module relies on Grobid correctly parsing the references. There are likely to be "
    "some false positives."
)


def _key_kind(s: pd.Series) -> str:
    """The vctrs type class of a join key: ``chr``, ``num``, ``lgl`` or ``unspecified``.

    vctrs checks types even for zero-row or all-NA keys (a character column of
    NAs still refuses an integer key), but R's logical ``NA`` joins anything, so
    only untyped missing values (an object column without values, an all-NaN
    numpy float column, an all-NA logical column) are "unspecified".
    """
    dt = s.dtype
    if pd.api.types.is_object_dtype(dt):
        kinds = {
            "lgl"
            if isinstance(v, bool | np.bool_)
            else "num"
            if isinstance(v, numbers.Number)
            else "chr"
            for v in s.dropna().tolist()
        }
        if not kinds:
            return "unspecified"
        return "chr" if "chr" in kinds else "num" if "num" in kinds else "lgl"
    if (
        len(s)
        and not s.notna().any()
        and (pd.api.types.is_bool_dtype(dt) or (isinstance(dt, np.dtype) and dt.kind == "f"))
    ):
        return "unspecified"  # NaN-filled or logical NA; typed string/Int64 keep their type
    if pd.api.types.is_bool_dtype(dt):
        return "lgl"
    return "num" if pd.api.types.is_numeric_dtype(dt) else "chr"


def _align_key(x: pd.DataFrame, y: pd.DataFrame, key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give *key* one dtype on both sides, as a dplyr join does.

    dplyr joins integer, double and logical keys, but refuses a character key
    against a numeric or logical one (``Can't join `x$bib_id` with `y$bib_id`
    due to incompatible types.``), e.g. character ``xref_id``s.
    """
    kinds = {_key_kind(x[key]), _key_kind(y[key])}
    if "chr" in kinds and kinds & {"num", "lgl"}:
        raise TypeError(f"Can't join `x${key}` with `y${key}` due to incompatible types.")
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


def _full_join(x: pd.DataFrame, y: pd.DataFrame, by: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """``dplyr::full_join()``: x's rows (with matches) then y's unmatched rows, in order.

    Also returns which rows came from *y* alone (no match in *x*).
    """
    from pytacheck._r.frames import bind_rows

    for k in by:
        x, y = _align_key(x, y, k)
    left = x.merge(y, on=by, how="left", sort=False)
    matched = y.merge(x.loc[:, by].drop_duplicates(), on=by, how="left", indicator=True)
    y_only = matched.loc[matched["_merge"] == "left_only"].drop(columns="_merge")
    out = bind_rows([left, y_only])
    out = out.loc[:, list(left.columns)].reset_index(drop=True)
    from_y = pd.Series([False] * len(left) + [True] * len(y_only), dtype=bool)
    return out, from_y


def _flag_counts(table: pd.DataFrame, flag: pd.Series, papers: pd.Series) -> pd.Series:
    """``count(table, paper_id, x = flag) |> pivot_wider(...)``, keeping only ``x_TRUE``.

    Per paper: the number of flagged rows, 0 for a paper whose rows are all
    unflagged and NA for a paper with no rows (``module_run()``'s ``na_replace``
    makes it 0). metacheck's ``pivot_wider()`` makes no ``_TRUE`` column when
    no row has the flag, so the ``n_missing``/``n_extra`` columns came and went
    with the input (U115); here they are always there.
    """
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
    from pytacheck.io.bibr12 import _bibr12_paper_ids
    from pytacheck.papers.tables import paper_id, paper_table, ref_table
    from pytacheck.report import scroll_table

    # detailed table of results ----
    refs = ref_table(paper)
    bibs = refs.loc[:, ["paper_id", "bib_id", "text"]].rename(columns={"text": "reference"})
    xref_all = paper_table(paper, "xref")
    if len(xref_all.columns) == 0:
        # a paper list with no xref tables at all: dplyr::select() would fail on it
        raise ValueError(
            "Can't select columns that don't exist.\n✖ Column `paper_id` doesn't exist."
        )
    # bibr 12.x papers cite a reference with a "bib" xref whose target_id is
    # the bib_id (their xref_id is the row's own key)
    v12 = xref_all["paper_id"].isin(_bibr12_paper_ids(paper)).to_numpy(dtype=bool)
    if v12.any():
        xref_all = xref_all.copy()
        xref_all.loc[v12, "xref_id"] = xref_all.loc[v12, "target_id"]
    xref_type = xref_all["xref_type"]
    is_bib = np.where(
        v12,
        xref_type.isin(["bib"]).to_numpy(dtype=bool),
        xref_type.isin(["bibr"]).to_numpy(dtype=bool),
    )
    xrefs = xref_all.loc[is_bib, ["paper_id", "xref_id", "contents", "text_id"]].rename(
        columns={"xref_id": "bib_id"}
    )
    xrefs = xrefs.reset_index(drop=True)
    text = paper_table(paper, "text").loc[:, ["paper_id", "text_id", "text"]]

    joined, from_xref = _full_join(bibs, xrefs, _KEYS)
    # a citation is missing from the bibliography when it has no bib_id or its
    # bib_id is not a reference; metacheck keeps only the first (U115)
    missing = (joined["bib_id"].isna() | from_xref).to_numpy(dtype=bool)
    keep = joined["contents"].isna().to_numpy(dtype=bool) | missing
    table = joined.loc[keep].reset_index(drop=True)
    missing = missing[keep]
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
    is_missing = pd.Series(missing, index=table.index, dtype=bool)
    for name, flag in (
        ("n_missing", is_missing),
        ("n_extra", table["contents"].isna() & ~is_missing),
    ):
        summary_table[name] = _flag_counts(table, flag, summary_table["paper_id"])

    # traffic light ----
    if len(bibs) == 0:
        tl = "na"
    elif len(table) > 0:
        tl = "red"
    else:
        tl = "green"

    # report ----
    report_table = pd.DataFrame(
        {
            "bib_id": table["bib_id"],
            "type": pd.Series(
                ["missing" if v else "extra" for v in missing.tolist()],
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
