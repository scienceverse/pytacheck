"""Tests for pytacheck.archives.dataone (port of test-archive-dataone.R, plus more)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.dataone import (
    _dataone_host,
    _dataone_host_regex,
    _dataone_hosts,
    _dataone_info,
    _dataone_pid,
    dataone_info,
    dataone_links,
)

# --------------------------------------------------------------------------- testthat ports


def test_dataone_pid_handles_a_doi_suffix_containing_a_further_slash() -> None:
    # Regression test upstream: PISCO (data.piscoweb.org, DOI prefix 10.6085)
    # mints PIDs whose suffix itself contains a slash.
    assert callable(_dataone_pid)
    assert _dataone_pid("https://doi.org/10.6085/AA/marine_ltm.20.1") == (
        "doi:10.6085/AA/marine_ltm.20.1"
    )
    assert _dataone_pid("10.6085/AA/marine_ltm.20.1") == "doi:10.6085/AA/marine_ltm.20.1"
    # existing single-segment-suffix shapes still work
    assert _dataone_pid("https://doi.org/10.18739/A2GT5FG86") == "doi:10.18739/A2GT5FG86"
    assert _dataone_pid("10.5063/PG1Q4B") == "doi:10.5063/PG1Q4B"
    # a "doi:" marker in a landing-page URL takes priority over the bare-DOI branch
    assert (
        _dataone_pid("https://arcticdata.io/metacat/d1/mn/v2/view/doi:10.18739/A2GT5FG86")
        == "doi:10.18739/A2GT5FG86"
    )


def test_dataone_links_recognises_the_piscoweb_host_and_its_doi_prefix() -> None:
    assert callable(dataone_links)
    pisco = [h for h in _dataone_hosts() if h["host"] == "data.piscoweb.org"]
    assert len(pisco) == 1
    assert pisco[0]["doi_prefix"] == "10.6085"

    paper = pc.test_paper(
        text=[
            "uploaded to DataONE and are accessible here: "
            "https://doi.org/10.6085/AA/marine_ltm.20.1"
        ]
    )
    links = dataone_links(paper)
    assert len(links) == 1
    assert links["dataone_host"].tolist() == ["data.piscoweb.org"]
    assert links["dataone_pid"].tolist() == ["doi:10.6085/AA/marine_ltm.20.1"]


# --------------------------------------------------------------------------- more


def test_host_regex_and_vectorisation() -> None:
    assert _dataone_host_regex().startswith(r"arcticdata\.io|knb\.ecoinformatics\.org")
    assert _dataone_host([]) == []
    assert _dataone_host(None) == []
    assert _dataone_host(["10.5063/X", "", None, "https://example.org"]) == [
        "knb.ecoinformatics.org",
        None,
        None,
        None,
    ]
    # U40: a host name in the URL wins over a DOI prefix, so a KNB page citing
    # an Arctic Data Center DOI is KNB's (metacheck: arcticdata.io)
    assert (
        _dataone_host("https://knb.ecoinformatics.org/view/doi:10.18739/X1")
        == "knb.ecoinformatics.org"
    )
    assert _dataone_host("https://doi.org/10.18739/X1") == "arcticdata.io"
    # the host name is matched case-sensitively
    assert _dataone_host("ARCTICDATA.IO/view/x") is None


def test_links_add_host_and_pid_columns() -> None:
    paper = pc.test_paper(
        ["Data at https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86 and 10.5063/PG1Q4B."],
        ["https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86/", "https://osf.io/x"],
    )
    links = dataone_links(paper)
    assert list(links.columns[-3:]) == ["dataone_url", "dataone_host", "dataone_pid"]
    # the hyperlink (trailing slash stripped) and the bare mention are one row;
    # a sentence-final "." is not part of the DOI (U40: metacheck keeps it)
    assert links["dataone_pid"].tolist() == ["doi:10.18739/A2GT5FG86", "doi:10.5063/PG1Q4B"]


def test_pid_forms() -> None:
    # U40: the marked form allows a slash in the suffix, as the bare form does
    assert (
        _dataone_pid("https://search.dataone.org/view/doi:10.6085/AA/marine_ltm.20.1")
        == "doi:10.6085/AA/marine_ltm.20.1"
    )
    assert _dataone_pid("https://doi.org/10.6085/AA/marine_ltm.20.1") == "doi:10.6085/AA/marine_ltm.20.1"
    assert _dataone_pid("doi:10.5063/PG1Q4B.") == "doi:10.5063/PG1Q4B"
    assert _dataone_pid("10.5063/PG1Q4B") == "doi:10.5063/PG1Q4B"
    none = dataone_links(pc.test_paper(["nothing"], ["https://osf.io/x"]))
    assert len(none) == 0
    assert "dataone_pid" in none.columns


def test_private_info_parses_eml(mock_api: object) -> None:
    info = _dataone_info("doi:10.18739/A2GT5FG86", host="arcticdata.io")
    assert list(info.columns) == [
        "dataone_host", "dataone_pid", "title", "doi", "publication_date", "authors",
        "license", "files",
    ]  # fmt: skip
    assert info["title"].iloc[0] == "Soil temperature, Utqiagvik, Alaska, 2015-2020"
    # U40: a missing name part is left out (metacheck: "NA Smith"), and an
    # organisation-only creator is named by its organisation (metacheck: "NA NA")
    assert info["authors"].iloc[0] == [
        "Jane Doe",
        "Smith",
        "National Snow and Ice Data Center",
        "Blank Org",
        None,
    ]
    assert info["license"].iloc[0].startswith("This work is dedicated")
    files = info["files"].iloc[0]
    assert files[0] == {"key": "soil_temp.csv", "size": 20480.0, "pid": "urn%3Auuid%3Aabc-123"}
    assert files[1]["key"] == "readme.txt" and math.isnan(files[1]["size"])
    assert files[2] == {"key": None, "size": 1000.0, "pid": None}


def test_private_info_errors(mock_api: object) -> None:
    assert _dataone_info("doi:1/x", host="example.org")["error"].iloc[0] == "unknown_host"
    for pid in ("doi:10.5063/NOTEML", "doi:10.5063/BROKEN"):
        got = _dataone_info(pid, host="knb.ecoinformatics.org")
        assert got["error"].iloc[0] == "unsupported_metadata_format"
    with pytest.warns(UserWarning, match="could not be found on arcticdata.io"):
        got = _dataone_info("doi:10.18739/MISSING", host="arcticdata.io")
    assert got["error"].iloc[0] == "unfound"


def test_info_joins_results_back(mock_api: object) -> None:
    with pytest.warns(UserWarning):
        info = dataone_info(["10.18739/A2GT5FG86", "10.18739/MISSING", "https://example.org", None])
    assert info["dataone_url"].tolist() == [
        "10.18739/A2GT5FG86",
        "10.18739/MISSING",
        "https://example.org",
    ]
    assert info["error"].isna().tolist() == [True, False, True]
    assert pd.isna(info["dataone_host"].iloc[2])


def test_info_table_with_a_missing_url(mock_api: object) -> None:
    # U33: metacheck fails ("row names contain missing values")
    out = dataone_info(pd.DataFrame({"u": ["10.18739/A2GT5FG86", None]}))
    assert out["dataone_pid"].tolist()[0] == "doi:10.18739/A2GT5FG86"
    assert pd.isna(out["dataone_pid"].iloc[1])


def test_info_fetches_a_dataset_once(mock_api: object) -> None:
    # U35: two URLs for one dataset are one request and one row each
    urls = ["10.18739/A2GT5FG86", "https://doi.org/10.18739/A2GT5FG86."]
    out = dataone_info(urls)
    assert out["dataone_url"].tolist() == urls
    assert out["dataone_pid"].tolist() == ["doi:10.18739/A2GT5FG86"] * 2


def test_info_uses_the_listing_cache(mock_api: object) -> None:
    first = dataone_info("10.18739/A2GT5FG86", cache=True)
    from tests.httpmock import replay

    with replay("apis_papers_empty"):
        again = dataone_info("10.18739/A2GT5FG86", cache=True)
    assert again["title"].tolist() == first["title"].tolist()


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"dataone\.org seems to be offline"):
        dataone_info("10.18739/A2GT5FG86")
    # nothing valid: no connectivity check needed
    assert len(dataone_info("https://example.org")) == 1
