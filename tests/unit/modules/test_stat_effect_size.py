from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from math import sqrt
from pathlib import Path
from typing import Any, cast

import pytest
from pytacheck.modules.stat_effect_size import stat_effect_size

from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import MODULE_REGISTRY, ModuleMetadata

FIXTURE_PATH = Path(__file__).parents[2] / "parity" / "fixtures" / "effect_size_cases.json"
FIXTURE = cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
ORACLE = cast(dict[str, Any], FIXTURE["oracle"])
CASES = cast(list[dict[str, Any]], FIXTURE["cases"])
CASES_BY_ID = {cast(str, case["id"]): case for case in CASES}

BASE_ROW_FIELDS = {
    "paper_id",
    "section_id",
    "paragraph_id",
    "text_id",
    "text",
    "test",
    "test_text",
    "es",
}
D_ROW_FIELDS = {
    "d_reported",
    "d_reported_text",
    "t_value",
    "df",
    "d_implied_paired_dz",
    "d_implied_paired_drm_r05",
    "d_implied_indep_equal_n",
    "d_implied_indep_unequal_min",
    "d_implied_indep_unequal_max",
    "d_implied_n",
    "d_coherence",
    "d_coherence_assumption",
    "d_coherence_note",
}
F_ROW_FIELDS = {
    "f_reported",
    "f_reported_text",
    "df1",
    "df2",
    "eta_implied_partial",
    "omega_implied_partial",
    "eta_coherence",
    "eta_coherence_assumption",
    "eta_coherence_note",
}
REQUIRED_ROW_FIELDS = BASE_ROW_FIELDS | D_ROW_FIELDS | F_ROW_FIELDS

NO_TESTS_MESSAGE = "No t-tests or F-tests were detected."
GREEN_MESSAGE = "All detected t-tests and F-tests had an effect size reported in the same sentence."
NO_MATCH_MESSAGE = (
    "All effect sizes were reported, but some appear inconsistent with the test statistic. "
    "This can be because the effect size is not clearly labeled (e.g., d, instead of d_rm), "
    "because the effect sizes is not reported with enough precisions (e.g., 0.3 instead of "
    "0.32), or because the effect size is incorrectly reported."
)


def context_for(
    *texts: str,
    paper_id: str = "test",
    section_types: tuple[str, ...] | None = None,
) -> PaperContext:
    if section_types is None:
        section_types = ("results",) * len(texts)
    if len(section_types) != len(texts):
        raise ValueError("section_types must contain one value per text")

    paper = BibrPaper.model_validate(
        {
            "paper_id": paper_id,
            "info": {"schema_version": "10.6"},
            "section": [
                {
                    "section_id": index,
                    "header": section_type.title(),
                    "section_type": section_type,
                }
                for index, section_type in enumerate(section_types, start=1)
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
                    "metadata": {"nested": [index]},
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


def result_for(case_id: str):
    return stat_effect_size(context_for_case(CASES_BY_ID[case_id]))


def test_stat_effect_size_is_registered_with_upstream_metadata() -> None:
    expected = ModuleMetadata(
        name="stat_effect_size",
        title="Effect Sizes in t-tests and F-tests",
        description=(
            "The Effect Size module checks if effect sizes are correctly reported in "
            "t-tests and F-tests."
        ),
        section="results",
        validated=True,
    )

    assert stat_effect_size.__pytacheck_metadata__ == expected
    assert MODULE_REGISTRY["stat_effect_size"].metadata == expected
    assert MODULE_REGISTRY["stat_effect_size"].function is stat_effect_size


def test_fixture_has_explicit_and_complete_oracle_provenance() -> None:
    assert ORACLE["source"] == "metacheck/inst/modules/stat_effect_size.R"
    assert ORACLE["labels"] == ["metacheck-r-current", "pytacheck-extension"]
    assert ORACLE["coherence_tolerance"] == 0.01
    assert len(CASES) == 24
    assert len(CASES_BY_ID) == len(CASES)

    required_ids = {
        "no_statistics",
        "all_effect_sizes_missing",
        "some_effect_sizes_missing",
        "no_match_precedes_partial_missing",
        "paired_dz_match",
        "independent_equal_n_match",
        "independent_unequal_n_match",
        "d_no_match",
        "noninteger_t_df",
        "d_rm_presence_only",
        "hedges_g_presence_only",
        "partial_eta_match",
        "partial_eta_no_match",
        "plain_eta_presence_only",
        "cohens_f_presence_only",
        "omega_presence_only",
        "decimal_f_df_ignored",
        "three_t_tests_pair_positionally",
        "three_f_tests_pair_positionally",
        "one_effect_broadcast_to_multiple_tests",
        "later_d_candidate_controls_match",
        "non_equal_comparator_is_indeterminate",
        "wrong_family_still_counts_as_present",
        "spelled_out_partial_eta",
    }
    assert required_ids == CASES_BY_ID.keys()

    allowed_oracles = set(cast(list[str], ORACLE["labels"]))
    assert {case["oracle"] for case in CASES} <= allowed_oracles
    extension_ids = {case["id"] for case in CASES if case["oracle"] == "pytacheck-extension"}
    assert extension_ids == {"spelled_out_partial_eta"}


@pytest.mark.parametrize("case", CASES, ids=[cast(str, case["id"]) for case in CASES])
def test_effect_size_matches_versioned_fixture(case: dict[str, Any]) -> None:
    expected = expected_for(case)
    result = stat_effect_size(context_for_case(case))

    expected_rows = cast(list[dict[str, Any]], expected["rows"])
    assert result.traffic_light == expected["traffic_light"]
    assert result.summary_table == [
        {
            "paper_id": case["id"],
            **cast(dict[str, int], expected["summary"]),
        }
    ]
    assert len(result.table) == len(expected_rows)
    for actual_row, expected_row in zip(result.table, expected_rows, strict=True):
        for field, value in expected_row.items():
            assert actual_row[field] == value


def test_output_rows_expose_complete_r_contract_and_full_source_sentence() -> None:
    source = "The independent contrast was t(48) = 2.00, d = .57."
    result = stat_effect_size(context_for(source, paper_id="row-contract"))

    assert len(result.table) == 1
    row = result.table[0]
    assert row.keys() >= REQUIRED_ROW_FIELDS
    assert row["paper_id"] == "row-contract"
    assert row["section_id"] == 1
    assert row["paragraph_id"] == 1
    assert row["text_id"] == 1
    assert row["text"] == source
    assert row["test_text"] == "t(48) = 2.00"
    assert row["es"] == "d = .57"
    assert row["eta_coherence"] is None
    assert row["f_reported"] is None

    serialized = result.model_dump(mode="json")
    json.dumps(serialized, allow_nan=False)


def test_t_formulas_and_assumption_precedence_match_upstream() -> None:
    paired = result_for("paired_dz_match").table[0]
    equal = result_for("independent_equal_n_match").table[0]
    unequal = result_for("independent_unequal_n_match").table[0]

    assert float(cast(str, paired["d_implied_paired_dz"])) == pytest.approx(2.73 / sqrt(24))
    assert float(cast(str, paired["d_implied_paired_drm_r05"])) == pytest.approx(
        2.73 / sqrt(24) / sqrt(0.5)
    )
    assert paired["d_coherence_assumption"] == "paired_dz"
    assert paired["d_implied_n"] == "n = 24"
    assert paired["d_coherence_note"] == "Match under paired-samples dz assumption."

    assert float(cast(str, equal["d_implied_indep_equal_n"])) == pytest.approx(4 / sqrt(50))
    assert equal["d_coherence_assumption"] == "independent_equal_n"
    assert equal["d_implied_n"] == "n1 = n2 = 25, N = 50"
    assert "n1 = n2 = 25, N = 50" in cast(str, equal["d_coherence_note"])

    assert float(cast(str, unequal["d_implied_indep_unequal_min"])) <= 0.68
    assert float(cast(str, unequal["d_implied_indep_unequal_max"])) >= 0.68
    assert unequal["d_coherence_assumption"] == "independent_unequal_n_range"
    assert unequal["d_implied_n"] == "n1 = 16, n2 = 24, N = 40"


def test_d_tolerance_is_inclusive_and_values_just_beyond_it_do_not_match() -> None:
    just_inside = stat_effect_size(context_for("t(38) = 2.00, d = .622455532034."))
    just_outside = stat_effect_size(context_for("t(38) = 2.00, d = .622."))

    assert just_inside.table[0]["d_coherence"] == "match_under_assumptions"
    assert just_inside.table[0]["d_coherence_assumption"] == "independent_equal_n"
    assert just_outside.table[0]["d_coherence"] == "no_match"
    assert just_outside.traffic_light == "red"


def test_noninteger_t_df_returns_before_reported_and_implied_d_fields() -> None:
    row = result_for("noninteger_t_df").table[0]

    assert row["t_value"] == "-1.96"
    assert row["df"] == "97.2"
    assert row["d_reported"] is None
    assert row["d_reported_text"] is None
    assert row["d_implied_paired_dz"] is None
    assert row["d_implied_paired_drm_r05"] is None
    assert row["d_implied_indep_equal_n"] is None
    assert row["d_implied_indep_unequal_min"] is None
    assert row["d_implied_indep_unequal_max"] is None
    assert row["d_coherence_note"] == (
        "Non-integer df indicates Welch's t-test (unequal variances); sample sizes "
        "cannot be determined."
    )


def test_dead_d_rm_and_hedges_g_paths_are_presence_only() -> None:
    d_rm = result_for("d_rm_presence_only").table[0]
    hedges = result_for("hedges_g_presence_only").table[0]

    for row in (d_rm, hedges):
        assert row["d_coherence"] == "indeterminate"
        assert row["d_coherence_assumption"] == "none"
        assert row["d_coherence_note"] == "No parseable d effect size found."
        assert row["d_implied_paired_drm_r05"] is None


def test_partial_eta_and_omega_formulas_are_exposed_as_strings() -> None:
    eta = result_for("partial_eta_match").table[0]
    omega = result_for("omega_presence_only").table[0]

    assert eta["f_reported"] == "4"
    assert eta["f_reported_text"] == "F(1, 38) = 4.00"
    assert eta["df1"] == "1"
    assert eta["df2"] == "38"
    assert float(cast(str, eta["eta_implied_partial"])) == pytest.approx(4 / 42)
    assert float(cast(str, eta["omega_implied_partial"])) == pytest.approx(3 / 43)

    assert float(cast(str, omega["omega_implied_partial"])) == pytest.approx(0)
    assert omega["eta_coherence"] == "indeterminate"
    assert omega["eta_coherence_assumption"] == "none"
    assert omega["eta_coherence_note"] == (
        "Effect size reported but not verifiable from F and degrees of freedom alone."
    )


def test_eta_tolerance_and_absolute_value_behavior_match_upstream() -> None:
    eta_inside = stat_effect_size(context_for("F(1, 38) = 4.00, ηp² = .085238095239."))
    eta_outside = stat_effect_size(context_for("F(1, 38) = 4.00, ηp² = .085."))
    signed = stat_effect_size(
        context_for("t(38) = -2.00, d = -.63; F(1, 38) = -4.00, ηp² = -.095.")
    )

    assert eta_inside.table[0]["eta_coherence"] == "match_under_assumptions"
    assert eta_outside.table[0]["eta_coherence"] == "no_match"
    assert eta_outside.traffic_light == "red"
    assert signed.table[0]["d_coherence"] == "match_under_assumptions"
    assert signed.table[1]["eta_coherence"] == "match_under_assumptions"


def test_plain_eta_and_cohens_f_have_exact_indeterminate_notes() -> None:
    eta = result_for("plain_eta_presence_only").table[0]
    cohens_f = result_for("cohens_f_presence_only").table[0]

    assert eta["eta_coherence_note"] == (
        "Eta-squared reported; cannot be reconstructed from F and dfs in factorial or "
        "repeated-measures designs."
    )
    assert cohens_f["eta_coherence_note"] == (
        "Cohen's f not checked: cannot determine whether based on eta squared or partial "
        "eta squared."
    )


def test_partial_eta_controls_when_plain_eta_or_cohens_f_is_also_present() -> None:
    with_f = stat_effect_size(context_for("F(1, 38) = 4.00, f = .31; ηp² = .095."))
    with_plain_eta = stat_effect_size(context_for("F(1, 38) = 4.00, η² = .40; ηp² = .095."))

    for result in (with_f, with_plain_eta):
        assert result.traffic_light == "green"
        assert result.table[0]["eta_coherence"] == "match_under_assumptions"
        assert result.table[0]["eta_coherence_assumption"] == "partial_eta_squared"


def test_equal_cardinality_pairing_is_positional_and_family_blind() -> None:
    result = stat_effect_size(context_for("t(23) = 2.73; F(1, 38) = 4.00, ηp² = .095; d = .56."))

    assert [row["test"] for row in result.table] == ["t-test", "F-test"]
    assert [row["es"] for row in result.table] == ["ηp² = .095", "d = .56"]
    assert result.table[0]["d_coherence"] == "indeterminate"
    assert result.table[1]["eta_coherence"] == "indeterminate"
    assert result.traffic_light == "green"


def test_only_cached_equations_are_consumed_without_rescanning_sentences() -> None:
    context = context_for("t(48) = 2.00, d = .57.", paper_id="cached-only")
    equations_only = replace(
        context,
        sentences=(),
        paragraphs=(),
        p_values=(),
        apa_tests=(),
    )
    sentences_only = replace(context, equations=())

    cached_result = stat_effect_size(equations_only)
    no_equations_result = stat_effect_size(sentences_only)

    assert len(cached_result.table) == 1
    assert cached_result.table[0]["text"] == "t(48) = 2.00, d = .57."
    assert no_equations_result.table == []
    assert no_equations_result.traffic_light == "na"


def test_cached_equations_and_nested_source_metadata_are_not_mutated_or_aliased() -> None:
    context = context_for("t(48) = 2.00, d = .57.")
    before = deepcopy(context.equations)

    first = stat_effect_size(context)
    second = stat_effect_size(context)

    assert context.equations == before
    assert first == second
    first.table[0]["test_text"] = "changed"
    assert context.equations == before
    assert second.table[0]["test_text"] == "t(48) = 2.00"


def test_reference_equations_are_excluded_by_the_shared_context() -> None:
    result = stat_effect_size(
        context_for(
            "Prior work reported t(48) = 2.00, d = .57.",
            paper_id="references-only",
            section_types=("references",),
        )
    )

    assert result.table == []
    assert result.summary_table == [{"paper_id": "references-only"}]
    assert result.traffic_light == "na"


def test_test_label_case_and_decimal_f_asymmetry_match_current_r() -> None:
    result = stat_effect_size(
        context_for(
            "Uppercase T(20) = 2.00, d = .50.",
            "Lowercase f(1, 20) = 4.00, ηp² = .17.",
            "Decimal F(1.5, 20) = 4.00, ηp² = .23.",
        )
    )

    assert result.table == []
    assert result.traffic_light == "na"


def test_core_summary_and_report_messages_follow_traffic_state() -> None:
    no_tests = result_for("no_statistics")
    green = result_for("paired_dz_match")
    yellow = result_for("some_effect_sizes_missing")
    all_missing = result_for("all_effect_sizes_missing")
    no_match = result_for("d_no_match")

    assert no_tests.summary_text == NO_TESTS_MESSAGE
    assert no_tests.report == NO_TESTS_MESSAGE
    assert green.summary_text == GREEN_MESSAGE
    assert green.report.startswith(GREEN_MESSAGE)
    assert yellow.summary_text == (
        "We found 1 t-test and/or F-test where effect sizes are not reported. Check these "
        "tests in the table below, and consider adding effect sizes"
    )
    assert all_missing.summary_text == (
        "We found 2 t-tests and/or F-tests where effect sizes are not reported. Check these "
        "tests in the table below, and consider adding effect sizes"
    )
    assert no_match.summary_text == NO_MATCH_MESSAGE
    assert "All tests had effect sizes" in no_match.report


def test_empty_text_paper_retains_original_id_in_null_summary() -> None:
    paper = BibrPaper.model_validate(
        {"paper_id": "empty-effect-size", "info": {"schema_version": "10.6"}}
    )

    result = stat_effect_size(PaperContext.from_paper(paper))

    assert result.table == []
    assert result.summary_table == [{"paper_id": "empty-effect-size"}]
    assert result.traffic_light == "na"
