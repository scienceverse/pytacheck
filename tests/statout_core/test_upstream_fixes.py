"""metacheck bugs pytacheck fixes in the statistical-output core (docs/UPSTREAM_ISSUES.md)."""

from __future__ import annotations

from pytacheck.statout.r_output import _r_echo_chunks, _r_output_oneline, _r_output_tables
from pytacheck.statout.stat_output import _stat_result_ids, _stat_test_id, stat_output_validate
from pytacheck.statout.stat_tables import _ipynb_stat_line


def test_oneline_results_take_the_source_label() -> None:
    # U139: an untitled one-line result is labelled with source_label (the
    # script name), as read_r_output() documents; R's `cur_title %||% ...`
    # never falls back from NA
    got = _r_output_oneline(["t = 2.10, p = 0.048"], source_label="analysis.R")
    assert [r["analysis"] for r in got] == ["analysis.R"]
    assert got[0]["title"] is None
    titled = _r_output_oneline(["\tWelch Two Sample t-test", "t = 2.1, p = 0.04"], "a.R")
    assert titled[0]["analysis"] == "Welch Two Sample t-test"
    assert _r_output_oneline(["t = 2.1"], source_label=None)[0]["analysis"] is None


def test_notebook_lines_without_a_result_class_have_an_empty_call_fn() -> None:
    # U139: call_fn is "" when the line prints no *Result class (R: NA)
    got = _ipynb_stat_line(["t = 2.1, p = 0.04"])
    assert got is not None and got[0]["call_fn"] == ""
    got = _ipynb_stat_line(["TtestResult(statistic=4.89, pvalue=0.001)"])
    assert got is not None and got[0]["call_fn"] != ""


def test_echo_chunks_keep_output_lines_starting_with_plus() -> None:
    # U140: only the "+" lines right after the ">" line continue the statement
    chunks = _r_echo_chunks(["> f(", "+   x)", "[1] 1", "+ 3 is printed"], ["f(", "  x)"])
    assert chunks[0]["call"] == "f( x)"
    assert chunks[0]["output"] == ["[1] 1", "+ 3 is printed"]
    assert chunks[0]["line"] == 1


def test_combined_df_cells_keep_the_columns_aligned() -> None:
    # U140: "1,   39" keeps its width, so the columns after it stay aligned
    # (R collapses it to one no-break space and splits "df F ges" as one)
    lines = [
        "Response: rt",
        "     Effect      df  MSE       F  ges p.value",
        "1 condition 1,   39 0.03  110.93 .128   <.001",
        "2      task 2,  178 0.05    3.21 .010    .046",
    ]
    df = _r_output_tables(lines)[0]["data"]
    assert list(df.columns) == ["V1", "Effect", "df", "MSE", "F", "ges", "p.value"]
    assert df["df"].tolist() == ["1, 39", "2, 178"]
    assert df["F"].tolist() == ["110.93", "3.21"]


def test_ids_without_a_source_file_and_without_repeats() -> None:
    # U138: no "na_" prefix, no repeated source prefix, both underscores trimmed
    assert _stat_result_ids([{"table_index": 2}]) == ["result_t2"]
    assert _stat_result_ids([{"table_index": 2}], None) == ["result_t2"]
    assert _stat_test_id({}, "odd name.spv", "odd_name_spv_t3_r1", "a") == "odd_name_spv_t3_a"
    assert _stat_test_id({}, None, "result_t1_r2", "") == "result_t1"


def test_validate_accepts_a_json_string() -> None:
    # U136: the same document as a string validates like the file
    doc = (
        '{"schema": "metacheck-statistical-output", "schema_version": "1.0", '
        '"paper_id": "p", "source_file": "a.R", "source_format": "R", '
        '"analyses": [{"analysis": "A", "results": [{"result_id": "r1", '
        '"values": {"t": {"value": 2.1}}}]}]}'
    )
    res = stat_output_validate(doc)
    assert res["valid"] is True
    assert res["summary"] == {"n_errors": 0, "n_analyses": 1, "n_results": 1}
