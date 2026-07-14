from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pytacheck import __version__
from pytacheck.context import PaperContext
from pytacheck.models import BibrPaper
from pytacheck.modules.base import (
    MODULE_REGISTRY,
    ModuleEntry,
    ModuleFunction,
    ModuleMetadata,
    ModuleResult,
)

DEFAULT_MODULES = (
    "power",
    "marginal",
    "stat_check",
    "stat_effect_size",
    "stat_p_exact",
    "stat_p_nonsig",
)


class UnknownModuleError(ValueError):
    """Raised when a requested module is not registered."""


class DuplicateModuleError(ValueError):
    """Raised when a module is selected more than once."""


class CheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    metacheck_version: str
    paper_info: dict[str, JsonValue] = Field(default_factory=dict)
    authors: list[dict[str, JsonValue]] = Field(default_factory=list)
    references: list[dict[str, JsonValue]] = Field(default_factory=list)
    cross_references: list[dict[str, JsonValue]] = Field(default_factory=list)
    modules_run: list[str] = Field(default_factory=list)
    results: dict[str, ModuleResult] = Field(default_factory=dict)
    report_html: str = ""
    timings_ms: dict[str, float] = Field(default_factory=dict)


RegistryValue = ModuleEntry | ModuleFunction


def _metadata_for_plain_function(name: str) -> ModuleMetadata:
    return ModuleMetadata(
        name=name,
        title=name,
        description="",
        section="",
        validated=False,
    )


def _normalize_registry(registry: Mapping[str, RegistryValue]) -> dict[str, ModuleEntry]:
    normalized: dict[str, ModuleEntry] = {}
    for name, value in registry.items():
        if isinstance(value, ModuleEntry):
            if value.metadata.name != name:
                raise ValueError(
                    f"Registry key {name!r} does not match module metadata name "
                    f"{value.metadata.name!r}"
                )
            normalized[name] = value
            continue

        metadata = getattr(value, "__pytacheck_metadata__", None)
        if not isinstance(metadata, ModuleMetadata) or metadata.name != name:
            metadata = _metadata_for_plain_function(name)
        normalized[name] = ModuleEntry(metadata=metadata, function=value)
    return normalized


def _duplicates(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


class CheckEngine:
    def __init__(self, registry: Mapping[str, RegistryValue] | None = None) -> None:
        source = MODULE_REGISTRY if registry is None else registry
        self._registry = _normalize_registry(source)

    def _selection(self, modules: Sequence[str] | None) -> list[str]:
        selected = list(DEFAULT_MODULES if modules is None else modules)

        unknown = [name for name in dict.fromkeys(selected) if name not in self._registry]
        if unknown:
            raise UnknownModuleError(f"Unknown modules: {', '.join(unknown)}")

        duplicates = _duplicates(selected)
        if duplicates:
            raise DuplicateModuleError(f"Duplicate modules: {', '.join(duplicates)}")

        return selected

    def check(
        self,
        paper: BibrPaper,
        modules: Sequence[str] | None = None,
    ) -> CheckResponse:
        selected = self._selection(modules)
        context = PaperContext.from_paper(paper)
        results: dict[str, ModuleResult] = {}
        timings_ms: dict[str, float] = {}

        for name in selected:
            entry = self._registry[name]
            started = perf_counter()
            try:
                module_result = entry.function(context)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                module_result = ModuleResult(
                    module=name,
                    title=entry.metadata.title,
                    table=[],
                    summary_table=[],
                    summary_text=message,
                    report=message,
                    traffic_light="fail",
                )
            finally:
                timings_ms[name] = (perf_counter() - started) * 1_000

            results[name] = module_result

        paper_payload = paper.model_dump(mode="json")
        return CheckResponse.model_validate(
            {
                "metacheck_version": __version__,
                "paper_info": paper_payload["info"],
                "authors": paper_payload["author"],
                "references": paper_payload["bib"],
                "cross_references": paper_payload["xref"],
                "modules_run": selected,
                "results": results,
                "report_html": "",
                "timings_ms": timings_ms,
            }
        )


__all__ = [
    "DEFAULT_MODULES",
    "CheckEngine",
    "CheckResponse",
    "DuplicateModuleError",
    "UnknownModuleError",
]
