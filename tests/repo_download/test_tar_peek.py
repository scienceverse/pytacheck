"""Listing plain .tar archives (metacheck #487; port of tests/testthat/test-tar-peek.R).

The archives are built here: with :mod:`tarfile` in the POSIX ``ustar`` form, and
by hand for the pax and GNU long-name forms and for a damaged header.
"""

from __future__ import annotations

import math
import secrets
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.archives import download as dlm
from metacheck.archives import tar_peek as tp
from metacheck.archives.download import download_repo_files
from metacheck.archives.tar_peek import _tar_fetch_members, _tar_list_file, _tar_peek

# -- building tar files ---------------------------------------------------------


def tar_header(
    name: str, size: int, type_: str = "0", ustar: bool = True, prefix: str = ""
) -> bytes:
    """One 512-byte tar header."""
    h = bytearray(512)

    def put(at: int, s: str | bytes) -> None:
        b = s.encode() if isinstance(s, str) else s
        h[at - 1 : at - 1 + len(b)] = b

    put(1, name)
    put(101, "0000644")
    put(109, "0000000")
    put(117, "0000000")
    put(125, f"{size:011o}")
    put(137, f"{0:011o}")
    put(157, type_)
    if ustar:
        put(258, "ustar")
        put(264, "00")
    else:
        put(258, "ustar  ")
    if prefix:
        put(346, prefix)
    h[148:156] = b"        "
    put(149, f"{sum(h):06o}")
    h[154] = 0
    return bytes(h)


def tar_pad(b: bytes) -> bytes:
    return b + bytes((512 - len(b) % 512) % 512)


def tar_entry(name: str, content: str | bytes, type_: str = "0", **kw: Any) -> bytes:
    data = content.encode() if isinstance(content, str) else content
    return tar_header(name, len(data), type_, **kw) + tar_pad(data)


def pax_record(key: str, value: str) -> str:
    """A pax record states its own length, digits included."""
    body = f" {key}={value}\n"
    n = len(body.encode())
    length = n + len(str(n))
    if len(str(length)) > len(str(n)):
        length = n + len(str(length))
    return f"{length}{body}"


def write_tar(path: Path, *entries: bytes) -> Path:
    path.write_bytes(b"".join(entries) + bytes(1024))
    return path


class Fx:
    def __init__(self, path: Path, long: str, src: Path) -> None:
        self.path, self.long, self.src = path, long, src


def make_tar(tmp_path: Path) -> Fx:
    """A tar with nested folders, an empty file and a path too long for the name field."""
    src = tmp_path / "src"
    long = f"study/{'a' * 60}/{'b' * 60}.csv"
    (src / "study" / "data").mkdir(parents=True)
    (src / long).parent.mkdir(parents=True)
    (src / "study" / "README.md").write_text("# Study\nRead me first.\n")
    (src / "study" / "data" / "scores.csv").write_text("id,score\n1,3\n2,5\n")
    (src / "study" / "empty.txt").write_text("")
    (src / long).write_text("x\n")
    path = tmp_path / "archive.tar"
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tf:
        tf.add(src / "study", arcname="study")
    return Fx(path, long, src)


@pytest.fixture(autouse=True)
def _clean_session() -> Any:
    tp._TAR_PEEK_CACHE.clear()
    tp._TAR_PEEK_COPIES.clear()
    yield
    tp._TAR_PEEK_CACHE.clear()
    tp._TAR_PEEK_COPIES.clear()


# -- _tar_list_file -------------------------------------------------------------


def test_tar_list_file_lists_a_tar_made_by_tarfile(tmp_path: Path) -> None:
    fx = make_tar(tmp_path)
    listing = _tar_list_file(str(fx.path))
    assert listing is not None
    files = sorted(
        str(p.relative_to(fx.src)).replace("\\", "/") for p in fx.src.rglob("*") if p.is_file()
    )
    assert sorted(listing["name"].tolist()) == files
    got = dict(zip(listing["name"].tolist(), listing["size"].tolist(), strict=True))
    for f in files:
        assert got[f] == (fx.src / f).stat().st_size
    assert fx.long in got
    assert got["study/empty.txt"] == 0


def test_tar_list_file_reads_pax_gnu_long_name_and_ustar_prefix_names(tmp_path: Path) -> None:
    long = f"deposit/{'x' * 120}/README.md"
    pax = pax_record("path", long)
    gnu_long = f"deposit/{'y' * 110}/data.csv"
    p = write_tar(
        tmp_path / "a.tar",
        tar_entry("pax_global_header", pax_record("comment", "made by git archive"), type_="g"),
        tar_entry("deposit/", b"", type_="5"),
        tar_entry("PaxHeaders/README.md", pax, type_="x"),
        tar_entry("README.md", "# Deposit\n"),
        tar_entry("././@LongLink", gnu_long.encode() + b"\0", type_="L", ustar=False),
        tar_entry(gnu_long[:100], "a,b\n1,2\n", ustar=False),
        tar_entry("scores.csv", "x\n1\n", prefix="deposit/sub"),
        tar_entry("./deposit/notes.txt", "notes"),
    )
    listing = _tar_list_file(str(p))
    assert listing is not None
    assert listing["name"].tolist() == [
        long,
        gnu_long,
        "deposit/sub/scores.csv",
        "deposit/notes.txt",
    ]
    assert listing["size"].tolist() == [10, 8, 4, 5]
    # the stored name is kept for extraction
    assert listing["member"].tolist()[3] == "./deposit/notes.txt"


def test_tar_list_file_skips_a_binary_pax_record_such_as_a_macs_extended_attributes(
    tmp_path: Path,
) -> None:
    # tar on a Mac stores a file's extended attributes as pax records whose value is
    # binary, with null bytes: metacheck's rawToChar() stopped on them ("embedded nul
    # in string") and with it data_check (PsyArXiv preprint 4z75t_v1)
    value = bytes([0x01, 0x00, 0x00, 0xB1, 0xA4, 0x7F, 0x3D])
    body = b" SCHILY.xattr.com.apple.provenance=" + value + b"\n"
    xattr = str(len(body) + 2).encode() + body  # two digits for the length itself
    p = write_tar(
        tmp_path / "a.tar",
        tar_entry(
            "PaxHeaders/data.csv",
            xattr + pax_record("path", "deposit/data.csv").encode(),
            type_="x",
        ),
        tar_entry("data.csv", "a,b\n1,2\n"),
    )
    listing = _tar_list_file(str(p))
    assert listing is not None
    assert listing["name"].tolist() == ["deposit/data.csv"]
    assert listing["size"].tolist() == [8]


def test_tar_list_file_takes_the_size_from_a_pax_record(tmp_path: Path) -> None:
    body = "x" * 700
    p = write_tar(
        tmp_path / "a.tar",
        tar_entry("PaxHeaders/big", pax_record("size", "700"), type_="x"),
        tar_header("big.txt", 0) + tar_pad(body.encode()),
        tar_entry("after.txt", "ok"),
    )
    listing = _tar_list_file(str(p))
    assert listing is not None
    assert listing["name"].tolist() == ["big.txt", "after.txt"]
    assert listing["size"].tolist() == [700, 2]


def test_tar_list_file_reads_a_base_256_size(tmp_path: Path) -> None:
    from metacheck.archives.tar_peek import _tar_num

    big = 9 * 1024**3  # over the 8 GB an octal field holds
    field = bytes([0x80]) + big.to_bytes(11, "big")
    assert _tar_num(field) == big
    assert _tar_num(b"0000644\0") == 0o644
    assert _tar_num(b"") == 0


def test_tar_list_file_returns_none_for_a_file_that_is_not_a_readable_tar(tmp_path: Path) -> None:
    p = tmp_path / "x.tar"
    p.write_text("not a tar archive\n" * 100)
    assert _tar_list_file(str(p)) is None
    assert _tar_list_file(str(tmp_path / "missing.tar")) is None

    # a damaged second header
    good = tar_entry("a.txt", "hello")
    bad = bytearray(tar_entry("b.txt", "world"))
    bad[9] = 0x7A
    write_tar(p, good, bytes(bad))
    assert _tar_list_file(str(p)) is None


# -- _tar_peek ------------------------------------------------------------------


def _fake_download(monkeypatch: pytest.MonkeyPatch, source: Path, calls: list[str]) -> None:
    def fake(url: str, dest: str, **kw: Any) -> None:
        calls.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)

    monkeypatch.setattr(dlm, "_download_one", fake)


def test_tar_peek_lists_a_remote_tar_with_one_download_and_keeps_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_tar(tmp_path)
    url = "https://example.org/files/archive-test-1.tar"
    calls: list[str] = []
    _fake_download(monkeypatch, fx.path, calls)
    size = fx.path.stat().st_size

    # size unknown, or over the limit: nothing is downloaded
    assert _tar_peek(url) is None
    assert _tar_peek(url, size=size, max_bytes=size - 1) is None
    assert calls == []

    listing = _tar_peek(url, size=size)
    assert listing is not None
    assert "study/README.md" in listing["name"].tolist()
    assert calls == [url]
    assert Path(tp._TAR_PEEK_COPIES[url]).exists()

    # a second listing this session needs no request
    assert _tar_peek(url, size=size) is listing
    assert len(calls) == 1

    # a file on disk is read without a request
    local = _tar_peek(str(fx.path))
    assert local is not None
    assert "study/README.md" in local["name"].tolist()
    assert len(calls) == 1


def test_tar_peek_does_not_list_a_download_shorter_than_the_listed_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_tar(tmp_path)
    url = "https://example.org/files/archive-test-2.tar"

    def fake(url: str, dest: str, **kw: Any) -> None:
        Path(dest).write_bytes(fx.path.read_bytes()[:1024])

    monkeypatch.setattr(dlm, "_download_one", fake)
    assert _tar_peek(url, size=fx.path.stat().st_size) is None
    assert url not in tp._TAR_PEEK_COPIES


def test_tar_peek_keeps_a_complete_listing_on_disk_for_later_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_tar(tmp_path)
    url = "https://example.org/files/archive-test-disk.tar"
    monkeypatch.setenv("PYTACHECK_CACHE_DIR", str(tmp_path / "cache"))
    calls: list[str] = []
    _fake_download(monkeypatch, fx.path, calls)
    size = fx.path.stat().st_size
    first = _tar_peek(url, size=size, cache=True)
    assert first is not None and calls == [url]

    tp._TAR_PEEK_CACHE.clear()  # a new session
    again = _tar_peek(url, cache=True)
    assert again is not None
    assert again["name"].tolist() == first["name"].tolist()
    assert calls == [url]


# -- _tar_fetch_members ---------------------------------------------------------


def test_tar_fetch_members_takes_members_from_a_local_tar_the_session_copy_or_one_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_tar(tmp_path)
    url = "https://example.org/files/archive-test-3.tar"
    calls: list[str] = []
    _fake_download(monkeypatch, fx.path, calls)
    want = ["study/README.md", "study/data/scores.csv"]
    size = fx.path.stat().st_size

    # from a tar on disk
    got = _tar_fetch_members(str(fx.path), want, str(tmp_path / "d1"))
    assert got["ok"].tolist() == [True, True]
    readme = got["path"][got["name"] == "study/README.md"].iloc[0]
    assert Path(readme).read_text().splitlines()[1] == "Read me first."

    # from the copy _tar_peek() kept: no request
    tp._TAR_PEEK_COPIES[url] = str(fx.path)
    got = _tar_fetch_members(url, want, str(tmp_path / "d2"))
    assert got["ok"].tolist() == [True, True]
    assert calls == []

    # in a new session: one download, kept at archive_path for a restarted run
    del tp._TAR_PEEK_COPIES[url]
    arc = str(tmp_path / "keep" / "archive.tar")
    got = _tar_fetch_members(url, want, str(tmp_path / "d3"), archive_path=arc, size=size)
    assert got["ok"].tolist() == [True, True]
    assert len(calls) == 1
    got = _tar_fetch_members(url, want, str(tmp_path / "d4"), archive_path=arc)
    assert got["ok"].tolist() == [True, True]
    assert len(calls) == 1

    # an archive over the download budget is not downloaded
    got = _tar_fetch_members(url, want, str(tmp_path / "d5"), size=size, max_whole_bytes=10)
    assert not got["ok"].any()
    assert set(got["error"]) == {"the archive is larger than the download budget"}
    assert len(calls) == 1


def test_tar_fetch_members_reports_what_went_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_tar(tmp_path)
    got = _tar_fetch_members(str(fx.path), ["nope.txt"], str(tmp_path / "d"))
    assert got["error"].tolist() == ["not found in the archive"]

    bad = tmp_path / "bad.tar"
    bad.write_text("not a tar\n" * 100)
    got = _tar_fetch_members(str(bad), ["a"], str(tmp_path / "d"))
    assert got["error"].tolist() == ["could not read the archive"]

    monkeypatch.setattr(dlm, "_download_one", lambda url, dest, **kw: "HTTP 404")
    got = _tar_fetch_members("https://example.org/x.tar", ["a"], str(tmp_path / "d"), size=10)
    assert got["error"].tolist() == ["HTTP 404"]
    assert math.isnan(got["size"].iloc[0])


def test_tar_fetch_members_refuses_a_member_whose_path_leaves_the_folder(tmp_path: Path) -> None:
    p = write_tar(tmp_path / "a.tar", tar_entry("../evil.txt", "x"), tar_entry("ok.txt", "fine"))
    dest = tmp_path / "out" / "inner"
    got = _tar_fetch_members(str(p), ["../evil.txt", "ok.txt"], str(dest))
    assert got["ok"].tolist() == [False, True]
    assert "path traversal" in got["error"].iloc[0]
    assert not (tmp_path / "out" / "evil.txt").exists()
    assert (dest / "ok.txt").read_text() == "fine"


def test_a_member_cut_short_by_the_end_of_the_archive_is_not_delivered(tmp_path: Path) -> None:
    # a header that promises more than the file holds
    p = tmp_path / "short.tar"
    p.write_bytes(tar_header("a.txt", 2000) + tar_pad(b"abc"))
    dest = tmp_path / "d"
    got = _tar_fetch_members(str(p), ["a.txt"], str(dest))
    assert got["ok"].tolist() == [False]
    assert got["error"].tolist() == ["could not extract the member from the archive"]
    assert not (dest / "a.txt").exists()


# -- download_repo_files --------------------------------------------------------


def test_download_repo_files_fetches_a_tar_member_through_tar_fetch_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = make_tar(tmp_path)
    repo = "https://example.org/repo-test-" + secrets.token_hex(6)
    url = f"{repo}/files/123/download"
    calls: list[str] = []
    _fake_download(monkeypatch, fx.path, calls)
    files = pd.DataFrame(
        {
            "repo_url": [repo],
            "file_name": ["scores.csv"],
            "file_path": ["archive.tar/study/data/scores.csv"],
            "file_url": [None],
            "file_size": [15.0],
            "file_location": [None],
            "archive_url": [url],
            "archive_member": ["study/data/scores.csv"],
            "archive_format": ["tar"],
            "archive_size": [float(fx.path.stat().st_size)],
            "paper_id": ["p.1"],
        }
    )
    dl = download_repo_files(files)
    loc = dl["file_location"].iloc[0]
    assert isinstance(loc, str)
    assert Path(loc).read_text().splitlines()[0] == "id,score"
    assert len(calls) == 1


# -- expanding a downloaded tar -------------------------------------------------


def test_expand_tar_stops_at_the_size_limit_from_the_headers(tmp_path: Path) -> None:
    from metacheck.archives.zip_peek import _expand_tar

    src = tmp_path / "src"
    src.mkdir()
    (src / "small.csv").write_text("a,b\n1,2\n")
    (src / "large.csv").write_text("x" * 5000)
    path = tmp_path / "data.tar"
    with tarfile.open(path, "w") as tf:
        tf.add(src / "small.csv", arcname="small.csv")
        tf.add(src / "large.csv", arcname="large.csv")
    row = pd.DataFrame(
        {
            "repo_url": ["r"],
            "file_name": ["data.tar"],
            "file_path": ["data.tar"],
            "file_url": ["u"],
            "file_location": [str(path)],
            "file_size": [float(path.stat().st_size)],
            "file_type": ["archive"],
            "repo_name": ["r"],
            "paper_id": ["p.1"],
        }
    )
    rows = _expand_tar(str(path), row, max_bytes=1000)
    by_name = dict(zip(rows["file_name"], rows["file_location"], strict=True))
    # the large member is listed (with its size) but was never unpacked
    assert Path(by_name["small.csv"]).read_text() == "a,b\n1,2\n"
    assert pd.isna(by_name["large.csv"])
    assert rows.attrs["oversize"]["file_size"].tolist() == [5000]
