"""Regression tests for divergences from metacheck found in the db review.

Each expectation was checked against R (see parity/cases/db_review.yaml for
the R-generated goldens covering the same branches).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pandas as pd
import pytest
import respx

from pytacheck.db import _utils
from pytacheck.db._utils import records_frame
from pytacheck.db.crossref import (
    _crossref_parse_item,
    _crossref_query_parse,
    _data_frame,
    _openalex_add_abstract,
    _repair_unique,
    crossref_doi,
    crossref_query,
)
from pytacheck.db.doi import doi_lookup, doi_resolves
from pytacheck.db.pubpeer import pubpeer_comments
from pytacheck.db.regcheck import _match_client, regcheck_compare


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(_utils, "online", lambda *_a, **_k: True)
    monkeypatch.setenv("PYTACHECK_NO_SLEEP", "1")
    yield


# --- data.frame() of parsed JSON ---------------------------------------------


def test_data_frame_naming() -> None:
    # data.frame(list(a = "x", ISBN = list("978")), check.names = FALSE)
    assert [n for n, _ in _data_frame([("a", "x"), ("ISBN", ["978"])])[0]] == ["a", '"978"']
    assert [n for n, _ in _data_frame([("a", "x"), ("ISBN", [5])])[0]] == ["a", "5L"]
    assert [n for n, _ in _data_frame([("a", "x"), ("I", [5.5, 2])])[0]] == ["a", "I.5.5", "I.2L"]
    cols, n = _data_frame([("a", "x"), ("b", {"k": "p", "m": ["q"]})])
    assert [c for c, _ in cols] == ["a", "b.k", 'b."q"'] and n == 1
    cols, _ = _data_frame([("a", "x"), ("b", {"k": "p"}), ("c", {"k": "q"})])
    assert [c for c, _ in cols] == ["a", "k", "k"]
    cols, _ = _data_frame([("a", "x"), ("b", [{"k": "p"}, {"k": "q"}])])
    assert [c for c, _ in cols] == ["a", "b.k", "b.k"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([("a", "x"), ("b", None)], "1, 0"),
        ([("x", None), ("a", "x")], "0, 1"),
        ([("a", "x"), ("b", [])], "1, 0"),
        ([("a", "x"), ("b", {"k": [], "m": "y"})], "0, 1"),
        ([("a", "x"), ("b", [{"k": "p", "aff": []}])], "1, 0"),
    ],
)
def test_data_frame_row_errors(args: list, message: str) -> None:
    with pytest.raises(ValueError, match=f"differing number of rows: {message}$"):
        _data_frame(args)


def test_data_frame_all_empty_has_no_rows() -> None:
    assert _data_frame([("volume", None)]) == ([], 0)
    assert _data_frame([]) == ([], 0)


def test_repair_unique() -> None:
    names = ["a", "b", "a", "c...7", "", "...", "..3", "d..4", "e...2", "e"]
    assert _repair_unique(names) == [
        "a...1",
        "b",
        "a...3",
        "c",
        "...5",
        "...6",
        "...7",
        "d..4",
        "e...9",
        "e...10",
    ]


def test_parse_item_keeps_duplicate_names_and_zero_rows() -> None:
    item = {"DOI": "10.1/x", "link": [{"URL": "u1"}, {"URL": "u2"}]}
    df = _crossref_parse_item(item, ["DOI", "link"])
    assert list(df.columns) == ["DOI", "link.URL", "link.URL"]
    # nothing selected: a 0-row data frame
    assert len(_crossref_parse_item(item, ["abstract"])) == 0
    # a title of [null] is removed, not kept as NA
    assert list(_crossref_parse_item({"DOI": "d", "title": [None]}, ["DOI", "title"]).columns) == [
        "DOI"
    ]


def test_query_parse_repairs_names_before_selecting() -> None:
    items = [
        {"DOI": "d", "score": 80, "editor": [{"given": "A"}, {"given": "B"}], "title": ["T"]},
    ]
    out = _crossref_query_parse(items, 50, ["DOI", "score", "title", "editor"])
    # U13: the editors are a table like the authors (R spreads them over
    # repeated editor.given columns, which the selection then drops)
    assert list(out.columns) == ["DOI", "score", "title", "editor"]
    assert out["editor"].iat[0] == [{"given": "A"}, {"given": "B"}]
    assert _crossref_query_parse(items, 50, ["nothing"]).shape == (0, 0)


# --- bind_rows() type checks -------------------------------------------------


def test_records_frame_refuses_mixed_types() -> None:
    with pytest.raises(
        TypeError, match=r"Can't combine `\.\.1\$volume` <integer> and `\.\.2\$volume` <character>"
    ):
        records_frame([{"volume": 7}, {"volume": "7"}])
    # missing values combine with anything; integers and doubles combine
    out = records_frame([{"v": 1}, {"v": None}, {"v": 2.5}])
    assert out["v"].dtype == "float64"


# --- Crossref ----------------------------------------------------------------


def _works(message: dict) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "message": message})


def test_crossref_doi_drops_zero_row_results(online: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "10.1234%2Fa" in str(request.url):
            return _works({"DOI": "10.1234/a", "abstract": "Abs"})
        return _works({"DOI": "10.1234/b"})

    with respx.mock() as router:
        router.route(host="api.labs.crossref.org").mock(side_effect=handler)
        out = crossref_doi(["10.1234/a", "10.1234/b"], select=["abstract"])
    assert out["abstract"].tolist() == ["Abs"]


def test_crossref_query_data_frame_uses_the_columns_it_has(online: None) -> None:
    # U13: R fails without author or container columns (differing number of rows)
    with respx.mock() as router:
        route = router.get(url__startswith="https://api.crossref.org/works").mock(
            return_value=httpx.Response(404)
        )
        out = crossref_query(pd.DataFrame({"title": ["X"], "container": ["Y"]}))
    assert out["ref"].tolist() == ["X; Y"]
    url = str(route.calls[0].request.url)
    assert "query.title=X" in url and "query.container-title=Y" in url
    assert "query.author" not in url
    # no title column: nothing to search
    assert crossref_query(pd.DataFrame({"authors": ["A"], "container": ["Y"]})).shape == (0, 0)


def test_crossref_query_ref_text_and_zero_rows(online: None) -> None:
    # U13: fields joined with "; ", list cells with ", " (R: a literal
    # backslash-n and deparsed list()); a missing field is not searched as "NA"
    ref = pd.DataFrame(
        {"title": ["T", None], "authors": [["Lisa", "Ann"], ["Lisa"]], "container": ["C", None]}
    )
    with respx.mock() as router:
        route = router.get(url__startswith="https://api.crossref.org/works").mock(
            return_value=httpx.Response(404)
        )
        out = crossref_query(ref)
    assert out["ref"].tolist() == ["T; Lisa, Ann; C", "Lisa"]
    assert out["error"].tolist() == ["request failed"] * 2
    second = str(route.calls[1].request.url)
    assert "query.author=Lisa" in second and "query.title" not in second

    body = {"status": "ok", "message": {"items": [{"DOI": "d", "score": 90}]}}
    with respx.mock() as router:
        router.get(url__startswith="https://api.crossref.org/works").mock(
            return_value=httpx.Response(200, json=body)
        )
        out = crossref_query("some reference", select=["nonexistent"])
    assert out["error"].tolist() == ["replacement has 1 row, data has 0"]


# --- doi.org ------------------------------------------------------------------


def test_doi_lookup_pastes_missing_names_as_empty() -> None:
    csl = {"DOI": "10.1/x", "author": [{"family": "Solo"}, {"given": "Only"}, {"literal": "Org"}]}
    with respx.mock() as router:
        router.get("https://doi.org/10.1%2Fx").mock(return_value=httpx.Response(200, json=csl))
        out = doi_lookup("10.1/x")
    # U12: an organisation is named by its literal (R pastes "character(0)")
    assert out["author"].tolist() == ["Solo, ; , Only; Org"]


def test_doi_resolves_compares_codes_like_r() -> None:
    codes = {"a": [1], "b": "1", "c": {"x": 100}, "d": [1, 2], "e": True}

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url).split("%2F", 1)[1].split("?", 1)[0]
        return httpx.Response(200, json={"responseCode": codes[key]})

    with respx.mock() as router:
        router.route(host="doi.org").mock(side_effect=handler)
        out = doi_resolves([f"10.1234/{k}" for k in codes])
    assert out == [True, True, False, None, True]


# --- OpenAlex ----------------------------------------------------------------


def test_openalex_abstract() -> None:
    info = {"abstract_inverted_index": {"b": [1, 3], "a": [0], "c": [2], "": [4]}}
    assert _openalex_add_abstract(info)["abstract"] == "a b c b "
    # positions without words (an unnamed list) give ""
    assert _openalex_add_abstract({"abstract_inverted_index": [[0], [1]]})["abstract"] == ""
    # U14: no positions at all is an empty abstract (R's order(NULL) fails)
    for aii in ({}, [], {"a": []}):
        assert _openalex_add_abstract({"abstract_inverted_index": aii})["abstract"] == ""


# --- PubPeer -----------------------------------------------------------------


def test_pubpeer_feedback_without_ids() -> None:
    # U14: R fails when no feedback has an id ("Join columns ...")
    with respx.mock() as router:
        router.post(url__startswith="https://pubpeer.com/").mock(
            return_value=httpx.Response(200, json={"feedbacks": [{"total_comments": 4}]})
        )
        out = pubpeer_comments("10.1/f")
    assert out["total_comments"].tolist() == [0]


def test_pubpeer_does_not_match_missing_ids_to_missing_dois() -> None:
    # U14: R's join gave a missing DOI the comments of a feedback without an id
    fb = [{"total_comments": 4, "url": "u"}, {"id": "10.1/e", "total_comments": 1}]
    with respx.mock() as router:
        route = router.post(url__startswith="https://pubpeer.com/").mock(
            return_value=httpx.Response(200, json={"feedbacks": fb})
        )
        out = pubpeer_comments([None, "10.1/E"])
    assert json.loads(route.calls[0].request.content) == {"dois": "10.1/e"}
    assert out["doi"].tolist()[1] == "10.1/E"
    assert out["total_comments"].tolist() == [0, 1]
    assert list(out.columns) == ["doi", "total_comments", "url", "users"]


# --- RegCheck ----------------------------------------------------------------


def test_regcheck_client_messages() -> None:
    with pytest.raises(ValueError, match="Unknown RegCheck client 'groqopenai'"):
        _match_client(["groq", "openai"])
    assert _match_client("gr") == "groq"


def test_regcheck_trimws_is_r_whitespace() -> None:
    # trimws() keeps a no-break space, so the text is not empty
    with pytest.raises(ValueError, match="exactly one of prereg_text"):
        regcheck_compare(" ")
    with pytest.raises(ValueError, match="single non-empty string"):
        regcheck_compare(" \t\n")
