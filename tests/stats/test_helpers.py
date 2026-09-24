"""R/stat_helpers.R (pytacheck.stats.helpers) and statcheck's R-condition semantics."""

from __future__ import annotations

import warnings

import pytest

from pytacheck._r.regex import regexec
from pytacheck.stats.helpers import (
    _r_stat_pattern,
    _stat_display_value,
    _stat_html_escape,
    _stato_strip_variant,
)
from pytacheck.stats.statcheck import (
    RError,
    StatcheckWarning,
    _statcheck_quiet,
    extract_pattern,
    statcheck,
)


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


# -- R conditions in statcheck -------------------------------------------------------


def test_direct_statcheck_warns_and_continues() -> None:
    with pytest.warns(StatcheckWarning, match="NAs introduced by coercion"):
        result = statcheck("t(28) = 2.20, p = .036 and p = .05-.10", messages=False)
    assert result["raw"].tolist() == ["t(28) = 2.20, p = .036"]


@pytest.mark.parametrize(
    ("txt", "message"),
    [
        ("Zero df t(0) = 2.1, p = .03.", "missing value where TRUE/FALSE needed"),
        ("Perfect r(20) = 1.00, p < .001.", "missing value where TRUE/FALSE needed"),
        ("Bracket t(20) = (2.1, p = .03.", "the condition has length > 1"),
        ("Twin the MZ = 2.1, p = .03 difference.", "argument is of length zero"),
    ],
)
def test_direct_statcheck_raises_r_errors(txt: str, message: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StatcheckWarning)
        with pytest.raises(RError, match=message):
            statcheck(txt, messages=False)
        # metacheck's stats() swallows the error: the sentence gives no rows
        sources, columns = _statcheck_quiet([txt, "t(28) = 2.20, p = .036"])
    assert sources == [1]
    assert columns["raw"].tolist() == ["t(28) = 2.20, p = .036"]


def test_quiet_mode_restores_warning_handler() -> None:
    _statcheck_quiet(["t(0) = 2.1, p = .03"])
    with pytest.warns(StatcheckWarning):
        statcheck("t(28) = 2.2, p = .03 and p = .05-.10", messages=False)


def test_extract_pattern_recycles_like_substring() -> None:
    assert extract_pattern("t(28) = 2.20, p = .03", "[<>=]") == ["=", "="]
    assert extract_pattern(["= 2.1, ", "< 3, "], "[<>=]") == ["=", "<"]
    assert extract_pattern("no match", "x") is None
    assert extract_pattern(None, "x") is None
    with pytest.raises(RError):
        extract_pattern([], "x")
