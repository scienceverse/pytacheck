"""One ``max_download_size`` budget per repository, shared by archive members and files with
their own address, counting the cache first (metacheck #698)."""

from __future__ import annotations

import math
import os
import secrets
import shutil
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.archives import zip_peek as zp
from metacheck.archives.download import _repo_cache_subdir, download_repo_files
from metacheck.utils import local_options


@pytest.fixture(autouse=True)
def _isolated_caches(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    cache = tmp_path_factory.mktemp("cache")
    with local_options({"metacheck.repo_cache.dir": str(cache)}):
        yield


def test_archive_members_share_one_budget_per_repository_with_its_other_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # three archives of three 300,000-byte members under a 1 MB budget: each archive had its
    # own budget, so all 9 members were fetched
    repo = "https://example.org/repo-698-" + secrets.token_hex(5)
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)
    a = [i for i in (1, 2, 3) for _ in (1, 2, 3)]
    m = [j for _ in (1, 2, 3) for j in (1, 2, 3)]
    files = pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": [f"m{i}_{j}.csv" for i, j in zip(a, m, strict=True)],
            "file_path": [f"arc{i}.zip/m{i}_{j}.csv" for i, j in zip(a, m, strict=True)],
            "file_url": None,
            "file_size": 300000.0,
            "file_location": pd.Series([None] * 9, dtype="string"),
            "archive_url": [f"{repo}/arc{i}.zip" for i in a],
            "archive_member": [f"m{i}_{j}.csv" for i, j in zip(a, m, strict=True)],
            "paper_id": "p",
        }
    )
    fetched: list[str] = []

    def fake_fetch(url: str, names: list[str], dest: str, **kw: Any) -> pd.DataFrame:
        fetched.extend(names)
        paths = []
        for n in names:
            p = Path(dest) / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(bytes(300000))
            paths.append(str(p))
        return pd.DataFrame(
            {
                "name": names,
                "path": paths,
                "size": [300000.0] * len(names),
                "ok": [True] * len(names),
                "error": [None] * len(names),
            }
        )

    monkeypatch.setattr(zp, "_zip_fetch_members", fake_fetch)
    with pytest.warns(
        UserWarning, match="Repository .* exceeds the 1 MB per-repository budget"
    ) as w:
        first = download_repo_files(files, max_download_size=1)
    assert any(
        "fetched the smallest archive members up to the cap, 6 members omitted" in str(x.message)
        for x in w
    )
    assert len(fetched) == 3
    assert first["file_location"].notna().sum() == 3

    # a second call counts the saved members first and fetches nothing new
    def never(*a: Any, **kw: Any) -> pd.DataFrame:
        raise RuntimeError("fetched again")

    monkeypatch.setattr(zp, "_zip_fetch_members", never)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        second = download_repo_files(files, max_download_size=1)
    assert len(second.attrs["failed"]) == 0

    # (the paths are the same files: on Windows the fake fetch writes "\\" where the saved
    # members are found by "/")
    def same(x: pd.Series) -> list[str | None]:
        return [None if pd.isna(v) else os.path.normpath(v) for v in x.tolist()]

    assert same(second["file_location"]) == same(first["file_location"])

    # a file with its own address shares the budget: the members kept (900,000 bytes) leave no
    # room for 200,000 bytes more
    own = pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": "own.csv",
            "file_path": "own.csv",
            "file_url": f"{repo}/own.csv",
            "file_size": 200000.0,
            "file_location": pd.Series([None], dtype="string"),
            "archive_url": None,
            "archive_member": None,
            "paper_id": "p",
        }
    )
    with pytest.warns(
        UserWarning, match="downloaded the smallest files up to the cap, 1 file omitted"
    ):
        third = download_repo_files(pd.concat([files, own], ignore_index=True), max_download_size=1)
    assert pd.isna(third["file_location"].iloc[9])
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)


def test_the_files_of_a_repository_with_their_own_address_start_from_what_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a cached file with its own address counts against the repository's budget when its
    # archive members are chosen: 600,000 bytes cached leave room for one 300,000-byte member
    repo = "https://example.org/repo-698b-" + secrets.token_hex(5)
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)
    cached = _repo_cache_subdir(repo)
    Path(cached).mkdir(parents=True, exist_ok=True)
    (Path(cached) / "own.csv").write_bytes(bytes(600000))
    files = pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": ["own.csv", "a.csv", "b.csv", "c.csv"],
            "file_path": ["own.csv", "x.zip/a.csv", "x.zip/b.csv", "x.zip/c.csv"],
            "file_url": [f"{repo}/own.csv", None, None, None],
            "file_size": [600000.0, 300000.0, 300000.0, 300000.0],
            "file_location": pd.Series([None] * 4, dtype="string"),
            "archive_url": [None, *([f"{repo}/x.zip"] * 3)],
            "archive_member": [None, "a.csv", "b.csv", "c.csv"],
            "paper_id": "p",
        }
    )
    seen: list[Any] = []

    def fake_fetch(url: str, names: list[str], dest: str, **kw: Any) -> pd.DataFrame:
        seen.append((list(names), kw["max_whole_bytes"]))
        paths = []
        for n in names:
            p = Path(dest) / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(bytes(300000))
            paths.append(str(p))
        return pd.DataFrame({"name": names, "path": paths, "size": 3e5, "ok": True, "error": None})

    monkeypatch.setattr(zp, "_zip_fetch_members", fake_fetch)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dl = download_repo_files(files, max_download_size=1, cache=True)
    assert [n for n, _ in seen] == [["a.csv"]]
    # a whole copy of the archive may use only what is left of the budget
    assert seen[0][1] == 1024 * 1024 - 600000 - 300000
    assert dl["file_location"].notna().tolist() == [True, True, False, False]
    assert math.isfinite(seen[0][1])
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)
