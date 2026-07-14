from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pytacheck.models import BibrPaper
from pytacheck.text import (
    assemble_paragraphs,
    extract_apa_tests,
    extract_equations,
    extract_p_values,
)


@dataclass(frozen=True, slots=True)
class PaperContext:
    paper_id: str
    sentences: tuple[dict[str, Any], ...]
    paragraphs: tuple[dict[str, Any], ...]
    p_values: tuple[dict[str, Any], ...]
    equations: tuple[dict[str, Any], ...]
    apa_tests: tuple[dict[str, Any], ...]

    @classmethod
    def from_paper(cls, paper: BibrPaper) -> PaperContext:
        sections = {section.section_id: section for section in paper.section}
        sentence_rows: list[dict[str, Any]] = []

        for record in paper.text:
            row = deepcopy(record.model_dump(mode="json"))
            section = sections.get(record.section_id) if record.section_id is not None else None
            row["paper_id"] = paper.paper_id
            row["header"] = section.header if section is not None else None
            row["section_type"] = section.section_type if section is not None else None
            sentence_rows.append(row)

        sentences = tuple(sentence_rows)
        paragraphs = assemble_paragraphs(sentences)
        return cls(
            paper_id=paper.paper_id,
            sentences=sentences,
            paragraphs=paragraphs,
            p_values=extract_p_values(sentences),
            equations=extract_equations(sentences),
            apa_tests=extract_apa_tests(sentences),
        )
