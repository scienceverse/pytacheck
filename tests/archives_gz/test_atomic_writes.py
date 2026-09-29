"""A stopped download or extraction leaves nothing half-written in the shared cache."""

from __future__ import annotations

import gzip
import os
import re
import shutil
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from pytacheck.archives import zip_peek as zp
from pytacheck.archives._atomic import atomic_write, staged_dir, sweep_stale
from pytacheck.archives.download import _perform_once

TEMP = re.compile(r"^\.~[0-9a-f]{4,12}$")
URL = "https://files.example.test/a.csv"


class _Stop(BaseException):
    """What the app's Stop button raises: not an ``Exception``."""


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if TEMP.match(p.name))


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


def _zip(path: Path, members: dict[str, bytes], method: int = zipfile.ZIP_STORED) -> None:
    with zipfile.ZipFile(path, "w", method) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _row() -> Any:
    import pandas as pd

    return pd.DataFrame({"name": ["z"], "file_location": ["z"]})


def _tree(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


_MEMBERS = {"a.csv": b"a,b\n1,2\n", "b.csv": b"c,d\n3,4\n", "c.csv": b"e\n5\n"}


def test_a_stopped_zip_extraction_leaves_nothing_and_the_next_run_extracts_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zpath = tmp_path / "z.zip"
    _zip(zpath, _MEMBERS)
    real = shutil.copyfileobj
    calls = []

    def copy(src: Any, dst: Any, *a: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise _Stop
        real(src, dst, *a)

    monkeypatch.setattr(shutil, "copyfileobj", copy)
    with pytest.raises(_Stop):
        zp._expand_zip(str(zpath), _row())
    assert list(tmp_path.iterdir()) == [zpath]  # no .contents, no temp folder
    monkeypatch.undo()
    rows = zp._expand_zip(str(zpath), _row())
    assert len(rows) == 3
    assert _tree(tmp_path / "z.zip.contents") == sorted(_MEMBERS)
    assert _leftovers(tmp_path) == []


def test_an_ordinary_error_mid_zip_extraction_keeps_the_partial_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zpath = tmp_path / "z.zip"
    _zip(zpath, _MEMBERS)
    real = shutil.copyfileobj
    calls = []

    def copy(src: Any, dst: Any, *a: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("boom")
        real(src, dst, *a)

    monkeypatch.setattr(shutil, "copyfileobj", copy)
    zp._expand_zip(str(zpath), _row())
    assert (tmp_path / "z.zip.contents" / "a.csv").read_bytes() == _MEMBERS["a.csv"]
    assert not (tmp_path / "z.zip.contents" / "c.csv").exists()
    assert _leftovers(tmp_path) == []


def test_a_corrupt_zip_member_is_kept_partial_as_r_keeps_it(tmp_path: Path) -> None:
    zpath = tmp_path / "z.zip"
    good = b"x,y\n" + b"1,2\n" * 200
    _zip(zpath, {"bad.csv": good, "good.csv": good}, zipfile.ZIP_DEFLATED)
    raw = bytearray(zpath.read_bytes())
    start = 30 + len("bad.csv")  # data of the first member
    for i in range(start + 2, start + 12):
        raw[i] ^= 0xFF
    zpath.write_bytes(bytes(raw))
    zp._expand_zip(str(zpath), _row())
    out = tmp_path / "z.zip.contents"
    assert (out / "bad.csv").exists()
    assert len((out / "bad.csv").read_bytes()) < len(good)
    assert _leftovers(tmp_path) == []


def test_a_lost_race_keeps_the_existing_folder(tmp_path: Path) -> None:
    dest = tmp_path / "d.contents"
    with staged_dir(str(dest)) as stage:
        Path(stage, "mine.txt").write_text("mine")
        dest.mkdir()  # another process finished first
        (dest / "theirs.txt").write_text("theirs")
    assert _tree(dest) == ["theirs.txt"]
    assert _leftovers(tmp_path) == []


def test_staged_dir_moves_on_success_and_drops_on_stop(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    with staged_dir(str(dest)) as stage:
        Path(stage, "f").write_text("1")
    assert _tree(dest) == ["f"]
    other = tmp_path / "e"
    with pytest.raises(_Stop), staged_dir(str(other)) as stage:
        Path(stage, "f").write_text("1")
        raise _Stop
    assert not other.exists()
    assert _leftovers(tmp_path) == []


def test_a_stopped_tar_extraction_leaves_nothing_and_the_next_run_extracts_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tpath = tmp_path / "t.tar"
    with tarfile.open(tpath, "w") as tf:
        for name, data in _MEMBERS.items():
            src = tmp_path / f"src_{name}"
            src.write_bytes(data)
            tf.add(src, arcname=name)
    real = tarfile.TarFile.extract
    calls = []

    def extract(self: Any, member: Any, *a: Any, **kw: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise _Stop
        real(self, member, *a, **kw)

    monkeypatch.setattr(tarfile.TarFile, "extract", extract)
    with pytest.raises(_Stop):
        zp._expand_tar(str(tpath), _row())
    assert not (tmp_path / "t.tar.contents").exists()
    assert _leftovers(tmp_path) == []
    monkeypatch.undo()
    zp._expand_tar(str(tpath), _row())
    assert _tree(tmp_path / "t.tar.contents") == sorted(_MEMBERS)


def test_sweep_stale_removes_old_temp_names_only(tmp_path: Path) -> None:
    sub = tmp_path / "repo" / "x"
    sub.mkdir(parents=True)
    old_file, new_file = sub / ".~abcdef01", sub / ".~abcdef02"
    old_dir, new_dir = sub / ".~0123456789ab", sub / ".~ab12"
    plain, lookalike = sub / "data.csv", sub / ".~notHEX"
    for f in (old_file, new_file, plain, lookalike):
        f.write_bytes(b"x")
    (old_dir).mkdir()
    (old_dir / "inner").write_bytes(b"x")
    new_dir.mkdir()
    old = time.time() - 7 * 3600
    for p in (old_file, old_dir, plain, lookalike):
        os.utime(p, (old, old))
    sweep_stale(str(tmp_path), 6 * 3600)
    assert sorted(p.name for p in sub.iterdir()) == sorted(
        [new_file.name, new_dir.name, plain.name, lookalike.name]
    )
    sweep_stale(str(tmp_path / "missing"), 1)  # never raises


def test_the_temp_name_is_no_longer_than_the_target_where_possible() -> None:
    from pytacheck.archives._atomic import _TEMP_NAME, _temp_name

    for base, n in [("a.csv", 4), ("x" * 8, 6), ("x" * 14, 12), ("x" * 100, 12)]:
        name = _temp_name("/c/" + base)
        assert _TEMP_NAME.match(name)
        assert len(name) == 2 + n
        if len(base) >= 6:
            assert len(name) <= len(base)


def test_an_oserror_names_the_target_not_the_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.dat"

    def replace(src: str, dst: str) -> None:
        raise PermissionError(13, "in use", src)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PermissionError) as ei, atomic_write(str(target)) as fh:
        fh.write(b"x")
    assert ei.value.filename == str(target)
    assert ".~" not in str(ei.value)
    monkeypatch.undo()
    with pytest.raises(FileNotFoundError) as ej, atomic_write(str(tmp_path / "no" / "f")):
        pass
    assert ej.value.filename == str(tmp_path / "no" / "f")


def test_the_move_is_retried_on_windows_while_a_file_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pytacheck.archives import _atomic

    calls = []

    def replace(src: str, dst: str) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "held", src)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(_atomic.time, "sleep", lambda s: None)
    monkeypatch.setattr(_atomic.os, "name", "nt")
    _atomic._replace("a", "b")
    assert len(calls) == 3
