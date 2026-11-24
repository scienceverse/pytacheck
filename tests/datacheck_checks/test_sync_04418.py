"""Personal-data checks synchronised with metacheck 0.4.4.18 (R/data_check_helpers.R at 8b1d22a).

The expected values follow the R test "a column name that suggests personal data is confirmed
by its values when it can be (#648, K6)" in ``test-data-checks.R``.
"""

from __future__ import annotations

from typing import Any

import pytest

from metacheck.datacheck._checks_pii import (
    _pii_ip_ok,
    data_check_pii_name,
    data_check_pii_values,
)


def _flag(name: str, x: Any) -> bool:
    return bool(data_check_pii_name(name, x)["problem"])


@pytest.mark.parametrize(
    ("name", "x", "flagged"),
    [
        ("phone", ["iPhone", "Android"], False),
        ("phone", ["+31 6 12345678", "06-87654321"], True),
        ("hosp_ward_IP", [3, 5, 0], False),
        ("ip_address", ["192.168.0.1", "10.0.0.2"], True),
        ("SiPhy_29way_logOdds", [1.2, 3.4], False),
        ("HLA-DOB", ["DOB*01:01", "DOB*01:02"], False),
        ("dob", ["1985-03-12", "1990-11-02"], True),
        ("birthyear", [1985, 1990], True),
        ("HLA-DOB", [1985.34, 2001.7, 12.5], False),
        ("GEO_LOCATION_VILLAGE", ["Kibera", "Mathare"], False),
        ("first_name", ["Anna", "Ben"], True),
    ],
)
def test_a_name_is_confirmed_by_its_values(name: str, x: list[Any], flagged: bool) -> None:
    assert _flag(name, x) is flagged


@pytest.mark.parametrize(
    "v", ["15-Mar-1985", "March 15, 1985", "19850315", "15 Mar 85", "12.03.1985"]
)
def test_a_date_of_birth_counts_however_it_is_written(v: str) -> None:
    assert _flag("dob", [v])


def test_without_values_the_name_alone_decides() -> None:
    assert data_check_pii_name("phone")["problem"]


def test_a_time_or_a_browser_version_is_not_an_ip_address() -> None:
    assert not data_check_pii_values(["06.04.2026 15.34.46.182"] * 5)["problem"]
    assert not data_check_pii_values(["Mozilla/5.0 AppleWebKit/537.36 Chrome/84.0.4147.89"] * 3)[
        "problem"
    ]
    assert data_check_pii_values(["192.168.0.1", "10.0.0.2", "10.0.0.3"])["problem"]


def test_pii_ip_ok_part_of_a_longer_run_is_no_ip() -> None:
    assert not _pii_ip_ok("1.2.3.4.5")
    assert not _pii_ip_ok("Chrome 131.0.0.0")
    assert _pii_ip_ok("from 10.0.0.2 today")
    # one real address among other text is enough
    assert _pii_ip_ok("Chrome/84.0.4147.89 via 10.1.2.3")
