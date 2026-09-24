"""Write the httptest2-format mock responses used by the archives_gz tests and parity cases.

metacheck's own recordings (``upstream/.../tests/testthat/apis``) cover GitHub's
contents API and a few Zenodo records. The GitHub Git Trees API, GitLab,
Zenodo downloads and Zenodo uploads are not recorded there, so hand-written
responses live in ``tests/archives_gz/mocks`` (and ``mocks_fail``). File names
follow ``httptest2::build_mock_url()`` (query and body hashes included), so the
same directory serves R (``httptest2::with_mock_dir()``) and Python
(``tests.httpmock.replay()``).

Regenerate with ``python tests/archives_gz/make_mocks.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from tests.httpmock import r_digest

MOCKS = HERE / "mocks"
MOCKS_FAIL = HERE / "mocks_fail"
UPLOAD = HERE / "fixtures" / "upload"


def h(s: str, native: bool = False) -> str:
    return r_digest(s, native=native)[:6]


def md5(b: bytes) -> str:
    return hashlib.md5(b, usedforsecurity=False).hexdigest()


def write(root: Path, rel: str, content: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.write_bytes(data)


def jdump(x: Any) -> str:
    return json.dumps(x, indent=1, ensure_ascii=False) + "\n"


def r_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def r_response(
    method: str, url: str, status: int, body: str = "", headers: dict[str, str] | None = None
) -> str:
    """A deparsed ``httr2_response``, as httptest2 records one."""
    hdrs = {"content-type": "application/json; charset=utf-8", **(headers or {})}
    hdr_txt = ", ".join(f"`{k}` = {r_string(v)}" for k, v in hdrs.items())
    return (
        f"structure(list(method = {r_string(method)}, url = {r_string(url)}, "
        f"status_code = {status}L, headers = structure(list({hdr_txt}), "
        f'class = "httr2_headers"), body = charToRaw({r_string(body)}), '
        f'cache = new.env(parent = emptyenv())), class = "httr2_response")\n'
    )


# ---------------------------------------------------------------------------
# GitHub (git trees API)
# ---------------------------------------------------------------------------


def github(root: Path) -> None:
    rec = h("recursive=1")
    for repo in ("treerepo", "bigrepo", "emptyrepo", "fallrepo", "notree", "limited"):
        write(root, f"github.com/gzorg/{repo}-HEAD.html", "<html></html>\n")

    write(
        root,
        "api.github.com/repos/gzorg/treerepo.json",
        jdump(
            {
                "id": 1,
                "full_name": "gzorg/treerepo",
                "default_branch": "dev",
                "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
            }
        ),
    )
    write(
        root,
        f"api.github.com/repos/gzorg/treerepo/git/trees/dev-{rec}.json",
        jdump(
            {
                "sha": "abc123",
                "tree": [
                    {"path": "README.md", "type": "blob", "size": 120},
                    {"path": "data", "type": "tree"},
                    {"path": "data/study 1.csv", "type": "blob", "size": 2048},
                    {"path": "code/analysis.R", "type": "blob", "size": 512},
                    {"path": "code/config.json", "type": "blob", "size": 64},
                    {"path": "LICENSE", "type": "blob", "size": 1070},
                    {"path": "archive.tar.gz", "type": "blob", "size": 99999},
                    {"path": "weird.file-name", "type": "blob"},
                    {"path": "sub", "type": "commit"},
                ],
                "truncated": False,
            }
        ),
    )

    write(
        root,
        "api.github.com/repos/gzorg/bigrepo.json",
        jdump({"default_branch": "main", "license": None}),
    )
    write(
        root,
        f"api.github.com/repos/gzorg/bigrepo/git/trees/main-{rec}.json",
        jdump({"tree": [{"path": "a.txt", "type": "blob", "size": 1}], "truncated": True}),
    )

    write(
        root,
        "api.github.com/repos/gzorg/emptyrepo.json",
        jdump({"default_branch": "main", "license": {"spdx_id": []}}),
    )
    write(
        root,
        f"api.github.com/repos/gzorg/emptyrepo/git/trees/main-{rec}.json",
        jdump({"tree": [{"path": "docs", "type": "tree"}], "truncated": False}),
    )

    # metadata request fails -> the recursive contents listing is used instead
    write(
        root,
        "api.github.com/repos/gzorg/fallrepo.R",
        r_response(
            "GET",
            "https://api.github.com/repos/gzorg/fallrepo",
            404,
            '{"message":"Not Found"}',
        ),
    )
    raw = "https://raw.githubusercontent.com/gzorg/fallrepo/main/"
    write(
        root,
        "api.github.com/repos/gzorg/fallrepo/contents.json",
        jdump(
            [
                {"name": "b.txt", "path": "b.txt", "type": "file", "size": 5,
                 "download_url": raw + "b.txt"},
                {"name": "A.csv", "path": "A.csv", "type": "file", "size": 10,
                 "download_url": raw + "A.csv"},
                {"name": "page.html", "path": "page.html", "type": "file", "size": 99,
                 "download_url": raw + "page.html"},
            ]
        ),
    )  # fmt: skip

    # tree request fails and the repository is empty
    write(
        root,
        "api.github.com/repos/gzorg/notree.json",
        jdump({"default_branch": "main", "license": {"spdx_id": "GPL-3.0"}}),
    )
    write(
        root,
        f"api.github.com/repos/gzorg/notree/git/trees/main-{rec}.R",
        r_response(
            "GET",
            "https://api.github.com/repos/gzorg/notree/git/trees/main?recursive=1",
            409,
            '{"message":"Git Repository is empty."}',
        ),
    )
    write(
        root,
        "api.github.com/repos/gzorg/notree/contents.R",
        r_response(
            "GET",
            "https://api.github.com/repos/gzorg/notree/contents/",
            404,
            '{"message":"This repository is empty."}',
        ),
    )

    # rate limited
    write(
        root,
        "api.github.com/repos/gzorg/limited/contents.R",
        r_response(
            "GET",
            "https://api.github.com/repos/gzorg/limited/contents/",
            403,
            '{"message":"API rate limit exceeded"}',
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1780590041"},
        ),
    )


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def graphql_body(clean_repo: str, paths: list[str]) -> str:
    quoted = ",".join('"' + p.replace('"', '\\"') + '"' for p in paths)
    query = (
        f'query {{ project(fullPath: "{clean_repo}") {{ repository {{ '
        f"blobs(paths: [{quoted}]) {{ nodes {{ path size }} }} }} }} }}"
    )
    return json.dumps({"query": query}, ensure_ascii=False, separators=(",", ":"))


def gitlab(root: Path) -> None:
    for proj in ("sub/glproj", "bigproj", "nometa", "notree", "emptyproj"):
        write(root, f"gitlab.com/gzorg/{proj}-HEAD.html", "<html></html>\n")
    write(
        root,
        "gitlab.com/gzorg/missing-HEAD.R",
        r_response(
            "HEAD", "https://gitlab.com/gzorg/missing", 404, "", {"content-type": "text/html"}
        ),
    )
    lic = h("license=true")
    api = "gitlab.com/api/v4/projects"

    def tree_q(page: int) -> str:
        return h(f"recursive=true&per_page=100&page={page}")

    # a project in a subgroup, two tree pages, sizes from GraphQL
    pid = "gzorg%2Fsub%2Fglproj"
    write(
        root,
        f"{api}/{pid}-{lic}.json",
        jdump({"id": 42, "default_branch": "develop",
               "license": {"key": "mit", "name": "MIT License"}}),
    )  # fmt: skip
    page1 = [
        {"id": "a1", "name": "README.md", "type": "blob", "path": "README.md", "mode": "100644"},
        {"id": "a2", "name": "data", "type": "tree", "path": "data", "mode": "040000"},
        {"id": "a3", "name": "d1.csv", "type": "blob", "path": "data/d1.csv", "mode": "100644"},
    ]
    page2 = [
        {"id": "a4", "name": "run.py", "type": "blob", "path": "src/run.py", "mode": "100644"},
        {"id": "a5", "name": "x.json", "type": "blob", "path": "x.json", "mode": "100644"},
    ]
    base_url = f"https://{api}/{pid}/repository/tree?recursive=true&per_page=100&page="
    write(
        root,
        f"{api}/{pid}/repository/tree-{tree_q(1)}.R",
        r_response("GET", base_url + "1", 200, json.dumps(page1), {"x-next-page": "2"}),
    )
    write(
        root,
        f"{api}/{pid}/repository/tree-{tree_q(2)}.R",
        r_response("GET", base_url + "2", 200, json.dumps(page2), {"x-next-page": ""}),
    )
    paths = ["README.md", "data/d1.csv", "src/run.py", "x.json"]
    write(
        root,
        f"gitlab.com/api/graphql-{h(graphql_body('gzorg/sub/glproj', paths), native=True)}-POST.json",
        jdump({"data": {"project": {"repository": {"blobs": {"nodes": [
            {"path": "README.md", "size": "1234"},
            {"path": "data/d1.csv", "size": 56},
            {"path": "src/run.py", "size": "789"},
        ]}}}}}),
    )  # fmt: skip

    # 95 files: two GraphQL batches (90 + 5)
    pid = "gzorg%2Fbigproj"
    write(root, f"{api}/{pid}-{lic}.json", jdump({"id": 43, "default_branch": "main"}))
    names = [f"f{i:03d}.txt" for i in range(1, 96)]
    write(
        root,
        f"{api}/{pid}/repository/tree-{tree_q(1)}.json",
        jdump([{"id": n, "name": n, "type": "blob", "path": n, "mode": "100644"} for n in names]),
    )
    for batch in (names[:90], names[90:]):
        body = graphql_body("gzorg/bigproj", batch)
        nodes = [{"path": n, "size": str(int(n[1:4]) * 10)} for n in batch]
        write(
            root,
            f"gitlab.com/api/graphql-{h(body, native=True)}-POST.json",
            jdump({"data": {"project": {"repository": {"blobs": {"nodes": nodes}}}}}),
        )

    # metadata refused -> gated
    write(
        root,
        f"{api}/gzorg%2Fnometa-{lic}.R",
        r_response("GET", f"https://{api}/gzorg%2Fnometa?license=true", 404,
                   '{"message":"404 Project Not Found"}'),
    )  # fmt: skip
    # tree refused on the first page -> no file list
    write(root, f"{api}/gzorg%2Fnotree-{lic}.json", jdump({"default_branch": "trunk"}))
    write(
        root,
        f"{api}/gzorg%2Fnotree/repository/tree-{tree_q(1)}.R",
        r_response("GET", f"https://{api}/gzorg%2Fnotree/repository/tree?recursive=true"
                   "&per_page=100&page=1", 500, '{"message":"500 Internal Server Error"}'),
    )  # fmt: skip
    # an empty project
    write(
        root,
        f"{api}/gzorg%2Femptyproj-{lic}.json",
        jdump({"default_branch": "main", "license": {"key": []}}),
    )
    write(root, f"{api}/gzorg%2Femptyproj/repository/tree-{tree_q(1)}.json", "[]\n")


# ---------------------------------------------------------------------------
# Zenodo downloads
# ---------------------------------------------------------------------------


def zenodo_file(
    root: Path, rec: str, key: str, content: bytes | None, size: float | None = None,
    checksum: str | None = "md5", link: bool = True, fid: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    url = f"https://zenodo.org/api/records/{rec}/files/{key}/content"
    if content is not None:
        write(root, f"zenodo.org/api/records/{rec}/files/{key}/content.txt", content)
    real = content or b""
    entry: dict[str, Any] = {
        "id": fid or f"{rec}-{key}",
        "key": key,
        "size": len(real) if size is None else size,
    }
    if checksum == "md5":
        entry["checksum"] = "md5:" + md5(real)
    elif checksum is not None:
        entry["checksum"] = checksum
    entry["links"] = {"self": url} if link else {}
    return entry


def zenodo_record(root: Path, rec: str, files: list[dict[str, Any]], title: str) -> None:
    write(
        root,
        f"zenodo.org/api/records/{rec}.json",
        jdump(
            {
                "id": int(rec),
                "doi": f"10.5281/zenodo.{rec}",
                "metadata": {
                    "title": title,
                    "publication_date": "2026-01-01",
                    "resource_type": {"type": "dataset"},
                    "license": {"id": "cc-by-4.0"},
                    "creators": [{"name": "Doe, Jane"}],
                    "keywords": ["one", "two"],
                },
                "updated": "2026-01-02T00:00:00+00:00",
                "owners": [{"id": "1"}],
                "stats": {"downloads": 1, "unique_downloads": 1, "views": 2},
                "files": files,
            }
        ),
    )


def zenodo(root: Path) -> None:
    mb = 1024 * 1024
    zenodo_record(
        root,
        "5550001",
        [
            zenodo_file(root, "5550001", "data.csv", b"id,x\n1,2\n"),
            zenodo_file(root, "5550001", "notes.txt", b"hello\n"),
        ],
        "All good",
    )
    zenodo_record(
        root,
        "5550002",
        [
            zenodo_file(root, "5550002", "good.csv", b"a,b\n3,4\n"),
            dict(
                zenodo_file(root, "5550002", "bad.csv", b"a,b\n9,9\n", checksum=None),
                checksum="md5:" + md5(b"a,b\n5,6\n"),
            ),
            zenodo_file(root, "5550002", "gone.csv", None, size=12),
            zenodo_file(root, "5550002", "huge.bin", None, size=20 * mb),
            zenodo_file(root, "5550002", "nolink.txt", None, size=3, link=False),
            zenodo_file(root, "5550002", "sha.txt", b"shasum\n", checksum="sha256:abc"),
            zenodo_file(root, "5550002", "short.txt", b"abc", size=10, checksum=None),
        ],
        "Mixed results",
    )
    zenodo_record(root, "5550003", [], "No files")
    zenodo_record(
        root,
        "5550004",
        [
            zenodo_file(root, "5550004", "a.bin", None, size=5 * mb),
            zenodo_file(root, "5550004", "b.bin", None, size=6 * mb),
            zenodo_file(root, "5550004", "c.txt", b"c" * 100),
        ],
        "Too big in total",
    )
    zenodo_record(
        root,
        "5550005",
        [
            zenodo_file(root, "5550005", "bundle.zip", b"PK-not-a-real-zip", size=130 * mb,
                        checksum="md5:zzz"),
            zenodo_file(root, "5550005", "readme.txt", b"read me\n"),
        ],
        "A zip",
    )  # fmt: skip


# ---------------------------------------------------------------------------
# Zenodo uploads (sandbox) and the OSF metadata they read
# ---------------------------------------------------------------------------


def multipart_hash(name: str, content: bytes) -> str:
    return h(f"Multipart form:\n  name = {name}\n  file = File: {md5(content)}\n")


def json_body(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def upload_folders() -> dict[str, dict[str, bytes]]:
    folders = {
        "proj_osf": {
            "data.csv": b"id,score\n1,10\n2,20\n",
            "code/analysis.R": b"x <- read.csv('data.csv')\n",
            "README.md": b"# Study\n",
            "_osf_metadata/metadata.json": (
                jdump(
                    {
                        "osf_id": "gzosf",
                        "osf_url": "https://osf.io/gzosf",
                        "title": 'A "quoted" study / test',
                        "description": "Café data\nsecond line",
                        "tags": ["open", "data"],
                        "license": "CC0 1.0 Universal",
                        "contributors": [
                            {
                                "name": "Jane Doe",
                                "given_name": "Jane",
                                "family_name": "Doe",
                                "orcid": "0000-0001-2345-6789",
                            },
                            {"name": "Solo", "given_name": "", "family_name": None, "orcid": None},
                            {"name": "", "given_name": "", "family_name": ""},
                        ],
                    }
                ).encode()
            ),
        },
        "proj_plain": {
            "results.csv": b"a\n1\n",
            "notes.txt": b"notes\n",
            "sub/results.csv": b"a\n2\n",
            ".hidden": b"secret\n",
        },
        "proj_fromosf": {"table.csv": b"t\n1\n"},
    }
    return folders


def upload(root: Path, fail_root: Path) -> None:
    folders = upload_folders()
    if UPLOAD.exists():
        shutil.rmtree(UPLOAD)
    for folder, files in folders.items():
        for rel, content in files.items():
            write(UPLOAD, f"{folder}/{rel}", content)

    api = "sandbox.zenodo.org/api/deposit/depositions"
    write(root, f"{api}-{h('size=1')}.json", "[]\n")
    dep = {
        "id": 123,
        "links": {
            "html": "https://sandbox.zenodo.org/deposit/123",
            "self": "https://sandbox.zenodo.org/api/deposit/depositions/123",
        },
        "metadata": {"prereserve_doi": {"doi": "10.5072/zenodo.123", "recid": 123}},
    }
    write(root, f"{api}-{h('{}', native=True)}-POST.json", jdump(dep))
    write(
        root,
        f"{api}/123/actions/publish-POST.json",
        jdump({"id": 123, "doi": "10.5072/zenodo.123.pub", "submitted": True}),
    )

    # each file, uploaded individually through the form endpoint (no bucket)
    flat = {
        "proj_osf": {
            "README.md": "README.md",
            "_osf_metadata/metadata.json": "metadata.json",
            "code/analysis.R": "analysis.R",
            "data.csv": "data.csv",
        },
        "proj_plain": {
            "notes.txt": "notes.txt",
            "results.csv": "results.csv",
            "sub/results.csv": "sub__results.csv",
        },
        "proj_fromosf": {"table.csv": "table.csv"},
    }
    for folder, names in flat.items():
        for rel, name in names.items():
            content = folders[folder][rel]
            write(
                root,
                f"{api}/123/files-{multipart_hash(name, content)}-POST.json",
                jdump({"id": f"f-{name}", "filename": name, "filesize": len(content)}),
            )

    # the metadata each deposition gets (json bodies must match httr2's exactly)
    osf_md = {
        "title": 'A "quoted" study / test',
        "upload_type": "dataset",
        "description": "Café data\nsecond line",
        "creators": [
            {"name": "Doe, Jane", "orcid": "0000-0001-2345-6789"},
            {"name": "Solo"},
        ],
        "license": "cc0-1.0",
        "keywords": ["open", "data"],
        "related_identifiers": [
            {"identifier": "https://osf.io/gzosf/", "relation": "isIdenticalTo", "scheme": "url"}
        ],
    }
    plain_md = {
        "title": "proj_plain",
        "upload_type": "dataset",
        "description": "Files archived from proj_plain",
        "creators": [{"name": "Unknown"}],
        "license": "cc-by-4.0",
    }
    override_md = {**plain_md, "title": "Override", "upload_type": "software", "version": "1.0"}
    fromosf_md = {
        "title": "An OSF project",
        "upload_type": "dataset",
        "description": "Described on the OSF",
        "creators": [
            {"name": "Doe, Jane", "orcid": "0000-0001-2345-6789"},
            {"name": "NA, Prince"},
            {"name": "Anon Ymous"},
        ],
        "license": "mit",
        "keywords": ["x"],
        "related_identifiers": [
            {"identifier": "https://osf.io/gzosf/", "relation": "isIdenticalTo", "scheme": "url"}
        ],
    }
    ok = jdump({"id": 123, "state": "unsubmitted"})
    for md in (osf_md, plain_md, override_md, fromosf_md):
        write(root, f"{api}/123-{h(json_body({'metadata': md}), native=True)}-PUT.json", ok)

    # the OSF node the "fromosf" folder came from (read with page[size]=100)
    node = {
        "data": {
            "id": "gzosf",
            "attributes": {
                "title": "An OSF project",
                "description": "Described on the OSF",
                "tags": ["x"],
                "date_created": "2020-01-01T00:00:00",
            },
            "embeds": {
                "license": {"data": {"attributes": {"name": "MIT License"}}},
                "bibliographic_contributors": {
                    "data": [
                        {"embeds": {"users": {"data": {"attributes": {
                            "full_name": "Jane Doe", "given_name": "Jane",
                            "family_name": "Doe",
                            "social": {"orcid": "0000-0001-2345-6789"}}}}}},
                        {"embeds": {"users": {"data": {"attributes": {
                            "full_name": "Prince", "given_name": "Prince",
                            "family_name": None, "social": {}}}}}},
                        {"embeds": {"users": {"data": {"attributes": {
                            "full_name": "Anon Ymous", "given_name": "",
                            "family_name": "", "social": {"orcid": ""}}}}}},
                    ]
                },
            },
        }
    }  # fmt: skip
    q = h("embed=license&embed=bibliographic_contributors&page[size]=100")
    write(
        root,
        f"api.osf.io/v2/nodes/gzosf-{q}.R",
        r_response(
            "GET",
            "https://api.osf.io/v2/nodes/gzosf/?embed=license&embed=bibliographic_contributors"
            "&page[size]=100",
            200,
            json.dumps(node),
            {"content-type": "application/vnd.api+json; charset=utf-8"},
        ),
    )
    write(
        root,
        f"api.osf.io/v2/nodes/gzgone-{q}.R",
        r_response(
            "GET",
            "https://api.osf.io/v2/nodes/gzgone/?embed=license&embed=bibliographic_contributors"
            "&page[size]=100",
            410,
            '{"errors":[{"detail":"This resource has been deleted"}]}',
            {"content-type": "application/vnd.api+json; charset=utf-8"},
        ),
    )

    # the real Zenodo refuses the token
    write(
        root,
        f"zenodo.org/api/deposit/depositions-{h('size=1')}.R",
        r_response(
            "GET",
            "https://zenodo.org/api/deposit/depositions?size=1",
            403,
            '{"message":"Permission denied.","status":403}',
        ),
    )

    # a sandbox whose deposition endpoint rejects the request
    write(fail_root, f"{api}-{h('size=1')}.json", "[]\n")
    write(
        fail_root,
        f"{api}-{h('{}', native=True)}-POST.R",
        r_response(
            "POST",
            "https://sandbox.zenodo.org/api/deposit/depositions",
            400,
            json.dumps(
                {
                    "status": 400,
                    "message": "Validation error.",
                    "errors": [
                        {"field": "metadata.title", "message": "Missing data for required field."},
                        {"message": "Something else."},
                    ],
                }
            ),
        ),
    )


def main() -> None:
    for d in (MOCKS, MOCKS_FAIL):
        if d.exists():
            shutil.rmtree(d)
    github(MOCKS)
    gitlab(MOCKS)
    zenodo(MOCKS)
    upload(MOCKS, MOCKS_FAIL)


if __name__ == "__main__":
    main()
