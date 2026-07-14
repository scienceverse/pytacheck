from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, NoReturn
from unittest.mock import patch

import pytest
from prometheus_client.parser import text_string_to_metric_families
from starlette.datastructures import FormData, UploadFile

from pytacheck import __version__

# Import the production boundary before importing FastAPI's test client. This keeps the
# intended RED signal (the missing API module) distinct from optional test-client tooling.
from pytacheck.api import MAX_UPLOAD_BYTES, create_app
from pytacheck.context import PaperContext
from pytacheck.engine import DEFAULT_MODULES, CheckEngine, CheckResponse
from pytacheck.models import BibrPaper
from pytacheck.modules.base import ModuleFunction, ModuleResult


@contextmanager
def api_client(engine: CheckEngine) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    with TestClient(create_app(engine)) as client:
        yield client


def module_result(name: str, *, traffic_light: str = "green") -> ModuleResult:
    return ModuleResult.model_validate(
        {
            "module": name,
            "title": name.replace("_", " ").title(),
            "table": [{"module": name}],
            "summary_table": [{"count": 1}],
            "summary_text": f"{name} completed",
            "report": f"{name} report",
            "traffic_light": traffic_light,
        }
    )


def make_module(
    name: str,
    calls: Counter[str],
    *,
    failure: str | None = None,
) -> ModuleFunction:
    def run(context: PaperContext) -> ModuleResult:
        del context
        calls[name] += 1
        if failure is not None:
            raise RuntimeError(failure)
        return module_result(name)

    return run


class RecordingEngine(CheckEngine):
    def __init__(self, registry: Mapping[str, ModuleFunction]) -> None:
        super().__init__(registry=registry)
        self.check_calls: list[tuple[str, tuple[str, ...] | None]] = []
        self.check_thread_ids: list[int] = []

    def check(
        self,
        paper: BibrPaper,
        modules: Sequence[str] | None = None,
    ) -> CheckResponse:
        self.check_calls.append((paper.paper_id, None if modules is None else tuple(modules)))
        self.check_thread_ids.append(threading.get_ident())
        return super().check(paper, modules)


def make_engine(
    calls: Counter[str] | None = None,
    *,
    extra_modules: Mapping[str, ModuleFunction] | None = None,
    reverse_defaults: bool = False,
) -> tuple[RecordingEngine, Counter[str]]:
    execution_counts: Counter[str] = Counter() if calls is None else calls
    default_names = tuple(reversed(DEFAULT_MODULES)) if reverse_defaults else DEFAULT_MODULES
    registry: dict[str, ModuleFunction] = {
        name: make_module(name, execution_counts) for name in default_names
    }
    if extra_modules is not None:
        registry.update(extra_modules)
    return RecordingEngine(registry), execution_counts


@pytest.fixture
def paper_payload() -> dict[str, Any]:
    return {
        "paper_id": "secret-paper-id",
        "info": {"schema_version": "10.6", "title": "API contract paper"},
        "text": [
            {
                "text": "A result was significant, p = .04.",
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


@pytest.fixture
def paper_bytes(paper_payload: dict[str, Any]) -> bytes:
    return json.dumps(paper_payload).encode("utf-8")


def assert_unboxed_error(response: Any, status_code: int, text: str | None = None) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert isinstance(payload["error"], str)
    if text is not None:
        assert text.lower() in payload["error"].lower()


def test_health_reports_liveness_and_package_version() -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
    assert engine.check_calls == []


def test_create_app_builds_an_engine_when_one_is_not_injected() -> None:
    engine, _ = make_engine()

    with patch("pytacheck.api.CheckEngine", return_value=engine) as constructor:
        app = create_app()

    assert app is not None
    constructor.assert_called_once_with()


def test_openapi_publishes_typed_native_json_request_body() -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    request_body = document["paths"]["/v1/checks"]["post"]["requestBody"]
    assert request_body["required"] is True
    request_schema = request_body["content"]["application/json"]["schema"]
    assert request_schema == {"$ref": "#/components/schemas/NativeCheckRequest"}

    schemas = document["components"]["schemas"]
    native_schema = schemas["NativeCheckRequest"]
    assert native_schema["additionalProperties"] is False
    assert native_schema["required"] == ["paper"]
    assert set(native_schema["properties"]) == {"paper", "modules", "render_html"}
    assert native_schema["properties"]["paper"] == {"$ref": "#/components/schemas/BibrPaper"}
    modules_array = next(
        option
        for option in native_schema["properties"]["modules"]["anyOf"]
        if option.get("type") == "array"
    )
    assert modules_array["items"] == {"type": "string"}
    assert native_schema["properties"]["render_html"]["type"] == "boolean"
    assert native_schema["properties"]["render_html"]["default"] is False
    assert schemas["BibrPaper"]["required"] == ["paper_id"]
    assert engine.check_calls == []


def test_ready_requires_every_validated_default_module() -> None:
    ready_engine, _ = make_engine()
    calls: Counter[str] = Counter()
    incomplete_engine = RecordingEngine(
        {name: make_module(name, calls) for name in DEFAULT_MODULES[:-1]}
    )

    with api_client(ready_engine) as client:
        ready_response = client.get("/ready")
    with api_client(incomplete_engine) as client:
        unavailable_response = client.get("/ready")

    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ready"
    assert unavailable_response.status_code == 503
    assert unavailable_response.json()["status"] != "ready"
    assert DEFAULT_MODULES[-1] in unavailable_response.json()["missing_modules"]
    assert ready_engine.check_calls == []
    assert incomplete_engine.check_calls == []


def test_module_catalog_matches_r_keys_and_has_deterministic_order() -> None:
    calls: Counter[str] = Counter()
    extras = {
        "zeta_experimental": make_module("zeta_experimental", calls),
        "alpha_experimental": make_module("alpha_experimental", calls),
    }
    engine, _ = make_engine(
        calls,
        extra_modules=extras,
        reverse_defaults=True,
    )

    with api_client(engine) as client:
        first = client.get("/paper/modules")
        second = client.get("/paper/modules")

    expected_modules = sorted((*DEFAULT_MODULES, *extras))
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json() == {
        "modules": expected_modules,
        "default_modules": list(DEFAULT_MODULES),
        "experimental_modules": sorted(extras),
        "count": len(expected_modules),
    }
    assert engine.check_calls == []


def test_platform_exact_multipart_request_with_only_file_runs_defaults(
    paper_bytes: bytes,
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["modules_run"] == list(DEFAULT_MODULES)
    assert list(payload["results"]) == list(DEFAULT_MODULES)
    assert payload["report_html"] == ""
    assert engine.check_calls == [("secret-paper-id", DEFAULT_MODULES)]
    assert calls == Counter(dict.fromkeys(DEFAULT_MODULES, 1))


def test_multipart_selection_is_trimmed_and_preserves_requested_order(
    paper_bytes: bytes,
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
            data={"modules": " stat_p_exact, power ", "report": "false"},
        )

    assert response.status_code == 200
    assert response.json()["modules_run"] == ["stat_p_exact", "power"]
    assert list(response.json()["results"]) == ["stat_p_exact", "power"]
    assert response.json()["report_html"] == ""
    assert calls == Counter({"stat_p_exact": 1, "power": 1})


@pytest.mark.parametrize("report_value", ["true", "1", "yes"])
def test_compatibility_report_request_never_renders_or_reruns_modules(
    paper_bytes: bytes,
    report_value: str,
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("secret-name.json", paper_bytes, "application/json")},
            data={"modules": "power", "report": report_value},
        )

    assert response.status_code == 200
    assert response.json()["report_html"] == ""
    assert calls == Counter({"power": 1})
    assert len(engine.check_calls) == 1


def test_json_compatibility_route_accepts_paper_and_query_selection(
    paper_payload: dict[str, Any],
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check?modules=marginal,stat_p_nonsig",
            json=paper_payload,
        )

    assert response.status_code == 200
    assert response.json()["modules_run"] == ["marginal", "stat_p_nonsig"]
    assert list(response.json()["results"]) == ["marginal", "stat_p_nonsig"]
    assert calls == Counter({"marginal": 1, "stat_p_nonsig": 1})


@pytest.mark.parametrize(
    ("selection", "error_text"),
    [
        ("power,does_not_exist", "does_not_exist"),
        ("power,power", "duplicate"),
        ("power,,marginal", "empty"),
    ],
)
def test_multipart_rejects_entire_invalid_selection_before_json_decode(
    selection: str,
    error_text: str,
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("broken.json", b"not-json", "application/json")},
            data={"modules": selection},
        )

    assert_unboxed_error(response, 400, error_text)
    assert engine.check_calls == []
    assert calls == Counter()


def test_json_compatibility_rejects_invalid_query_before_body_validation() -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check?modules=missing",
            content=b"{this is not json",
            headers={"content-type": "application/json"},
        )

    assert_unboxed_error(response, 400, "missing")
    assert engine.check_calls == []
    assert calls == Counter()


@pytest.mark.parametrize("selection", [None, "", "   "])
def test_empty_or_absent_multipart_selection_means_defaults(
    paper_bytes: bytes,
    selection: str | None,
) -> None:
    engine, calls = make_engine()
    data = {} if selection is None else {"modules": selection}

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
            data=data,
        )

    assert response.status_code == 200
    assert response.json()["modules_run"] == list(DEFAULT_MODULES)
    assert calls == Counter(dict.fromkeys(DEFAULT_MODULES, 1))


def test_paper_module_returns_one_unboxed_module_result(paper_bytes: bytes) -> None:
    calls: Counter[str] = Counter()
    extras = {"experimental": make_module("experimental", calls)}
    engine, _ = make_engine(calls, extra_modules=extras)

    with api_client(engine) as client:
        response = client.post(
            "/paper/module",
            files={"file": ("paper.json", paper_bytes, "application/json")},
            data={"name": "experimental"},
        )

    assert response.status_code == 200
    assert response.json() == module_result("experimental").model_dump(mode="json")
    assert calls == Counter({"experimental": 1})
    assert engine.check_calls == [("secret-paper-id", ("experimental",))]


@pytest.mark.parametrize(
    ("form_data", "error_text"),
    [({}, "name"), ({"name": ""}, "name"), ({"name": "missing"}, "missing")],
)
def test_paper_module_rejects_missing_or_unknown_names_before_paper_decode(
    form_data: dict[str, str],
    error_text: str,
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/module",
            files={"file": ("broken.json", b"not-json", "application/json")},
            data=form_data,
        )

    assert_unboxed_error(response, 400, error_text)
    assert engine.check_calls == []
    assert calls == Counter()


def test_missing_upload_is_a_compatibility_400() -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post("/paper/check")

    assert_unboxed_error(response, 400, "file")
    assert engine.check_calls == []


def test_upload_under_wrong_field_is_rejected(paper_bytes: bytes) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"paper": ("paper.json", paper_bytes, "application/json")},
        )

    assert_unboxed_error(response, 400, "file")
    assert engine.check_calls == []


def test_multiple_uploads_are_rejected(paper_bytes: bytes) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files=[
                ("file", ("first.json", paper_bytes, "application/json")),
                ("file", ("second.json", paper_bytes, "application/json")),
            ],
        )

    assert_unboxed_error(response, 400, "one file")
    assert engine.check_calls == []


def test_upload_limit_is_exactly_fifty_mebibytes_and_rejects_larger_body() -> None:
    engine, _ = make_engine()
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={
                "file": (
                    "oversized.json",
                    b"x" * (MAX_UPLOAD_BYTES + 1),
                    "application/json",
                )
            },
        )

    assert_unboxed_error(response, 413, "50")
    assert engine.check_calls == []


def test_native_body_is_stream_limited_before_envelope_validation() -> None:
    engine, _ = make_engine()
    oversized_invalid_json = b"{" + (b"x" * MAX_UPLOAD_BYTES)

    with api_client(engine) as client:
        response = client.post(
            "/v1/checks",
            content=oversized_invalid_json,
            headers={
                "content-type": "application/json",
                # The stream, not a caller-controlled header, is authoritative.
                "content-length": "1",
            },
        )

    assert_unboxed_error(response, 413, "50")
    assert engine.check_calls == []


def test_multipart_file_limit_is_enforced_before_oversize_bytes_are_spooled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = make_engine()
    written_bytes = 0
    original_write = UploadFile.write

    async def tracked_write(upload: UploadFile, data: bytes) -> None:
        nonlocal written_bytes
        written_bytes += len(data)
        await original_write(upload, data)

    monkeypatch.setattr("pytacheck.api.MAX_UPLOAD_BYTES", 128)
    monkeypatch.setattr(UploadFile, "write", tracked_write)

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("oversized.json", b"x" * 129, "application/json")},
        )

    assert response.status_code == 413
    assert written_bytes <= 128
    assert engine.check_calls == []


def test_multipart_parser_rejects_too_many_text_fields(paper_bytes: bytes) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files=[
                ("file", ("paper.json", paper_bytes, "application/json")),
                ("modules", (None, "power")),
                ("report", (None, "false")),
                ("extra", (None, "not allowed")),
            ],
        )

    assert_unboxed_error(response, 400, "fields")
    assert engine.check_calls == []


@pytest.mark.parametrize(
    "extra_part",
    [
        ("unexpected", (None, "text")),
        ("attachment", ("extra.json", b"{}", "application/json")),
    ],
)
def test_multipart_rejects_unexpected_parts(
    paper_bytes: bytes,
    extra_part: tuple[str, tuple[Any, ...]],
) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files=[
                ("file", ("paper.json", paper_bytes, "application/json")),
                extra_part,
            ],
        )

    assert_unboxed_error(response, 400)
    assert engine.check_calls == []


def test_multipart_parser_limits_text_field_bytes(paper_bytes: bytes) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
            data={"modules": "x" * (64 * 1024 + 1)},
        )

    assert_unboxed_error(response, 400, "maximum")
    assert engine.check_calls == []


def test_multipart_form_and_files_close_when_prevalidation_fails(
    paper_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = make_engine()
    closed_forms: list[FormData] = []
    closed_uploads: list[UploadFile] = []
    original_close = FormData.close

    async def tracked_close(form: FormData) -> None:
        closed_forms.append(form)
        closed_uploads.extend(
            value for _, value in form.multi_items() if isinstance(value, UploadFile)
        )
        await original_close(form)

    monkeypatch.setattr(FormData, "close", tracked_close)

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
            data={"modules": "missing"},
        )

    assert response.status_code == 400
    assert len(closed_forms) == 1
    assert len(closed_uploads) == 1
    assert closed_uploads[0].file.closed is True
    assert engine.check_calls == []


def test_unexpected_multipart_parser_failure_closes_spools_and_remains_a_500(
    paper_bytes: bytes,
) -> None:
    from fastapi.testclient import TestClient

    engine, _ = make_engine()
    app = create_app(engine)

    class FailingParser:
        def __init__(self) -> None:
            self.closed = False

        async def parse(self) -> NoReturn:
            raise RuntimeError("sensitive internal parser failure")

        def close_files(self) -> None:
            self.closed = True

    parser = FailingParser()
    with (
        patch("pytacheck.api._BoundedMultiPartParser", return_value=parser),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
        )
        metrics = client.get("/metrics")

    assert response.status_code == 500
    assert "sensitive internal parser failure" not in response.text
    assert parser.closed is True
    assert engine.check_calls == []
    assert (
        'pytacheck_http_requests_total{endpoint="/paper/check",outcome="server_error"} 1.0'
        in metrics.text
    )


@pytest.mark.parametrize(
    ("content", "content_type", "error_text"),
    [
        (b"{not-json", "application/json", "json"),
        (b"\xff", "application/json", "decode"),
        (b"{}", "text/plain", "content-type"),
    ],
)
def test_compatibility_decode_and_content_type_errors_are_unboxed(
    content: bytes,
    content_type: str,
    error_text: str,
) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            content=content,
            headers={"content-type": content_type},
        )

    assert_unboxed_error(response, 400, error_text)
    assert engine.check_calls == []


@pytest.mark.parametrize(
    "pathological_json",
    [
        b'{"paper_id":"paper","info":{"number":' + (b"9" * 5_000) + b"}}",
        (b"[" * 10_000) + b"0" + (b"]" * 10_000),
    ],
)
def test_compatibility_pathological_json_errors_remain_unboxed_400(
    pathological_json: bytes,
) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            content=pathological_json,
            headers={"content-type": "application/json"},
        )

    assert_unboxed_error(response, 400, "json")
    assert engine.check_calls == []


@pytest.mark.parametrize(
    "invalid_paper",
    [
        [],
        {"info": {"schema_version": "10.6"}},
        {"paper_id": "paper", "info": {"schema_version": "11.0"}},
    ],
)
def test_compatibility_paper_validation_errors_are_unboxed(
    invalid_paper: Any,
) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post("/paper/check", json=invalid_paper)

    assert_unboxed_error(response, 400)
    assert engine.check_calls == []


def test_native_json_envelope_preserves_selection_and_never_renders(
    paper_payload: dict[str, Any],
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/v1/checks",
            json={
                "paper": paper_payload,
                "modules": ["stat_p_nonsig", "power"],
                "render_html": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["modules_run"] == ["stat_p_nonsig", "power"]
    assert list(payload["results"]) == ["stat_p_nonsig", "power"]
    assert payload["report_html"] == ""
    assert calls == Counter({"stat_p_nonsig": 1, "power": 1})
    assert len(engine.check_calls) == 1


@pytest.mark.parametrize("modules", [None, []])
def test_native_empty_or_absent_selection_means_defaults(
    paper_payload: dict[str, Any],
    modules: list[str] | None,
) -> None:
    engine, calls = make_engine()
    envelope: dict[str, Any] = {"paper": paper_payload, "render_html": False}
    if modules is not None:
        envelope["modules"] = modules

    with api_client(engine) as client:
        response = client.post("/v1/checks", json=envelope)

    assert response.status_code == 200
    assert response.json()["modules_run"] == list(DEFAULT_MODULES)
    assert calls == Counter(dict.fromkeys(DEFAULT_MODULES, 1))


@pytest.mark.parametrize(
    ("modules", "error_text"),
    [
        (["power", "unknown"], "unknown"),
        (["power", "power"], "duplicate"),
        (["power", ""], "empty"),
    ],
)
def test_native_rejects_invalid_selection_without_running_valid_subset(
    paper_payload: dict[str, Any],
    modules: list[str],
    error_text: str,
) -> None:
    engine, calls = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/v1/checks",
            json={"paper": paper_payload, "modules": modules, "render_html": False},
        )

    assert_unboxed_error(response, 400, error_text)
    assert engine.check_calls == []
    assert calls == Counter()


@pytest.mark.parametrize(
    "invalid_envelope",
    [
        {},
        {"paper": {}},
        {"paper": {"paper_id": "paper"}, "modules": "power"},
        {"paper": {"paper_id": "paper"}, "render_html": "sometimes"},
        {"paper": {"paper_id": "paper"}, "unexpected": True},
    ],
)
def test_native_envelope_shape_errors_remain_pydantic_422(
    invalid_envelope: dict[str, Any],
) -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post("/v1/checks", json=invalid_envelope)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert engine.check_calls == []


def test_native_malformed_json_remains_pydantic_422() -> None:
    engine, _ = make_engine()

    with api_client(engine) as client:
        response = client.post(
            "/v1/checks",
            content=b"{not-json",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    assert "detail" in response.json()
    assert engine.check_calls == []


def test_engine_check_runs_off_the_event_loop_thread(paper_payload: dict[str, Any]) -> None:
    from fastapi.testclient import TestClient

    engine, _ = make_engine()
    app = create_app(engine)
    request_thread_ids: list[int] = []

    @app.middleware("http")
    async def record_event_loop_thread(request: Any, call_next: Callable[[Any], Any]) -> Any:
        request_thread_ids.append(threading.get_ident())
        return await call_next(request)

    with TestClient(app) as client:
        response = client.post(
            "/v1/checks",
            json={"paper": paper_payload, "modules": ["power"], "render_html": False},
        )

    assert response.status_code == 200
    assert len(request_thread_ids) == 1
    assert len(engine.check_thread_ids) == 1
    assert engine.check_thread_ids[0] != request_thread_ids[0]


@pytest.mark.parametrize("ingress", ["compatibility_json", "native_json", "multipart"])
def test_legal_request_parsing_runs_off_the_event_loop_thread(
    ingress: str,
    paper_payload: dict[str, Any],
    paper_bytes: bytes,
) -> None:
    from fastapi.testclient import TestClient

    import pytacheck.api as api_module

    engine, _ = make_engine()
    app = create_app(engine)
    event_loop_thread_ids: list[int] = []
    paper_parse_thread_ids: list[int] = []
    native_parse_thread_ids: list[int] = []
    original_parse_paper = api_module._parse_paper
    original_parse_native_request = api_module._parse_native_request

    def record_paper_parse(raw: bytes) -> BibrPaper:
        paper_parse_thread_ids.append(threading.get_ident())
        return original_parse_paper(raw)

    def record_native_parse(raw: bytes) -> api_module.NativeCheckRequest:
        native_parse_thread_ids.append(threading.get_ident())
        return original_parse_native_request(raw)

    @app.middleware("http")
    async def record_event_loop_thread(request: Any, call_next: Callable[[Any], Any]) -> Any:
        event_loop_thread_ids.append(threading.get_ident())
        return await call_next(request)

    with (
        patch.object(api_module, "_parse_paper", record_paper_parse),
        patch.object(api_module, "_parse_native_request", record_native_parse),
        TestClient(app) as client,
    ):
        if ingress == "compatibility_json":
            response = client.post("/paper/check?modules=power", json=paper_payload)
        elif ingress == "native_json":
            response = client.post(
                "/v1/checks",
                json={"paper": paper_payload, "modules": ["power"]},
            )
        else:
            response = client.post(
                "/paper/check",
                files={"file": ("paper.json", paper_bytes, "application/json")},
                data={"modules": "power"},
            )

    assert response.status_code == 200
    assert len(event_loop_thread_ids) == 1
    parse_thread_ids = (
        native_parse_thread_ids if ingress == "native_json" else paper_parse_thread_ids
    )
    assert len(parse_thread_ids) == 1
    assert parse_thread_ids[0] != event_loop_thread_ids[0]
    if ingress == "native_json":
        assert paper_parse_thread_ids == []
    else:
        assert native_parse_thread_ids == []


def test_metrics_use_app_local_registry_bounded_labels_and_no_sensitive_values(
    paper_payload: dict[str, Any],
) -> None:
    calls: Counter[str] = Counter()
    extras = {"broken": make_module("broken", calls, failure="secret-error-text")}
    first_engine, _ = make_engine(calls, extra_modules=extras)
    second_engine, _ = make_engine()

    # Constructing a second app must not collide with collectors from the first app.
    first_app = create_app(first_engine)
    second_app = create_app(second_engine)

    from fastapi.testclient import TestClient

    with TestClient(first_app) as client:
        checked = client.post(
            "/v1/checks",
            json={"paper": paper_payload, "modules": ["power", "broken"]},
        )
        first_metrics = client.get("/metrics")
        second_scrape = client.get("/metrics")
    with TestClient(second_app) as client:
        second_metrics = client.get("/metrics")

    assert checked.status_code == 200
    assert checked.json()["results"]["broken"]["traffic_light"] == "fail"
    assert first_metrics.status_code == 200
    assert second_metrics.status_code == 200
    assert first_metrics.headers["content-type"].startswith("text/plain")
    assert calls == Counter({"power": 1, "broken": 1})

    body = first_metrics.text
    assert "secret-paper-id" not in body
    assert "secret-name.json" not in body
    assert "secret-error-text" not in body
    assert second_scrape.status_code == 200
    assert calls == Counter({"power": 1, "broken": 1})

    families = list(text_string_to_metric_families(body))
    assert any(family.type == "counter" for family in families)
    assert any(family.type == "histogram" for family in families)

    samples = [sample for family in families for sample in family.samples]
    application_labels = {"endpoint", "module", "outcome"}
    for sample in samples:
        # Prometheus histograms add the reserved `le` bucket label themselves.
        assert set(sample.labels) - {"le"} <= application_labels

    assert any(
        "endpoint" in sample.labels and "outcome" in sample.labels
        for sample in samples
        if sample.name.endswith("_total")
    )
    assert any(
        "module" in sample.labels and "outcome" in sample.labels
        for sample in samples
        if sample.name.endswith("_total")
    )
    assert any(
        "endpoint" in sample.labels
        for family in families
        if family.type == "histogram"
        for sample in family.samples
    )
    assert any(
        "module" in sample.labels
        for family in families
        if family.type == "histogram"
        for sample in family.samples
    )
    assert any(
        sample.labels.get("module") == "broken" and "outcome" in sample.labels
        for sample in samples
        if sample.name.endswith("_total")
    )


def test_module_failure_is_isolated_at_api_boundary(paper_bytes: bytes) -> None:
    calls: Counter[str] = Counter()

    def broken(context: PaperContext) -> NoReturn:
        del context
        calls["broken"] += 1
        raise RuntimeError("boom")

    engine, _ = make_engine(calls, extra_modules={"broken": broken})

    with api_client(engine) as client:
        response = client.post(
            "/paper/check",
            files={"file": ("paper.json", paper_bytes, "application/json")},
            data={"modules": "broken,power"},
        )

    assert response.status_code == 200
    assert response.json()["modules_run"] == ["broken", "power"]
    assert response.json()["results"]["broken"]["traffic_light"] == "fail"
    assert response.json()["results"]["power"]["traffic_light"] == "green"
    assert calls == Counter({"broken": 1, "power": 1})
