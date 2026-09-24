"""R numeric primitives used by statcheck (pytacheck.stats._rmath); values from R 4.5."""

from __future__ import annotations

import math

import pytest

from pytacheck.stats._rmath import as_numeric, pchisq, pf, pnorm, pt, r_round, sqrt


@pytest.mark.parametrize(
    ("x", "digits", "expected"),
    [
        # R's round() picks the closer decimal candidate in double arithmetic,
        # which is not what Python's correctly-rounded round() does
        (0.80105, 4, 0.801),
        (0.0205, 3, 0.02),
        (0.1495, 3, 0.15),
        (0.90055, 4, 0.9006),
        (0.715, 2, 0.72),
        (0.505, 2, 0.5),
        (0.285, 2, 0.28),
        (2.675, 2, 2.67),
        (0.125, 2, 0.12),
        (2.5, 0, 2.0),
        (-2.5, 0, -2.0),
        (-0.715, 2, -0.72),
        (123.456, -1, 120.0),
        (0.052859364, 5, 0.05286),
        (1e-20, 3, 0.0),
    ],
)
def test_r_round(x: float, digits: int, expected: float) -> None:
    assert r_round(x, digits) == expected


def test_r_round_special_values() -> None:
    assert math.isnan(r_round(math.nan, 2))
    assert math.isnan(r_round(0.5, math.nan))
    assert r_round(math.inf, 2) == math.inf
    assert r_round(0.123456789012345678, 20) == 0.123456789012345678


@pytest.mark.parametrize(
    ("s", "value", "warned"),
    [
        ("0.05E2", 5.0, False),
        (".05e-3", 5e-05, False),
        (" 5 ", 5.0, False),
        ("+.5", 0.5, False),
        ("5.", 5.0, False),
        ("0x1A", 26.0, False),
        ("Inf", math.inf, False),
        ("-inf", -math.inf, False),
        ("infinity", math.inf, False),
        ("", math.nan, False),
        ("nan", math.nan, False),
        ("1e", math.nan, True),
        ("1e-", math.nan, True),
        (".05-", math.nan, True),
        (".", math.nan, True),
        ("1,000", math.nan, True),
        ("NA", math.nan, True),
        ("1d5", math.nan, True),
        ("- 5", math.nan, True),
        (" 5", math.nan, True),
    ],
)
def test_as_numeric(s: str, value: float, warned: bool) -> None:
    got, w = as_numeric(s)
    assert w is warned
    if math.isnan(value):
        assert math.isnan(got)
    else:
        assert got == value


def test_distributions_match_r() -> None:
    # print(..., digits = 15) in R 4.5
    assert pt(-2.2, 28) * 2 == pytest.approx(0.0362254847788378, rel=1e-13)
    assert pf(2.2, 2, 28, lower_tail=False) == pytest.approx(0.129593224474093, rel=1e-13)
    assert pchisq(22.2, 28, lower_tail=False) == pytest.approx(0.771948704032936, rel=1e-13)
    assert pnorm(2.2, lower_tail=False) * 2 == pytest.approx(0.0278068950269972, rel=1e-13)


def test_distribution_boundaries() -> None:
    assert pf(-1, 1, 20, lower_tail=False) == 1.0
    assert pf(0, 1, 20) == 0.0
    assert pchisq(-1, 3, lower_tail=False) == 1.0
    assert pchisq(3.84, 0, lower_tail=False) == 0.0  # point mass at zero
    assert pt(-math.inf, 5) == 0.0
    assert pt(1.96, math.inf) == pytest.approx(pnorm(1.96))
    assert math.isnan(pt(math.nan, 5))


def test_invalid_parameters_warn() -> None:
    seen: list[str] = []
    assert math.isnan(pt(1, 0, warn=seen.append))
    assert math.isnan(pf(1, 0, 20, warn=seen.append))
    assert math.isnan(pchisq(1, -1, warn=seen.append))
    assert math.isnan(sqrt(-1, warn=seen.append))
    assert seen == ["NaNs produced"] * 4
    # no callback: silent
    assert math.isnan(pt(1, -1))
