"""The ASGI app: Gradio mounted under the guards."""

from __future__ import annotations

from typing import Any

import gradio as gr
from fastapi import FastAPI

from pytacheck.app.security import TokenGuard
from pytacheck.app.ui import CSS, MOUNT_KWARGS, build_app, theme

__all__ = ["create_app"]


def create_app(port: int, token: str) -> Any:
    """The full app for ``127.0.0.1:port``; every route needs the token or its cookie."""
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    api = gr.mount_gradio_app(
        api,
        build_app(),
        path="/",
        server_name="127.0.0.1",
        server_port=port,
        theme=theme(),
        css=CSS,
        **MOUNT_KWARGS,
    )
    api.add_middleware(TokenGuard, port=port, token=token)
    return api
