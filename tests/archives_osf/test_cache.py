"""Tests for the cache-location and repository-listing-cache ports
(R/cache.R, R/repo-info-cache.R)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.archives.cache import (
    _format_big_mark,
    _metacheck_cache_root,
    _metacheck_cache_subdir,
    _metacheck_dir_size,
)
from pytacheck.archives.info_cache import (
    _repo_info_cache_dir,
    _repo_info_cache_get,
    _repo_info_cache_key,
    _repo_info_cache_path,
    _repo_info_cache_put,
    _repo_info_list_ok,
    _repo_info_ok,
    repo_info_cache,
    repo_info_cache_clear,
)
from pytacheck.utils import local_options


def test_cache_root_and_subdir(tmp_path: Path) -> None:
    with local_options({"metacheck.cache.dir": str(tmp_path)}):
        assert _metacheck_cache_root() == str(tmp_path)
        sub = _metacheck_cache_subdir(".metacheck_x")
        assert os.path.isdir(sub) and sub.endswith("/.metacheck_x")
        over = _metacheck_cache_subdir(".metacheck_x", override=str(tmp_path / "elsewhere"))
        assert over.endswith("/elsewhere") and os.path.isdir(over)
        assert _metacheck_cache_subdir(".metacheck_y", override="").endswith("/.metacheck_y")


def test_cache_root_defaults_to_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PYTACHECK_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    with local_options({"metacheck.cache.dir": None}):
        assert _metacheck_cache_root() == os.getcwd()


def test_dir_size(tmp_path: Path) -> None:
    assert _metacheck_dir_size(tmp_path / "missing") == 0
    assert _metacheck_dir_size(tmp_path) == 0
    (tmp_path / "a").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".hidden").write_bytes(b"123")
    assert _metacheck_dir_size(tmp_path) == 8


def test_format_big_mark() -> None:
    assert _format_big_mark(0) == "0"
    assert _format_big_mark(1234.5) == "1,234.5"
    assert _format_big_mark(1234567.1) == "1,234,567"


def test_metacheck_cache_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pytacheck.archives.download")
    pytest.importorskip("pytacheck.llm.cache")
    from pytacheck.archives.cache import metacheck_cache_info

    monkeypatch.delenv("METACHECK_LLM_CACHE_DIR", raising=False)
    with local_options({"metacheck.cache.dir": str(tmp_path)}):
        info = metacheck_cache_info()
    assert info["cache"].tolist() == ["repo_files", "repo_info", "llm"]
    assert list(info.columns) == ["cache", "path", "size_mb"]


def test_repo_info_cache_toggle() -> None:
    assert repo_info_cache() is False
    with local_options({"metacheck.repo_info.cache": None}):
        assert repo_info_cache(True) is True
        assert repo_info_cache() is True
        assert repo_info_cache(False) is False
        with pytest.raises(ValueError, match="TRUE or FALSE"):
            repo_info_cache("yes")  # type: ignore[arg-type]


def test_repo_info_cache_roundtrip(tmp_path: Path) -> None:
    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path / "info")}):
        assert _repo_info_cache_dir().endswith("/info")
        assert _repo_info_cache_get("dryad", "10.5061/x") is None
        value = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
        assert _repo_info_cache_put("dryad", "10.5061/x", value) is value
        assert Path(_repo_info_cache_path("dryad", "10.5061/x")).name == "dryad_10.5061_x.pkl"
        pd.testing.assert_frame_equal(_repo_info_cache_get("dryad", "10.5061/x"), value)
        Path(_repo_info_cache_path("osf", "bad")).write_bytes(b"not a pickle")
        assert _repo_info_cache_get("osf", "bad") is None
        (tmp_path / "info" / "other.txt").write_text("keep")
        assert repo_info_cache_clear() == 2
        assert (tmp_path / "info" / "other.txt").exists()


def test_repo_info_cache_key() -> None:
    assert _repo_info_cache_key("dryad", "10.5061/dryad.abc/def") == "dryad_10.5061_dryad.abc_def"
    assert _repo_info_cache_key("zenodo", None) == "zenodo_unknown"
    assert _repo_info_cache_key("x", "___") == "x"
    assert _repo_info_cache_key("", "") == "_unknown"
    assert _repo_info_cache_key("zenodo", float("nan")) == "zenodo_NA"


def test_repo_info_ok() -> None:
    assert _repo_info_ok(pd.DataFrame({"a": [1]})) is True
    assert _repo_info_ok(pd.DataFrame({"error": [None]})) is True
    assert _repo_info_ok(pd.DataFrame({"error": ["HTTP 404"]})) is False
    assert _repo_info_ok(None) is False
    assert _repo_info_ok(["x"]) is False
    assert _repo_info_list_ok({"gated": True}) is False
    assert _repo_info_list_ok({"gated": False}) is True
    assert _repo_info_list_ok({"files": []}) is True
    assert _repo_info_list_ok("x") is False
    assert _repo_info_list_ok(pd.DataFrame({"gated": [True]})) is False
