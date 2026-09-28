"""Write the recorded responses of the ``mod_repo_check_review`` parity cases.

python tests/mod_repo_check/make_review_mocks.py

Adds to ``tests/mod_repo_check/mocks`` (httptest2 file naming, see
``make_mocks.py``):

* ``mock.example.org/review/deposit.zip``: a zip whose central directory
  ``zip_peek()`` reads (HEAD + range GET) -- a README, directory entries,
  nested and non-ASCII member names, a zip inside the zip and a ``.tar.gz``;
* Zenodo record 5559101, which lists that zip beside a script, and record
  5559102, which lists the same zip URL again (a duplicate row dropped
  before peeking).
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

MOCKS = HERE / "mocks"
ZIP_URL = "https://mock.example.org/review/deposit.zip"
MEMBERS = [
    "README.md",
    "data/",
    "data/raw data.csv",
    "data/participant_01.csv",
    "data/participant_2.csv",
    "Study 1/",
    "Study 1/analysis.R",
    "données.csv",
    "inner.zip",
    "results.tar.gz",
    "task.edat2",
]


def _write(rel: str, content: str | bytes) -> None:
    path = MOCKS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def deposit_zip() -> bytes:
    """The review zip, byte-identical on every run (fixed timestamps)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for name in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            z.writestr(info, b"" if name.endswith("/") else f"{name}\n".encode())
    return buf.getvalue()


def main() -> None:
    from tests.mod_repo_check.make_mocks import r_response

    body = deposit_zip()
    _write(
        "mock.example.org/review/deposit.zip-HEAD.R",
        # "Content-Length" capitalised, as in the other zip mocks: the Python
        # replayer drops a lower-case content-length (it recomputes it from the
        # body), which would lose a HEAD response's size
        r_response("HEAD", ZIP_URL, 200, b"", content_type="application/zip").replace(
            '(`content-type` = "application/zip")',
            f'(`Content-Type` = "application/zip", `Content-Length` = "{len(body)}")',
        ),
    )
    _write(
        "mock.example.org/review/deposit.zip.R",
        r_response("GET", ZIP_URL, 206, body, content_type="application/zip"),
    )

    def f(key: str, size: int, url: str) -> dict[str, Any]:
        return {"id": key, "key": key, "size": size, "checksum": "md5:0", "links": {"self": url}}

    for rid, files in (
        (
            5559101,
            [
                f("deposit.zip", len(body), ZIP_URL),
                f(
                    "analysis.R",
                    300,
                    "https://zenodo.org/api/records/5559101/files/analysis.R/content",
                ),
            ],
        ),
        (5559102, [f("deposit.zip", len(body), ZIP_URL)]),
    ):
        rec = {
            "id": rid,
            "doi": f"10.5281/zenodo.{rid}",
            "metadata": {"title": "Zipped deposit", "license": {"id": "cc-by-4.0"}},
            "files": files,
        }
        _write(f"zenodo.org/api/records/{rid}.json", json.dumps(rec))


if __name__ == "__main__":
    main()
