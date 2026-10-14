"""Tests for metacheck.archives.dspace7 (R/archive-dspace7.R has no testthat file upstream)."""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck._r import regextract
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
    assert len(DSPACE7_HOSTS) == 66
    assert _dspace7_host_regex().count("|") == 65
    # metacheck #461: Cornell runs DSpace 8, not legacy DSpace; dspace.ut.ee
    # runs DSpace 7 and is no longer listed as legacy too
    from metacheck.archives.psycharchives import _dspace_legacy_hosts

    for host in ("ecommons.cornell.edu", "www.repository.cam.ac.uk", "dspace.ut.ee"):
        assert host in DSPACE7_HOSTS
        assert host not in _dspace_legacy_hosts()
    rx = _dspace7_host_regex()
    assert regextract(rx, "www.repository.cam.ac.uk") == "www.repository.cam.ac.uk"
    assert regextract(rx, "repositoryXgatechXedu") is None


def test_parse_maps_a_ui_host_to_its_api_host() -> None:
    # Apollo (Cambridge): the citable host does not serve /server/api
    parsed = _dspace7_parse(
        [
            "https://www.repository.cam.ac.uk/handle/1810/123456",
            "https://WWW.REPOSITORY.CAM.AC.UK/handle/1810/1",
            "https://repository.gatech.edu/handle/1853/67239",
        ]
    )
    assert parsed["host"].tolist() == [
        "api.repository.cam.ac.uk",
        "api.repository.cam.ac.uk",
        "repository.gatech.edu",
    ]
    assert parsed["handle"].iloc[0] == "1810/123456"


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


# --------------------------------------------------------------------------- one request per item

API = "https://repository.gatech.edu/server/api"
ITEM = "11111111-2222-3333-4444-555555555555"
EMBED = "?embed=bundles/bitstreams&embed.size=bundles/bitstreams=200"


def _bitstream(name: str) -> dict:
    return {
        "name": name,
        "sizeBytes": 1,
        "checkSum": {"value": "abc"},
        "_links": {"content": {"href": f"{API}/core/bitstreams/{name}/content"}},
    }


def _item(bundles: list) -> dict:
    return {
        "uuid": ITEM,
        "name": "An item",
        "metadata": {},
        "_embedded": {"bundles": {"_embedded": {"bundles": bundles}}},
    }


def test_info_lists_an_item_in_one_request_and_follows_pages(serve: object) -> None:
    import httpx

    original = {
        "name": "ORIGINAL",
        "uuid": "b-orig",
        "_embedded": {
            "bitstreams": {
                "_embedded": {"bitstreams": [_bitstream("a.csv"), _bitstream("b.csv")]},
                "page": {"size": 2, "totalElements": 3, "totalPages": 2, "number": 0},
                "_links": {"next": {"href": f"{API}/core/bundles/b-orig/bitstreams?page=1&size=2"}},
            }
        },
    }
    licence = {
        "name": "LICENSE",
        "uuid": "b-lic",
        "_embedded": {
            "bitstreams": {
                "_embedded": {"bitstreams": [_bitstream("license.txt")]},
                "page": {"totalElements": 1},
            }
        },
    }
    requests = serve(  # type: ignore[operator]
        {
            f"{API}/pid/find?id=1853%2F67239": httpx.Response(
                302, headers={"Location": f"{API}/core/items/{ITEM}"}
            ),
            f"{API}/core/items/{ITEM}{EMBED}": httpx.Response(200, json=_item([original, licence])),
            f"{API}/core/bundles/b-orig/bitstreams?page=1&size=2": httpx.Response(
                200,
                json={
                    "_embedded": {"bitstreams": [_bitstream("c.csv")]},
                    "page": {"size": 2, "totalElements": 3, "totalPages": 2, "number": 1},
                },
            ),
        }
    )
    info = _dspace7_info("repository.gatech.edu", handle="1853/67239")
    assert info["dspace7_uuid"].iloc[0] == ITEM
    # every ORIGINAL file, across two pages; the LICENSE bundle left out
    assert info["files"].iloc[0]["name"].tolist() == ["a.csv", "b.csv", "c.csv"]
    # /pid/find (its redirect not followed), the item, the second page
    # (metacheck at the pin: 4 requests, and only the first page)
    assert len(requests) == 3
    assert not any(str(r.url).endswith("/bundles") for r in requests)

    requests.clear()
    info = _dspace7_info("repository.gatech.edu", uuid=ITEM)
    assert info["files"].iloc[0]["name"].tolist() == ["a.csv", "b.csv", "c.csv"]
    assert len(requests) == 2


def test_info_warns_when_a_page_cannot_be_read(serve: object) -> None:
    import httpx

    original = {
        "name": "ORIGINAL",
        "uuid": "b-orig",
        "_embedded": {
            "bitstreams": {
                "_embedded": {"bitstreams": [_bitstream("a.csv")]},
                "page": {"totalElements": 2},
                "_links": {"next": {"href": f"{API}/core/bundles/b-orig/bitstreams?page=1&size=1"}},
            }
        },
    }
    serve({f"{API}/core/items/{ITEM}{EMBED}": httpx.Response(200, json=_item([original]))})  # type: ignore[operator]
    with pytest.warns(UserWarning, match="listed only 1 of the 2 files of bundle b-orig"):
        info = _dspace7_info("repository.gatech.edu", uuid=ITEM)
    assert info["files"].iloc[0]["name"].tolist() == ["a.csv"]


def test_info_asks_separately_when_embed_is_ignored(serve: object) -> None:
    import httpx

    requests = serve(  # type: ignore[operator]
        {
            f"{API}/core/items/{ITEM}{EMBED}": httpx.Response(
                200, json={"uuid": ITEM, "name": "An item", "metadata": {}}
            ),
            f"{API}/core/items/{ITEM}/bundles": httpx.Response(
                200, json={"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "b-orig"}]}}
            ),
            f"{API}/core/bundles/b-orig/bitstreams?size=200": httpx.Response(
                200,
                json={
                    "_embedded": {"bitstreams": [_bitstream("a.csv")]},
                    "page": {"totalElements": 1},
                },
            ),
        }
    )
    info = _dspace7_info("repository.gatech.edu", uuid=ITEM)
    assert info["files"].iloc[0]["name"].tolist() == ["a.csv"]
    assert len(requests) == 3


def test_find_uuid_reads_the_redirect_or_a_200_answer(serve: object) -> None:
    import httpx

    from metacheck.archives.dspace7 import _dspace7_find_uuid

    api = "https://h.example/server/api"
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    serve(  # type: ignore[operator]
        {
            f"{api}/pid/find?id=1%2F2": httpx.Response(
                302, headers={"Location": f"{api}/core/items/{uuid.upper()}"}
            ),
            f"{api}/pid/find?id=1%2F3": httpx.Response(200, json={"uuid": uuid}),
            f"{api}/core/items/{uuid.upper()}": httpx.Response(200, json={"uuid": "followed"}),
        }
    )
    assert _dspace7_find_uuid("h.example", "1/2") == uuid
    assert _dspace7_find_uuid("h.example", "1/3") == uuid
    assert _dspace7_find_uuid("h.example", "1/4") is None


# --------------------------------------------------------------------------- DOI mentions


def _handle_json(url: str) -> object:
    import httpx

    return httpx.Response(
        200,
        json={
            "responseCode": 1,
            "values": [{"index": 1, "type": "URL", "data": {"format": "string", "value": url}}],
        },
    )


def _doi_api(doi: str) -> str:
    return "https://doi.org/api/handles/" + doi.replace("/", "%2F") + "?type=URL"


def _hdl_api(handle: str) -> str:
    return "https://hdl.handle.net/api/handles/" + handle.replace("/", "%2F") + "?type=URL"


def test_skip_doi_prefixes_cover_other_detectors_but_no_dspace7_prefix() -> None:
    from metacheck.archives.dspace7 import _dspace7_skip_doi_prefixes

    skip = _dspace7_skip_doi_prefixes()
    for prefix in (
        "10.17605", "10.5281", "10.5061", "10.25338", "10.6084", "10.26180", "10.4121",
        "10.17632", "10.18739", "10.5063", "10.6085", "10.5255", "10.23668", "10.7910",
        "10.26300", "10.5522", "10.7294",
    ):  # fmt: skip
        assert prefix in skip
    # prefixes DataCite lists with URLs on DSpace 7 installations (metacheck's
    # test, 2026-10-13); 10.23673 is also a Dataverse prefix (datadoi.ee)
    dspace7_prefixes = {
        "10.83384", "10.82482", "10.58086", "10.58164", "10.57776", "10.34642",
        "10.34621", "10.48684", "10.13020", "10.71959", "10.71700", "10.60668",
        "10.21256", "10.2312", "10.18420", "10.17862", "10.57996", "10.15157",
        "10.23673", "10.57839", "10.82419", "10.26041", "10.82463", "10.82921",
        "10.25593", "10.60644", "10.18747", "10.83160", "10.60473", "10.82218",
        "10.60757", "10.82366", "10.34744", "10.13028", "10.35090", "10.25330",
        "10.25676", "10.82589", "10.13025", "10.25818", "10.25540", "10.34899",
        "10.25722", "10.34944", "10.7275", "10.58142", "10.83129", "10.71781",
        "10.7298", "10.17863",
    }  # fmt: skip
    assert dspace7_prefixes.isdisjoint(skip)


def test_resolve_doi_url_reads_the_handle_api(serve: object) -> None:
    import httpx

    from metacheck.archives.dspace7 import _DOI_CACHE, _dspace7_resolve_doi_url

    requests = serve(  # type: ignore[operator]
        {
            # 10.13020/q3y6-h459 -> hdl.handle.net/11299/208294 -> conservancy.umn.edu
            _doi_api("10.13020/q3y6-h459"): _handle_json("http://hdl.handle.net/11299/208294"),
            _hdl_api("11299/208294"): _handle_json(
                "https://conservancy.umn.edu/handle/11299/208294"
            ),
            # registered at the repository's former address
            _doi_api("10.35090/gatech/5981"): _handle_json(
                "https://smartech.gatech.edu/handle/1853/70320"
            ),
            _hdl_api("1853/70320"): _handle_json("https://repository.gatech.edu/handle/1853/70320"),
            _doi_api("10.1177/0956797620000000"): _handle_json(
                "https://journals.sagepub.com/doi/10.1177/0956797620000000"
            ),
            # a URL naming a DSpace 7 host only in its query string (U211)
            _doi_api("10.1234/b"): _handle_json("https://example.org/b?next=repository.gatech.edu"),
            # a listed host's www. form
            _doi_api("10.3929/x"): _handle_json(
                "https://research-collection.ethz.ch/handle/20.500.11850/1"
            ),
            _doi_api("10.9999/none"): httpx.Response(404, json={"responseCode": 100}),
            _doi_api("10.9999/error"): httpx.Response(500),
        }
    )
    assert (
        _dspace7_resolve_doi_url("10.13020/q3y6-h459")
        == "https://conservancy.umn.edu/handle/11299/208294"
    )
    assert len(requests) == 2
    # the same DOI again, in another case: answered from the session memo
    assert (
        _dspace7_resolve_doi_url("10.13020/Q3Y6-H459")
        == "https://conservancy.umn.edu/handle/11299/208294"
    )
    assert len(requests) == 2
    assert (
        _dspace7_resolve_doi_url("10.35090/gatech/5981")
        == "https://repository.gatech.edu/handle/1853/70320"
    )
    assert _dspace7_resolve_doi_url("10.1177/0956797620000000") is None
    assert _dspace7_resolve_doi_url("10.1234/b") is None
    assert (
        _dspace7_resolve_doi_url("10.3929/x")
        == "https://research-collection.ethz.ch/handle/20.500.11850/1"
    )
    # an unregistered DOI is a definite answer; a server error is not kept
    assert _dspace7_resolve_doi_url("10.9999/none") is None
    assert "10.9999/none" in _DOI_CACHE
    assert _dspace7_resolve_doi_url("10.9999/error") is None
    assert "10.9999/error" not in _DOI_CACHE
    # no request leaves for the landing pages themselves
    assert all("/api/handles/" in str(r.url) for r in requests)


def test_links_skip_the_papers_own_doi_and_other_detectors(serve: object) -> None:
    requests = serve(  # type: ignore[operator]
        {
            _doi_api("10.13020/q3y6-h459"): _handle_json(
                "https://conservancy.umn.edu/handle/11299/208294"
            )
        }
    )
    paper = pc.test_paper(
        [
            "Data are at https://doi.org/10.13020/q3y6-h459.",
            "Cite this paper as https://doi.org/10.1177/09567976231234567.",
            "Materials: https://doi.org/10.17605/OSF.IO/ABCDE and 10.5281/zenodo.1234567.",
            "Also 10.5061/dryad.j1fd7 and 10.7910/DVN/ABCDEF.",
        ]
    )
    paper.info["doi"] = "10.1177/09567976231234567"
    links = dspace7_links(paper)
    assert links["href"].tolist() == ["https://conservancy.umn.edu/handle/11299/208294"]
    assert [str(r.url) for r in requests] == [_doi_api("10.13020/q3y6-h459")]


def test_links_resolve_doi_mentions(serve: object) -> None:
    target = f"https://repository.gatech.edu/entities/publication/{GT}"
    requests = serve({_doi_api("10.35090/gatech/1"): _handle_json(target)})  # type: ignore[operator]
    paper = pc.test_paper(
        [
            "Data: https://doi.org/10.35090/gatech/1. and doi:10.1234/other.",
            "Again 10.35090/gatech/1",
        ],
        ["https://osf.io/abcde"],
    )
    links = dspace7_links(paper)
    # the mentions' href is the registered URL (one row per sentence); each
    # distinct DOI is looked up once
    assert links["href"].tolist() == [target, target]
    assert links["text_id"].tolist() == [1, 2]
    asked = sorted(str(r.url) for r in requests)
    assert asked == [_doi_api("10.1234/other"), _doi_api("10.35090/gatech/1")]
