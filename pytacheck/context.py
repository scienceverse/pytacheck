from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, NamedTuple

from pytacheck.models import BibrPaper
from pytacheck.text import (
    assemble_paragraphs,
    extract_apa_tests,
    extract_equations,
    extract_p_values,
)


def FrozenMapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a deeply immutable mapping backed by a fresh private dictionary."""

    frozen = {str(key): _freeze(value) for key, value in values.items()}
    return MappingProxyType(frozen)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_rows(rows: tuple[dict[str, Any], ...]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_freeze(row) for row in rows)


class PaperContext(NamedTuple):
    paper_id: str
    sentences: tuple[Mapping[str, Any], ...]
    paragraphs: tuple[Mapping[str, Any], ...]
    p_values: tuple[Mapping[str, Any], ...]
    equations: tuple[Mapping[str, Any], ...]
    apa_tests: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_paper(cls, paper: BibrPaper) -> PaperContext:
        sections = {section.section_id: section for section in paper.section}
        sentence_rows: list[dict[str, Any]] = []

        for record in paper.text:
            section = sections.get(record.section_id) if record.section_id is not None else None
            row = {
                "text": record.text,
                "text_id": record.text_id,
                "paragraph_id": record.paragraph_id,
                "section_id": record.section_id,
                "paper_id": paper.paper_id,
                "header": section.header if section is not None else None,
                "section_type": section.section_type if section is not None else None,
            }
            if "page_number" in record.model_fields_set:
                row["page_number"] = record.page_number
            if "formatted" in record.model_fields_set:
                row["formatted"] = record.formatted
            sentence_rows.append(row)

        sentences = _freeze_rows(tuple(sentence_rows))
        paragraphs = assemble_paragraphs(sentences)
        return cls(
            paper_id=paper.paper_id,
            sentences=sentences,
            paragraphs=_freeze_rows(paragraphs),
            p_values=_freeze_rows(extract_p_values(sentences)),
            equations=_freeze_rows(extract_equations(sentences)),
            apa_tests=_freeze_rows(extract_apa_tests(sentences)),
        )


__all__ = ["FrozenMapping", "PaperContext"]
