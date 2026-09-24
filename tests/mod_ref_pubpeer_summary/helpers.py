"""Helpers for the mod_ref_pubpeer_summary parity cases and tests.

The Python twin of ``tests/mod_ref_pubpeer_summary/helpers.R``: both build the
same synthetic papers and module chains. PubPeer responses for the synthetic
DOIs are recorded in ``tests/mod_ref_pubpeer_summary/mock`` (see
``make_mocks.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
MOCK_DIR = HERE / "mock"
ROOT = HERE.parent.parent

# Synthetic DOI sets (their PubPeer responses are in the mock directory).
PP_DOIS: dict[str, list[str | None]] = {
    "single": ["10.9999/pp.one"],
    "statcheck": ["10.9999/pp.stat", "10.9999/pp.none"],
    "fail": ["10.9999/pp.fail"],
    "p1": ["10.9999/PP.Upper", "10.9999/pp.zero", None, "", "10.9999/pp.dup", "10.9999/pp.dup"],
    "p2": ["10.9999/pp.nourl", "10.9999/pp.stat", "10.9999/pp.many"],
    "P0": ["10.9999/pp.dup", "10.9999/pp.nohit"],
    "mixed": [
        "10.1002/bdm.586",
        "10.1371/journal.ppat.1000066",
        "10.9999/pp.one",
        None,
        "10.9999/pp.stat",
        "10.1177/0956797614520714",
    ],
}


def pp_paper(dois: Sequence[str | None], id: str = "synthetic") -> Any:
    """A paper whose references have *dois* (``bib_id`` 0, 1, ...)."""
    import pytacheck as pc

    n = len(dois)
    refs = [
        f"Reference {i}. https://doi.org/{'NA' if d is None else d}" for i, d in enumerate(dois)
    ]
    p = pc.test_paper(["Body text.", *refs])
    p.paper_id = id
    p.bib = pd.DataFrame(
        {
            "bib_id": pd.array(range(n), dtype="Int64"),
            "text_id": pd.array(range(2, n + 2), dtype="Int64"),
            "doi": pd.array(list(dois), dtype="string"),
        }
    )
    return p


def pp_single() -> Any:
    return pp_paper(PP_DOIS["single"], "single")


def pp_statcheck() -> Any:
    return pp_paper(PP_DOIS["statcheck"], "statcheck")


def pp_fail() -> Any:
    return pp_paper(PP_DOIS["fail"], "fail")


def pp_mixed() -> Any:
    return pp_paper(PP_DOIS["mixed"], "mixed")


def pp_list() -> Any:
    """A paper list with duplicated, upper-case, NA and empty DOIs (see helpers.R)."""
    import pytacheck as pc

    return pc.PaperList([pp_paper(PP_DOIS[k], k) for k in ("p1", "p2", "P0")])


def demo_no_refs() -> Any:
    """The demo paper without references (testthat: "no references")."""
    import pytacheck as pc

    paper = pc.demopaper()
    paper.bib = paper.bib.iloc[0:0]
    paper.bib_match = paper.bib_match.iloc[0:0]
    return paper


def demo_no_dois() -> Any:
    """The demo paper keeping only references without a DOI (testthat: "no DOIs")."""
    import pytacheck as pc

    paper = pc.demopaper()
    bib = paper.bib
    paper.bib = bib.loc[bib["doi"].isna().to_numpy(dtype=bool)].reset_index(drop=True)
    del paper["bib_match"]
    return paper


def demo_no_match() -> Any:
    """The demo paper without bib_match (testthat: "no bib_match")."""
    import pytacheck as pc

    paper = pc.demopaper()
    del paper["bib_match"]
    return paper


def psychsci3() -> Any:
    """The psychsci fixture papers."""
    import pytacheck as pc

    d = ROOT / "upstream/metacheck/tests/testthat/fixtures/psychsci"
    return pc.read(
        [d / f for f in ("0956797613520608.json", "0956797614522816.json", "0956797614527830.json")]
    )


def chain(paper: Any, modules: Sequence[str]) -> Any:
    """Run modules one after another (``module_run(module_run(paper, m1), m2)`` ...)."""
    from pytacheck.module import module_run

    out = paper
    for m in modules:
        out = module_run(out, m)
    return out


def set_table_col(out: Any, col: str, values: Sequence[Any]) -> Any:
    """Replace one column of a module output's table (R: ``out$table[[col]] <- values``)."""
    table = out.table.copy()
    vals = list(values)
    if all(v is None or isinstance(v, bool) for v in vals):
        table[col] = pd.array(vals, dtype="boolean")
    elif all(v is None or isinstance(v, str) for v in vals):
        table[col] = pd.array(vals, dtype="string")
    else:
        table[col] = vals
    out.table = table
    return out


def drop_table_cols(out: Any, cols: Sequence[str]) -> Any:
    """Drop columns from a module output's table."""
    out.table = out.table.drop(columns=[c for c in cols if c in out.table.columns])
    return out


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


def set_mismatch(out: Any, value: bool | None) -> Any:
    """Set every ``*_mismatch`` column of a module output's table to *value*."""
    table = out.table.copy()
    for col in [c for c in table.columns if str(c).endswith("_mismatch")]:
        table[col] = pd.array([value] * len(table), dtype="boolean")
    out.table = table
    return out
