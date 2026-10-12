"""R/stat_helpers.R (metacheck.stats.helpers) and results statcheck cannot check (D78)."""

from __future__ import annotations

import pytest

from metacheck._r.regex import regexec
from metacheck.stats.helpers import (
    _r_stat_pattern,
    _stat_display_value,
    _stat_html_escape,
    _stato_strip_variant,
)
from metacheck.stats.statcheck import _statcheck_quiet, extract_pattern, statcheck


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (None, ""),
        (float("nan"), ""),
        (0.840583589880873, "0.841"),
        ("0.840583589880873", "0.841"),
        (1e-05, "1e-05"),  # would round to 0.000: kept as written
        (4.7e-108, "4.7e-108"),
        (100000.0, "100000"),
        (397.0, "397"),
        ("397", "397"),
        (42, "42"),
        (True, "TRUE"),
        ("1.2.3", "1.2.3"),
        (" 5", " 5"),
        ("Inf", "Inf"),
        ("-0.0004", "-0.0004"),
        ("5.", "5"),
        (".841", "0.841"),
        (-2.5, "-2.500"),
        (12345678.9, "12345678.900"),
    ],
)
def test_stat_display_value(x: object, expected: str) -> None:
    assert _stat_display_value(x) == expected


def test_stat_html_escape() -> None:
    assert _stat_html_escape(["a<b & c>d", "plain", "&amp;", None]) == [
        "a&lt;b &amp; c&gt;d",
        "plain",
        "&amp;amp;",
        None,
    ]
    assert _stat_html_escape(None) == ""
    assert _stat_html_escape(0.5) == "0.5"


def test_stato_strip_variant() -> None:
    keys = ["f[gg]", "p[hf]", "df[none]", "stat[stud]", "stat", "a[b]c", "x[]", "[y]"]
    assert _stato_strip_variant(keys) == ["f", "p", "df", "stat", "stat", "a[b]c", "x", ""]


def test_r_stat_pattern() -> None:
    pat = _r_stat_pattern()
    assert pat["op"] == "=<>~\u2248\u2260\u2264\u2265\u226a\u226b"
    m = regexec(pat["pattern"], "a result, \u03b7\u00b2p(1, 20) \u2264 0.05 and more")
    assert m == ["\u03b7\u00b2p(1, 20) \u2264 0.05", "\u03b7\u00b2p", "(1, 20)", "\u2264", "0.05"]
    m = regexec(pat["pattern"], "t(28) = -2.20, p < .001")
    assert m == ["t(28) = -2.20", "t", "(28)", "=", "-2.20"]


# -- results statcheck cannot check (U5, D78) ------------------------------------------


def test_direct_statcheck_keeps_valid_results_without_warnings(recwarn) -> None:
    result = statcheck("t(28) = 2.20, p = .036 and p = .05-.10", messages=False)
    assert result["raw"].tolist() == ["t(28) = 2.20, p = .036"]
    assert not recwarn.list  # R's "NAs introduced by coercion" is not reproduced


@pytest.mark.parametrize(
    ("txt", "raw", "error"),
    [
        # R: "missing value where TRUE/FALSE needed" (pt() with df = 0 is NaN)
        ("Zero df t(0) = 2.1, p = .03.", None, None),
        # R: the same (r = 1.005 is checked); the interval is capped at 1
        ("Perfect r(20) = 1.00, p < .001.", "r(20) = 1.00, p < .001", False),
        ("Perfect r(20) = 1.00, p = .001.", "r(20) = 1.00, p = .001", True),
        ("Perfect r(20) = -1.00, p < .001.", "r(20) = -1.00, p < .001", False),
        # R: "the condition has length > 1" (two test-name candidates)
        ("Bracket t(20) = (2.1, p = .03.", "t(20) = (2.1, p = .03", True),
        # R: "argument is of length zero" (no test name: not a Z test)
        ("Twin the MZ = 2.1, p = .03 difference.", None, None),
        # R: the unparseable p-value fails on if (NA)
        ("Range F(1, 20) = 3.1, p = .05-.10.", None, None),
    ],
)
def test_statcheck_skips_results_it_cannot_check(txt: str, raw: str | None, error) -> None:
    # U5: R stops the whole call; pytacheck checks what it can and keeps the rest
    fine = "t(28) = 2.20, p = .036"
    result = statcheck([txt, fine], messages=False)
    sources, columns = _statcheck_quiet([txt, fine])
    expected = ([raw] if raw else []) + [fine]
    assert result["raw"].tolist() == expected
    assert columns["raw"].tolist() == expected
    assert sources == ([0] if raw else []) + [1]
    if raw:
        assert result["error"].tolist()[0] is error


@pytest.mark.parametrize(
    "txt",
    [
        # R reports these as consistent (computed_p NaN, error FALSE): a false green
        "t(0) < 2.1, p > .05",
        "F(0, 20) > 2.1, p < .05",
        "r(0) < .30, p > .05",
        # p = 0.000 is an error, but its p-value cannot be computed either
        "t(0) = 2.20, p = 0.000",
    ],
)
def test_no_computable_p_value_is_never_green(txt: str, capsys) -> None:
    # D78: a result that cannot be checked is dropped, never reported as consistent
    assert statcheck(txt, messages=False) is None
    assert "did not find any results" in capsys.readouterr().out
    sources, columns = _statcheck_quiet([txt])
    assert sources == []
    assert columns["computed_p"].isna().sum() == 0


def test_statcheck_results_are_always_decided() -> None:
    texts = [
        "t(0) < 2.1, p > .05 but t(28) = 2.20, p = .03",
        "F(2, 20) = -4.2, p = .03",  # below the support: p = 1, as in R
        "χ2(2) = -3.1, p = .05",
        "F(1, 20) = 3.1, p = .05-.10 and z = 1.96, p = .05",
    ]
    result = statcheck(texts, messages=False)
    assert result["raw"].tolist() == [
        "t(28) = 2.20, p = .03",
        "F(2, 20) = -4.2, p = .03",
        "χ2(2) = -3.1, p = .05",
        "z = 1.96, p = .05",
    ]
    for col in ("computed_p", "error", "decision_error"):
        assert not result[col].isna().any()
    assert result["computed_p"].tolist()[1:3] == [1.0, 1.0]


def test_extract_pattern_recycles_like_substring() -> None:
    assert extract_pattern("t(28) = 2.20, p = .03", "[<>=]") == ["=", "="]
    assert extract_pattern(["= 2.1, ", "< 3, "], "[<>=]") == ["=", "<"]
    assert extract_pattern("no match", "x") is None
    assert extract_pattern(None, "x") is None
    with pytest.raises(ValueError):
        extract_pattern([], "x")
