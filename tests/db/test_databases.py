"""Ports of test-db-retractionwatch.R and test-db-replications.R, plus the
bundled-database loader, refresh and update tests."""

from __future__ import annotations

import datetime as dt
import gzip
import re
from pathlib import Path

import httpx
import orjson
import pandas as pd
import pytest
import respx

from metacheck.db import databases, replications, retractionwatch
from tests.db.conftest import DATA

# retractionwatch -----------------------------------------------------------------


def test_retractionwatch_defaults() -> None:
    rw = retractionwatch.retractionwatch()
    assert list(rw.columns) == ["doi", "retractionwatch"]
    assert len(rw) >= 44784
    assert str(rw["doi"].dtype) == "string"
    pd.testing.assert_frame_equal(rw, retractionwatch.rw())
    assert retractionwatch.rw_date() == rw.attrs["date"]
    assert isinstance(retractionwatch.rw_date(), dt.date)


def test_retractionwatch_has_no_blank_doi() -> None:
    # U15: metacheck keeps the notice whose DOI is "", so joins on doi matched
    # every reference without a DOI
    rw = retractionwatch.retractionwatch()
    doi = rw["doi"]
    assert not (doi.isna() | (doi.str.strip() == "")).any()


def test_retractionwatch_returns_independent_copies() -> None:
    a = retractionwatch.retractionwatch()
    a.loc[0, "doi"] = "changed"
    a["extra"] = 1
    b = retractionwatch.retractionwatch()
    assert b.loc[0, "doi"] != "changed"
    assert "extra" not in b.columns


def test_retractionwatch_lookup() -> None:
    rw = retractionwatch.retractionwatch()
    hit = rw[rw["doi"] == "10.1177/0956797614520714"]
    assert hit["retractionwatch"].tolist() == ["Retraction"]


# FLoRA ------------------------------------------------------------------------------


def test_flora() -> None:
    f = replications.FLoRA()
    assert len(f) >= 700
    assert f.shape[1] == 8
    assert list(f.columns) == [
        "doi_o",
        "apa_ref_o",
        "doi_r",
        "apa_ref_r",
        "url_r",
        "outcome",
        "outcome_quote",
        "type",
    ]
    d = replications.FLoRA_date()
    assert re.match(r"\d{4}-\d{2}-\d{2}", d.isoformat())
    assert f.attrs["date"] == d


def test_miscite() -> None:
    m = databases.miscite()
    assert list(m.columns) == ["doi", "reftext", "warning"]
    assert m["doi"].tolist() == [
        "10.1525/collabra.33267",
        "10.1016/j.jml.2012.11.001",
        "10.1371/journal.pone.0090779",
    ]
    assert m.attrs["date"] is None


# refreshed copies in the user data directory ---------------------------------------


def _write_user_copy(name: str, date: dt.date, frame: pd.DataFrame) -> Path:
    return databases.write_database(frame, databases.user_database_path(name), name, date)


def test_newer_user_copy_wins() -> None:
    frame = pd.DataFrame(
        {
            "doi": pd.array(["10.1/new"], dtype="string"),
            "retractionwatch": pd.array(["Retraction"], dtype="string"),
        }
    )
    _write_user_copy("retractionwatch", dt.date(2999, 1, 1), frame)
    rw = retractionwatch.retractionwatch()
    assert rw["doi"].tolist() == ["10.1/new"]
    assert retractionwatch.rw_date() == dt.date(2999, 1, 1)


def test_older_user_copy_is_ignored() -> None:
    frame = pd.DataFrame(
        {
            "doi": pd.array(["10.1/old"], dtype="string"),
            "retractionwatch": pd.array(["Retraction"], dtype="string"),
        }
    )
    _write_user_copy("retractionwatch", dt.date(2000, 1, 1), frame)
    assert len(retractionwatch.retractionwatch()) > 1
    assert retractionwatch.rw_date() > dt.date(2000, 1, 1)


def test_write_read_roundtrip(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "s": pd.array(["a", None], dtype="string"),
            "i": pd.array([1, None], dtype="Int64"),
            "f": [1.5, float("nan")],
            "b": pd.array([True, None], dtype="boolean"),
        }
    )
    path = databases.write_database(frame, tmp_path / "x.json.gz", "x", dt.date(2020, 2, 3))
    obj = orjson.loads(gzip.decompress(path.read_bytes()))
    assert obj["columns"] == {"s": "string", "i": "integer", "f": "number", "b": "boolean"}
    back = databases.read_database(path)
    pd.testing.assert_frame_equal(back, frame, check_dtype=True)
    assert back.attrs["date"] == dt.date(2020, 2, 3)


def test_bundled_files_are_deterministic_gzip() -> None:
    from importlib import resources

    for name in ("FLoRA", "retractionwatch", "miscite"):
        raw = (
            resources.files("metacheck.resources.databases")
            .joinpath(f"{name}.json.gz")
            .read_bytes()
        )
        assert raw[:2] == b"\x1f\x8b"
        assert raw[4:8] == b"\x00\x00\x00\x00"  # no timestamp in the gzip header


# updates (network mocked) ----------------------------------------------------------------


def test_rw_update() -> None:
    csv = (DATA / "retractions.csv").read_bytes()
    with respx.mock() as router:
        route = router.get(
            url__regex=r"^https://api\.labs\.crossref\.org/data/retractionwatch\?"
        ).mock(return_value=httpx.Response(200, content=csv, headers={"content-type": "text/csv"}))
        path = retractionwatch.rw_update()
    assert route.called
    assert str(route.calls.last.request.url).endswith("?metacheck@scienceverse.org")
    assert re.search(r"retractionwatch\.json\.gz$", str(path))
    assert path.exists()
    rw = retractionwatch.retractionwatch()
    # U15: no blank DOI (it would match every reference without one); spaces trimmed
    assert rw["doi"].tolist() == ["10.1234/abc", "10.1016/j.jml.2012.11.001", "10.7777/space"]
    assert rw["retractionwatch"].tolist()[:2] == [
        "Retraction;Correction",
        "Expression of concern;Retraction",
    ]
    assert retractionwatch.rw_date() == dt.date.today()


def test_rw_update_bad_download() -> None:
    with respx.mock() as router:
        router.get(url__regex=r"^https://api\.labs\.crossref\.org/").mock(
            return_value=httpx.Response(500, json={"error": "x"})
        )
        with pytest.raises(RuntimeError, match="not the expected CSV"):
            retractionwatch.rw_update()


def test_flora_update() -> None:
    csv = (DATA / "flora.csv").read_bytes()
    with respx.mock() as router:
        router.get("https://api.osf.io/v2/files/t4j8f/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "attributes": {"name": "flora.csv"},
                        "links": {"download": "https://osf.io/download/t4j8f/"},
                    }
                },
            )
        )
        router.get("https://osf.io/download/t4j8f/").mock(
            return_value=httpx.Response(200, content=csv)
        )
        path = replications.FLoRA_update()
    assert path.name == "FLoRA.json.gz"
    f = replications.FLoRA()
    assert f["doi_o"].tolist() == [
        "10.1037/0003-066x.54.6.408",
        "10.1002/acp.1376",
        "10.1177/0956797614520714",
    ]
    assert f["apa_ref_o"].tolist()[1] == "Garry, M. (2007). Eyewitness."
    assert replications.FLoRA_date() == dt.date.today()


def test_flora_update_missing_file() -> None:
    with respx.mock() as router:
        router.get("https://api.osf.io/v2/files/t4j8f/").mock(
            return_value=httpx.Response(
                200, json={"data": {"attributes": {"name": "flora.xlsx"}, "links": {}}}
            )
        )
        with pytest.raises(RuntimeError, match=r"The file at osf\.io/t4j8f is missing"):
            replications.FLoRA_update()


def test_summarise_retractionwatch_matches_r_rules(tmp_path: Path) -> None:
    out = retractionwatch.summarise_retractionwatch(DATA / "retractions.csv")
    # "unavailable" DOIs, empty natures and NA DOIs are dropped; "" DOIs kept
    assert "unavailable" not in out["doi"].tolist()
    assert "10.5555/empty.nature" not in out["doi"].tolist()
    assert out["doi"].isna().sum() == 0
