"""Concurrent metadata work preserves row order, gates and extracted bytes."""

import io
import math
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from metacheck.archives import download, osf, zip_peek
from metacheck.modules import _repo_check
from metacheck.utils import local_options


def test_osf_projects_overlap_but_keep_input_rows(monkeypatch):
    barrier = threading.Barrier(2)
    calls = []

    def listing(url, **kwargs):
        calls.append(url)
        barrier.wait(timeout=5)
        return pd.DataFrame({"name": [url]})

    monkeypatch.setattr(osf, "osf_info", listing)
    result = _repo_check._osf_listing(["abcde", "fghij", "abcde"], None, False)
    assert sorted(calls) == ["abcde", "fghij"]
    assert result["name"].tolist() == result["repo_name"].tolist() == ["abcde", "fghij", "abcde"]


def test_size_probes_overlap_and_duplicate_urls_share_one_probe(monkeypatch, tmp_path):
    barrier = threading.Barrier(2)
    calls = []

    def size(url):
        calls.append(url)
        barrier.wait(timeout=5)
        return 3.0

    def fetch(url, dest, **kwargs):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"abc")

    monkeypatch.setattr(download, "_remote_size", size)
    monkeypatch.setattr(download, "_download_one", fetch)
    files = pd.DataFrame(
        {
            "repo_url": ["https://example.org/project"] * 3,
            "file_url": [
                "https://files.example/a",
                "https://files.example/b",
                "https://files.example/a",
            ],
            "file_name": ["a.csv", "b.csv", "copy.csv"],
            "file_size": [math.nan] * 3,
        }
    )
    with local_options({"metacheck.repo_cache.session_dir": str(tmp_path)}):
        result = download.download_repo_files(files)
    assert sorted(calls) == ["https://files.example/a", "https://files.example/b"]
    assert result["file_name"].tolist() == files["file_name"].tolist()
    assert all(Path(p).read_bytes() == b"abc" for p in result["file_location"])
    assert files["file_size"].isna().all()


def test_refused_repository_needs_no_size_probes(monkeypatch, tmp_path):
    def forbidden(url):
        raise AssertionError("A refused repository should not make requests")

    monkeypatch.setattr(download, "_remote_size", forbidden)
    files = pd.DataFrame(
        {
            "repo_url": ["https://example.org/project"],
            "file_url": ["https://files.example/a"],
            "file_name": ["a.csv"],
            "file_size": [math.nan],
        }
    )
    with local_options({"metacheck.repo_cache.session_dir": str(tmp_path)}):
        result = download.download_repo_files(
            files, max_files_per_repo=2, repo_file_counts={"https://example.org/project": 10}
        )
    assert result["file_location"].isna().all()
    assert len(result.attrs["gated"]) == 1


def test_zip_members_reuse_valid_bytes_and_repair_corruption(monkeypatch, tmp_path):
    # Use zipfile's real size/CRC records; only the network-backed reader is mocked.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("data.csv", b"original bytes")
    raw = archive.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        listing = zip_peek._entry_table(zf.infolist())
    target = tmp_path / "data.csv"
    target.write_bytes(b"original bytes")
    calls = []

    def open_zip(*args, **kwargs):
        calls.append(args)
        return zipfile.ZipFile(io.BytesIO(raw)), SimpleNamespace(prefetch=lambda *args: None)

    monkeypatch.setattr(zip_peek, "zip_peek", lambda *a, **kw: listing)
    monkeypatch.setattr(zip_peek, "_open_zip", open_zip)
    first = zip_peek._zip_fetch_members("https://files.example/data.zip", dest=str(tmp_path))
    assert first["ok"].all()
    assert calls == []
    target.write_bytes(b"modified bytes")  # same length, different CRC
    repaired = zip_peek._zip_fetch_members("https://files.example/data.zip", dest=str(tmp_path))
    assert repaired["ok"].all()
    assert len(calls) == 1
    assert target.read_bytes() == b"original bytes"
