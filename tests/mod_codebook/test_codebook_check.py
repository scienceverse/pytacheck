"""Port of metacheck's tests/testthat/test-module-codebook_check.R (plus pytacheck checks).

The R tests run ``data_check`` then ``codebook_check`` over small local
fixture directories. The same directories are built by
``fixtures/make_fixtures.R``, which also stores metacheck's ``data_check``
output for each one; :func:`tests.mod_codebook.helpers.cbc_run` chains the
Python ``codebook_check`` onto that output, so these tests do not depend on
the (separately ported) ``data_check`` module. When a Python ``data_check``
is available, :func:`test_full_pipeline_matches_stored_data_check` also runs
the whole pipeline.

Everything runs offline with ``llm_use(FALSE)``, except the tests that check
LLM-gated behaviour, which replace ``llm()`` with a deterministic mock.
"""

from __future__ import annotations

import copy
import math
import random
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleOutput, module_run
from pytacheck.modules import _codebook as cbc
from pytacheck.report.blocks import cap_gate_count
from tests.mod_codebook.helpers import (
    FIXTURES,
    ROOT,
    cbc_llm_run,
    cbc_prev,
    cbc_prev_list,
    cbc_run,
    report_tables,
    report_text,
)

BIBR12_PREPRINT = ROOT / "upstream/metacheck/tests/testthat/fixtures/bibr12/preprint.json"
LEGACY_PREPRINT = ROOT / "upstream/metacheck/tests/testthat/fixtures/formats/preprint.pdf.tei.xml"

SUMMARY_COLS = [
    "paper_id",
    "column_n",
    "matched_n",
    "unmatched_n",
    "clean_n",
    "conflicted_n",
    "codebook_var_n",
    "unused_var_n",
    "scale_blocks_n",
    "scale_named_n",
    "scale_unnamed_n",
    "task_files_n",
    "task_named_n",
    "task_paper_only_n",
]


def st(out: ModuleOutput) -> dict[str, Any]:
    """The first summary_table row as a dict."""
    return out.summary_table.iloc[0].to_dict()


# ── Coverage vs. label quality ───────────────────────────────────────────────


def test_conflicting_definition_counts_as_matched_but_not_clean() -> None:
    cc = cbc_run("conflict")
    s = st(cc)
    assert s["conflicted_n"] >= 1
    assert s["matched_n"] == s["clean_n"] + s["conflicted_n"]
    assert s["clean_n"] < s["matched_n"]
    assert "Conflicting or Ambiguous Definitions" in report_text(cc)


def test_identical_labels_from_two_sources_merge() -> None:
    cc = cbc_run("merge")
    assert st(cc)["conflicted_n"] == 0
    assert st(cc)["clean_n"] > 0


# ── Documented but unused variables ──────────────────────────────────────────


def test_codebook_variables_absent_from_the_data_are_reported_as_unused() -> None:
    cc = cbc_run("unused")
    assert st(cc)["unused_var_n"] == 2
    assert "Documented but Unused Variables" in report_text(cc)
    unused = report_tables(cc)[-1]
    assert list(unused.columns) == ["Variable", "Label", "Source"]
    assert unused["Variable"].tolist() == ["income", "region"]


# ── Codebook misalignment ────────────────────────────────────────────────────


def test_misaligned_codebook_is_flagged() -> None:
    cc = cbc_run("misalign")
    assert "do not match the data columns" in report_text(cc)
    assert "do not match the data columns" in cc.summary_text
    assert st(cc)["unused_var_n"] > 0


def test_ordinary_partial_codebook_is_not_flagged() -> None:
    cc = cbc_run("partial")
    assert "do not match the data columns" not in report_text(cc)
    assert cc.traffic_light == "yellow"


# ── Values outside the documented range ──────────────────────────────────────


def test_value_the_codebook_does_not_list_is_reported_with_its_kind() -> None:
    cc = cbc_run("range")
    sv = cc.scale_violations
    assert isinstance(sv, pd.DataFrame)
    assert len(sv) >= 1
    assert "q1" in sv["column"].tolist()
    assert sv["documented"].tolist() == ["[1, 5]"]
    assert "Values Outside the Documented Range" in report_text(cc)


def test_no_documented_range_means_no_range_check() -> None:
    cc = cbc_run("norange")
    assert len(cc.scale_violations) == 0
    assert list(cc.scale_violations.columns) == [
        "source_file",
        "column",
        "documented",
        "n_values",
        "values",
        "kinds",
    ]
    assert "Values Outside the Documented Range" not in report_text(cc)


def test_values_are_classified_as_missing_codes_or_typos() -> None:
    cc = cbc_run("missing_codes")
    sv = cc.scale_violations
    # q1's code list includes its declared missing code 9 (so the documented
    # range is 1-9, as in R); -99 is a conventional missing code, 12 a typo.
    assert sv["column"].tolist() == ["q1", "q2"]
    assert sv["documented"].tolist() == ["[1, 9]", "[1, 5]"]
    assert sv["values"].tolist() == ["-99, 12", "77"]
    assert sv["kinds"].tolist() == ["missing, typo:2", "missing"]
    assert sv["n_values"].tolist() == [2, 1]


# ── Traffic light ────────────────────────────────────────────────────────────


def test_fully_documented_clean_repository_is_green() -> None:
    cc = cbc_run("green")
    assert st(cc)["unmatched_n"] == 0
    assert st(cc)["conflicted_n"] == 0
    assert cc.traffic_light == "green"


def test_documented_range_violation_downgrades_green_to_yellow() -> None:
    cc = cbc_run("downgrade")
    assert st(cc)["unmatched_n"] == 0
    assert len(cc.scale_violations) >= 1
    assert cc.traffic_light == "yellow"


@pytest.mark.parametrize(
    ("scenario", "light"),
    [
        ("green", "green"),
        ("pl_a", "green"),
        ("partial", "yellow"),
        ("qualtrics", "yellow"),
        ("conflict", "red"),
        ("nocb", "red"),
        ("empty", "na"),
    ],
)
def test_traffic_lights(scenario: str, light: str) -> None:
    assert cbc_run(scenario).traffic_light == light


# ── Empty and degenerate inputs ──────────────────────────────────────────────


def test_no_data_columns_returns_na_without_calling_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[Any] = []

    def boom(**kwargs: Any) -> Any:
        called.append(kwargs)
        raise AssertionError("llm() must not be called")

    monkeypatch.setattr(cbc, "_llm", boom)
    from pytacheck.utils import local_options

    with local_options({"metacheck.llm.use": True}):
        cc = module_run(cbc_prev("empty"), "codebook_check")
    assert not called
    assert cc.traffic_light == "na"
    assert len(cc.table) == 0
    assert "no extracted data columns" in cc.summary_text
    assert st(cc)["column_n"] == 0
    assert st(cc)["codebook_var_n"] == 0
    assert cc.report == []


def test_summary_table_always_carries_the_full_set_of_count_columns() -> None:
    cc = cbc_run("scope")
    assert list(cc.summary_table.columns) == SUMMARY_COLS
    counts = [c for c in cc.summary_table.columns if c != "paper_id"]
    assert all(c in cc.na_replace for c in counts)


# ── Duplicated column names ──────────────────────────────────────────────────


def test_repeated_column_names_are_reported_per_file() -> None:
    cc = cbc_run("dup")
    rpt = report_text(cc)
    assert "Duplicated Column Names" in rpt
    assert "loop.csv" in rpt
    assert "`POWER`×3" in rpt


def test_duplicate_name_warnings_are_silent_when_names_are_unique() -> None:
    warn = cbc._codebook_duplicate_name_warnings(
        {"clean.csv": pd.DataFrame({"a": [1, 2, 3], "b": [1, 2, 3], "c": [1, 2, 3]})}
    )
    assert warn == []


# ── Item vs. derived column split ────────────────────────────────────────────


def _ratings(cols: list[str], nrow: int = 20, seed: int = 0) -> pd.DataFrame:
    rng = random.Random(seed)
    return pd.DataFrame({c: [float(rng.randint(1, 5)) for _ in range(nrow)] for c in cols})


def test_aggregate_column_is_split_out_of_an_item_block_by_its_name() -> None:
    cols = [f"AQ{i:02d}" for i in range(1, 11)] + ["AQ_SUM"]
    df = _ratings(cols, seed=11)
    df["AQ_SUM"] = df.iloc[:, :10].sum(axis=1)
    sp = cbc._scale_split_items(cols, df)
    assert "AQ_SUM" in sp["derived"]
    assert "AQ_SUM" not in sp["items"]
    assert len(sp["items"]) == 10


def test_non_integer_mean_is_split_out_without_an_aggregate_name() -> None:
    cols = [f"bfi_{i}" for i in range(1, 10)]
    df = _ratings(cols, seed=12)
    df["bfi_9"] = df.iloc[:, :8].mean(axis=1) + 0.125
    sp = cbc._scale_split_items(cols, df)
    assert "bfi_9" in sp["derived"]


def test_block_of_only_totals_is_marked_totals_only() -> None:
    cols = ["IERQ_pos", "IERQ_persp", "IERQ_sooth", "IERQ_model"]
    rng = random.Random(13)
    df = pd.DataFrame({c: [rng.uniform(1, 40) for _ in range(20)] for c in cols})
    assert cbc._scale_split_items(cols, df)["totals_only"] is True


def test_small_block_is_left_intact() -> None:
    cols = ["x_1", "x_2", "x_sum"]
    df = pd.DataFrame(
        {"x_1": range(1, 6), "x_2": range(1, 6), "x_sum": [2 * i for i in range(1, 6)]}
    )
    sp = cbc._scale_split_items(cols, df)
    assert sp["items"] == cols
    assert sp["derived"] == []


# ── Prefix grouping ──────────────────────────────────────────────────────────


def test_zero_padded_numbering_does_not_split_one_scale() -> None:
    assert cbc._scale_alpha_prefix("AQ01") == "aq"
    assert cbc._scale_alpha_prefix("AQ10") == "aq"
    assert cbc._scale_alpha_prefix("CRS_EXP") == "crs_exp"
    assert cbc._scale_alpha_prefix("CRS_EXP") != cbc._scale_alpha_prefix("CRS_IDE")


def test_shared_stem_keeps_inner_separators() -> None:
    assert cbc._scale_shared_stem("Q1.RR_P_1", "Q1.RR_P_2") == "Q1.RR_P"
    assert cbc._scale_shared_stem("bfi_1", "bfi_2") == "bfi"
    assert cbc._scale_shared_stem("apple", "orange") == ""


def test_columns_differing_only_by_item_number_are_one_run() -> None:
    same = cbc._scale_same_number_run
    assert same("matwarmth1", "matwarmth2")
    assert same("AQ01", "AQ10")
    assert not same("matwarmth6", "mataggr1")
    assert not same("neighdev", "neighcur")


def test_repeated_stimulus_loop_collapses_to_one_base() -> None:
    assert cbc._scale_loop_base("POWER.PP1") == cbc._scale_loop_base("POWER.PP170")
    assert cbc._scale_loop_base("PANAS") == "panas"


def test_prefix_groups_split_zero_padded_blocks_from_word_subscales() -> None:
    names = [f"AQ{i:02d}" for i in range(1, 21)] + ["AQ_SUM", "CRS_EXP1", "CRS_EXP2", "CRS_EXP3"]
    groups = cbc._scale_prefix_groups(_ratings(names, seed=5))
    assert list(groups) == ["aq", "crs_exp"]
    # AQ01..AQ09 and AQ10..AQ20 merge into one block; AQ_SUM breaks the
    # numbering run and forms no block of its own
    assert groups["aq"]["columns"] == names[:20]
    assert groups["aq"]["derived"] == []
    assert groups["aq"]["max_item"] == 20
    assert groups["crs_exp"]["display"] == "CRS_EXP"


def test_derived_columns_inside_a_block_are_recorded() -> None:
    # checked against R: pss_10 holds a (non-integer) mean of the items
    df = pd.DataFrame(
        {f"pss_{j}": [float((i * 7 + j * 3) % 5 + 1) for i in range(1, 21)] for j in range(1, 11)}
    )
    df["pss_10"] = df.iloc[:, :9].mean(axis=1) + 0.125
    groups = cbc._scale_prefix_groups(df)
    assert list(groups) == ["pss"]
    g = groups["pss"]
    assert g["display"] == "PSS"
    assert g["derived"] == ["pss_10"]
    assert g["n_columns"] == 9
    assert g["max_item"] == 9
    assert g["totals_only"] is False


# ── Scale name propagation ───────────────────────────────────────────────────


def test_named_scale_propagates_to_same_prefix_siblings() -> None:
    labels_df = pd.DataFrame(
        {
            "source_file": "s.csv",
            "column_name": ["bfi_1", "bfi_2", "bfi_3", "other_1"],
            "scale": ["Big Five Inventory", None, None, None],
            "scale_confidence": ["high", None, None, None],
            "scale_source": ["manuscript", None, None, None],
        }
    )
    before = labels_df.copy()
    out = cbc._propagate_scale_by_prefix(labels_df)
    assert out["scale"].tolist()[:3] == ["Big Five Inventory"] * 3
    assert out["scale_confidence"].iloc[1] == "high"
    assert out["scale_source"].iloc[1] == "manuscript"
    assert pd.isna(out["scale"].iloc[3])
    pd.testing.assert_frame_equal(labels_df, before)  # input untouched


def test_propagation_never_overwrites_an_earlier_name() -> None:
    labels_df = pd.DataFrame(
        {
            "source_file": "s.csv",
            "column_name": ["bfi_1", "bfi_2"],
            "scale": ["Big Five Inventory", "Something Else"],
            "scale_confidence": ["high", "low"],
            "scale_source": ["manuscript", "self_generated"],
        }
    )
    out = cbc._propagate_scale_by_prefix(labels_df)
    assert out["scale"].iloc[1] == "Something Else"


# ── Paper-text scanning for instruments and tasks ────────────────────────────


def test_possessive_instrument_name_is_matchable() -> None:
    from pytacheck._r import grepl

    pat = cbc._scale_text_pattern("Raven's Advanced Progressive Matrices", None)
    for text in (
        "we used Raven's Advanced Progressive Matrices",
        "we used Raven’s Advanced Progressive Matrices",
    ):
        assert grepl(pat, text, ignore_case=True, perl=True)


def test_acronym_is_matched_only_at_word_boundaries() -> None:
    assert not cbc._scale_name_in_text(
        "Meaning in Everyday Scale (MES)", ["response times increased over trials"]
    )
    assert cbc._scale_name_in_text(
        "Meaning in Everyday Scale (MES)", ["the MES was administered first"]
    )


def test_task_named_in_the_paper_is_found_by_the_task_dictionary() -> None:
    p = pc.test_paper(["Participants completed a Stroop task.", "Reaction times were recorded."])
    found = cbc._scan_paper_for_tasks(p)
    assert any("stroop" in f.lower() for f in found)


def test_scanning_a_paper_with_no_text_returns_nothing() -> None:
    assert cbc._scan_paper_for_scales(pc.test_paper([])) == []
    assert cbc._scan_paper_for_tasks(pc.test_paper([])) == []
    assert cbc._scan_paper_for_tasks(None) == []


# ── Behavioural tasks in the data ────────────────────────────────────────────


def test_task_is_named_when_data_shows_it_and_paper_confirms_it() -> None:
    cc = cbc_run("task")
    assert st(cc)["task_files_n"] >= 1
    assert st(cc)["task_named_n"] == 1
    assert "#### Tasks" in report_text(cc)
    named = cc.table[cc.table["scale"].notna()]
    assert set(named["scale"]) == {"Stroop task"}
    assert set(named["scale_confidence"]) == {"high"}
    assert set(named["scale_source"]) == {"task_matched"}


def test_task_named_only_in_the_paper_is_reported_not_an_error() -> None:
    cc = cbc_run("taskonly")
    assert st(cc)["task_paper_only_n"] >= 1
    assert "named in the manuscript" in report_text(cc)
    assert cc.traffic_light in ("green", "yellow", "red")


def test_distinctive_task_prefix_alone_names_a_task_with_medium_confidence() -> None:
    cc = cbc_run("task_medium")
    named = cc.table[cc.table["scale"].notna()]
    assert set(named["scale_confidence"]) <= {"medium"}


def test_task_dictionary_has_colliding_stroop_variants() -> None:
    tasks = cbc._task_dictionary()
    assert sum("stroop" in n.lower() for n in tasks["name"]) > 1


# ── Orphan totals ────────────────────────────────────────────────────────────


def test_totals_only_block_is_detected_and_reported() -> None:
    cc = cbc_run("orphan")
    # IERQ_pos / IERQ_persp / ... differ by a word, not an item number, so they
    # form no prefix group; the Scales section still appears (as in R)
    assert "#### Scales" in report_text(cc)
    assert "needs an LLM" in report_text(cc)


# ── Paradata exclusion feeding scale detection ───────────────────────────────


def test_paradata_columns_are_never_grouped_as_a_scale() -> None:
    v = list(range(1, 6))
    df = pd.DataFrame(
        {
            "psqi_1_response_numeric": v,
            "psqi_1_response_time": v,
            "psqi_2_response_numeric": v,
            "psqi_2_response_time": v,
            "psqi_3_response_numeric": v,
            "psqi_3_response_time": v,
        }
    )
    mask = cbc._paradata_col(df)
    assert all(mask[i] for i in (1, 3, 5))
    assert not any(mask[i] for i in (0, 2, 4))
    groups = cbc._scale_prefix_groups(df)
    grouped = [c for g in groups.values() for c in g["columns"]]
    assert not any("response_time" in c for c in grouped)


def test_machinery_columns_are_self_labelled() -> None:
    labels_df = pd.DataFrame(
        {
            "source_file": "s.csv",
            "column_name": ["StartDate", "Q1_DO_order", "Q2_TEXT", "bfi_1"],
            "label": pd.Series([None] * 4, dtype="string"),
            "codebook_variable": pd.Series([None] * 4, dtype="string"),
            "label_status": "unlabelled",
            "label_method": pd.Series([None] * 4, dtype="string"),
        }
    )
    previews = {
        "s.csv": pd.DataFrame(
            {
                "StartDate": ["2024-01-01"],
                "Q1_DO_order": [1],
                "Q2_TEXT": ["free text"],
                "bfi_1": [3],
            }
        )
    }
    before = labels_df.copy()
    out = cbc._codebook_label_machinery(labels_df, previews)
    assert out["label_status"].tolist()[:3] == ["labelled"] * 3
    assert all(isinstance(x, str) and x for x in out["label"].tolist()[:3])
    assert out["label_method"].tolist()[:3] == ["paradata_rule"] * 3
    assert out["codebook_variable"].tolist()[:3] == ["StartDate", "Q1_DO_order", "Q2_TEXT"]
    assert out["label_status"].iloc[3] == "unlabelled"
    pd.testing.assert_frame_equal(labels_df, before)


def test_documented_column_keeps_its_real_label() -> None:
    labels_df = pd.DataFrame(
        {
            "source_file": ["s.csv"],
            "column_name": ["Q2_TEXT"],
            "label": ["Other, please specify"],
            "codebook_variable": ["Q2_TEXT"],
            "label_status": ["labelled"],
            "label_method": ["rules"],
        }
    )
    out = cbc._codebook_label_machinery(labels_df, {"s.csv": pd.DataFrame({"Q2_TEXT": ["x"]})})
    assert out["label"].tolist() == ["Other, please specify"]
    assert out["label_method"].tolist() == ["rules"]


def test_qualtrics_export_machinery_is_labelled_in_the_module() -> None:
    cc = cbc_run("qualtrics")
    tbl = cc.table.set_index("column_name")
    assert tbl.loc["StartDate", "label_method"] == "paradata_rule"
    assert tbl.loc["Q8timing_First.Click", "label_status"] == "labelled"
    # Q2_TEXT is documented in the codebook: it keeps that label
    assert tbl.loc["Q2_TEXT", "label"] == "Other please specify"


# ── LLM-gated behaviour ──────────────────────────────────────────────────────


def test_rules_only_mode_names_scales_from_the_dictionary_alone() -> None:
    cc = cbc_run("rulesonly")
    named = cc.table["scale"].notna()
    assert named.any()
    assert set(cc.table.loc[named, "scale_source"]) == {"matched"}
    assert "reviewed ambiguous cases" not in report_text(cc)
    assert "Scale naming from the manuscript needs an LLM" not in report_text(cc)


def test_llm_call_budget_refuses_a_tier_and_names_the_parameter() -> None:
    gate = cap_gate_count(
        50, "codebook_max_calls", 2, "text block", context="big_codebook.txt", action="parse"
    )
    assert gate is not None
    assert "codebook_max_calls" in gate


def test_llm_gate_skips_a_long_unstructured_codebook() -> None:
    with pytest.warns(UserWarning, match="codebook_max_calls"):
        cc = cbc_llm_run("readme_long", "parse", codebook_max_calls=1)
    assert (
        "- 2 text blocks for README.txt exceed the `codebook_max_calls` cap of 1."
        in report_text(cc)
    )
    assert st(cc)["codebook_var_n"] == 0


def test_llm_tiers_merge_match_and_parse() -> None:
    merged = cbc_llm_run("conflict", "merge")
    row = merged.table.set_index("column_name").loc["mood"]
    assert row["label"] == "Sleep quality rating"
    assert row["label_method"] == "merged_llm"
    assert "merged 1 label)" in report_text(merged)

    matched = cbc_llm_run("fuzzy", "match")
    tbl = matched.table.set_index("column_name")
    assert tbl.loc["age_years", "codebook_variable"] == "age"
    assert tbl.loc["age_years", "label_status"] == "llm"
    assert "matched 3 columns" in report_text(matched)

    parsed = cbc_llm_run("readme_text", "parse")
    assert parsed.codebook_vars["parse_method"].tolist() == ["llm"] * 3
    assert "parsed 1 file," in report_text(parsed)


def test_llm_scale_tiers_name_groups_and_report_manuscript_scales() -> None:
    cc = cbc_llm_run("scales_mixed", "scales")
    sources = set(cc.table["scale_source"].dropna())
    assert sources == {"manuscript", "matched", "self_generated"}
    rpt = report_text(cc)
    assert "#### Scales in the manuscript" in rpt
    assert "- **Aggression Questionnaire** (AQ; possibly not administered here)" in rpt
    assert "LLM model 'mock/cbc' reviewed" in rpt


def test_llm_errors_are_swallowed() -> None:
    cc = cbc_llm_run("scales_mixed", "errors")
    assert set(cc.table["scale_source"].dropna()) == {"matched"}
    assert "LLM reviewed ambiguous cases" in report_text(cc)


# ── Report structure ─────────────────────────────────────────────────────────


def test_report_states_how_many_sources_and_columns_were_examined() -> None:
    cc = cbc_run("scope")
    rpt = report_text(cc)
    assert "We examined" in rpt
    assert "data column" in rpt
    assert "#### Column Documentation" in rpt
    assert "::: {.panel-tabset}" in rpt


def test_no_codebook_says_so_instead_of_an_empty_table() -> None:
    cc = cbc_run("nocb")
    rpt = report_text(cc)
    assert "No codebook or README documentation was found" in rpt
    assert "#### Column Documentation" not in rpt
    assert cc.traffic_light == "red"


def test_one_tab_per_data_file() -> None:
    cc = cbc_run("multifile")
    rpt = report_text(cc)
    assert "## demographics.csv" in rpt
    assert "## results.csv" in rpt
    tabs = report_tables(cc)[:2]
    assert [t.columns.tolist() for t in tabs] == [
        ["Column", "Documented", "Label", "Codebook Variable", "Source", "Status"]
    ] * 2


# ── Embedded labels ──────────────────────────────────────────────────────────


def test_embedded_haven_labels_are_harvested() -> None:
    cc = cbc_run("haven")
    cv = cc.codebook_vars
    assert set(cv["parse_method"]) == {"haven"}
    # the .sav repeats the .dta's definitions exactly, so they are de-duplicated
    assert cv["codebook_variable"].tolist() == ["sex", "age", "q1"]
    assert set(cv["codebook_source"]) == {"labelled.dta"}
    assert st(cc)["matched_n"] == 6


def test_embedded_jasp_and_jamovi_labels_are_harvested() -> None:
    cc = cbc_run("jasp_omv")
    assert set(cc.codebook_vars["codebook_source"]) <= {"sample.jasp", "sample.omv"}
    assert len(cc.codebook_vars) > 0


# ── OSD export ───────────────────────────────────────────────────────────────


def test_scales_are_exported_as_openscales_osd() -> None:
    cc = cbc_run("panas_high")
    osd = cc.scales_osd
    assert len(osd) == 1
    o = osd[0]
    assert o["osd_version"] == "1.0"
    d = o["definition"]
    assert d["scale_info"]["name"] == "Positive and Negative Affect Schedule"
    assert d["likert_options"]["source"] == "codebook"
    assert d["likert_options"]["points"] == 5
    assert d["metacheck"]["confidence"] == "high"
    assert o["translations"]["en"]["PANAS1"] == "Affect item 1"
    assert o.attrs["write"] is True


def test_unnamed_rating_like_blocks_are_written_but_not_other_groups() -> None:
    cc = cbc_run("loop")
    writes = {
        o["definition"]["scale_info"]["abbreviation"]: o.attrs["write"] for o in cc.scales_osd
    }
    assert writes == {"POWER.PP1": True, "SLIDER": True}


# ── Paper lists ──────────────────────────────────────────────────────────────


def test_paper_list_gets_one_summary_row_per_paper() -> None:
    cc = module_run(cbc_prev_list(["pl_a", "pl_b"]), "codebook_check")
    s = cc.summary_table
    assert s["paper_id"].tolist() == ["p1", "p2"]
    # U91: definitions are de-duplicated per paper: p2's "id,Participant id"
    # repeats p1's but documents p2's own `id` (R dropped it, leaving p2's
    # column undocumented)
    assert s["codebook_var_n"].tolist() == [3, 2]
    assert s["unused_var_n"].tolist() == [1, 0]
    assert s["unmatched_n"].tolist() == [0, 1]
    ids = cc.table[cc.table["column_name"] == "id"]
    assert set(ids["label_status"]) == {"labelled"}


def test_paper_list_corroborates_each_file_against_its_own_paper() -> None:
    cc = module_run(cbc_prev_list(["task", "scales_mixed"]), "codebook_check")
    tbl = cc.table
    stroop = tbl[tbl["scale"] == "Stroop task"]
    assert set(stroop["paper_id"]) == {"p1"}
    pss = tbl[tbl["scale"] == "Perceived Stress Scale"]
    assert set(pss["scale_confidence"]) == {"high"}  # confirmed by p2's own text


# ── bibr export schema 12.x papers ───────────────────────────────────────────


def _comparable(out: ModuleOutput) -> dict[str, Any]:
    return {
        "table": out.table.reset_index(drop=True),
        "codebook_vars": out.codebook_vars.reset_index(drop=True),
        "scale_violations": out.scale_violations.reset_index(drop=True),
        "summary": out.summary_table.drop(columns="paper_id").reset_index(drop=True),
        "traffic_light": out.traffic_light,
        "summary_text": out.summary_text,
        "report": report_text(out),
        "tables": report_tables(out),
        "osd": [(dict(o), dict(o.attrs)) for o in out.scales_osd],
    }


@pytest.mark.parametrize("scenario", ["task", "scales_mixed", "panas_high", "taskonly", "conflict"])
def test_bibr12_paper_matches_the_legacy_paper(scenario: str) -> None:
    from pytacheck.io.bibr12 import read_bibr12

    p12 = read_bibr12(BIBR12_PREPRINT)
    legacy = pc.read(LEGACY_PREPRINT, schema_version=None)  # the older Grobid conversion

    a = _comparable(module_run(cbc_prev(scenario, paper=p12, pid="p1"), "codebook_check"))
    b = _comparable(module_run(cbc_prev(scenario, paper=legacy, pid="p1"), "codebook_check"))
    for k in ("table", "codebook_vars", "scale_violations", "summary"):
        pd.testing.assert_frame_equal(a[k], b[k], obj=k)
    for x, y in zip(a["tables"], b["tables"], strict=True):
        pd.testing.assert_frame_equal(x, y)
    assert a["traffic_light"] == b["traffic_light"]
    assert a["summary_text"] == b["summary_text"]
    assert a["report"] == b["report"]
    assert a["osd"] == b["osd"]


def test_default_reader_paper_runs() -> None:
    p = pc.read(BIBR12_PREPRINT)
    cc = module_run(cbc_prev("task", paper=p), "codebook_check")
    assert cc.traffic_light == "red"
    assert st(cc)["task_named_n"] == 0  # the preprint does not describe a Stroop task


# ── pytacheck contracts ──────────────────────────────────────────────────────


def test_inputs_are_not_mutated() -> None:
    prev = cbc_prev("qualtrics")
    snapshot = copy.deepcopy(
        {
            "table": prev.table,
            "structure": prev.get("structure"),
            "previews": prev.get("previews"),
        }
    )
    module_run(prev, "codebook_check")
    pd.testing.assert_frame_equal(prev.table, snapshot["table"])
    pd.testing.assert_frame_equal(prev.get("structure"), snapshot["structure"])
    for k, v in snapshot["previews"].items():
        pd.testing.assert_frame_equal(prev.get("previews")[k], v)


def test_runs_data_check_when_its_output_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.module as mod

    prev = cbc_prev("green")
    calls: list[dict[str, Any]] = []
    real = mod.module_run

    def fake(paper: Any, module: Any, **kwargs: Any) -> Any:
        if module == "data_check":
            calls.append(kwargs)
            return prev
        return real(paper, module, **kwargs)

    monkeypatch.setattr(mod, "module_run", fake)
    cc = real(prev.paper, "codebook_check", local_path="somewhere", local_only=True)
    assert calls == [{"local_path": "somewhere", "local_only": True}]
    assert cc.traffic_light == "green"


def test_full_pipeline_matches_stored_data_check() -> None:
    from pytacheck.module import ModuleError, module_find

    try:
        module_find("data_check")
    except Exception:
        pytest.skip("the data_check module is not ported yet")
    paper = cbc_prev("green").paper
    try:
        dc = module_run(
            paper, "data_check", local_path=str(FIXTURES / "repos" / "green"), local_only=True
        )
    except ModuleError as exc:  # pragma: no cover - depends on the data_check port
        pytest.skip(f"data_check failed: {exc}")
    live = module_run(dc, "codebook_check")
    stored = cbc_run("green")
    assert live.traffic_light == stored.traffic_light
    assert live.summary_text == stored.summary_text


def test_large_corpus_run_is_fast() -> None:
    import time

    prev = cbc_prev_list(["scales_mixed", "qualtrics", "loop", "task", "panas_high", "multifile"])
    t0 = time.perf_counter()
    module_run(prev, "codebook_check")
    assert time.perf_counter() - t0 < 30
    assert math.isfinite(t0)


# ── review findings ──────────────────────────────────────────────────────────


def test_empty_prefix_siblings_become_na_as_in_r() -> None:
    """R looks a sibling's prefix up by name: an empty prefix ("1", "_3", "__")
    never matches, so those rows are set to NA rather than given the scale."""
    from pytacheck.modules._codebook import _propagate_scale_by_prefix
    from tests.mod_codebook.review_helpers import rv_propagate_df

    out = _propagate_scale_by_prefix(rv_propagate_df())
    assert out is not None
    got = out[["column_name", "scale", "scale_confidence", "scale_source"]]
    rows = {r[0]: tuple(None if pd.isna(v) else v for v in r[1:]) for r in got.itertuples(False)}
    assert rows["1"] == ("S", "high", "manuscript")  # the named column itself
    assert rows["2"] == (None, None, None)  # prefix "" -> NA, not "S"
    assert rows["_3"] == (None, None, None)  # an empty-string scale is wiped too
    assert rows["__"] == (None, None, None)
    assert rows["q_2"] == ("Q", "medium", "matched")  # an ordinary prefix still fills


def test_haven_labels_are_read_without_the_data() -> None:
    """Embedded labels are harvested labels-only, as R's n_max = 0L read."""
    from pytacheck.datacheck._columns_codebook import _haven_frame
    from pytacheck.datacheck.columns import _extract_haven_labels
    from pytacheck.modules.codebook_check import _haven_labels_frame

    for rel in (
        "tests/datacheck_files/data/labelled.sav",
        "tests/datacheck_files/data/labelled.dta",
    ):
        f = str(Path(__file__).resolve().parents[2] / rel)
        ext = f.rsplit(".", 1)[1]
        lab = _haven_labels_frame(f, ext)
        assert len(lab) == 0
        a = _extract_haven_labels(lab, "x")
        b = _extract_haven_labels(_haven_frame(f, ext), "x")
        pd.testing.assert_frame_equal(a, b)


def test_osd_attributes_mark_redundant_and_orphan_totals() -> None:
    from tests.mod_codebook.review_helpers import rv_scales_to_osd

    res = rv_scales_to_osd()
    got = [
        (a["code"], a["write"], a["orphan_total"], o["definition"]["metacheck"]["source_files"])
        for o, a in zip(res["osd"], res["attrs"], strict=True)
    ]
    assert got == [
        ("autism_quotient", True, False, ["s.csv", "t.csv"]),  # same items in 2 files: merged
        ("autism_quotient", False, False, ["s.csv"]),  # totals of a scale with items: redundant
        ("zz", False, False, ["s.csv", "t.csv"]),  # unnamed, not rating-like: report only
        ("made_up", False, False, ["s.csv"]),  # self-generated, 2 items: too small
        ("orphan_total", True, True, ["s.csv"]),  # totals with no items anywhere: flagged
    ]


# ── U92: scale inventory ─────────────────────────────────────────────────────


def test_codebook_max_calls_does_not_cut_the_scale_inventory() -> None:
    # R stops listing prefix groups after the first file with groups once the
    # call cap is reached, even with the LLM off (codebook_max_calls = 0)
    prev = cbc_prev_list(["scales_mixed", "loop"])
    full = module_run(prev, "codebook_check")
    zero = module_run(cbc_prev_list(["scales_mixed", "loop"]), "codebook_check",
                      codebook_max_calls=0)  # fmt: skip
    assert len(zero.scales_osd) == len(full.scales_osd)
    groups = cbc._identify_scales_prefix_llm(
        {"a.csv": pd.DataFrame({f"AQ{i}": [1, 2, 3] for i in range(1, 6)}),
         "b.csv": pd.DataFrame({f"PSS{i}": [1, 2, 3] for i in range(1, 6)})},
        None, None, max_calls=0,
    )  # fmt: skip
    assert groups is not None
    assert sorted(groups["source_file"].unique()) == ["a.csv", "b.csv"]


def test_synonym_tokens_lower_case_first() -> None:
    # R strips [^a-z ] before lower-casing: "Emotion Recognition" -> "motion ecognition"
    assert cbc._synonym_tokens("Emotion Recognition") == ["emotion", "recognition"]
    res = pd.DataFrame(
        {
            "source_file": ["f.csv"] * 2,
            "column_name": ["a", "b"],
            "scale": ["Emotion Recognition", "emotion recognition accuracy"],
        }
    )
    merged = cbc._selfgen_merge_synonyms(res)
    assert merged is not None
    assert merged["scale"].nunique() == 1


# ── U93: "Scales in the manuscript" ──────────────────────────────────────────


def test_scale_text_report_skips_missing_fields_and_escapes_acronyms() -> None:
    ts = pd.DataFrame(
        {
            "scale_name": ["Grit Scale", None, "Cognitive Anxiety Scale", "Mixed Test"],
            "acronym": [None, "X", "C++", "(X"],
            "n_items": ["12", None, "9", None],
        }
    )
    out = cbc._scale_text_report(ts, matched=["c++ items", "na"])
    assert out[2:4] == ["- **Grit Scale** (12 items)", "- **Mixed Test** ((X)"]
    assert not any("NA" in line for line in out)


def test_prefix_llm_gets_the_sentences_of_the_files_own_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # U91: on a paper list R required `paper` itself to be one paper and sent no
    # manuscript sentences; each file now gets its own paper's sentences
    seen: list[Any] = []

    def fake_text(file: Any, groups: Any, paper: Any, wording_of: Any) -> str:
        seen.append((file, paper))
        return "x"

    monkeypatch.setattr(cbc, "_prefix_llm_text", fake_text)
    monkeypatch.setattr(cbc, "_llm_use", lambda: True)
    monkeypatch.setattr(cbc, "_llm_try", lambda **_: None)
    p1, p2 = pc.test_paper(["The AQ was used."]), pc.test_paper(["We used the PSS."])
    p1.paper_id, p2.paper_id = "p1", "p2"
    papers = pc.PaperList([p1, p2])
    labels = pd.DataFrame(
        {"paper_id": ["p1", "p2"], "source_file": ["a.csv", "b.csv"], "column_name": ["x", "y"]}
    )
    previews = {
        "a.csv": pd.DataFrame({f"AQ{i}": [1, 2, 3] for i in range(1, 6)}),
        "b.csv": pd.DataFrame({f"PSS{i}": [1, 2, 3] for i in range(1, 6)}),
    }
    cbc._identify_scales_prefix_llm(previews, labels, papers, max_calls=5)
    owners = {f: p.paper_id for f, p in seen}
    assert owners == {"a.csv": "p1", "b.csv": "p2"}
