"""What the delimited-file reader (``_files_delim``) does, case by case.

``test_delim.py`` holds it against R's recorded output; this file pins the rules that
output cannot show: the way it finds the table, types a column, reads a head, and the
files it reads that R does not.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd
import pytest

from metacheck.datacheck._files_delim import read_delim
from metacheck.datacheck.files import data_read_head


def _read(tmp_path: Path, content: bytes | str, *args: object, **kw: object) -> pd.DataFrame:
    p = tmp_path / "t.csv"
    p.write_bytes(content.encode() if isinstance(content, str) else content)
    return read_delim(p, *args, **kw)  # type: ignore[arg-type]


def _classes(df: pd.DataFrame) -> list[str]:
    return [str(d) for d in df.dtypes]


class TestTypes:
    def test_ladder(self, tmp_path: Path) -> None:
        df = _read(
            tmp_path,
            "l,i,big,d,s\nTRUE,1,3000000000,1.5,a\nFALSE,-2,4,2,b\nNA,,5,NA,\n",
            ",",
            True,
        )
        assert _classes(df) == ["boolean", "Int64", "Int64", "float64", "string"]
        assert df.attrs["col_attrs"] == {"big": {"class": "integer64"}}
        assert df["i"].tolist() == [1, -2, pd.NA]
        assert df["s"].tolist() == ["a", "b", ""]

    def test_logical_families_do_not_mix(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a,b\nTRUE,True\nfalse,False\n", ",", True)
        assert _classes(df) == ["string", "boolean"]

    def test_date_and_timestamp_columns(self, tmp_path: Path) -> None:
        df = _read(
            tmp_path,
            "d,t\n2020-01-02,2020-01-02 03:04:05\n2021-12-31,2021-12-31T23:59:59Z\n",
            ",",
            True,
        )
        assert df["d"].tolist() == [dt.date(2020, 1, 2), dt.date(2021, 12, 31)]
        assert str(df["t"].dt.tz) == "UTC"
        assert df.attrs["col_attrs"] == {
            "d": {"class": ["IDate", "Date"]},
            "t": {"class": ["POSIXct", "POSIXt"], "tzone": "UTC"},
        }

    def test_impossible_date_is_text(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "d\n2020-02-30\n2020-01-01\n", ",", True)
        assert _classes(df) == ["string"]

    def test_excel_specials_are_numbers(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "x\n1.5\n#DIV/0!\n-1.#INF\n#N/A\n", ",", True)
        assert _classes(df) == ["float64"]
        values = df["x"].tolist()
        assert values[0] == 1.5 and values[2] == -math.inf
        assert math.isnan(values[1]) and math.isnan(values[3])

    def test_a_double_beyond_range_is_text(self, tmp_path: Path) -> None:
        assert _classes(_read(tmp_path, "x\n1e400\n2\n", ",", True)) == ["string"]

    def test_hexadecimal_float(self, tmp_path: Path) -> None:
        assert _read(tmp_path, "x\n0x1.8p1\n0x1p0\n", ",", True)["x"].tolist() == [3.0, 1.0]

    def test_decimal_comma_is_voted_for(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a;b\n1,5;x\n2,25;y\n", ";", True)
        assert df["a"].tolist() == [1.5, 2.25]
        assert _classes(_read(tmp_path, "a;b\n1.5;x\n2.25;y\n", ";", True))[0] == "float64"

    def test_an_all_empty_column_is_logical(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a,b\n1,\n2,NA\n", ",", True)
        assert _classes(df) == ["Int64", "boolean"]
        assert df["b"].isna().all()


class TestFields:
    def test_blanks_around_unquoted_fields_go(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'a,b,c\n  x  , 1 ,"  y  "\n', ",", True)
        assert df.iloc[0].tolist() == ["x", 1, "  y  "]

    def test_a_one_column_file_keeps_its_tabs_and_loses_its_spaces(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "v\n\tLevel: 3\n  x  \n", ",", True)
        assert df["v"].tolist() == ["\tLevel: 3", "x"]

    def test_na_is_missing_unless_quoted_in_a_text_column(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'a,b\nNA,"NA"\nx,y\n', ",", True)
        assert df["a"].tolist() == [pd.NA, "x"]
        assert df["b"].tolist() == ["NA", "y"]
        assert df["b"].dtype == "string"
        assert _classes(_read(tmp_path, 'a\n"NA"\n"NA"\n', ",", True)) == ["boolean"]
        df = _read(tmp_path, 'a\n1\n"NA"\n', ",", True)
        assert _classes(df) == ["Int64"] and df["a"].isna().tolist() == [False, True]

    def test_doubled_quote_is_one_quote(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'a,b\n"he said ""hi""",2\n', ",", True)
        assert df["a"].tolist() == ['he said "hi"']

    def test_embedded_separator_and_newline(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'a,b\n"x,y","l1\nl2"\n', ",", True)
        assert df.iloc[0].tolist() == ["x,y", "l1\nl2"]

    def test_backslash_escaped_quote_is_kept_with_its_backslash(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'a,b\n"use of \\"x\\" here",2\n"plain",3\n', ",", True)
        assert df["a"].tolist() == ['use of \\"x\\" here', "plain"]

    def test_a_backslash_before_the_closing_quote_is_text(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'path,n\n"C:\\dir\\",1\n"D:\\x",2\n', ",", True)
        assert df["path"].tolist() == ["C:\\dir\\", "D:\\x"]

    def test_a_header_name_that_is_empty_or_na_is_numbered(self, tmp_path: Path) -> None:
        df = _read(tmp_path, ",NA,c\n1,2,3\n", ",", True)
        assert list(df.columns) == ["V1", "V2", "c"]

    def test_headerless_names(self, tmp_path: Path) -> None:
        assert list(_read(tmp_path, "1,2\n3,4\n", ",", False).columns) == ["V1", "V2"]

    def test_crlf_and_lone_cr(self, tmp_path: Path) -> None:
        assert _read(tmp_path, b"a,b\r\n1,2\r\n3,4\r\n", ",", True)["a"].tolist() == [1, 3]
        assert _read(tmp_path, b"a,b\r1,2\r3,4\r", ",", True)["a"].tolist() == [1, 3]

    def test_utf8_bom_is_dropped(self, tmp_path: Path) -> None:
        assert list(_read(tmp_path, b"\xef\xbb\xbfa,b\n1,2\n", ",", True).columns) == ["a", "b"]

    def test_invalid_utf8_stays_as_escaped_bytes_and_latin1_reads_it(self, tmp_path: Path) -> None:
        data = b"a\ncaf\xe9\n"
        assert _read(tmp_path, data, ",", True)["a"].tolist() == ["caf\udce9"]
        assert _read(tmp_path, data, ",", True, encoding="latin1")["a"].tolist() == ["café"]


class TestTable:
    def test_lines_before_the_table_are_skipped(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a note\n\na,b\n1,2\n3,4\n", ",", True)
        assert list(df.columns) == ["a", "b"] and len(df) == 2

    def test_a_footer_ends_the_table(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a,b\n1,2\n3,4\ntotal: 2\n", ",", True)
        assert len(df) == 2

    def test_a_blank_line_ends_the_table(self, tmp_path: Path) -> None:
        assert len(_read(tmp_path, "a,b\n1,2\n\n3,4\n", ",", True)) == 1

    def test_one_column_keeps_its_blank_lines_and_separators(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "v\nx;y\n\nz\n", ",", True)
        assert df["v"].tolist() == ["x;y", "", "z"]

    def test_a_quote_that_never_closes_is_a_character(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'a,b\n1,"open\n2,3\n', ",", True)
        assert df["b"].tolist() == ['"open', "3"]

    def test_improper_quotes_in_one_column_are_characters(self, tmp_path: Path) -> None:
        df = _read(tmp_path, 'v\n"1"2\n"x"y\n"z"\n', ",", True)
        assert df["v"].tolist() == ['"1"2', '"x"y', "z"]

    def test_empty_and_blank_files(self, tmp_path: Path) -> None:
        assert _read(tmp_path, "", ",", True).shape == (0, 0)
        assert _read(tmp_path, "  \n \n", ",", True).shape == (0, 0)

    def test_header_only(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a,b\n", ",", True)
        assert list(df.columns) == ["a", "b"] and len(df) == 0


class TestHead:
    def test_nrows_counts_data_rows(self, tmp_path: Path) -> None:
        text = "a,b\n" + "".join(f"{i},x\n" for i in range(50))
        assert len(_read(tmp_path, text, ",", True, nrows=5)) == 5
        assert len(_read(tmp_path, text, ",", False, nrows=5)) == 5
        assert len(_read(tmp_path, text, ",", True, nrows=0)) == 0

    def test_a_head_of_a_long_file_equals_the_start_of_the_whole_read(self, tmp_path: Path) -> None:
        rows = [f"{i},{'x' * (i % 40)}\n" for i in range(30000)]
        text = "a,b\n" + "".join(rows)  # several times the prefix a head scans first
        assert len(text) > 4 * (1 << 16)
        whole = _read(tmp_path, text, ",", True)
        for n in (5, 100, 1500, 20000):
            head = _read(tmp_path, text, ",", True, nrows=n)
            pd.testing.assert_frame_equal(head, whole.iloc[:n].reset_index(drop=True))

    def test_a_head_ends_at_the_same_row_as_the_whole_read(self, tmp_path: Path) -> None:
        text = "a,b\n" + "1,2\n" * 5000 + "footer\n" + "3,4\n" * 5000
        assert len(_read(tmp_path, text, ",", True)) == 5000
        assert len(_read(tmp_path, text, ",", True, nrows=7000)) == 5000


class TestFill:
    def test_short_lines_are_padded_and_blank_ones_skipped(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a,b,c\n1,2\n\n3,4,5\n", ",", True, fill=True)
        assert df.shape == (2, 3)
        assert df["c"].isna().tolist() == [True, False] or df["c"].tolist() == ["", 5]

    def test_it_starts_at_the_first_line_and_is_as_wide_as_its_widest(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "note\na,b\n1,2\n", ",", False, fill=True)
        assert df.shape == (3, 2)


class TestNotInR:
    def test_utf16_is_decoded(self, tmp_path: Path) -> None:
        df = _read(tmp_path, "a\tb\n1\tx\n".encode("utf-16"), "\t", True)
        assert list(df.columns) == ["a", "b"] and df["a"].tolist() == [1]

    def test_a_utf16_table_is_split_at_the_separator_of_its_decoded_text(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "t.txt"
        p.write_bytes("a\tb\n1\tx\n2\ty\n".encode("utf-16"))
        df = data_read_head(p, n_rows=math.inf)
        assert list(df.columns) == ["a", "b"] and df["a"].tolist() == [1, 2]

    def test_a_name_ends_at_a_nul_byte_and_a_value_loses_it(self, tmp_path: Path) -> None:
        df = _read(tmp_path, b"te\0xt,n\nab\0cd,1\n", ",", True)
        assert list(df.columns) == ["te", "n"] and df["te"].tolist() == ["abcd"]

    def test_a_file_of_nul_bytes_is_empty(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="empty"):
            _read(tmp_path, b"\0\0\0", ",", True)
