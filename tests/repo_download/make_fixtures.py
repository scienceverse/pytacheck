"""Build the fixture archives and recorded HTTP responses for the repo_download tests.

Run from the repository root (needs the ``zip`` command line tool, like
metacheck's own tests, for the archives with local-header extra fields)::

    .venv/bin/python tests/repo_download/make_fixtures.py

Writes ``tests/repo_download/data/`` (archives, compressed files, sample
downloads) and ``tests/repo_download/mocks/`` (httptest2-format responses
for ``zip_peek()`` parity cases: a ``HEAD`` with ``Content-Length`` and a
``206``/``200`` range answer holding the archive's bytes). The outputs are
committed; this script documents how they were made.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
MOCKS = HERE / "mocks"
FIXED = (2024, 1, 2, 3, 4, 6)
EPOCH = 1704164646  # 2024-01-02T03:04:06Z


def _zipfile(
    path: Path, members: list[tuple[str, bytes]], method: int, comment: bytes = b""
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=FIXED)
            info.compress_type = method
            info.external_attr = (0o40755 << 16) | 0x10 if name.endswith("/") else 0o644 << 16
            zf.writestr(info, data)
        zf.comment = comment


def _cli_zip(path: Path, members: dict[str, bytes]) -> None:
    """A zip made by the ``zip`` tool (as ``utils::zip()`` does), with extra fields."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, data in members.items():
            f = root / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            os.utime(f, (EPOCH, EPOCH))
        for d in sorted({p for p in root.rglob("*") if p.is_dir()}, reverse=True):
            os.utime(d, (EPOCH, EPOCH))
        subprocess.run(["zip", "-q", "-r", "out.zip", *sorted(members)], cwd=root, check=True)
        shutil.copyfile(root / "out.zip", path)


def _patch_zip64(src: Path, dest: Path) -> None:
    """Copy *src* with every central-directory size/offset set to the Zip64 sentinel."""
    raw = bytearray(src.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    n_entries, _cd_size, cd_offset = struct.unpack_from("<HII", raw, eocd + 10)
    p = cd_offset
    for k in range(n_entries):
        name_len, extra_len, comm_len = struct.unpack_from("<HHH", raw, p + 28)
        if k != 1:  # the second member keeps its real values
            struct.pack_into("<II", raw, p + 20, 0xFFFFFFFF, 0xFFFFFFFF)  # csize, usize
            struct.pack_into("<I", raw, p + 42, 0xFFFFFFFF)  # local header offset
        p += 46 + name_len + extra_len + comm_len
    dest.write_bytes(bytes(raw))


def _r_raw(data: bytes) -> str:
    """``as.raw(c(0x.., ...))`` as dput() writes it."""
    if not data:
        return "raw(0)"
    body = ", ".join(f"0x{b:02x}" for b in data)
    return f"as.raw(c({body}))"


def _mock(
    path: Path, url: str, method: str, status: int, headers: dict[str, str], body: bytes
) -> None:
    hdr = ", ".join(f'`{k}` = "{v}"' for k, v in headers.items())
    text = (
        f'structure(list(method = "{method}", url = "{url}", status_code = {status}L, '
        f'headers = structure(list({hdr}), class = "httr2_headers"), body = {_r_raw(body)}, '
        'timing = NULL, cache = new.env(parent = emptyenv())), class = "httr2_response")\n'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _zip_mocks(name: str, data: bytes, *, range_status: int = 206, head: bool = True) -> None:
    """Recorded answers for ``https://mock.example.org/zips/<name>``."""
    url = f"https://mock.example.org/zips/{name}"
    base = MOCKS / "mock.example.org" / "zips" / name
    if head:
        _mock(
            base.with_name(f"{name}-HEAD.R"),
            url,
            "HEAD",
            200,
            {"Content-Type": "application/zip", "Content-Length": str(len(data))},
            b"",
        )
    _mock(
        base.with_name(f"{name}.R"),
        url,
        "GET",
        range_status,
        {"Content-Type": "application/zip"},
        data,
    )


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    csv = b"id,x\n" + b"".join(f"{i},{i * 2}\n".encode() for i in range(200))
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))

    _cli_zip(
        DATA / "mixed.zip",
        {
            "study.csv": csv,
            "stim.png": png,
            "README.txt": b"notes\n",
            "analysis.R": b"x <- read.csv('study.csv')\nsummary(x)\n",
            "sub/codebook.csv": b"variable,label\nid,Participant\nx,Score\n",
            "__MACOSX/._study.csv": b"\x00\x05\x16\x07",
        },
    )
    content = "".join(f"line {i} {'x' * 20}\n" for i in range(1, 3001)).encode()
    _cli_zip(DATA / "big.zip", {"big.R": content})
    _zipfile(
        DATA / "stored.zip",
        [("data.csv", csv), ("notes.txt", b"hello\n"), ("empty.txt", b"")],
        zipfile.ZIP_STORED,
    )
    _zipfile(
        DATA / "comment.zip",
        [("folder/", b""), ("folder/results.csv", csv), ("folder/plot.png", png)],
        zipfile.ZIP_DEFLATED,
        comment=b"archived by the lab " * 10,
    )
    _zipfile(
        DATA / "stimuli.zip",
        [("a.png", png), ("b.jpg", png), ("readme.txt", b"stimuli\n")],
        zipfile.ZIP_DEFLATED,
    )
    _zipfile(
        DATA / "unicode.zip",
        [("données/résumé.csv", csv), ("übersicht.txt", b"x\n")],
        zipfile.ZIP_DEFLATED,
    )
    _zipfile(DATA / "empty.zip", [], zipfile.ZIP_DEFLATED)
    _patch_zip64(DATA / "stored.zip", DATA / "zip64.zip")
    (DATA / "notzip.bin").write_bytes(b"this is not a zip archive\n" * 40)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in (
            ("proj/data.csv", csv),
            ("proj/code.R", b"1 + 1\n"),
            ("proj/img.png", png),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = EPOCH
            tf.addfile(info, io.BytesIO(data))
    (DATA / "archive.tar.gz").write_bytes(gzip.compress(buf.getvalue(), mtime=0))
    (DATA / "results.csv.gz").write_bytes(gzip.compress(csv, mtime=0))
    (DATA / "results.csv.bz2").write_bytes(bz2.compress(csv))
    (DATA / "results.csv.xz").write_bytes(lzma.compress(csv))
    (DATA / "plain.csv.gz").write_bytes(csv)  # not actually compressed
    (DATA / "stimuli.png.gz").write_bytes(gzip.compress(png, mtime=0))

    # sample downloads for .is_login_page()
    (DATA / "login.html").write_bytes(
        b"<!DOCTYPE html>\n<html><head><title>OSF | Sign in</title></head>"
        b"<body>Please sign in</body></html>\n"
    )
    (DATA / "page.html").write_bytes(b"<html><body>A normal page</body></html>\n")
    (DATA / "binary.dat").write_bytes(b"<html>sign in\x00\x01\x02")

    shutil.rmtree(MOCKS, ignore_errors=True)
    for name in (
        "mixed.zip",
        "big.zip",
        "stored.zip",
        "comment.zip",
        "stimuli.zip",
        "unicode.zip",
        "zip64.zip",
    ):
        _zip_mocks(name, (DATA / name).read_bytes())
    _zip_mocks("notzip.zip", (DATA / "notzip.bin").read_bytes())
    _zip_mocks("ignored-range.zip", (DATA / "mixed.zip").read_bytes(), range_status=200)
    _zip_mocks("refused.zip", b"Forbidden", range_status=403)
    _zip_mocks("nohead.zip", (DATA / "mixed.zip").read_bytes(), head=False)


if __name__ == "__main__":
    main()
