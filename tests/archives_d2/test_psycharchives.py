"""Tests for pytacheck.archives.psycharchives (port of test-archive-psycharchives.R, plus more)."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.psycharchives import (
    DSPACE_LEGACY_HOSTS,
    _dspace_legacy_host_regex,
    _dspace_legacy_parse,
    _psycharchives_handle,
    _psycharchives_info,
    dspace_links,
    psycharchives_file_download,
    psycharchives_info,
    psycharchives_links,
)

PA = "https://hdl.handle.net/20.500.12034/17526"
BONN = "https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234"

# --------------------------------------------------------------------------- testthat ports


def test_exists() -> None:
    assert callable(psycharchives_links)
    assert callable(psycharchives_info)
    assert callable(psycharchives_file_download)
    assert psycharchives_links.__doc__


def test_handle_extraction() -> None:
    assert _psycharchives_handle(PA) == "20.500.12034/17526"
    assert (
        _psycharchives_handle("https://www.psycharchives.org/jspui/handle/20.500.12034/17526")
        == "20.500.12034/17526"
    )
    assert _psycharchives_handle("20.500.12034/17526") == "20.500.12034/17526"
    assert _psycharchives_handle("https://osf.io/abcde") is None


def test_psycharchives_links() -> None:
    links = psycharchives_links(pc.test_paper(url=[PA]))
    assert len(links) == 1
    assert links["href"].iloc[0] == PA

    # also matches psycharchives.org item pages
    paper2 = pc.test_paper(url=["https://www.psycharchives.org/jspui/handle/20.500.12034/17526"])
    assert len(psycharchives_links(paper2)) == 1


def test_private_psycharchives_info(apis: object) -> None:
    info = _psycharchives_info(PA)
    assert info["pa_url"].iloc[0] == PA
    assert "International Cognitive Ability Resource" in info["PA_title"].iloc[0]
    assert "Doebler" in info["PA_authors"].iloc[0]
    assert "restrictedAccess" in info["PA_license"].iloc[0]

    files = info["files"].iloc[0]
    # only public bitstreams are returned by the API (readme is public; the
    # restricted 1GB zip is not listed)
    assert "readme.txt" in files["name"].tolist()
    assert all(
        r.startswith("https://www.psycharchives.org/rest/bitstreams/") for r in files["retrieve"]
    )


def test_psycharchives_file_download(apis: object) -> None:
    files = psycharchives_file_download(PA)
    assert isinstance(files, pd.DataFrame)
    assert "readme.txt" in files["name"].tolist()
    # deferred download: file_url set, file_location NA (fetched later by
    # download_repo_files())
    assert files["file_url"].notna().all()
    assert files["file_location"].isna().all()
    assert "type" in files.columns

    # rights flag carried as an attribute (item is restrictedAccess), so the
    # module can warn about restricted files without a second API call
    rights = files.attrs["rights"]
    assert list(rights.values()) == ["restrictedAccess"]
    assert list(rights) == [PA]


# --------------------------------------------------------------------------- more


def test_host_allowlist_and_regex() -> None:
    assert DSPACE_LEGACY_HOSTS[0] == "www.psycharchives.org"
    rx = _dspace_legacy_host_regex()
    assert rx.split("|")[0] == r"www\.psycharchives\.org"
    assert len(rx.split("|")) == len(DSPACE_LEGACY_HOSTS)


def test_dspace_legacy_parse_shapes() -> None:
    parsed = _dspace_legacy_parse(
        [
            "https://www.psycharchives.org/jspui/handle/20.500.12034/17526",
            None,
            "",
            "20.500.12034/17526.",
            f"{BONN};",
            "https://DSPACE.UT.EE/handle/10062/12345.",
            "see 12.34/abc and 20.500.12034/1",
        ]
    )
    assert list(parsed.columns) == ["host", "handle"]
    assert parsed["host"].tolist()[:1] == ["www.psycharchives.org"]
    assert parsed["host"].isna().tolist()[1:3] == [True, True]
    assert parsed["handle"].iloc[3] == "20.500.12034/17526"
    assert parsed["host"].iloc[4] == "bonndoc.ulb.uni-bonn.de"
    assert parsed["handle"].iloc[4] == "20.500.11811/1234"
    assert parsed["host"].iloc[5] == "dspace.ut.ee"  # lower-cased
    assert parsed["handle"].iloc[5] == "10062/12345"
    # the first handle-shaped string wins; the host comes from the PsychArchives prefix
    assert parsed["handle"].iloc[6] == "12.34/abc"
    assert parsed["host"].iloc[6] == "www.psycharchives.org"
    assert len(_dspace_legacy_parse([])) == 0
    with pytest.raises(IndexError):
        _psycharchives_handle([])


def test_dspace_links_finds_every_host() -> None:
    paper = pc.test_paper(
        ["Also 20.500.12034/999 and bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234"],
        [PA, "https://dspace.ut.ee/handle/10062/1/", "https://osf.io/x"],
    )
    hrefs = dspace_links(paper)["href"].tolist()
    # hyperlinks must name a known host (a hdl.handle.net link does not), bare
    # mentions may also be a PsychArchives handle
    assert hrefs == [
        "https://dspace.ut.ee/handle/10062/1",
        "20.500.12034/999",
        "bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234",
    ]
    # psycharchives_links() stays PsychArchives-only
    assert psycharchives_links(paper)["href"].tolist() == [PA, "20.500.12034/999"]


def test_restricted_item_lists_no_files_but_keeps_rights(mock_api: object) -> None:
    files = psycharchives_file_download(BONN)
    assert isinstance(files, pd.DataFrame)
    assert len(files) == 0
    assert list(files.columns) == [
        "pa_url", "name", "file_url", "file_location", "size", "isdir", "ext", "type"
    ]  # fmt: skip
    assert files.attrs["rights"] == {BONN: "openAccess"}
    assert files.attrs["doi"] == {BONN: None}


def test_other_host_metadata(mock_api: object) -> None:
    info = _psycharchives_info(BONN)
    assert info["PA_title"].iloc[0] == "Bonn item"
    assert info["PA_authors"].iloc[0] == "Author, A.; Author, B."
    assert pd.isna(info["PA_doi"].iloc[0])
    assert info["PA_date"].iloc[0] == "2020-05-05"


def test_unfound_items_warn(mock_api: object) -> None:
    with pytest.warns(UserWarning, match="is not a valid PsychArchives handle"):
        info = _psycharchives_info("https://osf.io/abcde")
    assert info["error"].iloc[0] == "unfound"
    with pytest.warns(UserWarning, match="could not be found"):
        assert psycharchives_file_download("https://qsardb.org/repository/handle/10967/106") is None
    with pytest.warns(UserWarning, match="NA is not a valid PsychArchives handle"):
        assert psycharchives_file_download(None) is None  # R: NA


def test_info_stops_at_first_error(apis: object) -> None:
    with pytest.warns(UserWarning):
        info = psycharchives_info(["https://osf.io/x", PA])
    assert info["pa_url"].tolist() == ["https://osf.io/x", PA]
    assert info["error"].tolist()[0] == "unfound"
    assert "PA_title" not in info.columns  # the second item was never fetched


def test_info_joins_back_onto_a_links_table(apis: object) -> None:
    links = psycharchives_links(pc.test_paper(url=[PA, PA + "/"]))
    info = psycharchives_info(links, "href")
    assert len(info) == len(links) == 2  # rows differ in link_text, so both stay
    assert list(info.columns[: len(links.columns)]) == list(links.columns)
    assert info["PA_license"].iloc[0] == "restrictedAccess"


def test_info_uses_the_listing_cache(apis: object) -> None:
    first = psycharchives_info(PA, cache=True)
    from tests.httpmock import replay

    with replay("apis_papers_empty"):  # nothing recorded: only the cache can answer
        again = psycharchives_info(PA, cache=True)
    assert again["PA_title"].tolist() == first["PA_title"].tolist()


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"PsychArchives\.org seems to be offline"):
        psycharchives_info(PA)


def test_vector_download_is_aligned_with_input(apis: object) -> None:
    with pytest.warns(UserWarning):
        files = psycharchives_file_download([PA, None, PA, "https://osf.io/x"])
    assert files["pa_url"].tolist()[0] == PA
    assert files["pa_url"].isna().sum() == 1
    assert files.attrs["rights"] == {PA: "restrictedAccess"}


def test_file_lists_name_the_items_not_found(apis: object) -> None:
    # U43: repo_check() reports the items that could not be found
    from pytacheck.archives.psycharchives import _psycharchives_file_lists

    with pytest.warns(UserWarning):
        files, unfound = _psycharchives_file_lists([PA, None, "https://osf.io/x"])
    assert unfound == ["https://osf.io/x"]
    assert files is not None and files.attrs["rights"] == {PA: "restrictedAccess"}
    with pytest.warns(UserWarning):
        assert _psycharchives_file_lists(["https://osf.io/x", "https://osf.io/y"]) == (
            None,
            ["https://osf.io/x", "https://osf.io/y"],
        )
    with pytest.warns(UserWarning):
        assert _psycharchives_file_lists(None) == (None, [])
