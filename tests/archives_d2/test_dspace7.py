"""Tests for pytacheck.archives.dspace7 (R/archive-dspace7.R has no testthat file upstream)."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.dspace7 import (
    DSPACE7_HOSTS,
    _dspace7_host_regex,
    _dspace7_info,
    _dspace7_parse,
    dspace7_file_download,
    dspace7_links,
)

GT = "bac086e5-c606-474b-af1e-4a6122694af5"
GT_URL = f"https://repository.gatech.edu/entities/publication/{GT}"


def test_host_list() -> None:
    assert len(DSPACE7_HOSTS) == 64
    assert _dspace7_host_regex().count("|") == 63


def test_parse() -> None:
    parsed = _dspace7_parse(
        [
            GT_URL.upper().replace(
                "HTTPS://REPOSITORY.GATECH.EDU", "https://repository.gatech.edu"
            ),
            "https://repository.gatech.edu/handle/1853/67239.",
            "http://hdl.handle.net/1853/67239",
            None,
        ]
    )
    assert parsed["host"].tolist()[:2] == ["repository.gatech.edu"] * 2
    assert parsed["uuid"].iloc[0] == GT  # lower-cased
    assert parsed["handle"].iloc[1] == "1853/67239"  # trailing "." dropped
    assert pd.isna(parsed["handle"].iloc[2])  # only after "/handle/"
    assert parsed.iloc[3].isna().all()
    assert len(_dspace7_parse([])) == 0


def test_links() -> None:
    paper = pc.test_paper(
        ["Data: repository.gatech.edu/handle/1853/67239/ and nothing else."],
        [GT_URL + "/", "https://osf.io/abcde"],
    )
    assert dspace7_links(paper)["href"].tolist() == [
        GT_URL,
        "repository.gatech.edu/handle/1853/67239",
    ]


def test_info_by_uuid_and_by_handle(mock_api: object) -> None:
    by_uuid = _dspace7_info("repository.gatech.edu", uuid=GT)
    by_handle = _dspace7_info("repository.gatech.edu", handle="1853/67239")
    for info in (by_uuid, by_handle):
        assert info["dspace7_uuid"].iloc[0] == GT
        assert info["title"].iloc[0] == "Dataset for a robot study"
        assert info["authors"].iloc[0] == "Doe, Jane; Roe, Rich"
        assert pd.isna(info["doi"].iloc[0])
        # dc.rights has only null values, so the next licence field is used
        assert info["license"].iloc[0] == "http://creativecommons.org/licenses/by/4.0/"
        files = info["files"].iloc[0]
        assert files["name"].tolist() == ["data.CSV", "analysis.R", "model.rds", "noext"]
        assert list(files.columns) == ["name", "size", "checksum", "retrieve"]


def test_info_unfound(mock_api: object) -> None:
    # U42: the item is named by its handle when it has no uuid (metacheck: "(NA)")
    with pytest.warns(UserWarning, match=r"repository.gatech.edu \(1853/404\) could not be found"):
        info = _dspace7_info("repository.gatech.edu", handle="1853/404")
    assert list(info.columns) == ["dspace7_host", "error"]


def test_file_download_lists_the_original_bundle(mock_api: object) -> None:
    files = dspace7_file_download(GT_URL)
    assert files is not None
    assert list(files.columns) == [
        "dspace7_url", "name", "file_url", "file_location", "size", "isdir", "ext", "type"
    ]  # fmt: skip
    # U46: .rds is listed under two file types; the file is one row with the
    # first (metacheck repeats it once per type)
    assert files["name"].tolist() == ["data.CSV", "analysis.R", "model.rds", "noext"]
    assert files["ext"].tolist() == ["csv", "r", "rds", ""]
    assert files["type"].tolist()[2] == "code"
    assert files["file_location"].isna().all()


def test_file_download_edge_cases(mock_api: object) -> None:
    assert dspace7_file_download("https://example.org/x") is None
    assert dspace7_file_download(None) is None
    no_files = "https://scholarworks.umass.edu/items/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert dspace7_file_download(no_files) is None
    both = dspace7_file_download([GT_URL, no_files, None])
    assert both["dspace7_url"].iloc[-2] == no_files
    assert pd.isna(both["dspace7_url"].iloc[-1])
    # U43: several URLs that all fail give None (metacheck's join errors)
    assert dspace7_file_download(["https://example.org/a", "https://example.org/b"]) is None
