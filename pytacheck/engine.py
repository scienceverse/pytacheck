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
from pytacheck.text import ExtractionLimitError

DEFAULT_MODULES = (
    "power",
    "marginal",
    "stat_check",
    "stat_effect_size",
    "stat_p_exact",
    "stat_p_nonsig",
)
MAX_MODULE_RESULT_ROWS = 5_000
MAX_MODULE_RESULT_BYTES = 1_000_000
MAX_AGGREGATE_RESULT_BYTES = 1_250_000
MAX_FAILURE_MESSAGE_CHARS = 512


class UnknownModuleError(ValueError):
    """Raised when a requested module is not registered."""


class DuplicateModuleError(ValueError):
    """Raised when a module is selected more than once."""


class ResultLimitError(ValueError):
    """Raised when a module result exceeds a configured output budget."""


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


def _bounded_message(message: str) -> str:
    if len(message) <= MAX_FAILURE_MESSAGE_CHARS:
        return message
    return f"{message[: MAX_FAILURE_MESSAGE_CHARS - 3]}..."


def _failure_result(name: str, title: str, message: str) -> ModuleResult:
    bounded = _bounded_message(message)
    return ModuleResult(
        module=name,
        title=title,
        table=[],
        summary_table=[],
        summary_text=bounded,
        report=bounded,
        traffic_light="fail",
    )


def _utf8_size(value: str, remaining: int) -> int:
    size = 2
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            size += 2
        elif codepoint < 0x20:
            size += 6
        elif codepoint < 0x80:
            size += 1
        elif codepoint < 0x800:
            size += 2
        elif codepoint < 0x10000:
            size += 3
        else:
            size += 4
        if size > remaining:
            return size
    return size


def _bounded_json_size(value: object, limit: int) -> int:
    """Estimate JSON bytes without first materializing an unbounded serialization."""

    total = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if item is None:
            total += 4
        elif isinstance(item, bool):
            total += 5
        elif isinstance(item, str):
            total += _utf8_size(item, limit - total)
        elif isinstance(item, int):
            bit_length = item.bit_length()
            decimal_digits = max(1, (bit_length * 30_103 + 99_999) // 100_000)
            total += decimal_digits + int(item < 0)
        elif isinstance(item, float):
            total += 32
        elif isinstance(item, Mapping):
            separators = max(0, (2 * len(item)) - 1)
            total += 2 + separators
            for key, nested in item.items():
                stack.append(str(key))
                stack.append(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            total += 2 + max(0, len(item) - 1)
            stack.extend(item)
        else:
            total += 64
        if total > limit:
            return limit + 1
    return total


def _module_result_size(result: ModuleResult) -> int:
    payload = {
        "module": result.module,
        "title": result.title,
        "table": result.table,
        "summary_table": result.summary_table,
        "summary_text": result.summary_text,
        "report": result.report,
        "traffic_light": result.traffic_light,
    }
    return _bounded_json_size(payload, MAX_MODULE_RESULT_BYTES)


def _checked_result(name: str, result: ModuleResult) -> tuple[ModuleResult, int]:
    row_count = len(result.table) + len(result.summary_table)
    if row_count > MAX_MODULE_RESULT_ROWS:
        raise ResultLimitError(f"module result row limit exceeded ({MAX_MODULE_RESULT_ROWS})")
    size = _module_result_size(result)
    if size > MAX_MODULE_RESULT_BYTES:
        raise ResultLimitError(f"module result byte budget exceeded ({MAX_MODULE_RESULT_BYTES})")
    if result.module != name:
        raise ValueError(f"Module {name!r} returned result for {result.module!r}")
    return result, size


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
        results: dict[str, ModuleResult] = {}
        timings_ms: dict[str, float] = {}
        aggregate_result_bytes = 0

        try:
            context = PaperContext.from_paper(paper)
        except ExtractionLimitError as exc:
            message = f"{type(exc).__name__}: {exc}"
            for name in selected:
                entry = self._registry[name]
                results[name] = _failure_result(name, entry.metadata.title, message)
                timings_ms[name] = 0.0
            return self._response(paper, selected, results, timings_ms)

        for name in selected:
            entry = self._registry[name]
            started = perf_counter()
            try:
                module_result, result_size = _checked_result(
                    name,
                    entry.function(context),
                )
                if aggregate_result_bytes + result_size > MAX_AGGREGATE_RESULT_BYTES:
                    raise ResultLimitError(
                        "aggregate module result byte budget exceeded "
                        f"({MAX_AGGREGATE_RESULT_BYTES})"
                    )
                aggregate_result_bytes += result_size
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                module_result = _failure_result(name, entry.metadata.title, message)
            finally:
                timings_ms[name] = (perf_counter() - started) * 1_000

            results[name] = module_result

        return self._response(paper, selected, results, timings_ms)

    @staticmethod
    def _response(
        paper: BibrPaper,
        selected: list[str],
        results: dict[str, ModuleResult],
        timings_ms: dict[str, float],
    ) -> CheckResponse:
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
    "MAX_AGGREGATE_RESULT_BYTES",
    "MAX_FAILURE_MESSAGE_CHARS",
    "MAX_MODULE_RESULT_BYTES",
    "MAX_MODULE_RESULT_ROWS",
    "CheckEngine",
    "CheckResponse",
    "DuplicateModuleError",
    "ResultLimitError",
    "UnknownModuleError",
]
