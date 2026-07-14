from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pytacheck.context import PaperContext

TrafficLight = Literal["na", "info", "red", "yellow", "green", "fail"]


class ModuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    module: str
    title: str
    table: list[dict[str, JsonValue]] = Field(default_factory=list)
    summary_table: list[dict[str, JsonValue]] = Field(default_factory=list)
    summary_text: str
    report: str
    traffic_light: TrafficLight


ModuleFunction = Callable[[PaperContext], ModuleResult]


@dataclass(frozen=True, slots=True)
class ModuleMetadata:
    name: str
    title: str
    description: str
    section: str
    validated: bool = True


class RegisteredModuleFunction(Protocol):
    __pytacheck_metadata__: ModuleMetadata

    def __call__(self, context: PaperContext, /) -> ModuleResult: ...


@dataclass(frozen=True, slots=True)
class ModuleEntry:
    metadata: ModuleMetadata
    function: ModuleFunction


MODULE_REGISTRY: dict[str, ModuleEntry] = {}


def register_module(
    metadata: ModuleMetadata,
) -> Callable[[ModuleFunction], RegisteredModuleFunction]:
    def decorator(function: ModuleFunction) -> RegisteredModuleFunction:
        if metadata.name in MODULE_REGISTRY:
            raise ValueError(f"Module {metadata.name!r} is already registered")

        registered_function = cast(RegisteredModuleFunction, function)
        registered_function.__pytacheck_metadata__ = metadata
        MODULE_REGISTRY[metadata.name] = ModuleEntry(
            metadata=metadata,
            function=registered_function,
        )
        return registered_function

    return decorator
