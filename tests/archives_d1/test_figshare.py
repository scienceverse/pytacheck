"""Tests for metacheck.archives.figshare (port of tests/testthat/test-archive-figshare.R, plus more).

The R tests for ``.figshare_project_articles()`` and ``figshare_info()`` query
the live API; here they run against the recorded responses in ``mocks/``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives import figshare
from metacheck.archives.figshare import (
    _figshare_collection_articles,
    _figshare_collection_id,
    _figshare_doi_prefix_hosts,
    _figshare_headers,
    _figshare_id,
    _figshare_project_articles,
    _figshare_project_id,
    _figshare_vanity_hosts,
    _figshare_verify_downloads,
    figshare_file_download,
    figshare_info,
    figshare_links,
    figshare_pat,
)

# --------------------------------------------------------------------------- testthat ports


def test_figshare_links_finds_article_project_and_share_urls() -> None:
    paper = pc.test_paper(
        url=[
            "https://figshare.com/articles/dataset/some_title/18093368",
            "https://figshare.com/projects/PERICLES_-_Heritage_values/133332",
            "https://figshare.com/s/5e01cc0cae4cf3e2e14f",
            "https://osf.io/abcde",
        ]
    )
    links = figshare_links(paper)
    assert len(links) == 3
    by_id = dict(zip(links["href"], links["figshare_id"], strict=True))
    assert by_id["https://figshare.com/articles/dataset/some_title/18093368"] == "18093368"
    assert pd.isna(by_id["https://figshare.com/projects/PERICLES_-_Heritage_values/133332"])
    assert pd.isna(by_id["https://figshare.com/s/5e01cc0cae4cf3e2e14f"])
    flag = dict(zip(links["href"], links["figshare_unsupported"], strict=True))
    assert not flag["https://figshare.com/articles/dataset/some_title/18093368"]
    assert not flag["https://figshare.com/projects/PERICLES_-_Heritage_values/133332"]
    assert flag["https://figshare.com/s/5e01cc0cae4cf3e2e14f"]


def test_figshare_id() -> None:
    urls = [
        "18093368",
        "https://figshare.com/articles/dataset/some_title/18093368",
        "https://figshare.com/articles/18093368",
        "https://doi.org/10.6084/m9.figshare.18093368",
        "10.6084/m9.figshare.18093368.v1",
        "https://ndownloader.figshare.com/files/12345",
        "https://figshare.com/projects/some_project/133332",
        "https://figshare.com/s/5e01cc0cae4cf3e2e14f",
        "not-a-figshare-url",
        "",
        "https://figshare.com/articles/PxW_dataset/6934484",
        "https://doi.org/10.26180/19095317.v1",
        "https://doi.org/10.26188/14122688",
        "https://doi.org/10.25375/uct.14618526.v1",
        # two chained sub-prefix segments before the id (metacheck #439)
        "https://doi.org/10.17608/k6.auckland.25808182.v2",
        "https://doi.org/10.15131/shef.data.13712533",
    ]
    assert _figshare_id(urls) == [
        "18093368", "18093368", "18093368", "18093368", "18093368",
        "12345", None, None, None, None,
        "6934484", "19095317", "14122688", "14618526",
        "25808182", "13712533",
    ]  # fmt: skip
    assert _figshare_id(None) == []


def test_figshare_links_institutional_prefix_without_host() -> None:
    hosts = _figshare_doi_prefix_hosts()
    assert hosts["10.26180"] == "bridges.monash.edu"
    assert hosts["10.26188"] == "melbourne.figshare.com"
    assert hosts["10.25375"] == "zivahub.uct.ac.za"
    assert "bridges.monash.edu" in _figshare_vanity_hosts()

    paper = pc.test_paper(
        ["Data from: ... Monash University. Dataset, https://doi.org/10.26180/19095317.v1."]
    )
    links = figshare_links(paper)
    assert len(links) == 1
    assert links["figshare_id"].tolist() == ["19095317"]
    assert links["figshare_unsupported"].tolist() == [False]


def test_figshare_project_id() -> None:
    urls = [
        "https://figshare.com/projects/PERICLES_-_Heritage_values/133332",
        "https://figshare.com/projects/A_Project_With-Punctuation.In.It/999",
        "https://figshare.com/articles/dataset/some_title/18093368",
        "not-a-figshare-url",
        "",
    ]
    assert _figshare_project_id(urls) == ["133332", "999", None, None, None]


def test_figshare_project_articles(mock_api: object) -> None:
    ids = _figshare_project_articles("133332")
    assert ids == ["18093368", "6934484"]
    assert all(i.isdigit() for i in ids)
    assert _figshare_project_articles("999") == []
    with pytest.warns(UserWarning, match="Figshare project 404404 could not be found"):
        assert _figshare_project_articles("404404") == []


def test_figshare_collection_id() -> None:
    urls = [
        "https://figshare.com/collections/Some_collection/8742785",
        "https://figshare.com/collections/Some_collection/8742785/",
        "https://figshare.le.ac.uk/collections/Leicester/77",
        "https://doi.org/10.6084/m9.figshare.c.6190228",
        "10.6084/m9.figshare.c.6190228.v2",
        "https://figshare.com/projects/some_project/133332",
        "https://doi.org/10.6084/m9.figshare.18093368",
        "",
        None,
    ]
    assert _figshare_collection_id(urls) == [
        "8742785", "8742785", "77", "6190228", "6190228", None, None, None, None,
    ]  # fmt: skip
    assert _figshare_collection_id("https://doi.org/10.6084/m9.figshare.c.42") == "42"


def test_figshare_collection_articles(mock_api: object) -> None:
    assert _figshare_collection_articles("8742785") == ["6934484", "18093368"]
    with pytest.warns(UserWarning, match="Figshare collection 404405 could not be found"):
        assert _figshare_collection_articles("404405") == []


def test_figshare_info_expands_a_collection(mock_api: object) -> None:
    url = "https://doi.org/10.6084/m9.figshare.c.8742785.v1"
    info = figshare_info(url)
    assert (info["figshare_url"] == url).all()
    assert sorted(info["figshare_id"].tolist()) == ["18093368", "6934484"]


def test_figshare_info_expands_a_project(mock_api: object) -> None:
    url = "https://figshare.com/projects/PERICLES_-_Heritage_values/133332"
    info = figshare_info(url)
    assert len(info) >= 1
    assert (info["figshare_url"] == url).all()
    assert info["figshare_id"].notna().all()
    assert info["doi"].notna().all()
    assert not info["figshare_id"].duplicated().any()


def test_figshare_info_single_article_one_row(mock_api: object) -> None:
    info = figshare_info("https://doi.org/10.6084/m9.figshare.18093368")
    assert len(info) == 1
    assert info["figshare_id"].tolist() == ["18093368"]
    assert info["authors"].iloc[0] == ["Jane Doe", None]
    assert info["license"].tolist() == ["CC BY 4.0"]


# --------------------------------------------------------------------------- more


def test_figshare_project_articles_paginates(serve: object) -> None:
    page1 = [{"id": i} for i in range(100)]
    page2 = [{"id": 100}, {"id": 5}]
    base = "https://api.figshare.com/v2/projects/7/articles?page={}&page_size=100"
    seen = serve(  # type: ignore[operator]
        {
            base.format(1): httpx.Response(200, json=page1),
            base.format(2): httpx.Response(200, json=page2),
        }
    )
    ids = _figshare_project_articles("7")
    assert ids == [str(i) for i in range(101)]
    assert len(seen) == 2


def test_figshare_info_unfound_parse_error_and_no_valid(mock_api: object) -> None:
    with pytest.warns(UserWarning, match="1234 could not be found on api.figshare.com"):
        info = figshare_info(["1234", "19095317", "https://figshare.com/s/abc"])
    assert info["error"].tolist()[:2] == ["unfound", "parse_error"]
    assert pd.isna(info["figshare_id"].iloc[2])
    none = figshare_info(["nope"])
    assert none.columns.tolist() == ["figshare_url", "figshare_id"]


def test_figshare_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"data\.4tu\.nl seems to be offline"):
        figshare_info("123", host="data.4tu.nl")


def test_figshare_info_other_host(serve: object, online: None) -> None:
    seen = serve(  # type: ignore[operator]
        {"https://data.4tu.nl/v2/articles/42": httpx.Response(200, json={"title": "4TU"})}
    )
    info = figshare_info("42", host="data.4tu.nl")
    assert info["title"].tolist() == ["4TU"]
    assert str(seen[0].url) == "https://data.4tu.nl/v2/articles/42"


def test_figshare_pat_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert figshare_pat() == ""
    spec = _figshare_headers(
        {"url": "https://api.figshare.com/v2/articles/1", "headers": {"A": "b"}}
    )
    assert spec["headers"] == {"A": "b", "User-Agent": "metacheck"}
    monkeypatch.setenv("FIGSHARE_PAT", "env-token")
    assert _figshare_headers({"headers": {}})["headers"]["Authorization"] == "token env-token"
    figshare_pat("opt-token")
    assert figshare_pat() == "opt-token"
    with pytest.raises(ValueError, match="single string"):
        figshare_pat(True)  # type: ignore[arg-type]


def test_figshare_file_download(mock_api: object, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="2 of 3 files from Figshare article 18093368"):
        dl = figshare_file_download("10.6084/m9.figshare.18093368.v1", download_to=str(tmp_path))
    assert dl is not None
    assert dl.columns.tolist() == list(figshare._FILE_COLUMNS)
    # the omitted (too large) video stays in the table, not downloaded (U36)
    assert dl["key"].tolist() == ["data.csv", "readme.txt", "video.mp4", "missing.csv"]
    assert dl["downloaded"].tolist() == [True, False, False, False]
    assert dl["path"].isna().tolist() == [False, False, True, True]
    assert (tmp_path / "figshare_18093368" / "data.csv").exists()
    assert not (tmp_path / "figshare_18093368" / "video.mp4").exists()


def test_figshare_file_download_folder_suffix(mock_api: object, tmp_path: Path) -> None:
    # U37: an existing "figshare_18093368" gives "figshare_18093368_1", then
    # "_2" (metacheck strips "_<digits>" from the name itself: "figshare_1")
    (tmp_path / "figshare_18093368").mkdir()
    with pytest.warns(UserWarning):
        dl = figshare_file_download("18093368", download_to=str(tmp_path))
    assert dl is not None and set(dl["folder"]) == {"figshare_18093368_1"}
    with pytest.warns(UserWarning):
        dl = figshare_file_download("18093368", download_to=str(tmp_path))
    assert dl is not None and set(dl["folder"]) == {"figshare_18093368_2"}


def test_figshare_file_download_nothing(mock_api: object, tmp_path: Path) -> None:
    assert figshare_file_download("https://figshare.com/projects/x/1", str(tmp_path)) is None
    assert figshare_file_download("6934484", str(tmp_path)) is None
    with pytest.warns(UserWarning, match="could not be found"):
        assert figshare_file_download("1234", str(tmp_path)) is None


def test_figshare_verify_downloads_hashes_without_type(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"x,y\n")
    files = pd.DataFrame(
        {
            "path": ["a.csv", "a.csv"],
            "size": [4.0, 4.0],
            "checksum": ["043212BB9834E334677E9C9659294BD4", "ffffffffffffffffffffffffffffffff"],
            "downloaded": [True, True],
            "extracted": pd.array([None, 3], dtype="Int64"),
        }
    )
    out = _figshare_verify_downloads(files, str(tmp_path))
    assert out["checksum_ok"].tolist() == [True, False]
    # an extracted zip row counts as downloaded whatever its checksum
    assert out["downloaded"].tolist() == [True, True]


@pytest.mark.parametrize(
    ("module", "fn", "prefilters"),
    [
        ("dryad", "dryad_links", ["_dryad_prefilter"]),
        ("figshare", "figshare_links", ["_figshare_prefilter"]),
        ("dataverse", "dataverse_links", ["_dataverse_prefilter", "_dataverse_doi_prefilter"]),
    ],
)
def test_link_prefilters_are_exact(
    module: str,
    fn: str,
    prefilters: list[str],
    psychsci: object,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_api: object,
) -> None:
    """Searching only prefiltered sentences gives exactly the full search's links.

    (``mock_api``: dataverse_links() resolves shared DOI prefixes through doi.org.)
    """
    import importlib

    from pandas.testing import assert_frame_equal

    from tests.archives_d1.make_review_cases import FUZZ_TEXT, FUZZ_URL

    mod = importlib.import_module(f"metacheck.archives.{module}")
    tricky = pc.test_paper(
        [
            *FUZZ_TEXT,
            "FIGSHARE.COM/articles/x/1 and Sub.Figshare.Le.Ac.UK/projects/p/2",
            "doi.org/10.26180/19095317.v1. and 10.6084/m9.figshare.1 and 10.6084/M9.FIGSHARE.2",
            "DATADRYAD.ORG/stash/dataset/doi:10.5061/DRYAD.X and 10.25338/B8N33J",
            "DataVerse.Harvard.EDU/dataset.xhtml?persistentId=doi:10.7910/DVN/X and 10.18167/DVN1/Y",
            "nothing here, e.g. example.org/x and 10.1234/abc",
        ],
        FUZZ_URL,
    )
    papers = [psychsci, pc.read(fixtures_dir / "problems" / "203020.json"), tricky]
    fast = [getattr(mod, fn)(p) for p in papers]
    for name in prefilters:
        monkeypatch.setattr(mod, name, lambda: None)
    for got, paper in zip(fast, papers, strict=True):
        assert_frame_equal(got, getattr(mod, fn)(paper))
    assert len(fast[2]) >= 10  # the generated shapes do produce links


# ---------------------------------------------------------------- review round 2


def test_figshare_file_download_keeps_an_empty_file(mock_api: object, tmp_path: Path) -> None:
    # U36: a zero-byte file is downloaded as one (metacheck aborts the download)
    out = figshare.figshare_file_download("700006", download_to=str(tmp_path))
    assert out is not None
    empty = out[out["size"] == 0]
    assert len(empty) >= 1 and empty["downloaded"].all()
    out = figshare.figshare_file_download(["700006", "700009"], download_to=str(tmp_path))
    assert out is not None
    assert set(out["figshare_id"]) == {"700006", "700009"}


def test_figshare_file_listing_given_as_an_object(mock_api: object, tmp_path: Path) -> None:
    # R: rec$files is a named list; lapply() visits its values
    out = figshare.figshare_file_download("700009", download_to=str(tmp_path))
    assert out is not None
    assert out["key"].tolist() == ["first.csv", "second.txt"]
    assert out["downloaded"].tolist() == [True, True]


def test_figshare_info_list_valued_fields(mock_api: object) -> None:
    out = figshare.figshare_info("700007")
    assert out["title"].iloc[0] == ["Only title"]
    assert out["license"].iloc[0] == ["CC BY"]
    with pytest.raises(ValueError, match="replacement has 2 rows, data has 1"):
        figshare.figshare_info("700008")
