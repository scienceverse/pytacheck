"""Generate the ``archives_d2_review`` parity cases and their mock responses.

Written by the adversarial review of the archives_d2 port: each case targets a
branch the first round of cases did not reach (see the comment above each
group). Run from the repository root::

    python -m tests.archives_d2.make_review_cases
    python -m parity generate --area archives_d2_review
    python -m parity check --area archives_d2_review -v

Mock responses are written to ``tests/archives_d2/mocks_review`` in httptest2's
layout (``build_mock_url()`` names; ``.R`` files hold a full response with
status, headers and raw body), so R (``httptest2::with_mock_dir()``) and
Python (``tests.httpmock.replay()``) replay the same files.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from tests.archives_d2.make_mocks import q
from tests.archives_d2.make_parity_cases import (
    IGNORE_PID,
    RUN,
    Dumper,
    offline,
    online,
    rq,
    tp_py,
    tp_r,
)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "parity" / "cases" / "archives_d2_review.yaml"
MOCKS = Path(__file__).resolve().parent / "mocks_review"
MOCK = "tests/archives_d2/mocks_review"
APIS = "apis"

cases: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# mock responses
# ---------------------------------------------------------------------------


def put(path: str, content: str | bytes | dict[str, Any] | list[Any], ext: str) -> None:
    f = MOCKS / f"{path}{ext}"
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, dict | list):
        f.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif isinstance(content, bytes):
        f.write_bytes(content)
    else:
        f.write_text(content, encoding="utf-8")


def r_response(path: str, url: str, body: bytes, ctype: str, status: int = 200) -> None:
    """A full response (an ``httr2_response`` as httptest2 deparses it), raw body."""
    raw = ", ".join(f"0x{b:02x}" for b in body)
    f = MOCKS / f"{path}.R"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        f'structure(list(method = "GET", url = "{url}", status_code = {status}L, '
        f'headers = structure(list(`content-type` = "{ctype}"), class = "httr2_headers"), '
        f"body = as.raw(c({raw})), cache = new.env(parent = emptyenv())), "
        'class = "httr2_response")\n',
        encoding="utf-8",
    )


RB_SECTIONS = """<!DOCTYPE html>
<html><head><title>x</title></head>
<body>
<div>SUPPLEMENTARY FILES FOR</div> <span>My   target</span>
<div>LICENSE FOR USE</div>
  <b>CC BY</b> <i>4.0</i>
<table><tr><td>BOX PUBLIC SINCE</td> <td>June 1, 2023</td></tr></table>
<div>BOX CREATORS</div><p>Jane <b>Doe</b></p> <p>John&nbsp;Roe</p>
<h3>ABSTRACT</h3> <ul> <li>one</li> <li>two</li> </ul>
<script>$('.file_number')</script>
<p class="file_name">a.csv</p> <p class="file_name">b <b>c</b>.R</p>
<input type="checkbox" name="file1" value="11"><input type="checkbox" name="file2" value="12">
<input id="box_id" value="7001"><input id="reference" value="ref">
</body></html>
"""

RB_EMPTY_PIECE = """<html><body>
<p>SUPPLEMENTARY FILES FOR X LICENSE FOR USELICENSE FOR USE CC0 BOX PUBLIC SINCE today</p>
<p class="file_name">only.csv</p>
</body></html>
"""

EML_NS_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<eml:eml xmlns:eml="https://eml.ecoinformatics.org/eml-2.2.0" packageId="doi:10.18739/NSERR">
  <dataset>
    <title>Permafrost <x:b>cores</x:b> 2019</title>
    <creator><individualName><givenName>Ann</givenName><surName>Lee</surName></individualName></creator>
    <pubDate>2020-01-02</pubDate>
    <intellectualRights><para>CC0</para></intellectualRights>
    <dataTable><physical><objectName>cores.csv</objectName><size unit="bytes">120</size>
      <distribution><online><url>https://cn.dataone.org/cn/v2/resolve/urn:uuid:c1</url></online></distribution>
    </physical></dataTable>
  </dataset>
</eml:eml>
"""

EML_LATIN1_DECL = """<?xml version="1.0" encoding="ISO-8859-1"?>
<eml:eml xmlns:eml="https://eml.ecoinformatics.org/eml-2.2.0" packageId="doi:10.18739/LATIN">
  <dataset>
    <title>Café soils, Åland</title>
    <creator><individualName><givenName>Jörg</givenName><surName>Müller</surName></individualName></creator>
  </dataset>
</eml:eml>
"""

EML_ROOT_NS = """<eml:eml><dataset><title>Undeclared root prefix</title></dataset></eml:eml>
"""

DDI_NS_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<codeBook xmlns="ddi:codebook:2_5" version="2.5">
  <docDscr><citation><titlStmt><titl>DDI description: x</titl></titlStmt></citation></docDscr>
  <stdyDscr>
    <citation><titlStmt><titl>Barometer <x:i>survey</x:i> 2001</titl>
      <IDNo agency="DOI">10.60686/t-fsd3333</IDNo></titlStmt></citation>
    <dataAccs><useStmt><restrctn>The dataset is (A) openly available for all users.</restrctn></useStmt></dataAccs>
  </stdyDscr>
  <fileDscr ID="F1"><fileTxt><fileName>daF3333.sav</fileName><dimensns><caseQnty>1e3</caseQnty><varQnty> 42 </varQnty></dimensns><fileType>SPSS</fileType></fileTxt></fileDscr>
</codeBook>
"""

DDI_FILES = """<?xml version="1.0" encoding="UTF-8"?>
<codeBook xmlns="ddi:codebook:2_5" version="2.5">
  <stdyDscr><citation><titlStmt><titl>Files study</titl>
    <IDNo agency="FSD">FSD3335</IDNo></titlStmt></citation></stdyDscr>
  <fileDscr ID="F1"><fileTxt><fileName>a.sav</fileName><dimensns><caseQnty>3.7</caseQnty><varQnty>0x1A</varQnty></dimensns></fileTxt></fileDscr>
  <fileDscr ID="F2"><fileTxt><fileName>b.csv</fileName><dimensns><caseQnty>2147483648</caseQnty><varQnty>-2147483647.5</varQnty></dimensns><fileType>CSV</fileType></fileTxt></fileDscr>
  <fileDscr ID="F3"><fileTxt><dimensns><caseQnty>Inf</caseQnty><varQnty>1,000</varQnty></dimensns></fileTxt></fileDscr>
  <fileDscr ID="F4"><fileTxt><fileName></fileName><dimensns><caseQnty/><varQnty>1e-2</varQnty></dimensns></fileTxt></fileDscr>
</codeBook>
"""

DDI_LATIN1_DECL = """<?xml version="1.0" encoding="ISO-8859-1"?>
<codeBook xmlns="ddi:codebook:2_5" version="2.5">
  <stdyDscr><citation><titlStmt><titl>Työ ja perää 2002</titl></titlStmt></citation></stdyDscr>
</codeBook>
"""


def write_mocks() -> None:
    shutil.rmtree(MOCKS, ignore_errors=True)

    # ResearchBox: html_text2() sections of a page with blank text between blocks
    put("researchbox.org/7001", RB_SECTIONS, ".html")
    # a section heading repeated back to back: the piece between them is ""
    put("researchbox.org/7002", RB_EMPTY_PIECE, ".html")
    # 200 with a body that has no markup: xml2::read_html() takes it for a file path
    r_response("researchbox.org/7003", "https://researchbox.org/7003", b"OK", "text/html")
    # 200 with bytes that are not UTF-8: resp_body_string() is NA
    r_response("researchbox.org/7004", "https://researchbox.org/7004", b"<p>\xff</p>", "text/html")
    # a Latin-1 page that says so: converted to UTF-8 first
    r_response(
        "researchbox.org/7005",
        "https://researchbox.org/7005",
        '<html><body><p class="file_name">café.csv</p>'
        "<p>SUPPLEMENTARY FILES FOR Café LICENSE FOR USE x</p></body></html>".encode("latin-1"),
        "text/html; charset=ISO-8859-1",
    )
    # a NUL byte: readBin(what = character()) stops there
    r_response(
        "researchbox.org/7006",
        "https://researchbox.org/7006",
        b'<html><body><p class="file_name">a.csv</p>\x00<p class="file_name">b.csv</p>'
        b"</body></html>",
        "text/html",
    )
    # a charset iconv() does not know: an error
    r_response(
        "researchbox.org/7007",
        "https://researchbox.org/7007",
        b"<html><body>x</body></html>",
        "text/html; charset=x-no-such-charset",
    )
    # two redirects in one page: httr2::request() of two URLs is an error
    put(
        "researchbox.org/7008",
        "<html><head><script>window.location.replace('https://researchbox.org/801')</script>"
        "<script>window.location.replace('https://researchbox.org/4377')</script></head>"
        "<body></body></html>\n",
        ".html",
    )

    # DataONE: libxml2 errors that are not fatal (xml2 keeps the document)
    base = "arcticdata.io/metacat/d1/mn/v2/object/doi%3A10.18739%2F"
    put(base + "NSERR", EML_NS_ERROR, ".xml")
    # an XML declaration naming Latin-1 over UTF-8 text: xml2 parses a string as UTF-8
    put(base + "LATIN", EML_LATIN1_DECL, ".xml")
    # an undeclared prefix on the root: its name stays "eml:eml", not "eml"
    put(base + "ROOTNS", EML_ROOT_NS, ".xml")
    # bytes that are not UTF-8
    r_response(
        base + "BADUTF",
        "https://arcticdata.io/metacat/d1/mn/v2/object/doi%3A10.18739%2FBADUTF",
        b"<eml:eml xmlns:eml='x'><dataset><title>\xff</title></dataset></eml:eml>",
        "application/xml",
    )
    # a NUL byte after the document: resp_body_string() stops there
    r_response(
        base + "NUL",
        "https://arcticdata.io/metacat/d1/mn/v2/object/doi%3A10.18739%2FNUL",
        b"<eml:eml xmlns:eml='x'><dataset><title>Before NUL</title></dataset></eml:eml>"
        b"\x00trailing garbage",
        "application/xml",
    )

    # FSD: the same XML cases for the DDI records
    put("services.fsd.tuni.fi/catalogue/FSD3333/DDI/FSD3333_eng.xml", DDI_NS_ERROR, ".xml")
    put("services.fsd.tuni.fi/catalogue/FSD3334/DDI/FSD3334_eng.xml", DDI_LATIN1_DECL, ".xml")

    # legacy DSpace (qsardb.org): JSON arrays where R's vapply(character(1)) needs a string
    put("qsardb.org/rest/handle/10967/901", {"uuid": "q-901", "name": "Item 901"}, ".json")
    put(
        q("qsardb.org/rest/items/q-901", "expand=metadata"),
        {
            "uuid": "q-901",
            "metadata": [
                {"key": "dc.title", "value": "Item 901"},
                {"key": "dc.contributor.author", "value": ["Doe, Jane"]},
            ],
        },
        ".json",
    )
    put("qsardb.org/rest/handle/10967/902", {"uuid": "q-902", "name": "Item 902"}, ".json")
    put(
        q("qsardb.org/rest/items/q-902", "expand=metadata"),
        {"uuid": "q-902", "metadata": [{"key": "dc.rights", "value": "CC0"}]},
        ".json",
    )
    put(
        q("qsardb.org/rest/items/q-902/bitstreams", "limit=1000"),
        [{"name": ["x.csv"], "sizeBytes": 3, "retrieveLink": "/rest/bitstreams/1/retrieve"}],
        ".json",
    )
    # ... and a metadata list (not a list of records) that is a JSON object
    put("qsardb.org/rest/handle/10967/903", {"uuid": "q-903", "name": "Item 903"}, ".json")
    put(
        q("qsardb.org/rest/items/q-903", "expand=metadata"),
        {
            "uuid": "q-903",
            "metadata": {
                "a": {"key": "dc.contributor.author", "value": "Roe, Ann"},
                "b": {"key": "dc.contributor.author", "value": "Poe, Ed"},
                "c": {"key": "dc.rights", "value": []},
            },
        },
        ".json",
    )
    put(
        q("qsardb.org/rest/items/q-903/bitstreams", "limit=1000"),
        {
            "one": {"name": "a.csv", "sizeBytes": "12", "retrieveLink": "/rest/bitstreams/9/r"},
            "two": {"name": [], "sizeBytes": None},
        },
        ".json",
    )

    # DSpace 7 (scholarworks.umass.edu): the same, in the HAL-JSON shapes
    items = "scholarworks.umass.edu/server/api/core/items/"
    u1 = "11111111-2222-3333-4444-555555555555"
    put(
        items + u1,
        {"uuid": u1, "name": "N1", "metadata": {"dc.contributor.author": [{"value": ["A"]}]}},
        ".json",
    )
    u2 = "22222222-3333-4444-5555-666666666666"
    put(
        items + u2,
        {"uuid": u2, "name": "N2", "metadata": {"dc.contributor.author": [{"value": "B"}]}},
        ".json",
    )
    put(
        items + u2 + "/bundles",
        {"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "bb-2"}]}},
        ".json",
    )
    put(
        "scholarworks.umass.edu/server/api/core/bundles/bb-2/bitstreams",
        {"_embedded": {"bitstreams": [{"name": ["a.csv"], "sizeBytes": 1}]}},
        ".json",
    )
    # metadata values in several shapes, two ORIGINAL bundles (the last wins)
    u3 = "33333333-4444-5555-6666-777777777777"
    put(
        items + u3,
        {
            "uuid": u3,
            "name": [],
            "lastModified": None,
            "metadata": {
                "dc.title": [{"value": None}, {"value": "Title 3"}, {"value": []}],
                "dc.contributor.author": {"x": {"value": "C"}, "y": {"value": "D"}},
                "dc.rights": [],
                "dc.rights.uri": [{"value": "https://creativecommons.org/licenses/by/4.0/"}],
                "dc.date.issued": [{"value": None}],
                "dc.date.available": [{"value": "2020"}],
            },
        },
        ".json",
    )
    put(
        items + u3 + "/bundles",
        {
            "_embedded": {
                "bundles": [
                    {"name": "ORIGINAL", "uuid": "bb-3a"},
                    {"name": "LICENSE", "uuid": "bb-3l"},
                    {"name": "ORIGINAL", "uuid": "bb-3b"},
                ]
            }
        },
        ".json",
    )
    put(
        "scholarworks.umass.edu/server/api/core/bundles/bb-3b/bitstreams",
        {
            "_embedded": {
                "bitstreams": [
                    {
                        "name": "data.tar.gz",
                        "sizeBytes": "2048",
                        "checkSum": {"value": "abc"},
                        "_links": {"content": {"href": "https://x/content"}},
                    },
                    {"name": "README", "sizeBytes": None, "checkSum": {}, "_links": {}},
                ]
            }
        },
        ".json",
    )

    # --- JSON bodies as httr2::resp_body_json() reads them: always as UTF-8, up to a
    # NUL byte, parsed by jsonlite (a BOM is dropped, NaN refused, the first of two
    # repeated keys is what `$` finds, an escaped NUL ends a string)
    bom = b"\xef\xbb\xbf"
    js = "application/json"
    legacy = "qsardb.org/rest"
    # 904: handle with a BOM, metadata with a NUL and trailing junk, bitstreams
    # declared Latin-1 but sent as UTF-8
    r_response(
        f"{legacy}/handle/10967/904",
        "https://qsardb.org/rest/handle/10967/904",
        bom + b'{"uuid": "q-904", "name": "Item 904"}',
        js,
    )
    r_response(
        q(f"{legacy}/items/q-904", "expand=metadata"),
        "https://qsardb.org/rest/items/q-904?expand=metadata",
        b'{"metadata": [{"key": "dc.rights", "value": "CC0"}]}\x00{"junk": true}',
        js,
    )
    r_response(
        q(f"{legacy}/items/q-904/bitstreams", "limit=1000"),
        "https://qsardb.org/rest/items/q-904/bitstreams?limit=1000",
        '[{"name": "café.csv", "sizeBytes": 7, "retrieveLink": "/rest/bitstreams/4/retrieve"}]'.encode(),
        "application/json; charset=ISO-8859-1",
    )
    # 905: a repeated key; the item name holds an escaped NUL
    r_response(
        f"{legacy}/handle/10967/905",
        "https://qsardb.org/rest/handle/10967/905",
        b'{"uuid": "q-905", "name": "Item\\u0000 905", "uuid": "q-zzz"}',
        js,
    )
    put(
        q(f"{legacy}/items/q-905", "expand=metadata"),
        {"metadata": [{"key": "dc.contributor.author", "value": "Doe, Jane"}]},
        ".json",
    )
    put(q(f"{legacy}/items/q-905/bitstreams", "limit=1000"), [], ".json")
    # 906: Latin-1 bytes (declared as such): not UTF-8, so no item
    r_response(
        f"{legacy}/handle/10967/906",
        "https://qsardb.org/rest/handle/10967/906",
        '{"uuid": "q-906", "name": "Café"}'.encode("latin-1"),
        "application/json; charset=ISO-8859-1",
    )
    # 907: NaN is not JSON to jsonlite
    r_response(
        f"{legacy}/handle/10967/907",
        "https://qsardb.org/rest/handle/10967/907",
        b'{"uuid": "q-907", "size": NaN}',
        js,
    )
    # 909: a +json media type is JSON too; text/plain is not
    r_response(
        f"{legacy}/handle/10967/909",
        "https://qsardb.org/rest/handle/10967/909",
        b'{"uuid": "q-909"}',
        "application/hal+json;charset=UTF-8",
    )
    put(q(f"{legacy}/items/q-909", "expand=metadata"), {"metadata": []}, ".json")
    r_response(
        q(f"{legacy}/items/q-909/bitstreams", "limit=1000"),
        "https://qsardb.org/rest/items/q-909/bitstreams?limit=1000",
        b'[{"name": "x.csv"}]',
        "text/plain",
    )

    api = "data.mendeley.com/public-api/datasets"
    rec = '{"name": "Café data", "doi": {"id": "10.17632/bomds.1"}, "files": []}'
    r_response(
        f"{api}/bomds",
        "https://data.mendeley.com/public-api/datasets/bomds",
        bom + rec.encode(),
        js,
    )
    r_response(
        f"{api}/nands",
        "https://data.mendeley.com/public-api/datasets/nands",
        b'{"name": "x", "version": Infinity}',
        js,
    )
    r_response(
        f"{api}/latinds",
        "https://data.mendeley.com/public-api/datasets/latinds",
        rec.encode("latin-1"),
        "application/json; charset=latin1",
    )
    r_response(
        f"{api}/dupds",
        "https://data.mendeley.com/public-api/datasets/dupds",
        b'{"name": "first", "name": "second", "contributors": [{"first_name": "A",'
        b' "last_name": "B", "last_name": "C"}]}',
        js,
    )

    eprint = "reshare.ukdataservice.ac.uk/id/eprint"
    r_response(
        f"{eprint}/854910",
        "https://reshare.ukdataservice.ac.uk/id/eprint/854910",
        bom + b'{"title": "T\\u0000rest", "datestamp": "2020", "documents": [{"content": "data",'
        b' "files": [{"fileid": 3000000000, "filename": "a.csv"}]}]}\x00x',
        js,
    )
    r_response(
        f"{eprint}/854911",
        "https://reshare.ukdataservice.ac.uk/id/eprint/854911",
        b'{"title": "x", "lastmod": -Infinity}',
        js,
    )

    items7 = "scholarworks.umass.edu/server/api/core/items/"
    u4 = "44444444-5555-6666-7777-888888888888"
    r_response(
        items7 + u4,
        f"https://scholarworks.umass.edu/server/api/core/items/{u4}",
        bom
        + (
            f'{{"uuid": "{u4}", "name": "N4", "uuid": "other", "metadata": '
            '{"dc.title": [{"value": "T"}], "dc.title": [{"value": "U"}]}}'
        ).encode(),
        "application/json;charset=UTF-8",
    )
    r_response(
        items7 + u4 + "/bundles",
        f"https://scholarworks.umass.edu/server/api/core/items/{u4}/bundles",
        b'{"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "bb-4"}]}}',
        "application/json; charset=ISO-8859-1",
    )
    r_response(
        "scholarworks.umass.edu/server/api/core/bundles/bb-4/bitstreams",
        "https://scholarworks.umass.edu/server/api/core/bundles/bb-4/bitstreams",
        b'{"_embedded": {"bitstreams": [{"name": "caf\xc3\xa9.csv", "sizeBytes": 5}]}}',
        "application/json; charset=ISO-8859-1",
    )

    # --- arrays where strings are expected: `obj$x <- <list>` makes a list column (or
    # fails), paste0() drops an empty list and deparses a nested one, and an array of
    # two ids builds two URLs, which httr2::request() refuses
    put(
        f"{legacy}/handle/10967/910",
        {"uuid": ["q-910"], "name": ["Item 910"]},
        ".json",
    )
    put(q(f"{legacy}/items/q-910", "expand=metadata"), {"metadata": []}, ".json")
    put(
        q(f"{legacy}/items/q-910/bitstreams", "limit=1000"),
        [
            {"name": "a.csv", "retrieveLink": []},
            {"name": "b.csv", "retrieveLink": ["/rest/bitstreams/9/retrieve"]},
            {"name": "c.csv", "retrieveLink": [None]},
        ],
        ".json",
    )
    put(f"{legacy}/handle/10967/911", {"uuid": ["q-a", "q-b"], "name": []}, ".json")
    put(
        f"{api}/shapeds",
        {
            "name": ["N"],
            "contributors": [
                {"first_name": [None], "last_name": ["B"]},
                {"first_name": {"x": "C"}},
                {"first_name": [[1, "two"]], "last_name": {"k": {"a b": True}}},
            ],
            "data_licence": {"short_name": [], "full_name": "CC BY 4.0"},
            "publish_date": 20200101,
            "modified_on": 3000000000,
        },
        ".json",
    )
    put(f"{api}/twonames", {"name": ["a", "b"]}, ".json")
    put(
        f"{eprint}/854912",
        {
            "title": ["T"],
            "doi": "10.5255/UKDA-SN-854912",
            "creators": [{"name": {"given": ["Ann"], "family": []}}, None],
            "documents": [],
        },
        ".json",
    )
    u5 = "55555555-6666-7777-8888-999999999999"
    put(items7 + u5, {"uuid": ["u5x"], "name": "N5", "lastModified": [None]}, ".json")

    # FSD: several files, integers in R's as.integer() forms, no DOI
    put(
        "services.fsd.tuni.fi/catalogue/FSD3335/DDI/FSD3335_eng.xml",
        DDI_FILES,
        ".xml",
    )

    # ReShare: file entries that are not records
    put(
        "reshare.ukdataservice.ac.uk/id/eprint/854900",
        {
            "title": "Odd files",
            "creators": [{"name": {"given": "Ann", "family": None}}, {"name": {}}],
            "documents": [
                {"content": "data", "files": ["just-a-string", None]},
                {"files": [{"fileid": 7, "filename": "a.csv", "content": "old"}]},
            ],
        },
        ".json",
    )


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


def expr_case(
    id_: str, r: str, py: str, mock: str | None = MOCK, compare: dict[str, Any] | None = None
) -> None:
    c: dict[str, Any] = {
        "id": id_,
        "r": "identity",
        "py": RUN,
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    if mock:
        c["mock_dir"] = mock
    if compare:
        c["compare"] = compare
    cases.append(c)


def rbox_info_case(name: str, box: str, mock: str = MOCK) -> None:
    url = box if "/" in box else f"https://researchbox.org/{box}"
    expr_case(
        f".rbox_info.review.{name}",
        f"metacheck:::.rbox_info({rq(url)})",
        f"lambda m: m.researchbox._rbox_info({rq(url)})",
        mock,
    )


# ResearchBox: the page is parsed and sectioned exactly as xml2 + rvest do
rbox_info_case("sections", "7001")
rbox_info_case("empty_piece", "7002")
rbox_info_case("no_markup_error", "7003")
rbox_info_case("bad_utf8_error", "7004")
rbox_info_case("latin1", "7005")
rbox_info_case("nul", "7006")
rbox_info_case("bad_charset_error", "7007")
rbox_info_case("two_redirects_error", "7008")
# a bare mention from rbox_links() has no scheme: libcurl requests http://...
rbox_info_case("schemeless", "researchbox.org/801", APIS)
BARE = [
    "Materials: researchbox.org/801 and https://researchbox.org/801/ (see researchbox.org/801)."
]
expr_case(
    "rbox_info.review.from_bare_links",
    online(f"rbox_info(rbox_links({tp_r(text=BARE)}))"),
    f"lambda m: m.researchbox.rbox_info(m.researchbox.rbox_links({tp_py(text=BARE)}))",
    APIS,
    IGNORE_PID,
)
# list.files() leaves out hidden files and folders (all.files = FALSE)
HIDDEN = {
    "researchbox.org_801/unzipped/x.csv": "a,b",
    "researchbox.org_801/unzipped/.DS_Store": "junk",
    "researchbox.org_801/unzipped/__MACOSX/._x.csv": "junk",
    "researchbox.org_801/unzipped/.hidden/y.txt": "hidden dir",
    "researchbox.org_801/unzipped/sub/.z": "hidden file",
    "researchbox.org_801/unzipped/sub/z.txt": "z",
}
_hidden_r = "; ".join(
    f"p <- file.path(d, {rq(k)}); dir.create(dirname(p), recursive = TRUE, "
    f"showWarnings = FALSE); writeLines({rq(v)}, p)"
    for k, v in HIDDEN.items()
)
expr_case(
    "rbox_file_download.review.hidden_files",
    "(function(f) { d <- tempfile(); dir.create(d); on.exit(unlink(d, recursive = TRUE)); "
    f"f(d) }})(function(d) {{ {_hidden_r}; old <- options(metacheck.repo_cache.dir = d); "
    "on.exit(options(old), add = TRUE); "
    'res <- rbox_file_download("https://researchbox.org/801"); '
    'res$file_location <- sub(".*/unzipped/", "", res$file_location); res })',
    f"lambda m: m.repo_cache({json.dumps(HIDDEN)}, lambda d: (lambda res: "
    'res.assign(file_location=[str(v).split("/unzipped/", 1)[-1] for v in '
    'res["file_location"]]))(m.researchbox.rbox_file_download("https://researchbox.org/801")))',
)

# NULL input: data.frame(<url> = NULL) has no columns
for fn, mod in (
    ("psycharchives_info", "psycharchives"),
    ("rbox_info", "researchbox"),
    ("fsd_info", "fsd"),
):
    expr_case(f"{fn}.review.null", online(f"{fn}(NULL)"), f"lambda m: m.{mod}.{fn}(None)", None)
expr_case(
    "researchdata4tu_info.review.null_error",
    online("researchdata4tu_info(NULL)"),
    "lambda m: m.fourtu.researchdata4tu_info(None)",
    None,
)
expr_case(
    "researchdata4tu_info.review.null_offline_error",
    offline("researchdata4tu_info(NULL)"),
    "lambda m: m.offline(lambda: m.fourtu.researchdata4tu_info(None))",
    None,
)


# DataONE: lenient XML parsing (as xml2), text decoded as httr2 does
def dataone_info_case(name: str, pid: str) -> None:
    expr_case(
        f".dataone_info.review.{name}",
        f'metacheck:::.dataone_info("doi:10.18739/{pid}", host = "arcticdata.io")',
        f'lambda m: m.dataone._dataone_info("doi:10.18739/{pid}", host="arcticdata.io")',
    )


dataone_info_case("ns_error", "NSERR")
dataone_info_case("latin1_declaration", "LATIN")
dataone_info_case("root_prefix_undeclared", "ROOTNS")
dataone_info_case("bad_utf8", "BADUTF")
dataone_info_case("nul", "NUL")
expr_case(
    "dataone_info.review.table",
    online(
        'dataone_info(data.frame(x = 1:3, u = c("https://arcticdata.io/catalog/view/'
        'doi:10.18739/NSERR", "10.18739/LATIN", "10.18739/NSERR")), id_col = "u")'
    ),
    'lambda m: m.dataone.dataone_info(m.pd.DataFrame({"x": [1, 2, 3], "u": ['
    '"https://arcticdata.io/catalog/view/doi:10.18739/NSERR", "10.18739/LATIN", '
    '"10.18739/NSERR"]}), id_col="u")',
)

# FSD: the same for the DDI records
for name, study in (("ns_error", "FSD3333"), ("latin1_declaration", "FSD3334")):
    expr_case(
        f".fsd_info.review.{name}",
        f'metacheck:::.fsd_info("https://services.fsd.tuni.fi/catalogue/{study}")',
        f'lambda m: m.fsd._fsd_info("https://services.fsd.tuni.fi/catalogue/{study}")',
    )

# legacy DSpace: vapply(..., character(1)) over parsed JSON
for name, handle in (("list_value_error", "901"), ("list_name_error", "902"), ("objects", "903")):
    expr_case(
        f".psycharchives_info.review.{name}",
        f'metacheck:::.psycharchives_info("https://qsardb.org/repository/handle/10967/{handle}")',
        f"lambda m: m.psycharchives._psycharchives_info("
        f'"https://qsardb.org/repository/handle/10967/{handle}")',
    )
expr_case(
    "psycharchives_file_download.review.objects",
    'psycharchives_file_download("https://qsardb.org/repository/handle/10967/903")',
    "lambda m: m.psycharchives.psycharchives_file_download("
    '"https://qsardb.org/repository/handle/10967/903")',
)

# DSpace 7: the same
for name, uuid in (
    ("list_value_error", "11111111-2222-3333-4444-555555555555"),
    ("list_name_error", "22222222-3333-4444-5555-666666666666"),
    ("shapes", "33333333-4444-5555-6666-777777777777"),
):
    expr_case(
        f".dspace7_info.review.{name}",
        f'metacheck:::.dspace7_info("scholarworks.umass.edu", uuid = "{uuid}")',
        f'lambda m: m.dspace7._dspace7_info("scholarworks.umass.edu", uuid="{uuid}")',
    )
expr_case(
    "dspace7_file_download.review.shapes",
    'dspace7_file_download("https://scholarworks.umass.edu/items/'
    '33333333-4444-5555-6666-777777777777")',
    "lambda m: m.dspace7.dspace7_file_download("
    '"https://scholarworks.umass.edu/items/33333333-4444-5555-6666-777777777777")',
)

# ReShare: `f$content <-` on a file entry that is a string (or null)
expr_case(
    ".reshare_info.review.atomic_files",
    'metacheck:::.reshare_info("854900")',
    'lambda m: m.reshare._reshare_info("854900")',
)

# *_links(): PCRE caseless matching in UTF mode (Kelvin sign, long s) and the
# literal prefilter the port runs before the regex
UNICODE_TEXT = [
    "Data: Knb.ecoinformatics.org/view/doi:10.5063/F1K and 10.5063/KX.",
    "Deposit reſhare.ukdataservice.ac.uk/854001 and 10.5255/UKDA-ſN-854002.",
    "Box REſEARCHBOX.ORG/801, data.4tu.nl/articles/x and FSD1234 (fſd2345).",
    "Mendeley DATA.MENDELEY.COM/datasets/abc123/2 and 10.17632/XYZ.1; PsychArchives "
    "hdl.handle.net/20.500.12034/17526 and www.pſycharchives.org/en/item/abc",
    "DSpace 7: HTTPS://REPOSITORY.GATECH.EDU/handle/1853/67239. and "
    "https://ulir.ul.ie/entities/publication/x",
]
for name, mod in (
    ("dataone_links", "dataone"),
    ("reshare_links", "reshare"),
    ("rbox_links", "researchbox"),
    ("researchdata4tu_links", "fourtu"),
    ("fsd_links", "fsd"),
    ("mendeley_links", "mendeley"),
    ("psycharchives_links", "psycharchives"),
    ("dspace_links", "psycharchives"),
    ("dspace7_links", "dspace7"),
):
    expr_case(
        f"{name}.review.unicode_case",
        f"{name}({tp_r(text=UNICODE_TEXT)})",
        f"lambda m: m.{mod}.{name}({tp_py(text=UNICODE_TEXT)})",
        None,
        IGNORE_PID,
    )


# --- second review round -----------------------------------------------------

# an empty paper list: paper_table(, "url") has no columns and text_search() no text
for name, mod in (
    ("dataone_links", "dataone"),
    ("reshare_links", "reshare"),
    ("rbox_links", "researchbox"),
    ("researchdata4tu_links", "fourtu"),
    ("fsd_links", "fsd"),
    ("mendeley_links", "mendeley"),
    ("psycharchives_links", "psycharchives"),
    ("dspace_links", "psycharchives"),
    ("dspace7_links", "dspace7"),
):
    expr_case(
        f"{name}.review.empty_paperlist",
        f'{name}(paperlist(test_paper("a"))[0])',
        f"lambda m: m.{mod}.{name}(m.pc.PaperList([]))",
        None,
    )

# NULL input: data.frame(<url> = NULL) has no columns, so the join fails
expr_case(
    "dataone_info.review.null_error",
    "dataone_info(NULL)",
    "lambda m: m.dataone.dataone_info(None)",
    None,
)
for fn, mod in (("mendeley_info", "mendeley"), ("reshare_info", "reshare")):
    expr_case(
        f"{fn}.review.null_error", online(f"{fn}(NULL)"), f"lambda m: m.{mod}.{fn}(None)", None
    )

# JSON read as httr2 + jsonlite read it (see the mocks)
for handle in ("904", "905", "906", "907", "909"):
    expr_case(
        f".psycharchives_info.review.json_{handle}",
        f'metacheck:::.psycharchives_info("https://qsardb.org/repository/handle/10967/{handle}")',
        f"lambda m: m.psycharchives._psycharchives_info("
        f'"https://qsardb.org/repository/handle/10967/{handle}")',
    )
expr_case(
    "psycharchives_file_download.review.json_904",
    'psycharchives_file_download("https://qsardb.org/repository/handle/10967/904")',
    "lambda m: m.psycharchives.psycharchives_file_download("
    '"https://qsardb.org/repository/handle/10967/904")',
)
for ds in ("bomds", "nands", "latinds", "dupds"):
    expr_case(
        f".mendeley_info.review.json_{ds}",
        f'metacheck:::.mendeley_info("{ds}")',
        f'lambda m: m.mendeley._mendeley_info("{ds}")',
    )
expr_case(
    "mendeley_info.review.json_vector",
    online('mendeley_info(c("bomds", "https://data.mendeley.com/datasets/nands/2", "dupds"))'),
    'lambda m: m.mendeley.mendeley_info(["bomds", "https://data.mendeley.com/datasets/nands/2",'
    ' "dupds"])',
)
for eid in ("854910", "854911"):
    expr_case(
        f".reshare_info.review.json_{eid}",
        f'metacheck:::.reshare_info("{eid}")',
        f'lambda m: m.reshare._reshare_info("{eid}")',
    )
expr_case(
    ".dspace7_info.review.json",
    'metacheck:::.dspace7_info("scholarworks.umass.edu", '
    'uuid = "44444444-5555-6666-7777-888888888888")',
    'lambda m: m.dspace7._dspace7_info("scholarworks.umass.edu", '
    'uuid="44444444-5555-6666-7777-888888888888")',
)

# arrays where strings are expected (see the mocks)
for handle in ("910", "911"):
    expr_case(
        f".psycharchives_info.review.arrays_{handle}",
        f'metacheck:::.psycharchives_info("https://qsardb.org/repository/handle/10967/{handle}")',
        f"lambda m: m.psycharchives._psycharchives_info("
        f'"https://qsardb.org/repository/handle/10967/{handle}")',
    )
for ds in ("shapeds", "twonames"):
    expr_case(
        f".mendeley_info.review.arrays_{ds}",
        f'metacheck:::.mendeley_info("{ds}")',
        f'lambda m: m.mendeley._mendeley_info("{ds}")',
    )
expr_case(
    ".reshare_info.review.arrays",
    'metacheck:::.reshare_info("854912")',
    'lambda m: m.reshare._reshare_info("854912")',
)
expr_case(
    ".dspace7_info.review.arrays",
    'metacheck:::.dspace7_info("scholarworks.umass.edu", '
    'uuid = "55555555-6666-7777-8888-999999999999")',
    'lambda m: m.dspace7._dspace7_info("scholarworks.umass.edu", '
    'uuid="55555555-6666-7777-8888-999999999999")',
)

# table input whose columns collide with the looked-up fields: stale id columns are
# dropped, a clashing field gets the suffix, the id column is not the first
MOCK1 = "tests/archives_d2/mocks"
PA = "https://hdl.handle.net/20.500.12034/17526"
expr_case(
    "psycharchives_info.review.table_collisions",
    online(
        f"psycharchives_info(data.frame(x = 1:3, href = c({rq(PA)}, NA, {rq(PA)}), "
        'pa_url = c("old1", "old2", "old3"), PA_title = "mine"), id_col = 2)'
    ),
    f"lambda m: m.psycharchives.psycharchives_info(m.pd.DataFrame({{'x': [1, 2, 3], "
    f"'href': [{rq(PA)}, None, {rq(PA)}], 'pa_url': ['old1', 'old2', 'old3'], "
    "'PA_title': ['mine'] * 3}), id_col=2)",
    APIS,
)
expr_case(
    "mendeley_info.review.table_collisions",
    online(
        'mendeley_info(data.frame(u = c("10.17632/vjtxybrc28.2", "zzz999", "nope nope"), '
        'mendeley_url = "old", mendeley_id = "stale", title = "mine"), id_col = "u")'
    ),
    "lambda m: m.mendeley.mendeley_info(m.pd.DataFrame({'u': ['10.17632/vjtxybrc28.2', "
    "'zzz999', 'nope nope'], 'mendeley_url': ['old'] * 3, 'mendeley_id': ['stale'] * 3, "
    "'title': ['mine'] * 3}), id_col='u')",
    MOCK1,
)
expr_case(
    "dataone_info.review.table_collisions",
    'dataone_info(data.frame(dataone_pid = "stale", u = c("10.18739/A2GT5FG86", '
    '"https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86", "10.18739/MISSING"), '
    'dataone_host = "stale", title = "mine"), id_col = 2)',
    "lambda m: m.dataone.dataone_info(m.pd.DataFrame({'dataone_pid': ['stale'] * 3, "
    "'u': ['10.18739/A2GT5FG86', 'https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86', "
    "'10.18739/MISSING'], 'dataone_host': ['stale'] * 3, 'title': ['mine'] * 3}), id_col=2)",
    MOCK1,
)
expr_case(
    "reshare_info.review.table_collisions",
    online(
        'reshare_info(data.frame(reshare_id = "stale", u = c("854001", "10.5255/UKDA-SN-854001"),'
        ' files = "mine"), id_col = "u")'
    ),
    "lambda m: m.reshare.reshare_info(m.pd.DataFrame({'reshare_id': ['stale'] * 2, "
    "'u': ['854001', '10.5255/UKDA-SN-854001'], 'files': ['mine'] * 2}), id_col='u')",
    MOCK1,
)

# FSD: as.integer() of the DDI counts, a file without a name, no DOI
expr_case(
    ".fsd_info.review.files",
    'metacheck:::.fsd_info("https://urn.fi/urn:nbn:fi:fsd:T-FSD3335")',
    'lambda m: m.fsd._fsd_info("https://urn.fi/urn:nbn:fi:fsd:T-FSD3335")',
)


def main() -> None:
    write_mocks()
    header = (
        "# Review parity cases for the archives_d2 port (R/archive-{dataone,dspace7,4tu,\n"
        "# psycharchives,researchbox,reshare,mendeley,fsd}.R). Generated by\n"
        "# tests/archives_d2/make_review_cases.py; mocked HTTP lives in\n"
        "# tests/archives_d2/mocks_review and metacheck's own tests/testthat/apis.\n"
    )
    OUT.write_text(
        header
        + yaml.dump(
            {"area": "archives_d2_review", "cases": cases},
            Dumper=Dumper,
            sort_keys=False,
            allow_unicode=True,
            width=10_000,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
