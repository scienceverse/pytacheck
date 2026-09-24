"""Tests for the JASP (.jasp) reader (port of tests/testthat/test-jasp.R, plus extras)."""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.statout.jasp import (
    _dollar,
    _html_inline_images,
    _jasp_analyses_summary,
    _jasp_binary_labels,
    _url_decode,
    export_jasp_html,
    import_jasp,
)

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "archives"


@pytest.fixture
def sample_jasp(fixtures_dir: Path) -> Path:
    return fixtures_dir / "formats" / "sample.jasp"


def test_import_jasp_reads_a_real_binary_format_file(sample_jasp: Path) -> None:
    r = import_jasp(sample_jasp)
    assert r["format"] == "binary"
    assert r["data"].shape == (32, 40)
    assert "Age" in r["data"].columns


def test_import_jasp_attaches_haven_style_value_labels(sample_jasp: Path) -> None:
    r = import_jasp(sample_jasp)
    col_attrs = r["data"].attrs["col_attrs"]
    assert any("labels" in a for a in col_attrs.values())


def test_import_jasp_recovers_the_stored_analyses(sample_jasp: Path) -> None:
    r = import_jasp(sample_jasp)
    assert r["analyses"] is not None
    assert len(_jasp_analyses_summary(r["analyses"])) >= 1


def test_import_jasp_errors_on_a_non_jasp_file(tmp_path: Path) -> None:
    f = tmp_path / "not.jasp"
    f.write_text("not a zip\n")
    with pytest.raises(ValueError, match=re.escape("as a .jasp (zip) archive")):
        import_jasp(f)


def make_sqlite_jasp(d: Path) -> Path:
    """The minimal SQLite-format .jasp metacheck's test builds from the schema."""
    sq = d / "internal.sqlite"
    con = sqlite3.connect(sq)
    con.execute("CREATE TABLE Columns (id INT, name TEXT, columnType TEXT, colIdx INT, title TEXT)")
    con.execute(
        "INSERT INTO Columns VALUES (1,'grp','nominal',0,'grp'),(2,'score','scale',1,'Total')"
    )
    con.execute("CREATE TABLE DataSet_1 (rowNumber INT, Column_1_INT INT, Column_2_DBL REAL)")
    con.execute("INSERT INTO DataSet_1 VALUES (0,1,1.5),(1,2,2.5),(2,1,3.5)")
    con.execute("CREATE TABLE Labels (columnId INT, value INT, ordering INT, label TEXT)")
    con.execute("INSERT INTO Labels VALUES (1,1,0,'Control'),(1,2,1,'Treatment')")
    con.execute("CREATE TABLE DataSets (dataFilePath TEXT)")
    con.execute("INSERT INTO DataSets VALUES ('orig.csv')")
    con.commit()
    con.close()
    jasp = d / "sqlite.jasp"
    with zipfile.ZipFile(jasp, "w") as zf:
        zf.write(sq, "internal.sqlite")
    return jasp


def test_import_jasp_reads_the_modern_sqlite_format(tmp_path: Path) -> None:
    r = import_jasp(make_sqlite_jasp(tmp_path))
    assert r["format"] == "sqlite"
    assert len(r["data"]) == 3
    assert r["data"]["grp"].tolist() == [1, 2, 1]
    assert r["data"]["score"].tolist() == [1.5, 2.5, 3.5]
    col_attrs = r["data"].attrs["col_attrs"]
    assert col_attrs["grp"]["labels"] == {"Control": 1.0, "Treatment": 2.0}
    assert col_attrs["score"]["label"] == "Total"
    assert r["data_file_path"] == "orig.csv"
    assert "analyses" not in r


def test_sqlite_fixture_types_and_missing_codes() -> None:
    r = import_jasp(FIX / "sqlite.jasp")
    df = r["data"]
    assert list(df.columns) == ["grp", "score", "rating", "weight"]
    # non-scale columns come back as doubles (as.numeric), scale INT stays integer
    assert df["grp"].dtype == "float64"
    assert df["rating"].isna().tolist() == [False, True, False]
    assert str(df["weight"].dtype) == "Int64"
    assert r["columns"]["type"].tolist() == ["nominal", "scale", "ordinal", "scale"]


def test_binary_fixture_without_analyses() -> None:
    r = import_jasp(FIX / "binary.jasp")
    assert list(r) == ["data", "columns", "format", "data_file_path"]
    df = r["data"]
    assert df["id"].tolist() == [7, 8]
    assert pd.isna(df["rt"].iloc[1])
    assert df.attrs["col_attrs"]["rt"] == {"label": "Reaction time"}
    # R quirk: attr(x, "label") partially matches "labels"
    assert df.attrs["col_attrs"]["cond"] == {
        "labels": {"A": 1.0, "B": 2.0},
        "label": {"A": 1.0, "B": 2.0},
    }
    assert "id" not in df.attrs["col_attrs"]
    assert r["data_file_path"] == "C:/data/study.csv"


def test_import_jasp_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=re.escape("File not found")):
        import_jasp(tmp_path / "none.jasp")
    other = tmp_path / "x.txt"
    other.write_text("x")
    with pytest.raises(ValueError, match=re.escape("Not a .jasp file")):
        import_jasp(other)
    with pytest.raises(ValueError, match=re.escape("Unrecognised .jasp")):
        import_jasp(FIX / "nodata.jasp")


def test_jasp_binary_labels_falls_back_to_xdata() -> None:
    xdat = {"g": {"labels": [[1, "a"], ["2", "b"], ["x", "bad"], [4, ""]]}}
    assert _jasp_binary_labels({"name": "g", "labels": []}, xdat) == {"a": 1.0, "b": 2.0}
    assert _jasp_binary_labels({"name": "h"}, xdat) == {}
    assert _jasp_binary_labels({"name": "g", "labels": [[3, "own"]]}, xdat) == {"own": 3.0}
    # repeated codes are kept (R's named vector), a JSON null entry is an error
    assert _jasp_binary_labels({"labels": [[1, "one"], [1, "uno"]]}, {}) == {"one": 1.0, "uno": 1.0}
    with pytest.raises(ValueError, match="values must be length 1"):
        _jasp_binary_labels({"labels": [[1, None]]}, {})


def test_jasp_analyses_summary_shapes() -> None:
    assert _jasp_analyses_summary(None) == []
    nested = {"analyses": [{"title": "T-Test", "module": "jasp"}, {"name": "anova"}, {}]}
    assert _jasp_analyses_summary(nested) == [
        "1. T-Test  [module: jasp]",
        "2. anova",
        "3. analysis",
    ]
    # `$` partial matching, and scalars
    assert _jasp_analyses_summary([{"titles": "x"}, "plain", 2.5, True]) == [
        "1. x",
        "2. plain",
        "3. 2.5",
        "4. TRUE",
    ]


def test_dollar_partial_matching() -> None:
    assert _dollar({"title": 1, "titles": 2}, "title") == 1
    assert _dollar({"titles": 2}, "title") == 2
    assert _dollar({"titles": 2, "titlex": 3}, "title") is None
    assert _dollar([1, 2], "x") is None
    with pytest.raises(TypeError):
        _dollar("atomic", "x")


def test_export_jasp_html_inlines_images(tmp_path: Path) -> None:
    out = export_jasp_html(FIX / "binary.jasp", tmp_path / "out.html")
    html = Path(out).read_text()
    assert "data:image/gif;base64," in html
    assert html.endswith("\n")
    default = tmp_path / "copy.JASP"
    default.write_bytes((FIX / "binary.jasp").read_bytes())
    assert export_jasp_html(default) == str(tmp_path / "copy.html")


def test_html_inline_images_first_occurrence_only(tmp_path: Path) -> None:
    (tmp_path / "a b").mkdir()
    (tmp_path / "a b" / "p.png").write_bytes(b"png")
    html = '<img src="a%20b/p.png"><img src="a%20b/p.png"><img src="//x.org/p.png">'
    out = _html_inline_images(html, str(tmp_path))
    assert out.count("data:image/png;base64,cG5n") == 1
    assert out.count('src="a%20b/p.png"') == 1
    assert _url_decode("a%20b+c%2F") == "a b+c/"


def test_jasp_json_fixture_roundtrip(tmp_path: Path) -> None:
    meta = {"dataSet": {"rowCount": 2, "fields": [{"name": "x", "measureType": "Continuous"}]}}
    f = tmp_path / "m.jasp"
    with zipfile.ZipFile(f, "w") as zf:
        zf.writestr("metadata.json", json.dumps(meta))
        zf.writestr("data.bin", struct.pack("<2d", 1.0, float("nan")))
        zf.writestr("analyses.json", "{not json")
    r = import_jasp(f)
    assert r["data"]["x"].tolist()[0] == 1.0
    assert "analyses" not in r  # unparsable analyses.json -> NULL -> not set
