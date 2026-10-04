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
            resp = metacheck.http.request("GET", "https://api.crossref.org/works/10.1/x")

Requests with no fixture get a 404 (as httptest2 errors), so a test cannot
silently hit the network. ``no_network()`` refuses every connection and name
lookup outright (the parity runner runs each case inside it).
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import ipaddress
import json
import os
import re
import socket
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import respx

from tests import llmreplies

UPSTREAM_TESTS = (
    Path(__file__).resolve().parent.parent / "upstream" / "metacheck" / "tests" / "testthat"
)
_EXTENSIONS = {
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
}


def r_digest(text: str, native: bool = False) -> str:
    """``digest::digest(text)`` for a single string (MD5 of R's serialisation).

    A non-ASCII string is serialised as UTF-8-marked, or -- with *native*, for
    strings made by ``rawToChar()`` such as httptest2's request bodies -- in
    the native (unmarked) encoding.
    """
    raw = text.encode("utf-8")
    flags = 9 | ((64 if raw.isascii() else (0 if native else 8)) << 12)
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
        path += "-" + r_digest(body.decode("utf-8", "replace"), native=True)[:6]
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
    else:
        chars = re.search(r'body = charToRaw\("((?:[^"\\]|\\.)*)"\)', text, re.S)
        if chars:
            body = _r_unescape(chars.group(1)).encode("utf-8")
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return httpx.Response(int(status.group(1)) if status else 200, headers=headers, content=body)


def _r_unescape(s: str) -> str:
    """Undo the escapes of a deparsed R string literal."""
    simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'", "0": "\0"}

    def repl(m: re.Match[str]) -> str:
        e = m.group(1)
        if e[0] in ("u", "U"):
            return chr(int(e[1:].strip("{}"), 16))
        return simple.get(e, e)

    return re.sub(r"\\(u\{?[0-9a-fA-F]{4}\}?|U\{?[0-9a-fA-F]{8}\}?|.)", repl, s)


def fixture_file(root: Path, path: str) -> Path | None:
    """The recorded file for *path* under *root* (``path`` without its extension)."""
    r_file = root / f"{path}.R"
    if r_file.exists():
        return r_file
    for ext in _EXTENSIONS:
        f = root / f"{path}{ext}"
        if f.exists():
            return f
    return None


def file_response(f: Path) -> httpx.Response:
    """The response a recorded file holds."""
    if f.suffix == ".R":
        return _parse_r_response(f.read_text(encoding="utf-8"))
    return httpx.Response(
        200, headers={"content-type": _EXTENSIONS[f.suffix]}, content=f.read_bytes()
    )


def fixture_response(root: Path, path: str) -> httpx.Response | None:
    """The recorded response for *path* under *root*, if there is one."""
    f = fixture_file(root, path)
    return None if f is None else file_response(f)


def _llm_reply(root: Path, request: httpx.Request, asked: dict[str, Any]) -> httpx.Response | None:
    """The reply to an LLM request, by what it asks (see :mod:`tests.llmreplies`).

    A directory without an ``index.json`` holds no LLM replies; a request whose
    key the index lacks gets no reply (the caller answers 404).
    """
    index = llmreplies.load_index(root)
    if index is None:
        return None
    name = index.get(llmreplies.llm_key(asked))
    return None if name is None else file_response(root / name)


def _log_llm_reply(root: Path, path: str, asked: dict[str, Any], log: str) -> None:
    """Record which file answered an LLM request (``PYTACHECK_LLM_INDEX_LOG``)."""
    f = fixture_file(root, path)
    if f is None:
        return
    entry = {
        "root": str(root),
        "key": llmreplies.llm_key(asked),
        "file": f.relative_to(root).as_posix(),
        "request": asked,
    }
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def replay(*mock_dirs: str | Path, assert_all_called: bool = False) -> Iterator[respx.MockRouter]:
    """Serve requests from httptest2 mock directories (relative to metacheck's tests)."""
    roots = [
        Path(d) if Path(d).is_absolute() else UPSTREAM_TESTS / d for d in mock_dirs or ("apis",)
    ]

    log = os.environ.get("PYTACHECK_LLM_INDEX_LOG")

    def handler(request: httpx.Request) -> httpx.Response:
        path = mock_path(request)
        asked = llmreplies.llm_request(request.method, str(request.url), request.read())
        for root in roots:
            if asked is not None and log:
                _log_llm_reply(root, path, asked, log)  # recording: R's file names
            elif asked is not None and llmreplies.load_index(root) is not None:
                resp = _llm_reply(root, request, asked)
                if resp is not None:
                    return resp
                continue
            resp = fixture_response(root, path)
            if resp is not None:
                return resp
        host = urlsplit(str(request.url)).hostname
        return httpx.Response(404, json={"error": f"no recorded fixture for {path} ({host})"})

    with respx.mock(assert_all_called=assert_all_called) as router:
        router.route().mock(side_effect=handler)
        yield router


# -- no network ------------------------------------------------------------------------


class NetworkUse(ConnectionError):
    """A connection or name lookup refused by :func:`no_network`."""


_LOCAL_NAMES = {None, "", "localhost"}
_PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy")


def _proxies() -> set[tuple[str, int]]:
    """The ``(host, port)`` of every proxy the environment configures."""
    out = set()
    for name in _PROXY_VARIABLES:
        for value in (os.environ.get(name), os.environ.get(name.upper())):
            if not value:
                continue
            url = urlsplit(value if "://" in value else f"http://{value}")
            with contextlib.suppress(ValueError):
                if url.hostname:
                    out.add((url.hostname, url.port or 80))
    return out


def _is_loopback(host: Any) -> bool:
    try:
        return bool(ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback)
    except ValueError:
        return bool(host == "localhost")


@contextlib.contextmanager
def no_network(attempts: list[str] | None = None) -> Iterator[list[str]]:
    """Refuse every internet connection and every lookup of a host name.

    A socket connecting (or sending a datagram) to another host, or to a proxy
    the environment configures (``HTTPS_PROXY``, ...), and ``getaddrinfo()``/
    ``gethostbyname()`` of a name other than ``localhost`` raise
    :class:`NetworkUse`, and the attempt is added to *attempts* (yielded), so
    code that catches the error still shows up. Any other loopback address is
    refused as a closed port would refuse it (``ConnectionRefusedError``, not an
    attempt: code may probe a local port on purpose). Unix sockets (``asyncio``'s
    self-pipe) work as before; recorded responses served by :func:`replay` never
    open a socket. This process only: a program it starts (``git``, ``Rscript``)
    is not watched.
    """
    tried: list[str] = [] if attempts is None else attempts
    proxies = _proxies()

    def check(sock: socket.socket, address: Any, how: str = "connect to") -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return
        host, port = address[0], address[1]
        is_proxy = any(port == p and (host == h or _is_loopback(h)) for h, p in proxies)
        if _is_loopback(host) and not is_proxy:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "no network in a parity case")
        what = f"{how} {address!r}" + (" (a proxy)" if is_proxy else "")
        tried.append(what)
        raise NetworkUse(f"no network in a parity case: {what}")

    def connect(self: socket.socket, address: Any) -> None:
        check(self, address)
        return saved_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        check(self, address)
        return saved_connect_ex(self, address)

    def sendto(self: socket.socket, data: Any, *args: Any) -> int:
        # sendto(data, address) or sendto(data, flags, address): a datagram (a
        # DNS query of its own) needs no connect()
        if args:
            check(self, args[-1], "send a datagram to")
        return saved_sendto(self, data, *args)

    def lookup(fn: Any) -> Any:
        def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
            name = host.decode() if isinstance(host, bytes) else host
            if name not in _LOCAL_NAMES and not _is_address(name):
                tried.append(f"look up {name!r}")
                raise NetworkUse(f"no network in a parity case: look up {name!r}")
            return fn(host, *args, **kwargs)

        return guarded

    saved_connect, saved_connect_ex = socket.socket.connect, socket.socket.connect_ex
    saved_sendto = socket.socket.sendto
    lookups = {n: getattr(socket, n) for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex")}
    own = {n: n in vars(socket.socket) for n in ("connect", "connect_ex", "sendto")}
    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
    socket.socket.sendto = sendto  # type: ignore[method-assign,assignment]
    for name, fn in lookups.items():
        setattr(socket, name, lookup(fn))
    try:
        yield tried
    finally:
        for name, restore in (
            ("connect", saved_connect),
            ("connect_ex", saved_connect_ex),
            ("sendto", saved_sendto),
        ):
            if own[name]:
                setattr(socket.socket, name, restore)
            else:  # inherited from _socket.socket
                delattr(socket.socket, name)
        for name, fn in lookups.items():
            setattr(socket, name, fn)


def _is_address(host: Any) -> bool:
    """Whether *host* is a numeric IPv4/IPv6 address (no lookup needed)."""
    if not isinstance(host, str):
        return False
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, host.split("%", 1)[0])
            return True
        except OSError:
            continue
    return False
