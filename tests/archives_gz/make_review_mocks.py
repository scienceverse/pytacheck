"""Write the httptest2-format mocks used by the ``archives_gz_review`` parity cases.

These cover branches the first set of mocks (``make_mocks.py``) does not: a
GitHub API error body that is not a language table, integer/double language
byte counts, a contents listing with tricky names, sub-directories whose path
needs URL encoding, trees with a missing or slashed default branch, a GitLab
project on a branch with a space whose GraphQL sizes come back out of order,
and Zenodo records with unusual licence/stats/metadata shapes.

Regenerate with ``python tests/archives_gz/make_review_mocks.py``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from tests.archives_gz.make_mocks import (
    graphql_body,
    h,
    jdump,
    md5,
    r_response,
    write,
    zenodo_file,
)

MOCKS = HERE / "mocks_review"


def github(root: Path) -> None:
    rec = h("recursive=1")
    for repo in ("langerr", "langmix", "langnone", "files", "tree3", "tree4"):
        write(root, f"github.com/rv/{repo}-HEAD.html", "<html></html>\n")

    # an error body is still parsed as if it listed languages
    write(
        root,
        "api.github.com/repos/rv/langerr/languages.R",
        r_response(
            "GET",
            "https://api.github.com/repos/rv/langerr/languages",
            404,
            '{"message":"Not Found","documentation_url":"https://docs.github.com","status":"404"}',
        ),
    )
    # a byte count past 2^31 is a double in R, the rest integers
    write(
        root,
        "api.github.com/repos/rv/langmix/languages.json",
        jdump({"R": 12345, "Big": 3000000000, "C++": 7}),
    )
    write(root, "api.github.com/repos/rv/langnone/languages.json", "{}\n")
    write(
        root,
        "github.com/rv/nope-HEAD.R",
        r_response("HEAD", "https://github.com/rv/nope", 404, "", {"content-type": "text/html"}),
    )
    # README as GitHub sends it: base64 wrapped at 60 characters
    import base64

    b64 = base64.b64encode("# Ünïcode README\n\nLine two\n".encode()).decode()
    write(
        root,
        "api.github.com/repos/rv/files/readme.json",
        jdump({"content": "\n".join(b64[i : i + 60] for i in range(0, len(b64), 60)) + "\n"}),
    )
    write(root, "api.github.com/repos/rv/files/languages.json", jdump({"Shell": 5}))

    raw = "https://raw.githubusercontent.com/rv/files/main/"

    def f(name: str, path: str | None = None, kind: str = "file", size: int = 1) -> dict[str, Any]:
        path = path or name
        return {
            "name": name,
            "path": path,
            "type": kind,
            "size": size if kind == "file" else 0,
            "download_url": raw + path if kind == "file" else None,
        }

    write(
        root,
        "api.github.com/repos/rv/files/contents.json",
        jdump(
            [
                f("my dir", kind="dir"),
                f("Zeta.R", size=10),
                f("alpha.csv", size=20),
                f("_under.txt"),
                f(".gitignore"),
                f("file."),
                f("archive.tar.gz", size=300),
                f("DATA.JSON", size=40),
                f("Makefile"),
                f("analysis.R", kind="dir"),
                f("Beta.md"),
                f("émoji.txt"),
            ]
        ),
    )
    write(
        root,
        "api.github.com/repos/rv/files/contents/my%20dir.json",
        jdump([f("x y.txt", "my dir/x y.txt", size=3), f("deeper", "my dir/deeper", "dir")]),
    )
    write(
        root,
        "api.github.com/repos/rv/files/contents/my%20dir/deeper.json",
        jdump([f("z.sh", "my dir/deeper/z.sh", size=5)]),
    )

    tree_entries = [
        {"path": "Makefile", "type": "blob", "size": 10},
        {"path": ".gitignore", "type": "blob", "size": 11},
        {"path": "src/x.c++", "type": "blob", "size": 12},
        {"path": "docs/READ ME.md", "type": "blob", "size": 13},
        {"path": "data/d.JSON", "type": "blob", "size": 14},
        {"path": "noext.", "type": "blob", "size": 15},
        {"path": "nosize.bin", "type": "blob"},
        {"path": "src", "type": "tree"},
        {"path": "vendored", "type": "commit"},
        {"path": "Ünïcode/файл.CSV", "type": "blob", "size": 16},
    ]
    # default_branch null -> "main"; spdx_id NOASSERTION kept
    write(
        root,
        "api.github.com/repos/rv/tree3.json",
        jdump({"default_branch": None, "license": {"spdx_id": "NOASSERTION"}}),
    )
    write(
        root,
        f"api.github.com/repos/rv/tree3/git/trees/main-{rec}.json",
        jdump({"tree": tree_entries, "truncated": False}),
    )
    # a default branch with a slash is put into the URL as it is
    write(
        root,
        "api.github.com/repos/rv/tree4.json",
        jdump({"default_branch": "feat/x", "license": {"key": "mit"}}),
    )
    write(
        root,
        f"api.github.com/repos/rv/tree4/git/trees/feat/x-{rec}.json",
        jdump({"tree": tree_entries[:3], "truncated": False}),
    )


def gitlab(root: Path) -> None:
    write(root, "gitlab.com/rv/glp-HEAD.html", "<html></html>\n")
    api = "gitlab.com/api/v4/projects/rv%2Fglp"
    write(
        root,
        f"{api}-{h('license=true')}.json",
        jdump({"default_branch": "feat/x y", "license": {"key": "mit", "name": "MIT"}}),
    )
    q1 = h("recursive=true&per_page=100&page=1")
    q2 = h("recursive=true&per_page=100&page=2")
    page1 = [
        {"name": "b.txt", "path": "b.txt", "type": "blob"},
        {"name": "a", "path": "a", "type": "tree"},
        {"name": "c.R", "path": "a/c.R", "type": "blob"},
    ]
    page2 = [
        {"name": "z.csv", "path": "z.csv", "type": "blob"},
        {"name": "w.JSON", "path": "w.JSON", "type": "blob"},
        {"name": "q.md", "path": "q.md", "type": "blob"},
    ]
    import json

    write(
        root,
        f"{api}/repository/tree-{q1}.R",
        r_response(
            "GET",
            "https://gitlab.com/api/v4/projects/rv%2Fglp/repository/tree"
            "?recursive=true&per_page=100&page=1",
            200,
            json.dumps(page1),
            {"x-next-page": "2", "x-total-pages": "2"},
        ),
    )
    write(
        root,
        f"{api}/repository/tree-{q2}.R",
        r_response(
            "GET",
            "https://gitlab.com/api/v4/projects/rv%2Fglp/repository/tree"
            "?recursive=true&per_page=100&page=2",
            200,
            json.dumps(page2),
            {"x-next-page": "", "x-total-pages": "2"},
        ),
    )
    paths = ["b.txt", "a/c.R", "z.csv", "w.JSON", "q.md"]
    # sizes come back as strings (GraphQL BigInt), out of order, one missing, one null
    write(
        root,
        f"gitlab.com/api/graphql-{h(graphql_body('rv/glp', paths), native=True)}-POST.json",
        jdump(
            {
                "data": {
                    "project": {
                        "repository": {
                            "blobs": {
                                "nodes": [
                                    {"path": "z.csv", "size": "300"},
                                    {"path": "b.txt", "size": "10"},
                                    {"path": "w.JSON", "size": None},
                                    {"path": "b.txt", "size": "99"},
                                ]
                            }
                        }
                    }
                }
            }
        ),
    )


def zenodo(root: Path) -> None:
    base = {
        "doi": "10.5281/zenodo.X",
        "updated": "2026-01-02T00:00:00+00:00",
        "owners": [{"id": 7}],
    }
    # licence with a title object but no id; keywords empty; no description,
    # stats or creators
    write(
        root,
        "zenodo.org/api/records/5559001.json",
        jdump(
            {
                **base,
                "id": 5559001,
                "metadata": {
                    "title": "Title only licence",
                    "license": {"title": {"en": "Custom licence"}},
                    "keywords": [],
                    "resource_type": {"title": "Dataset"},
                },
                "files": [],
            }
        ),
    )
    # licence id "" is kept (only a zero-length value falls through); no title
    write(
        root,
        "zenodo.org/api/records/5559002.json",
        jdump(
            {
                "id": 5559002,
                "metadata": {"license": {"id": ""}, "description": ""},
                "stats": {},
                "files": [{"key": "a.txt"}],
            }
        ),
    )
    # no metadata at all; big and fractional stats
    write(
        root,
        "zenodo.org/api/records/5559003.json",
        jdump(
            {
                "id": 5559003,
                "doi": "",
                "stats": {"downloads": 3000000000, "unique_downloads": 2, "views": 1.5},
            }
        ),
    )
    write(
        root,
        "zenodo.org/api/records/5559004.R",
        r_response("GET", "https://zenodo.org/api/records/5559004", 410, '{"status":410}'),
    )
    write(root, "zenodo.org/api/records/5559005.json", "null\n")
    write(root, "zenodo.org/api/records/5559006.html", "<html>not json</html>\n")
    # licence given as title string only
    write(
        root,
        "zenodo.org/api/records/5559007.json",
        jdump(
            {
                **base,
                "id": 5559007,
                "metadata": {"title": "T7", "license": {"title": "Plain title"}},
            }
        ),
    )

    # downloads: missing sizes/checksums/keys, and ties for the total cap
    mb = 1024 * 1024
    rec = "5559101"
    write(
        root,
        f"zenodo.org/api/records/{rec}.json",
        jdump(
            {
                "id": int(rec),
                "metadata": {"title": "Odd files"},
                "files": [
                    {
                        "id": "f-nosize",
                        "key": "nosize.txt",
                        "links": {"self": f"https://zenodo.org/api/records/{rec}/files/nosize.txt/content"},
                    },
                    {
                        "id": "f-nokey",
                        "size": 4,
                        "checksum": "md5:" + md5(b"abc\n"),
                        "links": {"self": f"https://zenodo.org/api/records/{rec}/files/nokey/content"},
                    },
                    {"id": "f-noself", "key": "noself.txt", "size": 2},
                    {
                        "id": "f-upper",
                        "key": "UPPER.CSV",
                        "size": 4,
                        "checksum": "MD5:" + md5(b"abc\n").upper(),
                        "links": {"self": f"https://zenodo.org/api/records/{rec}/files/UPPER.CSV/content"},
                    },
                    {
                        "id": "f-hexupper",
                        "key": "hexupper.csv",
                        "size": 4,
                        "checksum": "md5:" + md5(b"abc\n").upper(),
                        "links": {"self": f"https://zenodo.org/api/records/{rec}/files/hexupper.csv/content"},
                    },
                ],
            }
        ),
    )  # fmt: skip
    for key in ("nosize.txt", "nokey", "UPPER.CSV", "hexupper.csv"):
        write(root, f"zenodo.org/api/records/{rec}/files/{key}/content.txt", b"abc\n")

    rec = "5559102"
    write(
        root,
        f"zenodo.org/api/records/{rec}.json",
        jdump(
            {
                "id": int(rec),
                "metadata": {"title": "Ties"},
                "files": [
                    zenodo_file(root, rec, "six1.bin", b"1", size=6 * mb),
                    zenodo_file(root, rec, "six2.bin", b"2", size=6 * mb),
                    zenodo_file(root, rec, "one.bin", b"3", size=1 * mb),
                    zenodo_file(root, rec, "half.bin", b"4", size=0.5 * mb + 0.04 * mb),
                ],
            }
        ),
    )


def github_more(root: Path) -> None:
    """Second review round: odd /languages bodies, a README with a NUL, trees with
    an untyped entry (Filter() realignment) and a string size."""
    import base64

    rec = h("recursive=1")
    for repo in ("langarr", "langscalar", "readmenul", "tree5", "tree6"):
        write(root, f"github.com/rv/{repo}-HEAD.html", "<html></html>\n")
    # a JSON array / scalar has no names(): data.frame() drops `language`
    write(root, "api.github.com/repos/rv/langarr/languages.json", "[1, 2.5]\n")
    write(root, "api.github.com/repos/rv/langscalar/languages.json", '"x"\n')
    # rawToChar() refuses an embedded NUL
    write(
        root,
        "api.github.com/repos/rv/readmenul/readme.json",
        jdump({"content": base64.b64encode(b"a\x00b").decode()}),
    )
    write(root, "api.github.com/repos/rv/tree5.json", jdump({"default_branch": "main"}))
    write(
        root,
        f"api.github.com/repos/rv/tree5/git/trees/main-{rec}.json",
        jdump(
            {
                "tree": [
                    {"path": "a.txt", "type": "blob", "size": 1},
                    {"path": "notype.txt", "size": 2},
                    {"path": "d", "type": "tree"},
                    {"path": "c.txt", "type": "blob", "size": 3},
                    {"path": "e.txt", "type": "blob", "size": 4},
                ],
                "truncated": False,
            }
        ),
    )
    write(root, "api.github.com/repos/rv/tree6.json", jdump({"default_branch": "main"}))
    write(
        root,
        f"api.github.com/repos/rv/tree6/git/trees/main-{rec}.json",
        jdump({"tree": [{"path": "s.txt", "type": "blob", "size": "12"}], "truncated": False}),
    )


def gitlab_more(root: Path) -> None:
    """An untyped and a pathless tree entry, a page that is a JSON object, and
    GraphQL nodes given as an object (including a node for the path "")."""
    import json

    write(root, "gitlab.com/rv/glq-HEAD.html", "<html></html>\n")
    write(
        root,
        "gitlab.com/rv/nope-HEAD.R",
        r_response("HEAD", "https://gitlab.com/rv/nope", 404, "", {"content-type": "text/html"}),
    )
    api = "gitlab.com/api/v4/projects/rv%2Fglq"
    write(root, f"{api}-{h('license=true')}.json", jdump({"default_branch": "main"}))
    page1 = [
        {"name": "a.txt", "path": "a.txt", "type": "blob"},
        {"name": "notype", "path": "notype"},
        {"name": "d", "path": "d", "type": "tree"},
        {"name": "c.txt", "path": "c.txt", "type": "blob"},
        {"name": "x", "type": "blob"},
    ]
    page2 = {"p": {"name": "e.txt", "path": "e.txt", "type": "blob"}}
    for page, body, nxt in ((1, page1, "2"), (2, page2, "")):
        q = f"recursive=true&per_page=100&page={page}"
        write(
            root,
            f"{api}/repository/tree-{h(q)}.R",
            r_response(
                "GET",
                f"https://gitlab.com/api/v4/projects/rv%2Fglq/repository/tree?{q}",
                200,
                json.dumps(body),
                {"x-next-page": nxt},
            ),
        )
    # Filter() keeps entries 1, 3, 4 and 5 of the six (the untyped one shifts
    # the flags, so the tree "d" is kept and the blob "e.txt" lost)
    paths = ["a.txt", "d", "c.txt", ""]
    write(
        root,
        f"gitlab.com/api/graphql-{h(graphql_body('rv/glq', paths), native=True)}-POST.json",
        jdump(
            {
                "data": {
                    "project": {
                        "repository": {
                            "blobs": {
                                "nodes": {
                                    "n1": {"path": "", "size": "7"},
                                    "n2": {"path": "a.txt", "size": 1},
                                    "n3": {"path": "d", "size": 2.5},
                                    "n4": {"path": "e.txt", "size": None},
                                }
                            }
                        }
                    }
                }
            }
        ),
    )


def zenodo_upload_more(root: Path) -> None:
    """proj_plain uploaded with user metadata holding doubles: the PUT body must be
    jsonlite's (digits = 22), e.g. 0.1 is 0.10000000000000001 and 2 has no ".0"."""
    from tests.archives_gz.make_mocks import multipart_hash, upload_folders

    api = "sandbox.zenodo.org/api/deposit/depositions"
    write(root, f"{api}-{h('size=1')}.json", "[]\n")
    write(
        root,
        f"{api}-{h('{}', native=True)}-POST.json",
        jdump({"id": 123, "links": {"html": "https://sandbox.zenodo.org/deposit/123"}}),
    )
    files = upload_folders()["proj_plain"]
    for rel, name in (
        ("notes.txt", "notes.txt"),
        ("results.csv", "results.csv"),
        ("sub/results.csv", "sub__results.csv"),
    ):
        write(
            root,
            f"{api}/123/files-{multipart_hash(name, files[rel])}-POST.json",
            jdump({"id": f"f-{name}", "filename": name}),
        )
    body = (
        '{"metadata":{"title":"proj_plain","upload_type":"dataset",'
        '"description":"Files archived from proj_plain","creators":[{"name":"Unknown"}],'
        '"license":"cc-by-4.0","version":2,"weight":0.10000000000000001,"count":3,'
        '"tiny":1.0000000000000001e-05,"keywords":["a",null]}}'
    )
    write(root, f"{api}/123-{h(body, native=True)}-PUT.json", jdump({"id": 123}))
    # published only when the metadata PUT above matched
    write(
        root,
        f"{api}/123/actions/publish-POST.json",
        jdump({"id": 123, "doi": "10.5072/zenodo.123.pub"}),
    )


def raw_response(url: str, body: bytes, ctype: str = "application/json") -> str:
    """A deparsed 200 ``httr2_response`` whose body is given byte for byte."""
    raw = ", ".join(f"0x{b:02x}" for b in body)
    return (
        f'structure(list(method = "GET", url = "{url}", status_code = 200L, '
        f'headers = structure(list(`content-type` = "{ctype}"), class = "httr2_headers"), '
        f"body = as.raw(c({raw})), cache = new.env(parent = emptyenv())), "
        'class = "httr2_response")\n'
    )


# Zenodo records whose bodies jsonlite (yajl) and json.loads() read differently
ZENODO_JSON_QUIRKS: dict[str, tuple[bytes, str]] = {
    # comments and a vertical tab between tokens; comment markers in strings kept
    "5559201": (
        b'{"id": 5559201, /* a comment */ "metadata": {"title": "T /* kept */ // kept",\x0b'
        b' "description": "D"}, // trailing\n "doi": "10.5281/zenodo.5559201"}',
        "application/json",
    ),
    # repeated keys: `$` finds the first value
    "5559202": (
        b'{"id": 5559202, "metadata": {"title": "first", "title": "second"},'
        b' "doi": "10.5281/zenodo.first", "doi": "10.5281/zenodo.second"}',
        "application/json",
    ),
    # an escaped NUL ends the string; a lone high surrogate becomes "?"
    "5559203": (
        b'{"id": 5559203, "metadata": {"title": "before\\u0000after", "description": "x\\ud800y"}}',
        "application/json",
    ),
    # a byte-order mark is dropped (with a warning)
    "5559204": (
        b'\xef\xbb\xbf{"id": 5559204, "metadata": {"title": "Caf\xc3\xa9"}}',
        "application/json",
    ),
    # the body is read as UTF-8 whatever charset the response names
    "5559205": (
        b'{"id": 5559205, "metadata": {"title": "Caf\xc3\xa9 \xe2\x82\xac"}}',
        "application/json; charset=ISO-8859-1",
    ),
    # NaN is not JSON to yajl: a parse error
    "5559206": (b'{"id": 5559206, "stats": {"views": NaN}}', "application/json"),
    # bytes that are not UTF-8: NA text, a parse error
    "5559207": (b'{"id": 5559207, "metadata": {"title": "Caf\xe9"}}', "application/json"),
    # the body ends at the first NUL byte; integers past 2^31 are doubles
    "5559208": (
        b'{"id": 5559208, "stats": {"downloads": 3000000000, "views": -2147483648,'
        b' "unique_downloads": 2147483647}}\x00trailing garbage',
        "application/json",
    ),
    # a +json media type is JSON too
    "5559209": (
        b'{"id": 5559209, "metadata": {"title": "vnd"}}',
        "application/vnd.api+json; charset=utf-8",
    ),
    # an empty body cannot be read
    "5559210": (b"", "application/json"),
}


def zenodo_json(root: Path) -> None:
    for rec, (body, ctype) in ZENODO_JSON_QUIRKS.items():
        write(
            root,
            f"zenodo.org/api/records/{rec}.R",
            raw_response(f"https://zenodo.org/api/records/{rec}", body, ctype),
        )
    # .zenodo_info(character(0)) asks for the record listing (paste0() drops
    # the empty ID)
    write(root, "zenodo.org/api/records.json", jdump({"hits": {"hits": [], "total": 0}}))


def main() -> None:
    if MOCKS.exists():
        shutil.rmtree(MOCKS)
    github(MOCKS)
    github_more(MOCKS)
    gitlab(MOCKS)
    gitlab_more(MOCKS)
    zenodo(MOCKS)
    zenodo_json(MOCKS)
    zenodo_upload_more(MOCKS)


if __name__ == "__main__":
    main()
