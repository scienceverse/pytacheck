"""Tests for peeking inside remote zips (port of test-zip-peek.R).

The first part ports ``tests/testthat/test-zip-peek.R`` (the central-directory
parser, CRC32, member inflation, ``zip_decision()`` with a stubbed
``zip_peek()``, the session cache and ``.expand_zip()``). The rest exercises the
HTTP side that metacheck's tests leave to live runs: local zip fixtures served
through respx by a handler that honours ``Range`` requests.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from pytacheck.archives import zip_peek as zp
from pytacheck.archives.zip_peek import (
    _crc32,
    _expand_compressed,
    _expand_tar,
    _expand_zip,
    _is_readable_archive,
    _is_single_compress,
    _is_tar_archive,
    _is_zip,
    _le_int,
    _parse_zip_central_dir,
    _zip_crc_ok,
    _zip_fetch_members,
    _zip_inflate_member,
    _zip_member_fetch,
    zip_decision,
    zip_peek,
)

DATA = Path(__file__).resolve().parent / "data"
HAS_ZIP = shutil.which("zip") is not None


@pytest.fixture(autouse=True)
def _fresh_peek_cache() -> Iterator[None]:
    zp._ZIP_PEEK_CACHE.clear()
    yield
    zp._ZIP_PEEK_CACHE.clear()


def _cli_zip(d: Path, names: list[str]) -> Path:
    """``utils::zip("test.zip", names)``: the zip command line tool, as R uses it."""
    if not HAS_ZIP:
        pytest.skip("zip utility unavailable")
    subprocess.run(["zip", "-q", "test.zip", *names], cwd=d, check=True)
    return d / "test.zip"


def _local_member(raw: bytes, entry: pd.Series) -> bytes:
    off = int(entry["offset"])
    lh = raw[off : off + 30]
    start = off + 30 + int(_le_int(lh, 27, 2) + _le_int(lh, 29, 2))
    return raw[start : start + int(entry["csize"])]


# -- test-zip-peek.R ----------------------------------------------------------


def test_parse_zip_central_dir_reads_names_and_sizes(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("x\n" * 100)
    (tmp_path / "stim.png").write_text("img\n")
    z = _cli_zip(tmp_path, ["data.csv", "stim.png"])
    cd = _parse_zip_central_dir(z.read_bytes())
    assert cd is not None
    assert {"data.csv", "stim.png"} <= set(cd["name"])
    assert cd.loc[cd["name"] == "data.csv", "size"].iloc[0] > 0


def test_parse_zip_central_dir_reports_fetch_fields(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("id,x\n" * 200)
    (tmp_path / "notes.txt").write_text("hello\n")
    z = _cli_zip(tmp_path, ["data.csv", "notes.txt"])
    cd = _parse_zip_central_dir(z.read_bytes())
    assert cd is not None
    assert list(cd.columns)[:2] == ["name", "size"]
    assert {"method", "csize", "offset", "crc"} <= set(cd.columns)
    assert cd["offset"].min() == 0
    assert set(cd["method"]) <= {0.0, 8.0}
    for name, crc in zip(cd["name"], cd["crc"], strict=True):
        assert _crc32((tmp_path / name).read_bytes()) == crc


def test_crc32_check_value() -> None:
    assert _crc32(b"123456789") == 3421780262
    assert _crc32(b"") == 0


def test_zip_crc_ok_match_mismatch_and_no_check() -> None:
    b = b"123456789"
    assert _zip_crc_ok(b, 3421780262) is True
    assert _zip_crc_ok(b, 12345) is False
    assert _zip_crc_ok(b, None) is None
    assert _zip_crc_ok(b, float("nan")) is None


def test_zip_crc_ok_above_integer_max() -> None:
    big = b"123456789"
    assert _crc32(big) > 2147483647
    assert _zip_crc_ok(big, _crc32(big)) is True


def test_zip_crc_ok_uses_all_equal_tolerance() -> None:
    # R compares with isTRUE(all.equal(got, crc)): relative tolerance 1.5e-8
    assert _zip_crc_ok(b"123456789", 3421780262 + 40) is True
    assert _zip_crc_ok(b"123456789", 3421780262 + 10000) is False


def test_zip_inflate_member_stored_and_unsupported() -> None:
    assert _zip_inflate_member(bytes([1, 2, 3, 4, 5]), 0) == bytes([1, 2, 3, 4, 5])
    assert _zip_inflate_member(bytes([1, 2, 3, 4, 5]), 12) is None


def test_zip_inflate_member_larger_than_32768(tmp_path: Path) -> None:
    content = [f"line {i} {'x' * 20}" for i in range(1, 3001)]
    (tmp_path / "big.R").write_text("\n".join(content) + "\n")
    z = _cli_zip(tmp_path, ["big.R"])
    raw = z.read_bytes()
    cd = _parse_zip_central_dir(raw)
    assert cd is not None
    entry = cd.loc[cd["name"] == "big.R"].iloc[0]
    assert entry["size"] > 32768
    comp = _local_member(raw, entry)

    truncated = _zip_inflate_member(comp, entry["method"])
    assert truncated is not None and len(truncated) == 32768  # documents the zip-package bug

    fixed = _zip_inflate_member(comp, entry["method"], size=entry["size"])
    assert fixed is not None and len(fixed) == entry["size"]
    assert re.split(r"\r?\n", fixed.decode())[:-1] == content


def test_zip_member_fetch_refuses_zip64_entry() -> None:
    entry = pd.DataFrame(
        {
            "name": ["big.dat"],
            "size": [None],
            "method": [8],
            "csize": [None],
            "offset": [None],
            "crc": [1],
        }
    )
    assert _zip_member_fetch("http://example.invalid/x.zip", entry) is None


def test_zip_decision_keeps_data_and_links_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_peek(url: str, *args: object, **kwargs: object) -> pd.DataFrame:
        if "data" in url:
            return pd.DataFrame({"name": ["study.csv", "notes.png"], "size": [100.0, 200.0]})
        return pd.DataFrame({"name": ["a.png", "b.jpg", "readme.txt"], "size": [1.0, 2.0, 3.0]})

    monkeypatch.setattr(zp, "zip_peek", fake_peek)
    d1 = zip_decision("http://x/data.zip", skip_types="materials")
    assert d1["worth"] is True
    d2 = zip_decision("http://x/stimuli.zip", skip_types="materials")
    assert d2["worth"] is False
    assert "link" in d2["reason"]
    assert d2["types"] == {"documentation": 1, "materials": 2}
    assert d2["n_entries"] == 3


def test_zip_decision_na_when_peek_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zp, "zip_peek", lambda url, *a, **k: None)
    d = zip_decision("http://x/opaque.zip")
    assert d["worth"] is None
    assert "could not peek" in d["reason"]
    assert d["contents"] is None and d["types"] is None


def test_zip_peek_reuses_session_result() -> None:
    url = "http://example.invalid/same.zip"
    assert url not in zp._ZIP_PEEK_CACHE
    cd = pd.DataFrame(
        {
            "name": ["study.csv"],
            "size": [100.0],
            "method": [0.0],
            "csize": [100.0],
            "offset": [0.0],
            "crc": [12345.0],
        }
    )
    zp._ZIP_PEEK_CACHE[url] = cd
    assert zip_peek(url) is cd  # no request made (respx is not active: none possible)
    assert "http://example.invalid/other.zip" not in zp._ZIP_PEEK_CACHE
    zp._ZIP_PEEK_CACHE["http://example.invalid/failed.zip"] = None
    assert zip_peek("http://example.invalid/failed.zip") is None  # a cached failure too


def _zip_row(z: Path) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": ["r"],
            "file_name": ["mixed.zip"],
            "file_path": ["mixed.zip"],
            "file_url": ["u"],
            "file_location": [str(z)],
            "file_size": [float(z.stat().st_size)],
            "file_type": ["archive"],
            "repo_name": ["r"],
            "paper_id": ["p.1"],
            "data_type": ["unknown"],
            "doc_role": pd.Series([None], dtype="string"),
            "data_format": ["tabular"],
            "group": pd.Series([None], dtype="string"),
        }
    )


def test_expand_zip_keeps_data_drops_materials(tmp_path: Path) -> None:
    (tmp_path / "study.csv").write_text("id,x\n1,2\n")
    (tmp_path / "stim.png").write_text("img\n")
    (tmp_path / "README.txt").write_text("notes\n")
    if not HAS_ZIP:
        pytest.skip("zip utility unavailable")
    subprocess.run(
        ["zip", "-q", "mixed.zip", "study.csv", "stim.png", "README.txt"], cwd=tmp_path, check=True
    )
    z = tmp_path / "mixed.zip"
    rows = _expand_zip(z, _zip_row(z), skip_types="materials")
    assert set(rows["file_name"]) == {"study.csv", "README.txt"}
    assert "stim.png" not in rows["file_name"].tolist()
    by_name = rows.set_index("file_name")
    assert by_name.loc["study.csv", "data_type"] == "data"
    assert by_name.loc["README.txt", "data_type"] == "documentation"
    assert by_name.loc["README.txt", "doc_role"] == "readme"
    assert all(Path(p).exists() for p in rows["file_location"])
    assert (rows["paper_id"] == "p.1").all()
    assert rows["file_url"].isna().all()
    assert rows["file_path"].tolist() == [f"mixed.zip/{n}" for n in rows["file_name"]]


# -- archive expansion beyond the upstream tests ------------------------------


def test_expand_zip_reuses_extraction_and_filters_macosx(tmp_path: Path) -> None:
    z = tmp_path / "mixed.zip"
    shutil.copyfile(DATA / "mixed.zip", z)
    rows = _expand_zip(z, _zip_row(z))
    assert sorted(rows["file_name"]) == ["README.txt", "analysis.R", "codebook.csv", "study.csv"]
    assert not any("__MACOSX" in p for p in rows["file_path"])
    assert (tmp_path / "mixed.zip.contents" / "study.csv").exists()
    # a second call reuses the extraction directory
    (tmp_path / "mixed.zip.contents" / "extra.csv").write_text("a\n1\n")
    again = _expand_zip(z, _zip_row(z))
    assert "extra.csv" in again["file_name"].tolist()


def test_expand_zip_unreadable_or_missing(tmp_path: Path) -> None:
    row = _zip_row(DATA / "mixed.zip")
    assert len(_expand_zip(tmp_path / "missing.zip", row)) == 0
    assert len(_expand_zip(None, row)) == 0
    bad = tmp_path / "bad.zip"
    shutil.copyfile(DATA / "notzip.bin", bad)
    out = _expand_zip(bad, row)
    assert len(out) == 0 and list(out.columns) == list(row.columns)


def test_expand_tar_and_compressed(tmp_path: Path) -> None:
    t = tmp_path / "archive.tar.gz"
    shutil.copyfile(DATA / "archive.tar.gz", t)
    rows = _expand_tar(t, _zip_row(t))
    assert sorted(rows["file_name"]) == ["code.R", "data.csv"]
    assert rows["file_path"].tolist()[0].startswith("archive.tar.gz/proj/")

    for name in ("results.csv.gz", "results.csv.bz2", "results.csv.xz", "plain.csv.gz"):
        f = tmp_path / name
        shutil.copyfile(DATA / name, f)
        out = _expand_compressed(f, _zip_row(f))
        assert out["file_name"].tolist() == ["results.csv" if "results" in name else "plain.csv"]
        extracted = Path(out["file_location"].iloc[0])
        assert extracted.read_bytes().startswith(b"id,x\n")
        # R reports the size before its output connection is flushed (0 here)
        assert out["file_size"].iloc[0] == 0
        # a second call finds the file and reports its real size
        assert _expand_compressed(f, _zip_row(f))["file_size"].iloc[0] == 1440
    f = tmp_path / "x.zip"
    shutil.copyfile(DATA / "mixed.zip", f)
    assert len(_expand_compressed(f, _zip_row(f))) == 0  # not .gz/.bz2/.xz


def test_archive_classification() -> None:
    names = ["a.zip", "B.ZIP", "t.tar.gz", "t.TGZ", "r.csv.gz", "r.bz2", "x.7z", None]
    assert _is_zip(names) == [True, True, False, False, False, False, False, False]
    assert _is_tar_archive(names) == [False, False, True, True, False, False, False, False]
    assert _is_single_compress(names) == [False, False, False, False, True, True, False, False]
    assert _is_readable_archive(names) == [True, True, True, True, True, True, False, False]
    assert _is_zip("one.zip") == [True]


def test_parse_zip_central_dir_edge_cases() -> None:
    assert _parse_zip_central_dir(b"") is None
    assert _parse_zip_central_dir((DATA / "empty.zip").read_bytes()) is None
    assert _parse_zip_central_dir((DATA / "notzip.bin").read_bytes()) is None
    big = (DATA / "big.zip").read_bytes()
    assert _parse_zip_central_dir(big[-22:]) is None  # central directory not in the tail
    cd = _parse_zip_central_dir((DATA / "zip64.zip").read_bytes())
    assert cd is not None
    assert cd["size"].isna().tolist() == [True, False, True]
    assert cd["offset"].isna().tolist() == [True, False, True]
    cd = _parse_zip_central_dir((DATA / "comment.zip").read_bytes())
    assert cd is not None and cd["name"].tolist()[0] == "folder/"
    cd = _parse_zip_central_dir((DATA / "unicode.zip").read_bytes())
    assert cd is not None and cd["name"].tolist()[0] == "données/résumé.csv"


def test_le_int() -> None:
    raw = bytes([0x50, 0x4B, 5, 6, 255, 255, 255, 255])
    assert _le_int(raw, 1, 4) == 0x06054B50
    assert _le_int(raw, 5, 4) == 4294967295
    assert _le_int(raw, 7, 4) == 0xFFFF  # bytes past the end read as 00


# -- HTTP: range requests served by respx ------------------------------------


def _range_route(data: bytes, *, honour_range: bool = True, head_length: bool = True):
    calls: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("Range")
        calls.append((request.method, rng))
        if request.method == "HEAD":
            headers = {"Content-Length": str(len(data))} if head_length else {}
            return httpx.Response(200, headers=headers)
        if rng and honour_range:
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
            assert m is not None
            a, b = int(m.group(1)), int(m.group(2))
            return httpx.Response(206, content=data[a : b + 1])
        return httpx.Response(200, content=data)

    return handler, calls


URL = "https://files.example.org/archive.zip"


def test_zip_peek_over_range_requests() -> None:
    data = (DATA / "mixed.zip").read_bytes()
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL)
        assert cd is not None
        assert cd["name"].tolist()[0] == "README.txt"
        assert len(cd) == 6
        # cached: a second peek makes no request
        n = len(calls)
        assert zip_peek(URL) is cd
        assert len(calls) == n
    assert calls[0] == ("HEAD", None)
    assert calls[1] == ("GET", f"bytes=0-{len(data) - 1}")


def test_zip_peek_retries_with_a_bigger_tail() -> None:
    data = (DATA / "mixed.zip").read_bytes()
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL, tail_bytes=50)
    assert cd is not None and len(cd) == 6
    assert [c[1] for c in calls[1:]] == [
        f"bytes={len(data) - 50}-{len(data) - 1}",
        f"bytes=0-{len(data) - 1}",
    ]


def test_zip_peek_whole_body_answer_and_failures() -> None:
    data = (DATA / "comment.zip").read_bytes()
    handler, _ = _range_route(data, honour_range=False)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL)
    assert cd is not None and cd["name"].tolist() == ["folder/results.csv", "folder/plot.png"]

    handler, _ = _range_route(data, head_length=False)
    with respx.mock(assert_all_called=False) as router:
        router.route(url="https://files.example.org/nolength.zip").mock(side_effect=handler)
        assert zip_peek("https://files.example.org/nolength.zip") is None
    assert zp._ZIP_PEEK_CACHE["https://files.example.org/nolength.zip"] is None

    with respx.mock(assert_all_called=False) as router:
        router.route(url="https://files.example.org/gone.zip").mock(
            return_value=httpx.Response(404)
        )
        assert zip_peek("https://files.example.org/gone.zip") is None

    handler, _ = _range_route(b"not a zip at all" * 10)
    with respx.mock(assert_all_called=False) as router:
        router.route(url="https://files.example.org/text.zip").mock(side_effect=handler)
        assert zip_peek("https://files.example.org/text.zip") is None


def test_zip_fetch_members_by_range(tmp_path: Path) -> None:
    data = (DATA / "mixed.zip").read_bytes()
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(
            URL, names=["study.csv", "README.txt", "sub/codebook.csv"], dest=str(tmp_path)
        )
    assert out is not None
    assert out["name"].tolist() == ["README.txt", "study.csv", "sub/codebook.csv"]
    assert out["ok"].tolist() == [True, True, True]
    with zipfile.ZipFile(DATA / "mixed.zip") as zf:
        for name, path in zip(out["name"], out["path"], strict=True):
            assert Path(path).read_bytes() == zf.read(name)
    assert Path(out["path"].iloc[2]) == tmp_path / "sub" / "codebook.csv"
    # two range requests per member: the local header, then the data
    assert sum(1 for m, r in calls if m == "GET" and r and r.endswith(f"-{0 + 29}")) >= 1


def test_zip_fetch_members_rejects_bad_crc_and_traversal(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.csv", "a,b\n1,2\n")
        zf.writestr("/abs/ok.csv", "a,b\n3,4\n")
        zf.writestr("good.csv", "a,b\n5,6\n")
    data = bytearray(buf.getvalue())
    # corrupt the stored CRC of good.csv in the central directory
    cd = _parse_zip_central_dir(bytes(data))
    assert cd is not None
    eocd = data.rfind(b"PK\x05\x06")
    cd_off = int.from_bytes(data[eocd + 16 : eocd + 20], "little")
    p = cd_off
    for name in cd["name"]:
        nlen = int.from_bytes(data[p + 28 : p + 30], "little")
        xlen = int.from_bytes(data[p + 30 : p + 32], "little")
        clen = int.from_bytes(data[p + 32 : p + 34], "little")
        if name == "good.csv":
            data[p + 16 : p + 20] = (zlib.crc32(b"other") & 0xFFFFFFFF).to_bytes(4, "little")
        p += 46 + nlen + xlen + clen
    handler, _ = _range_route(bytes(data))
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, dest=str(tmp_path / "dest"))
        assert out is not None
        assert out["ok"].tolist() == [False, True, False]  # traversal, absolute ok, bad CRC
        assert Path(out["path"].iloc[1]) == tmp_path / "dest" / "abs" / "ok.csv"
        assert not (tmp_path / "evil.csv").exists()
        # without verification the corrupt-CRC member is accepted
        out2 = _zip_fetch_members(URL, names="good.csv", dest=str(tmp_path / "d2"), verify=False)
    assert out2 is not None and out2["ok"].tolist() == [True]


def test_zip_fetch_members_nothing_wanted_and_unlistable(tmp_path: Path) -> None:
    data = (DATA / "stored.zip").read_bytes()
    handler, _ = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, names=["nothing.csv"], dest=str(tmp_path))
        assert out is not None and len(out) == 0 and list(out.columns) == ["name", "size"]
        router.route(url="https://files.example.org/refused.zip").mock(
            return_value=httpx.Response(403)
        )
        assert (
            _zip_fetch_members("https://files.example.org/refused.zip", dest=str(tmp_path)) is None
        )


def test_zip_fetch_members_host_ignoring_ranges_fails_cleanly(tmp_path: Path) -> None:
    data = (DATA / "stored.zip").read_bytes()
    handler, _ = _range_route(data, honour_range=False)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, dest=str(tmp_path))
    assert out is not None
    # the listing works from the whole body, but a 200 is never a member's bytes
    assert out["ok"].tolist() == [False, False, True]  # only the empty member
    assert out["path"].isna().tolist() == [True, True, False]
