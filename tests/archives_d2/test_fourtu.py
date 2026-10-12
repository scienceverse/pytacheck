"""Tests for metacheck.archives.fourtu (R/archive-4tu.R has no testthat file upstream)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives.fourtu import (
    _researchdata4tu_id,
    researchdata4tu_file_download,
    researchdata4tu_info,
    researchdata4tu_links,
    researchdata4tu_pat,
)


def test_ids() -> None:
    assert _researchdata4tu_id("16766929") == "16766929"
    assert _researchdata4tu_id("https://doi.org/10.4121/16766929.v1") == "16766929"
    uuid = "ce413614-1c82-4e81-90c0-323aa7d2fabd"
    assert _researchdata4tu_id(f"10.4121/{uuid}") == uuid
    assert _researchdata4tu_id(f"10.4121/uuid:{uuid.upper()}") == uuid.upper()
    # U41: a uuid that starts with digits is the uuid (metacheck tries the
    # numeric pattern first and gives "7")
    assert (
        _researchdata4tu_id("10.4121/7f866e02-eb39-4a2a-8f7d-2d053ee6cde9")
        == "7f866e02-eb39-4a2a-8f7d-2d053ee6cde9"
    )
    assert _researchdata4tu_id("https://data.4tu.nl/articles/dataset/x/16766929/1") == "16766929"
    assert _researchdata4tu_id(["", None, "x"]) == [None, None, None]
    assert _researchdata4tu_id([]) == []


def test_links() -> None:
    paper = pc.test_paper(
        ["Data: 10.4121/16766929.v2."], ["https://data.4tu.nl/articles/16766929/"]
    )
    links = researchdata4tu_links(paper)
    assert links["href"].tolist() == [
        "https://data.4tu.nl/articles/16766929",
        "10.4121/16766929.v2",
    ]
    assert links["researchdata4tu_id"].tolist() == ["16766929", "16766929"]


def test_info_keeps_the_table(mock_api: object) -> None:
    # U33: the table's columns are kept, as by the other *_info() functions
    # (metacheck keeps only the id column)
    table = pd.DataFrame({"a": [1, 2], "b": ["16766929", "x"]})
    info = researchdata4tu_info(table, id_col="b")
    assert list(info.columns[:4]) == ["a", "b", "researchdata4tu_url", "researchdata4tu_id"]
    assert info["a"].tolist() == [1, 2]
    assert info["title"].tolist()[0] == "Wind tunnel measurements"
    assert pd.isna(info["researchdata4tu_id"].iloc[1])


def test_info_vector(mock_api: object) -> None:
    urls = ["https://doi.org/10.4121/16766929.v1", "10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd"]
    with pytest.warns(UserWarning, match="could not be found on data.4tu.nl"):
        info = researchdata4tu_info([*urls, "99999999"])
    assert info["title"].tolist()[:2] == ["Wind tunnel measurements", "A uuid-only dataset"]
    assert info["error"].tolist()[2] == "unfound"
    # U33: a vector is de-duplicated without NA (metacheck fails on the NA)
    info = researchdata4tu_info(["16766929", None, "16766929"])
    assert info["researchdata4tu_url"].tolist() == ["16766929"]
    assert info["title"].tolist() == ["Wind tunnel measurements"]


UUID = "7f866e02-eb39-4a2a-8f7d-2d053ee6cde9"


def test_resolve_uuid(serve: object) -> None:
    # metacheck #431: v2/articles/<id> needs the numeric id the DOI redirects to
    import httpx

    from metacheck.archives.fourtu import _researchdata4tu_resolve_uuid

    landing = "https://data.4tu.nl/articles/_/16766929/1"
    serve(  # type: ignore[operator]
        {
            f"https://doi.org/10.4121/uuid:{UUID}": httpx.Response(
                302, headers={"Location": landing}
            ),
            landing: httpx.Response(200, text="<html></html>"),
            "https://doi.org/10.4121/uuid:00000000-0000-0000-0000-000000000000": httpx.Response(
                302, headers={"Location": "https://data.4tu.nl/datasets/x"}
            ),
            "https://data.4tu.nl/datasets/x": httpx.Response(200),
        }
    )
    assert _researchdata4tu_resolve_uuid(UUID) == "16766929"
    # a landing page without an article id, or no redirect: the uuid itself
    zero = "00000000-0000-0000-0000-000000000000"
    assert _researchdata4tu_resolve_uuid(zero) == zero
    other = "11111111-1111-1111-1111-111111111111"
    assert _researchdata4tu_resolve_uuid(other) == other


def test_info_and_download_resolve_uuids(
    mock_api: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from metacheck.archives import fourtu

    asked: list[str] = []

    def resolve(uuid: str) -> str:
        asked.append(uuid)
        return "16766929" if uuid == UUID else uuid

    monkeypatch.setattr(fourtu, "_researchdata4tu_resolve_uuid", resolve)
    info = researchdata4tu_info([f"https://doi.org/10.4121/uuid:{UUID}", "16766929"])
    assert info["researchdata4tu_id"].tolist() == ["16766929", "16766929"]
    assert info["title"].tolist() == ["Wind tunnel measurements"] * 2
    assert asked == [UUID]
    # beyond metacheck (U41): the download resolves the uuid the same way
    dl = researchdata4tu_file_download(f"10.4121/{UUID}", download_to=str(tmp_path))
    assert dl is not None and dl["researchdata4tu_id"].tolist() == ["16766929"]


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"data\.4tu\.nl seems to be offline"):
        researchdata4tu_info("16766929")


def test_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    assert researchdata4tu_pat() == ""
    monkeypatch.setenv("RESEARCHDATA4TU_PAT", "from-env")
    assert researchdata4tu_pat() == "from-env"
    assert researchdata4tu_pat("set") == "set"
    assert researchdata4tu_pat() == "set"
    with pytest.raises(ValueError, match="single string"):
        researchdata4tu_pat(1)  # type: ignore[arg-type]


def test_file_download_uses_the_4tu_token(mock_api: object, tmp_path: Path) -> None:
    researchdata4tu_pat("tok4tu")
    seen: list[str | None] = []
    import metacheck.archives.figshare as figshare

    orig = figshare._figshare_headers

    def spy(req: dict, host: str = "api.figshare.com") -> dict:
        out = orig(req, host)
        seen.append(out["headers"].get("Authorization"))
        return out

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(figshare, "_figshare_headers", spy)
        dl = researchdata4tu_file_download("16766929", download_to=str(tmp_path))
    assert dl is not None
    assert list(dl.columns[:3]) == ["folder", "researchdata4tu_id", "id"]
    assert dl["folder"].tolist() == ["figshare_16766929"]
    assert dl["downloaded"].tolist() == [True]
    assert (tmp_path / "figshare_16766929" / "readme.txt").read_text() == "4TU readme\n"
    assert "token tok4tu" in seen
    # the Figshare token is restored afterwards
    assert figshare.figshare_pat() == ""


def test_file_download_nothing() -> None:
    assert researchdata4tu_file_download("nope") is None
    assert researchdata4tu_file_download([]) is None
