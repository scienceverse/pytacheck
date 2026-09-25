"""Port of tests/testthat/test-import-papers.R, plus the RDS reader."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pandas as pd
import pytest

import pytacheck as pc
from parity.canonical import canonical
from parity.compare import Options, compare
from pytacheck.io.corpus import (
    _papers_cache_dir,
    _papers_release_assets,
    _read_rds,
    papers_available,
    papers_load,
    papers_metadata,
    papers_remove,
)
from tests.io.conftest import IO_FIXTURES, api

DOWNLOAD = re.compile(r"https://github\.com/scienceverse/retagtest/releases/download/.*")


def _serve(path: Path):
    return lambda _request: httpx.Response(200, content=path.read_bytes())


def test_cache_dir_is_shared_with_metacheck() -> None:
    assert _papers_cache_dir().parts[-2:] == ("metacheck", "papers")


def test_papers_available(cache_dir: Path) -> None:
    with api("apis"):
        avail = papers_available("scienceverse/papers")
    assert list(avail.columns) == ["name", "tag", "size_mb", "cached"]
    assert set(avail["name"]) == {"bmcmed", "plosmed", "collabra", "jdm"}
    assert (avail["size_mb"] > 0).all()
    assert avail["cached"].dtype == "boolean"
    # tags follow the {corpus}-{date} convention
    assert all(re.match(r"^[a-z]+-\d{4}-\d{2}-\d{2}$", t) for t in avail["tag"])


def test_papers_available_reflects_cache_state(cache_dir: Path) -> None:
    with api("apis"):
        avail = papers_available("scienceverse/papers")
        assert not avail["cached"].any()
        (cache_dir / "jdm.rds").touch()
        avail2 = papers_available("scienceverse/papers")
    cached = dict(zip(avail2["name"], avail2["cached"], strict=True))
    assert bool(cached["jdm"]) is True
    assert bool(cached["bmcmed"]) is False


def test_release_assets_newest_retag() -> None:
    with api("apis_papers_retag"):
        assets = _papers_release_assets("scienceverse/retagtest")
    demo = assets[assets["name"] == "demo.rds"]
    # demo-2026-06-18 was superseded by demo-2026-06-20: only the newer one is kept
    assert len(demo) == 1
    assert demo["tag"].iloc[0] == "demo-2026-06-20"
    assert demo["size"].iloc[0] == 2000
    # other family unaffected, non-rds assets excluded, empty releases add nothing
    assert "other.rds" in list(assets["name"])
    assert "manifest.csv" not in list(assets["name"])
    assert len(assets) == 2


def test_release_assets_no_releases() -> None:
    with api("apis_papers_empty"):
        assets = _papers_release_assets("scienceverse/norealeases")
    assert len(assets) == 0
    assert list(assets.columns) == ["name", "tag", "size", "download_url"]


def test_release_assets_unreachable_repo() -> None:
    with api("apis_papers_404"), pytest.raises(RuntimeError, match="GitHub API error"):
        _papers_release_assets("scienceverse/doesnotexist12345")


def test_papers_load_downloads(cache_dir: Path) -> None:
    rds = IO_FIXTURES / "fake_paperlist.rds"
    with api("apis_papers_retag", routes={("GET", DOWNLOAD): _serve(rds)}):
        result = papers_load("demo", repo="scienceverse/retagtest")
    assert result == {"info": {"title": ["fake paper"]}}
    assert not list(cache_dir.iterdir())  # not cached by default


def test_papers_load_returns_paperlist(cache_dir: Path) -> None:
    rds = IO_FIXTURES / "papers_small.rds"
    with api("apis_papers_retag", routes={("GET", DOWNLOAD): _serve(rds)}):
        result = papers_load("other", repo="scienceverse/retagtest")
    assert isinstance(result, pc.PaperList)
    assert result.names == ["0956797613520608", "0956797614522816", "to_err_is_human"]


def test_papers_load_caches(cache_dir: Path) -> None:
    rds = IO_FIXTURES / "fake_paperlist.rds"
    with api("apis_papers_retag", routes={("GET", DOWNLOAD): _serve(rds)}):
        result = papers_load("demo", repo="scienceverse/retagtest", cache=True)
    cache_path = cache_dir / "demo.rds"
    assert cache_path.exists()
    assert result == {"info": {"title": ["fake paper"]}}

    # a second call reuses the cached file without any request
    with api() as router:
        result2 = papers_load("demo", repo="scienceverse/retagtest", cache=True)
    assert router.calls.call_count == 0
    assert result2 == result


def test_papers_load_download_error(cache_dir: Path) -> None:
    fail = {("GET", DOWNLOAD): lambda _r: httpx.Response(503)}
    with api("apis_papers_retag", routes=fail), pytest.raises(RuntimeError, match="status 503"):
        papers_load("demo", repo="scienceverse/retagtest")


def test_papers_load_unknown_corpus() -> None:
    with api("apis"), pytest.raises(ValueError, match="not found in releases"):
        papers_load("not_a_real_corpus", repo="scienceverse/papers")


def test_papers_remove(cache_dir: Path) -> None:
    assert papers_remove("jdm") is False
    cache_path = cache_dir / "jdm.rds"
    cache_path.touch()
    assert papers_remove("jdm") is True
    assert not cache_path.exists()


def test_papers_metadata() -> None:
    with api("apis"):
        meta = papers_metadata("jdm")
    assert meta["@type"] == "Dataset"
    assert "Judgment and Decision Making" in meta["dc_title"]
    assert meta["dc_coverage"] == "2006-01-01/2022-12-31"
    # nested dc:provenance object is kept as a nested dict
    assert isinstance(meta["dc_provenance"], dict)
    assert meta["dc_provenance"]["conversion_tool"] == "GROBID 0.8"
    # list-valued fields stay lists
    assert "judgment" in meta["dc_subject"]
    assert len(meta["dc_format"]) > 1
    # colons in the dc:field names are replaced with underscores
    assert not any(":" in k for k in list(meta)[2:])


def test_papers_metadata_unknown_corpus() -> None:
    with api("apis"), pytest.raises(ValueError, match=r"No metadata\.json found"):
        papers_metadata("doesnotexist999")


# readRDS ----------------------------------------------------------------------------


def test_read_rds_paperlist_equals_json(fixtures_dir: Path) -> None:
    """A corpus RDS gives the same papers as reading the source files."""
    papers = _read_rds(IO_FIXTURES / "papers_small.rds")
    fx = fixtures_dir / "psychsci"
    direct = pc.read(
        [fx / "0956797613520608.json", fx / "0956797614522816.json", pc.demofile("xml")],
        schema_version=None,  # the RDS holds metacheck's read() of the TEI
    )
    # pytacheck's conversion of the TEI fixes metacheck bugs (U16, U17, U28), so
    # only the JSON papers are compared in full
    assert direct.names == papers.names
    ignore = {"to_err_is_human"}
    problems = compare(canonical(direct), canonical(papers), Options(ignore=ignore))
    assert problems == []


@pytest.mark.parametrize("name", ["demopaper_v2.rds", "edge_paperlist.rds"], ids=["xz-v2", "bzip2"])
def test_read_rds_compressions(name: str) -> None:
    obj = _read_rds(IO_FIXTURES / name)
    assert isinstance(obj, pc.Paper | pc.PaperList)


def test_read_rds_generic_objects() -> None:
    obj = _read_rds(IO_FIXTURES / "misc_objects.rds")
    assert obj["a"] == [1, 2, 3]
    assert isinstance(obj["b"], pd.Series)
    assert list(obj["b"].index) == ["x", "y"]
    assert obj["f"] == ["lo", "hi", None, "lo"] or list(obj["f"])[2] is pd.NA
    assert obj["s"] == ["é", None]
    assert list(obj["df"].columns) == ["n", "d"]
    assert str(obj["df"]["d"].iloc[0]) == "2020-01-01"
    assert obj["e"] == []


def test_read_rds_rejects_other_formats(tmp_path: Path) -> None:
    f = tmp_path / "ascii.rds"
    f.write_bytes(b"A\n3\n")
    with pytest.raises(ValueError, match=r"XDR"):
        _read_rds(f)
