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


def test_na_text_id_is_matched_without_regrouping() -> None:
    # a test without a text_id has no sentence to pool with: it is matched as
    # extracted (R's split() drops it, U142)
    res = match_reported_output(_tt([1, None], [T1, T2]), LONG)
    assert res["text_id"].isna().tolist() == [False, True]
    assert res.attrs["summary"]["n_tests"] == 2


def test_tests_without_text_ids_are_matched() -> None:
    # R: every test dropped by the regrouping, then "invalid argument type"
    for text_id in ([None, None], None):
        res = match_reported_output(_tt(text_id, [T1, T2]), LONG)
        assert res["text_id"].isna().all()
        assert res.attrs["summary"]["n_tests"] == 2


def test_absent_text_id_without_sites() -> None:
    # R: data.frame(text_id = NULL, ...) errors
    novalue = _long([("a", "t", "n/a")])
    res = match_reported_output(_tt(None, [T1]), novalue)
    assert res["text_id"].isna().tolist() == [True]
    assert not res["found"].iloc[0]
    assert _tests_from_extract(_tt(None, [T1]))[0]["text_id"] == []


def test_empty_test_id_site_is_a_site() -> None:
    # an output site named "" matches like any other (R: used_sites[[""]]
    # fails the call, or the site never matches, U142)
    long = _long([("", "t", "2.103"), ("", "p", "0.0481"), ("e", "t", "2.103")])
    res = match_reported_output(_tt([1], [T1]), long)
    assert res["found"].tolist()[0]
    sites = _build_sites(long)
    assert sites.names[0] == ""
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
    assert res["n_matched"].tolist() == [2]
    assert res["found"].iloc[0]


def test_infinite_values_do_not_match() -> None:
    # U142: an Inf value simply does not match (R: if (NA) fails the call)
    long = _long([("i", "t", "1e400"), ("i", "p", "0.01")])
    tt = _tt([1], [[_c("t", "1e400", 1), _c("p", ".01", 2)]])
    res = match_reported_output(tt, long)
    assert res["n_matched"].tolist() == [1]
    other = _tt([1], [[_c("F", "1e400", 1), _c("p", ".01", 2)]])
    res = match_reported_output(other, long)
    assert res["n_matched"].tolist() == [1]


def test_na_sentence_pos_is_never_near() -> None:
    rows = [
        [_c("t", "2.10", None), _c("p", ".048", None)],
        [_c("d", "0.45", None), _c("p", ".048", None)],
    ]
    tests = _tests_from_extract(_tt([4, 4], rows))
    assert all(math.isnan(c["pos"]) for t in tests for c in t["components"])
    long = _long([("p", "t", "2.103"), ("p", "p", "0.0481"), ("p", "d", "0.45")])
    out = _regroup_by_evidence(tests, _build_sites(long))
    assert [c["name"] for c in out[0]["components"]] == ["t", "p", "d", "p"]
    # the two identical p components stay distinct: nothing was split off
    # (R's match(list, list) maps the second onto the first, U143)
    assert out[0]["plausible_split"] is True
    # an unknown position is not within text_proximity (R: if (NA > x) fails)
    near = _regroup_by_evidence(tests, _build_sites(long), text_proximity=3)
    assert [c["name"] for c in near[0]["components"]] == ["t"]


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
    # no section_id: no caption, the tests are still built (R's
    # .table_caption(paper, NULL) fails and every table test is lost, U142)
    q = pc.test_paper(["x"])
    q.table = p.table.drop(columns=["section_id"])
    assert _table_tests(q)[0]["text_id"] == -1000001
    res = match_reported_output(q, LONG, include_tables=True)
    assert len(res) == 1
    # no table_id: numbered by position (R: a zero-length text_id fails the call)
    r = pc.test_paper(["x"])
    r.text = r.text.assign(section_id=[2.0])
    r.table = p.table.drop(columns=["table_id"])
    assert _table_tests(r)[0]["text_id"] == -1000001
    res = match_reported_output(r, LONG, include_tables=True)
    assert res["text_id"].tolist() == [-1000001]
    # the table's own caption comes first (R reads only the text rows, U143)
    c = pc.test_paper(["x"])
    c.table = p.table.assign(caption=["Correlations between the scales"])
    assert _table_tests(c)


def test_table_caption_column_is_used() -> None:
    # U143: a paper that is not bibr 12.x but whose tables carry a caption
    # column is typed by that caption; R reads only the text rows sharing the
    # table's section_id, so an ambiguous correlation matrix went untyped
    import pytacheck as pc

    p = pc.test_paper(["Results are in Table 1.", "Table 1. Scales"])
    p.text = p.text.assign(section_id=[1.0, 2.0])
    matrix = [["", "1", "2"], ["1. Anxiety", "", ""], ["2. Mood", ".45", ""]]
    p.table = pd.DataFrame(
        {
            "table_id": pd.Series([1], dtype="Int64"),
            "section_id": pd.Series([2], dtype="Int64"),
            "contents": pd.Series([matrix], dtype=object),
        }
    )
    # the text-row caption ("Table 1. Scales") names no statistic
    assert _table_tests(p) == []
    p.table = p.table.assign(caption=["Correlations between the scales"])
    comps = [c for t in _table_tests(p) for c in t["components"]]
    assert [(c["family"], c["value"]) for c in comps] == [("r", 0.45)]
    # a missing or empty caption falls back to the text rows
    p.table = p.table.assign(caption=[""])
    assert _table_tests(p) == []
    p.text = p.text.assign(text=["Results are in Table 1.", "Table 1. Correlations"])
    comps = [c for t in _table_tests(p) for c in t["components"]]
    assert [c["family"] for c in comps] == ["r"]


def test_paper_list_is_refused_clearly() -> None:
    import pytacheck as pc

    papers = pc.PaperList([pc.test_paper(["t(20) = 2.10, p = .048"])])
    with pytest.raises(TypeError, match="for each paper"):
        match_reported_output(papers, LONG)
    # a caption lookup on a text table without its text column is ""
    s = pc.test_paper(["x"])
    s.text = s.text.assign(section_id=[2.0]).drop(columns=["text"])
    assert _table_caption(s, 2) == ""


def test_scientific_notation_is_matched_at_its_own_precision() -> None:
    # U143: "1.5e-05" is written to 6 decimals; R counts the mantissa's one
    # and matched any output value that rounds to 0.0
    tiny = _tt([1], [[_c("b", "1.5e-05", 1), _c("p", ".048", 2)]])
    near = match_reported_output(tiny, _long([("s", "b", "0.0000151"), ("s", "p", "0.048")]))
    assert near["n_matched"].tolist() == [2]
    other = match_reported_output(tiny, _long([("s", "b", "0.00003"), ("s", "p", "0.048")]))
    assert other["n_matched"].tolist() == [1]
    assert not other["found"].iloc[0]
    # an exponent beyond double range (310 decimals) does not overflow the
    # tolerance: the same tiny value matches, another one does not
    for reported, output, found in [("2e-310", "2e-310", True), ("2e-310", "1.5e-05", False)]:
        tiny = _tt([1], [[_c("p", reported, 1)]])
        out = match_reported_output(tiny, _long([("s", "p", output)]))
        assert out["found"].tolist() == [found]


def test_duplicated_components_keep_their_own_tests() -> None:
    # U143: a component repeated in two tests of a sentence is not merged
    # into the first (R's match(list, list) compares the deparsed elements)
    tests = _tests_from_extract(
        _tt([4, 4], [[_c("t", "2.10", 1), _c("p", ".048", 2)], [_c("t", "2.10", 1)]])
    )
    long = _long([("x", "t", "2.103"), ("x", "p", "0.048"), ("y", "t", "2.1")])
    out = _regroup_by_evidence(tests, _build_sites(long))
    assert sum(len(t["components"]) for t in out) == 3


def test_output_row_without_test_id_forms_no_site() -> None:
    # U142: an output row without a test_id beside a jamovi "_residuals" row
    # belongs to no site and changes nothing (R adds an NA site and val_in()
    # fails the whole call: "non-numeric argument to mathematical function")
    import pytacheck as pc

    paper = pc.test_paper(
        ["A one-way ANOVA showed an effect of gender, F(2, 2159) = 6.76, p = .001."]
    )

    def long(rows: list[tuple[str | None, str, str, str]]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "source_file": ["o.omv"] * len(rows),
                "test_id": pd.Series([r[0] for r in rows], dtype="string"),
                "analysis": ["ANOVA"] * len(rows),
                "row_label": [r[3] for r in rows],
                "statistic": [r[1] for r in rows],
                "value": [r[2] for r in rows],
            }
        )

    rows = [
        ("an_a_gender", "F", "6.76", "gender"),
        ("an_a_gender", "df", "2", "gender"),
        ("an_a_gender", "p", "0.0012", "gender"),
        ("an_a_residuals", "df", "2159", "residuals"),
    ]
    clean = match_reported_output(paper, long(rows))
    with_na = match_reported_output(paper, long([*rows, (None, "F", "5.5", "z")]))
    assert clean["n_matched"].tolist() == [4]
    pd.testing.assert_frame_equal(clean, with_na)
