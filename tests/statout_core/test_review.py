"""Regression tests for the statout_core review (R quirks the first port missed)."""

from __future__ import annotations

import json
import warnings
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.statout.r_output import (
    _r_as_numeric,
    _r_dollar,
    _r_dollar_found,
    _r_echo_chunks,
    _r_values,
    _RError,
    _RNamedList,
)
from pytacheck.statout.stat_output import (
    _stat_result_ids,
    _stat_sanitize_id,
    stat_output_validate,
    stat_results_long,
)
from pytacheck.statout.stat_tables import (
    _ipynb_text,
    _parse_json_text,
    _pb_fields,
    _pb_str,
    _pb_varint,
    _read_html_file,
    _stat_table_parse,
    _xml_text,
    read_stat_tables,
)
from tests.statout_core.review_helpers import chr_frame, parse_html_tables

DATA = Path(__file__).resolve().parent / "data"


# -- R list semantics ------------------------------------------------------------


def test_dollar_partial_matching() -> None:
    x = {"line_seq": 2, "analysis_id": "a"}
    assert _r_dollar(x, "line") == 2  # unique partial match
    assert _r_dollar(x, "analysis") == "a"
    assert _r_dollar({"line": 1, "line_seq": 2}, "line") == 1  # exact wins
    assert _r_dollar({"ab": 1, "abc": 2}, "a") is None  # ambiguous partial
    assert _r_dollar_found({"a": None}, "a") == (True, None)
    assert _r_dollar_found(None, "a") == (False, None)
    assert _r_dollar_found([1, 2], "a") == (False, None)
    with pytest.raises(_RError, match="atomic"):
        _r_dollar("x", "a")


def test_json_keeps_duplicate_names_and_bom() -> None:
    obj = _parse_json_text('{"a": 1, "a": 2, "b": {"c": [1, 2147483648]}}')
    assert isinstance(obj, _RNamedList)
    assert obj["a"] == 1 and _r_values(obj) == [1, 2, obj["b"]]
    assert obj.pairs[1] == ("a", 2)
    # jsonlite: integers beyond 32 bits are doubles
    assert _r_values(obj["b"]["c"]) == [1, 2147483648.0]
    assert isinstance(obj["b"]["c"][1], float)
    with pytest.warns(UserWarning, match="byte-order-mark"):
        assert _parse_json_text("﻿[1]") == [1]
    with pytest.raises(ValueError):
        _parse_json_text("[NaN]")


def test_unlist_coercion_of_notebook_text() -> None:
    assert _ipynb_text([1, True, 2.5]) == "112.5"
    assert _ipynb_text(["t = ", 2.5, None, True]) == "t = 2.5TRUE"
    assert _ipynb_text([100000, 1]) == "1000001"
    assert _ipynb_text([100000.0, "x"]) == "1e+05x"
    assert _ipynb_text([True, False]) == "TRUEFALSE"


# -- numbers, raw bytes ----------------------------------------------------------


def test_as_numeric_hex_forms() -> None:
    got = [_r_as_numeric(x) for x in ["0x.8", "0x.", "0xp1", "0x", "0x1p", "-0x10", " 0X1A "]]
    assert got == [0.5, 0.0, 0.0, None, None, -16.0, 26.0]


def test_pb_str_follows_rawtochar() -> None:
    assert _pb_str(b"a\x00") == "a"  # trailing nuls are dropped
    assert _pb_str(b"a\x00\x00") == "a"
    assert _pb_str(b"\x00\x00") == ""
    assert _pb_str(b"a\x00b") == ""  # embedded nul: error -> ""
    assert _pb_str(b"\x00a") == ""
    assert _pb_str(2.0) == ""


def test_pb_reader_accepts_bytes_and_numbers() -> None:
    assert _pb_varint(b"\x96\x01", 1) == {"value": 150.0, "pos": 3}
    assert _pb_fields(b"") == []
    # a number handed back to .pb_fields(): as.integer() truncates
    assert _pb_fields(5.7) is None
    with pytest.raises(_RError):
        _pb_fields(2.0**40)


# -- archives and HTML -----------------------------------------------------------


def test_unzip_quirks(tmp_path: Path) -> None:
    # an empty archive or a directory has no tables (R: basename(NULL) fails, U137)
    assert read_stat_tables(DATA / "review_empty.jasp") == []
    assert read_stat_tables(tmp_path) == []
    assert read_stat_tables(DATA / "review_dirs_only.omv") == []
    # an unreadable entry stops extraction but keeps what came before it
    bad = tmp_path / "bad.jasp"
    with zipfile.ZipFile(bad, "w", zipfile.ZIP_STORED) as z:
        z.writestr("index.html", (DATA / "review_tables.html").read_bytes())
        z.writestr("zzz", b"payload")
    raw = bytearray(bad.read_bytes())
    pos = raw.rindex(b"payload")
    raw[pos] ^= 0xFF  # CRC mismatch in the second entry
    bad.write_bytes(bytes(raw))
    assert len(read_stat_tables(bad)) > 0


def test_html_is_decoded_in_its_declared_encoding() -> None:
    # without a declaration: UTF-8
    tabs = read_stat_tables(DATA / "review_nometa.jasp")
    assert tabs[0]["analysis"] == "Tést α"
    assert list(tabs[0]["data"].columns) == ["V1", "éffect", "p"]
    # <meta charset="latin1"> is honoured (U141; R's xml2 on libxml2 2.15
    # ignores it and reads "T\ufffdst")
    latin = read_stat_tables(DATA / "review_latin.jasp")
    assert latin[0]["analysis"] == "Tést"
    assert list(latin[0]["data"].columns) == ["a", "bé"]


def test_rows_without_content_are_dropped() -> None:
    first = parse_html_tables(DATA / "review_tables.html")[0]
    df = first["data"]
    # the <tr><td></td></tr> rows have no content and are dropped (R's
    # df[NA, ] turns them into rows of NAs, U141); the note row stays
    assert df["V1"].tolist() == ["x", "Note.", "y"]
    assert not df.isna().all(axis=1).any()
    doc = _read_html_file(str(DATA / "review_tables.html"))
    assert _xml_text(doc.xpath("//h2")[0]) == "T2 colspans"
    assert _stat_table_parse(doc.xpath("//table")[-1]) is None


# -- results tables ----------------------------------------------------------------


def test_result_ids_use_exact_names(stato: None) -> None:
    # table fields are matched by their exact names (R's tb$line also takes
    # line_seq, and tb$data takes data_x, U141)
    tables = [
        {"data": chr_frame(t=["1"]), "line_seq": 3},
        None,
        {"data_x": chr_frame(t=["1"]), "table_ind": 4, "analysis": "A b"},
    ]
    assert _stat_result_ids(tables, "x.R") == ["x_r_result_1", "x_r_result_2", "x_r_A b"]
    long = stat_results_long(tables, source_file="x.R")
    assert list(long["result_id"]) == ["x_r_result_1_r1_t"]


def test_sanitize_uses_r_tolower() -> None:
    assert _stat_sanitize_id("İstanbul Data.R") == "istanbul_data_r"
    assert _stat_sanitize_id("_abc_") == "abc"
    assert _stat_sanitize_id(None) is None


def test_numeric_paper_id_column_type(stato: None) -> None:
    long = stat_results_long(
        [{"data": chr_frame(p=["0.5"]), "table_index": 1}], paper_id=12, source_file="a.jasp"
    )
    assert long["paper_id"].dtype == "Int64"
    assert long["source_file"].dtype == "string"


# -- stat_output_validate ------------------------------------------------------------


def test_validate_json_quirks() -> None:
    null = stat_output_validate(str(DATA / "review_validate_null.json"))
    assert null["issues"] == ["Input is not valid JSON."]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bom = stat_output_validate(str(DATA / "review_validate_bom.json"))
    assert bom["valid"] and bom["summary"]["n_results"] == 1
    partial = stat_output_validate(str(DATA / "review_validate_partial.json"))
    # "analysesX", "resultsZ", "valuesQ", {"values": 1} all match through `$`
    assert partial["issues"] == [
        "Document missing top-level field: analyses.",
        'Result "r1": value "p" is missing `value`.',
    ]
    dup = stat_output_validate(str(DATA / "review_validate_dupkeys.json"))
    assert dup["issues"] == ['Result "r1": value "t" is missing `value`.'] * 2


def test_validate_na_is_not_null() -> None:
    doc = json.loads((DATA / "review_validate_bom.json").read_text(encoding="utf-8-sig"))
    doc["analyses"][0]["analysis"] = None  # NA_character_ in R, not NULL
    doc["analyses"][0]["results"][0]["values"]["t"]["value"] = None
    assert stat_output_validate(doc)["valid"]


# -- echo chunks ------------------------------------------------------------------


def test_echo_chunks_first_matching_line() -> None:
    lines = ["> x <- 1", "> x", "[1] 1", "> x", "[1] 1"]
    chunks = _r_echo_chunks(lines, ["x<-1", "x", "x"])
    assert [c["line"] for c in chunks] == [2, 2]


# -- capture runner == metacheck's callr runner ------------------------------------


@pytest.mark.r
@pytest.mark.parametrize("script", ["review_adversarial", "review_capture", "review_shadow"])
def test_capture_runner_matches_metacheck(script: str, reference_rscript: str, stato: None) -> None:
    """The Python capture runner reproduces metacheck's callr runner.

    ``data/review_capture_expected/<script>.json`` (``make_capture_expected.R``) holds what metacheck's own
    ``.r_capture_runner()`` (run with ``callr::r()``) printed and captured,
    plus ``.r_captures_to_tables()`` / ``.r_merge_captures()`` of it, in the
    parity harness' canonical encoding.
    """
    from parity.canonical import canonical
    from parity.compare import Options, compare
    from pytacheck.statout.r_capture import (
        _r_capture_run,
        _r_captures_to_tables,
        _r_merge_captures,
    )
    from pytacheck.statout.r_output import read_r_output
    from tests.statout_core._helpers import read_lines

    expected = json.loads(
        (DATA / "review_capture_expected" / f"{script}.json").read_text(encoding="utf-8")
    )
    path = DATA / f"{script}.R"
    res = _r_capture_run(path, rscript=reference_rscript)
    assert res.stdout + "\n" == expected["stdout"]
    if expected["error"] is None:
        assert res.error is None
    else:
        assert str(res.error) == expected["error"]
    code = read_lines(path)
    tabs = _r_captures_to_tables(res.captures, code_lines=code)
    merged = _r_merge_captures(tabs, read_r_output(res.stdout, code_lines=code))
    got = json.loads(json.dumps(canonical({"tabs": tabs, "merged": merged})))
    # the afex table's combined df cells keep their width, so its df/F/ges
    # columns split apart (R: one "df     F  ges" column, U140)
    diffs = compare(expected["tables"], got, Options())
    assert [d for d in diffs if "'df     F  ges'" not in d] == []
    assert isinstance(merged[0]["data"], pd.DataFrame)
