"""Port of metacheck's tests/testthat/test-archive-aspredicted.R."""

from __future__ import annotations

import re

import pandas as pd
import pytest
import respx

import pytacheck as pc
from pytacheck.archives import aspredicted
from pytacheck.archives.aspredicted import (
    _aspredicted_info,
    _html_text2,
    aspredicted_info,
    aspredicted_links,
)


@pytest.fixture(autouse=True)
def _online(monkeypatch: pytest.MonkeyPatch) -> None:
    # skip the DNS check in aspredicted_info(), as the R tests do
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: True)


def test_aspredicted_links(psychsci) -> None:  # type: ignore[no-untyped-def]
    links = aspredicted_links(psychsci)
    assert "text_id" in links.columns
    assert all(re.match(r"^https://aspredicted\.org", h, re.I) for h in links["href"])
    pd.testing.assert_frame_equal(links, links.drop_duplicates().reset_index(drop=True))

    urls = [
        "https://aspredicted.org/abcd1",
        "https://aspredicted.org/abcd2",
        "https://aspredicted.org/abcd3",
        "https://aspredicted.org/blind.php?x=abcd4",
        "https://aspredicted.org/abcd5.pdf",
        "https://aspredicted.org/ABC_DE6",
    ]
    assert aspredicted_links(pc.test_paper(url=urls))["href"].tolist() == urls


def test_aspredicted_links_none() -> None:
    links = aspredicted_links(pc.test_paper(["nothing"]))
    assert len(links) == 0
    assert list(links.columns) == ["href", "link_text", "text_id", "paper_id"]


def test_aspredicted_info_blind(mock_api: respx.MockRouter) -> None:
    ap_url = "https://aspredicted.org/blind.php?x=nq4xa3"
    info = aspredicted_info(ap_url)
    assert info["ap_url"].tolist() == [ap_url]
    assert info["AP_authors"].tolist() == [
        "This pre-registration is currently anonymous to enable blind peer-review.\n"
        "It has one author."
    ]


def test_aspredicted_info_pdf(mock_api: respx.MockRouter) -> None:
    ap_url = "https://aspredicted.org/ve2qn.pdf"
    info = aspredicted_info(ap_url)
    assert info["ap_url"].tolist() == [ap_url]
    assert info["AP_title"].tolist() == ["How infants encode unexpected events: a SSVEP study"]


def test_aspredicted_info_table(mock_api: respx.MockRouter) -> None:
    ap_url = pd.DataFrame(
        {
            "link": [
                "https://aspredicted.org/ve2qn.pdf",
                "https://aspredicted.org/blind.php?x=nq4xa3",
            ]
        }
    )
    info = aspredicted_info(ap_url, wait=0)
    assert info["link"].tolist() == ap_url["link"].tolist()
    assert info["AP_title"].tolist()[0] == "How infants encode unexpected events: a SSVEP study"
    assert list(info.columns)[:3] == ["link", "AP_title", "AP_authors"]


def test_aspredicted_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="seems to be offline"):
        aspredicted_info("https://aspredicted.org/x")


def test_aspredicted_info_no_valid_links() -> None:
    table = pd.DataFrame({"link": [None]}, dtype="string")
    assert aspredicted_info(table) is table


def test_private_aspredicted_info_proj(mock_api: respx.MockRouter) -> None:
    ap_url = "https://aspredicted.org/Y2F_6B7"
    info = _aspredicted_info(ap_url)
    assert info["ap_url"].tolist() == [ap_url]
    assert info["AP_title"].tolist() == [
        "Children's prosocial behavior in response to awe-inspiring art"
    ]
    assert info["AP_authors"].tolist() == [
        "This pre-registration is currently anonymous to enable blind peer-review.\n"
        "It has 5 authors."
    ]


def test_private_aspredicted_info_pdf(mock_api: respx.MockRouter) -> None:
    info = _aspredicted_info("https://aspredicted.org/ve2qn.pdf")
    assert info["AP_title"].tolist() == ["How infants encode unexpected events: a SSVEP study"]
    assert info["AP_authors"].tolist() == [
        "Moritz Köster (Freie Universität Berlin) - moritz.koester@ur.de\n"
        "Miriam Langeloh (Max Planck Institute for Human Cognitive and) - langeloh@cbs.mpg.de\n"
        "Stephanie Höhl (Max Planck Institute for Human Cognitive and) - "
        "stefanie.hoehl@univie.ac.at"
    ]


def test_private_aspredicted_info_blind(mock_api: respx.MockRouter) -> None:
    info = _aspredicted_info("https://aspredicted.org/blind.php?x=nq4xa3").iloc[0]
    assert info["AP_title"] == "Depre_ctrl_elicit [3x2] [N800 MT]"
    assert info["AP_created"] == "2021/06/14 02:17 (PT)"
    assert info["AP_data"] == "No, no data have been collected for this study yet."
    assert info["AP_hypotheses"].startswith("Participants will imagine a depressed person")
    assert info["AP_hypotheses"].endswith("to a control condition.")
    assert re.match(r"^Seven questions .* extroverted\?$", info["AP_key_dv"], re.S)
    assert re.match(
        r"^Participants will be .* quite OK psychologically speaking\.$",
        info["AP_conditions"],
        re.S,
    )
    assert info["AP_analyses"] == (
        "We will conduct two-way ANOVAs with gender and condition as IVs and all the seven DVs."
    )
    assert re.search(
        r"Participants who respond .* will be excluded from analyses\.$", info["AP_outliers"], re.S
    )
    assert info["AP_sample_size"] == "800 Mturkers."
    assert info["AP_anything_else"] == ""
    assert info["AP_version"] == "2.00"


def test_captcha_stops_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    page = "<html><body><p>CLICK after solving captcha</p></body></html>"
    with respx.mock() as router:
        route = router.get(url__startswith="https://aspredicted.org/").mock(
            return_value=httpx.Response(200, text=page)
        )
        with pytest.warns(UserWarning, match="CAPTCHA"):
            info = aspredicted_info(
                ["https://aspredicted.org/a", "https://aspredicted.org/b"], wait=0
            )
    assert route.call_count == 1
    assert info["error"].iloc[0] == "captcha"
    assert pd.isna(info["error"].iloc[1])
    assert "AP_title" not in info.columns


def test_http_error_raises() -> None:
    import httpx

    with respx.mock() as router:
        router.get("https://aspredicted.org/missing").mock(return_value=httpx.Response(404))
        with pytest.raises(RuntimeError, match="HTTP 404"):
            _aspredicted_info("https://aspredicted.org/missing")


def test_html_text2_layout() -> None:
    html = (
        "<html><body><h1>Title</h1><p>One  <b>bold</b>\n text</p><p>Two<br>lines</p>"
        "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
        "<!-- hidden --><ul><li>x</li><li>y</li></ul></body></html>"
    )
    assert _html_text2(html) == "Title\n\nOne bold text\n\nTwo\nlines\n\na\tb\nc\td\nx\ny"
    assert aspredicted._collapse_whitespace("  a   b ") == "a b"
