"""Port of metacheck's tests/testthat/test-archive-osf-helpers.R."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

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
    # the token is checked once, with a request (tests/archives_gz/test_api_keys.py)
    monkeypatch.setattr("metacheck.archives.osf_helpers._osf_key_check", lambda key: True)
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


def test_osf_pat_validate_is_a_bounded_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # metacheck ccc786cb: the probe runs at load time, so it is bounded, not the
    # default retries against a slow or unreachable OSF; d52b3fd: two tries of
    # 15 s, so a slow OSF does not make a valid token look unverifiable
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
    assert all(c["max_tries"] == 2 and c["timeout"] == 15.0 for c in calls)


def test_osf_pat_validate_retries_a_failed_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: True)
    probe = f"{API}/preprints/khbvy/"
    with respx.mock() as router:
        route = router.get(probe).mock(
            side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200), httpx.Response(200)]
        )
        assert _osf_pat_validate("GOODPAT") is True
    assert route.call_count == 3


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


# -- #672: a refused OSF request is not "not found"; a file is asked for without contributors


def _osf_json(status: int, body: str) -> httpx.Response:
    return httpx.Response(
        status, content=body.encode(), headers={"Content-Type": "application/vnd.api+json"}
    )


def test_a_refused_osf_request_is_not_reported_as_not_found_and_a_file_is_asked_for_again() -> None:
    from metacheck.archives.osf_helpers import _osf_embed_refused, _osf_parse_response

    embed = _osf_json(
        400,
        '{"errors":[{"source":{"parameter":"embed"},"detail":"The following fields are not '
        'embeddable: bibliographic_contributors","status":"400"}]}',
    )
    other = _osf_json(400, '{"errors":[{"detail":"Bad filter"}]}')
    assert _osf_embed_refused(embed) is True
    assert _osf_embed_refused(other) is False
    assert _osf_embed_refused(httpx.Response(404)) is False
    assert _osf_embed_refused(RuntimeError("no answer")) is False

    with pytest.warns(UserWarning, match="refused the request"):
        r = _osf_parse_response(other, osf_id="abcde")
    assert r is not None
    assert r["osf_type"].tolist() == ["error"]
    assert r["error"].tolist() == ["the OSF refused the request (HTTP 400): Bad filter"]
    from metacheck.archives.info_cache import _repo_info_ok

    assert _repo_info_ok(r) is False

    # _osf_info() asks for a file again without the contributors
    asked: list[str] = []
    file_json = (
        '{"data":{"id":"64db83e25e739d047b39b61e","type":"files","attributes":{"kind":"file",'
        '"name":"a.docx","size":10},"links":{"download":"https://osf.io/download/abcde/"},'
        '"relationships":{}}}'
    )
    with respx.mock(assert_all_called=False) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            return embed if "embed=" in str(request.url) else _osf_json(200, file_json)

        router.get(url__regex=r"https://api\.osf\.io/v2/guids/abcde.*").mock(side_effect=handler)
        info = _osf_info("abcde")
    assert asked == [
        "https://api.osf.io/v2/guids/abcde?embed=bibliographic_contributors",
        "https://api.osf.io/v2/guids/abcde",
    ]
    assert info["osf_type"].tolist() == ["files"]
    assert info["name"].tolist() == ["a.docx"]


def _contributor_page(n: int, total: int | None = None) -> dict[str, Any]:
    """A page of bibliographic contributors as the OSF sends it (names ``Person 1`` ...)."""
    data = [
        {
            "embeds": {
                "users": {
                    "data": {
                        "attributes": {
                            "full_name": f"Person {k}",
                            "family_name": f"P{k}",
                            "social": {"orcid": None},
                        }
                    }
                }
            }
        }
        for k in range(1, n + 1)
    ]
    return {"data": data, "links": {"meta": {"total": total if total is not None else n}}}


def test_a_record_with_more_contributors_than_the_osf_named_gets_the_full_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.archives import osf as osf_mod
    from metacheck.archives.osf_helpers import _osf_complete_contributors

    asked: list[str] = []

    def pages(urls: list[str]) -> list[Any]:
        asked.extend(urls)
        out: list[Any] = []
        for u in urls:
            if "fail" in u:
                out.append(osf_mod._osf_error_result("request_failed"))
            else:
                page = _contributor_page(11)
                page["data"][10]["embeds"]["users"]["data"]["attributes"].update(
                    full_name="Anna van 't Veer", family_name="van 't Veer"
                )
                out.append(page)
        return out

    monkeypatch.setattr(osf_mod, "_osf_get_pages_parallel", pages)
    ten = "; ".join(f"Person {k}" for k in range(1, 11))

    def row(osf_id: str, kind: str, **cols: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {"osf_id": [osf_id], "osf_type": [kind], **{k: [v] for k, v in cols.items()}}
        )

    rows = [
        row("xv5rp", "nodes", contributors=ten, n_contributors=11),
        row("small", "nodes", contributors="A B; C D", n_contributors=2),
        row("old10", "registrations", contributors=ten),  # cached before the count
        row("fail1", "nodes", contributors=ten, n_contributors=12),
        row("f1", "files"),
    ]
    out = _osf_complete_contributors(rows)
    assert sorted(u.split("/")[-3] for u in asked) == ["fail1", "old10", "xv5rp"]
    assert any("/registrations/old10/bibliographic_contributors/" in u for u in asked)
    assert "Anna van 't Veer" in out[0]["contributors"].iloc[0]
    assert "van 't Veer" in out[0]["contributor_families"].iloc[0]
    assert out[0]["n_contributors"].iloc[0] == 11
    assert out[2]["n_contributors"].iloc[0] == 11
    assert out[1] is rows[1]
    assert out[3] is rows[3]  # a failed request leaves the record as it was
    assert out[4] is rows[4]
    # nothing to complete: no request at all
    asked.clear()
    same = [rows[1], rows[4]]
    assert _osf_complete_contributors(same) is same
    assert asked == []


def test_a_whole_listing_gets_the_full_list_of_contributors_for_its_projects_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.archives import osf as osf_mod
    from metacheck.archives.osf_helpers import _osf_complete_table

    monkeypatch.setattr(
        osf_mod, "_osf_get_pages_parallel", lambda urls: [_contributor_page(11) for _ in urls]
    )
    ten = "; ".join(f"Person {k}" for k in range(1, 11))
    tbl = pd.DataFrame(
        {
            "osf_id": ["xv5rp", "comp1", "f1"],
            "osf_type": ["nodes", "nodes", "files"],
            "contributors": pd.Series([ten, "A B", None], dtype="string"),
        }
    )
    out = _osf_complete_table(tbl)
    assert out["n_contributors"].tolist()[0] == 11
    assert out["n_contributors"].isna().tolist() == [False, True, True]
    assert "Person 11" in out["contributors"].iloc[0]
    assert "P11" in out["contributor_families"].iloc[0]
    assert out["contributors"].iloc[1:].tolist() == ["A B", pd.NA]
    rest = tbl.iloc[1:]
    assert _osf_complete_table(rest) is rest
    empty = pd.DataFrame()
    assert _osf_complete_table(empty) is empty
