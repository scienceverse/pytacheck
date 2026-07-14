from __future__ import annotations

from copy import deepcopy

import pytest

from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import MODULE_REGISTRY, ModuleMetadata
from pytacheck.modules.marginal import marginal


def context_for(
    *texts: str,
    section_types: tuple[str, ...] | None = None,
) -> PaperContext:
    if section_types is None:
        section_types = ("results",) * len(texts)
    if len(section_types) != len(texts):
        raise ValueError("section_types must contain one value per text")

    paper = BibrPaper.model_validate(
        {
            "paper_id": "test",
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
                }
                for index, text in enumerate(texts, start=1)
            ],
        }
    )
    return PaperContext.from_paper(paper)


def test_marginal_is_registered_with_upstream_metadata() -> None:
    expected = ModuleMetadata(
        name="marginal",
        title="Marginal Significance",
        description="List all sentences that describe an effect as 'marginally significant'.",
        section="results",
        validated=True,
    )

    assert marginal.__pytacheck_metadata__ == expected
    assert MODULE_REGISTRY["marginal"].metadata == expected
    assert MODULE_REGISTRY["marginal"].function is marginal


def test_no_relevant_text_is_green() -> None:
    result = marginal(context_for("This effect is absent."))

    assert result.module == "marginal"
    assert result.title == "Marginal Significance"
    assert result.table == []
    assert result.summary_table == [{"paper_id": "test", "marginal": 0}]
    assert result.traffic_light == "green"
    assert result.summary_text == (
        "You described 0 effects with terms related to 'marginally significant'."
    )
    assert result.report == (
        "No effects were described with terms related to 'marginally significant'."
    )


def test_empty_text_paper_has_one_zero_summary_row_with_original_id() -> None:
    paper = BibrPaper.model_validate(
        {"paper_id": "empty-marginal", "info": {"schema_version": "10.6"}}
    )

    result = marginal(PaperContext.from_paper(paper))

    assert result.summary_table == [{"paper_id": "empty-marginal", "marginal": 0}]
    assert result.traffic_light == "green"


def test_upstream_marginal_and_approached_examples_are_red_and_counted() -> None:
    texts = (
        "This effect was marginally significant (p = .065).",
        "This effect approached significance (p = 0.34).",
    )

    result = marginal(context_for(*texts))

    assert result.traffic_light == "red"
    assert [row["text"] for row in result.table] == list(texts)
    assert result.summary_table == [{"paper_id": "test", "marginal": 2}]
    assert result.summary_text == (
        "You described 2 effects with terms related to 'marginally significant'."
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("The estimate was marginally significant.", id="marginal"),
        pytest.param("The estimate trended toward significance.", id="trend"),
        pytest.param("The estimate was almost conventionally significant.", id="almost"),
        pytest.param(
            "The estimate approached conventional statistical significance.",
            id="approach",
        ),
        pytest.param(
            "The estimate was borderline statistically significant.",
            id="border",
        ),
        pytest.param(
            "The estimate was close to conventional statistical significance.",
            id="close-to",
        ),
    ],
)
def test_each_upstream_phrase_family_is_detected(text: str) -> None:
    result = marginal(context_for(text))

    assert result.traffic_light == "red"
    assert [row["text"] for row in result.table] == [text]
    assert result.summary_table == [{"paper_id": "test", "marginal": 1}]


def test_reference_sentences_are_excluded() -> None:
    reference_text = "Prior work called this association marginally significant."
    result_text = "Our estimate approached significance."

    result = marginal(
        context_for(
            reference_text,
            result_text,
            section_types=("references", "results"),
        )
    )

    assert [row["text"] for row in result.table] == [result_text]
    assert result.table[0]["header"] == "Results"
    assert result.table[0]["section_type"] == "results"
    assert result.summary_table == [{"paper_id": "test", "marginal": 1}]


def test_match_preserves_sentence_and_bibr_location_fields() -> None:
    sentence = "The estimate was borderline significant."

    result = marginal(context_for(sentence))

    assert result.table == [
        {
            "text": sentence,
            "text_id": 1,
            "paragraph_id": 1,
            "section_id": 1,
            "page_number": 3,
            "paper_id": "test",
            "header": "Results",
            "section_type": "results",
        }
    ]


def test_marginal_does_not_mutate_context_sentences() -> None:
    context = context_for("The estimate was marginally significant.")
    original_sentences = context.sentences
    snapshot = deepcopy(original_sentences)

    result = marginal(context)

    assert context.sentences is original_sentences
    assert context.sentences == snapshot
    assert result.table[0] is not context.sentences[0]


def test_red_summary_and_report_include_upstream_guidance_and_match() -> None:
    sentence = "The estimate was close to statistical significance."

    result = marginal(context_for(sentence))

    assert result.summary_table == [{"paper_id": "test", "marginal": 1}]
    assert result.summary_text == (
        "You described 1 effect with terms related to 'marginally significant'."
    )
    assert result.report.startswith(
        "You described effects with terms related to 'marginally significant'. "
        "If *p* values above 0.05 are interpreted as an effect, you inflate the alpha level, "
        "and increase the Type 1 error rate. If a *p* value is higher than the prespecified "
        "alpha level, it should be interpreted as a non-significant result."
    )
    assert sentence in result.report
    assert "Results" in result.report
    assert "The Prevalence of Marginally Significant Results in Psychology Over Time" in (
        result.report
    )
    assert "10.1177/0956797619830326" in result.report
    assert "mchankins.wordpress.com/2013/04/21/still-not-significant-2/" in result.report
