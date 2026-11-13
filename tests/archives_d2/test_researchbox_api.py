"""Tests of the ResearchBox API route (port of test-archive-researchbox.R, metacheck 0.4.3.x).

Recorded API answers are metacheck's own ``tests/testthat/apis`` (``replay("apis")``);
the others mock ``_rbox_api()`` or the HTTP layer. Nothing here reaches the internet.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import warnings
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import researchbox as rb
from metacheck.archives.researchbox import (
    _rbox_api,
    _rbox_box_meta,
    _rbox_info,
    _rbox_license,
    _rbox_retry_after,
    _rbox_unfound_times,
    rbox_file_download,
    rbox_files,
    rbox_info,
    rbox_search,
)
from metacheck.utils import local_options
from tests.httpmock import replay

KEY = "test-token"
SECRET = "sk-secret-key-0123456789"
API = "https://researchbox.org/api_call.php"


@pytest.fixture(autouse=True)
def _session(tmp_path: Path) -> Iterator[None]:
    """A key, and every folder the functions write to in the test's own folder."""
    with local_options(
        {
            "metacheck.researchbox.token": KEY,
            "metacheck.repo_cache.session_dir": str(tmp_path / "session"),
            "metacheck.state.dir": str(tmp_path / "state"),
        }
    ):
        yield


@pytest.fixture
def no_key() -> Iterator[None]:
    with local_options({"metacheck.researchbox.token": ""}):
        yield


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API's 6 s pause between requests, and the waits for a retry, are skipped."""
    from metacheck import http

    monkeypatch.setattr(rb, "_throttle", lambda: http.Throttle(1000, 1))
    monkeypatch.setattr(http, "sleep", lambda *a, **k: None)


def _json(status: int, body: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, content=body.encode(), headers={"Content-Type": "application/json", **headers}
    )


def _no_request(*a: Any, **k: Any) -> Any:
    raise AssertionError("no request expected")


# --------------------------------------------------------------------------- .rbox_info


def test_rbox_info_from_the_api(online: None) -> None:
    url = "https://researchbox.org/801"
    with replay("apis"):
        info = _rbox_info(url)
        assert info["rb_url"].tolist() == [url]
        assert "Are Empathic People Better Adjusted" in info["RB_target"].iloc[0]
        # the reference comes with HTML markup, which is removed
        assert "<" not in info["RB_target"].iloc[0]
        assert info["RB_authors"].tolist() == ["Joyce He, Stéphane Côté"]
        assert "Are individuals adept at perceiving other" in info["RB_abstract"].iloc[0]
        assert info["RB_title"].tolist() == ["Empathic Accuracy and Self Views on Adju"]
        assert info["RB_status"].tolist() == ["public"]
        assert info["RB_paper_doi"].tolist() == ["10.1177/09567976231185127"]
        assert info["RB_zenodo_doi"].tolist() == ["10.5281/zenodo.15038649"]
        assert info["RB_license"].tolist() == ["CC-BY-4.0"]
        assert info["RB_license_url"].tolist() == ["https://creativecommons.org/licenses/by/4.0/"]
        assert info["RB_public"].tolist() == ["2023-06-09"]
        assert info["RB_last_modified"].tolist() == ["2023-05-29T23:36:09Z"]

        files = info["files"].iloc[0]
        assert len(files) == 10
        assert {"name", "file_id", "section", "type", "size", "md5"} <= set(files.columns)
        assert all(len(m) == 32 for m in files["md5"])

        # both routes give the same columns
        with local_options({"metacheck.researchbox.token": ""}):
            page = _rbox_info(url)
        assert set(info.columns) == set(page.columns)


def test_rbox_info_reads_the_web_page_when_the_api_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def api(rb_url: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "rb_url": [rb_url],
                "error": ["API rate limit exhausted: list_files same-box hourly limit"],
            }
        )

    def page(rb_url: Any) -> pd.DataFrame:
        return pd.DataFrame({"rb_url": [rb_url], "RB_title": [None]})

    monkeypatch.setattr(rb, "_rbox_info_api", api)
    monkeypatch.setattr(rb, "_rbox_info_page", page)
    assert "error" not in _rbox_info("https://researchbox.org/801").columns

    # both failed: the API's reason first
    monkeypatch.setattr(
        rb, "_rbox_info_page", lambda u: pd.DataFrame({"rb_url": [u], "error": ["HTTP 403"]})
    )
    info = _rbox_info("https://researchbox.org/801")
    assert info["error"].tolist() == [
        "API rate limit exhausted: list_files same-box hourly limit; web page: HTTP 403"
    ]

    # a box the API cannot find may be one that is not public: the web page tells which,
    # and its answer is the one reported
    monkeypatch.setattr(
        rb, "_rbox_info_api", lambda u: pd.DataFrame({"rb_url": [u], "error": ["unfound"]})
    )
    monkeypatch.setattr(
        rb, "_rbox_info_page", lambda u: pd.DataFrame({"rb_url": [u], "error": ["not public: x"]})
    )
    assert _rbox_info("https://researchbox.org/801")["error"].tolist() == ["not public: x"]


def test_rbox_info_cache(online: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with local_options({"metacheck.cache.dir": str(tmp_path / "cache")}):
        with replay("apis"):
            first = rbox_info("https://researchbox.org/801", cache=True)
        # the second call asks ResearchBox nothing, for any link to the same box
        monkeypatch.setattr(rb, "_rbox_info", _no_request)
        again = rbox_info("researchbox.org/801", cache=True)
    assert again["rb_url"].tolist() == ["researchbox.org/801"]
    assert again["RB_title"].tolist() == first["RB_title"].tolist()


# --------------------------------------------------------------------------- rbox_files


def test_rbox_files(online: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = "https://researchbox.org/801"
    with local_options({"metacheck.cache.dir": str(tmp_path / "cache")}):
        with replay("apis"):
            files = rbox_files(url)
            assert len(files) == 10
            assert list(files.columns) == [
                "rb_url",
                "name",
                "file_id",
                "section",
                "type",
                "description",
                "extension",
                "size",
                "md5",
            ]
            assert files["rb_url"].unique().tolist() == [url]
            # a file list cached by rbox_files() is reused
            rbox_files(url, cache=True)

        monkeypatch.setattr(rb, "_rbox_api", _no_request)
        pd.testing.assert_frame_equal(rbox_files(url, cache=True), files)

        # a box that could not be listed gets a row with the reason
        monkeypatch.setattr(
            rb,
            "_rbox_api",
            lambda *a, **k: {
                "ok": False,
                "error": "API rate limit exhausted: list_files same-box hourly limit",
                "body": None,
            },
        )
        with pytest.warns(UserWarning, match="did not list"):
            failed = rbox_files("https://researchbox.org/4377")
        assert failed["error"].tolist() == [
            "API rate limit exhausted: list_files same-box hourly limit"
        ]


def test_rbox_files_and_search_need_a_key(no_key: None) -> None:
    with pytest.raises(ValueError, match="needs a ResearchBox API key"):
        rbox_files("https://researchbox.org/801")
    with pytest.raises(ValueError, match="needs a ResearchBox API key"):
        rbox_search("happi*")


def test_a_peer_review_link_sends_its_passcode_to_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}
    files = [
        {
            "basename": "a.csv",
            "file_id": 1,
            "section": "Study 1",
            "type": "Data",
            "description": "",
            "extension": "csv",
            "size": 1,
            "md5": "0" * 32,
        }
    ]

    def api(action: str, *, code: str | None = None, path: str | None = None, **kw: Any) -> Any:
        sent[action] = code
        return {"ok": True, "error": None, "body": {"files": files}}

    monkeypatch.setattr(rb, "_rbox_api", api)
    url = "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS"
    rbox_files(url)
    assert sent["list_files"] == "MJUAAS"
    _rbox_info(url)
    assert sent["box_meta"] == "MJUAAS"

    # a link without one sends none
    rbox_files("https://researchbox.org/801")
    assert sent["list_files"] is None


# --------------------------------------------------------------------------- rbox_search


def test_rbox_search(online: None) -> None:
    with replay("apis"):
        hits = rbox_search("happi*", limit=3)
    assert len(hits) == 3
    assert hits.attrs["total"] == 123
    assert hits["rb_url"].iloc[0] == "https://researchbox.org/9614"
    assert {"box_id", "file_id", "var_name", "var_description"} <= set(hits.columns)
    assert str(hits["upload_time"].dtype).startswith("datetime64")

    with pytest.raises(ValueError, match="single string"):
        rbox_search(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least 1"):
        rbox_search("happi*", limit=0)
    with pytest.raises(ValueError):
        rbox_search("happi*", type="video")


def test_rbox_search_pages_through_the_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[dict[str, Any]] = []

    def api(action: str, **kw: Any) -> Any:
        asked.append(kw)
        n = max(0, min(kw["limit"], 250 - kw["offset"]))
        hits = [{"box_id": i + kw["offset"] + 1} for i in range(n)]
        return {"ok": True, "error": None, "body": {"total": 250, "hits": hits}}

    monkeypatch.setattr(rb, "_rbox_api", api)
    hits = rbox_search("x", limit=float("inf"))
    assert len(hits) == 250
    assert [a["offset"] for a in asked] == [0, 100, 200]
    assert hits["box_id"].tolist() == list(range(1, 251))

    asked.clear()
    hits = rbox_search("x", limit=30, offset=10)
    assert len(hits) == 30 and len(asked) == 1
    assert hits["box_id"].iloc[0] == 11

    # no hits
    monkeypatch.setattr(
        rb,
        "_rbox_api",
        lambda *a, **k: {"ok": True, "error": None, "body": {"total": 0, "hits": []}},
    )
    hits = rbox_search("x")
    assert len(hits) == 0 and hits.attrs["total"] == 0

    # a failed first request stops; a later one keeps the hits so far
    monkeypatch.setattr(
        rb,
        "_rbox_api",
        lambda *a, **k: {"ok": False, "error": "HTTP 400: query too short", "body": None},
    )
    with pytest.raises(RuntimeError, match="query too short"):
        rbox_search("x")
    calls: list[int] = []

    def flaky(*a: Any, **k: Any) -> Any:
        calls.append(1)
        if len(calls) > 1:
            return {"ok": False, "error": "API rate limit exhausted: x", "body": None}
        return {
            "ok": True,
            "error": None,
            "body": {"total": 250, "hits": [{"box_id": i} for i in range(1, 101)]},
        }

    monkeypatch.setattr(rb, "_rbox_api", flaky)
    with pytest.warns(UserWarning, match="offset = 100"):
        hits = rbox_search("x", limit=float("inf"))
    assert len(hits) == 100


# --------------------------------------------------------------------------- _rbox_api


def test_rbox_api_without_a_key_says_so(no_key: None) -> None:
    res = _rbox_api("box_meta", box_id=1)
    assert res["ok"] is False
    assert "no ResearchBox API key" in res["error"]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            _json(429, '{"ok":false,"error":"list_files same-box hourly limit"}'),
            "API rate limit exhausted: list_files same-box hourly limit",
        ),
        # a wait longer than a minute is not sat out, but named
        (
            _json(
                429,
                '{"ok":false,"error":"list_files same-box hourly limit","retry_after_seconds":1800}',
                **{"Retry-After": "1800"},
            ),
            "API rate limit exhausted: list_files same-box hourly limit "
            "(ResearchBox asks to wait 1800 seconds)",
        ),
        (httpx.Response(500, content=b""), "unfound"),
        (_json(400, '{"ok":false,"error":"invalid box_id"}'), "HTTP 400: invalid box_id"),
    ],
)
def test_rbox_api_turns_refusals_into_messages(response: httpx.Response, error: str) -> None:
    with respx.mock() as router:
        router.post(API).mock(return_value=response)
        res = _rbox_api("box_meta", box_id=1)
    assert res["ok"] is False
    assert res["error"] == error


def test_how_long_researchbox_asks_to_wait() -> None:
    def retry(resp: httpx.Response) -> float | None:
        resp.read()
        return _rbox_retry_after(resp)

    too_soon = '{"ok":false,"error":"too soon; retry later","retry_after_seconds":4}'
    assert retry(_json(429, too_soon)) == 4
    assert retry(_json(429, '{"ok":false}', **{"Retry-After": "30"})) == 30
    assert retry(_json(429, '{"ok":false,"error":"list_files same-box hourly limit"}')) is None
    assert retry(_json(200, '{"ok":true}')) is None


def test_boxes_without_a_passcode_are_not_sent_after_8_answers_of_unfound() -> None:
    # ResearchBox blocks the key for 10 days after 10 requests for boxes that are not
    # public, and answers those as if the box did not exist
    with respx.mock() as router:
        route = router.post(API).mock(return_value=httpx.Response(500, content=b""))
        for i in range(1, 9):
            assert _rbox_api("box_meta", box_id=i)["error"] == "unfound"
        assert len(_rbox_unfound_times()) == 8
        assert route.call_count == 8
        res = _rbox_api("list_files", box_id=9)
        assert res["error"].startswith("not sent: ")
        assert route.call_count == 8  # not sent

        # a request with a passcode is still sent, and its answer is not counted
        assert _rbox_api("box_meta", box_id=9, code="ABCDEF")["error"] == "unfound"
        assert len(_rbox_unfound_times()) == 8

        # answers older than 10 days no longer count
        old = time.time() - 11 * 86400
        with open(rb._unfound_path(), "w", encoding="utf-8") as fh:
            json.dump([old] * 8, fh)
        assert _rbox_api("box_meta", box_id=9)["error"] == "unfound"
        assert len(_rbox_unfound_times()) == 1


def test_the_key_is_sent_but_never_written_anywhere(tmp_path: Path) -> None:
    """The key is in the request header only: not in an error, a warning, the state file
    or any cache file (nor in the name of one)."""
    seen: list[httpx.Request] = []
    replies = [
        _json(401, '{"ok":false,"error":"token invalid"}'),
        _json(429, '{"ok":false,"error":"daily limit","retry_after_seconds":86400}'),
        httpx.Response(500, content=b""),
        _json(400, '{"ok":false,"error":"invalid box_id"}'),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies[min(len(seen), len(replies)) - 1]

    texts: list[str] = []
    with (
        local_options({"metacheck.researchbox.token": SECRET}),
        respx.mock() as router,
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        router.post(API).mock(side_effect=handler)
        router.get(url__startswith="https://researchbox.org/").mock(
            return_value=httpx.Response(404)
        )
        for i in range(1, 5):
            texts.append(str(_rbox_api("box_meta", box_id=i)["error"]))
        info = _rbox_info("https://researchbox.org/6")
        texts.append(" ".join(str(v) for v in info.iloc[0].tolist()))
        texts.extend(str(w.message) for w in caught)

    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in seen[0].content.decode()  # the header only, not the body
    assert not any(SECRET in t for t in texts), texts
    for root, dirs, names in os.walk(tmp_path):
        for n in [*dirs, *names]:
            assert SECRET not in n
        for n in names:
            assert SECRET.encode() not in (Path(root) / n).read_bytes(), n
    # (a key the visitor set is never recorded as a problem, so the walk above has seen
    # whatever the state folder holds)


# --------------------------------------------------------------------------- _rbox_license


def test_rbox_license(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    asked: list[str] = []

    def api(action: str, **kw: Any) -> Any:
        asked.append(action)
        return {"ok": True, "error": None, "body": {"license_id": "CC-BY-4.0"}}

    with local_options({"metacheck.cache.dir": str(tmp_path / "cache")}):
        monkeypatch.setattr(rb, "_rbox_api", api)
        assert _rbox_license("https://researchbox.org/801", cache=True) == "CC-BY-4.0"
        # cached for any link to the box
        assert _rbox_license("researchbox.org/801", cache=True) == "CC-BY-4.0"
        assert len(asked) == 1

    # the web page when the API fails
    monkeypatch.setattr(
        rb, "_rbox_api", lambda *a, **k: {"ok": False, "error": "HTTP 503", "body": None}
    )
    monkeypatch.setattr(
        rb, "_rbox_info_page", lambda u: pd.DataFrame({"rb_url": [u], "RB_license": ["CC-BY-4.0"]})
    )
    assert _rbox_license("https://researchbox.org/802") == "CC-BY-4.0"
    monkeypatch.setattr(
        rb, "_rbox_info_page", lambda u: pd.DataFrame({"rb_url": [u], "error": ["unfound"]})
    )
    assert _rbox_license("https://researchbox.org/803") is None


def test_rbox_box_meta_gives_the_licence_and_status_from_one_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """metacheck 0.4.3.23 (#621): the status comes with the licence."""
    asked: list[Any] = []

    def api(action: str, **kw: Any) -> Any:
        asked.append(kw.get("code"))
        return {
            "ok": True,
            "error": None,
            "body": {"license_id": "CC-BY-4.0", "status": "private"},
        }

    with local_options(
        {
            "metacheck.cache.dir": str(tmp_path / "cache"),
            "metacheck.repo_cache.session_dir": str(tmp_path / "s2"),
            "metacheck.state.dir": str(tmp_path / "st"),
        }
    ):
        monkeypatch.setattr(rb, "_rbox_api", api)
        meta = _rbox_box_meta(
            "https://researchbox.org/8285&PEER_REVIEW_passcode=NNONJR", cache=True
        )
        assert meta == {"RB_license": "CC-BY-4.0", "RB_status": "private"}
        assert asked == ["NNONJR"]
        # the status is saved with the licence
        assert _rbox_box_meta("https://researchbox.org/8285", cache=True)["RB_status"] == "private"
        assert len(asked) == 1

    # the web page gives no status
    monkeypatch.setattr(
        rb, "_rbox_api", lambda *a, **k: {"ok": False, "error": "HTTP 503", "body": None}
    )
    monkeypatch.setattr(
        rb, "_rbox_info_page", lambda u: pd.DataFrame({"rb_url": [u], "RB_license": ["CC-BY-4.0"]})
    )
    meta = _rbox_box_meta("https://researchbox.org/802")
    assert meta["RB_license"] == "CC-BY-4.0"
    assert meta["RB_status"] is None


# --------------------------------------------------------------------------- downloads

_BOX_FILES: dict[str, bytes] = {
    "Data/study1.csv": b"id,x\n1,2\n",
    "Codebook/study1.csv___CODEBOOK.csv": b"var,desc\nx,a value\n",
    "Code/analysis.R": b"x <- 1\n",
}
_TYPES = {
    "Data/study1.csv": "Data",
    "Codebook/study1.csv___CODEBOOK.csv": "Codebook",
    "Code/analysis.R": "Code",
}


def _box_listing() -> list[dict[str, Any]]:
    return [
        {
            "section": "Study 1",
            "type": _TYPES[p],
            "description": "",
            "basename": p.split("/")[-1],
            "extension": p.rsplit(".", 1)[-1],
            "size": len(d),
            "file_id": i,
            "md5": hashlib.md5(d, usedforsecurity=False).hexdigest(),
        }
        for i, (p, d) in enumerate(_BOX_FILES.items(), start=1)
    ]


def _box_zip(toc: bool = True, bad_md5: str | None = None, only: list[str] | None = None) -> bytes:
    """A box zipped as ResearchBox's API does: folders by type, with a table of contents
    unless *toc* is false; *bad_md5* names a file whose checksum in it is wrong."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p, d in _BOX_FILES.items():
            if only is None or p in only:
                zf.writestr(p, d)
        if toc:
            listing = _box_listing()
            for row in listing:
                row["file_name"] = row.pop("basename")
                if row["file_name"] == bad_md5:
                    row["md5"] = "0" * 32
            zf.writestr(
                "Table of Contents Box #99999.json",
                json.dumps({"box_id": 99999, "files": listing}),
            )
            zf.writestr("Table of Contents Box #99999.csv", "type,section\n")
    return buf.getvalue()


class _FakeApi:
    """A stand-in for ``_rbox_api()`` that records what was asked and writes *zip_bytes*
    for ``download_files``."""

    def __init__(self, zip_bytes: bytes, list_ok: bool = True) -> None:
        self.zip_bytes = zip_bytes
        self.list_ok = list_ok
        self.asked: list[str] = []
        self.sent: dict[str, Any] = {}
        self.codes: list[tuple[str, str | None]] = []

    def __call__(
        self, action: str, *, code: str | None = None, path: str | None = None, **kw: Any
    ) -> dict[str, Any]:
        self.asked.append(action)
        self.codes.append((action, code))
        if action == "download_files":
            self.sent = kw
            assert path is not None
            Path(path).write_bytes(self.zip_bytes)
            return {"ok": True, "error": None, "body": None}
        if action == "list_files" and not self.list_ok:
            return {"ok": False, "error": "API rate limit exhausted: x", "body": None}
        return {"ok": True, "error": None, "body": {"files": _box_listing()}}


def test_rbox_file_download_checks_the_files_against_the_table_of_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi(_box_zip())
    monkeypatch.setattr(rb, "_rbox_api", api)
    url = "https://researchbox.org/99999"
    files = rbox_file_download(url)
    # one request: the checksums come with the download
    assert api.asked == ["download_files"]
    # the table of contents is not one of the box's files, and is kept aside
    assert sorted(files["name"]) == sorted(_BOX_FILES)
    toc = Path(rb._cache_subdir(url, cache=False)) / "Table of Contents Box #99999.json"
    assert toc.exists()
    assert files["checksum_ok"].all()
    assert len(files.attrs["failed"]) == 0

    # a box already downloaded is reused without a request, checks included
    api.asked.clear()
    again = rbox_file_download(url)
    assert api.asked == []
    assert again["checksum_ok"].all()

    # a file that does not match its checksum is listed as failed, and the box is
    # downloaded again on the next call
    api.zip_bytes = _box_zip(bad_md5="analysis.R")
    url2 = "https://researchbox.org/99998"
    with pytest.warns(UserWarning, match="did not arrive intact"):
        files = rbox_file_download(url2)
    assert files.loc[files["name"] == "Code/analysis.R", "checksum_ok"].tolist() == [False]
    failed = files.attrs["failed"]
    assert failed["name"].tolist() == ["analysis.R"]
    assert failed["error"].tolist() == ["checksum does not match"]
    api.asked.clear()
    with pytest.warns(UserWarning):
        rbox_file_download(url2)
    assert api.asked == ["download_files"]


def test_rbox_file_download_without_a_table_of_contents_asks_for_the_file_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi(_box_zip(toc=False))
    monkeypatch.setattr(rb, "_rbox_api", api)
    files = rbox_file_download("https://researchbox.org/99999")
    assert api.asked == ["download_files", "list_files"]
    assert files["checksum_ok"].all()

    # a refused file list: the files arrive, unchecked
    api.list_ok = False
    files = rbox_file_download("https://researchbox.org/99998")
    assert len(files) == 3
    assert files["checksum_ok"].isna().all()
    assert len(files.attrs["failed"]) == 0


def test_rbox_file_download_of_chosen_files(monkeypatch: pytest.MonkeyPatch) -> None:
    # the data file comes with its codebook
    api = _FakeApi(
        _box_zip(toc=False, only=["Data/study1.csv", "Codebook/study1.csv___CODEBOOK.csv"])
    )
    monkeypatch.setattr(rb, "_rbox_api", api)
    files = rbox_file_download("https://researchbox.org/99999", file_ids=1, subfolders="section")
    assert str(api.sent["file_ids"]) == "1"
    assert api.sent["subfolders"] == "section"
    assert len(files) == 2
    # the codebook was not asked for, but is checked as well
    assert files["checksum_ok"].all()
    assert len(files.attrs["failed"]) == 0

    # a file asked for that the box does not have did not arrive
    with pytest.warns(UserWarning):
        files = rbox_file_download("https://researchbox.org/99998", file_ids=[1, 7])
    failed = files.attrs["failed"]
    assert failed["name"].tolist() == ["file_id 7"]
    assert failed["error"].tolist() == ["not in the download"]

    # chosen files need the API
    with local_options({"metacheck.researchbox.token": ""}):
        files = rbox_file_download("https://researchbox.org/99999", file_ids=1)
    assert len(files) == 0
    assert "needs the ResearchBox API" in files.attrs["failed"]["error"].iloc[0]


def test_several_links_to_one_box_download_it_once_with_the_passcode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi(_box_zip())
    monkeypatch.setattr(rb, "_rbox_api", api)
    plain = "https://researchbox.org/99999"
    review = "https://researchbox.org/99999&PEER_REVIEW_passcode=ABCDEF"
    files = rbox_file_download([plain, review])
    assert len(api.codes) == 1
    assert api.codes[0][1] == "ABCDEF"
    # every link gets the box's files
    assert len(files) == 6
    assert set(files["rb_url"]) == {plain, review}

    # a later call through any link to the box reuses the download
    rbox_file_download(review)
    rbox_file_download("researchbox.org/99999")
    assert len(api.codes) == 1


def test_a_failed_download_names_the_reason_but_not_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rb, "_rbox_api", lambda *a, **k: {"ok": False, "error": "HTTP 500", "body": None}
    )
    with respx.mock() as router, pytest.warns(UserWarning) as caught:
        router.get(url__startswith="https://researchbox.org/").mock(
            return_value=httpx.Response(404)
        )
        rbox_file_download("https://researchbox.org/99999")
    assert caught and all(KEY not in str(w.message) for w in caught)
