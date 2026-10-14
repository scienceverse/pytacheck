"""psychds_file_plan(): the Psych-DS file plan without running a module (metacheck 029bf63)."""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.datacheck.psychds import psychds_file_plan
from metacheck.module import ModuleOutput, module_run


def _structure() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "paper_id": ["p1"] * 7,
            "file_name": [
                "raw/scores.xlsx",
                "study-1_data.csv",
                "eeg.npy",
                "analysis.R",
                "README.md",
                "LICENSE",
                "notes.xyz",
            ],
            "file_path": [
                "raw/scores.xlsx",
                "study-1_data.csv",
                "eeg.npy",
                "analysis/analysis.R",
                "README.md",
                "LICENSE",
                "notes.xyz",
            ],
            "data_type": ["data", "data", "data", "code", "documentation", "unknown", "unknown"],
            "doc_role": [None, None, None, None, "readme", "license", None],
        }
    )


def test_psychds_file_plan_maps_each_file() -> None:
    out = psychds_file_plan(_structure())
    assert out["group_no_evidence"] is False
    plan = out["table"]
    assert list(plan.columns) == [
        "file_name",
        "data_type",
        "group",
        "current_path",
        "target_path",
        "status",
        "convert",
        "original_target",
        "referenced_by",
    ]
    by_name = plan.set_index("file_name")
    assert by_name["target_path"].to_dict() == {
        # tabular data is converted to CSV, its original kept beside it
        "raw/scores.xlsx": "data/study-scores_data.csv",
        # a valid Psych-DS data file name stays
        "study-1_data.csv": "data/study-1_data.csv",
        # raw data keeps its own name
        "eeg.npy": "data/eeg.npy",
        "analysis.R": "analysis/analysis.R",
        "README.md": "README.md",
        "LICENSE": "LICENSE",
        "notes.xyz": "unknown/notes.xyz",
    }
    assert by_name.loc["raw/scores.xlsx", "convert"]
    assert by_name.loc["raw/scores.xlsx", "original_target"] == "data/scores.xlsx"
    assert not by_name.loc["eeg.npy", "convert"]
    assert by_name["status"].tolist() == [
        "move",
        "move",
        "move",
        "present",
        "present",
        "present",
        "move",
    ]


def test_psychds_file_plan_with_several_studies() -> None:
    structure = pd.DataFrame(
        {
            "file_name": ["a.csv", "b.csv", "README.md"],
            "data_type": ["data", "data", "documentation"],
            "group": ["s1", "s2", None],
            "doc_role": [None, None, "readme"],
            "referenced_by": [["s2"], None, None],
        }
    )
    out = psychds_file_plan(structure, group_no_evidence=[True])
    assert out["group_no_evidence"] is True
    plan = out["table"]
    assert plan["target_path"].tolist() == [
        "study-s1/data/study-a_data.csv",
        "study-s2/data/study-b_data.csv",
        "README.md",
    ]
    # the input's lists are copied, not shared
    assert plan["referenced_by"].iloc[0] == ["s2"]
    assert plan["referenced_by"].iloc[0] is not structure["referenced_by"].iloc[0]


@pytest.mark.parametrize("structure", [None, pd.DataFrame()])
def test_psychds_file_plan_of_nothing(structure: pd.DataFrame | None) -> None:
    out = psychds_file_plan(structure, True)
    assert out["table"].empty and out["group_no_evidence"] is True


def test_psychds_check_reports_the_plan() -> None:
    # psychds_check's table is psychds_file_plan()'s, and its report no longer
    # points to convert_psychds()/convert_codebook()
    structure = _structure()
    paper = pc.test_paper(["x"])
    chained = ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        table=pd.DataFrame({"column": ["a"], "paper_id": ["p1"]}),
        paper=paper,
        summary_table=pd.DataFrame({"paper_id": pc.paper_id(paper)}),
        extras={"structure": structure, "group_no_evidence": False},
    )
    mo = module_run(chained, "psychds_check")
    pd.testing.assert_frame_equal(mo.table, psychds_file_plan(structure)["table"])
    report = "\n".join(b for b in mo.report if isinstance(b, str))
    assert "convert_psychds" not in report and "convert_codebook" not in report
    assert pc.psychds_file_plan is psychds_file_plan
