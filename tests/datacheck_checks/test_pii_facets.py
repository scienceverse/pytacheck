"""Port of the PII, demographic and facet tests in tests/testthat/test-data-checks.R."""

from __future__ import annotations

import pandas as pd

from pytacheck.datacheck.checks import (
    _concept_is_date,
    _concept_is_timestamp,
    _parse_frac,
    _pii_card_ok,
    _pii_luhn_ok,
    _pii_split_name,
    data_check_demographic,
    data_check_pii_freetext,
    data_check_pii_geo,
    data_check_pii_name,
    data_check_pii_values,
    data_col_concept,
    data_col_facets,
)

# -- data_check_pii_values ---------------------------------------------------------------


def test_pii_values_detects_patterns_without_false_positives() -> None:
    assert data_check_pii_values(["a@b.com", "c@d.org", "e@f.net"])["problem"]
    assert data_check_pii_values(["192.168.0.1", "10.0.0.255", "8.8.8.8"])["problem"]
    assert data_check_pii_values(["123-45-6789", "987-65-4321", "222-33-4444"])["problem"]
    assert data_check_pii_values(["4111111111111111", "5500005555555559", "4012888888881881"])[
        "problem"
    ]
    assert not data_check_pii_values(["1", "2", "3", "4", "5", "3", "2"])["problem"]
    assert not data_check_pii_values([str(10 + i * 20 / 39) for i in range(40)])["problem"]
    assert not data_check_pii_values(["yes", "no", "maybe", "yes", "no"])["problem"]
    # the report names the pattern, not the matching value
    r = data_check_pii_values(["a@b.com", "c@d.org", "e@f.net"])
    assert "@" not in r["message"]
    assert r["values"] == ["email"]
    assert r["message"] == (
        "Values look like personal information: email (3 values, 100%). Review before sharing."
    )


def test_pii_values_rare_but_real() -> None:
    assert data_check_pii_values(["n/a"] * 38 + ["a@b.com", "c@d.com"])["problem"]
    assert data_check_pii_values(["none"] * 39 + ["x@y.com"])["problem"]
    assert data_check_pii_values(["na"] * 197 + ["a@b.com", "c@d.org", "e@f.net"])["problem"]


def test_pii_values_validates() -> None:
    assert not data_check_pii_values(["1234567890123456"] * 4)["problem"]
    assert not data_check_pii_values(["A1234567890123456Z"] * 6)["problem"]
    assert not data_check_pii_values(["2021-05-03"] * 6)["problem"]
    assert not data_check_pii_values([str(1990 + i % 31) for i in range(40)])["problem"]
    assert not data_check_pii_values([str(100000000 + 7919 * i) for i in range(1, 41)])["problem"]
    assert not data_check_pii_values([f"{1 + i * 2.37:.2f}" for i in range(1, 41)])["problem"]


def test_pii_values_no_phone_or_timestamps() -> None:
    assert not data_check_pii_values(["2019-01-15 14:32:07"] * 10)["problem"]
    assert not data_check_pii_values(["+31612345678"] * 5)["problem"]
    assert not data_check_pii_values(["040-247 1234"] * 8)["problem"]


def test_luhn_and_card() -> None:
    assert _pii_luhn_ok("4111111111111111")
    assert not _pii_luhn_ok("4111111111111112")
    assert not _pii_luhn_ok("123")
    assert _pii_card_ok("4111 1111 1111 1111")
    assert not _pii_card_ok("1595957929810")  # a millisecond timestamp
    assert not _pii_card_ok("3411111111111111")  # 16 digits starting 34 is not Amex


# -- data_check_pii_name -------------------------------------------------------------------


def test_split_name() -> None:
    # both readings of each capital run; the stray fragments are harmless
    assert _pii_split_name("RecipientFirstName") == [
        "recipient", "first", "name", "r", "ecipient", "f", "irst", "n", "ame",
    ]  # fmt: skip
    assert _pii_split_name("IPAddress")[:2] == ["ip", "address"]
    assert "zip" in _pii_split_name("ZIPcode")
    assert _pii_split_name(None) == [None]


def test_pii_name_flags_identifying_names_only() -> None:
    for nm in ("participant_email", "DOB", "home_address", "latitude", "RecipientFirstName",
               "PeronalData_fullname", "IPAddress"):  # fmt: skip
        assert data_check_pii_name(nm)["problem"], nm
    for nm in ("description", "score", "reaction_time", "experimentName", "trial_name",
               "videoName", "fileName", "Conditionname", "Rolename"):  # fmt: skip
        assert not data_check_pii_name(nm)["problem"], nm
    assert not data_check_pii_name(None)["problem"]
    assert not data_check_pii_name("")["problem"]


# -- data_check_pii_geo --------------------------------------------------------------------


def test_pii_geo_requires_geographic_name() -> None:
    assert data_check_pii_geo("latitude", [52.37, 4.89, 51.5])["problem"]
    assert data_check_pii_geo("gps", [1, 2, 3])["problem"]
    assert not data_check_pii_geo("x", [10 + i * 20 / 39 for i in range(40)])["problem"]
    assert not data_check_pii_geo("temperature", [20.1, 21.5, 19.8])["problem"]


def test_pii_geo_compound_name() -> None:
    sib = ["LocationLatitude", "LocationLongitude"]
    assert data_check_pii_geo("LocationLatitude", [52.37, 4.89, 51.5], sib)["problem"]
    assert data_check_pii_geo("gps_lat", [52.37, 4.89, 51.5], ["gps_lat", "gps_lon"])["problem"]


def test_pii_geo_requires_partner_for_lat_lon() -> None:
    psych = ["id", "lat", "rt", "accuracy"]
    latency = [round(0.3 + i * 3.2 / 199, 3) for i in range(200)]
    assert not data_check_pii_geo("lat", latency, psych)["problem"]
    assert not data_check_pii_geo("lat", [0, 1] * 100, psych)["problem"]
    assert not data_check_pii_geo("lon", [*range(1, 8)] * 20, ["id", "lon", "rt"])["problem"]
    coords = [round(50 + i * 4 / 199, 3) for i in range(200)]
    assert data_check_pii_geo("lat", coords, ["id", "lat", "lon"])["problem"]
    assert data_check_pii_geo("gps", [-10 + i for i in range(20)], psych)["problem"]
    assert not data_check_pii_geo("lat", [650 + i for i in range(200)], ["id", "lat", "lon"])[
        "problem"
    ]


# -- data_check_pii_freetext -----------------------------------------------------------------


def test_pii_freetext_flags_prose_only() -> None:
    prose = [
        "I really enjoyed the study and thought it was interesting overall today.",
        "The instructions were a bit unclear at the start but fine later on here.",
        "My name is Jane and I live in Amsterdam near the central train station.",
        "Great experience overall, I would happily participate again next time.",
        "The room felt cold and I had some trouble focusing on the main tasks.",
    ]
    r = data_check_pii_freetext(prose)
    assert r["problem"]
    assert r["message"].startswith("Free-text column (median 71 characters, 100% distinct)")
    assert not data_check_pii_freetext(["yes", "no", "yes", "maybe", "no", "yes", "no"])["problem"]
    assert not data_check_pii_freetext([f"{1000 + i * 13.1234:.4f}" for i in range(30)])["problem"]
    assert not data_check_pii_freetext(
        [f"R_{i:016d}ABCDEFGH" for i in range(30)]
    )["problem"]  # fmt: skip
    assert not data_check_pii_freetext(
        [f"https://example.com/path/{i}/item?x={i}" for i in range(1, 31)]
    )["problem"]
    assert not data_check_pii_freetext(
        [f"C:/data/study/participant_{i}/trial_data.csv" for i in range(1, 31)]
    )["problem"]


# -- data_check_demographic --------------------------------------------------------------------


def test_demographic_age() -> None:
    assert data_check_demographic("age", [23, 45, 31, 29, 55, 19, 67, 40]) == "age"
    assert data_check_demographic("Age_years", list(range(18, 36))) == "age"
    assert data_check_demographic("participant_age", [34, 29, 41]) == "age"
    assert data_check_demographic("age", [23, 45, 31, 29, 55, 19, 67, 999]) == "age"
    assert data_check_demographic("age", [1990, 1985, 2001, 1979, 1995, 2003]) is None
    assert data_check_demographic("age", ["young", "old", "middle", "young", "old"]) is None


def test_demographic_age_false_friends() -> None:
    for nm in ("percentage", "page", "average_rt", "image_id", "coverage", "storage",
               "language", "damage", "usage", "agent"):  # fmt: skip
        assert data_check_demographic(nm, [10, 20, 30, 40, 50]) is None, nm


def test_demographic_gender() -> None:
    assert (
        data_check_demographic("gender", ["Male", "Female", "Female", "Male", "Non-binary"])
        == "gender"
    )
    assert data_check_demographic("sex", [1, 2, 1, 2, 1, 2]) == "gender"
    assert data_check_demographic("Geslacht", ["man", "vrouw", "man"]) == "gender"
    assert data_check_demographic("gender", ["apple", "banana", "cherry", "kiwi", "melon"]) is None
    assert data_check_demographic("condition", [1, 2, 1, 2, 1, 2]) is None


def test_demographic_race() -> None:
    assert (
        data_check_demographic("race", ["White", "Black", "Asian", "Hispanic", "Other"]) == "race"
    )
    assert data_check_demographic("ethnicity", ["Hispanic", "Non-Hispanic"]) == "race"
    assert data_check_demographic("race_ethnicity", [1, 2, 3, 4, 5]) == "race"
    assert data_check_demographic("race", [" ".join(["word"] * 30)] * 5) is None


def test_demographic_empty_names() -> None:
    assert data_check_demographic("", [1, 2, 3]) is None
    assert data_check_demographic([None], [1, 2, 3]) is None
    assert data_check_demographic([], [1, 2, 3]) is None
    assert data_check_demographic(None, [1, 2, 3]) is None


# -- facets and concepts ------------------------------------------------------------------------


def test_col_facets_split_type_into_orthogonal_properties() -> None:
    f = data_col_facets("subject_id", ["s01", "s02", "s03"])
    assert (f["representation"], f["role"], f["concept"]) == ("text", "identifier", "id")

    f = data_col_facets("age", [23, 45, 31, 29, 55, 19, 67, 40])
    assert f["representation"] == "numeric"
    assert f["measurement_level"] == "ratio"
    assert f["concept"] == "age"
    assert f["unit"] == "years"

    assert data_col_facets("RT", [543, 612, 498, 701, 555])["unit"] == "milliseconds"
    assert data_col_facets("rt_s", [0.54, 0.61, 0.49, 0.70, 0.55])["unit"] == "seconds"

    likert = [*range(1, 6)] * 12
    f = data_col_facets("panas_1", likert, in_scale_block=True)
    assert f["measurement_level"] == "ordinal"
    assert f["concept"] == "likert"
    assert data_col_facets("panas_1", likert)["concept"] is None
    assert data_col_facets("round", likert, in_scale_block=False)["concept"] is None

    f = data_col_facets("price", ["1,50", "2,30", "4,10", "5,00", "3,25", "6,60", "2,10", "1,90"])
    assert f["representation"] == "numeric"
    assert f["parse_note"] == "comma_decimal"

    assert data_col_facets("k", [7] * 10)["quality"] == "constant"
    assert data_col_facets("e", [None, None])["quality"] == "empty"


def test_col_facets_keys() -> None:
    f = data_col_facets("x", [1.5, 2.5, 3.7, 4.1, 5.9])
    assert list(f) == [
        "representation", "measurement_level", "concept", "role", "unit", "quality",
        "parse_note", "numeric_values", "n_coerced", "is_numeric", "ambiguous",
    ]  # fmt: skip


def test_col_concept_name_value_agreement() -> None:
    assert data_col_concept("RT", [543, 612, 498, 701]) == "reaction_time"
    assert data_col_concept("accuracy", [1, 0, 1, 1, 0]) == "accuracy"
    assert data_col_concept("correct", ["correct", "incorrect", "correct"]) == "accuracy"
    assert data_col_concept("condition", [1, 2, 1, 2]) == "condition"
    assert data_col_concept("age", [23, 45, 31]) == "age"
    assert data_col_concept("time", ["10:00", "10:05", "10:10"]) in (None, "timestamp")
    assert data_col_concept("rt", [-5] * 10) is None


def test_timestamp_and_date_concepts() -> None:
    ts = ["2021-05-01 10:00:00", "2021-05-02 10:00:00"]
    assert data_col_concept("StartDate", ts) == "timestamp"
    assert _concept_is_timestamp("StartDate", ts)
    assert not _concept_is_date("date", ts)
    assert data_col_concept("date", ["2021-01-01", "2021-02-01", "2021-03-01"]) == "date"
    psychopy = ["2020-05-19_16h20.01.792", "2020-05-20_09h01.11.001"]
    assert data_col_concept("session_time", psychopy) == "timestamp"
    # a timestamp under a date-only name is neither (the name must agree)
    assert data_col_concept("date", psychopy) is None


def test_parse_frac_strptime_rules() -> None:
    dt_fmts = ["%Y-%m-%d %H:%M:%S"]
    assert _parse_frac(["2021-01-01 24:00:00", "2021-01-01 24:30:00"], dt_fmts) == 0.5
    assert _parse_frac(["2021-02-29", "2020-02-29"], ["%Y-%m-%d"]) == 0.5
    assert _parse_frac([20210101.0], ["%Y-%m-%d"]) == 0
    assert _parse_frac(["2021-01-01" + " x" * 150, "2021-01-02"], ["%Y-%m-%d"]) == 0.5
    assert _parse_frac(pd.Series(["5 Mar 2021", "March 5, 2021"]), ["%d %b %Y"]) == 0.5
