"""Port of upstream tests/testthat/test-codebook-helpers.R (the rules path).

The LLM-backed tests of that file (``data_group_llm``, ``.llm_classify_batched``,
``.strip_llm_wrapper``) belong to other areas and are ported there.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pytacheck.datacheck.columns import (
    _decode_value_labels,
    _empty_codebook_vars,
    _encode_value_labels,
    _extract_haven_labels,
    _extract_structured_codebook,
    _infer_group,
    _parse_value_label_text,
    match_column_labels,
    normalize_label,
    normalize_varname,
    parse_codebook,
)

FIX = Path(__file__).parent / "fixtures"


def test_normalize_varname_canonicalises_names() -> None:
    assert normalize_varname("SSS_total") == "sss total"
    assert normalize_varname("  Age.  ") == "age"
    assert normalize_varname("subj-id") == "subj-id"


def test_normalize_label_stems_and_strips_for_equivalence() -> None:
    a = normalize_label("Participants' responses")
    b = normalize_label("participant response")
    assert a == b


def test_parse_codebook_reads_a_structured_csv_codebook(tmp_path: Path) -> None:
    d = tmp_path / "codebook.csv"
    d.write_text("varname,description\nid,participant identifier\nscore,outcome measure\n")
    res = parse_codebook(d)
    assert isinstance(res, pd.DataFrame)
    assert {"codebook_variable", "label"} <= set(res.columns)
    assert len(res) == 2
    assert res["parse_method"].iloc[0] == "structured"
    assert res["codebook_source"].tolist() == ["codebook.csv"] * 2


# -- DDI: value labels / code lists, missing scheme, question -----------------


def test_value_label_json_round_trips_through_encode_decode() -> None:
    s = _encode_value_labels([1, 2, -99], ["Male", "Female", "Refused"])
    assert "Male" in s
    vl = _decode_value_labels(s)
    assert vl["1"] == "Male"
    assert vl["-99"] == "Refused"
    assert _decode_value_labels(None) is None


def test_parse_value_label_text_parses_common_coding_encodings() -> None:
    assert _decode_value_labels(_parse_value_label_text("1 = Male; 2 = Female"))["2"] == "Female"
    assert _decode_value_labels(_parse_value_label_text("0: no | 1: yes"))["1"] == "yes"
    # A single pair is not a real mapping.
    assert _parse_value_label_text("1 = only") is None
    assert _parse_value_label_text("") is None


def test_extract_haven_labels_harvests_value_labels_and_missing_codes() -> None:
    # haven::labelled_spss(c(1, 2, -99, ...), labels = ..., na_values = -99, label = "Sex"):
    # column attributes live in df.attrs["col_attrs"] (data_read_head's convention).
    df = pd.DataFrame({"x": range(1, 7), "sex": [1.0, 2.0, -99.0, 1.0, 2.0, -99.0]})
    df.attrs["col_attrs"] = {
        "sex": {
            "label": "Sex",
            "labels": {"Male": 1.0, "Female": 2.0, "Refused": -99.0},
            "na_values": [-99.0],
        }
    }
    res = _extract_haven_labels(df, "study.sav")
    srow = res[res["codebook_variable"] == "sex"].iloc[0]
    assert _decode_value_labels(srow["value_labels"])["1"] == "Male"
    # -99 is a declared missing (and named "Refused") -> in the missing scheme.
    assert not pd.isna(srow["missing_values"])
    assert "-99" in srow["missing_values"]


def test_extract_structured_codebook_reads_values_and_question_columns() -> None:
    cb = pd.DataFrame(
        {
            "variable": ["sex", "age"],
            "label": ["Sex", "Age"],
            "values": ["1 = Male; 2 = Female; -99 = Refused", ""],
            "question": ["What is your sex?", "How old are you?"],
        }
    )
    res = _extract_structured_codebook(cb, "cb.csv")
    assert _decode_value_labels(res["value_labels"].iloc[0])["1"] == "Male"
    assert pd.isna(res["value_labels"].iloc[1])
    assert res["question"].iloc[0] == "What is your sex?"
    # "-99 = Refused" contributes to the missing scheme from a text codebook.
    assert not pd.isna(res["missing_values"].iloc[0])
    assert "-99" in res["missing_values"].iloc[0]


def test_match_column_labels_carries_ddi_properties_onto_data_columns() -> None:
    cols = pd.DataFrame({"paper_id": ["p"], "source_file": ["d.csv"], "column_name": ["sex"]})
    cbk = pd.DataFrame(
        {
            "codebook_variable": ["sex"],
            "label": ["Sex"],
            "codebook_source": ["cb.csv"],
            "group": pd.Series([None], dtype="string"),
            "value_labels": ['{"1":"Male","2":"Female"}'],
            "missing_values": pd.Series([None], dtype="string"),
            "question": ["What is your sex?"],
            "coding_instructions": ["recoded from raw gender field"],
            "parse_method": ["structured"],
        }
    )
    res = match_column_labels(cols, cbk)
    assert res["value_labels"].tolist() == ['{"1":"Male","2":"Female"}']
    assert res["question"].tolist() == ["What is your sex?"]
    assert res["coding_instructions"].tolist() == ["recoded from raw gender field"]


def test_parse_codebook_returns_text_lines_for_unstructured_files(tmp_path: Path) -> None:
    d = tmp_path / "readme.txt"
    d.write_text("This is a prose readme.\nNo variable table here.\n")
    res = parse_codebook(d)
    # character vector (for the LLM tier), not a data frame
    assert res == ["This is a prose readme.", "No variable table here."]


def test_match_column_labels_matches_by_normalised_name() -> None:
    cols = pd.DataFrame(
        {"paper_id": "p", "source_file": "d.csv", "column_name": ["age", "SSS_total", "unmatched"]}
    )
    cbk = pd.DataFrame(
        {
            "codebook_variable": ["age", "sss total"],
            "label": ["Age in years", "SSS total score"],
            "codebook_source": "cb.csv",
            "group": pd.Series([None, None], dtype="string"),
            "parse_method": "structured",
        }
    )
    res = match_column_labels(cols, cbk).set_index("column_name")
    assert res.loc["age", "label_status"] == "labelled"
    assert res.loc["SSS_total", "label_status"] == "labelled"
    assert res.loc["unmatched", "label_status"] == "unlabelled"


def test_match_column_labels_flags_conflicting_definitions() -> None:
    cols = pd.DataFrame({"paper_id": ["p"], "source_file": ["d.csv"], "column_name": ["dv"]})
    cbk = pd.DataFrame(
        {
            "codebook_variable": ["dv", "dv"],
            "label": ["reaction time", "accuracy"],  # semantically different
            "codebook_source": ["a.csv", "b.csv"],
            "group": pd.Series([None, None], dtype="string"),
            "parse_method": "structured",
        }
    )
    res = match_column_labels(cols, cbk)
    assert res["label_status"].tolist() == ["conflicting_definition"]


def test_match_column_labels_returns_unlabelled_when_no_codebook() -> None:
    cols = pd.DataFrame({"paper_id": "p", "source_file": "d.csv", "column_name": ["a", "b"]})
    res = match_column_labels(cols, _empty_codebook_vars())
    assert (res["label_status"] == "unlabelled").all()


def test_infer_group_maps_experiment_context_to_group_codes() -> None:
    assert _infer_group("Experiment 1") == ["ex1"]
    assert _infer_group("Study 2a") == ["ex2a"]
    assert _infer_group("Pilot 1") == ["pilot1"]
    assert _infer_group("") == [None]


# -- behaviour pinned by R runs (not reachable from a YAML parity case) -------


_COLS = [
    "paper_id", "source_file", "column_name", "group", "label", "codebook_variable",
    "label_source", "label_status", "label_method", "value_labels", "missing_values",
    "question", "coding_instructions", "scale_group",
]  # fmt: skip


def test_match_column_labels_tolerates_absent_id_columns() -> None:
    """U58: absent id columns are NA, and NULL / zero-row input gives zero rows
    (R's data.frame() refuses a NULL paper_id next to real rows, and the early
    return fails on NULL or zero-row columns_df)."""
    cols = pd.DataFrame({"source_file": ["d.csv"], "column_name": ["age"]})
    cbk = pd.DataFrame(
        {"codebook_variable": ["age"], "label": ["Age"], "codebook_source": ["cb"], "group": [None]}
    )
    res = match_column_labels(cols, cbk)
    assert list(res.columns) == _COLS
    assert res["paper_id"].isna().all()
    assert res["label"].tolist() == ["Age"]
    for empty in (None, cols.iloc[:0]):
        res = match_column_labels(empty, cbk)
        assert list(res.columns) == _COLS
        assert len(res) == 0
    # no column_name: nothing can match, one unlabelled row per column
    res = match_column_labels(pd.DataFrame({"paper_id": ["p", "p"]}), cbk)
    assert res["label_status"].tolist() == ["unlabelled", "unlabelled"]


def test_match_column_labels_expands_ranges_without_mutating_inputs() -> None:
    cols = pd.DataFrame(
        {"paper_id": "p", "source_file": "d.csv", "column_name": ["V2", "V5", "V10", "T1-T2"]}
    )
    cbk = pd.DataFrame(
        {
            "codebook_variable": ["V1-V3", "V4-6", "V9 – V10", "T1-T2", "A1-B3"],
            "label": ["Ratings", "Scores", "Late", "Change score", "Mixed"],
            "codebook_source": ["cb"] * 5,
            "group": [None] * 5,
        }
    )
    before_cols, before_cbk = cols.copy(), cbk.copy()
    res = match_column_labels(cols, cbk)
    # U58: "V1-V3" (prefix repeated) is expanded like "V4-6"; a range whose
    # own name is a data column ("T1-T2") is that variable, not a range
    assert res["label"].tolist() == ["Ratings", "Scores", "Late", "Change score"]
    assert res["codebook_variable"].tolist() == ["V2", "V5", "V10", "T1-T2"]
    pd.testing.assert_frame_equal(cols, before_cols)
    pd.testing.assert_frame_equal(cbk, before_cbk)


def test_match_column_labels_na_group() -> None:
    """U58: a column without a group takes unscoped definitions only; with
    only group-scoped ones it is ambiguous (R labelled it NA / "x | NA")."""
    cols = pd.DataFrame(
        {"paper_id": "p", "source_file": "d.csv", "column_name": ["age", "sex"], "group": None}
    )
    cbk = pd.DataFrame(
        {
            "codebook_variable": ["age", "sex", "sex"],
            "label": ["Age", "Sex", "Gender"],
            "codebook_source": ["a.csv", "a.csv", "b.csv"],
            "group": ["ex1", None, "ex2"],
        }
    )
    res = match_column_labels(cols, cbk)
    assert res["label_status"].tolist() == ["ambiguous_experiment", "labelled"]
    assert res["label"].tolist() == ["Age", "Sex"]


def test_parse_codebook_scopes_to_group_but_json_in_csv_returns_early() -> None:
    res = parse_codebook(FIX / "structured.csv", group="ex1")
    assert set(res["group"]) == {"ex1"}
    # R's return() inside the csv branch skips the group stamping.
    res = parse_codebook(FIX / "json_in.csv", group="ex1")
    assert res["group"].isna().all()
    assert set(res["parse_method"]) == {"structured"}


def test_parse_codebook_haven_and_qsf_parse_methods() -> None:
    sav = parse_codebook(FIX / "labelled.sav")
    assert set(sav["parse_method"]) == {"haven"}
    qsf = parse_codebook(FIX / "survey.qsf")
    assert set(qsf["parse_method"]) == {"qsf"}
    assert "scale_group" in qsf.columns


def test_parse_codebook_missing_file_is_none() -> None:
    assert parse_codebook(FIX / "does_not_exist.csv") is None


def test_haven_value_labels_na_label_name() -> None:
    from pytacheck.datacheck.columns import _haven_value_labels, _looks_like_freetext_labels

    # U60: R: structure(1:2, labels = setNames(1:5, c("a", "b", "c", "d", NA)))
    # fails in `&&` because the free-text test returns NA; the NA-named code is
    # dropped as it is with fewer labels
    attrs = {"labels": [("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 4.0), (None, 5.0)]}
    res = _haven_value_labels(None, attrs)
    assert res == {"value_labels": '{"1":"a","2":"b","3":"c","4":"d"}', "missing_values": None}
    assert _looks_like_freetext_labels(["x" * 50] * 5 + [None]) is True
    assert _looks_like_freetext_labels(["x" * 50] * 4 + [None]) is False
    # fewer than five labels are never judged, so an NA name passes through
    res = _haven_value_labels(None, {"labels": [("a", 1.0), (None, 2.0)]})
    assert res == {"value_labels": '{"1":"a"}', "missing_values": None}


def test_parse_codebook_utf8_with_na_cell_stays_utf8() -> None:
    # U64: an "NA" cell is not invalid UTF-8 (metacheck re-reads the file as
    # Latin-1 and turns "générale" into "gÃ©nÃ©rale")
    res = parse_codebook(FIX / "utf8_na.csv")
    assert res["label"].tolist() == [
        "Âge du participant",
        "Sexe (1 = homme; 2 = femme)",
        "Humeur générale",
    ]
