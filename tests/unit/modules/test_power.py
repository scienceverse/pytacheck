from __future__ import annotations

from copy import deepcopy

import pytest

from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import MODULE_REGISTRY, ModuleMetadata
from pytacheck.modules.power import power

Paragraph = str | tuple[str, ...]


def context_for_paragraphs(
    *paragraphs: Paragraph,
    paper_id: str = "test",
    section_types: tuple[str, ...] | None = None,
) -> PaperContext:
    if section_types is None:
        section_types = ("methods",) * len(paragraphs)
    if len(section_types) != len(paragraphs):
        raise ValueError("section_types must contain one value per paragraph")

    text_rows: list[dict[str, object]] = []
    text_id = 0
    for paragraph_id, paragraph in enumerate(paragraphs, start=1):
        sentences = (paragraph,) if isinstance(paragraph, str) else paragraph
        for sentence in sentences:
            text_id += 1
            text_rows.append(
                {
                    "text": sentence,
                    "text_id": text_id,
                    "paragraph_id": paragraph_id,
                    "section_id": paragraph_id,
                    "page_number": paragraph_id + 2,
                    "coordinates": {"x": paragraph_id * 10.0, "y": paragraph_id * 20.0},
                    "source_marker": f"sentence-{text_id}",
                }
            )

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
            "text": text_rows,
        }
    )
    return PaperContext.from_paper(paper)


def test_power_is_registered_with_upstream_validated_method_metadata() -> None:
    expected = ModuleMetadata(
        name="power",
        title="Power Analysis Check",
        description=(
            "This module uses uses regular expressions to identify sentences that contain a "
            "statistical power analysis. If specified by the user, it also uses a large language "
            "module (LLM) to extract information reported in power analyses, including the "
            "statistical test, sample size, alpha level, desired level of power, and magnitude and "
            "type of effect size."
        ),
        section="method",
        validated=True,
    )

    assert power.__pytacheck_metadata__ == expected
    assert MODULE_REGISTRY["power"].metadata == expected
    assert MODULE_REGISTRY["power"].function is power


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "Participants held 12 power poses during warm-up.",
            id="ordinary-power-pose",
        ),
        pytest.param(
            "Statistical power was discussed conceptually.",
            id="statistical-power-without-number",
        ),
        pytest.param(
            "A power analysis was preregistered in 2020.",
            id="four-digit-year-is-not-a-number-gate",
        ),
        pytest.param(
            "This powerful sample-size analysis used 25 participants.",
            id="power-must-be-a-standalone-word",
        ),
        pytest.param(
            "The power was rated 8 by the panel.",
            id="power-without-analysis-phrase",
        ),
    ],
)
def test_each_detector_gate_rejects_non_candidates(text: str) -> None:
    result = power(context_for_paragraphs(text))

    assert result.module == "power"
    assert result.title == "Power Analysis Check"
    assert result.table == []
    assert result.summary_table == [{"paper_id": "test", "power_n": 0, "power_complete": None}]
    assert result.traffic_light == "na"
    assert result.summary_text == "No power analyses were detected."
    assert result.report.startswith(result.summary_text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("A power analysis used 32 participants.", id="power-analysis"),
        pytest.param(
            "The design had power for an effect size of 0.30 with 40 observations.",
            id="effect-size",
        ),
        pytest.param(
            "The study was powered for a small effect with 40 observations.",
            id="sized-effect",
        ),
        pytest.param(
            "A sample-size calculation targeted power for 64 participants.",
            id="sample-size",
        ),
        pytest.param("We used G*Power 3.1 to estimate 80% power.", id="g-power"),
        pytest.param("The pwr package estimated power for 80 participants.", id="pwr"),
        pytest.param("The study had 80% statistical power.", id="statistical-power"),
        pytest.param("The design had a power of .80 with 64 cases.", id="a-power-of"),
        pytest.param("Power = .80 for 64 cases.", id="power-equals"),
        pytest.param(
            "We needed 64 observations to detect an effect with 80% power.",
            id="to-detect",
        ),
        pytest.param(
            "We needed 64 observations to achieve 80% power.",
            id="achieve",
        ),
    ],
)
def test_upstream_power_analysis_phrase_families_are_detected(text: str) -> None:
    result = power(context_for_paragraphs(text))

    assert len(result.table) == 1
    assert result.table[0]["text"] == text
    assert result.table[0]["complete"] is None
    assert result.table[0]["power_id"] == 1
    assert result.summary_table == [{"paper_id": "test", "power_n": 1, "power_complete": None}]
    assert result.traffic_light == "yellow"


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("A power\nanalysis used 64 participants.", "unknown"),
        ("An a\tpriori power analysis used 64 participants.", "apriori"),
        ("A compromise   power analysis used 64 participants.", "compromise"),
    ],
    ids=("newline-detection", "tab-classification", "repeated-space-classification"),
)
def test_downstream_whitespace_is_normalized_without_changing_returned_paragraph(
    text: str,
    expected_type: str,
) -> None:
    result = power(context_for_paragraphs(text))

    assert len(result.table) == 1
    row = result.table[0]
    assert row["power_type"] == expected_type
    assert row["text"] == text
    assert row["text_id"] == 1
    assert row["paragraph_id"] == 1
    assert row["section_id"] == 1
    assert row["page_number"] == 3
    assert row["source_marker"] == "sentence-1"


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("An a priori power analysis used 64 participants.", "apriori"),
        ("An a-priori power analysis used 64 participants.", "apriori"),
        ("A sensitivity power analysis considered 64 participants.", "sensitivity"),
        ("A compromise power analysis considered 64 participants.", "compromise"),
        ("A post-hoc power analysis considered 64 participants.", "posthoc"),
        ("A post hoc power analysis considered 64 participants.", "posthoc"),
        ("An a posteriori power analysis considered 64 participants.", "posthoc"),
        ("A retrospective power analysis considered 64 participants.", "posthoc"),
        ("A power analysis considered 64 participants.", "unknown"),
    ],
)
def test_regex_classifier_covers_all_upstream_types(text: str, expected_type: str) -> None:
    result = power(context_for_paragraphs(text))

    assert [row["power_type"] for row in result.table] == [expected_type]


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("A sensitivity and a priori power analysis used 64 participants.", "apriori"),
        ("A compromise sensitivity power analysis used 64 participants.", "sensitivity"),
        ("A post-hoc compromise power analysis used 64 participants.", "compromise"),
    ],
)
def test_classifier_uses_documented_precedence(text: str, expected_type: str) -> None:
    result = power(context_for_paragraphs(text))

    assert [row["power_type"] for row in result.table] == [expected_type]


def test_year_plus_non_year_number_passes_numeric_gate() -> None:
    text = "A power analysis preregistered in 2020 planned for 80 participants."

    result = power(context_for_paragraphs(text))

    assert [row["text"] for row in result.table] == [text]
    assert result.traffic_light == "yellow"


def test_powered_word_form_is_accepted_but_powerful_is_not() -> None:
    powered = "The study was powered to detect an effect with 60 participants."
    powerful = "A powerful design was used to detect an effect in 60 participants."

    result = power(context_for_paragraphs(powered, powerful))

    assert [row["text"] for row in result.table] == [powered]


def test_sentences_in_one_paragraph_produce_one_apriori_row_like_upstream() -> None:
    apriori = (
        "An a priori power analysis for an independent samples t-test indicated that "
        "80% power required at least 64 participants in each group."
    )
    sensitivity = (
        "A sensitivity power analysis indicated that 64 participants in each group "
        "could detect an effect size of d = 0.5."
    )

    result = power(context_for_paragraphs((apriori, sensitivity)))

    assert len(result.table) == 1
    assert result.table[0]["text"] == f"{apriori} {sensitivity}"
    assert result.table[0]["power_type"] == "apriori"
    assert result.table[0]["power_id"] == 1
    assert result.summary_table == [{"paper_id": "test", "power_n": 1, "power_complete": None}]
    assert result.summary_text == "We detected 1 potential power analysis."
    assert result.traffic_light == "yellow"
    assert "please check for all required information manually" in result.report


def test_separate_paragraphs_produce_ordered_rows_and_plural_summary() -> None:
    apriori = "An a priori power analysis required 64 participants for 80% power."
    sensitivity = "A sensitivity power analysis used 64 participants and 80% power."

    result = power(context_for_paragraphs(apriori, sensitivity))

    assert [row["text"] for row in result.table] == [apriori, sensitivity]
    assert [row["power_type"] for row in result.table] == ["apriori", "sensitivity"]
    assert [row["power_id"] for row in result.table] == [1, 2]
    assert all(row["complete"] is None for row in result.table)
    assert result.summary_table == [{"paper_id": "test", "power_n": 2, "power_complete": None}]
    assert result.summary_text == "We detected 2 potential power analyses."
    assert result.traffic_light == "yellow"


def test_match_preserves_assembled_paragraph_and_bibr_location_fields() -> None:
    first = "An a priori power analysis was conducted."
    second = "It required 64 participants to achieve 80% power."

    result = power(context_for_paragraphs((first, second)))

    row = result.table[0]
    assert row["text"] == f"{first} {second}"
    assert row["paper_id"] == "test"
    assert row["text_id"] == 1
    assert row["paragraph_id"] == 1
    assert row["section_id"] == 1
    assert row["page_number"] == 3
    assert row["coordinates"] == {"x": 10.0, "y": 20.0}
    assert row["source_marker"] == "sentence-1"
    assert row["header"] == "Methods"
    assert row["section_type"] == "methods"


def test_reference_paragraphs_are_excluded() -> None:
    reference = "Prior authors reported an a priori power analysis with 64 participants."
    methods = "Our sensitivity power analysis used 80 participants."

    result = power(
        context_for_paragraphs(
            reference,
            methods,
            section_types=("references", "methods"),
        )
    )

    assert [row["text"] for row in result.table] == [methods]
    assert result.table[0]["header"] == "Methods"
    assert result.table[0]["section_type"] == "methods"
    assert result.summary_table == [{"paper_id": "test", "power_n": 1, "power_complete": None}]


def test_empty_text_paper_has_one_null_summary_row_with_original_id() -> None:
    paper = BibrPaper.model_validate(
        {"paper_id": "empty-power", "info": {"schema_version": "10.6"}}
    )

    result = power(PaperContext.from_paper(paper))

    assert result.table == []
    assert result.summary_table == [
        {"paper_id": "empty-power", "power_n": 0, "power_complete": None}
    ]
    assert result.traffic_light == "na"


def test_repeated_calls_use_cached_paragraphs_without_mutating_or_rescanning() -> None:
    context = context_for_paragraphs(
        "An a priori power analysis used 64 participants to achieve 80% power."
    )
    cached_paragraphs = context.paragraphs
    cached_paragraphs[0]["cached_only_marker"] = "precomputed-paragraphs"
    cached_snapshot = deepcopy(cached_paragraphs)

    first = power(context)
    second = power(context)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.table[0]["cached_only_marker"] == "precomputed-paragraphs"
    assert second.table[0]["cached_only_marker"] == "precomputed-paragraphs"
    assert context.paragraphs is cached_paragraphs
    assert context.paragraphs == cached_snapshot
    assert "power_type" not in context.paragraphs[0]
    assert "complete" not in context.paragraphs[0]
    assert "power_id" not in context.paragraphs[0]
    assert first.table[0] is not context.paragraphs[0]
