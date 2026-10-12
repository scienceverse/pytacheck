"""Port of metacheck tests/testthat/test-doi.R."""

from __future__ import annotations

import math

import httpx
import pandas as pd
import pytest
import respx

from metacheck.db.doi import doi_clean, doi_lookup, doi_resolves, doi_valid_format

EXP = "10.1038/nphys1170"


# doi_lookup ------------------------------------------------------------------


def test_doi_lookup_empty() -> None:
    for doi in (None, []):
        info = doi_lookup(doi)
        assert list(info.columns) == ["doi"]
        assert len(info) == 0


def test_doi_lookup_one(apis) -> None:
    info = doi_lookup("10.7717/peerj.4375")
    assert len(info) == 1
    assert info["title"].iat[0] == (
        "The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles"
    )


def test_doi_lookup_na(apis) -> None:
    info = doi_lookup([None])
    assert len(info) == 1
    assert pd.isna(info["title"].iat[0])


def test_doi_lookup_multiple(apis) -> None:
    info = doi_lookup(["10.1177/2515245920970949", "10.1037/0003-066x.54.6.408"])
    assert info["title"].tolist() == [
        "Improving Transparency, Falsifiability, and Rigor by Making Hypothesis Tests Machine-Readable",
        "The origins of sex differences in human behavior: Evolved dispositions versus social roles.",
    ]
    assert list(info.columns) == [
        "doi",
        "type",
        "title",
        "container",
        "year",
        "author",
        "volume",
        "issue",
        "first_page",
        "last_page",
        "editor",
        "publisher",
        "url",
    ]


def test_doi_lookup_http_error_gives_an_na_row(apis) -> None:
    # U12: metacheck's `return(NULL)` inside tryCatch() leaves doi_lookup() entirely
    info = doi_lookup(["10.7717/peerj.4375", "10.9999/not.recorded"])
    assert len(info) == 2
    assert info["doi"].iat[0] == "10.7717/peerj.4375"
    assert info.iloc[1][["doi", "title"]].isna().all()


def test_doi_lookup_non_json_drops_slot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.raw_path.endswith(b"html"):
            return httpx.Response(200, html="<html></html>")
        return httpx.Response(
            200, json={"DOI": "10.1/json", "title": "T", "page": "5-9", "author": []}
        )

    with respx.mock() as router:
        router.route(host="doi.org").mock(side_effect=handler)
        info = doi_lookup(["10.1/html", "10.1/json", None])
    # U12: one row per DOI (R deletes the HTML slot and shifts the rows)
    assert len(info) == 3
    assert pd.isna(info["doi"].iat[0])
    assert info["doi"].iat[1] == "10.1/json"
    assert info["first_page"].iat[1] == "5" and info["last_page"].iat[1] == "9"
    assert info["author"].iat[1] == ""
    assert pd.isna(info["doi"].iat[2])


# doi_clean -------------------------------------------------------------------


@pytest.mark.parametrize(
    "doi",
    ["https://doi.org/10.1038/nphys1170", "doi:10.1038/nphys1170", "  DOI : 10.1038/nphys1170 "],
)
def test_doi_clean_prefixes(doi: str) -> None:
    assert doi_clean(doi) == EXP


def test_doi_clean_bad_unchanged() -> None:
    assert doi_clean("bad.doi") == "bad.doi"


def test_doi_clean_vector_and_list() -> None:
    doi = [
        "https://doi.org/10.1038/nphys1170",
        "doi:10.1038/nphys1170",
        "  DOI : 10.1038/nphys1170 ",
        "10.1038/nphys1170",
        "",
        None,
    ]
    exp = [EXP] * 4 + ["", None]
    assert doi_clean(doi) == exp
    assert doi_clean(tuple(doi)) == exp
    series = doi_clean(pd.Series(doi, index=list("abcdef")))
    assert series.tolist() == exp and list(series.index) == list("abcdef")


def test_doi_clean_special_cases() -> None:
    assert (
        doi_clean("http://journals.plos.org/plosone/article?id=10.1371/journal.pone.0004153")
        == "10.1371/journal.pone.0004153"
    )
    # the start matches at the first 10.###
    assert doi_clean("10.1234/more.10.2345") == "10.1234/more.10.2345"
    assert doi_clean("10.1234/mypaper#section") == "10.1234/mypaper"
    assert doi_clean("10.1234/mypaper/full") == "10.1234/mypaper"


# doi_valid_format ------------------------------------------------------------


def test_doi_valid_format() -> None:
    assert doi_valid_format("10.1038/nphys1170") is True
    assert doi_valid_format("no10.1038/nphys1170") is False
    assert doi_valid_format(None) is False
    assert doi_valid_format(["10.1038/nphys1170", "bad", None]) == [True, False, False]
    assert doi_valid_format("10.1002/(SICI)1099-1611(200001/02)9:1<11::AID-PON424>3.0.CO;2-Z")


# doi_resolves ----------------------------------------------------------------


def test_doi_resolves(apis) -> None:
    assert doi_resolves("10.1038/nphys1170") is True
    assert doi_resolves("10.1234/invalid.doi") is False
    assert doi_resolves(None) is None
    assert doi_resolves("") is None
    assert doi_resolves("bad.doi") is False
    doi = ["10.1038/nphys1170", "10.1234/invalid.doi", "bad.doi", None]
    assert doi_resolves(doi) == [True, False, False, None]


@pytest.mark.parametrize(
    "doi",
    ["https://doi.org/10.1038/nphys1170", "doi:10.1038/nphys1170", "  DOI : 10.1038/nphys1170 "],
)
def test_doi_resolves_cleans(apis, doi: str) -> None:
    assert doi_resolves(doi) is True


def test_doi_resolves_lists(apis) -> None:
    assert doi_resolves(["10.1038/nphys1170", "10.1234/invalid.doi", None]) == [True, False, None]
    nested = [["10.1038/nphys1170", "10.1234/invalid.doi"], ["10.1038/nphys1170", None]]
    assert doi_resolves(nested) == [True, False, True, None]


def test_doi_resolves_response_codes() -> None:
    codes = {"a": 1, "b": 100, "c": 2, "d": 200, "e": 3}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.raw_path.split(b"%2F")[-1].split(b"?")[0].decode()
        if key == "server":
            return httpx.Response(404, json={"responseCode": 100})
        if key == "down":
            raise httpx.ConnectError("down")
        if key == "html":
            return httpx.Response(200, html="<p>")
        return httpx.Response(200, json={"responseCode": codes[key]})

    with respx.mock() as router:
        router.route(host="doi.org").mock(side_effect=handler)
        res = doi_resolves(
            [f"10.1000/{k}" for k in codes] + ["10.1000/server", "10.1000/down", "10.1000/html"]
        )
    assert res == [True, False, None, False, None, False, None, None]


def test_doi_lookup_year_and_authors() -> None:
    body = {
        "DOI": "10.1/x",
        "type": "journal-article",
        "title": "A title",
        "container-title": "J",
        "published": {"date-parts": [[2020, 1, 2]]},
        "author": [{"family": "Smith", "given": "A"}, {"name": "Org"}],
        "editor": [{"given": "E", "family": "F"}],
        "volume": "1",
    }
    with respx.mock() as router:
        router.get("https://doi.org/10.1%2Fx").mock(return_value=httpx.Response(200, json=body))
        info = doi_lookup("10.1/x")
    assert info["year"].iat[0] == 2020
    # U12: an organisation is named (R pastes "character(0)")
    assert info["author"].iat[0] == "Smith, A; Org"
    assert info["editor"].iat[0] == "E; F"
    assert not math.isnan(float(info["year"].iat[0]))
