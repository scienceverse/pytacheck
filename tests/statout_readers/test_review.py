"""Regression tests for divergences found reviewing the statout readers against R.

Each test pins down an R behaviour that the first port missed; the matching
parity cases live in ``parity/cases/statout_readers_review.yaml``.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.statout.jasp import (
    _frame_from_columns,
    _html_inline_images,
    _rsqlite_column,
    _url_decode,
    import_jasp,
)
from pytacheck.statout.omv import _omv_extract_syntax, import_omv
from pytacheck.statout.spv import (
    _as_numeric,
    _r_function,
    _r_parse,
    _raw_to_char,
    _read_lines,
    _RParseError,
    _spv_chart_html,
    _spvviz_function_guide,
    import_spv,
    spv_assemble_table,
)

REVIEW = Path(__file__).resolve().parent / "fixtures" / "review"


def test_raw_to_char_drops_trailing_nuls_only() -> None:
    assert _raw_to_char(b"abc\x00\x00") == "abc"
    assert _raw_to_char(b"\x00") == ""
    with pytest.raises(ValueError, match="embedded nul"):
        _raw_to_char(b"a\x00b")


def test_light_table_strings_with_trailing_nuls_decode() -> None:
    with pytest.warns(UserWarning, match="embedded nul"):
        tabs = import_spv(REVIEW / "spv_strings.spv")
    titles = [t["title"] for t in tabs]
    assert "Nul Strings" in titles
    nul = next(t for t in tabs if t["title"] == "Nul Strings")["data"]
    assert list(nul.columns) == ["Statistic", "Variable", "value"]
    assert nul["value"].tolist() == ["1.5", "n/a", "^1", "0.25"]
    assert nul.attrs["spv_title"] == "Trailing NULs"


def test_url_decode_follows_r() -> None:
    assert _url_decode("a%20b.png") == "a b.png"
    assert _url_decode("a%") == "a"  # NA byte -> NUL, trailing NULs dropped
    assert _url_decode("a%2") == "a"
    assert _url_decode("%C3%A9") == "é"
    with pytest.raises(ValueError, match="embedded nul"):
        _url_decode("a%zz.png")


def test_inline_images_keep_absolute_src_inside_root(tmp_path: Path) -> None:
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "p.png").write_bytes(b"PNG")
    html = _html_inline_images('<img src="/r/p.png"><img src="/r/p.png">', str(tmp_path))
    assert html.count("data:image/png;base64,UE5H") == 1  # first occurrence only


def test_omv_extract_syntax_tab_class_is_a_real_tab() -> None:
    assert _omv_extract_syntax("R jmv::fn\\(x) jmv::ok(y)") == "jmv::ok(y)"
    assert _omv_extract_syntax("R pkg::fn\t(a)") == "pkg::fn (a)"
    assert _omv_extract_syntax("R pkg::t\\t(a)") == ""


def test_jasp_omv_labels_use_the_datacheck_col_attrs_convention() -> None:
    df = import_omv(REVIEW / "review.omv")["data"]
    col_attrs = df.attrs["col_attrs"]
    # repeated codes are kept (R's named vector c(label = code))
    assert col_attrs["n"]["labels"] == [("one", 1.0), ("uno", 1.0), ("two", 2.0)]
    assert col_attrs["n"]["label"] == "5"
    assert col_attrs["a"]["label"] == "partial match"  # f$description partial match
    assert "t" not in col_attrs  # description "" wins over title
    assert "d" not in col_attrs  # title equal to the name


def test_data_frame_recycles_only_attribute_free_columns() -> None:
    a = pd.array([1, 2, 3, 4], dtype="Int64")
    b = pd.array([1.0, 2.0], dtype="Float64")
    df = _frame_from_columns([a, b], ["a", "b"])
    assert df["b"].tolist() == [1.0, 2.0, 1.0, 2.0]
    with pytest.raises(ValueError, match="arguments imply differing number of rows: 4, 2"):
        _frame_from_columns([a, b], ["a", "b"], [{}, {"label": "B"}])
    with pytest.raises(ValueError, match="4, 3"):
        _frame_from_columns([a, pd.array([1, 2, 3], dtype="Int64")], ["a", "c"])


def test_import_jasp_errors_like_r() -> None:
    with pytest.raises(ValueError, match="differing number of rows"):
        import_jasp(REVIEW / "short.jasp")
    with pytest.raises(ValueError, match="missing value where TRUE/FALSE needed"):
        import_jasp(REVIEW / "sqlite_nulltype.jasp")


def test_rsqlite_column_typing(tmp_path: Path) -> None:
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.execute("CREATE TABLE t (a INT, b REAL, c, d)")
    con.executemany(
        "INSERT INTO t VALUES (?,?,?,?)",
        [(1, "abc", None, 1), (2.5, 0.1, None, "x"), ("t", 1e-7, None, -(2**31))],
    )
    cols = list(zip(*con.execute("SELECT a, b, c, d FROM t").fetchall(), strict=True))
    decl = ["INT", "REAL", "", "INT"]
    a, b, c, d = (_rsqlite_column(list(v), t) for v, t in zip(cols, decl, strict=True))
    assert a.tolist() == [1.0, 2.5, 0.0]  # int upgraded to double, text coerced to 0
    assert b.tolist() == ["abc", "0.1", "1.0e-07"]  # first value decides: character
    assert str(c.dtype) == "boolean"  # all NULL and untyped: logical NA
    assert d.tolist()[:2] == [1, 0] and pd.isna(d[2])  # -2^31 is NA_integer_


def test_r_parse_matches_r_on_edge_cases() -> None:
    ok = ["x;", "{x;;y}", "f(,)", "x |> f()", "a::'b'", "..1", "\\(x) x + 1", "x := 1", "é + x"]
    bad = [";", "x;;y", "a < b < c", "x |> exp", "1_000", "'\\q'", "function(x, x) 1", "x²"]
    for text in ok:
        assert _r_parse(text), text
    for text in bad:
        with pytest.raises(_RParseError):
            _r_parse(text)
    assert _r_parse("") == [] and _r_parse("  # c") == []
    assert len(_r_parse("x\n\ny")) == 2


def test_function_guides_follow_r_parse_and_lines() -> None:
    from lxml import etree

    def guide(value: str) -> object:
        node = etree.fromstring(f'<functionGuide value="{value}"/>')
        return _spvviz_function_guide(node)

    assert guide("TRUE") is not None and guide("a * x") is not None
    assert guide("1_000") is None and guide("   ") is None
    fn = _r_function("(function(z, k = 2) z / k)(x)")
    assert fn is not None and fn([2.0, 4.0]) == [1.0, 2.0]
    assert _r_function("NULL")([1.0]) is None  # type: ignore[misc]

    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 5.0]})
    df.attrs["spv_chart_type"] = "point"
    # a scalar, function or formula guide no longer fails the chart (R's
    # lines() stops: "'x' and 'y' lengths differ", U148); a constant is a
    # flat line
    for expr in ("TRUE", "3", "function(x) x", "y ~ x"):
        df.attrs["spv_chart_fits"] = [{"name": None, "expr": expr, "fn": _r_function(expr)}]
        assert _spv_chart_html(df).startswith("<img"), expr
    df.attrs["spv_chart_fits"] = [{"name": "a", "expr": "a * x", "fn": _r_function("a * x")}]
    assert _spv_chart_html(df).startswith("<img")


def test_boxplot_without_complete_rows_is_left_out() -> None:
    # U148: nothing to draw leaves the chart out (R's boxplot() stops the export)
    df = pd.DataFrame({"category": ["a", "b"], "value": [math.nan, math.nan]})
    df.attrs["spv_chart_type"] = "boxplot"
    assert _spv_chart_html(df) == ""


def test_assemble_table_tolerates_missing_n_leaves() -> None:
    df = spv_assemble_table(
        dims=[{"name": "A", "leaves": {"0": ["a"], "1": ["b"]}}],
        axes={"rows": [0]},
        cells=[{"index": 1.0, "value": {"type": "numeric", "x": 2.0}}],
    )
    assert df is not None
    assert df["A"].tolist() == ["a"]  # max(1L, NULL) is one leaf: 1 %% 1 is leaf 0
    assert df.attrs["spv_row_dims"] == ["A"]


def test_read_lines_cuts_at_nul(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_bytes(b"ab\x00cd\nef\r\n\x00x\rz")
    assert _read_lines(p) == ["ab", "ef", "", "z"]


def test_as_numeric_hex_floats() -> None:
    assert _as_numeric("0x1p3") == 8.0
    assert _as_numeric("0x.8") == 0.5
    assert _as_numeric("-0x1A") == -26.0
    assert _as_numeric("0x") is None
