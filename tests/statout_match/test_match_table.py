"""Tests for pytacheck.statout.match_table (port of R/match-table.R).

metacheck has no dedicated testthat file for the table matcher (it runs
inside reproducibility_check with include_tables = TRUE); these tests pin the
five typing tiers. Exhaustive agreement with R is in the parity cases.
"""

from __future__ import annotations

import pandas as pd

import pytacheck as pc
from pytacheck.statout.match_reported import match_reported_output
from pytacheck.statout.match_table import (
    _table_caption,
    _table_caption_family,
    _table_header_ambiguous,
    _table_matrix_cols,
    _table_tests,
    _table_tests_one,
    _table_typed_cells,
)

CORR = [
    ["Variable", "α", "M", "1.", "2.", "Correlations / 3."],
    ["1. Depression", ".86", "2.10", "—", "", ""],
    ["2. Anxiety", ".81", "1.95", ".45", "—", ""],
    ["3. Stress", ".79", "2.40", ".38", ".52**", "—"],
]


def _paper(contents: list[list[str]], caption: str) -> pc.Paper:
    p = pc.test_paper(["Some text.", caption])
    txt = p.text.copy()
    txt["section_id"] = [1.0, 2.0]
    p.text = txt
    p.table = pd.DataFrame(
        {
            "table_id": pd.Series([3], dtype="Int64"),
            "section_id": pd.Series([2], dtype="Int64"),
            "contents": pd.Series([contents], dtype=object),
        }
    )
    return p


def test_caption_family() -> None:
    assert _table_caption_family("Table 1. Correlations") == "r"
    assert _table_caption_family("Fixed-effect estimates") == "b"
    assert _table_caption_family("Internal consistency") == "alpha"
    assert _table_caption_family("Means and standard deviations") == "mean"
    assert _table_caption_family("Sample characteristics") is None
    assert _table_caption_family(None) is None


def test_header_ambiguity() -> None:
    assert [_table_header_ambiguous(h) for h in ["", " ", "1.", "12", "---", "M", "1. x"]] == [
        True, True, True, True, True, False, False,
    ]  # fmt: skip


def test_typed_cells_tiers() -> None:
    content = [
        ["Predictor", "b", "Range / SE", "", "95% CI"],
        ["Age", "0.05", "0.02", "7", "[0.01, 0.09]"],
        ["Gender", "4 females", "n/a", "1.2a", "0.3"],
    ]
    rows = _table_typed_cells(content, "Regression coefficients")
    age = [(c["col"], c["family"], c["name"], c["value"]) for c in rows[0]]
    assert age == [
        (2, "b", "b", 0.05),
        (3, "se", "Range / SE", 0.02),  # typed on the LAST header segment
        (4, "b", "b", 7.0),  # blank header: the caption's family
        (5, "ci_lower", "ci_lower", 0.01),  # value shape wins
        (5, "ci_upper", "ci_upper", 0.09),
    ]
    # "4 females" (a numeric prefix + a word) is rejected; "1.2a" is kept; a
    # plain number under "95% CI" gets the generic "ci" family from its header
    gender = [(c["col"], c["family"], c["value"]) for c in rows[1]]
    assert gender == [(4, "b", 1.2), (5, "ci", 0.3)]
    assert _table_typed_cells([["only header"]], None) == []
    assert _table_typed_cells(None, None) == []


def test_matrix_columns_use_the_last_header_segment() -> None:
    assert _table_matrix_cols(CORR) == [4, 5, 6]
    assert _table_matrix_cols([["Var", "1.", "M"], ["a", ".3", "2"]]) == []


def test_table_tests_one_splits_matrix_cells() -> None:
    tests = _table_tests_one(3, CORR, "Table 1. Correlations among the variables")
    ids = [t["text_id"] for t in tests]
    # row tests: -(table_id * 1e6 + row); matrix cells: -(... + row * 1000 + i)
    assert ids == [-3000001, -3000002, -3002001, -3000003, -3003001, -3003002]
    assert all(t["grp_id"] == 1 for t in tests)
    assert [c["family"] for c in tests[0]["components"]] == ["alpha", "mean"]
    assert tests[2]["components"] == [
        {"family": "r", "name": "r", "value": 0.45, "dec": 2, "censored": ""}
    ]
    assert "col" not in tests[0]["components"][0]


def test_table_tests_and_caption_from_paper() -> None:
    p = _paper(CORR, "Table 1. Correlations among the variables.")
    assert _table_caption(p, 2) == "Table 1. Correlations among the variables."
    assert _table_caption(p, 9) is None
    assert _table_caption(p, None) is None
    assert len(_table_tests(p)) == 6
    assert _table_tests(pc.demopaper()) == []


def test_include_tables_matches_table_rows() -> None:
    p = _paper(CORR, "Table 1. Correlations among the variables.")
    long = pd.DataFrame(
        {
            "test_id": ["c12", "c23", "rel"],
            "statistic": ["r", "r", "alpha"],
            "value": ["0.452", "0.518", "0.862"],
        },
        dtype="string",
    )
    off = match_reported_output(p, long)
    assert len(off) == 0
    on = match_reported_output(p, long, include_tables=True)
    assert on["reported"].tolist()[:3] == ["α=0.86 M=2.1", "α=0.81 M=1.95", "r=0.45"]
    # the alpha alone is not enough for a two-component row; each matrix cell
    # is its own single-value test
    found = on[on["found"]]
    assert found["text_id"].tolist() == [-3002001, -3003002]
    assert found["reported"].tolist() == ["r=0.45", "r=0.52"]
    assert on["n_matched"].iloc[0] == 1
