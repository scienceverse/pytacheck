"""psychds_file_plan() synchronised with metacheck 0.4.3.7 (R/psychds-file-plan.R at 4a61888).

The expected values follow the R source; the parity cases in ``parity/cases/mod_psychds*.yaml``
hold the values R produced.
"""

from __future__ import annotations

import pandas as pd

from metacheck.datacheck.psychds import (
    _plan_readme_folder,
    _plan_relative_paths,
    _plan_strip_archive,
    psychds_file_plan,
)


def test_strip_archive() -> None:
    assert _plan_strip_archive("data.zip/x.csv") == "x.csv"
    assert _plan_strip_archive("a\\b\\Data.ZIP.contents\\x.csv") == "x.csv"
    assert _plan_strip_archive("a/b.csv") == "a/b.csv"
    assert _plan_strip_archive(None) is None


def test_relative_paths_drop_the_folders_that_hold_every_file() -> None:
    assert _plan_relative_paths(["repo-1a2b3c/Code/a.R", "repo-1a2b3c/README.md"]) == [
        "Code/a.R",
        "README.md",
    ]
    # two top folders: nothing is dropped
    assert _plan_relative_paths(["a/x.R", "b/y.R"]) == ["a/x.R", "b/y.R"]
    # a missing path stays missing and does not count
    assert _plan_relative_paths(["top/a/x.R", None, "top/a/y.R"]) == ["x.R", None, "y.R"]


def test_readme_folder() -> None:
    assert _plan_readme_folder("Archive of OSF Storage/README.md") == "Archive-of-OSF-Storage"
    assert _plan_readme_folder("README.md") is None
    # the top of an archive is the top
    assert _plan_readme_folder("data.zip/README") is None
    assert _plan_readme_folder("data.zip.contents/README") is None
    assert _plan_readme_folder(None) is None
    assert _plan_readme_folder("") is None


def _plan(**cols: list[object]) -> dict[str, str]:
    df = pd.DataFrame(cols)
    table = psychds_file_plan(df)["table"]
    return dict(zip(table["current_path"], table["target_path"], strict=True))


def test_files_keep_the_folders_below_those_the_others_share() -> None:
    got = _plan(
        file_name=["a.R", "helpers.py", "x.csv", "y.csv"],
        file_path=[
            "repo-1/Code/a.R",
            "repo-1/Code/utils/helpers.py",
            "repo-1/Data/scenario_lb/x.csv",
            "repo-1/Data/y.csv",
        ],
        data_type=["code", "code", "data", "data"],
    )
    assert got["repo-1/Code/a.R"] == "analysis/a.R"
    assert got["repo-1/Code/utils/helpers.py"] == "analysis/utils/helpers.py"
    assert got["repo-1/Data/scenario_lb/x.csv"] == "data/scenario_lb/study-x_data.csv"
    assert got["repo-1/Data/y.csv"] == "data/study-y_data.csv"


def test_a_first_folder_that_names_the_kind_is_dropped() -> None:
    got = _plan(
        file_name=["eeg.npy", "other.npy"],
        file_path=["data/eeg.npy", "other.npy"],
        data_type=["data", "data"],
    )
    assert got["data/eeg.npy"] == "data/eeg.npy"
    assert got["other.npy"] == "data/other.npy"


def test_readmes_follow_their_folder() -> None:
    got = _plan(
        file_name=["README.md", "README.txt", "README_for_snowmelt.txt", "a.R"],
        file_path=["README.md", "Code/README.txt", "README_for_snowmelt.txt", "Code/a.R"],
        data_type=["documentation", "documentation", "documentation", "code"],
        doc_role=["readme", "readme", "readme", None],
    )
    assert got["README.md"] == "README.md"
    assert got["Code/README.txt"] == "documentation/Code_README.txt"
    # one about a single file keeps its name when another README is the main one
    assert got["README_for_snowmelt.txt"] == "documentation/README_for_snowmelt.txt"


def test_the_only_readme_at_the_top_is_the_main_one() -> None:
    got = _plan(
        file_name=["README_data.txt", "a.R"],
        file_path=["README_data.txt", "a.R"],
        data_type=["documentation", "code"],
        doc_role=["readme", None],
    )
    assert got["README_data.txt"] == "README.txt"


def test_target_names_are_portable() -> None:
    got = _plan(
        file_name=["R&D <final>.docx"],
        file_path=["documentation/R&D <final>.docx"],
        data_type=["documentation"],
    )
    assert got["documentation/R&D <final>.docx"] == "documentation/R&D _final_.docx"
