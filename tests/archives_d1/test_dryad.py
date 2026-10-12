"""Tests for metacheck.archives.dryad (port of tests/testthat/test-archive-dryad.R, plus more)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives import dryad
from metacheck.archives.dryad import (
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
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match="Dryad seems to be offline"):
        dryad_info("10.5061/dryad.j1fd7")


def test_dryad_info_table_with_a_missing_url(mock_api: object) -> None:
    # U33: metacheck's data.frame() takes .dryad_doi()'s URL names as row names
    # and fails on the NA; the row is kept without a DOI
    table = pd.DataFrame({"u": ["10.5061/dryad.j1fd7", None]})
    out = dryad_info(table)
    assert out["u"].tolist()[0] == "10.5061/dryad.j1fd7"
    assert out["dryad_doi"].tolist()[0] == "10.5061/dryad.j1fd7"
    assert pd.isna(out["dryad_doi"].iloc[1])


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


def test_dryad_info_follows_the_file_listing_pages(serve: object) -> None:
    # U38: metacheck reads only the first page of a version's file listing
    base = "https://datadryad.org/api/v2"
    body = {
        "title": "T",
        "identifier": "doi:10.5061/dryad.p",
        "_links": {"stash:version": {"href": "/api/v2/versions/9"}},
    }

    def page(names: list[str], nxt: str | None) -> httpx.Response:
        links = {"self": {"href": "x"}}
        if nxt is not None:
            links["next"] = {"href": nxt}
        files = [{"path": n, "size": 1} for n in names]
        return httpx.Response(200, json={"_embedded": {"stash:files": files}, "_links": links})

    seen = serve(  # type: ignore[operator]
        {
            f"{base}/datasets/doi%3A10.5061%2Fdryad.p": httpx.Response(200, json=body),
            f"{base}/versions/9/files": page(["a.csv", "b.csv"], "/api/v2/versions/9/files?page=2"),
            f"{base}/versions/9/files?page=2": page(["c.csv"], "/api/v2/versions/9/files?page=3"),
            f"{base}/versions/9/files?page=3": page([], None),
        }
    )
    info = _dryad_info("10.5061/dryad.p")
    assert [f["path"] for f in info["files"].iloc[0]] == ["a.csv", "b.csv", "c.csv"]
    assert len(seen) == 4
    # a listing that points back at a page already read stops there
    serve(  # type: ignore[operator]
        {f"{base}/versions/9/files?page=3": page(["d.csv"], "/api/v2/versions/9/files")}
    )
    info = _dryad_info("10.5061/dryad.p")
    assert [f["path"] for f in info["files"].iloc[0]] == ["a.csv", "b.csv", "c.csv", "d.csv"]


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


# R behaviour checked by hand (R 4.5.3, httr2 1.3.0, without httptest2, whose mocks
# bypass the auth hook): dryad_auth() with a token endpoint answering an OAuth
# `error`, a non-JSON body or JSON without `access_token` makes dryad_info() fail
# with an httr2_oauth* error, which req_perform_sequential() does not catch; a
# token endpoint that cannot be reached is an httr2_failure, so the dataset is
# only "unfound".


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (
            httpx.Response(401, json={"error": "invalid_client"}),
            r"OAuth failure \[invalid_client\]",
        ),
        (httpx.Response(200, text="<html>no</html>"), "Failed to parse"),
        (httpx.Response(200, json={"hello": 1}), "Failed to parse"),
        (httpx.Response(200, json={"access_token": 5}), "access_token"),
        (httpx.Response(200, json={"access_token": "t", "expires_in": 1.5}), "expires_in"),
    ],
)
def test_dryad_oauth_refusal_aborts_like_r(response: httpx.Response, match: str) -> None:
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.post("https://datadryad.org/oauth/token").mock(return_value=response)
        api = router.get(url__startswith="https://datadryad.org/api/").mock(
            return_value=httpx.Response(200, json={"title": "should not be reached"})
        )
        dryad_auth("cid", "bad")
        with pytest.raises(dryad.OAuthError, match=match):
            _dryad_headers({"url": "https://datadryad.org/api/v2/x", "headers": {}})
        with pytest.raises(dryad.OAuthError, match=match):
            _dryad_info("10.5061/dryad.x")
        with pytest.raises(dryad.OAuthError, match=match):
            dryad_file_download("10.5061/dryad.x", download_to="/nonexistent-never-created")
        # several datasets: each error becomes a warning, as in R
        with pytest.warns(UserWarning, match="resulted in an error"):
            assert dryad_file_download(["10.5061/dryad.x", "10.5061/dryad.y"]) is None
        assert not api.called


def test_dryad_oauth_status_is_ignored_when_the_body_has_a_token() -> None:
    import respx

    # oauth_flow_fetch() sets req_error(is_error = FALSE): only the body counts
    with respx.mock(assert_all_called=False) as router:
        router.post("https://datadryad.org/oauth/token").mock(
            return_value=httpx.Response(
                401, json={"access_token": "tok", "expires_in": "3600", "token_type": "bearer"}
            )
        )
        dryad_auth("cid", "odd")
        spec = _dryad_headers({"url": "https://datadryad.org/api/v2/x", "headers": {}})
    assert spec["headers"]["Authorization"] == "Bearer tok"


def test_dryad_oauth_unreachable_is_a_failed_request() -> None:
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.post("https://datadryad.org/oauth/token").mock(
            side_effect=httpx.ConnectError("refused")
        )
        router.get(url__startswith="https://datadryad.org/api/").mock(
            return_value=httpx.Response(200, json={"title": "should not be reached"})
        )
        dryad_auth("cid", "secret")
        with pytest.raises(ConnectionError):
            _dryad_headers({"url": "https://datadryad.org/api/v2/x", "headers": {}})
        with pytest.warns(UserWarning, match="could not be found on Dryad"):
            info = _dryad_info("10.5061/dryad.x")
    assert info["error"].tolist() == ["unfound"]


def _revoking_router(router: object, tokens: list[str]) -> list[str]:
    """Token endpoint handing out *tokens* in turn; the API rejects all but the last."""
    seen: list[str] = []
    issued = iter(tokens)

    def token(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": next(issued), "expires_in": 3600})

    def api(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("Authorization", "")
        seen.append(auth)
        if auth != f"Bearer {tokens[-1]}":
            return httpx.Response(
                401, headers={"WWW-Authenticate": 'Bearer realm="x", error="invalid_token"'}
            )
        if req.url.path.endswith("/download"):
            return httpx.Response(200, content=b"x,y\n1,2\n")
        if req.url.path.endswith("/files"):
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "stash:files": [
                            {
                                "_links": {
                                    "self": {"href": "/api/v2/files/1"},
                                    "stash:download": {"href": "/api/v2/files/1/download"},
                                },
                                "path": "d.csv",
                                "size": 8,
                            }
                        ]
                    }
                },
            )
        return httpx.Response(
            200, json={"title": "T", "_links": {"stash:version": {"href": "/api/v2/versions/9"}}}
        )

    router.post("https://datadryad.org/oauth/token").mock(side_effect=token)  # type: ignore[attr-defined]
    router.get(url__startswith="https://datadryad.org/api/").mock(side_effect=api)  # type: ignore[attr-defined]
    return seen


def test_dryad_oauth_reauthenticates_once_after_invalid_token(tmp_path: Path) -> None:
    import respx

    # httr2 req_perform(): a 401 with error="invalid_token" clears the cached token
    # and sends the request once more (resp_is_invalid_oauth_token())
    with respx.mock(assert_all_called=False) as router:
        seen = _revoking_router(router, ["revoked", "fresh"])
        dryad_auth("cid", "secret")
        info = _dryad_info("10.5061/dryad.x")
        assert info["title"].tolist() == ["T"]
        assert seen[:2] == ["Bearer revoked", "Bearer fresh"]

    with respx.mock(assert_all_called=False) as router:
        dryad._TOKENS.clear()
        seen = _revoking_router(router, ["old", "new"])
        # the metadata request re-authenticates; the download then uses the fresh token
        dl = dryad_file_download("10.5061/dryad.x", download_to=str(tmp_path))
        assert dl is not None and dl["downloaded"].tolist() == [True]

    with respx.mock(assert_all_called=False) as router:
        dryad._TOKENS.clear()
        seen = _revoking_router(router, ["a", "b", "c"])  # only the third is accepted
        with pytest.warns(UserWarning, match="could not be found on Dryad"):
            info = _dryad_info("10.5061/dryad.x")
        assert info["error"].tolist() == ["unfound"]  # one re-authentication only
        assert seen == ["Bearer a", "Bearer b"]


def test_dryad_download_reauthenticates_once(tmp_path: Path) -> None:
    import respx

    from metacheck.archives.dataverse import _fetch_file

    with respx.mock(assert_all_called=False) as router:
        seen = _revoking_router(router, ["stale", "fresh"])
        dryad_auth("cid", "secret")
        headers = _dryad_headers({"headers": {}})["headers"]
        target = str(tmp_path / "f")
        ok = _fetch_file(
            "https://datadryad.org/api/v2/files/1/download", headers, target, dryad._dryad_reauth
        )
        assert ok and seen == ["Bearer stale", "Bearer fresh"]
        # a static token has no OAuth policy: no second attempt
        dryad._TOKENS.clear()
        dryad_pat("static")
        from metacheck import utils

        with utils.local_options(
            {"metacheck.dryad.client_id": None, "metacheck.dryad.client_secret": None}
        ):
            seen.clear()
            headers = _dryad_headers({"headers": {}})["headers"]
            assert not _fetch_file(
                "https://datadryad.org/api/v2/files/1/download",
                headers,
                target,
                dryad._dryad_reauth,
            )
            assert seen == ["Bearer static"]


# --------------------------------------------------------------------------- downloads


def test_dryad_file_download(mock_api: object, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="2 of 4 files from Dryad dataset 10.5061/dryad.j1fd7"):
        dl = dryad_file_download("https://doi.org/10.5061/dryad.j1fd7", download_to=str(tmp_path))
    assert dl is not None
    assert dl.columns.tolist() == list(dryad._FILE_COLUMNS)
    # the omitted (too large) big.bin stays in the table, not downloaded (U36)
    assert dl["key"].tolist() == ["data.csv", "README.md", "big.bin", "sub/notes.txt", "nolink.txt"]
    assert dl["downloaded"].tolist() == [True, False, False, True, False]
    assert dl["checksum_ok"].tolist()[:2] == [True, False]
    folder = tmp_path / "10.5061_dryad.j1fd7"
    assert (folder / "data.csv").read_bytes() == b"x,y\n1,2\n"
    assert (folder / "sub" / "notes.txt").exists()
    assert set(dl["folder"]) == {"10.5061_dryad.j1fd7"}


def test_dryad_file_download_nothing_to_do(mock_api: object, tmp_path: Path) -> None:
    assert dryad_file_download("not a doi", download_to=str(tmp_path)) is None
    assert dryad_file_download("10.5061/dryad.nofiles", download_to=str(tmp_path)) is None
    # every file omitted: listed, none downloaded, no folder made (U36)
    out = dryad_file_download("10.5061/dryad.j1fd7", download_to=str(tmp_path), max_file_size=1e-6)
    assert out is not None and len(out) == 5
    assert not out["downloaded"].any()
    assert out["folder"].isna().all()
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


# ---------------------------------------------------------------- review round 2


def test_dryad_doi_escapes_decoding_to_invalid_utf8_find_nothing() -> None:
    # R: regexec(perl = TRUE) warns "input string 1 is invalid UTF-8" and matches nothing
    with pytest.warns(UserWarning, match="invalid UTF-8"):
        out = dryad._dryad_doi(
            ["10.5061/dryad.abc%E2%80", "%FF10.5061/dryad.abc", "10.5061/dryad.ok"]
        )
    assert out == [None, None, "10.5061/dryad.ok"]
    # a valid multi-byte escape is fine
    assert dryad._dryad_doi("10.5061/dryad.abc%E2%80%93x") == "10.5061/dryad.abc"


def test_dryad_links_on_an_empty_paper_list() -> None:
    out = dryad.dryad_links(pc.PaperList([]))
    # href first, as in a non-empty table (text_search() of an empty paper list: U79)
    assert out.columns.tolist() == ["href", "text_id", "paper_id", "dryad_url", "dryad_doi"]
    assert len(out) == 0


def test_dryad_file_download_keeps_an_empty_file(mock_api: object, tmp_path: Path) -> None:
    # U36: metacheck aborts the whole download on a zero-byte file
    out = dryad.dryad_file_download("10.5061/dryad.rev2", download_to=str(tmp_path))
    assert out is not None
    empty = out[out["key"] == "empty.txt"]
    assert empty["downloaded"].tolist() == [True]
    assert empty["size_on_disk"].tolist() == [0]
