"""Tests for metacheck.archives.researchbox (port of test-archive-researchbox.R, plus more)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives.researchbox import (
    _rbox_headers,
    _rbox_info,
    rbox_file_download,
    rbox_info,
    rbox_links,
)
from tests.httpmock import UPSTREAM_TESTS

# --------------------------------------------------------------------------- testthat ports


def test_exists() -> None:
    assert callable(rbox_links)
    assert rbox_links.__doc__


def test_rbox_links() -> None:
    links = rbox_links(pc.test_paper(url=["https://researchbox.org/801"]))
    assert len(links) == 1
    assert links["href"].iloc[0] == "https://researchbox.org/801"


def test_private_rbox_info(apis: object) -> None:
    url = "https://researchbox.org/801"
    info = _rbox_info(url)

    # using `in` because RB keeps subtly changing formats
    assert info["rb_url"].iloc[0] == url
    assert "Are Empathic People Better Adjusted" in info["RB_target"].iloc[0]
    assert "CC By 4.0" in info["RB_license"].iloc[0]
    assert info["RB_public"].iloc[0] == "June 09, 2023"
    assert "Joyce He" in info["RB_authors"].iloc[0]
    assert "Stéphane Côté" in info["RB_authors"].iloc[0]
    assert "Are individuals adept at perceiving other" in info["RB_abstract"].iloc[0]

    files = info["files"].iloc[0]
    assert len(files) == 9

    # peer review version
    rb_url = "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS"
    info = _rbox_info(rb_url)
    assert info["rb_url"].iloc[0] == rb_url
    assert info["RB_public"].iloc[0] == "October 07, 2024"


def test_rbox_info(apis: object) -> None:
    paper = pc.test_paper(url=["https://researchbox.org/4377", "https://researchbox.org/6018"])
    links = rbox_links(paper)
    info = rbox_info(links, "href")
    assert info["href"].tolist() == links["href"].tolist()
    assert info["RB_public"].tolist() == ["November 28, 2025", "February 15, 2026"]


# --------------------------------------------------------------------------- more


def test_headers_look_like_a_browser() -> None:
    h = _rbox_headers()
    assert list(h) == ["Accept", "User-Agent"]
    assert h["User-Agent"].startswith("Mozilla/5.0") and h["User-Agent"].endswith("metacheck")


def test_links_collapse_file_urls_to_their_box() -> None:
    paper = pc.test_paper(
        ["See researchbox.org/801/ and https://researchbox.org/2257.8 for files."],
        [
            "https://researchbox.org/2257/72",
            "https://ResearchBox.org/2257.9",
            "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS",
        ],
    )
    hrefs = rbox_links(paper)["href"].tolist()
    assert "https://researchbox.org/2257" in hrefs
    assert "https://ResearchBox.org/2257" in hrefs
    assert "researchbox.org/801" in hrefs
    # a peer-review link is not a file link and is kept whole
    assert "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS" in hrefs


def test_info_file_ids_and_download_tokens(apis: object) -> None:
    info = _rbox_info("https://researchbox.org/801")
    files = info["files"].iloc[0]
    assert list(files.columns) == ["name", "file_id"]
    assert info["box_id"].iloc[0] == "801"
    assert info["reference"].iloc[0] == "kkqtZcbEDQeScj3Ywc8pcf0hlkrBhcKipatjzfQlJHg"


def test_unfound_box_warns(mock_api: object) -> None:
    with pytest.warns(UserWarning, match="could not be found"):
        info = _rbox_info("https://researchbox.org/404")
    assert list(info.columns) == ["rb_url", "error"]
    with pytest.warns(UserWarning, match="could not be found"):
        assert rbox_file_download("https://researchbox.org/404") is None


def test_box_without_download_tokens(mock_api: object) -> None:
    info = _rbox_info("https://researchbox.org/99")
    files = info["files"].iloc[0]
    assert files["name"].tolist() == ["data.csv", "code.R"]
    assert files["file_id"].isna().all()  # no checkboxes to pair with
    assert pd.isna(info["reference"].iloc[0])
    assert info["RB_public"].iloc[0] == "January 01, 2024"
    with pytest.warns(UserWarning, match="Could not find downloadable files"):
        assert rbox_file_download("https://researchbox.org/99") is None


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"ResearchBox\.org seems to be offline"):
        rbox_info("https://researchbox.org/801")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_file_download_posts_for_a_zip_and_lists_it(
    serve: Callable[[dict[str, httpx.Response]], list[httpx.Request]],
    online: None,
    tmp_path: Path,
) -> None:
    page = (UPSTREAM_TESTS / "apis" / "researchbox.org" / "801.html").read_bytes()
    zipped = _zip_bytes({"data/x.csv": b"a,b\n1,2\n", "README.md": b"hi\n", "run.sh": b"echo\n"})
    requests = serve(
        {
            "https://researchbox.org/801": httpx.Response(200, content=page),
            "https://researchbox.org/download_files.php": httpx.Response(200, content=zipped),
        }
    )
    files = rbox_file_download("https://researchbox.org/801")
    assert files is not None
    post = next(r for r in requests if r.method == "POST")
    body = json.loads(post.content)
    assert body["box_id"] == "801"
    assert body["reference"] == "kkqtZcbEDQeScj3Ywc8pcf0hlkrBhcKipatjzfQlJHg"
    assert len(body["files"]) == 9 and all(isinstance(v, int) for v in body["files"])
    # U46: "sh" is listed under two file types; run.sh is one row with the
    # first (metacheck repeats it once per type)
    assert files["name"].tolist() == ["data/x.csv", "README.md", "run.sh"]
    assert files["type"].tolist() == ["data", "text", "code"]
    assert all(Path(p).exists() for p in files["file_location"])

    # a second call reuses the unzipped cache without any request
    n = len(requests)
    again = rbox_file_download("https://researchbox.org/801")
    assert len(requests) == n
    assert again["name"].tolist() == files["name"].tolist()


def test_file_download_failure_warns(
    serve: Callable[[dict[str, httpx.Response]], list[httpx.Request]], online: None
) -> None:
    page = (UPSTREAM_TESTS / "apis" / "researchbox.org" / "801.html").read_bytes()
    serve(
        {
            "https://researchbox.org/6018": httpx.Response(200, content=page),
            "https://researchbox.org/download_files.php": httpx.Response(500, content=b""),
        }
    )
    with pytest.warns(UserWarning, match="Download failed or resulted in an empty file"):
        assert rbox_file_download("https://researchbox.org/6018") is None
