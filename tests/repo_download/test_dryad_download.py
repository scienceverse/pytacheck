"""Dryad in download_repo_files() (metacheck f1062b4..dev: 8f2de4e, d52b3fd).

Ports the Dryad tests of tests/testthat/test-repo-download.R at metacheck's
dev: the bulk download for every Dryad DOI form, the dataset's own size
(``repo_size``) for the archive estimate, and the missing-key note on a 401.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import download as dlm
from metacheck.archives.download import (
    _archive_size_estimate,
    _download_many_parallel,
    _download_one,
    download_repo_files,
)
from metacheck.utils import local_options

_MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _isolated(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Throwaway caches, the cache notice marked as shown, and no Dryad key."""
    for var in ("DRYAD_PAT", "DRYAD_CLIENT_ID", "DRYAD_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    base = tmp_path_factory.mktemp("repo-cache")
    with local_options(
        {
            "metacheck.repo_cache.notified": True,
            "metacheck.repo_cache.dir": str(base / "persist"),
            "metacheck.repo_cache.session_dir": str(base / "session"),
            "metacheck.dryad.pat": None,
            "metacheck.dryad.client_id": None,
            "metacheck.dryad.client_secret": None,
        }
    ):
        yield


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(dlm, "_message", lambda *parts: out.append("".join(map(str, parts))))
    return out


def _dryad_files(repo: str, n: int = 16, repo_size: float | None = None) -> pd.DataFrame:
    files = pd.DataFrame(
        {
            "repo_url": [repo] * n,
            "file_name": [f"f{i}.csv" for i in range(1, n + 1)],
            "file_path": [f"f{i}.csv" for i in range(1, n + 1)],
            "file_url": [
                f"https://datadryad.org/api/v2/files/{i}/download" for i in range(1, n + 1)
            ],
            "file_size": [1024.0] * n,
            "file_location": pd.Series([None] * n, dtype="string"),
        }
    )
    if repo_size is not None:
        files["repo_size"] = [repo_size] * n
    return files


def _fake_zip(seen: list[str]) -> Any:
    def fake(files: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
        seen.append(zip_url)
        files = files.copy()
        files.loc[row_idx, "file_location"] = files.loc[row_idx, ".cache_path"]
        return files

    return fake


@pytest.mark.parametrize(
    ("repo", "doi"),
    [
        # metacheck at the pin matched only datadryad.org and doi.org/10.5061/dryad
        ("https://doi.org/10.25338/B8N33J", "10.25338%2Fb8n33j"),
        ("10.5061/dryad.testbare1", "10.5061%2Fdryad.testbare1"),
    ],
)
def test_the_dryad_bulk_download_is_used_for_every_dryad_doi_form(
    repo: str, doi: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(dlm, "_download_zip_to_cache", _fake_zip(seen))
    dl = download_repo_files(_dryad_files(repo), max_file_size=10, max_download_size=100)
    assert seen == [f"https://datadryad.org/api/v2/datasets/doi%3A{doi}/download"]
    assert dl["file_location"].notna().all()


def test_a_repository_without_a_dryad_doi_is_not_sent_to_dryad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(dlm, "_download_zip_to_cache", _fake_zip(seen))
    monkeypatch.setattr(dlm, "_download_one", lambda *a, **k: "HTTP 404")
    download_repo_files(_dryad_files("https://example.org/data"), max_download_size=100)
    assert seen == []


def test_archive_size_estimate_prefers_the_repository_total() -> None:
    repo = "https://doi.org/10.5061/dryad.x"
    files = _dryad_files(repo, n=3)
    assert _archive_size_estimate(files, repo) == 3 * 1024
    files["repo_size"] = [float("nan"), 5e6, 0.0]
    assert _archive_size_estimate(files, repo) == 5e6
    # another repository's total is not this one's
    assert _archive_size_estimate(files, "https://doi.org/10.5061/dryad.y") != 5e6


def test_dryad_archive_is_sized_by_the_whole_dataset(
    monkeypatch: pytest.MonkeyPatch, messages: list[str]
) -> None:
    # 16 wanted files of 1 KB from a 300 MB dataset: the archive holds the
    # whole dataset, over 2x the 100 MB budget, so the files go one by one
    seen: list[str] = []
    monkeypatch.setattr(dlm, "_download_zip_to_cache", _fake_zip(seen))

    def fake_one(url: str, dest: str, **kw: Any) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("x\n")

    monkeypatch.setattr(dlm, "_download_one", fake_one)
    files = _dryad_files("https://doi.org/10.5061/dryad.big", repo_size=300 * _MB)
    dl = download_repo_files(files, max_file_size=10, max_download_size=100)
    assert seen == []
    assert any("estimated archive size 300 MB exceeds 2x the 100 MB budget" in m for m in messages)
    assert dl["file_location"].notna().all()


def test_a_dryad_401_without_a_key_says_why(tmp_path: Path) -> None:
    dryad_url = "https://datadryad.org/api/v2/files/1/download"
    other_url = "https://example.com/file/1"
    with respx.mock(assert_all_called=False) as router:
        router.get(dryad_url).mock(return_value=httpx.Response(401))
        router.get(other_url).mock(return_value=httpx.Response(401))
        err = _download_one(dryad_url, str(tmp_path / "a"))
        assert err is not None and err.startswith(
            "HTTP 401 Unauthorized. (Dryad requires an API key"
        )
        assert _download_one(other_url, str(tmp_path / "b")) == "HTTP 401 Unauthorized."
        errs = _download_many_parallel(
            [dryad_url, other_url], [str(tmp_path / "c"), str(tmp_path / "d")]
        )
        assert errs[0] is not None and errs[0].startswith("HTTP 401 (Dryad requires an API key")
        assert errs[1] == "HTTP 401"
        # with a key set, a 401 has another cause
        with local_options({"metacheck.dryad.pat": "tok"}):
            assert _download_one(dryad_url, str(tmp_path / "e")) == "HTTP 401 Unauthorized."
