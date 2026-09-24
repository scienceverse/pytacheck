"""Review tests for match_reported_output(): R's NA/NULL and error branches.

Each behaviour here was checked against metacheck (see the
statout_match_review parity cases); these tests pin the Python side.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from pytacheck.statout.match_reported import (
    _build_sites,
    _regroup_by_evidence,
    _tests_from_extract,
    match_reported_output,
)
from pytacheck.statout.match_table import _table_caption, _table_tests


def _tt(text_id: list[object] | None, comps: list[list[dict[str, object]]]) -> pd.DataFrame:
    cols: dict[str, object] = {"test_no": pd.Series(range(1, len(comps) + 1), dtype="Int64")}
    if text_id is not None:
        cols["text_id"] = pd.Series(text_id, dtype="Int64")
    cols["sentence"] = pd.Series(["s"] * len(comps), dtype="string")
    cols["components"] = pd.Series(comps, dtype=object)
    return pd.DataFrame(cols)


def _c(name: str, value: str, pos: object = 1, df: object = None) -> dict[str, object]:
    return {"name": name, "comp": "=", "value": value, "df": df, "sentence_pos": pos}


def _long(rows: list[tuple[object, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": ["o.omv"] * len(rows),
            "test_id": pd.Series([r[0] for r in rows], dtype="string"),
            "analysis": ["T-Test"] * len(rows),
            "row_label": ["x"] * len(rows),
            "statistic": [r[1] for r in rows],
            "value": [r[2] for r in rows],
        }
    )


T1 = [_c("t", "2.10", 1, "(20)"), _c("p", ".048", 2)]
T2 = [_c("t", "3.10", 1, "(30)"), _c("p", ".002", 2)]
LONG = _long(
    [
        ("a", "t", "2.103"), ("a", "df", "20"), ("a", "p", "0.0481"),
        ("b", "t", "3.1"), ("b", "df", "30"), ("b", "p", "0.0021"),
    ]
)  # fmt: skip


def test_na_text_id_is_dropped_by_the_regrouping() -> None:
    res = match_reported_output(_tt([1, None], [T1, T2]), LONG)
    assert res["text_id"].tolist() == [1]
    assert res.attrs["summary"]["n_tests"] == 1


def test_all_tests_dropped_errors_like_r() -> None:
    # bind_rows(list()) is a 0x0 tibble and sum(!out$found) is !NULL in R
    with pytest.raises(TypeError, match="invalid argument type"):
        match_reported_output(_tt([None, None], [T1, T2]), LONG)
    with pytest.raises(TypeError, match="invalid argument type"):
        match_reported_output(_tt(None, [T1, T2]), LONG)


def test_absent_text_id_without_sites_errors_like_r() -> None:
    # data.frame(text_id = NULL, ...) errors in R
    novalue = _long([("a", "t", "n/a")])
    with pytest.raises(ValueError, match="differing number of rows"):
        match_reported_output(_tt(None, [T1]), novalue)
    assert _tests_from_extract(_tt(None, [T1]))[0]["text_id"] == []


def test_empty_test_id_site_is_null() -> None:
    long = _long([("", "t", "2.103"), ("", "p", "0.0481"), ("e", "t", "2.103")])
    # the regrouping's site search hits used_sites[[""]] first
    with pytest.raises(RuntimeError, match="zero-length variable name"):
        match_reported_output(_tt([1], [T1]), long)
    sites = _build_sites(long)
    assert sites.names[0] == "" and sites.null_sites == [0]
    # a raw eq table (no regrouping) with censored components only: the ""
    # site is never matched, so only e's t counts
    eq = pd.DataFrame(
        {
            "text_id": [1, 1],
            "grp_id": [1, 1],
            "lhs": ["t", "p"],
            "df": [None, None],
            "comp": ["<", "<"],
            "rhs": ["3", ".05"],
        }
    )
    res = match_reported_output(eq, long)
    assert res["n_matched"].tolist() == [1]
    assert not res["found"].iloc[0]


def test_infinite_values_error_like_r() -> None:
    long = _long([("i", "t", "1e400"), ("i", "p", "0.01")])
    tt = _tt([1], [[_c("t", "1e400", 1), _c("p", ".01", 2)]])
    with pytest.raises(RuntimeError, match="missing value"):
        match_reported_output(tt, long)
    # another family never compares Inf with Inf
    other = _tt([1], [[_c("F", "1e400", 1), _c("p", ".01", 2)]])
    res = match_reported_output(other, long)
    assert res["n_matched"].tolist() == [1]


def test_na_sentence_pos_releases_nothing() -> None:
    rows = [
        [_c("t", "2.10", None), _c("p", ".048", None)],
        [_c("d", "0.45", None), _c("p", ".048", None)],
    ]
    tests = _tests_from_extract(_tt([4, 4], rows))
    assert all(math.isnan(c["pos"]) for t in tests for c in t["components"])
    long = _long([("p", "t", "2.103"), ("p", "p", "0.0481"), ("p", "d", "0.45")])
    out = _regroup_by_evidence(tests, _build_sites(long))
    assert [c["name"] for c in out[0]["components"]] == ["t", "p", "d", "p"]
    with pytest.raises(RuntimeError, match="missing value"):
        _regroup_by_evidence(tests, _build_sites(long), text_proximity=3)


def test_character_text_id_stays_character() -> None:
    tt = _tt([1, 2], [T1, T2])
    tt["text_id"] = pd.Series(["s10", "s2"], dtype="string")
    res = match_reported_output(tt, LONG)
    assert str(res["text_id"].dtype) == "string"
    assert res["text_id"].tolist() == ["s10", "s2"]


def test_table_tests_missing_columns() -> None:
    import pytacheck as pc

    p = pc.test_paper(["Results are in Table 1.", "Table 1. Tests"])
    p.text = p.text.assign(section_id=[1.0, 2.0])
    p.table = pd.DataFrame(
        {
            "table_id": pd.Series([1], dtype="Int64"),
            "section_id": pd.Series([2], dtype="Int64"),
            "contents": pd.Series(
                [[["", "t", "df", "p"], ["Score", "2.10", "20", ".048"]]], dtype=object
            ),
        }
    )
    assert _table_tests(p)[0]["text_id"] == -1000001
    # no section_id: .table_caption(paper, NULL) errors, so R gets no table tests
    q = pc.test_paper(["x"])
    q.table = p.table.drop(columns=["section_id"])
    with pytest.raises(ValueError, match="missing value"):
        _table_tests(q)
    res = match_reported_output(q, LONG, include_tables=True)
    assert len(res) == 0
    # no table_id: a zero-length text_id, and the result row errors
    r = pc.test_paper(["x"])
    r.text = r.text.assign(section_id=[2.0])
    r.table = p.table.drop(columns=["table_id"])
    assert _table_tests(r)[0]["text_id"] == []
    with pytest.raises(ValueError, match="differing number of rows"):
        match_reported_output(r, LONG, include_tables=True)
    # a caption lookup on a text table without its text column is ""
    s = pc.test_paper(["x"])
    s.text = s.text.assign(section_id=[2.0]).drop(columns=["text"])
    assert _table_caption(s, 2) == ""
