"""``zenodo_file_download(unzip_types = ...)`` through the real ``zip_peek()``.

The other Zenodo tests stub ``_zenodo_zip_members()``; these serve a real zip
with byte-range support (see :mod:`tests.archives_gz.zipserve`), so the
central-directory listing, the member classification and the per-member range
fetches are the ones a user gets.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import respx

from metacheck.archives import zenodo
from metacheck.archives.zenodo import _zenodo_id, _zenodo_zip_members, zenodo_file_download
from tests.archives_gz.zipserve import MEMBERS, ZipServer, fresh_peek_cache, make_zip

URL = "https://zenodo.org/api/records/24680/files/bundle.zip/content"
DATA = make_zip()


@pytest.fixture(autouse=True)
def _peek_cache() -> Iterator[None]:
    with fresh_peek_cache():
        yield


def _serve(server: ZipServer) -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.route(url=URL).mock(side_effect=server)
    return router


def test_zip_members_fetches_only_wanted_members(tmp_path: Path) -> None:
    server = ZipServer(DATA)
    with _serve(server):
        out = _zenodo_zip_members(URL, str(tmp_path), keep_types="data", max_file_size=1)
    assert out is not None
    # big.csv (1.2 MB) is data too, but over the 1 MB cap
    assert out["name"].tolist() == ["bundle/data/study.csv"]
    assert out["ok"].tolist() == [True]
    assert (tmp_path / "bundle" / "data" / "study.csv").read_bytes() == MEMBERS[
        "bundle/data/study.csv"
    ]
    assert not (tmp_path / "bundle" / "data" / "big.csv").exists()
    assert not (tmp_path / "bundle" / "stimuli").exists()
    # a HEAD, then range requests only: the 128 KB tail holding the central
    # directory, and the one small member
    assert server.calls[0] == ("HEAD", None)
    assert all(rng is not None for method, rng in server.calls if method == "GET")
    assert server.sent < len(DATA) // 4


def test_zip_members_several_types_and_no_cap(tmp_path: Path) -> None:
    with _serve(ZipServer(DATA)):
        out = _zenodo_zip_members(
            URL, str(tmp_path), keep_types=["data", "documentation"], max_file_size=None
        )
    assert out is not None
    assert sorted(out["name"].tolist()) == [
        "bundle/data/big.csv",
        "bundle/data/study.csv",
        "bundle/docs/README.txt",
    ]
    assert out["ok"].tolist() == [True, True, True]
    for name in out["name"]:
        assert (tmp_path / name).read_bytes() == MEMBERS[name]


def test_zip_members_nothing_wanted_and_unlistable(tmp_path: Path) -> None:
    with _serve(ZipServer(DATA)):
        none = _zenodo_zip_members(URL, str(tmp_path), keep_types="output")
    assert none is not None and len(none) == 0
    assert list(none.columns) == ["name", "size"]

    with fresh_peek_cache(), _serve(ZipServer(DATA, head_length=False)):
        # no Content-Length: the listing cannot be read, so the caller downloads whole
        assert _zenodo_zip_members(URL, str(tmp_path), keep_types="data") is None


def _record(zenodo_url: Any, id_col: Any = 1, pb: Any = None, cache: bool = False) -> pd.DataFrame:
    zid = _zenodo_id(zenodo_url)
    files = [
        {
            "id": "f1",
            "key": "bundle.zip",
            "size": len(DATA),
            "checksum": "md5:" + hashlib.md5(DATA, usedforsecurity=False).hexdigest(),
            "links": {"self": URL},
        }
    ]
    return pd.DataFrame({"zenodo_url": [str(zenodo_url)], "zenodo_id": [zid], "files": [files]})


def test_file_download_extracts_members_through_zip_peek(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(zenodo, "zenodo_info", _record)
    server = ZipServer(DATA)
    with _serve(server):
        dl = zenodo_file_download(
            "24680", download_to=str(tmp_path), unzip_types="data", max_file_size=1
        )
    assert dl["key"].tolist() == ["bundle.zip"]
    assert dl["downloaded"].tolist() == [True]
    assert dl["extracted"].tolist() == [1]
    target = tmp_path / "24680"
    assert (target / "bundle" / "data" / "study.csv").read_bytes() == MEMBERS[
        "bundle/data/study.csv"
    ]
    assert not (target / "bundle.zip").exists()
    # the 128 KB tail holding the central directory, then the one small member
    assert server.sent < len(DATA) // 4


def test_file_download_listed_size_replaces_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(zenodo, "zenodo_info", _record)
    server = ZipServer(DATA, head_length=False)
    with _serve(server):
        dl = zenodo_file_download(
            "24680", download_to=str(tmp_path), unzip_types="data", max_file_size=1
        )
    # the host gives no size to HEAD, but the record's listing does: the archive is
    # listed with that size (metacheck d52b3fd) and its member extracted
    assert dl["extracted"].tolist() == [1]
    assert dl["downloaded"].tolist() == [True]
    assert "HEAD" not in [m for m, _ in server.calls]
    assert not (tmp_path / "24680" / "bundle.zip").exists()

    # with no size anywhere the archive cannot be listed: fetched whole, and
    # checked like any file
    def unsized(*args: Any, **kwargs: Any) -> pd.DataFrame:
        rec = _record(*args, **kwargs)
        rec["files"] = [[{k: v for k, v in f.items() if k != "size"} for f in rec["files"][0]]]
        return rec

    monkeypatch.setattr(zenodo, "zenodo_info", unsized)
    with fresh_peek_cache(), _serve(ZipServer(DATA, head_length=False)):
        dl = zenodo_file_download(
            "24680", download_to=str(tmp_path / "again"), unzip_types="data", max_file_size=1
        )
    assert pd.isna(dl["extracted"].iloc[0])
    assert dl["downloaded"].tolist() == [True]
    assert (tmp_path / "again" / "bundle.zip").read_bytes() == DATA
