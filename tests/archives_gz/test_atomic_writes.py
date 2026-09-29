"""A stopped download or extraction leaves nothing half-written in the shared cache."""

from __future__ import annotations

import gzip
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from pytacheck.archives import zip_peek as zp
from pytacheck.archives._atomic import atomic_write
from pytacheck.archives.download import _perform_once

URL = "https://files.example.test/a.csv"


class _Stop(BaseException):
    """What the app's Stop button raises: not an ``Exception``."""


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".part"))


def _fake_client(monkeypatch: pytest.MonkeyPatch, chunks: list[bytes], stop: bool) -> None:
    """A client whose body streams *chunks*, then (when *stop*) raises ``_Stop``."""
    from pytacheck import http

    class _Resp:
        status_code = 200
        is_redirect = False
        next_request = None

        def iter_bytes(self, chunk_size: int | None = None) -> Any:
            yield from chunks
            if stop:
                raise _Stop

        def read(self) -> bytes:
            return b"".join(chunks)

        def close(self) -> None:
            pass

    class _Client:
        def build_request(self, method: str, url: str, **kw: Any) -> httpx.Request:
            return httpx.Request(method, url)

        def send(self, req: httpx.Request, **kw: Any) -> _Resp:
            return _Resp()

    monkeypatch.setattr(http, "client_for", lambda url: _Client())


def test_an_interrupted_download_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_client(monkeypatch, [b"a,b\n", b"1,2\n"], stop=True)
    path = tmp_path / "a.csv"
    with pytest.raises(_Stop):
        _perform_once({"method": "GET", "url": URL}, path=str(path))
    assert not path.exists()
    assert _leftovers(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_a_finished_download_lands_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_client(monkeypatch, [b"a,b\n", b"1,2\n"], stop=False)
    path = tmp_path / "a.csv"
    _perform_once({"method": "GET", "url": URL}, path=str(path))
    assert path.read_bytes() == b"a,b\n1,2\n"
    assert list(tmp_path.iterdir()) == [path]


def test_an_interrupted_download_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"old complete bytes")
    _fake_client(monkeypatch, [b"new "], stop=True)
    with pytest.raises(_Stop):
        _perform_once({"method": "GET", "url": URL}, path=str(path))
    assert path.read_bytes() == b"old complete bytes"
    assert _leftovers(tmp_path) == []


def test_atomic_write_removes_its_file_when_the_move_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def replace(src: str, dst: str) -> None:
        raise PermissionError(13, "in use", dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PermissionError), atomic_write(str(tmp_path / "f")) as fh:
        fh.write(b"x")
    assert list(tmp_path.iterdir()) == []


def test_an_interrupted_unzip_keeps_the_old_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zpath = tmp_path / "z.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("x.txt", "new contents")
    out = tmp_path / "out"
    out.mkdir()
    (out / "x.txt").write_bytes(b"old")

    def copy(src: Any, dst: Any, *a: Any) -> None:
        dst.write(src.read(3))
        raise _Stop

    monkeypatch.setattr(shutil, "copyfileobj", copy)
    with pytest.raises(_Stop):
        zp._unzip_all(str(zpath), str(out))
    assert (out / "x.txt").read_bytes() == b"old"
    assert _leftovers(out) == []
    monkeypatch.undo()
    zp._unzip_all(str(zpath), str(out))
    assert (out / "x.txt").read_bytes() == b"new contents"
    assert _leftovers(out) == []


def test_an_interrupted_gz_extraction_leaves_nothing_to_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandas as pd

    gz = tmp_path / "d.csv.gz"
    gz.write_bytes(gzip.compress(b"a,b\n1,2\n"))
    row = pd.DataFrame({"name": ["d.csv.gz"], "file_location": [str(gz)]})

    class _Con:
        calls = 0

        def __enter__(self) -> _Con:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def read(self, n: int) -> bytes:
            _Con.calls += 1
            if _Con.calls > 1:
                raise _Stop
            return b"a,b\n"

    monkeypatch.setattr(zp, "_open_compressed", lambda p, e: _Con())
    with pytest.raises(_Stop):
        zp._expand_compressed(str(gz), row)
    contents = tmp_path / "d.csv.gz.contents"
    assert not (contents / "d.csv").exists()
    assert os.listdir(contents) == []
