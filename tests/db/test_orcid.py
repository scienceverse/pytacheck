"""Port of metacheck tests/testthat/test-svutils-orcid.R (ORCID API mocked)."""

from __future__ import annotations

from urllib.parse import unquote

import httpx
import pandas as pd
import pytest
import respx

from metacheck.db.orcid import _initials, check_orcid, credit_roles, get_orcid, orcid_person

SEARCH_ONE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<search:search num-found="1" xmlns:search="http://www.orcid.org/ns/search"
    xmlns:common="http://www.orcid.org/ns/common">
  <search:result>
    <common:orcid-identifier>
      <common:uri>https://orcid.org/0000-0002-7523-5539</common:uri>
      <common:path>0000-0002-7523-5539</common:path>
      <common:host>orcid.org</common:host>
    </common:orcid-identifier>
  </search:result>
</search:search>"""

SEARCH_MANY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<search:search num-found="2" xmlns:search="http://www.orcid.org/ns/search"
    xmlns:common="http://www.orcid.org/ns/common">
  <search:result><common:orcid-identifier>
    <common:path>0000-0002-7523-5539</common:path></common:orcid-identifier></search:result>
  <search:result><common:orcid-identifier>
    <common:path>0000-0001-2345-6789</common:path></common:orcid-identifier></search:result>
</search:search>"""

SEARCH_NONE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<search:search num-found="0" xmlns:search="http://www.orcid.org/ns/search"/>"""

PERSON = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<person:person path="/0000-0002-7523-5539/person"
    xmlns:person="http://www.orcid.org/ns/person"
    xmlns:personal-details="http://www.orcid.org/ns/personal-details"
    xmlns:email="http://www.orcid.org/ns/email"
    xmlns:address="http://www.orcid.org/ns/address"
    xmlns:keyword="http://www.orcid.org/ns/keyword"
    xmlns:researcher-url="http://www.orcid.org/ns/researcher-url"
    xmlns:common="http://www.orcid.org/ns/common">
  <person:name visibility="public">
    <personal-details:given-names>Lisa</personal-details:given-names>
    <personal-details:family-name>DeBruine</personal-details:family-name>
  </person:name>
  <researcher-url:researcher-urls>
    <researcher-url:researcher-url>
      <researcher-url:url-name>Website</researcher-url:url-name>
      <researcher-url:url>https://debruine.github.io</researcher-url:url>
    </researcher-url:researcher-url>
  </researcher-url:researcher-urls>
  <email:emails>
    <email:email visibility="public"><email:email>lisa.debruine@glasgow.ac.uk</email:email></email:email>
    <email:email visibility="public"><email:email>debruine@gmail.com</email:email></email:email>
  </email:emails>
  <address:addresses>
    <address:address><address:country>GB</address:country></address:address>
  </address:addresses>
  <keyword:keywords>
    <keyword:keyword><keyword:content>face   perception</keyword:content></keyword:keyword>
    <keyword:keyword><keyword:content>open science</keyword:content></keyword:keyword>
  </keyword:keywords>
</person:person>"""


def _xml(text: str) -> httpx.Response:
    return httpx.Response(200, content=text.encode(), headers={"content-type": "application/xml"})


def test_credit_roles(capsys: pytest.CaptureFixture[str]) -> None:
    assert credit_roles() is None
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        "[1/con] Conceptualization: Ideas; formulation or evolution of overarching research "
        "goals and aims."
    )
    assert len(out) == 14
    assert credit_roles("abbr")[:3] == ["con", "dat", "ana"]
    assert credit_roles("names")[-1] == "Writing - review & editing"


def test_check_orcid() -> None:
    assert check_orcid("0000-0002-7523-5539") == "0000-0002-7523-5539"
    assert check_orcid("0000-0002-0247-239X") == "0000-0002-0247-239X"
    assert check_orcid("https://orcid.org/0000-0002-0247-239X") == "0000-0002-0247-239X"
    with pytest.warns(UserWarning, match="is not valid"):
        assert check_orcid("0000-0002-0247-2394") is False
    with pytest.warns(UserWarning):
        assert check_orcid("0000-0002") is False
    # U14: R fails on if (NA) for a missing ORCiD and an X before the check digit
    with pytest.warns(UserWarning, match="The ORCiD NA is not valid"):
        assert check_orcid(None) is False
    with pytest.warns(UserWarning, match="is not valid"):
        assert check_orcid("0000-000X-0247-2394") is False


def test_check_orcid_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    import warnings

    monkeypatch.setenv("PYTACHECK_VERBOSE", "0")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert check_orcid("0000-0002-0247-2394") is False


def test_initials() -> None:
    assert _initials("Lisa") == "Lisa"
    assert _initials("L") == "L*"
    assert _initials("L.") == "L*"
    assert _initials("L. M.") == "L* M*"
    assert _initials("Lisa M") == "Lisa M*"
    assert _initials("A B C") == "A* B* C*"


def test_errors() -> None:
    with pytest.raises(ValueError, match="family name"):
        get_orcid("")
    with pytest.raises(ValueError, match="family name"):
        get_orcid(None)  # type: ignore[arg-type]


def test_get_orcid() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = unquote(str(request.url))
        urls.append(url)
        if url.endswith("given-names:*"):
            return _xml(SEARCH_MANY)
        return _xml(SEARCH_ONE)

    exp = "0000-0002-7523-5539"
    with respx.mock() as router:
        router.route(host="pub.orcid.org").mock(side_effect=handler)
        assert get_orcid("DeBruine", "Lisa") == exp
        assert get_orcid("DeBruine", "L") == exp
        assert get_orcid("DeBruine", "L.") == exp
        assert get_orcid("DeBruine", "L. M.") == exp
        obs = get_orcid("DeBruine")
    assert isinstance(obs, list) and len(obs) > 1 and exp in obs
    assert (
        urls[0] == "https://pub.orcid.org/v3.0/search/?q=family-name:DeBruine+AND+given-names:Lisa"
    )
    assert urls[3].endswith("given-names:L* M*")


def test_get_orcid_none_and_failure() -> None:
    with respx.mock() as router:
        router.route(host="pub.orcid.org").mock(return_value=_xml(SEARCH_NONE))
        assert get_orcid("Nobody") == ""
    # unreadable XML: a warning and ""
    with respx.mock() as router:
        router.route(host="pub.orcid.org").mock(
            return_value=httpx.Response(
                200, content=b"<not xml", headers={"content-type": "application/xml"}
            )
        )
        with pytest.warns(UserWarning, match="ORCID search failed"):
            assert get_orcid("Nobody") == ""
    # url(..., "rb") is opened outside tryCatch() in R: an HTTP error is an error
    with respx.mock() as router:
        route = router.route(host="pub.orcid.org").mock(return_value=httpx.Response(500))
        with pytest.raises(ConnectionError, match="HTTP status was '500"):
            get_orcid("Nobody")
        assert route.call_count == 1  # no retries, like url()


def test_orcid_person() -> None:
    orcid = "0000-0002-7523-5539"
    with respx.mock() as router:
        router.get(f"https://pub.orcid.org/v3.0/{orcid}/person").mock(return_value=_xml(PERSON))
        router.get("https://pub.orcid.org/v3.0/bad/person").mock(return_value=httpx.Response(404))
        person = orcid_person([orcid, "bad"])
    assert list(person.columns) == [
        "orcid",
        "given",
        "family",
        "email",
        "country",
        "keywords",
        "urls",
        "error",
    ]
    assert person["orcid"].tolist() == [orcid, "bad"]
    assert person["given"].iat[0] == "Lisa"
    assert person["family"].iat[0] == "DeBruine"
    assert person["country"].iat[0] == "GB"
    assert person["email"].iat[0] == ["lisa.debruine@glasgow.ac.uk", "debruine@gmail.com"]
    assert person["keywords"].iat[0] == ["face perception", "open science"]
    assert person["urls"].iat[0] == ["https://debruine.github.io"]
    assert pd.isna(person["error"].iat[0])
    assert "404" in person["error"].iat[1]
