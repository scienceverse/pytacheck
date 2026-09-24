"""Tests for pytacheck.archives.dataverse (port of tests/testthat/test-archive-dataverse.R, plus more).

Where the R tests mock ``.batch_query()`` or httr2's request functions, these
serve the same payloads over mocked HTTP (respx); where they mock
``.dataverse_info()`` / ``.dataverse_zip_members()``, these monkeypatch the
Python functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives import dataverse
from pytacheck.archives.dataverse import (
    DATAVERSE_HOSTS,
    _dataverse_headers,
    _dataverse_host_from_doi,
    _dataverse_info,
    _dataverse_parse,
    _dataverse_verify_downloads,
    _url_decode,
    dataverse_file_download,
    dataverse_info,
    dataverse_links,
    dataverse_pat,
)

API = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:"


def test_dataverse_links() -> None:
    with pytest.raises(TypeError):
        dataverse_links()  # type: ignore[call-arg]
    paper = pc.test_paper(
        url=[
            "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
            "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999",
            "https://osf.io/abcde",
        ]
    )
    links = dataverse_links(paper)
    assert len(links) == 2
    assert links["href"].tolist() == [
        "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
        "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999",
    ]
    assert links["dataverse_host"].tolist() == ["dataverse.harvard.edu", "dataverse.nl"]
    assert links["dataverse_doi"].tolist() == ["10.7910/DVN/ABC123", "10.34894/XYZ999"]


def test_dataverse_parse() -> None:
    parsed = _dataverse_parse(
        [
            "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
            "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999",
            "https://dataverse.harvard.edu/dataset.xhtml?id=12345",
            "https://notdataverse.com/foo",
            None,
        ]
    )
    assert parsed["host"].tolist()[:3] == [
        "dataverse.harvard.edu",
        "dataverse.nl",
        "dataverse.harvard.edu",
    ]
    assert parsed["host"].isna().tolist()[3:] == [True, True]
    assert parsed["doi"].tolist()[:2] == ["10.7910/DVN/ABC123", "10.34894/XYZ999"]
    assert parsed["doi"].isna().tolist()[2:] == [True, True, True]
    assert len(_dataverse_parse([])) == 0


def _dataset_json() -> dict[str, Any]:
    return {
        "status": "OK",
        "data": {
            "persistentUrl": "https://doi.org/10.7910/DVN/ABC123",
            "publicationDate": "2020-01-01",
            "latestVersion": {
                "releaseTime": "2020-01-01T00:00:00Z",
                "lastUpdateTime": "2020-01-02T00:00:00Z",
                "license": {"name": "CC0 1.0"},
                "metadataBlocks": {
                    "citation": {
                        "fields": [
                            {"typeName": "title", "value": "Example Dataset"},
                            {
                                "typeName": "author",
                                "value": [{"authorName": {"value": "Doe, Jane"}}],
                            },
                        ]
                    }
                },
                "files": [
                    {
                        "label": "data.csv",
                        "dataFile": {
                            "id": 999,
                            "filename": "data.csv",
                            "filesize": 1234,
                            "checksum": {"type": "MD5", "value": "abc123"},
                        },
                    }
                ],
            },
        },
    }


def test_private_dataverse_info(serve: Any) -> None:
    seen = serve({API + "10.7910/DVN/ABC123": httpx.Response(200, json=_dataset_json())})
    info = _dataverse_info("dataverse.harvard.edu", "10.7910/DVN/ABC123")
    assert info["dataverse_host"].tolist() == ["dataverse.harvard.edu"]
    assert info["dataverse_doi"].tolist() == ["10.7910/DVN/ABC123"]
    assert info["title"].tolist() == ["Example Dataset"]
    assert info["license"].tolist() == ["CC0 1.0"]
    assert info["authors"].iloc[0] == ["Doe, Jane"]
    assert len(info["files"].iloc[0]) == 1
    assert info["publication_date"].tolist() == ["2020-01-01T00:00:00Z"]
    assert seen[0].headers["User-Agent"] == "metacheck"


def test_private_dataverse_info_not_found(serve: Any) -> None:
    serve({})
    with pytest.warns(UserWarning, match="could not be found"):
        info = _dataverse_info("dataverse.harvard.edu", "10.7910/DVN/NOPE")
    assert info["error"].tolist() == ["unfound"]


def test_dataverse_links_bare_doi_without_host() -> None:
    paper = pc.test_paper(
        [
            "Data are available on the CIRAD Dataverse: https://doi.org/10.18167/DVN1/T0DMFJ.",
            "Also see https://doi.org/10.9999/unrelated123 for something unrelated.",
        ]
    )
    links = dataverse_links(paper)
    assert len(links) == 1
    assert links["dataverse_host"].tolist() == ["dataverse.cirad.fr"]
    assert links["dataverse_doi"].tolist() == ["10.18167/DVN1/T0DMFJ"]


def test_dataverse_host_from_doi_verified_prefix_only() -> None:
    assert _dataverse_host_from_doi("10.18167/DVN1/T0DMFJ") == "dataverse.cirad.fr"
    assert _dataverse_host_from_doi("10.7910/DVN/ABC123") == "dataverse.harvard.edu"
    assert _dataverse_host_from_doi("10.9999/unrelated123") is None


def test_dataverse_info_on_its_own_links_for_an_unfound_doi(serve: Any, online: None) -> None:
    serve({})
    paper = pc.test_paper(
        url=["https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/NOPE"]
    )
    links = dataverse_links(paper)
    with pytest.warns(UserWarning):
        info = dataverse_info(links)
    assert info["error"].tolist() == ["unfound"]
    assert "dataverse_host.x" not in info.columns


def test_dataverse_pat_per_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytacheck import utils

    monkeypatch.setenv("DATAVERSE_PAT_DATAVERSE_HARVARD_EDU", "")
    monkeypatch.setenv("DATAVERSE_PAT_DATAVERSE_NL", "")
    with utils.local_options(
        {
            "metacheck.dataverse.pat.DATAVERSE_HARVARD_EDU": None,
            "metacheck.dataverse.pat.DATAVERSE_NL": None,
        }
    ):
        assert dataverse_pat("dataverse.harvard.edu") == ""
        dataverse_pat("dataverse.harvard.edu", "hvd-token")
        assert dataverse_pat("dataverse.harvard.edu") == "hvd-token"
        assert dataverse_pat("dataverse.nl") == ""
        with pytest.raises(ValueError):
            dataverse_pat("dataverse.harvard.edu", 123)  # type: ignore[arg-type]
        spec = _dataverse_headers({"url": "https://DATAVERSE.harvard.edu/api/x", "headers": {}})
        assert spec["headers"]["X-Dataverse-key"] == "hvd-token"
        spec = _dataverse_headers({"url": "https://dataverse.nl/api/x", "headers": {}})
        assert "X-Dataverse-key" not in spec["headers"]


def _mock_info(files: list[dict[str, Any]]) -> Any:
    def fake(host: str, doi: str, pb: Any = None) -> pd.DataFrame:
        col = pd.Series([None], dtype=object)
        col.iloc[0] = files
        return pd.DataFrame({"dataverse_host": [host], "dataverse_doi": [doi], "files": col})

    return fake


def test_dataverse_file_download(
    monkeypatch: pytest.MonkeyPatch, serve: Any, tmp_path: Path
) -> None:
    assert dataverse_file_download(None, None) is None
    assert dataverse_file_download([None], [None]) is None

    monkeypatch.setattr(
        dataverse,
        "_dataverse_info",
        _mock_info(
            [
                {
                    "label": "small.csv",
                    "dataFile": {
                        "id": 1,
                        "filename": "small.csv",
                        "filesize": 100,
                        "checksum": {"type": "MD5", "value": "small"},
                    },
                },
                {
                    "label": "big.bin",
                    "dataFile": {
                        "id": 2,
                        "filename": "big.bin",
                        "filesize": 12 * 1024 * 1024,
                        "checksum": {"type": "MD5", "value": "big"},
                    },
                },
            ]
        ),
    )
    serve({})  # small.csv has no network target: its download fails
    with pytest.warns(UserWarning, match="did not arrive intact"):
        dl = dataverse_file_download(
            "dataverse.harvard.edu",
            "10.7910/DVN/ABC123",
            download_to=str(tmp_path),
            max_file_size=10,
        )
    assert dl is not None and len(dl) == 1
    assert dl["dataverse_doi"].tolist() == ["10.7910/DVN/ABC123"]
    assert dl["key"].tolist() == ["small.csv"]
    assert dl["downloaded"].tolist() == [False]
    folder = tmp_path / "10.7910_DVN_ABC123"
    assert folder.is_dir()
    assert list(folder.iterdir()) == []

    # vectorised: each dataset warns separately
    with pytest.warns(UserWarning, match="did not arrive intact") as record:
        dl2 = dataverse_file_download(
            host=["dataverse.harvard.edu", "dataverse.nl"],
            doi=["10.7910/DVN/ABC123", "10.34894/XYZ999"],
            download_to=str(tmp_path),
            max_file_size=10,
        )
    assert all("did not arrive intact" in str(w.message) for w in record)
    assert dl2 is not None
    assert set(dl2["dataverse_doi"]) == {"10.7910/DVN/ABC123", "10.34894/XYZ999"}
    assert not dl2["downloaded"].any()

    # successful download path
    body = b"x,y\n1,2\n"
    monkeypatch.setattr(
        dataverse,
        "_dataverse_info",
        _mock_info(
            [
                {
                    "label": "ok.csv",
                    "dataFile": {
                        "id": 3,
                        "filename": "ok.csv",
                        "filesize": len(body),
                        "checksum": {"type": "MD5", "value": "notreal"},
                    },
                }
            ]
        ),
    )
    serve(
        {"https://dataverse.harvard.edu/api/access/datafile/3": httpx.Response(200, content=body)}
    )
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    dl_ok = dataverse_file_download(
        "dataverse.harvard.edu", "10.7910/DVN/OK000", download_to=str(ok_dir), max_file_size=10
    )
    assert dl_ok is not None and len(dl_ok) == 1
    assert dl_ok["downloaded"].tolist() == [True]
    assert (ok_dir / "10.7910_DVN_OK000" / "ok.csv").read_bytes() == body


def _zip_dataset() -> Any:
    return _mock_info(
        [
            {
                "label": "bundle.zip",
                "dataFile": {
                    "id": 4,
                    "filename": "bundle.zip",
                    "filesize": 130 * 1024 * 1024,
                    "checksum": {"type": "MD5", "value": "zzz"},
                },
            }
        ]
    )


def test_dataverse_file_download_extracts_wanted_members(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requested: list[str] = []

    def fake_members(url: str, dest: str, keep_types: Any, max_file_size: Any) -> pd.DataFrame:
        requested.append(url)
        assert keep_types == "data"
        Path(dest, "study.csv").write_text("id,x\n1,2\n")
        return pd.DataFrame(
            {"name": ["study.csv"], "path": [f"{dest}/study.csv"], "size": [9.0], "ok": [True]}
        )

    monkeypatch.setattr(dataverse, "_dataverse_info", _zip_dataset())
    monkeypatch.setattr(dataverse, "_dataverse_zip_members", fake_members)
    dl = dataverse_file_download(
        "dataverse.harvard.edu",
        "10.7910/DVN/ZIP001",
        download_to=str(tmp_path),
        unzip_types="data",
        max_file_size=10,
    )
    assert dl is not None and len(dl) == 1
    assert dl["key"].tolist() == ["bundle.zip"]
    assert dl["downloaded"].tolist() == [True]
    assert dl["extracted"].tolist() == [1]
    assert requested == ["https://dataverse.harvard.edu/api/access/datafile/4"]
    assert (tmp_path / "10.7910_DVN_ZIP001" / "study.csv").exists()
    assert pd.isna(dl["size_on_disk"].iloc[0])


def test_dataverse_file_download_falls_back_to_whole_zip(
    monkeypatch: pytest.MonkeyPatch, serve: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(dataverse, "_dataverse_info", _zip_dataset())
    monkeypatch.setattr(dataverse, "_dataverse_zip_members", lambda *a, **k: None)
    serve(
        {
            "https://dataverse.harvard.edu/api/access/datafile/4": httpx.Response(
                200, content=b"PK-not-a-real-zip"
            )
        }
    )
    with pytest.warns(UserWarning):
        dl = dataverse_file_download(
            "dataverse.harvard.edu",
            "10.7910/DVN/ZIP002",
            download_to=str(tmp_path),
            unzip_types="data",
            max_file_size=10,
        )
    assert dl is not None
    assert pd.isna(dl["extracted"].iloc[0])
    assert (tmp_path / "10.7910_DVN_ZIP002" / "bundle.zip").exists()
    assert dl["downloaded"].tolist() == [False]  # the size does not match


def test_unzip_types_leaves_datasets_without_a_zip_unchanged(
    monkeypatch: pytest.MonkeyPatch, serve: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dataverse,
        "_dataverse_info",
        _mock_info(
            [
                {
                    "label": "plain.csv",
                    "dataFile": {"id": 5, "filename": "plain.csv", "filesize": 8, "checksum": None},
                }
            ]
        ),
    )
    serve(
        {
            "https://dataverse.harvard.edu/api/access/datafile/5": httpx.Response(
                200, content=b"x,y\n1,2\n"
            )
        }
    )
    dl = dataverse_file_download(
        "dataverse.harvard.edu",
        "10.7910/DVN/PLAIN01",
        download_to=str(tmp_path),
        unzip_types="data",
    )
    assert dl is not None
    assert dl["downloaded"].tolist() == [True]
    assert pd.isna(dl["extracted"].iloc[0])
    assert (tmp_path / "10.7910_DVN_PLAIN01" / "plain.csv").exists()


# --------------------------------------------------------------------------- more


def test_allowlists_are_complete() -> None:
    assert len(DATAVERSE_HOSTS) == 121
    assert len(set(DATAVERSE_HOSTS)) == 121
    assert all(h in DATAVERSE_HOSTS for h in dataverse.DATAVERSE_DOI_PREFIX_HOSTS)


def test_dataverse_parse_realignment_quirk() -> None:
    # R assigns vectorised regmatches() results in order, so a URL without a
    # host name takes the next URL's host (reproduced on purpose)
    parsed = _dataverse_parse(
        [
            "https://doi.org/10.18167/DVN1/T0DMFJ",
            "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X",
            "https://dataverse.nl/x",
        ]
    )
    assert parsed["host"].tolist() == ["dataverse.harvard.edu", "dataverse.nl", "dataverse.nl"]


def test_url_decode_matches_r() -> None:
    assert _url_decode("a%2Fb%3a") == "a/b:"
    assert _url_decode("a+b") == "a+b"
    assert _url_decode("%4g") == "P"
    with pytest.warns(UserWarning):
        assert _url_decode("abc%") == "abc"


def test_dataverse_info_found_and_offline(mock_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    info = dataverse_info(
        "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123"
    )
    assert info["title"].tolist() == ["Example Dataset"]
    assert info["authors"].iloc[0] == ["Doe, Jane", None]
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"dataverse\.harvard\.edu seems to be offline"):
        dataverse_info("https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X")
    # nothing valid: no network check at all
    out = dataverse_info("https://osf.io/abcde")
    assert out.columns.tolist() == ["dataverse_url", "dataverse_host", "dataverse_doi"]


def test_dataverse_info_old_string_license_errors_like_r(mock_api: Any) -> None:
    with pytest.raises(TypeError, match=r"\$ operator is invalid for atomic vectors"):
        dataverse_info("https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/OLDLIC")


def test_dataverse_verify_downloads_only_hashes_md5(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"x,y\n")
    files = pd.DataFrame(
        {
            "path": ["a.csv", "a.csv", "missing.csv"],
            "size": [4.0, 4.0, 1.0],
            "checksum": ["ffffffffffffffffffffffffffffffff"] * 3,
            "checksum_type": ["sha-1", "md5", "md5"],
            "downloaded": [True, True, True],
        }
    )
    out = _dataverse_verify_downloads(files, str(tmp_path))
    assert out["downloaded"].tolist() == [True, False, False]
    assert out["checksum_ok"].isna().tolist() == [True, False, True]
