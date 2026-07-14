from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn

from pytacheck.models import BibrPaper
from pytacheck.text import (
    assemble_paragraphs,
    extract_apa_tests,
    extract_equations,
    extract_p_values,
)


class FrozenMapping(Mapping[str, Any]):
    """Deeply immutable context row backed by read-only mapping composition."""

    __slots__ = ("_data",)
    _data: Mapping[str, Any]

    def __init__(self, values: Mapping[str, Any]) -> None:
        try:
            object.__getattribute__(self, "_data")
        except AttributeError:
            frozen = {str(key): _freeze(value) for key, value in values.items()}
            object.__setattr__(self, "_data", MappingProxyType(frozen))
            return
        raise TypeError("PaperContext rows are read-only")

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, key: str, value: object) -> NoReturn:
        del key, value
        raise TypeError("PaperContext rows are read-only")

    def __delitem__(self, key: str) -> NoReturn:
        del key
        raise TypeError("PaperContext rows are read-only")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PaperContext rows are read-only")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        raise TypeError("PaperContext rows are read-only")

    def __copy__(self) -> FrozenMapping:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenMapping:
        memo[id(self)] = self
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_rows(rows: tuple[dict[str, Any], ...]) -> tuple[FrozenMapping, ...]:
    return tuple(_freeze(row) for row in rows)


@dataclass(frozen=True, slots=True)
class PaperContext:
    paper_id: str
    sentences: tuple[FrozenMapping, ...]
    paragraphs: tuple[FrozenMapping, ...]
    p_values: tuple[FrozenMapping, ...]
    equations: tuple[FrozenMapping, ...]
    apa_tests: tuple[FrozenMapping, ...]

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
