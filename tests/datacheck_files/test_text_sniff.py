"""Text peeking and delimiter/header sniffing (values checked against R)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pytacheck.datacheck import files as F
from pytacheck.datacheck._files_readers import vec_as_names_unique

DATA = Path(__file__).parent / "data"


def test_text_peek_decodes_utf16_eprime() -> None:
    assert F.text_peek(DATA / "eprime_utf16.txt") == [
        "*** Header Start ***",
        "VersionPersist: 1",
        "LevelName: Session",
        "Experiment: naming",
        "Subject: 1",
        "*** Header End ***",
    ]


def test_text_peek_limits_and_missing(tmp_path: Path) -> None:
    assert len(F.text_peek(DATA / "hundred.csv", n=2)) == 2
    assert F.text_peek(tmp_path / "nope.txt") == []


def test_txt_classify_content() -> None:
    assert F.txt_classify_content(DATA / "eprime_utf16.txt") == "data"
    assert F.txt_classify_content(DATA / "table.txt") == "data"
    assert F.txt_classify_content(DATA / "prose.txt") is None


def test_sniffers() -> None:
    assert F._sniff_delimiter(DATA / "semicolon.csv") == ";"
    assert F._sniff_delimiter(DATA / "tabbed.tsv") == "\t"
    assert F._detect_header(DATA / "headerless.csv", ",") is False
    assert F._detect_header(DATA / "rwrite.csv", ",") is True
    assert F._count_fields('"a,b",c', ",") == 2
    assert F._count_fields("a;b;;c", ";") == 4
    assert F._is_single_field_blob(DATA / "blob.csv", ",") is True
    assert F._is_single_field_blob(DATA / "single_col.csv", ",") is False


def test_vec_as_names_unique() -> None:
    # vctrs::vec_as_names(c("a", "a", "b", "...1", ""), repair = "unique")
    assert vec_as_names_unique(["a", "a", "b", "...1", ""]) == [
        "a...1", "a...2", "b", "...4", "...5",
    ]  # fmt: skip


def test_rscript_path_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / ("Rscript.exe" if os.name == "nt" else "Rscript")  # PATHEXT on Windows
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PYTACHECK_RSCRIPT", str(fake))
    assert F.rscript_path() == str(fake)
    monkeypatch.delenv("PYTACHECK_RSCRIPT")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    assert Path(F.rscript_path()).name.lower() == fake.name.lower()


def test_read_rdata_isolated_needs_no_r(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytacheck deserialises the workspace itself; no R process is started
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("PYTACHECK_RSCRIPT", raising=False)
    df = F._read_rdata_isolated(DATA / "workspace.RData", n_rows=2)
    assert list(df.columns) == ["id", "score", "f", "s", "d", "l"]
    assert len(df) == 2
    assert F._read_rdata_isolated(DATA / "nothing.RData") is None
