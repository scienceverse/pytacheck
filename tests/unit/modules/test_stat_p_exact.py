from __future__ import annotations

import pytest

from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import MODULE_REGISTRY, ModuleMetadata
from pytacheck.modules.stat_p_exact import stat_p_exact


def context_for(*texts: str) -> PaperContext:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "test",
            "info": {"schema_version": "10.6"},
            "section": [
                {
                    "section_id": 1,
                    "header": "Results",
                    "section_type": "results",
                }
            ],
            "text": [
                {
                    "text": text,
                    "text_id": index,
                    "paragraph_id": index,
                    "section_id": 1,
                    "page_number": index + 2,
                    "source_marker": f"sentence-{index}",
                }
                for index, text in enumerate(texts, start=1)
            ],
        }
    )
    return PaperContext.from_paper(paper)


def test_stat_p_exact_is_registered_with_validated_results_metadata() -> None:
    expected = ModuleMetadata(
        name="stat_p_exact",
        title="Exact P-Values",
        description=(
            "List any p-values reported with insufficient precision (e.g., p < .05 or "
            "p = n.s.) or reported as exactly zero (e.g., p = .000)."
        ),
        section="results",
        validated=True,
    )

    assert stat_p_exact.__pytacheck_metadata__ == expected
    assert MODULE_REGISTRY["stat_p_exact"].metadata == expected
    assert MODULE_REGISTRY["stat_p_exact"].function is stat_p_exact


def test_no_detected_p_values_is_na() -> None:
    result = stat_p_exact(context_for("No probability value is reported here."))

    assert result.module == "stat_p_exact"
    assert result.title == "Exact P-Values"
    assert result.table == []
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 0, "n_zero": 0}]
    assert result.traffic_light == "na"
    assert result.summary_text == "We detected no *p* values."
    assert result.report == result.summary_text


def test_empty_text_paper_has_one_zero_summary_row_with_original_id() -> None:
    paper = BibrPaper.model_validate(
        {"paper_id": "empty-exact", "info": {"schema_version": "10.6"}}
    )

    result = stat_p_exact(PaperContext.from_paper(paper))

    assert result.summary_table == [{"paper_id": "empty-exact", "n_imprecise": 0, "n_zero": 0}]
    assert result.traffic_light == "na"


def test_exact_values_and_thresholds_at_or_below_point_zero_zero_one_are_green() -> None:
    result = stat_p_exact(
        context_for(
            "The exact result was p = .0123.",
            "The threshold result was p < .001.",
            "The smaller result was p < .0005.",
        )
    )

    assert [row["text"] for row in result.table] == [
        "p = .0123",
        "p < .001",
        "p < .0005",
    ]
    assert all(row["imprecise"] is False for row in result.table)
    assert all(row["zero"] is False for row in result.table)
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 0, "n_zero": 0}]
    assert result.traffic_light == "green"
    assert result.summary_text == (
        "We found no imprecise *p* values or *p*-values of exactly zero out of 3 detected."
    )
    assert result.report == result.summary_text


@pytest.mark.parametrize(
    ("text", "p_comp", "p_value"),
    [
        ("Bad p-value example (p < .05).", "<", 0.05),
        ("Bad p-value example (p<.05).", "<", 0.05),
        ("Bad p-value example (p < 0.05).", "<", 0.05),
        ("Bad p-value example; p < .005.", "<", 0.005),
        ("Bad p-value example (p > 0.05).", ">", 0.05),
        ("Bad p-value example (p > .1).", ">", 0.1),
        ("Bad p-value example (p <= .05).", "<=", 0.05),
        ("Bad p-value example (p = n.s.).", "=", None),
        ("Bad p-value example; p=ns.", "=", None),
    ],
)
def test_imprecise_forms_are_flagged(
    text: str,
    p_comp: str,
    p_value: float | None,
) -> None:
    result = stat_p_exact(context_for(text))

    assert len(result.table) == 1
    row = result.table[0]
    assert row["p_comp"] == p_comp
    assert row["p_value"] == p_value
    assert row["imprecise"] is True
    assert row["zero"] is False
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 1, "n_zero": 0}]
    assert result.traffic_light == "red"


def test_all_exact_zero_forms_are_flagged_separately_from_imprecision() -> None:
    result = stat_p_exact(
        context_for(
            "Significant result (p = .000).",
            "Significant result (p = 0.000).",
            "Significant result (p = 0.00).",
        )
    )

    assert [row["text"] for row in result.table] == [
        "p = .000",
        "p = 0.000",
        "p = 0.00",
    ]
    assert all(row["zero"] is True for row in result.table)
    assert all(row["imprecise"] is False for row in result.table)
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 0, "n_zero": 3}]
    assert result.traffic_light == "red"
    assert result.summary_text == (
        "We found 3 *p* values reported as exactly zero out of 3 detected *p* values."
    )
    assert "*P* values are never exactly zero." in result.report


def test_duplicate_imprecise_rows_use_unique_issue_count_in_summary_text() -> None:
    first_sentence = "The first estimate was p < .05."
    second_sentence = "The second estimate was p < .05."

    result = stat_p_exact(context_for(first_sentence, first_sentence, second_sentence))

    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 3, "n_zero": 0}]
    assert result.summary_text == ("We found 2 imprecise *p* values out of 3 detected *p* values.")
    assert result.report.count(f"| p < .05 | {first_sentence} |") == 1
    assert result.report.count(f"| p < .05 | {second_sentence} |") == 1


def test_duplicate_zero_rows_use_unique_issue_count_in_summary_text() -> None:
    first_sentence = "The first estimate was p = .000."
    second_sentence = "The second estimate was p = .000."

    result = stat_p_exact(context_for(first_sentence, first_sentence, second_sentence))

    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 0, "n_zero": 3}]
    assert result.summary_text == (
        "We found 2 *p* values reported as exactly zero out of 3 detected *p* values."
    )
    assert result.report.count(f"| p = .000 | {first_sentence} |") == 1
    assert result.report.count(f"| p = .000 | {second_sentence} |") == 1


def test_starred_table_note_is_detected_but_not_flagged_as_imprecise() -> None:
    result = stat_p_exact(context_for("Table note: * p < .05."))

    assert len(result.table) == 1
    assert result.table[0]["text"] == "p < .05"
    assert result.table[0]["imprecise"] is False
    assert result.table[0]["zero"] is False
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 0, "n_zero": 0}]
    assert result.traffic_light == "green"


def test_star_note_exemption_is_scoped_to_the_individual_match() -> None:
    result = stat_p_exact(context_for("Table note: * p < .05, but the estimate was p > .05."))

    assert [(row["text"], row["imprecise"]) for row in result.table] == [
        ("p < .05", False),
        ("p > .05", True),
    ]
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 1, "n_zero": 0}]
    assert all(not any(key.startswith("_") for key in row) for row in result.table)


def test_underflowed_nonzero_scientific_p_is_not_reported_as_exact_zero() -> None:
    result = stat_p_exact(context_for("The tiny result was p = .1e-400."))

    assert result.table[0]["p_value"] == 0.0
    assert result.table[0]["zero"] is False
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 0, "n_zero": 0}]
    assert not any(key.startswith("_") for key in result.table[0])


def test_invalid_probability_is_explicitly_flagged_and_treated_as_imprecise() -> None:
    result = stat_p_exact(context_for("The malformed report was p = 1.2."))

    assert result.table[0]["invalid"] is True
    assert result.table[0]["imprecise"] is True
    assert result.table[0]["zero"] is False
    assert result.traffic_light == "red"
    assert not any(key.startswith("_") for key in result.table[0])

    valid = stat_p_exact(context_for("The result was p = .051."))
    assert "invalid" not in valid.table[0]


def test_mixed_values_preserve_rows_and_report_both_problem_counts() -> None:
    imprecise_sentence = "Imprecise p-value example (p < .05)."
    zero_sentence = "Zero p-value example (p = .000)."
    result = stat_p_exact(
        context_for(
            imprecise_sentence,
            zero_sentence,
            "Exact p-value example (p = .0123).",
            "Small p-value example (p < .001).",
        )
    )

    assert [(row["text"], row["imprecise"], row["zero"]) for row in result.table] == [
        ("p < .05", True, False),
        ("p = .000", False, True),
        ("p = .0123", False, False),
        ("p < .001", False, False),
    ]
    assert result.table[0]["expanded"] == imprecise_sentence
    assert result.table[0]["paper_id"] == "test"
    assert result.table[0]["text_id"] == 1
    assert result.table[0]["paragraph_id"] == 1
    assert result.table[0]["section_id"] == 1
    assert result.table[0]["header"] == "Results"
    assert result.table[0]["section_type"] == "results"
    assert result.table[0]["page_number"] == 3
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 1, "n_zero": 1}]
    assert result.traffic_light == "red"
    assert result.summary_text == (
        "We found 1 imprecise *p* value and 1 *p* value reported as exactly zero "
        "out of 4 detected *p* values."
    )
    assert "Reporting *p* values imprecisely" in result.report
    assert "*P* values are never exactly zero." in result.report
    assert "p < .05" in result.report
    assert "p = .000" in result.report


def test_repeated_calls_use_cached_p_values_without_mutating_or_rescanning() -> None:
    context = context_for("The result was p < .05.")
    cached_p_values = context.p_values
    with pytest.raises(TypeError):
        cached_p_values[0]["cached_only_marker"] = "precomputed-p-values"
    cached_snapshot = tuple(dict(row) for row in cached_p_values)

    first = stat_p_exact(context)
    second = stat_p_exact(context)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert context.p_values is cached_p_values
    assert context.p_values == cached_snapshot
    assert "imprecise" not in context.p_values[0]
    assert "zero" not in context.p_values[0]
    assert first.table[0] is not context.p_values[0]
