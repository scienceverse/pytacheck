"""The personal-data detectors: one test group per detector, positive and negative cases."""

from __future__ import annotations

import random

import pytest

from metacheck.datapackage._pii_detect import (
    DETECTORS,
    bsn_ok,
    column_context,
    column_tokens,
    is_bsn,
    is_ipv6,
    is_person_name,
    is_phone,
    is_postcode,
    is_student_number,
    judge_column,
    name_column_kind,
)


def _bsn(seed: int) -> str:
    """A 9-digit number that passes the 11-check (made, not looked up)."""
    rng = random.Random(seed)
    while True:
        digits = "".join(str(rng.randrange(10)) for _ in range(9))
        if bsn_ok(digits):
            return digits


def _not_bsn(seed: int) -> str:
    rng = random.Random(seed)
    while True:
        digits = "".join(str(rng.randrange(10)) for _ in range(9))
        if not bsn_ok(digits):
            return digits


def _flagged(name: str, values: list[str]) -> set[str]:
    return {d.id for d, *_ in judge_column(name, values)}


# -- column names ---------------------------------------------------------------------


def test_column_tokens_split_camel_case_and_separators() -> None:
    assert column_tokens("StudentNr") == ["student", "nr"]
    assert column_tokens("student_nr") == ["student", "nr"]
    assert column_tokens("S-number") == ["s", "number"]
    assert column_tokens("firstName") == ["first", "name"]
    assert column_tokens("") == []


# -- BSN ------------------------------------------------------------------------------


def test_bsn_elfproef_valid_and_invalid() -> None:
    assert bsn_ok("111222333")
    assert bsn_ok("123456782")
    assert not bsn_ok("123456789")
    assert not bsn_ok("111222334")
    assert not bsn_ok("000000000")  # the sum is 0, but this is not a number
    assert not bsn_ok("12345678")  # not 9 digits
    assert not bsn_ok("1234567890")
    assert not bsn_ok("12345678a")


def test_is_bsn_whole_cell_forms() -> None:
    assert is_bsn("111222333")
    assert is_bsn(" 111222333 ")
    assert is_bsn("111.222.333")
    assert is_bsn("111 222 333")
    assert is_bsn("111-222-333")
    assert not is_bsn("111.222 333")  # one kind of separator
    assert not is_bsn("1112223334")  # a longer number does not contain a BSN
    assert not is_bsn("x111222333")
    assert not is_bsn("111222334")


def test_bsn_with_eight_digits_only_in_a_named_column() -> None:
    # a spreadsheet that stored 012345672 as a number wrote 12345672
    assert not is_bsn("12345672")
    assert is_bsn("12345672", context=True)
    assert not is_bsn("12345673", context=True)


def test_a_column_of_bsn_is_flagged() -> None:
    values = [_bsn(i) for i in range(10)]
    assert _flagged("id_number", values) == {"bsn"}
    assert _flagged("BSN", values[:2]) == {"bsn"}  # the name needs fewer cells


def test_a_column_of_ordinary_nine_digit_ids_is_not_flagged() -> None:
    # about one in eleven random numbers passes the 11-check, so a few cells do
    rng = random.Random(7)
    ids = [str(rng.randrange(10**8, 10**9)) for _ in range(200)]
    assert sum(bsn_ok(v) for v in ids) > 0
    assert "bsn" not in _flagged("participant_id", ids)
    # a few passing numbers among many that do not are not enough
    mixed = [_bsn(1), _bsn(2), _bsn(3)] + [_not_bsn(i) for i in range(20)]
    assert "bsn" not in _flagged("code", mixed)


def test_bsn_needs_three_cells_without_a_name_and_ignores_empty_cells() -> None:
    assert "bsn" not in _flagged("x", [_bsn(1), _bsn(2)])
    assert _flagged("x", [_bsn(1), _bsn(2), _bsn(3), "", "NA", ""]) == {"bsn"}


# -- student numbers ------------------------------------------------------------------


def test_student_number_values() -> None:
    assert is_student_number("1234567")
    assert is_student_number("s1234567")
    assert is_student_number("S123456")
    assert not is_student_number("123456")
    assert not is_student_number("12345678")
    assert not is_student_number("12345a7")


@pytest.mark.parametrize(
    "name",
    [
        "student",
        "studentnummer",
        "student_id",
        "StudentNr",
        "s-number",
        "s_nummer",
        "snumber",
        "stud_nr",
    ],
)
def test_student_columns_by_name(name: str) -> None:
    assert "student-number" in column_context(name)
    assert _flagged(name, ["1234567", "2345678", "3456789"]) == {"student-number"}


@pytest.mark.parametrize("name", ["participant_id", "id", "subject", "age", "snacks", "number"])
def test_a_bare_seven_digit_column_is_not_flagged(name: str) -> None:
    assert "student-number" not in column_context(name)
    assert "student-number" not in _flagged(name, ["1234567", "2345678", "3456789", "4567890"])


def test_a_student_column_without_seven_digit_values_is_not_flagged() -> None:
    assert _flagged("student_id", ["1", "2", "3", "4"]) == set()
    assert _flagged("student_id", ["S001", "S002", "S003"]) == set()
    assert _flagged("student_age", ["19", "20", "21"]) == set()


# -- phone numbers --------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "0612345678",
        "06-12345678",
        "06 12345678",
        "06 1234 5678",
        "040-2474747",
        "040 247 4747",
        "(020) 123 4567",
        "020-1234567",
        "+31612345678",
        "+31 6 12345678",
        "+31 (0)6 12345678",
        "+31 6-1234 5678",
        "0031612345678",
        "0031 6 12345678",
        "+44 20 7946 0958",
        "0044 20 7946 0958",
        "+1 (202) 555-0123",
        "+49 30 901820",
    ],
)
def test_phone_numbers(number: str) -> None:
    assert is_phone(number)


@pytest.mark.parametrize(
    "value",
    [
        "1700000000",  # a Unix timestamp
        "20190101123045",  # a time stamp, 14 digits
        "8712345678901",  # an EAN
        "123456789",
        "1234567890",
        "0123456789",  # starts with 0 but no separators and not 06: an ID
        "01-02-2019",
        "2019-01-02",
        "0.123456789",
        "-0123456789",
        "0800-1234567",  # a service number
        "0900 123 4567",
        "+1234",  # too short
        "+12345678",  # 8 digits
        "+0612345678",
        "00123456789",  # 00 and no separator, not Dutch
        "12.5.1999.77",
        "call 06-12345678",
        "06-12345678 ext",
        "",
    ],
)
def test_not_phone_numbers(value: str) -> None:
    assert not is_phone(value)


def test_a_run_of_digits_is_a_phone_number_only_in_a_phone_column() -> None:
    assert not is_phone("0123456789")
    assert is_phone("0123456789", context=True)
    assert not is_phone("1234567890", context=True)
    assert _flagged("telefoon", ["0123456789", "0234567890"]) == {"phone"}
    assert _flagged("ref", ["0123456789", "0234567890", "0345678901"]) == set()


def test_phone_column_thresholds() -> None:
    numbers = ["06-12345678", "+31 6 87654321", "040-2474747"]
    assert _flagged("contact", numbers) == {"phone"}
    assert _flagged("contact", numbers[:1] + ["x"] * 5) == set()  # one cell of six
    assert _flagged("mobiel", [*numbers[:1], "x", "y"]) == {"phone"}  # a named column needs less


def test_numeric_ids_and_timestamps_in_a_column_are_not_phone_numbers() -> None:
    assert _flagged("timestamp", ["1700000000", "1700000100", "1700000200"]) == set()
    assert _flagged("ean", ["8712345678901", "8712345678902"]) == set()
    assert _flagged("id", ["20190101123045", "20190101123046"]) == set()


# -- IPv6 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "2001:0db8:0000:0000:0000:ff00:0042:8329",
        "2001:db8:0:0:0:ff00:42:8329",
        "2001:db8::ff00:42:8329",  # compressed
        "2001:db8::1",
        "fe80::1",
        "fe80::1ff:fe23:4567:890a%eth0",
        "[2001:db8::1]",
        "2001:db8::/32",
        "::ffff:192.0.2.1",
        "2a02:a448:1234::5",
    ],
)
def test_ipv6_addresses(address: str) -> None:
    assert is_ipv6(address)


@pytest.mark.parametrize(
    "value",
    [
        "12:30:45",  # a time
        "12:30",
        "1:2:3",
        "aa:bb:cc:dd:ee:ff",  # a MAC address
        "00:1A:2B:3C:4D:5E",
        "::1",  # loopback
        "::",  # unspecified
        "192.168.0.1",  # IPv4: data_check has it
        "2001:db8::g",
        "2001:db8:::1",
        "10:20:30:40:50:60:70:80:90",
        "",
        "ratio 1:2:3",
    ],
)
def test_not_ipv6_addresses(value: str) -> None:
    assert not is_ipv6(value)


def test_ipv6_column_versus_times() -> None:
    assert _flagged("ip", ["2001:db8::1", "fe80::1", "x"]) == {"ipv6"}
    assert _flagged("time", ["12:30:45", "08:15:00", "23:59:59"]) == set()
    assert _flagged("mac", ["aa:bb:cc:dd:ee:ff", "00:1a:2b:3c:4d:5e"]) == set()


# -- postcodes ------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1234 AB", "5611 ZK", "9999 XX", "1012 AB", "1234 AB"])
def test_postcodes(value: str) -> None:
    assert is_postcode(value)


@pytest.mark.parametrize(
    "value",
    [
        "0123 AB",  # does not start with 0
        "1234 SA",  # SA, SD and SS do not occur
        "1234 SD",
        "1234 SS",
        "1234AB",  # no space: only in a postcode column
        "1234 ab",  # lower case: only in a postcode column
        "2500 ms",
        "1000 MB",
        "500 BC",  # three digits
        "2019 AD",
        "12345 AB",  # five digits
        "1234 ABC",
        "A1234 AB",
        "Straat 1, 1234 AB",  # inside a longer text
        "",
    ],
)
def test_not_postcodes_without_a_postcode_column(value: str) -> None:
    assert not is_postcode(value)


def test_postcode_forms_that_need_a_postcode_column() -> None:
    assert is_postcode("1234AB", context=True)
    assert is_postcode("1234 ab", context=True)
    assert not is_postcode("1234 SS", context=True)
    assert not is_postcode("0123 AB", context=True)
    assert _flagged("postcode", ["1234AB", "5611zk"]) == {"postcode"}
    assert _flagged("code", ["1234AB", "5611zk", "1234 SS"]) == set()


def test_postcode_column_thresholds() -> None:
    assert _flagged("home", ["1234 AB", "5611 ZK", "x"]) == {"postcode"}
    assert _flagged("home", ["1234 AB", "x", "y", "z"]) == set()
    assert _flagged("size", ["1000 MB", "2000 MB", "3000 GB"]) == set()


# -- person names ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Jan de Vries",
        "Anna Smit",
        "J.H. Smit",
        "Zoë Müller-Smit",
        "Piet van der Berg",
        "Ahmed Al-Sayed",
        "Sinead O'Brien",
        "Maria",
        "JAN DE VRIES",
    ],
)
def test_names(value: str) -> None:
    assert is_person_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "age",  # lower case
        "reaction_time",
        "Age",  # a variable word
        "Control group",
        "Condition",
        "participant_id",
        "results.csv",
        "Table 1",
        "S1",
        "x",
        "J",
        "A B C D E F",  # more than 5 words
        "Jan@example.org",
        "RT",
        "MEAN",
        "12345",
        "Jan/Piet",
        "",
    ],
)
def test_not_names(value: str) -> None:
    assert not is_person_name(value)


def test_full_names_are_needed_for_a_generic_name_column() -> None:
    assert is_person_name("Maria")
    assert not is_person_name("Maria", minimum_words=2)
    assert not is_person_name("van de", minimum_words=1)
    assert is_person_name("Maria Jansen", minimum_words=2)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("voornaam", "specific"),
        ("achternaam", "specific"),
        ("first_name", "specific"),
        ("FirstName", "specific"),
        ("surname", "specific"),
        ("last name", "specific"),
        ("participant_name", "specific"),
        ("naam_deelnemer", "specific"),
        ("name", "generic"),
        ("Naam", "generic"),
        ("name_1", "generic"),
        ("file_name", None),
        ("filename", None),
        ("variable_name", None),
        ("condition_name", None),
        ("country", None),
        ("age", None),
        ("name of the first scale used", None),
        ("", None),
    ],
)
def test_name_column_kinds(name: str, kind: str | None) -> None:
    assert name_column_kind(name) == kind


def test_a_name_column_with_names_is_flagged() -> None:
    names = ["Jan de Vries", "Anna Smit", "Piet van der Berg", "Zoë Müller"]
    assert _flagged("name", names) == {"person-name"}
    assert _flagged("naam", names) == {"person-name"}
    assert _flagged("voornaam", ["Jan", "Anna", "Piet", "Zoë"]) == {"person-name"}
    assert _flagged("participant_name", ["Jan", "Anna", "Piet"]) == {"person-name"}


def test_a_name_column_of_variables_or_files_is_not_flagged() -> None:
    assert _flagged("name", ["age", "gender", "reaction_time", "score_total"]) == set()
    assert _flagged("name", ["data_01.csv", "data_02.csv", "analysis.R"]) == set()
    assert _flagged("name", ["Age", "Gender", "Education", "Income"]) == set()  # single words
    assert _flagged("name", ["Control group", "Treatment group", "Pilot sample"]) == set()
    assert _flagged("name", ["Total score", "Mean RT", "Item 3"]) == set()
    assert _flagged("file_name", ["Jan de Vries", "Anna Smit", "Piet Bos"]) == set()
    assert _flagged("condition", ["Jan de Vries", "Anna Smit", "Piet Bos"]) == set()


def test_name_needs_three_names_and_most_of_the_column() -> None:
    assert _flagged("naam", ["Jan Bos", "Anna Smit"]) == set()
    assert _flagged("naam", ["Jan Bos", "Anna Smit", "Piet Dijk", "x1", "x2", "x3", "x4"]) == set()


# -- the detectors table --------------------------------------------------------------


def test_detector_ids_and_items_are_unique() -> None:
    assert len({d.id for d in DETECTORS}) == len(DETECTORS) == 6
    assert len({d.item for d in DETECTORS}) == 6
    assert [d.id for d in DETECTORS if d.needs_name] == ["student-number", "person-name"]


def test_judge_column_reports_counts_not_values() -> None:
    out = judge_column("telefoon", ["06-12345678", "x", "+31 6 87654321", ""])
    assert len(out) == 1
    det, hits, checked, named = out[0]
    assert (det.id, hits, checked, named) == ("phone", 2, 3, True)
    assert judge_column("telefoon", ["", "NA"]) == []
