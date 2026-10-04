"""Write the httptest2-format mock responses used by the archives_d1 tests and parity cases.

metacheck records no API responses for Dryad, Figshare or Dataverse, so these
are written by hand, in httptest2's layout (``build_mock_url()`` names), so
that R (``httptest2::with_mock_dir()``) and Python (``tests.httpmock.replay()``)
replay the same files. Regenerate with ``python -m tests.archives_d1.make_mocks``
from the repository root, then regenerate the parity goldens.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.httpmock import r_digest

ROOT = Path(__file__).resolve().parent / "mocks"


def put(path: str, content: object, ext: str = ".json") -> None:
    f = ROOT / f"{path}{ext}"
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        f.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    elif isinstance(content, bytes):
        f.write_bytes(content)
    else:
        f.write_text(content, encoding="utf-8")


def q(base: str, query: str) -> str:
    return f"{base}-{r_digest(query)[:6]}"


def md5(b: bytes) -> str:
    return hashlib.md5(b, usedforsecurity=False).hexdigest()


def r404(path: str, url: str, message: str = "Not Found") -> None:
    """A recorded 404 (an ``httr2_response`` deparsed as httptest2 stores it)."""
    body = json.dumps({"message": message}).replace('"', '\\"')
    f = ROOT / f"{path}.R"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        f'structure(list(method = "GET", url = "{url}", status_code = 404L, '
        'headers = structure(list(`content-type` = "application/json"), class = "httr2_headers"), '
        f'body = charToRaw("{body}"), cache = new.env(parent = emptyenv())), '
        'class = "httr2_response")\n',
        encoding="utf-8",
    )


def main() -> None:

    CSV = b"x,y\n1,2\n"
    README = b"# Readme\nhello world\n"
    NOTZIP = b"PK-not-a-real-zip"

    # ---------------------------------------------------------------- Dryad
    DRYAD = "datadryad.org/api/v2"
    put(
        f"{DRYAD}/datasets/doi%3A10.5061%2Fdryad.j1fd7",
        {
            "_links": {
                "self": {"href": "/api/v2/datasets/doi%3A10.5061%2Fdryad.j1fd7"},
                "stash:version": {"href": "/api/v2/versions/12345"},
                "stash:download": {"href": "/api/v2/datasets/doi%3A10.5061%2Fdryad.j1fd7/download"},
            },
            "identifier": "doi:10.5061/dryad.j1fd7",
            "id": 4321,
            "title": "Data from: An example study",
            "authors": [
                {"firstName": "Jane", "lastName": "Doe", "affiliation": "Uni"},
                {"firstName": "", "lastName": "Smith"},
                {"firstName": None, "lastName": None},
                {"lastName": "Solo"},
            ],
            "publicationDate": "2014-01-02",
            "lastModificationDate": "2020-05-06",
            "license": "https://spdx.org/licenses/CC0-1.0.html",
            "versionNumber": 2,
        },
    )
    put(
        f"{DRYAD}/versions/12345/files",
        {
            "_links": {"self": {"href": "/api/v2/versions/12345/files"}},
            "count": 5,
            "total": 5,
            "_embedded": {
                "stash:files": [
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/111"},
                            "stash:download": {"href": "/api/v2/files/111/download"},
                        },
                        "path": "data.csv",
                        "size": len(CSV),
                        "mimeType": "text/csv",
                        "digest": md5(CSV),
                        "digestType": "md5",
                    },
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/112"},
                            "stash:download": {"href": "/api/v2/files/112/download"},
                        },
                        "path": "README.md",
                        "size": len(README),
                        "mimeType": "text/markdown",
                        "digest": "0123456789abcdef0123456789abcdef",
                        "digestType": "MD5",
                    },
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/113"},
                            "stash:download": {"href": "/api/v2/files/113/download"},
                        },
                        "path": "big.bin",
                        "size": 20 * 1024 * 1024,
                        "digest": None,
                        "digestType": "md5",
                    },
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/114"},
                            "stash:download": {"href": "/api/v2/files/114/download"},
                        },
                        "path": "sub/notes.txt",
                        "size": 5,
                        "digest": "abc",
                        "digestType": "sha-256",
                    },
                    {
                        "_links": {"self": {"href": "/api/v2/files/115"}},
                        "path": "nolink.txt",
                        "size": 3,
                    },
                ]
            },
        },
    )
    put(f"{DRYAD}/files/111/download", CSV, ".txt")
    put(f"{DRYAD}/files/112/download", README, ".txt")
    put(f"{DRYAD}/files/114/download", b"notes", ".txt")

    # dataset without a version link (so no files)
    put(
        f"{DRYAD}/datasets/doi%3A10.5061%2Fdryad.nofiles",
        {
            "identifier": "doi:10.5061/dryad.nofiles",
            "title": [],
            "authors": [],
            "publicationDate": None,
            "license": {},
        },
    )
    # a body that is not JSON
    put(
        f"{DRYAD}/datasets/doi%3A10.5061%2Fdryad.notjson",
        "<html><body>maintenance</body></html>\n",
        ".html",
    )
    # alternate-prefix dataset with a zip in it
    put(
        f"{DRYAD}/datasets/doi%3A10.25338%2Fb8n33j",
        {
            "_links": {"stash:version": {"href": "/api/v2/versions/777"}},
            "identifier": "doi:10.25338/B8N33J",
            "title": "Zipped",
            "authors": [{"firstName": "Ann", "lastName": "Lee"}],
        },
    )
    put(
        f"{DRYAD}/versions/777/files",
        {
            "_embedded": {
                "stash:files": [
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/901"},
                            "stash:download": {"href": "/api/v2/files/901/download"},
                        },
                        "path": "bundle.zip",
                        "size": 130 * 1024 * 1024,
                        "digest": "zzz",
                        "digestType": "md5",
                    },
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/902"},
                            "stash:download": {"href": "/api/v2/files/902/download"},
                        },
                        "path": "small.csv",
                        "size": len(CSV),
                        "digest": md5(CSV),
                        "digestType": "md5",
                    },
                ]
            }
        },
    )
    put(f"{DRYAD}/files/901/download", NOTZIP, ".txt")
    put(f"{DRYAD}/files/902/download", CSV, ".txt")

    # ---------------------------------------------------------------- Figshare
    FS = "api.figshare.com/v2"
    put(
        f"{FS}/articles/18093368",
        {
            "id": 18093368,
            "title": "Example Figshare dataset",
            "doi": "10.6084/m9.figshare.18093368.v1",
            "published_date": "2022-01-01T10:00:00Z",
            "modified_date": "2022-02-01T10:00:00Z",
            "authors": [{"id": 1, "full_name": "Jane Doe"}, {"id": 2, "full_name": None}],
            "license": {
                "value": 1,
                "name": "CC BY 4.0",
                "url": "https://creativecommons.org/licenses/by/4.0/",
            },
            "files": [
                {
                    "id": 555,
                    "name": "data.csv",
                    "size": len(CSV),
                    "computed_md5": md5(CSV),
                    "download_url": "https://ndownloader.figshare.com/files/555",
                    "is_link_only": False,
                },
                {
                    "id": 556,
                    "name": "readme.txt",
                    "size": len(README) + 1,
                    "computed_md5": md5(README),
                    "download_url": "https://ndownloader.figshare.com/files/556",
                },
                {
                    "id": 557,
                    "name": "video.mp4",
                    "size": 50 * 1024 * 1024,
                    "computed_md5": "",
                    "download_url": "https://ndownloader.figshare.com/files/557",
                },
                {
                    "id": 558,
                    "name": "missing.csv",
                    "size": 4,
                    "computed_md5": "",
                    "download_url": "https://ndownloader.figshare.com/files/558",
                },
            ],
        },
    )
    put("ndownloader.figshare.com/files/555", CSV, ".txt")
    put("ndownloader.figshare.com/files/556", README, ".txt")
    put(
        f"{FS}/articles/6934484",
        {
            "id": 6934484,
            "title": "PxW dataset",
            "doi": "10.6084/m9.figshare.6934484",
            "authors": [],
            "license": {"name": "CC0"},
            "files": [],
        },
    )
    put(f"{FS}/articles/19095317", "not json at all\n", ".txt")
    put(
        q(f"{FS}/projects/133332/articles", "page=1&page_size=100"),
        [
            {"id": 18093368, "title": "a"},
            {"id": 6934484, "title": "b"},
            {"id": None},
            {"id": 18093368},
        ],
    )
    put(q(f"{FS}/projects/999/articles", "page=1&page_size=100"), [])
    # a collection (a figshare.com/collections/ URL or a 10.6084/m9.figshare.c. DOI)
    put(
        q(f"{FS}/collections/8742785/articles", "page=1&page_size=100"),
        [{"id": 6934484, "title": "b"}, {"id": 18093368}, {"id": None}],
    )

    # ---------------------------------------------------------------- Dataverse
    def dv_api(host: str, doi: str) -> str:
        return q(f"{host}/api/datasets/-persistentId", f"persistentId=doi:{doi}")

    def dv_record(doi, files, license_value=None, **extra):
        version = {
            "releaseTime": "2020-01-01T00:00:00Z",
            "lastUpdateTime": "2020-01-02T00:00:00Z",
            "license": license_value
            if license_value is not None
            else {"name": "CC0 1.0", "uri": "x"},
            "metadataBlocks": {
                "citation": {
                    "fields": [
                        {"typeName": "title", "value": "Example Dataset"},
                        {
                            "typeName": "author",
                            "value": [
                                {"authorName": {"value": "Doe, Jane"}},
                                {"authorAffiliation": {"value": "Uni"}},
                            ],
                        },
                    ]
                }
            },
            "files": files,
        }
        version.update(extra)
        return {
            "status": "OK",
            "data": {
                "persistentUrl": f"https://doi.org/{doi}",
                "publicationDate": "2019-12-31",
                "latestVersion": version,
            },
        }

    put(
        dv_api("dataverse.harvard.edu", "10.7910/DVN/ABC123"),
        dv_record(
            "10.7910/DVN/ABC123",
            [
                {
                    "label": "data.csv",
                    "dataFile": {
                        "id": 999,
                        "filename": "data.csv",
                        "filesize": len(CSV),
                        "checksum": {"type": "MD5", "value": md5(CSV)},
                    },
                },
                {
                    "label": "codebook.pdf",
                    "dataFile": {
                        "id": 1000,
                        "filename": "codebook.pdf",
                        "filesize": 11,
                        "checksum": {"type": "SHA-1", "value": "abc"},
                    },
                },
                {"dataFile": {"id": 1001, "filename": "fallback_name.txt", "filesize": 5}},
                {"label": "huge.dat", "dataFile": {"id": 1002, "filesize": 11 * 1024 * 1024}},
            ],
        ),
    )
    put("dataverse.harvard.edu/api/access/datafile/999", CSV, ".txt")
    put("dataverse.harvard.edu/api/access/datafile/1000", b"hello world", ".txt")
    put("dataverse.harvard.edu/api/access/datafile/1001", b"12345", ".txt")
    put(
        dv_api("dataverse.nl", "10.34894/XYZ999"),
        {
            "status": "OK",
            "data": {
                "persistentUrl": "https://doi.org/10.34894/XYZ999",
                "publicationDate": "2021-03-03",
                "latestVersion": {"metadataBlocks": {}, "files": []},
            },
        },
    )
    put(dv_api("dataverse.cirad.fr", "10.18167/DVN1/T0DMFJ"), {"status": "OK"})
    put(
        dv_api("dataverse.no", "10.18710/OLDLIC"),
        dv_record("10.18710/OLDLIC", [], license_value="CC0"),
    )

    # recorded "not found" answers (httptest2 aborts on an unrecorded request)
    r404(
        "datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.missing",
        "https://datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.missing",
    )
    r404("api.figshare.com/v2/articles/1234", "https://api.figshare.com/v2/articles/1234")
    r404(
        q("api.figshare.com/v2/projects/404404/articles", "page=1&page_size=100"),
        "https://api.figshare.com/v2/projects/404404/articles?page=1&page_size=100",
    )
    r404(
        q("api.figshare.com/v2/collections/404405/articles", "page=1&page_size=100"),
        "https://api.figshare.com/v2/collections/404405/articles?page=1&page_size=100",
    )
    r404("ndownloader.figshare.com/files/558", "https://ndownloader.figshare.com/files/558")
    for doi in ("10.7910/DVN/NOPE", "10.18167/DVN1/T0DMFJ"):
        r404(
            dv_api("dataverse.harvard.edu", doi),
            "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:" + doi,
        )


if __name__ == "__main__":
    main()
