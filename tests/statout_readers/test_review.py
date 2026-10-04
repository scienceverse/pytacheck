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

from metacheck.statout.jasp import (
    _frame_from_columns,
    _html_inline_images,
    _rsqlite_column,
    _url_decode,
    import_jasp,
)
from metacheck.statout.omv import _omv_extract_syntax, import_omv
from metacheck.statout.spv import (
    _as_numeric,
    _fit_function,
    _raw_to_char,
    _read_lines,
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
    # a template value is rendered (R keeps the raw "^1", U148)
    assert nul["value"].tolist() == ["1.5", "n/a", "t", "0.25"]
    assert nul.attrs["spv_title"] == "Trailing NULs"


def test_url_decode_keeps_malformed_escapes() -> None:
    # U148: a malformed escape stays as written (R makes a NUL byte, and an
    # embedded one fails export_*_html())
    assert _url_decode("a%20b.png") == "a b.png"
    assert _url_decode("a%") == "a%"
    assert _url_decode("a%2") == "a%2"
    assert _url_decode("%C3%A9") == "é"
    assert _url_decode("a%zz.png") == "a%zz.png"
    assert _url_decode("a%2%41") == "a%2A"


def test_inline_images_keep_absolute_src_inside_root(tmp_path: Path) -> None:
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "p.png").write_bytes(b"PNG")
    html = _html_inline_images('<img src="/r/p.png"><img src="/r/p.png">', str(tmp_path))
    assert html.count("data:image/png;base64,UE5H") == 2  # every occurrence (U148)


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


def test_data_frame_pads_short_columns() -> None:
    # U145: a shorter column is padded with missing values; R recycles it
    # (repeating values the file does not hold) or stops
    a = pd.array([1, 2, 3, 4], dtype="Int64")
    b = pd.array([1.0, 2.0], dtype="Float64")
    df = _frame_from_columns([a, b], ["a", "b"])
    assert df["b"].tolist()[:2] == [1.0, 2.0] and df["b"].isna().tolist()[2:] == [True, True]
    df = _frame_from_columns([a, b], ["a", "b"], [{}, {"label": "B"}])
    assert len(df) == 4
    df = _frame_from_columns([a, pd.array([1, 2, 3], dtype="Int64")], ["a", "c"])
    assert df["c"].isna().tolist() == [False, False, False, True]


def test_import_jasp_truncated_and_untyped_columns() -> None:
    # U145: a column shorter than rowCount is padded with NA (R stops), and a
    # column without a columnType is read as nominal (R stops)
    short = import_jasp(REVIEW / "short.jasp")["data"]
    assert short["g"].tolist() == [1, 2, 3, 1]
    assert short["s"].tolist()[:2] == [1.0, 2.0] and short["s"].isna().tolist()[2:] == [True] * 2
    untyped = import_jasp(REVIEW / "sqlite_nulltype.jasp")
    assert untyped["columns"]["type"].isna().any()


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


def test_fit_function_evaluates_arithmetic_only() -> None:
    xs = [1.0, 2.0, 4.0]
    for text, expected in {
        "0.5 * x + 3": [3.5, 4.0, 5.0],
        "0.01 * x^2 + 1": [1.01, 1.04, 1.16],
        "-x^2": [-1.0, -4.0, -16.0],  # ^ binds tighter than the sign
        "2^-1 * x": [0.5, 1.0, 2.0],
        "x ** 2": [1.0, 4.0, 16.0],
        "+x / 2": [0.5, 1.0, 2.0],
        "3": [3.0, 3.0, 3.0],  # a constant is a flat line
        ".5 * x + 1.5e-3": [0.5015, 1.0015, 2.0015],
        "exp(x) / 10": [math.exp(v) / 10 for v in xs],
        "log(x, 2)": [0.0, 1.0, 2.0],
        "sqrt(x) + log(x)": [1.0, math.sqrt(2) + math.log(2), 2.0 + math.log(4)],
        "\tx\n": xs,
    }.items():
        fn = _fit_function(text)
        assert fn is not None, text
        assert fn(xs) == pytest.approx(expected), text
    # undefined points are NaN, never an error
    y = _fit_function("1 / (x - 2)")([1.0, 2.0, 4.0])  # type: ignore[misc]
    assert y[0] == -1.0 and math.isnan(y[1]) and y[2] == 0.5
    assert all(math.isnan(v) for v in _fit_function("log(x - 10)")([1.0, 2.0]))  # type: ignore[misc]
    assert all(math.isnan(v) for v in _fit_function("(0 - x)^0.5")([1.0, 2.0]))  # type: ignore[misc]
    assert math.isnan(_fit_function("10^400^x")([2.0])[0])  # type: ignore[misc]


@pytest.mark.parametrize(
    "text",
    [
        "", "   ", "x +", "TRUE", "NULL", "a * x", "1; 2", "1_000", "2L * x", "1i * x", "x // 2",
        "x % 2", "x %% 2", "1 if x else 2", "x > 1", "!x", "not x", "x[0]", "x.real", "x$y",
        "'abc'", "abs(x)", "log(x, base=2)", "log()", "exp(x, 2)", "max(x, 1)", "y = x", "y <- x",
        "(lambda: 1)()", "__import__('os')", "x if 1 else 2", "[x]", "{x}", "-x if x else 1",
        "True", "x * True",
    ],
)  # fmt: skip
def test_fit_function_skips_everything_else(text: str) -> None:
    assert _fit_function(text) is None


def test_function_guides_keep_drawable_expressions() -> None:
    from lxml import etree

    def guide(value: str) -> object:
        node = etree.fromstring(f'<functionGuide name="g" value="{value}"/>')
        return _spvviz_function_guide(node)

    assert guide("0.5 * x + 3") is not None and guide("exp(x / 100)") is not None
    assert guide("a * x") is None and guide("x +") is None and guide("   ") is None

    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 5.0]})
    df.attrs["spv_chart_type"] = "point"
    # a constant is a flat line and an expression that is undefined everywhere
    # draws no line; neither fails the chart
    for expr in ("3", "1 / (x - x)", "log(0 - x)"):
        df.attrs["spv_chart_fits"] = [{"name": None, "expr": expr, "fn": _fit_function(expr)}]
        assert _spv_chart_html(df).startswith("<img"), expr


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
