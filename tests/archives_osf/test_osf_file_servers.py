"""Ports of metacheck 0.4.4.3's OSF tests: file servers, refusals, contributors, request limits."""

from __future__ import annotations

import warnings
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import download, osf
from metacheck.archives.info_cache import _repo_info_cache_get, _repo_info_cache_put
from metacheck.archives.osf import osf_cache_clear, osf_info
from metacheck.utils import local_options

SERVER = "https://files.de-1.osf.io/v1/resources/x7dar/providers/osfstorage/646cea"


@pytest.fixture(autouse=True)
def _fresh() -> None:
    osf_cache_clear()
    yield
    osf_cache_clear()


def test_a_file_server_the_listing_named_is_used_without_asking_osf_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.archives import api_keys

    download._osf_file_server_remember(["https://osf.io/download/kxjhg/"], [SERVER])
    seen: list[str] = []

    def no_request(*a: object, **k: object) -> None:
        seen.append("request")

    monkeypatch.setattr("metacheck.http.request", no_request)

    # with a token: the remembered server, with the token, no request
    assert download._osf_file_server_url("https://osf.io/download/kxjhg/", "tok") == SERVER
    monkeypatch.setattr(api_keys, "key_use", lambda *a, **k: "tok")
    spec = download._auth_for_url({"method": "GET", "url": "https://osf.io/download/kxjhg/"})
    assert spec["url"] == SERVER
    assert spec["headers"]["Authorization"] == "Bearer tok"

    # without a token: the remembered server, without a token
    monkeypatch.setattr(api_keys, "key_use", lambda *a, **k: "")
    spec = download._auth_for_url({"method": "GET", "url": "https://osf.io/download/kxjhg/"})
    assert spec["url"] == SERVER
    assert "Authorization" not in (spec.get("headers") or {})
    # a file not named by a listing is left as it is
    spec = download._auth_for_url({"method": "GET", "url": "https://osf.io/download/other/"})
    assert spec["url"] == "https://osf.io/download/other/"
    assert seen == []

    # forgotten with the listings
    osf_cache_clear()
    assert download._osf_file_server_known("https://osf.io/download/kxjhg/") is None


def test_the_file_server_a_redirect_named_is_asked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def request(method: str, url: str, **k: object) -> httpx.Response:
        calls.append(url)
        return httpx.Response(302, headers={"location": SERVER})

    monkeypatch.setattr("metacheck.http.request", request)
    url = "https://osf.io/download/kxjhg/"
    assert download._osf_file_server_url(url, "tok") == SERVER
    assert download._osf_file_server_url(url, "tok") == SERVER
    assert len(calls) == 1


def test_a_listing_remembers_the_file_server_of_osf_storage_files_only(
    mock_api: respx.MockRouter, filetype_available: bool
) -> None:
    table = osf_info("yt32c", recursive=True)
    files = table[(table["kind"] == "file") & (table["provider"] == "osfstorage")]
    assert len(files) > 0
    known = [download._osf_file_server_known(u) for u in files["download_url"]]
    # a file whose listing named its own address is remembered; none is wrong
    assert all(k is None or k.startswith("https://files.") for k in known)


def test_an_osf_refusal_is_not_kept_in_the_lasting_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from metacheck import http
    from metacheck.archives.osf_helpers import _osf_info

    n_calls = [0]

    def batch(urls: list[str], *a: object, **k: object) -> list[httpx.Response]:
        n_calls[0] += len(urls)
        return [httpx.Response(403, request=httpx.Request("GET", u)) for u in urls]

    monkeypatch.setattr(http, "batch_query", batch)
    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path)}):
        r = _osf_info("abcde", cache=True)
        assert r["osf_type"].tolist() == ["private"]
        assert _repo_info_cache_get("osf", "abcde") is None
        # one stored before is not reused: the OSF is asked again
        _repo_info_cache_put(
            "osf",
            "fghij",
            pd.DataFrame({"osf_id": ["fghij"], "osf_type": ["private"], "public": [False]}),
        )
        _osf_info("fghij", cache=True)
        assert n_calls[0] == 2


def test_the_guid_request_asks_for_the_contributors(mock_api: respx.MockRouter) -> None:
    osf_info("pngda")
    urls = [str(c.request.url) for c in mock_api.calls]
    assert any(
        u.endswith("/guids/pngda?embed=bibliographic_contributors")
        or "/guids/pngda/?resolve=false" in u
        for u in urls
    )


def test_contributors_are_read_from_the_embedded_answer() -> None:
    from metacheck.archives.osf_helpers import _osf_node_data, _osf_reg_data

    def user(name: str, orcid: str | None) -> dict[str, object]:
        social = {"orcid": orcid} if orcid else {}
        return {
            "embeds": {"users": {"data": {"attributes": {"full_name": name, "social": social}}}}
        }

    rec = {
        "id": "abcde",
        "type": "nodes",
        "attributes": {"title": "T", "date_created": "2026-01-02", "date_modified": "2026-02-03"},
        "embeds": {
            "bibliographic_contributors": {
                "data": [user("Ann A", "0000-0002-1825-0097"), user("Bob B", None)]
            }
        },
    }
    out = _osf_node_data(rec)
    assert out["contributors"].tolist() == ["Ann A; Bob B"]
    assert out["contributor_orcids"].tolist() == ["0000-0002-1825-0097"]
    assert out["date_created"].tolist() == ["2026-01-02"]
    # an answer without them (a listing of child nodes) has none
    bare = {"id": "fghij", "type": "nodes", "attributes": {"title": "U"}}
    out = _osf_node_data(bare)
    assert out["contributors"].isna().all()
    reg = {**rec, "type": "registrations", "attributes": {"title": "R", "description": "D"}}
    out = _osf_reg_data(reg)
    assert out["contributors"].tolist() == ["Ann A; Bob B"]
    assert out["description"].tolist() == ["D"]


def test_a_cached_record_without_contributors_is_asked_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from metacheck import http
    from metacheck.archives.osf_helpers import _osf_info

    old = pd.DataFrame({"osf_id": ["abcde"], "osf_type": ["nodes"], "name": ["Old"]})
    asked: list[str] = []

    def batch(urls: list[str], *a: object, **k: object) -> list[httpx.Response]:
        asked.extend(urls)
        body = {"data": {"id": "abcde", "type": "nodes", "attributes": {"title": "New"}}}
        return [httpx.Response(200, json=body, request=httpx.Request("GET", u)) for u in urls]

    monkeypatch.setattr(http, "batch_query", batch)
    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path)}):
        _repo_info_cache_put("osf", "abcde", old)
        out = _osf_info("abcde", cache=True)
    assert len(asked) == 1
    assert out["name"].tolist() == ["New"]


def test_osf_info_stops_a_listing_at_max_requests_and_marks_it_cut(
    mock_api: respx.MockRouter, filetype_available: bool
) -> None:
    with local_options({"metacheck.osf.cache": True}):
        n0 = osf._OSF_REQUESTS[0]
        full = osf_info("j3gcx", recursive=True)
        n_full = osf._OSF_REQUESTS[0] - n0
        assert "listing_cut" not in full
        assert n_full > 2

        osf_cache_clear()
        n0 = osf._OSF_REQUESTS[0]
        with pytest.warns(UserWarning, match="stopped at the limit of 2 requests"):
            cut = osf_info("j3gcx", recursive=True, max_requests=2)
        assert osf._OSF_REQUESTS[0] - n0 < n_full
        assert cut["listing_cut"].all()
        assert osf._count_files(cut) < osf._count_files(full)

        # the cut listing is kept for this session under its limit only: a call without a limit
        # lists the project in full again
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            again = osf_info("j3gcx", recursive=True, max_requests=2)
        pd.testing.assert_frame_equal(again, cut)
        whole = osf_info("j3gcx", recursive=True)
        assert "listing_cut" not in whole
        assert osf._count_files(whole) == osf._count_files(full)


def test_a_cut_listing_goes_on_where_it_stopped_with_cache(
    mock_api: respx.MockRouter, filetype_available: bool, tmp_path: Path
) -> None:
    full = osf_info("j3gcx", recursive=True)
    osf_cache_clear()
    with local_options(
        {"metacheck.repo_info_cache.dir": str(tmp_path), "metacheck.osf.cache": False}
    ):
        with pytest.warns(UserWarning, match="run again with `cache = TRUE`"):
            first = osf_info("j3gcx", recursive=True, cache=True, max_requests=2)
        assert first["listing_cut"].all()
        key = osf._osf_cache_key("j3gcx", True)
        assert _repo_info_cache_get("osf_listing_part", key) is not None
        x = first
        runs = 1
        while "listing_cut" in x and runs < 40:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                x = osf_info("j3gcx", recursive=True, cache=True, max_requests=2)
            runs += 1
        assert runs > 2
        assert "listing_cut" not in x
        assert set(x.loc[x["kind"] == "file", "name"]) == set(
            full.loc[full["kind"] == "file", "name"]
        )
        assert len(x) == len(full)
        assert _repo_info_cache_get("osf_listing_part", key) is None


def test_osf_info_asks_again_for_a_folder_the_osf_refused_and_marks_a_short_listing(
    mock_api: respx.MockRouter,
    filetype_available: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[int] = []
    monkeypatch.setattr(osf, "_osf_retry_wait", lambda: waits.append(1) or 0)
    # the recordings have no answer for the component tree: the listing goes node by node
    monkeypatch.setattr("metacheck.archives.osf._osf_tree", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("metacheck.archives.osf_helpers._osf_tree", lambda *a, **k: None)
    full = osf_info("j3gcx", recursive=True)
    osf_cache_clear()
    folder = "685a46eb8c103f8ab307047f"
    real = osf.osf_get_all_pages
    asked: list[str] = []

    def refuse(*answers: object) -> None:
        asked.clear()
        osf_cache_clear()
        queue = list(answers)

        def fake(url: str, *a: object, **k: object) -> object:
            if folder not in url:
                return real(url, *a, **k)
            asked.append(url)
            if not queue:
                return real(url, *a, **k)
            how = queue.pop(0)
            if how == "short":
                out = real(url, *a, **k)
                out = osf.OsfResult(out[:1])
                out.osf_incomplete = {"expected": 3, "got": 1}
                return out
            return osf._osf_error_result(how)

        monkeypatch.setattr(osf, "osf_get_all_pages", fake)

    with local_options(
        {"metacheck.repo_info_cache.dir": str(tmp_path), "metacheck.osf.cache": True}
    ):
        key = osf._osf_cache_key("j3gcx", True)

        # refused twice: short, with a warning, and not kept in the lasting cache
        refuse("request_failed", "request_failed")
        with pytest.warns(
            UserWarning,
            match=r"The OSF refused part of the listing of j3gcx, also when asked again \(1 folder\)",
        ):
            x = osf_info("j3gcx", recursive=True, cache=True)
        assert len(asked) == 2
        assert len(waits) == 1
        assert x["listing_short"].all()
        assert "listing_cut" not in x
        assert osf._count_files(x) < osf._count_files(full)
        assert _repo_info_cache_get("osf_tree", key) is None

        # refused once, answered when asked again: complete, and kept
        waits.clear()
        refuse("request_failed")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            x = osf_info("j3gcx", recursive=True, cache=True)
        assert len(asked) == 2
        assert len(waits) == 1
        assert "listing_short" not in x
        assert set(x["osf_id"]) == set(full["osf_id"])
        assert len(x) == len(full)
        assert _repo_info_cache_get("osf_tree", key) is not None

        # answered short (one row only), then in full: the row of the short answer is not listed twice
        refuse("short")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            x = osf_info("j3gcx", recursive=True, cache=False)
        assert "listing_short" not in x
        assert len(x) == len(full)
        assert not x["osf_id"].dropna().duplicated().any()

        # refused as forbidden in a project the OSF has shown public: a refusal under load
        refuse("forbidden", "forbidden")
        with pytest.warns(UserWarning, match="refused part of the listing"):
            x = osf_info("j3gcx", recursive=True, cache=False)
        assert x["listing_short"].all()

        # nothing refused: no wait
        waits.clear()
        refuse()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            osf_info("j3gcx", recursive=True, cache=False)
        assert waits == []


def test_only_a_refusal_of_a_component_storage_list_or_osf_storage_folder_can_be_one_under_load() -> (
    None
):
    api = "https://api.osf.io/v2/nodes/j3gcx"
    assert osf._osf_refusable_url(f"{api}/children/?page[size]=100")
    assert osf._osf_refusable_url(f"{api}/files/")
    assert osf._osf_refusable_url(f"{api}/files/osfstorage/")
    assert osf._osf_refusable_url(f"{api}/files/osfstorage/685a46eb8c103f8ab307047f/")
    # a linked storage can refuse for good when its link to the account lapsed
    assert not osf._osf_refusable_url(f"{api}/files/github/")
    assert not osf._osf_refusable_url(f"{api}/files/googledrive/abc/")


def test_osf_get_all_pages_marks_a_listing_short_when_a_later_page_fails_page_by_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a first page without the total, so later pages are asked one by one
    def one_page(url: str) -> object:
        if "page=2" in url:
            return osf._osf_error_result("forbidden")
        return {
            "data": [{"id": "a", "type": "files"}, {"id": "b", "type": "files"}],
            "links": {"next": "https://api.osf.io/v2/nodes/abcde/files/osfstorage/?page=2"},
        }

    monkeypatch.setattr(osf, "_osf_get_one_page", one_page)
    with pytest.warns(UserWarning, match="did not send every page"):
        out = osf.osf_get_all_pages("https://api.osf.io/v2/nodes/abcde/files/osfstorage/")
    assert len(out) == 2
    assert out.osf_incomplete is not None
