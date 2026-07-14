import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import pytacheck.models as models_module
from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper

FIXTURE_DIR = Path(__file__).parents[1] / "contract" / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURE_DIR / name).read_text()))


@pytest.fixture
def golden_payload() -> dict[str, Any]:
    return _load_fixture("bibr_v10_2.json")


@pytest.mark.parametrize(
    ("fixture_name", "schema_version"),
    [("bibr_v10_2.json", "10.2"), ("bibr_v10_6.json", "10.6")],
)
def test_supported_bibr_fixtures_validate(fixture_name: str, schema_version: str) -> None:
    paper = BibrPaper.model_validate(_load_fixture(fixture_name))

    assert paper.paper_id
    assert paper.schema_version == schema_version
    assert paper.info["schema_version"] == schema_version


def test_context_joins_sections_and_adds_paper_id(golden_payload: dict[str, Any]) -> None:
    context = PaperContext.from_paper(BibrPaper.model_validate(golden_payload))

    assert context.paper_id == golden_payload["paper_id"]
    first = context.sentences[0]
    assert first["paper_id"] == golden_payload["paper_id"]
    assert first["header"] == golden_payload["section"][0]["header"]
    assert first["section_type"] == "title"
    assert first["page_number"] == golden_payload["text"][0]["page_number"]


def test_empty_context_retains_paper_id() -> None:
    paper = BibrPaper.model_validate(
        {"paper_id": "empty-context", "info": {"schema_version": "10.6"}}
    )

    context = PaperContext.from_paper(paper)

    assert context.paper_id == "empty-context"
    assert context.sentences == ()


def test_unknown_bibr_fields_do_not_break_validation(golden_payload: dict[str, Any]) -> None:
    golden_payload["future_table"] = [{"new": "field"}]
    golden_payload["text"][0]["future_location"] = {"column": 2}

    paper = BibrPaper.model_validate(golden_payload)
    context = PaperContext.from_paper(paper)

    assert paper.paper_id
    assert paper.model_extra is not None
    assert paper.model_extra["future_table"] == [{"new": "field"}]
    assert "future_location" not in context.sentences[0]


def test_schema_version_is_preferred_over_producer_version() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "paper",
            "info": {"schema_version": "10.6", "bibr_version": "99.0"},
        }
    )

    assert paper.schema_version == "10.6"


def test_legacy_bibr_version_is_used_only_for_10_x() -> None:
    legacy = BibrPaper.model_validate({"paper_id": "legacy", "info": {"bibr_version": "10.2"}})
    producer_only = BibrPaper.model_validate(
        {"paper_id": "producer", "info": {"bibr_version": "0.3.0"}}
    )

    assert legacy.schema_version == "10.2"
    assert legacy.info["schema_version"] == "10.2"
    assert producer_only.schema_version is None
    assert "schema_version" not in producer_only.info


def test_null_schema_version_falls_back_to_valid_legacy_bibr_version() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "legacy-null",
            "info": {"schema_version": None, "bibr_version": "10.2"},
        }
    )

    assert paper.schema_version == "10.2"
    assert paper.info["schema_version"] == "10.2"


def test_structural_text_and_list_limits_reject_pathological_papers() -> None:
    with pytest.raises(ValidationError, match="string_too_long"):
        BibrPaper.model_validate(
            {
                "paper_id": "long-text",
                "text": [
                    {
                        "text": "x" * (models_module.MAX_TEXT_CHARS + 1),
                        "text_id": 1,
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="too_long"):
        BibrPaper.model_validate(
            {
                "paper_id": "many-authors",
                "author": [{} for _ in range(models_module.MAX_AUTHORS + 1)],
            }
        )


def test_context_rows_are_deeply_read_only_and_drop_unknown_text_extras() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "immutable",
            "text": [
                {
                    "text": "The result was t(18) = 2.10, p = .050.",
                    "text_id": 1,
                    "paragraph_id": 1,
                    "section_id": 1,
                    "page_number": 3,
                    "unknown_large_payload": {"nested": ["secret"]},
                }
            ],
        }
    )

    context = PaperContext.from_paper(paper)

    assert "unknown_large_payload" not in context.sentences[0]
    with pytest.raises(TypeError):
        context.sentences[0]["text"] = "corrupted"
    with pytest.raises(TypeError):
        context.apa_tests[0]["df"][0] = 999


def test_newer_minor_schema_warns_but_validates() -> None:
    with pytest.warns(UserWarning, match=r"schema version 10\.7 is newer"):
        paper = BibrPaper.model_validate({"paper_id": "future", "info": {"schema_version": "10.7"}})

    assert paper.schema_version == "10.7"


def test_unsupported_schema_major_has_clear_validation_error() -> None:
    with pytest.raises(
        ValidationError,
        match=r"Unsupported bibr schema major version 11; supported major is 10",
    ):
        BibrPaper.model_validate({"paper_id": "future", "info": {"schema_version": "11.0"}})


def test_sentences_preserve_source_order() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "ordered",
            "info": {"schema_version": "10.6"},
            "section": [{"section_id": 1, "header": "Results", "section_type": "results"}],
            "text": [
                {"text": "First in source.", "text_id": 20, "paragraph_id": 1, "section_id": 1},
                {"text": "Second in source.", "text_id": 10, "paragraph_id": 1, "section_id": 1},
            ],
        }
    )

    assert [row["text_id"] for row in PaperContext.from_paper(paper).sentences] == [20, 10]


def test_paragraphs_group_in_source_order_and_keep_first_location() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "paragraphs",
            "info": {"schema_version": "10.6"},
            "section": [{"section_id": 3, "header": "Methods", "section_type": "method"}],
            "text": [
                {
                    "text": "First sentence.",
                    "text_id": 8,
                    "paragraph_id": 4,
                    "section_id": 3,
                    "page_number": 2,
                    "coordinates": {"x": 1},
                },
                {
                    "text": "Second sentence.",
                    "text_id": 9,
                    "paragraph_id": 4,
                    "section_id": 3,
                    "page_number": 3,
                    "coordinates": {"x": 2},
                },
            ],
        }
    )

    paragraph = PaperContext.from_paper(paper).paragraphs[0]
    assert paragraph["text"] == "First sentence. Second sentence."
    assert paragraph["text_id"] == 8
    assert paragraph["page_number"] == 2
    assert "coordinates" not in paragraph
    assert paragraph["header"] == "Methods"


def test_shared_features_are_cached_json_safe_tuples() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "cached",
            "info": {"schema_version": "10.6"},
            "section": [{"section_id": 1, "header": "Results", "section_type": "results"}],
            "text": [
                {
                    "text": "The result was t(18) = 2.10, p = .050.",
                    "text_id": 1,
                    "paragraph_id": 1,
                    "section_id": 1,
                }
            ],
        }
    )
    context = PaperContext.from_paper(paper)

    for name in ("sentences", "paragraphs", "p_values", "equations", "apa_tests"):
        first = getattr(context, name)
        second = getattr(context, name)
        assert isinstance(first, tuple)
        assert first is second
        json.dumps(first)


def test_context_rows_do_not_alias_the_pydantic_input() -> None:
    payload = {
        "paper_id": "copies",
        "info": {"schema_version": "10.6"},
        "text": [
            {
                "text": "A sentence.",
                "text_id": 1,
                "paragraph_id": 1,
                "section_id": 1,
                "metadata": {"nested": [1]},
            }
        ],
    }
    paper = BibrPaper.model_validate(deepcopy(payload))
    row = PaperContext.from_paper(paper).sentences[0]

    assert "metadata" not in row
    with pytest.raises(TypeError, match="read-only"):
        row["text"] = "changed"

    assert paper.text[0].model_extra is not None
    assert paper.text[0].model_extra["metadata"] == {"nested": [1]}
