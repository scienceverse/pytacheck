"""Helpers for the mod_ref_db parity cases and tests.

The Python twin of ``tests/mod_ref_db/helpers.R``: both build the same
synthetic papers so the reference-database modules (``ref_retraction``,
``ref_replication``, ``ref_miscitation``) can be tested on DOIs that hit
RetractionWatch, FLoRA and the miscite database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd


def ref_paper(
    dois: Sequence[str | None],
    id: str = "synthetic",
    cites: Sequence[Sequence[int] | int] = (),
) -> Any:
    """A paper whose references have *dois* (``bib_id`` 0, 1, ...).

    Each element of *cites* is a body sentence citing those ``bib_id``\\ s
    (one ``bibr`` xref each).
    """
    import pytacheck as pc

    cites = [[c] if isinstance(c, int) else list(c) for c in cites]
    n = len(dois)
    body = [
        f"Body sentence {i} cites {' and '.join(str(b) for b in c)}."
        for i, c in enumerate(cites, start=1)
    ]
    refs = [
        f"Reference {i}. https://doi.org/{'NA' if d is None else d}" for i, d in enumerate(dois)
    ]
    p = pc.test_paper(body + refs)
    p.paper_id = id
    p.bib = pd.DataFrame(
        {
            "bib_id": pd.array(range(n), dtype="Int64"),
            "text_id": pd.array(range(len(body) + 1, len(body) + n + 1), dtype="Int64"),
            "doi": pd.array(list(dois), dtype="string"),
        }
    )
    ids = [b for c in cites for b in c]
    p.xref = pd.DataFrame(
        {
            "xref_id": pd.array(ids, dtype="Int64"),
            "xref_type": pd.array(["bibr"] * len(ids), dtype="string"),
            "contents": pd.array(["(ref)"] * len(ids), dtype="string"),
            "text_id": pd.array([i for i, c in enumerate(cites, start=1) for _ in c], "Int64"),
        }
    )
    return p


def report_tbl(o: Any) -> dict[str, Any]:
    """The first report table of a module output and its display arguments."""
    from pytacheck.report.blocks import ReportTable

    for block in o.report if isinstance(o.report, list) else [o.report]:
        if isinstance(block, ReportTable):
            return {
                "table": block.data,
                "colwidths": block.colwidths,
                "maxrows": block.maxrows,
                "escape": block.escape,
            }
    raise ValueError("the report has no table")


def demo_no_refs() -> Any:
    """The demo paper without references (testthat: "no references")."""
    import pytacheck as pc

    paper = pc.demopaper()
    paper.bib = paper.bib.iloc[0:0]
    del paper["bib_match"]
    return paper


def demo_no_dois() -> Any:
    """The demo paper keeping only references without a DOI (testthat: "no DOIs")."""
    import pytacheck as pc

    paper = pc.demopaper()
    bib = paper.bib.copy()
    bib.loc[bib.index[0], "doi"] = pd.NA
    paper.bib = bib.loc[bib["doi"].isna().to_numpy(dtype=bool)].reset_index(drop=True)
    del paper["bib_match"]
    return paper
