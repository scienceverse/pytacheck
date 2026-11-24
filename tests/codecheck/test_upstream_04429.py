"""Tests for the changes of metacheck 0.4.4.29 (one choice of files per repository, #699)."""

from __future__ import annotations

import secrets
import shutil
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.archives.download import _repo_cache_subdir
from metacheck.codecheck import core
from metacheck.utils import local_options


@pytest.fixture(autouse=True)
def _isolated_caches(tmp_path: Path) -> Iterator[None]:
    with local_options(
        {
            "metacheck.repo_cache.notified": True,
            "metacheck.repo_cache.dir": str(tmp_path / "persist"),
            "metacheck.repo_cache.session_dir": str(tmp_path / "session"),
        }
    ):
        yield


def test_code_expand_zip_fetches_code_files_within_the_repositorys_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def peek(url: str, **kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame({"name": ["a.R", "big.R"], "size": [10.0, 3.0 * 1024 * 1024]})

    def fetch(url: str, names: list[str], dest: str, **kwargs: Any) -> pd.DataFrame:
        seen.clear()
        seen.update(names=list(names), max_whole_bytes=kwargs["max_whole_bytes"])
        return pd.DataFrame(
            {
                "name": names,
                "path": None,
                "size": 10.0,
                "ok": False,
                "error": "not fetched in this test",
            }
        )

    monkeypatch.setattr("metacheck.archives.zip_peek.zip_peek", peek)
    monkeypatch.setattr("metacheck.archives.zip_peek._zip_fetch_members", fetch)
    repo = "https://osf.io/zip" + secrets.token_hex(4)
    folder = Path(_repo_cache_subdir(repo, cache=False))
    shutil.rmtree(folder, ignore_errors=True)
    files = pd.DataFrame(
        {
            "repo_url": [repo],
            "file_name": ["code.zip"],
            "file_path": ["code.zip"],
            "file_url": ["https://osf.io/download/zzzzz"],
            "file_size": [5000.0],
        }
    )
    mb = 1024 * 1024
    # a whole copy of the zip is held to max_file_size
    core._code_expand_zip(files, max_download_size=50, max_file_size=20)
    assert set(seen["names"]) == {"a.R", "big.R"}
    assert seen["max_whole_bytes"] == 20 * mb
    # a code file over max_file_size is left out
    seen.clear()
    core._code_expand_zip(files, max_download_size=50, max_file_size=2)
    assert seen["names"] == ["a.R"]
    # what the repository's folder holds counts: a full folder leaves no room, where the code
    # files were fetched with no budget at all before
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "full.bin").write_bytes(bytes(mb))
    seen.clear()
    with pytest.warns(UserWarning, match="1 MB per-repository budget"):
        out = core._code_expand_zip(files, max_download_size=1, max_file_size=2)
    assert not seen
    assert len(out) == 1
    shutil.rmtree(folder, ignore_errors=True)


def test_code_expand_zip_is_held_to_max_files_per_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    # a repository over the file count has no files fetched from its zips
    called: list[bool] = []

    monkeypatch.setattr(
        "metacheck.archives.zip_peek.zip_peek",
        lambda url, **kw: pd.DataFrame({"name": ["a.R"], "size": [10.0]}),
    )
    monkeypatch.setattr(
        "metacheck.archives.zip_peek._zip_fetch_members",
        lambda *a, **k: called.append(True),
    )
    files = pd.DataFrame(
        {
            "repo_url": "https://osf.io/many",
            "file_name": ["code.zip", "b.csv", "c.csv"],
            "file_path": ["code.zip", "b.csv", "c.csv"],
            "file_url": [f"https://osf.io/{n}" for n in "xyz"],
            "file_size": [5000.0, 10.0, 10.0],
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = core._code_expand_zip(files, max_files_per_repo=2)
    assert not called
    assert out["file_name"].tolist() == ["code.zip", "b.csv", "c.csv"]


def test_the_downloads_of_the_checks_pass_the_whole_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the file count and the choice of files for the budget come from the full listing, not
    # from the subset still to download (#699)
    import metacheck.archives.download as dl_mod

    asked: dict[str, Any] = {}

    def fake(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        asked.update(kwargs, names=files["file_name"].tolist())
        return files.assign(file_location="/x")

    monkeypatch.setattr(dl_mod, "download_repo_files", fake)
    listing = pd.DataFrame(
        {
            "repo_url": ["r", "r", "r"],
            "file_name": ["a.R", "b.R", "c.csv"],
            "file_url": ["u1", "u2", "u3"],
        }
    )
    out = core._download(listing.iloc[[0]], listing, max_file_size=1)
    assert out is not None
    assert asked["names"] == ["a.R"]
    assert asked["repo_listing"] is listing
    assert "repo_file_counts" not in asked
