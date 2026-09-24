"""Qualtrics / header-repair / trial-format / block detection, and the scales and tasks data.

Ports the Qualtrics tests of tests/testthat/test-data-checks.R and the
dictionary checks of tests/testthat/test-module-codebook_check.R; the rest
cover the helpers' documented behaviour (R's results are pinned by the
datacheck_checks parity cases).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.datacheck.checks import (
    _bh_is_trial_level_file,
    _detect_accuracy_blocks,
    _detect_header_row,
    _detect_scale_blocks,
    _detect_task_columns,
    _eprime_is_export,
    _is_likert_item,
    _is_placeholder_name,
    _is_task_data,
    _looks_like_accuracy,
    _looks_like_rt,
    _qualtrics_col_stem,
    _qualtrics_is_display_order,
    _qualtrics_is_header_row,
    _qualtrics_key,
    _qualtrics_tag_cols,
    _scale_block_is_ratinglike,
    _scale_block_range,
    _scale_name_prefix,
    _tabular_usable,
    data_check_is_behaverse,
    data_check_is_inquisit,
    data_check_is_jspsych,
    data_check_is_psychopy,
    data_check_is_qualtrics,
    data_promote_header_row,
    data_strip_qualtrics_header,
)
from pytacheck.datacheck.scales import scales
from pytacheck.datacheck.tasks import tasks

DATA = Path(__file__).parent / "data"
ROOT = Path(__file__).resolve().parents[2]

# -- Qualtrics ---------------------------------------------------------------------------


def test_is_qualtrics_fires_on_metadata_column_set() -> None:
    q = pd.DataFrame(
        {
            "StartDate": ["2021-01-01"],
            "EndDate": ["2021-01-01"],
            "Progress": [100],
            "Duration (in seconds)": [300],
            "Finished": [1],
            "ResponseId": ["R_abc123de"],
            "Q1": [3],
        }
    )
    assert data_check_is_qualtrics(q)
    assert not data_check_is_qualtrics(
        pd.DataFrame({"id": [1, 2, 3], "StartDate": ["a", "b", "c"], "score": [1, 2, 3]})
    )
    assert not data_check_is_qualtrics(pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}))
    assert not data_check_is_qualtrics(None)
    assert not data_check_is_qualtrics(pd.DataFrame())


def _long_survey() -> pd.DataFrame:
    meta = ["StartDate", "EndDate", "Progress", "Duration (in seconds)", "Finished",
            "RecordedDate", "ResponseId"]  # fmt: skip
    items = [f"q_{i}" for i in range(1, 61)]
    nm = meta + items
    question_row = ["Start Date", "End Date", "Progress", "Duration (in seconds)", "Finished",
                    "Recorded Date", "Response ID"] + [f"Question text for item {i}" for i in range(1, 61)]  # fmt: skip
    import_row = [f'{{"ImportId":"{n}"}}' for n in nm]
    data = [["x"] * 7 + [str((i + k) % 7 + 1) for i in range(60)] for k in range(3)]
    return pd.DataFrame([question_row, import_row, *data], columns=nm)


def test_strip_qualtrics_header_strips_both_rows_on_a_long_survey() -> None:
    q = _long_survey()
    before = q.copy()
    assert data_check_is_qualtrics(q)
    stripped = data_strip_qualtrics_header(q)
    assert len(stripped) == len(q) - 2
    assert pd.api.types.is_float_dtype(stripped["q_1"])
    assert list(stripped.index) == [0, 1, 2]
    pd.testing.assert_frame_equal(q, before)  # the input is not modified


def test_is_qualtrics_corroborates_thin_export_via_response_id() -> None:
    q = pd.DataFrame(
        {
            "StartDate": ["2021-01-01 10:00:00"] * 3,
            "Progress": [100, 100, 50],
            "ResponseId": ["R_abc123de", "R_xyz789gh", "R_qwe456rt"],
            "Q1": [1, 2, 3],
        }
    )
    assert data_check_is_qualtrics(q)
    q["ResponseId"] = ["1", "2", "R_x"]
    assert not data_check_is_qualtrics(q)


def test_strip_qualtrics_header_removes_rows_and_retypes() -> None:
    df = pd.DataFrame(
        {
            "Progress": ["Progress", '{"ImportId":"progress"}', "100", "100"],
            "Duration (in seconds)": [
                "Duration (in seconds)",
                '{"ImportId":"duration"}',
                "300",
                "120",
            ],
            "ResponseId": ["Response ID", '{"ImportId":"_recordId"}', "R_abc", "R_xyz"],
        }
    )
    out = data_strip_qualtrics_header(df)
    assert len(out) == 2
    assert out["Duration (in seconds)"].tolist() == [300.0, 120.0]
    assert out["Duration (in seconds)"].dtype == "float64"
    assert out["ResponseId"].tolist() == ["R_abc", "R_xyz"]
    # nothing to strip -> the same object back
    plain = pd.DataFrame({"a": ["1", "2"], "b": ["x", "y"]})
    assert data_strip_qualtrics_header(plain) is plain
    assert data_strip_qualtrics_header(None) is None


def test_qualtrics_helpers() -> None:
    assert _qualtrics_key("Duration (in seconds)") == "durationinseconds"
    assert _qualtrics_key(["Duration..in.seconds.", None]) == ["durationinseconds", None]
    assert _qualtrics_tag_cols(["StartDate", "Q1", "Location Latitude"]) == [
        "qualtrics_start", None, "qualtrics_lat",
    ]  # fmt: skip
    assert _qualtrics_col_stem("TIPI_1") == "TIPI"
    assert _qualtrics_col_stem("a_b_12") == "a_b"
    assert _qualtrics_col_stem("x_1") is None  # stem needs two letters
    assert _qualtrics_col_stem("Duration_1") == "Duration"
    assert _qualtrics_col_stem("StartDate") is None
    assert _qualtrics_col_stem(None) is None
    assert _qualtrics_is_display_order(["Q1_DO_1", "Q1_DO", "Q1_DOG", "doable"]) == [
        True, True, False, False,
    ]  # fmt: skip
    assert _qualtrics_is_header_row(['{"ImportId":"x"}', "a"])
    assert _qualtrics_is_header_row(["Start Date", "Response ID"])
    assert not _qualtrics_is_header_row(["Start Date", "x"])
    assert not _qualtrics_is_header_row(["", None, "  "])
    assert not _qualtrics_is_header_row(["Start Date", "End Date", "Progress", "Q1 text"])


# -- header repair -----------------------------------------------------------------------

CDA_RAW = [
    ["CDA", "CDA", "CDA", "CDA", "CDA"],
    ["Participant", "Reject", "Condition", "-100", "0"],
    ["1", "0", "1", "0.5", "0.25"],
    ["2", "1", "2", "0.7", "-0.1"],
    ["3", "0", "1", "0.2", "0.3"],
]


def test_detect_header_row_strips_a_banner() -> None:
    det = _detect_header_row(CDA_RAW)
    assert det["header_row"] == 1  # 0-based: R's header_row 2
    assert det["stripped"] == [CDA_RAW[0]]
    assert det["improved"] == pytest.approx(0.6)
    # a correct header, a headerless numeric file and an all-text table are left alone
    ok = [["id", "score", "grp"], ["1", "2", "a"], ["2", "3", "b"]]
    assert _detect_header_row(ok)["header_row"] == 0
    numeric = [["1", "1", "3"], ["2", "2", "4"], ["3", "5", "6"]]
    assert _detect_header_row(numeric)["header_row"] == 0
    text = [["", "", ""], ["term", "def", "note"], ["a", "b", "c"]]
    assert _detect_header_row(text)["header_row"] == 0


def test_promote_header_row() -> None:
    cda = pd.DataFrame(
        {f"CDA...{j + 1}": [row[j] for row in CDA_RAW[1:]] for j in range(5)}
    )  # fmt: skip
    before = cda.copy()
    out = data_promote_header_row(cda, raw_rows=CDA_RAW)
    assert out["promoted"] == 1
    assert list(out["df"].columns) == ["Participant", "Reject", "Condition", "-100", "0"]
    assert len(out["df"]) == 3
    assert out["df"]["Participant"].tolist() == [1.0, 2.0, 3.0]
    pd.testing.assert_frame_equal(cda, before)

    # the fallback path (the reader's names as the first candidate row)
    fb = data_promote_header_row(cda)
    assert fb["promoted"] == 1
    assert list(fb["df"].columns) == ["Participant", "Reject", "Condition", "-100", "0"]

    plain = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    res = data_promote_header_row(plain)
    assert res["df"] is plain and res["promoted"] == 0 and res["stripped"] == []
    assert data_promote_header_row(None)["df"] is None


def test_promote_header_row_names_are_unique() -> None:
    df = pd.DataFrame({"...1": ["id", "1", "2"], "...2": ["id", "3", "4"], "...3": ["", "5", "6"]})
    out = data_promote_header_row(df)
    assert list(out["df"].columns) == ["id", "id.1", "V3"]


def test_placeholder_names() -> None:
    assert _is_placeholder_name(["...1", "CDA...4", "V3", "X", "X.1", "col_2", "Unnamed: 2",
                                 "unnamed.3", "", "id", "Value", "V", "Xa"]) == [
        True, True, True, True, True, True, True, True, True, False, False, False, False,
    ]  # fmt: skip


# -- trial-level formats ------------------------------------------------------------------


def test_trial_level_format_detectors() -> None:
    assert data_check_is_behaverse(pd.DataFrame({"instrument_id": ["X"], "trial_index": [1]}))
    assert data_check_is_behaverse(
        pd.DataFrame({"a_response_numeric_i1": [1], "a_trial_index_i1": [1]})
    )
    assert not data_check_is_behaverse(pd.DataFrame({"instrument_id": ["X"], "score": [1]}))
    assert data_check_is_inquisit(
        pd.DataFrame({"subject": [1], "BlockCode": ["a"], "trialcode": ["b"]})
    )
    assert not data_check_is_inquisit(pd.DataFrame({"subject": [1], "latency": [300]}))
    assert data_check_is_jspsych(
        pd.DataFrame({"trial_type": ["x"], "rt": [1], "time_elapsed": [2]})
    )
    assert not data_check_is_jspsych(pd.DataFrame({"trial_type": ["x"], "rt": [1]}))
    assert data_check_is_psychopy(pd.DataFrame({"trials.thisN": [0]}))
    assert data_check_is_psychopy(pd.DataFrame({"text.started": [1.5]}))
    assert data_check_is_psychopy(pd.DataFrame({"expName": ["x"]}))
    assert not data_check_is_psychopy(pd.DataFrame({"trials_thisN": [0]}))
    for fn in (data_check_is_behaverse, data_check_is_inquisit, data_check_is_jspsych,
               data_check_is_psychopy):  # fmt: skip
        assert not fn(None)
        assert not fn(pd.DataFrame())


def test_trial_level_files() -> None:
    assert _eprime_is_export(DATA / "eprime.txt")
    assert _eprime_is_export(DATA / "eprime_utf16.txt")
    assert not _eprime_is_export(DATA / "notes.txt")
    assert _bh_is_trial_level_file(DATA / "eprime.txt")
    assert _bh_is_trial_level_file(DATA / "jspsych.csv")
    assert _bh_is_trial_level_file(DATA / "jspsych_bom.csv")
    assert _bh_is_trial_level_file(DATA / "inquisit.iqdat")
    assert _bh_is_trial_level_file(DATA / "behaverse.csv")
    assert not _bh_is_trial_level_file(DATA / "plain.csv")
    assert not _bh_is_trial_level_file(DATA / "psychopy.csv")  # not a Behaverse accumulator format
    assert not _bh_is_trial_level_file(DATA / "nope.csv")
    assert not _bh_is_trial_level_file(DATA)
    assert not _bh_is_trial_level_file(None)


# -- blocks ----------------------------------------------------------------------------------


def test_item_value_tests() -> None:
    assert _is_likert_item([*range(1, 6)] * 4)
    assert not _is_likert_item([1, 2, 3, 4, 5])  # too few
    assert not _is_likert_item([0, 1] * 10)  # binary
    assert _is_likert_item([str(i) for i in range(1, 6)] * 4)
    assert _looks_like_rt([300 + i * 23 for i in range(1, 41)])
    assert _looks_like_rt([0.3 + i * 0.037 for i in range(1, 41)])
    assert not _looks_like_rt([*range(1, 6)] * 8)
    assert _looks_like_accuracy([0, 1] * 5)
    assert _looks_like_accuracy([i / 11 for i in range(1, 11)])
    assert not _looks_like_accuracy([True, False])


def _task_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [i for i in range(1, 5) for _ in range(10)],
            "trial": [*range(1, 11)] * 4,
            "stroop_rt": [400 + (i * 37) % 500 for i in range(1, 41)],
            "stroop_correct": [0.0, 1.0, 1.0, 1.0] * 10,
            "condition": ["congruent", "incongruent"] * 20,
            "RT_mean": [*range(1, 6)] * 8,
        }
    )


def test_detect_task_columns() -> None:
    tc = _detect_task_columns(_task_frame())
    assert tc["column_name"].tolist() == ["stroop_rt", "stroop_correct", "condition"]
    assert tc["kind"].tolist() == ["rt", "accuracy", "condition"]
    assert tc["by_name"].tolist() == [True, True, True]
    assert tc["by_value"].tolist() == [True, True, False]
    assert _is_task_data(_task_frame())
    empty = _detect_task_columns(None)
    assert list(empty.columns) == ["column_name", "kind", "by_name", "by_value"]
    assert len(empty) == 0
    assert not _is_task_data(pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}))


def test_detect_blocks() -> None:
    likert = [*range(1, 6)] * 4
    df = pd.DataFrame(
        {
            "id": list(range(20)),
            **{f"panas_{i}": likert for i in range(1, 5)},
            **{f"rse{i}": likert for i in range(1, 3)},
            "note": ["x"] * 20,
            **{f"raven_{i}": [0, 1] * 10 for i in range(1, 9)},
        }
    )
    assert _detect_scale_blocks(df) == [[1, 2, 3, 4]]  # 0-based positions
    assert _detect_scale_blocks(df, min_items=2) == [[1, 2, 3, 4], [5, 6]]
    assert _detect_accuracy_blocks(df) == [list(range(8, 16))]
    assert _detect_accuracy_blocks(df, min_items=9) == []
    assert _detect_accuracy_blocks(None) == []
    assert _scale_name_prefix("bfi_1") == "bfi"
    assert _scale_name_prefix(["RSE10", "PANAS.3", "Q1_2_"]) == ["rse", "panas", "q1_2"]
    assert _scale_block_range(df[["panas_1", "panas_2"]]) == "1-5"
    assert _scale_block_range(pd.DataFrame({"a": ["x"]})) == "?"


def test_scale_block_is_ratinglike() -> None:
    cols = pd.DataFrame(
        {
            "source_file": ["a.csv"] * 4 + ["b.csv"],
            "column_name": ["s_1", "s_2", "s_3", "s_4", "s_1"],
            "min": [0, 3, 1, 0, -50],
            "max": [95, 100, 71, 88, 10],
        }
    )
    assert _scale_block_is_ratinglike(["s_1", "s_2", "s_3"], "a.csv", cols)
    assert not _scale_block_is_ratinglike(["s_1", "s_2"], "a.csv", cols)  # too few items
    assert not _scale_block_is_ratinglike(["s_1", "s_2", "s_3"], "b.csv", cols)
    probs = cols.assign(max=[0.9, 0.5, 1.0, 0.7, 1.0])
    assert not _scale_block_is_ratinglike(["s_1", "s_2", "s_3"], "a.csv", probs)
    assert not _scale_block_is_ratinglike(["s_1"], "a.csv", None)


def test_tabular_usable() -> None:
    text = {"representation": "text", "concept": None}
    num = {"representation": "numeric", "concept": None}
    df = pd.DataFrame(
        {
            "note_a": ["some long free text here", "another comment entirely", "third remark"],
            "note_b": ["alpha beta gamma", "delta epsilon", "zeta eta theta"],
            "value": [1, 2, 3],
        }
    )
    r = _tabular_usable([text, text, text], df)
    assert r == {"usable": False, "reason": "100% of columns are free text, not variables"}
    assert _tabular_usable([text, text, num], df) == {"usable": True, "reason": None}
    assert _tabular_usable({"a": text, "b": text, "c": num}, df)["usable"]
    assert _tabular_usable([], df) == {
        "usable": False, "reason": "the file has no data rows or columns",
    }  # fmt: skip


# -- scales / tasks data ---------------------------------------------------------------------


def test_scales_data() -> None:
    s = scales()
    assert list(s.columns) == ["name", "acronym", "code", "source"]
    assert len(s) == 791
    assert set(s["source"]) <= {"openscales", "curated"}
    assert all(str(dt) == "string" for dt in s.dtypes)
    s.loc[0, "name"] = "changed"
    assert scales().loc[0, "name"] != "changed"  # a fresh copy every call


def test_tasks_data() -> None:
    t = tasks()
    assert list(t.columns) == [
        "code", "name", "acronym", "atlas_id", "description", "citation", "pmid", "url",
        "n_conditions", "n_contrasts", "n_computable", "indicators", "text_ok",
    ]  # fmt: skip
    assert len(t) == 833
    assert str(t["n_conditions"].dtype) == "Int64"
    assert str(t["text_ok"].dtype) == "boolean"
    # the codebook_check task matcher relies on colliding Stroop variants
    assert t["name"].str.contains("stroop", case=False).sum() > 1


_RSCRIPT = os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")


@pytest.mark.skipif(_RSCRIPT is None, reason="needs R with metacheck")
def test_convert_script_reproduces_bundled_data(tmp_path: Path) -> None:
    subprocess.run(
        [str(_RSCRIPT), str(ROOT / "scripts" / "convert_datacheck_data.R"),
         str(ROOT / "upstream" / "metacheck"), str(tmp_path)],
        check=True, capture_output=True,
    )  # fmt: skip
    for name in ("scales", "tasks"):
        bundled = ROOT / "src" / "pytacheck" / "resources" / "data" / f"{name}.json.gz"
        assert (tmp_path / f"{name}.json.gz").read_bytes() == bundled.read_bytes()
