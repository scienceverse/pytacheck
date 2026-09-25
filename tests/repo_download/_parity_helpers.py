"""Python halves of the ``$expr`` parity cases in ``parity/cases/repo_download.yaml``.

Each helper mirrors the inline R code of its case: fixtures are read from
``tests/repo_download/data``, archives are expanded / files downloaded into a
fresh temporary directory, and temporary paths in the result are replaced by
``<TMP>`` / ``<SESSION>`` so R and Python can be compared.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def path(rel: str) -> str:
    """Absolute path of a fixture in ``tests/repo_download/data``."""
    return str(DATA / rel)


def raw(rel: str, tail: int | None = None, head: int | None = None) -> bytes:
    """The bytes of a fixture (R: ``readBin(path, "raw", n)``), optionally the tail/head."""
    data = (DATA / rel).read_bytes()
    if tail is not None:
        data = data[-tail:] if tail > 0 else b""
    if head is not None:
        data = data[:head]
    return data


def member_bytes(rel: str, name: str) -> tuple[bytes, float, float]:
    """A member's compressed bytes, method and size, read the way ``.zip_member_fetch()`` does."""
    from pytacheck.archives.zip_peek import _le_int, _parse_zip_central_dir

    data = raw(rel)
    cd = _parse_zip_central_dir(data)
    assert cd is not None
    row = cd.loc[cd["name"] == name].iloc[0]
    off = int(row["offset"])
    lh = data[off : off + 30]
    start = off + 30 + int(_le_int(lh, 27, 2) + _le_int(lh, 29, 2))
    return data[start : start + int(row["csize"])], float(row["method"]), float(row["size"])


def hexbytes(x: Any) -> list[str] | None:
    """R ``as.character()`` of a raw vector: two lower-case hex digits per byte."""
    if x is None:
        return None
    return [f"{b:02x}" for b in bytes(x)]


def inflate(rel: str, name: str, with_size: bool = True, method: float | None = None) -> Any:
    """``.zip_inflate_member()`` of a fixture member; returns ``length()`` and the CRC32."""
    from pytacheck.archives.zip_peek import _crc32, _zip_inflate_member

    comp, m, size = member_bytes(rel, name)
    out = _zip_inflate_member(comp, m if method is None else method, size if with_size else None)
    if out is None:
        return None
    return {"length": len(out), "crc": _crc32(out)}


def _row(name: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": ["r"],
            "file_name": [name],
            "file_path": [name],
            "file_url": ["u"],
            "file_location": ["<unset>"],
            "file_size": [1.0],
            "file_type": ["archive"],
            "paper_id": ["p.1"],
            "data_type": ["unknown"],
            "doc_role": pd.Series([None], dtype="string"),
            "data_format": ["tabular"],
            "group": pd.Series([None], dtype="string"),
        }
    )


def expand(fn: str, fixture: str, skip_types: Any = "materials", minimal: bool = False) -> Any:
    """Copy *fixture* to a temp dir and run ``.expand_zip``/``.expand_tar``/``.expand_compressed``."""
    from pytacheck.archives import zip_peek

    d = tempfile.mkdtemp(prefix="pc_rd_")
    try:
        f = os.path.join(d, fixture)
        shutil.copyfile(DATA / fixture, f)
        row = _row(fixture)
        if minimal:
            row = row[["repo_url", "file_name"]]
        out = getattr(zip_peek, fn)(f, row, skip_types=skip_types)
        if "file_location" in out.columns:
            out["file_location"] = pd.Series(
                [
                    None if v is None or v is pd.NA else str(v).replace(d, "<TMP>")
                    for v in out["file_location"].tolist()
                ],
                dtype="string",
            )
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def fetch_members(url: str, names: Any = None) -> Any:
    """``.zip_fetch_members(url, names, dest = <temp dir>)`` with the temp dir as ``<TMP>``."""
    from pytacheck.archives.zip_peek import _zip_fetch_members

    d = tempfile.mkdtemp(prefix="pc_rd_")
    try:
        out = _zip_fetch_members(url, names=names, dest=d)
        if out is not None and "path" in out.columns:
            out["path"] = pd.Series(
                [
                    None if v is None or v is pd.NA else str(v).replace(d, "<TMP>")
                    for v in out["path"].tolist()
                ],
                dtype="string",
            )
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def download(
    files: pd.DataFrame,
    file_url: list[str | None] | None = None,
    twice: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """``download_repo_files(files, ...)`` into a throwaway session dir; paths as ``<SESSION>``.

    *file_url* entries starting with ``data/`` become ``file://`` URLs of fixtures.
    With *twice*, the download is repeated (the second run reuses the cache)
    and the second result is returned.
    """
    from pytacheck.archives.download import download_repo_files
    from pytacheck.utils import get_option, options

    files = files.copy()
    if file_url is not None:
        files["file_url"] = [
            f"file://{DATA / u[5:]}" if isinstance(u, str) and u.startswith("data/") else u
            for u in file_url
        ]
    sess = tempfile.mkdtemp(prefix="pc_sess_")
    old = {
        "metacheck.repo_cache.session_dir": get_option("metacheck.repo_cache.session_dir"),
        "metacheck.repo_cache.notified": get_option("metacheck.repo_cache.notified"),
    }
    options({"metacheck.repo_cache.session_dir": sess, "metacheck.repo_cache.notified": True})
    try:
        dl = download_repo_files(files, **kwargs)
        if twice:
            dl = download_repo_files(files, **kwargs)
        assert dl is not None
        loc = [
            None if v is None or v is pd.NA else str(v).replace(sess, "<SESSION>")
            for v in dl["file_location"].tolist()
        ]
        return {
            "file_location": loc,
            "gated": dl.attrs["gated"],
            "oversize_skipped": dl.attrs["oversize_skipped"],
            "failed": _failed(dl.attrs["failed"]),
        }
    finally:
        options(old)
        shutil.rmtree(sess, ignore_errors=True)


def _failed(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["file_url"] = pd.Series(
        [
            None if v is None or v is pd.NA else str(v).replace(str(DATA), "<DATA>")
            for v in out["file_url"].tolist()
        ],
        dtype="string",
    )
    out["error"] = pd.Series(
        [
            None if v is None or v is pd.NA else str(v).replace(str(DATA), "<DATA>")
            for v in out["error"].tolist()
        ],
        dtype="string",
    )
    return out


def rate_limit_response(
    status: int, headers: dict[str, str], reset_offset: float | None = None
) -> Any:
    """An ``httr2::response()``-like object; *reset_offset* adds ``now + offset`` resets."""
    import time

    import httpx

    headers = dict(headers)
    if reset_offset is not None:
        headers["ratelimit-reset"] = str(round(time.time() + reset_offset))
    return httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://x.org/a"))


def nan() -> float:
    return math.nan


def do_call(what: Any, args: dict[str, Any] | None = None) -> Any:
    """R ``do.call(what, args)`` with a named argument list."""
    return what(**(args or {}))


def identity(x: Any = None) -> Any:
    """R ``identity()``."""
    return x
