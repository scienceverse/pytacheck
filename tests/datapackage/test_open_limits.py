"""Opening an archive that someone else made: the limits that keep a zip bomb from being unpacked."""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from metacheck.datapackage import PackageError, open_package
from metacheck.datapackage import _open as opening

MB = 1024 * 1024


def _zip(path: Path, members: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _tar(path: Path, members: dict[str, bytes], mode: str = "w:gz") -> Path:
    with tarfile.open(path, mode) as tf:  # type: ignore[call-overload]
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def small_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bomb of megabytes instead of hundreds of them, so that the tests are quick."""
    monkeypatch.setattr(opening, "RATIO_FLOOR", 1 * MB)
    monkeypatch.setattr(opening, "PROBE_FLOOR", 1 * MB)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the archive is unpacked, so that a test can see that nothing is left behind."""
    folder = tmp_path / "unpack"
    folder.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(folder))
    return folder


# -- the ratio -----------------------------------------------------------------------------


def test_a_zip_that_expands_a_thousand_times_is_refused_before_it_is_unpacked(
    tmp_path: Path, small_floor: None, scratch: Path
) -> None:
    archive = _zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (20 * MB)})
    assert archive.stat().st_size < 100_000
    with pytest.raises(PackageError, match="zip bomb"), open_package(archive):
        pass
    assert list(scratch.iterdir()) == []  # the folder it was to be unpacked in is gone


def test_the_message_says_what_it_expanded_to_and_what_to_do(
    tmp_path: Path, small_floor: None
) -> None:
    archive = _zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (20 * MB)})
    with pytest.raises(PackageError) as caught, open_package(archive):
        pass
    message = str(caught.value)
    assert "20 MB" in message and "250 times" in message and "give its folder" in message


def test_a_small_archive_is_never_taken_for_a_bomb(tmp_path: Path) -> None:
    # 10 MB of zeros is 1000 times its size, but nothing to worry about: below the floor
    archive = _zip(tmp_path / "zeros.zip", {"README.md": "x", "zeros.bin": b"\0" * (10 * MB)})
    with open_package(archive) as pkg:
        assert len(pkg.files()) == 2


def test_a_real_table_compresses_far_less_than_the_limit(tmp_path: Path, small_floor: None) -> None:
    table = "".join(f"{i},{i * 7 % 13},{i % 5}\n" for i in range(200_000)).encode()
    archive = _zip(tmp_path / "table.zip", {"data.csv": table})
    assert len(table) > 1 * MB
    with open_package(archive) as pkg:
        assert pkg.files()["rel"].tolist() == ["data.csv"]


def test_the_ratio_can_be_changed(tmp_path: Path, small_floor: None) -> None:
    archive = _zip(tmp_path / "zeros.zip", {"zeros.bin": b"\0" * (20 * MB)})
    with open_package(archive, max_ratio=1_000_000) as pkg:
        assert pkg.files()["rel"].tolist() == ["zeros.bin"]


def test_a_tar_gz_bomb_is_stopped_while_it_is_read(
    tmp_path: Path, small_floor: None, scratch: Path
) -> None:
    archive = _tar(tmp_path / "bomb.tar.gz", {"zeros.bin": b"\0" * (20 * MB)})
    with pytest.raises(PackageError, match="zip bomb"), open_package(archive):
        pass
    assert list(scratch.iterdir()) == []


def test_a_plain_tar_is_not_a_bomb_however_big_the_member(
    tmp_path: Path, small_floor: None
) -> None:
    archive = _tar(tmp_path / "plain.tar", {"zeros.bin": b"\0" * (3 * MB)}, "w")
    with open_package(archive) as pkg:  # a tar is not compressed: ratio 1
        assert pkg.files()["rel"].tolist() == ["zeros.bin"]


# -- bytes and files, counted as they are written ----------------------------------------------


def test_what_is_written_is_counted_not_only_what_the_header_says(tmp_path: Path) -> None:
    archive = _tar(tmp_path / "pkg.tar.gz", {f"f{i}.txt": b"x" * 1000 for i in range(5)})
    with pytest.raises(PackageError, match="bigger than"), open_package(archive, max_bytes=2048):
        pass
    with pytest.raises(PackageError, match="more than 3 files"), open_package(archive, max_files=3):
        pass


def test_the_byte_and_file_limits_are_also_checked_for_a_zip_before_it_is_unpacked(
    tmp_path: Path, scratch: Path
) -> None:
    archive = _zip(tmp_path / "pkg.zip", {f"f{i}.txt": "x" * 1000 for i in range(5)})
    with (
        pytest.raises(PackageError, match="bigger than 2 KB"),
        open_package(archive, max_bytes=2048),
    ):
        pass
    with pytest.raises(PackageError, match="more than 3 files"), open_package(archive, max_files=3):
        pass
    assert list(scratch.iterdir()) == []


def test_a_zip_with_many_empty_folders_counts_them_as_files(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "dirs.zip", "w") as zf:
        for i in range(10):
            zf.writestr(f"d{i}/", "")
    with pytest.raises(PackageError, match="more than 5 files"):
        with open_package(tmp_path / "dirs.zip", max_files=5):
            pass


def test_an_archive_that_does_not_fit_on_the_disk_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _zip(tmp_path / "pkg.zip", {"a.txt": "x" * 1000})
    usage = type("Usage", (), {"total": 10, "used": 10, "free": 100})
    monkeypatch.setattr("shutil.disk_usage", lambda _path: usage)
    with pytest.raises(PackageError, match="free space"), open_package(archive):
        pass


# -- encrypted and strange archives ------------------------------------------------------------


def test_an_encrypted_zip_is_refused_with_a_reason(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "secret.zip", {"data.csv": "a,b\n1,2\n"})
    raw = bytearray(archive.read_bytes())
    raw[raw.index(b"PK\x01\x02") + 8] |= (
        0x1  # the "encrypted" flag, as a password-protected zip has
    )
    archive.write_bytes(raw)
    with pytest.raises(PackageError, match="password-protected"), open_package(archive):
        pass


def test_a_compression_method_that_is_not_supported_is_a_plain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _zip(tmp_path / "pkg.zip", {"a.txt": "x"})

    def unsupported(*_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("That compression method is not supported")

    monkeypatch.setattr(zipfile.ZipFile, "open", unsupported)
    with pytest.raises(PackageError, match="compression method"), open_package(archive):
        pass


def test_a_file_where_a_folder_is_needed_is_a_plain_error(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "clash.zip", {"data": "a file", "data/inner.csv": "a\n1\n"})
    with pytest.raises(PackageError, match="cannot be unpacked"), open_package(archive):
        pass


def test_paths_a_file_system_would_refuse_are_skipped_and_listed(tmp_path: Path) -> None:
    deep = "/".join(f"d{i}" for i in range(45)) + "/deep.txt"
    members = {
        "ok.txt": "x",
        deep: "too deep",
        "n" * 300 + ".txt": "name too long",
        "a/" + "p" * 300 + "/b.txt": "part too long",
        "line\nbreak.txt": "control character",
        "p/" * 600 + "q.txt": "path too long",
    }
    with open_package(_zip(tmp_path / "paths.zip", members)) as pkg:
        assert pkg.files()["rel"].tolist() == ["ok.txt"]
        assert len(pkg.skipped) == 5


def test_the_depth_can_be_changed(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "d.zip", {"a/b/c/d.txt": "x", "e.txt": "y"})
    with open_package(archive, max_depth=2) as pkg:
        assert pkg.files()["rel"].tolist() == ["e.txt"]
        assert pkg.skipped == ("a/b/c/d.txt",)


def test_a_name_used_twice_is_unpacked_once(tmp_path: Path) -> None:
    archive = tmp_path / "twice.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            zf.writestr("a.txt", "second")
    with open_package(archive) as pkg:
        assert (pkg.root / "a.txt").read_text() == "first"
        assert pkg.skipped == ("a.txt",)


def test_a_tar_with_links_devices_and_unsafe_names_unpacks_only_files(tmp_path: Path) -> None:
    archive = tmp_path / "odd.tar"
    with tarfile.open(archive, "w") as tf:
        data = b"x"
        for name in ("ok.txt", "../escape.txt", "/abs.txt"):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, kind in (
            ("sym", tarfile.SYMTYPE),
            ("hard", tarfile.LNKTYPE),
            ("dev", tarfile.CHRTYPE),
        ):
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = "ok.txt"
            tf.addfile(info)
    with open_package(archive) as pkg:
        assert pkg.files()["rel"].tolist() == ["ok.txt"]
        assert set(pkg.skipped) == {"../escape.txt", "/abs.txt", "sym", "hard", "dev"}
    assert not (tmp_path / "escape.txt").exists()


# -- what is inside the package: nested archives, office files, compressed streams -----------------


def _office(path: Path, zeros: int) -> bytes:
    """The bytes of an ``.xlsx``-like zip whose one part is *zeros* bytes of zeros."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("xl/worksheets/sheet1.xml", b"\0" * zeros)
    return buffer.getvalue()


def test_a_workbook_that_expands_a_thousand_times_is_refused_by_name(
    tmp_path: Path, small_floor: None
) -> None:
    archive = _zip(tmp_path / "pkg.zip", {"data/results.xlsx": _office(tmp_path, 20 * MB)})
    with pytest.raises(PackageError, match=r"data/results\.xlsx.*zip bomb"):
        with open_package(archive):
            pass


def test_a_zip_inside_a_zip_inside_a_tar_is_looked_at_too(
    tmp_path: Path, small_floor: None
) -> None:
    inner = _zip(tmp_path / "inner.zip", {"zeros.bin": b"\0" * (20 * MB)}).read_bytes()
    archive = _tar(tmp_path / "pkg.tar.gz", {"pkg/inner.zip": inner})
    with pytest.raises(PackageError, match=r"pkg/inner\.zip.*zip bomb"), open_package(archive):
        pass


def test_a_nested_archive_is_also_held_to_the_size_the_checks_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(opening, "MAX_NESTED", 1 * MB)
    table = "".join(f"{i},{i * 7 % 13}\n" for i in range(300_000)).encode()
    archive = _zip(tmp_path / "pkg.zip", {"data.xlsx": _office(tmp_path, 3 * MB)})
    with pytest.raises(PackageError, match=r"data\.xlsx.*expands to 3 MB.*1 MB"):
        with open_package(archive):
            pass
    # a table of the same size, not in a container, is a plain file: the extractor's own limits
    with open_package(_zip(tmp_path / "ok.zip", {"data.csv": table})) as pkg:
        assert pkg.files()["rel"].tolist() == ["data.csv"]


def test_an_ordinary_workbook_and_a_damaged_one_pass_through(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "pkg.zip",
        {"fine.xlsx": _office(tmp_path, 5000), "broken.xlsx": b"PK\x03\x04" + b"junk" * 50},
    )
    with open_package(archive) as pkg:
        assert sorted(pkg.files()["rel"]) == ["broken.xlsx", "fine.xlsx"]


def test_a_gzip_bomb_inside_the_package_is_found_by_what_it_starts_with(
    tmp_path: Path, small_floor: None
) -> None:
    bomb = gzip.compress(b"\0" * (20 * MB))
    archive = _zip(tmp_path / "pkg.zip", {"results.RData": bomb})  # no .gz in the name
    with pytest.raises(PackageError, match=r"results\.RData.*zip bomb"), open_package(archive):
        pass


def test_an_ordinary_gzip_file_inside_the_package_is_not_refused(
    tmp_path: Path, small_floor: None
) -> None:
    table = "".join(f"{i},{i * 7 % 13},{i % 5}\n" for i in range(200_000)).encode()
    archive = _zip(tmp_path / "pkg.zip", {"data.csv.gz": gzip.compress(table)})
    with open_package(archive) as pkg:
        assert pkg.files()["rel"].tolist() == ["data.csv.gz"]


def test_the_probe_decompresses_a_bounded_amount(
    tmp_path: Path, small_floor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(opening, "PROBE_BYTES", 2 * MB)
    seen: list[int] = []
    real = opening._probe_stream

    def counted(*args: Any, **kwargs: Any) -> int:
        seen.append(real(*args, **kwargs))
        return seen[-1]

    monkeypatch.setattr(opening, "_probe_stream", counted)
    table = "".join(f"{i},{i * 7 % 13},{i % 5}\n" for i in range(1_000_000)).encode()
    archive = _zip(tmp_path / "pkg.zip", {"data.csv.gz": gzip.compress(table)})
    with open_package(archive):
        pass
    assert len(table) > 8 * MB and seen and seen[0] < 4 * MB  # it stopped long before the end


def test_a_folder_is_never_scanned_or_limited(tmp_path: Path, small_floor: None) -> None:
    root = tmp_path / "mine"
    root.mkdir()
    (root / "big.xlsx").write_bytes(_office(tmp_path, 20 * MB))
    with open_package(root, max_bytes=10, max_files=1) as pkg:  # the person's own files
        assert pkg.files()["rel"].tolist() == ["big.xlsx"]
