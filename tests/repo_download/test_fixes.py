"""metacheck bugs fixed in the repository download code (docs/UPSTREAM_ISSUES.md U72-U75).

The parity cases of these fixes are marked ``known_divergence``; these tests
pin the fixed behaviour.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import download as dlm
from metacheck.archives import zip_peek as zpm
from metacheck.archives.download import (
    _download_many_parallel,
    _download_one,
    _download_zip_to_cache,
    download_repo_files,
)
from metacheck.archives.zip_peek import zip_peek
from metacheck.utils import local_options


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    base = tmp_path_factory.mktemp("repo-cache")
    zpm._ZIP_PEEK_CACHE.clear()
    with local_options(
        {
            "metacheck.repo_cache.notified": True,
            "metacheck.repo_cache.dir": str(base / "persist"),
            "metacheck.repo_cache.session_dir": str(base / "session"),
        }
    ):
        yield
    zpm._ZIP_PEEK_CACHE.clear()


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(dlm, "_message", lambda *parts: out.append("".join(map(str, parts))))
    return out


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# -- U72: a HEAD answer without Content-Length -------------------------------------


def test_zip_peek_without_content_length_asks_for_the_tail() -> None:
    data = _zip_bytes({"a.csv": b"x,y\n1,2\n", "b/c.R": b"x <- 1\n"})
    url = "https://zip.example.org/nolength.zip"
    ranges: list[str] = []

    def get(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("Range", "")
        ranges.append(rng)
        assert rng.startswith("bytes=-")
        n = int(rng[len("bytes=-") :])
        return httpx.Response(206, content=data[-n:])

    with respx.mock(assert_all_called=False) as router:
        router.head(url).mock(return_value=httpx.Response(200))
        router.get(url).mock(side_effect=get)
        out = zip_peek(url)
    # metacheck: numeric(0) breaks its `||` test, and the peek fails (NULL)
    assert out is not None
    assert out["name"].tolist() == ["a.csv", "b/c.R"]
    assert ranges == ["bytes=-131072"]


# -- U73: the archive download --------------------------------------------------------


def _files(tmp_path: Path, names: list[str], with_path: bool = True) -> pd.DataFrame:
    cols: dict[str, Any] = {
        "repo_url": ["https://example.org/r"] * len(names),
        "file_name": names,
        "file_url": ["u"] * len(names),
        "file_location": pd.Series([None] * len(names), dtype=object),
        ".cache_path": [str(tmp_path / "cache" / n) for n in names],
    }
    if with_path:
        cols["file_path"] = names
    return pd.DataFrame(cols)


def test_zip_to_cache_without_a_file_path_column_matches_file_names(tmp_path: Path) -> None:
    # metacheck matches nothing (after downloading the whole archive)
    url = "https://zip.example.org/a.zip"
    data = _zip_bytes({"a.csv": b"1\n", "b.R": b"x\n"})
    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(return_value=httpx.Response(200, content=data))
        out = _download_zip_to_cache(
            _files(tmp_path, ["a.csv", "b.R"], with_path=False), [0, 1], url
        )
    assert out["file_location"].tolist() == out[".cache_path"].tolist()
    # nothing to match on at all: no download
    bare = _files(tmp_path, ["a.csv"], with_path=False).drop(columns="file_name")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url).mock(return_value=httpx.Response(200, content=data))
        _download_zip_to_cache(bare, [0], url)
    assert route.call_count == 0


def test_zip_timeout_message_with_a_fractional_timeout(
    tmp_path: Path, messages: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # metacheck's sprintf("%ds") fails on the size-scaled timeout, and that
    # error text becomes the reported failure
    url = "https://zip.example.org/slow.zip"
    clock = iter([0.0, 1000.0, 2000.0, 3000.0])
    monkeypatch.setattr(dlm.time, "monotonic", lambda: next(clock, 4000.0))
    data = _zip_bytes({"a.csv": b"1\n"})
    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(return_value=httpx.Response(200, content=data))
        _download_zip_to_cache(_files(tmp_path, ["a.csv"]), [0], url, timeout_s=12.5)
    assert messages[-1] == f"Zip download failed ({url}): archive download exceeded the 13s timeout"


# -- U74: the zip gates of download_repo_files() ------------------------------------------


_DRYAD_REPO = "https://doi.org/10.5061/dryad.u74"
_DRYAD_ZIP = "https://datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.u74/download"


def _dryad_rows(n: int) -> dict[str, list[Any]]:
    return {
        "repo_url": [_DRYAD_REPO] * n,
        "file_name": [f"f{i}.csv" for i in range(n)],
        "file_path": [f"f{i}.csv" for i in range(n)],
        "file_url": [f"https://datadryad.org/api/v2/files/{i}/download" for i in range(n)],
        "file_size": [10.0] * n,
    }


@pytest.mark.parametrize("dtype", [object, "string"])
def test_zip_gate_ignores_rows_without_a_repository(
    monkeypatch: pytest.MonkeyPatch, dtype: Any
) -> None:
    # metacheck: sum(files$repo_url == repo) is NA with any NA repo_url, so the
    # Dryad archive is skipped (the other hosts' archive branches are gone, #424)
    import metacheck.archives.dryad as dryad

    calls: list[str] = []

    def fake_zip(files: pd.DataFrame, row_idx: list[int], zip_url: str, **kw: Any) -> pd.DataFrame:
        calls.append(zip_url)
        for i in row_idx:
            files.iat[i, files.columns.get_loc("file_location")] = files[".cache_path"].iloc[i]
        return files

    monkeypatch.setattr(dryad, "_dryad_doi", lambda x: "10.5061/dryad.u74")
    monkeypatch.setattr(dlm, "_download_zip_to_cache", fake_zip)
    rows = _dryad_rows(13)
    rows["repo_url"].append(None)
    rows["file_name"].append("c.csv")
    rows["file_path"].append("c.csv")
    rows["file_url"].append(None)
    rows["file_size"].append(10.0)
    df = pd.DataFrame(rows)
    df["repo_url"] = df["repo_url"].astype(dtype)  # a string column holds pd.NA
    out = download_repo_files(df)
    assert calls == [_DRYAD_ZIP]
    assert out["file_location"].notna().tolist() == [True] * 13 + [False]


def test_stale_file_location_is_still_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # metacheck drops rows with any file_location from the remaining downloads
    import metacheck.archives.dryad as dryad

    monkeypatch.setattr(dryad, "_dryad_doi", lambda x: "10.5061/dryad.u74")
    monkeypatch.setattr(dlm, "_download_zip_to_cache", lambda files, *a, **k: files)  # zip fails
    fetched: list[str] = []

    def one(url: str, dest: str, **kw: Any) -> None:
        fetched.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("x\n")
        return None

    monkeypatch.setattr(dlm, "_download_one", one)
    monkeypatch.setattr(
        dlm,
        "_download_many_parallel",
        lambda urls, dests, *a, **k: [one(u, d) for u, d in zip(urls, dests, strict=True)],
    )
    rows = _dryad_rows(13)
    files = pd.DataFrame(rows)
    files["file_location"] = ["/stale/elsewhere/f0.csv"] + [None] * 12
    out = download_repo_files(files)
    assert "https://datadryad.org/api/v2/files/0/download" in fetched
    assert Path(out["file_location"].iloc[0]).read_text() == "x\n"


# -- U75: an empty answer -------------------------------------------------------------------


def test_empty_response_leaves_no_cache_file(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://osf.example.org/empty").mock(
            return_value=httpx.Response(200, content=b"")
        )
        dest = tmp_path / "e.csv"
        assert _download_one("https://osf.example.org/empty", str(dest)) == "empty response"
        # metacheck leaves the 0-byte file, which the next run takes as cached
        assert not dest.exists()
        # a file listed as empty is an empty file
        assert _download_one("https://osf.example.org/empty", str(dest), expected_bytes=0) is None
        assert dest.read_bytes() == b""
        dests = [str(tmp_path / "p1.csv"), str(tmp_path / "p2.csv")]
        errs = _download_many_parallel(["https://osf.example.org/empty"] * 2, dests, [5.0, 0.0])
    assert errs == ["empty response", None]
    assert not Path(dests[0]).exists()
    assert Path(dests[1]).read_bytes() == b""
