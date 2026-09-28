"""REST API (port of metacheck's plumber API, ``inst/plumber``).

Routes, parameters, status codes and JSON shapes follow the plumber API so
existing clients keep working:

========================  ====================================================
``GET  /health``           liveness
``GET  /paper/modules``    modules this server can run
``POST /paper/info``       info table (``fields``: comma-separated)
``POST /paper/authors``    author table
``POST /paper/references`` bibliography
``POST /paper/cross-references``  in-text cross-references
``POST /paper/search``     ``text_search()`` (``pattern`` required; ``return``,
                           ``ignore.case``, ``fixed``, ``perl`` optional)
``POST /paper/module``     run one module (``name``)
``POST /paper/check``      metadata + several modules (``modules``, ``report``)
========================  ====================================================

Uploads are multipart ``file`` fields holding bibr JSON (max 50 MB). As a
pytacheck extension, PDF/DOCX/HTML uploads are accepted too when the
``bibr`` extra is installed (extracted in-process).

Module system (pytacheck extensions): the server runs built-in modules (bare
names) and the modules of active packs (``pack::name``); it always runs with
``use(allow_local=False)`` (no ``./name.py`` files, paths or path packs) and
never installs packs. ``/paper/check`` also takes ``preset``; without
``modules`` or ``preset`` it runs the preset configured for the server
(``PYTACHECK_PRESET`` or config), else every available module as plumber
does. Parsing uploads and running modules happen off the event loop, at
most ``PYTACHECK_API_MAX_CHECKS`` (default: the CPU count) at a time, each
request inside a run session.

Access (pytacheck extension): plumber has no authentication. Set
``PYTACHECK_API_KEY`` (at least 32 characters; a shorter one stops the server
from starting) and every route except ``GET /health`` needs the header
``Authorization: Bearer <key>``. Without a key the API is open, and
``pytacheck serve`` then binds only to a loopback address unless
``--behind-authenticating-proxy`` says something in front of it authenticates.
That bind check belongs to ``pytacheck serve``; ``uvicorn`` starts the app with
whatever host it is given.

Run with ``pytacheck serve`` or ``uvicorn pytacheck.api.app:create_app --factory``.
LLM configuration follows the plumber API: when ``GEMINI_API_KEY`` is set,
LLM use is switched on with ``METACHECK_LLM_MODEL`` (default
``google_gemini/gemini-3.1-flash-lite-preview``) and ``METACHECK_LLM_MAX_CALLS``
(default 200).
"""

from __future__ import annotations

import asyncio
import contextvars
import hmac
import ipaddress
import logging
import os
import tempfile
import uuid
import weakref
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from pytacheck.api.jsonlite import to_json

__all__ = [
    "API_KEY_ENV",
    "MAX_UPLOAD_BYTES",
    "MIN_API_KEY_LENGTH",
    "ApiConfigError",
    "api_key",
    "create_app",
    "is_loopback",
]

LOG = logging.getLogger("pytacheck.api")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_SOURCE_SUFFIXES = (".pdf", ".docx", ".doc", ".html", ".htm", ".epub")
API_KEY_ENV = "PYTACHECK_API_KEY"
MIN_API_KEY_LENGTH = 32
_OPEN_PATH = "/health"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ApiConfigError(ValueError):
    """The server is set up in a way it refuses to run with."""


def api_key() -> str | None:
    """The API key from ``PYTACHECK_API_KEY`` (surrounding whitespace dropped), or ``None``.

    A key shorter than 32 characters is an error, not a reason to run open.
    """
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not key:
        return None
    if len(key) < MIN_API_KEY_LENGTH:
        raise ApiConfigError(
            f"{API_KEY_ENV} has {len(key)} characters; it needs at least "
            f"{MIN_API_KEY_LENGTH}. Make one with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return key


def is_loopback(host: str) -> bool:
    """Whether a ``--host`` value only accepts connections from this machine."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:  # any other name could resolve to a public address
        return False


def available_modules() -> list[str]:
    """Module names the API accepts: every built-in module, then active packs' ``pack::name``.

    Local code (path packs, ``./name.py``, file paths) is never available.
    """
    from pytacheck.module import _builtin_names, use

    names = list(_builtin_names())
    try:
        from pytacheck.packs.registry import active_packs

        with use(allow_local=False):
            for pack in active_packs(allow_local=False).values():
                if pack.kind != "builtin":
                    names.extend(f"{pack.name}::{m}" for m in pack.modules())
    except Exception as exc:  # a broken config must not take the API down
        LOG.warning("packs unavailable: %s", exc)
    return names


def max_checks() -> int:
    """How many uploads are parsed / checked at once (``PYTACHECK_API_MAX_CHECKS``)."""
    try:
        value = int(os.environ.get("PYTACHECK_API_MAX_CHECKS") or 0)
    except ValueError:
        value = 0
    return value if value > 0 else (os.cpu_count() or 1)


def parse_bool(x: str | None, default: bool = True) -> bool:
    """``parse_bool()`` from the plumber helpers."""
    if x is None or not str(x).strip():
        return default
    v = str(x).strip().lower()
    if v in ("true", "t", "1", "yes", "y"):
        return True
    if v in ("false", "f", "0", "no", "n"):
        return False
    return default


def parse_modules(value: str | None) -> list[str]:
    if value is not None and value != "":
        return [m.strip() for m in value.split(",")]
    return available_modules()


def info_fields(paper: Any, fields: list[str]) -> Any:
    """``info_fields()``: selected info columns (missing ones as NA) plus paper_id."""
    import pandas as pd

    from pytacheck.papers.tables import paper_table

    tbl = paper_table(paper, "info")
    for col in fields:
        if col not in tbl.columns:
            tbl[col] = pd.Series([pd.NA] * len(tbl), dtype="string")
    keep = [c for c in dict.fromkeys(["paper_id", *fields]) if c in tbl.columns]
    return tbl.loc[:, keep]


def _configure_llm() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        LOG.info("GEMINI_API_KEY not set — LLM modules will use fallbacks")
        return
    try:
        from pytacheck.llm import llm_max_calls, llm_model, llm_use
    except ImportError:  # pragma: no cover - LLM support not installed
        LOG.warning("LLM support unavailable; LLM modules will use fallbacks")
        return
    llm_use(True)
    llm_model(
        os.environ.get("METACHECK_LLM_MODEL") or "google_gemini/gemini-3.1-flash-lite-preview"
    )
    llm_max_calls(int(os.environ.get("METACHECK_LLM_MAX_CALLS") or 200))
    LOG.info("LLM enabled: %s", llm_model())


def _json(content: Any, status: int = 200, unboxed: bool = False) -> Any:
    from fastapi.responses import Response

    return Response(
        content=to_json(content, unboxed=unboxed), status_code=status, media_type="application/json"
    )


def _error(status: int, message: str) -> Any:
    return _json({"error": message}, status=status, unboxed=True)


class _BearerAuth:
    """ASGI middleware: every route but ``/health`` needs ``Authorization: Bearer <key>``."""

    def __init__(self, app: ASGIApp, key: str) -> None:
        self.app = app
        self.key = key.encode()

    def allowed(self, scope: Scope) -> bool:
        for name, value in scope["headers"]:
            if name == b"authorization":
                scheme, _, token = value.strip().partition(b" ")
                # compare_digest takes the same time wherever a guess differs
                return scheme.lower() == b"bearer" and hmac.compare_digest(token.strip(), self.key)
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if (
            kind == "lifespan"
            or (kind == "http" and scope["path"] == _OPEN_PATH)
            or self.allowed(scope)
        ):
            await self.app(scope, receive, send)
        elif kind == "http":
            LOG.warning("Rejected %s %s: missing or wrong API key", scope["method"], scope["path"])
            response = _error(
                401, "Missing or invalid API key. Send 'Authorization: Bearer <key>'."
            )
            response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
        else:  # a websocket: refuse the handshake
            await send({"type": "websocket.close", "code": 1008})


def _read_upload(data: bytes, filename: str, request_id: str) -> Any:
    """``read_paper()``: parse an uploaded bibr JSON (or a document via bibr)."""
    import orjson

    from pytacheck.papers.io import from_bibr

    LOG.info("Reading paper: %s", request_id)
    suffix = Path(filename or "").suffix.lower()
    if suffix in _SOURCE_SUFFIXES:
        from pytacheck.io.bibr import bibr_available, chew

        if not bibr_available():
            raise ApiError(400, "This server cannot extract documents; upload bibr JSON instead.")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"upload{suffix}"
            path.write_bytes(data)
            return chew(path)
    try:
        return from_bibr(orjson.loads(data))
    except (orjson.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
        raise ApiError(400, str(exc)) from exc


def _check_selection(mp: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    """``(ref, args)`` for ``/paper/check``: modules, else a preset, else plumber's default."""
    from pytacheck.presets import default_preset, select

    if mp.get("modules"):
        return [(m, {}) for m in parse_modules(mp["modules"])]
    if mp.get("preset"):
        return list(select(preset=mp["preset"], use_config=True))
    _, source = default_preset(use_config=True)
    if source != "default":  # the server's configured preset
        return list(select(use_config=True))
    return [(m, {}) for m in available_modules()]


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    from starlette.concurrency import run_in_threadpool

    from pytacheck._version import __version__
    from pytacheck.module import run_session, use

    key = api_key()
    _configure_llm()
    limit = max_checks()
    semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
        weakref.WeakKeyDictionary()
    )

    def semaphore() -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = semaphores.get(loop)
        if sem is None:
            sem = semaphores[loop] = asyncio.Semaphore(limit)
        return sem

    app = FastAPI(
        title="metacheck API (pytacheck)",
        description=(
            "API for analyzing academic papers. Upload bibr JSON (from the bibr extraction "
            "pipeline) to extract metadata, authors, references, and run metacheck modules."
        ),
        version=__version__,
    )
    app.state.max_checks = limit
    if key is None:
        LOG.warning("%s is not set: the API accepts requests without a key", API_KEY_ENV)
    else:
        app.add_middleware(_BearerAuth, key=key)

    async def with_uploaded_paper(
        request: Request,
        endpoint: str,
        handler: Callable[[Any, dict[str, Any], str], Any],
        prevalidate: Callable[[dict[str, Any]], tuple[int, str] | None] | None = None,
    ) -> Any:
        request_id = str(uuid.uuid4())
        LOG.info("Request started (%s): %s", endpoint, request_id)
        try:
            form = await request.form()
        except Exception:
            return _error(400, "No file uploaded. Please use the 'file' field.")
        files = form.getlist("file")
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        uploads = [f for f in files if not isinstance(f, str)]
        if not uploads:
            return _error(400, "No file uploaded. Please use the 'file' field.")
        if len(uploads) > 1:
            return _error(400, "Please upload only one file at a time.")
        upload = uploads[0]
        data = await upload.read()  # type: ignore[union-attr]
        if len(data) > MAX_UPLOAD_BYTES:
            return _error(
                413, f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."
            )
        filename = getattr(upload, "filename", "") or ""

        def work() -> Any:
            # blocking: runs in a worker thread, never with local modules, in a run session
            with use(allow_local=False), run_session():
                if prevalidate is not None:
                    problem = prevalidate(fields)
                    if problem is not None:
                        return _error(*problem)
                try:
                    paper = _read_upload(data, filename, request_id)
                except ApiError as exc:
                    return _error(exc.status, exc.message)
                try:
                    result = handler(paper, fields, request_id)
                except ApiError as exc:
                    return _error(exc.status, exc.message)
                return result if hasattr(result, "status_code") else _json(result)

        async with semaphore():
            return await run_in_threadpool(contextvars.copy_context().run, work)

    @app.get("/health")
    def health() -> Any:
        return _json({"status": "ok", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    @app.get("/paper/modules")
    def modules() -> Any:
        with use(allow_local=False):
            mods = available_modules()
        return _json({"modules": mods, "count": len(mods)})

    @app.post("/paper/info")
    async def info(request: Request) -> Any:
        def handler(paper: Any, mp: dict[str, Any], _rid: str) -> Any:
            fields = (
                mp["fields"].split(",")
                if mp.get("fields") is not None
                else ["title", "keywords", "doi", "description"]
            )
            return info_fields(paper, fields)

        return await with_uploaded_paper(request, "info", handler)

    @app.post("/paper/authors")
    async def authors(request: Request) -> Any:
        from pytacheck.papers.tables import paper_table

        return await with_uploaded_paper(
            request, "authors", lambda p, _m, _r: paper_table(p, "author")
        )

    @app.post("/paper/references")
    async def references(request: Request) -> Any:
        return await with_uploaded_paper(request, "references", lambda p, _m, _r: p.bib)

    @app.post("/paper/cross-references")
    async def cross_references(request: Request) -> Any:
        return await with_uploaded_paper(request, "cross-references", lambda p, _m, _r: p.xref)

    @app.post("/paper/search")
    async def search(request: Request) -> Any:
        from pytacheck.text.search import text_search

        def pre(mp: dict[str, Any]) -> tuple[int, str] | None:
            if not mp.get("pattern"):
                return 400, "Query parameter 'pattern' is required"
            return None

        def handler(paper: Any, mp: dict[str, Any], _rid: str) -> Any:
            params: dict[str, Any] = {"pattern": mp["pattern"]}
            if mp.get("return") is not None:
                params["return_"] = mp["return"]
            for r_name, py_name in (
                ("ignore.case", "ignore_case"),
                ("fixed", "fixed"),
                ("perl", "perl"),
            ):
                if mp.get(r_name) is not None:
                    params[py_name] = parse_bool(mp[r_name], default=False)
            # `section`: the section type(s) to search, comma-separated (e.g.
            # "method,results"). metacheck forwards it to text_search(), which
            # has no such argument, so every request using it failed (U2).
            target: Any = paper
            if mp.get("section"):
                from pytacheck.text.search import _text_frame

                wanted = [x.strip().lower() for x in str(mp["section"]).split(",") if x.strip()]
                table, _ = _text_frame(paper)
                types = table["section_type"].astype("string").str.lower()
                table = table.loc[types.isin(wanted).fillna(False).astype(bool)]
                target = table.reset_index(drop=True)
                # searching the references is what asking for them means
                params["include_refs"] = "references" in wanted
            try:
                return text_search(target, **params)
            except (ValueError, TypeError) as exc:
                raise ApiError(500, str(exc)) from exc

        return await with_uploaded_paper(request, "search", handler, pre)

    @app.post("/paper/module")
    async def run_module(request: Request) -> Any:
        from pytacheck.module import module_find

        def pre(mp: dict[str, Any]) -> tuple[int, str] | None:
            name = mp.get("name")
            if not name:
                return 400, "Module name parameter 'name' is required"
            mods = available_modules()
            if name not in mods:
                return 400, f"Module '{name}' not found. Available modules: {', '.join(mods)}"
            return None

        def handler(paper: Any, mp: dict[str, Any], _rid: str) -> Any:
            spec = module_find(mp["name"])
            try:
                return spec.func(paper)
            except Exception as exc:
                raise ApiError(500, f"Error running module '{mp['name']}': {exc}") from exc

        return await with_uploaded_paper(request, "module", handler, pre)

    @app.post("/paper/check")
    async def check(request: Request) -> Any:
        from pytacheck.module import ModuleError, ModuleOutput, module_run
        from pytacheck.papers.tables import paper_table

        def pre(mp: dict[str, Any]) -> tuple[int, str] | None:
            mods = available_modules()
            invalid = [m for m in parse_modules(mp.get("modules")) if m not in mods]
            if invalid:
                return (
                    400,
                    f"Invalid modules: {', '.join(invalid)}. Available modules: {', '.join(mods)}",
                )
            if mp.get("preset") and not mp.get("modules"):
                try:
                    _check_selection(mp)
                except ModuleError as exc:
                    return 400, f"Invalid preset: {exc}"
            return None

        def handler(paper: Any, mp: dict[str, Any], request_id: str) -> Any:
            entries = _check_selection(mp)
            names = [str(ref) for ref, _ in entries]
            include_report = parse_bool(mp.get("report"), default=True)
            full: dict[str, ModuleOutput] = {}
            for name, args in zip(names, (a for _, a in entries), strict=True):
                try:
                    full[name] = module_run(paper, name, **args)
                except Exception as exc:
                    full[name] = ModuleOutput(
                        module=name,
                        title=name,
                        section="general",
                        summary_text="Error running module",
                        report=f"Error running module '{name}': {exc}",
                        traffic_light="fail",
                    )
            results = {
                name: {
                    "module": o.module,
                    "title": o.title,
                    "table": o.table,
                    "summary_table": o.summary_table,
                    "summary_text": o.summary_text,
                    "report": _report_text(o.report),
                    "traffic_light": o.traffic_light,
                }
                for name, o in full.items()
            }
            report_html = (
                _render_report_html(list(full.values()), paper, request_id)
                if include_report
                else ""
            )
            from pytacheck._version import UPSTREAM

            return {
                "metacheck_version": UPSTREAM["version"],
                "paper_info": info_fields(
                    paper, ["title", "keywords", "doi", "submission", "received", "accepted"]
                ),
                "authors": paper_table(paper, "author"),
                "references": paper.bib,
                "cross_references": paper.xref,
                "modules_run": names,
                "results": results,
                "report_html": report_html,
            }

        return await with_uploaded_paper(request, "check", handler, pre)

    return app


def _report_text(report: Any) -> Any:
    """Report blocks as JSON-safe values (tables become row lists)."""
    if isinstance(report, list):
        return [getattr(b, "data", b) for b in report]
    return report


def _render_report_html(outputs: list[Any], paper: Any, request_id: str) -> str:
    """The HTML report for already-computed module outputs ("" if unavailable)."""
    try:
        from pytacheck.report.report import render_module_outputs
    except ImportError:
        LOG.warning("report rendering unavailable (%s)", request_id)
        return ""
    try:
        return str(render_module_outputs(outputs, paper, output_format="html"))
    except Exception as exc:
        LOG.warning("report render failed (%s): %s", request_id, exc)
        return ""
