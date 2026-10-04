"""Python halves of the review parity cases (``parity/cases/repo_download_review.yaml``).

Mirrors ``tests/repo_download/review_helpers.R``: :func:`serve` answers every
request from a table of routes -- an in-process HTTP server that honours Range
requests -- so ``zip_peek()``, the member fetches and ``download_repo_files()``
run against real bytes on both sides. A route is a mapping with ``url``,
``fixture`` (a file under ``tests/repo_download/data``) or ``body``,
``status`` (200), ``headers``, ``range`` (True: honour a Range header),
``length`` (True: a HEAD sends Content-Length), ``method`` (any) and
``suffix`` (how a ``bytes=-n`` range is answered: ``"206"``, ``"416"`` for
one longer than the file, ``"ignore"``; unset, it is an error).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def _bytes_text(x: Any) -> Any:
    from metacheck.fileinfo._strings import as_bytes_text

    return as_bytes_text(x) if isinstance(x, str) else x


def _df_bytes(df: Any) -> Any:
    if not isinstance(df, pd.DataFrame):
        return df
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "string" or out[col].dtype == object:
            vals = out[col].tolist()
            if any(isinstance(v, str) for v in vals):
                out[col] = pd.Series(
                    [_bytes_text(v) if isinstance(v, str) else v for v in vals],
                    dtype=out[col].dtype,
                    index=out.index,
                )
    return out


def _route_body(route: dict[str, Any]) -> bytes:
    if route.get("fixture") is not None:
        return (DATA / route["fixture"]).read_bytes()
    if route.get("body") is not None:
        return str(route["body"]).encode("utf-8")
    return b""


def _handler(routes: list[dict[str, Any]]) -> Callable[[Any], Any]:
    import httpx

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method
        hit = None
        for r in routes:
            if r.get("url") == url and (r.get("method") is None or r.get("method") == method):
                hit = r
                break
        if hit is None:
            return httpx.Response(404, content=b"not found")
        body = _route_body(hit)
        status = int(hit.get("status") or 200)
        headers = {str(k): str(v) for k, v in (hit.get("headers") or {}).items()}
        if method == "HEAD":
            if hit.get("length", True) is not False:
                headers["Content-Length"] = str(len(body))
            return httpx.Response(status, headers=headers, content=b"")
        rng = request.headers.get("range")
        if rng is not None and hit.get("range", True) is not False and status == 200:
            total = len(body)
            suffix = hit.get("suffix")
            sm = re.match(r"^bytes=-([0-9]+)$", rng)
            if suffix is not None and sm is not None:
                n = int(sm.group(1))
                if suffix == "ignore":
                    return httpx.Response(200, headers=headers, content=body)
                if suffix == "416" and n > total:
                    headers["Content-Range"] = f"bytes */{total}"
                    return httpx.Response(416, headers=headers, content=b"")
                start = max(0, total - n)
                headers["Content-Range"] = f"bytes {start}-{total - 1}/{total}"
                return httpx.Response(206, headers=headers, content=body[start:])
            m = re.match(r"^bytes=([0-9]+)-([0-9]+)$", rng)
            assert m is not None
            start, end = int(m.group(1)), min(int(m.group(2)), len(body) - 1)
            if start > len(body) - 1:
                return httpx.Response(416, headers=headers, content=b"")
            headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
            return httpx.Response(206, headers=headers, content=body[start : end + 1])
        return httpx.Response(status, headers=headers, content=body)

    return handle


@contextlib.contextmanager
def _served(routes: list[dict[str, Any]]) -> Iterator[None]:
    import respx

    from metacheck.archives import zip_peek

    zip_peek._ZIP_PEEK_CACHE.clear()
    try:
        with respx.mock(assert_all_called=False) as router:
            router.route().mock(side_effect=_handler(list(routes or [])))
            yield
    finally:
        zip_peek._ZIP_PEEK_CACHE.clear()


def serve(routes: list[dict[str, Any]], fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
    """``fn(*args, **kw)`` with every request answered from *routes*."""
    with _served(routes):
        return fn(*args, **kw)


def peek(routes: list[dict[str, Any]], url: str, **kw: Any) -> Any:
    from metacheck.archives.zip_peek import zip_peek

    return _df_bytes(serve(routes, zip_peek, url, **kw))


def size(routes: list[dict[str, Any]], url: str) -> Any:
    from metacheck.archives.download import _remote_size

    return serve(routes, _remote_size, url)


def peek_cached(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
    url: str,
    rate_limited: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    """``zip_peek(cache=True)`` twice with the in-memory cache cleared in between.

    Mirrors ``rv_peek_cached()``: the disk cache sits in a fresh temporary
    folder; *rate_limited* records a long rate limit for the host before the
    first peek (made with ``skip_on_api_limit=True``) and forgets it after.
    """
    from metacheck import http
    from metacheck.archives import zip_peek_cache as zpc
    from metacheck.archives.download import _host, _host_rate_limit_record
    from metacheck.archives.zip_peek import zip_peek
    from metacheck.utils import options

    d = tempfile.mkdtemp(prefix="rv_zpc_")
    old = options({"metacheck.zip_peek_cache.dir": d})
    host = _host(url)

    def forget() -> None:
        with http._reset_lock:
            http._host_reset.pop(host, None)

    try:
        if rate_limited:
            _host_rate_limit_record(host, 999)
        one = serve(first, zip_peek, url, cache=True, skip_on_api_limit=rate_limited, **kw)
        forget()
        stored = zpc._zip_peek_cache_has(url)
        two = serve(second, zip_peek, url, cache=True, **kw)
        return {
            "first": _df_bytes(one),
            "stored": stored,
            "second": _df_bytes(two),
            "entries": len(os.listdir(d)),
        }
    finally:
        forget()
        options(old)
        shutil.rmtree(d, ignore_errors=True)


def decision(routes: list[dict[str, Any]], url: str, **kw: Any) -> Any:
    from metacheck.archives.zip_peek import zip_decision

    out = serve(routes, zip_decision, url, **kw)
    out["contents"] = _df_bytes(out["contents"])
    if out["types"] is not None:
        out["types"] = {_bytes_text(k): v for k, v in out["types"].items()}
    return out


def _sorted_files(d: str) -> list[str]:
    from metacheck._r import r_sorted

    found = [
        os.path.relpath(os.path.join(root, f), d)
        for root, _dirs, files in os.walk(d)
        for f in files
    ]
    return list(r_sorted(found))


def _size(p: str) -> float:
    try:
        return float(os.path.getsize(p))
    except OSError:
        return float("nan")


def fetch(
    routes: list[dict[str, Any]], url: str, names: Any = None, verify: bool = True
) -> dict[str, Any]:
    from metacheck.archives.zip_peek import _zip_fetch_members

    d = tempfile.mkdtemp(prefix="rv_")
    try:
        out = serve(routes, _zip_fetch_members, url, names=names, dest=d, verify=verify)
        if out is not None and "path" in out.columns:
            out["path"] = pd.Series(
                [
                    None if v is None or v is pd.NA else str(v).replace(d, "<TMP>")
                    for v in out["path"]
                ],
                dtype="string",
            )
        files = _sorted_files(d)
        return {
            "result": _df_bytes(out),
            "files": [_bytes_text(f) for f in files],
            "sizes": [_size(os.path.join(d, f)) for f in files],
        }
    finally:
        shutil.rmtree(d, ignore_errors=True)


def download(
    routes: list[dict[str, Any]],
    files: pd.DataFrame,
    twice: bool = False,
    disk: bool = True,
    **kw: Any,
) -> dict[str, Any]:
    from metacheck.archives.download import download_repo_files
    from metacheck.utils import get_option, options

    sess = tempfile.mkdtemp(prefix="rv_sess_")
    shutil.rmtree(sess)
    old = {
        "metacheck.repo_cache.session_dir": get_option("metacheck.repo_cache.session_dir"),
        "metacheck.repo_cache.notified": get_option("metacheck.repo_cache.notified"),
    }
    options({"metacheck.repo_cache.session_dir": sess, "metacheck.repo_cache.notified": True})
    try:

        def run() -> Any:
            dl = download_repo_files(files, **kw)
            if twice:
                dl = download_repo_files(files, **kw)
            return dl

        dl = serve(routes, run)
        assert dl is not None
        on_disk = _sorted_files(sess) if os.path.isdir(sess) else []
        out = {
            "file_location": [
                None if v is None or v is pd.NA else str(v).replace(sess, "<SESSION>")
                for v in dl["file_location"].tolist()
            ],
            "gated": dl.attrs["gated"],
            "oversize_skipped": dl.attrs["oversize_skipped"],
            "failed": dl.attrs["failed"],
        }
        if disk:
            out["on_disk"] = on_disk
            out["sizes"] = [_size(os.path.join(sess, f)) for f in on_disk]
        return out
    finally:
        options(old)
        shutil.rmtree(sess, ignore_errors=True)


def download_spy(routes: list[dict[str, Any]], files: pd.DataFrame, **kw: Any) -> dict[str, Any]:
    """``download_repo_files()`` with its archive and one-file transports recorded.

    Mirrors ``rv_download_spy()``: which archive is requested (and with what
    byte limit) and which files are then fetched one by one.
    """
    import math

    from metacheck.archives import download as dl

    zips: list[dict[str, Any]] = []
    ones: list[str] = []

    def spy_zip(
        files: pd.DataFrame,
        row_idx: Any,
        zip_url: str,
        strip_dir: bool = False,
        req_func: Any = None,
        timeout_s: float = 120,
        max_bytes: float = math.inf,
        skip_on_api_limit: bool = False,
        expected_bytes: float = math.nan,
        _inplace: bool = False,
    ) -> pd.DataFrame:
        zips.append(
            {
                "zip_url": zip_url,
                "rows": len(row_idx),
                "strip_dir": strip_dir,
                "max_bytes": float(max_bytes),
                "expected_bytes": float(expected_bytes),
            }
        )
        return files

    def spy_one(
        url: str, dest: str, skip_on_api_limit: bool = False, expected_bytes: float = math.nan
    ) -> None:
        ones.append(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        Path(dest).write_bytes(b"\x01")
        return None

    real_zip, real_one = dl._download_zip_to_cache, dl._download_one
    dl._download_zip_to_cache, dl._download_one = spy_zip, spy_one  # type: ignore[assignment]
    try:
        out = download(routes, files, disk=False, **kw)
    finally:
        dl._download_zip_to_cache, dl._download_one = real_zip, real_one  # type: ignore[assignment]
    return {"zips": zips, "downloaded": ones, **out}


def expand(fn: str, fixture: str, skip_types: Any = "materials") -> Any:
    from metacheck.archives import zip_peek

    d = tempfile.mkdtemp(prefix="rv_")
    try:
        f = os.path.join(d, os.path.basename(fixture))
        shutil.copyfile(DATA / fixture, f)
        row = pd.DataFrame(
            {
                "repo_url": ["r"],
                "file_name": [os.path.basename(fixture)],
                "file_path": [os.path.basename(fixture)],
                "file_url": ["u"],
                "file_location": ["<unset>"],
                "file_size": [1.0],
                "data_type": ["unknown"],
                "doc_role": pd.Series([None], dtype="string"),
                "data_format": ["x"],
            }
        )
        out = getattr(zip_peek, fn)(f, row, skip_types=skip_types)
        out["file_location"] = pd.Series(
            [
                None if v is None or v is pd.NA else str(v).replace(d, "<TMP>")
                for v in out["file_location"]
            ],
            dtype="string",
        )
        return _df_bytes(out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def category(x: Any) -> Any:
    from metacheck.fileinfo.category import file_category

    return _df_bytes(file_category(x))


def filetype(x: Any) -> Any:
    from metacheck.fileinfo.category import filetype as ft

    out = ft(x)
    out.index = pd.Index([_bytes_text(v) for v in out.index], dtype=object)
    return out
