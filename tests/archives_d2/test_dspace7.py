"""Tests for metacheck.archives.dspace7 (R/archive-dspace7.R has no testthat file upstream)."""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives.dspace7 import (
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
    # an item without files is an empty listing carrying its doi/licence
    # (metacheck #435; it was NULL)
    empty = dspace7_file_download(no_files)
    assert empty is not None and len(empty) == 0
    assert list(empty.columns) == [
        "dspace7_url", "name", "file_url", "file_location", "size", "isdir", "ext", "type"
    ]  # fmt: skip
    assert empty.attrs == {"license": {no_files: None}, "doi": {no_files: None}}
    both = dspace7_file_download([GT_URL, no_files, None])
    assert both["dspace7_url"].iloc[-2] == no_files
    assert pd.isna(both["dspace7_url"].iloc[-1])
    # U43: several URLs that all fail give None (metacheck's join errors)
    assert dspace7_file_download(["https://example.org/a", "https://example.org/b"]) is None


def test_file_lists_name_the_items_not_found(mock_api: object) -> None:
    # U43: repo_check() reports the items that could not be found; an item
    # without files was found
    from metacheck.archives.dspace7 import _dspace7_file_lists

    no_files = "https://scholarworks.umass.edu/items/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    files, unfound = _dspace7_file_lists(no_files)
    assert files is not None and len(files) == 0 and unfound == []
    assert _dspace7_file_lists("https://example.org/x") == (None, ["https://example.org/x"])
    assert _dspace7_file_lists(None) == (None, [])
    files, unfound = _dspace7_file_lists([GT_URL, no_files, "https://example.org/x", None])
    assert unfound == ["https://example.org/x"]
    assert files is not None and files["name"].tolist()[0] == "data.CSV"
    urls = ["https://example.org/a", "https://example.org/b"]
    assert _dspace7_file_lists(urls) == (None, urls)


def test_file_download_carries_doi_and_licence(mock_api: object) -> None:
    lic = "http://creativecommons.org/licenses/by/4.0/"
    one = dspace7_file_download(GT_URL)
    assert one is not None
    assert one.attrs == {"license": {GT_URL: lic}, "doi": {GT_URL: None}}
    no_files = "https://scholarworks.umass.edu/items/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    both = dspace7_file_download([GT_URL, no_files, "https://example.org/x"])
    assert both.attrs["license"] == {GT_URL: lic, no_files: None}
    assert set(both.attrs["doi"]) == {GT_URL, no_files}


# --------------------------------------------------------------------------- DOI mentions


def _doi_redirect(serve: object, doi: str, target: str) -> list:
    import httpx

    return serve(  # type: ignore[operator,no-any-return]
        {
            f"https://doi.org/{doi}": httpx.Response(302, headers={"Location": target}),
            target: httpx.Response(200, text="<html></html>"),
        }
    )


def test_resolve_doi_url(serve: object) -> None:
    from metacheck.archives.dspace7 import _dspace7_resolve_doi_url

    target = f"https://repository.gatech.edu/entities/publication/{GT}"
    _doi_redirect(serve, "10.35090/gatech/1", target)
    assert _dspace7_resolve_doi_url("10.35090/gatech/1") == target
    # not a DSpace 7 host, or one named only in the query string (U206)
    _doi_redirect(serve, "10.1234/a", "https://example.org/a")
    _doi_redirect(serve, "10.1234/b", "https://example.org/b?next=repository.gatech.edu")
    assert _dspace7_resolve_doi_url("10.1234/a") is None
    assert _dspace7_resolve_doi_url("10.1234/b") is None
    # unresolvable (a 404 at doi.org)
    assert _dspace7_resolve_doi_url("10.1234/missing") is None
    # www. aside, a listed host matches
    _doi_redirect(serve, "10.3929/x", "https://research-collection.ethz.ch/handle/20.500.11850/1")
    assert (
        _dspace7_resolve_doi_url("10.3929/x")
        == "https://research-collection.ethz.ch/handle/20.500.11850/1"
    )


def test_links_resolve_doi_mentions(serve: object) -> None:
    target = f"https://repository.gatech.edu/entities/publication/{GT}"
    requests = _doi_redirect(serve, "10.35090/gatech/1", target)
    paper = pc.test_paper(
        [
            "Data: https://doi.org/10.35090/gatech/1. and doi:10.1234/other.",
            "Again 10.35090/gatech/1",
        ],
        ["https://osf.io/abcde"],
    )
    links = dspace7_links(paper)
    # the mentions' href is the resolved landing page (one row per sentence);
    # each distinct DOI is resolved once
    assert links["href"].tolist() == [target, target]
    assert links["text_id"].tolist() == [1, 2]
    asked = [str(r.url) for r in requests if "doi.org" in str(r.url)]
    assert sorted(asked) == ["https://doi.org/10.1234/other", "https://doi.org/10.35090/gatech/1"]
