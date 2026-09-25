"""Tests for the jamovi (.omv) reader (port of tests/testthat/test-omv.R, plus extras)."""

from __future__ import annotations

import json
import re
import struct
import zipfile
from pathlib import Path

import pytest

from pytacheck.statout.omv import _omv_extract_syntax, export_omv_html, import_omv

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "archives"


def make_omv(d: Path) -> Path:
    """The minimal but structurally real .omv metacheck's test builds."""
    meta = {
        "dataSet": {
            "rowCount": 3,
            "fields": [
                {
                    "name": "grp",
                    "dataType": "Integer",
                    "measureType": "Nominal",
                    "columnType": "Data",
                    "labels": [[1, "Control"], [2, "Treatment"]],
                },
                {
                    "name": "score",
                    "dataType": "Decimal",
                    "measureType": "Continuous",
                    "columnType": "Data",
                    "description": "Total score",
                },
                {
                    "name": "note",
                    "dataType": "Text",
                    "measureType": "Nominal",
                    "columnType": "Data",
                },
            ],
        }
    }
    data = (
        struct.pack("<3i", 1, 2, 1)
        + struct.pack("<3d", 1.5, 2.5, float("nan"))
        + struct.pack("<3i", 0, 1, 0)
    )
    blob = b"\x52\x01" + b"jmv::ttestIS(vars = vars(score), group = grp, students = TRUE)"
    omv = d / "fixture.omv"
    with zipfile.ZipFile(omv, "w") as zf:
        zf.writestr("metadata.json", json.dumps(meta))
        zf.writestr("xdata.json", "{}")
        zf.writestr("data.bin", data)
        zf.writestr("strings.bin", b"yes\x00no\x00")
        zf.writestr("01 ttestIS/analysis", blob)
    return omv


def test_import_omv_decodes_integer_decimal_and_text_columns(tmp_path: Path) -> None:
    r = import_omv(make_omv(tmp_path))
    assert r["format"] == "jamovi"
    assert len(r["data"]) == 3
    assert r["data"]["grp"].tolist() == [1, 2, 1]
    score = r["data"]["score"].tolist()
    assert score[:2] == [1.5, 2.5]
    assert r["data"]["score"].isna().tolist() == [False, False, True]  # NaN -> NA
    assert r["data"]["note"].tolist() == ["yes", "no", "yes"]  # strings.bin resolved


def test_import_omv_attaches_haven_style_labels_and_variable_label(tmp_path: Path) -> None:
    r = import_omv(make_omv(tmp_path))
    col_attrs = r["data"].attrs["col_attrs"]
    assert col_attrs["grp"]["labels"] == [("Control", 1.0), ("Treatment", 2.0)]
    assert col_attrs["score"]["label"] == "Total score"


def test_import_omv_recovers_the_analysis_r_syntax(tmp_path: Path) -> None:
    r = import_omv(make_omv(tmp_path))
    assert len(r["analyses"]) == 1
    a = r["analyses"][0]
    assert "ttestIS" in a
    assert "jmv::ttestIS(" in a
    assert "Rjmv" not in a  # framing byte stripped
    assert "vars(score)" in a  # nested paren kept


def test_import_omv_errors_on_a_non_omv_file(tmp_path: Path) -> None:
    f = tmp_path / "not.omv"
    f.write_text("not a zip\n")
    with pytest.raises(ValueError, match=re.escape("as a .omv (zip) archive")):
        import_omv(f)


def test_import_omv_reads_a_real_omv_file(fixtures_dir: Path) -> None:
    r = import_omv(fixtures_dir / "formats" / "sample.omv")
    assert r["format"] == "jamovi"
    assert r["data"].shape == (218, 9)
    assert len(r["analyses"]) >= 1
    assert any("jmv::" in a for a in r["analyses"])


def test_fixture_omv_analyses_order_and_labels() -> None:
    r = import_omv(FIX / "fixture.omv")
    assert r["analyses"] == [
        "1. 01 ttestIS  |  jmv::ttestIS(vars = vars(score), group = grp, students = TRUE)",
        "2. 02 descriptives",
        "3. 10 empty",
    ]
    df = r["data"]
    assert df["note"].tolist()[:2] == ["yes", "no"]
    assert df["note"].isna().tolist() == [False, False, True]
    assert df.attrs["col_attrs"]["code"] == {
        "labels": [("low", 1.0), ("high", 2.0)],
        "label": "Response code",
    }
    assert r["data_file_path"] is None


def test_omv_extract_syntax() -> None:
    assert (
        _omv_extract_syntax("R\x01jmv::ttestIS(vars = vars(score))")
        == "jmv::ttestIS(vars = vars(score))"
    )
    assert (
        _omv_extract_syntax("xx pkg.name::fn_1 (a, (b), c) trailing )")
        == "pkg.name::fn_1 (a, (b), c)"
    )
    assert _omv_extract_syntax("no call") == ""
    assert _omv_extract_syntax("a::b(unbalanced") == ""
    assert _omv_extract_syntax("  jmv::anova(\t f =   y,\n d)  ") == "jmv::anova( f = y, d)"


def test_import_omv_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        import_omv(tmp_path / "none.omv")
    with pytest.raises(ValueError, match=re.escape("Unrecognised .omv")):
        import_omv(FIX / "nodata.omv")


def test_export_omv_html(tmp_path: Path) -> None:
    out = Path(export_omv_html(FIX / "fixture.omv", tmp_path / "o.html"))
    html = out.read_text()
    # the first occurrence of each distinct src only (the second stays a path)
    assert html.count("data:image/png;base64,iVBORw0KGgpmYWtlcG5n") == 1
    assert 'src="01%20ttestIS/resources/plot.png"' in html
    assert "data:image/jpeg;base64," in html
    assert 'src="missing.png"' in html
    with pytest.raises(ValueError, match=re.escape("No 'index.html'")):
        export_omv_html(FIX / "nodata.omv", tmp_path / "x.html")
