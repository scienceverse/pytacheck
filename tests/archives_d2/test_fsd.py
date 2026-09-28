"""Tests for pytacheck.archives.fsd (R/archive-fsd.R has no testthat file upstream)."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.fsd import _fsd_ddi_xml, _fsd_info, _fsd_study_id, fsd_info, fsd_links


def test_study_ids() -> None:
    assert _fsd_study_id("https://services.fsd.tuni.fi/catalogue/FSD2653?lang=en") == "FSD2653"
    assert _fsd_study_id("https://doi.org/10.60686/t-fsd2653") == "FSD2653"
    assert _fsd_study_id("urn.fi/urn:nbn:fi:fsd:T-FSD3136") == "FSD3136"
    assert _fsd_study_id("fsd_1234567") == "FSD123456"
    assert _fsd_study_id("FSD12") is None
    assert _fsd_study_id(None) is None
    # U44: a vector gives a list aligned with it (metacheck drops non-matches
    # and fails on two ids)
    assert _fsd_study_id(["x", "FSD1234"]) == [None, "FSD1234"]
    assert _fsd_study_id(["FSD1234", "FSD5678"]) == ["FSD1234", "FSD5678"]
    assert _fsd_study_id([]) == []


def test_links() -> None:
    paper = pc.test_paper(
        ["The data (FSD2653) are at services.fsd.tuni.fi/catalogue/FSD2653/."],
        ["https://doi.org/10.60686/t-fsd2653", "https://osf.io/x"],
    )
    assert fsd_links(paper)["href"].tolist() == [
        "https://doi.org/10.60686/t-fsd2653",
        "FSD2653",
    ]


def test_ddi_record_is_namespace_stripped(mock_api: object) -> None:
    doc = _fsd_ddi_xml("FSD2653")
    assert doc is not None
    assert doc.tag == "codeBook"
    assert _fsd_ddi_xml("FSD9999") is None


def test_private_info(mock_api: object) -> None:
    info = _fsd_info("https://doi.org/10.60686/t-fsd2653")
    assert info["FSD_title"].iloc[0] == "Finnish National Election Study 2011"  # not the DDI's own
    assert info["FSD_doi"].iloc[0] == "10.60686/t-fsd2653"
    assert info["FSD_access"].iloc[0].startswith("The dataset is (B)")
    files = info["files"].iloc[0]
    assert files["name"].tolist() == ["daF2653_eng.sav", "daF2653_fin.por"]
    assert files["format"].tolist()[0] == "SPSS" and pd.isna(files["format"].iloc[1])
    assert files["cases"].tolist() == [1298, 12]  # as.integer() truncates
    assert files["variables"].tolist()[0] == 312 and pd.isna(files["variables"].iloc[1])

    sparse = _fsd_info("FSD1111")
    assert sparse[["FSD_title", "FSD_doi", "FSD_access"]].isna().all(axis=None)
    assert len(sparse["files"].iloc[0]) == 0

    with pytest.warns(UserWarning, match="could not be found"):
        assert _fsd_info("FSD9999")["error"].iloc[0] == "unfound"
    with pytest.warns(UserWarning, match="is not a valid FSD study reference"):
        assert _fsd_info("https://example.org")["error"].iloc[0] == "unfound"


def test_info_table_and_early_stop(mock_api: object) -> None:
    info = fsd_info(["FSD2653", "https://doi.org/10.60686/t-fsd1111", None, "FSD2653"])
    assert info["fsd_url"].tolist() == ["FSD2653", "https://doi.org/10.60686/t-fsd1111"]
    with pytest.warns(UserWarning):
        stopped = fsd_info(["nope", "FSD2653"])
    assert "FSD_title" not in stopped.columns


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match="FSD"):
        fsd_info("FSD2653")
