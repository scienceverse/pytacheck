"""The ASGI app: Gradio mounted under the guards."""

from __future__ import annotations

from typing import Any

import gradio as gr
from fastapi import FastAPI
from starlette.responses import FileResponse, JSONResponse, Response

from pytacheck.app.security import TokenGuard
from pytacheck.app.ui import CSS, MOUNT_KWARGS, Sessions, build_app, theme

__all__ = ["create_app"]


def create_app(port: int, token: str) -> Any:
    """The full app for ``127.0.0.1:port``; every route needs the token or its cookie."""
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

    blocks = build_app(sessions)
    # Only ``launch()`` sets this, and Gradio then refuses an upload field that names a
    # file outside its upload folder. Mounted apps skip that check unless it is set here.
    blocks.has_launched = True
    api = gr.mount_gradio_app(
        api,
        blocks,
        path="/",
        server_name="127.0.0.1",
        server_port=port,
        theme=theme(),
        css=CSS,
        **MOUNT_KWARGS,
    )
    api.add_middleware(TokenGuard, port=port, token=token)
    return api
