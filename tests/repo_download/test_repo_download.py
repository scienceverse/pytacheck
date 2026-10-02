"""Tests for download_repo_files() and its cache (port of test-repo-download.R).

Like metacheck's tests, downloads run offline against local ``file://`` URLs;
the whole-repository zip paths, the storage retry policy and rate-limit
handling are exercised with respx.
"""

from __future__ import annotations

import io
import math
import os
import secrets
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

from metacheck import http
from metacheck.archives import download as dlm
from metacheck.archives.download import (
    _auth_for_url,
    _download_many_parallel,
    _download_one,
    _download_zip_to_cache,
    _format_object_size,
    _host_rate_limit_record,
    _host_rate_limit_remaining,
    _is_login_page,
    _rate_limit_wait,
    _repo_cache_path,
    _repo_cache_rel,
    _repo_cache_subdir,
    _storage_is_transient_factory,
    _storage_request,
    _storage_retry_after_factory,
    _wait_out_known_rate_limit,
    _zip_timeout_for_size,
    download_repo_files,
    repo_cache_clear,
    repo_cache_dir,
    repo_cache_size,
)
from metacheck.utils import get_option, local_options

DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(autouse=True)
def _isolated_caches(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Both caches in throwaway directories; the cache notice marked as shown."""
    base = tmp_path_factory.mktemp("repo-cache")
    with local_options(
        {
            "metacheck.repo_cache.notified": True,
            "metacheck.repo_cache.dir": str(base / "persist"),
            "metacheck.repo_cache.session_dir": str(base / "session"),
        }
    ):
        yield


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect metacheck's ``message()`` output of the download module."""
    out: list[str] = []
    monkeypatch.setattr(dlm, "_message", lambda *parts: out.append("".join(map(str, parts))))
    return out


def make_dl_files(tmp_path: Path, sizes: tuple[int, ...] = (100, 100)) -> pd.DataFrame:
    """A repo_check-shaped table pointing at local source files (a fresh repo URL)."""
    repo = "https://example.org/repo-test-" + secrets.token_hex(6)
    srcs = []
    for i, size in enumerate(sizes, start=1):
        p = tmp_path / f"src{i}_{secrets.token_hex(3)}.csv"
        p.write_text("x" * size + "\n")
        srcs.append(p)
    return pd.DataFrame(
        {
            "repo_url": repo,
            "file_name": [f"f{i}.csv" for i in range(1, len(srcs) + 1)],
            "file_path": [f"data/f{i}.csv" for i in range(1, len(srcs) + 1)],
            "file_url": ["file:///" + str(p) for p in srcs],
            "file_size": [float(p.stat().st_size) for p in srcs],
            "file_location": pd.Series([None] * len(srcs), dtype="string"),
        }
    )


def located(dl: pd.DataFrame) -> int:
    return int(dl["file_location"].notna().sum())


# -- test-repo-download.R -------------------------------------------------------


def test_downloads_and_populates_file_location(tmp_path: Path) -> None:
    files = make_dl_files(tmp_path)
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    assert dl is not None
    assert located(dl) == 2
    assert all(Path(p).exists() for p in dl["file_location"])
    assert len(dl.attrs["gated"]) == 0
    # the input is not modified
    assert files["file_location"].isna().all()
    assert ".cache_path" not in dl.columns


def test_reuses_cache_without_redownloading(tmp_path: Path) -> None:
    files = make_dl_files(tmp_path)
    dl1 = download_repo_files(files)
    files["file_url"] = "file:///nonexistent/path.csv"
    dl2 = download_repo_files(files)
    assert dl1["file_location"].tolist() == dl2["file_location"].tolist()
    assert all(Path(p).exists() for p in dl2["file_location"])


def test_file_over_per_file_cap_is_skipped(tmp_path: Path) -> None:
    files = make_dl_files(tmp_path, sizes=(50, 5000))
    cap_mb = files["file_size"].iloc[1] / (1024 * 1024) * 0.5
    dl = download_repo_files(files, max_file_size=cap_mb, max_download_size=100)
    assert located(dl) == 1
    assert len(dl.attrs["gated"]) == 0
    os_ = dl.attrs["oversize_skipped"]
    assert len(os_) == 1
    assert os_["file_name"].tolist() == ["f2.csv"]


def test_repo_over_total_cap_takes_smallest_files(tmp_path: Path) -> None:
    files = make_dl_files(tmp_path, sizes=(1000, 3000))
    small_mb = files["file_size"].iloc[0] / (1024 * 1024)
    total_mb = files["file_size"].sum() / (1024 * 1024)
    cap = (small_mb + total_mb) / 2
    with pytest.warns(UserWarning, match="per-repository budget"):
        dl = download_repo_files(files, max_file_size=100, max_download_size=cap)
    assert located(dl) == 1
    assert len(dl.attrs["gated"]) >= 1
    assert dl.attrs["gated"]["message"].str.contains("per-repository budget").all()


def test_max_files_per_repo_gates_by_count(tmp_path: Path) -> None:
    files = make_dl_files(tmp_path, sizes=(100,) * 5)
    with pytest.warns(UserWarning, match="exceeding the 3-file cap"):
        dl = download_repo_files(
            files, max_file_size=100, max_download_size=500, max_files_per_repo=3
        )
    assert located(dl) == 0
    assert len(dl.attrs["gated"]) == 1
    assert "exceeding the 3-file cap" in dl.attrs["gated"]["message"].iloc[0]

    files2 = make_dl_files(tmp_path, sizes=(100, 100))
    dl2 = download_repo_files(
        files2, max_file_size=100, max_download_size=500, max_files_per_repo=3
    )
    assert located(dl2) == 2


def test_repo_file_counts_is_the_true_count(tmp_path: Path) -> None:
    files = make_dl_files(tmp_path, sizes=(100,))
    repo = files["repo_url"].iloc[0]
    with pytest.warns(UserWarning, match="holds 4000 files"):
        dl = download_repo_files(files, max_files_per_repo=3, repo_file_counts={repo: 4000})
    assert located(dl) == 0


def test_na_size_falls_back_to_head_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = make_dl_files(tmp_path)
    files["file_size"] = math.nan
    monkeypatch.setattr(dlm, "_remote_size", lambda url: 6e9)
    dl = download_repo_files(files, max_file_size=100, max_download_size=500)
    assert located(dl) == 0
    assert len(dl.attrs["oversize_skipped"]) == 2


def test_unknown_size_is_excluded_not_gating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = make_dl_files(tmp_path)
    files["file_size"] = math.nan
    monkeypatch.setattr(dlm, "_remote_size", lambda url: math.nan)
    dl = download_repo_files(files, max_file_size=100, max_download_size=500)
    assert located(dl) == 0
    assert len(dl.attrs["gated"]) == 0


def test_real_size_probe_on_file_urls(tmp_path: Path) -> None:
    # a file:// answer has status 0, which is not a 2xx answer, so neither the
    # HEAD nor the ranged GET gives a size (as in metacheck, whose .head_size()
    # reads only a 2xx answer): the files are left out, not gated
    files = make_dl_files(tmp_path)
    files["file_size"] = math.nan
    dl = download_repo_files(files, max_file_size=100, max_download_size=500)
    assert located(dl) == 0
    assert len(dl.attrs["gated"]) == 0


def test_failed_downloads_are_reported(tmp_path: Path, messages: list[str]) -> None:
    files = make_dl_files(tmp_path)
    files.loc[1, "file_url"] = "file:///nonexistent/never/there.csv"
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    assert any("failed after retries" in m for m in messages)
    assert not pd.isna(dl["file_location"].iloc[0])
    assert pd.isna(dl["file_location"].iloc[1])
    fa = dl.attrs["failed"]
    assert len(fa) == 1
    assert fa["file_name"].tolist() == ["f2.csv"]
    assert fa["error"].iloc[0].startswith("Failed to perform HTTP request.")
    assert fa["file_url"].tolist() == [files["file_url"].iloc[1]]
    assert fa["paper_id"].isna().all()


def test_download_one_applies_size_scaled_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake(method: str, url: str, **kw: Any) -> Any:
        captured.update(kw)
        raise RuntimeError("stop before any real request")

    monkeypatch.setattr(dlm, "_storage_request", fake)
    _download_one("https://example.org/a.csv", str(tmp_path / "a"), expected_bytes=1024)
    assert captured["timeout"] == 60
    _download_one(
        "https://example.org/big.csv", str(tmp_path / "b"), expected_bytes=500 * 1024 * 1024
    )
    assert captured["timeout"] == (500 * 1024 * 1024) / (200 * 1024)


def test_download_many_parallel_applies_size_scaled_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, float] = {}

    def fake(method: str, url: str, **kw: Any) -> Any:
        captured[url] = kw["timeout"]
        raise RuntimeError("stop before any real request")

    monkeypatch.setattr(dlm, "_storage_request", fake)
    _download_many_parallel(
        ["https://example.org/a.csv", "https://example.org/big.csv"],
        [str(tmp_path / "a"), str(tmp_path / "b")],
        expected_size=[1024, 500 * 1024 * 1024],
    )
    assert captured["https://example.org/a.csv"] == 60
    assert captured["https://example.org/big.csv"] == (500 * 1024 * 1024) / (200 * 1024)


def test_cache_paths_are_stable_and_per_repo() -> None:
    a = _repo_cache_subdir("https://osf.io/abc")
    b = _repo_cache_subdir("https://osf.io/abc")
    c = _repo_cache_subdir("https://osf.io/xyz")
    assert a == b
    assert a != c
    assert a.endswith("/osf.io_abc")
    assert _repo_cache_subdir(None).endswith("/unknown")
    assert _repo_cache_rel("https://osf.io/abc", "\\sub\\x.csv") == "osf.io_abc/sub/x.csv"
    assert _repo_cache_path("https://osf.io/abc", ["a", "/b"]) == [
        f"{repo_cache_dir()}/osf.io_abc/a",
        f"{repo_cache_dir()}/osf.io_abc/b",
    ]


def test_repo_cache_clear_one_repo(tmp_path: Path, messages: list[str]) -> None:
    with local_options({"metacheck.repo_cache.dir": str(tmp_path / "cache")}):
        files = make_dl_files(tmp_path)
        dl = download_repo_files(files, max_file_size=10, max_download_size=100, cache=True)
        assert all(Path(p).exists() for p in dl["file_location"])
        before = repo_cache_size()
        assert before > 0

        freed = repo_cache_clear(files["repo_url"].iloc[0])
        assert "Cleared 1 cached repository" in messages[-1]
        assert freed > 0
        assert not Path(_repo_cache_subdir(files["repo_url"].iloc[0])).exists()
        assert not any(Path(p).exists() for p in dl["file_location"])
        assert repo_cache_clear(files["repo_url"].iloc[0], quiet=True) == 0


def test_repo_cache_clear_everything(tmp_path: Path, messages: list[str]) -> None:
    with local_options({"metacheck.repo_cache.dir": str(tmp_path / "cache")}):
        files = make_dl_files(tmp_path)
        download_repo_files(files, max_file_size=10, max_download_size=100, cache=True)
        assert repo_cache_size() > 0
        freed = repo_cache_clear()
        assert freed > 0
        assert messages[-1].startswith("Cleared the repository file cache (")
        assert Path(repo_cache_dir()).is_dir()
        assert repo_cache_size() == 0


def test_repo_cache_clear_refuses_unredirected_cache_in_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTACHECK_CACHE_DIR", raising=False)
    with (
        local_options({"metacheck.repo_cache.dir": None, "metacheck.cache.dir": None}),
        pytest.raises(RuntimeError, match="refused to empty the cache"),
    ):
        repo_cache_clear()


def test_format_object_size() -> None:
    assert _format_object_size(0) == "0 bytes"
    assert _format_object_size(12) == "12 bytes"
    assert _format_object_size(1536) == "1.5 Kb"
    assert _format_object_size(1048575) == "1024 Kb"
    assert _format_object_size(1048576 * 1.26) == "1.3 Mb"
    assert _format_object_size(123456789012) == "115 Gb"


def test_cache_notice_once_per_session(tmp_path: Path, messages: list[str]) -> None:
    with local_options(
        {"metacheck.repo_cache.notified": None, "metacheck.repo_cache.dir": str(tmp_path / "c")}
    ):
        files = make_dl_files(tmp_path)
        download_repo_files(files, max_file_size=10, max_download_size=100, cache=True)
        assert any("repo_cache_clear()" in m for m in messages)
        messages.clear()
        files2 = make_dl_files(tmp_path)
        download_repo_files(files2, max_file_size=10, max_download_size=100, cache=True)
        assert not any("repo_cache_clear" in m for m in messages)


def test_cache_false_uses_session_dir(tmp_path: Path, messages: list[str]) -> None:
    with local_options(
        {
            "metacheck.repo_cache.notified": None,
            "metacheck.repo_cache.dir": str(tmp_path / "persist"),
            "metacheck.repo_cache.session_dir": None,
        }
    ):
        files = make_dl_files(tmp_path)
        dl = download_repo_files(files, max_file_size=10, max_download_size=100)
        assert not any("repo_cache_clear" in m for m in messages)
        assert all(Path(p).exists() for p in dl["file_location"])
        assert repo_cache_size() == 0
        session_dir = get_option("metacheck.repo_cache.session_dir")
        assert session_dir is not None
        assert all(
            str(Path(p).resolve()).startswith(str(Path(session_dir).resolve()))
            for p in dl["file_location"]
        )


def _github_files(repo: str = "https://github.com/owner/repo-timeout") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": [repo],
            "file_name": ["a.csv"],
            "file_path": ["a.csv"],
            "file_url": ["https://raw.githubusercontent.com/owner/repo/main/a.csv"],
            "file_size": [1024.0],
            "file_location": pd.Series([None], dtype="string"),
        }
    )


def _fill_zip(files: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
    files = files.copy()
    files.loc[row_idx, "file_location"] = files.loc[row_idx, ".cache_path"]
    return files


def test_zip_timeout_is_passed_to_zip_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import metacheck.archives.github as github

    seen: dict[str, Any] = {}

    def fake_zip(files: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
        seen.update(kw, zip_url=zip_url)
        return _fill_zip(files, row_idx, zip_url)

    # a listed (small) file size gives the archive an estimate within budget,
    # so the zip transport, which is given zip_timeout_s, runs
    monkeypatch.setattr(github, "github_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(dlm, "_download_zip_to_cache", fake_zip)
    dl = download_repo_files(
        _github_files(), max_file_size=10, max_download_size=100, zip_timeout_s=7
    )
    assert seen["timeout_s"] == 7
    assert seen["zip_url"] == "https://api.github.com/repos/owner/repo/zipball"
    assert not pd.isna(dl["file_location"].iloc[0])


def test_zip_timeout_for_size() -> None:
    assert _zip_timeout_for_size(120, math.nan) == 120
    assert _zip_timeout_for_size(120, 0) == 120
    assert _zip_timeout_for_size(120, -5) == 120
    assert _zip_timeout_for_size(10, 1024) == 10
    expected = 500 * 1024 * 1024
    assert _zip_timeout_for_size(10, expected) == expected / (200 * 1024)
    assert _zip_timeout_for_size(10, expected, min_bytes_per_s=1024 * 1024) == expected / (
        1024 * 1024
    )


def test_github_archive_is_limited_to_twice_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # metacheck #424: GitHub's zipball reports no size, so it used to be
    # downloaded with no byte limit; it is now limited like Dryad's archive
    import metacheck.archives.github as github

    seen: dict[str, Any] = {}

    def fake_zip(files: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
        seen.update(kw)
        return _fill_zip(files, row_idx, zip_url)

    monkeypatch.setattr(github, "github_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(dlm, "_download_zip_to_cache", fake_zip)
    dl = download_repo_files(
        _github_files("https://github.com/owner/repo-cap"), max_file_size=10, max_download_size=100
    )
    assert seen["max_bytes"] == 2 * 100 * 1024 * 1024
    assert not pd.isna(dl["file_location"].iloc[0])


def test_github_archive_unsized_or_too_large_is_not_used(
    monkeypatch: pytest.MonkeyPatch, messages: list[str]
) -> None:
    # no whole-repository archive reports its size, so it is estimated from
    # the listed file sizes: with none listed there is no estimate, and an
    # estimate over 2x the budget is refused; both go file by file
    import metacheck.archives.github as github

    def make_files(sizes: list[float], repo: str) -> pd.DataFrame:
        names = [f"{c}.csv" for c in "abc"[: len(sizes)]]
        return pd.DataFrame(
            {
                "repo_url": [repo] * len(sizes),
                "file_name": names,
                "file_path": names,
                "file_url": [
                    f"https://raw.githubusercontent.com/owner/repo/main/{n}" for n in names
                ],
                "file_size": sizes,
                "file_location": pd.Series([None] * len(sizes), dtype="string"),
            }
        )

    fallback: list[str] = []

    def no_zip(*a: Any, **k: Any) -> Any:
        raise AssertionError("the archive should not be used")

    def fake_one(url: str, dest: str, **kw: Any) -> None:
        fallback.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00")

    monkeypatch.setattr(github, "github_repo", lambda repo: "owner/repo")
    # a file whose size is unknown is sized on the spot: 1 KB lets it through
    monkeypatch.setattr(dlm, "_remote_size", lambda url: 1024.0)
    monkeypatch.setattr(dlm, "_download_zip_to_cache", no_zip)
    monkeypatch.setattr(dlm, "_download_one", fake_one)

    dl = download_repo_files(
        make_files([math.nan], "https://github.com/owner/repo-unsized"),
        max_file_size=10,
        max_download_size=100,
    )
    assert any("archive size unknown" in m for m in messages)
    assert not pd.isna(dl["file_location"].iloc[0])

    # a 0.5 MB wanted file next to a 5 MB one the per-file cap skips: the
    # archive would hold both (5.5 MB), over 2x the 1 MB budget
    dl = download_repo_files(
        make_files([0.5 * 1024 * 1024, 5 * 1024 * 1024], "https://github.com/owner/repo-big"),
        max_file_size=1,
        max_download_size=1,
    )
    assert any("exceeds 2x the 1 MB budget" in m for m in messages)
    assert not pd.isna(dl["file_location"].iloc[0])
    assert len(fallback) == 2


def _dryad_files(n: int, doi: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": [f"https://doi.org/{doi}"] * n,
            "file_name": [f"f{i}.csv" for i in range(1, n + 1)],
            "file_path": [f"f{i}.csv" for i in range(1, n + 1)],
            "file_url": [
                f"https://datadryad.org/api/v2/files/{i}/download" for i in range(1, n + 1)
            ],
            "file_size": [1024.0] * n,
            "file_location": pd.Series([None] * n, dtype="string"),
        }
    )


def test_dryad_small_dataset_skips_zip(
    monkeypatch: pytest.MonkeyPatch, messages: list[str]
) -> None:
    import metacheck.archives.dryad as dryad

    def no_zip(*a: Any, **k: Any) -> Any:
        raise AssertionError("zip transport should not be called for a 3-file Dryad dataset")

    def fake_one(
        url: str, dest: str, skip_on_api_limit: bool = False, expected_bytes: float = math.nan
    ) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("x\n")

    # the listed sizes (3 KB in total) pass the size test: the skip is the
    # file-count quota rule's
    monkeypatch.setattr(dryad, "_dryad_doi", lambda x: "10.5061/dryad.testquota1")
    monkeypatch.setattr(dlm, "_download_zip_to_cache", no_zip)
    monkeypatch.setattr(dlm, "_download_one", fake_one)
    dl = download_repo_files(
        _dryad_files(3, "10.5061/dryad.testquota1"), max_file_size=10, max_download_size=100
    )
    assert any("only 3 files wanted" in m for m in messages)
    assert dl["file_location"].notna().all()


def test_dryad_larger_dataset_uses_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    import metacheck.archives.dryad as dryad

    seen: dict[str, Any] = {}

    def fake_zip(files: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
        seen["zip_url"] = zip_url
        return _fill_zip(files, row_idx, zip_url)

    monkeypatch.setattr(dryad, "_dryad_doi", lambda x: "10.5061/dryad.testquota2")
    monkeypatch.setattr(dlm, "_download_zip_to_cache", fake_zip)
    dl = download_repo_files(
        _dryad_files(16, "10.5061/dryad.testquota2"), max_file_size=10, max_download_size=100
    )
    assert dl["file_location"].notna().all()
    assert seen["zip_url"] == (
        "https://datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.testquota2/download"
    )


def test_osf_zenodo_and_dataverse_are_downloaded_file_by_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # metacheck #424: their whole-record archives never report a size, so the
    # archive route for them never ran and was removed
    files = pd.DataFrame(
        {
            "repo_url": [
                "https://osf.io/fghij",
                "https://doi.org/10.5281/zenodo.424424",
                "https://doi.org/10.7910/DVN/ABCDEF",
            ],
            "file_name": ["a.csv", "b.csv", "c.csv"],
            "file_path": ["a.csv", "b.csv", "c.csv"],
            "file_url": [
                "https://files.osf.io/v1/resources/fghij/providers/osfstorage/a.csv",
                "https://zenodo.org/api/records/424424/files/b.csv/content",
                "https://dataverse.harvard.edu/api/access/datafile/1",
            ],
            "file_size": [1024.0] * 3,
            "file_location": pd.Series([None] * 3, dtype="string"),
        }
    )

    def no_zip(*a: Any, **k: Any) -> Any:
        raise AssertionError("no archive should be requested for OSF, Zenodo or Dataverse")

    def write(dest: str) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00")

    def fake_parallel(urls: list[str], dests: list[str], *a: Any, **kw: Any) -> list[None]:
        for d in dests:
            write(d)
        return [None] * len(urls)

    monkeypatch.setattr(dlm, "_download_zip_to_cache", no_zip)
    monkeypatch.setattr(dlm, "_download_many_parallel", fake_parallel)
    monkeypatch.setattr(dlm, "_download_one", lambda url, dest, **kw: write(dest))
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    assert dl["file_location"].notna().all()


def test_download_many_parallel_errors_and_truncation_retry(tmp_path: Path) -> None:
    d1 = tmp_path / "src.csv"
    d1.write_text("a,b\n1,2\n")
    ok_url = "file:///" + str(d1)
    bad_url = "file:///nonexistent/never/there.csv"
    dests = [str(tmp_path / "out" / "ok.csv"), str(tmp_path / "out" / "bad.csv")]
    errs = _download_many_parallel([ok_url, bad_url], dests)
    assert errs[0] is None
    assert Path(dests[0]).exists()
    assert errs[1] is not None
    assert not Path(dests[1]).exists()

    errs2 = _download_many_parallel(
        [ok_url], [str(tmp_path / "ok2.csv")], expected_size=d1.stat().st_size + 1
    )
    assert errs2[0] is not None and errs2[0].startswith("truncated (")
    assert _download_many_parallel([], []) == []
    assert _download_many_parallel([None], [str(tmp_path / "x")]) == ["bad URL"]


def test_parallel_routing_is_per_file(monkeypatch: pytest.MonkeyPatch) -> None:
    files = pd.DataFrame(
        {
            "repo_url": [
                "https://osf.io/abcde",
                "https://osf.io/abcde",
                "https://doi.org/10.5281/zenodo.123456",
            ],
            "file_name": ["a.csv", "b.csv", "c.csv"],
            "file_path": ["a.csv", "b.csv", "c.csv"],
            "file_url": [
                "https://files.osf.io/v1/resources/abcde/providers/osfstorage/a.csv",
                "https://files.osf.io/v1/resources/abcde/providers/dropbox/b.csv",
                "https://zenodo.org/api/records/123456/files/c.csv/content",
            ],
            "file_size": [1024.0] * 3,
            "file_location": pd.Series([None] * 3, dtype="string"),
        }
    )
    parallel_urls: list[str] = []
    sequential_urls: list[str] = []

    def fake_parallel(
        urls: list[str], dests: list[str], expected_size: Any = None, **kw: Any
    ) -> list[None]:
        parallel_urls.extend(urls)
        for d in dests:
            Path(d).parent.mkdir(parents=True, exist_ok=True)
            Path(d).write_bytes(b"\x00")
        return [None] * len(urls)

    def fake_one(url: str, dest: str, **kw: Any) -> None:
        sequential_urls.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x00")

    monkeypatch.setattr(dlm, "_download_many_parallel", fake_parallel)
    monkeypatch.setattr(dlm, "_download_one", fake_one)
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    assert set(parallel_urls) == {files["file_url"].iloc[0], files["file_url"].iloc[2]}
    assert sequential_urls == [files["file_url"].iloc[1]]
    assert dl["file_location"].notna().all()


# rate-limit handling ------------------------------------------------------------


def mk_ratelimit_resp(
    remaining: str, reset: str, prefix: str = "ratelimit", url: str = "https://x.org/a"
) -> httpx.Response:
    return httpx.Response(
        429,
        headers={f"{prefix}-remaining": remaining, f"{prefix}-reset": reset},
        request=httpx.Request("GET", url),
    )


def test_rate_limit_wait_reads_both_header_families() -> None:
    future = str(round(time.time()) + 900)
    assert round(_rate_limit_wait(mk_ratelimit_resp("0", future, "ratelimit"))) == 900
    assert round(_rate_limit_wait(mk_ratelimit_resp("0", future, "x-ratelimit"))) == 900


def test_rate_limit_wait_na_unless_confirmed_exhausted() -> None:
    future = str(round(time.time()) + 900)
    assert math.isnan(_rate_limit_wait(mk_ratelimit_resp("5", future)))
    assert math.isnan(_rate_limit_wait(httpx.Response(429)))
    assert math.isnan(_rate_limit_wait(mk_ratelimit_resp("0", "not-a-number")))


def test_rate_limit_wait_clamps_past_reset() -> None:
    past = str(round(time.time()) - 60)
    assert _rate_limit_wait(mk_ratelimit_resp("0", past)) == 0


def test_retry_after_factory_skip_returns_na(messages: list[str]) -> None:
    resp = mk_ratelimit_resp("0", str(round(time.time()) + 900), url="https://after-a.invalid/x")
    assert round(_storage_retry_after_factory(False)(resp)) == 900
    assert any("Rate limit reached from after-a.invalid; waiting 15.0 min" in m for m in messages)
    assert math.isnan(_storage_retry_after_factory(True)(resp))
    with local_options({"metacheck.skip_on_api_limit": True}):
        assert math.isnan(_storage_retry_after_factory(False)(resp))
    with http.skip_on_api_limit():
        assert math.isnan(_storage_retry_after_factory(False)(resp))


def test_host_rate_limit_round_trip() -> None:
    host = "test-ratelimit-roundtrip.invalid"
    _host_rate_limit_record(host, 5)
    remaining = _host_rate_limit_remaining(host)
    assert 4 < remaining <= 5
    # shared with metacheck.http's per-host memory
    assert http.host_reset_at(host) is not None


def test_host_rate_limit_later_reset_wins() -> None:
    host = "test-ratelimit-later-wins.invalid"
    _host_rate_limit_record(host, 100)
    _host_rate_limit_record(host, 1)
    assert _host_rate_limit_remaining(host) > 90
    _host_rate_limit_record(host, 200)
    assert _host_rate_limit_remaining(host) > 190


def test_host_rate_limit_unseen_and_expired() -> None:
    assert math.isnan(_host_rate_limit_remaining("test-ratelimit-unseen.invalid"))
    assert math.isnan(_host_rate_limit_remaining(None))
    host = "test-ratelimit-expiring.invalid"
    _host_rate_limit_record(host, 0.1)
    time.sleep(0.15)
    assert math.isnan(_host_rate_limit_remaining(host))
    assert math.isnan(_host_rate_limit_remaining(host))
    _host_rate_limit_record(host, math.nan)  # an unconfirmed wait records nothing
    assert math.isnan(_host_rate_limit_remaining(host))


def test_wait_out_known_rate_limit_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTACHECK_NO_SLEEP", raising=False)
    host = "test-ratelimit-wait.invalid"
    _host_rate_limit_record(host, 0.2)
    t0 = time.perf_counter()
    assert _wait_out_known_rate_limit(f"https://{host}/x") is True
    assert time.perf_counter() - t0 >= 0.15


def test_wait_out_known_rate_limit_clean_host_and_skip() -> None:
    t0 = time.perf_counter()
    assert _wait_out_known_rate_limit("https://test-ratelimit-clean.invalid/x") is True
    assert time.perf_counter() - t0 < 0.1
    host = "test-ratelimit-skip.invalid"
    _host_rate_limit_record(host, 5)
    t0 = time.perf_counter()
    assert _wait_out_known_rate_limit(f"https://{host}/x", skip_on_api_limit=True) is False
    assert time.perf_counter() - t0 < 0.1


def test_retry_after_factory_records_host_reset(messages: list[str]) -> None:
    host = "test-ratelimit-factory-record.invalid"
    resp = mk_ratelimit_resp("0", str(round(time.time()) + 30), url=f"https://{host}/api/x")
    _storage_retry_after_factory(False)(resp)
    remaining = _host_rate_limit_remaining(host)
    assert 25 < remaining <= 31


def test_is_transient_factory() -> None:
    exhausted = mk_ratelimit_resp("0", str(round(time.time()) + 900))
    plain = httpx.Response(429)
    wait, skip = _storage_is_transient_factory(False), _storage_is_transient_factory(True)
    assert wait(exhausted) is True
    assert skip(exhausted) is False
    assert wait(plain) is True and skip(plain) is True
    assert wait(httpx.Response(503)) is True and skip(httpx.Response(503)) is True
    assert wait(httpx.Response(403)) is True
    assert skip(httpx.Response(200)) is False
    assert wait(httpx.Response(404)) is False


def test_skip_on_api_limit_is_recorded_in_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = make_dl_files(tmp_path)
    monkeypatch.setattr(
        dlm,
        "_download_one",
        lambda *a, **k: "API rate limit exhausted: HTTP 429 Too Many Requests.",
    )
    dl = download_repo_files(files, max_file_size=10, max_download_size=100, skip_on_api_limit=True)
    fa = dl.attrs["failed"]
    assert len(fa) == len(files)
    assert fa["error"].str.startswith("API rate limit exhausted:").all()


# -- the storage request policy over HTTP (respx) ---------------------------------


def test_storage_request_retries_transient_statuses() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://store.example.org/f").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(403),
                httpx.Response(200, content=b"ok"),
            ]
        )
        resp = _storage_request("GET", "https://store.example.org/f")
    assert resp.status_code == 200 and route.call_count == 3

    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://store.example.org/g").mock(return_value=httpx.Response(503))
        resp = _storage_request("GET", "https://store.example.org/g")
    assert resp.status_code == 503 and route.call_count == 3  # max_tries = 3

    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://store.example.org/h").mock(return_value=httpx.Response(404))
        assert _storage_request("GET", "https://store.example.org/h").status_code == 404
    assert route.call_count == 1


def test_storage_request_gives_up_on_exhausted_quota_when_skipping(messages: list[str]) -> None:
    reset = str(round(time.time()) + 3600)
    limited = httpx.Response(429, headers={"ratelimit-remaining": "0", "ratelimit-reset": reset})
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://quota.example.org/f").mock(return_value=limited)
        resp = _storage_request("GET", "https://quota.example.org/f", skip_on_api_limit=True)
    assert resp.status_code == 429 and route.call_count == 1
    with respx.mock(assert_all_called=False) as router:
        router.get("https://quota2.example.org/f").mock(return_value=limited)
        err = _download_one(
            "https://quota2.example.org/f", "/tmp/pc-never-written", skip_on_api_limit=True
        )
    assert err == "API rate limit exhausted: HTTP 429 Too Many Requests."


def test_storage_request_connection_failures_raise() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://down.example.org/f").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(dlm._RequestError, match="Failed to perform HTTP request"):
            _storage_request("GET", "https://down.example.org/f")
    assert route.call_count == 3


def test_download_one_statuses_and_login_page(tmp_path: Path) -> None:
    login = (DATA / "login.html").read_bytes()
    with respx.mock(assert_all_called=False) as router:
        router.get("https://osf.example.org/ok").mock(
            return_value=httpx.Response(200, content=b"a,b\n1,2\n")
        )
        router.get("https://osf.example.org/missing").mock(
            return_value=httpx.Response(404, content=b"nope")
        )
        router.get("https://osf.example.org/private").mock(
            return_value=httpx.Response(200, content=login)
        )
        router.get("https://osf.example.org/empty").mock(
            return_value=httpx.Response(200, content=b"")
        )
        assert _download_one("https://osf.example.org/ok", str(tmp_path / "ok.csv")) is None
        assert (tmp_path / "ok.csv").read_bytes() == b"a,b\n1,2\n"
        assert (
            _download_one("https://osf.example.org/missing", str(tmp_path / "m.csv"))
            == "HTTP 404 Not Found."
        )
        assert not (tmp_path / "m.csv").exists()
        err = _download_one("https://osf.example.org/private", str(tmp_path / "p.csv"))
        assert err == "not authorised (the OSF returned a sign-in page; see ?osf_pat)"
        assert not (tmp_path / "p.csv").exists()
        assert (
            _download_one("https://osf.example.org/empty", str(tmp_path / "e.csv"))
            == "empty response"
        )


def test_is_login_page(tmp_path: Path) -> None:
    assert _is_login_page(str(DATA / "login.html")) is True
    assert _is_login_page(str(DATA / "page.html")) is False
    assert _is_login_page(str(DATA / "binary.dat")) is False
    assert _is_login_page(str(tmp_path / "missing.html")) is False


def test_auth_for_url_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "osf-token")
    spec = _auth_for_url({"method": "GET", "url": "https://osf.io/download/abcde/", "headers": {}})
    assert spec["headers"]["Authorization"] == "Bearer osf-token"
    assert spec["unrestricted_auth"] is True
    monkeypatch.delenv("OSF_PAT")
    with local_options({"metacheck.osf.pat": None}):
        spec = _auth_for_url(
            {"method": "GET", "url": "https://osf.io/download/abcde/", "headers": {}}
        )
    assert "Authorization" not in spec["headers"]
    spec = _auth_for_url({"method": "GET", "url": "https://example.org/x", "headers": {"A": "b"}})
    assert spec == {"method": "GET", "url": "https://example.org/x", "headers": {"A": "b"}}
    spec = _auth_for_url({"method": "GET", "url": "https://data.4tu.nl/file/1", "headers": {}})
    assert spec["headers"]["User-Agent"] == "metacheck"
    spec = _auth_for_url(
        {"method": "GET", "url": "https://reshare.ukdataservice.ac.uk/1/x", "headers": {}}
    )
    assert spec["headers"]["User-Agent"] == "metacheck"


@pytest.mark.parametrize(
    "url",
    [
        "https://raw.githubusercontent.com/someone/osf.io/main/d.csv",
        "https://attacker.example/osf.io/data.csv",
        "https://osf.io.attacker.example/d.csv",
        "https://notosf.io/d.csv",
    ],
)
def test_the_osf_token_goes_to_osf_hosts_only(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("OSF_PAT", "osf-token")
    spec = _auth_for_url({"method": "GET", "url": url, "headers": {}})
    assert "Authorization" not in spec["headers"] and "unrestricted_auth" not in spec
    for host in ("osf.io", "files.osf.io", "files.de-1.osf.io", "api.osf.io"):
        spec = _auth_for_url({"method": "GET", "url": f"https://{host}/x", "headers": {}})
        assert spec["headers"]["Authorization"] == "Bearer osf-token"


def test_unrestricted_auth_keeps_token_across_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OSF_PAT", "osf-token")
    seen: list[str | None] = []

    def storage(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, content=b"private bytes")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://osf.io/download/abcde/").mock(
            return_value=httpx.Response(
                302, headers={"Location": "https://files.de-1.osf.io/v1/abcde"}
            )
        )
        router.get("https://files.de-1.osf.io/v1/abcde").mock(side_effect=storage)
        assert _download_one("https://osf.io/download/abcde/", str(tmp_path / "f.bin")) is None
    assert seen == ["Bearer osf-token"]
    assert (tmp_path / "f.bin").read_bytes() == b"private bytes"


# -- whole-repository zips over HTTP (respx) ---------------------------------------


def _zip_bytes(members: dict[str, bytes], top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(top + name, data)
    return buf.getvalue()


def _cache_files(tmp_path: Path, paths: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": ["https://github.com/o/r"] * len(paths),
            "file_name": [Path(p).name for p in paths],
            "file_path": paths,
            "file_url": ["u"] * len(paths),
            "file_location": pd.Series([None] * len(paths), dtype=object),
            ".cache_path": [str(tmp_path / "cache" / p) for p in paths],
        }
    )


def test_download_zip_to_cache_extracts_selected_rows(tmp_path: Path) -> None:
    data = _zip_bytes({"a.csv": b"1\n", "sub/b.R": b"x <- 1\n", "c.txt": b""}, top="o-r-sha/")
    files = _cache_files(tmp_path, ["a.csv", "sub/b.R", "missing.csv", "c.txt"])
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/o/r/zipball").mock(
            return_value=httpx.Response(200, content=data)
        )
        out = _download_zip_to_cache(
            files, [0, 1, 2, 3], "https://api.github.com/repos/o/r/zipball", strip_dir=True
        )
    assert out["file_location"].tolist()[:2] == files[".cache_path"].tolist()[:2]
    assert out["file_location"].isna().tolist()[2:] == [True, True]  # not in the zip / empty member
    assert Path(out["file_location"].iloc[1]).read_bytes() == b"x <- 1\n"
    assert files["file_location"].isna().all()  # input untouched


def test_download_zip_to_cache_caps_and_failures(tmp_path: Path, messages: list[str]) -> None:
    data = _zip_bytes({"a.csv": bytes(range(256)) * 400})
    files = _cache_files(tmp_path, ["a.csv"])
    url = "https://zip.example.org/archive.zip"
    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(return_value=httpx.Response(200, content=data))
        out = _download_zip_to_cache(files, [0], url, max_bytes=10)
    assert out["file_location"].isna().all()
    assert (
        messages[-1]
        == f"Zip download failed ({url}): archive exceeded the 0 MB cap during download and was aborted"
    )

    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(return_value=httpx.Response(200, content=b"not a zip"))
        assert _download_zip_to_cache(files, [0], url)["file_location"].isna().all()

    reset = str(round(time.time()) + 30)
    limited = httpx.Response(
        429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url).mock(
            side_effect=[limited, httpx.Response(200, content=_zip_bytes({"a.csv": b"1\n"}))]
        )
        out = _download_zip_to_cache(files, [0], url)
    assert route.call_count == 2 and not pd.isna(out["file_location"].iloc[0])
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url).mock(return_value=limited)
        out = _download_zip_to_cache(files, [0], url, skip_on_api_limit=True)
    assert route.call_count == 1 and out["file_location"].isna().all()


def test_download_repo_files_github_zip_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.archives.github as gh

    data = _zip_bytes({"README.md": b"# r\n", "code/run.R": b"1\n"}, top="o-r-abc123/")
    files = pd.DataFrame(
        {
            "repo_url": ["https://github.com/o/r"] * 2,
            "file_name": ["README.md", "run.R"],
            "file_path": ["README.md", "code/run.R"],
            "file_url": ["https://raw.example.org/README.md", "https://raw.example.org/code/run.R"],
            "file_size": [4.0, 2.0],
        }
    )
    monkeypatch.setattr(gh, "github_repo", lambda repo: "o/r")
    with respx.mock(assert_all_called=False) as router:
        router.head("https://api.github.com/repos/o/r/zipball").mock(
            return_value=httpx.Response(200)
        )
        router.get("https://api.github.com/repos/o/r/zipball").mock(
            return_value=httpx.Response(200, content=data)
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # no transport-size warnings expected
            dl = download_repo_files(files)
    assert dl["file_location"].notna().all()
    assert Path(dl["file_location"].iloc[1]).read_bytes() == b"1\n"
    assert dl.attrs["failed"].empty


def test_empty_and_null_input() -> None:
    assert download_repo_files(None) is None
    empty = pd.DataFrame({"repo_url": [], "file_url": []})
    assert download_repo_files(empty) is empty


def test_archive_member_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import metacheck.archives.zip_peek as zp

    calls: list[tuple[str, list[str]]] = []

    def fake_fetch(
        url: str, names: list[str], dest: str, verify: bool = True, **kw: Any
    ) -> pd.DataFrame:
        calls.append((url, list(names)))
        paths = []
        for n in names:
            p = Path(dest) / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
            paths.append(str(p))
        return pd.DataFrame(
            {"name": names, "path": paths, "size": [1.0] * len(names), "ok": [True] * len(names)}
        )

    monkeypatch.setattr(zp, "_zip_fetch_members", fake_fetch)
    files = pd.DataFrame(
        {
            "repo_url": ["https://osf.io/abcde"] * 3,
            "file_name": ["a.csv", "b.csv", "huge.csv"],
            "file_path": ["x.zip/a.csv", "x.zip/b.csv", "x.zip/huge.csv"],
            "file_url": [None, None, None],
            "file_size": [10.0, 20.0, 5e9],
            "archive_url": ["https://files.example.org/x.zip"] * 3,
            "archive_member": ["a.csv", "dir/b.csv", "huge.csv"],
        }
    )
    dl = download_repo_files(files)
    assert calls == [("https://files.example.org/x.zip", ["a.csv", "dir/b.csv"])]
    assert dl["file_location"].notna().tolist() == [True, True, False]
    loc = dl["file_location"].iloc[0].replace(os.sep, "/")
    assert "/.archive_members/files.example.org_x.zip.contents/" in loc
    assert dl.attrs["oversize_skipped"]["file_name"].tolist() == ["huge.csv"]


# -- #429: archive-member failures are recorded --------------------------------


def _archive_member_files() -> pd.DataFrame:
    repo = "https://example.org/repo-test-" + secrets.token_hex(6)
    return pd.DataFrame(
        {
            "repo_url": [repo],
            "file_name": ["member.R"],
            "file_path": ["data/member.R"],
            "file_url": pd.Series([None], dtype="string"),
            "file_size": [100.0],
            "file_location": pd.Series([None], dtype="string"),
            "archive_url": [repo + "/archive.zip"],
            "archive_member": ["member.R"],
            "paper_id": ["p.1"],
        }
    )


def test_failed_archive_member_fetch_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    import metacheck.archives.zip_peek as zp

    def boom(url: str, names: list[str], dest: str, **kw: Any) -> Any:
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(zp, "_zip_fetch_members", boom)
    dl = download_repo_files(_archive_member_files())
    assert pd.isna(dl["file_location"].iloc[0])
    fa = dl.attrs["failed"]
    assert len(fa) == 1
    assert "simulated network failure" in fa["error"].iloc[0]
    assert fa["file_name"].tolist() == ["member.R"]
    assert fa["paper_id"].tolist() == ["p.1"]


def test_member_not_ok_is_recorded_with_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    import metacheck.archives.zip_peek as zp

    def not_ok(url: str, names: list[str], dest: str, **kw: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "name": names,
                "path": [None] * len(names),
                "size": [100.0] * len(names),
                "ok": [False] * len(names),
                "error": ["CRC32 mismatch (corrupt download)"] * len(names),
            }
        )

    monkeypatch.setattr(zp, "_zip_fetch_members", not_ok)
    dl = download_repo_files(_archive_member_files())
    assert pd.isna(dl["file_location"].iloc[0])
    fa = dl.attrs["failed"]
    assert len(fa) == 1
    assert "CRC32 mismatch" in fa["error"].iloc[0]


def test_member_fetched_fills_file_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.archives.zip_peek as zp

    extracted = tmp_path / "member.R"
    extracted.write_text("ok\n")

    def ok(url: str, names: list[str], dest: str, **kw: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "name": names,
                "path": [str(extracted)] * len(names),
                "size": [100.0] * len(names),
                "ok": [True] * len(names),
                "error": [None] * len(names),
            }
        )

    monkeypatch.setattr(zp, "_zip_fetch_members", ok)
    dl = download_repo_files(_archive_member_files())
    assert dl["file_location"].iloc[0] == str(extracted)
    assert len(dl.attrs["failed"]) == 0
