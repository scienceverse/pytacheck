from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, NoReturn, cast
from unittest.mock import patch

import pytest

import pytacheck.engine as engine_module
from pytacheck import __version__
from pytacheck.context import PaperContext
from pytacheck.engine import (
    DEFAULT_MODULES,
    CheckEngine,
    DuplicateModuleError,
    ModuleSelectionLimitError,
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


def encoded_results_size(response: engine_module.CheckResponse) -> int:
    payload = {
        name: module_result.model_dump(mode="json")
        for name, module_result in response.results.items()
    }
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
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


def test_engine_rejects_oversized_selection_before_context_or_execution() -> None:
    executed = False

    def make_module(name: str) -> Callable[[PaperContext], ModuleResult]:
        def selected(context: PaperContext) -> ModuleResult:
            nonlocal executed
            del context
            executed = True
            return result(name)

        return selected

    names = [f"module_{index}" for index in range(engine_module.MAX_SELECTED_MODULES + 1)]
    registry = {name: make_module(name) for name in names}

    with (
        patch("pytacheck.engine.PaperContext.from_paper") as context_factory,
        pytest.raises(ValueError, match="selection.*limit"),
    ):
        CheckEngine(registry=registry).check(minimal_paper(), names)

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


def test_repeated_source_amplification_returns_small_bounded_fail_results() -> None:
    sentence = ("p = .5; " * 1_000).ljust(9_000, "x")
    paper = BibrPaper.model_validate(
        {
            "paper_id": "amplification",
            "text": [{"text": sentence, "text_id": 1, "paragraph_id": 1}],
        }
    )

    response = CheckEngine().check(paper, ["stat_p_exact", "stat_p_nonsig"])
    encoded = response.model_dump_json()

    assert len(sentence) == 9_000
    assert all(result.traffic_light == "fail" for result in response.results.values())
    assert all(result.table == [] for result in response.results.values())
    assert len(encoded) < 10_000


def test_malformed_huge_exponent_does_not_break_unrelated_selected_module() -> None:
    exponent = "9" * 5_000
    paper = BibrPaper.model_validate(
        {
            "paper_id": "huge-exponent",
            "text": [
                {
                    "text": f"The result was marginally significant, p = .1e-{exponent}.",
                    "text_id": 1,
                    "paragraph_id": 1,
                }
            ],
        }
    )

    response = CheckEngine().check(paper, ["marginal"])

    assert response.results["marginal"].traffic_light == "red"


def test_mutating_failed_module_cannot_corrupt_later_module_context() -> None:
    observed_text: list[str] = []

    def mutating(context: PaperContext) -> ModuleResult:
        context.sentences[0]["text"] = "corrupted"
        return result("mutating")

    def observing(context: PaperContext) -> ModuleResult:
        observed_text.append(context.sentences[0]["text"])
        return result("observing")

    response = CheckEngine(registry={"mutating": mutating, "observing": observing}).check(
        minimal_paper(), ["mutating", "observing"]
    )

    assert response.results["mutating"].traffic_light == "fail"
    assert response.results["observing"].traffic_light == "green"
    assert observed_text == ["A result was reported."]


def test_inherited_dict_bypass_cannot_corrupt_later_module_context() -> None:
    observed_text: list[str] = []

    def mutating(context: PaperContext) -> ModuleResult:
        row = cast(dict[str, Any], context.sentences[0])
        dict.__setitem__(row, "text", "corrupted")
        return result("mutating")

    def observing(context: PaperContext) -> ModuleResult:
        observed_text.append(context.sentences[0]["text"])
        return result("observing")

    response = CheckEngine(registry={"mutating": mutating, "observing": observing}).check(
        minimal_paper(), ["mutating", "observing"]
    )

    assert response.results["mutating"].traffic_light == "fail"
    assert response.results["observing"].traffic_light == "green"
    assert observed_text == ["A result was reported."]


def test_object_setattr_cannot_replace_context_storage_for_later_modules() -> None:
    observed_text: list[str] = []

    def mutating(context: PaperContext) -> NoReturn:
        object.__setattr__(context.sentences[0], "_data", {"text": "corrupted"})
        raise RuntimeError("fail after mutation")

    def observing(context: PaperContext) -> ModuleResult:
        observed_text.append(context.sentences[0]["text"])
        return result("observing")

    response = CheckEngine(registry={"mutating": mutating, "observing": observing}).check(
        minimal_paper(), ["mutating", "observing"]
    )

    assert response.results["mutating"].traffic_light == "fail"
    assert response.results["observing"].traffic_light == "green"
    assert observed_text == ["A result was reported."]


def test_object_setattr_cannot_replace_context_fields_for_later_modules() -> None:
    observed_text: list[str] = []

    def mutating(context: PaperContext) -> NoReturn:
        object.__setattr__(context, "sentences", ({"text": "corrupted"},))
        raise RuntimeError("fail after mutation")

    def observing(context: PaperContext) -> ModuleResult:
        observed_text.append(context.sentences[0]["text"])
        return result("observing")

    response = CheckEngine(registry={"mutating": mutating, "observing": observing}).check(
        minimal_paper(), ["mutating", "observing"]
    )

    assert response.results["mutating"].traffic_light == "fail"
    assert response.results["observing"].traffic_light == "green"
    assert observed_text == ["A result was reported."]


def test_registry_rejects_module_names_that_discovery_cannot_render() -> None:
    def selected(context: PaperContext) -> ModuleResult:
        del context
        return result("unreachable")

    with pytest.raises(ModuleSelectionLimitError, match="Module name is not JSON-renderable"):
        CheckEngine(registry={"\ud800": selected})


@pytest.mark.parametrize("name", ["", " ", " leading", "trailing "])
def test_registry_rejects_module_names_that_api_normalization_cannot_select(name: str) -> None:
    def selected(context: PaperContext) -> ModuleResult:
        del context
        return result("unreachable")

    with pytest.raises(ModuleSelectionLimitError, match="non-empty.*surrounding whitespace"):
        CheckEngine(registry={name: selected})


def test_generic_module_row_budget_replaces_oversized_result_with_bounded_fail() -> None:
    def oversized(context: PaperContext) -> ModuleResult:
        del context
        module_result = result("oversized")
        module_result.table = [
            {"row": index} for index in range(engine_module.MAX_MODULE_RESULT_ROWS + 1)
        ]
        return module_result

    response = CheckEngine(registry={"oversized": oversized}).check(minimal_paper(), ["oversized"])

    assert response.results["oversized"].traffic_light == "fail"
    assert response.results["oversized"].table == []
    assert len(response.model_dump_json()) < 10_000


def test_generic_module_byte_budget_counts_json_escaping_conservatively() -> None:
    escaped_payload = '"' * ((engine_module.MAX_MODULE_RESULT_BYTES // 2) + 1_000)

    def escape_heavy(context: PaperContext) -> ModuleResult:
        del context
        module_result = result("escape_heavy")
        module_result.table = [{"payload": escaped_payload}]
        return module_result

    response = CheckEngine(registry={"escape_heavy": escape_heavy}).check(
        minimal_paper(), ["escape_heavy"]
    )

    assert response.results["escape_heavy"].traffic_light == "fail"
    assert response.results["escape_heavy"].table == []
    assert len(response.model_dump_json()) < 10_000


def test_generic_aggregate_budget_bounds_multiple_individually_legal_results() -> None:
    payload_size = engine_module.MAX_MODULE_RESULT_BYTES // 2

    def large(name: str) -> Callable[[PaperContext], ModuleResult]:
        def module(context: PaperContext) -> ModuleResult:
            del context
            module_result = result(name)
            module_result.table = [{"payload": "x" * payload_size}]
            return module_result

        return module

    registry = {name: large(name) for name in ("first", "second", "third")}
    response = CheckEngine(registry=registry).check(minimal_paper(), ["first", "second", "third"])

    assert response.results["third"].traffic_light == "fail"
    assert len(response.model_dump_json()) <= engine_module.MAX_AGGREGATE_RESULT_BYTES + 10_000


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(10**5_000, id="huge-integer"),
        pytest.param("\ud800", id="lone-surrogate"),
        pytest.param(object(), id="non-json-object"),
    ],
)
def test_renderer_hostile_module_payload_becomes_bounded_failure(payload: object) -> None:
    def hostile(context: PaperContext) -> ModuleResult:
        del context
        module_result = result("hostile")
        module_result.table = cast(Any, [{"payload": payload}])
        return module_result

    response = CheckEngine(registry={"hostile": hostile}).check(minimal_paper(), ["hostile"])

    assert response.results["hostile"].traffic_light == "fail"
    assert response.results["hostile"].table == []
    json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def test_every_module_failure_counts_toward_actual_aggregate_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"module_{index}" for index in range(8)]

    def broken(context: PaperContext) -> NoReturn:
        del context
        raise RuntimeError("x" * 10_000)

    monkeypatch.setattr(engine_module, "MAX_AGGREGATE_RESULT_BYTES", 2_000)
    response = CheckEngine(registry=dict.fromkeys(names, broken)).check(minimal_paper(), names)

    assert list(response.results) == names
    assert all(item.traffic_light == "fail" for item in response.results.values())
    assert encoded_results_size(response) <= engine_module.MAX_AGGREGATE_RESULT_BYTES


def test_shared_extraction_failures_count_toward_actual_aggregate_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"module_{index}" for index in range(8)]
    paper = BibrPaper.model_validate(
        {
            "paper_id": "shared-failure",
            "text": [
                {
                    "text": "p = .5; " * (engine_module.MAX_SELECTED_MODULES * 8),
                    "text_id": 1,
                    "paragraph_id": 1,
                }
            ],
        }
    )

    def unused(context: PaperContext) -> ModuleResult:
        raise AssertionError(f"shared extraction should fail first: {context.paper_id}")

    monkeypatch.setattr(engine_module, "MAX_AGGREGATE_RESULT_BYTES", 1_600)
    response = CheckEngine(registry=dict.fromkeys(names, unused)).check(paper, names)

    assert list(response.results) == names
    assert all(item.traffic_light == "fail" for item in response.results.values())
    assert encoded_results_size(response) <= engine_module.MAX_AGGREGATE_RESULT_BYTES


def test_multi_digit_invalid_probability_reaches_stat_p_exact() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "invalid-probability",
            "text": [{"text": "The malformed result was p = 10.2.", "text_id": 1}],
        }
    )

    response = CheckEngine().check(paper, ["stat_p_exact"])

    module_result = response.results["stat_p_exact"]
    assert module_result.traffic_light == "red"
    assert module_result.table[0]["p_value"] == 10.2
    assert module_result.table[0]["invalid"] is True
