"""FastAPI ingress for Pytacheck's compatibility and native contracts.

The compatibility routes intentionally mirror the small part of Metacheck's
Plumber API used by Platform. HTML report rendering is not implemented in this
milestone: every successful check returns an empty ``report_html`` string.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from time import perf_counter
from typing import Any

import anyio
from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client.exposition import generate_latest
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.responses import JSONResponse, Response

from pytacheck import __version__
from pytacheck.engine import DEFAULT_MODULES, CheckEngine, CheckResponse
from pytacheck.models import BibrPaper

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_KNOWN_ENDPOINTS = frozenset(
    {
        "/health",
        "/ready",
        "/paper/modules",
        "/paper/module",
        "/paper/check",
        "/v1/checks",
        "/metrics",
    }
)


class NativeCheckRequest(BaseModel):
    """Typed request envelope for the versioned API."""

    model_config = ConfigDict(extra="forbid")

    paper: BibrPaper
    modules: list[str] | None = None
    render_html: StrictBool = False


class CompatibilityError(Exception):
    """An error that must retain the unboxed Plumber-compatible shape."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _error_response(error: CompatibilityError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"error": error.message})


def _available_modules(engine: CheckEngine) -> list[str]:
    return sorted(engine._registry)


def _validate_selection(engine: CheckEngine, names: Sequence[str]) -> list[str]:
    normalized = [name.strip() for name in names]
    if any(not name for name in normalized):
        raise CompatibilityError(400, "Empty module names are not allowed")

    try:
        return engine._selection(normalized)
    except ValueError as exc:
        raise CompatibilityError(400, str(exc)) from exc


def _csv_selection(engine: CheckEngine, raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return _validate_selection(engine, DEFAULT_MODULES)
    return _validate_selection(engine, raw.split(","))


def _native_selection(engine: CheckEngine, raw: Sequence[str] | None) -> list[str]:
    if not raw:
        return _validate_selection(engine, DEFAULT_MODULES)
    return _validate_selection(engine, raw)


def _parse_paper(raw: bytes) -> BibrPaper:
    if not raw:
        raise CompatibilityError(400, "Invalid JSON: request body is empty")

    try:
        payload = json.loads(raw)
    except UnicodeDecodeError as exc:
        raise CompatibilityError(400, f"Unable to decode JSON as UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CompatibilityError(400, f"Invalid JSON: {exc.msg}") from exc

    try:
        return BibrPaper.model_validate(payload)
    except ValidationError as exc:
        raise CompatibilityError(400, f"Invalid bibr paper: {exc}") from exc


async def _request_body_limited(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_UPLOAD_BYTES:
            raise CompatibilityError(
                413,
                "File too large. Maximum size is 50MB.",
            )
        body.extend(chunk)
    return bytes(body)


async def _multipart_form(request: Request) -> FormData:
    try:
        return await request.form()
    except Exception as exc:
        raise CompatibilityError(400, f"Invalid multipart form: {exc}") from exc


def _optional_form_text(form: FormData, name: str) -> str | None:
    value = form.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CompatibilityError(400, f"Form field '{name}' must be text")
    return value


async def _uploaded_paper(form: FormData) -> BibrPaper:
    uploads = form.getlist("file")
    if not uploads:
        raise CompatibilityError(400, "No file uploaded. Please use the 'file' field.")
    if len(uploads) > 1:
        raise CompatibilityError(400, "Please upload only one file at a time.")

    upload = uploads[0]
    if not isinstance(upload, UploadFile):
        raise CompatibilityError(400, "No file uploaded. Please use the 'file' field.")

    try:
        if upload.size is not None and upload.size > MAX_UPLOAD_BYTES:
            raise CompatibilityError(413, "File too large. Maximum size is 50MB.")
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await upload.close()

    if len(raw) > MAX_UPLOAD_BYTES:
        raise CompatibilityError(413, "File too large. Maximum size is 50MB.")
    return _parse_paper(raw)


def _request_outcome(status_code: int) -> str:
    if status_code < 400:
        return "success"
    if status_code < 500:
        return "client_error"
    return "server_error"


def create_app(engine: CheckEngine | None = None) -> FastAPI:
    """Create an isolated Pytacheck ASGI application.

    Each application owns its engine reference and Prometheus registry, which
    keeps factory use, tests, and multiple in-process app instances collision-free.
    """

    active_engine = CheckEngine() if engine is None else engine
    app = FastAPI(
        title="Pytacheck API",
        version=__version__,
        description=(
            "High-performance Metacheck-compatible research checks. "
            "HTML report rendering is not available; report_html is always empty."
        ),
    )

    registry = CollectorRegistry()
    request_count = Counter(
        "pytacheck_http_requests_total",
        "HTTP requests handled by Pytacheck.",
        ("endpoint", "outcome"),
        registry=registry,
    )
    request_latency = Histogram(
        "pytacheck_http_request_duration_seconds",
        "Pytacheck HTTP request duration.",
        ("endpoint",),
        registry=registry,
    )
    module_count = Counter(
        "pytacheck_module_runs_total",
        "Pytacheck module executions by traffic-light outcome.",
        ("module", "outcome"),
        registry=registry,
    )
    module_latency = Histogram(
        "pytacheck_module_duration_seconds",
        "Pytacheck module execution duration.",
        ("module",),
        registry=registry,
    )
    app.state.metrics_registry = registry

    @app.middleware("http")
    async def observe_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        endpoint = request.url.path if request.url.path in _KNOWN_ENDPOINTS else "unmatched"
        started = perf_counter()
        outcome = "server_error"
        try:
            response = await call_next(request)
            outcome = _request_outcome(response.status_code)
            return response
        finally:
            request_count.labels(endpoint=endpoint, outcome=outcome).inc()
            request_latency.labels(endpoint=endpoint).observe(perf_counter() - started)

    async def run_check(paper: BibrPaper, selection: list[str]) -> CheckResponse:
        response = await anyio.to_thread.run_sync(active_engine.check, paper, selection)
        if response.report_html:
            response = response.model_copy(update={"report_html": ""})

        for name in response.modules_run:
            result = response.results[name]
            module_count.labels(module=name, outcome=result.traffic_light).inc()
            duration_ms = response.timings_ms.get(name)
            if duration_ms is not None:
                module_latency.labels(module=name).observe(duration_ms / 1_000)
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    async def ready() -> Response:
        available = set(_available_modules(active_engine))
        missing = [name for name in DEFAULT_MODULES if name not in available]
        payload: dict[str, Any] = {
            "status": "ready" if not missing else "not_ready",
            "version": __version__,
            "missing_modules": missing,
        }
        return JSONResponse(status_code=200 if not missing else 503, content=payload)

    @app.get("/paper/modules")
    async def paper_modules() -> dict[str, Any]:
        modules = _available_modules(active_engine)
        defaults = list(DEFAULT_MODULES)
        return {
            "modules": modules,
            "default_modules": defaults,
            "experimental_modules": [name for name in modules if name not in DEFAULT_MODULES],
            "count": len(modules),
        }

    @app.post("/paper/module")
    async def paper_module(request: Request) -> Response:
        try:
            form = await _multipart_form(request)
            raw_name = _optional_form_text(form, "name")
            if raw_name is None or not raw_name.strip():
                raise CompatibilityError(400, "Module name parameter 'name' is required")
            selection = _validate_selection(active_engine, [raw_name])
            paper = await _uploaded_paper(form)
            response = await run_check(paper, selection)
        except CompatibilityError as exc:
            return _error_response(exc)
        return JSONResponse(content=response.results[selection[0]].model_dump(mode="json"))

    @app.post("/paper/check")
    async def paper_check(request: Request) -> Response:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        try:
            if media_type == "multipart/form-data":
                form = await _multipart_form(request)
                selection = _csv_selection(
                    active_engine,
                    _optional_form_text(form, "modules"),
                )
                paper = await _uploaded_paper(form)
            elif media_type == "application/json" or media_type.endswith("+json"):
                selection = _csv_selection(active_engine, request.query_params.get("modules"))
                paper = _parse_paper(await _request_body_limited(request))
            elif not media_type and request.headers.get("content-length", "0") == "0":
                raise CompatibilityError(
                    400,
                    "No file uploaded. Please use the 'file' field.",
                )
            else:
                raise CompatibilityError(
                    400,
                    "Unsupported Content-Type; use multipart/form-data or application/json",
                )

            # The compatibility `report` form field is accepted but intentionally ignored.
            response = await run_check(paper, selection)
        except CompatibilityError as exc:
            return _error_response(exc)
        return JSONResponse(content=response.model_dump(mode="json"))

    @app.post("/v1/checks", response_model=CheckResponse)
    async def native_checks(payload: NativeCheckRequest) -> CheckResponse | Response:
        try:
            selection = _native_selection(active_engine, payload.modules)
        except CompatibilityError as exc:
            return _error_response(exc)
        return await run_check(payload.paper, selection)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=generate_latest(registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    return app


__all__ = ["MAX_UPLOAD_BYTES", "NativeCheckRequest", "create_app"]
