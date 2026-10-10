"""code_read()'s decoding of a code file's bytes (D74)."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import zipfile

import pytest

from metacheck.codecheck._decode import decode_lines

GERMAN = "Die Teilnehmer berichten über Größe und Häufigkeit."


@pytest.mark.parametrize(
    ("data", "lines"),
    [
        (b"x <- 1\ny <- 2\n", ["x <- 1", "y <- 2"]),
        (b"x <- 1\r\ny <- 2", ["x <- 1", "y <- 2"]),
        (b"x <- 1\ry <- 2\r", ["x <- 1", "y <- 2"]),
        (b"a\r\nb\rc\n", ["a", "b", "c"]),  # mixed line ends (vroom drops lines)
        (b"a\n\nb\n", ["a", "", "b"]),
        (b"a\x00b\n", ["ab"]),
        ("# café\n".encode(), ["# café"]),
        (b"\xef\xbb\xbfx\n", ["x"]),
        (b"", []),
    ],
)
def test_lines(data: bytes, lines: list[str]) -> None:
    assert decode_lines(data) == lines


@pytest.mark.parametrize("codec", ["utf-16", "utf-32"])
def test_byte_order_mark_picks_the_encoding(codec: str) -> None:
    assert decode_lines(f"# {GERMAN}\nx <- 1\n".encode(codec)) == [f"# {GERMAN}", "x <- 1"]


@pytest.mark.parametrize(
    ("text", "codec"),
    [
        (f"# {GERMAN}\nx <- 1\n" * 3, "latin-1"),
        ("# Les élèves préfèrent la méthode déjà décrite à l'été.\nx <- 1\n" * 3, "cp1252"),
        ("# Результаты анализа показывают, что участники сообщают.\nx <- 1\n" * 3, "cp1251"),
        ("# תוצאות הניתוח מראות שהמשתתפים חייבים לדווח.\nx <- 1\n" * 3, "cp1255"),
    ],
)
def test_legacy_encodings_are_detected(text: str, codec: str) -> None:
    assert decode_lines(text.encode(codec)) == text.splitlines()


def test_unknown_bytes_fall_back_to_windows_1252() -> None:
    assert decode_lines(b"caf\xe9\n") == ["café"]


def test_bytes_that_fit_no_encoding_are_shown_as_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    import chardet

    monkeypatch.setattr(chardet, "detect", lambda data: {"encoding": None})
    # 0x81 and 0x8d are undefined in Windows-1252
    assert decode_lines(b"caf\xe9\x81\n") == ["caf<e9><81>"]


def _zip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("script.R", data)
    return buf.getvalue()


@pytest.mark.parametrize("pack", [gzip.compress, bz2.compress, lzma.compress, _zip])
def test_compressed_files_are_read_decompressed(pack: object) -> None:
    assert decode_lines(pack(b"x <- 1\n")) == ["x <- 1"]  # type: ignore[operator]


def test_corrupt_compressed_data_is_read_as_it_is() -> None:
    assert decode_lines(b"BZh is not bzip2\n") == ["BZh is not bzip2"]
