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

from pytacheck.archives.osf import (
    _normalize,
    _r_basename,
    _r_dirname,
    osf_check_id,
    osf_type,
)
from pytacheck.archives.osf_all import _osf_walk_nodes
from pytacheck.archives.osf_helpers import _osf_parse_response
from pytacheck.archives.osf_metadata import _metadata_json, _osf_metadata_download


def _single(data: dict) -> httpx.Response:
    return httpx.Response(200, content=json.dumps({"data": data}).encode())


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
    row = _osf_parse_response(resp, osf_id="n1").iloc[0]
    assert row["name"] == "A"  # att$title -> titles
    assert bool(row["public"]) is True  # att$public -> publicity
    assert row["project"] == "rf"  # relationships$root -> root_folder


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
    from pytacheck.archives.osf import _osf_error_result

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
    assert _normalize(str(tmp_path)) == os.path.realpath(tmp_path)
    assert _normalize("~/not-there") == os.path.expanduser("~/not-there")


def test_local_files_empty_vector_errors() -> None:
    from pytacheck.archives.local import local_files

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
