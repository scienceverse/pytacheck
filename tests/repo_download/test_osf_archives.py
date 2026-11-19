"""OSF Storage and GitHub repositories linked to an OSF project as archives (#675).

Port of the #675 tests of metacheck's test-repo-download.R.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.archives import download as dlm
from metacheck.archives.download import (
    _archive_worth_it,
    _github_raw_parts,
    _osf_storage_totals,
    _repo_cache_subdir,
    _run_download_want,
    _storage_archive_skip,
    download_repo_files,
)
from metacheck.utils import local_options


@pytest.fixture(autouse=True)
def _isolated_caches(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    base = tmp_path_factory.mktemp("repo-cache")
    with local_options(
        {
            "metacheck.repo_cache.notified": True,
            "metacheck.repo_cache.dir": str(base / "persist"),
            "metacheck.repo_cache.session_dir": str(base / "session"),
        }
    ):
        yield


def test_github_raw_parts_reads_the_repository_branch_and_path_of_a_github_file_address() -> None:
    p = _github_raw_parts(
        [
            "https://raw.githubusercontent.com/Lakens/open_peer_review/master/peerj/10.txt",
            "https://raw.githubusercontent.com/o/r/main/data%20raw/a%20b.csv",
            "https://osf.io/download/abcde/",
            None,
        ]
    )
    assert p["repo"].tolist() == ["Lakens/open_peer_review", "o/r", pd.NA, pd.NA]
    assert p["ref"].tolist() == ["master", "main", pd.NA, pd.NA]
    assert p["path"].tolist() == ["peerj/10.txt", "data raw/a b.csv", pd.NA, pd.NA]


def test_osf_storage_totals_counts_each_osf_storage_once_per_file() -> None:
    f = pd.DataFrame(
        {
            "osf_node": ["n1", "n1", "n1", "n2", "n1", None],
            "provider": [
                "osfstorage",
                "osfstorage",
                "github",
                "osfstorage",
                "osfstorage",
                "osfstorage",
            ],
            "file_path": ["a.csv", "b.csv", "c.R", "a.csv", "a.csv", "x.csv"],
            "file_size": [100, 50, 10, math.nan, 100, 5],
        }
    )
    t = _osf_storage_totals(f)
    # the 5th row repeats the 1st (listed through a registration and its project)
    size = t["storage_size"].tolist()
    assert size[:3] == [150, 150, 10] and math.isnan(size[3]) and size[4] == 150
    assert math.isnan(size[5])
    assert t["storage_n"].tolist()[:5] == [2, 2, 1, 1, 2] and pd.isna(t["storage_n"].iloc[5])
    # a table without the columns is returned as it is
    plain = pd.DataFrame({"repo_name": []})
    assert _osf_storage_totals(plain) is plain


def test_archive_worth_it_needs_enough_files_and_not_much_more_data_than_they_hold() -> None:
    def w(**kw: float) -> bool:
        return _archive_worth_it(**kw)  # type: ignore[arg-type]

    assert w(n_wanted=3, record_n=3, wanted_bytes=100, archive_bytes=100)
    assert w(n_wanted=60, record_n=500, wanted_bytes=1e6, archive_bytes=1.5e6)
    assert not w(n_wanted=3, record_n=500, wanted_bytes=1e6, archive_bytes=1.1e6)  # too few files
    assert not w(n_wanted=60, record_n=61, wanted_bytes=1e6, archive_bytes=1e9)  # too much data
    assert not w(n_wanted=3, record_n=3, wanted_bytes=0, archive_bytes=100)  # sizes unknown
    assert not w(n_wanted=3, record_n=3, wanted_bytes=100, archive_bytes=math.nan)


def test_the_reason_an_archive_is_not_used_names_the_files_and_sizes() -> None:
    mb = 1024 * 1024
    files = pd.DataFrame(
        {
            "file_size": [1000.0],
            "storage_size": [1e6],
            "storage_n": [400],
        }
    )
    why = _storage_archive_skip(files, [0], 100, mb)
    assert why == "the archive holds 400 files (1 MB) for 1 wanted (0 MB)"
    files["storage_n"] = [1]
    files["storage_size"] = [1500.0]
    assert _storage_archive_skip(files, [0], 100, mb) is None
    files["storage_size"] = [math.nan]
    files["file_size"] = [0.0]
    assert "archive size unknown" in str(_storage_archive_skip(files, [0], 100, mb))


def test_a_github_file_address_makes_its_repository_a_whole_archive_in_run_download_want() -> None:
    files = pd.DataFrame(
        {
            "repo_url": ["https://osf.io/abcde", "https://osf.io/abcde"],
            "file_name": ["a.png", "b.png"],
            "file_path": ["a.png", "b.png"],
            "file_url": [
                "https://raw.githubusercontent.com/o/r/main/a.png",
                "https://osf.io/download/x/",
            ],
        }
    )
    # a picture of a GitHub repository is fetched with its archive; one of the OSF is not
    assert _run_download_want(files) == [True, False]


def test_download_repo_files_fetches_a_github_repository_behind_an_osf_project_and_osf_storage_as_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = "https://osf.io/abcde-675"
    gh = [f"https://raw.githubusercontent.com/own/rep/master/{p}" for p in ("a.R", "dir%20x/b.csv")]
    files = pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": ["a.R", "b.csv", "c.csv", "d.csv", "e.csv"],
            "file_path": ["a.R", "dir x/b.csv", "c.csv", "d.csv", "e.csv"],
            "file_url": [*gh, *[f"https://osf.io/download/f{i}/" for i in (3, 4, 5)]],
            "file_size": [1000.0] * 5,
            "provider": ["github", "github", "osfstorage", "osfstorage", "osfstorage"],
            "osf_node": ["abcde", "abcde", "abcde", "abcde", "kid12"],
            "storage_size": [2000.0, 2000.0, 2000.0, 2000.0, 1e6],
            "storage_n": [2, 2, 2, 2, 400],
            "file_location": pd.Series([None] * 5, dtype="string"),
        }
    )
    import shutil

    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)
    calls: list[dict[str, Any]] = []
    one_by_one: list[str] = []

    def fake_zip(
        df: pd.DataFrame,
        row_idx: list[int],
        zip_url: str,
        strip_dir: bool = False,
        rel_paths: list[Any] | None = None,
        **kw: Any,
    ) -> pd.DataFrame:
        paths = df["file_path"].tolist()
        calls.append(
            {
                "url": zip_url,
                "strip_dir": strip_dir,
                "rel": rel_paths if rel_paths is not None else [paths[i] for i in row_idx],
            }
        )
        df.loc[row_idx, "file_location"] = df.loc[row_idx, ".cache_path"]
        return df

    def fake_parallel(urls: list[str], dests: list[str], *a: Any, **kw: Any) -> list[None]:
        one_by_one.extend(urls)
        for d in dests:
            Path(d).parent.mkdir(parents=True, exist_ok=True)
            Path(d).write_bytes(b"\0")
        return [None] * len(urls)

    monkeypatch.setattr(dlm, "_download_zip_to_cache", fake_zip)
    monkeypatch.setattr(dlm, "_download_many_parallel", fake_parallel)
    monkeypatch.setattr(dlm, "_message", lambda *parts: None)
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    assert dl["file_location"].notna().all()
    urls = [c["url"] for c in calls]
    # the GitHub repository's archive, matched by the paths in the addresses
    assert urls[0] == "https://api.github.com/repos/own/rep/zipball/master"
    assert calls[0]["strip_dir"] is True
    assert calls[0]["rel"] == ["a.R", "dir x/b.csv"]
    # component abcde's OSF Storage (2 of its 2 files wanted) as one zip
    assert urls[1] == "https://files.osf.io/v1/resources/abcde/providers/osfstorage/?zip="
    assert calls[1]["strip_dir"] is False
    assert len(calls) == 2
    # component kid12: 1 file wanted of 400, so not its archive
    assert one_by_one == ["https://osf.io/download/f5/"]

    # a listing made before the component was recorded goes file by file
    calls.clear()
    one_by_one.clear()
    old = files.iloc[2:4].drop(columns=["osf_node", "storage_size", "storage_n"])
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)
    dl = download_repo_files(old, max_file_size=10, max_download_size=100)
    assert calls == []
    assert len(one_by_one) == 2
