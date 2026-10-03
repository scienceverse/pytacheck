"""The ASGI app: Gradio mounted under the guards."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

import gradio as gr
from fastapi import FastAPI
from starlette.responses import FileResponse, JSONResponse, Response

from metacheck.app.hosted import HostedConfig
from metacheck.app.package import UPLOAD_LIMIT
from metacheck.app.security import TokenGuard
from metacheck.app.ui import CSS, MOUNT_KWARGS, Sessions, build_app, theme

__all__ = ["create_app", "create_hosted_app"]


def create_app(port: int, token: str) -> Any:
    """The full app for ``127.0.0.1:port``; every route needs the token or its cookie."""
    return _assemble(port, None, lambda app: app.add_middleware(TokenGuard, port=port, token=token))


def create_hosted_app(port: int, config: HostedConfig) -> Any:
    """The app for a shared server: any of the tokens (or a sign-in proxy in front), https,
    the configured host names."""
    return _assemble(
        port,
        config,
        lambda app: app.add_middleware(
            TokenGuard,
            tokens=config.tokens,
            hosts=config.hosts,
            secure=True,
            proxy_auth=config.proxy_auth,
            user_header=config.user_header,
        ),
    )


def _assemble(port: int, hosted: HostedConfig | None, guard: Callable[[Any], None]) -> Any:
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions = Sessions()

    # Served from the app's own folder: Gradio's cache is swept while the page stays open.
    @api.get("/report/{session}/{name}")
    def report(session: str, name: str) -> Response:
        path = sessions.file(session, name)
        if path is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(
            path,
            media_type="text/html",
            filename=path.name,
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )

    blocks = build_app(sessions, hosted)
    # Only ``launch()`` sets this, and Gradio then refuses an upload field that names a
    # file outside its upload folder. Mounted apps skip that check unless it is set here.
    blocks.has_launched = True
    with warnings.catch_warnings():
        # SSR is off already (MOUNT_KWARGS); Gradio says so again for an app with two pages
        warnings.filterwarnings("ignore", message="SSR mode is not supported", category=UserWarning)
        api = gr.mount_gradio_app(
            api,
            blocks,
            path="/",
            server_name="127.0.0.1",
            server_port=port,
            theme=theme(),
            css=CSS,
            # the local app's data package page takes a zip of up to 1 GB
            **({**MOUNT_KWARGS, "max_file_size": UPLOAD_LIMIT} if hosted is None else MOUNT_KWARGS),
        )
    guard(api)
    return api
