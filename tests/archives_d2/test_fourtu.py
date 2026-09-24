"""Tests for pytacheck.archives.fourtu (R/archive-4tu.R has no testthat file upstream)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives.fourtu import (
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
    # R quirk: the numeric pattern is tried before the uuid one
    assert _researchdata4tu_id("10.4121/7f866e02-eb39-4a2a-8f7d-2d053ee6cde9") == "7"
    assert _researchdata4tu_id("https://data.4tu.nl/articles/dataset/x/16766929/1") == "16766929"
    assert _researchdata4tu_id(["", None, "x"]) == [None, None, None]
    assert _researchdata4tu_id([]) == []


def test_links() -> None:
    paper = pc.test_paper(["Data: 10.4121/16766929.v2."], ["https://data.4tu.nl/articles/16766929/"])
    links = researchdata4tu_links(paper)
    assert links["href"].tolist() == ["https://data.4tu.nl/articles/16766929", "10.4121/16766929.v2"]
    assert links["researchdata4tu_id"].tolist() == ["16766929", "16766929"]


def test_info_keeps_only_the_url_column(mock_api: object) -> None:
    table = pd.DataFrame({"a": [1, 2], "b": ["16766929", "x"]})
    info = researchdata4tu_info(table, id_col="b")
    assert list(info.columns[:2]) == ["researchdata4tu_url", "researchdata4tu_id"]
    assert "a" not in info.columns
    assert info["title"].tolist()[0] == "Wind tunnel measurements"
    assert pd.isna(info["researchdata4tu_id"].iloc[1])


def test_info_vector(mock_api: object) -> None:
    urls = ["https://doi.org/10.4121/16766929.v1", "10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd"]
    with pytest.warns(UserWarning, match="could not be found on data.4tu.nl"):
        info = researchdata4tu_info([*urls, "99999999"])
    assert info["title"].tolist()[:2] == ["Wind tunnel measurements", "A uuid-only dataset"]
    assert info["error"].tolist()[2] == "unfound"
    with pytest.raises(ValueError, match="row names contain missing values"):
        researchdata4tu_info(["16766929", None])


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match="data.4tu.nl seems to be offline"):
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
    import pytacheck.archives.figshare as figshare

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
