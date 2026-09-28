"""Tests for extract_tests() (R/extract-tests.R has no testthat file upstream)."""

from __future__ import annotations

import pandas as pd

import pytacheck as pc
from pytacheck.text.extract_tests import (
    _empty_tests,
    _is_anchor,
    _is_primary_anchor,
    _is_stat_name,
    _norm_stat_name,
    _render_test,
    _split_into_tests,
    extract_tests,
)

COLUMNS = [
    "paper_id",
    "test_no",
    "text_id",
    "paragraph_id",
    "section_id",
    "sentence",
    "anchor",
    "n_components",
    "reported",
    "components",
]


def comp(name: str, value: str, pos: int, comp_: str = "=", df: str | None = None) -> dict:
    return {"name": name, "comp": comp_, "value": value, "df": df, "sentence_pos": pos}


def test_norm_stat_name() -> None:
    assert _norm_stat_name("ηp²") == "etap2"
    assert _norm_stat_name("Cohen's d") == "cohens d"
    assert _norm_stat_name("Cohen’s   d") == "cohens d"
    assert _norm_stat_name("χ²") == "chi2"
    assert _norm_stat_name("Cronbach's α") == "cronbachs alpha"
    assert _norm_stat_name("95% CI") == "95 ci"
    assert _norm_stat_name(None) == ""


def test_anchor_roles() -> None:
    assert _is_anchor("t") and _is_primary_anchor("t")
    assert _is_anchor("d") and not _is_primary_anchor("d")
    assert not _is_anchor("p")
    assert not _is_anchor("range")
    assert _is_stat_name("SD")
    assert not _is_stat_name("height")
    assert not _is_stat_name("%")


def test_split_repeated_anchor() -> None:
    comps = [
        comp("t", "3.77", 1, df="(23)"),
        comp("p", ".001", 2),
        comp("d", "0.77", 3),
        comp("t", "2.69", 4, df="(23)"),
        comp("p", ".013", 5),
    ]
    groups = _split_into_tests(comps)
    assert [[c["sentence_pos"] for c in g] for g in groups] == [[1, 2, 3], [4, 5]]
    assert _render_test(groups[0]) == "t(23) = 3.77, p = .001, d = 0.77"


def test_split_estimate_after_ci() -> None:
    comps = [
        comp("F", "323.77", 1, df="(1,494)"),
        comp("p", ".001", 2, "<"),
        comp("95% CI", "[.22,.29]", 3),
        comp("Cohen's d", ".10", 4),
        comp("estimate", ".40", 5),
        comp("95% CI", "[.35,.44]", 6),
        comp("F", "375.81", 7, df="(1,573)"),
        comp("p", ".001", 8, "<"),
    ]
    groups = _split_into_tests(comps)
    # the estimate/CI lead-in has no anchor of its own and is dropped
    assert [[c["sentence_pos"] for c in g] for g in groups] == [[1, 2, 3, 4], [7, 8]]


def test_split_dual_role_anchors() -> None:
    comps = [
        comp("M", "3.28", 1),
        comp("95% CI", "[3.18, 3.38]", 2),
        comp("Cronbach's α", ".86", 3),
    ]
    groups = _split_into_tests(comps)
    assert [[c["sentence_pos"] for c in g] for g in groups] == [[1, 2], [3]]
    assert _split_into_tests([comp("p", ".05", 1)]) == []
    assert _split_into_tests([]) == []


def test_render_test_null_fields() -> None:
    # an absent comp is R's NULL (rendered "="); a missing df is dropped
    assert _render_test([{"name": "d", "value": "0.77"}]) == "d = 0.77"
    assert _render_test([{"name": "d", "comp": None, "value": None}]) == "d NA NA"


def test_extract_tests_demo(demo) -> None:
    tests = extract_tests(demo)
    assert list(tests.columns) == COLUMNS
    assert tests["reported"].tolist() == [
        "M = 9.12",
        "M = 10.9",
        "t(97.7) = 2.9, p = 0.005, d = 0.59",
        "M = 5.06",
        "M = 4.5",
        "t(97.2) = -1.96, p = 0.152",
    ]
    assert tests["anchor"].tolist() == ["m", "m", "t", "m", "m", "t"]
    assert tests["test_no"].tolist() == [1, 2, 3, 4, 5, 6]
    assert tests["components"].iloc[2][0] == {
        "name": "t",
        "comp": "=",
        "value": "2.9",
        "df": "(97.7)",
        "sentence_pos": 3,
    }


def test_extract_tests_test_paper() -> None:
    paper = pc.test_paper(
        [
            "Both effects held, t(23) = 3.77, p = .001, d = 0.77, t(23) = 2.69, p = .013.",
            "Figures were 4 inches (height = 4, width = 3).",
            "The interaction was significant, F(2, 560) = 41.86, p < .001, ηp² = .13.",
        ]
    )
    tests = extract_tests(paper)
    assert tests["text_id"].tolist() == [1, 1, 3]
    assert tests["sentence"].iloc[2] == paper.text["text"].iloc[2]
    assert tests["reported"].tolist()[-1] == "F(2, 560) = 41.86, p < .001, ηp² = .13"
    assert tests["n_components"].tolist() == [3, 2, 3]


def test_extract_tests_paper_list(psychsci) -> None:
    # U10: R gives every test to every paper id (348 = 3 x 116 rows) with NA sentences
    tests = extract_tests(psychsci)
    assert len(tests) == 116
    assert list(tests.columns) == COLUMNS
    for pid in psychsci.names:
        one = extract_tests(psychsci[pid])
        mine = tests.loc[tests["paper_id"] == pid].reset_index(drop=True)
        assert mine["reported"].tolist() == one["reported"].tolist()
        assert mine["test_no"].tolist() == list(range(1, len(one) + 1))
    assert tests["sentence"].notna().all()
    assert len(extract_tests(pc.PaperList([pc.test_paper(["No stats."])]))) == 0
    # a plain list or a dict of papers works like a PaperList
    pd.testing.assert_frame_equal(extract_tests(list(psychsci)), tests)
    pd.testing.assert_frame_equal(
        extract_tests(dict(zip(psychsci.names, psychsci, strict=True))), tests
    )


def test_extract_tests_empty() -> None:
    tests = extract_tests(pc.test_paper(["No statistics here.", "Only SD = 2."]))
    assert len(tests) == 0
    assert list(tests.columns) == COLUMNS
    pd.testing.assert_frame_equal(tests, _empty_tests())


def test_components_keep_every_key() -> None:
    """R's list(name =, comp =, value =, df =, sentence_pos =) keeps NULL elements."""
    p = pc.test_paper(["first sentence"])
    p.paper_id = "eqp"
    p["eq"] = pd.DataFrame(
        {
            "text_id": pd.Series([1, 1], dtype="Int64"),
            "lhs": ["t", "p"],
            "comp": pd.Series([None, "<"], dtype="string"),
            "rhs": ["1.5", ".03"],
        }
    )
    out = extract_tests(p)
    assert len(out) == 1
    comps = out["components"].iloc[0]
    assert [list(c) for c in comps] == [["name", "comp", "value", "df", "sentence_pos"]] * 2
    assert comps[0]["df"] is None and comps[0]["comp"] is None
    # an NA comparator prints "NA"; a missing df column prints nothing
    assert out["reported"].iloc[0] == "t NA 1.5, p < .03"
    assert out["sentence"].iloc[0] == "first sentence"


def test_render_test_null_elements() -> None:
    # absent comp is R's NULL (rendered "="); absent name/value make vapply() fail
    assert _render_test([{"name": "d", "value": "0.77"}]) == "d = 0.77"
    import pytest

    with pytest.raises(ValueError, match="length 0"):
        _render_test([{"comp": "=", "value": "2"}])
