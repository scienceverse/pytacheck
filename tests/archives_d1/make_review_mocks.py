"""Write the extra mock responses used by the ``archives_d1_review`` parity cases.

Same httptest2 layout as :mod:`tests.archives_d1.make_mocks` (and the same
``tests/archives_d1/mocks`` directory), but only new files, under ids no
other mock uses, so the two generators never overwrite each other. Each
record targets a branch the first round of cases did not reach: files with
no name and no id, empty names, missing sizes, duplicate names, a Dataverse
record without ``latestVersion`` or with an atomic ``data``, a non-JSON
Dataverse answer, and a Figshare record with a string licence.

Regenerate with ``python -m tests.archives_d1.make_review_mocks`` from the
repository root, then regenerate the ``archives_d1_review`` goldens.
"""

from __future__ import annotations

from tests.archives_d1.make_mocks import md5, put, q, r404

CSV = b"x,y\n1,2\n"
NOTES = b"notes"
OTHER = b"other text\n"


def main() -> None:
    # ---------------------------------------------------------------- Figshare
    fs = "api.figshare.com/v2"
    dl = "https://ndownloader.figshare.com/files"
    put(
        f"{fs}/articles/700001",
        {
            "id": 700001,
            "title": "Awkward files",
            "doi": "10.6084/m9.figshare.700001",
            "authors": [{"full_name": "A. Person"}],
            "license": {"name": "CC0"},
            "files": [
                # no name and no id: R copies it to <folder>/NA but records path = NA
                {"id": None, "name": None, "size": len(CSV), "download_url": f"{dl}/700101"},
                # an empty name falls back to the id
                {"id": 700102, "name": "", "size": len(NOTES), "download_url": f"{dl}/700102"},
                # no size: exempt from both caps, no size check
                {"id": 700103, "name": "nosize.txt", "size": None, "download_url": f"{dl}/700103"},
                # two files with one name: the second overwrites the first
                {
                    "id": 700104,
                    "name": "dup.txt",
                    "size": len(NOTES),
                    "computed_md5": md5(NOTES),
                    "download_url": f"{dl}/700104",
                },
                {
                    "id": 700105,
                    "name": "dup.txt",
                    "size": len(OTHER),
                    "computed_md5": md5(OTHER),
                    "download_url": f"{dl}/700105",
                },
                # upper-case MD5 still verifies
                {
                    "id": 700106,
                    "name": "UPPER.CSV",
                    "size": len(CSV),
                    "computed_md5": md5(CSV).upper(),
                    "download_url": f"{dl}/700106",
                },
                # no download URL at all
                {"id": 700107, "name": "nolink.bin", "size": 3},
            ],
        },
    )
    put("ndownloader.figshare.com/files/700101", CSV, ".txt")
    put("ndownloader.figshare.com/files/700102", NOTES, ".txt")
    put("ndownloader.figshare.com/files/700103", OTHER, ".txt")
    put("ndownloader.figshare.com/files/700104", NOTES, ".txt")
    put("ndownloader.figshare.com/files/700105", OTHER, ".txt")
    put("ndownloader.figshare.com/files/700106", CSV, ".txt")

    # the total-size cap with missing and tied sizes (which.max: first maximum, NA skipped)
    mb = 1024 * 1024
    put(
        f"{fs}/articles/700002",
        {
            "id": 700002,
            "title": "Sizes",
            "files": [
                {"id": 700201, "name": "a.bin", "size": 40 * mb, "download_url": f"{dl}/700201"},
                {"id": 700202, "name": "b.bin", "size": None, "download_url": f"{dl}/700202"},
                {"id": 700203, "name": "c.bin", "size": 60 * mb, "download_url": f"{dl}/700203"},
                {"id": 700204, "name": "d.bin", "size": 60 * mb, "download_url": f"{dl}/700204"},
                {"id": 700205, "name": "e.csv", "size": len(CSV), "download_url": f"{dl}/700205"},
            ],
        },
    )
    put("ndownloader.figshare.com/files/700202", NOTES, ".txt")
    put("ndownloader.figshare.com/files/700205", CSV, ".txt")
    r404("ndownloader.figshare.com/files/700201", f"{dl}/700201")
    r404("ndownloader.figshare.com/files/700203", f"{dl}/700203")
    r404("ndownloader.figshare.com/files/700204", f"{dl}/700204")

    # a string licence ($ on an atomic vector is an R error) and authors that are not a list
    put(
        f"{fs}/articles/700003",
        {"id": 700003, "title": "Old", "license": "CC0", "authors": "nobody", "files": []},
    )
    # authors present but not a list, files null, fields given as empty arrays
    put(
        f"{fs}/articles/700004",
        {
            "id": 700004,
            "title": [],
            "doi": [],
            "published_date": None,
            "authors": "nobody",
            "license": None,
            "files": None,
        },
    )
    # a project page listing ids as strings and numbers, plus an article that 404s
    put(
        q(f"{fs}/projects/700500/articles", "page=1&page_size=100"),
        [{"id": "700004"}, {"id": 1234}, {"title": "no id"}],
    )

    # ---------------------------------------------------------------- Dryad
    dryad = "datadryad.org/api/v2"
    put(
        f"{dryad}/datasets/doi%3A10.5061%2Fdryad.rev1",
        {
            "_links": {"stash:version": {"href": "/api/v2/versions/7001"}},
            "identifier": "doi:10.5061/dryad.rev1",
            "title": "Review dataset",
            "authors": {"one": {"firstName": "Ann", "lastName": "Lee"}},
            "publicationDate": "2021-01-01",
            "license": "https://spdx.org/licenses/CC0-1.0.html",
        },
    )
    put(
        f"{dryad}/versions/7001/files",
        {
            "_embedded": {
                "stash:files": [
                    # no self link (so no id) and no path: stored as <folder>/NA, path NA
                    {
                        "_links": {"stash:download": {"href": "/api/v2/files/70011/download"}},
                        "size": len(CSV),
                        "digest": md5(CSV),
                        "digestType": "md5",
                    },
                    # an empty path falls back to the id
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/70012"},
                            "stash:download": {"href": "/api/v2/files/70012/download"},
                        },
                        "path": "",
                        "size": len(NOTES),
                        "digest": md5(NOTES),
                        "digestType": "MD5",
                    },
                    # a size given as a string
                    {
                        "_links": {
                            "self": {"href": "/api/v2/files/70013"},
                            "stash:download": {"href": "/api/v2/files/70013/download"},
                        },
                        "path": "nested/deeper/file.txt",
                        "size": "11",
                        "digest": md5(OTHER),
                        "digestType": "md5",
                    },
                ]
            }
        },
    )
    put(f"{dryad}/files/70011/download", CSV, ".txt")
    put(f"{dryad}/files/70012/download", NOTES, ".txt")
    put(f"{dryad}/files/70013/download", OTHER, ".txt")

    # ---------------------------------------------------------------- Dataverse
    def dv_api(host: str, doi: str) -> str:
        return q(f"{host}/api/datasets/-persistentId", f"persistentId=doi:{doi}")

    harvard = "dataverse.harvard.edu"
    # no latestVersion at all: every field NA, no files
    put(
        dv_api(harvard, "10.7910/DVN/NOVER"),
        {
            "status": "OK",
            "data": {
                "persistentUrl": "https://doi.org/10.7910/DVN/NOVER",
                "publicationDate": "2019",
            },
        },
    )
    # data is a string: `$` on an atomic vector is an R error
    put(dv_api(harvard, "10.7910/DVN/STRDATA"), {"status": "OK", "data": "oops"})
    # not JSON: parse_error
    put(dv_api(harvard, "10.7910/DVN/HTML"), "<html>down for maintenance</html>\n", ".html")
    # partial-name fields, an empty label, a lower-case md5 and a string file id
    put(
        dv_api(harvard, "10.7910/DVN/REV2"),
        {
            "status": "OK",
            "data": {
                "persistentUrl": "https://doi.org/10.7910/DVN/REV2",
                "latestVersion": {
                    "lastUpdateTime": "2022-02-02",
                    "metadataBlocks": {
                        "citation": {
                            "fields": [
                                {"typeName": "author", "value": []},
                                {"typeName": "title", "value": "Second"},
                                {"typeName": "title", "value": "Ignored duplicate"},
                            ]
                        }
                    },
                    "files": [
                        {
                            "label": "",
                            "dataFile": {
                                "id": "2001",
                                "filename": "real_name.csv",
                                "filesize": len(CSV),
                                "checksum": {"type": "md5", "value": md5(CSV)},
                            },
                        },
                        {
                            "dataFile": {
                                "id": 2002,
                                "filesize": len(NOTES),
                                "checksum": {"type": "MD5", "value": md5(NOTES).upper()},
                            },
                        },
                    ],
                },
            },
        },
    )
    put(f"{harvard}/api/access/datafile/2001", CSV, ".txt")
    put(f"{harvard}/api/access/datafile/2002", NOTES, ".txt")


if __name__ == "__main__":
    main()
