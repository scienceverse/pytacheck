"""Tests for metacheck.statout.stato_map (port of R/stato-map.R).

metacheck has no dedicated testthat file for the STATO mapping; its behaviour
is exercised through test-stat-tables.R / test-stat-output.R (typed result
tables). These tests pin the four typing tiers, the call-aware
disambiguation and the SPSS row-label typing; exhaustive agreement with R is
in the statout_match parity cases.
"""

from __future__ import annotations

import pytest

from metacheck.statout import stato_map
from metacheck.statout.stato_map import (
    _MC_STAT_LABELS,
    _MC_STAT_MAP,
    _MC_STAT_NS,
    _STATO_BY_CALL,
    _STATO_LABELS,
    _STATO_MAP,
    _spv_stato_type_label,
    _stato_by_call,
    _stato_strip_variant,
    stato_type_column,
)

OBO = "http://purl.obolibrary.org/obo/"


def _stato(label: str, acc: str) -> dict[str, str]:
    return {"annotationValue": label, "termSource": "STATO", "termAccession": OBO + acc}


def _mc(term: str) -> dict[str, str]:
    return {
        "annotationValue": _MC_STAT_LABELS[term],
        "termSource": "metacheck",
        "termAccession": _MC_STAT_NS + term,
    }


def test_vocabulary_is_closed() -> None:
    # every mapped id has a label, so a lookup never falls back (R would error)
    assert set(_STATO_MAP.values()) <= set(_STATO_LABELS)
    assert set(_MC_STAT_MAP.values()) <= set(_MC_STAT_LABELS)
    for tbl in _STATO_BY_CALL.values():
        for hit in tbl.values():
            if hit.startswith("mcSTAT:"):
                assert hit.removeprefix("mcSTAT:") in _MC_STAT_LABELS
            else:
                assert hit in _STATO_LABELS
    assert all(k == k.lower().strip() for k in [*_STATO_MAP, *_MC_STAT_MAP])
    # sizes of metacheck's tables (.STATO_LABELS has one duplicated entry)
    assert (len(_STATO_LABELS), len(_MC_STAT_LABELS)) == (56, 38)
    assert (len(_MC_STAT_MAP), len(_STATO_MAP), len(_STATO_BY_CALL)) == (68, 271, 21)


def test_tier1_stato_class() -> None:
    assert stato_type_column("t") == _stato("t-statistic", "STATO_0000176")
    assert stato_type_column("  Std. Deviation ") == _stato(
        "standard deviation for sample", "STATO_0000684"
    )
    assert stato_type_column("χ²") == _stato("Chi-Squared statistic", "STATO_0000030")
    assert stato_type_column("BF₁₀")["termAccession"] == OBO + "STATO_0000266"


def test_tier2_metacheck_term() -> None:
    assert stato_type_column("Sum of Squares") == _mc("sumOfSquares")
    assert stato_type_column("tau") == _mc("kendallTau")
    assert stato_type_column("Error %") == _mc("bayesFactorError")


def test_tier3_variant_suffix_is_stripped() -> None:
    assert stato_type_column("F[gg]") == _stato("F-statistic", "STATO_0000282")
    assert stato_type_column("p[hf]") == _stato("p-value", "STATO_0000700")
    assert stato_type_column("ss[gg]") == _mc("sumOfSquares")
    # standing alone, gg/hf are the sphericity epsilons
    assert stato_type_column("gg") == _mc("greenhouseGeisserEpsilon")


def test_tier4_fallback_keeps_the_header() -> None:
    assert stato_type_column("  Condition ") == {
        "annotationValue": "Condition",
        "termSource": "",
        "termAccession": "",
    }
    # bare jamovi `stat` is deliberately untyped
    assert stato_type_column("stat")["termSource"] == ""


def test_jamovi_stat_brackets() -> None:
    assert stato_type_column("stat[stud]") == _stato("t-statistic", "STATO_0000176")
    assert stato_type_column("stat[welc]") == _stato("t-statistic", "STATO_0000176")
    assert stato_type_column("stat[mann]") == _mc("wilcoxonW")
    # any other bracket is a variant of bare `stat`: untyped
    assert stato_type_column("stat[bf]")["termSource"] == ""


def test_header_normalisations() -> None:
    mean = _stato("sample mean", "STATO_0000401")
    assert stato_type_column("mean in group control") == mean
    assert stato_type_column("mean of x") == mean
    assert stato_type_column("mean (y)") == mean  # r-capture "<fun> (<variable>)"
    assert stato_type_column("sample.size.1") == _stato(
        "study group population size", "STATO_0000088"
    )
    # a mapped key of the "<x> (<y>)" shape is not stripped
    assert stato_type_column("Prob (F-statistic)") == _stato("p-value", "STATO_0000700")
    # a key ending in digits without a dot is untouched
    assert stato_type_column("df2") == _stato("denominator degrees of freedom", "STATO_0000527")


def test_call_fn_disambiguates_bare_letters() -> None:
    assert stato_type_column("W", call_fn="shapiro.test") == _mc("shapiroWilkW")
    assert stato_type_column("W", call_fn="wilcox.test") == _mc("wilcoxonW")
    assert stato_type_column("V", call_fn="wilcox.test") == _mc("wilcoxonV")
    # the call is matched case-insensitively (a Python result class)
    assert stato_type_column("statistic", call_fn="TtestResult") == _stato(
        "t-statistic", "STATO_0000176"
    )
    # unknown call / no call: header-only typing, W stays untyped
    assert stato_type_column("W", call_fn="lm")["termSource"] == ""
    assert stato_type_column("W")["termSource"] == ""
    assert stato_type_column("statistic", call_fn="wilcox.test")["termSource"] == ""


def test_stato_by_call() -> None:
    assert _stato_by_call("w", "shapiro.test") == "mcSTAT:shapiroWilkW"
    assert _stato_by_call("statistic", "KSTESTRESULT") == "mcSTAT:kolmogorovSmirnovD"
    assert _stato_by_call("w", None) is None
    assert _stato_by_call("w", "") is None
    assert _stato_by_call("x", "t.test") is None


def test_null_and_na_headers() -> None:
    empty = {"annotationValue": "", "termSource": "", "termAccession": ""}
    assert stato_type_column(None) == empty
    assert stato_type_column("   ") == empty
    assert stato_type_column(float("nan")) == {
        "annotationValue": None,
        "termSource": "",
        "termAccession": "",
    }


def test_results_are_independent_copies() -> None:
    first = stato_type_column("p")
    first["termSource"] = "changed"
    assert stato_type_column("p")["termSource"] == "STATO"


def test_strip_variant_is_vectorised() -> None:
    assert _stato_strip_variant(["f[gg]", "a[b][c]", "noop"]) == ["f", "a[b]", "noop"]
    assert _stato_strip_variant("stat[stud]") == "stat"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Sig. (2-tailed)", _stato("p-value", "STATO_0000700")),
        ("Type III Sum of Squares", _mc("sumOfSquares")),
        ("Bootstrap / BCa 95% Confidence Interval / Lower", _stato("confidence interval", "STATO_0000196")),
        ("t-test for Equality... / Std. Error Diff...", _stato(
            "standard error of the difference between independent means", "STATO_0000648")),
        ("Change Statistics / F Change", _stato("F-statistic", "STATO_0000282")),
        ("-2 Log Likelihood Reduced", _stato("log likelihood", "STATO_0000550")),
        ("Error([%1:*NRHand, posture:]1)", {
            "annotationValue": "Error([%1:*NRHand, posture:]1)", "termSource": "", "termAccession": ""}),
        ("Something Else", {"annotationValue": "Something Else", "termSource": "", "termAccession": ""}),
        ("", {"annotationValue": "", "termSource": "", "termAccession": ""}),
    ],
)  # fmt: skip
def test_spv_row_labels(label: str, expected: dict[str, str]) -> None:
    assert _spv_stato_type_label(label) == expected


def test_notebook_statistic_is_typed_by_result_class(fixtures_dir) -> None:
    """test-stat-tables.R: a parsed TtestResult line carries call_fn, which lets
    stato_type_column() type its bare "statistic" column as a t-statistic."""
    from metacheck.statout.stat_output import stat_results_long
    from metacheck.statout.stat_tables import read_stat_tables

    tabs = read_stat_tables(str(fixtures_dir / "notebooks" / "notebook_python.ipynb"))
    long = stat_results_long(tabs, source_file="notebook_python.ipynb")
    row = long[long["statistic"] == "statistic"]
    assert len(row) == 1
    assert row["stato_iri"].iloc[0] == OBO + "STATO_0000176"


def test_r_capture_reads_the_vocabulary_keys() -> None:
    # r_capture.py ships the vocabulary keys to its child R process
    assert list(stato_map._STATO_MAP)[:3] == ["t", "f", "χ²"]
    assert "ss" in stato_map._MC_STAT_MAP
