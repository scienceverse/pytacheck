"""Python halves of the zip-mode budget cases (``osf_file_download.zip.*``).

Mirrors ``tests/archives_osf/zip_budget_helpers.R``: the listing is a table
built from the arguments (one component, ``child1``, of project ``abcde``),
the archive request is answered with a fixture zip from ``data/zip_budget``
(through the real transport, so the byte limit is enforced as it streams),
and the files fetched one by one are written with their listed size.

Run as a script to rebuild the fixture::

    .venv/bin/python tests/archives_osf/_zip_budget_helpers.py
"""

from __future__ import annotations

import math
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "zip_budget"
ZIP_URL = "https://files.osf.io/v1/resources/child1/providers/osfstorage/?zip="
A_CSV = b"id,x\n" + b"".join(f"{i},{i * 7}\n".encode() for i in range(5))
B_CSV = b"id,y\n" + b"".join(f"{i},{i * 11}\n".encode() for i in range(7))


def make_fixture() -> None:
    """Write ``data/zip_budget/child1.zip`` (a.csv, b.csv; fixed timestamps)."""
    DATA.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DATA / "child1.zip", "w") as zf:
        for name, data in (("a.csv", A_CSV), ("b.csv", B_CSV)):
            info = zipfile.ZipInfo(name, date_time=(2024, 1, 2, 3, 4, 6))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)


def zip_download(
    name: list[str],
    size: list[float],
    provider: list[str],
    budget_bytes: float | None = None,
    archive: str | None = None,
    unzip: bool = True,
) -> dict[str, Any]:
    """``osf_file_download("abcde", mode = "zip")`` against the listing and archive above."""
    import httpx
    import pandas as pd
    import respx

    from metacheck.archives import download, osf
    from tests.archives_osf.parity_support import download_summary

    n = len(name)
    contents = pd.DataFrame(
        {
            "osf_type": ["files"] * n + ["nodes"],
            "osf_id": [f"file{i + 1}" for i in range(n)] + ["child1"],
            "name": [*name, "Data"],
            "provider": [*provider, None],
            "path": [f"/{x}" for x in name] + [None],
            "kind": ["file"] * n + ["folder"],
            "size": [float(x) for x in size] + [math.nan],
            "download_url": [f"https://example.test/{x}" for x in name] + [None],
            "parent": ["child1"] * n + ["abcde"],
            "project": ["child1"] * n + ["abcde"],
            "filetype": ["csv"] * n + [None],
            "downloads": [1.0] * n + [math.nan],
        }
    )
    mds = math.inf if budget_bytes is None else budget_bytes / 1024**2
    archives: list[dict[str, Any]] = []
    fetched: list[str] = []
    real_stream = osf._stream_to_file

    def stream(url: str, path: str, *args: Any, max_bytes: float = math.inf, **kw: Any) -> int:
        archives.append({"url": url, "max_bytes": max_bytes if math.isfinite(max_bytes) else None})
        return real_stream(url, path, *args, max_bytes=max_bytes, **kw)

    def many(urls: list[str], dests: list[str], expected: Any = None, **kw: Any) -> list[Any]:
        fetched.extend(urls)
        sizes = list(expected) if expected is not None else [math.nan] * len(dests)
        for dest, s in zip(dests, sizes, strict=True):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(bytes(1 if s != s else int(s)))
        return [None] * len(urls)

    body = (DATA / archive).read_bytes() if archive else None
    with (
        tempfile.TemporaryDirectory(prefix="ob_") as d,
        respx.mock(assert_all_called=False) as router,
        mock.patch.object(osf, "osf_info", lambda *a, **k: contents),
        mock.patch.object(osf, "osf_type", lambda *a, **k: "nodes"),
        mock.patch.object(osf, "_stream_to_file", stream),
        mock.patch.object(download, "_download_many_parallel", many),
    ):
        router.get(ZIP_URL).mock(
            return_value=httpx.Response(200, content=body)
            if body is not None
            else httpx.Response(404)
        )
        dl = osf.osf_file_download(
            "abcde", d, mode="zip", unzip=unzip, max_download_size=mds, metadata=False
        )
        out = download_summary(dl, d)
    return {"value": out["value"], "archives": archives, "fetched": fetched, "files": out["files"]}


if __name__ == "__main__":
    make_fixture()
