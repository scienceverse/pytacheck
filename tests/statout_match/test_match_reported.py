"""Tests for pytacheck.statout.match_reported (port of R/match-reported.R).

Ports the match_reported_output() expectations of metacheck's
test-module-reproducibility_check.R (console output feeding the matcher; a
model described across several statements uniting into one site) on
recorded console output, plus unit tests of the internals. Exhaustive
agreement with R is in the statout_match parity cases.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.statout.match_reported import (
    _build_sites,
    _norm_df,
    _norm_interval,
    _norm_value,
    _recompose_eq,
    _regroup_by_evidence,
    _sites_share_variable,
    _stat_family,
    _tests_from_extract,
    match_reported_output,
)

DATA = Path(__file__).resolve().parent / "data"
COLUMNS = [
    "text_id", "grp_id", "reported", "n_components", "n_matched", "found", "match_values",
    "not_matched", "source_file", "analysis", "confidence", "plausible_split",
]  # fmt: skip


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _rout_long(name: str) -> pd.DataFrame:
    from pytacheck.statout.r_output import read_r_output
    from pytacheck.statout.stat_output import stat_results_long

    tabs = read_r_output(
        _lines(DATA / f"{name}.Rout"),
        source_label=f"{name}.R",
        code_lines=_lines(DATA / f"{name}.R"),
    )
    return stat_results_long(tabs, source_file=f"{name}.R")


def _long(rows: list[tuple[str, str, str]], **extra: list[str]) -> pd.DataFrame:
    """A minimal stat_results_long()-shaped table from (test_id, statistic, value)."""
    df = pd.DataFrame(
        {
            "source_file": ["out.R"] * len(rows),
            "test_id": [r[0] for r in rows],
            "analysis": ["analysis " + r[0] for r in rows],
            "statistic": [r[1] for r in rows],
            "value": [r[2] for r in rows],
        },
        dtype="string",
    )
    for k, v in extra.items():
        df[k] = pd.Series(v, dtype="string")
    return df


# ---------------------------------------------------------------- testthat ports


def test_console_output_feeds_match_reported_output() -> None:
    """execute = TRUE: console output feeds stat_output and match_reported_output."""
    paper = pc.test_paper("t(4.96) = -4.04, p = .010.")
    long = _rout_long("ok")
    assert len(long) > 0
    res = match_reported_output(paper, [{"file": "ok.R", "long": long}])
    assert len(res) > 0
    assert res["found"].any()
    assert res["match_values"].iloc[0] == "t=-4.04, df=4.96, p=0.01"


def test_model_described_across_statements_unites_into_one_site() -> None:
    """A model fitted once, then described by anova(m) and s$coefficients[2, ]."""
    long = _rout_long("model_object")
    refs = long["model_ref"].dropna().unique()
    assert len(refs) == 1
    assert (long["statistic"].str.contains("F", regex=False)).any()
    paper = pc.test_paper("The model F(1, 5) = 15.00, p = .012, b = 3.50, SE = 0.90, t = 3.87.")
    res = match_reported_output(paper, long)
    assert res["confidence"].tolist() == ["full", "full"]


# ---------------------------------------------------------------- value parsing


@pytest.mark.parametrize(
    ("x", "num", "dec", "cens"),
    [
        (".06", 0.06, 2, ""),
        ("< .001", 0.001, 3, "<"),
        ("> .05", 0.05, 2, ">"),
        ("1,234.5", 1234.5, 1, ""),
        ("-.5", -0.5, 1, ""),
        ("1.5e-05", 1.5e-05, 1, ""),
        ("3.14abc", 3.14, 2, ""),
        ("abc", None, 0, ""),
        ("", None, 0, ""),
        (None, None, 0, ""),
        (pd.NA, None, 0, ""),
        (0.681, 0.681, 3, ""),
        ("1e", None, 0, ""),
    ],
)
def test_norm_value(x: object, num: float | None, dec: int, cens: str) -> None:
    assert _norm_value(x) == {"num": num, "dec": dec, "censored": cens}


def test_norm_interval() -> None:
    lo_hi = _norm_interval("[.16, .29]")
    assert lo_hi is not None
    assert (lo_hi["lo"]["num"], lo_hi["hi"]["num"]) == (0.16, 0.29)
    for s in ["[.16; .29]", "[.16-.29]", "[.16–.29]", " [ .16 , .29 ] "]:
        got = _norm_interval(s)
        assert got is not None and got["hi"]["num"] == 0.29, s
    neg = _norm_interval("[-0.16, -.29]")
    assert neg is not None and neg["lo"]["num"] == -0.16
    for s in ["[1, 2, 3]", "[.16,]", "(.16, .29)", "[a, b]", "[-.16 - -.29]", None]:
        assert _norm_interval(s) is None, s


def test_norm_df() -> None:
    assert _norm_df("(28)") == {"df1": {"num": 28.0, "dec": 0, "censored": ""}}
    two = _norm_df("(2, 2159)")
    assert two is not None and two["df2"]["num"] == 2159.0
    # R's strsplit() drops a trailing empty piece: "(2,)" is one df
    assert _norm_df("(2,)") == {"df1": {"num": 2.0, "dec": 0, "censored": ""}}
    for s in ["28", "(1, 2, 3)", "()", "(a)", None, pd.NA, ""]:
        assert _norm_df(s) is None, s


def test_stat_family() -> None:
    names = ["Pr(>F)", "p[stud]", "stat[stud]", "stat[mann]", "beta", "meta", "η²", "etaSq",
             "Estimate", "ci.lb", "cihig", "95% CI", "df1", "Condition", None]  # fmt: skip
    assert _stat_family(names) == [
        "p", "p", "t", "W", "beta", None, "eta2", "eta2", "b", "ci_lower", "ci_upper", "ci",
        "df1", None, None,
    ]  # fmt: skip
    assert _stat_family("Cohen's d") == "d"
    assert _stat_family(None) is None


def test_sites_share_variable() -> None:
    assert _sites_share_variable("compassion_mindset", "compassion_mindset_1") is True
    assert _sites_share_variable("grit", "happiness") is False
    # short tokens never link two sites
    assert _sites_share_variable("a_b", "a_b") is None
    assert _sites_share_variable(None, "x") is None
    assert _sites_share_variable("", "x") is None


# ---------------------------------------------------------------- recomposition


def test_recompose_eq_groups_and_filters() -> None:
    eq = pd.DataFrame(
        {
            "text_id": pd.Series([2, 2, 2, 10], dtype="Int64"),
            "grp_id": [1.0, 1.0, 1.0, 1.0],
            "lhs": ["t", "p", "Condition", "95% CI"],
            "df": ["(28)", None, None, None],
            "comp": ["=", "<", "=", "="],
            "rhs": ["2.20", ".05", "3", "[.16, .29]"],
        }
    )
    tests = _recompose_eq(eq)
    # split() orders groups by the "<text_id>|<grp_id>" key as text: "10|1" < "2|1"
    assert [t["text_id"] for t in tests] == [10, 2]
    assert [c["family"] for c in tests[0]["components"]] == ["ci_lower", "ci_upper"]
    assert [(c["family"], c["value"], c["censored"]) for c in tests[1]["components"]] == [
        ("t", 2.2, ""),
        ("df", 28.0, ""),
        ("p", 0.05, "<"),
    ]
    assert _recompose_eq(None) == []
    with pytest.raises(ValueError, match="missing value"):
        _recompose_eq(pc.PaperList([pc.test_paper("x")]))


def test_tests_from_extract_tags_anchors() -> None:
    from pytacheck.text.extract_tests import extract_tests

    tt = extract_tests(pc.test_paper("It was t(28) = 2.20, p < .05, d = 0.59."))
    tests = _tests_from_extract(tt)
    comps = tests[0]["components"]
    assert [(c["name"], c["is_anchor"], c["pos"]) for c in comps] == [
        ("t", True, 1),
        ("df", False, 1),
        ("p", False, 2),
        ("d", True, 3),
    ]
    assert _tests_from_extract(None) == []


# ---------------------------------------------------------------- matching


TTEST = [
    ("tt1", "t", "6.89966"),
    ("tt1", "df", "8"),
    ("tt1", "p", "0.000124"),
    ("tt1", "d", "2.2999"),
    ("tt2", "t", "5.0471"),
    ("tt2", "df", "8"),
    ("tt2", "p", "0.00099"),
    ("desc", "mean", "1.93"),
    ("desc", "sd", "0.86"),
]


def test_whole_test_signature_is_matched() -> None:
    paper = pc.test_paper(
        [
            "The effect was t(8) = 6.90, p < .001, d = 2.30.",
            "A fabricated result, t(42) = 9.99, p = .001.",
            "Partly right: t(8) = 5.05, p = .20.",
        ]
    )
    res = match_reported_output(paper, _long(TTEST))
    assert list(res.columns) == COLUMNS
    assert res["reported"].tolist() == [
        "t=6.9 df=8 p=<0.001 d=2.3",
        "t=9.99 df=42 p=0.001",
        "t=5.05 df=8 p=0.2",
    ]
    assert res["confidence"].tolist() == ["full", "none", "partial"]
    # a lone coincidental p (< .001 censored bound) is never "found"
    assert res["n_matched"].tolist() == [4, 1, 2]
    assert res["not_matched"].iloc[2] == "p=0.2"
    assert res["analysis"].iloc[0] == "analysis tt1"
    assert res.attrs["summary"] == {
        "n_tests": 3, "n_found": 2, "n_full": 1, "n_partial": 1, "n_missing": 1,
        "pct_found": 66.7,
    }  # fmt: skip


def test_precision_aware_and_min_components() -> None:
    paper = pc.test_paper("The effect size was d = .68.")
    long = _long([("a", "d", "0.6810"), ("a", "p", "0.2")])
    res = match_reported_output(paper, long)
    assert res["found"].tolist() == [True]
    res2 = match_reported_output(paper, long, min_components=2)
    assert res2["found"].tolist() == [False]
    assert res2["n_matched"].tolist() == [0]


def test_beta_and_df_fallbacks_stay_on_one_site() -> None:
    paper = pc.test_paper(
        ["The slope was beta = 0.35, t = 7.00.", "The ANOVA gave F(2, 57) = 4.21, p = .020."]
    )
    long = _long(
        [
            ("reg", "Estimate", "0.35"),
            ("reg", "t value", "7.0"),
            ("an_a1_g", "F", "4.21"),
            ("an_a1_g", "df", "2"),
            ("an_a1_g", "p", "0.0201"),
            ("an_a1_residuals", "df", "57"),
        ]
    )
    res = match_reported_output(paper, long)
    assert res["confidence"].tolist() == ["full", "full"]
    assert res["match_values"].iloc[1] == "F=4.21, df1=2, df2=57, p=0.02"


def test_empty_inputs_give_the_empty_frame() -> None:
    paper = pc.test_paper("t(8) = 6.90, p < .001.")
    for out in (None, [], pd.DataFrame()):
        res = match_reported_output(paper, out)
        assert list(res.columns) == COLUMNS
        assert len(res) == 0
        assert res.attrs["summary"]["n_tests"] == 1
        assert res.attrs["summary"]["n_missing"] == 1
    none = match_reported_output(pc.test_paper("No statistics."), _long(TTEST))
    assert len(none) == 0 and none.attrs["summary"]["n_tests"] == 0
    assert str(none["found"].dtype) == "boolean"


def test_inputs_are_not_mutated() -> None:
    paper = pc.test_paper("The effect was t(8) = 6.90, p < .001, d = 2.30.")
    long = _long(TTEST)
    before_long = long.copy()
    before_text = paper.text.copy()
    match_reported_output(paper, long)
    pd.testing.assert_frame_equal(long, before_long)
    pd.testing.assert_frame_equal(paper.text, before_text)


def test_regroup_with_text_proximity() -> None:
    """text_proximity (unused by match_reported_output) limits what an anchor claims."""
    comps = [
        {"family": "t", "name": "t", "value": 6.9, "dec": 1, "censored": "", "is_anchor": True, "pos": 1},
        {"family": "p", "name": "p", "value": 0.001, "dec": 3, "censored": "<", "is_anchor": False, "pos": 9},
    ]  # fmt: skip
    tests = [{"text_id": 1, "grp_id": 1, "sentence": "s", "components": comps}]
    sites = _build_sites(_long(TTEST))
    together = _regroup_by_evidence(tests, sites)
    assert [len(t["components"]) for t in together] == [2]
    apart = _regroup_by_evidence(tests, _build_sites(_long(TTEST)), text_proximity=3)
    assert [len(t["components"]) for t in apart] == [1, 1]
    assert [t["plausible_split"] for t in apart] == [False, False]
    # tests without anchor tags are returned untouched
    legacy = [{"text_id": 1, "grp_id": 1, "components": [dict(comps[0], is_anchor=None)]}]
    assert _regroup_by_evidence(legacy, sites) == legacy


def test_many_sites_is_fast() -> None:
    rows = []
    for i in range(3000):
        rows += [(f"s{i}", "t", f"{i / 7:.4f}"), (f"s{i}", "p", f"{(i % 97) / 1000:.4f}"),
                 (f"s{i}", "df", str(i % 50 + 5)), (f"s{i}", "d", f"{i / 1000:.3f}")]  # fmt: skip
    long = _long(rows)
    texts = [f"Test {i}: t({i % 50 + 5}) = {i / 7:.2f}, p = {(i % 97) / 1000:.3f}." for i in range(300)]
    start = time.perf_counter()
    res = match_reported_output(pc.test_paper(texts), long)
    elapsed = time.perf_counter() - start
    assert len(res) == 300
    assert res["found"].mean() > 0.9
    assert elapsed < 20
