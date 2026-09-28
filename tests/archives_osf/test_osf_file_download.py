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

from pytacheck.archives import osf
from pytacheck.archives.osf import osf_cache_clear, osf_file_download, osf_info
from pytacheck.config import verbose


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    from pytacheck.config import _state

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
        router.head(url).mock(
            return_value=httpx.Response(200, headers={"content-length": str(len(archive))})
        )
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
