"""Build the extra fixtures of the review parity cases (``repo_download_review``).

Run from the repository root::

    .venv/bin/python tests/repo_download/make_review_fixtures.py

Writes ``tests/repo_download/data/review/``: zips whose member names or
central-directory fields are edge cases (names that climb out of the target
directory or collide with a file, CP437 names without the UTF-8 flag, a name
with trailing NULs, CRC/size mismatches) and hand-built central directories
(``*.cd``) for ``.parse_zip_central_dir()``. Deterministic: the zips are
written by :mod:`zipfile` with fixed timestamps.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "review"
FIXED = (2024, 1, 2, 3, 4, 6)

CSV = b"id,x\n" + b"".join(f"{i},{i * 3}\n".encode() for i in range(120))


def _zip(path: Path, members: list[tuple[str, bytes, int]]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, method in members:
            info = zipfile.ZipInfo(name, date_time=FIXED)
            info.compress_type = method
            info.external_attr = (0o40755 << 16) | 0x10 if name.endswith("/") else 0o644 << 16
            zf.writestr(info, data)


def _walk_cd(raw: bytes) -> list[int]:
    """Offsets of the central-directory headers."""
    eocd = raw.rfind(b"PK\x05\x06")
    n, _size, off = struct.unpack_from("<HII", raw, eocd + 10)
    out = []
    p = off
    for _ in range(n):
        out.append(p)
        name_len, extra_len, comm_len = struct.unpack_from("<HHH", raw, p + 28)
        p += 46 + name_len + extra_len + comm_len
    return out


def _patch(path: Path, names: dict[bytes, bytes], crc: dict[bytes, int] | None = None,
           usize: dict[bytes, int] | None = None) -> None:
    """Rename members (same byte length) in both headers; change CD crc/size fields."""
    raw = bytearray(path.read_bytes())
    for p in _walk_cd(bytes(raw)):
        name_len = struct.unpack_from("<H", raw, p + 28)[0]
        name = bytes(raw[p + 46 : p + 46 + name_len])
        local = struct.unpack_from("<I", raw, p + 42)[0]
        if crc and name in crc:
            struct.pack_into("<I", raw, p + 16, crc[name])
        if usize and name in usize:
            struct.pack_into("<I", raw, p + 24, usize[name])
        if name in names:
            new = names[name]
            assert len(new) == len(name)
            raw[p + 46 : p + 46 + name_len] = new
            lname = struct.unpack_from("<H", raw, local + 26)[0]
            assert bytes(raw[local + 30 : local + 30 + lname]) == name
            raw[local + 30 : local + 30 + lname] = new
    path.write_bytes(bytes(raw))


def _cd_entry(name: bytes, usize: int = 3, csize: int = 3, offset: int = 0) -> bytes:
    return (
        b"PK\x01\x02"
        + struct.pack("<HHHHHHIIIHHHHHII", 20, 20, 0, 0, 0, 0, 0, csize, usize,
                      len(name), 0, 0, 0, 0, 0, offset)
        + name
    )


def _cd(entries: list[bytes]) -> bytes:
    cd = b"".join(entries)
    prefix = b"\x00" * 64
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, len(entries), len(entries),
                                        len(cd), len(prefix), 0)
    return prefix + cd + eocd


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    d = zipfile.ZIP_DEFLATED
    # member paths: a file "a" before "a/b.csv" (the directory cannot be made),
    # climbing / absolute / drive / backslash names, an empty name, duplicates
    _zip(
        OUT / "paths.zip",
        [
            ("a", b"plain file a\n", d),
            ("a/b.csv", CSV, d),
            ("../evil.csv", b"x\n", d),
            ("/abs.csv", b"abs\n", d),
            ("C:/drive.csv", b"drive\n", d),
            ("sub\\win.csv", b"win\n", d),
            ("ok/data.csv", CSV, zipfile.ZIP_STORED),
            ("dir/", b"", zipfile.ZIP_STORED),
            ("dup.csv", b"first\n", d),
            ("dup.csv", b"second copy\n", d),
            ("z/../../up.csv", b"up\n", d),
        ],
    )
    # CRC / size mismatches between the central directory and the data
    _zip(
        OUT / "crc.zip",
        [
            ("good.csv", CSV, d),
            ("badcrc.csv", CSV, d),
            ("badsize.csv", CSV, d),
            ("stored.txt", b"stored text\n", zipfile.ZIP_STORED),
            ("empty.csv", b"", d),
        ],
    )
    _patch(OUT / "crc.zip", {}, crc={b"badcrc.csv": 12345}, usize={b"badsize.csv": len(CSV) + 1})
    # CP437 names without the UTF-8 flag (invalid UTF-8 bytes), a name with a
    # trailing NUL
    _zip(
        OUT / "cp437.zip",
        [
            ("donnQes/", b"", zipfile.ZIP_STORED),
            ("donnQes/rQsumQ.csv", CSV, d),
            ("cafQ.R", b"x <- 1\n", d),
            ("plain.csv", CSV, d),
            ("nul.csvZ", b"a,b\n1,2\n", d),
        ],
    )
    _patch(
        OUT / "cp437.zip",
        {
            b"donnQes/": b"donn\x82es/",
            b"donnQes/rQsumQ.csv": b"donn\x82es/r\x82sum\x82.csv",
            b"cafQ.R": b"caf\x82.R",
            b"nul.csvZ": b"nul.csv\x00",
        },
    )
    # hand-built central directories for .parse_zip_central_dir()
    cds = {
        "trailnul.cd": [_cd_entry(b"data.csv\x00\x00"), _cd_entry(b"b.R", offset=50)],
        "emptyname.cd": [_cd_entry(b""), _cd_entry(b"b.R", offset=50)],
        "emptyname_last.cd": [_cd_entry(b"b.R", offset=50), _cd_entry(b"")],
        "bigoffset.cd": [_cd_entry(b"", offset=0x01000000), _cd_entry(b"z.R")],
        "midnul.cd": [_cd_entry(b"a\x00b.csv")],
        "allnul.cd": [_cd_entry(b"\x00\x00"), _cd_entry(b"ok.csv")],
    }
    for name, entries in cds.items():
        (OUT / name).write_bytes(_cd(entries))


if __name__ == "__main__":
    main()
