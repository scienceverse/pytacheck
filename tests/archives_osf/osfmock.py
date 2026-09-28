"""Replaying metacheck's recorded OSF responses (shared by tests and parity cases).

metacheck's OSF fixtures were recorded before listing requests carried
``page[size]=100``; its test helper installs an httptest2 *redactor* that
strips that parameter before computing the mock file name. :func:`replay_osf`
does the same on top of :mod:`tests.httpmock`, and also serves recorded file
downloads whose body httptest2 stored in a separate ``.R-FILE``.

httptest2 does not answer an unrecorded request: it raises an error, which
httr2 reports as a failed request. ``missing="error"`` reproduces that (the
request raises :class:`httpx.ConnectError`); the default ``missing="404"``
answers 404 instead, which is what most tests want.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import respx

from tests.httpmock import UPSTREAM_TESTS, fixture_response, r_digest

_REDACT = re.compile(r"[?&]page(%5[Bb]size%5[Dd]|\[size\])=100")
_FILE_REF = re.compile(r'find_mock_file\("([^"]+)"\)')


def osf_mock_path(request: httpx.Request) -> str:
    """``httptest2::build_mock_url()`` after metacheck's ``page[size]`` redactor."""
    url = _REDACT.sub("", str(request.url))
    url = re.sub(r"^.*?://", "", url, count=1)
    base, _, query = url.partition("?")
    path = re.sub(r"/$", "", base).replace(":", "-")
    if query:
        path += "-" + r_digest(query)[:6]
    body = request.content
    if body:
        path += "-" + r_digest(body.decode("utf-8", "replace"))[:6]
    if request.method != "GET":
        path += "-" + request.method
    return path


def _response(root: Path, path: str) -> httpx.Response | None:
    resp = fixture_response(root, path)
    if resp is None:
        return None
    r_file = root / f"{path}.R"
    if r_file.exists():
        ref = _FILE_REF.search(r_file.read_text(encoding="utf-8"))
        if ref and (root / ref.group(1)).exists():
            return httpx.Response(
                resp.status_code, headers=resp.headers, content=(root / ref.group(1)).read_bytes()
            )
    return resp


@contextlib.contextmanager
def replay_osf(*mock_dirs: str | Path, missing: str = "404") -> Iterator[respx.MockRouter]:
    """Replay metacheck's recorded API responses, as its OSF tests do.

    Relative *mock_dirs* are metacheck test mock directories; absolute ones
    are used as given (searched in order, like httptest2's mock paths).
    """
    roots = [
        Path(d) if Path(d).is_absolute() else UPSTREAM_TESTS / d for d in (mock_dirs or ("apis",))
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = osf_mock_path(request)
        for root in roots:
            resp = _response(root, path)
            if resp is not None:
                return resp
        if missing == "error":
            raise httpx.ConnectError(f"no recorded fixture for {path}", request=request)
        host = urlsplit(str(request.url)).hostname
        return httpx.Response(404, json={"error": f"no recorded fixture for {path} ({host})"})

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=handler)
        yield router
