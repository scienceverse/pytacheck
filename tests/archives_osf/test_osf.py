"""Port of metacheck's tests/testthat/test-archive-osf.R and test-archive-osf_get_preprints.R."""

from __future__ import annotations

import math

import httpx
import pandas as pd
import pytest
import respx

import metacheck as pc
from metacheck.archives import osf
from metacheck.archives.osf import (
    OsfResult,
    _osf_error_result,
    _osf_max_page_size,
    _osf_status_error,
    osf_api_check,
    osf_cache_clear,
    osf_check_id,
    osf_delay,
    osf_get_all_pages,
    osf_info,
    osf_links,
    osf_preprint_list,
    osf_type,
)
from metacheck.utils import local_options

API = "https://api.osf.io/v2"


def test_osf_links() -> None:
    exp = ["osf.io/e2aks", "osf.io/tvyxz/", "osf.com/nope"]
    obs = osf_links(pc.test_paper(url=exp))
    # trailing slashes are stripped so a hyperlink and a bare mention dedupe
    assert obs["href"].tolist() == ["osf.io/e2aks", "osf.io/tvyxz"]
    assert list(obs.columns) == ["href", "link_text", "text_id", "paper_id"]


def test_osf_links_body_text_and_dedup() -> None:
    paper = pc.test_paper(
        ["see osf.io/abcde/ and https://osf.io/abcde", "none"], url=["https://osf.io/abcde"]
    )
    obs = osf_links(paper)
    assert obs["href"].tolist() == ["https://osf.io/abcde", "osf.io/abcde"]
    assert obs["text_id"].tolist() == [1, 1]


def test_osf_type(mock_api: respx.MockRouter) -> None:
    examples = {
        "project": ("pngda", "nodes"),
        "component": ("https://osf.io/6nt4v", "nodes"),
        "private": ("ybm3c", "nodes"),
        "file": ("osf.io/75qgk", "files"),
        "preprint": ("xp5cy", "preprints"),
        "user": ("4i578", "users"),
        "reg": ("8c3kb", "registrations"),
    }
    for guid, expected in examples.values():
        assert osf_type(guid) == expected
    with pytest.warns(UserWarning, match="not a valid OSF ID"):
        assert osf_type("xx") is None
    assert osf_type(["pngda", "4i578"]) == ["nodes", "users"]


def test_osf_type_inaccessible() -> None:
    with respx.mock() as router:
        router.get(url__startswith=f"{API}/guids/abcde/").mock(return_value=httpx.Response(410))
        assert osf_type("abcde") == "inaccessible"


def test_osf_check_id() -> None:
    assert osf_check_id("pngda") == "pngda"
    assert osf_check_id(["pngda", "8c3kb"]) == ["pngda", "8c3kb"]
    with pytest.warns(UserWarning):
        assert osf_check_id(["pngda", "xxx", "8c3kb"]) == ["pngda", None, "8c3kb"]
    wb = "6846ed88e49694cd45ab8375"
    assert osf_check_id(wb) == wb
    assert osf_check_id(f"https://osf.io/j3gcx/files/osfstorage/{wb}") == wb
    with pytest.warns(UserWarning):
        assert osf_check_id("6846ed894cd45ab8375") is None
    for url in ["https://osf.io/pngda", "http://osf.io/pngda", "osf.io/pngda", "osf .io/pngda"]:
        assert osf_check_id(url) == "pngda"
    assert osf_check_id("https://osf.io/j3gcx/files/osfstorage") == "j3gcx"
    with pytest.warns(UserWarning):
        assert osf_check_id("xx") is None
    token = "5acf039f24ac4ea28afec473548dd7f4"
    assert osf_check_id(f"https://osf.io/pngda/?view_only={token}") == f"pngda?view_only={token}"

    ids = [
        wb,
        f"https://osf.io/j3gcx/files/osfstorage/{wb}",
        "PNGDA",
        "pngda",
        "https://osf.io/pngda",
        "http://osf.io/pngda",
        "osf.io/pngda",
        "osf .io/pngda",
        "https://osf.io/pngda/files/osfstorage",
        f"https://osf.io/pngda/?view_only={token}",
        f"https://osf.io/pngda?view_only={token}",
        "xx",
        "6846ed88e49694cd45a",
    ]
    with pytest.warns(UserWarning) as record:
        obs = osf_check_id(ids)
    assert len(record) == 2
    exp = [wb] * 2 + ["pngda"] * 9 + [None] * 2
    exp[9:11] = [f"pngda?view_only={token}"] * 2
    assert obs == exp


def test_osf_check_id_silent_for_na_and_platform(recwarn: pytest.WarningsRecorder) -> None:
    ids = [None, "http://osf.io", "https://osf.io/", "osf.io", "https://www.osf.io/", "pngda"]
    assert osf_check_id(ids) == [None] * 5 + ["pngda"]
    assert len(recwarn) == 0


def test_osf_delay() -> None:
    assert osf_delay() >= 0
    assert osf_delay(0.005) == 0.005
    assert osf_delay() == 0.005
    osf_delay(0)
    with pytest.raises(ValueError, match="numeric value"):
        osf_delay("slow")  # type: ignore[arg-type]


def test_osf_api_check(mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osf, "_has_internet", lambda: True)
    assert osf_api_check() == "OK"


def test_osf_api_check_without_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osf, "_has_internet", lambda: True)
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(RuntimeError, match="OSF API seems to be having a problem"):
            osf_api_check()
        with pytest.warns(UserWarning, match="Error 0"):
            osf_api_check(on_error="warn")
        assert osf_api_check(on_error="ignore") == "offline"
        with pytest.raises(ValueError, match="should be one of"):
            osf_api_check(on_error="loud")
    # U53: offline, on_error applies too (metacheck returns "no internet" silently)
    monkeypatch.setattr(osf, "_has_internet", lambda: False)
    with pytest.raises(RuntimeError, match="Error 0: no internet"):
        osf_api_check()
    with pytest.warns(UserWarning, match="no internet"):
        assert osf_api_check(on_error="warn") == "no internet"
    assert osf_api_check(on_error="ignore") == "no internet"


def test_osf_get_all_pages(mock_api: respx.MockRouter) -> None:
    # fewer than 10
    data = osf_get_all_pages(f"{API}/nodes/pngda/files/osfstorage/")
    names = [d["attributes"]["name"] for d in data]
    assert {"test-folder", "README", "papercheck.png"} <= set(names)

    # more than 10: the second page is fetched too
    assert len(osf_get_all_pages(f"{API}/nodes/yt32c/files/osfstorage/")) == 14

    # no results
    empty = osf_get_all_pages(f"{API}/nodes/y6a34/files/osfstorage/")
    assert isinstance(empty, OsfResult) and len(empty) == 0 and empty.osf_error is None

    # limit pages
    assert len(osf_get_all_pages(f"{API}/preprints/", 1)) == 10
    assert len(osf_get_all_pages(f"{API}/preprints/", 2)) == 20
    assert len(osf_get_all_pages(f"{API}/preprints/?page=5", 5)) == 10

    # a single resource is returned as-is
    single = osf_get_all_pages(f"{API}/guids/pngda/?resolve=false")
    assert isinstance(single, dict) and single["id"] == "pngda"


def test_osf_get_all_pages_incomplete_listing() -> None:
    page1 = {
        "data": [{"id": "a"}],
        "links": {"next": f"{API}/x/?page=2", "meta": {"total": 3, "per_page": 1}},
    }
    with respx.mock() as router:
        router.get(url__regex=r".*/x/\?page\[size\]=100$").mock(
            return_value=httpx.Response(200, json=page1)
        )
        router.get(url__regex=r".*page=2.*").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "b"}]})
        )
        router.get(url__regex=r".*page=3.*").mock(return_value=httpx.Response(403))
        with pytest.warns(UserWarning, match="listed only 2 of the 3 items"):
            out = osf_get_all_pages(f"{API}/x/")
    assert [d["id"] for d in out] == ["a", "b"]
    assert out.osf_incomplete == {"expected": 3, "got": 2}


def test_osf_get_all_pages_errors() -> None:
    with respx.mock() as router:
        router.get(url__startswith=f"{API}/nodes/gone/").mock(return_value=httpx.Response(410))
        router.get(url__startswith=f"{API}/nodes/priv/").mock(return_value=httpx.Response(403))
        router.get(url__startswith=f"{API}/nodes/boom/").mock(return_value=httpx.Response(500))
        assert osf_get_all_pages(f"{API}/nodes/gone/").osf_error == "gone"
        assert osf_get_all_pages(f"{API}/nodes/priv/").osf_error == "forbidden"
        failed = osf_get_all_pages(f"{API}/nodes/boom/")
        assert failed.osf_error == "request_failed"
        # retried twice after the first refusal
        assert router.routes[2].call_count == 3


def test_osf_status_error() -> None:
    assert _osf_status_error(403) == "forbidden"
    assert _osf_status_error(401) == "forbidden"
    assert _osf_status_error(404) == "not_found"
    assert _osf_status_error(410) == "gone"
    assert _osf_status_error(500) == "request_failed"
    assert _osf_status_error(400) == "request_failed"
    for ok in (200, 201, 304):
        assert _osf_status_error(ok) is None


def test_osf_error_result() -> None:
    out = _osf_error_result("not_found")
    assert len(out) == 0
    assert out.osf_error == "not_found"
    assert out is not None
    combined = pc.archives.osf._fetch_listings([]) + out
    assert len(combined) == 0


def test_osf_max_page_size() -> None:
    assert _osf_max_page_size(f"{API}/nodes/x/") == f"{API}/nodes/x/?page[size]=100"
    assert _osf_max_page_size(f"{API}/x/?a=1") == f"{API}/x/?a=1&page[size]=100"
    assert _osf_max_page_size(f"{API}/x/?page%5Bsize%5D=5") == f"{API}/x/?page%5Bsize%5D=5"


EXAMPLES = {
    "project": "pngda",
    "component": "https://osf.io/6nt4v",
    "private": "ybm3c",
    "file": "osf.io/75qgk",
    "preprint": "xp5cy",
    "user": "4i578",
    "reg": "8c3kb",
    "duplicate": "6nt4v",
    "bad": "xx",
}


def test_osf_info(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    osf_url = pd.DataFrame({"url": list(EXAMPLES.values()), "type": list(EXAMPLES)})
    with pytest.warns(UserWarning, match="xx is not a valid OSF ID"):
        table = osf_info(osf_url)
    assert table["url"].tolist() == osf_url["url"].tolist()
    assert table["type"].tolist() == osf_url["type"].tolist()
    assert table.iloc[1, 2:10].tolist() == table.iloc[7, 2:10].tolist()
    assert table["self"].tolist()[:-1] == [
        f"{API}/nodes/pngda/",
        f"{API}/nodes/6nt4v/",
        f"{API}/nodes/ybm3c/",
        f"{API}/files/6846ed6a29684b023953943e/",
        f"{API}/preprints/xp5cy_v1/",
        f"{API}/users/4i578/",
        f"{API}/registrations/8c3kb/",
        f"{API}/nodes/6nt4v/",
    ]
    assert pd.isna(table["self"].iloc[-1])

    table = osf_info("pngda")
    assert table["osf_url"].tolist() == ["pngda"]
    assert table["name"].tolist() == ["Papercheck Test"]

    table = osf_info(pd.DataFrame({"id": [100], "osf_id": ["pngda"]}), "osf_id")
    assert table["osf_id"].tolist() == ["pngda"]
    assert table["name"].tolist() == ["Papercheck Test"]
    assert table["project"].tolist() == ["pngda"]
    assert "osf_id.osf" in table.columns

    assert osf_info("https://osf.io/pngda")["name"].tolist() == ["Papercheck Test"]

    links = osf_links(pc.test_paper(["No links"]))
    info = osf_info(links, recursive=True)
    assert len(info) == 0
    pd.testing.assert_frame_equal(info, links)


def test_osf_info_recursive(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    table = osf_info("yt32c", recursive=True)
    assert len(table) == 16
    assert table["parent"].tolist()[:2] == ["ckjef", "yt32c"]

    assert len(osf_info(["yt32c", "yt32c", None], recursive=True)) == 16

    osf_url = pd.DataFrame({"parent_id": ["yt32c", "yt32c", None], "n": [1, 2, 3]})
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        table = osf_info(osf_url, recursive=True)
    assert len(table) == 3 + 15
    assert table["n"].tolist()[:3] == [1, 2, 3]
    assert table["n"].isna().sum() == 15


def test_osf_info_recursive_nested(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    info = osf_info("j3gcx", recursive=True)
    names = set(info["name"].dropna())
    assert {f"nest-{i}" for i in range(1, 5)} | {"empty"} <= names
    assert {f"test-{i}.txt" for i in range(1, 5)} <= names
    folder_ids = info.loc[info["kind"] == "folder", "osf_id"].tolist()
    assert set(folder_ids[:5]) <= set(info["parent"].dropna())

    # github and osfstorage files
    info = osf_info("mc45x", recursive=True)
    assert {f"{i:02d}.R" for i in range(1, 11)} <= set(info["name"].dropna())

    # registrations have folders and files too
    contents = osf_info("jqkg7", recursive=True)
    assert {"folder", "file"} <= set(contents["kind"].dropna())


def test_osf_info_session_cache(mock_api: respx.MockRouter) -> None:
    osf_cache_clear()
    with local_options({"metacheck.osf.cache": True}):
        first = osf_info("pngda")
        calls = mock_api.calls.call_count
        second = osf_info("pngda")
        assert mock_api.calls.call_count == calls
        pd.testing.assert_frame_equal(first, second)
        second.loc[0, "name"] = "changed"
        assert osf_info("pngda")["name"].iloc[0] == "Papercheck Test"
    assert osf_cache_clear() == 1
    assert osf_cache_clear() == 0


def test_osf_info_disk_cache(mock_api: respx.MockRouter) -> None:
    from metacheck.archives.info_cache import _repo_info_cache_get

    osf_info("pngda", cache=True)
    hit = _repo_info_cache_get("osf", "pngda")
    assert hit is not None and hit["name"].tolist() == ["Papercheck Test"]


def test_osf_preprint_list_defaults(mock_api: respx.MockRouter) -> None:
    assert len(osf_preprint_list()) == 10

    psyarxiv = osf_preprint_list("psyarxiv")
    assert len(psyarxiv) == 10
    psyarxiv20 = osf_preprint_list("psyarxiv", page_end=2)
    assert len(psyarxiv20) == 20
    assert psyarxiv["osf_id"].tolist() == psyarxiv20["osf_id"].tolist()[:10]
    psyarxiv10 = osf_preprint_list("psyarxiv", page_start=2)
    assert psyarxiv20["osf_id"].tolist()[10:] == psyarxiv10["osf_id"].tolist()

    date_created = psyarxiv["date_created"].iloc[0]
    one = osf_preprint_list(provider="psyarxiv", date_created=date_created)
    assert len(one) == 1
    assert one["name"].iloc[0] == psyarxiv["name"].iloc[0]
    dates = ["2022-09-01", "2022-09-02"]
    two = osf_preprint_list(provider="psyarxiv", date_created=dates)
    assert {d[:10] for d in two["date_created"]} <= set(dates)

    date_modified = psyarxiv["date_modified"].iloc[0]
    one = osf_preprint_list(provider="psyarxiv", date_modified=date_modified)
    assert len(one) == 1
    assert one["name"].iloc[0] == psyarxiv["name"].iloc[0]
    two = osf_preprint_list(provider="psyarxiv", date_modified=dates)
    assert {d[:10] for d in two["date_modified"]} <= set(dates)


def test_osf_preprint_list_columns(mock_api: respx.MockRouter) -> None:
    pp = osf_preprint_list()
    assert list(pp.columns)[:5] == ["osf_id", "name", "description", "osf_type", "provider"]
    assert pp["osf_type"].eq("preprints").all()
    assert not math.isnan(len(pp))
