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

from tests.archives_gz.make_mocks import (  # noqa: E402
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


def main() -> None:
    if MOCKS.exists():
        shutil.rmtree(MOCKS)
    github(MOCKS)
    gitlab(MOCKS)
    zenodo(MOCKS)


if __name__ == "__main__":
    main()
