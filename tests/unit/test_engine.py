from __future__ import annotations

import json
from collections.abc import Callable
from typing import NoReturn
from unittest.mock import patch

import pytest

from pytacheck import __version__
from pytacheck.context import PaperContext
from pytacheck.engine import (
    DEFAULT_MODULES,
    CheckEngine,
    DuplicateModuleError,
    UnknownModuleError,
)
from pytacheck.models import BibrPaper
from pytacheck.modules.base import (
    MODULE_REGISTRY,
    ModuleMetadata,
    ModuleResult,
    TrafficLight,
    register_module,
)


def minimal_paper() -> BibrPaper:
    return BibrPaper.model_validate(
        {
            "paper_id": "paper-1",
            "info": {"schema_version": "10.6", "title": "A paper"},
            "text": [
                {
                    "text": "A result was reported.",
                    "text_id": 1,
                    "paragraph_id": 1,
                    "section_id": 1,
                }
            ],
            "section": [{"section_id": 1, "header": "Results", "section_type": "body"}],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "bib": [{"bib_id": 1, "title": "A reference"}],
            "xref": [{"text_id": 1, "bib_id": 1}],
        }
    )


def result(module: str, *, traffic_light: TrafficLight = "green") -> ModuleResult:
    return ModuleResult(
        module=module,
        title=module.title(),
        table=[{"module": module}],
        summary_table=[{"count": 1}],
        summary_text="One result",
        report="All good",
        traffic_light=traffic_light,
    )


def test_default_modules_match_upstream_order() -> None:
    assert DEFAULT_MODULES == (
        "power",
        "marginal",
        "stat_check",
        "stat_effect_size",
        "stat_p_exact",
        "stat_p_nonsig",
    )


def test_register_module_attaches_metadata_and_creates_one_entry_shape() -> None:
    metadata = ModuleMetadata(
        name="decorated_test_module",
        title="Decorated",
        description="Used to exercise registration",
        section="test",
    )

    @register_module(metadata)
    def decorated(context: PaperContext) -> ModuleResult:
        del context
        return result(metadata.name)

    entry = MODULE_REGISTRY.pop(metadata.name)

    assert decorated.__pytacheck_metadata__ == metadata
    assert entry.metadata == metadata
    assert entry.function is decorated


def test_register_module_rejects_duplicate_names_without_replacing_original() -> None:
    metadata = ModuleMetadata(
        name="duplicate_test_module",
        title="Original",
        description="Used to exercise duplicate registration",
        section="test",
    )

    @register_module(metadata)
    def original(context: PaperContext) -> ModuleResult:
        del context
        return result(metadata.name)

    replacement_metadata = ModuleMetadata(
        name=metadata.name,
        title="Replacement",
        description="Must not replace the original",
        section="test",
    )

    try:
        with pytest.raises(ValueError, match=metadata.name):

            @register_module(replacement_metadata)
            def replacement(context: PaperContext) -> ModuleResult:
                del context
                return result(metadata.name)

        assert MODULE_REGISTRY[metadata.name].metadata == metadata
        assert MODULE_REGISTRY[metadata.name].function is original
    finally:
        MODULE_REGISTRY.pop(metadata.name, None)


def test_custom_registry_accepts_plain_module_functions() -> None:
    def custom(context: PaperContext) -> ModuleResult:
        del context
        return result("custom")

    response = CheckEngine(registry={"custom": custom}).check(minimal_paper(), ["custom"])

    assert response.results["custom"].module == "custom"


def test_engine_builds_one_context_and_preserves_requested_order() -> None:
    execution_order: list[str] = []
    contexts: list[PaperContext] = []

    def make_module(name: str) -> Callable[[PaperContext], ModuleResult]:
        def module(context: PaperContext) -> ModuleResult:
            execution_order.append(name)
            contexts.append(context)
            return result(name)

        return module

    registry = {"first": make_module("first"), "second": make_module("second")}
    with patch(
        "pytacheck.engine.PaperContext.from_paper", wraps=PaperContext.from_paper
    ) as context_factory:
        response = CheckEngine(registry=registry).check(minimal_paper(), ["second", "first"])

    assert context_factory.call_count == 1
    assert len(contexts) == 2
    assert contexts[0] is contexts[1]
    assert execution_order == ["second", "first"]
    assert response.modules_run == ["second", "first"]
    assert list(response.results) == ["second", "first"]
    assert list(response.timings_ms) == ["second", "first"]
    assert all(duration >= 0 for duration in response.timings_ms.values())


def test_engine_rejects_unknown_modules_before_context_or_execution() -> None:
    executed = False

    def known(context: PaperContext) -> ModuleResult:
        nonlocal executed
        executed = True
        return result("known")

    engine = CheckEngine(registry={"known": known})
    with (
        patch("pytacheck.engine.PaperContext.from_paper") as context_factory,
        pytest.raises(UnknownModuleError, match=r"missing.*also_missing"),
    ):
        engine.check(minimal_paper(), ["known", "missing", "also_missing"])

    context_factory.assert_not_called()
    assert executed is False


def test_engine_rejects_duplicate_modules_before_context_or_execution() -> None:
    executed = False

    def selected(context: PaperContext) -> ModuleResult:
        nonlocal executed
        executed = True
        return result("selected")

    engine = CheckEngine(registry={"selected": selected})
    with (
        patch("pytacheck.engine.PaperContext.from_paper") as context_factory,
        pytest.raises(DuplicateModuleError, match="selected"),
    ):
        engine.check(minimal_paper(), ["selected", "selected"])

    context_factory.assert_not_called()
    assert executed is False


def test_response_contains_json_serializable_compatibility_metadata() -> None:
    def successful(context: PaperContext) -> ModuleResult:
        del context
        return result("successful")

    paper = minimal_paper()
    response = CheckEngine(registry={"successful": successful}).check(paper, ["successful"])
    payload = response.model_dump(mode="json")

    assert payload["metacheck_version"] == __version__
    assert payload["paper_info"] == paper.info
    assert payload["authors"] == paper.author
    assert payload["references"] == paper.bib
    assert payload["cross_references"] == paper.xref
    assert payload["modules_run"] == ["successful"]
    assert list(payload["results"]) == ["successful"]
    assert payload["report_html"] == ""
    assert payload["timings_ms"]["successful"] >= 0
    json.dumps(payload)


def test_engine_contains_one_module_failure_and_continues() -> None:
    executed: list[str] = []

    def broken(context: PaperContext) -> NoReturn:
        del context
        executed.append("broken")
        raise RuntimeError("boom")

    def healthy(context: PaperContext) -> ModuleResult:
        del context
        executed.append("healthy")
        return result("healthy")

    engine = CheckEngine(registry={"broken": broken, "healthy": healthy})
    response = engine.check(minimal_paper(), ["broken", "healthy"])

    failure = response.results["broken"]
    assert executed == ["broken", "healthy"]
    assert failure.module == "broken"
    assert failure.traffic_light == "fail"
    assert failure.table == []
    assert failure.summary_table == []
    assert "boom" in failure.summary_text
    assert "boom" in failure.report
    assert response.results["healthy"].traffic_light == "green"
    assert list(response.timings_ms) == ["broken", "healthy"]


def test_engine_does_not_catch_base_exceptions() -> None:
    def interrupted(context: PaperContext) -> NoReturn:
        del context
        raise KeyboardInterrupt

    engine = CheckEngine(registry={"interrupted": interrupted})

    with pytest.raises(KeyboardInterrupt):
        engine.check(minimal_paper(), ["interrupted"])
