"""Replay metacheck's recorded httptest2 API fixtures in pytest (via respx).

metacheck records API responses with httptest2 under
``upstream/metacheck/tests/testthat/apis*/``. File names follow
``httptest2::build_mock_url()``: the URL without scheme, a trailing ``-<hash>``
for a query string and another for a request body (the first 6 hex digits of
``digest::digest(x)``, i.e. MD5 of R's serialisation of the string), then
``-<METHOD>`` for non-GET requests, then an extension: ``.json``/``.html``/
``.xml``/``.txt`` hold a body (status 200), ``.R`` holds a full deparsed
``httr2_response`` (status, headers, raw body).

Usage::

    from tests.httpmock import replay

    def test_crossref(upstream_dir):
        with replay("apis"):
            resp = pytacheck.http.request("GET", "https://api.crossref.org/works/10.1/x")

Requests with no fixture get a 404 (as httptest2 errors), so a test cannot
silently hit the network.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import struct
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import respx

UPSTREAM_TESTS = (
    Path(__file__).resolve().parent.parent / "upstream" / "metacheck" / "tests" / "testthat"
)
_EXTENSIONS = {
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
}


def r_digest(text: str) -> str:
    """``digest::digest(text)`` for a single string (MD5 of R's serialisation)."""
    raw = text.encode("utf-8")
    flags = 9 | ((64 if raw.isascii() else 8) << 12)
    return hashlib.md5(
        struct.pack(">iiii", 16, 1, flags, len(raw)) + raw, usedforsecurity=False
    ).hexdigest()


def _multipart_string(request: httpx.Request, body: bytes) -> str | None:
    """httptest2's text for a multipart body (``get_string_request_body()``).

    ``"Multipart form:\\n  name = value\\n  file = File: <md5 of contents>\\n"``,
    fields in request order.
    """
    ctype = request.headers.get("content-type", "")
    if not ctype.startswith("multipart/form-data") or "boundary=" not in ctype:
        return None
    boundary = ctype.split("boundary=", 1)[1].split(";")[0].strip('"').encode()
    fields = []
    for part in body.split(b"--" + boundary)[1:]:
        if part.startswith(b"--"):
            break
        head, _, content = part.partition(b"\r\n\r\n")
        if content.endswith(b"\r\n"):
            content = content[:-2]
        name = re.search(rb'name="([^"]*)"', head)
        if name is None:
            continue
        if b"filename=" in head:
            value = "File: " + hashlib.md5(content, usedforsecurity=False).hexdigest()
        else:
            value = content.decode("utf-8", "replace")
        fields.append(f"{name.group(1).decode()} = {value}")
    return "\n  ".join(["Multipart form:", *fields]) + "\n"


def mock_path(request: httpx.Request) -> str:
    """``httptest2::build_mock_url()`` for an httpx request (no extension)."""
    url = str(request.url)
    url = re.sub(r"^.*?://", "", url, count=1)
    base, _, query = url.partition("?")
    path = re.sub(r"/$", "", base).replace(":", "-")
    if query:
        path += "-" + r_digest(query)[:6]
    body = request.read()
    multipart = _multipart_string(request, body)
    if multipart is not None:
        path += "-" + r_digest(multipart)[:6]
    elif body:
        path += "-" + r_digest(body.decode("utf-8", "replace"))[:6]
    if request.method != "GET":
        path += "-" + request.method
    return path


def _parse_r_response(text: str) -> httpx.Response:
    status = re.search(r"status_code\s*=\s*(\d+)L", text)
    headers: dict[str, str] = {}
    block = re.search(
        r"headers = structure\(list\((.*?)\), class = \"httr2_headers\"\)", text, re.S
    )
    if block:
        for key, value in re.findall(
            r"`?([A-Za-z0-9_-]+)`?\s*=\s*\"((?:[^\"\\]|\\.)*)\"", block.group(1)
        ):
            headers[key] = value.encode().decode("unicode_escape")
    body = b""
    raw = re.search(r"body = as\.raw\(c\((.*?)\)\)", text, re.S)
    if raw:
        body = bytes(int(h, 16) for h in re.findall(r"0x([0-9a-fA-F]{2})", raw.group(1)))
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return httpx.Response(int(status.group(1)) if status else 200, headers=headers, content=body)


def fixture_response(root: Path, path: str) -> httpx.Response | None:
    """The recorded response for *path* under *root*, if there is one."""
    r_file = root / f"{path}.R"
    if r_file.exists():
        return _parse_r_response(r_file.read_text(encoding="utf-8"))
    for ext, ctype in _EXTENSIONS.items():
        f = root / f"{path}{ext}"
        if f.exists():
            return httpx.Response(200, headers={"content-type": ctype}, content=f.read_bytes())
    return None


@contextlib.contextmanager
def replay(*mock_dirs: str | Path, assert_all_called: bool = False) -> Iterator[respx.MockRouter]:
    """Serve requests from httptest2 mock directories (relative to metacheck's tests)."""
    roots = [
        Path(d) if Path(d).is_absolute() else UPSTREAM_TESTS / d for d in mock_dirs or ("apis",)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = mock_path(request)
        for root in roots:
            resp = fixture_response(root, path)
            if resp is not None:
                return resp
        host = urlsplit(str(request.url)).hostname
        return httpx.Response(404, json={"error": f"no recorded fixture for {path} ({host})"})

    with respx.mock(assert_all_called=assert_all_called) as router:
        router.route().mock(side_effect=handler)
        yield router
