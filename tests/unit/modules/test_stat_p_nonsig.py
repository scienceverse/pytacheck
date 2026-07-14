from __future__ import annotations

from copy import deepcopy

import pytest

from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import MODULE_REGISTRY, ModuleMetadata
from pytacheck.modules.stat_p_nonsig import stat_p_nonsig


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


def test_stat_p_nonsig_is_registered_with_validated_results_metadata() -> None:
    expected = ModuleMetadata(
        name="stat_p_nonsig",
        title="Non-Significant P Value Check",
        description=(
            "This module checks for imprecisely reported p values. If p > .05 is "
            "detected, it warns for misinterpretations."
        ),
        section="results",
        validated=True,
    )

    assert stat_p_nonsig.__pytacheck_metadata__ == expected
    assert MODULE_REGISTRY["stat_p_nonsig"].metadata == expected
    assert MODULE_REGISTRY["stat_p_nonsig"].function is stat_p_nonsig


@pytest.mark.parametrize(
    "text",
    [
        "This sentence reports no probability value.",
        "The findings were p = .001, p = .050, and p < .05.",
    ],
)
def test_no_p_or_only_significant_p_values_is_green(text: str) -> None:
    result = stat_p_nonsig(context_for(text))

    assert result.module == "stat_p_nonsig"
    assert result.title == "Non-Significant P Value Check"
    assert result.table == []
    assert result.summary_table == [{"paper_id": "test", "n_nonsignificant": 0}]
    assert result.traffic_light == "green"
    assert result.summary_text == "We detected no nonsignificant p values."
    assert result.report == result.summary_text


def test_empty_text_paper_has_one_zero_summary_row_with_original_id() -> None:
    paper = BibrPaper.model_validate(
        {"paper_id": "empty-nonsig", "info": {"schema_version": "10.6"}}
    )

    result = stat_p_nonsig(PaperContext.from_paper(paper))

    assert result.summary_table == [{"paper_id": "empty-nonsig", "n_nonsignificant": 0}]
    assert result.traffic_light == "green"


def test_exact_alpha_with_equal_or_less_than_is_significant() -> None:
    result = stat_p_nonsig(context_for("The tests gave p = .05 and p < .05."))

    assert result.table == []
    assert result.summary_table == [{"paper_id": "test", "n_nonsignificant": 0}]
    assert result.traffic_light == "green"


def test_invalid_probability_above_one_is_not_called_nonsignificant() -> None:
    result = stat_p_nonsig(context_for("The malformed report was p = 1.2."))

    assert result.table == []
    assert result.summary_table == [{"paper_id": "test", "n_nonsignificant": 0}]


def test_private_match_metadata_is_removed_from_public_nonsignificant_rows() -> None:
    result = stat_p_nonsig(context_for("The result was p > .05."))

    assert len(result.table) == 1
    assert not any(key.startswith("_") for key in result.table[0])


def test_non_numeric_above_alpha_and_unsupported_comparators_are_yellow() -> None:
    result = stat_p_nonsig(context_for("The tests gave p = .051, p > .05, p = n.s., and p ≠ .01."))

    assert [row["text"] for row in result.table] == [
        "p = .051",
        "p > .05",
        "p = n.s.",
        "p ≠ .01",
    ]
    assert [row["p_comp"] for row in result.table] == ["=", ">", "=", "≠"]
    assert [row["p_value"] for row in result.table] == [0.051, 0.05, None, 0.01]
    assert {row["significance"] for row in result.table} == {"nonsignificant"}
    assert result.summary_table == [{"paper_id": "test", "n_nonsignificant": 4}]
    assert result.traffic_light == "yellow"
    assert result.summary_text == (
        "We found 4 non-significant p values that should be checked for appropriate interpretation."
    )


def test_result_preserves_expansion_location_and_source_context() -> None:
    sentence = "The primary result was p = .051 and deserves careful interpretation."
    context = context_for(sentence)
    original_p_values = context.p_values
    original_snapshot = deepcopy(original_p_values)

    result = stat_p_nonsig(context)

    assert len(result.table) == 1
    row = result.table[0]
    assert row["text"] == "p = .051"
    assert row["expanded"] == sentence
    assert row["significance"] == "nonsignificant"
    assert row["paper_id"] == "test"
    assert row["text_id"] == 1
    assert row["paragraph_id"] == 1
    assert row["section_id"] == 1
    assert row["header"] == "Results"
    assert row["section_type"] == "results"
    assert row["page_number"] == 3
    assert result.summary_table == [{"paper_id": "test", "n_nonsignificant": 1}]
    assert result.summary_text == (
        "We found 1 non-significant p value that should be checked for appropriate interpretation."
    )
    assert "nonsignificant p values are commonly misinterpreted" in result.report
    assert "p = .051" in result.report
    assert sentence in result.report
    assert "Improving Your Statistical Inferences" in result.report

    assert context.p_values is original_p_values
    assert context.p_values == original_snapshot
    assert "significance" not in context.p_values[0]
    assert row is not context.p_values[0]
