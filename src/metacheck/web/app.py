"""The ASGI app of ``pytacheck site``: the one-page website and the web interface under ``/api``.

The routes, fields, status codes and limits follow the web interface of the Metacheck site
on SURF Research Cloud, so a script written for that one keeps working (docs/SITE.md).
This module only takes requests apart and answers; the queue is ``jobs.JobManager`` and the
work is ``worker.run_job``.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from metacheck._logging import get_logger
from metacheck.web import uploads
from metacheck.web.config import (
    DEFAULT_MODULES,
    KEY_SERVICES,
    MAX_ZIP_BYTES,
    RESEARCHBOX_NO_KEY,
    SiteConfig,
    allowed_modules,
    module_info,
)
from metacheck.web.jobs import Job, JobManager, QueueFull
from metacheck.web.psyarxiv import psyarxiv_id
from metacheck.web.uploads import Refused

__all__ = ["SITE_DIR", "create_app"]

LOG = get_logger("web")
SITE_DIR = Path(__file__).resolve().parent / "site"
KINDS = ("report", "check", "convert", "repocheck")
#: the body of a request is read in pieces of this size when it is copied to the job's folder
_PIECE = 1024 * 1024
#: what the pages may load: themselves and nothing else, so no visitor's browser contacts
#: another site
PAGE_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
#: a report served by itself runs in a sandbox of its own: no access to this origin
REPORT_POLICY = "sandbox allow-scripts"
NOT_FOUND = "No such job (results are kept for one hour)."


def _json(content: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers={"Cache-Control": "no-store"})


def _error(status: int, message: str) -> JSONResponse:
    return _json({"error": message}, status)


def _flag(text: object, default: bool = False) -> bool:
    """``"true"``, ``"1"`` and ``"yes"`` are true, other text is false, nothing is *default*."""
    if not isinstance(text, str) or not text.strip():
        return default
    return text.strip().lower() in ("true", "1", "yes")


def _time(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")


def _base(request: Request) -> str:
    """The address a client reaches this server at, as far as the proxy in front says."""
    scheme = request.headers.get("x-forwarded-proto", "")
    if scheme not in ("http", "https"):
        scheme = request.url.scheme
    host = request.headers.get("host", "")
    if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,255}", host):
        host = request.url.netloc
    prefix = request.headers.get("x-forwarded-prefix", "")
    if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", prefix):
        prefix = ""
    return f"{scheme}://{host}{prefix}"


def _modules(text: str | None, kind: str) -> list[str]:
    """The checks asked for (``modules``), checked against the allowed ones."""
    allowed = allowed_modules()
    if kind == "convert":
        return []
    if kind == "repocheck":
        asked = [m.strip() for m in (text or "").split(",") if m.strip()]
        if asked:
            if len(asked) != 1:
                raise Refused(400, "A repository check runs one installed module at a time.")
            raise Refused(
                400, f"{asked[0]} is not installed on this server. Installed modules: none"
            )
        return []
    if not (text or "").strip():
        return [m for m in allowed if m in DEFAULT_MODULES]
    asked = [m.strip() for m in (text or "").split(",") if m.strip()]
    bad = [m for m in asked if m not in allowed]
    if bad or not asked:
        raise Refused(
            400,
            f"Unknown or not allowed modules: {', '.join(bad) or '(none)'}. "
            f"Allowed: {', '.join(allowed)}",
        )
    return list(dict.fromkeys(asked))


def _report_type(text: str | None) -> str:
    value = (text or "").strip().lower()
    if value not in ("", "checklist"):
        raise Refused(400, 'report_type must be "checklist" (or left out).')
    return value


#: a box on ResearchBox: its number, and the passcode of a box that is not public yet
_RBOX = re.compile(
    r"https?://(?:www\.)?researchbox\.org/(\d{1,7})"
    r"(&PEER_REVIEW_passcode=[A-Za-z0-9]+)?/?",
    re.IGNORECASE,
)


def _researchbox_link(link: str, keys: dict[str, str]) -> str:
    """A ResearchBox link as the worker takes it (``https://researchbox.org/<number>``, with a
    peer-review link's passcode), or :class:`Refused`. A box is checked only with a key, the
    sender's or the server's: ResearchBox's API says how big the box is before it is
    downloaded."""
    from metacheck.archives.api_keys import rbox_token

    found = _RBOX.fullmatch(link)
    if found is None:
        raise Refused(
            400,
            "That is not the link of a box. Give the box's number, such as "
            "https://researchbox.org/1234, or its peer-review link.",
        )
    if not keys.get("researchbox") and not rbox_token():
        raise Refused(400, RESEARCHBOX_NO_KEY)
    code = found.group(2) or ""
    return f"https://researchbox.org/{found.group(1)}" + (
        "&PEER_REVIEW_passcode=" + code.split("=", 1)[1] if code else ""
    )


def _shown_link(url: str) -> str:
    """*url* as the job's name: a peer-review passcode left out."""
    return re.sub(r"&PEER_REVIEW_passcode=[A-Za-z0-9]+", "", url)


def _repository_link(text: str, keys: dict[str, str] | None = None) -> str:
    """The link of a public repository this server can fetch from, or :class:`Refused`."""
    from urllib.parse import urlsplit

    from metacheck.datapackage import PackageError, parse_link
    from metacheck.datapackage._remote import _normalise

    if len(text.strip()) > 500:
        raise Refused(400, "That link is too long.")
    link = _normalise(text.strip())
    host = (urlsplit(link).hostname or "").lower().rstrip(".") if link else ""
    if host in ("researchbox.org", "www.researchbox.org"):
        return _researchbox_link(link, keys or {})
    path = urlsplit(link).path.lower().replace("\\", "/")
    if any(part in (".", "..", "%2e", "%2e%2e") for part in path.split("/")):
        raise Refused(400, "That link is not the address of a repository.")
    try:
        parse_link(link)
    except PackageError as exc:
        raise Refused(400, " ".join(str(exc).split())) from None
    return link


def _own_keys(fields: dict[str, str]) -> dict[str, str]:
    keys = {s: fields.get(f"key_{s}", "").strip() for s in KEY_SERVICES}
    return {s: v for s, v in keys.items() if v}


def _key_problems() -> list[dict[str, Any]]:
    """The server's keys that a service refused or whose limit is reached, with the message
    for visitors (``api_key_status()``), as the site on SURF Research Cloud lists them in
    ``/api/health``."""
    import pandas as pd

    from metacheck.archives.api_keys import api_key_status, api_keys_use
    from metacheck.utils import options

    before = api_keys_use({}, shared=True)  # the server is shared: "the key it provides"
    try:
        status = api_key_status()
    except Exception:
        return []
    finally:
        options(before)
    out = []
    for row in status.to_dict("records"):
        if pd.isna(row["message"]):
            continue
        until = row["until"]
        out.append(
            {
                "service": row["service"],
                "label": row["label"],
                "message": row["message"],
                "url": row["url"],
                "problem": None if pd.isna(row["problem"]) else row["problem"],
                "source": row["key"],
                "until": None if pd.isna(until) else until.strftime("%Y-%m-%d %H:%M UTC"),
            }
        )
    return out


def _grobid_kind() -> str:
    from urllib.parse import urlsplit

    from metacheck.app.run import grobid_servers

    hosts = {(urlsplit(u).hostname or "") for u in grobid_servers()}
    return "local" if hosts and hosts <= {"localhost", "127.0.0.1", "::1"} else "remote"


def _bibr_configured() -> bool:
    from metacheck.app import bibr

    try:
        return bibr._configured() is not None
    except ValueError:
        return False


def _docker_ready() -> bool:
    from metacheck.repro.docker import repro_docker_available

    return bool(repro_docker_available()["ok"])


def create_app(config: SiteConfig | None = None) -> FastAPI:
    """The site and its web interface, for *config* (the defaults when ``None``).

    With ``config.run_code`` the server runs a repository's code, but only when Docker is
    running: otherwise ``run_code`` is off, and ``/api/health`` says so.
    """
    from metacheck._version import UPSTREAM, __version__

    config = config or SiteConfig()
    manager = JobManager(config)
    run_code = config.run_code and _docker_ready()
    if config.run_code and not run_code:
        LOG.warning("run_code was asked for, but Docker is not running: code is not run")
    info_cache: list[dict[str, object]] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        manager.close()

    app = FastAPI(
        title="Metacheck web interface",
        description=(
            "Send a paper (PDF, bibr JSON or GROBID XML) or a zip of a data and code folder "
            "and get a Metacheck report back."
        ),
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.manager = manager
    app.state.run_code = run_code
    api = APIRouter(prefix="/api")

    # -- taking a request apart ----------------------------------------------------------

    async def read_form(request: Request) -> Any:
        kind = request.headers.get("content-type", "")
        if not kind.lower().startswith("multipart/form-data"):
            raise Refused(400, "Send the file as multipart/form-data, in the form field 'file'.")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_ZIP_BYTES + _PIECE:
            raise Refused(413, "The file is larger than 500 MB.")
        try:
            return await request.form()
        except Exception:
            raise Refused(400, "The upload could not be read as multipart/form-data.") from None

    async def make_job(request: Request, forced: str | None) -> Job:
        """Read the request and put its job in the queue; :class:`Refused` if it is turned down."""
        try:
            manager.check_capacity()
        except QueueFull as exc:
            raise Refused(503, str(exc)) from None
        form = await read_form(request)
        folder: Path | None = None
        try:
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
            sent = form.get("file")
            upload = sent if hasattr(sent, "read") and getattr(sent, "filename", None) else None
            job_id, folder = manager.new_folder()
            input_kind, filename = await _store(upload, fields, folder)
            kind = (
                forced
                or fields.get("kind")
                or ("repocheck" if input_kind in ("zip", "url") else "report")
            )
            spec = _spec(kind, input_kind, filename, fields)
            job = Job(
                id=job_id,
                kind=kind,
                spec=spec,
                folder=folder,
                run_code=spec["run_code"],
                keys=_own_keys(fields),
            )
            manager.submit(job)
            return job
        except QueueFull as exc:
            if folder is not None:
                manager.discard_folder(folder)
            raise Refused(503, str(exc)) from None
        except BaseException:
            if folder is not None:
                manager.discard_folder(folder)
            raise
        finally:
            await form.close()

    async def _store(upload: Any, fields: dict[str, str], folder: Path) -> tuple[str, str | None]:
        """Save the upload in the job's folder as ``input.<kind>``. Returns the kind of input
        (``pdf``, ``json``, ``xml``, ``zip``, ``url`` or ``psyarxiv``) and the file's name."""
        if upload is None:
            if fields.get("url", "").strip():
                return "url", None
            if fields.get("psyarxiv", "").strip():
                return "psyarxiv", None
            raise Refused(
                400,
                "Send exactly one file in the form field 'file' (or, for a repository check, a "
                "link in the field 'url'; for a PsyArXiv preprint, its link or id in the field "
                "'psyarxiv').",
            )
        size = getattr(upload, "size", None)
        if size == 0:
            raise Refused(400, "The file is empty.")
        if size is not None and size > MAX_ZIP_BYTES:
            raise Refused(413, "The file is larger than 500 MB.")
        raw = folder / "input.upload"

        def copy() -> str:
            with open(raw, "wb") as out:
                shutil.copyfileobj(upload.file, out, _PIECE)
            with open(raw, "rb") as fh:
                head = fh.read(512)
            kind = uploads.file_kind(head)
            if kind in ("zip", "docx"):
                kind = uploads.check_zip(raw)
                if kind == "docx":
                    raise Refused(
                        400, "Word files are not supported here. Send a PDF, bibr JSON or a .zip."
                    )
            if raw.stat().st_size > uploads.size_limit(kind):
                limit = "500 MB" if kind == "zip" else "50 MB"
                raise Refused(
                    413, f"The {'.zip ' if kind == 'zip' else ''}file is larger than {limit}."
                )
            raw.rename(folder / f"input.{kind}")
            return kind

        kind = await run_in_threadpool(copy)
        return kind, uploads.safe_name(getattr(upload, "filename", ""))

    def _spec(
        kind: str, input_kind: str, name: str | None, fields: dict[str, str]
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise Refused(400, "kind must be report, check, convert or repocheck.")
        if kind == "convert" and input_kind != "pdf":
            raise Refused(400, "convert needs a PDF.")
        if kind == "repocheck" and input_kind not in ("zip", "url"):
            raise Refused(
                400,
                "repocheck needs a .zip file of a data and code folder, or a link to an online "
                "repository (field 'url').",
            )
        if kind != "repocheck" and input_kind == "url":
            raise Refused(
                400,
                "A link can only be checked as a repository (kind=repocheck, or POST /repocheck).",
            )
        if kind != "repocheck" and input_kind == "zip":
            raise Refused(400, "A .zip file is checked with kind=repocheck (or POST /repocheck).")
        if input_kind == "psyarxiv" and kind not in ("report", "check"):
            raise Refused(
                400, "A PsyArXiv preprint is checked as a paper (kind=report or kind=check)."
            )
        if kind == "repocheck" and input_kind in ("pdf", "json", "xml"):
            raise Refused(400, "A paper is checked with kind=report or kind=check.")
        url = (
            _repository_link(fields.get("url", ""), _own_keys(fields))
            if input_kind == "url"
            else None
        )
        try:
            preprint = psyarxiv_id(fields.get("psyarxiv")) if input_kind == "psyarxiv" else None
        except ValueError as exc:
            raise Refused(400, str(exc)) from None
        asked = fields.get("run_code")
        if not run_code or kind == "convert":
            code = False
        elif kind == "repocheck":
            code = _flag(asked, default=True)
        else:
            code = _flag(asked)
        input_file = None if input_kind in ("url", "psyarxiv") else f"input.{input_kind}"
        return {
            "kind": kind,
            "input_kind": input_kind,
            "input_file": input_file,
            "name": (url and _shown_link(url)) or preprint or name or "",
            "url": url,
            "psyarxiv": preprint,
            "psyarxiv_api": config.psyarxiv_api,
            "modules": _modules(fields.get("modules"), kind),
            "report": _flag(fields.get("report")),
            "report_type": _report_type(fields.get("report_type")),
            "crossref": _flag(fields.get("crossref")),
            "run_code": code,
        }

    # -- answering -----------------------------------------------------------------------

    def status_body(job: Job, request: Request) -> dict[str, Any]:
        base = _base(request)
        body: dict[str, Any] = {
            "id": job.id,
            "status": job.status,
            "step": job.step,
            "position": manager.position(job) if job.status == "queued" else None,
            "progress": job.progress if job.status == "running" else None,
            "run_code": True if job.run_code else None,
            "converter": job.converter,
            "created": _time(job.created),
            "started": _time(job.started),
            "finished": _time(job.finished),
            "error": job.error,
            "status_url": f"{base}/api/jobs/{job.id}",
            "result_url": f"{base}/api/jobs/{job.id}/result",
        }
        return {k: v for k, v in body.items() if v is not None}

    def result_response(job: Job) -> Response:
        if job.status == "failed":
            return _error(422, job.error or "The job failed.")
        if job.status != "done" or job.result is None:
            return _json({"error": "The job is not finished yet.", "status": job.status}, 409)
        path = job.folder / job.result
        try:
            body = path.read_bytes()
        except OSError:
            return _error(404, NOT_FOUND)
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if (job.result_type or "").startswith("text/html"):
            headers["Content-Security-Policy"] = REPORT_POLICY
        if job.report_type:
            headers["X-Metacheck-Report-Type"] = job.report_type
        return Response(body, media_type=job.result_type, headers=headers)

    async def submit(request: Request, forced: str | None) -> Response:
        try:
            job = await make_job(request, forced)
        except Refused as exc:
            return _error(exc.status, exc.message)
        return _json(status_body(job, request), 202)

    async def one_call(request: Request, forced: str) -> Response:
        try:
            job = await make_job(request, forced)
        except Refused as exc:
            return _error(exc.status, exc.message)
        while not job.is_over:
            await asyncio.sleep(0.2)
        return result_response(job)

    # -- the routes ----------------------------------------------------------------------

    @api.get("/health")
    def health() -> JSONResponse:
        """Whether the server is up, what runs on it and how busy it is."""
        running, waiting = manager.counts()
        return _json(
            {
                "status": "ok",
                "metacheck_version": __version__,
                "upstream_version": UPSTREAM["version"],
                "bibr": _bibr_configured(),
                "grobid": _grobid_kind(),
                "jobs_running": running,
                "jobs_waiting": waiting,
                "run_code": run_code,
                "sandbox": "docker" if run_code else "none",
                "local": False,
                "key_problems": _key_problems(),
                "workers": config.max_running,
            }
        )

    @api.get("/modules")
    def modules() -> JSONResponse:
        """The checks that may be asked for, the default set, and each one's title."""
        if not info_cache:
            info_cache.extend(module_info())
        return _json(
            {
                "allowed": [str(m["name"]) for m in info_cache],
                "default": [str(m["name"]) for m in info_cache if m["default"]],
                "info": info_cache,
                "installed": [],
            }
        )

    @api.post("/jobs")
    async def jobs(request: Request) -> Response:
        """Send a job; the answer (HTTP 202) says where to follow it."""
        return await submit(request, None)

    @api.get("/jobs/{job_id}")
    def job_status(job_id: str, request: Request) -> Response:
        """The status of a job: queued, running, done or failed."""
        job = manager.get(job_id)
        if job is None:
            return _error(404, NOT_FOUND)
        return _json(status_body(job, request))

    @api.get("/jobs/{job_id}/result")
    def job_result(job_id: str) -> Response:
        """The result of a finished job (HTTP 409 before that, 422 if it failed)."""
        job = manager.get(job_id)
        if job is None:
            return _error(404, NOT_FOUND)
        return result_response(job)

    def one_call_route(kind: str, summary: str) -> Callable[[Request], Awaitable[Response]]:
        async def route(request: Request) -> Response:
            return await one_call(request, kind)

        route.__doc__ = summary
        route.__name__ = f"post_{kind}"
        return route

    api.post("/report")(one_call_route("report", "A paper in, the HTML report out."))
    api.post("/check")(
        one_call_route("check", "A paper in, the results of the checks as JSON out.")
    )
    api.post("/convert")(one_call_route("convert", "A PDF in, bibr JSON out."))
    api.post("/repocheck")(
        one_call_route(
            "repocheck", "A .zip of a data and code folder (or a link) in, the report out."
        )
    )
    app.include_router(api)

    # -- the website ---------------------------------------------------------------------

    app.mount("/site", StaticFiles(directory=SITE_DIR), name="site")

    def page(*parts: str) -> Callable[[], FileResponse]:
        path = SITE_DIR.joinpath(*parts, "index.html")

        def serve() -> FileResponse:
            return FileResponse(path, media_type="text/html")

        return serve

    app.get("/", include_in_schema=False)(page())
    app.get("/researchbox/", include_in_schema=False)(page("researchbox"))
    app.get("/psyarxiv/", include_in_schema=False)(page("psyarxiv"))

    @app.middleware("http")
    async def headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", PAGE_POLICY)
        return response

    return app
