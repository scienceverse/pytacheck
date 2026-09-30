"""``unzip_types`` in the Dataverse/Figshare/Dryad downloads through the real ``zip_peek()``.

``dataverse._zip_members()`` is the body of ``.dataverse_zip_members()``,
``.figshare_zip_members()`` and ``.dryad_zip_members()``. The other tests
stub it; these serve a real zip with byte-range support (see
:mod:`tests.archives_gz.zipserve`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import respx

from metacheck.archives import dataverse
from metacheck.archives.dataverse import _zip_members, dataverse_file_download
from metacheck.archives.dryad import _dryad_zip_members
from metacheck.archives.figshare import _figshare_zip_members
from tests.archives_gz.zipserve import MEMBERS, ZipServer, fresh_peek_cache, make_zip

URL = "https://dataverse.harvard.edu/api/access/datafile/42"
DATA = make_zip()


@pytest.fixture(autouse=True)
def _peek_cache() -> Iterator[None]:
    with fresh_peek_cache():
        yield


def _serve(server: ZipServer) -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.route(url=URL).mock(side_effect=server)
    return router


@pytest.mark.parametrize("members", [_zip_members, _dryad_zip_members, _figshare_zip_members])
def test_zip_members_fetch_wanted_members(members: Any, tmp_path: Path) -> None:
    server = ZipServer(DATA)
    with _serve(server):
        out = members(URL, str(tmp_path), "data", 1)
    assert out is not None
    assert out["name"].tolist() == ["bundle/data/study.csv"]  # big.csv is over 1 MB
    assert out["ok"].tolist() == [True]
    assert (tmp_path / "bundle" / "data" / "study.csv").read_bytes() == MEMBERS[
        "bundle/data/study.csv"
    ]
    assert not (tmp_path / "bundle" / "data" / "big.csv").exists()
    assert server.sent < len(DATA) // 4


def test_zip_members_types_cap_and_failures(tmp_path: Path) -> None:
    with _serve(ZipServer(DATA)):
        out = _zip_members(URL, str(tmp_path), ["code", "materials"], None)
        none = _zip_members(URL, str(tmp_path), "output", None)
    assert out is not None
    assert sorted(out["name"].tolist()) == ["bundle/code/analysis.R", "bundle/stimuli/face.png"]
    for name in out["name"]:
        assert (tmp_path / name).read_bytes() == MEMBERS[name]
    assert none is not None and len(none) == 0 and list(none.columns) == ["name", "size"]

    with fresh_peek_cache(), _serve(ZipServer(DATA, head_length=False)):
        assert _zip_members(URL, str(tmp_path), "data", 10) is None


def _zip_dataset(host: str, doi: str, pb: Any = None) -> pd.DataFrame:
    files = [
        {
            "label": "bundle.zip",
            "dataFile": {
                "id": 42,
                "filename": "bundle.zip",
                "filesize": len(DATA),
                "checksum": {
                    "type": "MD5",
                    "value": hashlib.md5(DATA, usedforsecurity=False).hexdigest(),
                },
            },
        }
    ]
    col = pd.Series([None], dtype=object)
    col.iloc[0] = files
    return pd.DataFrame({"dataverse_host": [host], "dataverse_doi": [doi], "files": col})


def test_dataverse_file_download_extracts_through_zip_peek(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dataverse, "_dataverse_info", _zip_dataset)
    server = ZipServer(DATA)
    with _serve(server):
        dl = dataverse_file_download(
            "dataverse.harvard.edu",
            "10.7910/DVN/ZIPREAL",
            download_to=str(tmp_path),
            unzip_types=["data", "documentation"],
            max_file_size=1,
        )
    assert dl is not None and dl["key"].tolist() == ["bundle.zip"]
    assert dl["downloaded"].tolist() == [True]
    assert dl["extracted"].tolist() == [2]  # study.csv and README.txt; big.csv is over 1 MB
    target = tmp_path / "10.7910_DVN_ZIPREAL"
    for name in ("bundle/data/study.csv", "bundle/docs/README.txt"):
        assert (target / name).read_bytes() == MEMBERS[name]
    assert not (target / "bundle.zip").exists()
    assert server.sent < len(DATA) // 4


def test_dataverse_file_download_whole_zip_without_ranges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dataverse, "_dataverse_info", _zip_dataset)
    with _serve(ZipServer(DATA, head_length=False)):
        dl = dataverse_file_download(
            "dataverse.harvard.edu",
            "10.7910/DVN/ZIPWHOLE",
            download_to=str(tmp_path),
            unzip_types="data",
            max_file_size=1,
        )
    assert dl is not None
    assert pd.isna(dl["extracted"].iloc[0])
    assert dl["downloaded"].tolist() == [True]
    assert dl["checksum_ok"].tolist() == [True]
    assert (tmp_path / "10.7910_DVN_ZIPWHOLE" / "bundle.zip").read_bytes() == DATA
