"""Port of metacheck's test-archive-osf_file_download.R and test-archive-osf-all.R.

Downloads replay metacheck's recorded responses (``osf.io/download/*``,
Waterbutler archives). ``download_available`` supplies the file-download
helper from the repository-download port (or a small stand-in until it
exists).
"""

from __future__ import annotations

import inspect
import io
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import osf
from metacheck.archives.osf import osf_cache_clear, osf_file_download, osf_info
from metacheck.config import verbose


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    from metacheck.config import _state

    osf_cache_clear()
    saved = _state.get("verbose", _UNSET)
    verbose(True)
    yield
    # do not leak verbose(True) into other tests in this worker
    if saved is _UNSET:
        _state.pop("verbose", None)
    else:
        _state["verbose"] = saved


_UNSET = object()


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr()
    return out.err + out.out


def _files(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def test_mode_names() -> None:
    sig = inspect.signature(osf_file_download)
    assert sig.parameters["mode"].default == "all"
    with pytest.raises(ValueError, match="should be one of"):
        osf_file_download("pngda", mode="everything")


def test_osf_file_download_select(
    mock_api: respx.MockRouter,
    filetype_available: bool,
    download_available: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.warns(UserWarning):
        assert osf_file_download("notanid") is None

    dl = osf_file_download("6nt4v", str(tmp_path), metadata=False, mode="select")
    f = tmp_path / "6nt4v"
    assert f.is_dir()
    assert (f / "osfstorage" / "Processed_Data" / "processed-data.csv").exists()
    assert dl["folder"].tolist() == ["6nt4v"]
    assert dl["downloaded"].tolist() == [True]
    assert all(len(x) in (5, 24) for x in dl["osf_id"])
    assert {"size_on_disk", "attempted", "download_path", "osf_project", "osf_url"} <= set(dl)
    assert dl["osf_url"].tolist() == ["https://osf.io/6nt4v"]
    assert dl["path"].tolist() == ["osfstorage/Processed_Data/processed-data.csv"]

    # a second download resumes into the same folder
    _output(capsys)
    osf_cache_clear()
    dl2 = osf_file_download("6nt4v", str(tmp_path), metadata=False, mode="select")
    assert dl2["folder"].tolist() == ["6nt4v"]
    assert not (tmp_path / "6nt4v_1").exists()
    assert "already on disk" in _output(capsys)
    assert (f / "osfstorage" / "Processed_Data" / "processed-data.csv").exists()

    # an invalid ID among valid ones is dropped with a warning
    import shutil

    shutil.rmtree(f)
    osf_cache_clear()
    with pytest.warns(UserWarning, match="yuck"):
        dl3 = osf_file_download(["yuck", "6nt4v"], str(tmp_path), metadata=False, mode="select")
    assert dl3["name"].tolist() == dl["name"].tolist()


def test_files_is_an_alias_for_select(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    dl = osf_file_download("6nt4v", str(tmp_path), metadata=False, mode="files")
    assert {"size_on_disk", "attempted"} <= set(dl)
    assert len(dl) == 1
    assert dl["downloaded"].tolist() == [True]


def test_too_small_max_file_size(
    mock_api: respx.MockRouter,
    filetype_available: bool,
    download_available: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dl = osf_file_download(
        "6nt4v", str(tmp_path), max_file_size=0.0001, metadata=False, mode="select"
    )
    assert len(dl) == 1
    assert dl["folder"].tolist() == ["6nt4v"]
    assert dl["downloaded"].tolist() == [False]
    assert dl["attempted"].tolist() == [False]
    assert "per-file limit" in _output(capsys)
    assert list((tmp_path / "6nt4v").iterdir()) == []


def test_too_small_max_download_size(
    mock_api: respx.MockRouter,
    filetype_available: bool,
    download_available: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.warns(UserWarning, match="per-repository limit"):
        dl = osf_file_download(
            "6nt4v", str(tmp_path), max_download_size=0.0001, metadata=False, mode="select"
        )
    assert len(dl) == 1
    assert dl["downloaded"].tolist() == [False]
    assert "per-repository limit" in _output(capsys)
    assert list((tmp_path / "6nt4v").iterdir()) == []


NESTED = [
    "README",
    "data.xlsx",
    "nest-1/README",
    "nest-1/test-1.txt",
    "nest-1/nest-2/test-2.txt",
    "nest-1/nest-2/nest-3/test-3.txt",
    "nest-1/nest-2/nest-3/nest-4/test-4.txt",
]


def test_nested(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    dl = osf_file_download("j3gcx", str(tmp_path), metadata=False, mode="select")
    base = tmp_path / "j3gcx" / "osfstorage" / "Raw_Data"
    assert (base / "nest-1").is_dir()
    assert (base / "data.xlsx").exists()
    exp = [f"osfstorage/Raw_Data/{p}" for p in NESTED]
    assert set(dl["path"]) == set(exp)
    for p in exp:
        assert (tmp_path / "j3gcx" / p).exists()
    assert dl["downloaded"].all()


def test_truncate(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    with pytest.warns(UserWarning, match="truncated"):
        dl = osf_file_download(
            "j3gcx", str(tmp_path), max_folder_length=3, metadata=False, mode="select"
        )
    assert (tmp_path / "j3gcx" / "osf" / "Raw" / "nes").is_dir()
    assert (tmp_path / "j3gcx" / "osf" / "Raw" / "data.xlsx").exists()
    exp = [
        "osf/Raw/"
        + p.replace("nest-1", "nes")
        .replace("nest-2", "nes")
        .replace("nest-3", "nes")
        .replace("nest-4", "nes")
        for p in NESTED
    ]
    assert set(dl["path"]) == set(exp)
    for p in exp:
        assert (tmp_path / "j3gcx" / p).exists()


def test_multiple_osf_ids(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    dl = osf_file_download(["6nt4v", "j3gcx"], str(tmp_path), metadata=False, mode="select")
    assert dl["folder"].tolist() == ["6nt4v"] + ["j3gcx"] * 7
    assert (tmp_path / "6nt4v" / "osfstorage" / "Processed_Data" / "processed-data.csv").exists()
    assert (tmp_path / "j3gcx" / "osfstorage" / "Raw_Data" / "nest-1" / "README").exists()


def test_waterbutler_folder(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    wb = "685a46eb8c103f8ab307047f"
    url = f"https://files.de-1.osf.io/v1/resources/j3gcx/providers/osfstorage/{wb}/?zip="
    dl = osf_file_download(url, str(tmp_path), metadata=False, mode="select")
    assert (dl["folder"] == wb).all()
    f = tmp_path / wb / "osfstorage" / "nest-1"
    assert f.is_dir()
    assert (f / "nest-2").is_dir()
    assert (f / "README").exists()


def test_long_unnested(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    dl = osf_file_download(
        "j3gcx", str(tmp_path), ignore_folder_structure=True, metadata=False, mode="select"
    )
    assert "test-4.txt" in set(dl["path"])
    f = tmp_path / "j3gcx"
    names = [p.name for p in f.iterdir()]
    assert len([n for n in names if n.endswith("README")]) == 2
    assert "test-4.txt" in names
    assert not (f / "nest-1").is_dir()


def test_issue_99(
    mock_api: respx.MockRouter, filetype_available: bool, download_available: bool, tmp_path: Path
) -> None:
    osf_file_download("msfcn", str(tmp_path), metadata=False, mode="select")
    assert len(_files(tmp_path / "msfcn")) == 3


def test_registrations(mock_api: respx.MockRouter, filetype_available: bool) -> None:
    contents = osf_info("jqkg7", recursive=True)
    assert {"folder", "file"} <= set(contents["kind"].dropna())


def test_mode_all_single_node(
    mock_api: respx.MockRouter, download_available: bool, tmp_path: Path
) -> None:
    dl = osf_file_download("6nt4v", str(tmp_path), mode="all", metadata=False)
    assert list(dl.columns) == [
        "folder",
        "osf_project",
        "osf_url",
        "title",
        "files",
        "bytes",
        "download_path",
        "downloaded",
    ]
    assert len(dl) == 1
    assert dl["downloaded"].tolist() == [True]
    assert dl["files"].tolist() == [1]
    assert dl["folder"].iloc[0].endswith("6nt4v")
    assert dl["folder"].iloc[0] == "Processed_Data_6nt4v"
    path = Path(dl["download_path"].iloc[0])
    assert path.is_dir()
    assert len(_files(path)) == 1


# -- mode = "zip", with the listing and the archive mocked --------------------


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _contents(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


@pytest.fixture
def zip_project(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def setup(contents: pd.DataFrame, archive: bytes, node: str) -> respx.MockRouter:
        monkeypatch.setattr(osf, "osf_info", lambda *a, **k: contents)
        monkeypatch.setattr(osf, "osf_type", lambda *a, **k: "nodes")
        router = respx.mock(assert_all_called=False)
        url = f"https://files.osf.io/v1/resources/{node}/providers/osfstorage/?zip="
        # no size request is made first: the OSF never reports an archive's
        # size, so it is estimated from the listing (metacheck #424)
        router.get(url).mock(return_value=httpx.Response(200, content=archive))
        return router

    return setup


def test_zip_keep_archive(zip_project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    contents = _contents(
        [
            {"osf_type": "files", "osf_id": "file1", "name": "processed-data.csv", "provider": "osfstorage",
             "path": "/processed-data.csv", "kind": "file", "size": 8.0, "download_url": "https://example.test/file.csv",
             "parent": "child1", "project": "child1", "filetype": "csv", "downloads": 1.0},
            {"osf_type": "nodes", "osf_id": "child1", "name": "Processed_Data", "provider": None, "path": None,
             "kind": "folder", "size": None, "download_url": None, "parent": "abcde", "project": "abcde",
             "filetype": None, "downloads": None},
        ]
    )  # fmt: skip
    with zip_project(contents, _zip_bytes({"processed-data.csv": "x,y\n1,2\n"}), "child1"):
        dl = osf_file_download("abcde", str(tmp_path), mode="zip", unzip=False, metadata=False)
    assert len(dl) == 1
    assert dl["downloaded"].tolist() == [True]
    # one archive per node that owns files, not one for the root
    assert dl["path"].tolist() == ["child1.zip"]
    assert (tmp_path / "abcde" / "child1.zip").exists()
    assert not (tmp_path / "abcde" / "abcde.zip").exists()


def test_zip_unzip_preserves_structure(zip_project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    text = "x,y\n1,2\n"
    contents = _contents(
        [
            {"osf_type": "files", "osf_id": "file1", "name": "processed-data.csv", "provider": "osfstorage",
             "path": "/processed-data.csv", "kind": "file", "size": float(len(text)),
             "download_url": "https://example.test/file.csv", "parent": "child1", "project": "child1",
             "filetype": "csv", "downloads": 1.0},
            {"osf_type": "nodes", "osf_id": "child1", "name": "Processed_Data", "provider": None, "path": None,
             "kind": "folder", "size": None, "download_url": None, "parent": "abcde", "project": "abcde",
             "filetype": None, "downloads": None},
        ]
    )  # fmt: skip
    with zip_project(contents, _zip_bytes({"processed-data.csv": text}), "child1"):
        dl = osf_file_download("abcde", str(tmp_path), mode="zip", unzip=True, metadata=False)
    assert dl["path"].tolist() == ["osfstorage/Processed_Data/processed-data.csv"]
    assert dl["downloaded"].tolist() == [True]
    assert not (tmp_path / "abcde" / "abcde.zip").exists()
    assert (tmp_path / "abcde" / "osfstorage" / "Processed_Data" / "processed-data.csv").exists()


def test_zip_unzip_can_flatten(zip_project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    files = {"README": "root\n", "nested/README": "nested\n"}
    contents = _contents(
        [
            {"osf_type": "files", "osf_id": "file1", "name": "README", "provider": "osfstorage", "path": "/README",
             "kind": "file", "size": float(len(files["README"])), "download_url": "https://example.test/README",
             "parent": "abcde", "project": "abcde", "filetype": "txt", "downloads": 1.0},
            {"osf_type": "files", "osf_id": "file2", "name": "README", "provider": "osfstorage",
             "path": "/nested/README", "kind": "file", "size": float(len(files["nested/README"])),
             "download_url": "https://example.test/nested/README", "parent": "abcde", "project": "abcde",
             "filetype": "txt", "downloads": 1.0},
            {"osf_type": "nodes", "osf_id": "abcde", "name": "Project Root", "provider": None, "path": None,
             "kind": "folder", "size": None, "download_url": None, "parent": None, "project": None,
             "filetype": None, "downloads": None},
        ]
    )  # fmt: skip
    with zip_project(contents, _zip_bytes(files), "abcde"):
        dl = osf_file_download(
            "abcde",
            str(tmp_path),
            mode="zip",
            unzip=True,
            ignore_folder_structure=True,
            metadata=False,
        )
    assert set(dl["path"]) == {"README", "file2-README"}
    assert dl["downloaded"].all()
    assert (tmp_path / "abcde" / "README").exists()
    assert (tmp_path / "abcde" / "file2-README").exists()
    assert os.path.getsize(tmp_path / "abcde" / "README") == len(files["README"])


# -- mode = "zip" with a budget (metacheck #424) -------------------------------


def _budget_contents(sizes: list[float | None]) -> pd.DataFrame:
    names = [f"{c}.csv" for c in "abcdefgh"[: len(sizes)]]
    n = len(sizes)
    return pd.DataFrame(
        {
            "osf_type": ["files"] * n + ["nodes"],
            "osf_id": [f"file{i + 1}" for i in range(n)] + ["child1"],
            "name": [*names, "Data"],
            "provider": ["osfstorage"] * n + [None],
            "path": [f"/{x}" for x in names] + [None],
            "kind": ["file"] * n + ["folder"],
            "size": [float("nan") if s is None else float(s) for s in sizes] + [float("nan")],
            "download_url": [f"https://example.test/{x}" for x in names] + [None],
            "parent": ["child1"] * n + ["abcde"],
            "project": ["child1"] * n + ["abcde"],
            "filetype": ["csv"] * n + [None],
            "downloads": [1.0] * n + [float("nan")],
        }
    )


@pytest.fixture
def budget_project(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from metacheck.archives import download

    fetched: list[str] = []

    def many(urls, dests, expected=None, **kw):  # type: ignore[no-untyped-def]
        fetched.extend(urls)
        for dest, s in zip(dests, expected, strict=True):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(bytes(1 if s != s else int(s)))
        return [None] * len(urls)

    def setup(contents: pd.DataFrame) -> list[str]:
        monkeypatch.setattr(osf, "osf_info", lambda *a, **k: contents)
        monkeypatch.setattr(osf, "osf_type", lambda *a, **k: "nodes")
        monkeypatch.setattr(download, "_download_many_parallel", many)
        return fetched

    return setup


def test_zip_node_over_budget_is_fetched_file_by_file_within_it(  # type: ignore[no-untyped-def]
    budget_project, tmp_path: Path
) -> None:
    # 130 listed bytes do not fit in 50: no archive is requested; a.csv and
    # b.csv (30 bytes) fit, c.csv does not and is not attempted. metacheck
    # stops here instead (its cap_report() call was not renamed): U209
    fetched = budget_project(_budget_contents([10, 20, 100]))
    with respx.mock(assert_all_mocked=True), pytest.warns(UserWarning, match="did not fit"):
        dl = osf_file_download(
            "abcde", str(tmp_path), mode="zip", max_download_size=50 / 1024**2, metadata=False
        )
    assert sorted(fetched) == ["https://example.test/a.csv", "https://example.test/b.csv"]
    by_id = dl.set_index("osf_id")
    assert by_id.loc[["file1", "file2", "file3"], "downloaded"].tolist() == [True, True, False]
    assert by_id.loc[["file1", "file2", "file3"], "attempted"].tolist() == [True, True, False]


def test_zip_budget_leaves_out_files_without_a_size(  # type: ignore[no-untyped-def]
    budget_project, tmp_path: Path
) -> None:
    # the node's 110 listed bytes do not fit in 50; of the files, only a.csv
    # does (b.csv has no listed size, so it cannot be counted against the limit)
    fetched = budget_project(_budget_contents([10, None, 100]))
    with respx.mock(assert_all_mocked=True), pytest.warns(UserWarning, match="2 files"):
        dl = osf_file_download(
            "abcde", str(tmp_path), mode="zip", max_download_size=50 / 1024**2, metadata=False
        )
    assert fetched == ["https://example.test/a.csv"]
    attempted = dl.set_index("osf_id").loc[["file1", "file2", "file3"], "attempted"]
    assert attempted.tolist() == [True, False, False]


def test_zip_archive_past_the_budget_is_stopped_and_removed(  # type: ignore[no-untyped-def]
    budget_project, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 30 listed bytes fit in 100, but the archive streams 500 (no
    # Content-Length): it is stopped at the limit, nothing is left on disk, and
    # the files come one by one
    fetched = budget_project(_budget_contents([10, 20]))
    body = b"PK" + b"\0" * 498

    def chunks() -> Iterator[bytes]:
        for i in range(0, len(body), 50):
            yield body[i : i + 50]

    with respx.mock(assert_all_mocked=True) as router:
        route = router.get(url__regex=r"\?zip=$").mock(
            side_effect=lambda req: httpx.Response(200, content=chunks())
        )
        dl = osf_file_download(
            "abcde", str(tmp_path), mode="zip", max_download_size=100 / 1024**2, metadata=False
        )
    assert route.call_count == 1  # a stopped transfer is not retried
    assert "the archive grew past the" in capsys.readouterr().err
    assert not (tmp_path / "abcde" / "child1.zip").exists()
    assert sorted(fetched) == ["https://example.test/a.csv", "https://example.test/b.csv"]
    assert dl["downloaded"].tolist() == [True, True]


def test_osf_download_zip_removes_an_error_page(tmp_path: Path) -> None:
    dest = tmp_path / "n.zip"
    with respx.mock(assert_all_mocked=True) as router:
        router.get("https://example.test/z").mock(return_value=httpx.Response(404, text="no"))
        with pytest.raises(RuntimeError, match="HTTP 404"):
            osf._osf_download_zip("https://example.test/z", str(dest))
    assert not dest.exists()


def test_stream_to_file_refuses_a_declared_length_over_the_limit(tmp_path: Path) -> None:
    dest = tmp_path / "n.zip"
    with respx.mock(assert_all_mocked=True) as router:
        router.get("https://example.test/z").mock(
            return_value=httpx.Response(200, content=b"x" * 10)
        )
        with pytest.raises(osf._MaxFileSizeExceeded):
            osf._stream_to_file("https://example.test/z", str(dest), max_bytes=9)
        assert not dest.exists()
        assert osf._stream_to_file("https://example.test/z", str(dest), max_bytes=10) == 200
    assert dest.read_bytes() == b"x" * 10


def test_stream_to_file_retries_like_every_storage_request(tmp_path: Path) -> None:
    dest = tmp_path / "r.zip"
    with respx.mock(assert_all_mocked=True) as router:
        route = router.get("https://example.test/r").mock(
            side_effect=[httpx.Response(503, content=b"busy"), httpx.Response(200, content=b"ok")]
        )
        assert osf._stream_to_file("https://example.test/r", str(dest)) == 200
        assert route.call_count == 2
        route = router.get("https://example.test/q").mock(return_value=httpx.Response(503))
        assert osf._stream_to_file("https://example.test/q", str(tmp_path / "q.zip")) == 503
        assert route.call_count == 5  # the tries ran out: the last answer, nothing written
    assert dest.read_bytes() == b"ok"
    assert not (tmp_path / "q.zip").exists()


def test_stream_to_file_raises_the_last_connection_error(tmp_path: Path) -> None:
    with respx.mock(assert_all_mocked=True) as router:
        route = router.get("https://example.test/d").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(httpx.ConnectError):
            osf._stream_to_file("https://example.test/d", str(tmp_path / "d.zip"))
        assert route.call_count == 5
        route.reset()
        with pytest.raises(httpx.ConnectError):  # a limited transfer is not started again
            osf._stream_to_file("https://example.test/d", str(tmp_path / "d.zip"), max_bytes=5)
        assert route.call_count == 1
