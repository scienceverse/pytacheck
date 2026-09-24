"""Tests for pytacheck.archives.mendeley (R/archive-mendeley.R has no testthat file upstream)."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.mendeley import (
    _mendeley_id,
    _mendeley_info,
    mendeley_info,
    mendeley_links,
)


def test_ids() -> None:
    assert _mendeley_id("vjtxybrc28") == "vjtxybrc28"
    assert _mendeley_id("https://doi.org/10.17632/vjtxybrc28.1") == "vjtxybrc28"
    assert _mendeley_id("https://data.mendeley.com/datasets/vjtxybrc28/2") == "vjtxybrc28"
    assert _mendeley_id(["has space", "", None]) == [None, None, None]


def test_links_add_a_doi_link() -> None:
    paper = pc.test_paper(
        ["Data: 10.17632/abc123.2."], ["https://data.mendeley.com/datasets/vjtxybrc28/1/"]
    )
    links = mendeley_links(paper)
    assert links["mendeley_id"].tolist() == ["vjtxybrc28", "abc123"]
    assert links["mendeley_link"].tolist() == [
        "https://doi.org/10.17632/vjtxybrc28",
        "https://doi.org/10.17632/abc123",
    ]


def test_private_info(mock_api: object) -> None:
    info = _mendeley_info("vjtxybrc28")
    assert info["doi"].iloc[0] == "10.17632/vjtxybrc28.1"
    assert info["authors"].iloc[0] == ["Ann Lee", "Solo", None]
    assert info["license"].iloc[0] == "Creative Commons Attribution 4.0"  # full name fallback
    assert info["files"].iloc[0][0]["filename"] == "survey.csv"
    assert _mendeley_info("short1")["license"].iloc[0] == "CC0"
    assert _mendeley_info("notjson")["error"].iloc[0] == "parse_error"
    with pytest.warns(UserWarning, match="zzz999 could not be found"):
        assert _mendeley_info("zzz999")["error"].iloc[0] == "unfound"
    # R: `rec$doi$id` on a string is an error
    with pytest.raises(TypeError, match=r"\$ operator is invalid for atomic vectors"):
        _mendeley_info("strdoi")


def test_info_table(mock_api: object) -> None:
    table = pd.DataFrame({"n": [1, 2], "u": ["short1", "http://x.org/a b"], "mendeley_id": "stale"})
    info = mendeley_info(table, id_col=2)
    assert list(info.columns[:4]) == ["n", "u", "mendeley_url", "mendeley_id"]
    assert info["mendeley_id"].tolist()[0] == "short1"
    assert pd.isna(info["mendeley_id"].iloc[1])
    assert info["title"].tolist()[0] == "Short licence"


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"data\.mendeley\.com seems to be offline"):
        mendeley_info("short1")
