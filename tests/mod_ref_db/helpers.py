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
    import metacheck as pc

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
    from metacheck.report.blocks import ReportTable

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
    import metacheck as pc

    paper = pc.demopaper()
    paper.bib = paper.bib.iloc[0:0]
    del paper["bib_match"]
    return paper


def demo_no_dois() -> Any:
    """The demo paper keeping only references without a DOI (testthat: "no DOIs")."""
    import metacheck as pc

    paper = pc.demopaper()
    bib = paper.bib.copy()
    bib.loc[bib.index[0], "doi"] = pd.NA
    paper.bib = bib.loc[bib["doi"].isna().to_numpy(dtype=bool)].reset_index(drop=True)
    del paper["bib_match"]
    return paper


def flora_originals(by: int = 1) -> list[str]:
    """Every *by*-th original DOI in the bundled FLoRA database (in order of first appearance)."""
    from metacheck.db.replications import FLoRA

    return list(dict.fromkeys(FLoRA()["doi_o"].tolist()))[::by]


def rw_sample(by: int = 100) -> list[str]:
    """One RetractionWatch DOI per notice type plus every *by*-th DOI.

    Sampled from the stored table, blank DOIs included, as R's ``rw()`` has
    them: :func:`~metacheck.db.retractionwatch.retractionwatch` leaves them out
    (U15), which would shift every *by*-th row and give R and Python different
    papers.
    """
    from metacheck.db.databases import load_database

    d = load_database("retractionwatch")
    first = d.loc[~d["retractionwatch"].duplicated().to_numpy(dtype=bool), "doi"].tolist()
    return list(dict.fromkeys(first + d["doi"].iloc[::by].tolist()))


def set_col(p: Any, table: str, col: str, values: Sequence[Any]) -> Any:
    """Replace one column of one of the paper's tables (R: ``p[[table]][[col]] <- values``)."""
    df = p[table].copy()
    vals = list(values)
    if all(v is None or isinstance(v, int) for v in vals):
        df[col] = pd.array(vals, dtype="Int64")
    else:
        df[col] = pd.array(vals, dtype="string")
    p[table] = df
    return p


def stress_papers() -> Any:
    """A deterministic paper list with many miscitation hits (see :func:`stress_db`)."""
    import metacheck as pc

    pool = [f"10.1000/s{i:02d}" for i in range(15)]
    ids = ["p10", "p2", "P1"]
    papers = []
    for k, pid in enumerate(ids, start=1):
        dois = [pool[(i + 4 * k) % 15] for i in range(12)]
        cites = [(i * (k + 2)) % 12 for i in range(10 + 5 * k)]
        papers.append(ref_paper(dois, id=pid, cites=cites))
    return pc.PaperList(papers)


def stress_db() -> pd.DataFrame:
    """The miscitation database for :func:`stress_papers`."""
    pool = [f"10.1000/s{i:02d}" for i in range(15)]
    return pd.DataFrame(
        {
            "doi": [pool[i] for i in (0, 2, 4, 4, 7, 11, 13)],
            "reftext": [f"Ref {i}" for i in range(1, 8)],
            "warning": [f"Warn {i}" for i in range(1, 8)],
        }
    )
