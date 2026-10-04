"""Tests for peeking inside remote zips (port of test-zip-peek.R).

The first part ports ``tests/testthat/test-zip-peek.R`` (``zip_decision()``
with a stubbed ``zip_peek()``, the session cache and ``.expand_zip()``); the zip
format itself is :mod:`zipfile`'s now, so what is tested here is what metacheck
asks of it: the listing, the members, the requests made. The rest exercises the
HTTP side that metacheck's tests leave to live runs: zips served through respx by
a handler that honours ``Range`` requests.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import time
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives import zip_peek as zp
from metacheck.archives.zip_peek import (
    _expand_compressed,
    _expand_tar,
    _expand_zip,
    _is_readable_archive,
    _is_single_compress,
    _is_tar_archive,
    _is_zip,
    _zip_fetch_members,
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


def _build_zip(
    members: list[tuple[str, bytes]],
    compression: int = zipfile.ZIP_DEFLATED,
    zip64_limit: int | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> bytes:
    """A zip of *members* made by zipfile.

    With *zip64_limit* (and *monkeypatch*) zipfile's Zip64 limit is lowered while
    writing, so small members get real Zip64 records.
    """
    if zip64_limit is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(zipfile, "ZIP64_LIMIT", zip64_limit)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, content in members:
            zf.writestr(name, content)
    return buf.getvalue()


# -- test-zip-peek.R ----------------------------------------------------------


def test_zip_peek_lists_names_sizes_and_fetch_fields() -> None:
    members = [("data.csv", b"id,x\n" * 200), ("notes.txt", b"hello\n")]
    handler, _ = _range_route(_build_zip(members))
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL)
    assert cd is not None
    assert list(cd.columns) == ["name", "size", "method", "csize", "offset", "crc"]
    assert cd["name"].tolist() == ["data.csv", "notes.txt"]
    assert cd["size"].tolist() == [1000.0, 6.0]
    assert cd["offset"].min() == 0
    assert set(cd["method"]) <= {0.0, 8.0}
    for (name, content), crc in zip(members, cd["crc"], strict=True):
        assert zlib.crc32(content) == crc, name


def test_zip_peek_reads_a_zip64_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    # real Zip64 records (sizes and offsets in the extra field): the sizes are known
    members = [("a.csv", b"x,y\n" * 100), ("b.csv", b"1,2\n" * 100)]
    data = _build_zip(members, zip64_limit=100, monkeypatch=monkeypatch)
    assert b"PK\x06\x06" in data  # the Zip64 end of central directory record
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL)
    assert cd is not None
    assert cd["size"].tolist() == [400.0, 400.0]
    assert not cd["offset"].isna().any() and not cd["csize"].isna().any()
    assert len(calls) == 2  # HEAD and the tail


def test_zip_peek_leaves_an_unresolved_zip64_field_unknown() -> None:
    cd = zip_peek_from_bytes((DATA / "zip64.zip").read_bytes())
    assert cd is not None
    assert cd["size"].isna().tolist() == [True, False, True]
    assert cd["offset"].isna().tolist() == [True, False, True]


def zip_peek_from_bytes(data: bytes) -> pd.DataFrame | None:
    handler, _ = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        return zip_peek(URL)


def test_zip_peek_edge_cases() -> None:
    assert zip_peek_from_bytes((DATA / "empty.zip").read_bytes()) is None
    zp._ZIP_PEEK_CACHE.clear()
    assert zip_peek_from_bytes((DATA / "notzip.bin").read_bytes()) is None
    zp._ZIP_PEEK_CACHE.clear()
    cd = zip_peek_from_bytes((DATA / "comment.zip").read_bytes())
    assert cd is not None and cd["name"].tolist() == ["folder/results.csv", "folder/plot.png"]
    zp._ZIP_PEEK_CACHE.clear()
    cd = zip_peek_from_bytes((DATA / "unicode.zip").read_bytes())
    assert cd is not None and cd["name"].tolist()[0] == "données/résumé.csv"


def test_zip_peek_reads_a_directory_larger_than_the_tail() -> None:
    members = [(f"folder/with/a/long/name/file_{i:04d}.csv", b"a\n") for i in range(600)]
    data = _build_zip(members)
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL, tail_bytes=4096)
    assert cd is not None and len(cd) == 600
    # the HEAD, the tail, and one more request for the rest of the directory
    assert [m for m, _ in calls] == ["HEAD", "GET", "GET"]


def test_zip_fetch_members_inflates_a_member_over_32768_bytes(tmp_path: Path) -> None:
    # U71: metacheck's inflate() stopped at 32768 bytes
    content = [f"line {i} {'x' * 20}" for i in range(1, 3001)]
    text = "\n".join(content) + "\n"
    data = _build_zip([("big.R", text.encode()), ("other.R", b"x <- 1\n")])
    handler, _ = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, names="big.R", dest=str(tmp_path))
    assert out is not None and out["ok"].tolist() == [True]
    assert Path(out["path"].iloc[0]).read_text() == text
    assert out["size"].iloc[0] > 32768


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_zip_fetch_members_reads_every_method_zipfile_does(tmp_path: Path, method: int) -> None:
    # metacheck's reader knew stored and deflate only
    members = [("a.csv", b"id,x\n" * 300), ("b.csv", b"9,9\n" * 40)]
    handler, _ = _range_route(_build_zip(members, compression=method))
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, dest=str(tmp_path))
    assert out is not None and out["ok"].tolist() == [True, True]
    for (_, content), path in zip(members, out["path"], strict=True):
        assert Path(path).read_bytes() == content


def test_zip_fetch_members_of_a_zip64_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = [("a.csv", b"x,y\n" * 100), ("b.csv", b"1,2\n" * 100)]
    data = _build_zip(members, zip64_limit=100, monkeypatch=monkeypatch)
    handler, _ = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, dest=str(tmp_path))
    assert out is not None and out["ok"].tolist() == [True, True]
    assert Path(out["path"].iloc[1]).read_bytes() == members[1][1]


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


def test_expand_zip_members_take_their_own_type(tmp_path: Path) -> None:
    # U213: metacheck copies the archive's file_type ("archive") to every member
    z = tmp_path / "mixed.zip"
    shutil.copyfile(DATA / "mixed.zip", z)
    rows = _expand_zip(z, _zip_row(z)).set_index("file_name")
    assert rows.loc["study.csv", "file_type"] == "data"
    assert rows.loc["analysis.R", "file_type"] == "code"
    assert "archive" not in rows["file_type"].tolist()


def test_finding_where_to_extract_does_not_create_the_repository_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the repository cache is relative to the working folder by default; creating
    # it just to compare paths left .metacheck_repo_cache in the user's folder
    from metacheck.utils import local_options

    z = tmp_path / "user" / "mixed.zip"
    z.parent.mkdir()
    shutil.copyfile(DATA / "mixed.zip", z)
    cache = tmp_path / "not-yet"
    with local_options({"metacheck.repo_cache.dir": str(cache)}):
        zp._contents_dir(str(z))
    assert not cache.exists()


def test_local_archive_is_not_extracted_into_the_users_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U214: metacheck extracts to <archive>.contents/ beside it, in the user's folder,
    # and the next run lists the extracted files too
    import tempfile

    from metacheck.utils import local_options

    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(systmp))
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    user = tmp_path / "user"
    user.mkdir()
    z = user / "mixed.zip"
    shutil.copyfile(DATA / "mixed.zip", z)
    cache = tmp_path / "cache"
    with local_options({"metacheck.repo_cache.dir": str(cache)}):
        rows = _expand_zip(z, _zip_row(z))
        assert sorted(rows["file_name"]) == [
            "README.txt",
            "analysis.R",
            "codebook.csv",
            "study.csv",
        ]
        assert [p.name for p in user.iterdir()] == ["mixed.zip"]
        assert all(
            Path(p).is_relative_to(systmp / "metacheck-archives") for p in rows["file_location"]
        )
        # extracted once per session
        again = _expand_zip(z, _zip_row(z))
        assert again["file_location"].tolist() == rows["file_location"].tolist()
        # an archive metacheck downloaded is still extracted beside itself
        cached = cache / "repo" / "mixed.zip"
        cached.parent.mkdir(parents=True)
        shutil.copyfile(DATA / "mixed.zip", cached)
        _expand_zip(cached, _zip_row(cached))
        assert (cache / "repo" / "mixed.zip.contents" / "study.csv").exists()


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
        # the real size (U69: metacheck reads it before the last buffer is flushed: 0)
        assert out["file_size"].iloc[0] == 1440
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


def test_zip_peek_asks_for_the_rest_of_a_larger_directory() -> None:
    data = (DATA / "mixed.zip").read_bytes()
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        cd = zip_peek(URL, tail_bytes=50)
    assert cd is not None and len(cd) == 6
    ranges = [c[1] for c in calls[1:]]
    assert ranges[0] == f"bytes={len(data) - 50}-{len(data) - 1}"
    assert len(ranges) == 2  # the tail, then the directory zipfile found it needs


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
    # the listing is the HEAD and the tail (the fetch reuses them to open the archive);
    # every member costs at most one range request
    assert [m for m, _ in calls].count("HEAD") == 1
    assert len([r for m, r in calls if m == "GET"]) <= 1 + 3


def test_zip_fetch_members_after_a_listing_opens_the_archive_without_a_request(
    tmp_path: Path,
) -> None:
    members = [(f"f{i}.csv", bytes([i + 1]) * 3000) for i in range(100)]  # larger than the tail
    data = _build_zip(members, compression=zipfile.ZIP_STORED)
    handler, calls = _range_route(data)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        assert zip_peek(URL) is not None
        before = len(calls)
        out = _zip_fetch_members(URL, names=["f3.csv", "f4.csv"], dest=str(tmp_path))
    assert out is not None and out["ok"].tolist() == [True, True]
    assert [m for m, _ in calls[before:]] == ["GET", "GET"]  # one range request per member


def test_zip_fetch_members_rejects_bad_crc_and_traversal(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("../evil.csv", "a,b\n1,2\n")
        zf.writestr("/abs/ok.csv", "a,b\n3,4\n")
        zf.writestr("good.csv", "a,b\n5,6\n")
    data = bytearray(buf.getvalue())
    data[data.index(b"a,b\n5,6\n")] ^= 1  # a byte of good.csv changed: its CRC32 no longer matches
    handler, _ = _range_route(bytes(data))
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, dest=str(tmp_path / "dest"))
        assert out is not None
        assert out["ok"].tolist() == [False, True, False]  # traversal, absolute ok, bad CRC
        assert "CRC32" in out["error"].iloc[2]
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


def test_zip_fetch_members_host_ignoring_ranges(tmp_path: Path) -> None:
    data = (DATA / "stored.zip").read_bytes()
    handler, _ = _range_route(data, honour_range=False)
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, dest=str(tmp_path))
    assert out is not None
    # an archive smaller than the tail came whole with the listing: its members are in hand
    assert out["ok"].tolist() == [True, True, True]


def test_zip_fetch_members_host_ignoring_ranges_for_a_larger_archive(tmp_path: Path) -> None:
    members = [(f"f{i}.csv", bytes([i]) * 3000) for i in range(100)]  # more than the tail
    handler, _ = _range_route(
        _build_zip(members, compression=zipfile.ZIP_STORED), honour_range=False
    )
    with respx.mock(assert_all_called=False) as router:
        router.route(url=URL).mock(side_effect=handler)
        out = _zip_fetch_members(URL, names=["f0.csv", "f99.csv"], dest=str(tmp_path))
    assert out is not None
    # a 200 is never a member's bytes; each failed member says why (#429)
    assert out["ok"].tolist() == [False, True]  # the last member is in the tail that came
    assert out["error"].iloc[0] == "HTTP 200 (range not honoured)"
    assert pd.isna(out["error"].iloc[1])


# -- #429: why a member fetch failed --------------------------------------------------


def _zip64_entry() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["big.dat"],
            "size": [None],
            "method": [8],
            "csize": [None],
            "offset": [None],
            "crc": [1],
        }
    )


def test_zip_fetch_members_reports_a_per_member_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(zp, "zip_peek", lambda url, *a, **k: _zip64_entry())
    got = _zip_fetch_members("http://example.invalid/x.zip", dest=str(tmp_path))
    assert got is not None
    assert got["ok"].tolist() == [False]
    assert "Zip64" in got["error"].iloc[0]


# -- hosts that refuse HEAD (metacheck #424) ----------------------------------------
# Dryad, Figshare and Harvard Dataverse redirect downloads to Amazon S3, which
# answers HEAD with 403 but a ranged GET with 206.


def _test_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.csv", "id,x\n" * 50)
        zf.writestr("notes.txt", "hello\n")
    return buf.getvalue()


def _s3_like_host(data: bytes, head_status: int = 403, suffix_status: int = 206):
    """A host serving *data*: HEAD gets *head_status*; a Range is honoured with
    206 and Content-Range, except a suffix range gets *suffix_status* when that
    is not 206 (200: the whole file; 416: an over-long suffix refused)."""
    total = len(data)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("Range")
        calls.append(f"{request.method} {rng}")
        if request.method == "HEAD":
            return httpx.Response(head_status)
        if rng is None:
            return httpx.Response(200, content=data)
        if rng.startswith("bytes=-"):
            if suffix_status == 200:
                return httpx.Response(200, content=data)
            n = int(rng[len("bytes=-") :])
            if suffix_status == 416 and n > total:
                return httpx.Response(416, headers={"Content-Range": f"bytes */{total}"})
            a, b = max(0, total - n), total - 1
        else:
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
            assert m is not None
            a, b = int(m.group(1)), min(int(m.group(2)), total - 1)
        return httpx.Response(
            206, headers={"Content-Range": f"bytes {a}-{b}/{total}"}, content=data[a : b + 1]
        )

    return handler, calls


S3 = "https://s3-like.example/refuses-head.zip"


def test_content_range_total() -> None:
    def resp(cr: str | None) -> httpx.Response:
        return httpx.Response(206, headers={"Content-Range": cr} if cr else {})

    assert zp._content_range_total(resp("bytes 0-0/2545568")) == 2545568
    assert zp._content_range_total(resp("bytes */2246")) == 2246
    assert pd.isna(zp._content_range_total(resp("bytes 0-99/*")))
    assert pd.isna(zp._content_range_total(resp(None)))


def test_zip_peek_lists_a_zip_on_a_host_that_refuses_head() -> None:
    handler, calls = _s3_like_host(_test_zip_bytes())
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        cd = zip_peek(S3)
    assert cd is not None
    assert sorted(cd["name"]) == ["data.csv", "notes.txt"]
    # the 403 was not retried: exactly one HEAD
    assert sum(c.startswith("HEAD") for c in calls) == 1
    assert any(c.startswith("GET bytes=-") for c in calls)


def test_zip_peek_rejects_a_whole_file_answer_when_the_size_is_unknown() -> None:
    # without a size, a host ignoring the range would send the whole file
    handler, _ = _s3_like_host(_test_zip_bytes(), suffix_status=200)
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        assert zip_peek(S3) is None


def test_range_status_is_transient() -> None:
    for status in (400, 401, 403, 429, 500):
        assert zp._range_status_is_transient(httpx.Response(status))
    for status in (200, 206):
        assert not zp._range_status_is_transient(httpx.Response(status))


def test_http_range_bytes_retries_a_401(monkeypatch: pytest.MonkeyPatch) -> None:
    # metacheck checks the request's retry policy; here the retries are real
    from metacheck import http

    monkeypatch.setattr(http, "sleep", lambda s: None)
    answers = iter([httpx.Response(401), httpx.Response(206, content=b"abc")])
    with respx.mock(assert_all_mocked=True) as router:
        route = router.get("https://flaky.example/x.zip").mock(
            side_effect=lambda req: next(answers)
        )
        got = zp._http_range_bytes("https://flaky.example/x.zip", 0, 2)
    assert got == b"abc"
    assert route.call_count == 2


def test_zip_peek_handles_a_416_to_an_over_long_suffix() -> None:
    # GitHub answers 416 to a tail longer than the file, with "bytes */N"
    handler, _ = _s3_like_host(_test_zip_bytes(), suffix_status=416)
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        cd = zip_peek(S3)
    assert cd is not None
    assert sorted(cd["name"]) == ["data.csv", "notes.txt"]


def test_zip_fetch_members_on_a_host_that_refuses_head(tmp_path: Path) -> None:
    handler, _ = _s3_like_host(_test_zip_bytes())
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        out = _zip_fetch_members(S3, names="notes.txt", dest=str(tmp_path))
    assert out is not None and out["ok"].tolist() == [True]
    assert Path(out["path"].iloc[0]).read_bytes() == b"hello\n"


def test_remote_size_falls_back_to_a_ranged_request() -> None:
    from metacheck.archives.download import _remote_size

    handler, _ = _s3_like_host(bytes(range(200)))
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url="https://s3-like.example/file.csv").mock(side_effect=handler)
        assert _remote_size("https://s3-like.example/file.csv") == 200
    # a 403 error page's Content-Length is not the file's size
    with respx.mock(assert_all_mocked=True) as router:
        router.head("https://s3-like.example/private.csv").mock(
            return_value=httpx.Response(403, headers={"Content-Length": "243"})
        )
        router.get("https://s3-like.example/private.csv").mock(return_value=httpx.Response(403))
        assert pd.isna(_remote_size("https://s3-like.example/private.csv"))


# -- the on-disk zip-peek cache (metacheck #427) -------------------------------------


@pytest.fixture
def peek_cache_dir(tmp_path: Path) -> Iterator[Path]:
    from metacheck.utils import local_options

    d = tmp_path / "zpc"
    with local_options({"metacheck.zip_peek_cache.dir": str(d)}):
        yield d


def test_zip_peek_cache_persists_and_is_reused(peek_cache_dir: Path) -> None:
    from metacheck.archives.zip_peek_cache import _zip_peek_cache_has

    handler, calls = _s3_like_host(_test_zip_bytes())
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        cd = zip_peek(S3, cache=True)
        assert cd is not None and calls
        assert _zip_peek_cache_has(S3)
        # a fresh session: the disk cache answers, with no request
        zp._ZIP_PEEK_CACHE.clear()
        calls.clear()
        cd2 = zip_peek(S3, cache=True)
    assert calls == []
    pd.testing.assert_frame_equal(cd2, cd)


def test_zip_peek_without_cache_never_touches_the_disk(peek_cache_dir: Path) -> None:
    from metacheck.archives.zip_peek_cache import _zip_peek_cache_has

    handler, _ = _s3_like_host(_test_zip_bytes())
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        zip_peek(S3)
    assert not _zip_peek_cache_has(S3)


def test_zip_peek_cache_clear_counts_the_entries(peek_cache_dir: Path) -> None:
    from metacheck.archives.zip_peek_cache import (
        _zip_peek_cache_has,
        _zip_peek_cache_put,
        zip_peek_cache_clear,
    )

    _zip_peek_cache_put("http://x/a.zip", pd.DataFrame({"name": ["a"], "size": [1.0]}))
    _zip_peek_cache_put("http://x/b.zip", None)
    assert _zip_peek_cache_has("http://x/a.zip")
    assert zip_peek_cache_clear() == 2
    assert not _zip_peek_cache_has("http://x/a.zip")


def test_zip_peek_cache_unreadable_entry_is_a_miss(peek_cache_dir: Path) -> None:
    # U210: metacheck reads an unreadable entry as a cached failure (NULL)
    from metacheck.archives.zip_peek_cache import _zip_peek_cache_lookup, _zip_peek_cache_path

    peek_cache_dir.mkdir(parents=True, exist_ok=True)
    Path(_zip_peek_cache_path(S3)).write_bytes(b"{not json")
    assert _zip_peek_cache_lookup(S3) == (False, None)
    handler, _ = _s3_like_host(_test_zip_bytes())
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        cd = zip_peek(S3, cache=True)
    assert cd is not None  # listed again, and the entry rewritten
    hit, value = _zip_peek_cache_lookup(S3)
    assert hit and value is not None


def test_zip_peek_cache_keeps_a_lasting_failure_only(peek_cache_dir: Path) -> None:
    # U210: a refused range (the host ignores it) is kept; a 503 is not
    from metacheck.archives.zip_peek_cache import _zip_peek_cache_has

    handler, _ = _s3_like_host(_test_zip_bytes(), suffix_status=200)
    with respx.mock(assert_all_mocked=True) as router:
        router.route(url=S3).mock(side_effect=handler)
        assert zip_peek(S3, cache=True) is None
    assert _zip_peek_cache_has(S3)

    url = "https://s3-like.example/unavailable.zip"
    zp._ZIP_PEEK_CACHE.clear()
    from metacheck import http

    with (
        pytest.MonkeyPatch.context() as mp,
        respx.mock(assert_all_mocked=True) as router,
    ):
        mp.setattr(http, "sleep", lambda s: None)
        router.route(url=url).mock(return_value=httpx.Response(503))
        assert zip_peek(url, cache=True) is None
    assert not _zip_peek_cache_has(url)


def test_zip_peek_cache_does_not_keep_a_failure_after_a_failed_head(
    peek_cache_dir: Path,
) -> None:
    # HEAD answers 503, so the size is unknown and the host's whole-file answer to
    # the tail request cannot be used: with HEAD working the zip would be listed
    from metacheck import http
    from metacheck.archives.zip_peek_cache import _zip_peek_cache_has

    handler, _ = _s3_like_host(_test_zip_bytes(), head_status=503, suffix_status=200)
    with (
        pytest.MonkeyPatch.context() as mp,
        respx.mock(assert_all_mocked=True) as router,
    ):
        mp.setattr(http, "sleep", lambda s: None)
        router.route(url=S3).mock(side_effect=handler)
        assert zip_peek(S3, cache=True) is None
    assert not _zip_peek_cache_has(S3)


def test_zip_peek_skips_a_known_rate_limit() -> None:
    from metacheck import http

    host = "s3-rate-limited-only.example"
    http._record_reset(host, time.time() + 999)
    try:
        # no route: a wait or a request would hang or fail; a quick None is the skip
        with respx.mock(assert_all_mocked=True):
            assert zip_peek(f"https://{host}/rate-limited.zip", skip_on_api_limit=True) is None
    finally:
        with http._reset_lock:
            http._host_reset.pop(host, None)
