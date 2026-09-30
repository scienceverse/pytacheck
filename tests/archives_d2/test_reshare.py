"""Tests for metacheck.archives.reshare (R/archive-reshare.R has no testthat file upstream)."""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives.reshare import (
    _reshare_headers,
    _reshare_id,
    _reshare_info,
    _reshare_verify_downloads,
    reshare_file_download,
    reshare_info,
    reshare_links,
)


def test_ids() -> None:
    assert _reshare_id("854001") == "854001"
    assert _reshare_id("https://doi.org/10.5255/UKDA-SN-854001") == "854001"
    assert _reshare_id("10.5255/ukda-sn-854243-2") == "854243"
    assert _reshare_id("https://reshare.ukdataservice.ac.uk/854243/") == "854243"
    assert _reshare_id(" 42 ") == "42"
    assert _reshare_id(["nope", "", None]) == [None, None, None]


def test_links() -> None:
    paper = pc.test_paper(
        ["Deposited at 10.5255/UKDA-SN-854001 and https://reshare.ukdataservice.ac.uk/854243/."],
        ["https://doi.org/10.5255/UKDA-SN-854001", "https://osf.io/x"],
    )
    links = reshare_links(paper)
    assert links["reshare_id"].tolist() == ["854001", "854001", "854243"]


def test_headers() -> None:
    spec = _reshare_headers({"method": "GET", "url": "u", "headers": {"Accept": "x"}})
    assert spec["headers"] == {"Accept": "x", "User-Agent": "metacheck"}


def test_private_info(mock_api: object) -> None:
    info = _reshare_info("854001")
    assert info["title"].iloc[0] == "Interviews with rural households, 2016-2018"
    assert info["authors"].iloc[0] == ["Jane Doe", "Solo", None, None]
    assert pd.isna(info["license"].iloc[0])
    files = info["files"].iloc[0]
    assert [f["fileid"] for f in files] == [1001, 1002, 1003, 1004]
    assert [f["content"] for f in files] == ["data", "documentation", None, None]
    assert _reshare_info("854243")["error"].iloc[0] == "parse_error"
    with pytest.warns(UserWarning, match="999999 could not be found on ReShare"):
        assert _reshare_info("999999")["error"].iloc[0] == "unfound"


def test_info(mock_api: object) -> None:
    with pytest.warns(UserWarning):
        info = reshare_info(["https://doi.org/10.5255/UKDA-SN-854001", "999999", "nope", None])
    assert info["reshare_id"].tolist()[:2] == ["854001", "999999"]
    assert info["error"].tolist()[1] == "unfound"
    assert pd.isna(info["reshare_id"].iloc[2])


def test_file_download(mock_api: object, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="2 of 3 files from ReShare deposit 854001"):
        files = reshare_file_download("https://doi.org/10.5255/UKDA-SN-854001", str(tmp_path))
    assert list(files.columns) == [
        "folder", "reshare_id", "id", "key", "path", "size", "size_on_disk", "checksum",
        "checksum_ok", "self", "downloaded", "extracted",
    ]  # fmt: skip
    # guide.pdf (20MB) is over the 10MB default cap: listed, not downloaded (U36)
    assert files["key"].tolist() == ["interviews.csv", "guide.pdf", "notes.txt", "gone.txt"]
    assert files["downloaded"].tolist() == [True, False, False, False]
    assert files["checksum_ok"].iloc[0] == True  # noqa: E712
    assert pd.isna(files["checksum_ok"].iloc[1])  # omitted: not checked
    assert files["checksum_ok"].iloc[2] == False  # noqa: E712
    assert files["self"].iloc[0].startswith("https://")  # upgraded from http
    folder = tmp_path / "reshare_854001"
    assert (folder / "interviews.csv").read_bytes() == b"id,answer\n1,yes\n2,no\n"

    # a second download goes to a new folder "reshare_854001_1" (U37: metacheck
    # strips the deposit id itself and uses "reshare_1")
    with pytest.warns(UserWarning):
        again = reshare_file_download("854001", str(tmp_path))
    assert again["folder"].iloc[0] == "reshare_854001_1"


def test_file_download_edge_cases(mock_api: object, tmp_path: Path) -> None:
    assert reshare_file_download("nope") is None
    assert reshare_file_download("854100", str(tmp_path)) is None  # no documents
    with pytest.warns(UserWarning):
        multi = reshare_file_download(["854001", "854100", "999999"], str(tmp_path))
    assert multi["reshare_id"].unique().tolist() == ["854001"]


def test_verify_downloads(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"abc\n")
    md5 = hashlib.md5(b"abc\n", usedforsecurity=False).hexdigest()
    files = pd.DataFrame(
        {
            "path": pd.Series(["a.txt", "a.txt", "missing.txt", None], dtype="string"),
            "size": [4.0, 5.0, 5.0, None],
            "checksum": pd.Series([md5.upper(), None, None, None], dtype="string"),
            "checksum_type": pd.Series(["md5", "md5", "md5", None], dtype="string"),
            "downloaded": [True, True, True, False],
            "extracted": pd.Series([None, None, None, 2], dtype="Int64"),
        }
    )
    out = _reshare_verify_downloads(files, str(tmp_path))
    assert out["downloaded"].tolist() == [True, False, False, True]
    assert out["checksum_ok"].tolist()[0] is True
    assert out["size_on_disk"].tolist()[:2] == [4.0, 4.0]


def test_file_download_zip_with_unzip_types(serve: object, tmp_path: Path) -> None:
    import io
    import json
    import zipfile

    import httpx

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.csv", "a,b\n1,2\n")
        zf.writestr("stimuli.png", b"\x89PNG....")
    body = buf.getvalue()
    rec = {
        "title": "Zipped",
        "documents": [
            {
                "content": "data",
                "files": [
                    {
                        "fileid": 7,
                        "filename": "all.zip",
                        "filesize": len(body),
                        "hash": hashlib.md5(body, usedforsecurity=False).hexdigest(),
                        "hash_type": "MD5",
                        "uri": "http://reshare.ukdataservice.ac.uk/id/file/7",
                    }
                ],
            }
        ],
    }
    serve(  # type: ignore[operator]
        {
            "https://reshare.ukdataservice.ac.uk/id/eprint/5": httpx.Response(
                200, content=json.dumps(rec).encode(), headers={"content-type": "application/json"}
            ),
            "https://reshare.ukdataservice.ac.uk/id/file/7": httpx.Response(200, content=body),
        }
    )
    # the zip is over the (tiny) size cap but exempt from it: only members are wanted
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        files = reshare_file_download("5", str(tmp_path), max_file_size=0.0001, unzip_types="data")
    assert files is not None
    assert files["key"].tolist() == ["all.zip"]
    # either the wanted members were extracted, or (when the archive cannot be
    # read remotely) the whole zip was fetched instead -- never skipped by the cap
    assert files["downloaded"].tolist() == [True] or files["extracted"].notna().all()
