"""Port of metacheck tests/testthat/test-db-crossref.R (Crossref, DataCite, OpenAlex)."""

from __future__ import annotations

import httpx
import pandas as pd
import pytest
import respx

import pytacheck as pc
from pytacheck.db import crossref as cr
from tests.db.parity_replay import paper_with_bib

LAKENS = (
    "Lakens, D., Mesquida, C., Rasti, S., & Ditroilo, M. (2024). The benefits of "
    "preregistration and Registered Reports. Evidence-Based Toxicology, 2(1)."
)
FAKE = "DeBruine, L. (2027) I haven't written this paper. Journal of Journals."


# add_bib_match -----------------------------------------------------------------


def test_add_bib_match_no_refs() -> None:
    paper = pc.test_paper("No refs")
    paper_bm = cr.add_bib_match(paper)
    assert len(paper_bm.bib) == 0
    assert paper_bm.get("bib_match") is None or len(paper_bm.bib_match) == 0


def test_add_bib_match_unmatched(apis) -> None:
    paper = paper_with_bib(
        {
            "bib_id": [1],
            "doi": [None],
            "title": ["Not a real paper"],
            "container": ["Journal of Journals"],
            "authors": ["Not A. Realname"],
        }
    )
    paper_bm = cr.add_bib_match(paper)
    assert paper_bm.bib["bib_id"].tolist() == [1]
    assert len(paper_bm.bib_match) == 0
    assert paper.get("bib_match") is None or len(paper.bib_match) == 0  # input untouched


def test_add_bib_match_invalid_doi(apis) -> None:
    paper = paper_with_bib(
        {
            "bib_id": [1],
            "doi": ["not.a.doi"],
            "title": ["Facial resemblance enhances trust"],
            "container": ["Proceedings of the Royal Society of London B"],
            "authors": ["Lisa DeBruine"],
        }
    )
    bm = cr.add_bib_match(paper).bib_match
    assert len(bm) == 1
    assert bm["doi"].tolist() == ["10.1098/rspb.2002.2034"]
    assert bm["score"].iat[0] >= 50


def test_add_bib_match_valid_doi(apis) -> None:
    paper = paper_with_bib(
        {
            "bib_id": [1],
            "doi": ["10.1098/rspb.2002.2034"],
            "title": ["Facial resemblance enhances trust"],
            "container": ["Proceedings of the Royal Society of London B"],
            "authors": ["Lisa DeBruine"],
        }
    )
    bm = cr.add_bib_match(paper).bib_match
    assert len(bm) == 1
    assert bm["doi"].tolist() == ["10.1098/rspb.2002.2034"]
    assert pd.isna(bm["score"].iat[0])
    assert list(bm.columns) == [
        "bib_id",
        "service",
        "service_id",
        "score",
        "bib_type",
        "doi",
        "title",
        "authors",
        "editors",
        "publisher",
        "year",
        "date",
        "container",
        "volume",
        "issue",
        "first_page",
        "last_page",
        "edition",
        "version",
        "url",
    ]
    assert bm["authors"].iat[0] == [{"given": "Lisa M.", "family": "DeBruine"}]


def _two_refs() -> dict[str, list]:
    return {
        "bib_id": [1, 2],
        "doi": [None, None],
        "title": ["Facial resemblance enhances trust", "Trustworthy but not Lustworthy"],
        "container": ["Proceedings of the Royal Society of London B"] * 2,
        "authors": [["Lisa DeBruine"], ["Lisa DeBruine"]],
    }


def test_add_bib_match_min_score(apis) -> None:
    paper = paper_with_bib(_two_refs())
    bm = cr.add_bib_match(paper, 0).bib_match
    assert bm["bib_id"].tolist() == [1, 2]
    assert bm["doi"].tolist() == ["10.1098/rspb.2002.2034", "10.1098/rspb.2004.3003"]
    assert bm["year"].tolist() == [2002, 2005]

    # set the threshold between the two papers
    min_score = bm["score"].mean()
    bm2 = cr.add_bib_match(paper, min_score).bib_match
    assert bm2["doi"].tolist() == ["10.1098/rspb.2002.2034"]


def test_add_bib_match_paperlist(apis) -> None:
    p1 = paper_with_bib(
        {
            "bib_id": [1, 2],
            "doi": [None, None],
            "title": ["Facial resemblance enhances trust", "Trustworthy but not Lustworthy"],
            "container": ["Proceedings of the Royal Society of London B"] * 2,
            "authors": ["Lisa DeBruine", "Lisa DeBruine"],
        },
        paper_id="p1",
    )
    p2 = paper_with_bib(
        {
            "bib_id": [1, 2],
            "doi": [None, None],
            "title": [
                "Trustworthy but not Lustworthy",
                "Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses",
            ],
            "container": [
                "Proceedings of the Royal Society of London B",
                "Social Psychological and Personality Science",
            ],
            "authors": ["Lisa DeBruine", "Daniel Lakens"],
        },
        paper_id="p2",
    )
    out = cr.add_bib_match(pc.PaperList([p1, p2]), 0)
    assert isinstance(out, pc.PaperList)
    assert out[0].bib_match["doi"].tolist() == ["10.1098/rspb.2002.2034", "10.1098/rspb.2004.3003"]
    assert out[1].bib_match["doi"].tolist() == [
        "10.1098/rspb.2004.3003",
        "10.1177/1948550617697177",
    ]


def test_add_bib_match_demo_reproduces_bundled_bib_match(apis, demo) -> None:
    bm = cr.add_bib_match(demo).bib_match
    expected = demo.bib_match
    assert bm["doi"].tolist() == expected["doi"].tolist()
    assert bm["title"].tolist() == expected["title"].tolist()
    assert bm["authors"].tolist() == expected["authors"].tolist()


def test_add_bib_match_network_warning() -> None:
    paper = paper_with_bib(_two_refs())
    with respx.mock() as router:
        router.route().mock(return_value=httpx.Response(503))
        cr_online = cr._utils.online
        cr._utils.online = lambda *_a, **_k: True  # type: ignore[assignment]
        try:
            with pytest.warns(UserWarning, match="2 of 2 reference lookups did not complete"):
                out = cr.add_bib_match(paper)
        finally:
            cr._utils.online = cr_online  # type: ignore[assignment]
    assert len(out.bib_match) == 0


# crossref_query ----------------------------------------------------------------


def test_crossref_query_psychsci_rows(apis, psychsci_papers) -> None:
    ref = psychsci_papers[0].bib.iloc[5:7]
    obs = cr.crossref_query(ref, min_score=50)
    assert obs["title"].tolist()[0] is pd.NA or pd.isna(obs["title"].iat[0])
    assert (
        obs["title"].iat[1] == "Action mirroring and action understanding: an alternative account"
    )


def test_crossref_query(apis) -> None:
    obs = cr.crossref_query(LAKENS, min_score=50)
    assert obs["DOI"].tolist() == ["10.1080/2833373x.2024.2376046"]
    assert set(obs.columns) == {
        "ref",
        "DOI",
        "score",
        "type",
        "title",
        "author",
        "publisher",
        "container-title",
        "volume",
        "issue",
        "URL",
        "year",
    }

    obs = cr.crossref_query(FAKE, min_score=50)
    assert pd.isna(obs["DOI"].iat[0])
    assert obs["ref"].tolist() == [FAKE]

    assert set(cr.crossref_query(LAKENS, select=["DOI"]).columns) == {"ref", "DOI"}
    assert set(cr.crossref_query(LAKENS, select=["score", "title"]).columns) == {
        "ref",
        "score",
        "title",
    }

    ref = pd.DataFrame(
        {
            "authors": ["Lakens, D., Mesquida, C., Rasti, S., & Ditroilo, M."],
            "title": ["The benefits of preregistration and Registered Reports"],
            "container": ["Evidence-Based Toxicology"],
            "year": [2024],
        }
    )
    obs = cr.crossref_query(ref, select=["score", "title"])
    assert obs["title"].tolist() == ["The benefits of preregistration and Registered Reports"]

    # vectorised
    obs = cr.crossref_query([LAKENS, FAKE])
    assert obs["DOI"].tolist()[0] == "10.1080/2833373x.2024.2376046"
    assert pd.isna(obs["DOI"].iat[1])

    # encoded ( and )
    ref = (
        "Levi DM, Klein SA, Aitsebaomo AP, Mon-Williams M, Tresilian JR, Strang NC, Kochhar P, "
        "Wann JP (1985. 1998).\n\u201cImproving vision: Neural compensation for optical defocus.\u201d "
        "_Proceedings of the Royal Society B:\nBiological Sciences_, *25*, 71-77. "
        "doi:10.1016/0042-6989(85)90207\n<https://doi.org/10.1016/0042-6989(85)90207>."
    )
    assert cr.crossref_query(ref)["DOI"].tolist() == ["10.1098/rspb.1998.0266"]


def test_crossref_query_empty_and_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cr.crossref_query([]).shape == (0, 0)
    monkeypatch.setattr(cr._utils, "online", lambda *_a, **_k: False)
    obs = cr.crossref_query(["a", "b"])
    assert obs["error"].tolist() == ["offline", "offline"]
    assert obs["bib_text"].tolist() == ["a", "b"]


def test_crossref_query_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_batch(urls, **kwargs):
        seen.update(kwargs)
        return [None] * len(urls)

    monkeypatch.setattr(cr._utils, "online", lambda *_a, **_k: True)
    monkeypatch.setattr("pytacheck.http.batch_query", fake_batch)
    obs = cr.crossref_query("x")
    assert seen["throttle_capacity"] == 3
    assert obs["error"].tolist() == ["request failed"]
    cr.crossref_doi("10.1234/x")
    assert seen["throttle_capacity"] == 10


# crossref_doi ------------------------------------------------------------------


def test_crossref_doi(apis, psychsci_papers) -> None:
    cr1 = cr.crossref_doi("10.1177/fake")
    assert cr1["DOI"].tolist() == ["10.1177/fake"]
    assert cr1["error"].tolist() == ["Not Found"]
    assert "author" not in cr1.columns

    cr1 = cr.crossref_doi("10.1177/0956797614520714")
    assert cr1["title"].tolist() == [
        "Retracted: Evil Genius? How Dishonesty Can Lead to Greater Creativity"
    ]

    dois = pc.paper_table(psychsci_papers, "info")["doi"].tolist()[:2]
    cr2 = cr.crossref_doi(dois, ["DOI", "title"])
    assert cr2["DOI"].tolist() == dois
    assert set(cr2.columns) == {"DOI", "title"}

    cr3 = cr.crossref_doi(psychsci_papers[0], ["DOI", "title"])
    assert cr3["DOI"].tolist() == dois[:1]

    cr4 = cr.crossref_doi(psychsci_papers[0:2], ["DOI", "title"])
    assert cr4["DOI"].tolist() == dois


def test_crossref_doi_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cr.crossref_doi([]).shape == (0, 0)
    na = cr.crossref_doi([None, None])
    assert list(na.columns) == ["DOI"] and len(na) == 2
    monkeypatch.setattr(cr._utils, "online", lambda *_a, **_k: False)
    off = cr.crossref_doi(["10.1234/a", "bad"])
    assert off["error"].tolist() == ["offline", "offline"]


def test_crossref_doi_status_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr._utils, "online", lambda *_a, **_k: True)
    with respx.mock() as router:
        router.route().mock(return_value=httpx.Response(200, json={"status": "failed", "body": {}}))
        out = cr.crossref_doi("10.1234/abc")
    assert out["error"].tolist() == ["unknown"]


# DataCite ----------------------------------------------------------------------


def test_datacite_doi(apis) -> None:
    for doi in (None, []):
        info = cr.datacite_doi(doi)
        assert len(info) == 0
        assert list(info.columns) == ["service", "title", "authors", "doi"]

    info = cr.datacite_doi("10.5281/zenodo.2669586")
    assert len(info) == 1
    assert info["service"].tolist() == ["datacite"]
    assert info["title"].tolist() == ["faux: Simulation for Factorial Designs"]
    assert info["authors"].iat[0] == [{"given": "Lisa", "family": "DeBruine"}]

    info = cr.datacite_doi([None])
    assert len(info) == 1
    assert info["service"].tolist() == ["datacite"]
    assert pd.isna(info["title"].iat[0])

    info = cr.datacite_doi(["10.5281/zenodo.2669586", "10.5281/zenodo.3564348"])
    assert info["title"].tolist() == [
        "faux: Simulation for Factorial Designs",
        "Data Skills for Reproducible Science",
    ]


def test_datacite_doi_not_found_returns_none(apis) -> None:
    assert cr.datacite_doi(["10.5281/zenodo.2669586", "10.9999/none"]) is None


# OpenAlex ----------------------------------------------------------------------


def test_openalex_doi(apis, psychsci_papers) -> None:
    oa = cr.openalex_doi("10.1177/fake")
    assert oa == {"DOI": "10.1177/fake", "error": "not found"}

    oa = cr.openalex_doi("bad.form")
    assert oa[0] == {"DOI": "bad.form", "error": "malformed"}

    oa = cr.openalex_doi("10.1177/0956797614520714")
    assert oa[0]["is_retracted"] is True
    assert oa[0]["abstract"].startswith(
        "We propose that dishonest and creative behavior have something in common"
    )
    assert oa[0]["abstract"].endswith("(Experiment 4) and moderation (Experiment 5).")

    oa = cr.openalex_doi("https://doi.org/10.1177/0956797613520608")
    assert oa[0]["id"] == "https://openalex.org/W2134722098"

    oa = cr.openalex_doi(["10.1177/0956797613520608", "10.1177/0956797614522816"])
    assert [o["id"] for o in oa] == [
        "https://openalex.org/W2134722098",
        "https://openalex.org/W2103593746",
    ]

    assert cr.openalex_doi(psychsci_papers[0])[0]["id"] == "https://openalex.org/W2134722098"
    oa = cr.openalex_doi(psychsci_papers[0:2])
    assert [o["id"] for o in oa] == [
        "https://openalex.org/W2134722098",
        "https://openalex.org/W2103593746",
    ]

    oa = cr.openalex_doi("10.1177/0956797614520714", select="is_retracted")
    assert oa[0]["is_retracted"] is True


def test_openalex_doi_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cr.openalex_doi([]) == []
    assert cr.openalex_doi([None]) == {"DOI": None}
    monkeypatch.setattr(cr._utils, "online", lambda *_a, **_k: False)
    assert cr.openalex_doi("10.1234/x") == {"DOI": "10.1234/x", "error": "offline"}


def test_openalex_add_abstract() -> None:
    info = {"id": "W1", "abstract_inverted_index": {"world": [1, 3], "Hello": [0], "big": [2]}}
    assert cr._openalex_add_abstract(info)["abstract"] == "Hello world big world"
    assert "abstract" not in info  # not modified in place
    assert cr._openalex_add_abstract({"id": "W2"}) == {"id": "W2"}


def test_openalex_query(apis) -> None:
    b = cr.openalex_query("Sample Size Justification", "Collabra Psychology", "Lakens, D")
    assert len(b) == 1
    assert b["display_name"].tolist() == ["Sample Size Justification"]
    assert b["doi"].tolist() == ["https://doi.org/10.1525/collabra.33267"]

    assert cr.openalex_query("Sample Size Justification") is None
    loose = cr.openalex_query("Sample Size Justification", strict=False)
    assert len(loose) == 1


def test_openalex_query_colon_retry() -> None:
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "Main%20title%20only" in str(request.url) and "subtitle" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "W1",
                            "relevance_score": 5.5,
                            "display_name": "Main title only",
                            "primary_location": {"source": {"display_name": "J"}},
                            "authorships": [{"raw_author_name": "A B"}],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    with respx.mock() as router:
        router.route(host="api.openalex.org").mock(side_effect=handler)
        out = cr.openalex_query("Main title only: a subtitle", "J")
    assert len(urls) == 2
    assert out["authors"].tolist() == ["A B"]
    assert out["relevance_score"].tolist() == ["5.5"]


# .bibtype_convert / parsers ------------------------------------------------------


def test_bibtype_convert() -> None:
    types = ["book-part", "journal-article", "monograph", None, "unmatched-type"]
    assert cr._bibtype_convert(types) == ["inbook", "article", "book", None, "unmatched-type"]
    assert cr._bibtype_convert(None) is None
    assert cr._bibtype_convert("dataset") == "misc"


def test_crossref_parse_item_authors_and_year() -> None:
    item = {
        "DOI": "10.1/x",
        "title": ["T1", "T2"],
        "journal-issue": {"published-print": {"date-parts": [[1999]]}},
        "published": {"date-parts": [[2001]]},
        "author": [{"family": "Solo"}, {"given": "A.", "family": "B", "ORCID": "o"}, {"name": "X"}],
    }
    row = cr._crossref_parse_item(item)
    assert list(row.columns) == ["DOI", "title", "year", "author"]
    assert row["title"].iat[0] == "T1"
    assert row["year"].iat[0] == 1999
    assert row["author"].iat[0] == [
        {"family": "Solo", "given": None, "ORCID": None},
        {"family": "B", "given": "A.", "ORCID": "o"},
    ]
    with pytest.raises(ValueError, match="differing number of rows"):
        cr._crossref_parse_item({"DOI": "x", "volume": None})
