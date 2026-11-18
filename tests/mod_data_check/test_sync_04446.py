"""data_check and codebook_check synchronised with metacheck 0.4.4.4 to 0.4.4.6 (#648, #651).

Mirrors the new tests of tests/testthat/test-data-checks.R and test-module-codebook_check.R.
"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import metacheck as pc
from metacheck.datacheck.checks import data_check_pii_freetext, data_check_pii_values
from metacheck.datacheck.columns import parse_codebook
from metacheck.datacheck.files import (
    _data_group_from_short_label,
    data_group_llm,
    data_study_roster,
)
from metacheck.module import module_run
from metacheck.utils import local_options

TERMS = [
    "Coronary arteriosclerosis in patient with history of previous myocardial infarction",
    "Acute infarction of papillary muscle following surgery on the heart",
    "Dialysis therapy started by renal service after kidney failure",
    "Ulcer of skin due to type 2 diabetes mellitus of the left foot",
    "Congestive heart failure due to left ventricular systolic dysfunction",
    "Glaucoma suspect due to diabetes mellitus type 2 in both eyes",
]


def test_card_numbers_need_most_look_alikes_to_pass() -> None:
    # long codes in which a few pass the card checks by chance are not a column of cards (#648)
    rng = random.Random(648)
    codes = [f"{rng.uniform(1e14, 3e14):015.0f}" for _ in range(97)]
    codes += ["4111111111111111", "5500005555555559", "4012888888881881"]
    assert not data_check_pii_values(codes)["problem"]
    # a single card number in a text column still is one
    assert data_check_pii_values(["no comment"] * 20 + ["my card is 4111111111111111"])["problem"]


def test_freetext_leaves_out_prose_that_describes_things_and_paths() -> None:
    assert not data_check_pii_freetext(TERMS)["problem"]
    paths = [
        f"/Users/me/Dropbox/Road Hazards - Raw Videos/Final Set/clip_{i}.mp4" for i in range(1, 31)
    ]
    assert not data_check_pii_freetext(paths)["problem"]
    # answers in the first person in one of ten values are enough
    ans = [
        *TERMS[:5],
        "The task was long",
        "Fine, nothing to add here at all",
        "It was a bit hard to focus on the long blocks of the task",
        "Too many questions about the same topic in the last part",
        "My neighbour Anna helped me set up the laptop for the study",
    ]
    for k in (5, 6):
        ans[k] += " and the screen was small for the pictures shown"
    r = data_check_pii_freetext(ans)
    assert r["problem"]
    assert "10% written in the first person" in r["message"]


def test_one_letter_study_labels_count_only_when_the_paper_confirms_them() -> None:
    paper = pc.demopaper()
    paper.text.loc[0, "text"] = "We ran Experiment 1A, Experiment 1B and Experiment 2."
    paths = [
        "data/Shift_E1A_sub1-40.txt",
        "data/Shift_E1B_sub1-20.txt",
        "E2/RT.csv",
        "analysis.R",
        "dat.e01",
    ]
    assert _data_group_from_short_label(paths, paper) == ["ex1a", "ex1b", "ex2", None, None]
    # E is experiment, S is study: the paper names no numbered study
    assert all(x is None for x in _data_group_from_short_label(["df_s1.csv"], paper))
    # no paper, or a label the paper does not name: nothing is placed by the letter
    assert all(x is None for x in _data_group_from_short_label(paths))
    assert all(x is None for x in _data_group_from_short_label([*paths, "E3_RT.csv"], paper))
    # S also stands for sessions and supplementary material
    p2 = pc.demopaper()
    p2.text.loc[0, "text"] = "Study 1 and Study 2 are reported here."
    assert _data_group_from_short_label(["placebo_df_s1.csv", "S2_data.xlsx"], p2) == ["ex1", "ex2"]
    p2.text.loc[1, "text"] = "See Table S1 in the supplement."
    assert all(x is None for x in _data_group_from_short_label(["placebo_df_s1.csv"], p2))
    p2.text.loc[1, "text"] = "Each person came to Session 1 and Session 2."
    assert all(x is None for x in _data_group_from_short_label(["placebo_df_s1.csv"], p2))
    # the grouping uses it; the paper's roster then agrees with the files
    files = pd.DataFrame(
        {
            "file_name": [p.split("/")[-1] for p in paths[:3]],
            "file_path": paths[:3],
            "data_type": "data",
        }
    )
    with local_options({"metacheck.llm.use": False}):
        grp = data_group_llm(files, paper=paper)
    assert grp is not None and grp["group"].tolist() == ["ex1a", "ex1b", "ex2"]
    assert data_study_roster(paper, words=("study",)) == []


def test_a_person_repeated_on_several_rows_is_flagged_once(tmp_path: Path) -> None:
    from tests.mod_data_check.test_data_check import run_local

    d = tmp_path / "d"
    (d / "data").mkdir(parents=True)
    rng = np.random.default_rng(8)
    items = pd.DataFrame(
        rng.integers(1, 6, size=(40, 10)), columns=[f"panas_{i}" for i in range(1, 11)]
    )
    items["panas_1"] = np.resize([1, 5], 40)
    items.iloc[0, :] = 3  # participant 1 straightlines
    survey = pd.concat([pd.DataFrame({"participant_id": range(1, 41)}), items], axis=1)
    log = survey.loc[np.repeat(np.arange(40), 20)].reset_index(drop=True)
    log["trial"] = np.tile(np.arange(1, 21), 40)
    log.to_csv(d / "data" / "log.csv", index=False)
    mo = run_local(d)
    assert mo.careless["respondent"].tolist() == ["1"]
    assert mo.careless["n_blocks_flagged"].tolist() == [1]


def _data_with_dictionary(d: Path) -> Path:
    pytest.importorskip("openpyxl")
    path = d / "model_data_with_dictionary.xlsx"
    with pd.ExcelWriter(path) as w:
        pd.DataFrame(
            {
                "id": range(1, 9),
                "age": [21, 34, 25, 40, 30, 28, 33, 22],
                "score": [3.5, 4.1, 2.8, 3.9, 4.4, 3.1, 2.6, 3.8],
            }
        ).to_excel(w, sheet_name="Data", index=False)
        pd.DataFrame(
            {
                "Variable_Name": ["id", "age", "score"],
                "Description": ["Participant number", "Age in years", "Mean rating of the task"],
                "Type": ["integer", "numeric", "numeric"],
            }
        ).to_excel(w, sheet_name="Data_Dictionary", index=False)
    return path


def test_codebook_sheet_inside_a_data_spreadsheet_is_read(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    xlsx = _data_with_dictionary(d)
    with local_options({"metacheck.llm.use": False}):
        dc = module_run(None, "data_check", local_path=str(d), local_only=True)
        assert dc["doc_sheets"]["sheet"].tolist() == ["Data_Dictionary"]
        assert set(dc.table["column_name"]) == {"id", "age", "score"}
        mo = module_run(None, "codebook_check", local_path=str(d), local_only=True)
    assert "no codebook" not in mo.summary_text
    lab = mo.table
    assert lab["label"].tolist()[lab["column_name"].tolist().index("age")] == "Age in years"
    # only the dictionary sheet is read: no definitions made of the data's rows
    pc_ = parse_codebook(str(xlsx), sheets=["Data_Dictionary"])
    assert isinstance(pc_, pd.DataFrame)
    assert set(pc_["codebook_variable"]) == {"id", "age", "score"}
    assert parse_codebook(str(xlsx), sheets=["nope"]) is None


def test_codebook_check_labels_a_local_folder_checked_without_a_paper(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    pd.DataFrame({"id": range(1, 9), "age": [21, 34, 25, 40, 30, 28, 33, 22]}).to_csv(
        d / "study_data.csv", index=False
    )
    pd.DataFrame(
        {"variable": ["id", "age"], "description": ["Participant number", "Age in years"]}
    ).to_csv(d / "codebook.csv", index=False)
    with local_options({"metacheck.llm.use": False}):
        mo = module_run(None, "codebook_check", local_path=str(d), local_only=True)
    # paper_id is NA on both sides, and NA == NA matched nothing
    assert mo.table["label_status"].tolist() == ["labelled", "labelled"]
    assert (
        mo.table["label"].tolist()[mo.table["column_name"].tolist().index("age")] == "Age in years"
    )
