"""Port of metacheck's tests/testthat/test-archive-osf-helpers.R."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import osf_helpers
from metacheck.archives.osf import osf_file_download, osf_get_all_pages
from metacheck.archives.osf_helpers import (
    _osf_expand_user_ids,
    _osf_file_data,
    _osf_headers,
    _osf_info,
    _osf_parent_project,
    _osf_pat_validate,
    _osf_verify_downloads,
    osf_pat,
    osf_user_projects,
)
from metacheck.utils import local_options

API = "https://api.osf.io/v2"


def test_osf_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "")
    obs = _osf_headers({"method": "GET", "url": API, "headers": {"Accept": "x"}})
    assert obs["headers"]["User-Agent"] == "metacheck"
    assert obs["headers"]["Accept"] == "application/vnd.api+json"
    assert "Authorization" not in obs["headers"]
    assert obs["url"] == API

    monkeypatch.setenv("OSF_PAT", "NOPTAREALPAT")
    obs = _osf_headers({})
    assert obs["headers"]["Authorization"] == "Bearer NOPTAREALPAT"


def test_osf_headers_explicit_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "")
    assert _osf_headers(pat="explicit-token")["headers"]["Authorization"] == "Bearer explicit-token"
    assert "Authorization" not in _osf_headers(pat="")["headers"]


def test_osf_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "")
    with local_options({"metacheck.osf.pat": None}):
        assert osf_pat() == ""
        monkeypatch.setenv("OSF_PAT", "from-renviron")
        assert osf_pat() == "from-renviron"
        osf_pat("from-function")
        assert osf_pat() == "from-function"
        with pytest.raises(ValueError, match="single string"):
            osf_pat(123)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="single string"):
            osf_pat(["a", "b"])  # type: ignore[arg-type]


def test_osf_pat_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _osf_pat_validate("") is False
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: True)
    probe = f"{API}/preprints/khbvy/"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401 if "Authorization" in request.headers else 200)

    with respx.mock() as router:
        router.get(probe).mock(side_effect=handler)
        monkeypatch.setenv("OSF_PAT", "BADPAT")
        with pytest.warns(UserWarning, match="Clearing OSF_PAT"):
            assert _osf_pat_validate("BADPAT") is False
        assert os.environ["OSF_PAT"] == ""

    with respx.mock() as router:
        router.get(probe).mock(return_value=httpx.Response(200))
        assert _osf_pat_validate("GOODPAT") is True

    with respx.mock() as router:
        router.get(probe).mock(return_value=httpx.Response(503))
        with pytest.warns(UserWarning, match="could not be validated"):
            assert _osf_pat_validate("ANY") is False


def test_osf_pat_validate_is_one_short_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # metacheck ccc786cb: the probe runs at load time, so one try with a 5 s
    # timeout, not the default retries against a slow or unreachable OSF
    from metacheck import http

    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: True)
    calls: list[dict[str, object]] = []

    def fake(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append({"method": method, "url": url, **kwargs})
        return httpx.Response(200)

    monkeypatch.setattr(http, "request", fake)
    assert _osf_pat_validate("GOODPAT") is True
    # anonymously and with the token
    assert len(calls) == 2
    assert all(c["max_tries"] == 1 and c["timeout"] == 5.0 for c in calls)


def test_osf_parent_project(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    assert _osf_parent_project("yt32c") == "pngda"
    assert _osf_parent_project("pngda") == "pngda"
    assert _osf_parent_project("xp5cy") == "3cz2e"
    with pytest.warns(UserWarning):
        assert _osf_parent_project("pda") is None


def test_osf_file_data(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    data = osf_get_all_pages(f"{API}/nodes/mc45x/files/")
    obs = _osf_file_data(data)
    assert obs["provider"].tolist() == ["osfstorage", "github"]

    osf_obs = _osf_file_data(osf_get_all_pages(f"{API}/nodes/mc45x/files/osfstorage/"))
    assert osf_obs["parent"].iloc[0] == obs["osf_id"].iloc[0]

    gh_obs = _osf_file_data(osf_get_all_pages(f"{API}/nodes/mc45x/files/github/"))
    assert gh_obs["path"].tolist() == ["/code/", "/folder/", "/good-example.R", "/README.md"]

    code_obs = _osf_file_data(osf_get_all_pages(f"{API}/nodes/mc45x/files/github/code/"))
    assert set(code_obs["filetype"]) <= {"code"}
    assert code_obs["path"].tolist() == [f"/code/{i:02d}.R" for i in range(1, 26)]
    assert set(code_obs["parent"]) <= {"mc45x"}


def test_osf_file_data_names_unnamed_folders() -> None:
    records = [
        {"id": "a", "type": "files", "attributes": {"kind": "folder", "provider": "github"}},
        {
            "id": "b",
            "type": "files",
            "attributes": {"kind": "folder", "name": "x", "provider": "p"},
        },
    ]
    obs = _osf_file_data(records)
    assert obs["name"].tolist() == ["github", "x"]
    assert obs["parent"].isna().all()


def test_osf_info_types(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    info = _osf_info("68472f93b21328dc7f539482")
    assert info["name"].tolist() == ["test-folder"]
    assert info["osf_type"].tolist() == ["files"]
    assert info["kind"].tolist() == ["folder"]

    info = _osf_info("pngda")
    assert info["osf_id"].tolist() == ["pngda"]
    assert info["osf_type"].tolist() == ["nodes"]
    assert info["name"].tolist() == ["Papercheck Test"]
    assert info["children"].tolist() == [f"{API}/nodes/pngda/children/"]
    assert info["files"].tolist() == [f"{API}/nodes/pngda/files/"]

    info = _osf_info("6nt4v")
    assert info["name"].tolist() == ["Processed Data"]
    assert info["children"].tolist() == [f"{API}/nodes/6nt4v/children/"]

    info = _osf_info("75qgk")
    assert info["osf_type"].tolist() == ["files"]
    assert info["kind"].tolist() == ["file"]
    assert info["name"].tolist() == ["processed-data.csv"]

    info = _osf_info("xp5cy")
    assert "xp5cy" in info["osf_id"].iloc[0]
    assert info["osf_type"].tolist() == ["preprints"]
    title = "Understanding mixed effects models through data simulation"
    assert info["name"].tolist() == [title]

    info = _osf_info("8c3kb")
    assert info["osf_type"].tolist() == ["registrations"]
    assert info["name"].tolist() == [title]

    info = _osf_info("4i578")
    assert info["osf_type"].tolist() == ["users"]
    assert info["name"].tolist() == ["Lisa DeBruine"]


def test_osf_info_complex(mock_api: respx.MockRouter) -> None:
    info = _osf_info("ybm3c")
    assert info["osf_id"].tolist() == ["ybm3c"]
    assert info["public"].tolist() == [False]

    with pytest.warns(UserWarning):
        info = _osf_info("xx")
    assert info["osf_id"].tolist() == ["xx"]
    assert info["osf_type"].tolist() == ["invalid"]

    with pytest.warns(UserWarning, match="could not be found on the OSF"):
        info = _osf_info("xxxxx")
    assert info["osf_type"].tolist() == ["unfound"]

    info = _osf_info(["mc45x", "y6a34"])
    assert info["osf_id"].tolist() == ["mc45x", "y6a34"]
    assert info["osf_type"].tolist() == ["nodes", "nodes"]

    info = _osf_info(["mc45x", "y6a34", "4i578"])
    assert info["osf_type"].tolist() == ["nodes", "nodes", "users"]


def test_osf_info_http_statuses() -> None:
    with respx.mock() as router:
        router.get(f"{API}/guids/aaaaa").mock(return_value=httpx.Response(401))
        router.get(f"{API}/guids/bbbbb").mock(return_value=httpx.Response(410))
        router.get(f"{API}/guids/ccccc").mock(side_effect=httpx.ConnectError("down"))
        with pytest.warns(UserWarning, match="deleted or withdrawn"):
            info = _osf_info(["aaaaa", "bbbbb", "ccccc"])
    assert info["osf_type"].tolist() == ["private", "unfound", "error"]
    assert info["public"].tolist()[0] is False


def test_osf_id_vs_waterbutler_id(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    by_guid = _osf_info("k6gbt")
    by_wb = _osf_info("6846ed88e49694cd45ab8375")
    pd.testing.assert_frame_equal(by_guid.iloc[:, 1:11], by_wb.iloc[:, 1:11])


def test_osf_expand_user_ids_passthrough() -> None:
    assert _osf_expand_user_ids([]) == []
    wb = "a" * 24
    assert _osf_expand_user_ids([wb]) == [wb]


def _node(nid: str, title: str | None, root: str | None, public: bool = True) -> dict:
    rel = {"root": {"data": {"id": root}}} if root else {}
    return {
        "id": nid,
        "type": "nodes",
        "attributes": {"title": title, "category": "project", "public": public},
        "relationships": rel,
    }


def test_osf_user_projects_and_expansion(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = {
        "data": [
            _node("proj1", "Project One", "proj1"),
            _node("comp1", "Component", "proj1"),
            _node("comp2", "Orphan component", "proj2"),
            _node("proj3", "Private", "proj3", public=False),
        ],
        "links": {"next": None, "meta": {"total": 4, "per_page": 100}},
    }
    with respx.mock() as router:
        router.get(url__startswith=f"{API}/users/user1/nodes/").mock(
            return_value=httpx.Response(200, json=listing)
        )
        router.get(f"{API}/nodes/proj2/").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"attributes": {"title": "Two", "category": "data", "public": True}}},
            )
        )
        router.get(url__startswith=f"{API}/guids/user1/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "relationships": {
                            "referent": {"links": {"related": {"meta": {"type": "users"}}}}
                        }
                    }
                },
            )
        )
        router.get(url__startswith=f"{API}/guids/proj9/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "relationships": {
                            "referent": {"links": {"related": {"meta": {"type": "nodes"}}}}
                        }
                    }
                },
            )
        )
        projects = osf_user_projects("user1")
        assert list(projects.columns) == ["osf_id", "name", "category", "public", "osf_url"]
        assert projects["osf_id"].tolist() == ["proj1", "proj3", "proj2"]
        assert projects["name"].tolist() == ["Project One", "Private", "Two"]
        assert projects["public"].tolist() == [True, False, True]
        assert projects["osf_url"].tolist()[0] == "https://osf.io/proj1"

        from metacheck.config import _state

        monkeypatch.setitem(_state, "verbose", True)
        expanded = _osf_expand_user_ids(["proj9", "user1", "proj1"])
        assert expanded == ["proj9", "proj1", "proj3", "proj2"]
        assert "projects to download" in capsys.readouterr().err


def test_osf_user_projects_nothing() -> None:
    assert len(osf_user_projects([None])) == 0
    with respx.mock() as router:
        router.get(url__startswith=f"{API}/users/abcde/nodes/").mock(
            return_value=httpx.Response(404)
        )
        assert list(osf_user_projects("abcde").columns) == [
            "osf_id",
            "name",
            "category",
            "public",
            "osf_url",
        ]


def test_osf_verify_downloads(tmp_path: Path) -> None:
    d = str(tmp_path)
    (tmp_path / "good.txt").write_text("hello\n")
    good_size = (tmp_path / "good.txt").stat().st_size

    def frame(**cols: list) -> pd.DataFrame:
        return pd.DataFrame(cols)

    r = frame(path=["good.txt"], size=[good_size], downloaded=[True])
    assert _osf_verify_downloads(r, d)["downloaded"].tolist() == [True]

    v = _osf_verify_downloads(frame(path=["good.txt"], size=[999999], downloaded=[True]), d)
    assert v["downloaded"].tolist() == [False]
    assert v["size_on_disk"].tolist() == [float(good_size)]

    assert not _osf_verify_downloads(frame(path=["missing.txt"], size=[10], downloaded=[True]), d)[
        "downloaded"
    ].iloc[0]
    assert not _osf_verify_downloads(frame(path=[None], size=[10], downloaded=[True]), d)[
        "downloaded"
    ].iloc[0]
    assert _osf_verify_downloads(frame(path=["good.txt"], size=[None], downloaded=[True]), d)[
        "downloaded"
    ].iloc[0]
    assert not _osf_verify_downloads(
        frame(path=["good.txt"], size=[good_size], downloaded=[False]), d
    )["downloaded"].iloc[0]
    (tmp_path / "adir").mkdir()
    assert not _osf_verify_downloads(frame(path=["adir"], size=[None], downloaded=[True]), d)[
        "downloaded"
    ].iloc[0]
    assert _osf_verify_downloads(
        frame(path=["good.txt"], size=[999999], downloaded=[True]), d, check_size=False
    )["downloaded"].iloc[0]
    assert len(_osf_verify_downloads(r.iloc[0:0], d)) == 0
    assert not _osf_verify_downloads(frame(size=[1], downloaded=[True]), d)["downloaded"].iloc[0]
    # inputs are never modified
    assert "size_on_disk" not in r.columns


def test_osf_file_download_needs_an_id_column() -> None:
    with pytest.raises(ValueError, match="no `osf_id` column"):
        osf_file_download(pd.DataFrame({"name": ["x"]}))


def test_osf_file_download_invalid_id() -> None:
    with pytest.warns(UserWarning, match="not a valid OSF ID"):
        assert osf_file_download("notanid") is None


def test_no_http_at_import() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert osf_helpers.__name__ == "metacheck.archives.osf_helpers"
