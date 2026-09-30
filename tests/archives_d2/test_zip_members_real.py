"""``reshare_file_download(unzip_types = ...)`` through the real ``zip_peek()``.

The deposit's zip is served with byte-range support (see
:mod:`tests.archives_gz.zipserve`), so the listing, the member choice and the
range fetches are the real ones.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Iterator
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives.reshare import _reshare_zip_members, reshare_file_download
from tests.archives_gz.zipserve import MEMBERS, ZipServer, fresh_peek_cache, make_zip

RECORD = "https://reshare.ukdataservice.ac.uk/id/eprint/5"
FILE = "https://reshare.ukdataservice.ac.uk/id/file/7"
DATA = make_zip()


@pytest.fixture(autouse=True)
def _peek_cache() -> Iterator[None]:
    with fresh_peek_cache():
        yield


def _record() -> httpx.Response:
    rec = {
        "title": "Zipped",
        "documents": [
            {
                "content": "data",
                "files": [
                    {
                        "fileid": 7,
                        "filename": "all.zip",
                        "filesize": len(DATA),
                        "hash": hashlib.md5(DATA, usedforsecurity=False).hexdigest(),
                        "hash_type": "MD5",
                        "uri": "http://reshare.ukdataservice.ac.uk/id/file/7",
                    }
                ],
            }
        ],
    }
    return httpx.Response(
        200, content=json.dumps(rec).encode(), headers={"content-type": "application/json"}
    )


def _serve(server: ZipServer) -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.route(url=RECORD).mock(return_value=_record())
    router.route(url=FILE).mock(side_effect=server)
    return router


def test_reshare_zip_members(tmp_path: Path) -> None:
    with _serve(ZipServer(DATA)):
        out = _reshare_zip_members(FILE, str(tmp_path), keep_types="documentation")
    assert out is not None
    assert out["name"].tolist() == ["bundle/docs/README.txt"]
    assert (tmp_path / "bundle" / "docs" / "README.txt").read_bytes() == MEMBERS[
        "bundle/docs/README.txt"
    ]


def test_reshare_file_download_extracts_through_zip_peek(tmp_path: Path) -> None:
    server = ZipServer(DATA)
    with _serve(server), warnings.catch_warnings():
        warnings.simplefilter("error")
        # the zip is over the (tiny) size cap but exempt from it: only members count
        files = reshare_file_download(
            "5", str(tmp_path), max_file_size=0.0001, unzip_types=["data", "code"]
        )
    assert files is not None
    assert files["key"].tolist() == ["all.zip"]
    assert files["downloaded"].tolist() == [True]
    # members do follow max_file_size: 0.0001 MB (104 bytes) admits the 13-byte
    # study.csv and the 34-byte script, not big.csv
    assert files["extracted"].tolist() == [2]
    target = tmp_path / "reshare_5"
    for name in ("bundle/data/study.csv", "bundle/code/analysis.R"):
        assert (target / name).read_bytes() == MEMBERS[name]
    assert not (target / "all.zip").exists()
    assert server.sent < len(DATA) // 4


def test_reshare_file_download_whole_zip_without_ranges(tmp_path: Path) -> None:
    with _serve(ZipServer(DATA, head_length=False)):
        files = reshare_file_download("5", str(tmp_path), max_file_size=None, unzip_types="data")
    assert files is not None
    assert pd.isna(files["extracted"].iloc[0])
    assert files["downloaded"].tolist() == [True]
    assert (tmp_path / "reshare_5" / "all.zip").read_bytes() == DATA
