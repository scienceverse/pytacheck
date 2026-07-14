from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from pytacheck.models import BibrPaper
from pytacheck.text import (
    assemble_paragraphs,
    extract_apa_tests,
    extract_equations,
    extract_p_values,
)


class FrozenDict(dict[str, Any]):
    """A JSON-compatible mapping whose contents cannot be changed after creation."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("PaperContext rows are read-only")

    def __delitem__(self, key: str, /) -> NoReturn:
        del key
        self._immutable()

    def __ior__(self, value: object, /) -> FrozenDict:  # type: ignore[override, misc]
        del value
        self._immutable()

    def __setitem__(self, key: str, value: Any, /) -> NoReturn:
        del key, value
        self._immutable()

    def clear(self) -> NoReturn:
        self._immutable()

    def pop(self, key: str, default: Any = None, /) -> NoReturn:
        del key, default
        self._immutable()

    def popitem(self) -> NoReturn:
        self._immutable()

    def setdefault(self, key: str, default: Any = None, /) -> NoReturn:
        del key, default
        self._immutable()

    def update(self, *args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        self._immutable()

    def __copy__(self) -> FrozenDict:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenDict:
        memo[id(self)] = self
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_rows(rows: tuple[dict[str, Any], ...]) -> tuple[FrozenDict, ...]:
    return tuple(_freeze(row) for row in rows)


@dataclass(frozen=True, slots=True)
class PaperContext:
    paper_id: str
    sentences: tuple[FrozenDict, ...]
    paragraphs: tuple[FrozenDict, ...]
    p_values: tuple[FrozenDict, ...]
    equations: tuple[FrozenDict, ...]
    apa_tests: tuple[FrozenDict, ...]

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


__all__ = ["FrozenDict", "PaperContext"]
