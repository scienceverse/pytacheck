"""Regression tests for the divergences found in the review of the datacheck_files port.

Expected values are what R (metacheck + data.table / utils / haven) returns for
the same inputs; the matching parity cases are in
``parity/cases/datacheck_files_review.yaml``.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.datacheck import files as F
from pytacheck.datacheck._files_fread import FreadError, fread
from pytacheck.datacheck._files_readtable import (
    ReadTableError,
    _map_cr,
    read_table,
    type_convert,
)
from pytacheck.datacheck._files_time import posixct_series

REVIEW = Path(__file__).parent / "data" / "review"


def _na(s: pd.Series) -> list[object]:
    return [None if v is pd.NA else v for v in s.tolist()]


def _write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    p = tmp_path / name
    p.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return p


# -- text_peek(): rawToChar() / iconv() and NUL bytes -----------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"a,b\n1,2\n\0\0", ["a,b", "1,2"]),  # UTF-16 decode has a NUL -> NA; trailing NULs dropped
        (b"a,b\n1,2\n\0", ["a,b", "1,2"]),
        (b"A\0\0\0B\0", []),  # embedded NUL everywhere -> nothing
        (b"\0\0\0\0", []),
        (b"x,y\n1,2\n\0\0z", []),
    ],
)
def test_text_peek_nul_handling(tmp_path: Path, data: bytes, expected: list[str]) -> None:
    assert F.text_peek(_write(tmp_path, "f.txt", data)) == expected


def test_text_peek_latin1_errors_like_r(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input string 1 is invalid"):
        F.text_peek(_write(tmp_path, "l.txt", b"caf\xe9,b\n1,2\n"))


# -- readLines(): a UTF-8 BOM is dropped from the first line of every call ------


def test_sniffers_skip_bom(tmp_path: Path) -> None:
    p = _write(tmp_path, "b.txt", b"\xef\xbb\xbf# comment line\rid|x\r1|a\r")
    assert F._sniff_delimiter(p) == "|"
    p2 = _write(tmp_path, "n.csv", b"\xef\xbb\xbf1,2,3\n4,5,6\n")
    assert F._detect_header(p2, ",") is False
    df = F.data_read_head(p2, n_rows=math.inf)
    assert df is not None
    assert list(df.columns) == ["col_1", "col_2", "col_3"]


# -- fread ------------------------------------------------------------------------


def test_fread_invalid_line_keeps_position() -> None:
    # R: sep=\t, quote rule 2 finds the only 2-field line -> header, no rows
    df = fread(REVIEW / "tab_in_quotes.tsv", "\t", True)
    assert list(df.columns) == ['"tab', 'here",00:00:01']
    assert len(df) == 0


def test_fread_nul_in_name_is_an_error_and_values_drop_nuls() -> None:
    with pytest.raises(FreadError, match="embedded nul"):
        fread(REVIEW / "fread_nul_name.csv", ",", True)
    df = fread(REVIEW / "fread_nul_value.csv", ",", True)
    assert df["b"].tolist() == ["xy", "z", "w"]


def test_fread_first_line_field_count_assertion() -> None:
    with pytest.raises(FreadError, match="first line has field count 0"):
        fread(REVIEW / "fread_cr_ws_first.tsv", "\t", False)


def test_fread_integer64_class() -> None:
    df = fread(REVIEW / "int64.csv", ",", True)
    assert df.attrs["col_attrs"]["big"] == {"class": "integer64"}
    assert df["big"].tolist()[:2] == [12345678901, 3]
    assert "small" not in df.attrs["col_attrs"]


# -- utils::read.delim() ----------------------------------------------------------


def test_map_cr_is_rconn_fgetc() -> None:
    assert _map_cr(b"a\r\nb\rc\r\r\nd\r") == b"a\nb\nc\n\n\nd\n"


def test_type_convert_rules() -> None:
    def tc(*vals: str | None) -> pd.Series:
        return type_convert([None if v is None else v.encode() for v in vals])

    assert str(tc("true", "false").dtype) == "string"  # only T/F/TRUE/FALSE are logical
    assert _na(tc("T", "FALSE", None, "")) == [True, False, None, None]
    assert str(tc(" 12", "3").dtype) == "Int64"  # strtol(): leading blanks allowed
    assert str(tc("12 ", "3").dtype) == "float64"  # ... trailing ones are not
    assert str(tc("-2147483648").dtype) == "float64"  # INT_MIN is NA_integer_
    assert tc("0x1A", "1e5").tolist() == [26.0, 1e5]
    assert str(tc("NAN", "NaN").dtype) == "string"  # "NA" prefix rules the first out
    assert tc("1i", "2+3i").tolist() == [1j, 2 + 3j]
    assert str(tc("", " ", None).dtype) == "boolean"
    assert str(tc(chr(0xA0) + "12").dtype) == "string"  # NBSP is not blank
    with pytest.raises(ReadTableError, match="invalid multibyte string at '<e9>t<e9>'"):
        type_convert([b"12", b"\xe9t\xe9"])
    assert _na(tc("a", None)) == ["a", None]


def test_read_table_quotes_and_rownames() -> None:
    df = read_table(REVIEW / "rt_nul_quote_header.csv", ",", True)
    assert list(df.columns) == ["ab a\tb TRUE he said hi"]  # NUL after a quote is lost
    assert df.iloc[:, 0].tolist() == ["a,b"]
    df = read_table(REVIEW / "rt_rownames.txt", ",", True)  # one more column: row names
    assert list(df.columns) == ["h"]
    assert _na(df["h"]) == [None, 1, 2, 3]
    with pytest.raises(ReadTableError, match=r"duplicate 'row\.names'"):
        read_table(REVIEW / "rt_dup_rownames.txt", ",", True)
    with pytest.raises(ReadTableError, match="more columns than column names"):
        read_table(REVIEW / "rt_wrap.csv", ",", True)
    with pytest.raises(ReadTableError, match="invalid 'nlines'"):
        read_table(REVIEW / "rt_wrap.csv", ",", False, nrows=0)


def test_read_table_blank_and_wrapped_lines() -> None:
    df = read_table(REVIEW / "rt_blank_na.csv", ",", True)
    assert _na(df["v"]) == ["xy", None, " "]  # `""` alone is a blank line
    df = read_table(REVIEW / "rt_wrap.csv", ",", False)
    assert df.shape == (5, 3)  # long lines wrap, short ones are padded with ""


def test_read_delim_fast_rereads_latin1_on_na() -> None:
    # is.na(iconv(col)) is TRUE for NA too: any NA in a character column of a
    # fallback read triggers the latin1 re-read (fread fails on the NUL here)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = F._read_delim_fast(REVIEW / "rt_fallback_latin1.csv", ",", True)
    assert _na(df["v"]) == ["ab", None, "caf" + chr(0xC3) + chr(0xA9)]


# -- haven ------------------------------------------------------------------------


def test_haven_types_come_from_metadata() -> None:
    df = F.data_read_head(REVIEW / "stata_int_na.dta", n_rows=math.inf)
    assert df is not None
    assert str(df["i"].dtype) == "float64"
    assert df["i"].isna().tolist() == [False, True, False]
    assert df.attrs["col_attrs"]["t"]["class"] == ["POSIXct", "POSIXt"]
    assert df["t"].isna().all()
    zero = F.data_read_head(REVIEW / "zero_rows.sav", n_rows=math.inf)
    assert zero is not None
    assert len(zero) == 0
    assert [str(t) for t in zero.dtypes][:2] == ["float64", "string"]
    assert zero.attrs["col_attrs"]["day"]["class"] == "Date"
    na = F.data_read_head(REVIEW / "all_na_dates.sav", n_rows=math.inf)
    assert na is not None
    assert na.attrs["col_attrs"]["dur"]["class"] == ["hms", "difftime"]
    assert na["dur"].tolist()[0] == 3600.0


# -- helpers ----------------------------------------------------------------------


def test_posixct_series_out_of_nanosecond_range() -> None:
    s = posixct_series([1.5, math.nan, 1e10 + 0.5, 1e13])
    assert s.isna().tolist() == [False, True, False, False]
    assert s.iloc[2].year == 2286


def test_posixct_and_complex_as_character() -> None:
    from pytacheck.datacheck._files_rdata import _complex_as_character, posixct_as_character

    ts = pd.Timestamp("2020-01-01 10:00:00.5", tz="UTC")
    assert posixct_as_character(ts) == "2020-01-01 10:00:00.5"
    assert posixct_as_character(pd.Timestamp("2020-01-01", tz="UTC")) == "2020-01-01"
    assert [_complex_as_character(z) for z in (1j, 1.5 - 2j, complex(0, -0.0))] == [
        "0+1i",
        "1.5-2i",
        "0+0i",
    ]


def test_detect_likert_scale_integer_overflow_is_na() -> None:
    out = F._detect_likert_scale([1, 2, 3, 4, 5] * 5 + [3e9])
    assert out is not None
    assert out["suspects"] == []
    assert out["coverage"] == pytest.approx(25 / 26)


def test_manifest_without_repo_url(tmp_path: Path) -> None:
    files = pd.DataFrame({"file_name": ["a.csv"], "file_url": [None], "data_type": ["data"]})
    path = tmp_path / "m.json"
    F._data_check_write_manifest(path, files, [True], None, "p1", "data", 100, 500)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["not_downloaded"]["unintentional_files"][0]["repo_url"] == {}
    assert "repo_url" not in doc["files"][0]
    files = pd.DataFrame({"file_name": ["b.csv"], "file_url": ["https://osf.io/b/"]})
    with pytest.raises(ValueError, match="argument is of length zero"):
        F._data_check_write_manifest(path, files, [True], None, "p1", "data", 100, 500)
    out = F._data_check_write_manifest(
        str(tmp_path / "d") + "/", pd.DataFrame({"repo_url": ["u"], "file_name": ["a"]}),
        [False], None, "P", "data", 1, 1,
    )  # fmt: skip
    assert out == [str(tmp_path / "d") + "//P.manifest.json"]


def test_duplicate_names_attrs_describe_the_first_column(tmp_path: Path) -> None:
    # col_attrs is keyed by name; with duplicated names it describes the first
    # column, the one R's df$x / df[["x"]] returns
    first_date = fread(_write(tmp_path, "a.csv", "x,x\n2020-01-01,1\n2020-01-02,2\n"), ",", True)
    assert first_date.attrs["col_attrs"] == {"x": {"class": ["IDate", "Date"]}}
    first_int = fread(_write(tmp_path, "b.csv", "x,x\n1,2020-01-01\n2,2020-01-02\n"), ",", True)
    assert "col_attrs" not in first_int.attrs
