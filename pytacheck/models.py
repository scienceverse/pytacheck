from __future__ import annotations

import math
import re
import warnings
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_SCHEMA_MAJOR = 10
LATEST_KNOWN_SCHEMA_MINOR = 6
MAX_TEXT_CHARS = 200_000
MAX_TEXT_RECORDS = 50_000
MAX_SECTIONS = 10_000
MAX_AUTHORS = 1_000
MAX_REFERENCES = 100_000
MAX_CROSS_REFERENCES = 250_000
MAX_JSON_INTEGER_DIGITS = 640
MAX_JSON_NESTING_DEPTH = 64
MAX_SECTION_HEADER_CHARS = 512
MAX_SECTION_TYPE_CHARS = 128

_SCHEMA_VERSION_PATTERN = re.compile(r"^(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.\d+)*$")
_LEGACY_SCHEMA_VERSION_PATTERN = re.compile(r"^10\.\d+$")
_SURROGATE_PATTERN = re.compile("[\ud800-\udfff]")


def _mapping_children(value: Mapping[Any, Any]) -> Iterator[Any]:
    for key, nested in value.items():
        if not isinstance(key, str):
            raise ValueError("JSON object keys must be strings")
        yield key
        yield nested


def _validate_json_structure(value: Any) -> None:
    stack: list[tuple[Iterator[Any], int, int | None]] = [(iter((value,)), 0, None)]
    active_containers: set[int] = set()
    while stack:
        iterator, depth, container_id = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            if container_id is not None:
                active_containers.remove(container_id)
            continue
        if depth > MAX_JSON_NESTING_DEPTH:
            raise ValueError(f"JSON nesting depth limit exceeded ({MAX_JSON_NESTING_DEPTH})")
        if isinstance(item, str):
            if _SURROGATE_PATTERN.search(item) is not None:
                raise ValueError("Unpaired Unicode surrogate is not allowed")
            continue
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            decimal_digits = max(1, (item.bit_length() * 30_103 + 99_999) // 100_000)
            if decimal_digits > MAX_JSON_INTEGER_DIGITS:
                raise ValueError(f"JSON integer digit limit exceeded ({MAX_JSON_INTEGER_DIGITS})")
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("Non-finite JSON numbers are not allowed")
            continue
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active_containers:
                raise ValueError("Circular JSON containers are not allowed")
            active_containers.add(identity)
            stack.append((_mapping_children(item), depth + 1, identity))
            continue
        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in active_containers:
                raise ValueError("Circular JSON containers are not allowed")
            active_containers.add(identity)
            stack.append((iter(item), depth + 1, identity))
            continue
        raise ValueError(f"Unsupported JSON value type: {type(item).__name__}")


class TextRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str = Field(max_length=MAX_TEXT_CHARS)
    text_id: int
    paragraph_id: int | None = None
    section_id: int | None = None
    page_number: int | None = None
    formatted: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)


class SectionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    section_id: int
    header: str | None = Field(default=None, max_length=MAX_SECTION_HEADER_CHARS)
    section_type: str | None = Field(default=None, max_length=MAX_SECTION_TYPE_CHARS)


class BibrPaper(BaseModel):
    model_config = ConfigDict(extra="allow")

    paper_id: str
    info: dict[str, Any] = Field(default_factory=dict)
    text: list[TextRecord] = Field(default_factory=list, max_length=MAX_TEXT_RECORDS)
    section: list[SectionRecord] = Field(default_factory=list, max_length=MAX_SECTIONS)
    author: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_AUTHORS)
    bib: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_REFERENCES)
    xref: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_CROSS_REFERENCES,
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_schema_version(cls, value: Any) -> Any:
        _validate_json_structure(value)
        if not isinstance(value, Mapping):
            return value

        payload = dict(value)
        raw_info = payload.get("info")
        if raw_info is None:
            info: dict[str, Any] = {}
        elif isinstance(raw_info, Mapping):
            info = dict(raw_info)
        else:
            return value

        if info.get("schema_version") is None:
            legacy_version = info.get("bibr_version")
            if isinstance(legacy_version, str) and _LEGACY_SCHEMA_VERSION_PATTERN.fullmatch(
                legacy_version
            ):
                info["schema_version"] = legacy_version

        raw_schema_version = info.get("schema_version")
        if raw_schema_version is None:
            payload["info"] = info
            return payload

        schema_version = str(raw_schema_version)
        match = _SCHEMA_VERSION_PATTERN.fullmatch(schema_version)
        if match is None:
            raise ValueError(f"Invalid bibr schema version {schema_version!r}")

        major = int(match.group("major"))
        if major != SUPPORTED_SCHEMA_MAJOR:
            raise ValueError(
                f"Unsupported bibr schema major version {major}; "
                f"supported major is {SUPPORTED_SCHEMA_MAJOR}"
            )

        minor = int(match.group("minor") or 0)
        if minor > LATEST_KNOWN_SCHEMA_MINOR:
            warnings.warn(
                f"bibr schema version {schema_version} is newer than the latest "
                f"tested version {SUPPORTED_SCHEMA_MAJOR}.{LATEST_KNOWN_SCHEMA_MINOR}",
                UserWarning,
                stacklevel=2,
            )

        info["schema_version"] = schema_version
        payload["info"] = info
        return payload

    @property
    def schema_version(self) -> str | None:
        value = self.info.get("schema_version")
        return str(value) if value is not None else None


__all__ = [
    "LATEST_KNOWN_SCHEMA_MINOR",
    "MAX_AUTHORS",
    "MAX_CROSS_REFERENCES",
    "MAX_JSON_INTEGER_DIGITS",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_REFERENCES",
    "MAX_SECTIONS",
    "MAX_SECTION_HEADER_CHARS",
    "MAX_SECTION_TYPE_CHARS",
    "MAX_TEXT_CHARS",
    "MAX_TEXT_RECORDS",
    "SUPPORTED_SCHEMA_MAJOR",
    "BibrPaper",
    "SectionRecord",
    "TextRecord",
]
