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
    _repo_cache_rel,
    _repo_cache_subdir,
    _run_download_want,
    _storage_archive_skip,
    _zip_timeout_for_size,
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
    # without the storage's own totals the archive's size is not known (metacheck #679)
    assert "not listed in full" in str(_storage_archive_skip(files, [0], 100, mb))


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


# -- #679: storage totals from whole listings only, storage in the cache path, zip time ----


def test_osf_storage_totals_counts_only_listings_of_whole_storages_and_the_archive_needs_them() -> (
    None
):
    # a project listed in full (rows 1-3) and two single files linked from another storage
    # (rows 4-5); row 6 is row 1's file, linked on its own too
    f = pd.DataFrame(
        {
            "osf_node": ["n1", "n1", "n1", "n2", "n2", "n1"],
            "provider": "osfstorage",
            "file_path": ["a.csv", "b.csv", "c.R", "x.R", "y.R", "a.csv"],
            "file_size": [100.0, 50.0, 10.0, 20.0, 30.0, 100.0],
        }
    )
    t = _osf_storage_totals(f, complete=[True, True, True, False, False, False])
    sizes = t["storage_size"].tolist()
    assert sizes[:3] == [160.0, 160.0, 160.0] and math.isnan(sizes[3]) and math.isnan(sizes[4])
    assert sizes[5] == 160.0
    assert t["storage_n"].tolist()[:3] == [3, 3, 3] and t["storage_n"].isna().tolist()[3:5] == [
        True,
        True,
    ]
    assert t["storage_n"].tolist()[5] == 3
    # the counted copy is kept when a file not counted comes first
    f2 = f.iloc[[5, 0, 1, 2]].reset_index(drop=True)
    t2 = _osf_storage_totals(f2, complete=[False, True, True, True])
    assert t2["storage_size"].tolist() == [160.0] * 4
    # unknown totals: no archive, whatever the wanted files would suggest
    mb = 1024.0**2
    why = _storage_archive_skip(t, [3, 4], 500, mb)
    assert why is not None and "not listed in full" in why
    assert _storage_archive_skip(t, [0, 1, 2], 500, mb) is None


def test_files_of_an_osf_projects_github_storage_and_its_osf_storage_are_kept_apart() -> None:
    assert _repo_cache_rel("https://osf.io/haz6u", "Data/a.csv", "github") == (
        "osf.io_haz6u/_storage_github/Data/a.csv"
    )
    assert _repo_cache_rel("https://osf.io/haz6u", "Data/a.csv", "osfstorage") == (
        "osf.io_haz6u/Data/a.csv"
    )
    assert _repo_cache_rel("https://osf.io/haz6u", "Data/a.csv") == "osf.io_haz6u/Data/a.csv"
    assert _repo_cache_rel("https://github.com/o/r", ["x.R", "y.R"], None) == [
        "github.com_o_r/x.R",
        "github.com_o_r/y.R",
    ]


def test_the_same_path_in_a_github_storage_and_in_osf_storage_is_two_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # osf.io/haz6u: the same path in its GitHub storage and in its OSF Storage, two different
    # files (metacheck #679)
    repo = "https://osf.io/haz6u-679"
    files = pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": "a.csv",
            "file_path": "Data/a.csv",
            "file_url": [
                "https://raw.githubusercontent.com/o/r/master/Data/a.csv",
                "https://osf.io/download/zz1/",
            ],
            "file_size": [1.0, 2.0],
            "provider": ["github", "osfstorage"],
            "osf_node": "haz6u",
            "storage_size": math.nan,
            "storage_n": pd.Series([pd.NA, pd.NA], dtype="Int64"),
            "file_location": pd.Series([None, None], dtype="string"),
        }
    )
    import shutil

    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)

    def write(url: str, dest: str) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("from github\n" if "github" in url else "from osf\n")

    def fake_parallel(urls: list[str], dests: list[str], *a: Any, **kw: Any) -> list[None]:
        for u, d in zip(urls, dests, strict=True):
            write(u, d)
        return [None] * len(urls)

    def fake_one(url: str, dest: str, **kw: Any) -> None:
        write(url, dest)

    monkeypatch.setattr(dlm, "_download_many_parallel", fake_parallel)
    monkeypatch.setattr(dlm, "_download_one", fake_one)
    monkeypatch.setattr(dlm, "_message", lambda *parts: None)
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    loc = dl["file_location"].tolist()
    assert all(v is not None and not pd.isna(v) for v in loc) and loc[0] != loc[1]
    assert Path(loc[0]).read_text() == "from github\n"
    assert Path(loc[1]).read_text() == "from osf\n"
    assert "_storage_github" in loc[0]
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)


def test_zip_timeout_for_size_also_allows_time_for_each_file_in_the_archive() -> None:
    # 612 files, 8.5 MB, as the OSF zip of osf.io/xe9v7 that took 178 s
    t = _zip_timeout_for_size(120, 8463365, expected_files=612)
    assert t > 178
    assert t == 8463365 / (200 * 1024) + 612 * 0.4
    assert _zip_timeout_for_size(120, math.nan, expected_files=10) == 120  # the floor
    assert _zip_timeout_for_size(120, math.nan, expected_files=1000) == 400
    assert _zip_timeout_for_size(120, 1024) == 120  # as before without files


def test_single_osf_files_whose_storage_was_not_listed_in_full_go_file_by_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = "https://osf.io/abcde-679"
    single = pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": ["c.csv", "d.csv"],
            "file_path": ["c.csv", "d.csv"],
            "file_url": [f"https://osf.io/download/s{i}/" for i in (3, 4)],
            "file_size": [1000.0, 1000.0],
            "provider": "osfstorage",
            "osf_node": "abcde",
            "storage_size": math.nan,
            "storage_n": pd.Series([pd.NA, pd.NA], dtype="Int64"),
            "file_location": pd.Series([None, None], dtype="string"),
        }
    )
    import shutil

    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)
    zips: list[str] = []
    one_by_one: list[str] = []

    def fake_zip(df: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
        zips.append(zip_url)
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
    download_repo_files(single, max_file_size=10, max_download_size=100)
    assert zips == []
    assert len(one_by_one) == 2
    shutil.rmtree(_repo_cache_subdir(repo), ignore_errors=True)


def test_repo_check_counts_a_storage_only_from_a_listing_of_whole_storages() -> None:
    from metacheck.modules._repo_check import _osf_file_frame, _osf_full_listings

    # a linked project (listed in full), a linked project listed only in part, and a link to a
    # single file
    urls = [
        "https://osf.io/aaaaa",
        "https://osf.io/aaaaa",
        "https://osf.io/bbbbb",
        "https://osf.io/ccccc",
        "https://osf.io/ccccc",
        "https://osf.io/aaaaa/files/x",
    ]
    info = pd.DataFrame(
        {
            "repo_name": urls,
            "osf_url": urls,
            "osf_type": ["nodes", "files", "nodes", "registrations", "files", "files"],
        }
    )
    assert _osf_full_listings(info, ["https://osf.io/bbbbb"]) == [
        "https://osf.io/aaaaa",
        "https://osf.io/ccccc",
    ]
    assert _osf_full_listings(info, []) == [
        "https://osf.io/aaaaa",
        "https://osf.io/bbbbb",
        "https://osf.io/ccccc",
    ]
    assert _osf_full_listings(pd.DataFrame({"repo_name": ["x"]}), []) == []

    files = pd.DataFrame(
        {
            "repo_name": ["https://osf.io/aaaaa", "https://osf.io/aaaaa/files/x"],
            "name": ["a.csv", "x.csv"],
            "path": ["/a.csv", "/x.csv"],
            "size": [1.0, 2.0],
        }
    )
    out = _osf_file_frame(files, files["repo_name"].tolist(), ["https://osf.io/aaaaa"])
    assert out["storage_listed"].tolist() == [True, False]
    # a listing without the provider and project columns gave one NA for no rows, and
    # data.frame() stopped with "differing number of rows: 0, 1"
    empty = _osf_file_frame(files.iloc[0:0], [], [])
    assert len(empty) == 0 and {"provider", "osf_node", "storage_listed"} <= set(empty.columns)
