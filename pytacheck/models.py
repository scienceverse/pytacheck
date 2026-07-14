from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_SCHEMA_MAJOR = 10
LATEST_KNOWN_SCHEMA_MINOR = 6

_SCHEMA_VERSION_PATTERN = re.compile(r"^(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.\d+)*$")
_LEGACY_SCHEMA_VERSION_PATTERN = re.compile(r"^10\.\d+$")


class TextRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    text_id: int
    paragraph_id: int | None = None
    section_id: int | None = None


class SectionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    section_id: int
    header: str | None = None
    section_type: str | None = None


class BibrPaper(BaseModel):
    model_config = ConfigDict(extra="allow")

    paper_id: str
    info: dict[str, Any] = Field(default_factory=dict)
    text: list[TextRecord] = Field(default_factory=list)
    section: list[SectionRecord] = Field(default_factory=list)
    author: list[dict[str, Any]] = Field(default_factory=list)
    bib: list[dict[str, Any]] = Field(default_factory=list)
    xref: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_schema_version(cls, value: Any) -> Any:
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

        if "schema_version" not in info:
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
