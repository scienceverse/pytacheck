"""Tests for pytacheck.statout.stat_output (port of R/stat-output.R)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.statout.stat_output import (
    _stat_is_placeholder,
    _stat_result_ids,
    _stat_sanitize_id,
    _stat_test_id,
    _to_json_pretty,
    stat_output_json,
    stat_output_validate,
    stat_output_write,
    stat_results_long,
)
from pytacheck.statout.stat_tables import read_stat_tables


def test_sanitize_id() -> None:
    assert _stat_sanitize_id("Sample.JASP") == "sample_jasp"
    assert _stat_sanitize_id("  Cohen's d ") == "cohen_s_d"
    # R's sub("^_|_$", "", x) removes only one of them
    assert _stat_sanitize_id("_abc_") == "abc_"
    assert _stat_sanitize_id("η²") == ""
    assert _stat_sanitize_id(None) is None


def test_result_and_test_ids() -> None:
    tables = [
        {"line": 5, "line_seq": 2},
        {"table_index": 3},
        {"analysis": "Descriptives"},
        {"analysis": "Descriptives"},
        {"analysis": ""},
        {"line": None, "table_index": None},
    ]
    assert _stat_result_ids(tables, "script.R") == [
        "script_r_l5_2",
        "script_r_t3",
        "script_r_Descriptives_1",
        "script_r_Descriptives_2",
        "script_r_result_1",
        "script_r_result_2",
    ]
    # R's default source_file is NA, which pastes as "NA"
    assert _stat_result_ids([{"table_index": 1}]) == ["NA_t1"]
    assert _stat_test_id({"analysis_id": "4"}, "a.jasp", "a_jasp_t1", "x") == "a_jasp_a4_x"
    assert _stat_test_id({"line": 3}, "s.R", "s_r_l3_1", "") == "s_r_l3_1"
    assert _stat_test_id({}, "s.R", "s_r_t2_r1", "grp A") == "s_r_s_r_t2_grp_a"


def test_placeholders() -> None:
    for x in [".", "-", "—", " NA ", "NaN", "null", "n/a", ""]:
        assert _stat_is_placeholder(x), x
    for x in ["0", "a", None, "< .001"]:
        assert not _stat_is_placeholder(x), x


def test_empty_inputs() -> None:
    long = stat_results_long(None)
    assert list(long.columns) == [
        "paper_id",
        "source_file",
        "test_id",
        "result_id",
        "analysis",
        "table_title",
        "row_label",
        "statistic",
        "stato_label",
        "stato_iri",
        "value",
        "model_ref",
    ]
    assert len(long) == 0
    assert len(stat_results_long([])) == 0
    assert stat_output_json(None) is None
    assert stat_output_json([]) is None


def test_stat_results_long_and_json(fixtures_dir: Path, stato: None) -> None:
    tabs = read_stat_tables(fixtures_dir / "formats" / "sample.jasp")
    long = stat_results_long(tabs, paper_id="p", source_file="sample.jasp")
    assert set(long["paper_id"]) == {"p"}
    t_rows = long[long["statistic"] == "t"]
    assert t_rows["result_id"].tolist() == ["sample_jasp_t2_r1_t", "sample_jasp_t2_r2_t"]
    assert t_rows["test_id"].str.startswith("sample_jasp_a0_").all()
    doc = stat_output_json(tabs, paper_id="p", source_file="sample.jasp")
    assert doc["source_format"] == "JASP"
    assert doc["analyses"][1]["results"][0]["values"]["t"]["value"] == pytest.approx(
        6.89966035949175
    )
    assert stat_output_validate(doc)["valid"]


def test_stat_output_validate_shapes(data_dir: Path) -> None:
    ok = stat_output_validate(str(data_dir / "stat_output.json"))
    assert ok["valid"] and ok["issues"] == []
    assert ok["summary"]["n_analyses"] == 2  # one entry per table

    mixed = stat_output_validate(str(data_dir / "validate_mixed.json"))
    assert not mixed["valid"]
    assert 'Result "r1": value "p" is missing `value`.' in mixed["issues"]
    assert "A result is missing `result_id`." in mixed["issues"]
    assert "An analysis entry has no `results`." in mixed["issues"]

    missing = stat_output_validate(str(data_dir / "validate_missing_top.json"))
    assert missing["issues"][0] == (
        "Document missing top-level fields: schema_version, paper_id, source_file, "
        "source_format, analyses."
    )
    # metacheck reads JSON *strings* through a text connection jsonlite cannot
    # read, so every JSON string is reported as invalid JSON
    as_string = stat_output_validate('{"schema": "x"}')
    assert as_string == {
        "valid": False,
        "issues": ["Input is not valid JSON."],
        "summary": {"n_errors": 1, "n_analyses": 0, "n_results": 0},
    }
    with pytest.raises(TypeError, match="atomic"):
        stat_output_validate(str(data_dir / "validate_analyses_string.json"))
    listed = stat_output_validate({"schema": "x", "analyses": []})
    assert listed["summary"] == {"n_errors": 1, "n_analyses": 0, "n_results": 0}


def test_json_pretty_matches_jsonlite(data_dir: Path) -> None:
    doc = json.loads((data_dir / "stat_output.json").read_text(encoding="utf-8"))
    expected = (data_dir / "stat_output.json").read_text(encoding="utf-8").rstrip("\n")
    assert _to_json_pretty(doc) == expected
    assert _to_json_pretty({"a": [], "b": {}, "c": None, "d": 1e15, "e": 1 / 3}) == (
        '{\n  "a": [],\n  "b": {},\n  "c": null,\n  "d": 1e+15,\n  "e": 0.333333333333333\n}'
    )


def test_stat_output_write(tmp_path: Path) -> None:
    long = pd.DataFrame(
        {
            "paper_id": pd.Series(["p", None], dtype="string"),
            "value": pd.Series(['say "hi"', "2"], dtype="string"),
        }
    )
    doc = {"schema": "metacheck-statistical-output", "analyses": [], "paper_id": None}
    out = stat_output_write(
        [
            {"file": "dir/sample.jasp", "json": doc, "long": long},
            {"file": "empty.R", "json": None, "long": long.iloc[0:0]},
        ],
        tmp_path,
    )
    assert out == str(tmp_path / "statistical_output")
    csv = (tmp_path / "statistical_output" / "results_long.csv").read_text()
    assert csv == '"paper_id","value"\n"p","say ""hi"""\n,"2"\n'
    js = (tmp_path / "statistical_output" / "sample.statistical_output.json").read_text()
    assert js == (
        '{\n  "schema": "metacheck-statistical-output",\n  "analyses": [],\n  "paper_id": null\n}\n'
    )
    assert stat_output_write(None, tmp_path / "x") is None
    assert stat_output_write([{"file": "a", "json": None, "long": None}], tmp_path / "y") is None
    assert not (tmp_path / "y").exists()
