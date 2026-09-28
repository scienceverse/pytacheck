"""Write the httptest2-format mock responses used by the archives_d2 tests and parity cases.

metacheck records API responses only for PsychArchives and ResearchBox
(``upstream/metacheck/tests/testthat/apis``); for DataONE, DSpace 7,
4TU.ResearchData, ReShare, Mendeley Data, FSD and the other legacy DSpace
hosts these are written by hand, in httptest2's layout (``build_mock_url()``
names), so that R (``httptest2::with_mock_dir()``) and Python
(``tests.httpmock.replay()``) replay the same files. The shapes follow the
real responses described in the R sources' comments. Regenerate with
``python -m tests.archives_d2.make_mocks`` from the repository root, then
regenerate the parity goldens.

A request that R's httptest2 cannot find raises an error (Python's replay
answers 404), so every URL a ``.batch_query()``-based lookup requests has a
file here, recorded 404s included.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from tests.httpmock import r_digest

ROOT = Path(__file__).resolve().parent / "mocks"

#: small file bodies served for downloads (name -> bytes)
BODIES = {
    "interviews": b"id,answer\n1,yes\n2,no\n",
    "notes": b"notes!\n",
    "readme4tu": b"4TU readme\n",
}


def md5(b: bytes) -> str:
    return hashlib.md5(b, usedforsecurity=False).hexdigest()


def put(path: str, content: object, ext: str = ".json") -> None:
    f = ROOT / f"{path}{ext}"
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, dict | list):
        f.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif isinstance(content, bytes):
        f.write_bytes(content)
    else:
        f.write_text(str(content), encoding="utf-8")


def q(base: str, query: str) -> str:
    """The mock path of ``base?query``."""
    return f"{base}-{r_digest(query)[:6]}"


def r404(path: str, url: str, body: str = '{"message": "Not Found"}') -> None:
    """A recorded 404 (an ``httr2_response`` deparsed as httptest2 stores it)."""
    esc = body.replace("\\", "\\\\").replace('"', '\\"')
    f = ROOT / f"{path}.R"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        f'structure(list(method = "GET", url = "{url}", status_code = 404L, '
        'headers = structure(list(`content-type` = "application/json"), class = "httr2_headers"), '
        f'body = charToRaw("{esc}"), cache = new.env(parent = emptyenv())), '
        'class = "httr2_response")\n',
        encoding="utf-8",
    )


EML_FULL = """<?xml version="1.0" encoding="UTF-8"?>
<eml:eml xmlns:eml="https://eml.ecoinformatics.org/eml-2.2.0" packageId="doi:10.18739/A2GT5FG86" system="https://arcticdata.io">
  <dataset>
    <title>Soil temperature, Utqiagvik, Alaska, 2015-2020</title>
    <creator>
      <individualName>
        <givenName>Jane</givenName>
        <givenName>Q</givenName>
        <surName>Doe</surName>
      </individualName>
      <organizationName>Arctic Lab</organizationName>
    </creator>
    <creator>
      <individualName><surName>Smith</surName></individualName>
    </creator>
    <creator>
      <organizationName>National Snow and Ice Data Center</organizationName>
    </creator>
    <creator>
      <individualName><givenName> </givenName><surName></surName></individualName>
      <organizationName>Blank Org</organizationName>
    </creator>
    <creator>
      <individualName><givenName></givenName><surName></surName></individualName>
    </creator>
    <pubDate>2021-03-04</pubDate>
    <intellectualRights>
      <para>This work is dedicated to the public domain under the Creative Commons Universal 1.0 Public Domain Dedication.</para>
      <para>Cite the data.</para>
    </intellectualRights>
    <dataTable>
      <entityName>soil_temp.csv</entityName>
      <physical>
        <objectName>soil_temp.csv</objectName>
        <size unit="bytes">20480</size>
        <distribution>
          <online><url function="download">https://cn.dataone.org/cn/v2/resolve/urn%3Auuid%3Aabc-123</url></online>
        </distribution>
      </physical>
    </dataTable>
    <otherEntity>
      <entityName>readme</entityName>
      <physical>
        <objectName>readme.txt</objectName>
        <size>n/a</size>
      </physical>
    </otherEntity>
    <otherEntity>
      <entityName>nameless</entityName>
      <physical>
        <size> 1e3 </size>
        <distribution><online><url>https://cn.dataone.org/cn/v2/resolve/</url></online></distribution>
      </physical>
    </otherEntity>
  </dataset>
</eml:eml>
"""

EML_MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<eml xmlns="https://eml.ecoinformatics.org/eml-2.2.0" packageId="doi:10.6085/AA/marine_ltm.20.1">
  <dataset>
    <title>PISCO: Intertidal Community Structure Surveys</title>
  </dataset>
</eml>
"""

ISO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd">
  <gmd:title>Not EML</gmd:title>
</gmd:MD_Metadata>
"""

DDI_FULL = """<?xml version="1.0" encoding="UTF-8"?>
<codeBook xmlns="ddi:codebook:2_5" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.5">
  <docDscr>
    <citation>
      <titlStmt>
        <titl>DDI description: Finnish National Election Study 2011</titl>
      </titlStmt>
    </citation>
  </docDscr>
  <stdyDscr>
    <citation>
      <titlStmt>
        <titl xml:lang="en">Finnish National Election Study 2011</titl>
        <IDNo agency="FSD">FSD2653</IDNo>
        <IDNo agency="DOI">10.60686/t-fsd2653</IDNo>
      </titlStmt>
    </citation>
    <dataAccs>
      <useStmt>
        <restrctn>The dataset is (B) available for research, teaching and study.</restrctn>
      </useStmt>
    </dataAccs>
  </stdyDscr>
  <fileDscr ID="F1">
    <fileTxt>
      <fileName>daF2653_eng.sav</fileName>
      <dimensns>
        <caseQnty>1298</caseQnty>
        <varQnty>312</varQnty>
      </dimensns>
      <fileType>SPSS</fileType>
    </fileTxt>
  </fileDscr>
  <fileDscr ID="F2">
    <fileTxt>
      <fileName>daF2653_fin.por</fileName>
      <dimensns>
        <caseQnty>12.9</caseQnty>
        <varQnty>many</varQnty>
      </dimensns>
    </fileTxt>
  </fileDscr>
</codeBook>
"""

DDI_SPARSE = """<?xml version="1.0" encoding="UTF-8"?>
<codeBook xmlns="ddi:codebook:2_5" version="2.5">
  <docDscr>
    <citation><titlStmt><titl>DDI description: something</titl></titlStmt></citation>
  </docDscr>
  <stdyDscr>
    <citation><titlStmt><IDNo agency="FSD">FSD1111</IDNo></titlStmt></citation>
  </stdyDscr>
</codeBook>
"""


def dataone() -> None:
    arctic = "arcticdata.io/metacat/d1/mn/v2/object"
    put(f"{arctic}/doi%3A10.18739%2FA2GT5FG86", EML_FULL, ".xml")
    r404(
        f"{arctic}/doi%3A10.18739%2FMISSING",
        "https://arcticdata.io/metacat/d1/mn/v2/object/doi%3A10.18739%2FMISSING",
    )
    put(
        "data.piscoweb.org/metacat/d1/mn/v2/object/doi%3A10.6085%2FAA%2Fmarine_ltm.20.1",
        EML_MINIMAL,
        ".xml",
    )
    knb = "knb.ecoinformatics.org/knb/d1/mn/v2/object"
    put(f"{knb}/doi%3A10.5063%2FNOTEML", ISO_XML, ".xml")
    put(f"{knb}/doi%3A10.5063%2FBROKEN", "this is not xml at all\n", ".txt")


GATECH_ITEM = "bac086e5-c606-474b-af1e-4a6122694af5"
GATECH_ORIGINAL = "22222222-2222-2222-2222-222222222222"
UMASS_ITEM = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def dspace7() -> None:
    api = "repository.gatech.edu/server/api"
    put(
        f"{api}/core/items/{GATECH_ITEM}",
        {
            "id": GATECH_ITEM,
            "uuid": GATECH_ITEM,
            "name": "Dataset for a robot study",
            "handle": "1853/67239",
            "metadata": {
                "dc.contributor.author": [
                    {"value": "Doe, Jane", "language": None},
                    {"value": "Roe, Rich"},
                    {"value": None},
                ],
                "dc.identifier.doi": [],
                "dc.rights": [{"value": None}],
                "dc.rights.uri": [{"value": "http://creativecommons.org/licenses/by/4.0/"}],
                "dc.date.issued": [{"value": "2022-06-01"}],
                "dc.title": [{"value": "Title from metadata"}],
            },
            "lastModified": "2023-01-02T03:04:05.000+00:00",
            "type": "item",
        },
    )
    put(
        f"{api}/core/items/{GATECH_ITEM}/bundles",
        {
            "_embedded": {
                "bundles": [
                    {"name": "LICENSE", "uuid": "11111111-1111-1111-1111-111111111111"},
                    {"name": "ORIGINAL", "uuid": GATECH_ORIGINAL},
                    {"name": "THUMBNAIL", "uuid": "33333333-3333-3333-3333-333333333333"},
                ]
            }
        },
    )
    content = "https://repository.gatech.edu/server/api/core/bitstreams/{}/content"
    put(
        f"{api}/core/bundles/{GATECH_ORIGINAL}/bitstreams",
        {
            "_embedded": {
                "bitstreams": [
                    {
                        "name": "data.CSV",
                        "sizeBytes": 1234,
                        "checkSum": {"value": "0123456789abcdef0123456789abcdef"},
                        "_links": {"content": {"href": content.format("b1")}},
                    },
                    {
                        "name": "analysis.R",
                        "sizeBytes": 99,
                        "checkSum": {"value": None, "checkSumAlgorithm": "MD5"},
                        "_links": {"content": {"href": content.format("b2")}},
                    },
                    {
                        "name": "model.rds",
                        "sizeBytes": 5000000000,
                        "_links": {"content": {"href": content.format("b3")}},
                    },
                    {"name": "noext", "sizeBytes": None, "_links": {}},
                ]
            }
        },
    )
    put(q(f"{api}/pid/find", "id=1853%2F67239"), {"uuid": GATECH_ITEM, "type": "item"})
    put(q(f"{api}/pid/find", "id=1853%2F404"), {"type": "item"})

    umass = "scholarworks.umass.edu/server/api"
    put(
        f"{umass}/core/items/{UMASS_ITEM}",
        {
            "uuid": UMASS_ITEM,
            "name": [],
            "metadata": {"dc.title": [{"value": "Fallback title"}, {"value": "Second title"}]},
        },
    )
    put(
        f"{umass}/core/items/{UMASS_ITEM}/bundles",
        {"_embedded": {"bundles": [{"name": "THUMBNAIL", "uuid": "t"}]}},
    )


def fourtu() -> None:
    api = "data.4tu.nl/v2/articles"
    put(
        f"{api}/16766929",
        {
            "id": 16766929,
            "uuid": "7f866e02-eb39-4a2a-8f7d-2d053ee6cde9",
            "title": "Wind tunnel measurements",
            "doi": "10.4121/16766929.v1",
            "published_date": "2021-10-12T10:00:00",
            "modified_date": "2022-01-01T00:00:00",
            "authors": [{"full_name": "Ada Author"}, {"full_name": "Bob Builder"}],
            "license": {"name": "CC BY 4.0"},
            "files": [
                {
                    "id": 501,
                    "name": "readme.txt",
                    "size": len(BODIES["readme4tu"]),
                    "download_url": "https://data.4tu.nl/file/7f866e02/f501",
                    "computed_md5": md5(BODIES["readme4tu"]),
                }
            ],
        },
    )
    put("data.4tu.nl/file/7f866e02/f501", BODIES["readme4tu"], ".txt")
    put(
        f"{api}/ce413614-1c82-4e81-90c0-323aa7d2fabd",
        {
            "id": 12345,
            "title": "A uuid-only dataset",
            "doi": "10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd",
            "authors": [],
            "files": [],
        },
    )
    r404(f"{api}/99999999", "https://data.4tu.nl/v2/articles/99999999")


def reshare() -> None:
    base = "reshare.ukdataservice.ac.uk"
    files = f"http://{base}/id/file"
    put(
        f"{base}/id/eprint/854001",
        {
            "eprintid": 854001,
            "title": "Interviews with rural households, 2016-2018",
            "doi": "10.5255/UKDA-SN-854001",
            "datestamp": "2020-01-01 10:00:00",
            "lastmod": "2021-02-03 04:05:06",
            "creators": [
                {"name": {"given": "Jane", "family": "Doe"}, "id": "jd@example.org"},
                {"name": {"family": "Solo"}},
                {"name": {}},
                {"id": "nobody"},
            ],
            "documents": [
                {
                    "content": "data",
                    "files": [
                        {
                            "fileid": 1001,
                            "filename": "interviews.csv",
                            "filesize": len(BODIES["interviews"]),
                            "hash": md5(BODIES["interviews"]),
                            "hash_type": "MD5",
                            "uri": f"{files}/1001",
                        }
                    ],
                },
                {
                    "content": "documentation",
                    "files": [
                        {
                            "fileid": 1002,
                            "filename": "guide.pdf",
                            "filesize": 20000000,
                            "hash": md5(b"never downloaded"),
                            "hash_type": "MD5",
                            "uri": f"{files}/1002",
                        }
                    ],
                },
                {
                    "files": [
                        {
                            "fileid": 1003,
                            "filename": "notes.txt",
                            "filesize": len(BODIES["notes"]),
                            "hash": md5(b"something else"),
                            "hash_type": "MD5",
                            "uri": f"{files}/1003",
                        },
                        {
                            "fileid": 1004,
                            "filename": "gone.txt",
                            "filesize": 3,
                            "hash_type": "SHA1",
                            "uri": f"{files}/1004",
                        },
                    ]
                },
            ],
        },
    )
    put(f"{base}/id/file/1001", BODIES["interviews"], ".txt")
    put(f"{base}/id/file/1003", BODIES["notes"], ".txt")
    r404(f"{base}/id/file/1004", f"https://{base}/id/file/1004")
    put(
        f"{base}/id/eprint/854243",
        "<html><body>Not JSON</body></html>\n",
        ".html",
    )
    put(f"{base}/id/eprint/854100", {"title": "An empty deposit", "documents": []})
    r404(f"{base}/id/eprint/999999", f"https://{base}/id/eprint/999999")


def mendeley() -> None:
    api = "data.mendeley.com/public-api/datasets"
    put(
        f"{api}/vjtxybrc28",
        {
            "id": "vjtxybrc28",
            "name": "Survey of research data practices",
            "doi": {"id": "10.17632/vjtxybrc28.1"},
            "description": "Responses to a survey.",
            "publish_date": "2020-01-01T00:00:00.000Z",
            "modified_on": "2020-02-02T00:00:00.000Z",
            "contributors": [
                {"first_name": "Ann", "last_name": "Lee"},
                {"last_name": "Solo"},
                {},
            ],
            "data_licence": {"full_name": "Creative Commons Attribution 4.0"},
            "files": [
                {
                    "filename": "survey.csv",
                    "size": 10,
                    "content_details": {"download_url": "https://data.mendeley.com/x/survey.csv"},
                }
            ],
        },
    )
    put(
        f"{api}/short1",
        {
            "name": "Short licence",
            "data_licence": {"short_name": "CC0", "full_name": "Public domain"},
            "contributors": [],
        },
    )
    put(f"{api}/strdoi", {"name": "DOI as a string", "doi": "10.17632/strdoi.1"})
    put(f"{api}/notjson", "<html>maintenance</html>\n", ".html")
    r404(f"{api}/zzz999", "https://data.mendeley.com/public-api/datasets/zzz999")


def fsd() -> None:
    base = "services.fsd.tuni.fi/catalogue"
    put(f"{base}/FSD2653/DDI/FSD2653_eng.xml", DDI_FULL, ".xml")
    put(f"{base}/FSD1111/DDI/FSD1111_eng.xml", DDI_SPARSE, ".xml")
    r404(
        f"{base}/FSD9999/DDI/FSD9999_eng.xml",
        "https://services.fsd.tuni.fi/catalogue/FSD9999/DDI/FSD9999_eng.xml",
    )


def dspace_legacy() -> None:
    base = "bonndoc.ulb.uni-bonn.de/rest"
    put(f"{base}/handle/20.500.11811/1234", {"uuid": "u-bonn-1", "name": "Bonn item"})
    put(
        q(f"{base}/items/u-bonn-1", "expand=metadata"),
        {
            "uuid": "u-bonn-1",
            "metadata": [
                {"key": "dc.contributor.author", "value": "Author, A."},
                {"key": "dc.contributor.author", "value": "Author, B."},
                {"key": "dc.rights", "value": "openAccess"},
                {"key": "dc.identifier.doi", "value": None},
                {"key": "dc.date.available", "value": "2020-05-05"},
            ],
        },
    )
    put(q(f"{base}/items/u-bonn-1/bitstreams", "limit=1000"), [])
    # an item whose handle resolves to no uuid
    put("qsardb.org/rest/handle/10967/106", {"name": "no uuid"})


def researchbox() -> None:
    page = """<html><head><title>Box 99</title></head><body>
<div><p class='file_name'>data.csv</p><p class='file_name'>code.R</p></div>
<input type="hidden" id="box_id" name="box_id" value="99">
<p>SUPPLEMENTARY FILES FOR</p><p>A synthetic study</p>
<p>LICENSE FOR USE</p><p>CC BY 4.0</p>
<p>BOX PUBLIC SINCE</p><p>January 01, 2024</p>
<p>BOX CREATORS</p><p>Ann Author</p>
<p>ABSTRACT</p><p>Nothing to see.</p>
</body></html>
"""
    put("researchbox.org/99", page, ".html")
    r404("researchbox.org/404", "https://researchbox.org/404", "<html>Not found</html>")


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    dataone()
    dspace7()
    fourtu()
    reshare()
    mendeley()
    fsd()
    dspace_legacy()
    researchbox()


if __name__ == "__main__":
    main()
