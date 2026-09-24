"""Tests for pytacheck.archives.dryad (port of tests/testthat/test-archive-dryad.R, plus more)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives import dryad
from pytacheck.archives.dryad import (
    _dryad_auth,
    _dryad_doi,
    _dryad_doi_prefixes,
    _dryad_headers,
    _dryad_info,
    _dryad_verify_downloads,
    dryad_auth,
    dryad_file_download,
    dryad_info,
    dryad_links,
    dryad_pat,
)

# --------------------------------------------------------------------------- testthat ports


def test_dryad_links_recognises_every_alternate_prefix() -> None:
    assert callable(dryad_links)
    alt = [p for p in _dryad_doi_prefixes() if p != "10.5061"]
    urls = [
        "https://doi.org/10.5061/dryad.j1fd7",
        *[f"https://doi.org/{p}/ABC123" for p in alt],
        "https://doi.org/10.9999/unrelated123",
    ]
    links = dryad_links(pc.test_paper(url=urls))
    assert len(links) == len(alt) + 1
    prefixes = {d.rsplit("/", 1)[0] + "/" for d in links["dryad_doi"]}
    assert {f"{p}/" for p in ["10.5061", *alt]} <= prefixes


def test_dryad_doi_extracts_every_recognised_prefix() -> None:
    assert _dryad_doi("https://doi.org/10.5061/dryad.j1fd7") == "10.5061/dryad.j1fd7"
    assert _dryad_doi("https://doi.org/10.25338/B8N33J") == "10.25338/b8n33j"
    assert _dryad_doi("https://doi.org/10.5068/D14671") == "10.5068/d14671"
    assert _dryad_doi("https://doi.org/10.6075/j0w37ttw") == "10.6075/j0w37ttw"
    assert _dryad_doi("https://doi.org/10.7941/d18907") == "10.7941/d18907"
    # an unrelated DOI under another registrant prefix is not Dryad's
    assert _dryad_doi("https://doi.org/10.9999/unrelated123") is None


def test_dryad_info_on_its_own_links_for_an_unfound_doi(mock_api: object) -> None:
    paper = pc.test_paper(url=["https://doi.org/10.5061/dryad.notarealdataset999"])
    links = dryad_links(paper)
    with pytest.warns(UserWarning, match="could not be found on Dryad"):
        info = dryad_info(links)
    assert info["dryad_doi"].tolist() == ["10.5061/dryad.notarealdataset999"]
    assert info["error"].tolist() == ["unfound"]
    assert "dryad_doi.x" not in info.columns


# --------------------------------------------------------------------------- ids and links


def test_dryad_doi_vectorised_and_edge_cases() -> None:
    assert _dryad_doi(None) == []
    assert _dryad_doi([]) == []
    assert _dryad_doi(["", None, "  10.5061/DRYAD.AbC  "]) == [None, None, "10.5061/dryad.abc"]
    # URL-decoded before matching
    assert _dryad_doi("https://datadryad.org/stash/dataset/doi%3A10.5061%2Fdryad.x1") == (
        "10.5061/dryad.x1"
    )
    # R's URLdecode() arithmetic: "%4g" is a byte ("P"), a trailing bad escape is dropped
    with pytest.warns(UserWarning, match="out-of-range"):
        assert _dryad_doi("10.5061/dryad.q2%zz") == "10.5061/dryad.q2"
    with pytest.raises(ValueError, match="embedded nul"), pytest.warns(UserWarning):
        _dryad_doi("10.5061/dryad.a%zzb")


def test_dryad_links_bare_mentions_and_normalisation() -> None:
    paper = pc.test_paper(
        [
            "Data: 10.5061/dryad.j1fd7 and datadryad.org/stash/dataset/doi:10.5061/dryad.xyz/",
            "unrelated 10.9999/abc",
        ],
        url=["https://doi.org/10.5061/dryad.j1fd7/"],
    )
    links = dryad_links(paper)
    assert links.columns.tolist()[-2:] == ["dryad_url", "dryad_doi"]
    assert "https://doi.org/10.5061/dryad.j1fd7" in links["href"].tolist()
    assert not any(h.endswith("/") for h in links["href"])
    assert set(links["dryad_doi"]) == {"10.5061/dryad.j1fd7", "10.5061/dryad.xyz"}


def test_dryad_links_none(demo: pc.Paper) -> None:
    links = dryad_links(demo)
    assert len(links) == 0
    assert "dryad_doi" in links.columns


# --------------------------------------------------------------------------- info


def test_dryad_info_found(mock_api: object) -> None:
    info = dryad_info("https://doi.org/10.5061/dryad.j1fd7")
    assert info.columns.tolist() == [
        "dryad_url",
        "dryad_doi",
        "title",
        "doi",
        "publication_date",
        "updated_date",
        "authors",
        "license",
        "files",
    ]
    row = info.iloc[0]
    assert row["title"] == "Data from: An example study"
    assert row["authors"] == ["Jane Doe", "Smith", None, "Solo"]
    assert len(row["files"]) == 5


def test_dryad_info_errors_and_offline(mock_api: object, monkeypatch: pytest.MonkeyPatch) -> None:
    info = dryad_info(["10.5061/dryad.notjson", "nope"])
    assert info["error"].tolist()[0] == "parse_error"
    assert pd.isna(info["dryad_doi"].iloc[1])
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match="Dryad seems to be offline"):
        dryad_info("10.5061/dryad.j1fd7")


def test_dryad_info_table_with_missing_url_errors_like_r(mock_api: object) -> None:
    # R: data.frame() takes .dryad_doi()'s URL names as row names and fails on NA
    table = pd.DataFrame({"u": ["10.5061/dryad.j1fd7", None]})
    with pytest.raises(ValueError, match="row names contain missing values"):
        dryad_info(table)


def test_dryad_info_uses_the_listing_cache(mock_api: object) -> None:
    first = dryad_info("10.5061/dryad.j1fd7", cache=True)
    calls = []
    orig = dryad._dryad_info

    def spy(doi: str, pb: object = None) -> pd.DataFrame:
        calls.append(doi)
        return orig(doi, pb=pb)

    dryad._dryad_info = spy  # type: ignore[assignment]
    try:
        again = dryad_info("10.5061/dryad.j1fd7", cache=True)
    finally:
        dryad._dryad_info = orig  # type: ignore[assignment]
    assert calls == []
    assert again["title"].tolist() == first["title"].tolist()


def test_private_dryad_info_requests_encoded_doi(serve: object) -> None:
    body = {"title": "T", "identifier": "doi:10.5061/dryad.x", "authors": []}
    seen = serve(  # type: ignore[operator]
        {
            "https://datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.x": httpx.Response(
                200, json=body
            )
        }
    )
    info = _dryad_info("10.5061/dryad.x")
    assert info["title"].tolist() == ["T"]
    assert info["files"].iloc[0] == []
    assert seen[0].headers["User-Agent"] == "metacheck"
    assert "Authorization" not in seen[0].headers


# --------------------------------------------------------------------------- tokens


def test_dryad_pat_get_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert dryad_pat() == ""
    monkeypatch.setenv("DRYAD_PAT", "from-env")
    assert dryad_pat() == "from-env"
    assert dryad_pat("set") == "set"
    assert dryad_pat() == "set"
    with pytest.raises(ValueError, match="single string"):
        dryad_pat(123)  # type: ignore[arg-type]


def test_dryad_auth_get_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert dryad_auth() is None
    monkeypatch.setenv("DRYAD_CLIENT_ID", "id-env")
    assert dryad_auth() is None  # a secret is needed too
    monkeypatch.setenv("DRYAD_CLIENT_SECRET", "secret-env")
    client = dryad_auth()
    assert client is not None and client.id == "id-env" and client.secret == "secret-env"
    assert "secret" not in repr(client)
    client = dryad_auth("cid", "csecret", token_url="https://example.org/token")
    assert _dryad_auth() == client
    with pytest.raises(ValueError, match="single client_id string"):
        dryad_auth("cid")


def test_dryad_headers_static_token() -> None:
    dryad_pat("tok")
    spec = _dryad_headers({"method": "GET", "url": "https://datadryad.org/x", "headers": {}})
    assert spec["headers"] == {"User-Agent": "metacheck", "Authorization": "Bearer tok"}


def test_dryad_headers_oauth_fetches_caches_and_refreshes() -> None:
    import respx

    tokens = iter(["t1", "t2"])
    calls = []

    def token_endpoint() -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 3600})

    with respx.mock(assert_all_called=False) as router:
        router.post("https://datadryad.org/oauth/token").mock(
            side_effect=lambda req: token_endpoint()
        )
        dryad_auth("cid", "csecret")
        pat_ignored = dryad_pat("static")
        assert pat_ignored == "static"
        spec = _dryad_headers({"url": "https://datadryad.org/api/v2/x", "headers": {}})
        assert spec["headers"]["Authorization"] == "Bearer t1"
        spec = _dryad_headers({"url": "https://datadryad.org/api/v2/y", "headers": {}})
        assert spec["headers"]["Authorization"] == "Bearer t1"  # cached
        assert len(calls) == 1
        body = router.calls[0].request.content.decode()
        assert "grant_type=client_credentials" in body and "client_id=cid" in body
        # expire it: a new token is fetched
        client = dryad._dryad_oauth_client()
        tok, _ = dryad._TOKENS[client]
        dryad._TOKENS[client] = (tok, time.time() + 5)
        spec = _dryad_headers({"url": "https://datadryad.org/api/v2/z", "headers": {}})
        assert spec["headers"]["Authorization"] == "Bearer t2"


def test_dryad_oauth_failure_is_a_failed_request() -> None:
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.post("https://datadryad.org/oauth/token").mock(
            return_value=httpx.Response(401, json={"error": "invalid_client"})
        )
        router.get(url__startswith="https://datadryad.org/api/").mock(
            return_value=httpx.Response(200, json={"title": "should not be reached"})
        )
        dryad_auth("cid", "bad")
        with pytest.raises(PermissionError, match="invalid_client"):
            _dryad_headers({"url": "https://datadryad.org/api/v2/x", "headers": {}})
        with pytest.warns(UserWarning, match="could not be found on Dryad"):
            info = _dryad_info("10.5061/dryad.x")
    assert info["error"].tolist() == ["unfound"]


# --------------------------------------------------------------------------- downloads


def test_dryad_file_download(mock_api: object, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="2 of 4 files from Dryad dataset 10.5061/dryad.j1fd7"):
        dl = dryad_file_download("https://doi.org/10.5061/dryad.j1fd7", download_to=str(tmp_path))
    assert dl is not None
    assert dl.columns.tolist() == list(dryad._FILE_COLUMNS)
    assert dl["key"].tolist() == ["data.csv", "README.md", "sub/notes.txt", "nolink.txt"]
    assert dl["downloaded"].tolist() == [True, False, True, False]
    assert dl["checksum_ok"].tolist()[:2] == [True, False]
    folder = tmp_path / "10.5061_dryad.j1fd7"
    assert (folder / "data.csv").read_bytes() == b"x,y\n1,2\n"
    assert (folder / "sub" / "notes.txt").exists()
    assert set(dl["folder"]) == {"10.5061_dryad.j1fd7"}


def test_dryad_file_download_nothing_to_do(mock_api: object, tmp_path: Path) -> None:
    assert dryad_file_download("not a doi", download_to=str(tmp_path)) is None
    assert dryad_file_download("10.5061/dryad.nofiles", download_to=str(tmp_path)) is None
    assert (
        dryad_file_download("10.5061/dryad.j1fd7", download_to=str(tmp_path), max_file_size=1e-6)
        is None
    )
    assert os.listdir(tmp_path) == []


def test_dryad_file_download_multiple(mock_api: object, tmp_path: Path) -> None:
    with pytest.warns(UserWarning):
        dl = dryad_file_download(
            ["10.5061/dryad.j1fd7", "10.25338/B8N33J", "10.5061/dryad.missing"],
            download_to=str(tmp_path),
        )
    assert dl is not None
    assert list(dict.fromkeys(dl["dryad_doi"])) == ["10.5061/dryad.j1fd7", "10.25338/b8n33j"]


def test_dryad_file_download_skips_hostile_names(serve: object, tmp_path: Path) -> None:
    files = [
        {
            "_links": {
                "self": {"href": "/api/v2/files/1"},
                "stash:download": {"href": "/api/v2/files/1/download"},
            },
            "path": "../escape.txt",
            "size": 2,
        }
    ]
    serve(  # type: ignore[operator]
        {
            "https://datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.h": httpx.Response(
                200, json={"_links": {"stash:version": {"href": "/api/v2/versions/9"}}}
            ),
            "https://datadryad.org/api/v2/versions/9/files": httpx.Response(
                200, json={"_embedded": {"stash:files": files}}
            ),
            "https://datadryad.org/api/v2/files/1/download": httpx.Response(200, content=b"hi"),
        }
    )
    target = tmp_path / "dl"
    target.mkdir()
    with pytest.warns(UserWarning, match="did not arrive intact"):
        dl = dryad_file_download("10.5061/dryad.h", download_to=str(target))
    assert dl is not None and dl["downloaded"].tolist() == [False]
    assert not (tmp_path / "escape.txt").exists()


def test_dryad_verify_downloads(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"x,y\n")
    files = pd.DataFrame(
        {
            "path": ["a.csv", "a.csv", None],
            "size": [4.0, 5.0, 1.0],
            "checksum": ["043212bb9834e334677e9c9659294bd4", None, None],
            "checksum_type": ["md5", None, None],
            "downloaded": [True, True, True],
        }
    )
    out = _dryad_verify_downloads(files, str(tmp_path))
    assert out["downloaded"].tolist() == [True, False, False]
    assert out["checksum_ok"].tolist()[0] is True
    assert files["downloaded"].tolist() == [True, True, True]  # input untouched
