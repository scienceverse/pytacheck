"""Regression tests for R behaviour found in the adversarial review of the OSF port.

Each test pins one metacheck behaviour that the first port missed; the parity
area ``archives_osf_review`` checks the same things against R goldens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from metacheck._r import slashed
from metacheck.archives.osf import (
    _normalize,
    _r_basename,
    _r_dirname,
    osf_check_id,
    osf_type,
)
from metacheck.archives.osf_all import _osf_walk_nodes
from metacheck.archives.osf_helpers import _osf_parse_response
from metacheck.archives.osf_metadata import _metadata_json, _osf_metadata_download


def _single(data: dict, content_type: str | None = "application/vnd.api+json") -> httpx.Response:
    headers = {} if content_type is None else {"content-type": content_type}
    return httpx.Response(200, content=json.dumps({"data": data}).encode(), headers=headers)


def test_parse_checks_content_type_like_httr2() -> None:
    # httr2::resp_body_json() wants application/json or a +json suffix
    data = {"id": "n1", "type": "nodes"}
    for ok in ("application/json", "application/vnd.api+json; charset=utf-8"):
        assert len(_osf_parse_response(_single(data, ok))) == 1
    assert len(_osf_parse_response(_single(data, "Application/JSON"))) == 1  # U152
    for bad in (None, "text/html"):
        with pytest.raises(ValueError, match="Unexpected content type"):
            _osf_parse_response(_single(data, bad))


# osf_check_id(): httr2::url_parse() query handling ---------------------------------


def test_check_id_view_only_value_stops_at_second_equals() -> None:
    # curl splits each parameter on "=" and keeps the second piece
    assert osf_check_id("https://osf.io/abcde/?view_only=abc=def") == "abcde?view_only=abc"
    assert osf_check_id("https://osf.io/abcde/?view_only==5") == "abcde?view_only="


def test_check_id_view_only_partial_matching() -> None:
    # `parsed$query$view_only` partially matches a unique longer name ...
    assert osf_check_id("https://osf.io/abcde/?view_onlyx=123") == "abcde?view_only=123"
    # ... but not an ambiguous one
    assert osf_check_id("https://osf.io/abcde/?view_onlyx=1&view_onlyy=2") == "abcde"


# .osf_parse_response(): R's `$` and jsonlite's data frames ---------------------------


def test_parse_single_resource_partial_matching() -> None:
    resp = _single(
        {
            "id": "n1",
            "type": "nodes",
            "attributes": {"titles": "A", "publicity": True, "reg_a": 1},
            "relationships": {"root_folder": {"data": {"id": "rf"}}},
        }
    )
    # U52: fields are matched by their exact names (R's `$` takes a unique
    # prefix: att$title -> titles, relationships$root -> root_folder)
    row = _osf_parse_response(resp, osf_id="n1").iloc[0]
    assert pd.isna(row["name"])
    assert pd.isna(row["public"])
    assert pd.isna(row["project"])


def test_parse_listing_null_column_is_present() -> None:
    recs = [
        {
            "id": f"f{i}",
            "type": "files",
            "attributes": {"kind": "folder", "materialized_path": None, "path": "/p"},
        }
        for i in range(2)
    ]
    # the materialized_path column exists (all NA), so `%||% att$path` is not used
    out = _osf_parse_response(recs)
    assert out["path"].isna().all()


def test_parse_listing_dollar_on_atomic_column_errors() -> None:
    recs = [
        {"id": "n1", "type": "nodes", "relationships": {"parent": {"data": None}}},
        {"id": "n2", "type": "nodes", "relationships": {"parent": {"data": None}}},
    ]
    with pytest.raises(TypeError, match="atomic vectors"):
        _osf_parse_response(recs)


def test_parse_missing_type_and_id() -> None:
    with pytest.raises(TypeError, match="missing value"):
        _osf_parse_response([{"id": "a", "type": "nodes"}, {"id": "b"}])
    with pytest.raises(TypeError, match="length zero"):
        _osf_parse_response(_single({"id": "a"}))
    assert len(_osf_parse_response([{"type": "nodes"}])) == 0
    assert len(_osf_parse_response(_single({"type": "nodes"}))) == 0


def test_parse_rejects_non_listings() -> None:
    from metacheck.archives.osf import _osf_error_result

    with pytest.raises(TypeError, match="HTTP response"):
        _osf_parse_response({"id": "a", "type": "nodes"})
    with pytest.raises(TypeError, match="empty list"):
        _osf_parse_response(_osf_error_result("not_found"))


# osf_type(), paths --------------------------------------------------------------------


def test_osf_type_empty_errors() -> None:
    with pytest.raises(ValueError, match="length zero"):
        osf_type([])


def test_r_dirname_basename() -> None:
    cases = {
        "a/b/c.txt": ("a/b", "c.txt"),
        "c.txt": (".", "c.txt"),
        "/c.txt": ("/", "c.txt"),
        "a//b": ("a", "b"),
        "a/b/": ("a", "b"),
        "": ("", ""),
    }
    for path, (d, b) in cases.items():
        assert (_r_dirname(path), _r_basename(path)) == (d, b)


def test_normalize_keeps_missing_paths_relative(tmp_path: Path) -> None:
    assert _normalize("not/there/yet") == "not/there/yet"
    assert _normalize(str(tmp_path)) == slashed(os.path.realpath(tmp_path))  # winslash = "/"
    assert _normalize("~/not-there") == os.path.expanduser("~/not-there")


def test_local_files_empty_vector_errors() -> None:
    from metacheck.archives.local import local_files

    with pytest.raises(ValueError, match="length zero"):
        local_files([])


# metadata.json: jsonlite::write_json(auto_unbox = TRUE, pretty = TRUE) ---------------------


def test_metadata_json_unboxes_and_inlines() -> None:
    meta = {
        "osf_id": "abcde",
        "title": None,
        "public": True,
        "tags": ["one"],
        "registrations": ["r1"],
        "forks": ["a", "b"],
        "contributors": [{"name": "A B", "given_name": None, "family_name": "B", "orcid": None}],
        "citation": {
            "author": [{"given": "A", "family": "B", "x": None}],
            "issued": {"date-parts": [[2019, 1, 1]]},
        },
        "wikis": [],
        "files_written": {"wiki_pages": [], "logs": None},
    }
    text = _metadata_json(meta)
    assert text.endswith("}\n")
    parsed = json.loads(text)
    assert parsed["tags"] == "one"
    assert parsed["registrations"] == "r1"
    assert parsed["forks"] == ["a", "b"]
    assert parsed["title"] is None
    assert parsed["contributors"] == [
        {"name": "A B", "given_name": None, "family_name": "B", "orcid": None}
    ]
    assert parsed["citation"]["author"] == [{"given": "A", "family": "B"}]  # NA fields dropped
    assert '"forks": ["a", "b"]' in text
    assert '"date-parts": [\n        [2019, 1, 1]\n      ]' in text


def test_metadata_download_matches_r_layout(mock_api: respx.MockRouter, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="listed only"):
        _osf_metadata_download("8uqfb", str(tmp_path))
    text = (tmp_path / "_osf_metadata" / "metadata.json").read_text(encoding="utf-8")
    assert '"registrations": "428fj",' in text
    assert text.endswith("}\n")


# .osf_walk_nodes() (R test skipped live; the component tree is recorded) ----------------


def test_walk_nodes_pngda(mock_api: respx.MockRouter) -> None:
    nodes = _osf_walk_nodes("pngda")
    assert isinstance(nodes, pd.DataFrame)
    assert list(nodes.columns) == ["osf_id", "title"]
    assert nodes["osf_id"].iloc[0] == "pngda"
    assert nodes["title"].iloc[0] == "Papercheck Test"
    assert {"ckjef", "6nt4v"} <= set(nodes["osf_id"])
    assert not nodes["osf_id"].duplicated().any()
    assert (nodes["title"] == "Raw Data").any()


# follow-up review (group A1) --------------------------------------------------------------


def test_links_on_an_empty_paper_list() -> None:
    """R: osf_links(paperlist()) is 0 x 3 (with a warning), aspredicted_links() 0 x 0."""
    import warnings

    from metacheck.archives.aspredicted import aspredicted_links
    from metacheck.archives.osf import osf_links
    from metacheck.papers import PaperList

    with pytest.warns(UserWarning, match="uninitialised column: `href`"):
        osf = osf_links(PaperList([]))
    assert list(osf.columns) == ["href", "text_id", "paper_id"] and len(osf) == 0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ap = aspredicted_links(PaperList([]))
    assert ap.shape == (0, 0)


def test_metadata_json_numbers_as_jsonlite_writes_them() -> None:
    """jsonlite's num_to_char(digits = 4), values checked in R."""
    from metacheck.archives.osf_metadata import _num_to_char

    cases = [
        (0.000012345, "0"),
        (1234.56789, "1234.5679"),
        (1e15, "1000000000000000"),
        (1e16, "10000000000000000"),
        (1e17, "1e+17"),
        (-0.00001, "-1e-05"),
        (1e-5, "1e-05"),
        (9.9999e-06, "9.9999e-06"),
        (0.00005, "0"),
        (0.00015, "0.0001"),
        (0.00025, "0.0002"),
        (0.12345, "0.1234"),
        (0.99995, "1"),
        (12345.67895, "12345.6789"),
        (2147483647.5, "2147483647.5"),
        (1e300, "1.0000000000000001e+300"),
        (0.0, "0"),
        (-0.0, "-0"),
        (-1.234e-05, "-0"),
    ]
    for x, expected in cases:
        assert _num_to_char(x) == expected, x


def test_metadata_json_simplifies_mixed_arrays_as_jsonlite() -> None:
    """write_json(fromJSON(simplifyVector = TRUE)) of mixed arrays, checked in R."""
    from metacheck.archives.osf_metadata import _metadata_json

    def tags(value: object) -> str:
        return _metadata_json({"tags": value}).split('"tags": ', 1)[1].rsplit("\n}", 1)[0]

    assert tags([1, None, 2.5]) == '[1, "NA", 2.5]'
    assert tags([True, None, 1]) == '[1, "NA", 1]'
    assert tags(["a", None, 3, True]) == '["a", null, "3", "TRUE"]'
    # data frame columns share one type across rows
    assert tags([{"a": 1}, {"a": "x"}, {"a": True}]) == (
        '[\n    {\n      "a": "1"\n    },\n    {\n      "a": "x"\n    },\n'
        '    {\n      "a": "TRUE"\n    }\n  ]'
    )
    # a nested object is a nested data frame: its NA row is {}
    assert tags([{"a": {"x": 1}}, {"a": {"x": None}}, None]) == (
        '[\n    {\n      "a": {\n        "x": 1\n      }\n    },\n    {\n      "a": {}\n    },\n'
        '    {\n      "a": {}\n    }\n  ]'
    )
    # a list column writes its NULL element as null
    assert tags([{"a": 1}, {"a": [1, 2]}, {"a": None}]) == (
        '[\n    {\n      "a": 1\n    },\n    {\n      "a": [1, 2]\n    },\n'
        '    {\n      "a": null\n    }\n  ]'
    )
