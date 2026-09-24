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


# ---- review scenarios (parity/cases/mod_ref_pubpeer_summary_review.yaml) ----------

PP_DOIS.update(
    {
        "odd": [
            "10.9999/pp.emptyusers",
            "10.9999/pp.nulltc",
            "10.9999/pp.statlist",
            "10.9999/pp.emptyurl",
            "10.9999/pp.statcase",
        ],
        "nourl_only": ["10.9999/pp.nourl"],
        "nourl_some": ["10.9999/pp.nourl", "10.9999/pp.zero"],
    }
)


def pp_odd() -> Any:
    return pp_paper(PP_DOIS["odd"], "odd")


def pp_nourl_only() -> Any:
    return pp_paper(PP_DOIS["nourl_only"], "nourl_only")


def pp_nourl_some() -> Any:
    return pp_paper(PP_DOIS["nourl_some"], "nourl_some")


def pp_list_empty() -> Any:
    """A paper list whose second paper has no references at all."""
    import pytacheck as pc

    return pc.PaperList([pp_paper(PP_DOIS["odd"], "odd"), pp_paper([], "empty")])


def pp_case_list() -> Any:
    """Mixed-case paper ids and a bib_match table (ref_table() sorts in the C locale)."""
    import pytacheck as pc

    b = pp_paper(["10.9999/pp.one", "10.9999/pp.dup"], "b")
    A = pp_paper(["10.9999/pp.none", "10.9999/pp.stat"], "A")
    A.bib_match = pd.DataFrame(
        {
            "bib_id": pd.array([1], dtype="Int64"),
            "doi": pd.array(["10.9999/pp.many"], dtype="string"),
        }
    )
    a = pp_paper(["10.9999/pp.zero"], "a")
    return pc.PaperList([b, A, a])


def _column(values: Sequence[Any], n: int) -> Any:
    """An R vector: logical, character, integer or double (recycled to *n*)."""
    vals = list(values)
    if len(vals) == 1 and n != 1:
        vals = vals * n
    if all(v is None or isinstance(v, bool) for v in vals):
        return pd.array(vals, dtype="boolean")
    if all(v is None or isinstance(v, str) for v in vals):
        return pd.array(vals, dtype="string")
    if all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in vals):
        return pd.array(vals, dtype="Int64")
    return pd.array([float("nan") if v is None else float(v) for v in vals], dtype="float64")


def add_rows(out: Any, idx: Sequence[int], values: dict[str, Sequence[Any]]) -> Any:
    """Append copies of rows *idx* (0-based) of a module output's table, with *values* set."""
    table = out.table
    new = table.iloc[list(idx)].reset_index(drop=True)
    for col, vals in values.items():
        new[col] = _column(vals, len(new))
    out.table = pd.concat([table, new], ignore_index=True)
    return out


def set_table_cols(out: Any, values: dict[str, Sequence[Any]]) -> Any:
    """Replace several columns of a module output's table (R: ``out$table[[col]] <- v``)."""
    table = out.table.copy()
    for col, vals in values.items():
        table[col] = _column(vals, len(table))
    out.table = table
    return out


def rv_ret_collide() -> Any:
    """ref_retraction's table gains columns that collide with ref_summary's own."""
    import pytacheck as pc

    out = chain(pc.demopaper(), ["ref_accuracy", "ref_pubpeer", "ref_retraction"])
    return set_table_cols(out, {"pubpeer": ["P"], "pubpeer.x": ["PX"], "accuracy_mismatch": ["AM"]})


def rv_ret_keys() -> Any:
    """ref_retraction's table keeps only the join keys (and the dropped text/doi)."""
    import pytacheck as pc

    return drop_table_cols(chain(pc.demopaper(), ["ref_retraction"]), ["retractionwatch"])


def rv_acc_dup() -> Any:
    """Duplicated ref_accuracy rows (same group, and a new no_match group)."""
    import pytacheck as pc

    out = chain(pc.demopaper(), ["ref_accuracy"])
    out = add_rows(out, [3], {"title_mismatch": [True]})
    out = add_rows(out, [0], {"no_match": [False]})
    return add_rows(out, [1], {"no_match": [None], "year_mismatch": [True]})


def rv_acc_chr() -> Any:
    """Character ``*_mismatch`` / ``no_match`` columns."""
    import pytacheck as pc

    return set_table_cols(
        chain(pc.demopaper(), ["ref_accuracy"]),
        {
            "doi_mismatch": ["TRUE", "FALSE", "false", None, "0"],
            "year_mismatch": ["FALSE", "FALSE", "yes", "FALSE", "FALSE"],
            "container_mismatch": ["FALSE"],
            "title_mismatch": ["FALSE", "TRUE", "FALSE", "FALSE", None],
            "author_mismatch": ["FALSE"],
            "no_match": ["TRUE", "FALSE", "FALSE", None, "1"],
        },
    )


def rv_acc_num() -> Any:
    """Integer / double ``*_mismatch`` columns mixed with logical ones."""
    import pytacheck as pc

    return set_table_cols(
        chain(pc.demopaper(), ["ref_accuracy"]),
        {
            "doi_mismatch": [1, 0, None, 2, 0],
            "year_mismatch": [0.0, 0.5, 0.0, 0.0, 0.0],
            "title_mismatch": [False, False, None, False, True],
            "no_match": [1.0, 0.0, None, 0.0, 2.0],
        },
    )


def rv_acc_mixed() -> Any:
    """Logical and character ``*_mismatch`` columns (pivot_longer() cannot combine them)."""
    import pytacheck as pc

    return set_table_cols(
        chain(pc.demopaper(), ["ref_accuracy"]),
        {"doi_mismatch": ["TRUE", "FALSE", "FALSE", "FALSE", "FALSE"]},
    )


def rv_acc_na_chr() -> Any:
    """An all-NA logical column combines with character ones."""
    import pytacheck as pc

    return set_table_cols(
        chain(pc.demopaper(), ["ref_accuracy"]),
        {
            "doi_mismatch": [None],
            "year_mismatch": ["TRUE", "FALSE", "FALSE", "x", "FALSE"],
            "container_mismatch": ["FALSE"],
            "title_mismatch": ["FALSE"],
            "author_mismatch": ["FALSE"],
        },
    )


def rv_acc_extra() -> Any:
    """Columns grep() selects but ends_with() does not, and one ends_with() matches by case."""
    import pytacheck as pc

    return set_table_cols(
        chain(pc.demopaper(), ["ref_accuracy"]),
        {
            "title_mismatch_score": [0.1, 0.2, 0.3, 0.4, 0.5],
            "no_match_reason": ["a", "b", "c", "d", "e"],
            "doi_mismatch_MISMATCH": [False, True, False, False, None],
        },
    )


def rv_acc_no_mismatch() -> Any:
    """ref_accuracy without any ``*_mismatch`` column."""
    import pytacheck as pc

    out = chain(pc.demopaper(), ["ref_accuracy"])
    return drop_table_cols(out, [c for c in out.table.columns if str(c).endswith("_mismatch")])


def rv_pp_url_na() -> Any:
    """ref_pubpeer's table with missing URLs."""
    import pytacheck as pc

    return set_table_cols(chain(pc.demopaper(), ["ref_pubpeer"]), {"url": [None]})


def rv_rep_no_type() -> Any:
    """ref_replication's table without replication_type, with a duplicated row."""
    import pytacheck as pc

    out = add_rows(chain(pc.demopaper(), ["ref_replication"]), [0], {})
    return drop_table_cols(out, ["replication_type"])


def test_paper_id(text: Sequence[str], id: str) -> Any:
    """``test_paper()`` with a fixed ``paper_id`` (R's comes from the clock)."""
    import pytacheck as pc

    p = pc.test_paper(list(text))
    p.paper_id = id
    return p
