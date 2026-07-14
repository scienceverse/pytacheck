from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import MODULE_REGISTRY, ModuleMetadata
from pytacheck.modules.stat_check import stat_check

FIXTURE_PATH = Path(__file__).parents[2] / "parity" / "fixtures" / "statcheck_cases.json"
FIXTURE = cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
ORACLE = cast(dict[str, Any], FIXTURE["oracle"])
CASES = cast(list[dict[str, Any]], FIXTURE["cases"])
CASES_BY_ID = {cast(str, case["id"]): case for case in CASES}

ORACLE_ROW_FIELDS = (
    "test_type",
    "raw",
    "df1",
    "df2",
    "test_comp",
    "test_value",
    "p_comp",
    "reported_p",
    "computed_p",
    "error",
    "decision_error",
)

NO_TESTS_MESSAGE = (
    "No detectable t- or F-tests. StatCheck currently only detects statistics written "
    "in APA format."
)


def context_for(*texts: str, paper_id: str = "test") -> PaperContext:
    paper = BibrPaper.model_validate(
        {
            "paper_id": paper_id,
            "info": {"schema_version": "10.6"},
            "section": [
                {
                    "section_id": index,
                    "header": "Results",
                    "section_type": "results",
                }
                for index in range(1, len(texts) + 1)
            ],
            "text": [
                {
                    "text": text,
                    "text_id": index,
                    "paragraph_id": index,
                    "section_id": index,
                    "page_number": index + 2,
                    "coordinates": {"x": index * 10.0, "y": index * 20.0},
                    "source_marker": f"sentence-{index}",
                }
                for index, text in enumerate(texts, start=1)
            ],
        }
    )
    return PaperContext.from_paper(paper)


def context_for_case(case: dict[str, Any]) -> PaperContext:
    return context_for(*cast(list[str], case["texts"]), paper_id=cast(str, case["id"]))


def expected_for(case: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], case["expected"])


def test_stat_check_is_registered_with_upstream_metadata() -> None:
    expected = ModuleMetadata(
        name="stat_check",
        title="StatCheck",
        description="Check consistency of p-values and test statistics",
        section="results",
        validated=True,
    )

    assert stat_check.__pytacheck_metadata__ == expected
    assert MODULE_REGISTRY["stat_check"].metadata == expected
    assert MODULE_REGISTRY["stat_check"].function is stat_check


def test_r_oracle_fixture_records_required_statcheck_1_5_0_cases() -> None:
    assert ORACLE["package"] == "statcheck"
    assert ORACLE["version"] == "1.5.0"
    assert ORACLE["validated_test_types"] == ["t", "F"]
    assert ORACLE["computed_p_abs_tolerance"] == 1e-12
    assert len(CASES) == 40

    required_ids = {
        "consistent_t",
        "inconsistent_t_fractional_df",
        "consistent_f",
        "inconsistent_f_decision",
        "reported_zero_error",
        "reported_four_decimal_small_p",
        "less_than_001_inside",
        "less_than_001_outside",
        "rounding_one_decimal_inside",
        "rounding_two_decimals_inside",
        "rounding_three_decimals_inside",
        "rounding_four_decimals_inside",
        "no_statistic",
        "chi_square_ignored",
        "duplicate_identical_sentences",
        "uppercase_t_ignored",
        "lowercase_f_ignored",
        "unicode_nbsp_ignored",
        "unicode_minus_supported",
        "comparator_matrix",
        "fractional_f_degrees_of_freedom",
        "letter_f_df1_forms",
        "nonsignificant_shorthand",
        "scientific_p_literal",
        "multiple_matches_one_sentence",
        "negative_f_retained",
        "greater_equal_test_discarded",
        "less_equal_test_discarded",
        "malformed_scientific_e_ignored",
        "malformed_scientific_e_minus_ignored",
        "embedded_t_without_boundary",
    }
    assert required_ids <= CASES_BY_ID.keys()

    expected_keys = set(ORACLE_ROW_FIELDS) | {"source_text_index"}
    for case in CASES:
        for row in cast(list[dict[str, Any]], expected_for(case)["table"]):
            assert set(row) == expected_keys
        for row in cast(list[dict[str, Any]], case.get("ignored_oracle_rows", [])):
            assert set(row) == expected_keys


@pytest.mark.parametrize("case", CASES, ids=[cast(str, case["id"]) for case in CASES])
def test_stat_check_matches_r_oracle_fixture(case: dict[str, Any]) -> None:
    expected = expected_for(case)
    expected_rows = cast(list[dict[str, Any]], expected["table"])
    result = stat_check(context_for_case(case))

    assert len(result.table) == len(expected_rows)
    tolerance = cast(float, ORACLE["computed_p_abs_tolerance"])
    for actual_row, expected_row in zip(result.table, expected_rows, strict=True):
        for field in ORACLE_ROW_FIELDS:
            if field == "computed_p":
                assert actual_row[field] == pytest.approx(
                    expected_row[field],
                    rel=0,
                    abs=tolerance,
                )
            else:
                assert actual_row[field] == expected_row[field]

    expected_counts = cast(dict[str, int], expected["counts"])
    assert result.summary_table == [{"paper_id": case["id"], **expected_counts}]
    assert result.traffic_light == expected["traffic_light"]


@pytest.mark.parametrize("case_id", ["no_statistic", "chi_square_ignored"])
def test_no_validated_tests_is_na(case_id: str) -> None:
    case = CASES_BY_ID[case_id]
    result = stat_check(context_for_case(case))

    assert result.module == "stat_check"
    assert result.title == "StatCheck"
    assert result.table == []
    assert result.summary_table == [
        {
            "paper_id": case_id,
            "statcheck_found": 0,
            "statcheck_errors": 0,
            "statcheck_decision_errors": 0,
        }
    ]
    assert result.traffic_light == "na"
    assert result.summary_text == NO_TESTS_MESSAGE
    assert result.report == NO_TESTS_MESSAGE


def test_nonvalidated_chi_square_cached_row_is_ignored() -> None:
    case = CASES_BY_ID["chi_square_ignored"]
    context = context_for_case(case)
    oracle_row = cast(list[dict[str, Any]], case["ignored_oracle_rows"])[0]
    cached_row = deepcopy(dict(context.sentences[0]))
    cached_row.update(
        {
            "text": oracle_row["raw"],
            "expanded": cast(list[str], case["texts"])[0],
            "raw": oracle_row["raw"],
            "test_type": oracle_row["test_type"],
            "statistic": oracle_row["test_value"],
            "df": [oracle_row["df1"]],
            "df1": oracle_row["df1"],
            "df2": oracle_row["df2"],
            "comp": oracle_row["test_comp"],
            "p_comp": oracle_row["p_comp"],
            "p_value": oracle_row["reported_p"],
        }
    )
    context = replace(context, apa_tests=(cached_row,))

    result = stat_check(context)

    assert result.table == []
    assert result.traffic_light == "na"
    assert result.summary_table == [
        {
            "paper_id": "chi_square_ignored",
            "statcheck_found": 0,
            "statcheck_errors": 0,
            "statcheck_decision_errors": 0,
        }
    ]


def test_table_preserves_source_location_and_duplicate_rows() -> None:
    case = CASES_BY_ID["duplicate_identical_sentences"]
    texts = cast(list[str], case["texts"])
    expected_rows = cast(list[dict[str, Any]], expected_for(case)["table"])
    result = stat_check(context_for_case(case))

    assert len(result.table) == 2
    for row, expected_row in zip(result.table, expected_rows, strict=True):
        source_index = cast(int, expected_row["source_text_index"])
        assert row["paper_id"] == "duplicate_identical_sentences"
        assert row["text_id"] == source_index
        assert row["paragraph_id"] == source_index
        assert row["section_id"] == source_index
        assert row["header"] == "Results"
        assert row["section_type"] == "results"
        assert row["page_number"] == source_index + 2
        assert row["text"] == texts[source_index - 1]
        assert row["raw"] == expected_row["raw"]
        assert row["expanded"] == texts[source_index - 1]


def test_inexact_statistic_comparator_matrix_matches_r_rules() -> None:
    case = CASES_BY_ID["comparator_matrix"]
    result = stat_check(context_for_case(case))

    assert [(row["test_comp"], row["p_comp"]) for row in result.table] == [
        ("=", "="),
        ("=", "<"),
        ("=", ">"),
        ("<", "="),
        ("<", "<"),
        ("<", ">"),
        (">", "="),
        (">", "<"),
        (">", ">"),
    ]
    assert [row["error"] for row in result.table] == [
        False,
        True,
        True,
        True,
        True,
        False,
        True,
        False,
        True,
    ]
    assert [row["decision_error"] for row in result.table] == [
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        True,
    ]


def test_multiple_matches_in_one_sentence_remain_in_source_order() -> None:
    case = CASES_BY_ID["multiple_matches_one_sentence"]
    expected_rows = cast(list[dict[str, Any]], expected_for(case)["table"])
    result = stat_check(context_for_case(case))

    assert [row["raw"] for row in result.table] == [row["raw"] for row in expected_rows]
    assert [row["test_type"] for row in result.table] == ["t", "F", "t"]
    assert [row["text_id"] for row in result.table] == [1, 1, 1]
    assert all(row["expanded"] == cast(list[str], case["texts"])[0] for row in result.table)


def test_negative_f_is_retained_as_a_decision_error() -> None:
    result = stat_check(context_for_case(CASES_BY_ID["negative_f_retained"]))

    assert len(result.table) == 1
    assert result.table[0]["test_value"] == -5.0
    assert result.table[0]["computed_p"] == 1.0
    assert result.table[0]["error"] is True
    assert result.table[0]["decision_error"] is True
    assert result.summary_table[0]["statcheck_found"] == 1


@pytest.mark.parametrize(
    "case_id",
    ["greater_equal_test_discarded", "less_equal_test_discarded"],
)
def test_repeated_test_comparators_are_discarded(case_id: str) -> None:
    context = context_for_case(CASES_BY_ID[case_id])

    assert context.apa_tests == ()
    assert stat_check(context).traffic_light == "na"


@pytest.mark.parametrize(
    "case_id",
    ["malformed_scientific_e_ignored", "malformed_scientific_e_minus_ignored"],
)
def test_malformed_p_exponents_do_not_abort_paper_context(case_id: str) -> None:
    context = context_for_case(CASES_BY_ID[case_id])
    result = stat_check(context)

    assert context.apa_tests == ()
    assert result.table == []
    assert result.traffic_light == "na"


def test_r_boundary_behavior_extracts_t_embedded_after_a_word_character() -> None:
    result = stat_check(context_for_case(CASES_BY_ID["embedded_t_without_boundary"]))

    assert [row["raw"] for row in result.table] == ["t(18) = 2.10, p = .050"]


def test_green_red_and_plural_summaries_match_wrapper_behavior() -> None:
    green = stat_check(context_for_case(CASES_BY_ID["consistent_t"]))
    red = stat_check(context_for_case(CASES_BY_ID["inconsistent_t_fractional_df"]))
    plural = stat_check(
        context_for(
            "The result was t(97.2) = -1.96, p = 0.152.",
            "The omnibus test was F(2, 30) = 5.00, p = .130.",
            paper_id="plural-errors",
        )
    )

    assert green.summary_text == "We detected no errors in t-tests or F-tests."
    assert green.report == green.summary_text

    assert red.summary_text == "1 possible error in t-tests or F-tests"
    assert "We detected possible errors in test statistics." in red.report
    assert "t(97.2) = -1.96, p = 0.152" in red.report

    assert plural.summary_text == "2 possible errors in t-tests or F-tests"
    assert plural.summary_table == [
        {
            "paper_id": "plural-errors",
            "statcheck_found": 2,
            "statcheck_errors": 2,
            "statcheck_decision_errors": 1,
        }
    ]
    assert plural.traffic_light == "red"


def test_stat_check_does_not_mutate_cached_context_rows() -> None:
    context = context_for("The result was t(18) = 2.10, p = .050.")
    cached_rows = context.apa_tests
    with pytest.raises(TypeError, match="read-only"):
        cached_rows[0]["cached_only_marker"] = "precomputed-apa-test"
    snapshot = deepcopy(cached_rows)

    first = stat_check(context)
    second = stat_check(context)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert context.apa_tests is cached_rows
    assert context.apa_tests == snapshot
    assert "computed_p" not in context.apa_tests[0]
    assert "error" not in context.apa_tests[0]
    assert "decision_error" not in context.apa_tests[0]
    assert first.table[0] is not context.apa_tests[0]
