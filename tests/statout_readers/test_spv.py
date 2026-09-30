"""Tests for the SPSS Viewer (.spv) reader (port of R/spv.R).

metacheck ships no .spv fixture (its corpus lives in an uncommitted cache), so
these run against the grammar-encoded archives built by ``make_fixtures.py``;
their exact outputs are pinned against R by the parity goldens.
"""

from __future__ import annotations

import re
import struct
import warnings
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from metacheck.statout import spv
from metacheck.statout.spv import (
    SpvBinaryEOF,
    _as_integer,
    _as_numeric,
    _make_unique,
    _spv_decode_legacy_data,
    _spv_decode_light_table,
    _spv_display_value,
    _spv_export_syntax,
    _spv_html_escape,
    _spv_read_structure,
    _spv_table_html,
    _spvbin_cursor,
    _spvbin_expect_bytes,
    _spvbin_match_bytes,
    _spvbin_read_bool,
    _spvbin_read_count,
    _spvbin_read_int,
    _spvbin_read_int16,
    _spvbin_read_int32,
    _spvbin_read_int64,
    _spvbin_read_string,
    _spvlb_value_text,
    export_spv_html,
    import_spv,
    spv_assemble_table,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "spv"


def quiet(fn, *a, **k):  # type: ignore[no-untyped-def]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*a, **k)


# -- cursor primitives -----------------------------------------------------------


def test_cursor_reads_and_eof() -> None:
    cur = _spvbin_cursor(struct.pack("<iH", -1, 7) + b"\x01")
    v, cur = _spvbin_read_int32(cur)
    assert v == 2**32 - 1  # unsigned correction, as R
    v, cur = _spvbin_read_int16(cur)
    assert v == 7
    b, cur = _spvbin_read_bool(cur)
    assert b is True
    with pytest.raises(
        SpvBinaryEOF, match=re.escape("needed 4 bytes at offset 8, only 0 available")
    ):
        _spvbin_read_int32(cur)


def test_cursor_int32_na_pattern_errors_like_r() -> None:
    with pytest.raises(ValueError, match=re.escape("missing value")):
        _spvbin_read_int32(_spvbin_cursor(b"\x00\x00\x00\x80"))
    assert _spvbin_read_int(_spvbin_cursor(b"\x00\x00\x00\x80"), 4, signed=True)[0] is None


def test_cursor_int64_and_big_endian() -> None:
    v, _ = _spvbin_read_int64(_spvbin_cursor(struct.pack("<Q", 2**40 + 5)))
    assert v == float(2**40 + 5)
    v, _ = _spvbin_read_int(_spvbin_cursor(b"\x00\x00\x01\x00"), 4, endian="big")
    assert v == 256


def test_cursor_bool_and_markers() -> None:
    with pytest.raises(ValueError, match=re.escape("bad bool byte 2 at offset 1")):
        _spvbin_read_bool(_spvbin_cursor(b"\x02"))
    cur = _spvbin_cursor(b"\x31\x58")
    matched, c2 = _spvbin_match_bytes(cur, 0x58)
    assert not matched and c2 is cur
    matched, c2 = _spvbin_match_bytes(cur, [0x31, 0x58])
    assert matched and c2.pos == 3
    with pytest.raises(ValueError, match=r"expected marker byte\(s\) 88 \(what\) at offset 1"):
        _spvbin_expect_bytes(cur, 0x58, "what")


def test_count_subcursor_drops_version() -> None:
    cur = spv._Cursor(struct.pack("<I", 2) + b"ab" + b"z", 1, 8, version=3)
    inner, after = _spvbin_read_count(cur)
    assert (inner.pos, inner.limit, inner.version) == (5, 7, None)
    assert (after.pos, after.version) == (7, 3)
    with pytest.raises(SpvBinaryEOF):
        _spvbin_read_count(_spvbin_cursor(struct.pack("<I", 9)))


def test_read_string_rejects_embedded_nul() -> None:
    s, _ = _spvbin_read_string(_spvbin_cursor(struct.pack("<I", 3) + b"abc"))
    assert s == "abc"
    with pytest.raises(ValueError, match=re.escape("embedded nul")):
        _spvbin_read_string(_spvbin_cursor(struct.pack("<I", 3) + b"a\x00c"))


# -- R helpers -----------------------------------------------------------------------


def test_make_unique_matches_r() -> None:
    assert _make_unique(["a", "a", "a.1", "a", "b", "a.2"]) == [
        "a",
        "a.3",
        "a.1",
        "a.4",
        "b",
        "a.2",
    ]
    assert _make_unique(["", "", "V1"]) == ["", ".1", "V1"]


def test_as_numeric_and_integer_match_r() -> None:
    vals = [
        "1.",
        ".",
        " 2 ",
        "1e5",
        "0x1A",
        "Inf",
        "-inf",
        "NaN",
        "NA",
        "1_0",
        "infinity",
        "1d5",
        "  ",
    ]
    got = [_as_numeric(v) for v in vals]
    assert got[0] == 1.0 and got[1] is None and got[2] == 2.0 and got[3] == 1e5 and got[4] == 26.0
    assert got[5] == float("inf") and got[6] == float("-inf") and got[7] != got[7]
    assert got[8:] == [None, None, float("inf"), None, None]
    assert [_as_integer(v) for v in [" 40", "40.7", "1e2", "", "0x1A", "-3.9", "1e10"]] == [
        40,
        40,
        100,
        None,
        26,
        -3,
        None,
    ]


# -- light-binary / legacy decoders ---------------------------------------------------


def _member(archive: str, name: str) -> bytes:
    with zipfile.ZipFile(FIX / archive) as zf:
        return zf.read(name)


def test_light_table_v3_decodes_layers_groups_and_footnotes() -> None:
    df = _spv_decode_light_table(_member("modern.spv", "0000000005_lightTableData.bin"))
    assert list(df.columns) == ["Gender", "Statistics", "Variables", "value"]
    assert df["Variables"].tolist()[:3] == ["Scales / age", "Scales / Score", "Valid N (listwise)"]
    assert df["value"].tolist()[:3] == ["120", "34.5678901234568", "0.333333333333333"]
    assert pd.isna(df["value"].iloc[5])  # DBL_MAX "not applicable"
    assert df["value"].iloc[6] == "1e+20"
    # a template cell is rendered (R keeps the raw "^1 of ^2", U148)
    assert df["value"].iloc[8] == "a of 2.5, b"
    assert df.attrs["spv_row_dims"] == ["Variables"]
    assert df.attrs["spv_col_dims"] == ["Statistics"]
    assert df.attrs["spv_layer_dims"] == ["Gender"]
    notes = [(f["text"], f["marker"]) for f in df.attrs["spv_footnotes"]]
    assert notes[0] == ("Based on 118 cases and ^12 others", "a")
    assert notes[1] == ("Listwise deletion.", None)


def test_light_table_v1_and_duplicate_dimension_names() -> None:
    df = _spv_decode_light_table(_member("modern.spv", "0000000004_lightTableData.bin"))
    assert list(df.columns) == ["Info", "Info.1", "dim", "value"]
    assert df.attrs["spv_title"] == "Notes for DataSet1"


def test_light_table_failures_warn_and_return_none() -> None:
    with pytest.warns(UserWarning, match=re.escape("could not decode light-binary table")):
        assert (
            _spv_decode_light_table(_member("modern.spv", "0000000006_lightTableData.bin")) is None
        )
    with pytest.warns(UserWarning, match=re.escape("unsupported light-table version 2")):
        assert (
            _spv_decode_light_table(_member("modern.spv", "0000000015_lightTableData.bin")) is None
        )
    # a table with no cells assembles to None without a warning
    assert _spv_decode_light_table(_member("modern.spv", "0000000014_lightTableData.bin")) is None


def test_legacy_data_strings_and_long_source_names() -> None:
    src = _spv_decode_legacy_data(_member("legacy.spv", "0000000003_tableData.bin"))
    assert [s["source_name"] for s in src] == [
        "tableData",
        "a_source_name_that_is_exactl_and_continues",
    ]
    labels = src[0]["variables"][1]["values"]
    assert [v["s"] for v in labels] == ["N", "Mean", "Std. Error Mean"] * 2
    assert src[0]["variables"][4]["values"][2] == {"d": None, "s": "."}
    with pytest.warns(UserWarning, match=re.escape("LegacyBinary marker")):
        assert _spv_decode_legacy_data(b"\x01garbage") is None


def test_value_text_template_substitution() -> None:
    num = {"type": "numeric", "x": 2.5}
    tmpl = {
        "type": "template",
        "s": "^1 and ^2 not ^12",
        "args": [{"values": [num]}, {"values": []}],
    }
    assert _spvlb_value_text(tmpl) == "2.5 and  not ^12"
    assert _spvlb_value_text({"type": "numeric", "x": float("nan")}) == ""
    assert _spvlb_value_text(None) is None
    assert _spvlb_value_text({"type": "other"}) == ""


def test_spv_assemble_table_direct() -> None:
    dims = [{"name": "", "leaves": {"0": ["a", "b"]}, "n_leaves": 1}]
    cells = [{"index": 0.0, "value": {"type": "numeric", "x": 1.0}}]
    df = spv_assemble_table(dims, {"rows": [0], "columns": [], "layers": []}, cells)
    assert df.to_dict("list") == {"dim": ["a / b"], "value": ["1"]}
    assert spv_assemble_table([], {}, cells) is None


# -- structure + top-level -----------------------------------------------------------


def test_structure_threads_command_and_syntax_across_documents(tmp_path: Path) -> None:
    with zipfile.ZipFile(FIX / "modern.spv") as zf:
        zf.extractall(tmp_path)
    rows = _spv_read_structure(tmp_path)
    assert [r["subtype"] for r in rows] == [
        "Notes",
        "",
        "Broken",
        "Gone",
        "Nothing",
        "One-Sample Test",
        None,
        "Empty",
        "Version 2",
    ]
    # numeric (not lexical) document order; a table without commandName is NA
    assert rows[5]["syntax"] == "T-TEST\n  /TESTVAL=0\n  /VARIABLES=score."
    assert rows[6]["command_name"] is None
    # the log's CSS <style> block is removed and trailing blanks before \n dropped
    assert (
        rows[0]["syntax"] == "DESCRIPTIVES VARIABLES=age score\n  /STATISTICS=MEAN STDDEV MIN MAX."
    )


def test_import_spv_modern() -> None:
    tabs = quiet(import_spv, FIX / "modern.spv")
    assert [(t["analysis"], t["title"], t["table_index"]) for t in tabs] == [
        ("Descriptives", "Notes", 1),
        ("Descriptives", "Descriptive Statistics", 2),
        ("T-Test", "One-Sample Test", 3),
        (None, "One-Sample Test", 4),
    ]
    assert not any(t["is_chart"] for t in tabs)


def test_import_spv_legacy_table() -> None:
    (tab,) = quiet(import_spv, FIX / "legacy.spv")
    df = tab["data"]
    assert list(df.columns) == ["Statistics", "dim2", "value"]
    assert df["Statistics"].tolist() == ["N", "Mean", "Std. Error Mean"] * 2
    assert df["value"].tolist() == ["118", "34.5", ".", "120", "-1.79769313486232e+308", "0.25"]
    assert df.attrs["spv_col_dims"] == ["Statistics"]


def test_import_spv_charts() -> None:
    tabs = quiet(import_spv, FIX / "charts.spv")
    assert [t["data"].attrs["spv_chart_type"] for t in tabs] == [
        "point",
        "interval",
        "boxplot",
        "boxplot",
    ]
    point = tabs[0]["data"]
    assert point["x"].tolist() == [150.0, 160.0, 170.5, 180.0]
    fits = point.attrs["spv_chart_fits"]
    assert [f["expr"] for f in fits] == ["0.5 * x + 3", "0.01 * x^2 + 1"]
    assert fits[0]["fn"]([10.0, 2.0]) == [8.0, 4.0]
    assert point.attrs["spv_chart_xlab"] == "Height (cm)"
    assert point.attrs["spv_chart_ylab"] == "Weight (kg)"
    box = tabs[2]["data"]
    assert box["category"].tolist() == ["Control", "Control", "Treatment", "2"]
    assert box["value"].tolist() == [1.75, 2.5, 3.0, 4.25]


def test_boxplot_from_case_data_decodes() -> None:
    # U144: a box plot backed by case data (a databin, no embeddedSource)
    # decodes to its relabelled categories and values; R's is_na_like() calls
    # abs() on the character categories and the chart is always dropped
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        tabs = import_spv(FIX / "charts.spv")
    messages = [str(w.message) for w in rec]
    assert not any("non-numeric argument" in m for m in messages)
    assert len(tabs) == 4
    box = tabs[3]["data"]
    assert box.attrs["spv_chart_type"] == "boxplot"
    assert box["category"].tolist() == ["Low", "Low", "High", "High", "3"]
    assert box["value"].tolist() == [0.5, 0.75, 1.25, 1.5, 2.0]


def test_import_spv_empty_and_errors(tmp_path: Path) -> None:
    assert import_spv(FIX / "empty.spv") == []
    assert quiet(import_spv, FIX / "logs_only.spv") == []
    with pytest.raises(ValueError, match=re.escape("as a .spv (zip) archive")):
        import_spv(FIX / "notzip.spv")
    with pytest.raises(FileNotFoundError):
        import_spv(tmp_path / "missing.spv")
    other = tmp_path / "x.sav"
    other.write_text("x")
    with pytest.raises(ValueError, match=re.escape("Not a .spv file")):
        import_spv(other)


def test_spv_export_syntax(tmp_path: Path) -> None:
    for name in ("logs_only.spv", "modern.spv", "empty.spv"):
        (tmp_path / name).write_bytes((FIX / name).read_bytes())
    out = _spv_export_syntax(tmp_path / "logs_only.spv")
    assert Path(out) == tmp_path / "code" / "logs_only.sps"  # joined with "/", as R does
    assert Path(out).read_text() == "GET FILE='data.sav'.\n\nFREQUENCIES age.\n"
    assert _spv_export_syntax(tmp_path / "empty.spv") is None
    with pytest.raises(FileNotFoundError):
        _spv_export_syntax(tmp_path / "nope.spv")


# -- HTML rendering --------------------------------------------------------------------


def test_display_value() -> None:
    assert _spv_display_value("0.840583589880873") == "0.841"
    assert _spv_display_value("397") == "397"
    assert _spv_display_value("4.7e-108") == "4.7e-108"
    assert _spv_display_value("-0.0001") == "-0.0001"
    assert _spv_display_value("1e20") == "100000000000000000000"
    assert _spv_display_value("abc") == "abc"
    assert _spv_display_value(None) == ""
    assert _spv_display_value(pd.NA) == ""
    assert _spv_display_value(0.5) == "0.500"


def test_html_escape() -> None:
    assert _spv_html_escape("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert _spv_html_escape(None) == ""


def test_table_html_pivot_and_flat() -> None:
    df = pd.DataFrame(
        {
            "G": pd.array(["g1", "g1", "g2"], dtype="string"),
            "S": pd.array(["N", "Mean", "N"], dtype="string"),
            "value": pd.array(["12", "3.14159", None], dtype="string"),
        }
    )
    flat = _spv_table_html(df)
    assert flat.startswith("<table>\n<thead><tr><th>G</th><th>S</th><th>value</th></tr></thead>")
    df.attrs.update({"spv_row_dims": ["G"], "spv_col_dims": ["S"]})
    html = _spv_table_html(df)
    assert "<tr><th>G</th><th>N</th><th>Mean</th></tr>" in html
    assert "<tr><td>g1</td><td>12</td><td>3.142</td></tr>" in html
    assert "<tr><td>g2</td><td></td><td></td></tr>" in html
    assert _spv_table_html(df.iloc[0:0]) == ""


def test_export_spv_html(tmp_path: Path) -> None:
    out = Path(quiet(export_spv_html, FIX / "charts.spv", tmp_path / "c.html"))
    html = out.read_text()
    # the case-data box plot decodes and is drawn too (U144)
    assert html.count('<img src="data:image/svg+xml;base64,') == 4
    assert "<h2>Graph</h2><h3>Weight by Height</h3>" in html
    copy = tmp_path / "e.SPV"
    copy.write_bytes((FIX / "empty.spv").read_bytes())
    default = export_spv_html(copy)
    assert default == str(tmp_path / "e.html")
    assert "No result tables could be recovered" in Path(default).read_text()
