"""A real zip served over (respx-mocked) HTTP with byte-range support.

Shared by the archive tests that go through the real ``zip_peek()`` and
``_zip_fetch_members()`` (``unzip_types`` in the ``*_file_download()``
functions) instead of stubbing them: the handler answers ``HEAD`` with the
archive's length and ``Range: bytes=a-b`` with the slice, as Zenodo, Dataverse
and Figshare file hosts do, and records every request so tests can check what
was transferred.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx

#: members of the test archive: one data file, documentation, materials, code
#: and a data file too big for a 1 MB cap (stored, so the archive is large too)
MEMBERS: dict[str, bytes] = {
    "bundle/data/study.csv": b"id,x\n1,2\n3,4\n",
    "bundle/docs/README.txt": b"Read me first.\n",
    "bundle/stimuli/face.png": b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8,
    "bundle/code/analysis.R": b"x <- read.csv('data/study.csv')\n",
    "bundle/data/big.csv": b"a,b\n" + b"1,2\n" * 300_000,
}
STORED = {"bundle/data/big.csv", "bundle/stimuli/face.png"}


def make_zip(members: dict[str, bytes] = MEMBERS, stored: set[str] = STORED) -> bytes:
    """The bytes of a zip holding *members* (deflated, except those in *stored*)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            method = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
            zf.writestr(name, data, compress_type=method)
    return buf.getvalue()


@dataclass
class ZipServer:
    """A respx side effect serving *data*; ``calls`` lists ``(method, Range)``."""

    data: bytes
    honour_range: bool = True
    head_length: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    sent: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("Range")
        self.calls.append((request.method, rng))
        if request.method == "HEAD":
            headers = {"Content-Length": str(len(self.data))} if self.head_length else {}
            return httpx.Response(200, headers=headers)
        if rng and self.honour_range:
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
            assert m is not None, rng
            a, b = int(m.group(1)), int(m.group(2))
            body = self.data[a : b + 1]
            self.sent += len(body)
            return httpx.Response(206, content=body)
        self.sent += len(self.data)
        return httpx.Response(200, content=self.data)


@contextmanager
def fresh_peek_cache() -> Iterator[None]:
    """``zip_peek()`` caches listings per URL for the session: start and end empty."""
    from pytacheck.archives import zip_peek as zp

    zp._ZIP_PEEK_CACHE.clear()
    try:
        yield
    finally:
        zp._ZIP_PEEK_CACHE.clear()


def routes(router: object, mapping: dict[str, Callable[[httpx.Request], httpx.Response]]) -> None:
    """Install ``{url: side_effect}`` on a respx *router*."""
    for url, handler in mapping.items():
        router.route(url=url).mock(side_effect=handler)  # type: ignore[attr-defined]
