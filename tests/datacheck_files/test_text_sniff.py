"""Text peeking and delimiter/header sniffing (values checked against R)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metacheck.datacheck import files as F
from metacheck.datacheck._files_readers import vec_as_names_unique

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


def test_the_sniffer_counts_separators_outside_quoted_text(tmp_path: Path) -> None:
    def sniff(text: str) -> str:
        (tmp_path / "t.csv").write_text(text, encoding="utf-8")
        return F._sniff_delimiter(tmp_path / "t.csv")

    assert sniff('"Last, First";"Age"\nx;1\n') == ";"  # a comma in quotes is not a separator
    assert sniff('"a;b;c",d\n1,2\n') == ","
    assert sniff('x"y,z;a;b\n') == ";"  # a quote inside a field is text, so every ; counts
    assert sniff('"a\tb"\t"c"\n') == "\t"
    assert sniff('"open,a;b;c\n') == ";"  # a quote that never closes is text too
    assert sniff("a,b\n") == ","


def test_a_tsv_is_read_with_a_tab_unless_no_tab_is_outside_quotes(tmp_path: Path) -> None:
    def sep(text: str, name: str = "t.tsv") -> str:
        (tmp_path / name).write_text(text, encoding="utf-8")
        return F._delimiter(tmp_path / name, name.rsplit(".", 1)[1])

    assert sep("a\tb\n1\t2\n") == "\t"
    assert sep('a,b\n1,"x\ty"\n2,3\n') == ","  # the only tab is inside quotes (D73)
    assert sep('a,b\n1,2\n3,4\n5,6\n"x\ty",8\n') == ","
    assert sep("a,b\n1\t2\n") == "\t"  # a tab outside quotes, whatever else is there
    assert sep("a,b\n" + "1,2\n" * 5 + "x\ty\n") == "\t"  # in any of the first 100 lines
    assert sep("Smith, John\nDoe\nRoe, Jane, Jr\n") == "\t"  # not regular: the tab, as R does
    assert sep('"Smith, John"\n"Doe"\n') == "\t"  # nothing outside quotes to separate by
    assert sep("one\ntwo\n") == "\t"
    assert sep("a;b\n1;2\n") == ";"
    assert sep("a,b\n1\t2\n", "t.csv") == ","  # only .tsv is read with a tab
    (tmp_path / "h.tsv").write_text('a,b\n1,"x\ty"\n2,3\n', encoding="utf-8")
    head = F.data_read_head(tmp_path / "h.tsv")
    assert head is not None and head.shape == (2, 2) and head.iloc[0, 1] == "x\ty"


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
