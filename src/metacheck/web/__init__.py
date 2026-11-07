"""The demo website and its web interface: ``pytacheck site``.

One command serves a one-page website and the job-queue API under ``/api`` that the page
talks to (``POST /api/jobs``, ``GET /api/jobs/{id}``, ``GET /api/jobs/{id}/result`` and the
one-call ``/api/report``, ``/api/check``, ``/api/convert`` and ``/api/repocheck``). The
checks are pytacheck's own. See ``docs/SITE.md``.

Importing this package never imports FastAPI: ``config``, ``psyarxiv`` and ``uploads`` are
plain Python. ``app`` builds the ASGI app and needs the ``api`` extra
(``pip install "metacheck[api]"``).
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(config: object | None = None) -> object:
    """The ASGI app for the site and its web interface (see :func:`metacheck.web.app.create_app`)."""
    from metacheck.web.app import create_app as build

    return build(config)  # type: ignore[arg-type]
