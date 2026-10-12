"""REST API (port of metacheck's plumber API, ``inst/plumber``).

Routes, parameters and status codes follow the plumber API:

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
``POST /package/check``    a data package: ``file`` (a zip or tar) or ``link`` (a repository)
========================  ====================================================

Responses are plain JSON (D79), not plumber's jsonlite encoding: scalars are
not wrapped in arrays, tables are arrays of row objects in which every column
appears (missing cells are ``null``), numbers keep full precision, and
``NaN``/``Inf`` are ``null``. An absent table is ``[]``.

Uploads are multipart ``file`` fields holding bibr JSON (max 50 MB). As a
pytacheck extension, PDF/DOCX/HTML uploads are accepted too when the
``bibr`` extra is installed (extracted in-process).

``POST /package/check`` takes exactly one of a ``file`` field (a zip or tar of a data
package) or a ``link`` field (the link of a public repository), and runs the
``datapackage`` checks under the hosted limits of ``metacheck.app.package.HOSTED``
(50 MB downloaded, 1 GB unpacked, no token of this server, concepts by rules). It answers
``name``, ``checks`` (``check``, ``status`` and ``result`` for each module) and
``report_html``.

Module system (pytacheck extensions): the server runs built-in modules (bare
names) and the modules of active packs (``pack::name``); it always runs with
``use(allow_local=False)`` (no ``./name.py`` files, paths or path packs) and
never installs packs. ``/paper/check`` also takes ``preset``; without
``modules`` or ``preset`` it runs the preset configured for the server
(``METACHECK_PRESET`` or ``PYTACHECK_PRESET``, or config), else every available
module as plumber does. Parsing uploads and running modules happen off the event
loop, at most ``METACHECK_API_MAX_CHECKS`` or ``PYTACHECK_API_MAX_CHECKS``
(default: the CPU count) at a time, each request inside a run session.

Access (pytacheck extension): plumber has no authentication. Set
``METACHECK_API_KEY`` or ``PYTACHECK_API_KEY`` (at least 32 characters; a shorter
one stops the server from starting) and every route except ``GET /health``
needs the header ``Authorization: Bearer <key>``. Without a key the API is open, and
``pytacheck serve`` then binds only to a loopback address unless
``--behind-authenticating-proxy`` says something in front of it authenticates.
That bind check belongs to ``pytacheck serve``; ``uvicorn`` starts the app with
whatever host it is given.

Run with ``pytacheck serve`` or ``uvicorn metacheck.api.app:create_app --factory``.
LLM configuration follows the plumber API: when ``GEMINI_API_KEY`` (or
``GOOGLE_API_KEY``) is set,
LLM use is switched on with ``METACHECK_LLM_MODEL`` (default
``google_gemini/gemini-3.1-flash-lite-preview``) and ``METACHECK_LLM_MAX_CALLS``
(default 200).
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import hmac
import ipaddress
import math
import os
import re
import shutil
import tempfile
import uuid
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from metacheck._env import env_get, env_lookup, env_names
from metacheck._logging import get_logger

if TYPE_CHECKING:
    from metacheck.app.package import Limits

__all__ = [
    "API_KEY_ENV",
    "API_KEY_ENVS",
    "MAX_UPLOAD_BYTES",
    "MIN_API_KEY_LENGTH",
    "ApiConfigError",
    "api_key",
    "create_app",
    "is_loopback",
]

LOG = get_logger("api")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_SOURCE_SUFFIXES = (".pdf", ".docx", ".doc", ".html", ".htm", ".epub")
#: the variables read for the API key, in order; ``API_KEY_ENV`` is the old name
API_KEY_ENVS = env_names("API_KEY")
API_KEY_ENV = API_KEY_ENVS[-1]
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
    """The API key from ``METACHECK_API_KEY`` or ``PYTACHECK_API_KEY`` (surrounding
    whitespace dropped), or ``None``.

    A key shorter than 32 characters is an error, not a reason to run open, even
    when the other variable holds a valid key: the error names the variable read.
    """
    hit = env_lookup("API_KEY")
    if hit is None:
        return None
    name, key = hit[0], hit[1].strip()
    if len(key) < MIN_API_KEY_LENGTH:
        raise ApiConfigError(
            f"{name} has {len(key)} characters; it needs at least "
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
    from metacheck.module import _builtin_names, use

    names = list(_builtin_names())
    try:
        from metacheck.packs.registry import active_packs

        with use(allow_local=False):
            for pack in active_packs(allow_local=False).values():
                if pack.kind != "builtin":
                    names.extend(f"{pack.name}::{m}" for m in pack.modules())
    except Exception as exc:  # a broken config must not take the API down
        LOG.warning("packs unavailable: %s", exc)
    return names


def max_checks() -> int:
    """How many uploads are parsed / checked at once (``METACHECK_API_MAX_CHECKS``).

    ``PYTACHECK_API_MAX_CHECKS`` works too; not set, or not a positive number: the CPU count.
    """
    try:
        value = int(env_get("API_MAX_CHECKS") or 0)
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

    from metacheck.papers.tables import paper_table

    tbl = paper_table(paper, "info")
    for col in fields:
        if col not in tbl.columns:
            tbl[col] = pd.Series([pd.NA] * len(tbl), dtype="string")
    keep = [c for c in dict.fromkeys(["paper_id", *fields]) if c in tbl.columns]
    return tbl.loc[:, keep]


def _configure_llm() -> None:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        LOG.info("GEMINI_API_KEY (or GOOGLE_API_KEY) not set — LLM modules will use fallbacks")
        return
    try:
        from metacheck.llm import llm_max_calls, llm_model, llm_use
    except ImportError:  # pragma: no cover - LLM support not installed
        LOG.warning("LLM support unavailable; LLM modules will use fallbacks")
        return
    llm_use(True)
    llm_model(env_get("LLM_MODEL") or "google_gemini/gemini-3.1-flash-lite-preview")
    llm_max_calls(int(env_get("LLM_MAX_CALLS") or 200))
    LOG.info("LLM enabled: %s", llm_model())


def _plain(x: Any) -> Any:
    """*x* as plain JSON values: tables become lists of row objects with every
    column, missing values and non-finite numbers ``None``."""
    import numpy as np
    import pandas as pd

    from metacheck.papers.model import Paper

    if x is None or isinstance(x, str | bool):
        return x
    if isinstance(x, np.generic):
        return _plain(x.item())
    if isinstance(x, int):
        return x if -(2**63) <= x < 2**64 else float(x)  # orjson's integer range
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, pd.DataFrame):
        cols = [str(c) for c in x.columns]
        return [
            {c: _plain(v) for c, v in zip(cols, row, strict=True)}
            for row in x.itertuples(index=False, name=None)
        ]
    if isinstance(x, Paper):
        return _plain(x.to_dict())
    if isinstance(x, Mapping):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, pd.Series | np.ndarray):
        return [_plain(v) for v in x.tolist()]
    if isinstance(x, list | tuple | set | frozenset):
        return [_plain(v) for v in x]
    if x is pd.NA or x is pd.NaT:
        return None
    if isinstance(x, dt.date | dt.time):
        return x.isoformat()
    data = getattr(x, "data", None)  # a report table block
    if isinstance(data, pd.DataFrame):
        return _plain(data)
    return str(x)


def _json(content: Any, status: int = 200) -> Any:
    from fastapi.responses import Response

    return Response(
        content=orjson.dumps(_plain(content)), status_code=status, media_type="application/json"
    )


def _table(x: Any) -> Any:
    """A paper table, ``[]`` when the paper has none."""
    return [] if x is None else x


def _error(status: int, message: str) -> Any:
    return _json({"error": message}, status=status)


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

    @staticmethod
    def is_open(scope: Scope) -> bool:
        path = scope["path"]
        root = scope.get("root_path", "")
        if root and path.startswith(root):  # behind a path prefix, as Starlette's router
            path = path[len(root) :]
        return bool(path == _OPEN_PATH)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan" or (kind == "http" and self.is_open(scope)) or self.allowed(scope):
            await self.app(scope, receive, send)
        elif kind == "http":
            # %r keeps a newline in the path from forging a log line
            LOG.warning("Rejected %s %r: missing or wrong API key", scope["method"], scope["path"])
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

    from metacheck.papers.io import from_bibr

    LOG.info("Reading paper: %s", request_id)
    suffix = Path(filename or "").suffix.lower()
    if suffix in _SOURCE_SUFFIXES:
        from metacheck.io.bibr import bibr_available, chew

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
    from metacheck.presets import default_preset, select

    if mp.get("modules"):
        return [(m, {}) for m in parse_modules(mp["modules"])]
    if mp.get("preset"):
        return list(select(preset=mp["preset"], use_config=True))
    _, source = default_preset(use_config=True)
    if source != "default":  # the server's configured preset
        return list(select(use_config=True))
    return [(m, {}) for m in available_modules()]


#: an upload is copied to disk this many bytes at a time
_UPLOAD_CHUNK = 1024 * 1024
#: what a multipart body holds beyond a file's own bytes: its headers, the boundary, a field
_FORM_SLACK = 1024 * 1024
#: the longest refusal text a data package returns (the library's sentences are short)
_PACKAGE_MESSAGE_MAX = 300
_PACKAGE_FORM = (
    "Send multipart/form-data with one 'file' (a zip or tar archive) or one 'link' "
    "(the link of a public repository)."
)
_PACKAGE_NEITHER = "Give a zip or tar archive as 'file', or the link of a repository as 'link'."
_PACKAGE_BOTH = "Give a file or a link, not both."
_PACKAGE_MANY = "Give one file or one link at a time."
_PACKAGE_NOT_LINK = "That is not the link of a repository this server can check."
_PACKAGE_UNREADABLE = "The archive could not be opened as a data package."
_PACKAGE_FAILED = "The package could not be checked."


def _megabytes(n: int) -> str:
    return f"{n / 1024**2:g} MB" if n >= 1024**2 else f"{n:,} bytes"


def _too_big(limit: int) -> Any:
    return _error(413, f"The package is bigger than the {_megabytes(limit)} this server checks.")


def _upload_name(filename: str | None) -> str:
    """The name an upload is kept under: its own base name when that is a plain one.

    The name only ever appears inside the temporary folder, and in the messages that name the
    archive; a name with a folder in it, a control character or an odd length gives ``upload``.
    """
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", "..") or len(name.encode()) > 200:
        return "upload"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "upload"
    return name


async def _store_upload(upload: Any, path: Path, limit: int) -> bool:
    """Copy *upload* to *path* a chunk at a time; ``False`` as soon as it is over *limit* bytes."""
    size = 0
    with open(path, "wb") as out:
        while chunk := await upload.read(_UPLOAD_CHUNK):
            size += len(chunk)
            if size > limit:
                return False
            out.write(chunk)
    return True


def _package_refusal(exc: Exception, folder: str) -> str:
    """The text of a refusal: short, and never the folder an upload was copied to."""
    text = " ".join(str(exc).split())
    if folder and folder in text:
        return _PACKAGE_UNREADABLE
    return text[:_PACKAGE_MESSAGE_MAX] or _PACKAGE_UNREADABLE


def _check_options(limits: Limits) -> dict[str, Any]:
    """The arguments of ``check_package`` on this server: the hosted limits, no token of this
    server, and the concepts named by rules (no classifier model is downloaded), as the hosted
    app runs a package."""
    return {
        "max_bytes": limits.unpacked,
        "max_files": limits.files,
        "max_ratio": limits.ratio,
        "max_depth": limits.depth,
        "max_download": limits.download,
        "download_timeout": limits.download_seconds,
        "credentials": False,
        "args": {"data_check": {"concepts": "rules"}},
    }


def _run_package(target: str, fallback: str, folder: str, limits: Limits, request_id: str) -> Any:
    """Check the package at *target* (an upload's copy, or a link) and answer for it.

    Runs in a worker thread, as the paper checks do: local modules are off and the run is in
    one run session. *folder* is the upload's folder (``""`` for a link), kept out of messages.
    """
    from metacheck.datapackage import PackageError, check_package
    from metacheck.datapackage._check import _stand_in_paper
    from metacheck.module import run_session, use

    try:
        with use(allow_local=False), run_session():
            chain = check_package(target, **_check_options(limits))
            name = _package_name(chain, fallback)
            report_html = _without_folders(
                _render_report_html(list(chain), _stand_in_paper(name), request_id)
            )
            checks = [
                {
                    "check": str(o.module),
                    "status": str(o.traffic_light),
                    "result": _without_folders(o.summary_text),
                }
                for o in chain
            ]
    except PackageError as exc:
        return _error(400, _package_refusal(exc, folder))
    except Exception as exc:
        LOG.error("Package check failed (%s): %s", request_id, type(exc).__name__)
        return _error(500, _PACKAGE_FAILED)
    return _json({"name": name, "checks": checks, "report_html": report_html})


def _without_folders(text: Any) -> Any:
    """*text* with this server's temporary package folders taken out of it.

    A module can name the folder it was given: data_check's report names each repository by its
    local path, which is the folder the package was unpacked to. Those folders (the library's
    ``metacheck-package-`` folder and the upload's own) are shown as ``package`` instead.
    """
    if not isinstance(text, str):
        return text
    prefix = re.escape(tempfile.gettempdir().rstrip("/"))
    folders = rf"{prefix}/(?:metacheck-package-[^/\s\"'<>]+/package|pytacheck-upload-[^/\s\"'<>]+)"
    return re.sub(folders, "package", text)


def _package_name(chain: Any, fallback: str) -> str:
    """The package's name from its run record; *fallback* (the file's name) if it has none."""
    package = getattr(getattr(chain, "run_record", None), "package", None) or {}
    name = package.get("name") if isinstance(package, dict) else None
    return str(name) if name else (fallback or "package")


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    from starlette.concurrency import run_in_threadpool

    from metacheck._version import __version__
    from metacheck.module import run_session, use

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
        return _json({"status": "ok", "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

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
        from metacheck.papers.tables import paper_table

        return await with_uploaded_paper(
            request, "authors", lambda p, _m, _r: paper_table(p, "author")
        )

    @app.post("/paper/references")
    async def references(request: Request) -> Any:
        return await with_uploaded_paper(request, "references", lambda p, _m, _r: _table(p.bib))

    @app.post("/paper/cross-references")
    async def cross_references(request: Request) -> Any:
        return await with_uploaded_paper(
            request, "cross-references", lambda p, _m, _r: _table(p.xref)
        )

    @app.post("/paper/search")
    async def search(request: Request) -> Any:
        from metacheck.text.search import text_search

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
                from metacheck.text.search import sentence_table

                wanted = [x.strip().lower() for x in str(mp["section"]).split(",") if x.strip()]
                table, _ = sentence_table(paper)
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
        from metacheck.module import module_find

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
        from metacheck.module import ModuleError, ModuleOutput, module_run
        from metacheck.papers.tables import paper_table

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
            from metacheck._version import UPSTREAM

            return {
                "metacheck_version": UPSTREAM["version"],
                "paper_info": info_fields(
                    paper, ["title", "keywords", "doi", "submission", "received", "accepted"]
                ),
                "authors": paper_table(paper, "author"),
                "references": _table(paper.bib),
                "cross_references": _table(paper.xref),
                "modules_run": names,
                "results": results,
                "report_html": report_html,
            }

        return await with_uploaded_paper(request, "check", handler, pre)

    @app.post("/package/check")
    async def package_check(request: Request) -> Any:
        from metacheck.app import package as hosted
        from metacheck.datapackage import PackageError, is_link, parse_link
        from metacheck.datapackage._remote import _normalise

        request_id = str(uuid.uuid4())
        LOG.info("Request started (package check): %s", request_id)
        limits = hosted.HOSTED  # read per request, so the limits can be changed in one place
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limits.download + _FORM_SLACK:
            return _too_big(limits.download)  # refused before the body is spooled to disk
        try:
            form = await request.form()
        except Exception:
            return _error(400, _PACKAGE_FORM)
        files, fields = form.getlist("file"), form.getlist("link")
        if any(isinstance(f, str) for f in files) or any(not isinstance(v, str) for v in fields):
            return _error(400, _PACKAGE_FORM)
        uploads = [f for f in files if not isinstance(f, str)]
        links = [str(v).strip() for v in fields if str(v).strip()]
        if uploads and links:
            return _error(400, _PACKAGE_BOTH)
        if len(uploads) > 1 or len(links) > 1:
            return _error(400, _PACKAGE_MANY)

        async def checked_upload(upload: Any) -> Any:
            name = _upload_name(getattr(upload, "filename", None))
            folder = tempfile.mkdtemp(prefix="pytacheck-upload-")
            try:
                path = Path(folder) / name
                if not await _store_upload(upload, path, limits.download):
                    return _too_big(limits.download)
                async with semaphore():
                    return await run_in_threadpool(
                        contextvars.copy_context().run,
                        _run_package,
                        str(path),
                        name,
                        folder,
                        limits,
                        request_id,
                    )
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        async def checked_link(text: str) -> Any:
            # check_package reads a value that is not a web link as a folder or archive path, so
            # the text is read the way parse_link reads it and passed on in that https form
            link = _normalise(text)
            try:
                parse_link(link)
            except PackageError as exc:
                return _error(400, _package_refusal(exc, ""))
            if not is_link(link):
                return _error(400, _PACKAGE_NOT_LINK)
            async with semaphore():
                return await run_in_threadpool(
                    contextvars.copy_context().run,
                    _run_package,
                    link,
                    "package",
                    "",
                    limits,
                    request_id,
                )

        if uploads:
            return await checked_upload(uploads[0])
        if links:
            return await checked_link(links[0])
        return _error(400, _PACKAGE_NEITHER)

    return app


def _report_text(report: Any) -> Any:
    """Report blocks as JSON-safe values (tables become row lists)."""
    if isinstance(report, list):
        return [getattr(b, "data", b) for b in report]
    return report


def _render_report_html(outputs: list[Any], paper: Any, request_id: str) -> str:
    """The HTML report for already-computed module outputs ("" if unavailable)."""
    try:
        from metacheck.report.report import render_module_outputs
    except ImportError:
        LOG.warning("report rendering unavailable (%s)", request_id)
        return ""
    try:
        return str(render_module_outputs(outputs, paper, output_format="html"))
    except Exception as exc:
        LOG.warning("report render failed (%s): %s", request_id, exc)
        return ""
