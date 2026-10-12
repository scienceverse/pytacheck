"""Port of metacheck's tests/testthat/test-archive-zenodo.R."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

import metacheck as pc
from metacheck.archives import zenodo
from metacheck.archives.zenodo import (
    _zenodo_id,
    _zenodo_info,
    _zenodo_unread,
    _zenodo_verify_downloads,
    zenodo_file_download,
    zenodo_info,
    zenodo_links,
)
from metacheck.utils import _col_chr

FULL = [
    "zenodo_id",
    "title",
    "doi",
    "description",
    "publication_date",
    "updated_date",
    "creators",
    "keywords",
    "resource_type",
    "journal",
    "owners",
    "license",
    "downloads",
    "unique_downloads",
    "views",
    "files",
    "error",
]


def test_zenodo_links() -> None:
    with pytest.raises(TypeError):
        zenodo_links(1)

    paper = pc.test_paper(
        url=[
            "https://zenodo.org/records/12345",
            "https://doi.org/10.5281/zenodo.98765",
            "https://osf.io/abcde",
        ]
    )
    links = zenodo_links(paper)
    assert len(links) == 2
    assert links["href"].tolist() == [
        "https://zenodo.org/records/12345",
        "https://doi.org/10.5281/zenodo.98765",
    ]
    assert links["zenodo_id"].tolist() == ["12345", "98765"]
    assert links["zenodo_link"].tolist() == [
        "https://doi.org/10.5281/zenodo.12345",
        "https://doi.org/10.5281/zenodo.98765",
    ]


def test_zenodo_links_bare_text_and_duplicates() -> None:
    paper = pc.test_paper(
        ["Data: 10.5281/zenodo.12345 and https://zenodo.org/record/777/."],
        url=["https://zenodo.org/records/12345/"],
    )
    paper["text"] = pd.concat([paper["text"], paper["text"]], ignore_index=True)
    links = zenodo_links(paper)
    # trailing slashes are dropped and the repeated sentence's links are not doubled
    assert links["href"].tolist() == [
        "https://zenodo.org/records/12345",
        "10.5281/zenodo.12345",
        "https://zenodo.org/record/777",
    ]
    assert links["zenodo_id"].tolist() == ["12345", "12345", "777"]


def test_zenodo_links_none() -> None:
    links = zenodo_links(pc.test_paper(["nothing"]))
    assert len(links) == 0
    assert list(links.columns)[-3:] == ["zenodo_url", "zenodo_id", "zenodo_link"]


def test_zenodo_id() -> None:
    zenodo_url = [
        "12345",
        "https://zenodo.org/records/12345",
        "https://zenodo.org/record/12345",
        "https://doi.org/10.5281/zenodo.12345",
        "zenodo.12345",
        "https://zenodo.org/records/12345 zenodo.98765",  # malformed
        "not-a-zenodo-id",
        "",
    ]
    assert _zenodo_id(zenodo_url) == ["12345"] * 6 + [None, None]
    # NULL
    assert _zenodo_id(None) == []
    assert _zenodo_id("zenodo.org/uploads/7") == "7"
    assert _zenodo_id(12345) == "12345"
    assert _zenodo_id(pd.NA) is None


def test_zenodo_info_one(apis: object) -> None:
    zenodo_id = _zenodo_id("10.5281/zenodo.2669586")
    info = _zenodo_info(zenodo_id)
    assert info["zenodo_id"].tolist() == ["2669586"]
    assert info["title"].tolist() == ["faux: Simulation for Factorial Designs"]
    assert info["doi"].tolist() == ["10.5281/zenodo.7852893"]
    assert info["resource_type"].tolist() == ["software"]
    assert info["license"].tolist() == ["mit-license"]
    assert info["downloads"].iloc[0] > 200
    assert info["files"].iloc[0][0]["key"] == "debruine/faux-v1.2.1.zip"

    with pytest.warns(UserWarning, match="could not be found"):
        unfound = _zenodo_info("00000000")
    assert unfound["error"].tolist() == ["unfound"]


def _batch_query_returning(resp: httpx.Response) -> Any:
    def fake(urls: list[str], **_: Any) -> list[httpx.Response]:
        return [resp for _ in urls]

    return fake


def test_zenodo_info_keeps_schema_when_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    # non-200: Zenodo had nothing for us
    monkeypatch.setattr("metacheck.http.batch_query", _batch_query_returning(httpx.Response(404)))
    with pytest.warns(UserWarning):
        unread = _zenodo_info("5498371")
    assert len(unread) == 1
    assert set(unread.columns) == set(FULL)
    assert unread["error"].tolist() == ["unfound"]

    # 200 with a body that will not parse
    monkeypatch.setattr(
        "metacheck.http.batch_query",
        _batch_query_returning(httpx.Response(200, content=b"not json")),
    )
    bad = _zenodo_info("5498371")
    assert set(bad.columns) == set(FULL)
    assert bad["error"].tolist() == ["parse_error"]


def test_zenodo_info_keeps_doi_license_when_every_record_unreadable() -> None:
    from metacheck._r import bind_rows
    from metacheck.utils import left_join

    unread = bind_rows([_zenodo_unread("5498371", "unfound"), _zenodo_unread("5498372", "unfound")])
    table = pd.DataFrame(
        {
            "zenodo_url": [
                "https://doi.org/10.5281/zenodo.5498371",
                "https://doi.org/10.5281/zenodo.5498372",
            ]
        }
    )
    ids = pd.DataFrame({"zenodo_url": table["zenodo_url"], "zenodo_id": ["5498371", "5498372"]})
    zi = left_join(
        left_join(table, ids, by="zenodo_url"), unread, by="zenodo_id", suffix=("", ".zenodo")
    )
    assert len(zi) == 2
    assert len(_col_chr(zi, "doi")) == len(zi)
    assert len(_col_chr(zi, "license")) == len(zi)


def test_zenodo_info(apis: object) -> None:
    z = [
        "https://doi.org/10.5281/zenodo.17754445",
        "https://zenodo.org/records/123456789",
        "https://doi.org/10.5281/zenodo.17754445",
        None,
    ]
    info = zenodo_info(z)
    assert set(info["zenodo_id"]) == {"17754445", "123456789"}
    assert "Example title" in info["title"].tolist()

    tbl = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "href": [
                "https://doi.org/10.5281/zenodo.17754445",
                "https://zenodo.org/records/123456789",
                "not-a-zenodo-id",
            ],
        }
    )
    info2 = zenodo_info(tbl, "href")
    assert len(info2) == 3
    assert info2["href"].tolist() == tbl["href"].tolist()
    assert info2["zenodo_id"].tolist()[:2] == ["17754445", "123456789"]
    assert pd.isna(info2["zenodo_id"].iloc[2])
    assert pd.isna(info2["title"].iloc[2])
    # the input table is not modified
    assert list(tbl.columns) == ["id", "href"]


def test_zenodo_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="offline"):
        zenodo_info("12345")


def test_zenodo_info_on_zenodo_links_output_with_unfound_record(apis: object) -> None:
    paper = pc.test_paper(url=["https://doi.org/10.5281/zenodo.00000000"])
    links = zenodo_links(paper)
    with pytest.warns(UserWarning):
        info = zenodo_info(links)
    assert "zenodo_id" in info.columns
    assert "zenodo_id.x" not in info.columns
    assert info["error"].tolist() == ["unfound"]


def test_zenodo_info_na_url_in_table(apis: object) -> None:
    # U33: metacheck fails ("row names contain missing values")
    tbl = pd.DataFrame({"url": ["10.5281/zenodo.2669586", None]})
    out = zenodo_info(tbl)
    assert out["zenodo_id"].tolist()[0] == "2669586"
    assert pd.isna(out["zenodo_id"].iloc[1])


def test_zenodo_info_cache(apis: object, tmp_path: Path) -> None:
    from metacheck import utils

    with utils.local_options({"metacheck.repo_info_cache.dir": str(tmp_path)}):
        first = zenodo_info("2669586", cache=True)
        assert list(tmp_path.iterdir())
        # a cached listing is reused without a request
        with respx.mock(assert_all_called=False) as router:
            again = zenodo_info("2669586", cache=True)
            assert router.calls.call_count == 0
    assert again["title"].tolist() == first["title"].tolist()


# ---------------------------------------------------------------------------
# zenodo_file_download()
# ---------------------------------------------------------------------------


def _mock_info(files: list[dict[str, Any]]) -> Any:
    def fake(zenodo_url: Any, id_col: Any = 1, pb: Any = None, cache: bool = False) -> pd.DataFrame:
        zid = _zenodo_id(zenodo_url)
        entries = [
            {**f, "id": f["id"].format(zid=zid), "links": dict(f.get("links", {}))} for f in files
        ]
        return pd.DataFrame(
            {"zenodo_url": [str(zenodo_url)], "zenodo_id": [zid], "files": [entries]}
        )

    return fake


def test_zenodo_file_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert zenodo_file_download(None) is None

    monkeypatch.setattr(
        zenodo,
        "zenodo_info",
        _mock_info(
            [
                {"id": "small_{zid}", "key": "small.csv", "size": 100, "checksum": "md5:small",
                 "links": {"self": None}},
                {"id": "big_{zid}", "key": "big.bin", "size": 12 * 1024 * 1024,
                 "checksum": "md5:big", "links": {"self": None}},
            ]
        ),
    )  # fmt: skip

    # small.csv has no URL, so it cannot arrive: the warning reports that
    with pytest.warns(UserWarning, match="did not arrive intact"):
        dl = zenodo_file_download("12345", download_to=str(tmp_path), max_file_size=10)
    # the omitted (too large) big.bin stays in the table, not downloaded (U36)
    assert len(dl) == 2
    assert dl["zenodo_id"].tolist() == ["12345", "12345"]
    assert dl["key"].tolist() == ["small.csv", "big.bin"]
    assert dl["downloaded"].tolist() == [False, False]

    folder = tmp_path / "12345"
    assert folder.is_dir()
    assert list(folder.iterdir()) == []

    # vectorised: each record warns separately
    with pytest.warns(UserWarning, match="did not arrive intact") as record:
        dl2 = zenodo_file_download(["12345", "67890"], download_to=str(tmp_path), max_file_size=10)
    assert all("did not arrive intact" in str(w.message) for w in record)
    assert set(dl2["zenodo_id"]) == {"12345", "67890"}
    assert not dl2["downloaded"].any()
    # the second download of 12345 went into a new folder
    assert (tmp_path / "12345_1").is_dir()

    # every file omitted: listed, none downloaded, no folder made (U36)
    dl3 = zenodo_file_download("13579", download_to=str(tmp_path), max_file_size=1e-6)
    assert dl3["key"].tolist() == ["small.csv", "big.bin"]
    assert not dl3["downloaded"].any()
    assert dl3["folder"].isna().all()
    assert not (tmp_path / "13579").exists()


def test_zenodo_file_download_folder_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    # an existing record folder gives "<id>_1", then "<id>_2"
    monkeypatch.setattr(
        zenodo,
        "zenodo_info",
        _mock_info([{"id": "a_{zid}", "key": "a.csv", "size": 1, "links": {"self": None}}]),
    )
    # no file is omitted, so the whole-record archive is tried first: there is none
    respx_mock.get("https://zenodo.org/api/records/12345/files-archive").mock(
        return_value=httpx.Response(404)
    )
    for expected in ("12345", "12345_1", "12345_2"):
        with pytest.warns(UserWarning):
            dl = zenodo_file_download("12345", download_to=str(tmp_path))
        assert dl["folder"].tolist() == [expected]


def test_zenodo_file_download_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    body = b"x,y\n1,2\n"
    monkeypatch.setattr(
        zenodo,
        "zenodo_info",
        _mock_info(
            [
                {"id": "ok_{zid}", "key": "ok.csv", "size": len(body), "checksum": "md5:ok",
                 "links": {"self": "https://files.example/24680/ok.csv"}},
            ]
        ),
    )  # fmt: skip
    respx_mock.get("https://zenodo.org/api/records/24680/files-archive").mock(
        return_value=httpx.Response(404)
    )
    respx_mock.get("https://files.example/24680/ok.csv").mock(
        return_value=httpx.Response(200, content=body)
    )
    dl = zenodo_file_download("24680", download_to=str(tmp_path), max_file_size=10)
    assert len(dl) == 1
    assert dl["downloaded"].tolist() == [True]
    assert (tmp_path / "24680" / "ok.csv").read_bytes() == body
    assert dl["path"].tolist() == ["ok.csv"]
    assert dl["size_on_disk"].tolist() == [8.0]
    assert pd.isna(dl["checksum_ok"].iloc[0])  # "md5:ok" is not a real md5


def test_zenodo_file_download_bulk_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    import io
    import zipfile

    a, b = b"alpha\n", b"beta\n"
    monkeypatch.setattr(
        zenodo,
        "zenodo_info",
        _mock_info(
            [
                {"id": "a_{zid}", "key": "a.txt", "size": len(a),
                 "checksum": "md5:" + hashlib.md5(a).hexdigest(),
                 "links": {"self": "https://files.example/a.txt"}},
                {"id": "b_{zid}", "key": "sub/b.txt", "size": len(b),
                 "checksum": "md5:" + hashlib.md5(b).hexdigest(),
                 "links": {"self": "https://files.example/b.txt"}},
            ]
        ),
    )  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", a)
        zf.writestr("sub/b.txt", b)
    archive = respx_mock.get("https://zenodo.org/api/records/111/files-archive").mock(
        return_value=httpx.Response(200, content=buf.getvalue())
    )
    single = respx_mock.get(url__startswith="https://files.example/").mock(
        return_value=httpx.Response(500)
    )
    dl = zenodo_file_download("111", download_to=str(tmp_path))
    assert archive.called
    assert not single.called  # everything came out of the archive
    assert dl["downloaded"].tolist() == [True, True]
    assert dl["checksum_ok"].tolist() == [True, True]
    assert (tmp_path / "111" / "sub" / "b.txt").read_bytes() == b


def _zip_record(
    zenodo_url: Any, id_col: Any = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    zid = _zenodo_id(zenodo_url)
    files = [
        {"id": f"z_{zid}", "key": "bundle.zip", "size": 130 * 1024 * 1024, "checksum": "md5:zzz",
         "links": {"self": "https://files.example/bundle.zip"}},
    ]  # fmt: skip
    return pd.DataFrame({"zenodo_url": [str(zenodo_url)], "zenodo_id": [zid], "files": [files]})


def test_zenodo_file_download_extracts_wanted_members(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requested: list[str] = []

    def members(url: str, dest: str, keep_types: Any, max_file_size: Any) -> pd.DataFrame:
        requested.append(url)
        assert keep_types == "data"
        Path(dest, "study.csv").write_text("id,x\n1,2\n")
        return pd.DataFrame(
            {"name": ["study.csv"], "path": [os.path.join(dest, "study.csv")], "size": [9],
             "ok": [True]}
        )  # fmt: skip

    monkeypatch.setattr(zenodo, "zenodo_info", _zip_record)
    monkeypatch.setattr(zenodo, "_zenodo_zip_members", members)

    dl = zenodo_file_download(
        "13579", download_to=str(tmp_path), unzip_types="data", max_file_size=10
    )
    assert len(dl) == 1
    assert dl["key"].tolist() == ["bundle.zip"]
    # the 130 MB zip survived the 10 MB cap: only its members are transferred
    assert dl["downloaded"].tolist() == [True]
    assert dl["extracted"].tolist() == [1]
    assert requested == ["https://files.example/bundle.zip"]
    assert (tmp_path / "13579" / "study.csv").exists()
    # Zenodo's size and MD5 describe the zip, which was never downloaded
    assert pd.isna(dl["size_on_disk"].iloc[0])


def test_zenodo_file_download_falls_back_when_zip_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    monkeypatch.setattr(zenodo, "zenodo_info", _zip_record)
    monkeypatch.setattr(zenodo, "_zenodo_zip_members", lambda *a, **k: None)
    respx_mock.get("https://files.example/bundle.zip").mock(
        return_value=httpx.Response(200, content=b"PK-not-a-real-zip")
    )
    with pytest.warns(UserWarning, match="did not arrive intact"):
        dl = zenodo_file_download(
            "13579", download_to=str(tmp_path), unzip_types="data", max_file_size=10
        )
    # downloaded whole: an ordinary row again, checked against Zenodo's size
    assert pd.isna(dl["extracted"].iloc[0])
    assert (tmp_path / "13579" / "bundle.zip").exists()
    assert dl["downloaded"].tolist() == [False]


def test_unzip_types_leaves_records_without_zip_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    def info(zenodo_url: Any, id_col: Any = 1, pb: Any = None, cache: bool = False) -> pd.DataFrame:
        zid = _zenodo_id(zenodo_url)
        files = [{"id": f"c_{zid}", "key": "plain.csv", "size": 8, "checksum": None,
                  "links": {"self": "https://files.example/plain.csv"}}]  # fmt: skip
        return pd.DataFrame({"zenodo_url": [str(zenodo_url)], "zenodo_id": [zid], "files": [files]})

    monkeypatch.setattr(zenodo, "zenodo_info", info)
    respx_mock.get("https://zenodo.org/api/records/11111/files-archive").mock(
        return_value=httpx.Response(404)
    )
    respx_mock.get("https://files.example/plain.csv").mock(
        return_value=httpx.Response(200, content=b"x,y\n1,2\n")
    )
    dl = zenodo_file_download("11111", download_to=str(tmp_path), unzip_types="data")
    assert dl["downloaded"].tolist() == [True]
    assert pd.isna(dl["extracted"].iloc[0])
    assert (tmp_path / "11111" / "plain.csv").exists()


def test_zenodo_file_download_recorded(mocks: object, tmp_path: Path) -> None:
    dl = zenodo_file_download("5550001", download_to=str(tmp_path))
    assert dl["downloaded"].tolist() == [True, True]
    assert dl["checksum_ok"].tolist() == [True, True]
    assert dl["folder"].tolist() == ["5550001", "5550001"]
    assert sorted(p.name for p in (tmp_path / "5550001").iterdir()) == ["data.csv", "notes.txt"]

    assert zenodo_file_download("5550003", download_to=str(tmp_path)) is None

    with pytest.warns(UserWarning, match="1 of 2 files"):
        capped = zenodo_file_download(
            "5550004", download_to=str(tmp_path), max_file_size=None, max_download_size=10
        )
    # b.bin, the largest, was omitted: listed, not downloaded (U36)
    assert capped["key"].tolist() == ["a.bin", "b.bin", "c.txt"]
    assert capped["downloaded"].tolist()[1] is False


def test_zenodo_verify_downloads(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"abc\n")
    good = hashlib.md5(b"abc\n").hexdigest()
    files = pd.DataFrame(
        {
            "path": ["a.txt", "a.txt", "missing.txt", None],
            "size": [4.0, 5.0, 1.0, 1.0],
            "checksum": [f"md5:{good}", None, None, None],
            "downloaded": [True, True, True, True],
            "extracted": pd.Series([None, None, None, 3], dtype="Int64"),
        }
    )
    out = _zenodo_verify_downloads(files, str(tmp_path))
    assert out["downloaded"].tolist() == [True, False, False, True]
    assert out["checksum_ok"].tolist()[0] is True
    assert out["size_on_disk"].tolist()[:2] == [4.0, 4.0]
    assert "size_on_disk" not in files.columns  # the input is not modified

    assert _zenodo_verify_downloads(None, str(tmp_path)) is None
    no_path = _zenodo_verify_downloads(pd.DataFrame({"size": [1.0]}), str(tmp_path))
    assert no_path["downloaded"].tolist() == [False]


def test_zenodo_info_internal_with_several_ids(tmp_path: Path) -> None:
    """R .zenodo_info(c(a, b)): one row per ID, every field from the first record."""
    from tests.httpmock import replay

    mocks = Path(__file__).resolve().parent / "mocks_review"
    with replay(mocks):
        out = zenodo._zenodo_info(["5559007", "5559001"])
        assert out["zenodo_id"].tolist() == ["5559007", "5559001"]
        assert out["title"].tolist() == ["T7", "T7"]
        with (
            pytest.warns(UserWarning, match="55590045559001 could not be found"),
            pytest.raises(ValueError, match="differing number of rows: 2, 1"),
        ):
            zenodo._zenodo_info(["5559004", "5559001"])
        with pytest.raises(ValueError, match="replacement has 1 row, data has 0"):
            zenodo._zenodo_info([])


def test_zenodo_downloads_retry_through_the_shared_stack(tmp_path: Path) -> None:
    arc = "https://zenodo.org/api/records/1/files-archive"
    with respx.mock(assert_all_mocked=True) as router:
        route = router.get(arc).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, content=b"zip bytes")]
        )
        assert zenodo._download_archive(arc, str(tmp_path / "a.zip")) is True
        assert route.call_count == 2
        assert (tmp_path / "a.zip").read_bytes() == b"zip bytes"

        gone = router.get("https://files.example/gone").respond(404)
        assert zenodo._download_file("https://files.example/gone", str(tmp_path / "g")) is False
        assert gone.call_count == 1 and not (tmp_path / "g").exists()  # a 404 is final

        flaky = router.get("https://files.example/flaky").mock(
            side_effect=[httpx.Response(502), httpx.Response(200, content=b"x")]
        )
        assert zenodo._download_file("https://files.example/flaky", str(tmp_path / "f")) is True
        assert flaky.call_count == 2 and (tmp_path / "f").read_bytes() == b"x"

        down = router.get("https://files.example/down").mock(side_effect=httpx.ConnectError("x"))
        assert zenodo._download_file("https://files.example/down", str(tmp_path / "d")) is False
        assert down.call_count == 5
        empty = router.get("https://files.example/empty").respond(200, content=b"")
        assert zenodo._download_archive("https://files.example/empty", str(tmp_path / "e")) is False
        assert empty.call_count == 1
