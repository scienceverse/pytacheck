"""``R/zip-peek.R::.unzip_all()`` and friends (metacheck #680, test-zip-peek.R)."""

from __future__ import annotations

import struct
import warnings
import zlib
from pathlib import Path
from typing import Any

import pandas as pd

from metacheck.archives.zip_peek import (
    _expand_zip,
    _unzip_all,
    _unzip_failed_note,
    _unzip_failed_text,
    _zip_list_local,
    _zip_unpack_reason,
    _zip_unsafe_path,
)


def _zip_bytes(members: list[dict[str, Any]]) -> bytes:
    """A zip built byte by byte, as zip tools write names in UTF-8 now: one dict per member with
    its stored ``name`` and ``data`` (bytes), optionally the general-purpose ``flag`` (bit 0:
    encrypted, bit 11: the name is UTF-8) and compression ``method`` (0, stored, by default; the
    data are written as given whatever the method says)."""
    local = b""
    central = b""
    for m in members:
        data: bytes = m["data"]
        name: bytes = m["name"]
        fixed = struct.pack(
            "<HHHHHIIIHH",
            20,
            m.get("flag", 0),
            m.get("method", 0),
            0,
            0,
            zlib.crc32(data),
            len(data),
            len(data),
            len(name),
            0,
        )
        central += (
            b"PK\x01\x02"
            + struct.pack("<H", 20)
            + fixed
            + struct.pack("<HHHII", 0, 0, 0, 0, len(local))
            + name
        )
        local += b"PK\x03\x04" + fixed + name + data
    n = len(members)
    end = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, n, n, len(central), len(local), 0)
    return local + central + end


def test_unzip_all_unpacks_every_member_it_can_and_says_why_not_the_others(
    tmp_path: Path,
) -> None:
    # The OSF stores a name with an accented letter in CP437, without the UTF-8 flag
    # (osf.io/9gju4). Here: a plain name, a CP437 name, a UTF-8 name with the flag, a nested
    # folder, a ".." member, a member compressed with Deflate64 (method 9), an encrypted member,
    # and a plain member after those two.
    o = chr(246)  # o with two dots; CP437 0x94
    z = tmp_path / "mixed.zip"
    z.write_bytes(
        _zip_bytes(
            [
                {"name": b"a.csv", "data": b"a\n1\n"},
                {"name": b"R\x94seler.txt", "data": b"cp\n"},
                {"name": f"R{o}seler_utf8.txt".encode(), "data": b"u8\n", "flag": 2048},
                {"name": b"sub/dir/b.csv", "data": b"b\n2\n"},
                {"name": b"../evil.txt", "data": b"evil\n"},
                {"name": b"big.bin", "data": b"zzzz", "method": 9},
                {"name": b"secret.txt", "data": b"ssss", "flag": 1},
                {"name": b"z_last.csv", "data": b"z\n"},
            ]
        )
    )
    ex = tmp_path / "out"
    res = _unzip_all(str(z), str(ex))
    assert res is not None
    written = ["a.csv", f"R{o}seler.txt", f"R{o}seler_utf8.txt", "sub/dir/b.csv", "z_last.csv"]
    assert sorted(res["name"][res["path"].notna()]) == sorted(written)
    assert all((ex / w).is_file() for w in written)
    assert (ex / f"R{o}seler.txt").read_text() == "cp\n"
    assert (ex / "sub" / "dir" / "b.csv").read_text() == "b\n2\n"
    assert res["reason"][res["path"].notna()].isna().all()

    # the others are not written, each with its reason
    reason = dict(zip(res["name"], res["reason"], strict=True))
    assert "outside the folder" in reason["../evil.txt"]
    assert "Deflate64" in reason["big.bin"]
    assert "encrypted" in reason["secret.txt"]
    assert not (tmp_path / "evil.txt").exists() and not (ex / "evil.txt").exists()
    assert not (ex / "big.bin").exists() and not (ex / "secret.txt").exists()

    note = _unzip_failed_note(res, "mixed.zip")
    assert note is not None
    assert note.startswith("3 files in mixed.zip could not be unpacked: ")
    assert "big.bin (it is compressed with Deflate64" in note
    assert _unzip_failed_note(res[res["path"].notna()], "mixed.zip") is None

    # only the members asked for; a name not in the zip is said to be missing
    ex2 = tmp_path / "out2"
    res2 = _unzip_all(str(z), str(ex2), files=[f"R{o}seler.txt", "nope.csv"])
    assert res2 is not None and len(res2) == 2
    assert Path(res2["path"][0]).read_text() == "cp\n"
    assert "not in the archive" in res2["reason"][1]
    assert sorted(p.name for p in ex2.rglob("*") if p.is_file()) == [f"R{o}seler.txt"]

    # a zip of plain names only is unpacked in one go, folders and all
    z2 = tmp_path / "plain.zip"
    z2.write_bytes(
        _zip_bytes(
            [{"name": b"x/y.csv", "data": b"1\n"}, {"name": b"w.txt", "data": b""}],
        )
    )
    ex3 = tmp_path / "out3"
    res3 = _unzip_all(str(z2), str(ex3))
    assert res3 is not None and res3["path"].notna().all()
    assert (ex3 / "w.txt").stat().st_size == 0

    # not a zip
    nz = tmp_path / "not.zip"
    nz.write_text("<html>error</html>\n")
    assert _unzip_all(str(nz), str(tmp_path / "out4")) is None


def test_zip_unsafe_path_and_unpack_reasons() -> None:
    assert _zip_unsafe_path("/abs.csv") and _zip_unsafe_path("C:/drive.csv")
    assert _zip_unsafe_path("a/../b.csv") and _zip_unsafe_path("../b.csv")
    assert not _zip_unsafe_path("a/b..c/d.csv") and not _zip_unsafe_path("sub/win.csv")
    assert _zip_unpack_reason(0, 1) == "it is encrypted"
    assert _zip_unpack_reason(99, 0) == "it is encrypted (AES)"
    assert "Deflate64" in _zip_unpack_reason(9, 0)
    assert "Zstandard" in _zip_unpack_reason(93, 0)
    assert "method 77" in _zip_unpack_reason(77, 0)
    assert _zip_unpack_reason(8, 0) == "its contents did not unpack to the size the archive records"


def test_an_encrypted_zip_member_is_named_as_not_unpacked_and_data_checks_report_says_so(
    tmp_path: Path,
) -> None:
    z = tmp_path / "mixed.zip"
    z.write_bytes(
        _zip_bytes(
            [
                {"name": b"ok.csv", "data": b"a,b\n1,2\n"},
                {"name": b"secret.csv", "data": b"a,b\n3,4\n", "flag": 1},
            ]
        )
    )
    assert _zip_list_local(str(z))["encrypted"].tolist() == [False, True]

    out = _unzip_all(str(z), str(tmp_path / "out"))
    assert out is not None
    by = dict(zip(out["name"], zip(out["path"], out["reason"], strict=True), strict=True))
    assert by["ok.csv"][0] is not None
    assert by["secret.csv"][0] is None and by["secret.csv"][1] == "it is encrypted"

    row = pd.DataFrame(
        {
            "paper_id": ["p"],
            "repo_url": ["https://osf.io/abcde"],
            "file_name": ["mixed.zip"],
            "file_path": ["data/mixed.zip"],
            "file_location": [str(z)],
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rows = _expand_zip(str(z), row)
        again = _expand_zip(str(z), row)
    assert rows["file_name"].tolist() == ["ok.csv"]
    uf = rows.attrs["unpack_failed"]
    assert uf["archive"].tolist() == ["data/mixed.zip"] and uf["file_name"].tolist() == [
        "secret.csv"
    ]
    # named again on a later run, as the member is still not on disk
    assert again.attrs.get("unpack_failed") is not None

    txt = _unzip_failed_text(uf)
    assert txt["report"][0] == "#### Files that could not be unpacked"
    assert (
        "1 file in `data/mixed.zip` could not be unpacked: secret.csv (it is encrypted)"
        in " ".join(txt["report"])
    )
    assert txt["summary"] == (
        "1 file in the repository's archives could not be unpacked, so the checks did not see it."
    )
    assert _unzip_failed_text(None)["report"] is None
    assert _unzip_failed_text(uf.iloc[0:0])["summary"] is None


REVIEW = Path(__file__).resolve().parent / "data" / "review"


def _written(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()
    )


def test_unzip_all_names_the_members_whose_path_leaves_the_folder(tmp_path: Path) -> None:
    res = _unzip_all(str(REVIEW / "climb.zip"), str(tmp_path))
    assert res is not None
    bad = res[res["path"].isna()]
    assert set(bad["reason"]) == {"its path leads outside the folder"}
    assert {"../evil.csv", "/abs.csv", "C:/drive.csv", "z/../../up.csv"} <= set(bad["name"])
    got = _written(tmp_path)
    assert "evil.csv" not in got and "z/up.csv" not in got and "drive.csv" not in got
    assert "ok/data.csv" in got and "x/y.csv" in got
    assert not (tmp_path.parent / "evil.csv").exists()


def test_unzip_all_goes_on_past_a_member_it_cannot_write(tmp_path: Path) -> None:
    # utils::unzip() stopped at the first member it could not open; each member stands alone now
    res = _unzip_all(str(REVIEW / "dots.zip"), str(tmp_path / "d"))
    assert res is not None
    assert "after.csv" in _written(tmp_path / "d")


def test_unzip_all_reads_what_zipfile_reads(tmp_path: Path) -> None:
    # R cannot unpack a bzip2 or LZMA member and names it (D154); zipfile reads them, and the
    # CRC is not checked, as R's unzip() does not check it
    for name, got in (
        ("lzma.zip", ["first.csv", "last.csv", "lz.csv"]),
        ("crc.zip", ["badcrc.csv", "empty.csv", "good.csv", "stored.txt"]),
        ("bzcrc.zip", ["badcrc.csv", "bz.csv", "last.csv"]),
    ):
        res = _unzip_all(str(REVIEW / name), str(tmp_path / name))
        assert res is not None, name
        assert _written(tmp_path / name) == got
        if name == "crc.zip":
            # a member whose bytes are not the size the archive records is named, not kept
            bad = res[res["path"].isna()]
            assert bad["name"].tolist() == ["badsize.csv"]
            assert "did not unpack to the size" in bad["reason"].iloc[0]
