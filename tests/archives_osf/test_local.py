"""Port of metacheck's tests/testthat/test-archive-local.R.

``file_type`` comes from the file-category port; tests that need it are
skipped until it is available.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pytacheck.archives.local import local_files

COLUMNS = ["repo_url", "file_name", "file_url", "file_location", "file_size", "file_type"]


@pytest.fixture
def category() -> None:
    pytest.importorskip("pytacheck.fileinfo.category")


def test_local_files_missing_path() -> None:
    path = "/no/such/path/exists/anywhere"
    with pytest.warns(UserWarning, match="does not exist"):
        obs = local_files(path)
    assert len(obs) == 0
    assert list(obs.columns) == COLUMNS


def test_local_files_mixed_missing(fixtures_dir: Path, category: None) -> None:
    path = [str(fixtures_dir / "code_files" / "analysis.R"), "/no/such/path/exists/anywhere"]
    with pytest.warns(UserWarning, match="/no/such/path/exists/anywhere"):
        obs = local_files(path)
    assert obs["file_name"].tolist() == ["analysis.R"]


def test_local_files_file_path(fixtures_dir: Path, category: None) -> None:
    path = str(fixtures_dir / "code_files" / "analysis.R")
    obs = local_files(path)
    assert obs["repo_url"].tolist() == [path]
    assert obs["file_name"].tolist() == ["analysis.R"]
    assert obs["file_location"].tolist() == [os.path.realpath(path)]


def test_local_files_dir_path(fixtures_dir: Path, category: None) -> None:
    path = str(fixtures_dir / "demo" / "code")
    obs = local_files(path)
    assert set(obs["repo_url"]) == {path}
    assert obs["file_name"].tolist() == [f"{i:02d}.R" for i in range(1, 26)]


def test_local_files_mixed_file_and_dir(fixtures_dir: Path, category: None) -> None:
    path = [str(fixtures_dir / "demo" / "code"), str(fixtures_dir / "demo" / "README.md")]
    obs = local_files(path)
    assert set(obs["repo_url"].tolist()[:25]) <= {path[0]}
    assert set(obs["file_name"]) == {f"{i:02d}.R" for i in range(1, 26)} | {"README.md"}


def test_local_files_empty_directory(tmp_path: Path) -> None:
    result = local_files(str(tmp_path))
    assert list(result.columns) == COLUMNS
    assert len(result) == 0


def test_local_files_column_structure(fixtures_dir: Path, category: None) -> None:
    path = str(fixtures_dir / "code_files")
    result = local_files(path)
    assert list(result.columns) == COLUMNS
    assert (result["repo_url"] == path).all()
    assert result["file_name"].tolist() == [os.path.basename(p) for p in result["file_location"]]
    assert result["file_url"].isna().all()
    assert all(os.path.exists(p) for p in result["file_location"])
    assert str(result["file_size"].dtype) == "float64"
    assert (result["file_size"] > 0).all()


def test_local_files_recursive(fixtures_dir: Path, category: None) -> None:
    path = str(fixtures_dir / "code_files")
    result = local_files(path, recursive=True)
    names = set(result["file_name"])
    assert {"analysis.R", "analysis_no_comments.R", "data.csv", "README.md", "helper.R"} <= names
    assert len(result) == 7
    result = local_files(path, recursive=False)
    assert len(result) == 6
    assert "helper.R" not in set(result["file_name"])


def test_local_files_file_types(fixtures_dir: Path, category: None) -> None:
    result = local_files(str(fixtures_dir / "code_files"))
    types = dict(zip(result["file_name"], result["file_type"], strict=True))
    assert types["analysis.R"] == "code"
    assert types["data.csv"] == "data"
    assert types["README.md"] == "readme"


def test_local_files_vectorised(tmp_path: Path, category: None) -> None:
    d1, d2 = tmp_path / "one", tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    (d1 / "script1.R").write_text("# script\nlibrary(dplyr)\n")
    (d2 / "data.csv").write_text("x,y\n1,2\n")
    result = local_files([str(d1), str(d2)])
    assert len(result) == 2
    urls = dict(zip(result["file_name"], result["repo_url"], strict=True))
    assert urls == {"script1.R": str(d1), "data.csv": str(d2)}


def test_local_files_no_extension(tmp_path: Path, category: None) -> None:
    (tmp_path / "Makefile").write_text("content\n")
    result = local_files(str(tmp_path))
    assert result.loc[result["file_name"] == "Makefile", "file_type"].isna().all()


def test_local_files_vector_passes_recursive(tmp_path: Path, category: None) -> None:
    # U49: each path is listed with `recursive` (metacheck drops it for several paths)
    sub = tmp_path / "a" / "deep"
    sub.mkdir(parents=True)
    (sub / "x.R").write_text("1\n")
    (tmp_path / "b").mkdir()
    result = local_files([str(tmp_path / "a"), str(tmp_path / "b")], recursive=True)
    assert result["file_name"].tolist() == ["x.R"]
    assert len(local_files([str(tmp_path / "a"), str(tmp_path / "b")])) == 0
