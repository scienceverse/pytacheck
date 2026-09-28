"""Write the httptest2-format mock responses of the mod_repo_check tests and parity cases.

python tests/mod_repo_check/make_mocks.py

metacheck records OSF, GitHub (contents API), Zenodo and PsychArchives
responses (``upstream/.../testthat/apis``), which the cases use directly. This
script adds, under ``tests/mod_repo_check/mocks``:

* synthetic OSF registrations whose source project is private (with and
  without a copy of the files), public, or missing, and a private project --
  built from metacheck's own OSF recordings with the ids rewritten;
* a legacy DSpace item with restricted access;
* a Zenodo record whose ``.zip`` files can be peeked (the zip recordings of
  ``tests/repo_download/mocks``) or not;
* the ResearchBox zip download (a POST: a 404 here, the zip in
  ``mocks_researchbox`` -- R cannot replay a download to a file);
* copies of the recordings of the other archive areas (``tests/archives_*``)
  that the platform cases request -- found by replaying those cases and
  keeping the files they read, so this directory is self-contained.

File names follow ``httptest2::build_mock_url()`` (query and body hashes), so
the same directory serves R (httptest2) and Python (``parity_support``).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from tests.httpmock import fixture_response

MOCKS = HERE / "mocks"
MOCKS_RB = HERE / "mocks_researchbox"
APIS = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "apis"
SOURCES = [
    ROOT / "tests" / "archives_d1" / "mocks",
    ROOT / "tests" / "archives_d2" / "mocks",
    ROOT / "tests" / "archives_gz" / "mocks",
    ROOT / "tests" / "repo_download" / "mocks",
]


def write(rel: str, content: str | bytes) -> None:
    path = MOCKS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def r_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def r_response(
    method: str,
    url: str,
    status: int,
    body: str | bytes = "",
    content_type: str = "application/json; charset=utf-8",
) -> str:
    """A deparsed ``httr2_response`` (raw bodies inline, not in an .R-FILE)."""
    if isinstance(body, bytes):
        hexes = ", ".join(f"0x{b:02x}" for b in body)
        body_r = f"as.raw(c({hexes}))"
    else:
        body_r = f"charToRaw({r_string(body)})"
    return (
        f"structure(list(method = {r_string(method)}, url = {r_string(url)}, "
        f"status_code = {status}L, headers = structure(list(`content-type` = "
        f'{r_string(content_type)}), class = "httr2_headers"), body = {body_r}, '
        f'cache = new.env(parent = emptyenv())), class = "httr2_response")\n'
    )


def recorded(path: str) -> str:
    """The body of one of metacheck's OSF recordings."""
    resp = fixture_response(APIS, path)
    assert resp is not None, path
    return resp.content.decode("utf-8")


def new_ids(text: str, salt: str) -> str:
    """Give every 24-hex OSF file id a new id (so copies are distinct files)."""

    def repl(m: re.Match[str]) -> str:
        return hashlib.md5((salt + m.group(0)).encode(), usedforsecurity=False).hexdigest()[:24]

    return re.sub(r"\b[0-9a-f]{24}\b", repl, text)


# ---------------------------------------------------------------------------
# OSF
# ---------------------------------------------------------------------------

EMPTY_LIST = recorded("api.osf.io/v2/nodes/y6a34/children")
REG_GUID = recorded("api.osf.io/v2/guids/8c3kb")  # a registration (registered_from 3cz2e)
NODE_GUID = recorded("api.osf.io/v2/guids/629bx")  # a public project
FILES = recorded("api.osf.io/v2/nodes/629bx/files")
OSFSTORAGE = recorded("api.osf.io/v2/nodes/629bx/files/osfstorage")


def osf_registration(reg: str, parent: str | None, with_files: bool) -> None:
    body = REG_GUID.replace("8c3kb", reg)
    if parent is None:
        body = json.dumps(_drop_registered_from(json.loads(body)))
    else:
        body = body.replace("3cz2e", parent)
    write(f"api.osf.io/v2/guids/{reg}.json", body)
    write(f"api.osf.io/v2/registrations/{reg}/children.json", EMPTY_LIST)
    if with_files:
        files = FILES.replace("nodes/629bx", f"registrations/{reg}").replace("629bx", reg)
        storage = OSFSTORAGE.replace("nodes/629bx", f"registrations/{reg}").replace("629bx", reg)
        write(f"api.osf.io/v2/registrations/{reg}/files.json", new_ids(files, reg))
        write(f"api.osf.io/v2/registrations/{reg}/files/osfstorage.json", new_ids(storage, reg))
    else:
        write(f"api.osf.io/v2/registrations/{reg}/files.json", EMPTY_LIST)


def _drop_registered_from(doc: dict[str, Any]) -> dict[str, Any]:
    doc["data"]["relationships"].pop("registered_from", None)
    return doc


def osf_public(node: str) -> None:
    write(f"api.osf.io/v2/guids/{node}.json", NODE_GUID.replace("629bx", node))
    write(f"api.osf.io/v2/nodes/{node}/children.json", EMPTY_LIST)
    files = FILES.replace("629bx", node)
    storage = OSFSTORAGE.replace("629bx", node)
    write(f"api.osf.io/v2/nodes/{node}/files.json", new_ids(files, node))
    write(f"api.osf.io/v2/nodes/{node}/files/osfstorage.json", new_ids(storage, node))


def osf_private(node: str) -> None:
    body = json.dumps(
        {"errors": [{"detail": "You do not have permission to perform this action."}]}
    )
    write(
        f"api.osf.io/v2/guids/{node}.R",
        r_response("GET", f"https://api.osf.io/v2/guids/{node}", 403, body),
    )


def osf() -> None:
    # registration with its own copy of the files; source project private
    osf_registration("regc1", "parc1", with_files=True)
    osf_private("parc1")
    # registration without files; source project private
    osf_registration("regc2", "parc2", with_files=False)
    osf_private("parc2")
    # registration without files; source project public (files listed there)
    osf_registration("regp3", "parp3", with_files=False)
    osf_public("parp3")
    # registration with files and no registered_from
    osf_registration("regor", None, with_files=True)
    # a second pair of each closed kind (plural report texts)
    osf_registration("regc5", "parc5", with_files=True)
    osf_private("parc5")
    osf_registration("regc6", "parc6", with_files=False)
    osf_private("parc6")
    # a private project linked directly
    osf_private("privn")
    # the OSF project of the bibr 12.0 fixture preprint.json
    osf_public("n9uvj")
    # osf_license = TRUE: 629bx's licence (nodes/629bx/?embed=license)
    write(
        "api.osf.io/v2/nodes/629bx-5c8228.json",
        json.dumps(
            {
                "data": {
                    "id": "629bx",
                    "type": "nodes",
                    "embeds": {
                        "license": {
                            "data": {
                                "id": "563c1cf88c5e4a3877f9e96a",
                                "type": "licenses",
                                "attributes": {"name": "CC-By Attribution 4.0 International"},
                            }
                        }
                    },
                }
            }
        ),
    )


# ---------------------------------------------------------------------------
# DSpace (legacy REST API): restricted access
# ---------------------------------------------------------------------------


def dspace() -> None:
    host = "bonndoc.ulb.uni-bonn.de/rest"
    write(
        f"{host}/handle/20.500.11811/5678.json",
        json.dumps({"uuid": "u-bonn-5678", "name": "Restricted item"}),
    )
    write(
        f"{host}/items/u-bonn-5678-afaea9.json",
        json.dumps(
            {
                "uuid": "u-bonn-5678",
                "metadata": [
                    {"key": "dc.contributor.author", "value": "Author, A."},
                    {"key": "dc.rights", "value": "info:eu-repo/semantics/restrictedAccess"},
                    {"key": "dc.identifier.doi", "value": "10.48565/bonndoc-5678"},
                    {"key": "dc.date.available", "value": "2021-01-01"},
                ],
            }
        ),
    )
    bitstreams = [
        {
            "uuid": f"bs-{i}",
            "name": name,
            "type": "bitstream",
            "bundleName": "ORIGINAL",
            "sizeBytes": size,
            "retrieveLink": f"/rest/bitstreams/bs-{i}/retrieve",
        }
        for i, (name, size) in enumerate((("readme.txt", 812), ("trial data.csv", 20480)), 1)
    ]
    write(f"{host}/items/u-bonn-5678/bitstreams-77c04c.json", json.dumps(bitstreams))


# ---------------------------------------------------------------------------
# Zenodo: a record whose zips can (or cannot) be peeked
# ---------------------------------------------------------------------------


def zenodo() -> None:
    def f(key: str, size: int, url: str) -> dict[str, Any]:
        return {"id": key, "key": key, "size": size, "checksum": "md5:0", "links": {"self": url}}

    rec = {
        "id": 5559001,
        "doi": "10.5281/zenodo.5559001",
        "metadata": {
            "title": "Stimuli and data",
            "license": {"id": "cc-by-4.0"},
            "creators": [{"name": "Doe, Jane"}],
            "publication_date": "2026-01-01",
        },
        "files": [
            f("stimuli.zip", 3000, "https://mock.example.org/zips/stimuli.zip"),
            f("notzip.zip", 64, "https://mock.example.org/zips/notzip.zip"),
            f(
                "config.json",
                120,
                "https://zenodo.org/api/records/5559001/files/config.json/content",
            ),
            f(
                "results.tar.gz",
                4096,
                "https://zenodo.org/api/records/5559001/files/results.tar.gz/content",
            ),
            f(
                "session1.edat2",
                1024,
                "https://zenodo.org/api/records/5559001/files/session1.edat2/content",
            ),
            f("analysis.R", 300, "https://zenodo.org/api/records/5559001/files/analysis.R/content"),
        ],
        "stats": {"downloads": 1, "unique_downloads": 1, "views": 2},
    }
    write("zenodo.org/api/records/5559001.json", json.dumps(rec))
    # a file without a name: its NA path makes check_file_naming() (and the module) fail
    rec2 = {
        "id": 5559002,
        "doi": "10.5281/zenodo.5559002",
        "metadata": {"title": "Nameless", "license": {"id": "cc0-1.0"}},
        "files": [
            f("data.csv", 10, "https://zenodo.org/api/records/5559002/files/data.csv/content"),
            {"id": "noname", "size": 1},
        ],
    }
    write("zenodo.org/api/records/5559002.json", json.dumps(rec2))


# ---------------------------------------------------------------------------
# DataONE: a KNB dataset (EML) whose files all have names
# ---------------------------------------------------------------------------


def dataone() -> None:
    src = SOURCES[1] / "arcticdata.io/metacat/d1/mn/v2/object/doi%3A10.18739%2FA2GT5FG86.xml"
    xml = src.read_text(encoding="utf-8")
    # drop the last <physical> (no objectName), keep the two named files
    last = xml.rfind("<physical")
    end = xml.find("</physical>", last) + len("</physical>")
    xml = xml[:last] + xml[end:]
    xml = xml.replace("10.18739/A2GT5FG86", "10.5063/F1TIDY")
    write("knb.ecoinformatics.org/knb/d1/mn/v2/object/doi%3A10.5063%2FF1TIDY.xml", xml)


# ---------------------------------------------------------------------------
# ResearchBox: the zip of box 4377 (POST download_files.php)
# ---------------------------------------------------------------------------


def researchbox() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in (
            ("Data/Study 1.csv", "id,x\n1,2\n"),
            ("Data/Study 1 - csv - dataset 1.csv", "id,y\n1,3\n"),
            ("Code/Study 1.r", "d <- read.csv('Study 1.csv')\n"),
            ("Materials/Study 1 - test txt file.txt", "test\n"),
        ):
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)
    path = "researchbox.org/download_files.php-00e5b5-POST.R"
    url = "https://researchbox.org/download_files.php"
    # R cannot replay a download: under httptest2, httr2 does not write a mocked
    # body to `req_perform(path = )`, so metacheck's download "fails" in mock
    # mode. The parity mocks answer 404 (both sides take that failure path);
    # the zip itself is served from mocks_researchbox, for the Python tests.
    write(path, r_response("POST", url, 404, "Not found", "text/plain"))
    dest = MOCKS_RB / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(r_response("POST", url, 200, buf.getvalue(), "application/zip"))


# ---------------------------------------------------------------------------
# copies of the other areas' recordings
# ---------------------------------------------------------------------------


def copy_recorded() -> None:
    """Replay the platform cases over the source directories; copy what they read."""
    import warnings

    import tests.mod_repo_check.parity_support as ps
    from tests.mod_repo_check.make_cases import PLATFORM_URLS

    hits: set[tuple[Path, str]] = set()
    orig = ps._response

    def logged(root: Path, path: str) -> Any:
        resp = orig(root, path)
        if resp is not None and root in SOURCES:
            hits.add((root, path))
        return resp

    ps._response = logged
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for urls in PLATFORM_URLS:
                try:
                    ps.run_repo(
                        [{"url": urls, "id": "p"}], mocks=["local", *map(str, SOURCES), "apis"]
                    )
                except Exception as e:
                    print("case error:", urls, e)
    finally:
        ps._response = orig

    for root, path in sorted(hits):
        for ext in (".R", ".R-FILE", ".json", ".html", ".xml", ".txt"):
            f = root / f"{path}{ext}"
            if not f.exists():
                continue
            dest = MOCKS / f.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            print("copied", f.relative_to(ROOT))


def main() -> None:
    osf()
    dspace()
    zenodo()
    dataone()
    researchbox()
    copy_recorded()


if __name__ == "__main__":
    main()
